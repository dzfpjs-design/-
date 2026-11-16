"""Real localhost supplier protocol, isolated PG, no real device or cloud calls."""
import json,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import Acquisition,ObservationPlan,SensorReservation,MonitorPolicy,Observation,Event,Task,SensorAsset,now
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.security import Principal
from generic_agents.perception import dispatch_acquisition,receive_acquisition_observation
from generic_agents.worker import process_once,deliver_events
from .test_vertical_slice import manifest,publish,entity

@pytest.fixture
def supplier(monkeypatch):
    state={'calls':[],'receipts':{},'modes':{}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def reply(self,value):
            encoded=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));state['calls'].append(body)
            mode=state['modes'].get(body['asset_external_id'],'accepted')
            receipt={'receipt_id':uuid4().hex,'request_key':body['request_key'],'asset_external_id':body['asset_external_id'],
                'status':'rejected' if mode in {'rejected','unsafe_rejected'} else 'accepted' if mode=='running' else 'completed',
                'terminal':mode not in {'running','unsafe_rejected'},'no_future_observation':mode=='rejected','no_future_device_activity':mode not in {'running','unsafe_rejected'}}
            state['receipts'][body['request_key']]=receipt
            if mode=='unknown':self.connection.close();return
            if mode=='unbound':receipt={**receipt,'asset_external_id':'different-device'}
            self.reply(receipt)
        def do_GET(self):
            from urllib.parse import parse_qs,urlparse
            key=parse_qs(urlparse(self.path).query)['request_key'][0];state.setdefault('read_calls',[]).append(key)
            self.reply(state['receipts'][key])
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state['url']='http://127.0.0.1:'+str(server.server_port)
    monkeypatch.setattr(settings(),'development_connector_urls',state['url']+'/observe,'+state['url']+'/receipt')
    yield state
    server.shutdown();thread.join();server.server_close()

def setup(c,s,fields=None,actions=None):
    fields=fields or ['temperature'];actions=actions or ['observe']
    package=publish(c,manifest(specification={'entity_schemas':{'site':{'type':'object','required':['name','temperature'],
        'additionalProperties':False,'properties':{'name':{'type':'string'},'temperature':{'type':'number'},'humidity':{'type':'number'}}}}}))
    obj=entity(c,package)
    source=publish(c,manifest('source',name='synthetic_sensor',allowed_agent_roles=['perception'],execution={'adapter':'http',
        'endpoint':s['url']+'/observe','receipt_endpoint':s['url']+'/receipt','side_effect':'read_existing' not in actions},
        specification={'timezone':'UTC','observation_capability':{'fields':fields,'modalities':['numeric'],'actions':actions}}))
    return obj,source

def asset(c,obj,source,**changes):
    d={'coverage_entity_ids':[obj['id']],'fields':['temperature'],'modalities':['numeric'],'actions':['observe'],
        'resolution_m':1,'latency_seconds':30,'quote_ceiling_minor':0,'currency':'CNY','health':'healthy',
        'health_observed_at':(now()-timedelta(seconds=1)).isoformat(),'health_valid_until':(now()+timedelta(hours=1)).isoformat(),**changes}
    r=c.post('/api/sensing/assets',json={'source_id':source['id'],'external_id':uuid4().hex,'title':'合成设备','specification':d,'reason':'核查合成供应商声明'})
    assert r.status_code==201,r.text
    result=r.json()
    d=c.post('/api/sensing/devices',json={'title':'合成统一设备','identity_scheme':'inventory_tag','identity_issuer':'隔离测试台账',
        'identity_reference':result['external_id'],'reason':'明确合成台账号，不代表硬件实测'});assert d.status_code==201,d.text
    dev=d.json();d=c.post('/api/sensing/devices/'+dev['id']+'/approve',json={'expected_version':dev['version'],'reason':'核对隔离测试身份'});assert d.status_code==200,d.text
    dev=d.json();d=c.post('/api/sensing/assets/'+result['id']+'/device',json={'expected_version':result['version'],
        'device_id':dev['id'],'device_expected_version':dev['version'],'reason':'核对该接口归属统一设备'});assert d.status_code==200,d.text
    return next(a for a in c.get('/api/sensing').json()['assets'] if a['id']==result['id'])

