"""One auto-created anomaly Task -> approved goal -> real HTTP supplement -> ACK.

Root alone executes. The loopback supplier is explicitly simulated; HTTP,
registry, extraction, approvals, leases and receipts use production control
paths. No transport/model/ACK success substitute and no edited observation.
There is one legitimate V177 input-producer Task plus one response Task; this
suite forbids an extra parallel coordination Task, not that required producer.
"""
import copy
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session

from generic_agents import models as m,worker
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.signal_approval_models_v247 import SignalApprovalOccurrenceV247 as Occurrence
from tests.test_acquisition_ack_settlement_pg_v245 import simulated_supplier,good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import operator,command as approval_command
from tests.test_instance_workspace import configuration,save,object_row,complete
from tests.test_sensing_selection import monitor
from tests.test_signal_approval_routing_pg_v247 import configure_policy,enable_route,process_pending,occurrence
from tests.test_vertical_slice import manifest,publish


def exact_job(s,identity,kind):
    assert identity and worker.process_once(identity,kinds=[kind],workspace_id=s.workspace,deliver=False)
    with Session(s.bound) as db:
        job=db.get(m.Job,identity)
        assert job.status=='succeeded',{'kind':job.kind,'status':job.status,'error':job.data.get('error')}


def ready_goal_jobs(s,task_ids):
    with Session(s.bound) as db:
        return list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,
            m.Job.kind=='check_native_business_goal',m.Job.status=='queued',m.Job.available_at<=func.clock_timestamp(),
            m.Job.data['task_id'].as_string().in_(task_ids)).order_by(m.Job.available_at,m.Job.id)))


def settle_and_check(s,*,task_ids,acquisition_ids):
    # Explicit bounded local handlers only. Never claim arbitrary workspace jobs
    # or dispatch an additional device from this helper itself.
    for _ in range(12):
        with Session(s.bound) as db:
            settlements=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==s.workspace,
                m.Job.kind=='settle_received_acquisition_v245',m.Job.status=='queued',m.Job.available_at<=func.clock_timestamp(),
                m.Job.data['settlement']['acquisition_id'].as_string().in_(acquisition_ids)).order_by(m.Job.available_at,m.Job.id)))
        checks=ready_goal_jobs(s,task_ids)
        if not settlements and not checks:return
        for identity in settlements:exact_job(s,identity,'settle_received_acquisition_v245')
        for identity in checks:exact_job(s,identity,'check_native_business_goal')
    raise AssertionError('bounded original-task reconciliation did not quiesce')


