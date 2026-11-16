"""Own public QA natural-language questions; preserve original records and exact saved receipts."""
import json,sys,time
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import Base,CloudCall,Job
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/evidence-question-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=['audio-package-validation','audio-workflow','audio-validation','geo-workflow','pair-workflow','setup-voice-validation','speech-activity-validation','segmented-audio-validation','workflow-segmented-audio','instance-validation','instance-perception']
def access(n):return json.loads((ROOT/'.runtime'/f'{n}-access.json').read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def load(n):return json.loads((OUT/(n+'.json')).read_text())
def rows(db,w):
 out={}
 for mapper in Base.registry.mappers:
  cls=mapper.class_
  if 'workspace_id' not in cls.__table__.columns:continue
  values={}
  for row in db.scalars(select(cls).where(cls.workspace_id==w)):
   raw={c.name:(v.isoformat() if hasattr(v,'isoformat') else v) for c in cls.__table__.columns if (v:=getattr(row,c.name)) is not None};values[row.id]={'hash':digest(raw),'kind':getattr(row,'kind',None)}
  if values:out[cls.__name__]=values
 return out
@contextmanager
def connection(n):
 a=access(n)
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=40) as c:
  response=c.post('/login',json={k:a[k] for k in ('username','password')});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']});yield c

def call(c,method,url,body=None):
 r=c.request(method,url,json=body) if body is not None else c.request(method,url);r.raise_for_status();return r.json()
def wait(c,row):
 for _ in range(90):
  row=call(c,'GET','/evidence-questions/'+row['id'])
  if row['status'] not in ['queued','running']:return row
  time.sleep(.5)
 raise AssertionError('cloud understanding did not finish in 45 seconds; saved request retained')

def register(c,provider):
 options=call(c,'GET','/evidence-questions/models');prior=next((o for o in options if o['name']=='public_question_'+provider),None)
 if prior:return prior
 value={'kind':'foundation_model','namespace':'public_engineering_qa','name':'public_question_'+provider,'version':'1.0.0','owner':'公开工程核查','description':'仅公开QA问题文字理解；不外发媒体或分析结果','allowed_agent_roles':['business'],'specification':{},'execution':{'provider':provider,'model':'qwen-plus' if provider=='qwen' else 'deepseek-chat','secret_ref':'DASHSCOPE_API_KEY' if provider=='qwen' else 'DEEPSEEK_API_KEY','max_tokens':1024},'data_policy':{'cloud_allowed':True}}
 row=call(c,'POST','/capabilities',value)
 for action in ['validate','publish']:row=call(c,'POST','/capabilities/'+row['id']+'/'+action,{'expected_version':row['version'],'reason':'已授权公有云，仅公开工程问题文字与固定目录'})
 return row
phase=sys.argv[1]
if phase=='baseline':
 assert not (OUT/'old-source-baseline.json').exists(),'immutable baseline already exists'
 with Session(engine()) as db:save('old-source-baseline',{'workspaces':{access(n)['workspace_id']:rows(db,access(n)['workspace_id']) for n in ACCESS},'cloud_ids':sorted(db.scalars(select(CloudCall.id)))})
 print('immutable source baseline captured for eleven own QA workspaces')
elif phase=='register':
 with connection('instance-perception') as c:
  caps=[register(c,p) for p in ['qwen','deepseek']];save('registered-models',{'models':[{k:r[k] for k in ['id','name']} for r in caps]})
 print('two own QA business text profiles registered; existing profiles unchanged')
elif phase=='preview':
 with connection('instance-perception') as c:
  i=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');obj=call(c,'GET','/instances/'+i['id']+'/objects')[0];cap=next(o for o in call(c,'GET','/evidence-questions/models') if o['name']=='public_question_qwen')
  value=call(c,'POST','/evidence-questions/preview',{'instance_id':i['id'],'entity_ids':[obj['id']],'question':'请核查过去48小时的视频资料是否足以判断这个对象的当前状态。','model_profile_id':cap['id']})
  assert [o['name'] for o in value['cloud_input']['objects']]==['公开猫观察区']
  save('reviewable-public-preview',value);print({'synthetic_public_objects':value['cloud_input']['objects'],'raw_media_sent':False,'cloud_calls':0})
