import asyncio,json,threading,time
from datetime import datetime,timezone
from asyncua import ua
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from tests.test_opcua_secure_transport import secure_server,SERVERS
from generic_agents.api import app
from generic_agents.opcua_gateway_api import router
if not any(getattr(route,'path','')=='/api/opcua/grants' for route in app.routes):app.include_router(router)

@pytest.fixture(autouse=True)
def isolate_worker_queue():
    from generic_agents.models import Base
    from generic_agents.db import engine
    from sqlalchemy import text
    tables=', '.join('"'+t.name+'"' for t in Base.metadata.tables.values() if t.name!='auto_evaluation_recovery_control_v360')
    with engine().begin() as c:c.execute(text('TRUNCATE '+tables+' RESTART IDENTITY CASCADE'))

def _configure(account,server,monkeypatch):
    server=dict(server,workspace=account[2]);monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([server]));return server

def _entity(account):
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents.models import Capability,Entity
    from generic_agents.contracts import Manifest,digest
    m=Manifest(kind='domain_package',namespace='fixture',name='domain',version='1.0.0',owner='test',description='test',specification={'entity_schemas':{'plot':{'type':'object','properties':{'temperature':{'type':'number','unit':'℃','title':'温度'}}}}})
    data=m.model_dump();h=digest(data)
    with Session(engine()) as db:
      cap=Capability(workspace_id=account[2],namespace=m.namespace,name=m.name,release_version=m.version,kind='domain_package',status='published',content_hash=h,validated_hash=h,data=data);db.add(cap);db.flush()
      obj=Entity(workspace_id=account[2],namespace='fixture',entity_type='plot',external_id='林地 A',schema_capability_id=cap.id,data={});db.add(obj);db.commit();return obj.id

def test_secure_device_to_persistent_unverified_observation(client,account,secure_server,monkeypatch):
    from generic_agents.db import engine
    from sqlalchemy.orm import Session
    from generic_agents.models import Observation
    _configure(account,secure_server,monkeypatch)
    grant=client.get('/api/opcua/grants');assert grant.status_code==200,grant.text
    body=dict(request_id=str(uuid4()),name='温度',grant_id=grant.json()['grants'][0]['id'],unit_label='℃')
    res=client.post('/api/opcua/connections',json=body);assert res.status_code==200,res.text
    ident=res.json()['result']['connection_id'];assert client.post('/api/opcua/connections',json=body).json()['result']['connection_id']==ident
    entity_id=_entity(account);bind={'request_id':str(uuid4()),'version':1,'entity_id':entity_id,'field':'temperature'}
    res=client.post('/api/opcua/connections/'+ident+'/binding',json=bind);assert res.status_code==200,res.text
    request={'request_id':str(uuid4()),'version':1};path='/api/opcua/connections/'+ident+'/readings'
    result=client.post(path,json=request);assert result.status_code==200,result.text
    payload=result.json()['result'];assert payload['value']==23.5 and payload['quality']=='good' and payload['source_timestamp'].startswith('2026-01-02T03:04:05')
    assert client.post(path,json=request).json()['result']['reading_id']==payload['reading_id']
    engine().dispose()
    assert client.get(path).json()['readings'][0]['id']==payload['reading_id']
    with Session(engine()) as db:
        obs=db.get(Observation,payload['observation_id']);assert obs.entity_id==entity_id and obs.observed_at is None
        assert obs.data['source_reported_timestamp']==payload['source_timestamp'] and obs.data['quality']=='good' and obs.data['extracted']['attributes']=={'temperature':23.5}
    state,loop=SERVERS[secure_server['workspace']]
    reading=ua.DataValue(ua.Variant(21.0,ua.VariantType.Double),StatusCode=ua.StatusCode(ua.StatusCodes.Uncertain),SourceTimestamp=datetime(2026,1,3,tzinfo=timezone.utc))
    asyncio.run_coroutine_threadsafe(state['variable'].write_value(reading),loop).result(timeout=5)
    uncertain=client.post(path,json={'request_id':str(uuid4()),'version':1}).json()['result']
    assert uncertain['quality']=='uncertain' and uncertain['value']==21.0 and uncertain['status_code']==ua.StatusCodes.Uncertain
    with Session(engine()) as db:
        obs=db.get(Observation,uncertain['observation_id']);assert obs.data['quality']=='uncertain' and obs.data['extracted']['attributes']=={}


def test_recovery_fences_cancelled_original_after_role_loss(client,account,secure_server,monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents.models import Membership
    _configure(account,secure_server,monkeypatch);grant=client.get('/api/opcua/grants').json()['grants'][0]
    original={'request_id':str(uuid4()),'name':'温度','grant_id':grant['id']};envelope={'kind':'register','connection_id':None,'submission':original}
    monkeypatch.setenv('GA_OPCUA_READ_GRANTS','[]');assert client.post('/api/opcua/connections',json=original).status_code==409
    with Session(engine()) as db:
        m=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));m.role='viewer';db.commit()
    r=client.post('/api/opcua/operations/'+original['request_id']+'/resolve',json=envelope);assert r.status_code==200,r.text
    assert r.json()['state']=='cancelled'
    with Session(engine()) as db:
        m=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));m.role='admin';db.commit()
    _configure(account,secure_server,monkeypatch);assert client.post('/api/opcua/connections',json=original).status_code==409

def test_cancel_waits_until_inflight_read_commits(client,account,secure_server,monkeypatch):
    import generic_agents.opcua_gateway_api as api
    _configure(account,secure_server,monkeypatch);grant=client.get('/api/opcua/grants').json()['grants'][0]
    ident=client.post('/api/opcua/connections',json={'request_id':str(uuid4()),'name':'温度','grant_id':grant['id']}).json()['result']['connection_id']
    req={'request_id':str(uuid4()),'version':1};path='/api/opcua/connections/'+ident+'/readings';started=threading.Event();end=threading.Event();original=api.read_value
    def blocked(grant):started.set();assert end.wait(5);return original(grant)
    monkeypatch.setattr(api,'read_value',blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading=pool.submit(client.post,path,json=req);assert started.wait(3)
        recovery=pool.submit(client.post,'/api/opcua/operations/'+req['request_id']+'/resolve',json={'kind':'read','connection_id':ident,'submission':req})
        time.sleep(.15);assert not recovery.done();end.set()
        assert reading.result(timeout=8).json()['state']=='succeeded';assert recovery.result(timeout=8).json()['state']=='succeeded'