def register_source(s,field,wire,label,*,anomaly=False):
    extractor=publish(s.client,manifest('tool',name='goal_handoff_rule_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules','side_effect':False},
        specification={'rules':[{'id':'high_value','field':field,'operator':'gt','threshold':20,'unit':'°C'}] if anomaly else []},
        input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False}))
    with Session(s.bound) as db:package_hash=db.get(m.Capability,s.release['package_id']).content_hash
    unit='°C' if wire=='temperature_c' else '%'
    mapping={'schema':'http-observation-mapping-v177','scope':{'workspace_id':s.workspace,'environment_id':s.environment['id'],
        'instance_id':s.release['instance_id'],'release_id':s.release['id'],'package_id':s.release['package_id'],
        'package_hash':package_hash,'entity_type':s.entity['entity_type']},
        'links':{wire:{'field_key':field,'unit':unit}},'supplier_contract':'sim-acquisition-receipt-v176','time_semantics':'simulator_sample_time'}
    source=publish(s.client,manifest('source',name='goal_handoff_source_'+uuid4().hex[:8],allowed_agent_roles=['perception','data'],
        execution={'adapter':'http','endpoint':s.sim.base+'/acquisitions','receipt_endpoint':s.sim.base+'/acquisitions/receipts',
            'side_effect':True,'secret_ref':'GA_CONNECTOR_SECRET_V245_ACK_TEST'},input_schema={'type':'object'},output_schema={'type':'object'},
        data_policy={'deployment':'local','simulation':True,'test_only':True,'cloud_allowed':False,'external_allowed':False},
        specification={'timezone':'Asia/Shanghai','simulation_environment_id':s.environment['id'],
            'http_observation_mapping_v177':mapping,'http_observation_extractor_id_v177':extractor['id'],
            'http_observation_extractor_hash_v177':extractor['content_hash'],
            'observation_capability':{'fields':[field],'modalities':['numeric'],'actions':['observe'],'receipt_contract':'request_asset_terminal_v1'}}))
    asset=good(s.client.post('/api/sensing/assets',json={'source_id':source['id'],'external_id':'sim-weather-01',
        'title':label+'（同一明确模拟设备的独立登记字段来源）','reason':'只登记本来源真实映射的一项字段',
        'specification':{'coverage_entity_ids':[s.entity['id']],'fields':[field],'modalities':['numeric'],'actions':['observe'],
            'latency_seconds':30,'quote_ceiling_minor':0,'currency':'CNY','health':'healthy',
            'health_observed_at':(m.now()-timedelta(seconds=1)).isoformat(),
            'health_valid_until':(m.now()+timedelta(hours=1)).isoformat()}}),201)
    current=good(s.client.get('/api/sensing/devices/'+s.device['id']))
    good(s.client.post('/api/sensing/assets/'+asset['id']+'/device',json={'expected_version':asset['version'],
        'device_id':s.device['id'],'device_expected_version':current['version'],'reason':'核对同一模拟设备的独立来源接口'}))
    return source,extractor,asset


def goal_spec(s,fields=None,*,allow_new=True,purpose='补齐原异常任务的两项设备登记资料并由独立人员验收'):
    return {'purpose':purpose,'entity_ids':[s.entity['id']],'required_fields':fields or [s.temperature,s.humidity],
        'observation_mode':'allow_new' if allow_new else 'existing_only',
        'evidence':{'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}},
        'max_steps':12,'max_tool_calls':12,'stop_on_conflict':True,'deadline':None,
        'subgoals':[{'id':'check','description':'核对原对象的明确字段','criterion_ids':['enough','human']}],
        'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'原字段资料充分且独立接收'},
            {'id':'human','kind':'human_confirmation','description':'完成原流程后由独立人员正式验收'}]}


def dispatch_and_extract(s,acquisition_id):
    with Session(s.bound) as db:
        identity=db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind=='dispatch_acquisition',
            m.Job.data['acquisition_id'].as_string()==acquisition_id,m.Job.status=='queued'))
    exact_job(s,identity,'dispatch_acquisition')
    with Session(s.bound) as db:
        exchange=db.scalar(select(Exchange).where(Exchange.workspace_id==s.workspace,Exchange.acquisition_id==acquisition_id))
        assert exchange and exchange.status=='received' and exchange.observation_id
        observation_id=exchange.observation_id
        child=db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace,m.Job.kind=='process_http_mapped_observation_v177',
            m.Job.data['observation_id'].as_string()==observation_id,m.Job.status=='queued'))
    exact_job(s,child,'process_http_mapped_observation_v177')
    return observation_id


