"""Real V177 loopback HTTP -> anomaly -> independent ACK -> V175 draft.

No successful Task, exchange, extraction, Event, Memory or approval is forged.
Root executes on disposable PG. The supplier is explicitly simulated, and
bounded product worker calls are not autonomous/field-effect acceptance.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from threading import Event as ThreadEvent
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import func,select,text
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from generic_agents import models as m,worker,signal_approval_routing_v247 as routing
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.signal_approval_models_v247 import (SignalApprovalRouteV247 as Route,
    SignalApprovalOccurrenceV247 as Occurrence,SignalApprovalCommandV247 as Command)
from generic_agents.security import Principal
from tests.test_acquisition_ack_settlement_pg_v245 import good,simulated_supplier
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import operator,command as approval_command
from tests.test_instance_workspace import configuration,save,object_row
from tests.test_sensing_selection import monitor
from tests.test_vertical_slice import manifest,publish


def test_fastapi_mount_has_exact_public_paths_and_no_duplicate_workspace_prefix(client):
    # workspace_api exports an unprefixed aggregate router after wrapping its
    # original prefixed workspace_routes. Assert the real app before any HTTP
    # supplier or scene is constructed, rather than inspecting one subrouter.
    paths=set(client.app.openapi()['paths'])
    assert '/api/workspace/instances/{identity}/signal-approval-routing/options' in paths
    assert '/api/workspace/tasks/{task_id}/signal-approval-source' in paths
    assert not any('/api/workspace/api/workspace/' in path and 'signal-approval' in path for path in paths)


def prefix(s):return '/api/workspace/instances/'+s.release['instance_id']+'/signal-approval-routing'


def configure_policy(s):
    unit=str(uuid4());base='/api/workspace/approval-settings-v175'
    good(s.client.post(base+'/units',json={'request_id':str(uuid4()),'unit_id':unit,'expected_version':0,
        'name':'异常三级核对范围','parent_id':None,'enabled':True}))
    reviewers=[]
    for number in range(3):
        account,client=operator(s,s.stack);reviewers.append((account,client))
        good(s.client.post(base+'/roles',json={'request_id':str(uuid4()),'grant_id':str(uuid4()),'expected_version':0,
            'unit_id':unit,'user_id':account['id'],'role_key':'review_'+str(number),'enabled':True}))
    acceptor,acceptor_client=operator(s,s.stack)
    for identity,role in [(s.p.user_id,'execute'),(acceptor['id'],'accept')]:
        good(s.client.post(base+'/roles',json={'request_id':str(uuid4()),'grant_id':str(uuid4()),'expected_version':0,
            'unit_id':unit,'user_id':identity,'role_key':role,'enabled':True}))
    stage=lambda label,role:{'name':label,'role_key':role,'scope_id':unit}
    body={'request_id':str(uuid4()),'instance_id':s.release['instance_id'],'business_id':s.business_id,'expected_version':0,
        'name':'异常先存草稿再三级核对','scope_id':unit,
        'stages':[stage('第'+str(index+1)+'级核对','review_'+str(index)) for index in range(3)],
        'executor':stage('明确启动','execute'),'acceptance':stage('独立验收','accept'),'enabled':True}
    s.policy=good(s.client.post(base+'/policies',json=body));s.policy_body=body
    s.reviewers=reviewers;s.acceptor=acceptor;s.acceptor_client=acceptor_client


def enable_route(s,*,max_tasks=10):
    options=good(s.client.get(prefix(s)+'/options'))
    body={'schema':'signal-approval-enable-v247','request_id':str(uuid4()),'expected_version':options['instance_version'],
        'release_id':s.release['id'],'source_id':s.source['id'],'extractor_id':s.extractor['id'],
        'entity_ids':[s.entity['id']],'business_id':s.business_id,'valid_for_hours':24,'max_tasks':max_tasks,
        'reason':'明确将这项模拟设备的新异常交给业务人员审批，不自动启动','options_hash':options['options_hash'],
        'confirm_approval_drafts':True}
    raw=json.dumps(body,ensure_ascii=False,separators=(', ',': '))+'\n'
    response=s.client.post(prefix(s)+'/enable',content=raw.encode(),headers={'Content-Type':'application/json'})
    result=good(response,201);s.route=result['route'];s.original=(body,raw,result)
    return result


def build_scene(client,sim,stack,*,configuration_transform=None,route_business_name=None,authorize=True):
    cfg=configuration();cfg['instanceProfile']={**cfg['instanceProfile'],'name':'V247新HTTP异常审批实例','simulation_v176':True}
    if configuration_transform is not None:cfg=configuration_transform(client,cfg)
    revision=save(client,cfg,review_automation=True);member=good(client.get('/api/instances/members'))[0]
    objects=[{'name':'环境监测点','fields':[{'label':'名称','kind':'文本','required':True},
        {'label':'温度','kind':'数值','unit':'°C'},{'label':'湿度','kind':'数值','unit':'%'}]}]
    body={'revision':revision,'objects':objects,'role_bindings':{'核验负责人':member['id']}}
    checked=good(client.post('/api/instances/check',json=body))
    release=good(client.post('/api/instances',json={**body,'confirmed_configuration_hash':checked['configuration_hash'],
        'confirm_configuration':True,'generation_intent':'new_instance','request_id':str(uuid4())}),201)
    instance=next(row for row in good(client.get('/api/instances')) if row['id']==release['instance_id'])
    good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
    entity=object_row(client,release)
    environment=good(client.post('/api/workspace/simulation-v176/environments',json={'request_id':str(uuid4()),
        'instance_id':release['instance_id'],'release_id':release['id'],'note':'真实隔离模拟HTTP协议，不是现场设备','confirm_simulation':True}),201)
    me=good(client.get('/api/me'));p=Principal(me['user_id'],me['workspace_id'],'admin','signal-approval-real-pg')
    fields={field['label']:field['key'] for field in release['snapshot']['object_types'][0]['fields']}
    temperature,humidity=fields['温度'],fields['湿度'];selected=[temperature,humidity]
    extractor=publish(client,manifest('tool',name='approval_numeric_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules','side_effect':False},
        specification={'rules':[{'id':'high_temperature','field':temperature,'operator':'gt','threshold':20,'unit':'°C'}]},
        input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False}))
    with Session(engine()) as db:package_hash=db.get(m.Capability,release['package_id']).content_hash
    mapping={'schema':'http-observation-mapping-v177','scope':{'workspace_id':p.workspace_id,'environment_id':environment['id'],
        'instance_id':release['instance_id'],'release_id':release['id'],'package_id':release['package_id'],'package_hash':package_hash,
        'entity_type':entity['entity_type']},'links':{'temperature_c':{'field_key':temperature,'unit':'°C'},
        'humidity_percent':{'field_key':humidity,'unit':'%'}},'supplier_contract':'sim-acquisition-receipt-v176','time_semantics':'simulator_sample_time'}
    source=publish(client,manifest('source',name='approval_http_'+uuid4().hex[:8],allowed_agent_roles=['perception','data'],
        execution={'adapter':'http','endpoint':sim.base+'/acquisitions','receipt_endpoint':sim.base+'/acquisitions/receipts',
            'side_effect':True,'secret_ref':'GA_CONNECTOR_SECRET_V245_ACK_TEST'},input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','simulation':True,'test_only':True,'cloud_allowed':False,'external_allowed':False},
        specification={'timezone':'Asia/Shanghai','simulation_environment_id':environment['id'],'http_observation_mapping_v177':mapping,
            'http_observation_extractor_id_v177':extractor['id'],'http_observation_extractor_hash_v177':extractor['content_hash'],
            'observation_capability':{'fields':selected,'modalities':['numeric'],'actions':['observe'],'receipt_contract':'request_asset_terminal_v1'}}))
    asset=good(client.post('/api/sensing/assets',json={'source_id':source['id'],'external_id':'sim-weather-01','title':'明确模拟天气站',
        'reason':'隔离来源协议验收','specification':{'coverage_entity_ids':[entity['id']],'fields':selected,'modalities':['numeric'],
            'actions':['observe'],'latency_seconds':30,'quote_ceiling_minor':0,'currency':'CNY','health':'healthy',
            'health_observed_at':(m.now()-timedelta(seconds=1)).isoformat(),'health_valid_until':(m.now()+timedelta(hours=1)).isoformat()}}),201)
    device=good(client.post('/api/sensing/devices',json={'title':'模拟统一设备','identity_scheme':'supplier_device_id',
        'identity_issuer':'V247隔离模拟器','identity_reference':'sim-weather-01','reason':'工程协议验收，不称实物'}),201)
    device=good(client.post('/api/sensing/devices/'+device['id']+'/approve',json={'expected_version':device['version'],'reason':'核对模拟身份'}))
    good(client.post('/api/sensing/assets/'+asset['id']+'/device',json={'expected_version':asset['version'],
        'device_id':device['id'],'device_expected_version':device['version'],'reason':'核对来源归属'}))
    monitor_policy=monitor(client,entity,source,[asset],fields=selected,max_attempts=2)
    goal={'purpose':'核对本次温湿度资料并由人验收','entity_ids':[entity['id']],'required_fields':selected,'observation_mode':'allow_new',
        'evidence':{'source':'observations','minimum_verification':'unverified_extraction','time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}},
        'subgoals':[{'id':'check','description':'核对温湿度','criterion_ids':['enough','human']}],
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'所需字段资料充分'},
            {'id':'human','kind':'human_confirmation','description':'独立人工验收'}]}
    # The original authorized acquisition Task precedes the new routing policy.
    parent=good(client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={'release_id':release['id'],
        'business_id':release['available_business_ids'][0],'entity_ids':[entity['id']],'title':'原明确批准的HTTP补采任务',
        'request_id':str(uuid4()),'goal_spec':goal}),201)
    with Session(engine()) as db:
        goal_job=db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='check_native_business_goal',
            m.Job.data['task_id'].as_string()==parent['id'],m.Job.status=='queued'))
    assert goal_job and worker.process_once(goal_job,kinds=['check_native_business_goal'],workspace_id=p.workspace_id,deliver=False)
    with Session(engine()) as db:
        acquisition=db.scalar(select(m.Acquisition).where(m.Acquisition.workspace_id==p.workspace_id,
            m.Acquisition.data['policy_id'].as_string()==monitor_policy['id']))
        assert acquisition and acquisition.status=='authorized'
        acquisition_id=acquisition.id
        dispatch=db.scalar(select(m.Job.id).where(m.Job.idempotency_key=='dispatch:'+acquisition.id))
    business=next(row for row in release['snapshot']['businesses'] if row['name']==(route_business_name or '资料核验'))
    s=NS(client=client,stack=stack,workspace=p.workspace_id,workspace_id=p.workspace_id,p=p,bound=engine(),sim=sim,
        release=release,entity=entity,source=source,extractor=extractor,environment=environment,business_id=business['id'],
        selected=selected,temperature=temperature,humidity=humidity,parent=parent,acquisition_id=acquisition_id,dispatch_id=dispatch)
    configure_policy(s)
    if authorize:enable_route(s)
    return s


@pytest.fixture
def scene(client,simulated_supplier):
    with ExitStack() as stack:yield build_scene(client,simulated_supplier,stack)


def collect(s):
    assert worker.process_once(s.dispatch_id,kinds=['dispatch_acquisition'],workspace_id=s.workspace,deliver=False)
    with Session(s.bound) as db:
        exchange=db.scalar(select(Exchange).where(Exchange.workspace_id==s.workspace,Exchange.acquisition_id==s.acquisition_id))
        assert exchange.status=='received' and exchange.observation_id
        s.observation_id=exchange.observation_id;s.exchange_id=exchange.id
        job=db.scalar(select(m.Job).where(m.Job.workspace_id==s.workspace,m.Job.kind=='process_http_mapped_observation_v177',
            m.Job.data['observation_id'].as_string()==s.observation_id))
        process_id=job.id
    assert worker.process_once(process_id,kinds=['process_http_mapped_observation_v177'],workspace_id=s.workspace,deliver=False)
    worker.deliver_events(workspace_id=s.workspace,observation_id=s.observation_id,event_kind='perception.anomaly')
    with Session(s.bound) as db:
        signal=db.scalar(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id==s.workspace,
            m.PerceptionSignal.data['basis']['observation_id'].as_string()==s.observation_id))
        assert signal and signal.data['basis']['simulation'] is True
        s.signal_id=signal.id
        occurrence=db.scalar(select(Occurrence).where(Occurrence.workspace_id==s.workspace,Occurrence.signal_id==signal.id))
        s.occurrence_id=occurrence.id if occurrence else None
    return s


def process_pending(s):
    with Session(s.bound) as db:
        ids=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind==routing.KIND,
            m.Job.status=='queued',m.Job.data['occurrence_id'].as_string()==s.occurrence_id).order_by(m.Job.created_at,m.Job.id)))
    for identity in ids:assert worker.process_once(identity,kinds=[routing.KIND],workspace_id=s.workspace,deliver=False)


def ack(s):worker.deliver_events(workspace_id=s.workspace,observation_id=s.observation_id,event_kind='observation.data')


def occurrence(s):return good(s.client.get(prefix(s)+'/occurrences/'+s.occurrence_id))


def no_new_effects(s):
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace))==0
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id==s.workspace))==1
    assert s.sim.calls==[('POST','/acquisitions')]


def test_real_anomaly_waits_for_ack_then_one_real_approval_draft_and_normal_three_levels(scene):
    s=collect(scene);assert occurrence(s)['status']=='frozen'
    process_pending(s);assert occurrence(s)['status']=='waiting_data'
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==1
        assert not db.scalar(select(m.Memory.id).where(m.Memory.workspace_id==s.workspace,
            m.Memory.data['observation_id'].as_string()==s.observation_id,m.Memory.data['type'].as_string()=='observation_evidence'))
    ack(s);process_pending(s);saved=occurrence(s)
    assert saved['status']=='approval_draft' and saved['task_id'] and saved['task_path']=='/api/workspace/tasks/'+saved['task_id']
    path=saved['task_path'];approval=path+'/approval-v175'
    task=good(s.client.get(path));assert task['status']=='approval_draft'
    source=good(s.client.get(path+'/signal-approval-source'))
    assert source['found'] and source['source']['data_received'] and source['source']['simulation']
    assert next(field['value'] for field in source['source']['fields'] if field['key']==s.temperature)==25
    route_state=good(s.client.get(prefix(s)))
    good(s.client.post(prefix(s)+'/'+s.route['id']+'/pause',json={'schema':'signal-approval-pause-v247',
        'request_id':str(uuid4()),'expected_version':route_state['instance_version'],'reason':'暂停新增草稿，原任务继续正规办理'}))
    original=good(s.client.get(approval));draft=copy.deepcopy(original['value']);draft['note']='已保存人工核查安排，等待逐级审核'
    approval_command(s.client,approval,'revise',value=draft)
    assert good(s.client.get(approval))['value']['note']==draft['note']
    approval_command(s.client,approval,'submit')
    submission=good(s.client.get(approval))['submission_hash']
    for account,reviewer in s.reviewers:
        assert good(reviewer.get(path+'/signal-approval-source'))['source']['receipt_hash']==source['source']['receipt_hash']
        approval_command(reviewer,approval,'decide',submission_hash=submission,decision='approve')
    with Session(s.bound) as db:
        assert not db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.data['task_id'].as_string()==saved['task_id']))
    approval_command(s.client,approval,'start')
    assert good(s.client.get(approval))['status']=='executing'
    # Neither draft edits, another role, Task version growth nor route pause
    # rewrite the original observation/provider anchor.
    assert good(s.acceptor_client.get(path+'/signal-approval-source'))['source']==source['source']
    ack(s);routing.poll_once(s.workspace);process_pending(s)
    assert occurrence(s)['task_id']==saved['task_id']
    with Session(s.bound) as db:
        assert len(db.get(Route,s.route['id']).data['task_ids'])==1
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
    no_new_effects(s)


def test_raw_original_recovery_and_unknown_nonce_do_not_rewrite_authority(scene):
    s=scene;body,raw,result=s.original;sha=hashlib.sha256(raw.encode()).hexdigest()
    recovered=good(s.client.get(prefix(s)+'/requests/'+body['request_id'],params={'raw_sha256':sha}))
    assert recovered['found'] and recovered['raw']==raw and recovered['result']==result
    assert good(s.client.post(prefix(s)+'/enable',content=raw.encode(),headers={'Content-Type':'application/json'}),201)==result
    changed=raw+' '
    assert s.client.post(prefix(s)+'/enable',content=changed.encode(),headers={'Content-Type':'application/json'}).status_code==409
    unknown=good(s.client.get(prefix(s)+'/requests/'+str(uuid4()),params={'raw_sha256':sha}))
    assert unknown['found'] is False and unknown['result'] is None
    other=s.reviewers[0][1]
    assert other.get(prefix(s)+'/requests/'+body['request_id'],params={'raw_sha256':sha}).status_code==403


@pytest.mark.parametrize('fault',['missing_memory','bad_pending','wrong_producer_actor','missing_producer'])
def test_corrupt_data_or_producer_never_creates_a_draft_or_repairs_receipt(scene,fault):
    s=collect(scene);process_pending(s)
    if fault=='missing_memory':ack(s)
    with Session(s.bound) as db:
        row=db.get(Occurrence,s.occurrence_id)
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==s.workspace,m.Event.kind=='observation.data',
            m.Event.data['observation_id'].as_string()==s.observation_id))
        if fault=='missing_memory':
            memory=db.scalar(select(m.Memory).where(m.Memory.workspace_id==s.workspace,m.Memory.data['event_id'].as_string()==event.id,
                m.Memory.data['type'].as_string()=='observation_evidence'));assert memory;db.delete(memory)
        elif fault=='bad_pending':
            value=copy.deepcopy(event.data);value['extracted']['attributes'][s.temperature]=999;event.data=value
        else:
            proof=db.get(m.Audit,row.data['producer_audit_id'])
            if fault=='missing_producer':db.delete(proof)
            else:proof.actor_id=s.reviewers[0][0]['id']
        db.commit()
    # Explicit retry uses a new human command but the same frozen occurrence.
    if fault in {'wrong_producer_actor','missing_producer'}:
        assert s.client.get(prefix(s)+'/occurrences/'+s.occurrence_id).status_code==409
    else:
        view=occurrence(s)
        good(s.client.post(prefix(s)+'/occurrences/'+s.occurrence_id+'/retry',json={'schema':'signal-approval-retry-v247',
            'request_id':str(uuid4()),'expected_version':view['version'],'reason':'核对原始数据，不重新采集'}))
        process_pending(s);assert occurrence(s)['status']=='blocked_integrity'
    with Session(s.bound) as db:
        assert not db.get(Occurrence,s.occurrence_id).task_id
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==1
    no_new_effects(s)


@pytest.mark.parametrize('fault',['pause','disabled_policy','human_reject'])
def test_waiting_permission_changes_never_fall_back_to_direct_execution(scene,fault):
    s=collect(scene);process_pending(s)
    if fault=='pause':
        current=good(s.client.get(prefix(s)))
        good(s.client.post(prefix(s)+'/'+s.route['id']+'/pause',json={'schema':'signal-approval-pause-v247',
            'request_id':str(uuid4()),'expected_version':current['instance_version'],'reason':'先暂停核对'}))
    elif fault=='disabled_policy':
        good(s.client.post('/api/workspace/approval-settings-v175/policies',json={**s.policy_body,
            'request_id':str(uuid4()),'expected_version':s.policy['version'],'enabled':False}))
    else:
        with Session(s.bound) as db:version=db.get(m.Observation,s.observation_id).version
        good(s.client.post('/api/observations/'+s.observation_id+'/verify',json={'expected_version':version,
            'decision':'rejected','reason':'人工核对本次登记不能采用'}))
    ack(s);process_pending(s)
    assert occurrence(s)['status'] in {'blocked_authority','blocked_integrity'} and not occurrence(s)['task_id']
    with Session(s.bound) as db:assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==1
    no_new_effects(s)


@pytest.mark.parametrize('damage',['missing','empty_rehashed','wrong_hash'])
def test_independent_active_grant_cannot_be_hidden_by_instance_marker(scene,damage):
    s=scene
    with Session(s.bound) as db:
        instance=db.get(m.DomainInstance,s.release['instance_id']);value=copy.deepcopy(instance.data)
        if damage=='missing':value.pop(routing.KEY)
        elif damage=='empty_rehashed':value[routing.KEY]=routing.sealed({'schema':'signal-approval-lock-v247','routes':[]},'hash')
        else:value[routing.KEY]['hash']='0'*64
        instance.data=value;db.commit()
    with Session(s.bound) as db:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as blocked:
            routing.assert_no_active(db,s.p,db.get(m.DomainInstance,s.release['instance_id']),
                s.source['id'],s.extractor['id'],[s.entity['id']])
        assert blocked.value.status_code==409
    opts=good(s.client.get(prefix(s)+'/options'));assert not opts['can_enable']


def test_real_registration_before_enable_is_not_adopted_by_a_later_anomaly_receipt(client,simulated_supplier):
    with ExitStack() as stack:
        s=build_scene(client,simulated_supplier,stack,authorize=False);collect(s)
        assert s.occurrence_id is None
        enable_route(s)
        with Session(s.bound) as db:
            signal=db.get(m.PerceptionSignal,s.signal_id);original=db.get(m.Observation,s.observation_id)
            event=db.get(m.Event,signal.event_id);instance=db.get(m.DomainInstance,s.release['instance_id'])
            assert routing.freeze(db,instance,event,original,signal,signal.data['basis']) is None
            db.commit()
            assert db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.workspace_id==s.workspace))==0
        ack(s);routing.poll_once(s.workspace)
        with Session(s.bound) as db:assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==1
        no_new_effects(s)


def test_stored_ack_and_queued_job_resume_in_a_new_consumer_without_another_http_request(scene):
    s=collect(scene);process_pending(s);ack(s)
    # No device call or receipt rebuild: the already committed ACK and queued
    # local Job are consumed through fresh independent worker Sessions.
    routing.poll_once(s.workspace);process_pending(s)
    first=occurrence(s);assert first['status']=='approval_draft'
    routing.poll_once(s.workspace);process_pending(s)
    assert occurrence(s)['task_id']==first['task_id']
    no_new_effects(s)


def test_real_created_occurrence_prevents_erasing_used_quota_with_a_resigned_route_list(client,simulated_supplier):
    from fastapi import HTTPException
    with ExitStack() as stack:
        s=build_scene(client,simulated_supplier,stack,authorize=False);enable_route(s,max_tasks=1)
        collect(s);process_pending(s);ack(s);process_pending(s)
        assert occurrence(s)['status']=='approval_draft'
        with Session(s.bound) as db:
            route=db.get(Route,s.route['id']);assert len(route.data['task_ids'])==1
            routing.seal(route,{**route.data,'task_ids':[]})  # explicit isolated integrity fault
            db.commit()
        with Session(s.bound) as db:
            with pytest.raises(HTTPException) as failure:routing.grant(db,s.p,db.get(Route,s.route['id']))
            assert failure.value.status_code==409
            assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
        assert s.client.get(prefix(s)).status_code==409
        no_new_effects(s)


def test_two_real_owned_consumer_jobs_serialize_one_draft_and_one_quota(scene):
    s=collect(scene);ack(s)
    with Session(s.bound) as db:
        identities=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind==routing.KIND,
            m.Job.status=='queued',m.Job.data['occurrence_id'].as_string()==s.occurrence_id)))
        assert len(identities)==2  # original intent and exact same-observation ACK wake
    def limits(connection):
        # Functional serialization budget, not a 2.5-second performance SLO.
        # The observed normal attempt completed inside the prior 12s bound.
        connection.exec_driver_sql("SET LOCAL lock_timeout='15000ms'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='30000ms'")
    db_errors=[];process_errors=[];original_process=routing.process
    def database_error(context):
        original=context.original_exception;diagnostic=getattr(original,'diag',None)
        db_errors.append({'kind':type(original).__name__,
            'sqlstate':getattr(original,'sqlstate',None) or getattr(original,'pgcode',None),
            'message_primary':str(getattr(diagnostic,'message_primary','') or '')[:180]})
    def observe_process(*args,**kwargs):
        try:return original_process(*args,**kwargs)
        except Exception as failure:
            from fastapi import HTTPException
            if isinstance(failure,HTTPException):
                detail=failure.detail
                if isinstance(detail,dict):detail={key:detail.get(key) for key in ('code','message')}
                process_errors.append({'kind':type(failure).__name__,'http_status':failure.status_code,
                    'detail':str(detail)[:200]})
            else:
                original=getattr(failure,'orig',failure)
                process_errors.append({'kind':type(failure).__name__,
                    'sqlstate':getattr(original,'sqlstate',None) or getattr(original,'pgcode',None)})
            raise
    pool=ThreadPoolExecutor(max_workers=2);futures=[]
    sql_event.listen(s.bound,'begin',limits)
    sql_event.listen(s.bound,'handle_error',database_error)
    routing.process=observe_process
    try:
        for identity in identities:
            futures.append(pool.submit(worker.process_once,identity,kinds=[routing.KIND],workspace_id=s.workspace,deliver=False))
        for future in futures:assert future.result(timeout=45)
    finally:
        pool.shutdown(wait=False,cancel_futures=True)
        routing.process=original_process
        sql_event.remove(s.bound,'handle_error',database_error)
        sql_event.remove(s.bound,'begin',limits)
    saved=occurrence(s)
    assert saved['status']=='approval_draft',{'db_errors':db_errors[-2:],'process_errors':process_errors[-2:]}
    with Session(s.bound) as db:
        jobs=[db.get(m.Job,identity) for identity in identities]
        diagnostics=[{'status':job.status,'attempts':job.attempts,
            'error':str(job.data.get('error') or '')[:240]} for job in jobs]
        observed={'jobs':diagnostics,'db_errors':db_errors[-2:],'process_errors':process_errors[-2:]}
        assert len(db.get(Route,s.route['id']).data['task_ids'])==1,observed
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2,observed
        assert all(job.status=='succeeded' for job in jobs),observed
    no_new_effects(s)


def test_forced_short_lock_timeout_keeps_failed_attempt_and_recovers_original_saved_draft(scene):
    s=collect(scene);ack(s)
    with Session(s.bound) as db:
        identities=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind==routing.KIND,
            m.Job.status=='queued',m.Job.data['occurrence_id'].as_string()==s.occurrence_id)
            .order_by(m.Job.created_at,m.Job.id)))
        assert len(identities)==2
    assert worker.process_once(identities[0],kinds=[routing.KIND],workspace_id=s.workspace,deliver=False)
    saved=occurrence(s);assert saved['status']=='approval_draft'
    blocked=ThreadEvent();sqlstates=[]
    def short_budget(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout='200ms'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='5000ms'")
    def observe_timeout(context):
        original=context.original_exception
        state=getattr(original,'sqlstate',None) or getattr(original,'pgcode',None)
        sqlstates.append(state)
        if state=='55P03':blocked.set()
    # Hold only the real Instance row. The deliberate 55P03 must be observed
    # before releasing it, so a failed attempt is preserved by normal worker
    # bookkeeping rather than an artificial reset or fabricated retry result.
    blocker=Session(s.bound);pool=ThreadPoolExecutor(max_workers=1)
    blocker.execute(select(m.DomainInstance).where(m.DomainInstance.id==s.release['instance_id']).with_for_update()).scalar_one()
    sql_event.listen(s.bound,'begin',short_budget);sql_event.listen(s.bound,'handle_error',observe_timeout)
    try:
        future=pool.submit(worker.process_once,identities[1],kinds=[routing.KIND],workspace_id=s.workspace,deliver=False)
        assert blocked.wait(timeout=8),{'sqlstates':sqlstates[-2:]}
        blocker.rollback()
        assert future.result(timeout=15)
    finally:
        blocker.rollback();blocker.close()
        pool.shutdown(wait=False,cancel_futures=True)
        sql_event.remove(s.bound,'handle_error',observe_timeout);sql_event.remove(s.bound,'begin',short_budget)
    assert '55P03' in sqlstates
    assert occurrence(s)['task_id']==saved['task_id']
    task=good(s.client.get(saved['task_path']));assert task['status']=='approval_draft'
    source=good(s.client.get(saved['task_path']+'/signal-approval-source'))
    assert source['found'] and source['source']['data_received'] and not source['integrity_issue']
    body,raw,original=s.original
    recovered=good(s.client.get(prefix(s)+'/requests/'+body['request_id'],params={'raw_sha256':hashlib.sha256(raw.encode()).hexdigest()}))
    assert recovered['result']==original and recovered['current']['used_tasks']==1
    with Session(s.bound) as db:
        failed=db.get(m.Job,identities[1])
        assert failed.status=='failed' and failed.attempts==1 and failed.data.get('error')
        assert db.get(m.Job,identities[0]).status=='succeeded'
        assert len(db.get(Route,s.route['id']).data['task_ids'])==1
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
    no_new_effects(s)


def test_real_approval_source_and_origin_verify_in_read_only_transaction_for_another_reviewer(scene):
    s=collect(scene);process_pending(s);ack(s);process_pending(s)
    saved=occurrence(s);reviewer=s.reviewers[0][0]
    p=Principal(reviewer['id'],s.workspace,'operator','actual-read-only-reviewer')
    with Session(s.bound) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        task=db.get(m.Task,saved['task_id'])
        original=routing.checked_task_origin(db,p,task)
        source=routing.task_source(db,p,task.id)
        assert original['occurrence_id']==s.occurrence_id
        assert source['found'] and not source['integrity_issue']
        assert source['source']['receipt_hash']==original['receipt_hash']
        db.rollback()
    no_new_effects(s)
