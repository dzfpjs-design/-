"""A native workflow must prove its own business goal; no parallel coordinator task."""
from uuid import uuid4
import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Job,DataRun,DataGap,CloudCall,Acquisition,Membership,DomainInstance,Memory,now
from generic_agents.worker import process_once
from tests.test_instance_workspace import prepared,enable,object_row,task_row,complete


def setup(client,source='archive',observation_mode='existing_only',expected=None):
    r,_,_=prepared(client);enable(client,r);obj=object_row(client,r);task,_=task_row(client,r,obj)
    field=next(iter(obj['attributes']))
    criteria=[{'id':'enough','kind':'evidence_sufficient','description':'原资料满足要求'},{'id':'human','kind':'human_confirmation','description':'原任务最终人工验收'}]
    if expected is not None:criteria.append({'id':'value','kind':'field_value','field':field,'operator':'eq','expected':expected,'description':'登记名称符合任务标准'})
    spec={'purpose':'核对当前公开业务档案并保留原流程结果','entity_ids':[obj['id']],'required_fields':[field],
        'subgoals':[{'id':'check','description':'完成原任务资料核查','criterion_ids':[c['id'] for c in criteria]}],'criteria':criteria,
        'evidence':{'source':source,'minimum_verification':'recorded' if source=='archive' else 'confirmed','time':{'mode':'archive','timezone':'Asia/Shanghai'} if source=='archive' else {'mode':'latest','max_age_seconds':86400,'timezone':'Asia/Shanghai'}},
        'observation_mode':observation_mode}
    base='/api/workspace/tasks/'+task['id']+'/goal'
    body={'request_id':str(uuid4()),'expected_version':task['version'],'reason':'确认公开工程目标，不证明现场','goal_spec':spec}
    return r,obj,task,base,body


def define(client,base,body):
    response=client.post(base,json=body);assert response.status_code==200,response.text;return response.json()


def drain(task):
    with Session(engine()) as db:ids=list(db.scalars(select(Job.id).where(Job.kind=='check_native_business_goal',Job.status=='queued',Job.available_at<=now(),Job.data['task_id'].as_string()==task['id'])))
    for identity in ids:assert process_once(identity,deliver=False)


def test_original_workflow_goal_requires_flow_evidence_and_explicit_saved_acceptance(client):
    r,obj,task,base,body=setup(client);declared=define(client,base,body)
    assert define(client,base,body)['version']==declared['version']
    assert client.post(base,json={**body,'reason':'changed'}).status_code==409
    drain(task);goal=client.get(base).json();assert not goal['check']['evaluation']['ready_for_acceptance']
    acceptance={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'原步骤和依据已核查','basis_hash':goal['check']['evaluation']['basis_hash']}
    assert client.post(base+'/accept',json=acceptance).status_code==409
    task=client.get('/api/workspace/tasks/'+task['id']).json()
    task=complete(client,task).json();task=complete(client,task).json();assert task['status']=='workflow_completed'
    drain(task);goal=client.get(base).json();assert goal['check']['evaluation']['ready_for_acceptance'] and not goal['acceptance']
    acceptance={**acceptance,'expected_version':goal['version'],'basis_hash':goal['check']['evaluation']['basis_hash']}
    accepted=client.post(base+'/accept',json=acceptance);assert accepted.status_code==200,accepted.text
    version=accepted.json()['version'];assert accepted.json()['acceptance']['evaluation']['completed']
    assert client.post(base+'/accept',json=acceptance).json()['version']==version
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Task))))==1
        receipts=list(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='native_goal_outcome')));assert len(receipts)==1 and receipts[0].status=='candidate'
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Acquisition)))
        assert db.get(Task,task['id']).data['workflow_runtime']['receipts']==task['workflow_runtime']['receipts']


def test_only_relevant_original_records_are_reviewable_and_damaged_review_blocks_acceptance(client):
    from datetime import datetime,timezone,timedelta
    from generic_agents.models import Observation
    from tests.test_vertical_slice import manifest,publish
    r,obj,task,base,body=setup(client,source='observations');define(client,base,body)
    field=body['goal_spec']['required_fields'][0]
    source=publish(client,manifest('source',name='native_goal_record',allowed_agent_roles=['perception'],execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    rule=publish(client,manifest('tool',name='native_goal_rule',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    ids=[]
    for values in ({'公开登记值':1},{'公开登记值':2,field:obj['attributes'][field]}):
        clock=datetime.now(timezone.utc)-timedelta(seconds=1)
        response=client.post('/api/workspace/instances/'+r['instance_id']+'/reported-observations',json={'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':rule['id'],'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual','attributes':values})
        assert response.status_code==201,response.text
        identity=response.json()['id'];ids.append(identity)
        with Session(engine()) as db:job_id=db.scalar(select(Job.id).where(Job.idempotency_key=='extract:'+identity))
        assert process_once(job_id,deliver=False)
    drain(task);goal=client.get(base).json();assert [record['id'] for record in goal['reviewable_records']]==[ids[1]]
    record=goal['reviewable_records'][0]
    response=client.post('/api/observations/'+record['id']+'/verify',json={'request_id':str(uuid4()),'expected_version':record['version'],'expected_input_hash':record['input_hash'],'decision':'confirmed','reason':'原登记与本地回执一致，非现场结论'})
    assert response.status_code==200,response.text
    task=client.get('/api/workspace/tasks/'+task['id']).json();task=complete(client,task).json();task=complete(client,task).json();drain(task)
    goal=client.get(base).json();assert goal['check']['evaluation']['ready_for_acceptance']
    with Session(engine()) as db:
        obs=db.get(Observation,ids[1]);obs.data={**obs.data,'verification_history':[]};db.commit()
    assert not client.get(base).json()['reviewable_records']
    response=client.post(base+'/accept',json={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'损坏核查不可验收','basis_hash':goal['check']['evaluation']['basis_hash']})
    assert response.status_code==409 and '历史摘要' in response.json()['detail']


def test_data_gap_belongs_to_original_goal_and_permission_missing_does_not_fake_collection(client):
    r,obj,task,base,body=setup(client,source='observations',observation_mode='allow_new');define(client,base,body);drain(task)
    goal=client.get(base).json();assert not goal['check']['evaluation']['ready_for_acceptance']
    gap=goal['check']['gaps'][0];assert gap['status']=='needs_authorization' and gap['business_task_id']==task['id']
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Task))))==1 and not list(db.scalars(select(Acquisition)))
        assert db.get(Task,task['id']).status=='workflow_waiting'


