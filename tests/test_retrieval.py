"""Real API/PG pipeline; cloud transport is an explicit owned test substitute."""
import copy,json
from pathlib import Path
import httpx,pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import VectorCache,Invocation,CloudCall,DataRun,Capability
from .test_vertical_slice import publish,manifest
from .test_data_tools import post,finish

def setup(c):
 model=publish(c,json.loads(Path('examples/qwen_embedding.json').read_text()))
 lex=publish(c,json.loads(Path('examples/data_fulltext.json').read_text()))
 m=json.loads(Path('examples/data_semantic.json').read_text());m['execution']['model_profile_id']=model['id'];m['dependencies']=[model['id']]
 sem=publish(c,m);aggregate=publish(c,json.loads(Path('examples/data_aggregate.json').read_text()))
 pkg=publish(c,manifest(namespace='retrieval_validation',name='search_package',dependencies=[lex['id'],sem['id'],aggregate['id']],
  specification={**manifest()['specification'],'data_tool_ids':[lex['id'],sem['id'],aggregate['id']]}))
 objects=[]
 for i,(name,t) in enumerate([('灌溉管道关闭造成根部供水不足',10),('持续灌溉保持土壤湿润',30),('仓库照明电路检修',99)]):
  objects.append(post(c,'/api/entities',{'package_id':pkg['id'],'entity_type':'site','external_id':'r'+str(i),'attributes':{'name':name,'temperature':t}}))
 return model,lex,sem,aggregate,pkg,objects

def req(objects,cap,*,semantic=False,text='灌溉',**kw):
 params={'field':'name','text':text,'top_k':10,'min_score':.3 if semantic else 0}
 if not semantic:params['match_mode']='all_terms'
 return {'question':'在原范围内检索证据；不证明设备事实','entity_ids':[o['id'] for o in objects],'fields':['name','temperature'],
  'mode':'structured','allow_external_tools':semantic,'tool_steps':[{'id':'search','capability_id':cap['id'],'input_from':'evidence','parameters':params}],**kw}

def fake_cloud(monkeypatch,*,malform=None,hook=None):
 import generic_agents.retrieval as r
 from generic_agents.config import settings
 monkeypatch.setenv('DASHSCOPE_API_KEY','isolated-test-transport-key');monkeypatch.setattr(settings(),'cloud_daily_token_limit',500000)
 # Only this explicitly mocked transport receives finite synthetic capacity.
 # The owned runner keeps real providers at zero; reserve/finish stay genuine.
 monkeypatch.setattr(settings(),'cloud_daily_call_limit',20)
 original=httpx.Client;calls=[]
 def response(request):
  body=json.loads(request.content);calls.append(body)
  assert request.url.path=='/compatible-mode/v1/embeddings' and body['encoding_format']=='float'
  rows=[]
  for i,text in enumerate(body['input']):
   axis=0 if '干旱' in text or '供水不足' in text else 1
   v=[0.0]*body['dimensions'];v[axis]=1.0
   if malform=='dimension':v=v[:-1]
   if malform=='nan':v[0]=float('nan')
   if malform=='zero':v=[0.0]*body['dimensions']
   rows.append({'index':0 if malform=='duplicate' else i,'embedding':v})
  if hook:hook(len(calls))
  if malform=='http':return httpx.Response(503,json={'error':'owned failure'})
  if malform=='nan':return httpx.Response(200,content=json.dumps({'data':rows,'usage':{'total_tokens':30}}).encode())
  return httpx.Response(200,json={'data':rows,'usage':{'total_tokens':30}})
 monkeypatch.setattr(r.httpx,'Client',lambda *a,**kw:original(*a,**{**kw,'transport':httpx.MockTransport(response)}))
 return calls

def test_fulltext_ranking_then_aggregate_keeps_selected_source_subset(client):
 m,lex,sem,agg,pkg,objects=setup(client);request=req(objects,lex)
 request['tool_steps'].append({'id':'mean','capability_id':agg['id'],'input_from':'search','parameters':{'field':'temperature','operation':'mean'}})
 run=finish(client,post(client,'/api/data/queries',request));assert run['status']=='sufficient',run
 assert run['data']['result']['derived_records'][0]['values']['value']==20
 summary=run['data']['result']['tool_trace'][0]['output']['summary'];assert summary['scanned_records']==3 and summary['returned_records']==2
 assert summary['ranked_subset']
 assert len(run['data']['result']['derived_records'][0]['evidence_refs'])==2

