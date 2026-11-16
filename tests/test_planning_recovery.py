"""Durable planning and concurrency on isolated PG, with explicit test-only cloud responses."""
import copy
from datetime import timedelta
from threading import Event,Thread,Barrier
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Plan,Task,Job,Entity,Membership,User,now
from generic_agents.worker import process_once
from generic_agents.contracts import digest
from .test_vertical_slice import manifest,publish,entity,explicit_goal


def setup(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg)
    model=publish(client,manifest('foundation_model',name='planning_model',execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
    task=client.post('/api/tasks',json={'title':'合成规划恢复 · 档案核查','package_id':pkg['id'],'entity_ids':[obj['id']],'fields':['temperature'],'goal_spec':explicit_goal(obj)}).json()
    calls=[]
    def cloud(*a):
        calls.append(True)
        return {'summary':'仅合成计划，尚未执行','steps':[{'title':'查询','agent':'data','action':'query_evidence'},{'title':'报告','agent':'business','action':'report'}]}, {'provider':'explicit_test_fixture','usage':{'total_tokens':0}}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    return pkg,obj,model,task,calls

def request(client,t,m):return client.post('/api/tasks/'+t['id']+'/plan',json={'expected_version':t['version'],'reason':'保存有界规划请求','model_profile_id':m['id']})
def saved(client,t):return next(x for x in client.get('/api/overview').json()['tasks'] if x['id']==t['id'])
def expire(plan):
    with Session(engine()) as db:
        job=db.get(Job,plan['data']['planning_job_id']);job.lease_until=now()-timedelta(seconds=1);db.commit()


def test_post_only_queues_durable_intent_and_exact_job_finishes(client,monkeypatch):
    pkg,obj,m,t,calls=setup(client,monkeypatch);r=request(client,t,m);assert r.status_code==200;p=r.json()
    assert p['status']=='queued' and calls==[] and saved(client,t)['status']=='planning'
    assert p['data']['request']['goal_snapshot']['hash']==t['data']['goal_hash']
    assert process_once(p['data']['planning_job_id']);p=client.get('/api/plans/'+p['id']).json()
    assert p['status']=='review' and calls==[True] and p['data']['planning_checkpoint']['value']['summary']=='仅合成计划，尚未执行'
    assert saved(client,t)['status']=='review'


def test_concurrent_same_version_enqueues_only_one_job(client,monkeypatch):
    _,_,m,t,calls=setup(client,monkeypatch);barrier=Barrier(2)
    def send():barrier.wait(4);return request(client,t,m).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(lambda _:send(),range(2)))
    assert sorted(statuses)==[200,409] and not calls
    jobs=[j for j in client.get('/api/overview').json()['jobs'] if j['kind']=='generate_plan'];assert len(jobs)==1


def test_cancel_queued_planning_prevents_any_model_call(client,monkeypatch):
    _,_,m,t,calls=setup(client,monkeypatch);p=request(client,t,m).json();t=saved(client,t)
    assert client.post('/api/tasks/'+t['id']+'/cancel',json={'expected_version':t['version'],'reason':'模型请求尚未发送，取消'}).status_code==200
    assert not process_once(p['data']['planning_job_id']) and not calls
    assert client.get('/api/plans/'+p['id']).json()['status']=='cancelled'


def test_late_result_after_cancel_saves_receipt_without_reactivating_task(client,monkeypatch):
    _,obj,m,t,calls=setup(client,monkeypatch);entered,release=Event(),Event()
    def cloud(*a):
        calls.append(True);entered.set();assert release.wait(5)
        return {'summary':'迟到测试计划','steps':[{'title':'报告','agent':'business','action':'report'}]}, {'provider':'explicit_test_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud);p=request(client,t,m).json();errors=[]
    def work():
        try:process_once(p['data']['planning_job_id'])
        except BaseException as e:errors.append(e)
    th=Thread(target=work);th.start();assert entered.wait(5)
    latest=saved(client,t);g=explicit_goal(obj)
    assert client.put('/api/tasks/'+t['id']+'/goal',json={'expected_version':latest['version'],'reason':'等待模型期间不能改目标','goal_spec':g}).status_code==409
    assert client.put('/api/tasks/'+t['id']+'/goal/draft',json={'expected_version':latest['version'],'reason':'活动规划不可改草稿','draft':{}}).status_code==409
    assert request(client,latest,m).status_code==409
    assert client.post('/api/tasks/'+t['id']+'/cancel',json={'expected_version':latest['version'],'reason':'停止后续规划和执行'}).status_code==200
    release.set();th.join(5);assert not th.is_alive() and not errors
    assert saved(client,t)['status']=='cancelled';p=client.get('/api/plans/'+p['id']).json();assert p['status']=='cancelled' and p['data']['planning_checkpoint']['receipt']['provider']=='explicit_test_fixture'
    assert calls==[True]


def crash_after_saved_response(client,monkeypatch):
    data=setup(client,monkeypatch);_,_,m,t,_=data;p=request(client,t,m).json()
    from generic_agents.agents import validate
    monkeypatch.setattr('generic_agents.agents.validate',lambda *a:(_ for _ in ()).throw(SystemExit('simulated process exit after saved cloud response')))
    with pytest.raises(SystemExit):process_once(p['data']['planning_job_id'])
    monkeypatch.setattr('generic_agents.agents.validate',validate)
    assert client.get('/api/plans/'+p['id']).json()['data']['planning_checkpoint'];expire(p)
    return data,p

@pytest.mark.parametrize('attempts',[1,3])
def test_saved_response_is_adopted_even_at_recovery_budget_without_new_cloud_call(client,monkeypatch,attempts):
    data,p=crash_after_saved_response(client,monkeypatch);_,_,_,t,calls=data
    with Session(engine()) as db:
        j=db.get(Job,p['data']['planning_job_id']);j.attempts=attempts;db.commit()
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('已保存响应不得再次付费调用'))
    assert process_once(p['data']['planning_job_id']);latest=client.get('/api/plans/'+p['id']).json()
    assert latest['status']=='review' and saved(client,t)['status']=='review' and calls==[True]

@pytest.mark.parametrize('mutation',['source','hash'])
def test_changed_source_or_corrupt_checkpoint_blocks_without_recalling_model(client,monkeypatch,mutation):
    data,p=crash_after_saved_response(client,monkeypatch);_,obj,_,t,calls=data
    with Session(engine()) as db:
        if mutation=='source':
            e=db.get(Entity,obj['id']);e.data={**e.data,'temperature':99};e.version+=1
        else:
            row=db.get(Plan,p['id']);value=copy.deepcopy(row.data);value['planning_checkpoint']['value']['summary']='tampered';row.data=value
        db.commit()
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('变化时应阻塞，不自动重付费'))
    assert process_once(p['data']['planning_job_id']);latest=client.get('/api/plans/'+p['id']).json()
    assert latest['status']=='blocked_evidence' and saved(client,t)['status']=='waiting_evidence' and calls==[True]


