"""Disposable-only durable file intent; never deletes or authorizes cloud work.

The original task and current data-role authority are still checked by the
caller before file creation. This receipt merely makes a potential shared
content-addressed file discoverable after a transaction rollback. It is not a
safe garbage collector: legacy upload writers do not share its locks.
"""
import hashlib
import copy
from contextvars import ContextVar
from uuid import UUID
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
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

def _unbind_commit_guard(db):
    prior=db.info.pop('v2_file_intent_commit_guard',None)
    if prior is not None and event.contains(db,'before_commit',prior[1]):
        event.remove(db,'before_commit',prior[1])


def _clear_guard_after_root(session, transaction):
    if transaction.parent is None: _unbind_commit_guard(session)


def _clear_guard_after_rollback(session, transaction):
    prior=session.info.get('v2_file_intent_commit_guard')
    current=prior[2] if prior is not None else None
    while current is not None:
        if current is transaction:
            _unbind_commit_guard(session);return
        current=current.parent


def _bind_commit_guard(db, p, task_id, policy_ref):
    # A source read or filesystem operation may cross the expiry boundary.
    # Keep the exact original pinned and recheck the DB clock at commit.
    identity = (p.workspace_id, p.user_id, p.role, p.session_id, task_id, digest(policy_ref))
    prior = db.info.get('v2_file_intent_commit_guard')
    if prior is not None:
        old.need(prior[0] == identity and event.contains(db, 'before_commit', prior[1])); return
    def check(session): _check_current(session, p, task_id, policy_ref)
    if not db.info.get('v2_file_intent_guard_cleanup_installed'):
        event.listen(db,'after_transaction_end',_clear_guard_after_root)
        event.listen(db,'after_soft_rollback',_clear_guard_after_rollback)
        db.info['v2_file_intent_guard_cleanup_installed']=True
    event.listen(db, 'before_commit', check)
    db.info['v2_file_intent_commit_guard'] = (identity, check, db.get_nested_transaction() or db.get_transaction())

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
    _check_current(db, p, task.id, prep['budget']['policy_ref'])
    resolved = sources.resolve_documents(db, p, task, prep['request']['document_source'])
    _check_current(db, p, task.id, prep['budget']['policy_ref'])
    _bind_commit_guard(db, p, task.id, prep['budget']['policy_ref'])
    target = _target(p, task, plan, job, prep, resolved)
    # Reading and validating the source is potentially slow.  It does not
    # write a file or create an intent.  Take the global DDL/index fence only
    # after that read, then recheck the exact unique index before any write.
    # The fence is NOWAIT, so a competing writer fails closed rather than
    # waiting while holding this task's row locks.
    from . import same_goal_advice_intent_index_gate_candidate_v2 as index_gate
    index_gate.require(db, p.workspace_id)
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

# A witness exists only inside the coordinator-owned fresh Session and one
# transaction. It is never accepted from an API, Job payload or db.info.
# This is a trusted internal call boundary, not a defence against arbitrary
# Python code running in this process.
_verified_preflight = ContextVar('advice_v2_file_preflight', default=None)


def _snapshot(db, p, job_id, target, *, lock):
    record = _load(db, p, job_id, target, lock=lock)
    query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == record.id, Audit.action == ACTION)
    if lock: query = query.with_for_update(read=True)
    events = list(db.scalars(query.execution_options(populate_existing=True)))
    old.need(len(events) == 1)
    receipt = events[0]
    return {'record_id': record.id, 'record_version': record.version,
        'record_created': record.created_at.isoformat(), 'record_hash': digest(record.data),
        'receipt_id': receipt.id, 'receipt_version': receipt.version,
        'receipt_created': receipt.created_at.isoformat(), 'receipt_actor': receipt.actor_id,
        'receipt_hash': digest(receipt.data)}


@old.checked
def require_committed(db, p, task, plan, job, prep, resolved):
    """Require exact prior durable originals, never same-transaction inserts."""
    target = _target(p, task, plan, job, prep, resolved)
    from . import same_goal_advice_intent_index_gate_candidate_v2 as index_gate
    index_gate.require(db, p.workspace_id)
    witness = _verified_preflight.get()
    if witness is not None:
        # Only a new Session owned by the coordinator is eligible. Refuse any
        # reuse, savepoint, transaction switch or pending ORM edits.
        old.need(type(witness) is dict and witness.get('session') is db
            and witness.get('transaction') is db.get_transaction()
            and db.get_nested_transaction() is None and witness.get('used') is False
            and witness.get('principal') == p and witness.get('job_id') == job.id
            and witness.get('target') == target and witness.get('poisoned') is False
            and not db.new and not db.dirty and not db.deleted,
            '原资料登记预核对不可复用；未写入文件')
        witness['used'] = True
        old.need(_snapshot(db, p, job.id, target, lock=True) == witness['snapshot'],
            '原资料登记或回执在处理前变化；未写入文件')
        return
    # Existing entry points remain conservative: a second independent Session
    # must see committed state. This path may block with a one-connection pool.
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
