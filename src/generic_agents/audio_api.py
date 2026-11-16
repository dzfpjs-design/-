"""Real original → local ASR → durable human correction; no microphone permissions."""
import hashlib,json
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter,UploadFile,File,Form,HTTPException
from fastapi.responses import Response
from pydantic import Field,ValidationError
from sqlalchemy import select,text
from .contracts import Contract,digest
from .setup_api import P,DB
from .models import AudioRun,Job,Memory,Membership
from .security import Principal
from .services import scoped,require,check_version,audit
from .evidence import save_immutable,evidence_bytes
from .local_audio import RECIPE,RECIPE_HASH,ready,checked

router=APIRouter(prefix='/api/audio')

class Configuration(Contract):
    channel:Literal['mono','mix','1','2','3','4','5','6','7','8']='mono'
    language:Literal['auto','zh','en','yue','ja','ko']='auto'

class Version(Contract):version:int=Field(ge=1)
class Review(Version):
    text:str=Field(max_length=12000)
    confirmed:Literal[True]

class ReviewDraft(Version):
    text:str=Field(max_length=12000)

def view(row):return {'id':row.id,'status':row.status,'version':row.version,'evidence_id':row.evidence_id,'request_id':row.request_id,**row.data}

@router.get('/catalog')
def catalog(p:P,db:DB):
    try:ready();available=True;error=None
    except (ValueError,OSError) as e:available=False;error=str(e)
    from .local_audio_install import installed,trace
    try:installation=installed(db,p);workspace_prepared=True;installation_info=trace(installation)
    except HTTPException:workspace_prepared=False;installation_info=None
    return {'workspace_prepared':workspace_prepared,'installation':installation_info,'recipe':RECIPE,'recipe_hash':RECIPE_HASH,'node_prepared':available,'error':error,'max_bytes':20*1024*1024,'max_seconds':60,'default_deployment':'local','quality_verified':False}

@router.get('')
def runs(p:P,db:DB):return [view(r) for r in db.scalars(select(AudioRun).where(AudioRun.workspace_id==p.workspace_id).order_by(AudioRun.created_at.desc()).limit(100))]

@router.get('/requests/{request_id}')
def recovery(request_id:str,p:P,db:DB,expected_hash:str):
    row=db.scalar(select(AudioRun).where(AudioRun.workspace_id==p.workspace_id,AudioRun.request_id==request_id))
    if not row:raise HTTPException(404,'尚无此请求的已保存音频')
    if row.data['request_hash']!=expected_hash:raise HTTPException(409,'原请求的音频或设置不一致，不能替换')
    checked(db,p,row);return view(row)

@router.post('',status_code=201)
async def create(p:P,db:DB,file:UploadFile=File(),configuration:str=Form(),request_id:str=Form()):
    require(p,'admin','operator')
    try:UUID(request_id);config=Configuration.model_validate_json(configuration).model_dump()
    except (ValueError,ValidationError):raise HTTPException(422,'音频请求编号、通道或语言设置无效')
    content=await file.read(20*1024*1024+1)
    if not content or len(content)>20*1024*1024:raise HTTPException(413,'音频为空或超过20MB')
    if file.content_type not in {'audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/wave','audio/flac','audio/x-flac','audio/ogg','audio/mp4','audio/x-m4a'}:raise HTTPException(415,'请选择WAV、MP3、FLAC、OGG或M4A音频')
    if not (content[:4] in {b'RIFF',b'fLaC',b'OggS'} or content[:3]==b'ID3' or len(content)>8 and content[4:8]==b'ftyp' or len(content)>2 and content[0]==255 and content[1]&224==224):raise HTTPException(415,'文件头不是当前支持的本地音频，未解析外部引用')
    sha=hashlib.sha256(content).hexdigest();request_hash=digest({'input_sha256':sha,'configuration':config})
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'audio_request':request_id})[:16],16)-(1<<63)})
    row=db.scalar(select(AudioRun).where(AudioRun.workspace_id==p.workspace_id,AudioRun.request_id==request_id))
    if row:
        if row.data['request_hash']!=request_hash:raise HTTPException(409,'同一音频请求内容变化，请先恢复原请求')
        checked(db,p,row);return view(row)
    try:ready()
    except (ValueError,OSError) as e:raise HTTPException(409,str(e))
    from .local_audio_install import installed,trace
    installation=installed(db,p)
    ev=save_immutable(db,p,content,file.content_type,{'modality':'audio','purpose':'local_transcription','cloud_model_ids':[]})
    evidence_bytes(db,p,ev.id) # Human labor starts only after a checked durable original.
    row=AudioRun(workspace_id=p.workspace_id,request_id=request_id,evidence_id=ev.id,data={'installation':trace(installation),'name':(file.filename or '音频').replace('\\','/').split('/')[-1][:250],'input_sha256':sha,'size_bytes':len(content),'media_type':file.content_type,'configuration':config,'recipe_hash':RECIPE_HASH,'request_hash':request_hash,'generation':1,'deployment':'local','stage':'queued','error':None,'result':None,'result_hash':None,'review':None,'review_history':[]})
    db.add(row);db.flush();job=Job(workspace_id=p.workspace_id,kind='transcribe_audio',idempotency_key='audio:'+row.id+':1',data={'run_id':row.id,'generation':1,'actor_id':p.user_id});db.add(job);db.flush();row.data={**row.data,'job_id':job.id}
    audit(db,p,'audio.queued',row.id,{'input_sha256':sha,'recipe_hash':RECIPE_HASH,'cloud_calls':0});db.commit();return view(row)

