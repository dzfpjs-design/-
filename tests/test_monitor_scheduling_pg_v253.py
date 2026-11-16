"""Real approved policies and scoped production consumers; explicit HTTP simulation.

The baseline fixture has a real temperature producer and one response draft.
Mode changes never forge a V200 installation or alter approved_hash/observations.
"""
import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from datetime import datetime,timedelta
import pytest
from sqlalchemy import select,func,event,text
from sqlalchemy.orm import Session
from generic_agents import models as m,worker,workspace_monitoring
from generic_agents.contracts import digest
from generic_agents.monitor_scheduling_models_v253 import MonitorSchedulingHeadV253 as Head,MonitorSchedulingCommandV253 as Command
from generic_agents import monitor_scheduling_service_v253 as control
from generic_agents import workflow_monitor_schedule_v200 as v200
from generic_agents.workflow_monitor_schedule_models_v200 import WorkflowMonitorScheduleHeadV200,WorkflowMonitorScheduleInstallationV200
from tests.test_sensing_selection import supplier,monitor
from tests.test_vertical_slice import manifest,publish
from generic_agents.config import settings
from tests.test_anomaly_goal_handoff_v253 import (handoff_scene,simulated_supplier,good,save_goal,goal_spec,approve_and_start,
    response_goal,response_acquisitions,settle_and_check)


def path(s,policy):return '/api/sensing/monitor-policies/'+policy['id']+'/scheduling-control'

def choose_mode(s,policy,mode='goal_only'):
    before=good(s.client.get(path(s,policy),params={'instance_id':s.release['instance_id']}))
    body={'schema':'monitor-scheduling-command-v253','request_id':'原监测/方式?-'+uuid4().hex,
        'instance_id':s.release['instance_id'],'expected_version':before['control']['version'] if before['control'] else 0,
        'expected_policy_version':before['policy_version'],'approved_hash':before['approved_hash'],
        'mode':mode,'reason':'周期调度与已确认业务目标的补采分开；方式不是设备授权'}
    raw=json.dumps(body,ensure_ascii=False,separators=(', ',': '))+'\n'
    result=good(s.client.post(path(s,policy),content=raw.encode('utf8'),headers={'Content-Type':'application/json'}),201)
    return body,raw,result


def consume_scoped(s,kinds,*,limit=16):
    for _ in range(limit):
        if not worker.process_once(kinds=kinds,workspace_id=s.workspace,deliver=False):return
    raise AssertionError('bounded workspace-specific consumer did not quiesce')


def inventory(s):
    with Session(s.bound) as db:
        return {cls.__name__:[(r.id,r.version,digest(r.data)) for r in db.scalars(select(cls).where(
            cls.workspace_id==s.workspace).order_by(cls.id))] for cls in (Head,Command,m.MonitorPolicy,m.Job,m.ObservationPlan,m.Acquisition,m.Observation,m.Memory,m.Audit)}