def test_fulltext_no_result_stops_aggregate_no_fake_zero(client):
 m,lex,sem,agg,pkg,objects=setup(client);request=req(objects,lex,text='无匹配字符𐐀')
 request['tool_steps'].append({'id':'mean','capability_id':agg['id'],'input_from':'search','parameters':{'field':'temperature','operation':'mean'}})
 run=finish(client,post(client,'/api/data/queries',request));assert run['status']=='insufficient'
 with Session(engine()) as db:assert len(db.scalars(select(Invocation)).all())==1

def test_semantic_synonym_rank_copies_original_values_and_persists_vectors(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch)
 run=finish(client,post(client,'/api/data/queries',req(objects,sem,semantic=True,text='干旱胁迫')))
 assert run['status']=='sufficient',run['data'];result=run['data']['result']
 assert result['derived_records'][0]['values']==objects[0]['data'] and result['derived_records'][0]['entity_ids']==[objects[0]['id']]
 assert result['derivation_verification']=='source_preserving_semantic_ranking' and len(calls)==1
 with Session(engine()) as db:
  rows=db.scalars(select(VectorCache)).all();assert len(rows)==4
  assert all('text' not in r.data and r.data['dimension']==64 for r in rows)
  assert db.scalar(select(CloudCall)).status=='succeeded'

def test_same_query_reuses_source_model_index_without_cloud_calls(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch)
 value=req(objects,sem,semantic=True,text='干旱胁迫')
 first=finish(client,post(client,'/api/data/queries',value));second=finish(client,post(client,'/api/data/queries',value))
 assert first['status']==second['status']=='sufficient' and len(calls)==1
 assert second['data']['result']['tool_trace'][0]['output']['summary']['cached_vectors']==4

def test_field_update_invalidates_vectors_new_query_rebuilds_current_only(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch);value=req(objects,sem,semantic=True,text='干旱胁迫')
 first=finish(client,post(client,'/api/data/queries',value))
 r=client.patch('/api/entities/'+objects[0]['id'],json={'expected_version':1,'attributes':{'name':'仓库照明系统已修复','temperature':10},'reason':'权威记录更新'});assert r.status_code==200
 with Session(engine()) as db:
  old=db.scalars(select(VectorCache).where(VectorCache.status=='invalidated')).all();assert len(old)==2 and all('vector' not in r.data for r in old)
 second=finish(client,post(client,'/api/data/queries',value));assert second['status']=='insufficient' and len(calls)==2
 assert client.get('/api/data/queries/'+first['id']).json()['data']['result']['derived_records'][0]['values']['name']==objects[0]['data']['name']

def test_missing_external_permission_no_transport_call(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch)
 run=finish(client,post(client,'/api/data/queries',req(objects,sem,semantic=True,allow_external_tools=False)))
 assert run['status']=='blocked_evidence' and not calls

@pytest.mark.parametrize('malform',['dimension','nan','zero','duplicate','http'])
def test_malformed_vector_or_cloud_failure_never_silently_falls_back(client,monkeypatch,malform):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch,malform=malform)
 run=finish(client,post(client,'/api/data/queries',req(objects,sem,semantic=True)))
 assert run['status']=='failed' and len(calls)==1
 with Session(engine()) as db:assert not db.scalars(select(VectorCache)).all()

def test_vector_cache_tamper_even_with_new_hash_rejected_against_saved_cloud_ledger(client,monkeypatch):
 from generic_agents.contracts import digest
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch);value=req(objects,sem,semantic=True)
 finish(client,post(client,'/api/data/queries',value))
 with Session(engine()) as db:
  row=db.scalar(select(VectorCache));v=[0.0]*64;v[3]=1;row.data={**row.data,'vector':v,'vector_hash':digest(v)};db.commit()
 run=finish(client,post(client,'/api/data/queries',value));assert run['status']=='failed' and len(calls)==1

def test_cancel_during_response_keeps_budget_receipt_but_no_vectors_or_results(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);run=post(client,'/api/data/queries',req(objects,sem,semantic=True))
 def cancel(_):
  with Session(engine()) as db:
   row=db.get(DataRun,run['id']);row.status='cancelled';row.version+=1;db.commit()
 calls=fake_cloud(monkeypatch,hook=cancel);result=finish(client,run);assert result['status']=='cancelled' and len(calls)==1
 with Session(engine()) as db:assert not db.scalars(select(VectorCache)).all();assert db.scalar(select(CloudCall)).status=='succeeded'

