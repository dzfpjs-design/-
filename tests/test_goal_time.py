from .planning_helpers import post_plan
"""Goal/date contracts on isolated PostgreSQL; cloud fixtures are explicitly synthetic."""
from datetime import datetime,timedelta,timezone
import copy
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.db import engine
from generic_agents.models import Observation,Entity,Plan,Task,Job,now
from generic_agents.temporal import resolve,matches,instant
from generic_agents.worker import process_once
from .test_vertical_slice import manifest,publish,entity,explicit_goal
from .test_coordination import planned_task


def saved_task(client,id):return next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==id)
def model(client):return publish(client,manifest('foundation_model',name='goal_qwen',execution={'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
def create_task(client,pkg,obj,goal=None):
    r=client.post('/api/tasks',json={'title':'核对昨天温度与明确完成标准','package_id':pkg['id'],'entity_ids':[obj['id']],'fields':['temperature'],**({'goal_spec':goal} if goal else {})});assert r.status_code==201,r.text;return r.json()
def query(client,obj,req,question='限定时间核查温度',**kw):
    r=client.post('/api/data/queries',json={'question':question,'entity_ids':[obj['id']],'fields':['temperature'],'mode':'structured','evidence_requirement':req,**kw});assert r.status_code==200,r.text;return r.json()
def observation(obj,source,at,value,record):
    with Session(engine()) as db:
        o=Observation(workspace_id=obj['workspace_id'],source_id=source['id'],entity_id=obj['id'],source_record_id=record,processing_version='1',observed_at=at,business_day=str(at.date()),status='processed',data={'extracted':{'attributes':{'temperature':value},'anomalies':[]},'evidence_ids':[]});db.add(o);db.commit();return o.id

@pytest.mark.parametrize('day,hours',[('2026-03-08',23),('2026-11-01',25)])
def test_calendar_dates_resolve_dst_and_end_boundary(day,hours):
    window=resolve({'mode':'range','timezone':'America/Los_Angeles','start':day,'end':day})
    start,end=instant(window['start']),instant(window['end']);assert (end-start).total_seconds()==hours*3600
    assert matches(start,window,end);assert not matches(end,window,end)

def test_relative_date_uses_business_zone_and_rejects_future():
    ref=instant('2026-10-05T01:00:00+00:00');w=resolve({'mode':'relative','timezone':'America/Los_Angeles','relative':'yesterday'},ref)
    assert w['start']=='2026-10-03T07:00:00+00:00' and w['end']=='2026-10-04T07:00:00+00:00'
    assert not matches(ref+timedelta(seconds=1),resolve({'mode':'archive'}),ref)
    with pytest.raises(HTTPException):resolve({'mode':'relative','relative':'today'},ref)
    with pytest.raises(HTTPException):resolve({'mode':'as_of','at':'2026-10-04','timezone':'Asia/Shanghai'},ref)


def test_unconfirmed_title_cannot_create_official_plan_and_draft_is_durable(client):
    pkg=publish(client,manifest());obj=entity(client,pkg);task=create_task(client,pkg,obj);m=model(client)
    r=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'未确认不能规划','model_profile_id':m['id']});assert r.status_code==409
    draft={'purpose':'尚待确定阈值','required_fields':['temperature'],'form_reason':'保存半填条件'}
    r=client.put('/api/tasks/'+task['id']+'/goal/draft',json={'expected_version':task['version'],'reason':'保存未完成工作','draft':draft});assert r.status_code==200
    saved=saved_task(client,task['id']);assert saved['data']['goal_draft']==draft
    assert saved['version']==task['version']+1
    assert client.put('/api/tasks/'+task['id']+'/goal/draft',json={'expected_version':task['version'],'reason':'旧编辑器不可覆盖','draft':{}}).status_code==409
    assert saved_task(client,task['id'])['data']['goal_draft']==draft
    assert client.post('/api/tasks/'+task['id']+'/goal/interpret',json={'expected_version':task['version'],
        'reason':'旧编辑器也不能另起目标解析','model_profile_id':m['id']}).status_code==409
    with Session(engine()) as db:
        assert db.scalar(select(Job).where(Job.kind=='interpret_goal',Job.data['task_id'].as_string()==task['id'])) is None

@pytest.mark.parametrize('mutation',['scope','field','tool','relative_zone','unlinked','conflict'])
def test_invalid_goal_contracts_are_rejected(client,mutation):
    pkg=publish(client,manifest());obj=entity(client,pkg);task=create_task(client,pkg,obj);g=explicit_goal(obj)
    if mutation=='scope':g['entity_ids']=['outside']
    if mutation=='field':g['required_fields']=['invented']
    if mutation=='tool':g['criteria'][0].update(kind='capability_success',capability_id='outside')
    if mutation=='relative_zone':g['evidence']['time']={'mode':'relative','relative':'yesterday'}
    if mutation=='unlinked':g['criteria'].append({'id':'missing','description':'没有关联','kind':'evidence_sufficient'})
    if mutation=='conflict':g['stop_on_conflict']=False
    r=client.put('/api/tasks/'+task['id']+'/goal',json={'expected_version':task['version'],'reason':'核对完整目标','goal_spec':g});assert r.status_code==422,r.text
    assert saved_task(client,task['id'])['data']['goal_spec'] is None


def test_goal_revision_supersedes_old_plan_without_erasing_receipts(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);task,plan=planned_task(client,obj,pkg,monkeypatch);t=saved_task(client,task['id']);g=explicit_goal(obj);g['purpose']='新目标，要求同一字段达到阈值';g['criteria'].append({'id':'threshold','description':'温度低于20','kind':'field_value','field':'temperature','operator':'lt','expected':20});g['subgoals'][0]['criterion_ids'].append('threshold')
    r=client.put('/api/tasks/'+t['id']+'/goal',json={'expected_version':t['version'],'reason':'原业务标准已调整','goal_spec':g});assert r.status_code==200,r.text
    assert r.json()['data']['goal_version']==2 and len(r.json()['data']['goal_history'])==1
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'旧计划不可批准'}).status_code==409
    plans=client.get('/api/overview').json()['plans'];assert next(p for p in plans if p['id']==plan['id'])['status']=='superseded'


