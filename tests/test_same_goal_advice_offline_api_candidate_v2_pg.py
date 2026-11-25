"""Real HTTP sessions/CSRF against disposable PG; source and Goal are fixtures."""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, User, Workspace
from tests.test_same_goal_advice_offline_grant_candidate_v2_pg import packet_ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from generic_agents import same_goal_advice_offline_grant_candidate_v2 as grants


def path(s):return '/api/workspace/tasks/'+s['task_id']+'/business-advice/offline-delegations'


def test_default_off_and_cookie_csrf(packet_ready,client,monkeypatch):
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',False)
    assert client.get(path(s)).json()=={'enabled':False,'items':[]}
    assert client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())}).status_code==404
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    csrf=client.headers.pop('X-CSRF-Token')
    try:assert client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())}).status_code==403
    finally:client.headers['X-CSRF-Token']=csrf
    with Session(engine()) as db:assert not list(db.scalars(select(Audit).where(Audit.action==grants.GRANT)))


def test_authorize_refresh_logout_relogin_revoke(packet_ready,client,account,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    s,created=packet_ready;monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    response=client.get(path(s));assert response.status_code==200,response.text
    assert response.json()['items'][0]['state']=='not_authorized'
    body={'request_id':str(uuid4())};url=path(s)+'/'+created['job_id']+'/grant'
    first=client.post(url,json=body);assert first.status_code==200,first.text
    grant=first.json()['grant_id'];assert client.post(url,json=body).json()['grant_id']==grant
    engine().dispose()
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        view=fresh.get(path(s));assert view.status_code==200,view.text
        item=view.json()['items'][0];assert item['state']=='authorized' and item['background_available'] is False
        assert '原文' not in view.text and '合成已批准资料包' not in view.text
        revoke=fresh.post(path(s)+'/'+created['job_id']+'/grants/'+grant+'/revoke',json={'request_id':str(uuid4()),'reason':'资料岗位撤销授权'})
        assert revoke.status_code==200,revoke.text
        assert fresh.get(path(s)).json()['items'][0]['state']=='revoked'
    engine().dispose()
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Audit).where(Audit.action==grants.GRANT))))==1
        assert len(list(db.scalars(select(Audit).where(Audit.action==grants.REVOKE))))==1
        assert not list(db.scalars(select(CloudCall)))


def test_role_revocation_and_workspace_isolation(packet_ready,client,account,monkeypatch):
    s,created=packet_ready;monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    grant=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert grant.status_code==200,grant.text
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        other=Workspace(name='隔离测试工作区');db.add(other);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=other.id,role='admin'));other_id=other.id;db.commit()
    client.headers['X-Workspace-ID']=other_id
    assert client.get(path(s)).status_code in {403,404,409}
    client.headers['X-Workspace-ID']=account[2]
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id==s['principal'].user_id))
        member.role='viewer';db.commit()
    response=client.post(path(s)+'/'+created['job_id']+'/grants/'+grant.json()['grant_id']+'/revoke',json={'request_id':str(uuid4()),'reason':'撤销'})
    assert response.status_code in {403,409}
    assert client.get(path(s)).status_code in {403,409}


def test_admin_sees_paused_data_role_and_can_revoke(packet_ready,client,account,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.security import password_hash
    s,created=packet_ready;monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200,issued.text
    username='synthetic-admin-'+uuid4().hex[:10];password='synthetic-password-123'
    with Session(engine()) as db:
        owner=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.user_id==s['principal'].user_id))
        owner.role='viewer';admin=User(username=username,password_hash=password_hash(password));db.add(admin);db.flush()
        db.add(Membership(user_id=admin.id,workspace_id=account[2],role='admin'));db.commit()
    with TestClient(app) as admin_client:
        logged=admin_client.post('/api/login',json={'username':username,'password':password});assert logged.status_code==200
        admin_client.headers.update({'X-CSRF-Token':logged.json()['csrf_token'],'X-Workspace-ID':account[2]})
        visible=admin_client.get(path(s));assert visible.status_code==200,visible.text
        item=visible.json()['items'][0];assert item['state']=='role_unavailable' and item['can_revoke'] is True
        response=admin_client.post(path(s)+'/'+created['job_id']+'/grants/'+issued.json()['grant_id']+'/revoke',
            json={'request_id':str(uuid4()),'reason':'原资料岗位不再负责'})
        assert response.status_code==200,response.text
        assert admin_client.get(path(s)).json()['items'][0]['state']=='revoked'