def test_normal_directory_raw_recovery_and_paused_authority_do_not_rewrite_monitor_approval(handoff_scene):
    s=handoff_scene
    paths=s.client.app.openapi()['paths']
    assert '/api/sensing/monitor-policy-scheduling' in paths
    assert '/api/sensing/monitor-policies/{policy_id}/scheduling-control' in paths
    before=inventory(s)
    directory=good(s.client.get('/api/sensing/monitor-policy-scheduling',params={'instance_id':s.release['instance_id'],'limit':1}))
    assert directory['total']==2 and directory['has_more'] and len(directory['records'])==1
    entries=directory['records']+good(s.client.get('/api/sensing/monitor-policy-scheduling',params={
        'instance_id':s.release['instance_id'],'limit':1,'offset':1}))['records']
    assert {x['id'] for x in entries}=={s.monitor_a['id'],s.monitor_b['id']}
    assert {x['fields'][0]['label'] for x in entries}=={'温度','湿度'}
    assert {x['fields'][0]['unit'] for x in entries}=={'°C','%'}
    assert all(x['all_entities_in_instance'] and x['view']['effective_mode']=='legacy' and x['view']['control'] is None for x in entries)
    assert inventory(s)==before
    body,raw,result=choose_mode(s,s.monitor_b)
    assert result['mode']=='goal_only' and result['version']==result['epoch']==1
    after=inventory(s)
    assert after['MonitorPolicy']==before['MonitorPolicy']
    sha=hashlib.sha256(raw.encode()).hexdigest()
    original=good(s.client.get(path(s,s.monitor_b)+'/requests',params={'request_id':body['request_id'],'raw_sha256':sha}))
    assert original['found'] and original['raw']==raw and original['result']==result
    assert good(s.client.post(path(s,s.monitor_b),content=raw.encode(),headers={'Content-Type':'application/json'}))==result
    assert inventory(s)==after
    assert s.client.post(path(s,s.monitor_b),content=(raw+' ').encode(),headers={'Content-Type':'application/json'}).status_code==409
    unknown=good(s.client.get(path(s,s.monitor_b)+'/requests',params={'request_id':'未知/原号?'+uuid4().hex,'raw_sha256':sha}))
    assert not unknown['found'] and unknown['raw'] is None and unknown['result'] is None
    reader=s.reviewers[0][1]
    assert good(reader.get(path(s,s.monitor_b),params={'instance_id':s.release['instance_id']}))['can_change'] is False
    assert reader.get(path(s,s.monitor_b)+'/requests',params={'request_id':body['request_id'],'raw_sha256':sha}).status_code==404
    good(s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/revoke',json={
        'expected_version':s.monitor_b['version'],'reason':'撤销批准后只读原请求仍可恢复'}))
    saved=inventory(s)
    assert good(s.client.get(path(s,s.monitor_b)+'/requests',params={'request_id':body['request_id'],'raw_sha256':sha}))['result']==result
    assert good(s.client.post(path(s,s.monitor_b),content=raw.encode(),headers={'Content-Type':'application/json'}))==result
    assert inventory(s)==saved
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(WorkflowMonitorScheduleHeadV200).where(WorkflowMonitorScheduleHeadV200.workspace_id==s.workspace))==0
        assert db.scalar(select(func.count()).select_from(WorkflowMonitorScheduleInstallationV200).where(WorkflowMonitorScheduleInstallationV200.workspace_id==s.workspace))==0


def test_goal_only_blocks_old_queued_ticks_and_normal_scans_but_normal_scoped_goal_supplements(handoff_scene):
    s=handoff_scene
    assert workspace_monitoring.schedule_once(s.workspace)==2
    with Session(s.bound) as db:
        old=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind==workspace_monitoring.KIND)))
        assert len(old)==2
    choose_mode(s,s.monitor_a);choose_mode(s,s.monitor_b)
    assert workspace_monitoring.schedule_once(s.workspace)==0
    consume_scoped(s,[workspace_monitoring.KIND,'dispatch_acquisition'])
    with Session(s.bound) as db:
        tick_rows=[db.get(m.Job,j) for j in old]
        diagnostics=[{'status':j.status,'error':j.data.get('error'),'state':(j.data.get(workspace_monitoring.RESULT_KEY) or {}).get('state')} for j in tick_rows]
        assert all(j.status=='failed' and isinstance(j.data.get(workspace_monitoring.RESULT_KEY),dict)
            and j.data[workspace_monitoring.RESULT_KEY].get('state')=='blocked'
            and bool(j.data.get('error')) and j.data['error']==j.data[workspace_monitoring.RESULT_KEY].get('reason') for j in tick_rows),diagnostics
        for tick in tick_rows:
            anchors=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace,
                m.Audit.action==workspace_monitoring.CHECKED,m.Audit.object_id==tick.id)))
            assert len(anchors)==1 and anchors[0].actor_id==tick.data['actor_id']
            assert digest(anchors[0].data)==digest(tick.data[workspace_monitoring.RESULT_KEY])
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
        policy=db.get(m.MonitorPolicy,s.monitor_b['id'])
        assert v200.consume_policy_tick(db,s.p,policy) is True
        db.commit()
    assert s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/scan').status_code==409
    save_goal(s);approve_and_start(s)
    # Real workspace/kind claim, no exact Job selection and no fake consumer.
    consume_scoped(s,['check_native_business_goal','dispatch_acquisition','process_http_mapped_observation_v177'])
    ids=response_acquisitions(s);assert len(ids)==1
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,ids[0]);assert acquisition.capability_id==s.source_b['id']
        assert acquisition.status=='awaiting_observation'
        assert acquisition.data[control.KEY]['trigger']=='formal_goal'
        observation=db.scalar(select(m.Observation).where(m.Observation.workspace_id==s.workspace,m.Observation.data['acquisition_id'].as_string()==ids[0]))
        assert observation and observation.status=='processed';observation_id=observation.id
    assert response_goal(s)['check']['evidence']['status']=='insufficient'
    worker.deliver_events(workspace_id=s.workspace,observation_id=observation_id,event_kind='observation.data')
    consume_scoped(s,['settle_received_acquisition_v245','check_native_business_goal'])
    assert response_goal(s)['check']['evidence']['status']=='sufficient'
    assert workspace_monitoring.schedule_once(s.workspace)==0
    assert s.sim.calls==[('POST','/acquisitions'),('POST','/acquisitions')]
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace))==0


