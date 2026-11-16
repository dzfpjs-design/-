"""Native task delegation reuses the original inference and human progress."""
import copy
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.worker import process_once
from tests.test_native_signal_reviews import prepare,link_body
from tests.test_native_receipts import scenes,pairs,pytestmark
from tests.test_vertical_slice import publish,manifest
from tests.test_signal_advice import start,cloud,get,confirmation

def test_original_task_route_links_once_then_reuses_review_without_new_inference(client,pairs,monkeypatch):
    _,r,base,task,signal=prepare(client,pairs,completed=True)
    with Session(engine()) as db:before=copy.deepcopy(db.get(m.Task,task['id']).data);native_before=len(list(db.query(m.GeoPair)))
    cap=publish(client,manifest('foundation_model',name='native_advice_qwen',allowed_agent_roles=['business'],execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':2048},data_policy={'cloud_allowed':True}));calls=[]
    def original(value,context):value['action']={'route':'original_task','business_id':None,'task_id':context['original_task_id'],'criteria':None,'purpose':'核对原任务候选，不重新推理'}
    cloud(monkeypatch,calls,original)
    for attempt in range(2):
        row,_,_=start(client,r,signal,cap);assert process_once(row['job_id'],deliver=False);row=get(client,row)
        assert row['request']['original_task']['already_linked']==bool(attempt)
        body=confirmation(row);response=client.post('/api/signal-advice/'+row['id']+'/confirm',json=body);assert response.status_code==200,response.text
        assert response.json()['dispatch']['task_id']==task['id']
        signal=client.get(base+'/signals').json()['records'][0]
        with Session(engine()) as db:
            assert db.get(m.Task,task['id']).data==before
            assert len(list(db.query(m.GeoPair)))==native_before
    assert len(calls)==2

def test_registered_native_workflow_uses_fixed_original_inputs_and_preserves_old_progress(client,pairs,monkeypatch):
    from tests.test_advice_workflows import workflow
    from tests.test_workflow_abilities import finish_job
    _,r,base,original,signal=prepare(client,pairs,completed=True)
    cap=publish(client,manifest('foundation_model',name='registered_native_advice',allowed_agent_roles=['business'],execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':2048},data_policy={'cloud_allowed':True}));calls=[]
    with Session(engine()) as db:before=copy.deepcopy(db.get(m.Task,original['id']).data)
    row,_,preview=start(client,r,signal,cap)
    context=preview['cloud_input'];progress=next(v for v in context['references'] if v['id']=='original-task')
    assert 'workflow_completed' in progress['text'] and '原流程结论保留' in progress['text']
    assert context['workflow_choices'][0]['input_sets'][0]['id']=='original_media'
    cloud(monkeypatch,calls,workflow);assert process_once(row['job_id'],deliver=False);row=get(client,row)
    result=client.post('/api/signal-advice/'+row['id']+'/confirm',json={**confirmation(row),'acknowledge_new_local_processing':True});assert result.status_code==200,result.text
    task=client.get('/api/workspace/tasks/'+result.json()['dispatch']['task_id']).json();task=finish_job(client,task)
    assert task['status']=='workflow_waiting' and task['workflow_runtime']['receipts'][0]['execution_mode']=='registered_ability'
    with Session(engine()) as db:assert db.get(m.Task,original['id']).data==before
    assert client.get(base+'/signals').json()['records'][0]==signal
