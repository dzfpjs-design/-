"""Disposable-only explicit data-role delegation for one advice preparation.

An authenticated data-role user may grant or revoke local document preparation
for one sealed Plan and source selection.  The grant never authorizes model
calls, sending, automatic renewal, or a different task.  A worker must hold a
real lease and recheck the user's current role, grant, policy and source in each
transaction.  No route or general worker is registered by this experiment.
"""
from uuid import UUID, uuid4
from datetime import timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from . import leases
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_document_lease_candidate_v2 as control
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_unlimited_policy_v2 as policy
from .contracts import digest
from .models import Audit, Job, Plan, uid
from .security import Principal

SCHEMA = 'same-goal-advice-offline-document-grant-v2'
GRANT = 'same_goal_advice.offline_document_grant_candidate_v2'
REVOKE = 'same_goal_advice.offline_document_revocation_candidate_v2'


def _uuid(value):
    old.need(type(value) is str)
    try: old.need(str(UUID(value)) == value)
    except (ValueError, AttributeError): old.need(False)
    return value


def _http_actor(db, p):
    old.need(type(p) is Principal and p.role in {'admin', 'operator'})
    _uuid(p.session_id)
    old.need(v2._lock_current_reader(db, p), '需要有效的资料岗位登录会话')


def _live_policy(db, p, task_id, ref):
    row = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task_id, Audit.id == ref['id'], Audit.action == policy.POLICY)
        .with_for_update(read=True).execution_options(populate_existing=True))
    clock = db.scalar(select(func.clock_timestamp()))
    old.need(row is not None and digest(row.data) == ref['record_hash'])
    saved, _ = policy.policy_shape(row, clock)
    old.need(policy.instant(saved['expires_at']) > clock, '原政策已到期，不能继续准备资料')
    return saved['expires_at']


def _original(db, p, task_id, job_id, *, allow_prepared=False, require_data_actor=True):
    task = old.existing._task(db, p, task_id, lock=True)
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job_id)
        .with_for_update().execution_options(populate_existing=True))
    old.need(job is not None and job.kind == v2.PREPARE_KIND)
    plan = db.scalar(select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.task_id == task.id,
        Plan.id == job.data['plan_id']).with_for_update().execution_options(populate_existing=True))
    old.need(plan is not None)
    prep, checked = (v2.read_prepared(db, p, task, plan) if allow_prepared and job.status == 'succeeded'
        else v2.read_queued(db, p, task, plan))
    old.need(checked.id == job.id and (not require_data_actor or prep['data_actor_id'] == p.user_id))
    return task, plan, job, prep


def _grant_data(event, prep, task, plan, job):
    d = event.data
    old.need(type(d) is dict and set(d) == {'schema','request_id','task_id','plan_id','job_id',
        'actor_id','requested_by','preparation_hash','selection_hash','source_hash','policy_ref',
        'expires_at','record_hash'} and d['schema'] == SCHEMA)
    _uuid(d['request_id']); _uuid(d['actor_id'])
    content = {k: v for k, v in d.items() if k != 'record_hash'}
    old.need(d['record_hash'] == digest(content) and event.object_id == plan.id
        and event.actor_id == d['actor_id'] == prep['data_actor_id']
        and d['requested_by'] == prep['requested_by'] and d['task_id'] == task.id
        and d['plan_id'] == plan.id and d['job_id'] == job.id
        and d['preparation_hash'] == prep['record_hash']
        and d['selection_hash'] == digest(prep['request']['document_source'])
        and d['policy_ref'] == prep['budget']['policy_ref'])
    return d


def _revocations(db, p, grant):
    rows = old._rows(db, p, Audit, Audit.action == REVOKE, Audit.object_id == grant.id)
    old.need(len(rows) <= 1)
    if rows:
        r = rows[0]; d = r.data
        old.need(type(d) is dict and set(d) == {'schema','request_id','grant_id','grant_hash','reason','actor_id','actor_role'}
            and d['schema'] == SCHEMA and d['grant_id'] == grant.id
            and d['actor_id'] == r.actor_id and d['actor_role'] in {'admin', 'operator'}
            and (d['actor_id'] == grant.actor_id or d['actor_role'] == 'admin')
            and d['grant_hash'] == digest(grant.data) and type(d['reason']) is str and 0 < len(d['reason']) <= 500)
        _uuid(d['request_id'])
    return rows