def test_ordinary_data_need_request_does_not_become_a_confirmed_business_goal(handoff_scene):
    s=handoff_scene;choose_mode(s,s.monitor_a);choose_mode(s,s.monitor_b)
    run=good(s.client.post('/api/data/queries',json={'question':'核对原对象湿度登记资料','entity_ids':[s.entity['id']],
        'fields':[s.humidity],'mode':'structured','business_timezone':'Asia/Shanghai',
        'evidence_requirement':{'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}}}))
    assert run['status']=='insufficient' and not run['data'].get('goal_delegation')
    worker.deliver_events(workspace_id=s.workspace,event_kind='data.gap')
    consume_scoped(s,['route_data_gap'])
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace,m.Job.kind=='route_data_gap')))
        assert jobs and all(j.status=='failed' for j in jobs)
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
    assert s.sim.calls==[('POST','/acquisitions')]


@pytest.mark.parametrize('fault',['missing_head','missing_command','missing_audit'])
def test_missing_independent_mode_evidence_never_downgrades_to_legacy(handoff_scene,fault):
    s=handoff_scene;body,raw,result=choose_mode(s,s.monitor_b)
    with Session(s.bound) as db:
        if fault=='missing_head':db.delete(db.get(Head,result['control_id']))
        elif fault=='missing_command':db.delete(db.get(Command,result['command_id']))
        else:
            audit=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace,m.Audit.action==control.ACTION,
                m.Audit.object_id==result['command_id']));db.delete(audit)
        db.commit()
    before=inventory(s)
    assert s.client.get(path(s,s.monitor_b),params={'instance_id':s.release['instance_id']}).status_code==409
    assert s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/scan').status_code==409
    assert inventory(s)==before
    assert s.sim.calls==[('POST','/acquisitions')]



def mode_diagnostics(s,policy_id,asset_id,device_id):
    """Limited records and reasons, never full transport bodies or credentials."""
    with Session(s.bound) as db:
        acquisitions=list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
            m.Acquisition.data['policy_id'].as_string()==policy_id)))
        ids={a.id for a in acquisitions}
        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace,
            m.Job.kind.in_([workspace_monitoring.KIND,'dispatch_acquisition']))))
        plans=list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id==s.workspace,
            m.ObservationPlan.policy_id==policy_id)))
        asset=db.scalar(select(m.SensorReservation).where(m.SensorReservation.workspace_id==s.workspace,m.SensorReservation.asset_id==asset_id))
        device=db.scalar(select(m.DeviceReservation).where(m.DeviceReservation.workspace_id==s.workspace,m.DeviceReservation.device_id==device_id))
        sources=list(db.scalars(select(m.SourceReservation).where(m.SourceReservation.workspace_id==s.workspace,
            m.SourceReservation.acquisition_id.in_(ids)))) if ids else []
        return {'jobs':[{'kind':j.kind,'status':j.status,'error':str(j.data.get('error'))[:400],
                'state':(j.data.get(workspace_monitoring.RESULT_KEY) or {}).get('state')}
            for j in jobs if j.data.get('policy_id')==policy_id or j.data.get('acquisition_id') in ids],
            'plans':[{'status':p.status,'reason':str(p.data.get('reason'))[:400],
                'options':[{'eligible':c.get('eligible'),'reasons':c.get('reasons',[])} for c in p.data.get('candidates',[])]} for p in plans],
            'acquisitions':[{'status':a.status,'reason':str(a.data.get('reason'))[:400]} for a in acquisitions],
            'asset_hold':asset.acquisition_id if asset else None,'device_hold':device.acquisition_id if device else None,
            'source_holds':[r.acquisition_id for r in sources]}


