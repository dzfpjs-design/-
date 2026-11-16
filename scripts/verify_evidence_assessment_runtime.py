"""Readonly original-source baselines; own public QA media and actual persisted assessments."""
import json,sys,hashlib,time
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
from contextlib import contextmanager
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Base,EvidenceAssessment,CloudCall,Entity
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/evidence-query-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=['audio-package-validation','audio-workflow','audio-validation','geo-workflow','pair-workflow','setup-voice-validation','speech-activity-validation','segmented-audio-validation','workflow-segmented-audio','instance-validation','instance-perception']
def a(n):return json.loads((ROOT/'.runtime'/f'{n}-access.json').read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def load(n):return json.loads((OUT/(n+'.json')).read_text())
def hashes(db,workspace):
 result={}
 for mapper in Base.registry.mappers:
  cls=mapper.class_
  if 'workspace_id' not in cls.__table__.columns:continue
  values={}
  for row in db.scalars(select(cls).where(cls.workspace_id==workspace)):
   raw={c.name:(v.isoformat() if hasattr(v,'isoformat') else v) for c in cls.__table__.columns if (v:=getattr(row,c.name)) is not None};values[row.id]=digest(raw)
  if values:result[cls.__name__]=values
 return result
@contextmanager
def connection(name):
 access=a(name)
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
  r=c.post('/login',json={k:access[k] for k in ('username','password')});r.raise_for_status();c.headers.update({'X-Workspace-ID':access['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']});yield c

def call(c,method,url,body=None):
 r=c.request(method,url,json=body) if body is not None else c.request(method,url);r.raise_for_status();return r.json()
def wait(c,identity):
 for _ in range(60):
  row=call(c,'GET','/evidence-assessments/'+identity)
  if row['status'] not in ['queued','running']:assert row['status']=='completed',row.get('error');return row
  time.sleep(.5)
 raise AssertionError('actual background assessment not terminal within thirty seconds')
phase=sys.argv[1]
if phase=='baseline':
 with Session(engine()) as db:save('old-source-baseline',{'workspaces':{a(n)['workspace_id']:hashes(db,a(n)['workspace_id']) for n in ACCESS},'cloud_ids':sorted(db.scalars(select(CloudCall.id)))})
 print('eleven own QA workspaces baseline saved; no credentials or raw source content exported')
elif phase=='cases':
 results=[]
 for name,modality in [('pair-workflow','imagery'),('workflow-segmented-audio','audio'),('instance-perception','video')]:
  with connection(name) as c:
   instance=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');objects=call(c,'GET','/instances/'+instance['id']+'/objects')
   body={'instance_id':instance['id'],'entity_ids':[objects[0]['id']],'purpose':'实际已保存'+modality+'资料时效与充分性核查','goal':'current_state','required_modalities':[modality],'lookback_hours':24,'request_id':str(uuid5(NAMESPACE_URL,'evidence-query-v1:'+name))}
   row=call(c,'POST','/evidence-assessments',body);row=wait(c,row['id']);records=row['result']['records'];assert records and all(r['integrity_verified'] for r in records)
   assert not row['result']['objects'][0]['current_state_sufficient'] and row['result']['cloud_calls']==0
   if modality=='audio':assert any(r['kind']=='segments' and r['metrics']['saved_asr_segments']==2 for r in records)
   if modality=='video':assert all(r['metrics']['saved_frames'] for r in records)
   result={'workspace':name,'id':row['id'],'modality':modality,'processing_records':len(records),'source_sha256':[s for r in records for s in r['source_sha256']],'current_state_sufficient':False,'background_job_completed':True,'source_time_verified':False,'cloud_calls':0}
   results.append(result)
 save('actual-source-cases',{'cases':results,'domain_quality_verified':False});print('actual saved native imagery/audio/video inputs checked by background worker; unknown clocks stay insufficient')
elif phase in {'snapshot','restart-check'}:
 snapshots={}
 for name in ['pair-workflow','workflow-segmented-audio','instance-perception']:
  with connection(name) as c:
   i=next(i for i in call(c,'GET','/instances') if i['status']=='partially_active');rows=call(c,'GET','/evidence-assessments?instance_id='+i['id']);assert rows
   proofs={r['id']:digest(call(c,'GET','/evidence-assessments/'+r['id']+'/export')) for r in rows};snapshots[name]={'rows':digest(rows),'exports':proofs}
 with Session(engine()) as db:
  count=0;old=load('old-source-baseline');current={a(n)['workspace_id']:hashes(db,a(n)['workspace_id']) for n in ACCESS}
  for w,classes in old['workspaces'].items():
   for kind,records in classes.items():
    for identity,sha in records.items():assert current[w].get(kind,{}).get(identity)==sha,(kind,identity);count+=1
  assert sorted(db.scalars(select(CloudCall.id)))==old['cloud_ids']
  state={'workspaces':current,'snapshots':snapshots,'old_records_identical':count,'new_cloud_calls':0}
 if phase=='snapshot':save('before-restart',state)
 else:assert state==load('before-restart');save('after-restart',state)
 print('all previous source records identical; current assessments, handoff and source/export signatures preserved')
