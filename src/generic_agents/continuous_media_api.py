"""Continuous read grants, durable controls and bounded recording budgets."""
from datetime import timedelta,datetime
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field
from sqlalchemy import select
from .contracts import Contract,digest
from .models import ContinuousStream,MediaChannel,MediaGateway,MediaMonitor,StreamSession,VideoRun
from .setup_api import P,DB
from .media_api import view
from .media import validated_channel,CAPTURE_HASH
from .local_video import PIPELINE_HASH
from .local_specialists import ready_detection_install as ready_install
from .services import scoped,require,check_version,audit
from .media_monitor import node_lock,clock,ACTIVE_CAPTURE,OPEN_MONITOR
from .continuous_media import RECORD_HASH,OPEN,enqueue,stop_current
from .video_api import Configuration

router=APIRouter(prefix='/api/media')


class Start(Contract):
    channel_version:int=Field(ge=1)
    installation_id:str
    seconds:int=Field(default=6,ge=4,le=12)
    max_segments:int=Field(default=10,ge=1,le=100)
    runtime_minutes:int=Field(default=10,ge=1,le=60)
    configuration:Configuration=Field(default_factory=Configuration)
    allow_continuous_reads:Literal[True]


@router.get('/continuous')
def streams(p:P,db:DB):
    rows=db.scalars(select(ContinuousStream).where(ContinuousStream.workspace_id==p.workspace_id)
        .order_by(ContinuousStream.created_at.desc()).limit(50)).all()
    totals={x.id:{'analysis_completed':0,'analysis_pending':0,'analysis_failed':0} for x in rows}
    for stream_id,status in db.execute(select(StreamSession.data['continuous_id'].as_string(),VideoRun.status)
        .join(VideoRun,StreamSession.video_run_id==VideoRun.id).where(StreamSession.workspace_id==p.workspace_id,
        VideoRun.workspace_id==p.workspace_id,StreamSession.data['continuous_id'].as_string().in_(totals))):
        key='analysis_completed' if status=='succeeded' else 'analysis_pending' if status in ['queued','running'] else 'analysis_failed'
        totals[stream_id][key]+=1
    return [{**view(x),'channel_id':x.channel_id,**totals[x.id]} for x in rows]


@router.get('/continuous/{identity}/sessions')
def saved_sessions(identity:str,p:P,db:DB):
    row=scoped(db,ContinuousStream,identity,p)
    records=db.execute(select(StreamSession,VideoRun.status).outerjoin(VideoRun,StreamSession.video_run_id==VideoRun.id)
        .where(StreamSession.workspace_id==p.workspace_id,StreamSession.id.in_(row.data['session_ids']))
        .order_by(StreamSession.created_at)).all()
    return [{**view(s),'channel_id':s.channel_id,'evidence_id':s.evidence_id,'video_run_id':s.video_run_id,'processing_status':status}
        for s,status in records]


@router.post('/channels/{identity}/continuous',status_code=201)
def start(identity:str,body:Start,p:P,db:DB):
    require(p,'admin','operator');node_lock(db)
    channel,gateway=validated_channel(db,p,identity);check_version(channel,body.channel_version)
    install=ready_install(db,p,body.installation_id)
    if body.configuration.recorded_at:raise HTTPException(422,'来源拍摄UTC未校准，不接受填写日期冒充来源时间')
    if db.scalar(select(ContinuousStream.id).where(ContinuousStream.status.in_(OPEN))):
        raise HTTPException(409,'本地节点已有未结束的持续读取；请先停止原读取')
    for monitor in db.scalars(select(MediaMonitor).where(MediaMonitor.status.in_(OPEN_MONITOR))):
        other=db.get(MediaChannel,monitor.channel_id);node=db.get(MediaGateway,other.gateway_id)
        if other.path==channel.path and node.data['rtsp_origin']==gateway.data['rtsp_origin']:
            raise HTTPException(409,'来源已有未结束的分段计划，请先停止旧计划')
    for session in db.scalars(select(StreamSession).where(StreamSession.status.in_(ACTIVE_CAPTURE))):
        other=db.get(MediaChannel,session.channel_id);node=db.get(MediaGateway,other.gateway_id)
        if other.path==channel.path and node.data['rtsp_origin']==gateway.data['rtsp_origin']:
            raise HTTPException(409,'来源正在读取，请等待完成或停止')
    current=clock(db)
    from .video_read_boundary import available
    read_authority,_=available(db,p,channel,gateway,'automatic',body.runtime_minutes*60,current)
    row=ContinuousStream(workspace_id=p.workspace_id,channel_id=identity,actor_id=p.user_id,data={
        **body.model_dump(mode='json'),'generation':1,'deadline':(current+timedelta(minutes=body.runtime_minutes)).isoformat(),
        'record_hash':RECORD_HASH,'capture_hash':CAPTURE_HASH,'pipeline_hash':PIPELINE_HASH,
        'channel_generation':channel.data['generation'],'gateway_generation':gateway.data['generation'],
        'installation_generation':install.data['generation'],'backend':install.data['backend'],
        'attempts':0,'saved_segments':0,'connections':0,'session_ids':[],'current_session_id':None,
        'reason':'读取请求已保存，等待录制处理器；模型分析由另一处理器运行','last_error':None,
        'deployment':'local','source_clock_utc':'unknown','cross_segment_tracking':False,'max_auto_reconnects':3,
        'read_authority':read_authority,'read_authority_hash':digest(read_authority),'read_reservation_ids':[]})
    db.add(row);db.flush();enqueue(db,row);audit(db,p,'continuous.started',row.id,{'max_segments':body.max_segments})
    db.commit();return {**view(row),'channel_id':identity}


class Command(Contract):
    version:int=Field(ge=1)


@router.post('/continuous/{identity}/{action}')
def command(identity:str,action:Literal['pause','stop','resume'],body:Command,p:P,db:DB):
    require(p,'admin','operator');node_lock(db);row=scoped(db,ContinuousStream,identity,p,True)
    if action=='resume':
        check_version(row,body.version)
        if row.status not in {'paused','blocked'}:raise HTTPException(409,'只有暂停或受阻的读取可以恢复')
        from .worker import actor
        from .continuous_media import dependencies,ReadFence
        try:actor(db,row.workspace_id,row.actor_id)
        except ValueError:raise HTTPException(409,'原启动人的权限尚未恢复')
        if row.data['attempts']>=row.data['max_segments'] or clock(db)>=datetime.fromisoformat(row.data['deadline']):
            raise HTTPException(409,'原次数或时长上限已达到，不能重置后继续')
        try:dependencies(db,p,row)
        except ReadFence as error:raise HTTPException(409,str(error))
        ready_install(db,p,row.data['installation_id'])
        row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,
            'reason':'恢复读取当前视频，暂停期间的画面不会补造'}
        enqueue(db,row)
    else:
        if body.version>row.version:raise HTTPException(409,'记录版本无效')
        target='paused' if action=='pause' else 'stopped'
        if row.status==target:return view(row)
        if row.status in {'stopped','exhausted'}:raise HTTPException(409,'读取已经结束，历史原件与结果保留')
        stop_current(db,row);row.status=target;row.version+=1
        row.data={**row.data,'reason':'已停止接收；当前未完成片段没有保存，已有原件继续本地分析并保留'}
    audit(db,p,'continuous.'+action,row.id);db.commit();return view(row)
