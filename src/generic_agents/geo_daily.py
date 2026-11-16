"""Local imagery provenance, daily receipts and explicit data-quality gaps."""
import copy
from datetime import datetime,timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .models import GeoScene,GeoRun,GeoPair,GeoDaily,Task,Memory,Audit,now
from .contracts import digest
from .services import scoped
from .geo_storage import path_for
from .db import engine
from .audio_daily import task_review

TZ=ZoneInfo('Asia/Shanghai')
KINDS={'scene':GeoScene,'segmentation':GeoRun,'comparison':GeoPair}
QUALITY_VERSION=1

def day(value):return str(value.astimezone(TZ).date())
def key(kind,row):return kind+':'+row.id

def sources(db,p,kind,row):
 d=row.data
 if kind=='comparison':
  saved=[{'id':s['scene_id'],'evidence_id':s['evidence_id'],'sha256':s['sha256'],'name':s['name'],'configuration':s['configuration'],'metadata':s['metadata']} for s in d['sources']]
 elif kind=='segmentation':saved=[{'id':row.scene_id,'evidence_id':d['original_evidence_id'],'sha256':d['input_sha256'],'name':None,'configuration':d['configuration'],'metadata':d['metadata']}]
 else:saved=[{'id':row.id,'evidence_id':row.evidence_id,'sha256':d['input_sha256'],'name':d['name'],'configuration':d.get('configuration'),'metadata':d.get('metadata')}]
 result=[]
 for value in saved:
  scene=scoped(db,GeoScene,value['id'],p)
  if scene.evidence_id!=value['evidence_id'] or scene.data['input_sha256']!=value['sha256']:raise HTTPException(409,'归集原件与原上传记录不一致')
  provenance={}
  if scene.data.get('wms_origin'):
   from .wms import source_provenance
   provenance={'wms_origin':source_provenance(db,p,scene)}
  origin_day=day(datetime.fromisoformat(provenance['wms_origin']['read_requested_at'])) if provenance else day(scene.created_at)
  result.append({**copy.deepcopy(value),'name':value['name'] or scene.data['name'],'upload_day':origin_day,'observed_at':(value['configuration'] or {}).get('observed_at'),'time_basis':'user_declared_unverified' if (value['configuration'] or {}).get('observed_at') else 'unknown',**provenance})
 return result

def check_source(db,p,kind,row):
 src=sources(db,p,kind,row)
 for s in src:
  ev,_=path_for(db,p,s['evidence_id'])
  if ev.sha256!=s['sha256']:raise HTTPException(409,'影像摘要不一致')
 d=row.data
 if kind=='scene' and d.get('preview'):
  from .geo_api import preview
  preview(row.id,p,db)
 if row.status=='succeeded':
  if kind=='segmentation':
   from .geo_api import checked_run
   from .workflow_scenes import verify_result_masks
   checked_run(db,p,row.id);verify_result_masks(db,p,row)
   result=d['result']['source_raster']
   if result['sha256']!=d['input_sha256'] or result['roi']!=d['roi'] or result['rgb_bands']!=d['configuration']['rgb_bands']:raise HTTPException(409,'区域候选与原件设置不一致')
  elif kind=='comparison':
   from .geo_pairs import checked,artifact_bytes,kinds
   checked(db,p,row)
   for a in kinds(row):artifact_bytes(db,p,row,a)
 if d.get('workflow_task_id'):
  task=scoped(db,Task,d['workflow_task_id'],p)
  if kind=='comparison':
   from .workflow_geo_pairs import verify_inputs,verify_pair_result
   verify_inputs(db,p,task)
   if row.status=='succeeded':verify_pair_result(db,p,task,row)
  elif kind=='segmentation':
   from .workflow_scenes import verify
   verify(db,p,task);saved=task.data['scene_snapshots'][0];snap=saved['snapshot']
   if d['input_scene_id']!=saved['id'] or d['input_scene_snapshot_hash']!=saved['snapshot_hash'] or d['input_sha256']!=snap['input_sha256'] or d['roi']!=snap['roi'] or d['configuration']!=snap['configuration']:raise HTTPException(409,'区域候选与原任务固定输入不一致')
 return src

def integrity(db,p,kind,row):
 try:check_source(db,p,kind,row);return True,None
 except (HTTPException,ValueError,KeyError,TypeError,OSError):return False,'原影像、来源或处理证据未通过一致性核对。请查看原记录，恢复后重新核对。'

