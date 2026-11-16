"""Local, explicit evidence sufficiency rules; never infer a domain state from media."""
import copy
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,or_
from .contracts import digest
from .models import EvidenceAssessment,Task,Entity,DomainInstance,InstanceRelease,GeoRun,GeoPair,AudioRun,SpeechActivityRun,SegmentedAudioRun,VideoRun,VideoFrame,Memory,Job,Observation,Capability,Invocation,Evidence,WorkflowPixelRun,now
from .services import scoped,require,audit
from .instance_api import active
from .leases import checkpoint

RULE_VERSION='media-evidence-v1'
MODALITIES={'imagery':'影像','audio':'音频','video':'视频','archive':'业务档案','observations':'登记观测数据'}
SOURCE_TYPES={c.__name__:c for c in [VideoFrame,Task,Entity,GeoRun,GeoPair,AudioRun,SpeechActivityRun,SegmentedAudioRun,VideoRun,Observation,Capability,Invocation,Evidence,WorkflowPixelRun]}
MAX_TASKS=200
MAX_SOURCES=300

def signature(row,extended=False):
 value={'version':row.version,'status':getattr(row,'status',None),'data':row.data}
 if extended:
  value['columns']={col.name:getattr(row,col.name).isoformat() if hasattr(getattr(row,col.name),'isoformat') else getattr(row,col.name) for col in row.__table__.columns if col.name!='data' and not col.info.get('typed_payload_internal')}
 return digest(value)

def ref(row,extended=False):return {'type':type(row).__name__,'id':row.id,'signature':signature(row,extended),**({'signature_scope':'row-v2'} if extended else {})}

def checked_request(row):
 if digest(row.data['request'])!=row.data.get('request_hash'):raise HTTPException(409,'原核查条件不一致，请保留请求并核对')
 if row.data.get('question_reference') and digest(row.data['question_reference'])!=row.data.get('question_reference_hash'):raise HTTPException(409,'原问题与确认引用不一致，请保留记录核对')
 return row

def checked(row):
 checked_request(row)
 if 'result' in row.data and digest(row.data['result'])!=row.data.get('result_hash'):raise HTTPException(409,'核查回执不一致，原请求保留，请重新核查')
 return row

def scoped_objects(db,p,instance,entity_ids,*,historical_release_id=None):
 if historical_release_id is None:i,release=active(db,p,instance)
 else:
  i=scoped(db,DomainInstance,instance,p);release=scoped(db,InstanceRelease,historical_release_id,p)
 if not entity_ids or len(entity_ids)!=len(set(entity_ids)):raise HTTPException(422,'请至少选择一个对象，且不能重复')
 rows=[scoped(db,Entity,k,p) for k in entity_ids]
 from .entity_release_compat import require_entity
 for row in rows:require_entity(db,p,i,release,row)
 return i,release,rows

