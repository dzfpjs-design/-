"""Authenticated recovery on disposable PG and a loopback fake provider."""
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership, User, Workspace
from generic_agents import same_goal_advice_deepseek_adapter_candidate_v2 as adapter
from generic_agents import same_goal_advice_spool_recovery_candidate_v2 as recovery
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_response_spool_test_v2 as spool
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as reconcile
from generic_agents import cloud_budget
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup, synthetic_source_expiry
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark = pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'), reason='isolated fake provider only')


@pytest.fixture(autouse=True)
def spool_root(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='advice-v2-response-spool-') as path:
        monkeypatch.setattr(settings(), 'v2_response_spool_enabled', True)
        monkeypatch.setattr(settings(), 'v2_response_spool_root', Path(path))
        monkeypatch.setattr(settings(), 'v2_advice_recovery_console_enabled', True)
        yield


def _url(task):
    return '/api/workspace/tasks/' + task + '/business-advice/response-recovery'


def _pending(started, monkeypatch, mode='reply'):
    _, args, opinion = setup(started)
    port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port, mode=mode, content=json.dumps(opinion), usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        with monkeypatch.context() as patch:
            patch.setattr(recovery, 'recover', lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic database unavailable')))
            outcome = adapter.execute_synthetic_test_only(*args)
        assert len(server.requests) == 1
        with Session(engine()) as db:
            permit = db.scalar(select(Audit).where(Audit.workspace_id == args[0].workspace_id,
                Audit.action == permits.ACTION, Audit.data['cloud_call_id'].as_string() == args[-1]))
            assert permit is not None
            permit_id = permit.id
        return args, outcome, permit_id


def test_authenticated_recovery_after_disconnect_and_relogin(started, client, account, monkeypatch):
    args, outcome, permit = _pending(started, monkeypatch)
    assert outcome['state'] == 'capture_pending'
    url = _url(args[1]); view = client.get(url)
    assert view.status_code == 200, view.text
    items = view.json()['items']
    assert len(items) == 1 and items[0]['permit_id'] == permit and items[0]['state'] == 'ready'
    assert items[0]['recorded_at']
    csrf = client.headers.pop('X-CSRF-Token')
    try:
        assert client.post(url + '/' + permit + '/recover').status_code == 403
    finally:
        client.headers['X-CSRF-Token'] = csrf
    result = client.post(url + '/' + permit + '/recover')
    assert result.status_code == 200, result.text
    assert result.json() == {'state':'settled','answer_state':'succeeded','usage_unavailable':False,
        'ledger_verified':False,'retry_authorized':False}
    engine().dispose()
    with TestClient(app) as fresh:
        login = fresh.post('/api/login', json={'username':account[0], 'password':account[1]})
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert fresh.get(url).json()['items'][0]['state'] == 'settled'
        assert fresh.post(url + '/' + permit + '/recover').status_code == 200
    with Session(engine()) as db:
        call = db.get(CloudCall, args[-1])
        assert call.charged_tokens == 5
        budget = db.scalar(select(Job).where(Job.workspace_id == account[2],
            Job.idempotency_key == 'cloud-budget:' + call.data['day']))
        budget.data = {**budget.data, 'calls': 0}; db.commit()
    assert client.get(url).json()['items'][0]['state'] == 'needs_attention'


def test_scope_roles_and_missing_or_incomplete_response(started, client, account, monkeypatch):
    args, _, permit = _pending(started, monkeypatch, mode='drop')
    url = _url(args[1]); assert client.get(url).json()['items'][0]['state'] == 'needs_attention'
    assert client.post(url + '/' + permit + '/recover').status_code == 409
    assert client.post(_url(str(uuid4())) + '/' + permit + '/recover').status_code == 404
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        other = Workspace(name='隔离工作区'); db.add(other); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=other.id, role='admin')); db.commit()
        other_id = other.id
    client.headers['X-Workspace-ID'] = other_id
    assert client.get(url).status_code == 404
    assert client.post(url + '/' + permit + '/recover').status_code == 404
    client.headers['X-Workspace-ID'] = account[2]
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        member = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == account[2]))
        member.role = 'viewer'; db.commit()
    assert client.get(url).status_code in (403,409)
    assert client.post(url + '/' + permit + '/recover').status_code in (403,409)
    monkeypatch.setattr(settings(), 'v2_advice_recovery_console_enabled', False)
    assert client.get(url).json() == {'enabled':False, 'items':[]}
    assert client.post(url + '/' + permit + '/recover').status_code == 404


