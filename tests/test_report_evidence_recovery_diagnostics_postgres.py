"""Synthetic HTTP checks: diagnostics never start work or mutate original data."""
import copy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, SetupReportDraft
from generic_agents.worker import process_once
from generic_agents.report_evidence_worker_isolation import KIND
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import owned_disposable_database
from tests.test_report_evidence_delegated_wire_postgres import owned_capture_roots, start_delegated, install_http


def path(identity):
    return f'/api/setup/report-evidence-readings/{identity}/delegated-recovery-diagnostic'


def test_readonly_lease_and_allowance_facts_never_imply_sent(reading_scene, monkeypatch):
    scene=reading_scene
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch)
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);job=db.get(Job,created['job_id'])
        now=db.scalar(select(func.clock_timestamp()))
        job.status='running';job.lease_token=str(uuid4());job.lease_until=now-timedelta(seconds=1)
        db.commit();revision=row.version
        baseline=(digest(row.data),row.version,db.scalar(select(func.count()).select_from(CloudCall)))
    from sqlalchemy import text
    from generic_agents import report_evidence_recovery_diagnostics as diagnostic_module
    original=diagnostic_module.diagnostic
    def assert_readonly(db,*args):
        assert db.scalar(text('SHOW transaction_read_only'))=='on'
        return original(db,*args)
    monkeypatch.setattr(diagnostic_module,'diagnostic',assert_readonly)
    response=scene['client'].get(path(created['id']),params={'revision':revision})
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='private, no-store'
    v=response.json()
    assert v['lease']=='expired' and v['job_status']=='running'
    assert v['response']=='not_determined' and v['recovery']=='not_evaluated'
    assert v['allowance_time']=='within_time_window'
    assert v['can_resend'] is v['can_resume'] is v['can_apply'] is False
    assert v['diagnostic_hash']==digest({k:x for k,x in v.items() if k!='diagnostic_hash'})
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id'])
        assert (digest(row.data),row.version,db.scalar(select(func.count()).select_from(CloudCall)))==baseline
    assert scene['client'].get(path(created['id']),params={'revision':revision+1}).status_code==409


def test_allowance_expiration_requires_consistent_issuance(reading_scene, monkeypatch):
    from generic_agents.cloud_report_plan_allowance import plan_key
    scene=reading_scene
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch)
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);revision=row.version
        grant=db.scalar(select(Job).where(Job.idempotency_key==plan_key(created['job_id'])))
        issued=db.get(Audit,grant.data['audit_id']);now=db.scalar(select(func.clock_timestamp()))
        data=copy.deepcopy(grant.data);data['manifest']['not_before']=(now-timedelta(days=2)).isoformat()
        data['manifest']['expires_at']=(now-timedelta(days=1)).isoformat();data['manifest_hash']=digest(data['manifest'])
        issued.data={**issued.data,'manifest_hash':data['manifest_hash']};grant.data=data;db.commit()
    response=scene['client'].get(path(created['id']),params={'revision':revision})
    assert response.status_code==200,response.text
    assert response.json()['allowance_time']=='expired'
    with Session(engine()) as db:
        grant=db.scalar(select(Job).where(Job.idempotency_key==plan_key(created['job_id'])))
        issued=db.get(Audit,grant.data['audit_id']);issued.data={**issued.data,'manifest_hash':'0'*64};db.commit()
    response=scene['client'].get(path(created['id']),params={'revision':revision})
    assert response.status_code==200,response.text
    assert response.json()['allowance_time']=='unverified'


def test_saved_response_eligible_requires_existing_preflight_and_no_new_call(reading_scene,monkeypatch):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene=reading_scene
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch)
    requests=[];install_http(monkeypatch,requests)
    original=delegated.response_context;once={'fail':True}
    def interrupted(*args,**kwargs):
        if once['fail']:
            once['fail']=False
            raise ValueError('synthetic interruption after saved wire')
        return original(*args,**kwargs)
    monkeypatch.setattr(delegated,'response_context',interrupted)
    assert process_once(created['job_id'],kinds=[KIND])
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id']);revision=row.version
        assert row.status=='response_unknown'
        before=(digest(row.data),db.scalar(select(func.count()).select_from(CloudCall)))
    sent=len(requests)
    response=scene['client'].get(path(created['id']),params={'revision':revision})
    assert response.status_code==200,response.text
    assert response.json()['response']=='complete_unverified'
    assert response.json()['recovery']=='eligible'
    assert len(requests)==sent
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,created['id'])
        assert (digest(row.data),db.scalar(select(func.count()).select_from(CloudCall)))==before


def test_enabled_policy_denies_downgraded_member(reading_scene,monkeypatch):
    from tests.test_report_evidence_post_policy_postgres import _set_operator
    scene=reading_scene
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch)
    # Existing helpers exercise the actual FastAPI guard; policy is set through
    # the same endpoint an administrator uses, before changing the test role.
    client=scene['client']
    _set_operator(scene,policy=True)
    response=client.get(path(created['id']),params={'revision':created['revision']})
    assert response.status_code==403


def test_only_owner_can_read_and_logout_revokes_access(reading_scene,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import AuthSession,User,Workspace
    from generic_agents.security import password_hash
    scene=reading_scene
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
    _,_,created=start_delegated(scene,monkeypatch)
    password=uuid4().hex
    with Session(engine()) as db:
        same=User(username='diag_'+uuid4().hex,password_hash=password_hash(password))
        outside=User(username='diag_'+uuid4().hex,password_hash=password_hash(password))
        workspace=Workspace(name='合成其他工作区');db.add_all([same,outside,workspace]);db.flush()
        db.add_all([Membership(user_id=same.id,workspace_id=scene['p'].workspace_id,role='admin'),
                    Membership(user_id=outside.id,workspace_id=workspace.id,role='admin')])
        db.commit();same_name,outside_name,outside_workspace=same.username,outside.username,workspace.id
    for username,workspace_id in ((same_name,scene['p'].workspace_id),(outside_name,outside_workspace)):
        with TestClient(app) as other:
            login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
            other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':workspace_id})
            assert other.get(path(created['id']),params={'revision':created['revision']}).status_code==404
    with Session(engine()) as db:
        for auth in db.scalars(select(AuthSession).where(AuthSession.user_id==scene['p'].user_id)).all():auth.revoked=True
        db.commit()
    assert scene['client'].get(path(created['id']),params={'revision':created['revision']}).status_code==401
