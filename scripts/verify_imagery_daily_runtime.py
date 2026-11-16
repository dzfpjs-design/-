"""Real background imagery receipts using existing own public QA workspaces."""
import os,sys,json,time,hashlib
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Base,GeoDaily,GeoScene,AudioDaily,Memory,CloudCall,now
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);OUT=ROOT/'docs/evidence/imagery-daily-v06';OUT.mkdir(parents=True,exist_ok=True)
phase=sys.argv[1];ACCESS=['audio-package-validation-access.json','audio-workflow-access.json','audio-validation-access.json','geo-workflow-access.json','pair-workflow-access.json','setup-voice-validation-access.json','speech-activity-validation-access.json','segmented-audio-validation-access.json','workflow-segmented-audio-access.json']
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def load(name):return json.loads((OUT/(name+'.json')).read_text())
def hashes(db,workspace):
 result={}
 for mapper in Base.registry.mappers:
  cls=mapper.class_
  if 'workspace_id' not in cls.__table__.columns or cls is GeoDaily:continue
  values={}
  for row in db.scalars(select(cls).where(cls.workspace_id==workspace)):
   raw={c.name:(v.isoformat() if hasattr(v,'isoformat') else v) for c in cls.__table__.columns if (v:=getattr(row,c.name)) is not None};values[row.id]=digest(raw)
  if values:result[cls.__name__]=values
 return result
if phase in {'baseline','preserve'}:
 with Session(engine()) as db:
  records={json.loads((ROOT/'.runtime'/name).read_text())['workspace_id']:hashes(db,json.loads((ROOT/'.runtime'/name).read_text())['workspace_id']) for name in ACCESS};cloud=sorted(db.scalars(select(CloudCall.id)).all())
  if phase=='baseline':save('old-source-baseline',{'workspaces':records,'cloud_ids':cloud});print('nine own workspaces baseline saved; no raw contents exported')
  else:
   old=load('old-source-baseline');count=0;rollovers=[]
   for workspace,classes in old['workspaces'].items():
    for name,rows in classes.items():
     for identity,value in rows.items():
      if records[workspace].get(name,{}).get(identity)!=value and name=='AudioDaily':
       # Natural China-day closing is a derived update, never ignored wholesale.
       from types import SimpleNamespace
       from generic_agents.audio_daily import checked_daily
       from generic_agents.geo_daily import day
       daily=checked_daily(db,SimpleNamespace(workspace_id=workspace),identity);assert not daily.data['provisional'] and daily.received_day<day(now())
       canonical={k:v for k,v in daily.data.items() if k not in {'provisional','receipt_hash','data_memory_id','updated_at','recovery_history'}};matches=[]
       for old_id in classes.get('Memory',{}):
        m=db.get(Memory,old_id)
        if m.data.get('type')=='audio_daily_summary' and m.data.get('daily_id')==identity and m.data.get('provisional') is True and {k:v for k,v in m.data.items() if k not in {'type','daily_id','provisional','receipt_hash'}}==canonical:matches.append(m.id)
       assert matches,'date closing must retain identical source records and summary values'
       rollovers.append({'daily_id':identity,'matching_preserved_old_memory_id':matches[-1],'only_canonical_provisional_flag_changed':True,'received_day':daily.received_day});continue
      assert records[workspace].get(name,{}).get(identity)==value,(name,identity);count+=1
   assert cloud==old['cloud_ids'];new=len(db.scalars(select(Memory).where(Memory.workspace_id.in_(list(records)),Memory.data['type'].astext=='imagery_daily_summary')).all())
   save('old-source-preservation',{'old_source_and_memory_records_identical':True,'old_records_checked':count,'expected_audio_daily_date_closings':rollovers,'new_imagery_daily_memories':new,'new_cloud_calls':0});print('old source and summary records identical:',count,'verified natural date closings:',len(rollovers),'new imagery receipts:',new)
 sys.exit(0)
