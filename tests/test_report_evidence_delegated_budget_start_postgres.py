"""Isolated PG contract tests; synthetic source/model and no provider access."""
import copy
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_delegated_budget_start as subject
from generic_agents import report_evidence_delegated_origin as origin
from generic_agents import report_evidence_delegated_dispatch_gate as dispatch_gate
from generic_agents import cloud_report_plan_allowance as plans
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership, SetupReportDraft, User, Workspace
from generic_agents.security import authenticated, password_hash
from tests.test_cloud_report_plan_allowance_postgres import own_database, prepared_request
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_second_batch_preview import scene_with_candidate
from tests.test_vertical_slice import publish


@pytest.fixture(autouse=True)
def isolated_dispatch_gate(monkeypatch):
    # Contract tests only: no worker or network transport is started.
    monkeypatch.setattr(dispatch_gate, 'ready', lambda: True)


def scene_and_preview(scene, monkeypatch, *, preview_tamper=None):
    _, old, _ = scene_with_candidate(scene,monkeypatch)
    model=json.loads((Path(__file__).parents[1]/'examples/deepseek.json').read_text())
    model.update(name='synthetic_start_'+uuid4().hex[:10],description='Synthetic no-network scope')
    model['execution'].update(model='deepseek-v4-pro',thinking_mode='disabled',max_tokens=8192)
    cap=publish(scene['client'],model)
    reading={**copy.deepcopy(scene['body']),'request_id':'fl_'+str(uuid4()),'model_id':cap['id']}
    command=origin.LinkedPreviewCommand(original_draft_id=old['id'],original_revision=old['revision'],reading=reading)
    request=prepared_request(scene)
    with Session(engine()) as db:
        principal=authenticated(request,db)
        preview=subject.preview_tx(db,principal,request,command)
        if preview_tamper is None:
            db.commit()
        else:
            if preview_tamper=='record':
                row=db.get(Job,preview['preview_id']);row.data={**row.data,'actor_id':str(uuid4())}
            elif preview_tamper=='audit':
                a=db.scalar(select(Audit).where(Audit.object_id==preview['preview_id'],Audit.action=='cloud.report_plan_preview.created_v1'))
                a.data={**a.data,'allowance_hash':'0'*64}
            else:db.get(AuthSession,principal.session_id).revoked=True
            with pytest.raises(plans.PlanAllowanceDenied):db.commit()
            db.rollback()
            with Session(engine()) as check:
                assert check.get(Job,preview['preview_id']) is None
                assert check.scalar(select(Audit).where(Audit.object_id==preview['preview_id'])) is None
            return None
    start=origin.LinkedStartCommand(**command.model_dump(mode='json'),
        linked_preview_hash=preview['linked_preview_hash'],consent=True)
    return principal,request,start,preview,old


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(m)) for m in (SetupReportDraft,Job,CloudCall,Audit))


def expire_preview(preview_id):
    # Synthetic database only: simulate the full signed TTL passing.
    with Session(engine()) as db:
        row=db.get(Job,preview_id);data=copy.deepcopy(row.data);allowance=data['allowance']
        now=db.scalar(select(func.clock_timestamp()))
        allowance['not_before']=(now-timedelta(hours=26)).isoformat()
        allowance['expires_at']=(now-timedelta(hours=2)).isoformat()
        allowance['allowance_preview_hash']=digest({k:v for k,v in allowance.items() if k!='allowance_preview_hash'})
        row.data=data
        a=db.scalar(select(Audit).where(Audit.object_id==preview_id,Audit.action=='cloud.report_plan_preview.created_v1'))
        a.data={**a.data,'allowance_hash':digest(allowance)}
        db.commit()


def test_default_disabled_before_touching_database():
    assert subject._enabled() is False


@pytest.mark.parametrize('tamper',['record','audit','authority'])
def test_preview_commit_rejects_mutation_and_rolls_back(reading_scene,monkeypatch,tamper):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    scene_and_preview(reading_scene,monkeypatch,preview_tamper=tamper)


