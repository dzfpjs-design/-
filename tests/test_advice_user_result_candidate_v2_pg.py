"""Current-user result access against the real test-only v2 terminal chain.

Task/Goal/source fixtures are synthetic substitutes; loopback provider only.
These tests do not prove real forestry results or production readiness.
"""
import copy
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, Task, User, Workspace
from generic_agents.contracts import digest
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_user_result_candidate_v2 as subject
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_v1_history_reserved_candidate_pg import history
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview as base_prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root


@pytest.fixture
def prepared_preview(synthetic_custody,monkeypatch,request):
    # This synthetic fixture substitutes a Goal/frame, so make the frozen
    # test snapshot consistent before the original preview is created. It does
    # not claim a real configured object, source, or production result.
    s,created,body,frame=base_prepared_preview.__wrapped__(synthetic_custody,monkeypatch,request)
    pins=frame['capability_pins'];snapshots=[]
    for entity in pins['entities']:
        fields=[{'key':field['field'],'label':'模拟项目'+str(index)} for index,field in enumerate(pins['fields'],1) if field['entity_id']==entity['id']]
        snapshots.append({'id':entity['id'],'type_name':'模拟对象','fields':fields})
    pins['object_snapshots_hash']=digest(snapshots)
    pins['pins_hash']=digest({key:value for key,value in pins.items() if key!='pins_hash'})
    with Session(engine()) as db:
        task=db.get(Task,s['task_id']);task.data={**task.data,'object_snapshots':snapshots};db.commit()
    monkeypatch.setattr(preview.frame_builder,'build_current',lambda *_,**__:copy.deepcopy(frame))
    return s,created,body,frame


@pytest.fixture
def candidate(monkeypatch):
    routes=list(app.router.routes)
    app.include_router(subject.router, prefix='/api/workspace')
    monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',True)
    yield
    app.router.routes[:]=routes
    app.openapi_schema=None


@pytest.fixture
def settled(history, spool_root, monkeypatch):
    s,created,owned,reserved,_,_=history;p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
        saved=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==created['plan_id']))
        opinion=_opinion(saved.data['preview_record']['cloud_request']['decision_input'])
    with Session(engine()) as db:terminal.install(db);db.commit()
    monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
    monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)
    args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'])
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),
                  usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);assert first['state']=='captured'
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,args[-1],first['intent_audit_id']);db.commit()
        assert len(server.requests)==1
    return s,created,proof,opinion


def url(s, created):
    return f"/api/workspace/tasks/{s['task_id']}/business-advice-v2/preparations/{created['plan_id']}/result"


def test_terminal_result_persists_after_relogin(candidate,settled,client,account):
    s,created,proof,opinion=settled
    response=client.get(url(s,created));assert response.status_code==200,response.text
    value=response.json();assert value['state']=='ready' and value['usage_tokens']==5
    assert value['recommendation']['reason']==opinion['recommendation']['reason']
    assert value['facts'] and all(isinstance(row['field'],str) and row['field'] and
        row['type'] in {'现场观测','资料','已登记能力','推断'} for row in value['facts'])
    assert all(row['assessment'] in {'已分析所提供内容','无法读取','无法比较'} for row in value['source_assessments'])
    assert all(row['source_type'] in {'文档','业务资料','观测资料','档案资料','能力资料'} for row in value['source_assessments'])
    assert set(value['recommendation']['supporting_facts']) <= {row['number'] for row in value['facts']}
    rendered=json.dumps(value,ensure_ascii=False)
    for text in [proof['call_id'],proof['plan_id'],proof['intent_id'],'candidate_ref','supporting_fact_refs']:
        assert text not in rendered
    engine().dispose()
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert fresh.get(url(s,created)).json()==value


def test_result_denies_other_user_and_workspace(candidate,settled,client,account):
    s,created,_,_=settled
    username,password=uuid4().hex,uuid4().hex
    assert client.post('/api/users',json={'username':username,'password':password,'role':'operator'}).status_code==201
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.get(url(s,created)).status_code in (403,404)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));workspace=Workspace(name='合成另一个工作区')
        db.add(workspace);db.flush();db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();identity=workspace.id
    client.headers['X-Workspace-ID']=identity
    assert client.get(url(s,created)).status_code in (403,404)


