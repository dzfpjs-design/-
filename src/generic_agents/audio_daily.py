"""Source-cohort audio receipts, including late processing and human review changes."""
import copy
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .contracts import digest
from .models import AudioDaily,AudioRun,SpeechActivityRun,SegmentedAudioRun,WorkflowAudio,Task,Memory,Audit,now
from .services import scoped
from .db import engine

TZ=ZoneInfo('Asia/Shanghai')
from .sound_models import EnvironmentSoundRun
LEGACY_KINDS={'transcription':AudioRun,'activity':SpeechActivityRun,'segments':SegmentedAudioRun}
KINDS={**LEGACY_KINDS,'environment_sound':EnvironmentSoundRun}
NAMES={'transcription':'整片语音转写','activity':'语音区间检测','segments':'分段语音转写','environment_sound':'环境声音候选'}

def row_key(kind,row):return kind+':'+row.id

def checked_source(db,p,kind,row):
 if kind=='environment_sound':
  from .sound_daily import checked_source as checked_sound
  return checked_sound(db,p,row)
 if kind=='transcription':
  from .local_audio import checked
 elif kind=='activity':
  from .local_vad import checked
 else:
  from .local_segmented import checked
 source,content=checked(db,p,row)
 if row.data.get('workflow_task_id'):
  task=scoped(db,Task,row.data['workflow_task_id'],p)
  from .workflow_audio import verify
  verify(db,p,task);saved=task.data['audio_snapshots'][0]
  if row.data['input_audio_id']!=saved['id'] or row.data['input_snapshot_hash']!=saved['snapshot_hash']:raise HTTPException(409,'业务音频归集来源与任务原件不一致')
  if row.status=='succeeded':
   if kind=='segments':
    from .workflow_segmented_audio import verify_result
    verify_result(db,p,task,row)
   elif kind=='transcription':
    from .workflow_audio import verify_result
    verify_result(db,p,task,row)
   elif row.evidence_id!=saved['snapshot']['evidence_id'] or row.data['configuration']!={'channel':saved['snapshot']['configuration']['channel'],'threshold':.5}:raise HTTPException(409,'业务语音检测与原任务通道不一致')
 return source,content

def origin(db,p,kind,row):
 ancestors=[]
 if kind=='segments':
  row=scoped(db,SpeechActivityRun,row.source_run_id,p);kind='activity';ancestors.append((kind,row))
 if row.data.get('workflow_task_id'):
  attached=scoped(db,WorkflowAudio,row.data['input_audio_id'],p)
  if attached.data['snapshot_hash']!=row.data['input_snapshot_hash']:raise HTTPException(409,'任务音频关联摘要已变化')
  row=scoped(db,AudioRun,attached.source_run_id,p);kind='transcription';ancestors.append((kind,row))
  if row.data.get('workflow_task_id'):raise HTTPException(409,'任务音频来源不是独立上传原件')
 return kind,row,ancestors

def integrity(db,p,kind,row):
 try:checked_source(db,p,kind,row);return True,None
 except (HTTPException,ValueError,KeyError,TypeError,OSError):return False,'原件、来源或处理结果未通过一致性核对，请查看原处理记录。'

def task_review(task):
 if not task:return 'not_applicable'
 rt=task.data.get('workflow_runtime',{})
 if task.status=='workflow_cancelled':return 'cancelled'
 if task.status=='workflow_completed':return 'confirmed' if any(r.get('execution_mode')!='registered_ability' for r in rt.get('receipts',[])) else 'not_requested'
 if task.status in {'workflow_failed','workflow_blocked'}:return 'blocked'
 if rt.get('drafts'):return 'draft_saved'
 return 'waiting' if task.status in {'workflow_waiting','workflow_pending'} else 'processing'

