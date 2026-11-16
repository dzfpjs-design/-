"""Isolated DB and protocol simulators: no production data or equipment."""
import json,socket,threading,subprocess,time
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.sensor_automation_api import router
from generic_agents.db import engine
from generic_agents.models import Capability,Entity,Observation,Job,Memory,Membership,now
from generic_agents.contracts import Manifest,digest
from generic_agents import sensor_automation as automation
from tests.test_opcua_secure_transport import secure_server
from tests.test_opcua_gateway_pg import _configure as configure_opcua

if not any(getattr(r,'path','')=='/api/sensor-automation/options' for r in app.routes):app.include_router(router)

@pytest.fixture
def device():
    ip=subprocess.check_output(['ipconfig','getifaddr','en0'],text=True).strip()
    server=socket.socket();server.bind((ip,0));server.listen(8);server.settimeout(.2)
    calls=[];running=threading.Event();running.set()
    def serve():
        while running.is_set():
            try:conn,_=server.accept()
            except socket.timeout:continue
            except OSError:break
            with conn:
                msg=conn.recv(12);calls.append(msg)
                if len(msg)==12:conn.sendall(msg[:2]+b'\0\0\0\x05'+msg[6:7]+msg[7:8]+b'\x02\x00\x19')
    thread=threading.Thread(target=serve,daemon=True);thread.start();yield ip,server.getsockname()[1],calls
    running.clear();server.close();thread.join(1)

def entity(account):
    with Session(engine()) as db:
        m=Manifest(kind='domain_package',namespace='fixture',name='domain',version='1.0.0',owner='test',description='test',specification={'entity_schemas':{'plot':{'type':'object','properties':{'temperature':{'type':'number','unit':'℃','title':'温度'}}}}})
        data=m.model_dump();h=digest(data);cap=Capability(workspace_id=account[2],namespace=m.namespace,name=m.name,release_version=m.version,kind='domain_package',status='published',content_hash=h,validated_hash=h,data=data);db.add(cap);db.flush()
        e=Entity(workspace_id=account[2],namespace='fixture',entity_type='plot',external_id='合成对象',schema_capability_id=cap.id,data={});db.add(e);db.commit();return e.id

def modbus(client,account,device,monkeypatch):
    ip,port,_=device;monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([dict(workspace=account[2],host=ip,port=port,unit=1,functions=[3],start=0,end=10)]))
    gid=client.get('/api/modbus/grants').json()['grants'][0]['id'];r=client.post('/api/modbus/connections',json=dict(request_id=str(uuid4()),name='合成温度',grant_id=gid,function=3,address=1,value_type='uint16',scale=.1,unit_label='℃'));assert r.status_code==200,r.text
    row=r.json()['connection'];r=client.post('/api/modbus/connections/'+row['id']+'/binding',json={'request_id':str(uuid4()),'version':row['version'],'entity_id':entity(account),'field':'temperature'});assert r.status_code==200,r.text
    return row

def policy(client,protocol,row,threshold=None):
    data={'request_id':str(uuid4()),'protocol':protocol,'connection_id':row['id'],'connection_version':row['version'],'expected_version':0,'enabled':True,'interval_seconds':60,'max_age_seconds':3600,'threshold':threshold}
    r=client.post('/api/sensor-automation/policy',json=data);assert r.status_code==200,r.text
    assert client.post('/api/sensor-automation/policy',json=data).json()==r.json()
    return data,r.json()['policy']

def test_real_modbus_read_to_data_and_business_candidate(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);request,p=policy(client,'modbus',row,{'operator':'above','value':2.0})
    assert automation.tick(account[2])==1 and len(device[2])==1
    alerts=client.get('/api/sensor-automation/alerts');assert alerts.status_code==200,alerts.text
    a=alerts.json()['alerts'][0];assert a['value']==2.5 and a['freshness']=='unknown' and a['quality']=='unverified' and a['status']=='unconfirmed'
    receipts=client.get('/api/sensor-automation/data',params={'connection_id':row['id']}).json()
    assert len(receipts['records'])==1 and receipts['records'][0]['object_name']=='合成对象' and receipts['records'][0]['value']==2.5
    with Session(engine()) as db:
        obs=db.get(Observation,a['observation_id']);assert obs.observed_at is None and obs.data['extracted']['attributes']=={'temperature':2.5}
        assert db.scalar(select(Memory.id).where(Memory.workspace_id==account[2],Memory.data['type'].as_string()==automation.DATA))
    ack={'request_id':str(uuid4()),'version':a['version']};res=client.post('/api/sensor-automation/alerts/'+a['id']+'/acknowledge',json=ack);assert res.status_code==200,res.text
    assert client.post('/api/sensor-automation/alerts/'+a['id']+'/acknowledge',json=ack).json()['alert']['status']=='acknowledged'
    engine().dispose();assert client.get('/api/sensor-automation/alerts').json()['alerts'][0]['status']=='acknowledged'
    assert automation.tick(account[2])==0 and len(device[2])==1
    opts=client.get('/api/sensor-automation/options').json()['connections'];state=next(x['policy'] for x in opts if x['connection_id']==row['id'])
    pause={**request,'request_id':str(uuid4()),'expected_version':state['generation'],'enabled':False};assert client.post('/api/sensor-automation/policy',json=pause).status_code==200
    assert automation.tick(account[2],at=now()+timedelta(seconds=120))==0 and len(device[2])==1

