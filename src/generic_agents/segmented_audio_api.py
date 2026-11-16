"""Selected saved VAD intervals → local ASR checkpoints → human review receipts."""
import json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field,StrictInt
from sqlalchemy import select,text
from .setup_api import P,DB
from .contracts import Contract,digest
from .models import SegmentedAudioRun,Job,Memory,now
from .services import scoped,require,check_version,audit
from .local_segmented import RECIPE,RECIPE_HASH,source_value,capture,installations,valid_configuration,checked
router=APIRouter(prefix='/api/audio-segments')
class Configuration(Contract):
 indexes:list[StrictInt]=Field(max_length=250)
 language:Literal['auto','zh','yue','en','ja','ko']='auto'
class Create(Contract):
 request_id:UUID
 source_run_id:str
 source_snapshot_hash:str=Field(pattern='^[a-f0-9]{64}$')
 configuration:Configuration
class Version(Contract):version:int=Field(ge=1)
class ReviewDraft(Version):
 texts:list[str]=Field(max_length=250)
 note:str=Field(max_length=5000)
class Review(ReviewDraft):confirmed:Literal[True]
def view(row):return {'id':row.id,'source_run_id':row.source_run_id,'status':row.status,'version':row.version,'request_id':row.request_id,**row.data}
@router.get('/catalog')
def catalog(p:P,db:DB):
 try:trace=installations(db,p);prepared=True;error=None
 except (ValueError,HTTPException,OSError) as e:trace=None;prepared=False;error=str(e.detail) if isinstance(e,HTTPException) else str(e)
 return {'prepared':prepared,'installations':trace,'error':error,'recipe':RECIPE,'recipe_hash':RECIPE_HASH,'cloud_fallback':False,'domain_quality_verified':False}
@router.get('/source/{identity}')
def source(identity:str,p:P,db:DB):
 s,_=source_value(db,p,identity);return {'source':s,'source_snapshot_hash':digest(s)}
@router.get('')
def rows(p:P,db:DB,source_run_id:str|None=None):
 q=select(SegmentedAudioRun).where(SegmentedAudioRun.workspace_id==p.workspace_id,SegmentedAudioRun.data['workflow_task_id'].astext.is_(None))
 if source_run_id:q=q.where(SegmentedAudioRun.source_run_id==source_run_id)
 return [view(r) for r in db.scalars(q.order_by(SegmentedAudioRun.created_at.desc()).limit(100))]
@router.get('/requests/{identity}')
def recover(identity:UUID,expected_hash:str,p:P,db:DB):
 row=db.scalar(select(SegmentedAudioRun).where(SegmentedAudioRun.workspace_id==p.workspace_id,SegmentedAudioRun.request_id==str(identity)))
 if not row:raise HTTPException(404,'尚未查到分段转写请求，请按原区间和设置重试')
 if row.data['request_hash']!=expected_hash:raise HTTPException(409,'分段转写原请求或设置已变化')
 checked(db,p,row);return view(row)
def create_record(db,p,identity,source_id,expected_hash,config,queue=True):
 require(p,'admin','operator');s,_=source_value(db,p,source_id)
 from .models import SpeechActivityRun
 if scoped(db,SpeechActivityRun,source_id,p).data.get('workflow_task_id'):raise HTTPException(409,'业务原音频请通过原任务节点处理，不能在资源页另建请求')
 if digest(s)!=expected_hash:raise HTTPException(409,'语音区间来源已变化，不能替换原请求')
 try:valid_configuration(s,config)
 except ValueError as e:raise HTTPException(422,str(e))
 request_hash=digest({'source_snapshot_hash':expected_hash,'configuration':config});db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'segment_request':identity})[:16],16)-(1<<63)})
 row=db.scalar(select(SegmentedAudioRun).where(SegmentedAudioRun.workspace_id==p.workspace_id,SegmentedAudioRun.request_id==identity))
 if row:
  if row.source_run_id!=source_id or row.data['request_hash']!=request_hash:raise HTTPException(409,'同一分段请求不能更换来源、区间或语言')
  checked(db,p,row);return row
 traces=installations(db,p,s)
 row=SegmentedAudioRun(workspace_id=p.workspace_id,request_id=identity,source_run_id=source_id,data={'name':s['name'],'source':s,'source_snapshot_hash':expected_hash,'recipe_hash':RECIPE_HASH,'configuration':config,'request_hash':request_hash,'installations':traces,'generation':1,'stage':'queued','segments':[],'checkpoint_hash':digest([]),'error':None,'result':None,'result_hash':None,'review':None,'review_draft':None,'review_history':[]});db.add(row);db.flush()
 if queue:
  job=Job(workspace_id=p.workspace_id,kind='transcribe_speech_segments',idempotency_key='audio-segments:'+row.id+':1',data={'run_id':row.id,'generation':1,'actor_id':p.user_id});db.add(job);db.flush();row.data={**row.data,'job_id':job.id}
 audit(db,p,'audio.segments.queued',row.id,{'cloud_calls':0,'selected_count':len(config['indexes'])});db.commit();return row
