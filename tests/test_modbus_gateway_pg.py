"""Isolated PG + real local Modbus TCP transport; no production data or hardware."""
import json
import socket
import threading
from uuid import uuid4
import pytest
from generic_agents.api import app
from generic_agents.modbus_gateway_api import router
from generic_agents.modbus_binding import router as binding_router

if not any(getattr(route, 'path', '') == '/api/modbus/grants' for route in app.routes):
    app.include_router(router)
if not any(getattr(route, "path", "") == "/api/modbus/connections/{identity}/binding" for route in app.routes):
    app.include_router(binding_router)

@pytest.fixture(autouse=True)
def isolate_worker_queue():
    # The staged v360 control table intentionally has not been migrated.
    from generic_agents.models import Base
    from generic_agents.db import engine
    from sqlalchemy import text
    tables=', '.join('"'+table.name+'"' for table in Base.metadata.tables.values() if table.name != 'auto_evaluation_recovery_control_v360')
    with engine().begin() as connection:
        connection.execute(text('TRUNCATE '+tables+' RESTART IDENTITY CASCADE'))

@pytest.fixture
def device():
    ip='192.168.0.100'
    server=socket.socket()
    try: server.bind((ip,0))
    except OSError: pytest.skip('该隔离测试需要本机 RFC1918 网络地址')
    server.listen(8); server.settimeout(.2)
    calls=[]; running=threading.Event(); running.set()
    def loop():
        while running.is_set():
            try: conn,_=server.accept()
            except socket.timeout: continue
            except OSError: break
            with conn:
                request=conn.recv(12); calls.append(request)
                if len(request)==12:
                    conn.sendall(request[:2]+b'\0\0\0\x05'+request[6:7]+request[7:8]+b'\x02\x00\x19')
    thread=threading.Thread(target=loop,daemon=True); thread.start()
    yield ip,server.getsockname()[1],calls
    running.clear();server.close();thread.join(1)

def test_real_read_registration_persistence_retry_and_revocation(client,account,device,monkeypatch):
    ip,port,calls=device
    grant=dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([grant]))
    res=client.get('/api/modbus/grants'); assert res.status_code==200,res.text
    grant_id=res.json()['grants'][0]['id']
    body=dict(request_id=str(uuid4()),name='温度传感器',grant_id=grant_id,function=3,address=2,value_type='uint16',word_order='big',scale=.1,offset=0,unit_label='℃')
    created=client.post('/api/modbus/connections',json=body);assert created.status_code==200,created.text
    row=created.json()['connection']; ident=row['id']
    assert client.post('/api/modbus/connections',json=body).json()['connection']['id']==ident
    from generic_agents.db import engine
    engine().dispose()  # Force the next request onto a fresh database connection.
    assert len(client.get('/api/modbus/connections').json()['connections'])==1
    req={'request_id':str(uuid4()),'version':row['version']}
    path='/api/modbus/connections/'+ident+'/readings'
    response=client.post(path,json=req);assert response.status_code==200,response.text
    result=response.json()['reading'];assert result['value']==2.5 and result['register_values']==[25]
    assert '服务器读取时间' in result['time_note']
    assert client.post(path,json=req).json()['reading']['id']==result['id']
    assert len(calls)==1
    assert client.get(path).json()['readings'][0]['id']==result['id']
    wrong=dict(req,version=99); assert client.post(path,json=wrong).status_code==409
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS','[]')
    assert client.post(path,json={'request_id':str(uuid4()),'version':row['version']}).status_code==409
    assert len(calls)==1

def test_out_of_scope_address_is_rejected_without_read(client,account,device,monkeypatch):
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    body=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=10,value_type='uint32')
    assert client.post('/api/modbus/connections',json=body).status_code==409
    assert not calls


def test_failed_read_can_retry_with_same_request(client,account,device,monkeypatch):
    import generic_agents.modbus_gateway_api as api
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    body=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=1,value_type='uint16')
    row=client.post('/api/modbus/connections',json=body).json()['connection']
    path='/api/modbus/connections/'+row['id']+'/readings'; request={'request_id':str(uuid4()),'version':row['version']}
    original=api.read_registers
    def fail_once(*args): raise api.ModbusError('模拟连接故障')
    monkeypatch.setattr(api,'read_registers',fail_once)
    first=client.post(path,json=request).json()['reading']; assert first['state']=='failed'
    monkeypatch.setattr(api,'read_registers',original)
    retried=client.post(path,json=request).json()['reading']; assert retried['state']=='succeeded' and retried['id']==first['id']
    assert len(calls)==1