def test_evidence_report_cannot_satisfy_unmet_field_goal(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);g=explicit_goal(obj);g['criteria'].append({'id':'threshold','description':'所有观测温度低于20','kind':'field_value','field':'temperature','operator':'lt','expected':20});g['subgoals'][0]['criterion_ids'].append('threshold');task=create_task(client,pkg,obj,g);m=model(client)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:({'summary':'合成计划','steps':[{'title':'查询','agent':'data','action':'query_evidence'},{'title':'报告','agent':'business','action':'report'}]},{}))
    r=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':1,'reason':'规划可核验条件','model_profile_id':m['id']});assert r.status_code==200;r=r.json();assert r['data']['goal_snapshot']['version']==1
    assert r['data']['result']['plan']['steps'][1]['depends_on']==['step_1']
    assert client.post('/api/plans/'+r['id']+'/approve',json={'expected_version':r['version'],'reason':'批准限定取证'}).status_code==200
    assert process_once();t=saved_task(client,task['id']);assert t['status']=='waiting_evidence'
    assert not next(c for c in t['data']['goal_evaluation']['checks'] if c['id']=='threshold')['passed']
    assert client.post('/api/tasks/'+t['id']+'/accept',json={'expected_version':t['version'],'reason':'报告不等于业务成功'}).status_code==409