def test_periodic_new_epoch_makes_one_real_new_request_and_preserves_admitted_receipt(handoff_scene,supplier,monkeypatch):
    s=handoff_scene
    # A separate ordinary HTTP source uses the existing real loopback supplier
    # protocol. It has no V177 mapping/Goal requirement; no fake parent is added.
    allowed=','.join([supplier['url']+'/observe',supplier['url']+'/receipt',
        s.sim.base+'/acquisitions',s.sim.base+'/acquisitions/receipts'])
    monkeypatch.setattr(settings(),'development_connector_urls',allowed)
    choose_mode(s,s.monitor_a);choose_mode(s,s.monitor_b)
    source=publish(s.client,manifest('source',name='mode_plain_http_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'http','endpoint':supplier['url']+'/observe','receipt_endpoint':supplier['url']+'/receipt','side_effect':True},
        data_policy={'deployment':'local','simulation':True,'test_only':True,'cloud_allowed':False,'external_allowed':False},
        specification={'timezone':'Asia/Shanghai','observation_capability':{'fields':[s.humidity],
            'modalities':['numeric'],'actions':['observe'],'receipt_contract':'request_asset_terminal_v1'}}))
    asset=good(s.client.post('/api/sensing/assets',json={'source_id':source['id'],'external_id':'plain-'+uuid4().hex,
        'title':'明确模拟的普通HTTP采集接口','reason':'仅验证调度版本及真实HTTP请求，不生成观测或现场事实',
        'specification':{'coverage_entity_ids':[s.entity['id']],'fields':[s.humidity],'modalities':['numeric'],'actions':['observe'],
            'latency_seconds':30,'quote_ceiling_minor':0,'currency':'CNY','health':'healthy',
            'health_observed_at':(m.now()-timedelta(seconds=1)).isoformat(),
            'health_valid_until':(m.now()+timedelta(hours=3)).isoformat()}}),201)
    device=good(s.client.post('/api/sensing/devices',json={'title':'普通HTTP独立模拟设备','identity_scheme':'supplier_device_id',
        'identity_issuer':'V253普通HTTP隔离测试','identity_reference':asset['external_id'],'reason':'明确模拟协议身份'}),201)
    device=good(s.client.post('/api/sensing/devices/'+device['id']+'/approve',json={'expected_version':device['version'],'reason':'核对模拟接口身份'}))
    good(s.client.post('/api/sensing/assets/'+asset['id']+'/device',json={'expected_version':asset['version'],
        'device_id':device['id'],'device_expected_version':device['version'],'reason':'正常绑定独立设备，不手写占用'}))
    policy=good(s.client.post('/api/monitor-policies',json={'title':'按正式300秒冷却恢复周期采集',
        'entity_ids':[s.entity['id']],'source_ids':[source['id']],'fields':[s.humidity],'sensor_asset_ids':[asset['id']],
        'timezone':'Asia/Shanghai','cooldown_seconds':300,'max_acquisitions_per_day':5,
        'valid_until':(m.now()+timedelta(hours=3)).isoformat(),
        'selection_requirement':{'allowed_modalities':['numeric'],'allowed_actions':['observe'],'max_attempts':2,
            'plan_timeout_seconds':7200}}),201)
    policy=good(s.client.post('/api/monitor-policies/'+policy['id']+'/approve',json={
        'expected_version':policy['version'],'reason':'明确批准300秒冷却、3小时有效期和2小时计划期限'}))
    policy_hash=digest(policy['data'])
    _,_,first=choose_mode(s,policy,'periodic')
    old=good(s.client.post('/api/monitor-policies/'+policy['id']+'/scan'))['acquisition_ids'];assert len(old)==1
    with Session(s.bound) as db:
        assert db.get(m.Acquisition,old[0]).data[control.KEY]['control']['epoch']==first['epoch']
        assert not db.scalar(select(m.DeviceReservation.id).where(m.DeviceReservation.acquisition_id==old[0]))
    choose_mode(s,policy,'goal_only')
    consume_scoped(s,['dispatch_acquisition'])
    with Session(s.bound) as db:
        old_row=db.get(m.Acquisition,old[0]);assert old_row.status=='blocked_connector' and '监测方式' in old_row.data['reason']
        original=digest(old_row.data)
    assert supplier['calls']==[]
    _,_,fresh=choose_mode(s,policy,'periodic');assert fresh['epoch']==3
    assert workspace_monitoring.schedule_once(s.workspace)==1
    consume_scoped(s,[workspace_monitoring.KIND,'dispatch_acquisition'])
    with Session(s.bound) as db:
        rows=list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
            m.Acquisition.data['policy_id'].as_string()==policy['id'])))
        plans=list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id==s.workspace,
            m.ObservationPlan.policy_id==policy['id'])))
        resumed=next(p for p in plans if p.data.get(control.KEY,{}).get('control',{}).get('epoch')==3)
        diagnostics=mode_diagnostics(s,policy['id'],asset['id'],device['id'])
        assert len(rows)==1 and resumed.status=='waiting_resource',diagnostics
        assert any('对象与资产仍在冷却期' in c.get('reasons',[]) for c in resumed.data['candidates']),diagnostics
        assert digest(db.get(m.MonitorPolicy,policy['id']).data)==policy_hash
        assert not db.scalar(select(m.DeviceReservation.id).where(m.DeviceReservation.acquisition_id==old[0]))
        assert not db.scalar(select(m.SensorReservation.id).where(m.SensorReservation.acquisition_id==old[0]))
        tick=db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace,m.Job.kind==workspace_monitoring.KIND,
            m.Job.data['policy_id'].as_string()==policy['id']).order_by(m.Job.created_at.desc(),m.Job.id.desc()).limit(1))
        assert tick.status=='succeeded' and tick.data[workspace_monitoring.RESULT_KEY]['state']=='checked',diagnostics
        next_due=datetime.fromisoformat(tick.data[workspace_monitoring.RESULT_KEY]['checked_at'])+timedelta(seconds=300)
        initial_clock=db.scalar(select(func.clock_timestamp()))
    assert supplier['calls']==[],diagnostics
    # Observe actual elapsed wall time and the real database clock. No original
    # timestamp, policy, quota or production clock is rewritten to make it due.
    wait_budget=max(0,(next_due-initial_clock).total_seconds())+35
    monotonic_deadline=time.monotonic()+wait_budget
    while True:
        with Session(s.bound) as db:remaining=(next_due-db.scalar(select(func.clock_timestamp()))).total_seconds()
        if remaining<=0:break
        assert time.monotonic()<monotonic_deadline,{'next_due':next_due.isoformat(),'remaining':round(remaining,2)}
        time.sleep(min(1,remaining))
    assert workspace_monitoring.schedule_once(s.workspace)==1,mode_diagnostics(s,policy['id'],asset['id'],device['id'])
    consume_scoped(s,[workspace_monitoring.KIND,'dispatch_acquisition'])
    with Session(s.bound) as db:
        rows=list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
            m.Acquisition.data['policy_id'].as_string()==policy['id'])))
        assert len(rows)==2,mode_diagnostics(s,policy['id'],asset['id'],device['id'])
        new=next(x for x in rows if x.id!=old[0]);new_id=new.id
        assert new.status=='awaiting_observation' and new.data[control.KEY]['control']['epoch']==3
        assert new.data['observation_plan_id']!=db.get(m.Acquisition,old[0]).data['observation_plan_id']
        assert digest(db.get(m.Acquisition,old[0]).data)==original
        assert db.scalar(select(m.DeviceReservation).where(m.DeviceReservation.device_id==device['id'])).acquisition_id==new.id
        expected_key=new.idempotency_key
    assert len(supplier['calls'])==1 and supplier['calls'][0]['request_key']==expected_key
    choose_mode(s,policy,'goal_only')
    good(s.client.post('/api/acquisitions/'+new_id+'/reconcile'))
    with Session(s.bound) as db:
        assert db.get(m.Acquisition,new_id).status=='awaiting_observation'
        assert db.scalar(select(m.DeviceReservation).where(m.DeviceReservation.device_id==device['id'])).acquisition_id==new_id
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
            m.Acquisition.data['policy_id'].as_string()==policy['id']))==2
        assert not db.scalar(select(m.Observation.id).where(m.Observation.workspace_id==s.workspace,m.Observation.source_id==source['id']))
    assert len(supplier['calls'])==1 and len(supplier.get('read_calls',[]))==1
    assert s.sim.calls==[('POST','/acquisitions')]



