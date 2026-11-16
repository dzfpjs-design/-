"""Formal workflow + real isolated Modbus + durable task evidence, no cloud."""
import json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job,CloudCall,Entity
from generic_agents import sensor_automation
from generic_agents.sensor_native_packet_api import router
from tests.test_sensor_automation_pg import device,policy
from tests.test_instance_workspace import prepared,enable,object_row,task_row

if not any(getattr(r,'path','')=='/api/sensor-native/tasks/{task_id}/context' for r in app.routes):app.include_router(router)

def setup_native(client,account,device,monkeypatch):
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release,index=1);task,_=task_row(client,release,obj)
    ip,port,_=device;monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    gid=client.get('/api/modbus/grants').json()['grants'][0]['id'];r=client.post('/api/modbus/connections',json=dict(request_id=str(uuid4()),name='本地合成传感器',grant_id=gid,function=3,address=1,value_type='uint16',scale=1,unit_label=''));assert r.status_code==200,r.text
    conn=r.json()['connection'];field=release['snapshot']['object_types'][1]['fields'][1]['key'];r=client.post('/api/modbus/connections/'+conn['id']+'/binding',json={'request_id':str(uuid4()),'version':conn['version'],'entity_id':obj['id'],'field':field});assert r.status_code==200,r.text
    policy(client,'modbus',conn,{'operator':'above','value':20});assert sensor_automation.tick(account[2])==1
    return task,obj

def test_formal_task_packet_survives_restart_and_keeps_full_source(client,account,device,monkeypatch):
    task,obj=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];res=client.get(base+'/context');assert res.status_code==200,res.text
    value=res.json();assert not value['cloud_analysis_supported'] and {r['kind'] for r in value['records']}=={'data','alert'}
    assert all(r['object_name']=='公开测试条目' for r in value['records'])
    selection=[{'kind':r['kind'],'id':r['id'],'record_hash':r['record_hash']} for r in value['records']];body={'request_id':str(uuid4()),'expected_task_version':value['task_version'],'selection':selection}
    response=client.post(base+'/packets',json=body);assert response.status_code==200,response.text
    packet=response.json()['packet'];assert len(packet['records'])==2 and '待核实' in packet['notice']
    assert all(r['provenance']['entity_id']==obj['id'] and r['provenance']['reading_id'] and r['provenance']['observation_id'] for r in packet['records'])
    assert client.post(base+'/packets',json=body).json()['packet']['id']==packet['id']
    assert client.post(base+'/packets',json={**body,'selection':selection[:1]}).status_code==409
    engine().dispose();assert client.get(base+'/requests/'+body['request_id']).json()['packet']['id']==packet['id']
    assert client.get(base+'/context').json()['packets'][0]['id']==packet['id']
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all();entity=db.get(Entity,obj['id']);assert entity.version==1

def test_stopped_request_cannot_later_create_packet(client,account,device,monkeypatch):
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();rid=str(uuid4());path=base+'/requests/'+rid
    assert client.post(path+'/cancel',json={}).json()['state']=='cancelled'
    body={'request_id':rid,'expected_task_version':value['task_version'],'selection':[{'kind':value['records'][0]['kind'],'id':value['records'][0]['id'],'record_hash':value['records'][0]['record_hash']}]}
    assert client.post(base+'/packets',json=body).status_code==409 and client.get(path).json()['state']=='cancelled'