@old.checked
def issue(db, p, task_id, job_id, request_id):
    """Issue one grant only from the real current data-role HTTP session."""
    guard.require_disposable_database(db); _http_actor(db, p); _uuid(request_id)
    task, plan, job, prep = _original(db, p, task_id, job_id)
    old.need(p.user_id == prep['data_actor_id'])
    expires = _live_policy(db, p, task.id, prep['budget']['policy_ref'])
    resolved = sources.resolve_documents(db, p, task, prep['request']['document_source'])
    source_hash = digest({'selection': resolved['approved_selection'],
        'source_snapshot_hash': resolved['source_set']['source_snapshot_hash'],
        'sha256': resolved['sha256'], 'size_bytes': resolved['size_bytes']})
    rows = old._rows(db, p, Audit, Audit.action == GRANT, Audit.object_id == plan.id)
    for existing in rows:
        value = _grant_data(existing, prep, task, plan, job)
        revoked = _revocations(db, p, existing)
        if value['request_id'] == request_id:
            old.need(not revoked and value['source_hash'] == source_hash and value['expires_at'] == expires,
                '原授权已撤销或资料变化，请重新核对')
            return {'grant_id': existing.id, 'record_hash': digest(value), 'replayed': True}
        old.need(bool(revoked) or policy.instant(value['expires_at']) <= db.scalar(select(func.clock_timestamp())),
            '原资料授权仍有效，请先撤销或沿用原授权')
    value = {'schema': SCHEMA, 'request_id': request_id, 'task_id': task.id, 'plan_id': plan.id,
        'job_id': job.id, 'actor_id': p.user_id, 'requested_by': prep['requested_by'],
        'preparation_hash': prep['record_hash'], 'selection_hash': digest(prep['request']['document_source']),
        'source_hash': source_hash, 'policy_ref': prep['budget']['policy_ref'], 'expires_at': expires}
    value['record_hash'] = digest(value)
    row = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=plan.id,
        action=GRANT, data=value); db.add(row); db.flush()
    # Natural expiry and session expiry can cross while preparing source.
    def before_commit(session):
        _http_actor(session, p); _live_policy(session, p, task.id, prep['budget']['policy_ref'])
        current = sources.resolve_documents(session, p, task, prep['request']['document_source'])
        current_hash = digest({'selection': current['approved_selection'],
            'source_snapshot_hash': current['source_set']['source_snapshot_hash'],
            'sha256': current['sha256'], 'size_bytes': current['size_bytes']})
        old.need(current_hash == source_hash, '授权资料已变化，未保存委托')
    event.listen(db, 'before_commit', before_commit)
    return {'grant_id': row.id, 'record_hash': digest(value), 'replayed': False}


@old.checked
def revoke(db, p, task_id, job_id, grant_id, request_id, reason):
    """Keep the original grant and source; revoke only this local delegation."""
    guard.require_disposable_database(db); _http_actor(db, p); _uuid(request_id); _uuid(grant_id)
    old.need(type(reason) is str and 0 < len(reason.strip()) <= 500); reason = reason.strip()
    task, plan, job, prep = _original(db, p, task_id, job_id, allow_prepared=True, require_data_actor=False)
    old.need(p.user_id == prep['data_actor_id'] or p.role == 'admin', '只有原资料岗位或管理员可撤销授权')
    row = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == plan.id, Audit.id == grant_id, Audit.action == GRANT).with_for_update())
    old.need(row is not None); _grant_data(row, prep, task, plan, job)
    existing = _revocations(db, p, row)
    body = {'schema': SCHEMA, 'request_id': request_id, 'grant_id': row.id,
        'grant_hash': digest(row.data), 'reason': reason, 'actor_id': p.user_id, 'actor_role': p.role}
    if existing:
        old.need(existing[0].actor_id == p.user_id and existing[0].data == body)
        return {'grant_id': row.id, 'revoked': True, 'replayed': True}
    db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        object_id=row.id, action=REVOKE, data=body)); db.flush()
    event.listen(db, 'before_commit', lambda session: _http_actor(session, p))
    return {'grant_id': row.id, 'revoked': True, 'replayed': False}