def monitor(c,obj,source,assets,fields=None,**requirements):
    r=c.post('/api/monitor-policies',json={'title':'合成来源择优','entity_ids':[obj['id']],'source_ids':[source['id']],
        'fields':fields or ['temperature'],'sensor_asset_ids':[a['id'] for a in assets],
        'selection_requirement':{'allowed_modalities':['numeric'],'allowed_actions':['observe'],**requirements},
        'max_acquisitions_per_day':5,'valid_until':(now()+timedelta(days=1)).isoformat()})
    assert r.status_code==201,r.text
    row=r.json();r=c.post('/api/monitor-policies/'+row['id']+'/approve',json={'expected_version':row['version'],'reason':'批准合成范围与预算'})
    assert r.status_code==200,r.text
    return r.json()

def scan(c,m):
    r=c.post('/api/monitor-policies/'+m['id']+'/scan');assert r.status_code==200,r.text
    return r.json()['acquisition_ids']

def principal(m):return Principal(m['approved_by'],m['workspace_id'],'admin','sensing-test')
def dispatch(m,identifier):
    with Session(engine()) as db:dispatch_acquisition(db,principal(m),identifier);db.commit()
def current(c):return c.get('/api/sensing').json()['plans'][0]
def observe(c,m,obj,src,acq,attrs):
    extractor=publish(c,manifest('tool',name='numeric_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[]}))
    at=now();r=c.post('/api/observations',json={'source_id':src['id'],'source_record_id':uuid4().hex,'entity_id':obj['id'],
        'observed_at':at.isoformat(),'business_day':str(at.date()),'attributes':attrs,'extractor_id':extractor['id'],
        'startup_mode':'automatic','acquisition_id':acq})
    assert r.status_code==201,r.text
    while process_once():pass
    deliver_events()
    return r.json()

def test_selection_ranking_bounds_and_dispatch_version(client,supplier):
    obj,src=setup(client,supplier)
    bad=asset(client,obj,src,health='offline');expensive=asset(client,obj,src,quote_ceiling_minor=50)
    slow=asset(client,obj,src,latency_seconds=1000);good=asset(client,obj,src)
    m=monitor(client,obj,src,[bad,expensive,slow,good],max_total_cost_minor=10)
    ids=scan(client,m);assert len(ids)==1 and scan(client,m)==[]
    plan=current(client);assert plan['data']['candidates'][0]['asset_id']==good['id']
    assert sum(c['eligible'] for c in plan['data']['candidates'])==1
    assert plan['data']['budget']['daily_count']==1
    edited=client.patch('/api/sensing/assets/'+good['id'],json={'expected_version':good['version'],'title':'核对后停用','status':'disabled','specification':good['data'],'reason':'不得下发'})
    assert edited.status_code==200
    dispatch(m,ids[0]);assert supplier['calls']==[]
    assert current(client)['status']=='escalated'
    assert len(client.get('/api/sensing/assets/'+good['id']+'/history').json())==3
    assert client.patch('/api/sensing/assets/'+good['id'],json={'expected_version':1,'title':'过时修改','specification':good['data'],'reason':'旧版'}).status_code==409

def test_terminal_rejection_chooses_alternative_under_same_plan(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src,latency_seconds=10);b=asset(client,obj,src,latency_seconds=20)
    supplier['modes'][a['external_id']]='rejected';m=monitor(client,obj,src,[a,b]);first=scan(client,m)[0];plan=current(client)
    dispatch(m,first);new=current(client);assert new['id']==plan['id'] and new['data']['current_acquisition_id']!=first
    with Session(engine()) as db:
        second=db.get(Acquisition,new['data']['current_acquisition_id']);assert second.data['sensor_asset_id']==b['id']
        assert db.scalar(select(SensorReservation).where(SensorReservation.asset_id==a['id'])).acquisition_id is None
    dispatch(m,second.id);assert len(supplier['calls'])==2
    assert supplier['calls'][0]['max_cost_minor']==0 and supplier['calls'][0]['fields']==['temperature']
    assert current(client)['status']=='awaiting_observation'

@pytest.mark.parametrize('mode',['unknown','unbound','unsafe_rejected'])
def test_unknown_never_substitutes_or_replays(client,supplier,mode):
    obj,src=setup(client,supplier);a=asset(client,obj,src,latency_seconds=10);b=asset(client,obj,src,latency_seconds=20)
    supplier['modes'][a['external_id']]=mode;m=monitor(client,obj,src,[a,b]);identifier=scan(client,m)[0];dispatch(m,identifier)
    plan=current(client);assert plan['status']=='escalated' and plan['data']['escalation_code']=='unknown_action'
    dispatch(m,identifier);assert scan(client,m)==[] and len(supplier['calls'])==1
    with Session(engine()) as db:assert db.scalar(select(SensorReservation).where(SensorReservation.asset_id==a['id'])).acquisition_id==identifier
    r=client.post('/api/sensing/plans/'+plan['id']+'/retry',json={'expected_version':plan['version'],'reason':'已核查仍未知'})
    assert r.status_code==200 and r.json()['acquisition_ids']==[]
    deliver_events();after=current(client);assert after['data']['business_delivery']=='received'
    task=after['data']['business_task_id'];deliver_events();assert current(client)['data']['business_task_id']==task
    with Session(engine()) as db:assert len(db.scalars(select(Task)).all())==1

def test_timeout_late_confirmed_observation_and_collection_close(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a],minimum_verification='confirmed',observation_wait_seconds=1)
    identifier=scan(client,m)[0];dispatch(m,identifier)
    with Session(engine()) as db:
        acq=db.get(Acquisition,identifier);acq.data={**acq.data,'dispatch_started_at':(now()-timedelta(seconds=2)).isoformat()};db.commit()
    assert scan(client,m)==[];assert current(client)['data']['escalation_code']=='observation_timeout'
    obs=observe(client,m,obj,src,identifier,{'temperature':27})
    assert current(client)['status']=='escalated'
    # Human confirmation of persisted evidence is required; file arrival/automatic extraction is insufficient.
    row=client.get('/api/overview').json()['observations'][0]
    r=client.post('/api/observations/'+obs['id']+'/verify',json={'expected_version':row['version'],'decision':'confirmed','reason':'核对合成观测与原记录'})
    assert r.status_code==200,r.text
    assert current(client)['status']=='completed'
    with Session(engine()) as db:
        assert db.get(Acquisition,identifier).status=='succeeded'
        assert db.scalar(select(SensorReservation).where(SensorReservation.asset_id==a['id'])).acquisition_id is None

def test_existing_observation_requires_actual_collection_receipt(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a])
    with Session(engine()) as db:
        obs=Observation(workspace_id=m['workspace_id'],source_id=src['id'],source_record_id=uuid4().hex,processing_version='1.0.0',entity_id=obj['id'],
            observed_at=now(),business_day=str(now().date()),status='processed',data={'extracted':{'attributes':{'temperature':21}},'submitted_by':m['approved_by']})
        db.add(obs);db.flush();identifier=obs.id;db.commit()
    assert scan(client,m)==[];assert current(client)['status']=='awaiting_collection'
    with Session(engine()) as db:
        db.add(Event(workspace_id=m['workspace_id'],kind='observation.data',idempotency_key='data:'+identifier,
            status='received',data={'observation_id':identifier,'entity_id':obj['id']}));db.commit()
    assert scan(client,m)==[] and current(client)['status']=='completed' and supplier['calls']==[]

