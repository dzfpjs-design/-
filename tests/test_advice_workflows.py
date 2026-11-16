"""Real registered local nodes from approved advice, not navigation-only handoffs."""
import copy
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.worker import process_once
from tests.test_workflow_abilities import tool,conf,enable,finish_job
from tests.test_instance_workspace import prepared,complete
from tests.test_perception_signals import signals
from tests.test_vertical_slice import publish,manifest
from tests.test_signal_advice import start,cloud,get,confirmation

def setup(client,mode='自动运行'):
    local=tool(client);r,_,_=prepared(client,conf(local,mode=mode));enable(client,r)
    kind=r['snapshot']['object_types'][1]
    obj=client.post('/api/instances/'+r['instance_id']+'/objects',json={'release_id':r['id'],'object_type':kind['key'],
        'attributes':{f['key']:('公开数量记录' if f['label']=='标题' else 10) for f in kind['fields']}}).json()
    src=publish(client,manifest('source',name='public_advice_data',allowed_agent_roles=['perception'],execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    extractor=publish(client,manifest('tool',name='public_advice_threshold',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'公开数值','operator':'gt','threshold':5}]},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(seconds=10)
    response=client.post('/api/workspace/instances/'+r['instance_id']+'/reported-observations',json={'source_id':src['id'],'extractor_id':extractor['id'],'entity_id':obj['id'],'source_record_id':str(uuid4()),'observed_at':clock.isoformat(),'business_day':str(clock.astimezone(ZoneInfo('Asia/Shanghai')).date()),'startup_mode':'manual','attributes':{'公开数值':10}});assert response.status_code==201,response.text
    with Session(engine()) as db:job=db.scalar(select(m.Job).where(m.Job.data['observation_id'].as_string()==response.json()['id']))
    assert process_once(job.id,deliver=False);signal=signals(client,r)[0]
    cap=publish(client,manifest('foundation_model',name='workflow_advice_qwen',allowed_agent_roles=['business'],execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':2048},data_policy={'cloud_allowed':True}))
    return r,obj,signal,cap,local

def workflow(value,context):
    choice=context['workflow_choices'][0]
    value['action']={'route':'business_workflow','business_id':choice['business_id'],'input_set':choice['input_sets'][0]['id'],'task_id':None,'criteria':None,'purpose':'按已确认本地流程核对登记数量'}

def test_local_registered_node_executes_then_human_review_preserves_source(client,monkeypatch):
    r,obj,signal,cap,local=setup(client);calls=[];cloud(monkeypatch,calls,workflow);row,_,p=start(client,r,signal,cap)
    assert p['cloud_input']['workflow_choices'][0]['automatic_steps'][0]['mode']=='自动运行'
    assert process_once(row['job_id'],deliver=False);row=get(client,row)
    confirm=confirmation(row);url='/api/signal-advice/'+row['id']+'/confirm'
    assert client.post(url,json=confirm).status_code==409 # explicit new processing ack required
    confirm['acknowledge_new_local_processing']=True;response=client.post(url,json=confirm);assert response.status_code==200,response.text;task_id=response.json()['dispatch']['task_id']
    assert client.post(url,json=confirm).json()['dispatch']['task_id']==task_id
    task=client.get('/api/workspace/tasks/'+task_id).json();assert task['status']=='workflow_queued' and task['business_advice']['advice_id']==row['id'] and not task.get('perception_signal')
    task=finish_job(client,task);receipt=task['workflow_runtime']['receipts'][0];assert receipt['output']['value']==10 and receipt['output']['sample_count']==1
    completed=complete(client,task,note='已核对本地登记数值为10，不代表现场状态');assert completed.status_code==200,completed.text
    assert signals(client,r)[0]==signal and len(calls)==1
    with Session(engine()) as db:assert db.get(m.Entity,obj['id']).version==1 and len(list(db.scalars(select(m.Invocation))))==2

def test_preexecution_confirmation_is_kept_and_unapproved_direct_task_rejected(client,monkeypatch):
    r,obj,signal,cap,local=setup(client,mode='执行前确认');row,_,_=start(client,r,signal,cap);calls=[];cloud(monkeypatch,calls,workflow);assert process_once(row['job_id'],deliver=False);row=get(client,row)
    direct={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id']],'title':'绕过处置确认','request_id':str(uuid4()),'advice_id':row['id']}
    assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=direct).status_code==409
    result=client.post('/api/signal-advice/'+row['id']+'/confirm',json={**confirmation(row),'acknowledge_new_local_processing':True});assert result.status_code==200,result.text
    task=client.get('/api/workspace/tasks/'+result.json()['dispatch']['task_id']).json();assert task['status']=='workflow_waiting' and not task['workflow_runtime'].get('automatic')
    with Session(engine()) as db:assert len(list(db.scalars(select(m.Invocation))))==1 # original threshold only

def test_capability_change_blocks_old_advice_without_refiring_cloud(client,monkeypatch):
    r,obj,signal,cap,local=setup(client);row,_,_=start(client,r,signal,cap);calls=[];cloud(monkeypatch,calls,workflow);assert process_once(row['job_id'],deliver=False);row=get(client,row)
    with Session(engine()) as db:
        current=db.get(m.Capability,local['id']);current.data={**current.data,'execution':{**current.data['execution'],'side_effect':True}};db.commit()
    result=client.post('/api/signal-advice/'+row['id']+'/confirm',json={**confirmation(row),'acknowledge_new_local_processing':True});assert result.status_code==409
    assert get(client,row)['model_receipt']==row['model_receipt'] and len(calls)==1
    with Session(engine()) as db:assert not list(db.scalars(select(m.Task)))

def test_committed_task_lost_response_recovery_does_not_repeat_registered_node(client,monkeypatch):
    r,obj,signal,cap,local=setup(client);row,_,_=start(client,r,signal,cap);calls=[];cloud(monkeypatch,calls,workflow);assert process_once(row['job_id'],deliver=False);row=get(client,row);body={**confirmation(row),'acknowledge_new_local_processing':True}
    import generic_agents.workspace_api as api
    real=api.create
    def lost(*args,**kwargs):
        value=real(*args,**kwargs)
        raise RuntimeError('isolated committed-response interruption')
    monkeypatch.setattr(api,'create',lost)
    try:client.post('/api/signal-advice/'+row['id']+'/confirm',json=body)
    except RuntimeError:pass
    assert get(client,row)['status']=='applying'
    monkeypatch.setattr(api,'create',real);result=client.post('/api/signal-advice/'+row['id']+'/confirm',json=body);assert result.status_code==200,result.text
    with Session(engine()) as db:assert len(list(db.scalars(select(m.Task))))==1 and len(list(db.scalars(select(m.Job).where(m.Job.kind=='execute_workflow_step'))))==1

def test_created_task_cannot_replace_approved_creation_body_or_fixed_media(client,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from generic_agents.security import Principal
    from generic_agents.advice_workflows import verify
    r,obj,signal,cap,local=setup(client);row,_,_=start(client,r,signal,cap);calls=[];cloud(monkeypatch,calls,workflow)
    assert process_once(row['job_id'],deliver=False);row=get(client,row)
    result=client.post('/api/signal-advice/'+row['id']+'/confirm',json={**confirmation(row),'acknowledge_new_local_processing':True});assert result.status_code==200,result.text
    with Session(engine()) as db:
        task=db.get(m.Task,result.json()['dispatch']['task_id']);p=Principal(task.owner_id,task.workspace_id,'admin','isolated_test')
        original=copy.deepcopy(task.data);verify(db,p,task)
        for patch,message in [({'creation_hash':'changed'},'发起依据'),({'image_snapshots':[{'id':'other-original'}]},'媒体')]:
            task.data={**original,**patch}
            with pytest.raises(HTTPException) as error:verify(db,p,task)
            assert message in str(error.value.detail)
        db.rollback()


def test_original_creation_replay_keeps_nonempty_goal_signed_and_cannot_clear_it(client):
    from generic_agents.workspace_api import NewTask,creation_body_hash
    from tests.test_native_goals import setup as goal_setup
    r,obj,_,_,goal=goal_setup(client)
    body={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id']],
          'title':'核对已批准目标的原发起请求','request_id':str(uuid4()),'goal_spec':goal['goal_spec']}
    url='/api/workspace/instances/'+r['instance_id']+'/tasks'
    response=client.post(url,json=body);assert response.status_code==201,response.text
    task=response.json();assert client.post(url,json=body).json()['id']==task['id']
    changed=copy.deepcopy(body);changed['goal_spec']['purpose']='替换原目标，不能借同请求重新批准'
    assert client.post(url,json=changed).status_code==409
    assert client.post(url,json={**body,'goal_spec':None}).status_code==409
    explicit=NewTask.model_validate(body)
    empty=NewTask.model_validate({**body,'goal_spec':None})
    assert creation_body_hash(explicit)!=creation_body_hash(empty)
    with Session(engine()) as db:
        persisted=db.get(m.Task,task['id'])
        assert persisted.data['creation_hash']==creation_body_hash(explicit)
        assert persisted.data['goal_spec']['purpose']==goal['goal_spec']['purpose']
