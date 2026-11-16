"""Durable continuous recording controller; independent from model processing."""
import hashlib
import os
import tempfile
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import ContinuousStream,StreamSession,MediaChannel,MediaGateway,Job,now
from .services import scoped,audit
from .media_monitor import clock,node_lock,queue_capture
from .contracts import digest
from .db import engine

OPEN=['queued','recording','reconnecting','paused','blocked']

class ReadFence(ValueError):
    """A dependency change requires human review, never automatic reconnect."""

def dependencies(db,p,row):
    from .media import CAPTURE_HASH
    from .local_video import PIPELINE_HASH
    from .local_specialists import RECIPE_HASH
    from .models import SpecialistInstallation
    channel=scoped(db,MediaChannel,row.channel_id,p);gateway=scoped(db,MediaGateway,channel.gateway_id,p)
    install=scoped(db,SpecialistInstallation,row.data['installation_id'],p)
    from .video_read_boundary import authority
    saved=row.data.get('read_authority')
    try:
        if not saved or digest(saved)!=row.data.get('read_authority_hash') or authority(db,p,channel,gateway,'automatic')!=saved:
            raise ReadFence('持续读取的已发布业务或设备边界已变化，请保留原读取并重新核对')
    except HTTPException as error:raise ReadFence(str(error.detail)) from error
    if (not channel.data.get('read_authorized') or channel.status=='disabled' or gateway.status!='ready'
        or row.data['channel_generation']!=channel.data['generation'] or row.data['gateway_generation']!=gateway.data['generation']
        or row.data['record_hash']!=RECORD_HASH or row.data['capture_hash']!=CAPTURE_HASH or row.data['pipeline_hash']!=PIPELINE_HASH
        or install.status!='ready' or install.data.get('recipe_hash')!=RECIPE_HASH
        or row.data['installation_generation']!=install.data['generation'] or row.data['backend']!=install.data['backend']):
        raise ReadFence('来源授权、连接或本地模型版本已变化，请重新验证后新建读取')
    return channel,gateway

def reject_source_conflict(db,channel,gateway):
    for row in db.scalars(select(ContinuousStream).where(ContinuousStream.status.in_(OPEN))):
        other=db.get(MediaChannel,row.channel_id);node=db.get(MediaGateway,other.gateway_id)
        if other.path==channel.path and node.data['rtsp_origin']==gateway.data['rtsp_origin']:
            raise HTTPException(409,'这个来源已有未结束的持续读取，请先停止原读取')
RECORD_HASH=digest({name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    for name in ['continuous_media.py','continuous_media_api.py','continuous_capture_runner.py']})


def enqueue(db,row,delay=0):
    row.data={**row.data,'connections':row.data['connections']+1}
    job=Job(workspace_id=row.workspace_id,kind='record_continuous_media',
        idempotency_key=f'continuous:{row.id}:{row.data["generation"]}:{row.data["connections"]}',
        available_at=clock(db)+timedelta(seconds=delay),data={'stream_id':row.id,'actor_id':row.actor_id,'generation':row.data['generation']})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id};return job


def stop_current(db,row):
    session=db.get(StreamSession,row.data.get('current_session_id')) if row.data.get('current_session_id') else None
    if session and session.status in {'queued','connecting','capturing'}:
        session.status='cancelled';session.version+=1
        session.data={**session.data,'error':'持续读取已停止，当前未完成片段没有保存；此前原件保留'}
    row.data={**row.data,'current_session_id':None}

def saved_count(db,row):
    return db.scalar(select(func.count()).select_from(StreamSession).where(StreamSession.workspace_id==row.workspace_id,
        StreamSession.id.in_(row.data['session_ids']),StreamSession.evidence_id.is_not(None)))

def mark_saved(db,p,session,completion,context):
    """Part of the original + VideoRun transaction, before its first commit."""
    row=scoped(db,ContinuousStream,context['continuous_id'],p,True)
    if row.status!='recording' or row.data['generation']!=session.data['continuous_generation'] or row.data.get('current_session_id')!=session.id:
        raise ReadFence('持续读取已停止或由新请求接管，旧窗口未写入')
    row.version+=1;row.data={**row.data,'current_session_id':None,'saved_segments':saved_count(db,row),
        'last_boundary':{'connection_id':context['connection_id'],'last_source_pts':completion['last_source_pts'],
            'source_time_base':completion['source_time_base']},'last_saved_at':now().isoformat()}


