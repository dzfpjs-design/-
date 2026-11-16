"""Original audio → local speech candidates → durable human review, without ASR conflation."""
import hashlib,json
from typing import Literal
from uuid import UUID
from urllib.parse import quote
from fastapi import APIRouter,HTTPException,UploadFile,File,Form
from fastapi.responses import Response
from pydantic import Field,ValidationError
from sqlalchemy import select,text
from .setup_api import P,DB
from .contracts import Contract,digest
from .models import SpeechActivityRun,Job,Memory,Membership,now
from .security import Principal
from .services import scoped,require,check_version,audit
from .evidence import save_immutable,evidence_bytes
from .local_vad import RECIPE,RECIPE_HASH,ready,checked
from .local_vad_install import installed,trace
router=APIRouter(prefix='/api/audio-activity')
class Configuration(Contract):
 channel:Literal['mono','mix','1','2','3','4','5','6','7','8']='mono'
 threshold:float=Field(default=.5,ge=.1,le=.9,allow_inf_nan=False)
class Version(Contract):version:int=Field(ge=1)
class ReviewDraft(Version):
 decisions:list[Literal['speech','not_speech','uncertain']]=Field(max_length=250)
 note:str=Field(max_length=5000)
class Review(ReviewDraft):
 decisions:list[Literal['speech','not_speech','uncertain']]=Field(max_length=250)
 note:str=Field(max_length=5000)
 confirmed:Literal[True]
def view(row):return {'id':row.id,'status':row.status,'version':row.version,'request_id':row.request_id,'evidence_id':row.evidence_id,**row.data}
@router.get('/catalog')
def catalog(p:P,db:DB):
 try:ready();runtime=True;error=None
 except (ValueError,OSError) as e:runtime=False;error=str(e)
 try:install=installed(db,p);workspace=True;info=trace(install)
 except HTTPException:workspace=False;info=None
 return {'recipe':RECIPE,'recipe_hash':RECIPE_HASH,'node_prepared':runtime,'workspace_prepared':workspace,'installation':info,'error':error,'max_bytes':20*1024*1024,'max_seconds':60,'quality_verified':False,'cloud_fallback':False}
@router.get('')
def runs(p:P,db:DB):return [view(r) for r in db.scalars(select(SpeechActivityRun).where(SpeechActivityRun.workspace_id==p.workspace_id,SpeechActivityRun.data['workflow_task_id'].astext.is_(None)).order_by(SpeechActivityRun.created_at.desc()).limit(100))]
@router.get('/requests/{request_id}')
def recovery(request_id:UUID,expected_hash:str,p:P,db:DB):
 row=db.scalar(select(SpeechActivityRun).where(SpeechActivityRun.workspace_id==p.workspace_id,SpeechActivityRun.request_id==str(request_id)))
 if not row:raise HTTPException(404,'尚无已保存的语音区间请求，请用原音频及原设置重试')
 if row.data['request_hash']!=expected_hash:raise HTTPException(409,'原音频或VAD设置已变化，不替换原请求')
 checked(db,p,row);return view(row)
@router.post('',status_code=201)
async def create(p:P,db:DB,file:UploadFile=File(),configuration:str=Form(),request_id:UUID=Form()):
 require(p,'admin','operator')
 try:config=Configuration.model_validate_json(configuration).model_dump()
 except (ValueError,ValidationError):raise HTTPException(422,'VAD通道或门槛无效')
 content=await file.read(20*1024*1024+1)
 if not content or len(content)>20*1024*1024:raise HTTPException(413,'请选择非空且不超过20MB的音频')
 media=file.content_type
 if media not in {'audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/wave','audio/flac','audio/x-flac','audio/ogg','audio/mp4','audio/x-m4a'}:raise HTTPException(415,'请选择WAV、MP3、FLAC、OGG或M4A')
 if not (content[:4] in {b'RIFF',b'fLaC',b'OggS'} or content[:3]==b'ID3' or len(content)>8 and content[4:8]==b'ftyp' or len(content)>2 and content[0]==255 and content[1]&224==224):raise HTTPException(415,'文件头不是受支持的音频格式')
 sha=hashlib.sha256(content).hexdigest();request_hash=digest({'input_sha256':sha,'configuration':config});identity=str(request_id)
 db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'vad_request':identity})[:16],16)-(1<<63)})
 row=db.scalar(select(SpeechActivityRun).where(SpeechActivityRun.workspace_id==p.workspace_id,SpeechActivityRun.request_id==identity))
 if row:
  if row.data['request_hash']!=request_hash:raise HTTPException(409,'同一请求的音频或设置变化，请先核对原记录')
  checked(db,p,row);return view(row)
 install=installed(db,p);ev=save_immutable(db,p,content,media,{'modality':'audio','purpose':'local_speech_activity','cloud_model_ids':[]});evidence_bytes(db,p,ev.id)
 row=SpeechActivityRun(workspace_id=p.workspace_id,request_id=identity,evidence_id=ev.id,data={'name':(file.filename or '音频').replace('\\','/').split('/')[-1][:250],'media_type':media,'input_sha256':sha,'size_bytes':len(content),'installation':trace(install),'recipe_hash':RECIPE_HASH,'configuration':config,'request_hash':request_hash,'generation':1,'stage':'queued','deployment':'local','error':None,'result':None,'result_hash':None,'review':None,'review_draft':None,'review_history':[]});db.add(row);db.flush();job=Job(workspace_id=p.workspace_id,kind='detect_speech_activity',idempotency_key='vad:'+row.id+':1',data={'run_id':row.id,'generation':1,'actor_id':p.user_id});db.add(job);db.flush();row.data={**row.data,'job_id':job.id};audit(db,p,'audio.activity.queued',row.id,{'input_sha256':sha,'cloud_calls':0});db.commit();return view(row)
