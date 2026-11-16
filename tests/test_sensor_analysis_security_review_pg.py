"""Independent, isolated PostgreSQL review of native analysis dispatch fences."""
import threading,time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Membership,User
from generic_agents import sensor_native_analysis as service
from tests.test_sensor_native_analysis_pg import setup_analysis,prepare,fake_provider,device

def _queued(client,account,device,monkeypatch):
    task,base,packet,model=setup_analysis(client,account,device,monkeypatch);preview=prepare(client,base,packet,model)
    body={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    saved=client.post(base+'/analyses/'+preview['preview_id']+'/send',json=body);assert saved.status_code==200,saved.text
    return task,base,body

def test_task_cancellation_cannot_commit_while_authorized_http_in_flight(client,account,device,monkeypatch):
    task,base,body=_queued(client,account,device,monkeypatch);entered=threading.Event();release=threading.Event();cancelled=threading.Event();calls=fake_provider(monkeypatch,entered=entered,release=release)
    def cancel():
        with Session(engine()) as db:
            row=db.scalar(select(Task).where(Task.id==task['id']).with_for_update());row.status='workflow_cancelled';db.commit();cancelled.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sent=pool.submit(service.tick,account[2]);assert entered.wait(10)
        later=pool.submit(cancel);time.sleep(.3);assert not cancelled.is_set();release.set();assert sent.result(15)==1;later.result(15)
    assert len(calls)==1 and cancelled.is_set()
    result=client.get(base+'/analyses/requests/'+body['request_id']);assert result.status_code==200 and result.json()['state']=='completed'
    assert service.tick(account[2])==0 and len(calls)==1

def test_membership_downgrade_cannot_commit_while_authorized_http_in_flight(client,account,device,monkeypatch):
    task,base,body=_queued(client,account,device,monkeypatch);entered=threading.Event();release=threading.Event();changed=threading.Event();calls=fake_provider(monkeypatch,entered=entered,release=release)
    def downgrade():
        with Session(engine()) as db:
            user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]).with_for_update());member.role='viewer';db.commit();changed.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sent=pool.submit(service.tick,account[2]);assert entered.wait(10)
        later=pool.submit(downgrade);time.sleep(.3);assert not changed.is_set();release.set();assert sent.result(15)==1;later.result(15)
    assert len(calls)==1 and changed.is_set()
    result=client.get(base+'/analyses/requests/'+body['request_id']);assert result.status_code==200 and result.json()['state']=='completed'
    assert service.tick(account[2])==0 and len(calls)==1

def test_revoked_source_policy_before_dispatch_prevents_cloud_send(client,account,device,monkeypatch):
    from generic_agents.models import Capability
    from generic_agents.contracts import digest
    task,base,body=_queued(client,account,device,monkeypatch);calls=fake_provider(monkeypatch)
    packet=client.get(base+'/packets').json()['packets'][0];source_id=packet['records'][0]['provenance']['source_id']
    with Session(engine()) as db:
        source=db.get(Capability,source_id);value={**source.data,'data_policy':{'cloud_allowed':False}};source.data=value;source.content_hash=digest(value);source.validated_hash=source.content_hash;db.commit()
    assert service.tick(account[2])==0 and calls==[]
    assert client.get(base+'/analyses/requests/'+body['request_id']).json()['state']=='blocked'

def test_model_replacement_or_role_downgrade_before_dispatch_prevents_cloud_send(client,account,device,monkeypatch):
    from generic_agents.models import Capability
    from generic_agents.contracts import digest
    task,base,body=_queued(client,account,device,monkeypatch);calls=fake_provider(monkeypatch)
    with Session(engine()) as db:
        from generic_agents.models import Job
        row=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.idempotency_key==service.send_key(body['request_id'])));cap=db.get(Capability,row.data['model_id']);value={**cap.data,'allowed_agent_roles':['perception']};cap.data=value;cap.content_hash=digest(value);cap.validated_hash=cap.content_hash;db.commit()
    assert service.tick(account[2])==0 and calls==[]
    assert client.get(base+'/analyses/requests/'+body['request_id']).json()['state']=='blocked'