def test_partial_fields_continue_same_plan_after_qualified_data(client,supplier):
    obj,src=setup(client,supplier,['temperature','humidity']);a=asset(client,obj,src,fields=['temperature']);b=asset(client,obj,src,fields=['humidity'])
    m=monitor(client,obj,src,[a,b],fields=['temperature','humidity']);identifier=scan(client,m)[0];plan=current(client)
    with Session(engine()) as db:chosen=db.get(Acquisition,identifier).data['sensor_asset_id']
    dispatch(m,identifier);observe(client,m,obj,src,identifier,{'temperature':27} if chosen==a['id'] else {'humidity':60})
    after=current(client);assert after['id']==plan['id']
    with Session(engine()) as db:
        rows=db.scalars(select(Acquisition).order_by(Acquisition.created_at)).all();assert len(rows)==2
        assert rows[0].status=='succeeded' and rows[1].status=='awaiting_observation'
        last=rows[1];remaining=last.data['selected_fields'][0]
    observe(client,m,obj,src,last.id,{remaining:61 if remaining=='humidity' else 28});assert current(client)['status']=='completed'

def test_revocation_cancels_unissued_and_keeps_unknown_receipt_recovery(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0]
    assert client.post('/api/monitor-policies/'+m['id']+'/revoke',json={'expected_version':m['version'],'reason':'停止新动作'}).status_code==200
    with Session(engine()) as db:assert db.get(Acquisition,identifier).status=='cancelled'
    assert current(client)['status']=='cancelled' and supplier['calls']==[]