def test_first_control_waits_for_policy_lock_then_old_undispatched_request_is_blocked(handoff_scene):
    s=handoff_scene
    identities=good(s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/scan'))['acquisition_ids']
    assert len(identities)==1
    def bounded(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout='15s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='25s'")
    event.listen(s.bound,'begin',bounded)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with Session(s.bound) as owner:
                policy=owner.scalar(select(m.MonitorPolicy).where(m.MonitorPolicy.id==s.monitor_b['id'],
                    m.MonitorPolicy.workspace_id==s.workspace).with_for_update())
                owner_pid=owner.scalar(select(func.pg_backend_pid()))
                # This is the actual Policy serialization seam, not a claim
                # that a device was sent. No row/slot/status is fabricated.
                assert control.runtime_pin(owner,s.p,policy) is None
                future=executor.submit(choose_mode,s,s.monitor_b,'goal_only')
                deadline=time.monotonic()+6;blocked=False
                while time.monotonic()<deadline and not future.done():
                    with Session(s.bound) as observer:
                        blocked=observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE a.wait_event_type='Lock' AND :pid = ANY(pg_blocking_pids(a.pid)))"),{'pid':owner_pid})
                    if blocked:break
                    time.sleep(.02)
                assert blocked,'new control did not wait on the existing Policy lock'
                assert not future.done()
                owner.commit()
            _,_,saved=future.result(timeout=35)
            assert saved['epoch']==1 and saved['mode']=='goal_only'
        consume_scoped(s,['dispatch_acquisition'])
        with Session(s.bound) as db:
            original=db.get(m.Acquisition,identities[0])
            assert original.status=='blocked_connector' and not original.data.get('dispatch_started_at')
            proof=db.scalar(select(m.Audit).where(m.Audit.workspace_id==s.workspace,
                m.Audit.object_id==original.id,m.Audit.action=='monitor.scheduling_action_blocked_v253'))
            assert proof and proof.data['post_sent'] is False
            assert not db.scalar(select(m.DeviceReservation.id).where(m.DeviceReservation.acquisition_id==original.id))
        assert s.sim.calls==[('POST','/acquisitions')]
    finally:event.remove(s.bound,'begin',bounded)



def test_cross_business_policy_is_one_readable_group_but_cannot_be_changed_here(handoff_scene):
    s=handoff_scene
    package=publish(s.client,manifest(namespace='mode_other_'+uuid4().hex[:8],specification={'entity_schemas':{
        'site':{'type':'object','required':['name'],'additionalProperties':False,
            'properties':{'name':{'type':'string'},s.humidity:{'type':'number'}}}}}))
    other=good(s.client.post('/api/entities',json={'package_id':package['id'],'entity_type':'site','external_id':uuid4().hex,
        'attributes':{'name':'另一个业务范围的真实登记对象'}}),201)
    with Session(s.bound) as db:
        original_entity=db.get(m.Entity,s.entity['id']);other_entity=db.get(m.Entity,other['id'])
        assert original_entity.workspace_id==other_entity.workspace_id==s.workspace
        assert other_entity.namespace!=original_entity.namespace
    source=publish(s.client,manifest('source',name='cross_scope_records_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai',
            'observation_capability':{'fields':[s.humidity],'modalities':['numeric'],'actions':['observe']}}))
    policy=good(s.client.post('/api/monitor-policies',json={'title':'跨业务范围的整组只读策略',
        'entity_ids':[s.entity['id'],other['id']],'source_ids':[source['id']],'fields':[s.humidity],
        'valid_until':(m.now()+timedelta(days=1)).isoformat(),'max_acquisitions_per_day':1}),201)
    policy=good(s.client.post('/api/monitor-policies/'+policy['id']+'/approve',json={
        'expected_version':policy['version'],'reason':'正常批准记录来源；没有登记可控设备'}))
    directory=good(s.client.get('/api/sensing/monitor-policy-scheduling',params={'instance_id':s.release['instance_id']}))
    entry=next(x for x in directory['records'] if x['id']==policy['id'])
    assert not entry['all_entities_in_instance'] and not entry['view']['can_change']
    assert {x['in_instance'] for x in entry['objects']}=={True,False}
    assert {x['title'] for x in entry['objects']} >= {'另一个业务范围的真实登记对象'}
    before=inventory(s)
    v=entry['view']
    response=s.client.post(path(s,policy),json={'schema':'monitor-scheduling-command-v253','request_id':str(uuid4()),
        'instance_id':s.release['instance_id'],'expected_version':0,'expected_policy_version':v['policy_version'],
        'approved_hash':v['approved_hash'],'mode':'goal_only','reason':'不得从一个业务页面修改其他业务的整组策略'})
    assert response.status_code==403,response.text
    assert inventory(s)==before