def guard(db,p,identity,generation):
    db.expire_all();row=scoped(db,ContinuousStream,identity,p,True)
    if row.status not in {'queued','recording'} or row.data['generation']!=generation:
        raise ReadFence('持续读取已暂停、停止或由新请求接管')
    channel,gateway=dependencies(db,p,row)
    if clock(db)>=datetime.fromisoformat(row.data['deadline']):raise ValueError('持续读取运行时长已到上限')
    current=db.get(StreamSession,row.data.get('current_session_id')) if row.data.get('current_session_id') else None
    if current and current.status=='cancelled':raise ReadFence('当前片段已停止，持续读取连接不再继续')
    if row.status=='recording' and row.data.get('read_reservation_id'):
        from .video_read_boundary import guard as read_guard
        try:read_guard(db,p,row.data['read_reservation_id'])
        except HTTPException as error:raise ReadFence(str(error.detail)) from error
    db.commit();return row,channel,gateway


def process_record(db,p,job):
    from .media import source_state,stream_url,save_capture,validate_completion
    from .local_video import child
    row=scoped(db,ContinuousStream,job.data['stream_id'],p,True);identity=row.id;generation=job.data['generation'];connection_id=str(uuid4())
    if row.data['generation']!=generation or row.status in {'paused','stopped','exhausted','blocked','reconnecting'}:return
    # Recovery discards the incomplete window and counts a fresh session separately.
    if row.data.get('current_session_id'):
        old=db.get(StreamSession,row.data['current_session_id'])
        if old and old.status in {'connecting','capturing','queued'}:
            old.status='failed';old.version+=1;old.data={**old.data,'error':'录制进程中断，此未保存窗口无法补回'}
        elif old and old.evidence_id and old.status in {'captured','partial'}:
            # save_capture commits the original/model job first. A crash before
            # this controller's receipt must reconcile that already durable save.
            row.data={**row.data,'saved_segments':saved_count(db,row)}
        row.data={**row.data,'current_session_id':None,'interruptions':row.data.get('interruptions',0)+1};db.commit()
    try:
        row,channel,gateway=guard(db,p,identity,generation);initial=source_state(gateway,channel)
        row.status='recording';row.version+=1;row.data={**row.data,'reason':'连接保持读取，片段保存后由独立本地分析处理器提取信息'};db.commit()
        remaining=row.data['max_segments']-row.data['attempts']
        if remaining<=0:
            row.status='exhausted';row.data={**row.data,'reason':'片段次数已达到上限'};db.commit();return
        runtime=max(1,(datetime.fromisoformat(row.data['deadline'])-clock(db)).total_seconds()-1)
        from .video_read_boundary import reserve
        try:
            reservation=reserve(db,p,channel,gateway,'automatic',runtime,'continuous:'+identity+':'+connection_id,'continuous',identity,generation)
        except HTTPException as error:raise ReadFence(str(error.detail)) from error
        row.data={**row.data,'read_reservation_id':reservation.id,
            'read_reservation_ids':[*row.data.get('read_reservation_ids',[]),reservation.id]};db.commit()
        with tempfile.TemporaryDirectory(prefix='ga-continuous-') as directory:
            temp=Path(directory);opened=None;closed=0;finished=False;stream_error=None
            def checked():return guard(db,p,identity,generation)
            def receive(value,_):
                nonlocal opened,closed,finished,stream_error
                current,ch,gw=checked();index=value.get('index')
                if value.get('kind')=='segment.open':
                    if opened is not None or index!=closed or current.data['attempts']>=current.data['max_segments']:raise ValueError('片段次序或读取上限不一致')
                    installation=SimpleNamespace(id=current.data['installation_id'],data={'generation':current.data['installation_generation'],'backend':current.data['backend']})
                    session=queue_capture(db,p,ch,gw,installation,current.data['seconds'],current.data['configuration'],create_job=False,
                        reservation_id=current.data['read_reservation_id'])
                    session.status='capturing';session.data={**session.data,'job_id':job.id,'continuous_id':current.id,
                        'continuous_generation':generation,'connection_id':connection_id,'continuous_sequence':current.data['attempts']+1}
                    current.version+=1;current.data={**current.data,'attempts':current.data['attempts']+1,
                        'current_session_id':session.id,'session_ids':[*current.data['session_ids'],session.id]}
                    opened=session.id;db.commit()
                elif value.get('kind')=='segment.progress':
                    if opened is None or index!=closed:raise ValueError('片段进度顺序不一致')
                    elapsed=value.get('seconds')
                    if not isinstance(elapsed,(int,float)) or not 0<=elapsed<=current.data['seconds']+5:raise ValueError('片段进度无效')
                    session=scoped(db,StreamSession,opened,p,True);session.version+=1;session.data={**session.data,'progress':{'seconds':elapsed}};db.commit()
                elif value.get('kind')=='segment.complete':
                    if opened is None or index!=closed or value.get('file')!=f'segment-{index:04d}.mp4':raise ValueError('保存片段顺序或文件不一致')
                    normalized={**value,'kind':'capture.complete','file':'stream.mp4'};validate_completion(normalized,current.data['seconds'])
                    if not isinstance(value.get('last_source_pts'),int) or value['last_source_pts']<value['first_source_pts']:raise ValueError('来源片段边界无效')
                    previous=current.data.get('last_boundary')
                    continuity=bool(previous and previous['connection_id']==connection_id and previous['last_source_pts']==value['first_source_pts']
                        and previous['source_time_base']==value['source_time_base'])
                    if index>0 and not continuity:raise ValueError('相邻来源片段未衔接，不能标记连续')
                    session=scoped(db,StreamSession,opened,p,True)
                    extra={'continuous_id':current.id,'connection_id':connection_id,'continuous_sequence':session.data['continuous_sequence'],
                        'continuous_with_previous':continuity,'boundary_frame_overlap':value['boundary_frame_overlap'],
                        'continuous_record_hash':RECORD_HASH}
                    save_capture(db,p,session,ch,gw,initial,normalized,(temp/value['file']).read_bytes(),extra_context=extra)
                    (temp/value['file']).unlink();opened=None;closed+=1
                    if session.status=='partial':raise ValueError('片段不完整或来源身份变化；重新连接前不能认定视频连续')
                elif value.get('kind')=='stream.complete':
                    if opened is not None or value.get('segments')!=closed:raise ValueError('持续读取完成回执无效')
                    finished=True
                elif value.get('kind')=='stream.failed':
                    codes={'read_timeout':'来源读取超时','stream_ended':'视频来源连接结束','connection_refused':'来源拒绝连接',
                        'video_absent':'来源没有视频轨道','codec_unsupported':'编码或画面尺寸不支持','reordered_codec':'来源使用当前不支持的画面重排',
                        'pts_missing':'来源画面时间戳缺失','dts_missing':'来源解码时间戳缺失','dts_regression':'来源解码时间戳倒退',
                        'timeline_changed':'来源时间线发生变化','keyframe_timeout':'等待关键帧超时','segment_limit':'片段保存限额已达到',
                        'runtime_limit':'持续读取时长已到上限','invalid_stream':'视频数据无法解码','connection_or_codec_error':'视频连接或编码读取失败'}
                    code=value.get('error_code','connection_or_codec_error')
                    if code not in codes:raise ValueError('持续读取错误回执不支持')
                    current.data={**current.data,'last_error_code':code};db.commit()
                    stream_error=codes[code]+'；已保存原件保留，未保存窗口无法补回'
                else:raise ValueError('持续读取回执类型不支持')
            request={'url':stream_url(gateway,channel),'seconds':row.data['seconds'],'segments':remaining,'runtime_seconds':runtime,
                'output_dir':str(temp),'parent_pid':os.getpid(),'lock_fd':None}
            child('continuous_capture_runner.py',request,temp,checked,receive,timeout_seconds=runtime+15)
            current,_,_=checked()
            if not finished or stream_error:raise ValueError(stream_error or '视频连接未完成')
            current.status='exhausted';current.version+=1;current.data={**current.data,'reason':'片段次数已达到上限；已保存原件的本地分析继续完成'};db.commit()
    except (ValueError,HTTPException) as error:
        db.rollback();current=scoped(db,ContinuousStream,identity,p,True)
        if current.data['generation']==generation and current.status in {'queued','recording'}:
            stop_current(db,current)
            expired=clock(db)>=datetime.fromisoformat(current.data['deadline'])
            remaining=current.data['attempts']<current.data['max_segments']
            retry=not isinstance(error,ReadFence) and not expired and remaining and current.data['connections']<4
            current.status='reconnecting' if retry else 'exhausted' if expired or not remaining else 'blocked';current.version+=1
            current.data={**current.data,'last_error':str(error.detail if isinstance(error,HTTPException) else error),
                'reason':'连接中断，等待有限重连；中断时段不视为已监测' if retry else '读取已停止，请核对保存片段和运行条件',
                'reconnect_at':(clock(db)+timedelta(seconds=15)).isoformat()};db.commit()


def schedule_once():
    from .worker import actor
    with Session(engine()) as db:
        node_lock(db)
        for row in db.scalars(select(ContinuousStream).where(ContinuousStream.status=='reconnecting').with_for_update()):
            if clock(db)<datetime.fromisoformat(row.data['reconnect_at']):continue
            try:actor(db,row.workspace_id,row.actor_id)
            except ValueError:row.status='blocked';row.data={**row.data,'reason':'启动人执行权限已撤销'};continue
            if clock(db)>=datetime.fromisoformat(row.data['deadline']):row.status='exhausted';continue
            row.status='queued';row.version+=1;enqueue(db,row)
        db.commit()
