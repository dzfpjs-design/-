"""Actual extraction -> independent ACK -> original local acquisition settlement.

Root runs on disposable PG. The ordinary case uses the existing normal HTTP
supplier. V177 cases use the actual durable simulator and acquisition adapter
over loopback HTTP, not a fabricated exchange, extraction or receipt.
"""
import copy
import socket
import threading
import time
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import uvicorn
from fastapi import HTTPException
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import acquisition_ack_settlement_v245 as settlement
from generic_agents import models as m,worker,workspace_observation_intake as intake
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.device_simulator_v175 import create_app
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.security import Principal
from tests.test_acquisition_observation_refresh_v127 import observation_job
from tests.test_instance_workspace import configuration,save,enable,object_row,prepared
from tests.test_sensing_selection import supplier,monitor,asset as registered_asset
from tests.test_vertical_slice import manifest,publish

TOKEN='synthetic-only-http-acquisition-ack-v245-token'


def good(response,status=200):
    assert response.status_code==status,response.text[:700]
    return response.json()


def event(db,s):
    return db.scalar(select(m.Event).where(m.Event.workspace_id==s.workspace_id,
        m.Event.idempotency_key=='data:'+s.observation_id))


def settle_job(db,s):
    return db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind==settlement.KIND,
        m.Job.data['settlement']['observation_id'].as_string()==s.observation_id))


def counts(db,s):
    return {model.__name__:db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace_id))
        for model in (m.Acquisition,m.Invocation,m.Observation,m.CloudCall)}|{
        'dispatch':db.scalar(select(func.count()).select_from(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind=='dispatch_acquisition'))}


def ordinary(client,supplier,*,terminal=True):
    value=observation_job(client,supplier,terminal=terminal)
    me=good(client.get('/api/me'));p=Principal(me['user_id'],me['workspace_id'],'admin','settlement-real-pg')
    return NS(bound=engine(),workspace_id=p.workspace_id,p=p,observation_id=value['observation_id'],
        process_id=value['job_id'],acquisition_id=value['acquisition_id'],value=value)


def ordinary_without_native_parent(client,supplier):
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release,1)
    field=next(row['key'] for row in release['snapshot']['object_types'][1]['fields'] if row['label']=='数量')
    source=publish(client,manifest('source',name='ordinary_managed_ack_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'http','endpoint':supplier['url']+'/observe','receipt_endpoint':supplier['url']+'/receipt','side_effect':True},
        specification={'timezone':'Asia/Shanghai','observation_capability':{'fields':[field],'modalities':['numeric'],'actions':['observe']}}))
    asset=registered_asset(client,obj,source,fields=[field]);policy=monitor(client,obj,source,[asset],fields=[field])
    acquisition_id=good(client.post('/api/monitor-policies/'+policy['id']+'/scan'))['acquisition_ids'][0]
    me=good(client.get('/api/me'));p=Principal(me['user_id'],me['workspace_id'],'admin','ordinary-no-native-parent')
    with Session(engine()) as db:identity=db.scalar(select(m.Job.id).where(m.Job.idempotency_key=='dispatch:'+acquisition_id))
    assert worker.process_once(identity,kinds=['dispatch_acquisition'],workspace_id=p.workspace_id,deliver=False)
    extractor=publish(client,manifest('tool',name='ordinary_ack_local_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules','side_effect':False},specification={'rules':[]}))
    stamp=m.now()
    from zoneinfo import ZoneInfo
    observation=good(client.post('/api/observations',json={'source_id':source['id'],'source_record_id':str(uuid4()),
        'entity_id':obj['id'],'observed_at':stamp.isoformat(),'business_day':str(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date()),
        'attributes':{field:27},'extractor_id':extractor['id'],'startup_mode':'automatic','acquisition_id':acquisition_id}),201)
    with Session(engine()) as db:
        process_id=db.scalar(select(m.Job.id).where(m.Job.idempotency_key=='extract:'+observation['id']))
        assert not list(db.scalars(select(m.Task).where(m.Task.workspace_id==p.workspace_id)))
    return NS(bound=engine(),workspace_id=p.workspace_id,p=p,observation_id=observation['id'],process_id=process_id,
        acquisition_id=acquisition_id,value={'r':release})