def test_atomic_issue_replay_and_old_source_untouched(reading_scene,monkeypatch):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    p,request,command,preview,old=scene_and_preview(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        source_before=copy.deepcopy(db.get(SetupReportDraft,old['id']).data)
        before=counts(db); result=subject.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance'])
        grant=db.get(Job,result['grant_id']); manifest=grant.data['manifest']
        assert manifest['max_tokens']==sum(s['token_bound'] for s in preview['allowance']['stages'])
        assert manifest['max_calls']==len(preview['allowance']['stages'])
        assert counts(db)[2]==before[2] and result['replayed'] is False
        db.commit()
    with Session(engine()) as db:
        assert db.get(SetupReportDraft,old['id']).data==source_before
        after=counts(db)
        replay=subject.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance'])
        assert replay['replayed'] is True and replay['grant_id']==result['grant_id']
        assert counts(db)==after


@pytest.mark.parametrize('mode',['client_amount','client_rehash','saved_preview_tampered','audit_tampered',
    'rollback','revoked_before','member_revoked','revoked_commit','preview_commit','audit_commit'])
def test_bad_approval_or_crash_leaves_no_partial_issue(reading_scene,monkeypatch,mode):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    p,request,command,preview,old=scene_and_preview(reading_scene,monkeypatch)
    if mode=='client_amount':
        preview['allowance']['max_tokens']+=1
    if mode in ('client_rehash','saved_preview_tampered'):
        allowance=preview['allowance'];allowance['draft_id']=str(uuid4())
        allowance['allowance_preview_hash']=digest({k:v for k,v in allowance.items() if k!='allowance_preview_hash'})
    if mode=='saved_preview_tampered':
        with Session(engine()) as db:
            row=db.get(Job,preview['preview_id']);row.data={**row.data,'allowance':preview['allowance']};db.commit()
    if mode=='audit_tampered':
        with Session(engine()) as db:
            a=db.scalar(select(Audit).where(Audit.object_id==preview['preview_id'],Audit.action=='cloud.report_plan_preview.created_v1'))
            a.data={**a.data,'allowance_hash':'0'*64};db.commit()
    if mode=='revoked_before':
        with Session(engine()) as db:
            db.get(AuthSession,p.session_id).revoked=True;db.commit()
    if mode=='member_revoked':
        with Session(engine()) as db:
            db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==p.workspace_id)).role='operator';db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        before=counts(db);source_before=copy.deepcopy(db.get(SetupReportDraft,old['id']).data)
        if mode in ('client_amount','client_rehash','saved_preview_tampered','audit_tampered','revoked_before','member_revoked'):
            with pytest.raises((HTTPException,plans.PlanAllowanceDenied)):
                subject.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance'])
            db.rollback()
        else:
            subject.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance'])
            if mode in ('revoked_commit','preview_commit','audit_commit'):
                if mode=='revoked_commit':db.get(AuthSession,p.session_id).revoked=True
                elif mode=='preview_commit':
                    r=db.get(Job,preview['preview_id']);r.data={**r.data,'actor_id':str(uuid4())}
                else:
                    a=db.scalar(select(Audit).where(Audit.object_id==preview['preview_id'],Audit.action=='cloud.report_plan_preview.created_v1'))
                    a.data={**a.data,'allowance_hash':'0'*64}
                with pytest.raises(plans.PlanAllowanceDenied):db.commit()
            db.rollback()
    with Session(engine()) as db:
        assert counts(db)==before
        assert db.get(SetupReportDraft,old['id']).data==source_before


def test_concurrent_same_request_creates_one_task(reading_scene,monkeypatch):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    p,request,command,preview,_=scene_and_preview(reading_scene,monkeypatch)
    def run():
        with Session(engine(),expire_on_commit=False) as db:
            result=subject.start_tx(db,p,request,command,preview_id=preview['preview_id'],allowance_preview=preview['allowance'])
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(),range(2)))
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert results[0]['grant_id']==results[1]['grant_id']


def test_concurrent_distinct_requests_cannot_duplicate_old_source(reading_scene,monkeypatch):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    p,request,first,preview,_=scene_and_preview(reading_scene,monkeypatch)
    different=first.model_dump(mode='json',exclude={'linked_preview_hash','consent'})
    different['reading']['request_id']='fl_'+str(uuid4())
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as error:
            subject.preview_tx(db,p,request,origin.LinkedPreviewCommand(**different))
        assert error.value.detail['code']=='delegated_preview_exists'