def test_saved_grant_can_be_consumed_by_existing_isolated_worker(packet_ready,client,monkeypatch):
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    s,created=packet_ready;monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200,issued.text
    assert runner.tick()['completed']==1
    engine().dispose()
    response=client.get(path(s));assert response.status_code==200,response.text
    item=response.json()['items'][0]
    assert item['prepared'] is True and item['background_available'] is True and item['work_state']=='complete'
    runner.stop_heartbeat()
    assert client.get(path(s)).json()['items'][0]['background_available'] is False
    revoked=client.post(path(s)+'/'+created['job_id']+'/grants/'+issued.json()['grant_id']+'/revoke',
        json={'request_id':str(uuid4()),'reason':'不再授权后台整理'})
    assert revoked.status_code==200,revoked.text
    assert client.get(path(s)).json()['items'][0]['state']=='revoked'


def test_worker_default_closed_and_revoked_role_never_processes(packet_ready,client,monkeypatch):
    import pytest
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',False)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',False)
    with pytest.raises(RuntimeError):runner.tick()
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==s['principal'].workspace_id,
            Membership.user_id==s['principal'].user_id));membership.role='viewer';db.commit()
    result=runner.tick();assert result['completed']==0
    with Session(engine()) as db:
        ctl=db.scalar(select(Job).where(Job.kind==control.KIND));assert ctl.status=='queued' and ctl.attempts==0
        assert not list(db.scalars(select(CloudCall)))


def test_worker_failure_has_safe_visible_status(packet_ready,client,monkeypatch):
    from fastapi import HTTPException
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200
    def fail(*args,**kwargs):raise HTTPException(409,'SYNTHETIC_SECRET_NOT_FOR_AUDIT')
    monkeypatch.setattr(grants,'process_packet',fail)
    assert runner.tick()['completed']==0
    view=client.get(path(s));assert view.status_code==200,view.text
    item=view.json()['items'][0];assert item['work_state']=='needs_attention' and item['background_available'] is True
    assert item['failure_kind']=='access_or_source'
    assert 'SYNTHETIC_SECRET_NOT_FOR_AUDIT' not in view.text
    with Session(engine()) as db:
        errors=list(db.scalars(select(Audit).where(Audit.action==runner.FAILURE)))
        assert len(errors)==1 and 'SYNTHETIC_SECRET_NOT_FOR_AUDIT' not in str(errors[0].data)


