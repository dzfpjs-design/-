"""Receipts for manually requested, scheduled and uploaded video; no empty success."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, text, or_, func
from sqlalchemy.orm import Session
from .db import engine
from .models import StreamSession, VideoRun, VideoFrame, VideoAlert, VideoDaily, MediaChannel, Memory, now
from .contracts import digest
from .worker_scope import related

TZ = ZoneInfo('Asia/Shanghai')


def source_rows(db, workspace_id=None):
    cutoff = now() - timedelta(days=8)
    unfinished=select(VideoDaily).where(func.jsonb_array_length(VideoDaily.data['pending_ids'])>0)
    if workspace_id is not None:unfinished=unfinished.where(VideoDaily.workspace_id==workspace_id)
    # Older pending receipts must still change when delayed processing finally finishes.
    retained_ids=[record['id'] for receipt in db.scalars(unfinished) for record in receipt.data['records']]
    captures = select(StreamSession).where(or_(StreamSession.created_at >= cutoff,StreamSession.id.in_(retained_ids)))
    uploads = select(VideoRun).where(or_(VideoRun.created_at >= cutoff,VideoRun.id.in_(retained_ids)))
    if workspace_id is not None:
        captures = captures.where(StreamSession.workspace_id == workspace_id); uploads = uploads.where(VideoRun.workspace_id == workspace_id)
    groups = {}
    def add(workspace, source, name, day, record):
        group = groups.setdefault((workspace, source, day), {'name': name, 'records': []}); group['records'].append(record)
    def record(run, identity, capture_status, mode, evidence_id):
        frames_q = select(VideoFrame).where(VideoFrame.run_id == run.id) if run else None
        alerts_q = select(VideoAlert).where(VideoAlert.run_id == run.id) if run else None
        if run and workspace_id is not None:
            frames_q=frames_q.where(VideoFrame.workspace_id==workspace_id)
            alerts_q=alerts_q.where(VideoAlert.workspace_id==workspace_id)
        frames = db.scalars(frames_q).all() if run else []
        alerts = db.scalars(alerts_q).all() if run else []
        return {'id': identity, 'run_id': run.id if run else None, 'capture_status': capture_status,
            'processing_status': run.status if run else None, 'mode': mode, 'evidence_id': evidence_id,
            'saved_frames': len(frames), 'business_candidates': len(alerts),
            'business_task_ids': sorted(a.data['task_id'] for a in alerts if a.data.get('task_id')),
            'business_target':{k:run.data['source_context']['domain_binding'][k] for k in ['instance_id','instance_release_id','business_id','business_name','entity_id'] if k in run.data['source_context']['domain_binding']} if run and (run.data.get('source_context') or {}).get('domain_binding') else None,
            'source_clock_utc': 'unknown' if not run or not run.data['configuration'].get('recorded_at') else 'user_supplied_unverified'}
    for session in db.scalars(captures):
        run = related(db, VideoRun, session.video_run_id, workspace_id) if session.video_run_id else None
        received = (session.data.get('receipt') or {}).get('received_start_unix')
        # Keep the original attempt cohort; delayed reception must not leave yesterday's
        # pending receipt behind while silently moving the record to another date.
        day = str(session.created_at.astimezone(TZ).date()); channel = related(db, MediaChannel, session.channel_id, workspace_id)
        received_day=str(datetime.fromtimestamp(received,timezone.utc).astimezone(TZ).date()) if received is not None else None
        add(session.workspace_id, 'channel:' + session.channel_id, channel.data['name'], day,
            {**record(run, session.id, session.status, 'automatic' if session.data.get('monitor_id') or session.data.get('continuous_id') else 'manual', session.evidence_id),
             'received_day':received_day,'late_received':bool(received_day and received_day!=day)})
    for run in db.scalars(uploads):
        if run.data.get('source_context'): continue
        add(run.workspace_id, 'uploaded_video', '上传的视频资料', str(run.created_at.astimezone(TZ).date()),
            record(run, run.id, 'uploaded', 'upload', run.evidence_id))
    return groups


def reconcile(db, workspace_id=None):
    from .media_daily_delivery import publish_collection
    db.execute(text('SELECT pg_advisory_xact_lock(734106092)'))
    today = str(now().astimezone(TZ).date())
    for (workspace, source, day), group in source_rows(db, workspace_id).items():
        records = sorted(group['records'], key=lambda x: x['id'])
        pending = [r['id'] for r in records if r['capture_status'] in {'queued','connecting','capturing'} or r['processing_status'] in {'queued','running'}]
        failed = [r['id'] for r in records if r['capture_status'] in {'failed','partial','cancelled'} or r['processing_status'] in {'failed','cancelled'}]
        value = {'source_name': group['name'], 'records': records, 'provisional': day == today,
            'date_basis': 'attempt_or_upload_received_clock', 'source_observation_day_verified': False,
            'pending_ids': pending, 'failed_or_partial_ids': failed, 'not_authoritative': True,
            'manual_attempts': sum(r['mode']=='manual' for r in records), 'automatic_attempts': sum(r['mode']=='automatic' for r in records),
            'uploads': sum(r['mode']=='upload' for r in records), 'unique_originals': len({r['evidence_id'] for r in records if r['evidence_id']}),
            'late_received_records':sum(bool(r.get('late_received')) for r in records),
            'saved_frames': sum(r['saved_frames'] for r in records), 'business_candidates': sum(r['business_candidates'] for r in records),
            'business_tasks': sum(len(r['business_task_ids']) for r in records),
            'unverified_source_clock_records': sum(r['source_clock_utc']!='calibrated' for r in records),
            'status': 'incomplete' if pending or failed else 'collected_with_limits'}
        fingerprint = digest(value)
        row = db.scalar(select(VideoDaily).where(VideoDaily.workspace_id == workspace, VideoDaily.source_key == source,
            VideoDaily.business_day == day).with_for_update())
        if row and row.data.get('receipt_hash') == fingerprint:
            publish_collection(db, row, 'video')
            continue
        if not row:
            row = VideoDaily(workspace_id=workspace, source_key=source, business_day=day); db.add(row); db.flush()
        else: row.version += 1
        memory = Memory(workspace_id=workspace, status='candidate', text=f'{day}视频资料归集：{group["name"]}（含待核项）',
            data={**value, 'type':'video_daily_summary', 'daily_id':row.id, 'receipt_hash':fingerprint, 'source_key':source, 'received_day':day})
        db.add(memory); db.flush()
        row.data = {**value, 'receipt_hash':fingerprint, 'data_memory_id':memory.id, 'updated_at':now().isoformat()}
        publish_collection(db, row, 'video')


def schedule_once(workspace_id=None):
    with Session(engine()) as db:
        if workspace_id is None: reconcile(db)
        else: reconcile(db, workspace_id=workspace_id)
        db.commit()