def test_explicit_binding_writes_unclocked_observation(client,account,device,monkeypatch):
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents.models import Capability,Entity,Observation
    from generic_agents.contracts import Manifest,digest
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    with Session(engine()) as db:
        m=Manifest(kind='domain_package',namespace='fixture',name='domain',version='1.0.0',owner='test',description='test',specification={'entity_schemas':{'plot':{'type':'object','properties':{'temperature':{'type':'number','unit':'℃','title':'温度'}}}}})
        data=m.model_dump(); h=digest(data)
        package=Capability(workspace_id=account[2],namespace=m.namespace,name=m.name,release_version=m.version,kind='domain_package',status='published',content_hash=h,validated_hash=h,data=data)
        db.add(package);db.flush()
        entity=Entity(workspace_id=account[2],namespace='fixture',entity_type='plot',external_id='林地 A',schema_capability_id=package.id,data={})
        db.add(entity);db.commit();entity_id=entity.id
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    row=client.post('/api/modbus/connections',json=dict(request_id=str(uuid4()),name='温度',grant_id=grant_id,function=3,address=1,value_type='uint16',scale=.1,unit_label='℃')).json()['connection']
    path='/api/modbus/connections/'+row['id'];opts=client.get(path+'/binding').json()
    assert opts['entities'][0]['id']==entity_id
    request=dict(request_id=str(uuid4()),version=row['version'],entity_id=entity_id,field='temperature')
    saved=client.post(path+'/binding',json=request);assert saved.status_code==200,saved.text
    assert client.post(path+'/binding',json=request).status_code==200
    res=client.post(path+'/readings',json={'request_id':str(uuid4()),'version':row['version']});assert res.status_code==200,res.text
    reading=res.json()['reading'];assert reading['state']=='succeeded' and 'observation_id' in reading,reading
    with Session(engine()) as db:
        observation=db.get(Observation,reading['observation_id'])
        assert observation.entity_id==entity_id and observation.observed_at is None and observation.business_day is None
        assert observation.status=='extracted_unclocked' and observation.data['extracted']['attributes']=={'temperature':2.5}
        assert observation.data['measurement_time_known'] is False and observation.data['server_read_at']==reading['read_at']


def test_recovery_cancels_before_execution_even_after_demotion(client,account,device,monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from generic_agents.db import engine
    from generic_agents.models import Membership
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    registration=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=1,value_type='uint16')
    row=client.post('/api/modbus/connections',json=registration).json()['connection']
    body=dict(request_id=str(uuid4()),version=row['version']);path='/api/modbus/connections/'+row['id']+'/readings'
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS','[]')
    assert client.post(path,json=body).status_code==409
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2])); membership.role='viewer'; db.commit()
    resolve='/api/modbus/operations/'+body['request_id']+'/resolve'
    envelope={'kind':'read','connection_id':row['id'],'submission':body}
    result=client.post(resolve,json=envelope);assert result.status_code==200,result.text
    assert result.json()['state']=='cancelled'
    assert client.post(resolve,json=envelope).json()['state']=='cancelled'
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2])); membership.role='admin'; db.commit()
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    assert client.post(path,json=body).status_code==409
    assert not calls


def test_recovery_reports_committed_result_without_reexecution(client,account,device,monkeypatch):
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    registration=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=1,value_type='uint16')
    row=client.post('/api/modbus/connections',json=registration).json()['connection']
    req={'request_id':str(uuid4()),'version':row['version']};path='/api/modbus/connections/'+row['id']+'/readings'
    original=client.post(path,json=req).json()['reading']
    envelope={'kind':'read','connection_id':row['id'],'submission':req}
    response=client.post('/api/modbus/operations/'+req['request_id']+'/resolve',json=envelope)
    assert response.status_code==200 and response.json()['state']=='succeeded'
    assert response.json()['result_id']==original['id'] and len(calls)==1
    wrong=dict(envelope,submission={**req,'version':123})
    assert client.post('/api/modbus/operations/'+req['request_id']+'/resolve',json=wrong).status_code==409


def test_recovery_waits_for_inflight_read(client,account,device,monkeypatch):
    import threading, time
    from concurrent.futures import ThreadPoolExecutor
    import generic_agents.modbus_gateway_api as api
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    row=client.post('/api/modbus/connections',json=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=1,value_type='uint16')).json()['connection']
    req={'request_id':str(uuid4()),'version':row['version']};path='/api/modbus/connections/'+row['id']+'/readings'
    entered=threading.Event();release=threading.Event();orig=api.read_registers
    def paused(*args):
        entered.set();assert release.wait(3);return orig(*args)
    monkeypatch.setattr(api,'read_registers',paused)
    envelope={'kind':'read','connection_id':row['id'],'submission':req}
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading=pool.submit(client.post,path,json=req);assert entered.wait(2)
        resolving=pool.submit(client.post,'/api/modbus/operations/'+req['request_id']+'/resolve',json=envelope)
        time.sleep(.15);assert not resolving.done()
        release.set()
        assert reading.result(timeout=4).json()['reading']['state']=='succeeded'
        assert resolving.result(timeout=4).json()['state']=='succeeded'
    assert len(calls)==1


def test_registration_cancel_fences_replay_and_recovers_saved(client,account,device,monkeypatch):
    ip,port,calls=device
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    grant_id=client.get('/api/modbus/grants').json()['grants'][0]['id']
    body=dict(request_id=str(uuid4()),name='设备',grant_id=grant_id,function=3,address=1,value_type='uint16')
    envelope={'kind':'register','connection_id':None,'submission':body}
    resolve='/api/modbus/operations/'+body['request_id']+'/resolve'
    assert client.post(resolve,json=envelope).json()['state']=='cancelled'
    assert client.post('/api/modbus/connections',json=body).status_code==409
    assert client.get('/api/modbus/connections').json()['connections']==[]
    body=dict(body,request_id=str(uuid4()))
    saved=client.post('/api/modbus/connections',json=body).json()['connection']
    envelope['submission']=body
    response=client.post('/api/modbus/operations/'+body['request_id']+'/resolve',json=envelope)
    assert response.status_code==200,response.text
    assert response.json()['state']=='succeeded' and response.json()['result_id']==saved['id']