@pytest.fixture
def handoff_scene(client,simulated_supplier):
    with ExitStack() as stack:
        cfg=configuration();cfg['instanceProfile']={**cfg['instanceProfile'],'name':'V253同一异常任务补证','simulation_v176':True}
        revision=save(client,cfg,review_automation=True);member=good(client.get('/api/instances/members'))[0]
        objects=[{'name':'原业务监测对象','fields':[{'label':'名称','kind':'文本','required':True},
            {'label':'温度','kind':'数值','unit':'°C'},{'label':'湿度','kind':'数值','unit':'%'}]}]
        compilation={'revision':revision,'objects':objects,'role_bindings':{'核验负责人':member['id']}}
        checked=good(client.post('/api/instances/check',json=compilation))
        release=good(client.post('/api/instances',json={**compilation,'confirmed_configuration_hash':checked['configuration_hash'],
            'confirm_configuration':True,'generation_intent':'new_instance','request_id':str(uuid4())}),201)
        instance=next(x for x in good(client.get('/api/instances')) if x['id']==release['instance_id'])
        good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
            'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
        entity=object_row(client,release);me=good(client.get('/api/me'))
        p=Principal(me['user_id'],me['workspace_id'],'admin','anomaly-goal-real-pg')
        fields={v['label']:v['key'] for v in release['snapshot']['object_types'][0]['fields']}
        environment=good(client.post('/api/workspace/simulation-v176/environments',json={'request_id':str(uuid4()),
            'instance_id':instance['id'],'release_id':release['id'],'note':'明确模拟HTTP，不是现场效果','confirm_simulation':True}),201)
        s=NS(client=client,stack=stack,sim=simulated_supplier,bound=engine(),workspace=p.workspace_id,workspace_id=p.workspace_id,
            p=p,release=release,entity=entity,temperature=fields['温度'],humidity=fields['湿度'],environment=environment,
            business_id=release['available_business_ids'][0])
        s.device=good(client.post('/api/sensing/devices',json={'title':'V253明确模拟天气设备','identity_scheme':'supplier_device_id',
            'identity_issuer':'V253隔离模拟器','identity_reference':'sim-weather-01','reason':'同一模拟设备的两个独立登记来源，不称两台实物'}),201)
        s.device=good(client.post('/api/sensing/devices/'+s.device['id']+'/approve',json={
            'expected_version':s.device['version'],'reason':'核对明确模拟身份'}))
        s.source,s.extractor,s.asset_a=register_source(s,s.temperature,'temperature_c','来源A：温度',anomaly=True)
        s.source_b,s.extractor_b,s.asset_b=register_source(s,s.humidity,'humidity_percent','来源B：湿度')
        s.monitor_a=monitor(client,entity,s.source,[s.asset_a],fields=[s.temperature],max_attempts=2)
        s.monitor_b=monitor(client,entity,s.source_b,[s.asset_b],fields=[s.humidity],max_attempts=2)
        parent=good(client.post('/api/workspace/instances/'+instance['id']+'/tasks',json={'release_id':release['id'],
            'business_id':s.business_id,'entity_ids':[entity['id']],'title':'仅提供温度的原输入生产任务',
            'request_id':str(uuid4()),'goal_spec':goal_spec(s,[s.temperature],purpose='仅登记温度，不要求也不删除其他源数据')}),201)
        s.parent=parent
        for identity in ready_goal_jobs(s,[parent['id']]):exact_job(s,identity,'check_native_business_goal')
        with Session(s.bound) as db:
            a=db.scalar(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
                m.Acquisition.data['policy_id'].as_string()==s.monitor_a['id']))
            assert a and a.status=='authorized';s.acquisition_id=a.id
        configure_policy(s)
        # Independent executor: do not conflate uploader/owner, processing
        # actor, reviewers, human handler and independent final acceptor.
        s.executor,s.executor_client=operator(s,stack)
        unit=s.policy_body['scope_id'];settings_base='/api/workspace/approval-settings-v175'
        # The approval executor role is also required by ordinary human
        # completion. Explicitly grant the real assigned handler as well; the
        # independent account still performs start and the acceptor has neither.
        for actor_id in (s.executor['id'],s.p.user_id):
            good(client.post(settings_base+'/roles',json={'request_id':str(uuid4()),'grant_id':str(uuid4()),'expected_version':0,
                'unit_id':unit,'user_id':actor_id,'role_key':'independent_execute','enabled':True}))
        s.policy_body={**s.policy_body,'request_id':str(uuid4()),'expected_version':s.policy['version'],
            'executor':{'name':'独立执行人明确启动','role_key':'independent_execute','scope_id':unit}}
        s.policy=good(client.post(settings_base+'/policies',json=s.policy_body))
        enable_route(s)
        s.observation_id=dispatch_and_extract(s,s.acquisition_id)
        worker.deliver_events(workspace_id=s.workspace,observation_id=s.observation_id,event_kind='perception.anomaly')
        with Session(s.bound) as db:
            signal=db.scalar(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id==s.workspace,
                m.PerceptionSignal.data['basis']['observation_id'].as_string()==s.observation_id))
            assert signal;s.signal_id=signal.id
            occ=db.scalar(select(Occurrence).where(Occurrence.workspace_id==s.workspace,Occurrence.signal_id==signal.id));assert occ
            s.occurrence_id=occ.id
        process_pending(s);assert occurrence(s)['status']=='waiting_data'
        worker.deliver_events(workspace_id=s.workspace,observation_id=s.observation_id,event_kind='observation.data')
        process_pending(s);delivered=occurrence(s)
        assert delivered['status']=='approval_draft';s.task_id=delivered['task_id'];s.path=delivered['task_path'];s.approval=s.path+'/approval-v175'
        settle_and_check(s,task_ids=[parent['id']],acquisition_ids=[s.acquisition_id])
        with Session(s.bound) as db:
            observation=db.get(m.Observation,s.observation_id)
            assert set(observation.data['extracted']['attributes'])=={s.temperature}
            assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
            assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
            assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace))==0
            s.entity_hash=digest(db.get(m.Entity,entity['id']).data)
        assert s.sim.calls==[('POST','/acquisitions')]
        yield s