a=json.loads((ROOT/'.runtime/pair-workflow-access.json').read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=45) as c:
 for attempt in range(60):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==59:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 def post(path,body=None):r=c.post(path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
 def wait(check):
  end=time.monotonic()+130
  while True:
   value=check()
   if value:return value
   if time.monotonic()>end:raise TimeoutError('saved operation still pending; do not create another request')
   time.sleep(.5)
 def collection(count):return next((r for r in get('/imagery-daily')['rows'] if r['processing_records']==count),None)
 if phase=='automatic':
  row=wait(lambda:collection(4));assert row['unique_originals']==2 and row['completed_comparisons']==2 and row['business_tasks']==1
  save('automatic',{'collection_id':row['id'],'read_only_poll':True,'manual_reconcile_used':False,'actual_background_collection':True,'processing_records':4,'unique_originals':2});print('actual background imagery receipt observed without manual reconcile')
 elif phase=='cases':
  for name,content in [('cancel',(ROOT/'.runtime/geo-pair-fixture/public-synthetic-A.tif').read_bytes()),('invalid',b'II*\x00'+b'\x00'*20)]:
   identity=str(uuid5(NAMESPACE_URL,a['workspace_id']+':imagery-daily:'+name));r=c.get('/geo/uploads/'+identity);row=r.json() if r.status_code==200 else None
   if r.status_code not in {200,404}:r.raise_for_status()
   if not row:
    r=c.post('/geo/scenes',data={'request_id':identity},files={'file':(('公开取消影像.tif' if name=='cancel' else '公开无法解析.tif'),content,'image/tiff')});r.raise_for_status();row=r.json()
   if name=='cancel' and row['status'] in {'queued','running'}:
    for attempt in range(8):
     fresh=get('/geo/scenes/'+row['id']);r=c.post('/geo/scenes/'+row['id']+'/cancel',json={'version':fresh['version']})
     if r.status_code==200:row=r.json();break
     if r.status_code!=409:r.raise_for_status()
    assert row['status']=='cancelled',row['status']
   if name=='invalid':row=wait(lambda:(value if value['status']=='failed' else None) if (value:=get('/geo/scenes/'+row['id'])) else None)
   save('case-'+name,{'scene_id':row['id'],'input_sha256':row['input_sha256'],'status':row['status'],'request_id':identity})
  row=wait(lambda:(value if len(value['failed_ids'])==1 and len(value['cancelled_ids'])==1 else None) if (value:=collection(6)) else None)
  assert row['unique_originals']==3;save('cases-automatic',{'collection_id':row['id'],'processing_records':6,'unique_originals':3,'failed':1,'cancelled':1});print('real invalid TIFF and cancellation automatically collected')
 elif phase=='fault':
  row=collection(6);assert row
  with Session(engine()) as db:
   daily=db.get(GeoDaily,row['id']);assert daily.workspace_id==a['workspace_id'];m=db.get(Memory,daily.data['data_memory_id']);assert m.status=='candidate' and m.data['type']=='imagery_daily_summary';value=dict(m.data);value['unique_originals']=999;m.data=value;db.commit();save('fault',{'collection_id':daily.id,'memory_id':m.id,'fault':'only own derived count; originals and human notes unchanged'})
  r=c.get('/imagery-daily');assert r.status_code==409;save('fault-observed',{'api_status':409,'source_modified':False});print('actual damaged derived receipt rejected')
 elif phase in {'snapshot','restart-check'}:
  row=wait(lambda:collection(6));assert row['unique_originals']==3 and row['recovery_history'] and len(row['failed_ids'])==1 and len(row['cancelled_ids'])==1
  task_record=next(r for r in row['records'] if r['task_id']);detail=get('/imagery-daily/'+row['id']+'/records/'+task_record['key']);assert detail['integrity_verified'] and len(detail['images'])==4 and detail['business_reviews']
  proofs={}
  for image in detail['images']:
   r=c.get(image['url']);r.raise_for_status();sha=hashlib.sha256(r.content).hexdigest();assert sha==image['sha256'];proofs[image['label']]=sha
  for index,source in enumerate(task_record['sources']):
   r=c.get('/imagery-daily/'+row['id']+'/records/'+task_record['key']+'/original/'+str(index));r.raise_for_status();assert hashlib.sha256(r.content).hexdigest()==source['sha256'];proofs['original-'+str(index)]=source['sha256']
  export=get('/imagery-daily/'+row['id']+'/export');fault=load('fault')
  with Session(engine()) as db:
   m=db.get(Memory,fault['memory_id']);assert m.status=='invalidated' and m.data['unique_originals']==999
   records=hashes(db,a['workspace_id']);daily={r.id:digest({'version':r.version,'data':r.data}) for r in db.scalars(select(GeoDaily).where(GeoDaily.workspace_id==a['workspace_id']))};assert sorted(db.scalars(select(CloudCall.id)).all())==load('old-source-baseline')['cloud_ids']
  value={'records':records,'daily':daily,'hashes':{**proofs,'collection':digest(row),'detail':digest(detail),'export':digest(export)},'new_cloud_calls':0}
  if phase=='snapshot':save('before-restart',value)
  else:assert value==load('before-restart');save('after-restart',value)
  print('actual original/4 comparison images/human receipt and daily record verified')
 elif phase=='sam-source-check':
  g=json.loads((ROOT/'.runtime/geo-workflow-access.json').read_text());r=c.post('/login',json={k:g[k] for k in ('username','password')});r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':g['workspace_id']})
  all_rows=get('/imagery-daily')['rows'];records=[(day,r) for day in all_rows for r in day['records'] if r['kind']=='segmentation' and r['status']=='succeeded'];assert records
  checked=[]
  for day,r in records:
   detail=get('/imagery-daily/'+day['id']+'/records/'+r['key']);assert detail['integrity_verified'] and detail['machine']['masks'] and detail['images'];proofs=[]
   for mask in detail['machine']['masks']:
    image=c.get('/geo/runs/'+r['id']+'/masks/'+mask['mask_evidence_id']);image.raise_for_status();assert hashlib.sha256(image.content).hexdigest()==mask['mask_sha256'];proofs.append(mask['mask_sha256'])
   checked.append({'run_id':r['id'],'source_sha256':detail['machine']['source_raster']['sha256'],'actual_saved_mask_hashes':proofs,'mask_count':len(proofs),'new_inference':False,'original_native_result_reused_readonly':True})
  save('sam-source-check',{'records':checked,'domain_quality_verified':False,'cloud_calls_added':0});print('saved real native SAM masks and lineage verified:',len(checked))
 else:raise ValueError('unknown phase')