def collect(db,p,request,*,historical=False,source_key=None):
 if 'supplement_frame_v236' in request:
  if source_key is not None:raise HTTPException(409,'补充固定画面不能混用旧机器来源选择器')
  from .frame_supplement_source_v236 import collect as collect_supplement
  return collect_supplement(db,p,request)
 if 'daily_task_frame_v233' in request:
  if source_key is not None:raise HTTPException(409,'固定任务画面不能混用旧机器来源选择器')
  from .daily_frame_assessment_source_v233 import collect as collect_frame
  return collect_frame(db,p,request)
 i,release,objects=scoped_objects(db,p,request['instance_id'],request['entity_ids'],
  **({'historical_release_id':request['release_id']} if historical else {}))
 if release.id!=request['release_id'] or [ref(r) for r in objects]!=request['object_refs']:raise HTTPException(409,'核查期间实例或对象资料发生变化，请保留原请求并重新核查')
 exact=None;source_task=None
 if source_key is not None:
  if not historical:raise HTTPException(422,'精确原来源只用于已保存归集的历史核对')
  try:
   kind,source_id=source_key.split(':',1)
   cls={c.__name__:c for c in [GeoRun,GeoPair,AudioRun,SpeechActivityRun,SegmentedAudioRun,VideoRun]}[kind]
   exact=scoped(db,cls,source_id,p)
   if cls is VideoRun:
    binding=exact.data['source_context']['domain_binding']
    if binding['instance_id']!=i.id or binding['entity_id'] not in request['entity_ids']:raise ValueError()
   else:
    source_task=scoped(db,Task,exact.data['workflow_task_id'],p)
    if source_task.data['instance_id']!=i.id or not set(source_task.data['entity_ids'])&set(request['entity_ids']):raise ValueError()
  except (KeyError,TypeError,ValueError,AttributeError):raise HTTPException(409,'原机器来源与实例或对象关联无法核对') from None
 q=select(Task).where(Task.workspace_id==p.workspace_id,Task.data['instance_id'].as_string()==i.id,
  or_(*(Task.data['entity_ids'].contains([k]) for k in request['entity_ids'])))
 if source_task is not None:q=q.where(Task.id==source_task.id)
 elif exact is not None:q=q.where(Task.data['run_id'].as_string()==exact.id)
 q=q.order_by(Task.created_at.desc()).limit(MAX_TASKS+1)
 tasks=list(db.scalars(q));truncated=len(tasks)>MAX_TASKS;tasks=tasks[:MAX_TASKS];by_id={t.id:t for t in tasks}
 dependencies=[ref(r) for r in objects]+[ref(t) for t in tasks];records=[];ids=list(by_id)
 adapters=[('imagery','segmentation',GeoRun),('imagery','comparison',GeoPair),('audio','transcription',AudioRun),('audio','activity',SpeechActivityRun),('audio','segments',SegmentedAudioRun)]
 for modality,kind,cls in adapters:
  if modality not in request['required_modalities'] or not ids or exact is not None and type(exact) is not cls:continue
  query=select(cls).where(cls.workspace_id==p.workspace_id,cls.data['workflow_task_id'].as_string().in_(ids))
  if exact is not None:query=query.where(cls.id==exact.id)
  rows=list(db.scalars(query.order_by(cls.created_at.desc()).limit(MAX_SOURCES+1)))
  truncated=truncated or len(rows)>MAX_SOURCES
  for row in rows[:MAX_SOURCES]:
   task=by_id[row.data['workflow_task_id']];dependencies.append(ref(row));issue=None
   try:
    if modality=='imagery':
     from .geo_daily import record
     r=record(db,p,kind,row,force=True);times=[{'value':s['observed_at'],'basis':s['time_basis']} for s in r['sources']]
     metrics={'candidate_masks':r['candidate_masks'],'candidate_pixels':r['candidate_pixels'],'unknown_pixels':r['unknown_pixels']}
     source_shas=[s['sha256'] for s in r['sources']]
    else:
     from .audio_daily import record,origin,TZ
     k,root,_=origin(db,p,kind,row);r=record(db,p,kind,row,(k,root),str(root.created_at.astimezone(TZ).date()))
     times=[{'value':None,'basis':'unknown'}];metrics={'saved_asr_segments':r['saved_asr_segments'],'vad_intervals':r['vad_intervals'],'whole_asr_performed':r['whole_asr_performed']};source_shas=[r['input_sha256']]
    valid=r['integrity_verified'];issue=r['integrity_issue'];quality=r.get('quality_gaps',[]);review=r['review_state']
   except (HTTPException,ValueError,KeyError,TypeError,OSError):
    valid=False;issue='原件、固定输入或已保存结果未通过核对。';times=[];metrics={};source_shas=[];quality=[];review='blocked'
   records.append({'key':cls.__name__+':'+row.id,'modality':modality,'kind':kind,'name':row.data.get('name') or task.title,'source_ref':ref(row),'task_id':task.id,'task_title':task.title,
    'entity_ids':[k for k in task.data['entity_ids'] if k in request['entity_ids']],'association_basis':'task_context_not_verified_coverage','status':row.status,'integrity_verified':valid,'integrity_issue':issue,
    'source_times':times,'source_time_verified':False,'source_sha256':source_shas,'quality_gaps':quality,'review_state':review,'metrics':metrics if valid else {},'domain_quality_verified':False})
 if 'video' in request['required_modalities'] and (exact is None or isinstance(exact,VideoRun)):
  from .evidence import evidence_bytes
  vq=select(VideoRun).where(VideoRun.workspace_id==p.workspace_id,VideoRun.data['source_context']['domain_binding']['instance_id'].as_string()==i.id,
   VideoRun.data['source_context']['domain_binding']['entity_id'].as_string().in_(request['entity_ids']))
  if exact is not None:vq=vq.where(VideoRun.id==exact.id)
  vq=vq.order_by(VideoRun.created_at.desc()).limit(MAX_SOURCES+1)
  rows=list(db.scalars(vq));truncated=truncated or len(rows)>MAX_SOURCES
  for row in rows[:MAX_SOURCES]:
   dependencies.append(ref(row));valid=True;issue=None;binding=row.data['source_context']['domain_binding'];frames=list(db.scalars(select(VideoFrame).where(VideoFrame.run_id==row.id)));hashes=[]
   try:
    ev,_=evidence_bytes(db,p,row.evidence_id)
    if ev.sha256!=row.data['input_sha256']:raise ValueError('视频输入原件不一致')
    if row.status=='succeeded' and digest(row.data['result'])!=row.data.get('result_hash'):raise ValueError('视频完成结果不一致')
    hashes.append(ev.sha256)
    for f in frames:
     dependencies.append(ref(f));ev,_=evidence_bytes(db,p,f.evidence_id)
     stable={k:f.data[k] for k in ['frame_sha256','pts','time_base','seconds','width','height','segment','detections','tracks','events','gap_before']}
     if ev.sha256!=f.data['frame_sha256'] or digest(stable)!=f.data['receipt_hash']:raise ValueError('保存画面或候选回执不一致')
     hashes.append(ev.sha256)
   except (HTTPException,OSError,ValueError,KeyError,TypeError):valid=False;issue='视频原件、保存画面或候选回执未通过核对。'
   linked=[t for t in tasks if t.data.get('run_id')==row.id]
   from .audio_daily import task_review
   review='confirmed' if linked and all(task_review(t)=='confirmed' for t in linked) else 'unreviewed'
   records.append({'key':'VideoRun:'+row.id,'modality':'video','kind':'video','name':row.data.get('name','视频分析'),'source_ref':ref(row),'task_id':linked[0].id if linked else None,'task_title':linked[0].title if linked else None,'entity_ids':[binding['entity_id']],
    'association_basis':'configured_device_target_not_verified_coverage','status':row.status,'integrity_verified':valid,'integrity_issue':issue,'source_times':[{'value':row.data['configuration'].get('recorded_at'),'basis':'user_declared_unverified' if row.data['configuration'].get('recorded_at') else 'unknown'}],
    'source_time_verified':False,'source_sha256':hashes if valid else [],'quality_gaps':[],'review_state':review,'metrics':{'saved_frames':len(frames)} if valid else {},'domain_quality_verified':False})
 if source_key is None:
  from .native_pixel_provider_v250 import assessment_records as pixel_records
  extra,refs,cut=pixel_records(db,p,request,tasks)
  records.extend(extra);dependencies.extend(refs);truncated=truncated or cut
 if request.get('source_catalog_version')==2:
  from .mixed_evidence import collect as mixed
  extra,refs,cut=mixed(db,p,request,release,objects)
  originals={r['key']:r for r in records}
  pixel_observations={r['native_observation_id']:r for r in records
   if r.get('kind')=='pixel_comparison' and r.get('native_observation_id')}
  retained=[]
  for r in extra:
   pixel=pixel_observations.get(r.get('source_ref',{}).get('id')) if r.get('source_ref',{}).get('type')=='Observation' else None
   if pixel:
    # Its closed typed receipt was checked above. Do not reinterpret it as an
    # ordinary extractor input, or count the handoff as another machine run.
    pixel['modalities']=['imagery','observations'];pixel['common_observation_ref']=r['source_ref']
    continue
   original=originals.get(r.get('native_source_key'))
   if original:
    original['modalities']=[original['modality'],'observations'];original['common_observation_ref']=r['source_ref']
    original['reported_fields']=r['reported_fields'];original['record_stage']='processed'
    if not r['integrity_verified']:original.update(integrity_verified=False,integrity_issue=r['integrity_issue'],metrics={})
   else:retained.append(r)
  extra=retained
  records.extend(extra);dependencies.extend(refs);truncated=truncated or cut
 return records,dependencies,truncated