@router.post('',status_code=201)
def create(body:Create,p:P,db:DB):return view(create_record(db,p,str(body.request_id),body.source_run_id,body.source_snapshot_hash,body.configuration.model_dump()))
@router.get('/{identity}')
def detail(identity:str,p:P,db:DB):
 row=scoped(db,SegmentedAudioRun,identity,p);checked(db,p,row);return view(row)
@router.post('/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SegmentedAudioRun,identity,p,True);check_version(row,body.version)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请通过业务任务取消，以保留授权节点和已有段')
 if row.status not in {'queued','running'}:raise HTTPException(409,'本次处理已结束，已有转写段和原件保留')
 row.status='cancelled';row.version+=1;row.data={**row.data,'stage':'cancelled'};audit(db,p,'audio.segments.cancelled',row.id,{});db.commit();return view(row)
@router.post('/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SegmentedAudioRun,identity,p,True);check_version(row,body.version)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请通过原业务节点重试，不能跳过任务租约')
 if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只能恢复失败或取消的原分段请求')
 s,_=checked(db,p,row)
 if installations(db,p,s)!=row.data['installations']:raise HTTPException(409,'本地安装版本已变化，原段保留，请核对模型来源')
 generation=row.data['generation']+1;job=Job(workspace_id=p.workspace_id,kind='transcribe_speech_segments',idempotency_key='audio-segments:'+row.id+':'+str(generation),data={'run_id':row.id,'generation':generation,'actor_id':p.user_id});db.add(job);db.flush();row.status='queued';row.version+=1;row.data={**row.data,'generation':generation,'stage':'queued','error':None,'job_id':job.id};db.commit();return view(row)
def review_value(row,body,p):
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请在业务核查步骤保存说明，资源页核查不能替代业务回执')
 if row.status!='succeeded' or len(body.texts)!=len(row.data['segments']) or any(len(t)>6000 for t in body.texts) or sum(len(t) for t in body.texts)>12000:raise HTTPException(422,'请逐段核对已保存的文字，总文字不超过12000字')
 return {'texts':body.texts,'note':body.note,'actor_id':p.user_id,'saved_at':now().isoformat()}
@router.put('/{identity}/review-draft')
def draft_review(identity:str,body:ReviewDraft,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SegmentedAudioRun,identity,p,True);check_version(row,body.version);checked(db,p,row);value=review_value(row,body,p);old=row.data.get('review_draft')
 if old and old['texts']==body.texts and old['note']==body.note and old['actor_id']==p.user_id:return view(row)
 row.data={**row.data,'review_draft':value};row.version+=1;audit(db,p,'audio.segments.review.draft',row.id,{'confirmed':False});db.commit();return view(row)
@router.put('/{identity}/review')
def review(identity:str,body:Review,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SegmentedAudioRun,identity,p,True);check_version(row,body.version);checked(db,p,row);value=review_value(row,body,p);value.update(confirmed=True,input_sha256=row.data['source']['input_sha256'],source_snapshot_hash=row.data['source_snapshot_hash'],result_hash=row.data['result_hash'])
 receipt=Memory(workspace_id=p.workspace_id,status='candidate',text='分段音频人工文字核查；原始采集时间与领域准确率未验证',data={'kind':'segmented_audio_review','source_run_id':row.id,'review':value,'entity_state_updated':False,'cloud_calls':0});db.add(receipt);db.flush();value['candidate_receipt_id']=receipt.id;old=row.data['review'];row.data={**row.data,'review':value,'review_draft':None,'review_history':row.data['review_history']+([old] if old else [])};row.version+=1;audit(db,p,'audio.segments.review.saved',row.id,{'candidate_receipt_id':receipt.id,'entity_state_updated':False});db.commit();return view(row)
@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):
 row=scoped(db,SegmentedAudioRun,identity,p);checked(db,p,row)
 return {'record':view(row),'pipeline':RECIPE,'partial':row.status!='succeeded','word_timestamps':False,'domain_quality_verified':False,'cloud_calls':0,'entity_state_updated':False}

class Register(Contract):
 source_run_id:str
 source_snapshot_hash:str=Field(pattern='^[a-f0-9]{64}$')
@router.post('/register')
def register_cap(body:Register,p:P,db:DB):
 from .segmented_audio_capability import register_pipeline
 cap=register_pipeline(db,p,body.source_run_id,body.source_snapshot_hash);db.commit();return {'capability_id':cap.id,'pipeline_hash':RECIPE_HASH,'deployment':'local','backend':'cpu'}

@router.post('/register-business')
def register_business(body:Register,p:P,db:DB):
 from .workflow_segmented_audio import register_from_source
 cap=register_from_source(db,p,body.source_run_id,body.source_snapshot_hash)
 return {'capability_id':cap.id,'pipeline_hash':RECIPE_HASH,'deployment':'local','backend':'cpu'}