def save_goal(s,spec=None):
    original=good(s.client.get(s.approval));value=copy.deepcopy(original['value']);value['goal_spec']=spec or goal_spec(s)
    body={'request_id':str(uuid4()),'expected_version':original['version'],'reason':'明确原异常任务需要哪些资料和已有授权内的补采',
        'value':value}
    response=good(s.client.post(s.approval+'/revise',json=body))
    return body,response


def approve_and_start(s):
    approval_command(s.client,s.approval,'submit')
    submission=good(s.client.get(s.approval))['submission_hash']
    for _,reviewer in s.reviewers:approval_command(reviewer,s.approval,'decide',submission_hash=submission,decision='approve')
    before=good(s.executor_client.get(s.approval));assert before['permissions']['start']
    approval_command(s.executor_client,s.approval,'start')
    return submission


def response_acquisitions(s):
    with Session(s.bound) as db:
        task=db.get(m.Task,s.task_id);query_id=task.data.get('native_goal_query_id')
        if not query_id:return []
        plans=select(m.ObservationPlan.id).where(m.ObservationPlan.workspace_id==s.workspace,
            m.ObservationPlan.data['observation_request']['query_id'].as_string()==query_id)
        return [a.id for a in db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace,
            m.Acquisition.data['observation_plan_id'].as_string().in_(plans)).order_by(m.Acquisition.id))]


def response_goal(s):return good(s.client.get(s.path+'/goal'))

def complete_original_humans(s):
    task=good(s.client.get(s.path))
    for _ in range(4):
        if task['status']=='workflow_completed':return task
        note='已核对原温度、补充湿度及独立收件；模拟登记值不能判定现场事实'
        good(s.client.put(s.path+'/note',json={'version':task['version'],'step_id':task['workflow_runtime']['current_step_id'],'note':note}))
        task=good(s.client.get(s.path));task=good(complete(s.client,task,note=note,perception_decision='uncertain'))
    raise AssertionError('original configured human flow did not complete')



def inventory(s):
    with Session(s.bound) as db:
        counts={cls.__name__:db.scalar(select(func.count()).select_from(cls).where(cls.workspace_id==s.workspace))
            for cls in (m.Task,m.Acquisition,m.Observation,m.CloudCall)}
        task=db.get(m.Task,s.task_id)
        return counts,task.version,digest(task.data)