def test_long_worker_heartbeat_tracks_lease_without_renewing(packet_ready,client,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
    from generic_agents.contracts import digest
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    monkeypatch.setattr(runner,'HEARTBEAT_INTERVAL_SECONDS',0.05)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200
    entered=threading.Event();release=threading.Event();original=grants.process_packet
    def slow(*args,**kwargs):
        entered.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(grants,'process_packet',slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(runner.tick)
        try:
            assert entered.wait(5)
            with Session(engine()) as db:
                ctl=db.scalar(select(Job).where(Job.kind==control.KIND));before=ctl.lease_until;token=ctl.lease_token
            time.sleep(0.3)
            with Session(engine()) as db:
                ctl=db.scalar(select(Job).where(Job.kind==control.KIND));heart=db.scalar(select(Job).where(Job.idempotency_key==runner.HEARTBEAT_KEY))
                assert ctl.lease_until==before and ctl.lease_token==token
                assert heart.data['state']=='processing' and heart.data['control_id']==ctl.id
                assert heart.data['lease_hash']==digest(token) and heart.data['worker_id']==runner.WORKER_ID
                assert heart.data['lease_until']==before.isoformat()
            view=client.get(path(s));assert view.status_code==200 and view.json()['items'][0]['background_available'] is True
        finally:release.set()
        assert future.result(timeout=20)['completed']==1
    runner.stop_heartbeat()
    assert client.get(path(s)).json()['items'][0]['background_available'] is False


def test_worker_process_sigterm_stops_its_heartbeat(packet_ready,client,monkeypatch):
    import os,subprocess,sys,time
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==s['principal'].workspace_id,
            Membership.user_id==s['principal'].user_id));member.role='viewer';db.commit()
    env=os.environ.copy();env.update(GA_OFFLINE_DOCUMENT_DELEGATION_ENABLED='true',GA_OFFLINE_DOCUMENT_WORKER_ENABLED='true',
        GA_STORAGE_DIR=str(settings().storage_dir))
    process=subprocess.Popen([sys.executable,'-m','generic_agents.same_goal_advice_offline_worker_candidate_v2'],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+20;found=False
        while time.monotonic()<deadline and process.poll() is None:
            with Session(engine()) as db:
                rows=list(db.scalars(select(Job).where(Job.kind==runner.HEARTBEAT_KIND)))
                found=any(row.data.get('pid')==process.pid for row in rows)
            if found:break
            time.sleep(0.1)
        assert found,'worker failed to publish heartbeat'
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    assert process.returncode==0
    with Session(engine()) as db:
        row=next(row for row in db.scalars(select(Job).where(Job.kind==runner.HEARTBEAT_KIND)) if row.data.get('pid')==process.pid)
        assert row.data['state']=='stopped'
        assert not list(db.scalars(select(CloudCall)))


def test_worker_does_not_claim_legacy_or_other_queues(packet_ready,monkeypatch):
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    from generic_agents import same_goal_advice_contracts_v302 as legacy
    s,_=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    with Session(engine()) as db:
        old=Job(workspace_id=s['principal'].workspace_id,kind=legacy.SEND_KIND,status='queued',attempts=0,
            idempotency_key='synthetic-legacy-'+uuid4().hex,data={'synthetic':True})
        db.add(old);db.commit();identity=old.id
    assert runner.tick()['completed']==0
    with Session(engine()) as db:
        unchanged=db.get(Job,identity);assert unchanged.status=='queued' and unchanged.attempts==0


def test_blocked_controls_do_not_starve_new_authorized_work(packet_ready,client,monkeypatch):
    from datetime import timedelta
    from generic_agents import same_goal_advice_offline_worker_candidate_v2 as runner
    from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
    s,created=packet_ready
    monkeypatch.setattr(settings(),'offline_document_delegation_enabled',True)
    monkeypatch.setattr(settings(),'offline_document_worker_enabled',True)
    monkeypatch.setattr(runner,'_CURSOR',None)
    with Session(engine()) as db:
        from sqlalchemy import func
        before=db.scalar(select(func.clock_timestamp()))-timedelta(minutes=1)
        db.add_all([Job(workspace_id=s['principal'].workspace_id,kind=control.KIND,status='queued',attempts=0,
            idempotency_key='synthetic-blocked-'+uuid4().hex,created_at=before,
            data={'actor_id':s['principal'].user_id,'target':{'plan_id':str(uuid4()),'job_id':str(uuid4())}})
            for _ in range(51)]);db.commit()
    issued=client.post(path(s)+'/'+created['job_id']+'/grant',json={'request_id':str(uuid4())});assert issued.status_code==200
    assert runner.tick()['completed']==0
    assert runner.tick()['completed']==1
    assert client.get(path(s)).json()['items'][0]['prepared'] is True
    runner.stop_heartbeat()