def test_http_saved_preview_gate_idempotency_and_refresh(reading_scene, monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app

    monkeypatch.setattr(subject, '_enabled', lambda: True)
    scene=reading_scene; client=scene['client']
    p,request,command,preview,old=scene_and_preview(scene,monkeypatch)
    base='/api/setup/report-evidence-delegated/budget-plan/'
    body=command.model_dump(mode='json',exclude={'linked_preview_hash','consent'})
    with Session(engine()) as db:
        before=counts(db); original=copy.deepcopy(db.get(SetupReportDraft,old['id']).data)
    response=client.post(base+'preview',json=body)
    assert response.status_code==200, response.text
    plan=response.json()
    assert response.headers['cache-control']=='private, no-store'
    assert plan['preview_id']==preview['preview_id'] and plan['status']=='saved'
    assert plan['max_calls']==len(preview['allowance']['stages']) and plan['can_send'] is False
    assert plan['allowance_issued'] is None and plan['budget_reserved'] is None and plan['model_called'] is None
    assert plan['usage_marker']=='not_recorded' and plan['execution_history']=='unknown'
    assert plan['preview_action_calls_model'] is False
    assert plan['command_hash']==subject._preview_command_hash(p,origin.LinkedPreviewCommand(**body))
    assert plan['selection_hash']==digest(body['reading']['selection'])
    assert plan['source_hash']==preview['allowance']['source_hash']
    assert plan['coverage']['selected_pages']==len(body['reading']['selection']['pages'])
    assert plan['coverage']['word_blocks']>0 and plan['source_summary_verified'] is True
    assert plan['summary_hash']==digest({k:v for k,v in plan.items() if k!='summary_hash'})
    changed=copy.deepcopy(body);changed['reading']['note']+=' changed'
    assert client.post(base+'preview',json=changed).status_code==409
    lookup={'original_draft_id':old['id']}
    assert client.post(base+'find',json=lookup).json()['items'][0]['preview_id']==preview['preview_id']
    with TestClient(app) as reopened:
        reopened.cookies.update(client.cookies)
        reopened.headers.update(dict(client.headers))
        found=reopened.post(base+'find',json=lookup)
        assert found.status_code==200,found.text
        assert found.json()['items'][0]['status']=='saved'
    assert client.post(base+'start',json={'preview_id':preview['preview_id'],'consent':False}).status_code==422
    monkeypatch.setattr(dispatch_gate,'ready',lambda:False)
    denied=client.post(base+'start',json={'preview_id':preview['preview_id'],'consent':True})
    assert denied.status_code==409,denied.text
    with Session(engine()) as db:
        assert counts(db)==before
    monkeypatch.setattr(dispatch_gate,'ready',lambda:True)
    payload={'preview_id':preview['preview_id'],'consent':True}
    started=client.post(base+'start',json=payload)
    assert started.status_code==202,started.text
    receipt=started.json()
    assert receipt['replayed'] is False and receipt['user_confirmed_business'] is False
    # Even if dispatch is subsequently disabled, a lost receipt can be replayed.
    monkeypatch.setattr(dispatch_gate,'ready',lambda:False)
    monkeypatch.setattr(subject,'_enabled',lambda:False)
    again=client.post(base+'start',json=payload)
    assert again.status_code==202,again.text
    assert again.json()['replayed'] is True and again.json()['draft_id']==receipt['draft_id']
    found=client.post(base+'find',json=lookup)
    assert found.status_code==200,found.text
    assert found.json()['items'][0]['status']=='started'
    assert found.json()['items'][0]['draft_id']==receipt['draft_id']
    with Session(engine()) as db:
        assert db.get(SetupReportDraft,old['id']).data==original
        assert counts(db)[2]==before[2]
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_report_plan_allowance_v1'))==1


def test_http_plan_is_private_to_actor(reading_scene,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    username='other_'+uuid4().hex; password=uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        workspace=Workspace(name='synthetic isolated workspace');db.add(workspace);db.flush()
        other_workspace=workspace.id
        db.add_all([Membership(user_id=user.id,workspace_id=scene['p'].workspace_id,role='admin'),
                    Membership(user_id=user.id,workspace_id=other_workspace,role='admin'),
                    Membership(user_id=scene['p'].user_id,workspace_id=other_workspace,role='admin')]);db.commit()
    with TestClient(app) as client:
        login=client.post('/api/login',json={'username':username,'password':password})
        assert login.status_code==200
        client.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':scene['p'].workspace_id})
        base='/api/setup/report-evidence-delegated/budget-plan/'
        assert client.post(base+'find',json={'original_draft_id':old['id']}).status_code==404
        assert client.post(base+'start',json={'preview_id':preview['preview_id'],'consent':True}).status_code==404
    with TestClient(app) as client:
        client.cookies.update(scene['client'].cookies)
        client.headers.update(dict(scene['client'].headers))
        client.headers['X-Workspace-ID']=other_workspace
        assert client.post(base+'find',json={'original_draft_id':old['id']}).status_code==404
        # The otherwise empty workspace has no setup draft, so authorization
        # fails before the request can inspect any foreign preview.
        assert client.post(base+'start',json={'preview_id':preview['preview_id'],'consent':True}).status_code==409
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.user_id==scene['p'].user_id,
            Membership.workspace_id==scene['p'].workspace_id));member.role='operator';db.commit()
    assert scene['client'].post(base+'find',json={'original_draft_id':old['id']}).status_code==403
    assert scene['client'].post(base+'start',json={'preview_id':preview['preview_id'],'consent':True}).status_code==403