def start_missing_humidity(s):
    save_goal(s);approve_and_start(s)
    jobs=ready_goal_jobs(s,[s.task_id]);assert len(jobs)==1
    with Session(s.bound) as db:
        task=db.get(m.Task,s.task_id)
        assert task.data['native_goal_context']['actor_id']==s.executor['id']
        assert task.data['goal_spec']['required_fields']==[s.temperature,s.humidity]
        assert task.data['native_goal_check_job_pin']['job_id']==jobs[0]
    exact_job(s,jobs[0],'check_native_business_goal')
    with Session(s.bound) as db:
        task=db.get(m.Task,s.task_id);query=db.get(m.DataRun,task.data['native_goal_query_id'])
        assert query.data['goal_delegation']['task_id']==s.task_id
        assert query.data['native_goal_task_id']==s.task_id
        gaps=list(db.scalars(select(m.DataGap).where(m.DataGap.workspace_id==s.workspace,m.DataGap.query_id==query.id)))
        assert len(gaps)==1;gap=gaps[0]
        assert gap.data['business_task_id']==s.task_id
        assert gap.data['missing_fields']==[s.humidity]
        s.gap_id=gap.id;s.query_id=query.id
    return response_acquisitions(s)


def test_approval_draft_goal_metadata_and_explicit_revision_preserve_original_null(handoff_scene):
    s=handoff_scene;before=inventory(s)
    original=good(s.client.get(s.approval));assert original['status']=='draft' and original['value']['goal_spec'] is None
    metadata=response_goal(s)
    assert metadata['task_status']=='approval_draft' and metadata['goal'] is None and not metadata['goal_confirmed']
    assert metadata['entity_ids']==[s.entity['id']]
    assert metadata['fields'][s.temperature]=='温度' and metadata['fields'][s.humidity]=='湿度'
    assert metadata['field_units'][s.temperature]=='°C' and metadata['field_units'][s.humidity]=='%'
    assert inventory(s)==before
    body,_=save_goal(s)
    reopened=good(s.client.get(s.approval))
    assert reopened['value']['goal_spec']['required_fields']==[s.temperature,s.humidity]
    assert reopened['value']['goal_spec']['evidence']['minimum_verification']=='unverified_extraction'
    assert reopened['draft_history'][0]['value']['goal_spec'] is None
    saved=inventory(s)
    # Same saved command reads its original receipt even though expected_version
    # is now stale. It never redefines a goal or makes a second device action.
    good(s.client.post(s.approval+'/revise',json=body))
    assert inventory(s)==saved
    assert response_goal(s)['goal'] is None and not response_goal(s)['goal_confirmed']
    assert not ready_goal_jobs(s,[s.task_id]) and not response_acquisitions(s)
    with Session(s.bound) as db:
        original_body=db.get(Occurrence,s.occurrence_id).data['creation_body']
        assert original_body.get('goal_spec') is None
        assert db.get(m.Task,s.task_id).data.get('native_goal_context') is None
        assert not db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==s.workspace,
            m.Audit.action=='native_goal.defined',m.Audit.object_id==s.task_id))
    assert s.sim.calls==[('POST','/acquisitions')]