def evaluate(request,records,truncated):
 results=[]
 for obj in request['objects']:
  matching=[r for r in records if obj['id'] in r['entity_ids']];gaps=[]
  for m in request['required_modalities']:
   subset=[r for r in matching if r['modality']==m or m in r.get('modalities',[])]
   if not subset:gaps.append({'kind':'missing_saved_processing','modality':m,'message':'尚无关联的'+MODALITIES[m]+'处理记录；可能只有上传或配置，不能据此判断没有异常。'})
  if any(not r['integrity_verified'] for r in matching):gaps.append({'kind':'integrity','message':'有来源或结果不一致，不能作为判断依据。'})
  if any(r['status'] in {'queued','running','received','failed','cancelled'} for r in matching):gaps.append({'kind':'processing','message':'有尚未完成、失败或取消的处理，请先查看原任务。'})
  if any(r['review_state'] not in {'confirmed','not_applicable'} for r in matching):gaps.append({'kind':'human_review','message':'有机器候选或登记观测尚未得到已保存的人工核查；草稿不能替代结论。'})
  # No current adapter certifies acquisition clocks, domain accuracy or coverage.
  # Source reception timestamps and task membership must never fill these gaps.
  if request['goal']=='current_state':
   gaps.extend([{'kind':'source_time','message':'来源采集时间未核实，不能证明在所要求时效窗口内。'},
    {'kind':'coverage','message':'对象关联只表明任务或设备配置，尚不能证明完整观察范围。'},
    {'kind':'domain_quality','message':'专业模型领域效果尚未验收，不能把机器候选直接认定为对象当前状态。'}])
  if any(r['quality_gaps'] for r in matching):gaps.append({'kind':'source_metadata','message':'影像采集时间、地图参考、RGB或范围设置有缺口，请查看原影像依据。'})
  if truncated:gaps.append({'kind':'bounded_scan','message':'达到本次扫描上限，结果不是全部资料。'})
  results.append({'object':obj,'processing_records':sum(r.get('record_stage','processed')=='processed' for r in matching),**({'saved_records':len(matching)} if request.get('source_catalog_version')==2 else {}),'verified_records':sum(r['integrity_verified'] for r in matching),'reviewed_records':sum(r['review_state']=='confirmed' for r in matching),
   'record_keys':[r['key'] for r in matching],'gaps':gaps,'status':'insufficient_for_current_state' if request['goal']=='current_state' else 'evidence_found_with_limits' if matching else 'no_saved_processing',
   'current_state_sufficient':False,'state_conclusion':None})
 return {'rule_version':'mixed-evidence-v2' if request.get('source_catalog_version')==2 else RULE_VERSION,'scope':'selected_instance_objects_saved_records_and_processing' if request.get('source_catalog_version')==2 else 'selected_instance_objects_associated_processing','window_start':request['window_start'],'window_end':request['window_end'],
  'objects':results,'records':records,'truncated':truncated,'cloud_calls':0,'entity_state_updated':False,'experience_published':False,'open_language_question_answered':False}