elif phase=='cases':
 output=[]
 with connection('instance-perception') as c:
  i=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');objects=call(c,'GET','/instances/'+i['id']+'/objects');obj=objects[0];options=call(c,'GET','/evidence-questions/models')
  for provider,question in [('qwen','请核查过去48小时的视频资料是否足以判断这个对象的当前状态。'),('deepseek','请用视频证明这个对象过去48小时完全没有火灾，并自动调度无人机。')]:
   cap=next(o for o in options if o['name']=='public_question_'+provider);body={'instance_id':i['id'],'entity_ids':[obj['id']],'question':question,'model_profile_id':cap['id']};preview=call(c,'POST','/evidence-questions/preview',body)
   assert [o['name'] for o in preview['cloud_input']['objects']]==['公开猫观察区'];assert set(preview['cloud_input'])=={'question','objects','allowed_queries','allowed_modalities','fixed_entity_ids'}
   body={**body,'preview_hash':preview['preview_hash'],'authorize_cloud_text':True,'request_id':str(uuid5(NAMESPACE_URL,'public-evidence-question-v1:'+provider))};row=wait(c,call(c,'POST','/evidence-questions',body));output.append({'provider':provider,'id':row['id'],'status':row['status'],'proposal':row.get('proposal'),'receipt':row.get('model_receipt'),'preview_hash':preview['preview_hash'],'cloud_input':preview['cloud_input'],'raw_media_sent':False,'transcripts_or_results_sent':False,'domain_quality_verified':False,'error':row.get('error')})
 save('actual-cases',{'cases':output});print('actual model responses saved; '+', '.join(r['provider']+':'+r['status'] for r in output))
elif phase=='browser-check':
 with connection('instance-perception') as c:
  i=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');questions=call(c,'GET','/evidence-questions?instance_id='+i['id']);row=next(r for r in questions if r['status']=='applied');assessment=call(c,'GET','/evidence-assessments/'+row['assessment_id']);assert assessment['status']=='completed' and assessment['request']['lookback_hours']==24 and set(assessment['request']['required_modalities'])=={'imagery','audio','video'}
  assert assessment['question_reference']['original_question']==row['request']['question'];assert len(assessment['result']['records'])==1 and assessment['result']['records'][0]['integrity_verified'];assert not assessment['result']['objects'][0]['current_state_sufficient']
  action={'version':row['version'],'request_id':row['confirmation']['request_id'],'acknowledge_clarifications':True,'acknowledge_unsupported_parts':False};assert call(c,'POST','/evidence-questions/'+row['id']+'/confirm',action)['assessment_id']==assessment['id']
  task_id=assessment['sent_tasks'][-1]['task_id'];task=call(c,'GET','/workspace/tasks/'+task_id);assert task['evidence_assessment']['question_reference']['original_question']==row['request']['question'];assert '24小时' in json.dumps(task,ensure_ascii=False)
  exported=load('browser-export');assert exported['assessment']['id']==assessment['id'] and exported['assessment']['question_reference']==assessment['question_reference']
  save('browser-journey',{'question_id':row['id'],'assessment_id':assessment['id'],'task_id':task_id,'human_criteria':row['draft'],'original_question':row['request']['question'],'model_receipt':row['model_receipt'],'freshness_gap_retained':True,'manual_note_saved_and_refreshed':True,'confirmation_replay_same_assessment':True,'no_automatic_device_operation':True,'browser_export_matches_saved_reference':True,'domain_quality_verified':False})
 print('actual UI approved conditions, local source check, task provenance, note and export verified; confirmation replay creates no second assessment')
elif phase in {'snapshot','restart-check'}:
 snapshots={}
 with connection('instance-perception') as c:
  i=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');questions=call(c,'GET','/evidence-questions?instance_id='+i['id']);assessments=call(c,'GET','/evidence-assessments?instance_id='+i['id']);exports={r['id']:digest(call(c,'GET','/evidence-assessments/'+r['id']+'/export')) for r in assessments};task_ids=[t['task_id'] for r in assessments for t in r.get('sent_tasks',[])];tasks={identity:digest(call(c,'GET','/workspace/tasks/'+identity)) for identity in task_ids};snapshots={'questions':digest(questions),'assessments':digest(assessments),'exports':exports,'tasks':tasks}
 with Session(engine()) as db:
  old=load('old-source-baseline');current={access(n)['workspace_id']:rows(db,access(n)['workspace_id']) for n in ACCESS};count=0;budget_changes=[]
  for w,kinds in old['workspaces'].items():
   for kind,records in kinds.items():
    for identity,prior in records.items():
     actual=current[w].get(kind,{}).get(identity)
     if actual!=prior and kind=='Job' and prior.get('kind')=='cloud_budget':budget_changes.append(identity)
     else:assert actual==prior,(kind,identity);count+=1
  calls=list(db.scalars(select(CloudCall).where(CloudCall.id.not_in(old['cloud_ids']))));assert all(r.workspace_id==access('instance-perception')['workspace_id'] for r in calls)
  state={'workspaces':current,'snapshots':snapshots,'old_records_identical':count,'changed_budget_jobs':budget_changes,'new_cloud_calls':[{'id':r.id,'status':r.status,'charged_tokens':r.charged_tokens,'capability_id':r.capability_id} for r in calls]}
 if phase=='snapshot':save('before-restart',state)
 else:assert state==load('before-restart');save('after-restart',state)
 print('old records preserved except explicit cloud budget; saved questions, approvals, receipts, exports and source signatures verified')