def test_revoked_admin_pauses_before_next_read(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);_,p=policy(client,'modbus',row)
    with Session(engine()) as db:
        m=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.role=='admin'));m.role='viewer';db.commit()
    assert automation.tick(account[2])==1 and not device[2]
    with Session(engine()) as db:
        saved=db.get(Job,p['id']);assert not saved.data['enabled'] and saved.data['state']=='paused'

def test_transport_failure_retains_schedule_and_avoids_fake_observation(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);_,p=policy(client,'modbus',row)
    from generic_agents import modbus_readonly_transport as transport
    def fail(*a,**k):raise transport.ModbusError('测试断线')
    monkeypatch.setattr(transport,'read_registers',fail)
    assert automation.tick(account[2])==1
    with Session(engine()) as db:
        saved=db.get(Job,p['id']);assert saved.data['state']=='error' and saved.data['enabled'];assert not db.scalars(select(Observation)).all()
    assert automation.tick(account[2])==0

def test_opcua_stale_source_does_not_create_candidate(client,account,secure_server,monkeypatch):
    configure_opcua(account,secure_server,monkeypatch);grant=client.get('/api/opcua/grants').json()['grants'][0]
    r=client.post('/api/opcua/connections',json={'request_id':str(uuid4()),'name':'合成OPC','grant_id':grant['id'],'unit_label':'℃'});assert r.status_code==200,r.text
    row={'id':r.json()['result']['connection_id'],'version':1};r=client.post('/api/opcua/connections/'+row['id']+'/binding',json={'request_id':str(uuid4()),'version':1,'entity_id':entity(account),'field':'temperature'});assert r.status_code==200,r.text
    policy(client,'opcua',row,{'operator':'above','value':20.0});assert automation.tick(account[2])==1
    assert client.get('/api/sensor-automation/alerts').json()['alerts']==[]
    with Session(engine()) as db:
        obs=db.scalars(select(Observation)).one();assert obs.data['source_reported_timestamp'].startswith('2026-01-02') and obs.observed_at is None
        m=db.scalars(select(Memory).where(Memory.data['type'].as_string()==automation.DATA)).one();assert m.data['freshness']=='stale' and m.data['quality']=='good'

def test_policy_cancel_fence_and_original_owner_recovery(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch)
    body={'request_id':str(uuid4()),'protocol':'modbus','connection_id':row['id'],'connection_version':row['version'],'expected_version':0,'enabled':True,'interval_seconds':60,'max_age_seconds':3600,'threshold':None}
    path='/api/sensor-automation/policy-requests/'+body['request_id']+'/cancel'
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.role=='admin'));member.role='viewer';db.commit()
    result=client.post(path,json=body);assert result.status_code==200 and result.json()['state']=='cancelled',result.text
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='admin';db.commit()
    assert client.post('/api/sensor-automation/policy',json=body).status_code==409
    assert automation.tick(account[2])==0 and not device[2]

def test_tampered_threshold_is_held_not_shown_as_fact(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);policy(client,'modbus',row,{'operator':'above','value':2.0});automation.tick(account[2])
    alert=client.get('/api/sensor-automation/alerts').json()['alerts'][0]
    with Session(engine()) as db:
        saved=db.get(Job,alert['id']);saved.data={**saved.data,'threshold':{'operator':'above','value':999}};db.commit()
    result=client.get('/api/sensor-automation/alerts').json();assert result['alerts']==[] and result['unverified_count']==1
    assert client.post('/api/sensor-automation/alerts/'+alert['id']+'/acknowledge',json={'request_id':str(uuid4()),'version':alert['version']}).status_code==409

def test_worker_loop_persists_first_due_read_and_stops(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);policy(client,'modbus',row,{'operator':'above','value':2.0})
    stop=threading.Event();thread=automation.start_workspace_collector(account[2],stop)
    try:
        for _ in range(100):
            if client.get('/api/sensor-automation/alerts').json()['alerts']:break
            time.sleep(.05)
        else:pytest.fail('后台采集未送达候选收件箱')
    finally:stop.set();thread.join(5)
    assert not thread.is_alive() and len(device[2])==1