def test_observation_window_excludes_outside_future_and_unconfirmed_values(client):
    pkg=publish(client,manifest());obj=entity(client,pkg);src=publish(client,manifest('source',name='date_source',execution={'adapter':'upload'}))
    end=now()-timedelta(days=2);start=end-timedelta(days=1)
    admitted=observation(obj,src,start,26,'in');observation(obj,src,end,900,'end');observation(obj,src,start-timedelta(seconds=1),901,'before');observation(obj,src,now()+timedelta(days=1),902,'future')
    req={'source':'observations','minimum_verification':'confirmed','time':{'mode':'range','start':start.isoformat(),'end':end.isoformat()}}
    r=query(client,obj,req);assert r['status']=='insufficient' and r['data']['result']['observation_evidence']==[]
    assert client.post('/api/observations/'+admitted+'/verify',json={'expected_version':1,'decision':'confirmed','reason':'根据合成原始记录核实'}).status_code==200
    r=query(client,obj,req);assert r['status']=='sufficient';rows=r['data']['result']['observation_evidence'];assert [(x['observation_id'],x['attributes']) for x in rows]==[(admitted,{'temperature':26})]
    assert rows[0]['verification']=='confirmed'
    assert client.post('/api/observations/'+admitted+'/verify',json={'expected_version':2,'decision':'rejected','reason':'重新核查后撤回'}).status_code==200
    r=query(client,obj,req);assert r['status']=='insufficient';assert r['data']['result']['observation_evidence']==[]


def test_confirmation_fingerprint_cannot_survive_extraction_tampering(client):
    pkg=publish(client,manifest());obj=entity(client,pkg);src=publish(client,manifest('source',name='confirmed_source',execution={'adapter':'upload'}));at=now()-timedelta(seconds=1);id=observation(obj,src,at,26,'original')
    assert client.post('/api/observations/'+id+'/verify',json={'expected_version':1,'decision':'confirmed','reason':'确认合成值'}).status_code==200
    with Session(engine()) as db:
        o=db.get(Observation,id);o.data={**o.data,'extracted':{'attributes':{'temperature':99},'anomalies':[]}};db.commit()
    r=query(client,obj,{'source':'observations','minimum_verification':'confirmed'});assert r['status']=='insufficient'

@pytest.mark.parametrize('question',['昨天温度如何','过去3天温度如何','2026-10-01的温度','最新温度如何'])
def test_ambiguous_or_contextless_time_is_saved_without_calling_cloud(client,monkeypatch,question):
    pkg=publish(client,manifest());obj=entity(client,pkg);m=model(client)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('不得为缺少日期语义的问题付费调用'))
    r=client.post('/api/data/queries',json={'question':question,'entity_ids':[obj['id']],'fields':['temperature'],'mode':'cloud','model_profile_id':m['id']});assert r.status_code==200
    assert r.json()['status']=='needs_clarification';assert r.json()['data']['request']['clarifications'];assert not client.get('/api/overview').json()['jobs']


def test_latest_window_moves_and_date_source_stays_scoped(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);src=publish(client,manifest('source',name='last_source',execution={'adapter':'upload'}));id=observation(obj,src,now()-timedelta(seconds=10),26,'last')
    req={'source':'observations','minimum_verification':'unverified_extraction','time':{'mode':'latest','max_age_seconds':30}}
    r=query(client,obj,req);assert r['status']=='sufficient';fixed=r['data']['result']['resolved_time']
    from generic_agents.security import Principal
    from generic_agents.data_requirements import query as scoped_query
    from generic_agents.models import User
    with Session(engine()) as db:
        uid=db.scalar(select(User.id).where(User.username==client.get('/api/me').json()['username']))
        p=Principal(uid,obj['workspace_id'],'admin','test')
        monkeypatch.setattr('generic_agents.data_requirements.now',lambda:instant(fixed['reference_at'])+timedelta(seconds=60))
        result=scoped_query(db,p,[obj['id']],['temperature'],req,fixed_window=fixed);assert result['status']=='insufficient'