@router.get('/{identity}')
def detail(identity:str,p:P,db:DB):
    row=scoped(db,AudioRun,identity,p);checked(db,p,row);return view(row)

@router.post('/{identity}/register-business')
def register_business(identity:str,body:Version,p:P,db:DB):
    from .workflow_audio import register_from_run
    row=scoped(db,AudioRun,identity,p,True);check_version(row,body.version)
    return register_from_run(db,p,row)

@router.get('/{identity}/original')
def original(identity:str,p:P,db:DB,workspace_id:str|None=None):
    if workspace_id and workspace_id!=p.workspace_id:
        m=db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==workspace_id))
        if not m:raise HTTPException(403,'无此工作区音频访问权限')
        p=Principal(p.user_id,m.workspace_id,m.role,p.session_id)
    row=scoped(db,AudioRun,identity,p);ev,content=checked(db,p,row)
    return Response(content,media_type=ev.media_type,headers={'X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff','Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.data['name'])})

@router.post('/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,AudioRun,identity,p,True);check_version(row,body.version)
    if row.data.get('workflow_task_id'):raise HTTPException(409,'请在业务任务中取消处理，以保留授权节点和回执')
    if row.status not in {'queued','running'}:raise HTTPException(409,'音频处理已结束，原件和结果保留')
    row.status='cancelled';row.version+=1;row.data={**row.data,'stage':'cancelled'};audit(db,p,'audio.cancelled',row.id,{});db.commit();return view(row)

@router.post('/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,AudioRun,identity,p,True);check_version(row,body.version)
    if row.data.get('workflow_task_id'):raise HTTPException(409,'业务音频请通过原业务节点重试，不能跳过任务租约')
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'仅失败或取消的处理可重试')
    checked(db,p,row);generation=row.data['generation']+1;row.status='queued';row.version+=1
    job=Job(workspace_id=p.workspace_id,kind='transcribe_audio',idempotency_key=f'audio:{row.id}:{generation}',data={'run_id':row.id,'generation':generation,'actor_id':p.user_id});db.add(job);db.flush()
    row.data={**row.data,'generation':generation,'job_id':job.id,'stage':'queued','error':None};audit(db,p,'audio.retry',row.id,{'generation':generation});db.commit();return view(row)

@router.put('/{identity}/review')
def review(identity:str,body:Review,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,AudioRun,identity,p,True);check_version(row,body.version);checked(db,p,row)
    if row.data.get('workflow_task_id'):raise HTTPException(409,'请在业务核查步骤保存说明，不能用资源页核对代替业务结果')
    if row.status!='succeeded':raise HTTPException(409,'请等待真实本地结果保存后核对文字')
    record_review(db,p,row,body.text)
    db.commit();return view(row)

def record_review(db,p,row,review_text):
    """Caller owns authorization, row/version checks and the transaction."""
    from .models import now
    value={'text':review_text.strip(),'confirmed':True,'actor_id':p.user_id,'saved_at':now().isoformat(),'input_sha256':row.data['input_sha256'],'result_hash':row.data['result_hash']}
    receipt=Memory(workspace_id=p.workspace_id,status='candidate',text='音频“'+row.data['name']+'”的人工核对文字已保存，来源时间和领域效果仍待确认',data={'kind':'audio_transcription_review','source_run_id':row.id,'input_sha256':row.data['input_sha256'],'recipe_hash':RECIPE_HASH,'review':value,'source_time':'unknown','entity_state_updated':False,'cloud_calls':0});db.add(receipt);db.flush()
    previous=row.data['review'];row.data={**row.data,'review':{**value,'candidate_receipt_id':receipt.id},'review_history':row.data['review_history']+([previous] if previous else []),'review_draft':None};row.version+=1
    audit(db,p,'audio.review.saved',row.id,{'candidate_receipt_id':receipt.id,'entity_state_updated':False})

@router.put('/{identity}/review-draft')
def review_draft(identity:str,body:ReviewDraft,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,AudioRun,identity,p,True);check_version(row,body.version);checked(db,p,row)
    if row.data.get('workflow_task_id') or row.status!='succeeded':raise HTTPException(409,'请在已完成的独立音频中核对文字；业务任务仍使用原核查步骤')
    from .models import now
    value={'text':body.text,'actor_id':p.user_id,'saved_at':now().isoformat()}
    old=row.data.get('review_draft')
    if old and old['text']==body.text and old['actor_id']==p.user_id:return view(row)
    row.data={**row.data,'review_draft':value};row.version+=1
    audit(db,p,'audio.review.draft',row.id,{'confirmed':False});db.commit();return view(row)

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):
    row=scoped(db,AudioRun,identity,p);checked(db,p,row)
    if row.status!='succeeded':raise HTTPException(409,'还没有已保存的本地转写结果')
    return {'record':view(row),'recipe':RECIPE,'quality_verified':False,'source_time':'unknown','entity_state_updated':False}
