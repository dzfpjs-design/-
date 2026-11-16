"""Business delegation and truthful completion, real isolated PG; no paid model calls."""
import copy,json
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Plan,Job,Invocation,DataRun,Memory
from generic_agents.worker import process_once
from .test_data_tools import setup,post,external_result
from .test_goal_time import model,saved_task

def goal(objects,steps,*,budget=2,expected=20,external=False):
 return {'purpose':'业务委托验收：筛选名称含热的证据，再计算温度平均值并核查','entity_ids':[o['id'] for o in objects],
  'required_fields':['name','temperature'],'observation_mode':'existing_only','evidence':{'source':'archive'},'max_tool_calls':budget,
  'data_query':{'tool_steps':steps,'allow_external_tools':external},
  'subgoals':[{'id':'calculation','description':'完成有来源的委托计算','criterion_ids':['sources','mean','human']}],
  'criteria':[{'id':'sources','description':'委托证据充分','kind':'evidence_sufficient'},
   {'id':'mean','description':'最终均值达到业务标准','kind':'query_value','field':'value','operator':'eq','expected':expected},
   {'id':'human','description':'负责人协议验收','kind':'human_confirmation'}]}

def fixture(c,monkeypatch,*,budget=2,expected=20,remote=False,observations=False):
 pkg,objects,tools,steps=setup(c,remote=remote,monkeypatch=monkeypatch)
 spec=goal(objects,steps,budget=budget,expected=expected,external=remote)
 if observations:spec['evidence']={'source':'observations','minimum_verification':'unverified_extraction'}
 task=post(c,'/api/tasks',{'title':'业务委托有界查询验收','package_id':pkg['id'],'entity_ids':[o['id'] for o in objects],'goal_spec':spec})
 m=model(c);calls=[]
 def fake(cap,messages,schema,p):
  evidence=json.loads(messages[0]['content'])['evidence'];assert evidence['derived_records'][0]['values']['value']==20
  calls.append(evidence)
  return {'summary':'明确模型替身：核对已保存委托查询后报告','steps':[{'title':'查询委托结果','agent':'data','action':'query_evidence'},
   {'title':'报告','agent':'business','action':'report'}]},{'provider':'test','usage':{'total_tokens':1}}
 monkeypatch.setattr('generic_agents.agents.cloud_json',fake)
 plan=post(c,'/api/tasks/'+task['id']+'/plan',{'expected_version':task['version'],'reason':'执行已确认查询方案后再规划','model_profile_id':m['id']})
 assert process_once(plan['data']['planning_job_id'])
 return task,pkg,objects,tools,steps,m,plan,calls

def run_data(c,task):
 query_id=saved_task(c,task['id'])['data']['data_query_run_id']
 job=next(j for j in c.get('/api/overview').json()['jobs'] if j['data'].get('query_id')==query_id)
 assert process_once(job['id']);return c.get('/api/data/queries/'+query_id).json()

def resume(c,plan):
 jobs=[j for j in c.get('/api/overview').json()['jobs'] if j['kind']=='generate_plan' and j['data']['plan_id']==plan['id'] and j['status']=='queued']
 assert len(jobs)==1,[(t['status'],t['data'].get('execution_error')) for t in c.get('/api/overview').json()['tasks']];assert process_once(jobs[0]['id']);return c.get('/api/plans/'+plan['id']).json()

def execute(c,task,plan):
 approved=post(c,'/api/plans/'+plan['id']+'/approve',{'expected_version':plan['version'],'reason':'核对委托均值和原证据'})
 job=next(j for j in c.get('/api/overview').json()['jobs'] if j['kind']=='execute_plan' and j['data']['plan_id']==plan['id'])
 assert process_once(job['id']);return saved_task(c,task['id'])