def gaps(src,kind,row):
 result=[]
 if any(s['time_basis']=='unknown' for s in src):result.append('capture_time_unknown')
 if any(s.get('wms_origin') for s in src):result.append('rendered_map_not_raw_bands')
 if any(not s['metadata'] for s in src):result.append('metadata_not_read')
 if any(s['metadata'] and s['metadata'].get('georeference_status')!='affine_available' for s in src):result.append('map_reference_unavailable')
 if any(not s['configuration'] for s in src):result.append('rgb_not_configured')
 elif any(not s['configuration'].get('map_image_confirmed') for s in src):result.append('map_reference_not_confirmed')
 return result

def record(db,p,kind,row,cached=None,force=False):
 d=row.data;task=None;task_error=False
 if d.get('workflow_task_id'):
  try:task=scoped(db,Task,d['workflow_task_id'],p)
  except HTTPException:task_error=True
 today=day(now());version=task.version if task else None
 wms_cache_current=not cached or not any(s.get('wms_origin') for s in cached.get('sources',[])) or cached.get('wms_source_schema_version')==1
 if not force and cached and wms_cache_current and cached.get('quality_schema_version')==QUALITY_VERSION and cached['source_version']==row.version and cached['task_version']==version and cached['integrity_checked_on']==today:return copy.deepcopy(cached)
 valid,issue=integrity(db,p,kind,row)
 if task_error:valid=False;issue='原业务任务无法核对，原处理记录保留。'
 try:src=sources(db,p,kind,row)
 except (HTTPException,ValueError,KeyError,TypeError):src=[];valid=False;issue='原上传来源无法核对，按处理请求日期暂存，原记录保留。'
 origin_day=src[0]['upload_day'] if src else day(row.created_at);result=d.get('result') or {}
 action={'scene':'geo.scene_checked','segmentation':'geo.candidates_saved','comparison':'geo.pair_saved'}[kind]
 completed=db.scalar(select(Audit.created_at).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==action).order_by(Audit.created_at.desc()).limit(1)) if row.status in {'ready','parsed','succeeded'} else None
 r={'key':key(kind,row),'kind':kind,'id':row.id,'name':d.get('name') or (' / '.join(s['name'] for s in src) if src else '影像处理记录'),'source_version':row.version,'status':row.status,'sources':src,'origin_upload_day':origin_day,
  'date_basis':('A_map_read_request_for_comparison' if kind=='comparison' else 'WMS_map_read_request') if src and src[0].get('wms_origin') else 'A_upload_request_for_comparison' if kind=='comparison' and src else 'original_upload_request' if src else 'processing_request_origin_unavailable','processing_request_day':day(row.created_at),
  'processing_completed_day':day(completed) if completed else None,'late_processing':bool(day(row.created_at)!=origin_day or completed and day(completed)!=origin_day),'integrity_verified':valid,'integrity_issue':issue,'integrity_checked_on':today,'error':d.get('error'),'result_hash':d.get('result_hash'),
  'quality_schema_version':QUALITY_VERSION,'quality_gaps':gaps(src,kind,row),'review_state':task_review(task) if task else 'confirmed' if d.get('review') else 'unreviewed' if kind!='scene' else 'not_applicable',
  'review_hash':digest(d['review']) if d.get('review') else None,'task_id':task.id if task else None,'task_version':version,'task_title':task.title if task else None,'instance_id':task.data['instance_id'] if task else None,
  'candidate_masks':len(result.get('masks',[])) if valid else 0,'candidate_pixels':result.get('candidate_pixels',0) if valid else 0,'unknown_pixels':result.get('unknown_pixels') if valid else None,
  'learned_change_model':d.get('request_kind')=='learned_binary_change','entity_state_updated':False,'not_authoritative':True}
 if any(s.get('wms_origin') for s in src):r['wms_source_schema_version']=1
 return r

