"""Synthetic isolated-PG HTTP checks for recovering an activation ID."""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, SetupDraft, User, Workspace
from generic_agents.security import password_hash
from generic_agents import report_evidence_scoped_activation as activation
from generic_agents import report_evidence_delegated_budget_start as budget
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview

PATH = '/api/setup/report-evidence-delegated/activation/status'


def check_response(response, p, preview_id, status, activation_id=None):
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['cache-control']
    value = response.json()
    assert value['status_hash'] == digest({k: v for k, v in value.items() if k != 'status_hash'})
    assert value['schema'] == 'report-scoped-activation-status-v1'
    assert (value['actor_id'], value['workspace_id'], value['preview_id']) == (p.user_id, p.workspace_id, preview_id)
    assert (value['status'], value['activation_id']) == (status, activation_id)
    assert value['execution_history'] == 'unknown' and value['this_action_calls_model'] is False
    assert value['user_confirmed_business'] is False and value['in_flight_may_continue'] is True
    return value


def test_status_is_durable_across_issue_start_revoke_and_never_calls_model(reading_scene, monkeypatch):
    scene = reading_scene
    p, request, command, preview, _ = scene_and_preview(scene, monkeypatch)
    pid = preview['preview_id']; client = scene['client']
    with Session(engine()) as db:
        before = db.scalar(select(func.count()).select_from(CloudCall))
    result = check_response(client.post(PATH, json={'preview_id':pid}), p, pid, 'not_found')
    assert result['expires_at'] is None and result['plan_status'] == 'saved'
    with Session(engine()) as held:
        held.scalar(select(SetupDraft).where(SetupDraft.workspace_id==p.workspace_id).with_for_update())
        busy=client.post(PATH,json={'preview_id':pid})
        assert busy.status_code==409 and busy.json()['detail']['code']=='report_scope_busy'
        held.rollback()
    created = client.post('/api/setup/report-evidence-delegated/activation', json={'preview_id':pid,'consent':True})
    assert created.status_code == 201, created.text
    aid = created.json()['activation_id']
    active = check_response(client.post(PATH,json={'preview_id':pid}), p, pid, 'active', aid)
    assert active['expires_at'] and active['plan_status'] == 'saved'
    with Session(engine(), expire_on_commit=False) as db:
        budget.start_tx(db,p,request,command,preview_id=pid,allowance_preview=preview['allowance']); db.commit()
    started = check_response(client.post(PATH,json={'preview_id':pid}), p, pid, 'active', aid)
    assert started['plan_status'] == 'started'
    assert client.post('/api/setup/report-evidence-delegated/activation/'+aid+'/revoke').status_code == 200
    revoked = check_response(client.post(PATH,json={'preview_id':pid}), p, pid, 'revoked', aid)
    assert revoked['plan_status'] == 'started' and revoked['expires_at'] == active['expires_at']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before


def test_expiry_and_corrupt_activation_or_audit_never_report_absent(reading_scene,monkeypatch):
    scene=reading_scene; p,_,_,preview,_=scene_and_preview(scene,monkeypatch); pid=preview['preview_id']
    issued=scene['client'].post('/api/setup/report-evidence-delegated/activation',json={'preview_id':pid,'consent':True}).json()
    aid=issued['activation_id']
    with Session(engine()) as db:
        row=db.get(Job,aid); data=copy.deepcopy(row.data); now=db.scalar(select(func.clock_timestamp()))
        data['manifest']['issued_at']=(now-timedelta(hours=2)).isoformat()
        data['manifest']['expires_at']=(now-timedelta(hours=1)).isoformat()
        data['manifest_hash']=digest(data['manifest']); row.data=data
        audit=db.get(Audit,data['issue_audit_id']); audit.data={**audit.data,'manifest_hash':data['manifest_hash']};db.commit()
    expired=check_response(scene['client'].post(PATH,json={'preview_id':pid}),p,pid,'expired',aid)
    assert expired['expires_at']
    with Session(engine()) as db:
        preview_audit=db.scalar(select(Audit).where(Audit.object_id==pid,
                         Audit.action=='cloud.report_plan_preview.created_v1'))
        saved=copy.deepcopy(preview_audit.data);preview_audit.data={**saved,'allowance_hash':'0'*64};db.commit()
    invalid=scene['client'].post(PATH,json={'preview_id':pid})
    assert invalid.status_code==409 and 'not_found' not in invalid.text
    with Session(engine()) as db:
        preview_audit=db.scalar(select(Audit).where(Audit.object_id==pid,
                         Audit.action=='cloud.report_plan_preview.created_v1'))
        preview_audit.data=saved
        row=db.get(Job,aid); audit=db.get(Audit,row.data['issue_audit_id']); db.delete(audit);db.commit()
    response=scene['client'].post(PATH,json={'preview_id':pid})
    assert response.status_code==409 and 'not_found' not in response.text


@pytest.mark.parametrize('damaged_reference', [False, True])
def test_damaged_key_and_kind_with_surviving_reference_fails_closed(reading_scene,monkeypatch,damaged_reference):
    scene=reading_scene;p,_,_,preview,_=scene_and_preview(scene,monkeypatch);pid=preview['preview_id']
    response=scene['client'].post('/api/setup/report-evidence-delegated/activation',json={'preview_id':pid,'consent':True})
    assert response.status_code==201,response.text
    aid=response.json()['activation_id']
    with Session(engine()) as db:
        row=db.get(Job,aid);row.kind='corrupt_kind';row.idempotency_key='altered-'+uuid4().hex
        if damaged_reference:
            data=copy.deepcopy(row.data);data['manifest']['preview_id']=str(uuid4());row.data=data
        db.commit()
    response=scene['client'].post(PATH,json={'preview_id':pid})
    assert response.status_code==409 and 'not_found' not in response.text


def test_same_workspace_other_admin_and_operator_cannot_view(reading_scene,monkeypatch):
    scene=reading_scene;p,_,_,preview,_=scene_and_preview(scene,monkeypatch);pid=preview['preview_id']
    aid=scene['client'].post('/api/setup/report-evidence-delegated/activation',json={'preview_id':pid,'consent':True}).json()['activation_id']
    username='other_'+uuid4().hex;password=uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=p.workspace_id,role='admin'));db.commit()
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':p.workspace_id})
        response=other.post(PATH,json={'preview_id':pid})
        assert response.status_code in (403,404) and aid not in response.text
    with Session(engine()) as db:
        existing=db.scalar(select(SetupDraft).where(SetupDraft.workspace_id==p.workspace_id))
        workspace=Workspace(name='synthetic_other_workspace');db.add(workspace);db.flush()
        db.add(Membership(user_id=p.user_id,workspace_id=workspace.id,role='admin'))
        db.add(SetupDraft(workspace_id=workspace.id,data=copy.deepcopy(existing.data)))
        other_workspace=workspace.id;db.commit()
    scene['client'].headers['X-Workspace-ID']=other_workspace
    other_space=scene['client'].post(PATH,json={'preview_id':pid})
    assert other_space.status_code in (403,404) and aid not in other_space.text
    scene['client'].headers['X-Workspace-ID']=p.workspace_id
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='operator';db.commit()
    denied=scene['client'].post(PATH,json={'preview_id':pid})
    assert denied.status_code==403 and aid not in denied.text