def _check_worker(db, p, task_id, job_id, grant_id):
    """Verify an actual existing worker lease and current local delegation.

    `p` must be the exact actor installed by the worker on the fenced Session.
    This function does not itself create a job, file, receipt or cloud call.
    """
    guard.require_disposable_database(db)
    old.need(type(p) is Principal and p.session_id == 'worker'
        and db.info.get('worker_actor') is p and leases.fenced(db))
    owned = leases.ownership(db); _uuid(owned.get('job_id')); _uuid(grant_id)
    ctl = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == owned['job_id'],
        Job.kind == control.KIND).with_for_update().execution_options(populate_existing=True))
    old.need(ctl is not None and ctl.status == 'running' and ctl.lease_token == owned['lease_token']
        and ctl.lease_until > db.scalar(select(func.clock_timestamp())) and ctl.data['actor_id'] == p.user_id
        and ctl.data['target']['job_id'] == job_id and ctl.data['target']['task_id'] == task_id)
    return _check_grant(db, p, ctl, task_id, job_id, grant_id)


def _check_grant(db, p, ctl, task_id, job_id, grant_id):
    # The control audit, actor/role and same original Plan are rechecked under
    # the actual control->Task lock order; revoked user sessions are irrelevant
    # only because explicit durable delegation replaces session authority.
    control._control(db, p, ctl.id)
    old.need(ctl.data['actor_id'] == p.user_id and ctl.data['target']['job_id'] == job_id
        and ctl.data['target']['task_id'] == task_id)
    v2._lock_current_reader(db, p)
    task, plan, job, prep = _original(db, p, task_id, job_id, allow_prepared=True)
    row = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.id == grant_id,
        Audit.object_id == plan.id, Audit.action == GRANT).with_for_update(read=True))
    old.need(row is not None); value = _grant_data(row, prep, task, plan, job)
    old.need(not _revocations(db, p, row), '资料岗位已撤销授权')
    expires = _live_policy(db, p, task.id, value['policy_ref'])
    old.need(value['expires_at'] == expires and policy.instant(expires) > db.scalar(select(func.clock_timestamp())))
    resolved = sources.resolve_documents(db, p, task, prep['request']['document_source'])
    source_hash = digest({'selection': resolved['approved_selection'],
        'source_snapshot_hash': resolved['source_set']['source_snapshot_hash'],
        'sha256': resolved['sha256'], 'size_bytes': resolved['size_bytes']})
    old.need(source_hash == value['source_hash'], '授权后的原资料已变化，未继续准备')
    return {'grant_id': row.id, 'plan_id': plan.id, 'job_id': job.id, 'source_hash': source_hash,
        'requested_by': prep['requested_by']}


@old.checked
def check_worker(db, p, task_id, job_id, grant_id):
    """Validate the original durable delegation and bind a commit-time recheck.

    This candidate is not a permit to call a model, execute a different Job,
    or bypass the worker's lease. It remains unwired until source and actor
    checks are integrated atomically with the actual document handler.
    """
    result = _check_worker(db, p, task_id, job_id, grant_id)
    identity = (p.workspace_id, p.user_id, p.role, task_id, job_id, grant_id,
        leases.ownership(db)['job_id'], leases.ownership(db)['lease_token'])
    prior = db.info.get('offline_document_grant_guard')
    if prior is not None:
        old.need(prior[0] == identity and event.contains(db, 'before_commit', prior[1]))
    else:
        def before_commit(session): _check_worker(session, p, task_id, job_id, grant_id)
        event.listen(db, 'before_commit', before_commit)
        db.info['offline_document_grant_guard'] = (identity, before_commit)
    return result


@old.checked
def process_packet(db, p, control_id, token, grant_id):
    """Process one already-approved packet using a fenced delegated worker.

    This is deliberately not registered as a worker entry point or route. It
    creates no source file and cannot send to a model. Source-set generation
    remains blocked until its file durability and pool issues are resolved.
    """
    old.need(leases.fenced(db) and leases.ownership(db) == {'job_id': control_id, 'lease_token': token})
    ctl = control._control(db, p, control_id)
    target = ctl.data['target']
    check_worker(db, p, target['task_id'], target['job_id'], grant_id)
    from . import same_goal_advice_prepare_document_candidate_v2 as document
    return document.process(db, p, target['task_id'], target['job_id'], delegation_id=grant_id)