def test_business_delegates_before_model_and_accepts_actual_mean_with_provenance(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 assert not calls and saved_task(client,task['id'])['status']=='waiting_data'
 assert client.get('/api/plans/'+plan['id']).json()['status']=='waiting_data'
 query=run_data(client,task);assert query['status']=='sufficient'
 plan=resume(client,plan);assert plan['status']=='review' and len(calls)==1
 assert plan['data']['result']['evidence']['data_query']['id']==query['id']
 task=execute(client,task,plan);assert task['status']=='awaiting_acceptance'
 assert next(c for c in task['data']['goal_evaluation']['checks'] if c['id']=='mean')['passed']
 task=post(client,'/api/tasks/'+task['id']+'/accept',{'expected_version':task['version'],'reason':'核对两条10/30来源和20计算；仅合成协议验收'})
 assert task['status']=='completed' and task['data']['final_evidence']['derived_records'][0]['values']['value']==20
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==2

def test_report_with_valid_raw_fields_cannot_satisfy_wrong_derived_target(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,expected=25)
 run_data(client,task);plan=resume(client,plan);task=execute(client,task,plan)
 assert task['status']=='waiting_evidence'
 assert not next(c for c in task['data']['goal_evaluation']['checks'] if c['id']=='mean')['passed']
 assert client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':task['version'],'reason':'不得以已生成报告验收'}).status_code==409

def test_source_change_blocks_old_query_plan_before_approval_and_no_new_calls(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 run_data(client,task);plan=resume(client,plan)
 assert client.patch('/api/entities/'+objects[0]['id'],json={'expected_version':1,'attributes':{'name':'热设备甲','temperature':50},'reason':'事实改变'}).status_code==200
 r=client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'旧值不得再批准'})
 assert r.status_code==409 and '来源' in r.json()['detail']
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==2

