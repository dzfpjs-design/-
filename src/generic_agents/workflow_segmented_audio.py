"""Task-owned VAD and resumable ASR, fenced by the existing workflow lease."""
import copy
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import Manifest,digest
from .models import Task,Job,Capability,SpeechActivityRun,SegmentedAudioRun
from .services import scoped,require,register,validate_capability,publish,audit
from . import local_segmented as segments,local_vad as vad
from .workflow_audio import manifest as whole_manifest,verify,ref

ADAPTER='local_task_segmented_audio'

def manifest(db,p):
 traces=segments.installations(db,p);base=whole_manifest().model_dump()
 base.update(name='vad_sensevoice_task_audio',version='1.0.'+str(int(digest({'recipe':segments.RECIPE_HASH,'installations':traces})[:12],16)),
  description='业务任务原音频本地语音检测和分段转写；使用任务固定通道/语言，保留200ms上下文与已完成段，不识别业务异常。')
 base['execution']={'adapter':ADAPTER,'recipe_hash':segments.RECIPE_HASH,'installations':traces,'backend':'cpu','side_effect':False,'input_bindings':{'audio_ref':'task_audio_ref','task_id':'task_identifier'}}
 base['output_schema']={'type':'object','required':['attributes','anomalies','segmented_audio','segmented_run_id','speech_activity_run_id'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'segmented_audio':{'type':'object'},'segmented_run_id':{'type':'string'},'speech_activity_run_id':{'type':'string'}}}
 base['specification'].update(vad_used=True,context_ms=200,interval_selection='all_machine_candidates',vad_threshold=.5,license_chain='pending')
 return Manifest.model_validate(base)

def validate_manifest(db,p,value):
 if value.model_dump()!=manifest(db,p).model_dump():raise HTTPException(422,'业务分段能力必须锁定任务原件、两套本地安装及只读权限')

def register_from_source(db,p,source_id,expected):
 require(p,'admin');source,_=segments.source_value(db,p,source_id)
 row=scoped(db,SpeechActivityRun,source_id,p)
 if row.data.get('workflow_task_id') or digest(source)!=expected:raise HTTPException(409,'请使用独立且来源未变化的真实语音检测记录登记业务能力')
 value=manifest(db,p)
 db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'segmented_task_cap':value.version})[:16],16)-(1<<63)})
 cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==value.namespace,Capability.name==value.name,Capability.release_version==value.version))
 if cap:
  if cap.status!='published' or cap.data!=value.model_dump():raise HTTPException(409,'已登记业务分段版本不一致，原记录保留')
 else:
  cap=register(db,p,value);validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'本地业务候选；无外发、设备操作或对象状态写入权限')
 audit(db,p,'audio.segments.workflow_registered',cap.id,{'source_run_id':source_id});db.commit();return cap

def local_inputs(db,p,task,step,inputs):
 if step['execution']['adapter']!=ADAPTER:return
 verify(db,p,task)
 if inputs.get('task_id')!=task.id or inputs.get('audio_ref')!=ref(task):raise HTTPException(409,'业务分段不能替换任务原音频、通道或语言')

def guard_task(db,p,row):
 if not row.data.get('workflow_task_id'):return row
 if row.data.get('workflow_graph_scope'):
  from .workflow_graph_scope import guard_record
  row=guard_record(db,p,row)
 from .leases import ownership
 task=scoped(db,Task,row.data['workflow_task_id'],p);owned=ownership(db)
 if task.status=='workflow_cancelled':
  row.status='cancelled';row.version+=1;row.data={**row.data,'stage':'cancelled_with_business'};db.commit();raise ValueError('业务已取消，原音频与已完成转写段保留')
 if not row.data.get('workflow_graph_scope') and (task.status!='workflow_running' or task.data['workflow_runtime'].get('automatic',{}).get('job_id')!=row.data['workflow_job_id'] or owned.get('job_id')!=row.data['workflow_job_id']):raise ValueError('业务节点或租约已变化，分段结果不接受')
 saved=task.data['audio_snapshots'][0]
 if row.data['input_audio_id']!=saved['id'] or row.data['input_snapshot_hash']!=saved['snapshot_hash']:raise ValueError('任务原音频引用变化')
 from .models import SpecialistInstallation
 cap=scoped(db,Capability,row.data['workflow_capability_id'],p)
 if cap.content_hash!=row.data['workflow_capability_hash'] or digest(cap.data)!=cap.content_hash:raise ValueError('处理期间业务分段能力版本变化')
 for trace in cap.data['execution']['installations'].values():
  install=scoped(db,SpecialistInstallation,trace['installation_id'],p)
  if install.status!='ready' or install.data['generation']!=trace['generation'] or install.data['recipe_hash']!=trace['recipe_hash']:raise ValueError('处理期间业务分段安装版本变化')
 return row