def test_http_failure_rolls_back_task_and_audit(reading_scene,monkeypatch):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    with Session(engine()) as db:
        before=counts(db); source=copy.deepcopy(db.get(SetupReportDraft,old['id']).data)
    def fail_after_link(*args,**kwargs):
        raise RuntimeError('synthetic issuance failure')
    monkeypatch.setattr(plans,'issue_plan_tx',fail_after_link)
    with pytest.raises(RuntimeError,match='synthetic issuance failure'):
        scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/start',
            json={'preview_id':preview['preview_id'],'consent':True})
    with Session(engine()) as db:
        assert counts(db)==before
        assert db.get(SetupReportDraft,old['id']).data==source
        assert db.get(Job,preview['preview_id']).data['used_grant_id'] is None


def test_preview_open_without_dispatch_and_workspace_rate_limit(reading_scene,monkeypatch):
    # Leave the production write gate closed; only budget preview is accessible.
    monkeypatch.setattr(dispatch_gate,'ready',lambda:False)
    scene=reading_scene
    _,_,command,preview,_=scene_and_preview(scene,monkeypatch)
    body=command.model_dump(mode='json',exclude={'linked_preview_hash','consent'})
    base='/api/setup/report-evidence-delegated/budget-plan/'
    with Session(engine()) as db:
        db.add_all([Job(workspace_id=scene['p'].workspace_id,kind='cloud_report_plan_preview_v1',
            status='internal',idempotency_key='synthetic-rate-'+uuid4().hex,data={}) for _ in range(19)])
        db.commit()
    replay=scene['client'].post(base+'preview',json=body)
    assert replay.status_code==200 and replay.json()['preview_id']==preview['preview_id']
    body['reading']['request_id']='fl_'+str(uuid4())
    limited=scene['client'].post(base+'preview',json=body)
    assert limited.status_code==429,limited.text
    assert limited.json()['detail']['code']=='delegated_preview_rate_limited'
    monkeypatch.setattr(subject,'MAX_WORKSPACE_SAVED_PREVIEWS',1)
    capacity=scene['client'].post(base+'preview',json=body)
    assert capacity.status_code==429
    assert capacity.json()['detail']['code']=='delegated_preview_storage_limit'
    locked=scene['client'].post(base+'start',json={'preview_id':preview['preview_id'],'consent':True})
    assert locked.status_code==409,locked.text


