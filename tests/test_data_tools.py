"""Registry pipelines over persisted authorized data, isolated PostgreSQL, no paid provider calls."""
import copy,json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import DataRun,Job,Invocation,Entity,Membership
from generic_agents.worker import process_once
from .test_vertical_slice import publish,manifest

def post(c,path,value):
 r=c.post(path,json=value);assert r.status_code in {200,201},r.text;return r.json()

def setup(c,*,remote=False,monkeypatch=None):
 tools=[]
 for name in ['data_keyword','data_aggregate']:
  value=json.loads(Path('examples/'+name+'.json').read_text());value['namespace']='query_validation'
  if remote and name=='data_aggregate':
   from generic_agents.config import settings
   monkeypatch.setattr(settings(),'development_connector_urls','http://127.0.0.1:9044/read')
   value['execution']={**value['execution'],'adapter':'http','endpoint':'http://127.0.0.1:9044/read'}
   value['data_policy']['external_allowed']=True
  tools.append(publish(c,value))
 package=publish(c,manifest(namespace='query_validation',name='query_package',
  dependencies=[t['id'] for t in tools],specification={**manifest()['specification'],'data_tool_ids':[t['id'] for t in tools]}))
 objects=[]
 for n,(name,temp) in enumerate([('热设备甲',10),('热设备乙',30),('冷设备',99)]):
  objects.append(post(c,'/api/entities',{'package_id':package['id'],'entity_type':'site','external_id':'query-'+str(n),
   'attributes':{'name':name,'temperature':temp}}))
 steps=[{'id':'select_hot','capability_id':tools[0]['id'],'input_from':'evidence','parameters':{'field':'name','text':'热'}},
  {'id':'average','capability_id':tools[1]['id'],'input_from':'select_hot','parameters':{'field':'temperature','operation':'mean'}}]
 return package,objects,tools,steps

def request(objects,steps,**extra):
 return {'question':'筛选名称含热的授权证据记录，计算温度平均值','entity_ids':[o['id'] for o in objects],
  'fields':['name','temperature'],'mode':'structured','tool_steps':steps,'evidence_requirement':{'source':'archive'},**extra}

def finish(c,run):
 jobs=c.get('/api/overview').json()['jobs'];j=next(j for j in jobs if j['data'].get('query_id')==run['id'])
 assert process_once(j['id']);return c.get('/api/data/queries/'+run['id']).json()

def test_registered_two_step_pipeline_has_actual_bound_sources_and_mean20(client):
 pkg,objects,tools,steps=setup(client);run=post(client,'/api/data/queries',request(objects,steps));assert run['status']=='queued'
 run=finish(client,run);assert run['status']=='sufficient',run['data'].get('result')
 result=run['data']['result'];row=result['derived_records'][0]
 assert row['values']['value']==20 and row['values']['sample_count']==2
 assert set(row['entity_ids'])=={o['id'] for o in objects[:2]} and len(row['evidence_refs'])==2
 assert len(result['tool_trace'][0]['inputs']['records'])==3 and len(result['tool_trace'][1]['inputs']['records'])==2
 assert result['derivation_verification']=='deterministic_registered_computation'
 assert [e['data']['temperature'] for e in client.get('/api/entities').json()[::-1]]==[10,30,99]

def test_budget_dependency_cycle_scope_binding_and_sql_rejected_before_execution(client):
 pkg,objects,tools,steps=setup(client)
 for modify in [lambda x:x.update(max_tool_calls=1),
  lambda x:x['tool_steps'][0].update(input_from='average'),
  lambda x:x['tool_steps'][0]['parameters'].update(records=[]),
  lambda x:x['tool_steps'][0]['parameters'].update(sql='SELECT outside'),
  lambda x:x['tool_steps'][0]['parameters'].update(field='secret')]:
  value=copy.deepcopy(request(objects,steps));modify(value)
  assert client.post('/api/data/queries',json=value).status_code in {422,403}
 assert not client.get('/api/overview').json()['jobs']