def test_budget_attempts_and_capability_scope(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src,quote_ceiling_minor=10);b=asset(client,obj,src,quote_ceiling_minor=10,latency_seconds=40)
    supplier['modes'][a['external_id']]='rejected';m=monitor(client,obj,src,[a,b],max_total_cost_minor=10)
    dispatch(m,scan(client,m)[0]);plan=current(client);assert plan['status']=='escalated'
    assert any('累计费用预留不足' in c['reasons'] for c in plan['data']['candidates'])
    bad={**a['data'],'actions':['fly_route']}
    r=client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':a['version'],'title':'错误能力','specification':bad,'reason':'错误动作'})
    assert r.status_code==422

@pytest.mark.parametrize('change',[{'available_until':'bad-time'},{'health_observed_at':(now()+timedelta(days=1)).isoformat()}, {'fields':['temperature','temperature']}])
def test_asset_contract_rejects_invalid_state(client,supplier,change):
    obj,src=setup(client,supplier);a=asset(client,obj,src)
    r=client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':a['version'],'title':'无效','specification':{**a['data'],**change},'reason':'契约核对'})
    assert r.status_code==422

def test_per_asset_reservation_and_original_resource_recovery(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src)
    m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,src,[b]);m3=monitor(client,obj,src,[a])
    first=scan(client,m1)[0];second=scan(client,m2)[0];pending=scan(client,m3)[0]
    dispatch(m1,first);dispatch(m2,second);dispatch(m3,pending)
    assert len(supplier['calls'])==2
    with Session(engine()) as db:
        assert db.get(Acquisition,pending).status=='blocked_resource'
        assert db.get(Acquisition,second).status=='awaiting_observation'
    # Permanently close first request via supplier GET; no reissue of first action.
    original=supplier['calls'][0];supplier['receipts'][original['request_key']].update(status='completed_no_data',terminal=True,no_future_observation=True)
    r=client.post('/api/acquisitions/'+first+'/reconcile');assert r.status_code==200,r.text
    assert scan(client,m3)==[pending];dispatch(m3,pending)
    assert len(supplier['calls'])==3
    with Session(engine()) as db:
        assert len(db.scalars(select(Acquisition).where(Acquisition.data['policy_id'].as_string()==m3['id'])).all())==1