@old.checked
def claim_worker(db, control_id, grant_id):
    """Claim a dedicated control Job without borrowing a user's HTTP session.

    Worker identity comes from the durable control record and the existing
    database-backed worker actor factory. Caller-supplied user identities are
    never accepted. This disposable-only function is not a public route.
    """
    from . import worker as runtime_worker
    guard.require_disposable_database(db); _uuid(control_id); _uuid(grant_id)
    ctl = db.scalar(select(Job).where(Job.id == control_id, Job.kind == control.KIND)
        .with_for_update().execution_options(populate_existing=True))
    old.need(ctl is not None and type(ctl.data) is dict and type(ctl.data.get('target')) is dict)
    p = runtime_worker.actor(db, ctl.workspace_id, ctl.data.get('actor_id'))
    old.need(type(p) is Principal and p.session_id == 'worker')
    target = ctl.data['target']; task_id, job_id = target['task_id'], target['job_id']
    proof = _check_grant(db, p, ctl, task_id, job_id, grant_id)
    clock = db.scalar(select(func.clock_timestamp()))
    old.need(ctl.status == 'queued' or (ctl.status == 'running' and ctl.lease_until is not None
        and ctl.lease_until < clock), '处理任务尚未到可领取状态')
    if ctl.status == 'queued':
        old.need(ctl.attempts == 0 and ctl.lease_token is None and ctl.lease_until is None)
    else:
        old.need(ctl.attempts >= 1 and bool(ctl.lease_token))
    token = str(uuid4()); ctl.status = 'running'; ctl.lease_token = token
    ctl.lease_until = clock + timedelta(seconds=leases.LEASE_SECONDS); ctl.attempts += 1; ctl.version += 1
    db.flush()
    def before_commit(session):
        runtime_worker.actor(session, p.workspace_id, p.user_id)
        _check_grant(session, p, ctl, task_id, job_id, grant_id)
        old.need(ctl.status == 'running' and ctl.lease_token == token
            and ctl.lease_until > session.scalar(select(func.clock_timestamp())))
    event.listen(db, 'before_commit', before_commit)
    return {'control_id': control_id, 'lease_token': token, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'grant_id': proof['grant_id']}


@old.checked
def settle_worker(db, control_id, token, grant_id):
    """Close an original fenced Job after its prepared receipt is durable.

    The actor is re-derived from the stored control record. No caller supplies
    a user identity, and commit rechecks the original lease and grant. This
    never creates a source or calls a model; it remains disposable-only.
    """
    from . import worker as runtime_worker
    guard.require_disposable_database(db); _uuid(control_id); _uuid(token); _uuid(grant_id)
    ctl = db.scalar(select(Job).where(Job.id == control_id, Job.kind == control.KIND)
        .with_for_update().execution_options(populate_existing=True))
    old.need(ctl is not None and type(ctl.data) is dict and type(ctl.data.get('target')) is dict)
    p = runtime_worker.actor(db, ctl.workspace_id, ctl.data.get('actor_id'))
    target = ctl.data['target']; task_id, job_id = target['task_id'], target['job_id']
    _check_grant(db, p, ctl, task_id, job_id, grant_id)
    original = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job_id)
        .with_for_update().execution_options(populate_existing=True))
    old.need(original is not None and original.status == 'succeeded')
    old.need(ctl.status == 'running' and ctl.lease_token == token
        and ctl.lease_until > db.scalar(select(func.clock_timestamp())))
    def before_commit(session):
        # A child identity map avoids refreshing or flushing the pending
        # succeeded state in the caller while checking the original DB lease.
        with session.no_autoflush:
            with Session(bind=session.connection(), autoflush=False, expire_on_commit=False,
                    join_transaction_mode='rollback_only') as reader:
                actor = runtime_worker.actor(reader, p.workspace_id, p.user_id)
                current = reader.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
                    Job.id == control_id, Job.kind == control.KIND).with_for_update()
                    .execution_options(populate_existing=True))
                old.need(current is not None and current.status == 'running' and current.lease_token == token
                    and current.lease_until > reader.scalar(select(func.clock_timestamp())),
                    '原后台租约已失效，保留已保存回执')
                _check_grant(reader, actor, current, task_id, job_id, grant_id)
    event.listen(db, 'before_commit', before_commit)
    ctl.status = 'succeeded'; ctl.lease_token = None; ctl.lease_until = None; ctl.version += 1
    return {'control_id': control_id, 'job_id': job_id, 'settled': True}