def test_model_change_does_not_mix_dimensions_or_reuse_old_vectors(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch);value=req(objects,sem,semantic=True)
 finish(client,post(client,'/api/data/queries',value))
 with Session(engine()) as db:
  row=db.get(Capability,m['id']);row.status='draft';db.commit()
 run=finish(client,post(client,'/api/data/queries',value));assert run['status']=='failed' and len(calls)==1

def test_source_changes_during_cloud_response_discard_vectors_before_saving(client,monkeypatch):
 m,lex,sem,agg,pkg,objects=setup(client)
 def change(_):
  r=client.patch('/api/entities/'+objects[0]['id'],json={'expected_version':1,'attributes':{'name':'权威来源已纠正','temperature':10},'reason':'模拟云响应期间变化'})
  assert r.status_code==200
 calls=fake_cloud(monkeypatch,hook=change)
 run=finish(client,post(client,'/api/data/queries',req(objects,sem,semantic=True)))
 assert run['status']=='failed' and len(calls)==1
 with Session(engine()) as db:assert not db.scalars(select(VectorCache)).all();assert db.scalar(select(CloudCall)).status=='succeeded'

def test_unapproved_model_dependency_and_unbounded_retrieval_manifest_rejected(client):
 m,lex,sem,agg,pkg,objects=setup(client)
 value=json.loads(Path('examples/data_semantic.json').read_text());value['name']='unsafe_search';value['execution']['model_profile_id']=m['id'];value['dependencies']=[]
 cap=post(client,'/api/capabilities',value)
 assert client.post('/api/capabilities/'+cap['id']+'/validate',json={'expected_version':cap['version'],'reason':'不能漏依赖'}).status_code==422
 value=json.loads(Path('examples/data_fulltext.json').read_text());value['name']='unbounded_search';value['input_schema']['properties']['top_k']['maximum']=999
 cap=post(client,'/api/capabilities',value)
 assert client.post('/api/capabilities/'+cap['id']+'/validate',json={'expected_version':cap['version'],'reason':'不能扩大上限'}).status_code==422
 value=json.loads(Path('examples/data_fulltext.json').read_text());value['name']='unknown_match_mode';value['input_schema']['properties']['match_mode']['enum']=['unsupported']
 cap=post(client,'/api/capabilities',value)
 assert client.post('/api/capabilities/'+cap['id']+'/validate',json={'expected_version':cap['version'],'reason':'不支持的词项要求不能静默按任意匹配'}).status_code==422

def test_business_goal_uses_semantic_subset_and_rejects_invalidated_index(client,monkeypatch):
 from .test_goal_queries import goal,run_data,resume
 from .test_goal_time import model
 from generic_agents.worker import process_once
 m,lex,sem,agg,pkg,objects=setup(client);calls=fake_cloud(monkeypatch)
 steps=req(objects,sem,semantic=True,text='干旱胁迫')['tool_steps']
 steps.append({'id':'mean','capability_id':agg['id'],'input_from':'search','parameters':{'field':'temperature','operation':'mean'}})
 spec=goal(objects,steps,expected=10,external=True);spec['purpose']='在原范围按干旱胁迫语义排序，计算返回证据均值并保留来源'
 task=post(client,'/api/tasks',{'title':'语义检索子集目标','package_id':pkg['id'],'entity_ids':[o['id'] for o in objects],'goal_spec':spec})
 planner=model(client)
 def fake(cap,messages,schema,p):
  assert json.loads(messages[0]['content'])['evidence']['derived_records'][0]['values']['value']==10
  return {'summary':'明确规划替身','steps':[{'title':'证据','agent':'data','action':'query_evidence'},{'title':'报告','agent':'business','action':'report'}]}, {'provider':'test','usage':{'total_tokens':1}}
 monkeypatch.setattr('generic_agents.agents.cloud_json',fake)
 plan=post(client,'/api/tasks/'+task['id']+'/plan',{'expected_version':task['version'],'reason':'语义排序与有来源的子集计算','model_profile_id':planner['id']})
 assert process_once(plan['data']['planning_job_id']);run_data(client,task);plan=resume(client,plan)
 assert plan['status']=='review' and len(calls)==1
 with Session(engine()) as db:
  row=db.scalar(select(VectorCache));row.status='invalidated';db.commit()
 result=client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'索引撤回后不得批准旧查询'})
 assert result.status_code==409