def summary(records):
 def ids(test):return [r['key'] for r in records if test(r)]
 return {'processing_records':len(records),'unique_originals':len({s['sha256'] for r in records for s in r['sources']}),'verified_originals':len({s['sha256'] for r in records if r['integrity_verified'] for s in r['sources']}),
  'upload_requests':len({s['id'] for r in records for s in r['sources']}),'completed_segmentations':sum(r['kind']=='segmentation' and r['status']=='succeeded' and r['integrity_verified'] for r in records),
  'completed_comparisons':sum(r['kind']=='comparison' and r['status']=='succeeded' and r['integrity_verified'] for r in records),'business_tasks':len({r['task_id'] for r in records if r['task_id']}),
  'pending_ids':ids(lambda r:r['status'] in {'queued','running'}),'failed_ids':ids(lambda r:r['status']=='failed'),'cancelled_ids':ids(lambda r:r['status']=='cancelled'),
  'integrity_issue_ids':ids(lambda r:not r['integrity_verified']),'quality_gap_ids':ids(lambda r:bool(r['quality_gaps'])),'unreviewed_ids':ids(lambda r:r['review_state'] in {'unreviewed','waiting','draft_saved'}),
  'status':'requires_attention' if any(not r['integrity_verified'] or r['status']=='failed' for r in records) else 'pending' if any(r['status'] in {'queued','running'} for r in records) else 'collected_with_limits'}

def checked_daily(db,p,identity):
 daily=scoped(db,GeoDaily,identity,p);value={k:v for k,v in daily.data.items() if k not in {'receipt_hash','data_memory_id','updated_at','recovery_history'}};memory=scoped(db,Memory,daily.data['data_memory_id'],p)
 if digest(value)!=daily.data['receipt_hash'] or memory.status!='candidate' or memory.data!={**value,'type':'imagery_daily_summary','daily_id':daily.id,'receipt_hash':daily.data['receipt_hash']}:raise HTTPException(409,'影像归集与数据回执不一致，请重新核对源记录')
 return daily

def reconcile(db,workspace_id=None,force=False,target_day=None):
 from .media_daily_delivery import publish_collection
 from .worker_scope import related
 db.execute(text('SELECT pg_advisory_xact_lock(734106094)'));today=day(now());cutoff=now()-timedelta(days=8)
 q=select(GeoDaily)
 if workspace_id is not None:q=q.where(GeoDaily.workspace_id==workspace_id)
 previous=db.scalars(q).all();existing={(r.workspace_id,r.received_day):r for r in previous};cached={r['key']:r for d in previous for r in d.data['records']};chosen=set();candidates={};recovery_keys=set()
 if target_day is not None:chosen.add((workspace_id,target_day))
 for daily in previous:
  if daily.data['provisional'] and daily.received_day!=today:chosen.add((daily.workspace_id,daily.received_day))
  try:checked_daily(db,SimpleNamespace(workspace_id=daily.workspace_id),daily.id)
  except HTTPException:
   chosen.add((daily.workspace_id,daily.received_day));recovery_keys.update(r['key'] for r in daily.data['records'])
 task_q=select(Task.id,Task.version)
 if workspace_id is not None:task_q=task_q.where(Task.workspace_id==workspace_id)
 versions=dict(db.execute(task_q).all())
 for kind,cls in KINDS.items():
  q=select(cls.id,cls.workspace_id,cls.created_at,cls.version,cls.data['workflow_task_id'].astext)
  if workspace_id is not None:q=q.where(cls.workspace_id==workspace_id)
  for identity,workspace,created,version,task in db.execute(q):
   old=cached.get(kind+':'+identity)
   if not old or old.get('quality_schema_version')!=QUALITY_VERSION or created>=cutoff or old['source_version']!=version or old['task_version']!=versions.get(task) or old['status'] in {'queued','running'} or old['review_state'] in {'waiting','draft_saved','processing','blocked'}:
    row=related(db,cls,identity,workspace_id);p=SimpleNamespace(workspace_id=workspace)
    try:origin=sources(db,p,kind,row)[0]['upload_day']
    except (HTTPException,ValueError,KeyError,TypeError):origin=day(created)
    chosen.add((workspace,origin));candidates[key(kind,row)]=(kind,row)
 for daily in previous:
  if (daily.workspace_id,daily.received_day) not in chosen:continue
  for r in daily.data['records']:
   row=related(db,KINDS[r['kind']],r['id'],workspace_id)
   if row and row.workspace_id==daily.workspace_id:candidates[r['key']]=(r['kind'],row)
 groups={}
 for k,row in candidates.values():
  old=cached.get(key(k,row));force_record=force and (target_day is None or old and old['origin_upload_day']==target_day)
  r=record(db,SimpleNamespace(workspace_id=row.workspace_id),k,row,old,force_record or key(k,row) in recovery_keys);groups.setdefault((row.workspace_id,r['origin_upload_day']),[]).append(r)
 for (workspace,received),records in groups.items():
  records=sorted(records,key=lambda r:r['key']);value={'received_day':received,'provisional':received==today,'records':records,**summary(records),'cloud_calls':0,'entity_state_updated':False,'domain_quality':'not_verified'};fingerprint=digest(value);daily=existing.get((workspace,received));recovery=None
  if daily:
   try:
    checked_daily(db,SimpleNamespace(workspace_id=workspace),daily.id)
    if daily.data['receipt_hash']==fingerprint:
     publish_collection(db,daily,'imagery');continue
   except HTTPException:
    recovery=daily.data.get('data_memory_id');old=related(db,Memory,recovery,workspace_id,allow_missing=True)
    if old and old.workspace_id==workspace and old.status=='candidate' and old.data.get('type')=='imagery_daily_summary':old.status='invalidated';old.version+=1
   daily.version+=1
  else:daily=GeoDaily(workspace_id=workspace,received_day=received);db.add(daily);db.flush()
  history=(daily.data.get('recovery_history',[]) if daily.data else [])+([{'memory_id':recovery,'at':now().isoformat()}] if recovery else [])
  memory=Memory(workspace_id=workspace,status='candidate',text=received+'影像资料与质量缺口归集（非对象状态）',data={**value,'type':'imagery_daily_summary','daily_id':daily.id,'receipt_hash':fingerprint});db.add(memory);db.flush()
  daily.data={**value,'receipt_hash':fingerprint,'data_memory_id':memory.id,'updated_at':now().isoformat(),'recovery_history':history}
  publish_collection(db,daily,'imagery')