def extract(s):
    with Session(s.bound) as db:job=db.get(m.Job,s.process_id);kind=job.kind
    assert worker.process_once(s.process_id,kinds=[kind],workspace_id=s.workspace_id,deliver=False)
    with Session(s.bound) as db:
        assert db.get(m.Observation,s.observation_id).status=='processed'
        assert event(db,s).status=='pending_delivery' and settle_job(db,s) is None
        assert db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation'
        assert not list(db.scalars(select(m.Memory).where(m.Memory.data['event_id'].as_string()==event(db,s).id)))
        with pytest.raises(HTTPException) as failure:
            settlement.enqueue(db,s.p,event(db,s),db.get(m.Observation,s.observation_id))
        assert failure.value.status_code==409
        db.rollback()


def ack(s):
    intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        row=event(db,s);job=settle_job(db,s)
        assert row.status=='received' and row.received_at is not None and job is not None and job.status=='queued'
        assert db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation'
        assert db.scalar(select(func.count()).select_from(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==row.id,m.Memory.data['type'].as_string()=='observation_evidence'))==1
        return job.id


def finish(s,identity):
    assert settlement.KIND in worker.SETUP_KINDS
    assert worker.process_once(identity,kinds=[settlement.KIND],workspace_id=s.workspace_id,deliver=False)


def test_ordinary_managed_original_request_closes_only_after_independent_ack_with_no_new_action(client,supplier):
    s=ordinary(client,supplier);extract(s)
    with Session(s.bound) as db:before=counts(db,s)
    identity=ack(s);finish(s,identity)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id);plan=db.get(m.ObservationPlan,acquisition.data['observation_plan_id'])
        assert acquisition.status=='succeeded' and acquisition.data['closing_observation_id']==s.observation_id
        assert plan.status=='completed' and plan.data['result']['status']=='sufficient'
        assert db.get(m.Job,identity).status=='succeeded' and db.get(m.Job,identity).attempts==1
        assert db.get(m.Job,identity).data['settlement']['family']=='original_managed_acquisition'
        assert counts(db,s)==before and before['CloudCall']==0
        assert db.scalar(select(m.SensorReservation.id).where(m.SensorReservation.acquisition_id==acquisition.id)) is None
        assert db.scalar(select(m.DeviceReservation.id).where(m.DeviceReservation.acquisition_id==acquisition.id)) is None
        version=acquisition.version
    intake.deliver_pending(s.workspace_id);intake.deliver_pending(s.workspace_id)
    assert not worker.process_once(identity,kinds=[settlement.KIND],workspace_id=s.workspace_id,deliver=False)
    with Session(s.bound) as db:
        assert db.get(m.Acquisition,s.acquisition_id).version==version and counts(db,s)==before
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action=='acquisition.close',m.Audit.object_id==s.acquisition_id))==1
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


@pytest.mark.parametrize('pause_after_ack',[False,True])
def test_ordinary_managed_without_native_task_uses_original_pause_gate(client,supplier,pause_after_ack):
    s=ordinary_without_native_parent(client,supplier);extract(s);identity=ack(s)
    if pause_after_ack:
        instance=next(row for row in good(client.get('/api/instances')) if row['id']==s.value['r']['instance_id'])
        good(client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}))
    with Session(s.bound) as db:before=counts(db,s)
    finish(s,identity)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id)
        assert acquisition.status==('awaiting_observation' if pause_after_ack else 'succeeded')
        assert db.get(m.Job,identity).status==('failed' if pause_after_ack else 'succeeded') and counts(db,s)==before
        if not pause_after_ack:
            assert db.get(m.ObservationPlan,acquisition.data['observation_plan_id']).status=='completed'
        assert not list(db.scalars(select(m.Task).where(m.Task.workspace_id==s.workspace_id)))
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