def test_total_budget_counts_delegation_across_new_plans_and_versions(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 run_data(client,task);plan=resume(client,plan)
 task=saved_task(client,task['id']);g=copy.deepcopy(task['data']['goal_spec']);g['purpose']='新的目的仍使用相同任务累计预算'
 changed=client.put('/api/tasks/'+task['id']+'/goal',json={'expected_version':task['version'],'reason':'保存新目标但未增加预算','goal_spec':g});assert changed.status_code==200
 changed=changed.json();new=post(client,'/api/tasks/'+task['id']+'/plan',{'expected_version':changed['version'],'reason':'不能超过累计两次','model_profile_id':m['id']})
 assert process_once(new['data']['planning_job_id']);new=client.get('/api/plans/'+new['id']).json()
 assert new['status']=='blocked_evidence' and '累计调用预算' in new['data']['error'] and len(calls)==1
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==2

def test_cancel_task_during_first_call_stops_following_call_and_no_model_reactivation(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 import generic_agents.gateway as gateway
 original=gateway.invoke
 def cancel(*args,**kwargs):
  call=original(*args,**kwargs);latest=saved_task(client,task['id'])
  post(client,'/api/tasks/'+task['id']+'/cancel',{'expected_version':latest['version'],'reason':'停止委托及旧业务'})
  return call
 monkeypatch.setattr(gateway,'invoke',cancel);query=run_data(client,task)
 assert query['status']=='blocked_evidence' and saved_task(client,task['id'])['status']=='cancelled' and not calls
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1
 assert not [j for j in client.get('/api/overview').json()['jobs'] if j['kind']=='generate_plan' and j['status']=='queued']

def test_external_result_waits_for_review_then_withdrawal_blocks_approval_and_case_reuse(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,remote=True)
 from generic_agents.data_tools import builtin
 monkeypatch.setattr('generic_agents.connectors.call_http',lambda execution,inputs,key:builtin('data_record_aggregate',inputs))
 query=run_data(client,task);assert query['status']=='needs_review' and not calls
 assert saved_task(client,task['id'])['status']=='waiting_evidence'
 query=post(client,'/api/data/queries/'+query['id']+'/review-derived',{'expected_version':query['version'],'decision':'confirmed','reason':'合成专业核查两条原值及20'})
 plan=resume(client,plan);task=execute(client,task,plan)
 task=post(client,'/api/tasks/'+task['id']+'/accept',{'expected_version':task['version'],'reason':'合成推导协议通过'})
 with Session(engine()) as db:
  mem=db.scalar(select(Memory).where(Memory.task_id==task['id']));mem.status='trusted';db.commit()
  from generic_agents.learning import eligible
  from generic_agents.security import Principal
  p=Principal(task['owner_id'],task['workspace_id'],'admin','test')
  assert eligible(db,p,pkg['id'])
 rejected=post(client,'/api/data/queries/'+query['id']+'/review-derived',{'expected_version':query['version'],'decision':'rejected','reason':'撤回原专业核查，保留旧验收历史'})
 assert rejected['status']=='rejected' and len(rejected['data']['derived_review_history'])==2
 with Session(engine()) as db:assert not eligible(db,p,pkg['id'])

def test_delegated_query_parameters_cannot_be_detached_from_confirmed_goal(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 query_id=saved_task(client,task['id'])['data']['data_query_run_id'];query=client.get('/api/data/queries/'+query_id).json()
 r=client.put('/api/data/queries/'+query_id+'/draft',json={'expected_version':query['version'],'reason':'不得绕开业务目标','draft':{'fields':['name']}})
 assert r.status_code==409 and not calls

def test_invalid_goal_query_output_scope_cycle_and_budget_do_not_change_task(client,monkeypatch):
 pkg,objects,tools,steps=setup(client)
 task=post(client,'/api/tasks',{'title':'未确认业务查询','package_id':pkg['id'],'entity_ids':[o['id'] for o in objects]})
 for modify in [lambda g:g['criteria'][1].update(field='secret'),lambda g:g['data_query']['tool_steps'][0].update(input_from='average'),
  lambda g:g.update(max_tool_calls=1),lambda g:g['data_query']['tool_steps'][0]['parameters'].update(field='secret')]:
  g=copy.deepcopy(goal(objects,steps));modify(g)
  r=client.put('/api/tasks/'+task['id']+'/goal',json={'expected_version':task['version'],'reason':'不能确认无效契约','goal_spec':g})
  assert r.status_code==422,r.text
  assert saved_task(client,task['id'])['data']['goal_spec'] is None

def test_derived_cache_tampering_is_not_accepted_for_business_planning(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 query=run_data(client,task)
 with Session(engine()) as db:
  row=db.get(DataRun,query['id']);data=copy.deepcopy(row.data);data['result']['derived_records'][0]['values']['value']=999;row.data=data;db.commit()
 plan=resume(client,plan);assert plan['status']=='blocked_evidence' and not calls

def test_saved_success_receipts_recover_at_attempt_limit_without_extra_invocations(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch)
 query=run_data(client,task)
 with Session(engine()) as db:
  row=db.get(DataRun,query['id']);row.status='queued'
  job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==row.id));job.status='queued';job.attempts=3;db.commit();identifier=job.id
 assert process_once(identifier);assert client.get('/api/data/queries/'+query['id']).json()['status']=='sufficient'
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==2

def test_gap_rechecks_resume_original_business_and_reuse_one_computation_for_three_gaps(client,monkeypatch):
 from .test_data_gaps import drain,observe
 from .test_vertical_slice import publish,manifest
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,observations=True)
 original=run_data(client,task);assert original['status']=='insufficient';drain()
 source=publish(client,manifest('source',name='delegated_source',execution={'adapter':'upload'},specification={'timezone':'UTC'}))
 extractor=publish(client,manifest('tool',name='delegated_extract',allowed_agent_roles=['perception'],
  execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[]}))
 for obj in objects:observe(client,obj,source,extractor,attrs=obj['data'])
 drain();latest=saved_task(client,task['id']);assert latest['status']=='review',latest['data'].get('execution_error')
 gaps=client.get('/api/data/gaps',params={'query_id':original['id']}).json()['items']
 assert len(gaps)==3 and all(g['status']=='resolved' for g in gaps)
 assert len({g['data']['latest_recheck_query_id'] for g in gaps})==1
 assert client.get('/api/data/queries/'+original['id']).json()['status']=='insufficient' and len(calls)==1
 assert len(client.get('/api/overview').json()['tasks'])==1
 with Session(engine()) as db:
  assert len(db.scalars(select(Invocation).where(Invocation.capability_id.in_([t['id'] for t in tools]))).all())==2

