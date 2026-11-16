"""DRAFT: append after a real initial head. Not yet a passing test or release.

Use only in the disposable PG fixture: `scoped`, `publication`, `task_id`,
`monkeypatch` originate from the same real instance/task approved in the test.
This captures a new software packet via the MQTT intake, not an actual broker.
"""
import json,time
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.contracts import digest
from generic_agents.mqtt_inbox import capture
from generic_agents import auto_evaluation_policy_api_v356 as policy_api
from generic_agents import native_goal_scoped_worker_v355 as task_worker
from generic_agents.auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356
from generic_agents.auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from tests.test_vertical_slice import manifest,publish
from tests.test_mqtt_binding_routes import process_all
from tests.test_authored_service_workflow_pg_v270 import good


def append_fresh_mqtt_observation(scoped,publication,task_id,monkeypatch):
    s=scoped.scene;client=s.client;release=scoped.release;workspace=s.workspace_id
    # Only this disposable test lifts the unfinished implementation gate.
    # It cannot enable the live product or authorize external calls.
    monkeypatch.setattr(policy_api,'automatic_runner_available',lambda:True)
    policy_path='/api/workspace/tasks/'+task_id+'/auto-evaluation-policy'
    policy=good(client.put(policy_path,json={'request_id':str(uuid4()),'expected_version':0,
        'min_interval_minutes':1,'max_evaluations_per_day':5,'reason':'授权此隔离任务本地评价新资料。'}))
    assert policy['policy_snapshot']['enabled']
    with Session(engine()) as db:
        task=db.get(m.Task,task_id);old_head=dict(task.data[KEY]);old_q0=task.data['native_goal_query_id']
        original_graph=digest(task.data['graph_runtime']);clouds=db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==workspace))
    # Broker is a declared loopback source. capture() below inserts one original
    # software packet through normal intake without assuming broker/PUBACK.
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    rule=publish(client,manifest('tool',name='mqtt_goal_'+uuid4().hex[:12],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},specification={'rules':[]}))
    config={'broker':'mqtt://127.0.0.1:1883','topics':{'sensor/one':'device-one'},
        'mapping':{'t':{'attribute':'temperature','unit':'°C'},'h':{'attribute':'humidity','unit':'%'}},
        'fields':{},'username_ref':'','password_ref':''}
    gateway=good(client.post('/api/mqtt/gateways',json={'name':'隔离 MQTT 软件源','configuration':config}),201)
    gateway=good(client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={'expected_version':gateway['version'],'status':'active'}))
    # Explicit simulation provenance is required; do not present software data as field evidence.
    good(client.post('/api/mqtt/gateways/'+gateway['id']+'/test-origin',json={'request_id':str(uuid4()),
        'expected_gateway_version':gateway['version'],'expected_config_hash':gateway['config_hash'],
        'source_label':'隔离软件模拟传感器','basis_type':'manufacturer_parameters',
        'basis_notes':'仅软件生成数值，不连接物理设备，不证明现场数据。'}),201)
    route=good(client.post('/api/mqtt/gateways/'+gateway['id']+'/routes',json={'expected_version':gateway['version'],
        'request_id':str(uuid4()),'topic':'sensor/one','entity_id':scoped.entity['id'],'extractor_id':rule['id'],
        'field_links':{'temperature':scoped.fields['气温'],'humidity':scoped.fields['相对湿度']},'include_existing':False}),201)
    body={'record_id':'auto-goal-'+uuid4().hex,'device_id':'device-one','observed_at':datetime.now(timezone.utc).isoformat(),
        'values':{'t':26.5,'h':65.0},'units':{'t':'°C','h':'%'}}
    raw=json.dumps(body,ensure_ascii=False).encode()
    with Session(engine()) as db:
        p=Principal(s.actor_id,workspace,'admin','isolated-simulation-mqtt')
        receipt=capture(db,p,gateway['id'],gateway['version'],'sensor/one',raw,1);db.flush();receipt_id=receipt.id;db.commit()
    identities=process_all(receipt_id);assert len(identities)==1;observation_id=identities[0]
    with Session(engine()) as db:
        obs=db.get(m.Observation,observation_id)
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==workspace,m.Event.kind=='observation.data',
            m.Event.idempotency_key=='data:'+observation_id))
        assert obs.entity_id==scoped.entity['id'] and event and event.status=='received'
        from generic_agents.instance_data_receipts import observation_receipt
        receipt_proof=observation_receipt(db,p,obs)['data_reception'];assert receipt_proof
        witness=db.scalar(select(AutoEvaluationAckWitnessV356).where(AutoEvaluationAckWitnessV356.workspace_id==workspace,
            AutoEvaluationAckWitnessV356.task_id==task_id,AutoEvaluationAckWitnessV356.event_id==event.id))
        assert witness and witness.data['receipt_hash']==digest(receipt_proof)
    scanned=task_worker.scan_once(workspace,release['instance_id'],task_id)
    assert scanned['queued']==1 and not scanned['blocked'] and not scanned['unknown']
    with Session(engine()) as db:
        origin=db.scalar(select(AutoEvaluationTriggerV356).where(AutoEvaluationTriggerV356.workspace_id==workspace,
            AutoEvaluationTriggerV356.task_id==task_id,AutoEvaluationTriggerV356.event_id==event.id))
        assert origin;producer_id=origin.job_id
    # Scoped worker claims only the original approved producer and its candidate
    # and promotion; no manual synthetic success/ack/head is inserted.
    cursor=None
    for _ in range(40):
        result=task_worker.tick(workspace,release['instance_id'],task_id,after=cursor);cursor=result['after']
        with Session(engine()) as db:
            task=db.get(m.Task,task_id);current=task.data.get(KEY)
            producer=db.get(m.Job,producer_id)
            if producer.status in {'failed','blocked'}:raise AssertionError('原追加评价未完成：'+str(producer.data.get('error')))
            if current and current['generation']>old_head['generation']:break
        if not result['processed']:time.sleep(.25)
    else:raise AssertionError('原自动评价未形成新的成熟 head；保留原请求诊断')
    with Session(engine()) as db:
        task=db.get(m.Task,task_id);head=task.data[KEY];producer=db.get(m.Job,producer_id)
        assert producer.status=='succeeded' and head['generation']==old_head['generation']+1
        assert head['previous_hash']==old_head['head_hash'] and task.data['native_goal_query_id']==old_q0
        assert digest(task.data['graph_runtime'])==original_graph
        evaluation=db.get(GoalEvaluationV288,head['evaluation_ref']['id']);assert evaluation.job_id==producer_id
        query=db.get(m.DataRun,evaluation.evaluation_query_id)
        from generic_agents.goal_evaluation_append_query_v299 import checked_frozen
        frozen=checked_frozen(db,p,task,query.id,evaluation_id=evaluation.invocation_id)
        selected=frozen['result']['result']['observation_evidence']
        used=evaluation.data['evaluation_result']['result']['observation_evidence']
        accepted=[item for item in selected if item.get('observation_id')==observation_id]
        assert len(accepted)==1 and accepted[0] in used and accepted[0].get('attributes'), '新观测没有作为相关正式证据进入实际评价'
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==workspace))==clouds
    return {'observation_id':observation_id,'head':head,'simulation_only':True,'broker_puback_proven':False}