def test_revocation_during_file_read_blocks_capture(started, client, account, monkeypatch):
    args, outcome, permit = _pending(started, monkeypatch)
    url = _url(args[1]); original_load = spool.load
    def revoke_after_read(*a, **kw):
        value = original_load(*a, **kw)
        with Session(engine()) as db:
            user = db.scalar(select(User).where(User.username == account[0]))
            member = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == account[2]))
            member.role = 'viewer'; db.commit()
        return value
    with monkeypatch.context() as patch:
        patch.setattr(spool, 'load', revoke_after_read)
        assert client.post(url + '/' + permit + '/recover').status_code == 409
    with Session(engine()) as db:
        assert storage.read(db, args[0].workspace_id, args[-1], outcome['intent_audit_id'])['state'] == 'unknown'
        user = db.scalar(select(User).where(User.username == account[0]))
        member = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == account[2]))
        member.role = 'admin'; db.commit()
    assert client.post(url + '/' + permit + '/recover').status_code == 200


def test_already_captured_answer_can_settle_without_spool(started, client, monkeypatch):
    _, args, opinion = setup(started)
    port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port, content=json.dumps(opinion), usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        with monkeypatch.context() as patch:
            patch.setattr(reconcile, 'reconcile', lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic outage')))
            outcome = adapter.execute_synthetic_test_only(*args)
        assert outcome['state'] == 'capture_pending' and len(server.requests) == 1
    url = _url(args[1]); permit = outcome['permit_audit_id']
    with monkeypatch.context() as patch:
        patch.setattr(spool, 'load', lambda *_: (_ for _ in ()).throw(spool.SpoolError('spool_incomplete')))
        assert client.get(url).json()['items'][0]['state'] == 'needs_settlement'
        assert client.post(url + '/' + permit + '/recover').status_code == 200
        assert client.get(url).json()['items'][0]['state'] == 'settled'
    with Session(engine()) as db:
        assert db.get(CloudCall, args[-1]).charged_tokens == 5


def test_missing_provider_usage_is_reported_as_unknown(started, client):
    _, args, opinion = setup(started)
    port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port, content=json.dumps(opinion)) as server:
        result = adapter.execute_synthetic_test_only(*args)
        assert result['state'] == 'settled' and result['usage_unavailable'] is True
        assert len(server.requests) == 1
    url = _url(args[1]); item = client.get(url).json()['items'][0]
    assert item['state'] == 'settled_usage_unknown'
    again = client.post(url + '/' + item['permit_id'] + '/recover')
    assert again.status_code == 200 and again.json()['usage_unavailable'] is True
    assert again.json()['retry_authorized'] is False
    with Session(engine()) as db:
        call = db.get(CloudCall, args[-1])
        assert call.charged_tokens == call.reserved_tokens > 0


@pytest.mark.parametrize('mode,content,usage,expected',[
    ('http_error','{}',None,'failed_usage_unknown'),
    ('reply','not valid json',{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5},'failed'),
])
def test_failed_model_response_never_looks_like_generated_advice(started, client, mode, content, usage, expected):
    _, args, _ = setup(started)
    port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port, mode=mode, content=content, usage=usage) as server:
        result = adapter.execute_synthetic_test_only(*args)
        assert result['answer_state'] == 'failed' and len(server.requests) == 1
    item = client.get(_url(args[1])).json()['items'][0]
    assert item['state'] == expected


@pytest.mark.parametrize('damaged',['task_id','object_id'])
def test_corrupted_permit_link_stays_visible_and_cannot_recover(started, client, monkeypatch, damaged):
    args, _, permit = _pending(started, monkeypatch)
    with Session(engine()) as db:
        row = db.get(Audit, permit)
        if damaged == 'task_id': row.data = {**row.data, 'task_id':str(uuid4())}
        else: row.object_id = str(uuid4())
        db.commit()
    url = _url(args[1]); result = client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()['items'][0]['state'] == 'needs_attention'
    assert client.post(url + '/' + permit + '/recover').status_code == 409


def test_intent_without_permit_is_visible_and_never_recovered(started, client):
    _, args, _ = setup(started)
    p,task_id,plan_id,job_id,lease_token,call_id = args
    with Session(engine()) as db:
        made=intents.issue(db,p,task_id,plan_id,job_id,lease_token,call_id);db.commit()
    url=_url(task_id); result=client.get(url)
    assert result.status_code==200, result.text
    item=result.json()['items'][0]
    assert item['state']=='needs_attention' and item['permit_id']==made['intent_audit_id']
    assert client.post(url+'/'+item['permit_id']+'/recover').status_code == 404


