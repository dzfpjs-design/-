"""The first real registered call must be bounded by the saved parent goal."""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Job,Invocation,CloudCall
from generic_agents.contracts import digest
from tests.test_workflow_abilities import tool,start,finish_job
from tests.test_native_goals import drain


def goal(release,objects,budget=12):
    field=next(f['key'] for f in release['snapshot']['object_types'][1]['fields'] if f['label']=='数量')
    criteria=[{'id':'enough','kind':'evidence_sufficient','description':'原对象资料充分'},{'id':'human','kind':'human_confirmation','description':'核对本地结果后验收'}]
    return {'purpose':'核对原数量登记和本地汇总结果','entity_ids':[o['id'] for o in objects],'required_fields':[field],
        'criteria':criteria,'subgoals':[{'id':'check','description':'核对原资料并执行已确认流程','criterion_ids':['enough','human']}],
        'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}},'observation_mode':'existing_only','max_tool_calls':budget}


def test_parent_goal_is_saved_before_first_auto_call_and_acceptance_uses_original_result(client):
    release,task,objects=start(client,tool(client),goal_factory=goal,ending=True)
    base='/api/workspace/tasks/'+task['id']+'/goal'
    with Session(engine()) as db:
        saved=db.get(Task,task['id']);assert saved.data['native_goal_context'] and saved.data['goal_spec']['entity_ids']==[o['id'] for o in objects]
        assert not list(db.scalars(select(Invocation)))
    assert client.get(base).json()['goal_confirmed']
    task=finish_job(client,task);assert task['status']=='workflow_completed'
    assert task['workflow_runtime']['receipts'][0]['output']['value']==20
    drain(task);state=client.get(base).json();assert state['check']['evaluation']['ready_for_acceptance']
    response=client.post(base+'/accept',json={'request_id':str(uuid4()),'expected_version':state['version'],'basis_hash':state['check']['evaluation']['basis_hash'],'reason':'已对照原数量与注册本地汇总结果'})
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Invocation))))==1 and not list(db.scalars(select(CloudCall)))
        assert len(list(db.scalars(select(Task))))==1


def test_zero_budget_blocks_first_call_at_creation_without_a_tool_job(client):
    release,task,_=start(client,tool(client),goal_factory=lambda r,o:goal(r,o,0),ending=True)
    assert task['status']=='workflow_blocked' and '预算' in task['workflow_runtime']['automatic']['error']
    with Session(engine()) as db:
        assert not list(db.scalars(select(Invocation)))
        assert not list(db.scalars(select(Job).where(Job.kind=='execute_workflow_step')))
        assert db.get(Task,task['id']).data['goal_spec']['max_tool_calls']==0


def test_goal_deadline_is_rechecked_before_queued_first_call(client,monkeypatch):
    from datetime import datetime,timezone,timedelta
    from generic_agents import native_goals
    current=datetime.now(timezone.utc)
    def with_deadline(r,o):return {**goal(r,o),'deadline':(current+timedelta(minutes=5)).isoformat()}
    _,task,_=start(client,tool(client),goal_factory=with_deadline,ending=True)
    monkeypatch.setattr(native_goals,'now',lambda:current+timedelta(minutes=6))
    task=finish_job(client,task)
    assert task['status']=='workflow_blocked' and '截止' in task['workflow_runtime']['automatic']['error']
    with Session(engine()) as db:assert not list(db.scalars(select(Invocation)))


def test_receipt_input_cannot_be_substituted_even_when_output_and_local_digest_match(client):
    release,task,objects=start(client,tool(client),goal_factory=goal,ending=True);task=finish_job(client,task)
    with Session(engine()) as db:
        saved=db.get(Task,task['id']);rt=copy.deepcopy(saved.data['workflow_runtime']);receipt=rt['receipts'][0]
        receipt['input_snapshot']['operation']='sum';receipt['input_hash']=digest(receipt['input_snapshot']);saved.data={**saved.data,'workflow_runtime':rt};db.commit()
    drain(task);base='/api/workspace/tasks/'+task['id']+'/goal';state=client.get(base).json()
    response=client.post(base+'/accept',json={'request_id':str(uuid4()),'expected_version':state['version'],'basis_hash':state['check']['evaluation']['basis_hash'],'reason':'替换输入不得验收'})
    assert response.status_code==409 and '输入版本' in response.json()['detail']
    with Session(engine()) as db:assert not db.get(Task,task['id']).data.get('native_goal_acceptance')


def test_invalid_goal_rolls_back_creation_and_replay_cannot_substitute_goal(client):
    release,task,objects=start(client,tool(client),goal_factory=goal,ending=True)
    with Session(engine()) as db:
        prior=db.get(Task,task['id']);request_id=prior.data['creation_request_id'];count=len(list(db.scalars(select(Job))))
    payload={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[o['id'] for o in objects],'title':'核对公开登记数量','request_id':request_id,'goal_spec':goal(release,objects)}
    path='/api/workspace/instances/'+release['instance_id']+'/tasks'
    assert client.post(path,json=payload).json()['id']==task['id']
    changed=copy.deepcopy(payload);changed['goal_spec']['purpose']='替换原目标'
    assert client.post(path,json=changed).status_code==409
    changed['request_id']=str(uuid4());changed['goal_spec']['required_fields']=['不存在的业务字段']
    assert client.post(path,json=changed).status_code==422
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Task))))==1 and len(list(db.scalars(select(Job))))==count
        assert not list(db.scalars(select(Invocation)))


def test_saved_creation_response_can_be_recovered_after_pause_without_starting_another_call(client):
    release,task,objects=start(client,tool(client),goal_factory=goal,ending=True)
    with Session(engine()) as db:request_id=db.get(Task,task['id']).data['creation_request_id']
    instance=next(row for row in client.get('/api/instances').json() if row['id']==release['instance_id'])
    assert client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}).status_code==200
    payload={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[o['id'] for o in objects],'title':'核对公开登记数量','request_id':request_id,'goal_spec':goal(release,objects)}
    path='/api/workspace/instances/'+release['instance_id']+'/tasks'
    response=client.post(path,json=payload);assert response.status_code==201 and response.json()['id']==task['id']
    assert client.post(path,json={**payload,'request_id':str(uuid4())}).status_code==409
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Task))))==1 and not list(db.scalars(select(Invocation)))
        assert len(list(db.scalars(select(Job).where(Job.kind=='execute_workflow_step'))))==1