def test_unknown_after_restart_requires_explicit_ack_on_a_second_browser(client,account,device,monkeypatch):
    from fastapi.testclient import TestClient
    task,base,body=_queued(client,account,device,monkeypatch);calls=fake_provider(monkeypatch,crash=True)
    assert service.tick(account[2])==1 and len(calls)==1;engine().dispose()
    assert service.tick(account[2])==0 and len(calls)==1
    assert client.get(base+'/analyses/requests/'+body['request_id']).json()['state']=='response_unknown'
    with TestClient(__import__('generic_agents.api',fromlist=['app']).app) as other:
        login=other.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        packet=other.get(base+'/packets').json()['packets'][0];model=other.get(base+'/analyses/context').json()['models'][0]['id']
        new=prepare(other,base,packet,model);assert new['requires_uncertainty_ack']
        request={'request_id':str(uuid4()),'preview_hash':new['preview_hash'],'consent':True};url=base+'/analyses/'+new['preview_id']+'/send'
        assert other.post(url,json=request).status_code==409
        assert len(calls)==1
        assert other.post(url,json={**request,'acknowledge_prior_uncertain':True}).status_code==200
        assert len(calls)==1

def test_revoked_actor_before_dispatch_never_calls_cloud(client,account,device,monkeypatch):
    task,base,body=_queued(client,account,device,monkeypatch);calls=fake_provider(monkeypatch)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]).with_for_update());member.role='viewer';db.commit()
    assert service.tick(account[2])==0 and calls==[]
    result=client.get(base+'/analyses/requests/'+body['request_id']);assert result.status_code==200 and result.json()['state']=='blocked'

def test_workspace_admin_can_read_another_owners_analysis_but_not_send_or_cancel(client,account,device,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.models import Workspace
    from generic_agents.api import app
    task,base,body=_queued(client,account,device,monkeypatch);calls=fake_provider(monkeypatch);assert service.tick(account[2])==1 and len(calls)==1
    analysis=client.get(base+'/analyses/requests/'+body['request_id']).json()['analysis'];name='manager_'+uuid4().hex[:12];password=uuid4().hex
    created=client.post('/api/users',json={'username':name,'password':password,'role':'admin'});assert created.status_code==201,created.text
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==name));other=Workspace(name='隔离工作区');db.add(other);db.flush();db.add(Membership(user_id=user.id,workspace_id=other.id,role='admin'));db.commit();other_ws=other.id
    with TestClient(app) as manager:
        login=manager.post('/api/login',json={'username':name,'password':password});assert login.status_code==200
        manager.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        history=manager.get(base+'/analyses/history');assert history.status_code==200 and history.json()['analyses'][0]['id']==analysis['id']
        detail=manager.get(base+'/analyses/'+analysis['id']);assert detail.status_code==200 and detail.json()['analysis']['id']==analysis['id']
        assert manager.get(base+'/analyses/requests/'+body['request_id']).status_code==403
        assert manager.post(base+'/analyses/requests/'+str(uuid4())+'/cancel',json={}).status_code==403
        manager.headers['X-Workspace-ID']=other_ws
        assert manager.get(base+'/analyses/history').status_code==404
        assert manager.get(base+'/analyses/'+analysis['id']).status_code==404

def test_two_workers_cannot_dispatch_same_request_twice(client,account,device,monkeypatch):
    task,base,body=_queued(client,account,device,monkeypatch);entered=threading.Event();release=threading.Event();calls=fake_provider(monkeypatch,entered=entered,release=release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(service.tick,account[2]);assert entered.wait(10)
        second=pool.submit(service.tick,account[2]);assert second.result(10)==0
        assert len(calls)==1;release.set();assert first.result(15)==1
    assert len(calls)==1 and client.get(base+'/analyses/requests/'+body['request_id']).json()['state']=='completed'