def test_collection_receipts_are_required_before_business_resumes(client,monkeypatch):
 from datetime import timedelta
 from .test_vertical_slice import publish,manifest
 from generic_agents.models import Observation,Event,now
 from generic_agents.worker import deliver_events
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,observations=True)
 source=publish(client,manifest('source',name='receipt_source',execution={'adapter':'upload'},specification={'timezone':'UTC'}))
 ids=[]
 with Session(engine()) as db:
  for n,obj in enumerate(objects):
   at=now()-timedelta(seconds=1);obs=Observation(workspace_id=task['workspace_id'],entity_id=obj['id'],source_id=source['id'],
    source_record_id='receipt-'+str(n),processing_version='1',observed_at=at,business_day=str(at.date()),status='processed',
    data={'extracted':{'attributes':obj['data']},'evidence_ids':[],'submitted_by':task['owner_id']})
   db.add(obs);db.flush();ids.append((obs.id,obj['id']))
  db.commit()
 query=run_data(client,task);assert query['status']=='sufficient'
 assert saved_task(client,task['id'])['status']=='waiting_evidence' and not calls
 with Session(engine()) as db:
  for identifier,entity_id in ids:db.add(Event(workspace_id=task['workspace_id'],kind='observation.data',idempotency_key='data:'+identifier,
   data={'observation_id':identifier,'entity_id':entity_id,'actor_id':task['owner_id']}))
  db.commit()
 deliver_events();plan=resume(client,plan);assert plan['status']=='review' and len(calls)==1

def test_cancelled_goal_stops_gap_routing_without_new_devices_or_query_calls(client,monkeypatch):
 from .test_data_gaps import drain
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,observations=True)
 original=run_data(client,task);latest=saved_task(client,task['id'])
 post(client,'/api/tasks/'+task['id']+'/cancel',{'expected_version':latest['version'],'reason':'停止旧目标补证'})
 drain();gaps=client.get('/api/data/gaps',params={'query_id':original['id']}).json()['items']
 assert len(gaps)==3 and all(g['status']=='cancelled' for g in gaps)
 assert not client.get('/api/overview').json()['acquisitions'] and not calls

def test_sdk_preserves_tool_steps_budget_and_durable_external_review(client,monkeypatch):
 import httpx
 from generic_agents.sdk import Client
 from .test_data_tools import request,finish
 objects,steps,external=external_result(client,monkeypatch)
 token=client.post('/api/tokens').json()['token'];sdk=Client('http://testserver',token);sdk.http.close()
 def transport(r):
  response=client.request(r.method,r.url.path,content=r.content,headers=dict(r.headers))
  return httpx.Response(response.status_code,content=response.content,headers={'Content-Type':response.headers.get('content-type','application/json')},request=r)
 sdk.http=httpx.Client(base_url='http://testserver',headers={'Authorization':'Bearer '+token},transport=httpx.MockTransport(transport))
 try:
  query=sdk.query('名称含热记录的温度平均值',[o['id'] for o in objects],fields=['name','temperature'],mode='structured',
   evidence_requirement={'source':'archive'},tool_steps=steps,max_tool_calls=2,allow_external_tools=True)
  assert query['data']['request']['tool_steps']==steps and query['data']['request']['max_tool_calls']==2
  query=finish(client,query);assert sdk.get_query(query['id'])['status']=='needs_review'
  confirmed=sdk.review_derived(query['id'],query['version'],'confirmed','核对SDK提交的原记录及20计算')
  assert confirmed['status']=='sufficient' and sdk.get_query(query['id'])['data']['derived_review']['reason'].startswith('核对SDK')
 finally:sdk.close()


def test_missing_delegated_observations_are_not_described_as_changed_sources(client,monkeypatch):
 task,pkg,objects,tools,steps,m,plan,calls=fixture(client,monkeypatch,observations=True)
 query=run_data(client,task);assert query['status']=='insufficient' and not calls
 task=saved_task(client,task['id']);assert task['status']=='waiting_evidence'
 assert '证据仍不足' in task['data']['execution_error'] and '已变化' not in task['data']['execution_error']