def test_query_registration_rejects_write_and_missing_dependency(client):
 bad=json.loads(Path('examples/data_aggregate.json').read_text());bad['execution']['side_effect']=True
 cap=post(client,'/api/capabilities',bad)
 assert client.post('/api/capabilities/'+cap['id']+'/validate',json={'expected_version':cap['version'],'reason':'必须拒绝外部写查询'}).status_code==422
 good=publish(client,{**json.loads(Path('examples/data_keyword.json').read_text()),'name':'good_keyword'})
 pkg=post(client,'/api/capabilities',manifest(specification={**manifest()['specification'],'data_tool_ids':[good['id']]}))
 assert client.post('/api/capabilities/'+pkg['id']+'/validate',json={'expected_version':pkg['version'],'reason':'必须锁定依赖'}).status_code==422

def test_foreign_package_tool_not_admitted(client):
 pkg,objects,tools,steps=setup(client)
 other=publish(client,manifest(namespace='other_domain',name='other'))
 obj=post(client,'/api/entities',{'package_id':other['id'],'entity_type':'site','external_id':'outside','attributes':{'name':'热范围外设备','temperature':999}})
 r=client.post('/api/data/queries',json=request(objects+[obj],steps));assert r.status_code==403
 assert not client.get('/api/overview').json()['jobs']

def test_success_checkpoint_reuses_receipts_without_new_invocations(client):
 pkg,objects,tools,steps=setup(client);run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 with Session(engine()) as db:
  assert len(db.scalars(select(Invocation)).all())==2
  row=db.get(DataRun,run['id']);row.status='queued'
  job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==run['id']));job.status='queued';db.commit();identifier=job.id
 assert process_once(identifier)
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==2
 assert client.get('/api/data/queries/'+run['id']).json()['data']['result']['derived_records'][0]['values']['value']==20

def test_tampered_deterministic_receipt_is_not_reused_as_fact(client):
 pkg,objects,tools,steps=setup(client);run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 with Session(engine()) as db:
  call=db.scalar(select(Invocation).where(Invocation.capability_id==tools[1]['id']));output=copy.deepcopy(call.data['output']);output['records'][0]['values']['value']=999
  call.data={**call.data,'output':output};row=db.get(DataRun,run['id']);row.status='queued'
  job=db.scalar(select(Job).where(Job.data['query_id'].as_string()==run['id']));job.status='queued';db.commit();identifier=job.id
 assert process_once(identifier);value=client.get('/api/data/queries/'+run['id']).json()
 assert value['status']=='blocked_evidence' and '原输入不一致' in value['data']['result']['tool_blocker']

def test_source_changes_during_call_prevent_derivation_and_next_step(client,monkeypatch):
 pkg,objects,tools,steps=setup(client)
 import generic_agents.gateway as gateway
 original=gateway.invoke
 def change(*args,**kwargs):
  call=original(*args,**kwargs)
  r=client.patch('/api/entities/'+objects[0]['id'],json={'expected_version':1,'attributes':{'name':'热设备甲','temperature':50},'reason':'模拟处理期间权威事实变化'})
  assert r.status_code==200;return call
 monkeypatch.setattr(gateway,'invoke',change)
 run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 assert run['status']=='blocked_evidence' and '期间原证据' in run['data']['result']['tool_blocker']
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1

def test_cancel_during_call_saves_receipt_and_stops_following_step(client,monkeypatch):
 pkg,objects,tools,steps=setup(client);run=post(client,'/api/data/queries',request(objects,steps))
 import generic_agents.gateway as gateway
 original=gateway.invoke
 def cancel(*args,**kwargs):
  call=original(*args,**kwargs);current=client.get('/api/data/queries/'+run['id']).json()
  post(client,'/api/data/queries/'+run['id']+'/cancel',{'expected_version':current['version'],'reason':'停止此合成工具查询，保留原回执'})
  return call
 monkeypatch.setattr(gateway,'invoke',cancel);run=finish(client,run);assert run['status']=='cancelled'
 assert len(run['data']['tool_trace'])==1
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1

def test_missing_and_non_numeric_data_cannot_be_silently_skipped(client):
 pkg,objects,tools,steps=setup(client)
 with Session(engine()) as db:
  obj=db.get(Entity,objects[0]['id']);obj.data={**obj.data,'temperature':'坏值'};db.commit()
 run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 assert run['status']=='failed' and '不能静默跳过' in run['data']['result']['tool_blocker']

def test_no_keyword_match_does_not_run_aggregate_or_invent_zero(client):
 pkg,objects,tools,steps=setup(client);steps[0]['parameters']['text']='不存在的关键字'
 run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 assert run['status']=='insufficient' and '前序检索' in run['data']['result']['tool_blocker']
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1