def test_one_response_task_approved_goal_supplements_humidity_ack_returns_and_independent_accepts(handoff_scene):
    s=handoff_scene;ids=start_missing_humidity(s);assert len(ids)==1;s.b_acquisition=ids[0]
    with Session(s.bound) as db:
        a=db.get(m.Acquisition,s.b_acquisition);plan=db.get(m.ObservationPlan,a.data['observation_plan_id'])
        assert a.status=='authorized' and a.capability_id==s.source_b['id']
        assert a.data['policy_id']==s.monitor_b['id']
        assert plan.data['observation_request']['fields']==[s.humidity]
        assert plan.data['observation_request']['goal_delegation']['task_id']==s.task_id
    assert response_goal(s)['check']['evidence']['status']=='insufficient'
    observation_b=dispatch_and_extract(s,s.b_acquisition)
    with Session(s.bound) as db:
        obs=db.get(m.Observation,observation_b)
        assert set(obs.data['extracted']['attributes'])=={s.humidity}
        assert obs.status=='processed' and obs.data.get('human_verification',{}).get('decision')!='confirmed'
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==s.workspace,m.Event.kind=='observation.data',
            m.Event.data['observation_id'].as_string()==observation_b))
        assert event.status=='pending_delivery'
        assert not db.scalar(select(m.Memory.id).where(m.Memory.workspace_id==s.workspace,
            m.Memory.data['event_id'].as_string()==event.id,m.Memory.data['type'].as_string()=='observation_evidence'))
        assert db.get(m.Acquisition,s.b_acquisition).status=='awaiting_observation'
    # A user may save and complete the actual configured human work while data
    # is pending, but the independent final acceptance must still reject it.
    complete_original_humans(s)
    pending=response_goal(s)
    assert pending['check']['evidence']['status']=='insufficient'
    assert not pending['check']['evaluation']['completed']
    blocked=good(s.acceptor_client.get(s.approval))
    assert blocked['acceptance_basis'] is None and not blocked['permissions']['accept']
    rejected=s.acceptor_client.post(s.approval+'/accept',json={'request_id':str(uuid4()),
        'expected_version':blocked['version'],'reason':'验证尚未接收不能验收','basis_hash':'0'*64,'decision':'accept'})
    assert rejected.status_code==409,rejected.text
    worker.deliver_events(workspace_id=s.workspace,observation_id=observation_b,event_kind='observation.data')
    settle_and_check(s,task_ids=[s.parent['id'],s.task_id],acquisition_ids=[s.acquisition_id,s.b_acquisition])
    restored=response_goal(s)
    assert restored['check']['evidence']['status']=='sufficient'
    assert restored['check']['evaluation']['data_received'] and not restored['check']['evaluation']['open_device_actions']
    assert restored['check']['evaluation']['ready_for_acceptance']
    assert not restored['check']['evaluation']['completed'] # separate human confirmation has not happened
    evidence=restored['check']['evidence']['observation_evidence']
    assert {item['observation_id'] for item in evidence}=={s.observation_id,observation_b}
    basis=good(s.acceptor_client.get(s.approval))
    assert basis['permissions']['accept'] and basis['acceptance_basis']['evaluation']['completed']
    assert basis['acceptance_basis']['physical_state_verified'] is False
    receipt=approval_command(s.acceptor_client,s.approval,'accept',basis_hash=basis['acceptance_basis']['basis_hash'],decision='accept')
    assert receipt['status']=='accepted'
    closed=response_goal(s)
    assert closed['approval_completion']['accepted'] and closed['acceptance_source']=='layered_approval_v175'
    assert closed['acceptance'] is None # no fabricated native-goal receipt
    assert occurrence(s)['task_id']==s.task_id
    source=good(s.acceptor_client.get(s.path+'/signal-approval-source'))
    assert source['found'] and source['source']['observation_id']==s.observation_id
    with Session(s.bound) as db:
        a=db.get(m.Acquisition,s.b_acquisition);plan=db.get(m.ObservationPlan,a.data['observation_plan_id'])
        assert a.status=='succeeded' and a.data['closing_observation_id']==observation_b
        assert plan.status=='completed' and plan.data['result']['status']=='sufficient'
        assert db.get(m.DataGap,s.gap_id).status=='resolved'
        task=db.get(m.Task,s.task_id)
        assert task.status=='workflow_completed' and task.data['native_goal_query_id']==s.query_id
        assert len(task.data['workflow_runtime']['receipts'])==2
        assert {r['actor'] for r in task.data['workflow_runtime']['receipts']}=={s.p.user_id}
        assert digest(db.get(m.Entity,s.entity['id']).data)==s.entity_hash
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.Observation).where(m.Observation.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace))==0
        memories=list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==s.workspace,
            m.Memory.data['type'].as_string()=='observation_evidence',
            m.Memory.data['observation_id'].as_string().in_([s.observation_id,observation_b]))))
        assert len(memories)==2
    assert s.sim.calls==[('POST','/acquisitions'),('POST','/acquisitions')]