@pytest.mark.parametrize('fault',['missing_memory','wrong_workspace','nonterminal'])
def test_missing_ack_scope_and_unfinished_device_never_fake_settlement(client,supplier,fault):
    s=ordinary(client,supplier,terminal=fault!='nonterminal');extract(s);identity=ack(s)
    with Session(s.bound) as db:
        if fault=='missing_memory':
            memory=db.scalar(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
                m.Memory.data['event_id'].as_string()==event(db,s).id,m.Memory.data['type'].as_string()=='observation_evidence'))
            db.delete(memory)
        if fault=='wrong_workspace':
            other=m.Workspace(name='explicit negative settlement scope');db.add(other);db.flush();foreign=other.id
        db.commit();before=counts(db,s)
    if fault=='wrong_workspace':
        assert not worker.process_once(identity,kinds=[settlement.KIND],workspace_id=foreign,deliver=False)
    else:finish(s,identity)
    with Session(s.bound) as db:
        assert db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation' and counts(db,s)==before
        expected={'missing_memory':'failed','wrong_workspace':'queued','nonterminal':'succeeded'}[fault]
        assert db.get(m.Job,identity).status==expected
        assert not db.scalar(select(m.Audit.id).where(m.Audit.action=='acquisition.close',m.Audit.object_id==s.acquisition_id))
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


def test_cancelled_original_plan_is_not_revived_by_later_local_settlement(client,supplier):
    s=ordinary(client,supplier);extract(s);identity=ack(s)
    current=good(client.get(s.value['base']))
    good(client.post(s.value['base']+'/stop',json={'request_id':str(uuid4()),'expected_version':current['version'],
        'reason':'停止原目标，独立收妥的原请求只核验收口'}))
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id);plan=db.get(m.ObservationPlan,acquisition.data['observation_plan_id'])
        # Goal stop keeps already-dispatched actions. Explicit persisted
        # cancelled-plan fault setup verifies that settlement cannot revive it.
        plan.status='cancelled';db.commit();before=counts(db,s)
    finish(s,identity)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id)
        assert acquisition.status=='succeeded' and db.get(m.ObservationPlan,acquisition.data['observation_plan_id']).status=='cancelled'
        assert counts(db,s)==before
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


def test_reducing_instance_hold_keeps_its_original_reception_path_without_settlement_job(client,supplier):
    s=ordinary(client,supplier);extract(s);identity=s.value['r']['instance_id']
    instance=next(row for row in good(client.get('/api/instances')) if row['id']==identity)
    good(client.post('/api/instances/'+identity+'/pause',json={'version':instance['version']}))
    intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        row=event(db,s)
        assert row.status=='received' and row.data.get('derivation_receipt')
        assert settle_job(db,s) is None and db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation'
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


def test_additional_unsettled_original_plan_row_blocks_whole_plan_completion(client,supplier):
    s=ordinary(client,supplier);extract(s);identity=ack(s)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id)
        # Negative corruption/safety sentinel only: this row represents an
        # unresolved inventory entry, never a claimed successful device call.
        unresolved=m.Acquisition(workspace_id=s.workspace_id,entity_id=acquisition.entity_id,capability_id=acquisition.capability_id,
            idempotency_key='negative-unresolved-'+uuid4().hex,status='unknown',data=copy.deepcopy(acquisition.data))
        db.add(unresolved);db.commit();before=counts(db,s)
    finish(s,identity)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id);plan=db.get(m.ObservationPlan,acquisition.data['observation_plan_id'])
        assert acquisition.status=='succeeded' and plan.status=='awaiting_observation'
        assert counts(db,s)==before
    assert len(supplier['calls'])==1 and not supplier.get('read_calls')


@pytest.fixture
def simulated_supplier(tmp_path,monkeypatch):
    app=create_app(tmp_path/'durable-simulator.sqlite3',TOKEN);calls=[]
    @app.middleware('http')
    async def count_original_requests(request,call_next):
        calls.append((request.method,request.url.path));return await call_next(request)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
        assert server.started,'actual simulator never started'
        base='http://127.0.0.1:'+str(port)
        monkeypatch.setattr(settings(),'development_connector_urls',base+'/acquisitions,'+base+'/acquisitions/receipts')
        monkeypatch.setenv('GA_CONNECTOR_SECRET_V245_ACK_TEST',TOKEN)
        yield NS(base=base,calls=calls)
    finally:
        server.should_exit=True;thread.join(timeout=6);listener.close()
        assert not thread.is_alive(),'actual simulator did not stop'