def test_late_last_step_receipt_is_saved_without_budget_overrun_answer(client,monkeypatch):
 from datetime import timedelta
 import generic_agents.data_tools as pipeline
 import generic_agents.gateway as gateway
 pkg,objects,tools,steps=setup(client);clock=[pipeline.now()]
 monkeypatch.setattr(pipeline,'now',lambda:clock[0]);original=gateway.invoke
 def delayed(*args,**kwargs):
  call=original(*args,**kwargs);clock[0]+=timedelta(seconds=121);return call
 monkeypatch.setattr(gateway,'invoke',delayed)
 run=finish(client,post(client,'/api/data/queries',request(objects,[steps[1]|{'input_from':'evidence'}])))
 assert run['status']=='failed' and '响应到达' in run['data']['result']['tool_blocker']
 assert run['data']['tool_trace'][0]['status']=='succeeded' and not run['data']['result'].get('derived_records')

def test_external_explicit_permission_and_false_provenance_guard(client,monkeypatch):
 pkg,objects,tools,steps=setup(client,remote=True,monkeypatch=monkeypatch)
 calls=[]
 def fake(execution,inputs,key):
  calls.append(key)
  return {'records':[{'values':{'value':999,'sample_count':2,'field':'temperature','operation':'mean'},'entity_ids':[objects[0]['id']],'evidence_refs':['entity:outside:1']}],'summary':{'confidence':1}}
 monkeypatch.setattr('generic_agents.connectors.call_http',fake)
 run=finish(client,post(client,'/api/data/queries',request(objects,steps)))
 assert run['status']=='blocked_evidence' and not calls
 run=finish(client,post(client,'/api/data/queries',request(objects,steps,allow_external_tools=True)))
 assert run['status']=='blocked_evidence' and len(calls)==1 and '无效的证据来源' in run['data']['result']['tool_blocker']

def test_cloud_proposal_draft_confirmation_queue_keeps_original_model_receipt(client,monkeypatch):
 pkg,objects,tools,steps=setup(client)
 model=publish(client,manifest('foundation_model',name='query_model',execution={'provider':'qwen','model':'test','secret_ref':'DASHSCOPE_API_KEY'},data_policy={'cloud_allowed':True}))
 monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *a,**k:({'entity_ids':[o['id'] for o in objects],'fields':['name','temperature'],
  'purpose':'登记过滤后计算','query_intent':'tools','tool_steps':steps},{'provider':'test','usage':{'total_tokens':1}}))
 value=request(objects,[],mode='cloud',model_profile_id=model['id']);source=finish(client,post(client,'/api/data/queries',value))
 assert source['status']=='needs_confirmation' and not client.get('/api/overview').json()['acquisitions']
 draft={'question':value['question'],'fields':value['fields'],'tool_steps':steps,'evidence_requirement':{'source':'archive'}}
 saved=client.put('/api/data/queries/'+source['id']+'/draft',json={'expected_version':source['version'],'reason':'保存合成查询方案','draft':draft})
 assert saved.status_code==200;saved=saved.json()
 confirmed=post(client,'/api/data/queries/'+source['id']+'/confirm',{'expected_version':saved['version'],'reason':'核对两个登记步骤及原范围'})
 assert confirmed['query']['status']=='queued';child=finish(client,confirmed['query']);assert child['status']=='sufficient'
 original=client.get('/api/data/queries/'+source['id']).json()
 assert original['data']['need_checkpoint']==source['data']['need_checkpoint'] and original['data']['resolved_query_id']==child['id']

def external_result(client,monkeypatch):
 pkg,objects,tools,steps=setup(client,remote=True,monkeypatch=monkeypatch)
 def fake(execution,inputs,key):
  from generic_agents.data_tools import builtin
  result=builtin('data_record_aggregate',inputs);result['summary']['confidence']=1
  return result
 monkeypatch.setattr('generic_agents.connectors.call_http',fake)
 return objects,steps,finish(client,post(client,'/api/data/queries',request(objects,steps,allow_external_tools=True)))

