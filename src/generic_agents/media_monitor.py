"""Durable sampled stream schedules. Never claim coverage between saved clips."""
from datetime import datetime, timedelta
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .db import engine
from .models import MediaMonitor, MediaChannel, MediaGateway, StreamSession, VideoRun, Job
from .services import scoped, audit
from .local_specialists import ready_detection_install as ready_install

ACTIVE_CAPTURE = ['queued', 'connecting', 'capturing']
OPEN_MONITOR = ['active', 'paused', 'blocked']
MAX_NODE_MONITORS = 2


def node_lock(db):
    # Serialize budget/capacity decisions across workers and workspaces on this DB.
    db.execute(text('SELECT pg_advisory_xact_lock(734106091)'))


def clock(db):
    return db.scalar(select(func.clock_timestamp()))


def queue_capture(db, p, channel, gateway, installation, seconds, configuration, monitor=None, *, create_job=True, read_mode=None, reservation_id=None):
    from .media import CAPTURE_HASH
    from uuid import uuid4
    from .video_read_boundary import reserve, guard
    identity = str(uuid4())
    mode = read_mode or ('automatic' if monitor or not create_job else 'manual')
    reservation = guard(db, p, reservation_id) if reservation_id else reserve(
        db, p, channel, gateway, mode, seconds, 'clip:' + identity)
    receipt = reservation.data['receipt']
    if receipt['request']['channel_id'] != channel.id or receipt['request']['mode'] != mode or receipt['request']['seconds'] < seconds:
        raise HTTPException(409, '读取请求与原预算回执不一致')
    row = StreamSession(id=identity, workspace_id=p.workspace_id, channel_id=channel.id, data={
        'name': channel.data['name'], 'seconds': seconds, 'configuration': configuration,
        'installation_id': installation.id, 'gateway_generation': gateway.data['generation'],
        'channel_generation': channel.data['generation'], 'capture_hash': CAPTURE_HASH,
        'generation': 1, 'error': None, 'deployment': 'local',
        'installation_generation': installation.data['generation'], 'backend': installation.data['backend'],
        'monitor_id': monitor.id if monitor else None,
        'monitor_sequence': monitor.data['attempts'] + 1 if monitor else None,
        'read_reservation_id': reservation.id, 'read_reservation_hash': reservation.data['receipt_hash']})
    from .video_domain import snapshot
    row.data = {**row.data, 'domain_binding': snapshot(db, p, channel)}
    db.add(row); db.flush()
    if create_job:
        job = Job(workspace_id=p.workspace_id, kind='capture_media', idempotency_key='capture:' + row.id,
                  data={'session_id': row.id, 'actor_id': p.user_id})
        db.add(job); db.flush(); row.data = {**row.data, 'job_id': job.id}
    audit(db, p, 'media.capture.queued', row.id, {'monitor_id': row.data['monitor_id']})
    return row


def dependencies(db, p, monitor):
    from .media import CAPTURE_HASH
    from .local_video import PIPELINE_HASH
    channel = scoped(db, MediaChannel, monitor.channel_id, p)
    gateway = scoped(db, MediaGateway, channel.gateway_id, p)
    installation = ready_install(db, p, monitor.data['installation_id'])
    from .video_read_boundary import authority
    from .contracts import digest
    saved = monitor.data.get('read_authority')
    if (not saved or digest(saved) != monitor.data.get('read_authority_hash')
        or authority(db, p, channel, gateway, 'automatic') != saved):
        raise HTTPException(409, '自动读取的已发布业务或设备边界已变化，请保留原计划并重新核对')
    if (not channel.data.get('read_authorized') or channel.status == 'disabled' or gateway.status != 'ready'
        or monitor.data['channel_generation'] != channel.data['generation']
        or monitor.data['gateway_generation'] != gateway.data['generation']
        or monitor.data['installation_generation'] != installation.data['generation']
        or monitor.data['backend'] != installation.data['backend']
        or monitor.data['capture_hash'] != CAPTURE_HASH or monitor.data['pipeline_hash'] != PIPELINE_HASH):
        raise HTTPException(409, '读取授权、连接、本地模型或处理版本已变化，请重新验证并创建监测计划')
    return channel, gateway, installation


def stop_read(db, monitor, reason):
    identity = monitor.data.get('current_session_id')
    row = db.get(StreamSession, identity) if identity else None
    if row and row.status in ACTIVE_CAPTURE:
        row.status = 'cancelled'; row.version += 1
        row.data = {**row.data, 'stop_reason': reason}
    # Already persisted originals remain usable; their queued/running analysis may finish.


def transition(db, p, row, status, reason):
    row.status = status; row.version += 1
    row.data = {**row.data, 'reason': reason, 'updated_at': clock(db).isoformat()}
    if status != 'active': stop_read(db, row, reason)
    audit(db, p, 'media.monitor.' + status, row.id, {'reason': reason})


