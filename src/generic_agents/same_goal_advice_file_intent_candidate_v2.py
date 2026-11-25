"""Disposable-only durable file intent; never deletes or authorizes cloud work.

The original task and current data-role authority are still checked by the
caller before file creation. This receipt merely makes a potential shared
content-addressed file discoverable after a transaction rollback. It is not a
safe garbage collector: legacy upload writers do not share its locks.
"""
import hashlib
from uuid import UUID
from sqlalchemy import event, func, select
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_sources_v302 as sources
from .contracts import digest
from .models import Audit, Job, Plan
from .relations import lock_current_actor

KIND = 'advice_v2_file_intent'
SCHEMA = 'same-goal-advice-file-intent-v2'
ACTION = 'same_goal_advice.file_intent_candidate_v2'

def _check_current(db, p, task_id, policy_ref):
    """Check only authenticated original policy/session; never renew either."""
    try: UUID(p.session_id)
    except (ValueError, TypeError, AttributeError): old.need(False, '需要有效登录会话，未登记或写入资料')
    old._reader(db, p, write=True); v2._lock_current_reader(db, p)
    row = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.object_id == task_id,
        Audit.id == policy_ref['id'], Audit.action == policy.POLICY).with_for_update(read=True))
    clock = db.scalar(select(func.clock_timestamp()))
    old.need(row is not None and digest(row.data) == policy_ref['record_hash'])
    saved, _ = policy.policy_shape(row, clock)
    old.need(policy.instant(saved['expires_at']) > clock, '原政策已到期；未登记或继续写入资料')

def _bind_commit_guard(db, p, task_id, policy_ref):
    # A source read or filesystem operation may cross the expiry boundary.
    # Keep the exact original pinned and recheck the DB clock at commit.
    identity = (p.workspace_id, p.user_id, p.role, p.session_id, task_id, digest(policy_ref))
    prior = db.info.get('v2_file_intent_commit_guard')
    if prior is not None:
        old.need(prior[0] == identity and event.contains(db, 'before_commit', prior[1])); return
    def check(session): _check_current(session, p, task_id, policy_ref)
    event.listen(db, 'before_commit', check)
    db.info['v2_file_intent_commit_guard'] = (identity, check)

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
    return record, events[0]


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
    _check_current(db, p, task.id, prep['budget']['policy_ref'])
    resolved = sources.resolve_documents(db, p, task, prep['request']['document_source'])
    _check_current(db, p, task.id, prep['budget']['policy_ref'])
    _bind_commit_guard(db, p, task.id, prep['budget']['policy_ref'])
    target = _target(p, task, plan, job, prep, resolved)
    record = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == _key(job.id)).with_for_update())
    if record is not None:
        return _load(db, p, job.id, target, lock=True)[0].id
    record = Job(workspace_id=p.workspace_id, kind=KIND, status='internal', attempts=0,
        idempotency_key=_key(job.id), data={'schema': SCHEMA, 'target': target})
    db.add(record); db.flush()
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, object_id=record.id,
        action=ACTION, data={'schema': SCHEMA, 'intent_hash': digest(record.data)})); db.flush()
    return record.id

@old.checked
def require_committed(db, p, task, plan, job, prep, resolved):
    """DISABLED: subtransaction status is not proof of parent commit.

    pg_xact_status may report a subtransaction committed before its parent
    commits. No alternate proof is installed. Fail closed before any file
    write, even when an intent appears committed on this connection.
    """
    old.need(False, '单连接提交证明已禁用；尚无法确认登记已提交，未写入文件')


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