def record(db,p,kind,row,root,origin_day,origin_error=None):
 valid,issue=integrity(db,p,kind,row)
 if origin_error:valid=False;issue=origin_error
 d=row.data;task=None
 if d.get('workflow_task_id'):
  try:task=scoped(db,Task,d['workflow_task_id'],p)
  except HTTPException:valid=False;issue='原业务任务无法核对，处理记录保留。'
 s=d.get('source',d);result=d.get('result') or {};machine=result.get('transcription',{})
 segment_count=len(d.get('segments',[])) if valid and kind=='segments' else 0
 intervals=len(result.get('activity',{}).get('intervals',[])) if valid and kind=='activity' else 0
 review=d.get('review');draft=d.get('review_draft');review_state=task_review(task) if task else 'confirmed' if review else 'draft_saved' if draft else 'unreviewed'
 action={'transcription':'audio.transcription.saved','activity':'audio.activity.saved','segments':'audio.segments.saved'}[kind]
 completed=db.scalar(select(Audit.created_at).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==action).order_by(Audit.created_at.desc()).limit(1)) if row.status=='succeeded' else None
 completed_day=str(completed.astimezone(TZ).date()) if completed else None
 return {'key':row_key(kind,row),'kind':kind,'id':row.id,'name':d.get('name','音频处理记录'),'source_version':row.version,'generation':d.get('generation'),'status':row.status,
  'mode':'business_automatic' if task else 'manual_processing' if kind=='segments' else 'upload','origin_key':row_key(root[0],root[1]) if root else None,'origin_upload_day':origin_day,
  'date_basis':'original_upload_request_clock' if root else 'processing_request_clock_origin_unavailable','processing_request_day':str(row.created_at.astimezone(TZ).date()),
  'processing_completed_day':completed_day,'late_processing':bool(str(row.created_at.astimezone(TZ).date())!=origin_day or completed_day and completed_day!=origin_day),'input_sha256':s.get('input_sha256'),'evidence_id':s.get('evidence_id',getattr(row,'evidence_id',None)),
  'source_time':'unknown','integrity_verified':valid,'integrity_issue':issue,'error':d.get('error'),'result_hash':d.get('result_hash'),
  'saved_asr_segments':segment_count,'vad_intervals':intervals,'whole_asr_performed':bool(valid and kind=='transcription' and machine.get('inference_performed')),
  'no_speech_candidates':bool(valid and row.status=='succeeded' and (kind=='activity' and not intervals or kind=='segments' and not d['configuration']['indexes'])),
  'review_state':review_state,'review_hash':digest(review) if review else None,'review_draft_hash':digest(draft) if draft else None,
  'task_id':task.id if task else None,'task_version':task.version if task else None,'task_status':task.status if task else None,'task_title':task.title if task else None,
  'instance_id':task.data['instance_id'] if task else None,'not_authoritative':True}

def source_groups(db,workspace_id=None):
 from .worker_scope import related
 previous_q=select(AudioDaily)
 if workspace_id is not None:previous_q=previous_q.where(AudioDaily.workspace_id==workspace_id)
 previous=db.scalars(previous_q).all();by_key={r['key']:(d.workspace_id,d.received_day,r) for d in previous for r in d.data['records'] if r['kind'] in LEGACY_KINDS}
 task_q=select(Task.id,Task.version)
 if workspace_id is not None:task_q=task_q.where(Task.workspace_id==workspace_id)
 task_versions=dict(db.execute(task_q).all());cutoff=now()-timedelta(days=8);candidates={};selected=set()
 # A damaged saved receipt must remain recoverable even outside the recent window.
 # This checks derived records only; original bytes are read for selected cohorts.
 for daily in previous:
  if daily.data.get('provisional') and daily.received_day!=str(now().astimezone(TZ).date()):selected.add((daily.workspace_id,daily.received_day))
  try:checked_daily(db,SimpleNamespace(workspace_id=daily.workspace_id),daily.id)
  except HTTPException:selected.add((daily.workspace_id,daily.received_day))
 for kind,cls in LEGACY_KINDS.items():
  q=select(cls.id,cls.workspace_id,cls.version,cls.created_at,cls.data['workflow_task_id'].astext)
  if workspace_id is not None:q=q.where(cls.workspace_id==workspace_id)
  for identity,workspace,version,created,task_id in db.execute(q):
   old=by_key.get(kind+':'+identity);r=old[2] if old else None
   if created>=cutoff or r and (r['source_version']!=version or r['task_version']!=task_versions.get(task_id) or r['status'] in {'queued','running'} or r['review_state'] in {'waiting','draft_saved','processing','blocked'}):
    row=related(db,cls,identity,workspace_id);candidates[row_key(kind,row)]=(kind,row)
 def cohort(kind,row):
  p=SimpleNamespace(workspace_id=row.workspace_id)
  try:
   k,root,ancestors=origin(db,p,kind,row);day=str(root.created_at.astimezone(TZ).date());error=None
  except (HTTPException,KeyError,TypeError):
   root=None;k=None;ancestors=[];day=str(row.created_at.astimezone(TZ).date());error='原上传来源无法核对，此项按处理请求日期暂存，录制日期仍未知。'
  return (row.workspace_id,day),(k,root) if root else None,ancestors,error
 # Late operations retain their original cohort and all already-collected members.
 for kind,row in list(candidates.values()):
  key,root,ancestors,error=cohort(kind,row);selected.add(key)
  for k,r in ancestors:candidates[row_key(k,r)]=(k,r)
 for daily in previous:
  if (daily.workspace_id,daily.received_day) not in selected:continue
  for r in daily.data['records']:
   if r['kind'] not in LEGACY_KINDS:continue
   row=related(db,LEGACY_KINDS[r['kind']],r['id'],workspace_id)
   if row and row.workspace_id==daily.workspace_id:candidates[r['key']]=(r['kind'],row)
 groups={}
 for kind,row in candidates.values():
  key,root,_,error=cohort(kind,row)
  groups.setdefault(key,[]).append(record(db,SimpleNamespace(workspace_id=row.workspace_id),kind,row,root,key[1],error))
 return groups