def test_cancel_after_saved_response_and_before_takeover_is_terminal(client,monkeypatch):
    data,p=crash_after_saved_response(client,monkeypatch);_,_,_,t,calls=data;t=saved(client,t)
    assert client.post('/api/tasks/'+t['id']+'/cancel',json={'expected_version':t['version'],'reason':'已保存候选，但停止后续工作'}).status_code==200
    assert process_once(p['data']['planning_job_id']);assert saved(client,t)['status']=='cancelled' and calls==[True]


def test_revoked_actor_cannot_adopt_saved_response(client,monkeypatch):
    data,p=crash_after_saved_response(client,monkeypatch);_,obj,_,t,calls=data
    with Session(engine()) as db:
        job=db.get(Job,p['data']['planning_job_id']);user=db.get(User,job.data['actor_id']);user.active=False;db.commit()
    assert process_once(p['data']['planning_job_id'])
    with Session(engine()) as db:
        assert db.get(Plan,p['id']).status=='failed' and db.get(Task,t['id']).status=='planning_failed'
    assert calls==[True]


def test_review_artifact_saved_before_job_completion_is_not_regenerated(client,monkeypatch):
    _,_,m,t,calls=setup(client,monkeypatch);p=request(client,t,m).json()
    from generic_agents.planning import run
    def die_after_commit(db,*a,**kw):
        result=run(db,*a,**kw);db.commit();raise SystemExit('saved review artifact before job completion')
    monkeypatch.setattr('generic_agents.planning.run',die_after_commit)
    with pytest.raises(SystemExit):process_once(p['data']['planning_job_id'])
    version=saved(client,t)['version'];expire(p)
    monkeypatch.setattr('generic_agents.planning.run',run)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('已保存审核成果不得再调用'))
    assert process_once(p['data']['planning_job_id']);assert saved(client,t)['version']==version and calls==[True]