def process(db,p,job):
 require(p,'admin','operator');row=scoped(db,EvidenceAssessment,job.data['assessment_id'],p,True);checked(row)
 from .frame_supplement_source_v236 import is_record as supplement_record,checked_result as supplement_checked,checked_association as supplement_association
 if supplement_record(db,row):
  supplement_checked(db,p,row)
  bound_job=supplement_association(db,p,row)
  if bound_job.id!=job.id or row.owner_id!=p.user_id:raise HTTPException(409,'补充画面核查与实际后台工作不一致')
 from .daily_frame_assessment_api_v233 import is_frame_record,guard_record
 if is_frame_record(db,row):
  guard_record(db,p,row)
  from .daily_frame_assessment_api_v233 import _associated
  bound,original_job=_associated(db,p,row.data['request']['daily_task_frame_v233']['task_id'],row.id)
  if bound.id!=row.id or original_job.id!=job.id or row.owner_id!=p.user_id:raise HTTPException(409,'原任务画面核查与实际后台工作不一致')
 if row.status in {'completed','cancelled'}:return
 from .assessment_triggers import guard as automatic_guard
 automatic_guard(db,p,row)
 row.status='running';row.version+=1;row.data={**row.data,'progress':'正在核对关联原件与保存结果'};request=copy.deepcopy(row.data['request']);checkpoint(db)
 records,deps,truncated=collect(db,p,request);result=evaluate(request,records,truncated)
 if 'daily_task_frame_v233' in request:
  result.update(rule_version='daily-frame-quality-v233',scope='selected_daily_task_saved_single_frame')
  for item in result['objects']:
   item['gaps']=[gap for gap in item['gaps'] if gap['kind']!='source_metadata']
   item['gaps'].extend(gap for gap in records[0]['daily_frame_result_v233']['gaps']
    if gap['kind'] not in {existing['kind'] for existing in item['gaps']})
 if 'supplement_frame_v236' in request:
  result.update(rule_version='daily-frame-quality-v233',scope='explicit_supplement_saved_single_frame')
  for item in result['objects']:
   item['gaps']=[gap for gap in item['gaps'] if gap['kind']!='source_metadata']
   item['gaps'].extend(gap for gap in records[0]['supplement_frame_result_v236']['gaps']
    if gap['kind'] not in {existing['kind'] for existing in item['gaps']})
 # Metadata is reread after all file checks; cancellation wins before the save.
 db.expire_all();row=scoped(db,EvidenceAssessment,job.data['assessment_id'],p,True)
 if row.status=='cancelled':return
 automatic_guard(db,p,row)
 if 'daily_task_frame_v233' in request:guard_record(db,p,row)
 if 'supplement_frame_v236' in request:supplement_checked(db,p,row)
 for d in deps:
  if d['type']=='SupplementFrameV236':
   from .frame_supplement_source_v236 import recheck_dependency as recheck_supplement
   recheck_supplement(db,p,d)
   continue
  if d['type']=='DailyTaskFrameV233':
   from .daily_frame_assessment_source_v233 import recheck_dependency
   recheck_dependency(db,p,d)
   continue
  source=scoped(db,SOURCE_TYPES[d['type']],d['id'],p)
  if signature(source,d.get('signature_scope')=='row-v2')!=d['signature']:raise HTTPException(409,'核查过程中来源或人工说明改变，请保留请求后重新核查')
 result['dependencies']=deps;row.status='completed';row.version+=1;row.data={**row.data,'result':result,'result_hash':digest(result),'completed_at':now().isoformat(),'progress':'核查结果与缺口已保存'}
 if 'daily_task_frame_v233' in request:
  from .daily_frame_assessment_source_v233 import RESULT_AUDIT,result_proof
  audit(db,p,RESULT_AUDIT,row.id,result_proof(row))
 if 'supplement_frame_v236' in request:
  from .frame_supplement_source_v236 import RESULT as SUPPLEMENT_RESULT,result_proof as supplement_proof
  audit(db,p,SUPPLEMENT_RESULT,row.id,supplement_proof(row))
 audit(db,p,'data.evidence_assessed',row.id,{'records':len(records),'truncated':truncated});checkpoint(db)