def test_original_goal_existing_only_does_not_inherit_monitor_authority(client):
    r,obj,task,base,body=setup(client,source='observations');define(client,base,body);drain(task)
    goal=client.get(base).json();assert goal['check']['gaps'][0]['status']=='needs_existing_evidence'
    assert goal['check']['gaps'][0]['business_task_id']==task['id']


def test_standard_failure_cannot_be_accepted_even_after_steps_finished(client):
    r,obj,task,base,body=setup(client,expected='不等于已有公开档案名称');define(client,base,body)
    task=client.get('/api/workspace/tasks/'+task['id']).json();task=complete(client,task).json();task=complete(client,task).json();drain(task)
    goal=client.get(base).json();assert not goal['check']['evaluation']['ready_for_acceptance']
    response=client.post(base+'/accept',json={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'不能绕过标准','basis_hash':goal['check']['evaluation']['basis_hash']})
    assert response.status_code==409


def test_parent_scope_cancel_and_old_task_api_cannot_bypass_native_workflow(client):
    r,obj,task,base,body=setup(client);declared=define(client,base,body)
    legacy={'expected_version':declared['version'],'reason':'不得切换旧计划','goal_spec':body['goal_spec']}
    assert client.put('/api/tasks/'+task['id']+'/goal',json=legacy).status_code==409
    assert client.post('/api/tasks/'+task['id']+'/plan',json={'expected_version':declared['version'],'reason':'不得切换旧计划'}).status_code==409
    assert client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':declared['version'],'reason':'不得绕过'}).status_code==409
    with Session(engine()) as db:
        instance=db.get(DomainInstance,r['instance_id']);instance.data={**instance.data,'enabled_business_ids':[]};db.commit()
    drain(task)
    goal=client.get(base).json();assert goal['scope_error']
    with Session(engine()) as db:assert db.scalar(select(Job).where(Job.kind=='check_native_business_goal')).status=='failed'


def test_stopping_goal_preserves_steps_and_blocks_future_progress(client):
    r,obj,task,base,body=setup(client);declared=define(client,base,body)
    stop={'request_id':str(uuid4()),'expected_version':declared['version'],'reason':'停止本目标协调，保留原记录'}
    response=client.post(base+'/stop',json=stop);assert response.status_code==200,response.text
    assert client.post(base+'/stop',json=stop).json()['version']==response.json()['version']
    task=client.get('/api/workspace/tasks/'+task['id']).json();assert complete(client,task).status_code==409
    drain(task)
    with Session(engine()) as db:assert not db.get(Task,task['id']).data['workflow_runtime']['receipts']


def test_changed_original_request_or_goal_cannot_continue_old_data_gap(client):
    r,obj,task,base,body=setup(client,source='observations');define(client,base,body);drain(task)
    goal=client.get(base).json();query=goal['check']['gaps'][0]['id']
    with Session(engine()) as db:
        gap=db.get(DataGap,query);run=db.get(DataRun,gap.query_id);data=copy.deepcopy(run.data);data['request']['fields']=['changed'];run.data=data;db.commit()
    command={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'只读恢复原目标'}
    assert client.post(base+'/check',json=command).status_code==200;drain(task)
    with Session(engine()) as db:assert list(db.scalars(select(Job).where(Job.kind=='check_native_business_goal').order_by(Job.created_at)))[-1].status=='failed'


def test_unpersisted_rejected_request_can_be_abandoned_and_late_original_is_blocked(client):
    r,obj,task,base,body=setup(client)
    invalid={**body,'goal_spec':{**body['goal_spec'],'required_fields':['outside-schema']}}
    assert client.post(base,json=invalid).status_code==422
    request={'operation':'define','original_request':invalid,'expected_version':task['version'],'reason':'保存原草稿，核对失败请求'}
    probe=client.post(base+'/requests/recover',json=request);assert probe.status_code==200 and not probe.json()['saved']
    cancelled=client.post(base+'/requests/recover',json={**request,'action':'abandon'});assert cancelled.status_code==200 and cancelled.json()['abandoned']
    assert client.post(base,json=invalid).status_code==409
    declared=define(client,base,{**body,'request_id':str(uuid4()),'expected_version':cancelled.json()['version']})
    assert declared['goal_confirmed']


def test_recovery_of_successful_unknown_response_reads_original_without_second_effect(client):
    r,obj,task,base,body=setup(client);declared=define(client,base,body)
    response=client.post(base+'/requests/recover',json={'operation':'define','original_request':body,'expected_version':declared['version'],
        'reason':'原响应未知，核对已保存结果','action':'abandon'})
    assert response.status_code==200 and response.json()['saved'] and not response.json()['abandoned']
    assert client.get(base).json()['version']==declared['version']