@pytest.mark.parametrize('remaining',['plan','acquisition','action_audit','tick_job','tick_audit'])
def test_lost_entire_mode_ledger_with_independent_control_trace_never_becomes_legacy(handoff_scene,remaining):
    s=handoff_scene
    # Genuine legacy evidence/Plans already exist for A. It must remain readable
    # before any control ever existed for B, without materializing a Head.
    initial=good(s.client.get(path(s,s.monitor_b),params={'instance_id':s.release['instance_id']}))
    assert initial['effective_mode']=='legacy' and initial['control'] is None
    _,_,saved=choose_mode(s,s.monitor_b,'periodic')
    ids=good(s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/scan'))['acquisition_ids'];assert len(ids)==1
    assert workspace_monitoring.schedule_once(s.workspace)==2
    with Session(s.bound) as db:
        acquisition=db.get(m.Acquisition,ids[0]);plan=db.get(m.ObservationPlan,acquisition.data['observation_plan_id'])
        assert control.KEY in acquisition.data and control.KEY in plan.data
        # This is deliberate persisted data-loss/corruption, never a success
        # fixture or a re-signed historical receipt. Leave one independent trace.
        if remaining!='plan':
            data=copy.deepcopy(plan.data);data.pop(control.KEY);plan.data=data
        if remaining!='acquisition':
            data=copy.deepcopy(acquisition.data);data.pop(control.KEY);acquisition.data=data
        for proof in db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace,
                m.Audit.action==control.ACTION_PIN,m.Audit.object_id==acquisition.id)):
            if remaining!='action_audit':db.delete(proof)
        ticks=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace,m.Job.kind==workspace_monitoring.KIND,
            m.Job.data['policy_id'].as_string()==s.monitor_b['id'])))
        assert len(ticks)==1
        if remaining!='tick_job':
            data=copy.deepcopy(ticks[0].data);data[workspace_monitoring.KEY]['schedule_hint'].pop('control');ticks[0].data=data
        tick_proofs=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace,m.Audit.action==workspace_monitoring.QUEUED,
            m.Audit.object_id==ticks[0].id)))
        assert len(tick_proofs)==1
        if remaining!='tick_audit':
            data=copy.deepcopy(tick_proofs[0].data);data['pin']['schedule_hint'].pop('control');tick_proofs[0].data=data
        for command in db.scalars(select(Command).where(Command.workspace_id==s.workspace,Command.policy_id==s.monitor_b['id'])):db.delete(command)
        for proof in db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace,m.Audit.action==control.ACTION,
                m.Audit.data['policy_id'].as_string()==s.monitor_b['id'])):db.delete(proof)
        db.delete(db.get(Head,saved['control_id']));db.commit()
    damaged=inventory(s)
    assert s.client.get(path(s,s.monitor_b),params={'instance_id':s.release['instance_id']}).status_code==409
    assert s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/scan').status_code==409
    assert inventory(s)==damaged
    assert s.sim.calls==[('POST','/acquisitions')]
