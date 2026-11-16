"""DJI read-only onboarding to an already-authorized broker; no device commands."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.dji_cloud_api import router, read_authorized_dock_status
from generic_agents.models import Job, Membership, MqttGateway, MqttReceipt, User, Workspace
from generic_agents.mqtt_inbox import capture
from generic_agents.security import Principal
from tests.test_mqtt_protocol import broker, reader, publish, ready_line, finish
from generic_agents.managed_mqtt_grants import router as managed_router

if not any(getattr(r,'path',None)=='/api/dji/cloud/connections' for r in app.routes): app.include_router(router)
if not any(getattr(r,'path',None)=='/api/mqtt/authorizations' for r in app.routes): app.include_router(managed_router)


def body(origin, **kw):
    return {'request_id': str(uuid4()), 'name': '隔离状态源', 'broker': origin,
            'device_sn': 'SIMDOCK123', 'username_ref': '', 'password_ref': '', **kw}


def test_registration_recovery_cancel_scope_and_role_downgrade(client,account,monkeypatch):
    origin = 'mqtt://127.0.0.1:18833'; monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', origin)
    url='/api/dji/cloud'; b=body(origin)
    options=client.get(url+'/options');assert options.status_code==200 and options.json()['brokers']==[{'value':origin,'label':origin,'broker':origin,'authorization_id':None}]
    assert client.post(url+'/connections',json={**b,'broker':'mqtt://127.0.0.1:12345'}).status_code==403
    created=client.post(url+'/connections',json=b);assert created.status_code==200,created.text
    out=created.json();g=out['connection'];assert g['status']=='paused' and g['topic']=='thing/product/SIMDOCK123/osd'
    assert client.post(url+'/connections',json=b).json()==out
    assert client.post(url+'/connections',json={**b,'name':'不同输入'}).status_code==409
    assert client.get(url+'/operations/'+b['request_id']).json()==out
    assert client.post(url+'/operations/'+b['request_id']+'/cancel').json()==out
    with Session(engine()) as db:
        row=db.scalar(select(MqttGateway).where(MqttGateway.id==g['id']));assert list(row.data['topics'])==['thing/product/SIMDOCK123/osd']
        assert not any('status' in topic or 'topo' in topic for topic in row.data['topics'])
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    assert client.get(url+'/operations/'+b['request_id']).json()==out
    pending=body(origin);stop=client.post(url+'/operations/'+pending['request_id']+'/cancel');assert stop.json()['state']=='cancelled'
    assert client.post(url+'/connections',json=pending).status_code==403
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='admin';db.commit()
    assert client.post(url+'/connections',json=pending).json()['state']=='cancelled'
    other=Workspace(name='隔离另一工作区')
    with Session(engine()) as db:db.add(other);db.commit();other_id=other.id
    assert client.get(url+'/connections/'+g['id']+'/status',headers={'X-Workspace-ID':other_id}).status_code==403
    listing=client.get(url+'/connections').json();assert listing['page']==1 and len(listing['connections'])==1


def test_real_mqtt_osd_and_pause_survive_backend_restart(client,account,broker,monkeypatch):
    origin=f'mqtt://127.0.0.1:{broker}';monkeypatch.setattr(settings(),'mqtt_allowed_brokers',origin)
    base='/api/dji/cloud';b=body(origin);created=client.post(base+'/connections',json=b).json();g=created['connection']
    change={'request_id':str(uuid4()),'expected_version':g['version'],'status':'active'}
    enabled=client.post(base+'/connections/'+g['id']+'/status',json=change);assert enabled.status_code==200,enabled.text
    assert client.post(base+'/connections/'+g['id']+'/status',json=change).json()==enabled.json()
    enabled_version=enabled.json()['connection']['version'];raw=json.dumps({'timestamp':1760000000123,'data':{'mode_code':4,'wind_speed':3.2,'drone_charge_state':{'capacity_percent':75}}}).encode()
    process=reader(g['id'],origin,1)
    try:ready_line(process,'SUBSCRIBED');publish(broker,[(g['topic'],raw)]);assert finish(process)['received']==1
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
    view=client.get(base+'/connections/'+g['id']+'/status');assert view.status_code==200,view.text
    result=view.json();assert result['latest']['parsed'] and result['latest']['reported']['battery_percent']==75
    assert result['receiver']['state']!='receiving' or result['receiver']['heartbeat_fresh']
    with Session(engine()) as db:
        receipt=db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id==g['id']));assert receipt.input_sha256==sha256(raw).hexdigest()
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','fixture')
        evidence=read_authorized_dock_status(db,p,g['id'],'SIMDOCK123')
        assert evidence['latest']['evidence_id']==receipt.evidence_id
        assert evidence['latest']['reported']['battery_percent']==75 and evidence['connection']['status']=='active'
        with pytest.raises(HTTPException) as denied:read_authorized_dock_status(db,p,g['id'],'DIFFERENT')
        assert denied.value.status_code==403
    tool_body={'request_id':str(uuid4()),'expected_version':enabled_version,'confirm':True}
    published=client.post(base+'/connections/'+g['id']+'/capability',json=tool_body)
    assert published.status_code==200,published.text
    assert published.json()['capability']['status']=='published'
    assert client.post(base+'/connections/'+g['id']+'/capability',json=tool_body).json()==published.json()
    tool_id=published.json()['capability']['id']
    assert client.get(base+'/connections/'+g['id']+'/status').json()['connection']['capability']['id']==tool_id
    from generic_agents.dji_cloud_tool import manifest
    fake=manifest(g['id'],'SIMDOCK123',enabled.json()['connection']['configuration_hash']).model_dump(mode='json')
    fake['execution']['side_effect']=True
    assert client.post('/api/capabilities',json=fake).status_code==422
    from generic_agents.gateway import invoke
    from generic_agents.native_planning_grants import pin
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','fixture')
        granted=pin(db,p,[{'capability_id':tool_id,'allow_external':False}])
        assert granted[0]['capability_id']==tool_id and granted[0]['external'] is False
        with pytest.raises(HTTPException):invoke(db,p,tool_id,'perception',{'device_sn':'OTHER'},'dji-tool-input:'+str(uuid4()))
        invocation=invoke(db,p,tool_id,'perception',{},'dji-tool-test:'+str(uuid4()))
        assert invocation.status=='succeeded',invocation.data
        assert invocation.data['output']['latest']['reported']['battery_percent']==75
    task_id,plan_id=_native_task_journey(client,account,monkeypatch,tool_id,result['latest']['evidence_id'])
    pause={'request_id':str(uuid4()),'expected_version':enabled_version,'status':'paused'}
    assert client.post(base+'/connections/'+g['id']+'/status',json=pause).json()['connection']['status']=='paused'
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','fixture')
        with pytest.raises(HTTPException):capture(db,p,g['id'],enabled_version,g['topic'],raw,1)
        with pytest.raises(HTTPException) as denied:read_authorized_dock_status(db,p,g['id'],'SIMDOCK123')
        assert denied.value.status_code==409
        invocation=invoke(db,p,tool_id,'perception',{},'dji-tool-paused:'+str(uuid4()))
        assert invocation.status=='blocked_evidence'
    with TestClient(app) as restarted:
        login=restarted.post('/api/login',json={'username':account[0],'password':account[1]}).json()
        restarted.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':account[2]})
        again=restarted.get(base+'/connections/'+g['id']+'/status').json()
        assert again['connection']['status']=='paused' and again['latest']['receipt_id']==result['latest']['receipt_id']


def test_managed_broker_binding_cannot_outlive_revoked_authorization(client,account,monkeypatch,tmp_path):
    from cryptography.fernet import Fernet
    key=tmp_path/'file.key';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(key))
    monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1')
    monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/24')
    body={'request_id':str(uuid4()),'name':'隔离 TLS 授权','host':'192.168.0.81','port':8883,
          'username':'synthetic-user','password':'synthetic-password'}
    response=client.post('/api/mqtt/authorizations',json=body);assert response.status_code==200,response.text
    authorization=response.json()['authorization'];grant_id=authorization['id']
    assert 'synthetic-password' not in response.text
    choices=client.get('/api/dji/cloud/options').json()['brokers']
    assert any(x['authorization_id']==grant_id and x['broker']=='mqtts://192.168.0.81:8883' for x in choices)
    request={'request_id':str(uuid4()),'name':'合成设备','device_sn':'SIMULATED','authorization_id':grant_id}
    created=client.post('/api/dji/cloud/connections',json=request);assert created.status_code==200,created.text
    g=created.json()['connection'];assert g['broker']=='mqtts://192.168.0.81:8883' and g['authorization_available']
    mixed={**request,'request_id':str(uuid4()),'broker':'mqtts://192.168.0.82:8883'}
    assert client.post('/api/dji/cloud/connections',json=mixed).status_code==422
    active=client.post('/api/dji/cloud/connections/'+g['id']+'/status',json={'request_id':str(uuid4()),'expected_version':g['version'],'status':'active'})
    assert active.status_code==200,active.text
    version=active.json()['connection']['version']
    published=client.post('/api/dji/cloud/connections/'+g['id']+'/capability',json={'request_id':str(uuid4()),'expected_version':version,'confirm':True})
    assert published.status_code==200,published.text
    cap_id=published.json()['capability']['id']
    with Session(engine()) as db:
        gateway=db.get(MqttGateway,g['id']);assert 'synthetic-password' not in json.dumps(gateway.data)
    held=threading.Event();release=threading.Event();revoke_started=threading.Event()
    def hold_read():
        with Session(engine()) as db:
            user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','fixture')
            read_authorized_dock_status(db,p,g['id'],'SIMULATED');held.set();assert release.wait(8);db.rollback()
    revoke_body={'request_id':str(uuid4()),'version':authorization['version'],'enabled':False}
    def revoke():
        revoke_started.set()
        return client.post('/api/mqtt/authorizations/'+grant_id+'/status',json=revoke_body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future=pool.submit(hold_read);assert held.wait(5)
        future=pool.submit(revoke);assert revoke_started.wait(3);time.sleep(.2)
        try:
            assert not future.done(), '撤销不能在已经授权的读取尚未结束时先提交'
        finally:release.set()
        read_future.result(timeout=8);revoked=future.result(timeout=8)
    assert revoked.status_code==200,revoked.text
    view=client.get('/api/dji/cloud/connections/'+g['id']+'/status')
    assert view.status_code==200 and view.json()['connection']['authorization_available'] is False
    from generic_agents.gateway import invoke
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','fixture')
        result=invoke(db,p,cap_id,'perception',{},'dji-revoked-test:'+str(uuid4()))
        assert result.status=='blocked_evidence'


def _native_task_journey(client,account,monkeypatch,tool_id,evidence_id):
    """Formal task/approved planning and SDK execution; provider output is synthetic."""
    import copy,httpx
    from pydantic import SecretStr
    from generic_agents.instance_compiler import signature_value
    from generic_agents.models import Entity,Job
    from generic_agents.worker import process_once
    from tests.test_instance_workspace import configuration,prepared,enable,object_row,task_row
    from tests.test_vertical_slice import manifest,publish
    def ok(response):
        assert response.status_code in (200,201),response.text
        return response.json()
    def job(task_id,kind,plan_id=None):
        with Session(engine()) as db:
            q=select(Job).where(Job.kind==kind,Job.data['task_id'].as_string()==task_id)
            if plan_id is not None:q=q.where(Job.data['plan_id'].as_string()==plan_id)
            rows=list(db.scalars(q));assert len(rows)==1
            return rows[0].id
    def worker(identity):
        assert process_once(identity,deliver=False)
        with Session(engine()) as db:
            row=db.get(Job,identity);assert row.status=='succeeded',str(row.data.get('error',''))[:500]
    config=configuration();flow=config['flows']['资料核验']
    flow['steps'][0]['planning_tools']=[{'capability_id':tool_id,'allow_external':False}]
    flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
    release,_,_=prepared(client,config);enable(client,release);obj=object_row(client,release,1)
    kind=next(x for x in release['snapshot']['object_types'] if x['key']==obj['entity_type'])
    field=next(x['key'] for x in kind['fields'] if x['kind']=='整数')
    with Session(engine()) as db:
        entity=db.get(Entity,obj['id']);attrs=copy.deepcopy(entity.data);version=entity.version
    attrs[field]=12
    ok(client.patch('/api/entities/'+obj['id'],json={'expected_version':version,'reason':'合成业务任务用于读取已授权机场状态','attributes':attrs}))
    task,_=task_row(client,release,obj);path='/api/workspace/tasks/'+task['id']
    criteria=[{'id':'enough','kind':'evidence_sufficient','description':'对象依据充分'},{'id':'human','kind':'human_confirmation','description':'现场情况需要人工确认'}]
    spec={'purpose':'核对机场只读状态并保留原始依据，不推断现场真实状态','entity_ids':[obj['id']],
          'required_fields':[field],'criteria':criteria,'subgoals':[{'id':'checked','description':'任务所需依据核对','criterion_ids':['enough','human']}],
          'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}},
          'observation_mode':'existing_only','max_steps':12,'max_tool_calls':8,'stop_on_conflict':True}
    ok(client.post(path+'/goal',json={'request_id':str(uuid4()),'expected_version':task['version'],'reason':'确定本次任务与只读范围','goal_spec':spec}))
    worker(job(task['id'],'check_native_business_goal'))
    model=publish(client,manifest('foundation_model',namespace='dji_synthetic',name='model_'+uuid4().hex,
        allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY','max_tokens':2048},data_policy={'cloud_allowed':True}))
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',10);monkeypatch.setattr(settings(),'cloud_daily_token_limit',1000000)
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-only');monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-only'))
    original=httpx.Client.post;calls=[]
    def provider(self,target,**kwargs):
        if str(target)!='https://api.deepseek.com/chat/completions':return original(self,target,**kwargs)
        payload=kwargs['json'];calls.append(1)
        content=json.loads(payload['messages'][-1]['content']);assert any(x['capability_id']==tool_id for x in content['registered_tools'])
        plan={'summary':'核对人工授权的机场历史状态，并保留原始来源供人工确认','steps':[
             {'title':'读取已授权机场状态','agent':'perception','action':'invoke_capability','capability_id':tool_id,'arguments':{}},
             {'title':'整理等待复核的报告','agent':'business','action':'report'}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(plan)}}],
            'usage':{'prompt_tokens':60,'completion_tokens':30,'total_tokens':90}},headers={'x-request-id':'synthetic-native-dji'})
    monkeypatch.setattr(httpx.Client,'post',provider)
    planning=path+'/intelligent-plan';preview=ok(client.post(planning+'/preview',json={'model_profile_id':model['id']}))
    listing=ok(client.post(planning,json={'request_id':str(uuid4()),'expected_version':preview['task_version'],
        'reason':'确认本次合成任务的本地只读计划','model_profile_id':model['id'],'preview_hash':preview['preview_hash'],'cloud_authorized':True}))
    plan_id=listing['current']['id'];worker(job(task['id'],'generate_native_step_plan',plan_id));assert len(calls)==1
    execution_path=planning+'/'+plan_id+'/execution';preview=ok(client.post(execution_path+'/preview',json={}))
    ok(client.post(execution_path+'/approve',json={'request_id':str(uuid4()),'expected_version':preview['task_version'],
       'reason':'确认固定设备的本地只读状态工具','expected_plan_version':preview['plan_version'],
       'approval_hash':preview['approval_hash'],'authorize_external_inputs':False}))
    worker(job(task['id'],'execute_native_step_plan',plan_id));saved=ok(client.get(execution_path))['execution']
    step=next(x for x in saved['steps'] if x['action']=='invoke_capability')
    assert step['output']['latest']['evidence_id']==evidence_id
    assert step['output']['latest']['reported']['battery_percent']==75
    # A fresh application session reads the same durable task invocation.
    with TestClient(app) as restarted:
        login=ok(restarted.post('/api/login',json={'username':account[0],'password':account[1]}))
        restarted.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':account[2]})
        again=ok(restarted.get(execution_path))['execution']
        assert next(x for x in again['steps'] if x['action']=='invoke_capability')['output']==step['output']
    # A separate API process also reloads the durable task and evidence.
    import os,subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code='''import os\nfrom fastapi.testclient import TestClient\nfrom generic_agents.api import app\nwith TestClient(app) as c:\n r=c.post('/api/login',json={'username':os.environ['DJI_TEST_USER'],'password':os.environ['DJI_TEST_PASSWORD']});assert r.status_code==200\n c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':os.environ['DJI_TEST_WORKSPACE']})\n r=c.get(os.environ['DJI_TEST_PATH']);assert r.status_code==200\n v=r.json()['execution'];step=next(x for x in v['steps'] if x['action']=='invoke_capability');assert step['output']['latest']['evidence_id']==os.environ['DJI_TEST_EVIDENCE']\n print('saved task evidence recovered')\n'''
    env={**os.environ,'GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
         'GA_MQTT_ALLOWED_BROKERS':settings().mqtt_allowed_brokers,'DJI_TEST_USER':account[0],
         'DJI_TEST_PASSWORD':account[1],'DJI_TEST_WORKSPACE':account[2],'DJI_TEST_PATH':execution_path,
         'DJI_TEST_EVIDENCE':evidence_id,'PYTHONPATH':str(root/'src')}
    child=subprocess.run([str(root/'.venv/bin/python'),'-c',code],cwd=root,env=env,capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr[-1000:]
    assert 'saved task evidence recovered' in child.stdout
    return task['id'],plan_id