def test_same_threshold_stays_one_clue_until_reading_stops_matching(client,account,device,monkeypatch):
    row=modbus(client,account,device,monkeypatch);policy(client,'modbus',row,{'operator':'above','value':2.0});moment=now()
    assert automation.tick(account[2],at=moment+timedelta(seconds=1))==1
    a=client.get('/api/sensor-automation/alerts').json()['alerts'][0]
    assert client.post('/api/sensor-automation/alerts/'+a['id']+'/acknowledge',json={'request_id':str(uuid4()),'version':a['version']}).status_code==200
    assert automation.tick(account[2],at=moment+timedelta(seconds=65))==1
    rows=client.get('/api/sensor-automation/alerts').json()['alerts'];assert len(rows)==1 and rows[0]['status']=='acknowledged' and rows[0]['repeat_count']==2
    from generic_agents import modbus_readonly_transport as transport
    monkeypatch.setattr(transport,'read_registers',lambda *a:[1])
    assert automation.tick(account[2],at=moment+timedelta(seconds=130))==1
    rows=client.get('/api/sensor-automation/alerts').json()['alerts'];assert len(rows)==1 and rows[0]['status']=='no_longer_matching'
    monkeypatch.undo()

def test_concurrent_workers_do_not_duplicate_one_due_read(client,account,device,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    row=modbus(client,account,device,monkeypatch);policy(client,'modbus',row,{'operator':'above','value':2.0});moment=now()+timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:automation.tick(account[2],at=moment),range(2)))
    assert sorted(results)==[0,1] and len(device[2])==1
    assert len(client.get('/api/sensor-automation/alerts').json()['alerts'])==1

def test_managed_grant_revocation_waits_for_inflight_collection(client,account,device,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import modbus_readonly_transport as transport
    ip,port,calls=device;row=modbus(client,account,device,monkeypatch)
    monkeypatch.setenv('GA_MANAGED_MODBUS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS',ip.rsplit('.',2)[0]+'.0.0/16')
    body={'request_id':str(uuid4()),'name':'合成授权','host':ip,'port':port,'unit':1,'start':0,'end':10,'functions':[3]}
    grant=client.post('/api/modbus/authorizations',json=body);assert grant.status_code==200,grant.text
    data=grant.json()['result'];policy(client,'modbus',row)
    entered=threading.Event();release=threading.Event();original=transport.read_registers
    def slow(*args,**kwargs):entered.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(transport,'read_registers',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading=pool.submit(automation.tick,account[2]);assert entered.wait(5)
        revoke=pool.submit(client.post,'/api/modbus/authorizations/'+data['id']+'/status',json={'request_id':str(uuid4()),'version':data['version'],'enabled':False})
        time.sleep(.2);assert not revoke.done();release.set();assert reading.result(timeout=10)==1;assert revoke.result(timeout=10).status_code==200
    assert len(calls)==1
    automation.tick(account[2],at=now()+timedelta(seconds=120));assert len(calls)==1

@pytest.mark.parametrize('kind',['membership','user'])
def test_demotion_or_deactivation_waits_for_inflight_read_and_blocks_next(client,account,device,monkeypatch,kind):
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import modbus_readonly_transport as transport
    from generic_agents.models import User
    row=modbus(client,account,device,monkeypatch);policy(client,'modbus',row,{'operator':'above','value':2.0})
    entered=threading.Event();release=threading.Event();original=transport.read_registers
    def slow(*args,**kwargs):entered.set();assert release.wait(6);return original(*args,**kwargs)
    monkeypatch.setattr(transport,'read_registers',slow)
    def revoke():
        with Session(engine()) as db:
            m=db.scalar(select(Membership).where(Membership.workspace_id==account[2],Membership.role=='admin'))
            if kind=='membership':m.role='viewer'
            else:db.get(User,m.user_id).active=False
            db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading=pool.submit(automation.tick,account[2]);assert entered.wait(5)
        revoke_future=pool.submit(revoke);time.sleep(.2);assert not revoke_future.done();release.set()
        assert reading.result(timeout=10)==1;revoke_future.result(timeout=10)
    assert len(device[2])==1
    automation.tick(account[2],at=now()+timedelta(seconds=120));assert len(device[2])==1
    with Session(engine()) as db:
        saved=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind==automation.POLICY));assert saved.data['enabled'] is False