def reconcile(db, monitor, current_time):
    identity = monitor.data.get('current_session_id')
    session = db.get(StreamSession, identity) if identity else None
    if not session: return False
    if session.status in ACTIVE_CAPTURE: return True
    run = db.get(VideoRun, session.video_run_id) if session.video_run_id else None
    if run and run.status in {'queued', 'running'}: return True
    outcome = {'session_id': session.id, 'sequence': session.data['monitor_sequence'],
               'capture_status': session.status, 'processing_status': run.status if run else None,
               'video_run_id': session.video_run_id, 'evidence_id': session.evidence_id,
               'saved_at': current_time.isoformat(), 'error': session.data.get('error') or (run.data.get('error') if run else None)}
    receipt = session.data.get('receipt') or {}
    # Reception time is a local receive boundary, never a source UTC assertion.
    start, end = receipt.get('received_start_unix'), receipt.get('received_end_unix')
    previous = monitor.data.get('last_received_end_unix')
    if start is not None and previous is not None:
        outcome['unobserved_receive_interval'] = {'start_unix': previous, 'end_unix': start,
            'seconds': max(0, start - previous), 'clock': 'receiver', 'source_utc': 'unknown'}
    success = session.status == 'captured' and run is not None and run.status == 'succeeded'
    monitor.version += 1
    monitor.data = {**monitor.data, 'current_session_id': None,
        'outcomes': [*monitor.data.get('outcomes', []), outcome],
        'successful_segments': monitor.data['successful_segments'] + int(success),
        'consecutive_failures': 0 if success else monitor.data['consecutive_failures'] + 1,
        'last_received_end_unix': end if end is not None else previous,
        'last_error': None if success else outcome['error'] or '片段未完整读取或本地分析未完成'}
    return False


def advance(db, monitor):
    from .worker import actor
    from .media import CAPTURE_HASH
    current_time = clock(db)
    if monitor.status != 'active':
        reconcile(db, monitor, current_time); return
    try:
        p = actor(db, monitor.workspace_id, monitor.actor_id)
    except ValueError:
        from .security import Principal
        p = Principal(monitor.actor_id, monitor.workspace_id, 'viewer', 'scheduler')
        transition(db, p, monitor, 'blocked', '启动人的执行权限已撤销，读取已停止'); return
    try:
        channel, gateway, installation = dependencies(db, p, monitor)
    except (HTTPException, ValueError):
        transition(db, p, monitor, 'blocked', '读取授权、连接、本地模型或处理版本发生变化，请核对后重新验证'); return
    if current_time >= datetime.fromisoformat(monitor.data['deadline']):
        transition(db, p, monitor, 'exhausted', '运行时长已达到上限，停止新的读取');
        reconcile(db, monitor, current_time); return
    if reconcile(db, monitor, current_time): return
    if monitor.data['attempts'] >= monitor.data['max_segments']:
        transition(db, p, monitor, 'exhausted', '读取次数已达到上限；失败尝试也计入上限'); return
    if monitor.data['consecutive_failures'] >= 3:
        transition(db, p, monitor, 'blocked', '连续三个片段未成功，自动读取已停止；请核对后恢复'); return
    due = datetime.fromisoformat(monitor.data['next_due_at'])
    if current_time < due: return
    # One capture at a time per node. Inference finishes before this plan reads again.
    if db.scalar(select(StreamSession.id).where(StreamSession.status.in_(ACTIVE_CAPTURE)).limit(1)):
        return
    if current_time + timedelta(seconds=monitor.data['seconds'] + 20) > datetime.fromisoformat(monitor.data['deadline']):
        transition(db, p, monitor, 'exhausted', '剩余运行时长不足以完成下一段读取'); return
    try:
        session = queue_capture(db, p, channel, gateway, installation, monitor.data['seconds'], monitor.data['configuration'], monitor)
    except HTTPException as error:
        from .video_read_boundary import ReadBudgetWait
        if isinstance(error, ReadBudgetWait):
            monitor.version += 1
            monitor.data = {**monitor.data, 'next_due_at':error.retry_at.isoformat(), 'reason':str(error.detail)}
            return
        transition(db, p, monitor, 'blocked', str(error.detail)); return
    monitor.version += 1
    monitor.data = {**monitor.data, 'attempts': monitor.data['attempts'] + 1,
        'reserved_seconds': monitor.data['reserved_seconds'] + monitor.data['seconds'],
        'current_session_id': session.id, 'next_due_at': (current_time + timedelta(seconds=monitor.data['interval_seconds'])).isoformat(),
        'last_scheduled_at': current_time.isoformat(), 'last_schedule_delay_seconds': max(0, (current_time - due).total_seconds()),
        'reason': '后台正在按已保存计划分段读取；片段之间没有观测覆盖'}


def schedule_once(workspace_id=None):
    """DB clock + transaction locks; restart resumes current rows, no catch-up fiction."""
    with Session(engine(), expire_on_commit=False) as db:
        node_lock(db)
        query = select(MediaMonitor).where(
            (MediaMonitor.status == 'active') | (MediaMonitor.data['current_session_id'].as_string().is_not(None)))
        if workspace_id is not None:query=query.where(MediaMonitor.workspace_id==workspace_id)
        rows = db.scalars(query.order_by(MediaMonitor.created_at).with_for_update()).all()
        rows.sort(key=lambda row: datetime.fromisoformat(row.data['next_due_at']))
        for row in rows: advance(db, row)
        db.commit()