def test_returned_goal_revision_requires_a_new_complete_approval_round_without_starting_work(handoff_scene):
    s=handoff_scene;save_goal(s);approval_command(s.client,s.approval,'submit')
    old=good(s.client.get(s.approval));old_hash=old['submission_hash']
    approval_command(s.reviewers[0][1],s.approval,'decide',submission_hash=old_hash,decision='approve')
    approval_command(s.reviewers[1][1],s.approval,'decide',submission_hash=old_hash,decision='return')
    changed=goal_spec(s,purpose='退回后明确修改：只补原对象湿度，完整原证据仍需逐级审批')
    save_goal(s,changed)
    approval_command(s.client,s.approval,'submit')
    current=good(s.client.get(s.approval));new_hash=current['submission_hash']
    assert new_hash!=old_hash and current['current_stage']==0 and current['decisions']==[]
    assert len(current['submissions'])==2
    assert current['submissions'][0]['value']['goal_spec']['purpose']!=current['value']['goal_spec']['purpose']
    stale=s.reviewers[0][1].post(s.approval+'/decide',json={'request_id':str(uuid4()),
        'expected_version':current['version'],'reason':'旧提交版本不得批准新目标','submission_hash':old_hash,'decision':'approve'})
    assert stale.status_code==409,stale.text
    for index,(_,reviewer) in enumerate(s.reviewers):
        before=good(s.executor_client.get(s.approval));assert not before['permissions']['start']
        approval_command(reviewer,s.approval,'decide',submission_hash=new_hash,decision='approve')
        if index<2:assert not good(s.executor_client.get(s.approval))['permissions']['start']
    final=good(s.executor_client.get(s.approval));assert final['status']=='approved' and final['permissions']['start']
    assert response_goal(s)['goal'] is None and not response_goal(s)['goal_confirmed']
    assert not ready_goal_jobs(s,[s.task_id]) and not response_acquisitions(s)
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
    assert s.sim.calls==[('POST','/acquisitions')]


@pytest.mark.parametrize('boundary',['existing_only','monitor_revoked'])
def test_goal_permission_intersection_never_creates_an_unapproved_supplement(handoff_scene,boundary):
    s=handoff_scene
    if boundary=='monitor_revoked':
        good(s.client.post('/api/monitor-policies/'+s.monitor_b['id']+'/revoke',json={
            'expected_version':s.monitor_b['version'],'reason':'撤销湿度补采；允许补采的目标本身不能授设备权限'}))
    save_goal(s,goal_spec(s,allow_new=boundary!='existing_only'));approve_and_start(s)
    jobs=ready_goal_jobs(s,[s.task_id]);assert len(jobs)==1
    exact_job(s,jobs[0],'check_native_business_goal')
    goal=response_goal(s);assert goal['check']['evidence']['status']=='insufficient'
    assert {g['status'] for g in goal['current_gaps']}=={
        'needs_existing_evidence' if boundary=='existing_only' else 'needs_authorization'}
    assert not response_acquisitions(s)
    with Session(s.bound) as db:
        task=db.get(m.Task,s.task_id);gaps=list(db.scalars(select(m.DataGap).where(m.DataGap.workspace_id==s.workspace,
            m.DataGap.query_id==task.data['native_goal_query_id'])))
        assert len(gaps)==1 and gaps[0].data['business_task_id']==s.task_id
        assert gaps[0].data['missing_fields']==[s.humidity]
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.workspace_id==s.workspace))==2
        assert db.scalar(select(func.count()).select_from(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace))==1
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace))==0
    assert s.sim.calls==[('POST','/acquisitions')]