def mapped_scene(client,sim,*,partial=False):
    cfg=configuration();cfg['instanceProfile']={**cfg['instanceProfile'],'name':'新HTTP映射独立实例','simulation_v176':True}
    revision=save(client,cfg,review_automation=True);member=good(client.get('/api/instances/members'))[0]
    objects=[{'name':'环境监测点','fields':[{'label':'名称','kind':'文本','required':True},
        {'label':'温度','kind':'数值','unit':'°C'},{'label':'湿度','kind':'数值','unit':'%'}]}]
    body={'revision':revision,'objects':objects,'role_bindings':{'核验负责人':member['id']}}
    check=good(client.post('/api/instances/check',json=body))
    release=good(client.post('/api/instances',json={**body,'confirmed_configuration_hash':check['configuration_hash'],
        'confirm_configuration':True,'generation_intent':'new_instance','request_id':str(uuid4())}),201)
    enable(client,release);obj=object_row(client,release)
    environment=good(client.post('/api/workspace/simulation-v176/environments',json={'request_id':str(uuid4()),
        'instance_id':release['instance_id'],'release_id':release['id'],'note':'明确隔离模拟HTTP，不使用Sample','confirm_simulation':True}),201)
    me=good(client.get('/api/me'));p=Principal(me['user_id'],me['workspace_id'],'admin','mapped-ack-real-pg')
    fields={row['label']:row['key'] for row in release['snapshot']['object_types'][0]['fields']}
    temperature,humidity=fields['温度'],fields['湿度'];selected=[temperature,humidity]
    extractor=publish(client,manifest('tool',name='mapped_ack_rule_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules','side_effect':False},specification={'rules':[]},
        input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False}))
    with Session(engine()) as db:package=db.get(m.Capability,release['package_id']);package_hash=package.content_hash
    mapping={'schema':'http-observation-mapping-v177','scope':{'workspace_id':p.workspace_id,'environment_id':environment['id'],
        'instance_id':release['instance_id'],'release_id':release['id'],'package_id':release['package_id'],'package_hash':package_hash,
        'entity_type':obj['entity_type']},'links':{'temperature_c':{'field_key':temperature,'unit':'°C'},
        'humidity_percent':{'field_key':humidity,'unit':'%'}},'supplier_contract':'sim-acquisition-receipt-v176','time_semantics':'simulator_sample_time'}
    source=publish(client,manifest('source',name='mapped_ack_source_'+uuid4().hex[:8],allowed_agent_roles=['perception','data'],
        execution={'adapter':'http','endpoint':sim.base+'/acquisitions','receipt_endpoint':sim.base+'/acquisitions/receipts',
            'side_effect':True,'secret_ref':'GA_CONNECTOR_SECRET_V245_ACK_TEST'},input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','simulation':True,'test_only':True,'cloud_allowed':False,'external_allowed':False},
        specification={'timezone':'Asia/Shanghai','simulation_environment_id':environment['id'],'http_observation_mapping_v177':mapping,
            'http_observation_extractor_id_v177':extractor['id'],'http_observation_extractor_hash_v177':extractor['content_hash'],
            'observation_capability':{'fields':selected,'modalities':['numeric'],'actions':['observe'],'receipt_contract':'request_asset_terminal_v1'}}))
    asset=good(client.post('/api/sensing/assets',json={'source_id':source['id'],'external_id':'sim-weather-01','title':'明确模拟天气站',
        'reason':'隔离协议验收','specification':{'coverage_entity_ids':[obj['id']],'fields':[temperature] if partial else selected,
            'modalities':['numeric'],'actions':['observe'],'latency_seconds':30,'quote_ceiling_minor':0,'currency':'CNY','health':'healthy',
            'health_observed_at':(m.now()-timedelta(seconds=1)).isoformat(),'health_valid_until':(m.now()+timedelta(hours=1)).isoformat()}}),201)
    device=good(client.post('/api/sensing/devices',json={'title':'明确模拟统一设备','identity_scheme':'supplier_device_id',
        'identity_issuer':'新V245隔离模拟器','identity_reference':'sim-weather-01','reason':'不宣称实物设备'}),201)
    device=good(client.post('/api/sensing/devices/'+device['id']+'/approve',json={'expected_version':device['version'],'reason':'核对模拟身份'}))
    good(client.post('/api/sensing/assets/'+asset['id']+'/device',json={'expected_version':asset['version'],
        'device_id':device['id'],'device_expected_version':device['version'],'reason':'核对明确归属'}))
    policy=monitor(client,obj,source,[asset],fields=selected,max_attempts=2)
    goal={'purpose':'核对所选监测字段并由人工验收','entity_ids':[obj['id']],'required_fields':selected,'observation_mode':'allow_new',
        'evidence':{'source':'observations','minimum_verification':'unverified_extraction','time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}},
        'subgoals':[{'id':'check','description':'原字段核对','criterion_ids':['enough','human']}],
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'原要求证据充分'},
            {'id':'human','kind':'human_confirmation','description':'独立人工验收'}]}
    task=good(client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={'release_id':release['id'],
        'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],'title':'新普通HTTP补采任务',
        'request_id':str(uuid4()),'goal_spec':goal}),201)
    with Session(engine()) as db:
        identity=db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='check_native_business_goal',m.Job.status=='queued'))
    assert identity and worker.process_once(identity,kinds=['check_native_business_goal'],workspace_id=p.workspace_id,deliver=False)
    with Session(engine()) as db:
        acquisition=db.scalar(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
            m.Acquisition.data['policy_id'].as_string()==policy['id']))
        assert acquisition and acquisition.status=='authorized';acquisition_id=acquisition.id
        dispatch=db.scalar(select(m.Job.id).where(m.Job.idempotency_key=='dispatch:'+acquisition.id))
    assert worker.process_once(dispatch,kinds=['dispatch_acquisition'],workspace_id=p.workspace_id,deliver=False)
    with Session(engine()) as db:
        exchange=db.scalar(select(Exchange).where(Exchange.workspace_id==p.workspace_id,Exchange.acquisition_id==acquisition_id))
        assert exchange and exchange.status=='received' and exchange.observation_id
        child=db.scalar(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='process_http_mapped_observation_v177',
            m.Job.data['observation_id'].as_string()==exchange.observation_id))
        assert child and child.status=='queued';observation_id=exchange.observation_id;process_id=child.id
    return NS(bound=engine(),workspace_id=p.workspace_id,p=p,acquisition_id=acquisition_id,observation_id=observation_id,
        process_id=process_id,exchange_id=exchange.id,task=task,release=release,selected=selected,temperature=temperature)


