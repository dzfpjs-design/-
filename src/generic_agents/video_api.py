"""Local uploaded-video workflow, saved evidence, recoverable jobs and human review."""
import json
from typing import Literal
from datetime import timezone
from urllib.parse import quote
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import Field, ValidationError
from sqlalchemy import select
from .setup_api import P, DB
from .contracts import Contract, digest
from .models import VideoRun, VideoFrame, VideoAlert, Job, Membership, Event, Memory
from .security import Principal
from .services import scoped, require, audit, check_version
from .evidence import save_video_evidence, evidence_bytes
from .local_specialists import ready_detection_install as ready_install
from .local_video import PIPELINE, PIPELINE_HASH
from .specialist_api import Version

router=APIRouter(prefix='/api/videos')


class Presence(Contract):
    label_id: Literal[0,2,15,16]
    seconds: float=Field(ge=.5,le=30,allow_inf_nan=False)


class Configuration(Contract):
    sample_hz: Literal[1,2]=2
    threshold: float=Field(default=.5,ge=.2,le=.8,allow_inf_nan=False)
    recorded_at: str | None=Field(default=None,max_length=80)
    presence_rule: Presence | None=None


def config(value):
    try: body=Configuration.model_validate(json.loads(value))
    except (ValidationError,ValueError): raise HTTPException(422,'视频设置无效，请核对采样、分数与规则')
    if body.recorded_at:
        from .services import timestamp
        body.recorded_at=timestamp(body.recorded_at).astimezone(timezone.utc).isoformat()
    return body.model_dump(mode='json')


def view(row):
    return {'id':row.id,'version':row.version,'status':row.status,'created_at':row.created_at.isoformat(),
            'evidence_id':row.evidence_id,'installation_id':row.installation_id,**row.data}


@router.get('/catalog')
def catalog(p:P):
    return {'pipeline':PIPELINE,'pipeline_hash':PIPELINE_HASH,'source_types':['uploaded_mp4'],
            'streaming_ready':False,'cloud_fallback':False}


@router.get('')
def runs(p:P,db:DB):
    return [view(x) for x in db.scalars(select(VideoRun).where(VideoRun.workspace_id==p.workspace_id)
                                      .order_by(VideoRun.created_at.desc()).limit(50))]


def queue(db,p,installation,ev,name,configuration,retry_of=None,source_context=None):
    from .setup_api import draft
    draft(db,p,True)
    key=digest({'input':ev.sha256,'configuration':configuration,'pipeline':PIPELINE_HASH,
                'installation':installation.id,'generation':installation.data['generation'],
                'backend':installation.data['backend'],'retry_of':retry_of,'actor':p.user_id,'source_context':source_context})
    prior=db.scalar(select(VideoRun).where(VideoRun.workspace_id==p.workspace_id,VideoRun.request_key==key))
    if prior:
        result=view(prior);db.rollback();return result
    row=VideoRun(workspace_id=p.workspace_id,request_key=key,installation_id=installation.id,evidence_id=ev.id,
        data={'name':name,'configuration':configuration,'input_sha256':ev.sha256,'pipeline_hash':PIPELINE_HASH,
              'installation_generation':installation.data['generation'],'backend':installation.data['backend'],
              'generation':1,'stage':'queued','saved_samples':0,'deployment':'local','domain_evaluation':'pending',
              'retry_of':retry_of,'error':None,'source_context':source_context})
    db.add(row);db.flush()
    if source_context:
        from .media import context_guard
        _,_,session=context_guard(db,p,source_context)
        session.video_run_id=row.id;session.version+=1
    job=Job(workspace_id=p.workspace_id,kind='process_video',idempotency_key='video:'+row.id,
            data={'run_id':row.id,'generation':1,'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id}
    audit(db,p,'video.queued',row.id,{'input_sha256':ev.sha256,'cloud_calls':0,'retry_of':retry_of})
    db.commit();return view(row)


@router.post('',status_code=201)
async def upload(p:P,db:DB,installation_id:str=Form(),configuration:str=Form('{}'),file:UploadFile=File()):
    require(p,'admin','operator')
    installation=ready_install(db,p,installation_id)
    configured=config(configuration)
    raw=await file.read(20*1024*1024+1)
    ev=save_video_evidence(db,p,raw,file.content_type or '')
    name=(file.filename or '视频.mp4').replace('\\','/').split('/')[-1][:250]
    return queue(db,p,installation,ev,name,configured)


@router.get('/{object_id}')
def detail(object_id:str,p:P,db:DB):
    row=scoped(db,VideoRun,object_id,p)
    frames=db.scalars(select(VideoFrame).where(VideoFrame.run_id==row.id).order_by(VideoFrame.sample_index)).all()
    alerts=db.scalars(select(VideoAlert).where(VideoAlert.run_id==row.id).order_by(VideoAlert.created_at)).all()
    events=db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind.in_(['video.data','video.presence']),
                                         Event.data['run_id'].as_string()==row.id)).all()
    return {**view(row),'frames':[{'id':f.id,'evidence_id':f.evidence_id,'sample_index':f.sample_index,**f.data} for f in frames],
        'alerts':[{'id':a.id,'version':a.version,'status':a.status,**a.data} for a in alerts],
        'delivery':{'data_received':sum(e.kind=='video.data' and e.status=='received' for e in events),
                    'business_received':sum(e.kind=='video.presence' and e.status=='received' for e in events),
                    'pending':sum(e.status=='pending_delivery' for e in events)}}