@router.get('/{identity}')
def detail(identity:str,p:P,db:DB):
 row=scoped(db,SpeechActivityRun,identity,p);checked(db,p,row);return view(row)
@router.get('/{identity}/original')
def original(identity:str,p:P,db:DB,workspace_id:str|None=None):
 if workspace_id and workspace_id!=p.workspace_id:
  m=db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==workspace_id))
  if not m:raise HTTPException(403,'没有此工作区的音频访问权限')
  p=Principal(p.user_id,m.workspace_id,m.role,p.session_id)
 row=scoped(db,SpeechActivityRun,identity,p);ev,content=checked(db,p,row)
 return Response(content,media_type=ev.media_type,headers={'X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff','Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.data['name'])})
@router.post('/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SpeechActivityRun,identity,p,True);check_version(row,body.version)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请通过原业务任务取消语音处理')
 if row.status not in {'queued','running'}:raise HTTPException(409,'处理已经结束，原音频及结果保留')
 row.status='cancelled';row.version+=1;row.data={**row.data,'stage':'cancelled'};audit(db,p,'audio.activity.cancelled',row.id,{});db.commit();return view(row)
@router.post('/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SpeechActivityRun,identity,p,True);check_version(row,body.version)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请通过原业务节点重试语音处理')
 if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只能重试失败或取消的原请求')
 checked(db,p,row);installation=installed(db,p,row.data['installation']['installation_id'])
 if trace(installation)!=row.data['installation']:raise HTTPException(409,'当前安装来源或代次已变化，原记录保留，请重新核对后建立新请求')
 generation=row.data['generation']+1;job=Job(workspace_id=p.workspace_id,kind='detect_speech_activity',idempotency_key='vad:'+row.id+':'+str(generation),data={'run_id':row.id,'generation':generation,'actor_id':p.user_id});db.add(job);db.flush();row.status='queued';row.version+=1;row.data={**row.data,'generation':generation,'stage':'queued','job_id':job.id,'error':None};audit(db,p,'audio.activity.retry',row.id,{'generation':generation});db.commit();return view(row)
@router.put('/{identity}/review')
def review(identity:str,body:Review,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SpeechActivityRun,identity,p,True);check_version(row,body.version);checked(db,p,row)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请在业务任务中核查，不能用资源页替代业务回执')
 if row.status!='succeeded' or len(body.decisions)!=len(row.data['result']['activity']['intervals']):raise HTTPException(409,'请在真实已保存区间上逐项核对，不能更换区间或数量')
 value={'decisions':body.decisions,'note':body.note,'confirmed':True,'actor_id':p.user_id,'saved_at':now().isoformat(),'input_sha256':row.data['input_sha256'],'result_hash':row.data['result_hash']}
 receipt=Memory(workspace_id=p.workspace_id,status='candidate',text='音频语音区间的人工核查已保存；不等于环境声音或对象状态',data={'kind':'speech_activity_review','source_run_id':row.id,'review':value,'entity_state_updated':False,'source_time':'unknown','cloud_calls':0});db.add(receipt);db.flush();value['candidate_receipt_id']=receipt.id
 old=row.data['review'];row.data={**row.data,'review':value,'review_draft':None,'review_history':row.data['review_history']+([old] if old else [])};row.version+=1;audit(db,p,'audio.activity.review.saved',row.id,{'candidate_receipt_id':receipt.id,'entity_state_updated':False});db.commit();return view(row)
@router.put('/{identity}/review-draft')
def review_draft(identity:str,body:ReviewDraft,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,SpeechActivityRun,identity,p,True);check_version(row,body.version);checked(db,p,row)
 if row.data.get('workflow_task_id'):raise HTTPException(409,'请在业务任务中保存核查草稿')
 if row.status!='succeeded' or len(body.decisions)!=len(row.data['result']['activity']['intervals']):raise HTTPException(409,'请在已保存的语音区间上核对')
 value={'decisions':body.decisions,'note':body.note,'actor_id':p.user_id,'saved_at':now().isoformat()}
 old=row.data.get('review_draft')
 if old and old['decisions']==body.decisions and old['note']==body.note and old['actor_id']==p.user_id:return view(row)
 row.data={**row.data,'review_draft':value};row.version+=1;audit(db,p,'audio.activity.review.draft',row.id,{'confirmed':False});db.commit();return view(row)
@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):
 row=scoped(db,SpeechActivityRun,identity,p);checked(db,p,row)
 if row.status!='succeeded':raise HTTPException(409,'还没有完成真实本地语音区间处理')
 return {'record':view(row),'recipe':RECIPE,'domain_quality_verified':False,'environment_classifier_used':False,'entity_state_updated':False,'source_time':'unknown'}
