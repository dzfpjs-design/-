"""Fail-closed custody reader for an unstarted v2 preparation.

No writer, route, worker or send adapter is registered here. It authenticates
only a pristine queued Plan/Job and original immutable Audit records. Until
versioned receipt, lease, cancellation and cross-version inventory are
implemented, all later states are intentionally refused.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select

from . import same_goal_advice_contracts_v302 as old_contracts
from . import same_goal_advice_store_v302 as old_store
from . import same_goal_advice_unlimited_contracts_v2 as contracts
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_quota_v304 as legacy_quota
from . import workspace_command_nonce_lock_v312 as nonces
from .contracts import digest
from .models import Audit, AuthSession, Capability, CloudCall, Evidence, Invocation, Job, Membership, Plan, Task, User
from .security import Principal
from . import same_goal_advice_read_session_v2 as read_session

PLAN = 'same-goal-advice-unlimited-plan-v2'
PREPARE_KIND = 'prepare_same_goal_advice_unlimited_v2'
JOB_SCHEMA = 'same-goal-advice-unlimited-prepare-job-v2'
ACCEPTED = 'same_goal_advice.unlimited_preparation_accepted_v2'
CANCELLED_EXPIRED = 'same_goal_advice.unlimited_expired_preparation_cancelled_v2'
DOCUMENT_PREPARED = 'same_goal_advice.unlimited_documents_prepared_v2'
DOCUMENT_KEY = 'same_goal_advice_unlimited_document_preparation_v2'
UNAVAILABLE_TO_GENERAL_WORKERS = datetime(9999, 1, 1, tzinfo=timezone.utc)


def job_key(task_id, plan_id):
    return 'same-goal-advice-prepare-v2:' + task_id + ':' + plan_id


def job_data(plan_id, prep, key):
    return {'schema': JOB_SCHEMA, 'plan_id': plan_id, 'task_id': prep['scope']['task_id'],
        'actor_id': prep['data_actor_id'], 'preparation_snapshot_hash': prep['record_hash'],
        'invocation_key': key}


def accepted_data(plan_id, prep, job_id, key):
    return {'schema': contracts.PREPARATION, 'plan_id': plan_id, 'task_id': prep['scope']['task_id'],
        'actor_id': prep['requested_by'], 'request_id': prep['request']['request_id'],
        'body_hash': prep['request_hash'], 'preparation_snapshot_hash': prep['record_hash'],
        'job_id': job_id, 'job_key': key}


def _lock_current_reader(db, p, *, lock=True):
    """Protect the existing read-role semantics through this transaction.

    A real HTTP session is held and checked; descriptive internal worker
    markers follow the existing trusted-worker convention. No write role or
    execution authority is granted, and viewers retain read-only access.
    """
    old_store._reader(db, p)
    allowed = (['admin', 'operator', 'viewer'] if p.role == 'viewer'
        else ['admin', 'operator'] if p.role == 'operator' else ['admin'])
    stmt = select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(allowed))
    locked = [User, Membership]
    try:
        UUID(p.session_id); http_session = True
    except (ValueError, TypeError, AttributeError):
        http_session = False
    if http_session:
        stmt = stmt.join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.id == p.session_id, AuthSession.revoked.is_(False),
            AuthSession.expires_at > func.clock_timestamp())
        locked.append(AuthSession)
    if lock:
        stmt = stmt.with_for_update(read=True, of=locked)
    value = db.scalar(stmt.execution_options(populate_existing=True))
    old_store.need(value == p.user_id, '当前账号、工作区角色或会话无法核对；未读取资料')
    return http_session


@old_store.checked
def read_queued(db, p, task, plan):
    """Authenticate saved bytes; never checks current source or grants work."""
    old_store.need(type(p) is Principal and type(task) is Task and type(plan) is Plan)
    with read_session.isolated(db, task.id) as reader:
        return _read_queued(reader, p, task.id, plan.id)


@old_store.checked
def read_prepared(db, p, task, plan):
    """Authenticate a complete isolated v2 document receipt, not a send grant."""
    old_store.need(type(p) is Principal and type(task) is Task and type(plan) is Plan)
    with read_session.isolated(db, task.id) as reader:
        return _read_queued(reader, p, task.id, plan.id, prepared=True)


@old_store.checked
def read_expired_cancelled(db, p, task, plan):
    """Authenticate an expired cancellation without reviving its original Job."""
    with read_session.isolated(db, task.id) as reader:
        return _read_queued(reader, p, task.id, plan.id, cancelled=True)


def _read_queued(db, p, task_id, plan_id, *, lock=True, prepared=False, cancelled=False):
    with db.no_autoflush:
        # Writers take the Task lock before actor/Plan/Job locks. Holding a
        # compatible read lock in that same order prevents an in-flight
        # claimant from making the returned queued state stale mid-read.
        old_store._reader(db, p)
        query = select(Task).where(Task.workspace_id == p.workspace_id, Task.id == task_id)
        if lock: query = query.with_for_update(read=True)
        task = db.scalar(query.execution_options(populate_existing=True))
        old_store.need(task is not None)
        http_session = _lock_current_reader(db, p) if lock else _lock_current_reader(db, p, lock=False)
        query = select(Plan).where(Plan.workspace_id == p.workspace_id, Plan.task_id == task.id, Plan.id == plan_id)
        if lock: query = query.with_for_update(read=True)
        plan = db.scalar(query.execution_options(populate_existing=True))
        old_store.need(plan is not None)
        data = plan.data
        old_store.need(type(prepared) is bool and type(cancelled) is bool and not (prepared and cancelled))
        old_store.need(plan.workspace_id == task.workspace_id == p.workspace_id and plan.task_id == task.id
            and ((plan.status == 'awaiting_consent' and type(plan.version) is int and plan.version >= 2)
                if prepared else (plan.status, plan.version) == ('queued', 1))
            and type(data) is dict
            and set(data) == {'schema', 'preparation_snapshot', 'jobs', 'operations', 'document_preparation', 'reviews'}
            and data['schema'] == PLAN and (type(data['document_preparation']) is dict if prepared
                else data['document_preparation'] is None) and data['reviews'] == [])
        raw = data['preparation_snapshot']
        cap = old_store._fresh(db, p, Capability, raw['advice_capability_ref']['id'])
        old_store.need(cap.kind == 'tool' and cap.content_hash == cap.validated_hash
            == digest(cap.data) == raw['advice_capability_ref']['content_hash']
            and cap.release_version == raw['advice_capability_ref']['release_version'])
        prep = contracts.check_preparation(raw, manifest=cap.data)
        old_store.need(prep['scope']['workspace_id'] == p.workspace_id
            and prep['scope']['task_id'] == task.id and prep['scope']['analysis_plan_id'] == plan.id)
        ref = prep['budget']['policy_ref']
        rows = old_store._rows(db, p, Audit, Audit.action == policy.POLICY,
            Audit.object_id == task.id, Audit.id == ref['id'])
        old_store.need(len(rows) == 1 and digest(rows[0].data) == ref['record_hash'])
        now = db.scalar(select(func.clock_timestamp()))
        old_store.need(type(now) is datetime and now.tzinfo is not None)
        saved_policy, limits = policy.policy_shape(rows[0], now)
        old_store.need({k: v for k, v in prep['scope'].items() if k != 'analysis_plan_id'} == saved_policy['scope']
            and saved_policy['mature_head_hash'] == prep['mature_head']['head_hash']
            and limits == prep['budget']
            and all(saved_policy[k] == prep[k] for k in
                ('source_business_capability_ref', 'advice_capability_ref', 'model_pin')))
        auths = old_store._rows(db, p, Audit, Audit.action == policy.AUTHORIZATION,
            Audit.object_id == task.id, Audit.data['policy_ref']['id'].as_string() == rows[0].id)
        old_store.need(len(auths) == 1 and auths[0].actor_id == rows[0].actor_id)
        request = policy.Authorize.model_validate(auths[0].data['body']).model_dump(mode='json')
        issuer = Principal(auths[0].actor_id, p.workspace_id, 'admin', 'historical-policy-reader')
        receipt = policy._receipt(db, issuer, task, request, auths[0], now)
        old_store.need(receipt['policy_ref'] == ref
            and all(saved_policy['limits'][name] == request['limits'][name] for name in request['limits']))
        jobs = data['jobs']; old_store.need(type(jobs) is dict and set(jobs) == {'prepare'})
        pin = jobs['prepare']; key = job_key(task.id, plan.id)
        old_store.need(type(pin) is dict and set(pin) == {'id', 'kind', 'schema', 'key'}
            and pin['kind'] == PREPARE_KIND and pin['schema'] == JOB_SCHEMA and pin['key'] == key)
        originals = old_store._rows(db, p, Job, or_(Job.id == pin['id'], Job.idempotency_key == key,
            Job.data['plan_id'].as_string() == plan.id))
        old_store.need(len(originals) == 1)
        query = select(Job).where(Job.workspace_id == p.workspace_id, Job.id == originals[0].id)
        if lock: query = query.with_for_update(read=True)
        job = db.scalar(query.execution_options(populate_existing=True))
        old_store.need(job is not None)
        old_store.need(job.id == pin['id'] and job.kind == PREPARE_KIND and job.idempotency_key == key
            and (job.status, job.attempts) == (('succeeded', 1) if prepared else ('queued', 0))
            and job.lease_token is None and job.lease_until is None
            and job.available_at == UNAVAILABLE_TO_GENERAL_WORKERS
            and digest(job.data) == digest(job_data(plan.id, prep, key)))
        ops = data['operations']; old_store.need(type(ops) is list and len(ops) == 1)
        op = ops[0]
        old_store.need(type(op) is dict and set(op) == {'operation', 'request_id', 'body', 'body_hash', 'actor_id', 'audit_id'}
            and op['operation'] == 'prepare' and op['request_id'] == prep['request']['request_id']
            and digest(op['body']) == digest(prep['request']) and op['body_hash'] == prep['request_hash']
            and op['actor_id'] == prep['requested_by'])
        event = old_store._audit(db, p, ACCEPTED, plan.id, prep['requested_by'],
            accepted_data(plan.id, prep, job.id, key))
        # The future writer must use one DB clock for Plan, Job and acceptance;
        # never infer a prior authorization from an independently forged row.
        old_store.need(event.id == op['audit_id'] and event.created_at is not None
            and event.created_at.tzinfo is not None and plan.created_at == job.created_at == event.created_at
            and rows[0].created_at <= event.created_at <= now)
        accepted_at = event.created_at
        # A correct local pointer is insufficient if the same workspace nonce
        # was also used by another advice or continuation operation. The new
        # writer must take the shared workspace nonce lock before the Task lock;
        # this read itself never creates or grants that lock/authority.
        nonce = prep['request']['request_id']
        nonce_events = old_store._rows(db, p, Audit,
            Audit.action.in_(nonces.ACTIONS | {ACCEPTED, policy.AUTHORIZATION, legacy_quota.AUTHORIZATION}), or_(
            Audit.data['request_id'].as_string() == nonce,
            Audit.data['request']['request_id'].as_string() == nonce,
            Audit.data['body']['request_id'].as_string() == nonce))
        nonce_plans = old_store._rows(db, p, Plan, or_(
            Plan.data['operations'].contains([{'request_id': nonce}]),
            Plan.data['revisions'].contains([{'request_id': nonce}])))
        old_store.need(len(nonce_events) == 1 and nonce_events[0].id == event.id
            and len(nonce_plans) == 1 and nonce_plans[0].id == plan.id,
            '原请求编号与其他申请冲突，保留全部记录等待核对')
        calls = old_store._rows(db, p, Invocation, or_(Invocation.data['job_id'].as_string() == job.id,
            Invocation.data['approval']['plan_ref']['id'].as_string() == plan.id,
            Invocation.data['inputs']['plan_ref']['id'].as_string() == plan.id,
            Invocation.idempotency_key == key))
        old_store.need(not calls, 'v2 准备任务出现未知调用，保留记录等待核对')
        # A missing Plan marker or Invocation is not proof that no separate
        # receipt or CloudCall was written. Reject identifiable orphan records;
        # never repair or silently treat them as not sent.
        from . import same_goal_advice_preparation_v304 as legacy_documents
        from . import same_goal_advice_spending_v308 as legacy_spending
        from . import same_goal_advice_execution_v308 as legacy_execution
        known_actions = [legacy_documents.PREPARED, legacy_execution.STARTED,
            *legacy_execution._SDK_ACTIONS, *legacy_execution._PLAN_ACTIONS]
        events = old_store._rows(db, p, Audit, or_(Audit.action.in_(known_actions),
            Audit.action.startswith('same_goal_advice.unlimited_', autoescape=True) & (Audit.action != ACCEPTED)),
            or_(Audit.object_id == plan.id,
            Audit.data['plan_id'].as_string() == plan.id, Audit.data['job_id'].as_string() == job.id,
            Audit.data['send_job_id'].as_string() == job.id, Audit.data['prepare_job_id'].as_string() == job.id))
        files = old_store._rows(db, p, Evidence, or_(
            Evidence.data[legacy_documents.KEY]['plan_id'].as_string() == plan.id,
            Evidence.data[legacy_documents.KEY]['prepare_job_id'].as_string() == job.id,
            Evidence.data[DOCUMENT_KEY]['plan_id'].as_string() == plan.id,
            Evidence.data[DOCUMENT_KEY]['prepare_job_id'].as_string() == job.id))
        cloud = old_store._rows(db, p, CloudCall, or_(
            CloudCall.data['gateway_dispatch']['identity']['job_id'].as_string() == job.id,
            CloudCall.data[legacy_spending.KEY]['plan_id'].as_string() == plan.id,
            CloudCall.data['same_goal_advice_unlimited_v2']['plan_id'].as_string() == plan.id,
            CloudCall.data['same_goal_advice_unlimited_v2']['job_id'].as_string() == job.id))
        if prepared:
            from . import same_goal_advice_prepare_document_candidate_v2 as document
            from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
            document_events = [event for event in events if event.action == DOCUMENT_PREPARED]
            preview_events = [event for event in events if event.action == previews.ACTION]
            old_store.need(len(document_events) + len(preview_events) == len(events))
            receipt = document.verify_original(db, p, task, plan, prep, job, document_events, files)
            previews.validate_chain(db, p, task, plan, prep, receipt, preview_events)
        elif cancelled:
            old_store.need(len(events) == 1 and events[0].action == CANCELLED_EXPIRED and not files)
            event = events[0]; body = old_contracts.Cancel.model_validate(event.data['body']).model_dump(mode='json')
            expected = {'schema': CANCELLED_EXPIRED, 'task_id': task.id, 'plan_id': plan.id,
                'job_id': job.id, 'job_key': job.idempotency_key, 'actor_id': event.actor_id,
                'request_id': body['request_id'], 'body': body, 'body_hash': digest(body),
                'preparation_snapshot_hash': prep['record_hash'], 'policy_ref': ref}
            old_store.need(event.object_id == plan.id and digest(event.data) == digest(expected)
                and body['expected_plan_version'] == plan.version and body['request_id'] != nonce
                and event.created_at is not None and event.created_at.tzinfo is not None
                and accepted_at < policy.instant(saved_policy['expires_at']) <= event.created_at <= now,
                '原到期取消回执无法核对')
            cancel_nonce = body['request_id']
            cancel_events = old_store._rows(db, p, Audit,
                Audit.action.in_(nonces.ACTIONS | {policy.AUTHORIZATION, legacy_quota.AUTHORIZATION}), or_(
                Audit.data['request_id'].as_string() == cancel_nonce,
                Audit.data['request']['request_id'].as_string() == cancel_nonce,
                Audit.data['body']['request_id'].as_string() == cancel_nonce))
            cancel_plans = old_store._rows(db, p, Plan, or_(
                Plan.data['operations'].contains([{'request_id': cancel_nonce}]),
                Plan.data['revisions'].contains([{'request_id': cancel_nonce}])))
            old_store.need(len(cancel_events) == 1 and cancel_events[0].id == event.id and not cancel_plans,
                '原取消编号与其他申请冲突，保留全部记录等待核对')
        old_store.need((prepared or cancelled or not events and not files) and not cloud,
            'v2 准备任务存在未归档的资料或调用记录，保留原记录等待核对')
        if http_session:
            # Row locks prevent revocation changes, but cannot stop a session
            # from expiring while the remaining custody checks are running.
            valid = db.scalar(select(AuthSession.id).where(AuthSession.id == p.session_id,
                AuthSession.user_id == p.user_id, AuthSession.revoked.is_(False),
                AuthSession.expires_at > func.clock_timestamp()))
            old_store.need(valid == p.session_id, '当前会话已过期；未读取资料')
        return prep, job