from .test_business_tools import service,INPUT,OUTPUT


def test_recovered_tool_plan_has_stable_binding_and_executes_only_once(client,monkeypatch,service):
    state=service
    # Independent local HTTP server is real; model response is the declared fixture.
    tool=publish(client,manifest('tool',name='planning_tool',specification={},input_schema=INPUT,output_schema=OUTPUT,
        allowed_agent_roles=['data'],data_policy={'external_allowed':True},execution={'adapter':'http','endpoint':service['http'],'input_bindings':{'records':'archive_records'}}))
    pkg=publish(client,manifest(dependencies=[tool['id']],specification={'business_tool_ids':[tool['id']],'entity_schemas':manifest()['specification']['entity_schemas']}));obj=entity(client,pkg)
    m=publish(client,manifest('foundation_model',name='tool_model',execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
    t=client.post('/api/tasks',json={'title':'合成恢复后执行一次工具','package_id':pkg['id'],'entity_ids':[obj['id']],'fields':['temperature'],'goal_spec':explicit_goal(obj,source='archive')}).json();calls=[]
    def cloud(*a):
        calls.append(True);return {'summary':'将计算档案','steps':[{'title':'计算','agent':'data','action':'invoke_capability','capability_id':tool['id'],'arguments':{'field':'temperature'}},{'title':'报告','agent':'business','action':'report'}]},{'provider':'explicit_test_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud);p=request(client,t,m).json()
    from generic_agents.agents import validate
    monkeypatch.setattr('generic_agents.agents.validate',lambda *a:(_ for _ in ()).throw(SystemExit('after saved raw response')))
    with pytest.raises(SystemExit):process_once(p['data']['planning_job_id'])
    monkeypatch.setattr('generic_agents.agents.validate',validate);expire(p)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('不能重复规划模型调用'))
    assert process_once(p['data']['planning_job_id']);p=client.get('/api/plans/'+p['id']).json();assert p['status']=='review'
    assert client.post('/api/plans/'+p['id']+'/approve',json={'expected_version':p['version'],'reason':'批准恢复后的同一输入版本'}).status_code==200
    assert process_once();assert saved(client,t)['status']=='awaiting_acceptance' and state['calls']==1 and calls==[True]


def test_expired_goal_is_not_queued_or_sent_to_model(client,monkeypatch):
    _,obj,m,t,calls=setup(client,monkeypatch);g=explicit_goal(obj);g['deadline']=(now()-timedelta(seconds=1)).isoformat()
    r=client.put('/api/tasks/'+t['id']+'/goal',json={'expected_version':1,'reason':'保留到期目标作为核查记录','goal_spec':g});assert r.status_code==200;t=r.json()
    r=request(client,t,m);assert r.status_code==409 and '截止' in r.json()['detail'] and not calls
    assert not client.get('/api/overview').json()['jobs']