@pytest.mark.parametrize('partial',[False,True])
def test_actual_v177_exchange_closes_original_after_ack_and_never_recollects_remaining_fields(client,simulated_supplier,partial):
    s=mapped_scene(client,simulated_supplier,partial=partial);extract(s)
    with Session(s.bound) as db:before=counts(db,s)
    identity=ack(s);finish(s,identity)
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,s.acquisition_id);plan=db.get(m.ObservationPlan,acquisition.data['observation_plan_id'])
        assert acquisition.status=='succeeded' and acquisition.data['closing_observation_id']==s.observation_id
        assert db.get(m.Job,identity).status=='succeeded' and db.get(m.Job,identity).data['settlement']['family']=='http_mapped_v177'
        assert counts(db,s)==before and before['CloudCall']==0
        assert db.get(Exchange,s.exchange_id).observation_id==s.observation_id
        if partial:
            assert plan.status=='awaiting_collection' and not plan.data.get('result',{}).get('status')=='sufficient'
        else:
            assert plan.status=='completed' and plan.data['result']['status']=='sufficient'
            assert not plan.data['result']['gaps']
            original=next(row for row in plan.data['result']['observation_evidence'] if row['observation_id']==s.observation_id)
            assert all(original['attributes'].get(field) is not None for field in s.selected)
    assert simulated_supplier.calls==[('POST','/acquisitions')]


def test_actual_v177_lost_producer_audit_after_ack_never_turns_status_into_settlement(client,simulated_supplier):
    s=mapped_scene(client,simulated_supplier);extract(s);identity=ack(s)
    with Session(s.bound) as db:
        producer=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,m.Audit.action=='observation.ingest',m.Audit.object_id==s.observation_id))
        assert producer;db.delete(producer);db.commit();before=counts(db,s)
    finish(s,identity)
    with Session(s.bound) as db:
        assert db.get(m.Job,identity).status=='failed' and db.get(m.Acquisition,s.acquisition_id).status=='awaiting_observation'
        assert counts(db,s)==before and event(db,s).status=='received'
    assert simulated_supplier.calls==[('POST','/acquisitions')]