def test_manual_auto_and_grant_revocation_share_lock_order_without_deadlock(client,account,device,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import modbus_readonly_transport as transport,managed_modbus_grants as managed
    ip,port,calls=device;row=modbus(client,account,device,monkeypatch)
    monkeypatch.setenv('GA_MANAGED_MODBUS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS',ip.rsplit('.',2)[0]+'.0.0/16')
    body={'request_id':str(uuid4()),'name':'合成授权','host':ip,'port':port,'unit':1,'start':0,'end':10,'functions':[3]}
    grant=client.post('/api/modbus/authorizations',json=body);assert grant.status_code==200,grant.text;data=grant.json()['result'];policy(client,'modbus',row)
    read_entered=threading.Event();release=threading.Event();manual_shared=threading.Event();counter=0;guard=threading.Lock();old_read=transport.read_registers;old_lock=managed.lock_workspace
    def slow(*args,**kwargs):read_entered.set();assert release.wait(8);return old_read(*args,**kwargs)
    def track(db,workspace,*,shared=False):
        nonlocal counter
        result=old_lock(db,workspace,shared=shared)
        if shared:
            with guard:
                counter+=1
                if counter>=2:manual_shared.set()
        return result
    monkeypatch.setattr(transport,'read_registers',slow);monkeypatch.setattr(managed,'lock_workspace',track)
    with ThreadPoolExecutor(max_workers=3) as pool:
        auto=pool.submit(automation.tick,account[2]);assert read_entered.wait(5)
        manual=pool.submit(client.post,'/api/modbus/connections/'+row['id']+'/readings',json={'request_id':str(uuid4()),'version':row['version']});assert manual_shared.wait(5)
        revoke=pool.submit(client.post,'/api/modbus/authorizations/'+data['id']+'/status',json={'request_id':str(uuid4()),'version':data['version'],'enabled':False})
        time.sleep(.2);assert not revoke.done();release.set()
        assert auto.result(timeout=12)==1;assert manual.result(timeout=12).status_code==200;assert revoke.result(timeout=12).status_code==200
    assert len(calls)==2
    automation.tick(account[2],at=now()+timedelta(seconds=120));assert len(calls)==2

def test_stop_prevents_starting_another_device_read(client,account,device,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents import modbus_readonly_transport as transport
    first=modbus(client,account,device,monkeypatch);policy(client,'modbus',first)
    # A second independently approved connection shares the same test sensor.
    grant=client.get('/api/modbus/grants').json()['grants'][0]
    created=client.post('/api/modbus/connections',json={'request_id':str(uuid4()),'name':'另一传感器','grant_id':grant['id'],'function':3,'address':2,'value_type':'uint16','scale':.1,'unit_label':'℃'}).json()['connection']
    with Session(engine()) as db:existing_entity=db.scalar(select(Entity.id).where(Entity.workspace_id==account[2]))
    b=client.post('/api/modbus/connections/'+created['id']+'/binding',json={'request_id':str(uuid4()),'version':1,'entity_id':existing_entity,'field':'temperature'});assert b.status_code==200,b.text
    policy(client,'modbus',created)
    started=threading.Event();release=threading.Event();stop=threading.Event();original=transport.read_registers
    def slow(*a,**kw):started.set();assert release.wait(8);return original(*a,**kw)
    monkeypatch.setattr(transport,'read_registers',slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(automation.tick,account[2],_stop_event=stop);assert started.wait(5);stop.set();release.set();assert task.result(timeout=10)==1
    assert len(device[2])==1

def test_due_policy_not_starved_by_hundred_future_policies(client,account,device,monkeypatch):
    from datetime import timedelta
    row=modbus(client,account,device,monkeypatch);_,p=policy(client,'modbus',row)
    current=now()
    with Session(engine()) as db:
        real=db.get(Job,p['id']);real.created_at=current
        for index in range(100):
            db.add(Job(workspace_id=account[2],kind=automation.POLICY,status='internal',idempotency_key='future-synthetic-'+str(index),created_at=current-timedelta(seconds=120),data={'enabled':True,'next_due':automation._iso(current+timedelta(hours=2))}))
        db.commit()
    assert automation.tick(account[2],at=current+timedelta(seconds=1))==1
    assert len(device[2])==1

def test_more_than_hundred_connections_are_paginated_and_can_pause(client,account,device,monkeypatch):
    from datetime import timedelta
    row=modbus(client,account,device,monkeypatch);request,p=policy(client,'modbus',row)
    with Session(engine()) as db:
        for index in range(101):
            db.add(Job(workspace_id=account[2],kind='modbus_connection_v1',status='internal',idempotency_key='synthetic-connection-'+str(index),created_at=now()+timedelta(seconds=index+1),data={'name':'合成连接 '+str(index)}))
        db.commit()
    first=client.get('/api/sensor-automation/options?page=1');assert first.status_code==200 and first.json()['has_more']
    last=client.get('/api/sensor-automation/options?page=3');assert last.status_code==200 and not last.json()['has_more']
    found=next(x for x in last.json()['connections'] if x['connection_id']==row['id']);assert found['policy']['enabled']
    pause={**request,'request_id':str(uuid4()),'expected_version':p['generation'],'enabled':False}
    assert client.post('/api/sensor-automation/policy',json=pause).status_code==200
    assert not next(x for x in client.get('/api/sensor-automation/options?page=3').json()['connections'] if x['connection_id']==row['id'])['policy']['enabled']