def verify_result(db,p,task,row):
 if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
  from .workflow_graph_scope import record_node
  record_node(db,p,task,row)
 source,_=segments.checked(db,p,row);verify(db,p,task);saved=task.data['audio_snapshots'][0];s=saved['snapshot'];v=scoped(db,SpeechActivityRun,row.source_run_id,p)
 if 'graph_runtime' in task.data:
  record_node(db,p,task,v)
  if v.data['workflow_graph_scope']!=row.data['workflow_graph_scope']:raise HTTPException(409,'语音检测和分段必须属于同一图节点')
 for r in (row,v):
  if r.status!='succeeded' or r.data.get('workflow_task_id')!=task.id or r.data.get('input_audio_id')!=saved['id'] or r.data.get('input_snapshot_hash')!=saved['snapshot_hash']:raise HTTPException(409,'分段结果不是本任务已保存的原音频')
 if v.evidence_id!=s['evidence_id'] or source['input_sha256']!=s['input_sha256'] or v.data['configuration']!={'channel':s['configuration']['channel'],'threshold':.5} or row.data['configuration']!={'indexes':list(range(len(source['intervals']))),'language':s['configuration']['language']}:raise HTTPException(409,'业务语音检测或分段设置与任务不一致')
 # The published capability hash is recorded independently of mutable resource-page review.
 cap=scoped(db,Capability,row.data['workflow_capability_id'],p)
 if cap.content_hash!=row.data['workflow_capability_hash'] or digest(cap.data)!=cap.content_hash or row.data['installations']!=cap.data['execution']['installations'] or source['installation']!=row.data['installations']['vad']:raise HTTPException(409,'业务分段安装来源或能力版本不一致')
 return source

def local_result(db,p,task,step,inputs,output):
 if step['execution']['adapter']!=ADAPTER:return
 row=scoped(db,SegmentedAudioRun,output['segmented_run_id'],p);verify_result(db,p,task,row)
 if 'graph_runtime' in task.data:
  from .workflow_graph_scope import record_node
  record_node(db,p,task,row,step)
 if row.data['workflow_capability_id']!=step['execution']['capability_id'] or output['segmented_audio']!=row.data['result'] or output['speech_activity_run_id']!=row.source_run_id or output.get('anomalies') or output['attributes']!={'segmented_text_candidates':[s['text'] for s in row.data['segments']],'not_authoritative':True}:raise HTTPException(409,'业务分段回执与真实机器结果不一致')

def verify_receipts(db,p,task):
 for receipt in task.data['workflow_runtime'].get('receipts',[]):
  out=receipt.get('output',{})
  if out.get('segmented_run_id'):
   row=scoped(db,SegmentedAudioRun,out['segmented_run_id'],p);verify_result(db,p,task,row)
   if out['segmented_audio']!=row.data['result'] or out['speech_activity_run_id']!=row.source_run_id:raise HTTPException(409,'业务分段回执与原保存结果不一致')

def resume(db,row,job):
 if row.status=='succeeded':return
 if row.status=='cancelled':raise HTTPException(409,'业务分段请求已取消，原件与已有段保留')
 row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'workflow_job_id':job.id,'job_id':job.id,'error':None};db.commit()