def test_http_saved_previews_paginate_without_losing_old_records(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,command,first,old=scene_and_preview(scene,monkeypatch)
    base='/api/setup/report-evidence-delegated/budget-plan/'
    expire_preview(first['preview_id'])
    renewal={'preview_id':first['preview_id'],'request_id':'fl_'+str(uuid4())}
    created=scene['client'].post(base+'renew',json=renewal)
    assert created.status_code==200,created.text
    created=created.json()['plan']
    lookup={'original_draft_id':old['id'],'page_size':1}
    page=scene['client'].post(base+'find',json=lookup)
    assert page.status_code==200,page.text
    assert page.json()['truncated'] is True
    assert page.json()['items'][0]['preview_id']==created['preview_id']
    lookup['before_preview_id']=page.json()['next_before_preview_id']
    page=scene['client'].post(base+'find',json=lookup)
    assert page.status_code==200,page.text
    assert page.json()['items'][0]['preview_id']==first['preview_id']
    assert page.json()['truncated'] is False and page.json()['next_before_preview_id'] is None


def test_renew_requires_expired_unstarted_and_replays_same_number(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    base='/api/setup/report-evidence-delegated/budget-plan/'
    body={'preview_id':preview['preview_id'],'request_id':'fl_'+str(uuid4())}
    active=scene['client'].post(base+'renew',json=body)
    assert active.status_code==409 and active.json()['detail']['code']=='delegated_renewal_not_allowed'
    expire_preview(preview['preview_id'])
    with Session(engine()) as db:
        before=counts(db); source=copy.deepcopy(db.get(SetupReportDraft,old['id']).data)
    first=scene['client'].post(base+'renew',json=body)
    assert first.status_code==200,first.text
    assert first.json()['replayed'] is False and first.json()['plan']['renewed_from']==preview['preview_id']
    second=scene['client'].post(base+'renew',json=body)
    assert second.status_code==200 and second.json()['replayed'] is True
    assert second.json()['plan']['preview_id']==first.json()['plan']['preview_id']
    conflicting=scene['client'].post(base+'renew',json={**body,'request_id':'fl_'+str(uuid4())})
    assert conflicting.status_code==409
    with Session(engine()) as db:
        after=counts(db)
        assert after[0]==before[0] and after[2]==before[2]
        assert db.get(SetupReportDraft,old['id']).data==source
        assert db.get(Job,preview['preview_id']).data['used_grant_id'] is None


def test_renew_fails_closed_for_unknown_outcome(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    expire_preview(preview['preview_id'])
    with Session(engine()) as db:
        data=db.get(Job,preview['preview_id']).data['allowance']
        db.add(Job(id=data['job_id'],workspace_id=scene['p'].workspace_id,kind='synthetic_partial',
            idempotency_key='synthetic-partial-'+uuid4().hex,data={}));db.commit()
    found=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/find',
        json={'original_draft_id':old['id']})
    assert found.status_code==200,found.text
    summary=found.json()['items'][0]
    assert summary['usage_marker']=='not_recorded' and summary['execution_history']=='unknown'
    assert summary['allowance_issued'] is None and summary['budget_reserved'] is None and summary['model_called'] is None
    result=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/renew',
        json={'preview_id':preview['preview_id'],'request_id':'fl_'+str(uuid4())})
    assert result.status_code==409 and result.json()['detail']['code']=='delegated_renewal_outcome_unknown'


def test_renew_rechecks_old_source_revision(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    expire_preview(preview['preview_id'])
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,old['id']);row.version+=1;db.commit()
        before=counts(db)
    result=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/renew',
        json={'preview_id':preview['preview_id'],'request_id':'fl_'+str(uuid4())})
    assert result.status_code==409
    with Session(engine()) as db:
        assert counts(db)==before


def test_renew_rejects_already_started_plan(reading_scene,monkeypatch):
    monkeypatch.setattr(subject,'_enabled',lambda:True)
    scene=reading_scene
    _,_,_,preview,_=scene_and_preview(scene,monkeypatch)
    base='/api/setup/report-evidence-delegated/budget-plan/'
    started=scene['client'].post(base+'start',json={'preview_id':preview['preview_id'],'consent':True})
    assert started.status_code==202,started.text
    renewal=scene['client'].post(base+'renew',json={'preview_id':preview['preview_id'],'request_id':'fl_'+str(uuid4())})
    assert renewal.status_code==409 and renewal.json()['detail']['code']=='delegated_renewal_not_allowed'


def test_audited_source_summary_tampering_is_rejected(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    with Session(engine()) as db:
        row=db.get(Job,preview['preview_id']);data=copy.deepcopy(row.data)
        data['source_summary']['coverage']['word_blocks']+=1;row.data=data;db.commit()
    result=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/find',
        json={'original_draft_id':old['id']})
    assert result.status_code==409 and result.json()['detail']['code']=='delegated_allowance_invalid'


def test_concurrent_renewal_has_one_successor(reading_scene,monkeypatch):
    principal,request,_,preview,_=scene_and_preview(reading_scene,monkeypatch)
    expire_preview(preview['preview_id'])
    body=subject.SavedPlanRenew(preview_id=preview['preview_id'],request_id='fl_'+str(uuid4()))
    def run():
        with Session(engine(),expire_on_commit=False) as db:
            record,_,replayed=subject.renew_tx(db,principal,request,body)
            db.commit();return record.id,replayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(),range(2)))
    assert results[0][0]==results[1][0]
    assert sorted(item[1] for item in results)==[False,True]


def test_legacy_preview_without_source_summary_is_not_fabricated(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,preview,old=scene_and_preview(scene,monkeypatch)
    with Session(engine()) as db:
        row=db.get(Job,preview['preview_id']);data=copy.deepcopy(row.data)
        data.pop('source_summary');data.pop('renewed_from');row.data=data
        a=db.scalar(select(Audit).where(Audit.object_id==preview['preview_id'],Audit.action=='cloud.report_plan_preview.created_v1'))
        value=copy.deepcopy(a.data);value.pop('source_summary_hash');value.pop('renewed_from');a.data=value;db.commit()
    result=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/find',
        json={'original_draft_id':old['id']})
    assert result.status_code==200,result.text
    plan=result.json()['items'][0]
    assert plan['source_summary_verified'] is False and plan['coverage'] is None