def test_queued_goal_interpretation_freezes_date_at_request_and_requires_confirmation(client,monkeypatch):
    pkg=publish(client,manifest(specification={**manifest()['specification'],'timezone':'Asia/Shanghai'}));obj=entity(client,pkg);task=create_task(client,pkg,obj);m=model(client);candidate=explicit_goal(obj);candidate['evidence']['time']={'mode':'relative','relative':'yesterday','timezone':'Asia/Shanghai'}
    monkeypatch.setattr('generic_agents.goals.cloud_json',lambda *a:({'goal':candidate,'clarifications':[]},{'provider':'test_fixture'}))
    r=client.post('/api/tasks/'+task['id']+'/goal/interpret',json={'expected_version':1,'reason':'解析候选','model_profile_id':m['id']});assert r.status_code==200,r.text
    request=r.json()['data']['goal_interpretation_request'];expected=resolve(candidate['evidence']['time'],instant(request['requested_at']))
    monkeypatch.setattr('generic_agents.temporal.now',lambda:instant(request['requested_at'])+timedelta(days=2))
    assert process_once();t=saved_task(client,task['id']);assert t['status']=='goal_review';assert t['data']['goal_spec'] is None
    time=t['data']['goal_candidate']['evidence']['time'];assert time['start']==expected['start'] and time['end']==expected['end']
    assert post_plan(client,'/api/tasks/'+t['id']+'/plan',json={'expected_version':t['version'],'reason':'候选仍未确认','model_profile_id':m['id']}).status_code==409


def test_model_cannot_omit_a_selected_entity(client,monkeypatch):
    pkg=publish(client,manifest());a=entity(client,pkg);b=entity(client,pkg);m=model(client)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *x:({'entity_ids':[a['id']],'fields':['temperature'],'purpose':'只返回一个对象'},{}))
    r=client.post('/api/data/queries',json={'question':'核对两对象温度','entity_ids':[a['id'],b['id']],'fields':['temperature'],'mode':'cloud','model_profile_id':m['id']});assert r.status_code==200
    assert process_once();saved=client.get('/api/data/queries').json()[0];assert saved['status']=='blocked_model' and '遗漏' in saved['data']['error']


def test_result_truncation_and_same_instant_conflicts_are_explicit(client):
    pkg=publish(client,manifest());obj=entity(client,pkg);src=publish(client,manifest('source',name='dense_source',execution={'adapter':'upload'}));at=now()-timedelta(days=1)
    for i in range(21):observation(obj,src,at,20+i,'dense_'+str(i))
    r=query(client,obj,{'source':'observations','minimum_verification':'unverified_extraction'})
    result=r['data']['result'];assert r['status']=='insufficient' and len(result['observation_evidence'])==20
    assert any('上限' in g['reason'] for g in result['gaps']) and result['conflicts']


def test_as_of_does_not_present_mutated_current_archive_as_history(client):
    pkg=publish(client,manifest());obj=entity(client,pkg)
    cutoff=now()-timedelta(hours=1)
    with Session(engine()) as db:
        e=db.get(Entity,obj['id']);e.observed_at=cutoff-timedelta(minutes=1);db.commit()
    r=query(client,obj,{'source':'archive','time':{'mode':'as_of','at':cutoff.isoformat()}})
    assert r['status']=='insufficient' and '历史' in r['data']['result']['excluded_evidence'][0]['reason']


def test_goal_existing_only_cannot_be_widened_in_plan_request(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);g=explicit_goal(obj,mode='existing_only');task=create_task(client,pkg,obj,g);m=model(client)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:pytest.fail('应在调用模型前拒绝扩大采集'))
    r=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'不能越过目标范围','model_profile_id':m['id'],'observation_mode':'allow_new'});assert r.status_code==409


def test_relative_query_rejects_mixed_package_timezone_inference(client):
    spec=manifest()['specification'];a=publish(client,manifest(name='zoned',specification={**spec,'timezone':'Asia/Shanghai'}));b=publish(client,manifest(name='unzoned'));ea=entity(client,a);eb=entity(client,b)
    r=client.post('/api/data/queries',json={'question':'昨天的温度','entity_ids':[ea['id'],eb['id']],'fields':['temperature'],'mode':'structured'});assert r.status_code==200
    assert r.json()['status']=='needs_clarification' and r.json()['data']['request']['resolved_time'] is None


def test_conflicting_sources_stop_before_approval_and_any_execution(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);task,plan=planned_task(client,obj,pkg,monkeypatch);src=publish(client,manifest('source',name='conflict_stop',execution={'adapter':'upload'}));at=now()-timedelta(seconds=1)
    observation(obj,src,at,24,'c1');observation(obj,src,at,26,'c2')
    r=client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'冲突不得继续'});assert r.status_code==409 and '冲突' in r.json()['detail']
    assert not saved_task(client,task['id'])['data'].get('step_receipts')


