"""Saved local sampled-monitor plans; explicit activation, budgets and recovery."""
from datetime import timedelta, datetime
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select
from .setup_api import P, DB
from .models import MediaMonitor, StreamSession, MediaGateway
from .contracts import Contract
from .services import scoped, require, check_version, audit
from .specialist_api import Version
from .video_api import Configuration
from .media_api import view
from .media import validated_channel, CAPTURE_HASH
from .local_video import PIPELINE_HASH
from .local_specialists import ready_detection_install as ready_install
from .media_monitor import node_lock, clock, dependencies, transition, OPEN_MONITOR, MAX_NODE_MONITORS, ACTIVE_CAPTURE

router = APIRouter(prefix='/api/media')


class Monitor(Contract):
    channel_version: int = Field(ge=1)
    installation_id: str
    seconds: int = Field(default=6, ge=4, le=12)
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    max_segments: int = Field(default=10, ge=1, le=100)
    runtime_minutes: int = Field(default=60, ge=1, le=1440)
    configuration: Configuration = Field(default_factory=Configuration)
    allow_automatic_reads: Literal[True]


@router.get('/monitors')
def monitors(p: P, db: DB):
    return [{**view(x), 'channel_id': x.channel_id} for x in db.scalars(select(MediaMonitor).where(
        MediaMonitor.workspace_id == p.workspace_id).order_by(MediaMonitor.created_at.desc()).limit(50))]


@router.post('/channels/{identity}/monitors', status_code=201)
def create(identity: str, body: Monitor, p: P, db: DB):
    require(p, 'admin', 'operator'); node_lock(db)
    channel, gateway = validated_channel(db, p, identity); check_version(channel, body.channel_version)
    installation = ready_install(db, p, body.installation_id)
    from .continuous_media import reject_source_conflict
    reject_source_conflict(db,channel,gateway)
    if body.configuration.recorded_at: raise HTTPException(422, '来源拍摄UTC未校准，不能填写日期冒充来源时间')
    if body.interval_seconds < body.seconds: raise HTTPException(422, '分段间隔不能小于单段时长')
    existing = db.scalars(select(MediaMonitor).where(MediaMonitor.status.in_(OPEN_MONITOR))).all()
    # Duplicate gateway records must not accidentally monitor the same source twice.
    from .models import MediaChannel
    for row in existing:
        c = db.get(MediaChannel, row.channel_id); g = db.get(MediaGateway, c.gateway_id)
        if c.path == channel.path and g.data['rtsp_origin'] == gateway.data['rtsp_origin']:
            raise HTTPException(409, '这个视频来源已有未结束的自动读取计划，请先恢复或停止原计划')
    if len(existing) >= MAX_NODE_MONITORS: raise HTTPException(409, '本地节点同时保留的监测计划已达上限，请先停止旧计划')
    if db.scalar(select(StreamSession.id).where(StreamSession.channel_id == channel.id, StreamSession.status.in_(ACTIVE_CAPTURE))):
        raise HTTPException(409, '通道正在读取，请等待结束后再启动自动读取')
    current = clock(db)
    from .video_read_boundary import available
    from .contracts import digest
    read_authority, _ = available(db, p, channel, gateway, 'automatic', body.seconds, current)
    row = MediaMonitor(workspace_id=p.workspace_id, channel_id=channel.id, actor_id=p.user_id, data={
        **body.model_dump(mode='json'), 'channel_generation': channel.data['generation'], 'gateway_generation': gateway.data['generation'],
        'installation_generation': installation.data['generation'], 'backend': installation.data['backend'],
        'capture_hash': CAPTURE_HASH, 'pipeline_hash': PIPELINE_HASH, 'deployment': 'local',
        'deadline': (current + timedelta(minutes=body.runtime_minutes)).isoformat(), 'next_due_at': current.isoformat(),
        'attempts': 0, 'reserved_seconds': 0, 'successful_segments': 0, 'consecutive_failures': 0,
        'current_session_id': None, 'outcomes': [], 'reason': '计划已保存，等待后台开始读取',
        'coverage': 'sampled_segments_only', 'source_clock_utc': 'unknown', 'continuous_recording': False,
        'authorized_at': current.isoformat(), 'last_error': None,
        'read_authority': read_authority, 'read_authority_hash': digest(read_authority)})
    db.add(row); db.flush(); audit(db, p, 'media.monitor.started', row.id, {'max_segments': body.max_segments, 'cloud_calls': 0})
    db.commit(); return {**view(row), 'channel_id': channel.id}


@router.post('/monitors/{identity}/pause')
def pause(identity: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator'); node_lock(db); row = scoped(db, MediaMonitor, identity, p, True)
    if body.version > row.version: raise HTTPException(409, '记录版本无效，请重新读取')
    if row.status == 'paused': return view(row)
    if row.status != 'active': raise HTTPException(409, '当前计划没有运行，请核对保存状态')
    transition(db, p, row, 'paused', '已暂停新的读取；正在读取的片段已停止，保存的原件继续保留并分析')
    db.commit(); return view(row)


@router.post('/monitors/{identity}/stop')
def stop(identity: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator'); node_lock(db); row = scoped(db, MediaMonitor, identity, p, True)
    if body.version > row.version: raise HTTPException(409, '记录版本无效，请重新读取')
    if row.status == 'stopped': return view(row)
    if row.status == 'exhausted': raise HTTPException(409, '计划已达到上限；原记录保留，可创建新计划')
    transition(db, p, row, 'stopped', '已停止此计划；原视频、失败记录和核查结果保留')
    db.commit(); return view(row)


@router.post('/monitors/{identity}/resume')
def resume(identity: str, body: Version, p: P, db: DB):
    require(p, 'admin', 'operator'); node_lock(db); row = scoped(db, MediaMonitor, identity, p, True); check_version(row, body.version)
    if row.status not in {'paused', 'blocked'}: raise HTTPException(409, '只有暂停或受阻的计划可以恢复')
    from .worker import actor
    try: actor(db, row.workspace_id, row.actor_id)
    except ValueError: raise HTTPException(409, '原启动人的执行权限尚未恢复，不能由另一人静默接管授权')
    dependencies(db, p, row)
    current = clock(db)
    if current >= datetime.fromisoformat(row.data['deadline']) or row.data['attempts'] >= row.data['max_segments']:
        raise HTTPException(409, '原计划已达到次数或运行时长上限，请创建新计划')
    # Recovery reads current live data; old missed intervals are not fabricated/backfilled.
    row.data = {**row.data, 'next_due_at': current.isoformat(), 'consecutive_failures': 0}
    transition(db, p, row, 'active', '已恢复，将读取当前新片段；暂停期间的画面没有补回')
    db.commit(); return view(row)