def test_evidence_change_rejected_and_original_task_preserved(client,account,device,monkeypatch):
    task,obj=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();row=next(r for r in value['records'] if r['kind']=='data')
    with Session(engine()) as db:
        reading=db.get(Job,row['provenance']['reading_id']);reading.data={**reading.data,'value':999};db.commit()
    body={'request_id':str(uuid4()),'expected_task_version':value['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]}
    assert client.post(base+'/packets',json=body).status_code==409
    assert client.get(base+'/requests/'+body['request_id']).json()['state']=='not_found'
    with Session(engine()) as db:assert db.get(Entity,obj['id']).version==1

def test_same_workspace_other_operator_cannot_see_or_create(client,account,device,monkeypatch):
    from fastapi.testclient import TestClient
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();row=value['records'][0]
    username='other_'+uuid4().hex[:10];password=uuid4().hex
    response=client.post('/api/users',json={'username':username,'password':password,'role':'operator'});assert response.status_code==201,response.text
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200,login.text
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.get(base+'/context').status_code==403
        body={'request_id':str(uuid4()),'expected_task_version':value['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]}
        assert other.post(base+'/packets',json=body).status_code==403

def test_concurrent_save_and_stop_have_one_durable_outcome(client,account,device,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();row=value['records'][0];rid=str(uuid4());path=base+'/requests/'+rid
    body={'request_id':rid,'expected_task_version':value['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]};barrier=threading.Barrier(2)
    def save():barrier.wait();return client.post(base+'/packets',json=body)
    def stop():barrier.wait();return client.post(path+'/cancel',json={})
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1=pool.submit(save);f2=pool.submit(stop);saved=f1.result(15);stopped=f2.result(15)
    assert stopped.status_code==200 and saved.status_code in (200,409)
    state=client.get(path).json();assert state['state'] in ('saved','cancelled')
    if state['state']=='saved':assert saved.status_code==200 and stopped.json()['packet']['id']==state['packet']['id']
    else:assert saved.status_code==409 and stopped.json()['state']=='cancelled'
    with Session(engine()) as db:assert len(list(db.scalars(select(Job).where(Job.workspace_id==account[2],Job.idempotency_key=='sensor-native-packet:'+rid))))==1

def test_packet_task_lock_serializes_task_cancellation(client,account,device,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents.models import Task
    from generic_agents import sensor_agent_context
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();row=value['records'][0]
    body={'request_id':str(uuid4()),'expected_task_version':value['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]}
    entered=threading.Event();release=threading.Event();cancelled=threading.Event();original=sensor_agent_context._read
    def read(*args,**kwargs):
        entered.set();assert release.wait(8);return original(*args,**kwargs)
    monkeypatch.setattr(sensor_agent_context,'_read',read)
    def cancel():
        with Session(engine()) as db:
            target=db.scalar(select(Task).where(Task.id==task['id']).with_for_update());target.status='workflow_cancelled';db.commit();cancelled.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved=pool.submit(lambda:client.post(base+'/packets',json=body));assert entered.wait(8)
        cancelling=pool.submit(cancel);time.sleep(.25);assert not cancelled.is_set();release.set()
        result=saved.result(15);cancelling.result(15)
    assert result.status_code==200 and cancelled.is_set()
    assert client.get(base+'/requests/'+body['request_id']).json()['state']=='saved'

def test_packet_history_pages_and_survives_invalid_current_release(client,account,device,monkeypatch):
    import copy
    from generic_agents.models import InstanceRelease
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();r=value['records'][0];body={'request_id':str(uuid4()),'expected_task_version':value['task_version'],'selection':[{'kind':r['kind'],'id':r['id'],'record_hash':r['record_hash']}]}
    saved=client.post(base+'/packets',json=body);assert saved.status_code==200,saved.text;packet=saved.json()['packet']
    with Session(engine()) as db:
        original=db.get(Job,packet['id'])
        for index in range(51):db.add(Job(workspace_id=account[2],kind=original.kind,status='internal',idempotency_key='synthetic-history:'+str(index),data=copy.deepcopy(original.data)))
        release=db.get(InstanceRelease,task['instance_release_id']);release.configuration_hash='0'*64;db.commit()
    assert client.get(base+'/context').status_code==409
    page1=client.get(base+'/packets?page=1');page2=client.get(base+'/packets?page=2')
    assert page1.status_code==200 and page1.json()['has_more'] and len(page1.json()['packets'])==50
    assert page2.status_code==200 and not page2.json()['has_more'] and len(page2.json()['packets'])==2
    assert client.get(base+'/packets/'+packet['id']).json()['packet']['id']==packet['id']
    assert client.get(base+'/requests/'+body['request_id']).json()['state']=='saved'

def test_demoted_owner_can_recover_or_stop_original_request(client,account,device,monkeypatch):
    from generic_agents.models import Membership,User
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];value=client.get(base+'/context').json();r=value['records'][0];rid=str(uuid4());body={'request_id':rid,'expected_task_version':value['task_version'],'selection':[{'kind':r['kind'],'id':r['id'],'record_hash':r['record_hash']}]}
    saved=client.post(base+'/packets',json=body);assert saved.status_code==200,saved.text
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    assert client.get(base+'/context').status_code==403
    assert client.get(base+'/requests/'+rid).json()['state']=='saved'
    assert client.get(base+'/packets').json()['packets'][0]['id']==saved.json()['packet']['id']
    other=str(uuid4());assert client.post(base+'/requests/'+other+'/cancel',json={}).json()['state']=='cancelled'
    assert client.post(base+'/packets',json={**body,'request_id':other}).status_code==403

def test_other_workspace_admin_may_read_packet_only_within_current_workspace(client,account,device,monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.models import User,Workspace,Membership
    task,_=setup_native(client,account,device,monkeypatch);base='/api/sensor-native/tasks/'+task['id'];context=client.get(base+'/context').json();r=context['records'][0]
    rid=str(uuid4());body={'request_id':rid,'expected_task_version':context['task_version'],'selection':[{'kind':r['kind'],'id':r['id'],'record_hash':r['record_hash']}]}
    original=client.post(base+'/packets',json=body);assert original.status_code==200,original.text;packet=original.json()['packet']
    name='manager_'+uuid4().hex[:10];password=uuid4().hex
    created=client.post('/api/users',json={'username':name,'password':password,'role':'admin'});assert created.status_code==201,created.text
    with Session(engine()) as db:
        manager=db.scalar(select(User).where(User.username==name));elsewhere=Workspace(name='隔离测试另一工作区');db.add(elsewhere);db.flush();db.add(Membership(user_id=manager.id,workspace_id=elsewhere.id,role='admin'));db.commit();other_ws=elsewhere.id
    with TestClient(app) as manager:
        login=manager.post('/api/login',json={'username':name,'password':password});assert login.status_code==200
        manager.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        context=manager.get(base+'/context');assert context.status_code==200 and context.json()['read_only'] is True and context.json()['records']==[]
        history=manager.get(base+'/packets');assert history.status_code==200 and [x['id'] for x in history.json()['packets']]==[packet['id']]
        assert manager.get(base+'/packets/'+packet['id']).json()['packet']['id']==packet['id']
        assert manager.post(base+'/packets',json={**body,'request_id':str(uuid4())}).status_code==403
        assert manager.get(base+'/requests/'+rid).status_code==403
        assert manager.post(base+'/requests/'+str(uuid4())+'/cancel',json={}).status_code==403
        manager.headers['X-Workspace-ID']=other_ws
        assert manager.get(base+'/packets').status_code==404
        assert manager.get(base+'/packets/'+packet['id']).status_code==404