def test_expired_deadline_rejects_approval_without_execution(client,monkeypatch):
    pkg=publish(client,manifest());obj=entity(client,pkg);g=explicit_goal(obj);g['deadline']=(now()+timedelta(minutes=5)).isoformat();task=create_task(client,pkg,obj,g);m=model(client)
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a:({'summary':'合成计划','steps':[{'title':'报告','agent':'business','action':'report'}]},{}))
    r=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':1,'reason':'保存过期目标用于核查','model_profile_id':m['id']});assert r.status_code==200;p=r.json()
    monkeypatch.setattr('generic_agents.goals.now',lambda:instant(g['deadline'])+timedelta(seconds=1))
    r=client.post('/api/plans/'+p['id']+'/approve',json={'expected_version':p['version'],'reason':'截止后不得批准'});assert r.status_code==409 and '截止' in r.json()['detail']

from .test_business_tools import service,setup as tool_setup


def test_tool_threshold_and_retry_budget_are_verified_not_report_claims(client,monkeypatch,service):
    tool,pkg,obj,task,old=tool_setup(client,monkeypatch,service);t=saved_task(client,task['id']);g=explicit_goal(obj,mode='existing_only',source='archive');g['max_tool_calls']=1;g['criteria'].append({'id':'tool_threshold','description':'登记工具均值必须小于20','kind':'tool_value','capability_id':tool['id'],'field':'value','operator':'lt','expected':20});g['subgoals'][0]['criterion_ids'].append('tool_threshold')
    r=client.put('/api/tasks/'+t['id']+'/goal',json={'expected_version':t['version'],'reason':'限定阈值和累计调用预算','goal_spec':g});assert r.status_code==200;t=r.json();m=next(c for c in client.get('/api/capabilities').json() if c['kind']=='foundation_model')
    r=post_plan(client,'/api/tasks/'+t['id']+'/plan',json={'expected_version':t['version'],'reason':'核对目标工具标准','model_profile_id':m['id']});assert r.status_code==200;p=r.json()
    assert client.post('/api/plans/'+p['id']+'/approve',json={'expected_version':p['version'],'reason':'批准一次真实本地只读调用'}).status_code==200
    service['fail']=True;assert process_once();t=saved_task(client,t['id']);assert t['status']=='waiting_tool' and service['calls']==1
    service['fail']=False
    r=client.post('/api/tasks/'+t['id']+'/retry_execution',json={'expected_version':t['version'],'reason':'服务恢复但预算不能暗中扩大'});assert r.status_code==200
    assert process_once();t=saved_task(client,t['id']);assert t['status']=='waiting_tool' and service['calls']==1
    assert '预算' in next(r['result']['reason'] for r in t['data']['step_receipts'].values())


def test_successful_tool_with_unmet_value_does_not_complete_goal(client,monkeypatch,service):
    tool,pkg,obj,task,old=tool_setup(client,monkeypatch,service);t=saved_task(client,task['id']);g=explicit_goal(obj,mode='existing_only',source='archive');g['criteria'].append({'id':'tool_threshold','description':'均值低于20','kind':'tool_value','capability_id':tool['id'],'field':'value','operator':'lt','expected':20});g['subgoals'][0]['criterion_ids'].append('tool_threshold')
    t=client.put('/api/tasks/'+t['id']+'/goal',json={'expected_version':t['version'],'reason':'明确可核验工具标准','goal_spec':g}).json();m=next(c for c in client.get('/api/capabilities').json() if c['kind']=='foundation_model');p=post_plan(client,'/api/tasks/'+t['id']+'/plan',json={'expected_version':t['version'],'reason':'测试工具结果阈值','model_profile_id':m['id']}).json()
    assert client.post('/api/plans/'+p['id']+'/approve',json={'expected_version':p['version'],'reason':'执行本地参考均值'}).status_code==200
    assert process_once();t=saved_task(client,t['id']);assert t['status']=='waiting_evidence' and service['calls']==1
    assert not next(c for c in t['data']['goal_evaluation']['checks'] if c['id']=='tool_threshold')['passed']