@pytest.mark.parametrize('target',['terminal','ledger','raw','preview'])
def test_tampered_terminal_or_ledger_never_returns_original_answer(candidate,settled,client,target):
    s,created,proof,_=settled
    with Session(engine()) as db:
        if target=='terminal':
            audit=db.get(Audit,proof['terminal_audit_id']);audit.data={**copy.deepcopy(audit.data),'charged_tokens':999}
        elif target=='ledger':
            call=db.get(CloudCall,proof['call_id'])
            ledger=db.scalar(select(Job).where(Job.workspace_id==s['principal'].workspace_id,
                Job.idempotency_key=='cloud-budget:'+call.data['day']))
            ledger.data={**ledger.data,'tokens':ledger.data['tokens']+1}
        elif target=='preview':
            row=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==created['plan_id']))
            row.data={**copy.deepcopy(row.data),'tampered_synthetic':True}
        else:
            db.execute(text('UPDATE public.ga_test_advice_v2_first_response SET raw_bytes=:raw '
                'WHERE workspace_id=:w AND call_id=:c'),{'raw':b'tampered','w':s['principal'].workspace_id,'c':proof['call_id']})
        db.commit()
    response=client.get(url(s,created));assert response.status_code==200
    assert response.json()=={'state':'needs_attention'}


def test_unsettled_chain_never_claims_a_result(candidate,history,client):
    s,created,_,_,_,_=history
    result=client.get(url(s,created));assert result.status_code==200
    assert result.json() in ({'state':'pending'},{'state':'needs_attention'})


def test_logout_revokes_user_result_access(candidate,settled,client):
    s,created,_,_=settled
    assert client.get(url(s,created)).json()['state']=='ready'
    assert client.post('/api/logout').status_code==200
    assert client.get(url(s,created)).status_code==401


def test_over_1000_history_never_returns_a_partial_result(candidate,settled,client):
    from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled
    s,created,_,_=settled
    with Session(engine()) as db:
        for _ in range(1001): add_cancelled(db,s)
        db.commit()
    result=client.get(url(s,created))
    assert result.status_code==200 and result.json()=={'state':'needs_attention'}


def test_session_expiring_during_read_hides_result_then_relogin_recovers(candidate,settled,client,account,monkeypatch):
    from datetime import timedelta
    from sqlalchemy import func
    from generic_agents.models import AuthSession
    s,created,_,_=settled
    with Session(engine()) as db:
        session=db.get(AuthSession,s['principal'].session_id)
        session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=3);db.commit()
    original=subject.terminal.verify;blocked=[]
    def wait(db,*args):
        value=original(db,*args)
        if not blocked:
            blocked.append(True);db.execute(select(func.pg_sleep(3.5)))
        return value
    monkeypatch.setattr(subject.terminal,'verify',wait)
    result=client.get(url(s,created));assert result.status_code==200 and result.json()=={'state':'needs_attention'}
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert fresh.get(url(s,created)).json()['state']=='ready'


def test_role_revocation_hides_result(candidate,settled,client,account):
    s,created,_,_=settled
    assert client.get(url(s,created)).json()['state']=='ready'
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]))
        member.role='viewer';db.commit()
    assert client.get(url(s,created)).status_code in (403,404)


def test_disconnect_spool_recovery_then_relogin_reads_original(candidate,started,spool_root,client,account,monkeypatch):
    from tests.test_advice_v2_recovery_console_candidate_pg import _pending
    s,created,_=started
    with Session(engine()) as db:terminal.install(db);db.commit()
    args,outcome,permit=_pending(started,monkeypatch)
    assert outcome['state']=='capture_pending'
    before=client.get(url(s,created));assert before.status_code==200 and before.json()['state'] in {'pending','needs_attention'}
    monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
    engine().dispose()
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        recovered=fresh.post(f"/api/workspace/tasks/{s['task_id']}/business-advice/response-recovery/{permit}/recover")
        assert recovered.status_code==200,recovered.text
        result=fresh.get(url(s,created));assert result.status_code==200 and result.json()['state']=='ready'
    with Session(engine()) as db:
        assert len(list(db.scalars(select(CloudCall).where(CloudCall.workspace_id==account[2]))))==1