def test_external_self_reported_confidence_needs_durable_human_review(client,monkeypatch):
 objects,steps,run=external_result(client,monkeypatch)
 assert run['status']=='needs_review' and run['data']['result']['derivation_verification']=='unverified_registered_extension'
 value=post(client,'/api/data/queries/'+run['id']+'/review-derived',{'expected_version':run['version'],'decision':'confirmed','reason':'逐条核对两条授权证据10和30，算术结果20'})
 saved=client.get('/api/data/queries/'+run['id']).json()
 assert saved==value and saved['status']=='sufficient'
 assert saved['data']['derived_review']['reason'].endswith('20') and len(saved['data']['derived_review_history'])==1
 assert saved['data']['result']['tool_trace']==run['data']['result']['tool_trace']
 assert client.post('/api/data/queries/'+run['id']+'/review-derived',json={'expected_version':value['version'],'decision':'confirmed','reason':'不重复确认'}).status_code==409

def test_changed_source_blocks_external_confirmation_but_rejection_preserves_receipt(client,monkeypatch):
 objects,steps,run=external_result(client,monkeypatch)
 post_value={'expected_version':1,'attributes':{'name':'热设备甲','temperature':50},'reason':'权威事实已变化'}
 assert client.patch('/api/entities/'+objects[0]['id'],json=post_value).status_code==200
 body={'expected_version':run['version'],'decision':'confirmed','reason':'不能确认旧来源'}
 assert client.post('/api/data/queries/'+run['id']+'/review-derived',json=body).status_code==409
 body.update(decision='rejected',reason='旧来源已失效，拒绝旧推导并保留依据')
 saved=post(client,'/api/data/queries/'+run['id']+'/review-derived',body)
 assert saved['status']=='rejected' and saved['data']['result']['derived_records']==run['data']['result']['derived_records']
 assert saved['data']['result']['derivation_verification']=='human_rejected_extension'

def test_failed_tool_query_can_be_corrected_into_new_run_without_overwriting_failure(client):
 pkg,objects,tools,steps=setup(client)
 with Session(engine()) as db:
  obj=db.get(Entity,objects[0]['id']);obj.data={**obj.data,'temperature':'坏值'};db.commit()
 run=finish(client,post(client,'/api/data/queries',request(objects,steps)));assert run['status']=='failed'
 assert client.patch('/api/entities/'+objects[0]['id'],json={'expected_version':1,'attributes':{'name':'热设备甲','temperature':50},'reason':'修正错误原始数据'}).status_code==200
 saved=client.put('/api/data/queries/'+run['id']+'/draft',json={'expected_version':run['version'],'reason':'来源修复后明确新建查询并重新计算',
  'draft':{'question':run['question'],'fields':['name','temperature'],'tool_steps':steps,'evidence_requirement':{'source':'archive'}}})
 assert saved.status_code==200;saved=saved.json()
 created=post(client,'/api/data/queries/'+run['id']+'/confirm',{'expected_version':saved['version'],'reason':'批准新的调用预算，保留旧失败记录'})
 child=finish(client,created['query']);assert child['data']['result']['derived_records'][0]['values']['value']==40
 original=client.get('/api/data/queries/'+run['id']).json();assert original['status']=='failed'
 assert original['data']['result']==run['data']['result'] and original['data']['resolved_query_id']==child['id']
 assert client.post('/api/data/queries/'+run['id']+'/confirm',json={'expected_version':original['version'],'reason':'不能重复创建'}).status_code==409

def test_successful_tool_query_requery_keeps_original_computation_and_budget(client):
 pkg,objects,tools,steps=setup(client)
 run=finish(client,post(client,'/api/data/queries',request(objects,steps,max_tool_calls=2)))
 steps[0]['parameters']['text']='冷'
 saved=client.put('/api/data/queries/'+run['id']+'/draft',json={'expected_version':run['version'],'reason':'改查冷样本，显式新建查询',
  'draft':{'question':run['question'],'fields':['name','temperature'],'tool_steps':steps,'max_tool_calls':2,'evidence_requirement':{'source':'archive'}}})
 assert saved.status_code==200;saved=saved.json()
 created=post(client,'/api/data/queries/'+run['id']+'/confirm',{'expected_version':saved['version'],'reason':'核对冷样本新参数和两次调用预算'})
 child=finish(client,created['query']);assert child['data']['result']['derived_records'][0]['values']['value']==99
 assert child['data']['request']['max_tool_calls']==2 and len(child['data']['tool_trace'])==2
 original=client.get('/api/data/queries/'+run['id']).json();assert original['status']=='sufficient' and original['data']['result']==run['data']['result']