def execute(db,p,cap,inputs,key):
 from .leases import ownership
 require(p,'admin','operator');validate_manifest(db,p,Manifest.model_validate(cap.data));task=scoped(db,Task,inputs['task_id'],p);job_id=ownership(db).get('job_id');job=db.get(Job,job_id) if job_id else None
 from .workflow_graph_scope import owned_graph_job,graph_context,metadata,record_node
 graph_job=owned_graph_job(db,p);graph_meta={}
 if graph_job:
  task,job,node,activation_scope=graph_context(db,p,key,cap)
  if inputs!=job.data['inputs']:raise HTTPException(409,'图分段输入与冻结节点不一致')
  graph_meta=metadata(task,job,node,activation_scope,cap)
 elif not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or task.status!='workflow_running':raise HTTPException(409,'业务分段只允许在本任务已授权节点租约中执行')
 local_inputs(db,p,task,{'execution':cap.data['execution']},inputs);saved=task.data['audio_snapshots'][0];s=saved['snapshot'];traces=cap.data['execution']['installations']
 # Attempt number and job ID are excluded: recovery resumes this same logical node.
 scope=digest({'scope':activation_scope,'capability_hash':cap.content_hash,'inputs':inputs}) if graph_job else digest({'task_id':task.id,'step_id':job.data['scope']['step_id'],'visit':job.data['scope']['visit'],'capability_hash':cap.content_hash,'inputs':inputs})
 meta={'workflow_task_id':task.id,'workflow_job_id':job.id,'job_id':job.id,'input_audio_id':saved['id'],'input_snapshot_hash':saved['snapshot_hash'],'workflow_capability_id':cap.id,'workflow_capability_hash':cap.content_hash,**graph_meta}
 v=db.scalar(select(SpeechActivityRun).where(SpeechActivityRun.workspace_id==p.workspace_id,SpeechActivityRun.request_id==scope).with_for_update())
 if v:
  if graph_job:record_node(db,p,task,v)
  resume(db,v,job)
 else:
  config={'channel':s['configuration']['channel'],'threshold':.5}
  v=SpeechActivityRun(workspace_id=p.workspace_id,request_id=scope,evidence_id=s['evidence_id'],data={**meta,'name':s['name'],'media_type':s['media_type'],'input_sha256':s['input_sha256'],'size_bytes':s['size_bytes'],'installation':traces['vad'],'recipe_hash':vad.RECIPE_HASH,'configuration':config,'request_hash':digest({'input_sha256':s['input_sha256'],'configuration':config}),'generation':1,'stage':'queued','deployment':'local','error':None,'result':None,'result_hash':None,'review':None,'review_draft':None,'review_history':[]});db.add(v);db.commit()
 if v.status!='succeeded':vad.process(db,p,SimpleNamespace(data={'run_id':v.id,'generation':v.data['generation']}));db.refresh(v)
 source,_=segments.source_value(db,p,v.id)
 if segments.installations(db,p,source)!=traces:raise HTTPException(409,'业务分段的两套安装已变化')
 config={'indexes':list(range(len(source['intervals']))),'language':s['configuration']['language']}
 row=db.scalar(select(SegmentedAudioRun).where(SegmentedAudioRun.workspace_id==p.workspace_id,SegmentedAudioRun.request_id==scope).with_for_update())
 if row:
  if graph_job:record_node(db,p,task,row)
  segments.checked(db,p,row)
  if row.data['configuration']!=config or row.data['installations']!=traces:raise HTTPException(409,'恢复分段请求的原设置或安装不一致')
  resume(db,row,job)
 else:
  row=SegmentedAudioRun(workspace_id=p.workspace_id,request_id=scope,source_run_id=v.id,data={**meta,'name':s['name'],'source':source,'source_snapshot_hash':digest(source),'recipe_hash':segments.RECIPE_HASH,'configuration':config,'request_hash':digest({'source_snapshot_hash':digest(source),'configuration':config}),'installations':traces,'generation':1,'stage':'queued','segments':[],'checkpoint_hash':digest([]),'error':None,'result':None,'result_hash':None,'review':None,'review_draft':None,'review_history':[]});db.add(row);db.commit()
 if row.status!='succeeded':segments.process(db,p,SimpleNamespace(data={'run_id':row.id,'generation':row.data['generation']}));db.refresh(row)
 verify_result(db,p,task,row)
 return {'attributes':{'segmented_text_candidates':[s['text'] for s in row.data['segments']],'not_authoritative':True},'anomalies':[],'segmented_audio':copy.deepcopy(row.data['result']),'segmented_run_id':row.id,'speech_activity_run_id':v.id}
