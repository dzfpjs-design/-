"""Disposable-only durable file intent; never deletes or authorizes cloud work.

The original task and current data-role authority are still checked by the
caller before file creation. This receipt merely makes a potential shared
content-addressed file discoverable after a transaction rollback. It is not a
safe garbage collector: legacy upload writers do not share its locks.
"""
import hashlib
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_sources_v302 as sources
from .contracts import digest
from .models import Audit, Job, Plan
from .relations import lock_current_actor

KIND = 'advice_v2_file_intent'
SCHEMA = 'same-goal-advice-file-intent-v2'
ACTION = 'same_goal_advice.file_intent_candidate_v2'

def _key(job_id):
    return 'advice-v2-file-intent:' + job_id

def _target(p, task, plan, job, prep, resolved):
    raw = resolved['content']; sha = resolved['sha256']
    old.need(type(raw) is bytes and 0 < len(raw) <= 20000 and hashlib.sha256(raw).hexdigest() == sha
        and resolved['size_bytes'] == len(raw) and type(resolved['source_set']['source_snapshot_hash']) is str)
    return {'workspace_id': p.workspace_id, 'task_id': task.id, 'plan_id': plan.id, 'prepare_job_id': job.id,
        'actor_id': p.user_id, 'preparation_hash': prep['record_hash'],
        'selection_hash': digest(prep['request']['document_source']),
        'source_snapshot_hash': resolved['source_set']['source_snapshot_hash'],
        'sha256': sha, 'size_bytes': len(raw), 'storage_key': p.workspace_id + '/' + sha + '.bin'}

def _load(db, p, job_id, target, *, lock):
    query = select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == _key(job_id))
    if lock: query = query.with_for_update(read=True)
    record = db.scalar(query.execution_options(populate_existing=True))
    old.need(record is not None and record.kind == KIND and record.status == 'internal'
        and record.attempts == 0 and record.lease_token is None and record.lease_until is None
        and record.data == {'schema': SCHEMA, 'target': target},
        '原资料文件登记缺失或内容变化；保留原任务，未写文件')
    event_query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == record.id, Audit.action == ACTION)
    if lock: event_query = event_query.with_for_update(read=True)
    events = list(db.scalars(event_query.execution_options(populate_existing=True)))
    old.need(len(events) == 1 and events[0].actor_id == p.user_id
        and events[0].data == {'schema': SCHEMA, 'intent_hash': digest(record.data)},
        '原资料文件登记凭据无法核对；未写文件')
    return record

@old.checked
def register(db, p, task_id, job_id):
    """Record intended bytes; caller must commit before attempting processing."""
    guard.require_disposable_database(db); old._reader(db, p, write=True)
    task = old.existing._task(db, p, task_id, lock=True); lock_current_actor(db, p)
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job_id).with_for_update())
    old.need(job is not None and job.kind == v2.PREPARE_KIND and job.available_at == guard.UNAVAILABLE_TO_GENERAL_WORKERS)
    plan = db.scalar(select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.id == job.data['plan_id'],
        Plan.task_id == task.id).with_for_update())
    old.need(plan is not None)
    prep, checked = v2.read_queued(db, p, task, plan)
    old.need(checked.id == job.id and prep['data_actor_id'] == p.user_id
        and prep['request']['document_source']['kind'] == 'approved_source_set')
    resolved = sources.resolve_documents(db, p, task, prep['request']['document_source'])
    target = _target(p, task, plan, job, prep, resolved)
    record = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == _key(job.id)).with_for_update())
    if record is not None:
        return _load(db, p, job.id, target, lock=True).id
    record = Job(workspace_id=p.workspace_id, kind=KIND, status='internal', attempts=0,
        idempotency_key=_key(job.id), data={'schema': SCHEMA, 'target': target})
    db.add(record); db.flush()
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, object_id=record.id,
        action=ACTION, data={'schema': SCHEMA, 'intent_hash': digest(record.data)})); db.flush()
    return record.id

@old.checked
def require_committed(db, p, task, plan, job, prep, resolved):
    """Require a prior durable intent, never a row inserted in this transaction."""
    target = _target(p, task, plan, job, prep, resolved)
    # A separate connection sees only committed state. A same-transaction
    # flush must never turn an uncommitted intent into file-write authority.
    with Session(bind=db.get_bind(), autoflush=False) as committed:
        _load(committed, p, job.id, target, lock=False)
    _load(db, p, job.id, target, lock=True)

@old.checked
def check_private_directory(p, sha):
    """Reject pre-existing unsafe paths; concurrent outside changes remain a blocker."""
    import os, stat
    from uuid import UUID
    from .config import settings
    old.need(str(UUID(p.workspace_id)) == p.workspace_id and len(sha) == 64
        and all(ch in '0123456789abcdef' for ch in sha))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root = os.open(settings().storage_dir, flags)
        try:
            base = os.fstat(root)
            old.need(base.st_uid == os.geteuid() and stat.S_IMODE(base.st_mode) == 0o700,
                '资料存储目录权限不安全；未写文件')
            try: os.mkdir(p.workspace_id, 0o700, dir_fd=root)
            except FileExistsError: pass
            workspace = os.open(p.workspace_id, flags, dir_fd=root)
            try:
                directory = os.fstat(workspace)
                old.need(directory.st_uid == os.geteuid() and stat.S_IMODE(directory.st_mode) == 0o700,
                    '工作区资料目录权限不安全；未写文件')
                try: existing = os.stat(sha + '.bin', dir_fd=workspace, follow_symlinks=False)
                except FileNotFoundError: existing = None
                if existing is not None:
                    old.need(stat.S_ISREG(existing.st_mode) and existing.st_uid == os.geteuid()
                        and stat.S_IMODE(existing.st_mode) == 0o600,
                        '已存在资料文件权限或类型异常；未写文件')
            finally: os.close(workspace)
        finally: os.close(root)
    except OSError:
        old.need(False, '资料存储目录或原文件无法安全核对；未写文件')