def summary(records):
 sounds=[r for r in records if r.get('kind')=='environment_sound']
 records=[r for r in records if r.get('kind')!='environment_sound']
 pending=[r['key'] for r in records if r['status'] in {'queued','running'}];failed=[r['key'] for r in records if r['status']=='failed'];cancelled=[r['key'] for r in records if r['status']=='cancelled'];bad=[r['key'] for r in records if not r['integrity_verified']]
 value={'pending_ids':pending,'failed_ids':failed,'cancelled_ids':cancelled,'integrity_issue_ids':bad,'unique_originals':len({r['input_sha256'] for r in records if r['input_sha256']}),'verified_originals':len({r['input_sha256'] for r in records if r['input_sha256'] and r['integrity_verified']}),
   'upload_requests':len({r['origin_key'] for r in records if r['origin_key']}),'processing_records':len(records),'business_tasks':len({r['task_id'] for r in records if r['task_id']}),
   'business_tasks_awaiting_review':len({r['task_id'] for r in records if r['task_id'] and r['review_state'] in {'waiting','draft_saved'}}),
   'saved_asr_segments':sum(r['saved_asr_segments'] for r in records),'whole_transcriptions':sum(r['whole_asr_performed'] for r in records),'vad_intervals':sum(r['vad_intervals'] for r in records),
   'no_speech_candidate_records':sum(r['no_speech_candidates'] for r in records),'late_processing_records':sum(r['late_processing'] for r in records),
   'status':'requires_attention' if failed or bad else 'pending' if pending else 'collected_with_limits'}
 if sounds:
  value['environment_sounds']={'received_runs':len(sounds),'window_count':sum(r['sound_windows'] for r in sounds),
   'candidate_class_count':sum(r['sound_candidate_classes'] for r in sounds),'reviewed_runs':sum(r['review_state']=='confirmed' for r in sounds),
   'date_basis':'machine_result_registered','recording_time_verified':False,'not_authoritative':True}
 return value

def save_collection(db,workspace,day,records):
 from .media_daily_delivery import publish_collection
 from .worker_scope import related
 today=str(now().astimezone(TZ).date())
 records=sorted(records,key=lambda r:r['key']);value={'received_day':day,'date_basis':'original_upload_request_clock_with_explicit_unavailable_origins','source_time':'unknown','source_time_verified':False,'provisional':day==today,'records':records,**summary(records),'not_authoritative':True,'cloud_calls':0,'entity_state_updated':False}
 has_sound=any(r.get('kind')=='environment_sound' for r in records)
 if has_sound:value['date_basis']='per_record_declared_clock_with_unknown_recording_time'
 fingerprint=digest(value);daily=db.scalar(select(AudioDaily).where(AudioDaily.workspace_id==workspace,AudioDaily.received_day==day).with_for_update())
 recovery=None
 # Unscoped legacy collection may refresh ASR, but cannot drop signed sound rows.
 if daily and not has_sound:
  retained=[copy.deepcopy(item) for item in daily.data.get('records',[]) if item.get('kind')=='environment_sound']
  if retained:
   checked_daily(db,SimpleNamespace(workspace_id=workspace),daily.id)
   records=sorted([*records,*retained],key=lambda item:item['key']);has_sound=True
   value.update(records=records,**summary(records),date_basis='per_record_declared_clock_with_unknown_recording_time')
   fingerprint=digest(value)
 if daily:
  try:
   checked_daily(db,SimpleNamespace(workspace_id=workspace),daily.id)
   if daily.data['receipt_hash']==fingerprint:
    publish_collection(db,daily,'audio');return daily
  except HTTPException:
   # A new sound never repairs or invalidates an existing ASR/day receipt.
   if has_sound:raise
   old=related(db,Memory,daily.data.get('data_memory_id'),workspace,allow_missing=True);recovery=daily.data.get('data_memory_id')
   if old and old.workspace_id==workspace and old.status=='candidate' and old.data.get('type')=='audio_daily_summary':old.status='invalidated';old.version+=1
 if not daily:daily=AudioDaily(workspace_id=workspace,received_day=day);db.add(daily);db.flush()
 else:daily.version+=1
 history=(daily.data.get('recovery_history',[]) if daily.data else [])+([{'memory_id':recovery,'at':now().isoformat()}] if recovery else [])
 memory=Memory(workspace_id=workspace,status='candidate',text=day+'音频资料归集（录制时间与领域质量待核）',data={**value,'type':'audio_daily_summary','daily_id':daily.id,'receipt_hash':fingerprint});db.add(memory);db.flush()
 daily.data={**value,'receipt_hash':fingerprint,'data_memory_id':memory.id,'updated_at':now().isoformat(),'recovery_history':history}
 publish_collection(db,daily,'audio')
 return daily