def freshness(db,p,row):
 checked(row);changed=[]
 from .daily_frame_assessment_api_v233 import guard_record
 guard_record(db,p,row)
 from .frame_supplement_source_v236 import checked_result as supplement_checked
 supplement_checked(db,p,row)
 for d in row.data.get('result',{}).get('dependencies',[]):
  if d['type']=='SupplementFrameV236':
   from .frame_supplement_source_v236 import recheck_dependency as recheck_supplement
   try:recheck_supplement(db,p,d)
   except (HTTPException,ValueError,KeyError,TypeError,OSError):changed.append(d['id'])
   continue
  if d['type']=='DailyTaskFrameV233':
   from .daily_frame_assessment_source_v233 import recheck_dependency,checked_result
   try:checked_result(db,p,row);recheck_dependency(db,p,d)
   except (HTTPException,ValueError,KeyError,TypeError,OSError):changed.append(d['id'])
   continue
  source=db.get(SOURCE_TYPES[d['type']],d['id'])
  if not source or source.workspace_id!=p.workspace_id or signature(source,d.get('signature_scope')=='row-v2')!=d['signature']:changed.append(d['id'])
 supplement='supplement_frame_v236' in row.data['request'];frame='daily_task_frame_v233' in row.data['request'] or supplement
 dependency_type='SupplementFrameV236' if supplement else 'DailyTaskFrameV233'
 if frame and not any(d.get('type')==dependency_type for d in row.data.get('result',{}).get('dependencies',[])):
  changed.append(row.id)
 return {'metadata_versions_unchanged':not changed,'changed_source_count':len(changed),'physical_files_rechecked':frame and row.status=='completed' and not changed,'new_sources_checked':False,'message':('已重新读取固定画面及原来源、独立接收证明；未查找新增来源，也未认证拍摄时间或对象当前状态。' if frame and not changed else '原固定画面或来源证明已改变、缺失或尚未核查，原结果保留。' if frame else '已核对原结果引用的记录版本；没有重新读取原件或查找新增来源。请另建核查获得最新结果。')}