def test_revoked_unknown_can_read_original_receipt_without_new_actions(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);supplier['modes'][a['external_id']]='unknown'
    m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0];dispatch(m,identifier)
    assert client.post('/api/monitor-policies/'+m['id']+'/revoke',json={'expected_version':m['version'],'reason':'撤销新动作'}).status_code==200
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='cancelled',terminal=True,no_future_observation=True)
    # Registry withdrawal does not discard the approved request's read-only receipt endpoint snapshot.
    with Session(engine()) as db:
        from generic_agents.models import Capability
        cap=db.get(Capability,src['id']);cap.status='withdrawn';db.commit()
    r=client.post('/api/acquisitions/'+identifier+'/reconcile');assert r.status_code==200,r.text
    assert current(client)['status']=='cancelled' and len(supplier['calls'])==1
    with Session(engine()) as db:assert db.scalar(select(SensorReservation).where(SensorReservation.asset_id==a['id'])).acquisition_id is None

def test_precision_requires_bound_observation_metadata(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src,resolution_m=1);m=monitor(client,obj,src,[a],max_resolution_m=2)
    with Session(engine()) as db:
        obs=Observation(workspace_id=m['workspace_id'],source_id=src['id'],source_record_id=uuid4().hex,processing_version='1.0.0',entity_id=obj['id'],observed_at=now(),business_day=str(now().date()),status='processed',data={'extracted':{'attributes':{'temperature':21}},'submitted_by':m['approved_by']})
        db.add(obs);db.flush();db.add(Event(workspace_id=m['workspace_id'],kind='observation.data',idempotency_key='data:'+obs.id,status='received',data={'observation_id':obs.id,'entity_id':obj['id']}));db.commit()
    identifier=scan(client,m)[0];assert current(client)['status']=='awaiting_dispatch'
    dispatch(m,identifier);observe(client,m,obj,src,identifier,{'temperature':22});assert current(client)['status']=='completed'

def test_existing_read_and_attempt_bound(client,supplier):
    obj,src=setup(client,supplier,actions=['read_existing']);a=asset(client,obj,src,actions=['read_existing'],latency_seconds=10);b=asset(client,obj,src,actions=['read_existing'],latency_seconds=20)
    supplier['modes'][a['external_id']]='rejected';m=monitor(client,obj,src,[a,b],allowed_actions=['read_existing'],max_attempts=1)
    dispatch(m,scan(client,m)[0]);assert current(client)['data']['escalation_code']=='attempts'
    assert supplier['calls'][0]['action']=='read_existing' and len(supplier['calls'])==1

def test_same_policy_concurrent_scans_have_one_durable_request(client,supplier):
    from concurrent.futures import ThreadPoolExecutor
    from generic_agents.perception import propose_acquisitions
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a])
    def scan_db():
        with Session(engine()) as db:
            value=propose_acquisitions(db,principal(m),m['id']);db.commit();return value
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:scan_db(),range(2)))
    assert sorted(map(len,results))==[0,1]
    with Session(engine()) as db:assert len(db.scalars(select(ObservationPlan)).all())==1 and len(db.scalars(select(Acquisition)).all())==1

@pytest.mark.parametrize('change,reason',[
    ({'health_valid_until':(now()-timedelta(seconds=1)).isoformat(),'health_observed_at':(now()-timedelta(hours=1)).isoformat()},'健康声明'),
    ({'resolution_m':100},'分辨率'),({'currency':'USD'},'币种'),
    ({'available_from':(now()+timedelta(hours=1)).isoformat()},'可用窗口')])
def test_hard_eligibility_filters_have_saved_explanations(client,supplier,change,reason):
    obj,src=setup(client,supplier);a=asset(client,obj,src,**change);m=monitor(client,obj,src,[a],max_resolution_m=2)
    assert scan(client,m)==[];plan=current(client)
    assert reason in ' '.join(plan['data']['candidates'][0]['reasons']) and supplier['calls']==[]

