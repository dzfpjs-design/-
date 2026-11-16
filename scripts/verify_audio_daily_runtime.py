"""Own public sample workspace; real background receipts and UI recovery evidence."""
import os,sys,json,time,hashlib
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Base,AudioDaily,AudioRun,Memory,CloudCall,now
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);OUT=ROOT/'docs/evidence/audio-daily-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/workflow-segmented-audio-access.json';a=json.loads(ACCESS.read_text());phase=sys.argv[1]
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def hashes(db,workspace):
 result={}
 for mapper in Base.registry.mappers:
  cls=mapper.class_
  if 'workspace_id' not in cls.__table__.columns or cls is AudioDaily:continue
  values={}
  for row in db.scalars(select(cls).where(cls.workspace_id==workspace)):
   raw={c.name:(v.isoformat() if hasattr(v,'isoformat') else v) for c in cls.__table__.columns if (v:=getattr(row,c.name)) is not None}
   values[row.id]=digest(raw)
  if values:result[cls.__name__]=values
 return result
if phase=='preserve-baseline':
 files=['audio-package-validation-access.json','audio-workflow-access.json','audio-validation-access.json','geo-workflow-access.json','pair-workflow-access.json','setup-voice-validation-access.json','speech-activity-validation-access.json','segmented-audio-validation-access.json','workflow-segmented-audio-access.json']
 with Session(engine()) as db:
  records={json.loads((ROOT/'.runtime'/name).read_text())['workspace_id']:hashes(db,json.loads((ROOT/'.runtime'/name).read_text())['workspace_id']) for name in files}
  save('old-source-baseline',{'workspaces':records,'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
 print('nine own workspaces source hashes saved; no raw credentials or contents exported');sys.exit(0)
if phase=='preserve-check':
 baseline=json.loads((OUT/'old-source-baseline.json').read_text());count=0;new=0
 with Session(engine()) as db:
  for workspace,classes in baseline['workspaces'].items():
   current=hashes(db,workspace)
   for name,rows in classes.items():
    for identity,fingerprint in rows.items():assert current.get(name,{}).get(identity)==fingerprint,(name,identity);count+=1
   new+=len(db.scalars(select(Memory).where(Memory.workspace_id==workspace,Memory.data['type'].astext=='audio_daily_summary')).all())
  assert sorted(db.scalars(select(CloudCall.id)).all())==baseline['cloud_ids']
 save('old-source-preservation',{'old_records_checked':count,'old_records_identical':True,'new_audio_daily_memories':new,'derived_receipts_are_additive':True,'new_cloud_calls':0});print('old source records identical',count,'new derived audio memories',new);sys.exit(0)
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 def post(path,body=None):r=c.post(path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
 def wait(check):
  deadline=time.monotonic()+130
  while True:
   value=check()
   if value:return value
   if time.monotonic()>deadline:raise TimeoutError('background operation still pending; original request retained')
   time.sleep(.25)
 if phase=='automatic-check':
  row=wait(lambda:next((r for r in get('/audio-daily')['rows'] if r['business_tasks']==1 and r['processing_records']==4),None));assert row['unique_originals']==1 and row['saved_asr_segments']==2
  save('automatic',{'collection_id':row['id'],'read_only_poll':True,'manual_reconcile_used':False,'automatic_background_receipt':True,'processing_records':4,'unique_originals':1});print('actual background audio receipt observed without manual reconcile')
 elif phase=='cases':
  for name,content,media in [('cancel', (ROOT/'.runtime/vad-bootstrap/two-public-zh.wav').read_bytes(),'audio/wav'),('invalid',b'ID3'+b'\x00'*20,'audio/mpeg')]:
   identity=str(uuid5(NAMESPACE_URL,a['workspace_id']+':daily:'+name));row=next((r for r in get('/audio') if r['request_id']==identity),None)
   if not row:
    r=c.post('/audio',data={'request_id':identity,'configuration':json.dumps({'channel':'mono','language':'zh'})},files={'file':(('公开取消恢复.wav' if name=='cancel' else '公开无法解析.mp3'),content,media)});r.raise_for_status();row=r.json()
   if name=='cancel' and row['status'] in {'queued','running'}:
    for attempt in range(5):
     fresh=get('/audio/'+row['id']);r=c.post('/audio/'+row['id']+'/cancel',json={'version':fresh['version']})
     if r.status_code==200:row=r.json();break
     if r.status_code!=409:r.raise_for_status()
    assert row['status']=='cancelled'
   if name=='invalid':row=wait(lambda:(r if r['status']=='failed' else None) if (r:=get('/audio/'+row['id'])) else None)
   save('case-'+name,{'run_id':row['id'],'input_sha256':row['input_sha256'],'status':row['status'],'request_id':identity})
  row=wait(lambda:next((r for r in get('/audio-daily')['rows'] if r['processing_records']==6 and len(r['failed_ids'])==1 and len(r['cancelled_ids'])==1),None));save('cases-automatic',{'collection_id':row['id'],'failed':1,'cancelled':1,'processing_records':6,'unique_originals':2});print('actual decode failure and cancel are automatically represented')
 elif phase=='fault-receipt':
  row=get('/audio-daily')['rows'][0]
  with Session(engine()) as db:
   daily=db.get(AudioDaily,row['id']);assert daily.workspace_id==a['workspace_id'];m=db.get(Memory,daily.data['data_memory_id']);assert m.status=='candidate' and m.data['type']=='audio_daily_summary'
   value=dict(m.data);value['unique_originals']=999;m.data=value;db.commit();save('fault-receipt',{'memory_id':m.id,'collection_id':daily.id,'fault_injection':'own derived summary count only; source and human records unchanged'})
  response=c.get('/audio-daily');assert response.status_code==409;save('fault-observed',{'api_status':409,'source_modified':False});print('real derived receipt mismatch rejected; ready for UI reconcile')
 elif phase in {'snapshot','restart-check'}:
  case=json.loads((OUT/'case-cancel.json').read_text());row=get('/audio/'+case['run_id']);assert row['status']=='succeeded' and row['generation']==2 and row['result']['transcription']['inference_performed']
  collection=wait(lambda:next((r for r in get('/audio-daily')['rows'] if r['processing_records']==6 and not r['cancelled_ids'] and len(r['failed_ids'])==1),None));assert collection['unique_originals']==2 and collection['saved_asr_segments']==2 and collection['whole_transcriptions']==2 and collection['recovery_history']
  rec=next(r for r in collection['records'] if r['kind']=='segments');detail=get('/audio-daily/'+collection['id']+'/records/'+rec['key']);assert len(detail['machine']['segments'])==2 and '公开双区间工程验证' in detail['business_reviews'][-1]['note']
  original=c.get('/audio-daily/'+collection['id']+'/records/'+rec['key']+'/original');original.raise_for_status();assert hashlib.sha256(original.content).hexdigest()==rec['input_sha256'];export=get('/audio-daily/'+collection['id']+'/export')
  fault=json.loads((OUT/'fault-receipt.json').read_text())
  with Session(engine()) as db:
   bad=db.get(Memory,fault['memory_id']);assert bad.status=='invalidated' and bad.data['unique_originals']==999
   daily=db.get(AudioDaily,collection['id']);m=db.get(Memory,daily.data['data_memory_id']);assert m.status=='candidate' and m.data['unique_originals']==2
   current={cls.__name__:{r.id:{'version':r.version,'data':r.data,'status':getattr(r,'status',None)} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']))} for cls in (AudioDaily,AudioRun,Memory)}
   assert sorted(db.scalars(select(CloudCall.id)).all())==json.loads((OUT/'old-source-baseline.json').read_text())['cloud_ids']
  record={'records':current,'hashes':{'original':hashlib.sha256(original.content).hexdigest(),'collection':digest(collection),'detail':digest(detail),'export':digest(export)},'new_cloud_calls':0}
  if phase=='snapshot':save('before-restart',record)
  else:assert record==json.loads((OUT/'before-restart.json').read_text());save('after-restart',record)
  print('actual audio collection, retry, human receipt and source hashes verified')
 else:raise ValueError('unknown phase')