def test_reservation_without_intent_is_visible_and_never_recovered(started, client):
    _, args, _ = setup(started)
    url=_url(args[1]); result=client.get(url)
    assert result.status_code==200, result.text
    item=result.json()['items'][0]
    assert item['state']=='needs_attention'
    assert client.post(url+'/'+item['permit_id']+'/recover').status_code == 404


def test_session_expiration_before_commit_prevents_capture(started, client, account, monkeypatch):
    args, outcome, permit = _pending(started, monkeypatch)
    with Session(engine()) as db:
        session=db.get(AuthSession,args[0].session_id)
        session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=1);db.commit()
    original=storage.capture
    def slow_capture(*a,**kw):
        result=original(*a,**kw);time.sleep(1.5);return result
    with monkeypatch.context() as patch:
        patch.setattr(storage,'capture',slow_capture)
        assert client.post(_url(args[1])+'/'+permit+'/recover').status_code == 409
    with Session(engine()) as db:
        assert storage.read(db,args[0].workspace_id,args[-1],outcome['intent_audit_id'])['state']=='unknown'


def test_session_expiration_before_settlement_commit_preserves_original_for_relogin(started,client,account,monkeypatch):
    args,outcome,permit=_pending(started,monkeypatch)
    with Session(engine()) as db:
        session=db.get(AuthSession,args[0].session_id)
        session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=1);db.commit()
    original=cloud_budget.settle_paid_response
    def slow_settlement(*a,**kw):
        result=original(*a,**kw);time.sleep(1.5);return result
    url=_url(args[1])
    with monkeypatch.context() as patch:
        patch.setattr(cloud_budget,'settle_paid_response',slow_settlement)
        assert client.post(url+'/'+permit+'/recover').status_code==409
    with Session(engine()) as db:
        assert isinstance(storage.read(db,args[0].workspace_id,args[-1],outcome['intent_audit_id']),tuple)
        assert db.get(CloudCall,args[-1]).status=='reserved'
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]})
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert fresh.post(url+'/'+permit+'/recover').status_code==200


def test_many_records_paginate_and_stay_workspace_scoped(started, client, account, monkeypatch):
    args, _, real_permit = _pending(started, monkeypatch)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0])); other=Workspace(name='隔离工作区');db.add(other);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=other.id,role='admin'))
        for _ in range(106):
            db.add(Audit(id=str(uuid4()),workspace_id=account[2],actor_id=user.id,action=permits.ACTION,
                object_id=str(uuid4()),data={'schema':permits.SCHEMA,'task_id':args[1],
                'cloud_call_id':str(uuid4()),'intent_ref':{'id':str(uuid4())}}))
        db.commit();other_id=other.id
    url=_url(args[1]); cursor=None; seen=[]
    while True:
        result=client.get(url,params={'cursor':cursor} if cursor else {})
        assert result.status_code==200,result.text
        page=result.json();seen.extend(item['permit_id'] for item in page['items']);cursor=page['next_cursor']
        if not cursor:break
    assert len(seen)==107 and len(set(seen))==107 and real_permit in seen
    assert client.post(url+'/'+real_permit+'/recover').status_code==200
    client.headers['X-Workspace-ID']=other_id
    assert client.get(url).status_code==404


def test_same_day_totals_do_not_claim_per_call_accounting_proof(started, client):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}):
        assert adapter.execute_synthetic_test_only(*args)['state']=='settled'
    with Session(engine()) as db:
        call=db.get(CloudCall,args[-1]);budget=db.scalar(select(Job).where(Job.workspace_id==call.workspace_id,
            Job.idempotency_key=='cloud-budget:'+call.data['day']))
        db.add(CloudCall(id=str(uuid4()),workspace_id=call.workspace_id,capability_id=call.capability_id,
            status='succeeded',reserved_tokens=5,charged_tokens=5,data={'day':call.data['day']}))
        # Only one of two same-day calls appears in the aggregate, which still
        # satisfies the per-record lower bound checked by discovery.
        budget.data={**budget.data,'calls':1,'tokens':5};db.commit()
    item=client.get(_url(args[1])).json()['items'][0]
    assert item['state']=='settled' and item['ledger_verified'] is False