def test_autonomous_escalation_does_not_claim_an_unpinned_business_goal(client,supplier):
    from generic_agents.models import DataRun,DataGap
    obj,src=setup(client,supplier);a=asset(client,obj,src,health='offline');m=monitor(client,obj,src,[a])
    with Session(engine()) as db:
        task=Task(workspace_id=m['workspace_id'],owner_id=m['approved_by'],package_id=obj['schema_capability_id'],title='原业务目标',status='draft',data={})
        db.add(task);db.flush();query=DataRun(workspace_id=m['workspace_id'],owner_id=m['approved_by'],question='原业务查询',status='insufficient',data={'goal_delegation':{'task_id':task.id}})
        db.add(query);db.flush();gap=DataGap(workspace_id=m['workspace_id'],query_id=query.id,entity_id=obj['id'],status='waiting_observation',data={'policy_id':m['id'],'actor_id':m['approved_by'],'missing_fields':['temperature'],'request':{}})
        db.add(gap);db.commit();task_id=task.id
    scan(client,m);deliver_events();plan=current(client)
    assert plan['data']['business_task_id']!=task_id
    with Session(engine()) as db:
        assert len(db.scalars(select(Task)).all())==2
        assert not db.get(Task,task_id).data.get('perception_escalations')

def test_operator_sensing_asset_is_served(client):
    response=client.get('/assets/sensing.js');assert response.status_code==200
    assert 'javascript' in response.headers['content-type'] and 'function augmentSensing' in response.text
    assert '/assets/sensing.js' in client.get('/').text

def test_cancelled_unissued_job_finishes_as_cancelled(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);identifier=scan(client,m)[0]
    client.post('/api/monitor-policies/'+m['id']+'/revoke',json={'expected_version':m['version'],'reason':'撤销尚未派发请求'})
    while process_once():pass
    from generic_agents.models import Job
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['acquisition_id'].as_string()==identifier))
        assert job.status=='cancelled' and supplier['calls']==[]

def test_pending_asset_version_change_does_not_bypass_resource_guard(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m1=monitor(client,obj,src,[a]);m2=monitor(client,obj,src,[a])
    first=scan(client,m1)[0];other=scan(client,m2)[0];dispatch(m1,first)
    client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':a['version'],'title':'新版设备','specification':a['data'],'reason':'版本已经改变'})
    dispatch(m2,other)
    with Session(engine()) as db:assert db.get(Acquisition,other).status=='blocked_connector'
    assert len(supplier['calls'])==1

def test_same_reason_new_attempt_gets_new_business_receipt_and_same_task(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src,health='offline');m=monitor(client,obj,src,[a]);scan(client,m);deliver_events()
    first=current(client);task_id=first['data']['business_task_id'];event_id=first['data']['escalation_event_id']
    r=client.patch('/api/sensing/assets/'+a['id'],json={'expected_version':a['version'],'title':a['title'],
        'specification':{**a['data'],'health':'healthy'},'reason':'已核查合成设备恢复健康'});assert r.status_code==200
    supplier['modes'][a['external_id']]='rejected'
    r=client.post('/api/sensing/plans/'+first['id']+'/retry',json={'expected_version':first['version'],'reason':'在原授权内恢复健康资产'})
    assert r.status_code==200,r.text
    dispatch(m,r.json()['acquisition_ids'][0]);again=current(client)
    assert again['data']['escalation_code']=='no_source' and again['data']['escalation_event_id']!=event_id
    assert again['data']['business_delivery']=='pending';deliver_events();final=current(client)
    assert final['data']['business_delivery']=='received' and final['data']['business_task_id']==task_id
    with Session(engine()) as db:assert len(db.get(Task,task_id).data['perception_escalations'])==2 and len(db.scalars(select(Task)).all())==1