@router.get('/{object_id}/original')
def original(object_id:str,p:P,db:DB,workspace_id:str|None=None):
    if workspace_id and workspace_id!=p.workspace_id:
        membership=db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==workspace_id))
        if not membership:raise HTTPException(403,'没有此工作区的访问权限')
        p=Principal(p.user_id,membership.workspace_id,membership.role,p.session_id)
    row=scoped(db,VideoRun,object_id,p);ev,content=evidence_bytes(db,p,row.evidence_id)
    if ev.sha256!=row.data['input_sha256']:raise HTTPException(409,'视频原件摘要不一致')
    return Response(content,media_type='video/mp4',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.data['name']),
                    'X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff'})


@router.post('/{object_id}/cancel')
def cancel(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,VideoRun,object_id,p,True)
    if body.version>row.version:raise HTTPException(409,'记录版本无效，请重新读取')
    if row.status=='cancelled':return view(row)
    if row.status not in {'queued','running'}:raise HTTPException(409,'处理已结束，原件与结果保留')
    row.status='cancelled';row.version+=1;audit(db,p,'video.cancelled',row.id,{});db.commit();return view(row)


@router.post('/{object_id}/retry',status_code=201)
def retry(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,VideoRun,object_id,p,True);check_version(row,body.version)
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的记录可以重试')
    installation=ready_install(db,p,row.installation_id);ev,_=evidence_bytes(db,p,row.evidence_id)
    return queue(db,p,installation,ev,row.data['name'],row.data['configuration'],row.id,source_context=row.data.get('source_context'))


class Review(Contract):
    version:int=Field(ge=1)
    decision:Literal['confirmed','dismissed','draft']
    note:str=Field(min_length=1,max_length=1000)


@router.post('/{object_id}/alerts/{alert_id}/review')
def review(object_id:str,alert_id:str,body:Review,p:P,db:DB):
    require(p,'admin','operator');run=scoped(db,VideoRun,object_id,p)
    if len(body.note.strip()) < (1 if body.decision=='draft' else 5):raise HTTPException(422,'核查说明过短，请补充实际情况和判断依据')
    alert=scoped(db,VideoAlert,alert_id,p,True);check_version(alert,body.version)
    if alert.run_id!=run.id:raise HTTPException(404,'此视频中没有该线索')
    evidence_bytes(db,p,alert.data['evidence_id'])
    if body.decision=='draft':
        alert.version+=1;alert.data={**alert.data,'review_draft':body.note.strip()}
        audit(db,p,'video.alert.note_saved',alert.id,{});db.commit()
        return {'id':alert.id,'version':alert.version,'status':alert.status,**alert.data}
    history=alert.data.get('review_history',[])
    if alert.data.get('review'):history=[*history,alert.data['review']]
    from .models import now
    alert.status=body.decision;alert.version+=1
    alert.data={**alert.data,'review':{'decision':body.decision,'note':body.note.strip(),'actor_id':p.user_id,'at':now().isoformat()},
                'review_history':history,'review_draft':None}
    audit(db,p,'video.alert.reviewed',alert.id,{'decision':body.decision});db.commit()
    return {'id':alert.id,'version':alert.version,'status':alert.status,**alert.data}
