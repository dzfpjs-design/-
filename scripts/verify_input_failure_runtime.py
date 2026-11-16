"""One normal public malformed registration; never app DB fault injection."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/input-failure-v06';OUT.mkdir(parents=True,exist_ok=True)
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def prepare(a,c):
 if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，禁止重复准备或新增登记')
 p=json.loads((ROOT/'docs/evidence/automatic-state-v06/prepared.json').read_text())
 before=state(a['workspace_id']);save('before',before)
 with Session(engine()) as db:
  old=db.get(m.Observation,'d4a492d0-9211-4df2-87d2-0b52ae73979c');assert old.workspace_id==a['workspace_id'] and old.status=='processed' and old.data['extracted']['attributes']=={'公开登记值':38.5}
  original=old.data['original_request'];source_id=old.source_id;extractor_id=original['extractor_id']
  future=[j.id for j in db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.kind=='refresh_object_state',m.Job.status=='queued')) if j.data['state_request'].get('due_state_hash') and j.data['state_request']['entity_id']==p['entity_id']]
 clock=datetime.now(timezone.utc)-timedelta(seconds=2)
 body={'source_id':source_id,'extractor_id':extractor_id,'source_record_id':'public-null-input-'+str(uuid4()),'entity_id':p['entity_id'],'attributes':{'公开登记值':None},'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual'}
 save('prepared',{'instance_id':p['instance_id'],'entity_id':p['entity_id'],'body':body,'previous_expiry_jobs':future,'new_accounts':0,'new_permissions':0,'device_worker':'off'})
 r=c.post('/workspace/instances/'+p['instance_id']+'/reported-observations',json=body);r.raise_for_status();save('submitted',r.json())
 print('One normal public null-input registration saved; no cloud/device action')
def verify(a,c,phase):
 p=read('prepared');obs=read('submitted');before=read('before');after=state(a['workspace_id'])
 assert before['other']==after['other'] and before['own_originals']==after['own_originals'] and before['other_originals']==after['other_originals']
 with Session(engine()) as db:
  row=db.get(m.Observation,obs['id']);assert row.status=='failed' and row.entity_id==p['entity_id']
  assert digest(row.data['original_request'])==row.data['input_hash'] and all(row.data['original_request'][k]==v for k,v in p['body'].items())
  job=db.scalar(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.idempotency_key=='extract:'+row.id));assert job.status=='failed'
  expected=1 if phase=='first' else 2;assert job.attempts==expected
  events=list(db.scalars(select(m.Event).where(m.Event.workspace_id==a['workspace_id'],m.Event.kind=='observation.failed',m.Event.data['observation_id'].as_string()==row.id).order_by(m.Event.created_at)))
  assert [e.data['attempt'] for e in events]==list(range(1,expected+1)) and all(e.status=='received' for e in events)
  for e in events:
   call=db.get(m.Invocation,e.data['invocation_id']);cap=db.get(m.Capability,call.capability_id)
   assert call.status=='blocked_evidence' and '不是有效数值' in call.data.get('error','') and cap.data['execution']['handler']=='numeric_rules' and cap.data['data_policy']['cloud_allowed'] is False
  saved=db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id==a['workspace_id'],m.ObjectState.entity_id==p['entity_id']))
  assert saved.data['status']=='insufficient' and saved.data['fields']['公开登记值']['value']==38.5 and any(i['observation_id']==row.id and i['stage']=='failed' for i in saved.data['input_issues'])
  permitted={'ObjectState:'+saved.id}|{'Job:'+i for i in p['previous_expiry_jobs']}
  changed={k for k,h in before['own'].items() if after['own'].get(k)!=h};assert changed<=permitted,changed
  assert not [k for k in after['own'] if k.startswith(('CloudCall:','Task:','Entity:')) and k not in before['own']]
  fresh_obs=[k for k in after['own'] if k.startswith('Observation:') and k not in before['own']];assert fresh_obs==['Observation:'+row.id]
  jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'])))
  assert all(j.kind in {'process_reported_observation','refresh_object_state'} for j in jobs if j.kind not in {'login_limit','cloud_budget'} and 'Job:'+j.id not in before['own'])
  proof={'passed':True,'observation_id':row.id,'attempts':expected,'old_value_retained':38.5,'state_hash':saved.data['state_hash'],'state_version':saved.version,'new_cloud_calls':0,'device_actions':0,'new_specialist_inferences':0,'entity_updated':False,'original_input_unchanged':True,'own_records':len(after['own']),'other_records_unchanged':len(after['other']),'old_own_records_unchanged':len(before['own'])-len(changed),'changed_old_records':sorted(changed),'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),'physical_state_verified':False,'production_ready':False}
 r=c.get('/workspace/instances/'+p['instance_id']+'/objects/'+p['entity_id']+'/state');r.raise_for_status();view=r.json();assert view['state']['state_hash']==proof['state_hash'];save(phase+'-view',view)
 r=c.get('/workspace/instances/'+p['instance_id']+'/objects/'+p['entity_id']+'/state-inputs/'+row.id,params={'expected_state_hash':proof['state_hash']});r.raise_for_status();detail=r.json();assert detail['original_attributes']=={'公开登记值':None} and len(detail['failure_history'])==expected and detail['retry_receipts_verified'];save(phase+'-input',detail)
 if phase=='before-restart':save('before-restart',after)
 if phase=='after-restart':assert after==read('before-restart')
 save(phase+'-verification',proof);print(json.dumps(proof,ensure_ascii=False))
def main():
 a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
  r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
  if sys.argv[1]=='prepare':prepare(a,c)
  else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