def reconcile(db,workspace_id=None):
 db.execute(text('SELECT pg_advisory_xact_lock(734106093)'))
 groups=source_groups(db,workspace_id)
 from .sound_daily import groups as sound_groups
 for cohort,records in sound_groups(db,workspace_id).items():groups.setdefault(cohort,[]).extend(records)
 for (workspace,day),records in groups.items():save_collection(db,workspace,day,records)


def checked_daily(db,p,identity):
 daily=scoped(db,AudioDaily,identity,p);value={k:v for k,v in daily.data.items() if k not in {'receipt_hash','data_memory_id','updated_at','recovery_history'}};memory=scoped(db,Memory,daily.data['data_memory_id'],p)
 if digest(value)!=daily.data['receipt_hash'] or memory.status!='candidate' or memory.data!={**value,'type':'audio_daily_summary','daily_id':daily.id,'receipt_hash':daily.data['receipt_hash']}:raise HTTPException(409,'音频归集与数据回执不一致，请核对原记录')
 return daily

def schedule_once(workspace_id=None):
 with Session(engine()) as db:
  if workspace_id is None:reconcile(db)
  else:reconcile(db,workspace_id=workspace_id)
  db.commit()

def detail(db,p,daily,key):
 saved=next((r for r in daily.data['records'] if r['key']==key),None)
 if not saved:raise HTTPException(404,'此处理记录不属于当前音频归集')
 kind=saved['kind']
 if kind=='environment_sound':
  from .sound_daily import detail as sound_detail
  return sound_detail(db,p,saved)
 row=scoped(db,KINDS[kind],saved['id'],p);valid,issue=integrity(db,p,kind,row);d=row.data;s=d.get('source',d)
 if s['input_sha256']!=saved['input_sha256']:valid=False;issue='音频原件已变化，不能替换原归集依据。'
 task=scoped(db,Task,d['workflow_task_id'],p) if d.get('workflow_task_id') else None
 result=d.get('result') or {};rt=task.data.get('workflow_runtime',{}) if task else {}
 def human(v):return {k:copy.deepcopy(v[k]) for k in ['text','texts','decisions','note','confirmed','saved_at'] if k in v} if v else None
 return {'record':saved,'current_status':row.status,'current_version':row.version,'changed_since_collection':row.version!=saved['source_version'] or (task.version if task else None)!=saved['task_version'],'integrity_verified':valid,'integrity_issue':issue,'error':d.get('error'),
  'machine':{'transcription':result.get('transcription',{}).get('text') if kind=='transcription' else None,'intervals':result.get('activity',{}).get('intervals',[]) if kind=='activity' else [],'segments':d.get('segments',[]) if kind=='segments' else []} if valid else None,
  'review':human(d.get('review')),'review_draft':human(d.get('review_draft')),
  'business_reviews':[{'step_name':r['step_name'],'note':r['note'],'at':r['at']} for r in rt.get('receipts',[]) if r.get('execution_mode')!='registered_ability'],
  'business_drafts':[{'note':v['note']} for v in rt.get('drafts',{}).values()],
  'task_url':'/setup/?view=workspace&instance='+task.data['instance_id']+'&task='+task.id if task else None,'source_time':'unknown','not_authoritative':True}