def schedule_once(workspace_id=None):
 with Session(engine()) as db:
  if workspace_id is None:reconcile(db)
  else:reconcile(db,workspace_id=workspace_id)
  db.commit()

def detail(db,p,daily,identity):
 saved=next((r for r in daily.data['records'] if r['key']==identity),None)
 if not saved:raise HTTPException(404,'此处理不属于当前影像归集')
 row=scoped(db,KINDS[saved['kind']],saved['id'],p);current=record(db,p,saved['kind'],row,force=True);valid=current['integrity_verified']
 if [s['sha256'] for s in current['sources']]!=[s['sha256'] for s in saved['sources']]:valid=False;current['integrity_issue']='原件已变化，不能替换此归集依据。'
 d=row.data;task=scoped(db,Task,d['workflow_task_id'],p) if d.get('workflow_task_id') else None;rt=task.data.get('workflow_runtime',{}) if task else {}
 images=[]
 if valid:
  if saved['kind']=='scene' and d.get('preview'):images=[{'label':'已保存RGB预览','url':'/geo/scenes/'+row.id+'/preview','sha256':d['preview']['sha256']}]
  elif saved['kind']=='segmentation' and row.status=='succeeded':images=[{'label':'实际分析RGB范围','url':'/geo/runs/'+row.id+'/rgb','sha256':d['rgb_checkpoint']['rgb_sha256']}]
  elif saved['kind']=='comparison' and row.status=='succeeded':images=[{'label':label,'url':'/geo/pairs/'+row.id+'/artifacts/'+k,'sha256':d['result']['artifacts'][k]['sha256']} for k,label in [('before','A参考影像'),('after','B实际对齐范围'),('valid','共同有效区域'),('candidate','变化候选' if d.get('request_kind')=='learned_binary_change' else '像素差异候选')]]
 return {'record':saved,'current':current,'integrity_verified':valid,'images':images,'machine':copy.deepcopy(d.get('result')) if valid else None,'review':copy.deepcopy(d.get('review')),
  'business_reviews':[{'step_name':r['step_name'],'note':r['note']} for r in rt.get('receipts',[]) if r.get('execution_mode')!='registered_ability'],'business_drafts':[{'note':v['note']} for v in rt.get('drafts',{}).values()],
  'task_url':'/setup/?view=workspace&instance='+task.data['instance_id']+'&task='+task.id if task else None,'changed_since_collection':current['source_version']!=saved['source_version'] or current['task_version']!=saved['task_version']}
