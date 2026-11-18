"""Transaction primitive for a single scoped extra cloud reservation.

This module is deliberately NOT wired into a gateway, a worker, or an API.
One internal delegated-evidence adapter verifies the task, frozen stage,
prepared messages and options. It never commits or sends HTTP; a separate
reviewed gateway and worker integration is required before any model call.

Trust boundary: this detects mismatched or separately altered permit/audit
records; it does not defend against a database owner who rewrites both.
The ORM commit/flush checks assume trusted in-process transaction code: direct
SQL that bypasses the ORM identity map, or later engine-level commit listeners
that write after validation, require separate database-enforced controls.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import unicodedata
from uuid import UUID

from sqlalchemy import event, func, inspect, select
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .leases import fenced, ownership
from .models import Audit, Capability, CloudCall, Job, Membership, User
from .security import Principal


class ScopedReservationDenied(ValueError):
    """No reservation was accepted; caller must roll back its transaction."""


_HASH = re.compile(r'^[0-9a-f]{64}$')
_MANIFEST_KEYS = frozenset({'schema', 'workspace_id', 'actor_id', 'issuer_id', 'job_id',
    'job_kind', 'permit_id', 'capability_id', 'capability_hash', 'scope_kind', 'stage_id',
    'scope_hash', 'request_hash', 'options_hash', 'token_bound', 'max_tokens', 'max_calls',
    'not_before', 'expires_at'})
_PERMIT_KEYS = frozenset({'schema', 'manifest', 'manifest_hash', 'audit_id', 'used_call_id'})
_AUDIT_KEYS = frozenset({'schema', 'permit_id', 'manifest_hash'})
_SESSION_KEY = 'cloud_scoped_extra_commit_checks_v1'
_SESSION_WITNESS = object()


def _deny():
    raise ScopedReservationDenied('定向额度或后台任务无法安全核对；未授予发送权限')


def _instant(value):
    if type(value) is not str:
        _deny()
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _deny()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _deny()


def _task_adapter(scope_kind):
    # This internal adapter is bound to an already authenticated, versioned
    # report plan. Merely selecting this scope cannot create a grant, obtain a
    # lease, access credentials, or send HTTP.
    if scope_kind == 'delegated-evidence-map-v1':
        from .cloud_report_plan_allowance import prepare_map_stage
        return prepare_map_stage
    if scope_kind == 'delegated-evidence-reduce-v1':
        from .report_evidence_delegated_reduce_reservation import prepare_reduce_stage
        return prepare_reduce_stage
    return None


def _stage_identity(job_id, stage_id):
    if type(job_id) is not str or type(stage_id) is not str or not stage_id or len(stage_id) > 120:
        _deny()
    try:
        if (str(UUID(job_id)) != job_id or unicodedata.normalize('NFC', stage_id) != stage_id
                or stage_id != stage_id.strip() or any(unicodedata.category(c).startswith('C') for c in stage_id)):
            _deny()
    except (ValueError, TypeError):
        _deny()
    return digest({'job_id':job_id, 'stage_id':stage_id})


def permit_key(job_id, stage_id):
    return 'cloud-extra-permit-v1:' + _stage_identity(job_id, stage_id)


def dispatch_key(job_id, stage_id):
    return 'cloud-extra-dispatch-v1:' + _stage_identity(job_id, stage_id)


def _session_checks(db):
    state = db.info.get(_SESSION_KEY)
    if state is not None:
        if (not isinstance(state, dict) or state.get('witness') is not _SESSION_WITNESS
                or not isinstance(state.get('checks'), dict)
                or not callable(state.get('before')) or not callable(state.get('after'))
                or not callable(state.get('post_flush'))
                or not event.contains(db, 'before_commit', state.get('before'))
                or not event.contains(db, 'after_flush_postexec', state.get('post_flush'))
                or not event.contains(db, 'after_transaction_end', state.get('after'))):
            _deny()
        return state['checks']
    checks = {}
    def before_commit(session):
        check = checks.get(session.get_transaction())
        if check is not None:
            check(session)
    def after_transaction_end(session, transaction):
        # Nested flush transactions are different objects. Only the owning
        # outer transaction can remove its pending private verification data.
        checks.pop(transaction, None)
    def after_flush_postexec(session, _flush_context):
        # before_commit listeners installed after our first check can still
        # mutate ORM objects. Recheck the same immutable transaction snapshot
        # after SQLAlchemy executes each flush, before COMMIT can proceed.
        check = checks.get(session.get_transaction())
        if check is not None:
            check(session)
    event.listen(db, 'before_commit', before_commit)
    event.listen(db, 'after_flush_postexec', after_flush_postexec)
    event.listen(db, 'after_transaction_end', after_transaction_end)
    db.info[_SESSION_KEY] = {'witness':_SESSION_WITNESS, 'checks':checks,
                             'before':before_commit, 'after':after_transaction_end,
                             'post_flush':after_flush_postexec}
    return checks


def reserve_scoped_tx(db, principal, permit_id, capability_id, token_bound,
                      request_hash, options_hash):
    """Prepare exactly one reservation in the existing fenced transaction.

    On any error the caller MUST roll back. The caller must commit with the
    lease fence still attached before using the returned call ID. This function
    does not record a transport intent, access credentials, or send anything.
    """
    if (not fenced(db) or type(principal) is not Principal
            or db.info.get('worker_actor') != principal
            or principal.role not in ('admin', 'operator')
            or type(permit_id) is not str or not permit_id
            or type(capability_id) is not str or not capability_id
            or type(token_bound) is not int or token_bound < 1
            or type(request_hash) is not str or not _HASH.fullmatch(request_hash)
            or type(options_hash) is not str or not _HASH.fullmatch(options_hash)):
        _deny()
    owner = ownership(db)
    if set(owner) != {'job_id', 'lease_token'} or not owner['job_id'] or not owner['lease_token']:
        _deny()

    # leases.attach has already locked the worker Job on transaction begin.
    job = db.scalar(select(Job).where(Job.id == owner['job_id'],
        Job.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
    transaction = db.get_transaction()
    checks = _session_checks(db)
    # Each stage is committed before any network operation. Multiple stages in
    # the same Session require separate transactions and separate permits.
    if transaction is None or transaction in checks:
        _deny()
    permit = db.scalar(select(Job).where(Job.id == permit_id,
        Job.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
    clock = db.scalar(select(func.clock_timestamp()))
    if (not job or job.status != 'running' or job.lease_token != owner['lease_token']
            or not job.lease_until or job.lease_until <= clock
            or not isinstance(job.data, dict) or job.data.get('actor_id') != principal.user_id
            or not permit or permit.kind != 'cloud_extra_allowance_v1' or permit.status != 'internal'
            or not isinstance(permit.data, dict) or frozenset(permit.data) != _PERMIT_KEYS):
        _deny()
    data = permit.data
    manifest = deepcopy(data.get('manifest'))
    if (data.get('schema') != 'cloud-extra-allowance-record-v1'
            or not isinstance(manifest, dict) or frozenset(manifest) != _MANIFEST_KEYS
            or type(data.get('audit_id')) is not str or not data['audit_id']
            or data.get('used_call_id') is not None or data.get('manifest_hash') != digest(manifest)
            or manifest.get('schema') != 'cloud-extra-allowance-manifest-v1'
            or any(type(manifest.get(k)) is not str or not manifest[k] for k in
                   ('workspace_id', 'actor_id', 'issuer_id', 'job_id', 'job_kind', 'permit_id',
                    'capability_id', 'scope_kind', 'stage_id'))
            or any(type(manifest.get(k)) is not str or not _HASH.fullmatch(manifest[k]) for k in
                   ('capability_hash', 'scope_hash', 'request_hash', 'options_hash'))
            or any(type(manifest.get(k)) is not int for k in ('token_bound', 'max_tokens', 'max_calls'))
            or manifest['max_calls'] != 1 or manifest['token_bound'] != token_bound
            or token_bound > manifest['max_tokens'] or manifest['max_tokens'] < 1
            or manifest['workspace_id'] != principal.workspace_id
            or manifest['actor_id'] != principal.user_id or manifest['job_id'] != job.id
            or manifest['job_kind'] != job.kind or manifest['permit_id'] != permit.id
            or manifest['capability_id'] != capability_id or manifest['request_hash'] != request_hash
            or manifest['options_hash'] != options_hash
            or permit.idempotency_key != permit_key(job.id, manifest['stage_id'])
            or not (_instant(manifest['not_before']) <= clock < _instant(manifest['expires_at']))):
        _deny()

    # Lock current executor and original issuer through commit; auth revocation
    # racing this transaction either wins before validation or waits for it.
    actor_ids = sorted({principal.user_id, manifest['issuer_id']})
    users = db.scalars(select(User).where(User.id.in_(actor_ids)).order_by(User.id)
        .with_for_update().execution_options(populate_existing=True)).all()
    memberships = db.scalars(select(Membership).where(Membership.workspace_id == principal.workspace_id,
        Membership.user_id.in_(actor_ids)).order_by(Membership.user_id)
        .with_for_update().execution_options(populate_existing=True)).all()
    if len(users) != len(actor_ids) or len(memberships) != len(actor_ids) or any(not u.active for u in users):
        _deny()
    roles = {m.user_id: m.role for m in memberships}
    if roles.get(principal.user_id) != principal.role or roles.get(manifest['issuer_id']) != 'admin':
        _deny()

    audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
        Audit.object_id == permit.id, Audit.action == 'cloud.extra_allowance.issued_v1')
        .limit(2).with_for_update().execution_options(populate_existing=True)).all()
    audit = audits[0] if len(audits) == 1 else None
    if (not audit or audit.id != data['audit_id'] or audit.actor_id != manifest['issuer_id'] or audit.object_id != permit.id
            or audit.action != 'cloud.extra_allowance.issued_v1' or not isinstance(audit.data, dict)
            or frozenset(audit.data) != _AUDIT_KEYS
            or audit.data != {'schema':'cloud-extra-allowance-issue-v1', 'permit_id':permit.id,
                              'manifest_hash':data['manifest_hash']}):
        _deny()
    capability = db.scalar(select(Capability).where(Capability.id == capability_id,
        Capability.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
    if (not capability or capability.kind != 'foundation_model' or capability.status != 'published'
            or capability.content_hash != manifest['capability_hash'] or capability.validated_hash != capability.content_hash
            or digest(capability.data) != capability.content_hash
            or not isinstance(capability.data.get('data_policy'), dict)
            or capability.data['data_policy'].get('cloud_allowed') is not True):
        _deny()
    adapter = _task_adapter(manifest['scope_kind'])
    original_job_data = deepcopy(job.data)
    decision = adapter(db, principal, job, capability, deepcopy(manifest)) if callable(adapter) else None
    from .cloud_report_plan_allowance import _MapBinding
    from .report_evidence_delegated_reduce_reservation import _Binding as _ReduceBinding
    binding = decision if ((manifest['scope_kind']=='delegated-evidence-map-v1' and type(decision) is _MapBinding)
        or (manifest['scope_kind']=='delegated-evidence-reduce-v1' and type(decision) is _ReduceBinding)) else None
    if (not callable(adapter) or (decision is not True and binding is None)
            or db.get_transaction() is not transaction or not transaction.is_active
            or digest(manifest) != data['manifest_hash'] or job.data != original_job_data):
        _deny()
    clock = db.scalar(select(func.clock_timestamp()))
    if not (_instant(manifest['not_before']) <= clock < _instant(manifest['expires_at'])):
        _deny()

    # There is a single deterministic dispatch key per Job and stage. A used
    # permit or existing dispatch is never automatically retried, even if its
    # earlier caller crashed. The DB unique key serializes conflicting inserts.
    key = dispatch_key(job.id, manifest['stage_id'])
    if db.scalar(select(Job.id).where(Job.workspace_id == principal.workspace_id,
            Job.idempotency_key == key)) is not None:
        _deny()
    day = str(clock.astimezone(timezone.utc).date())
    budget_key = 'cloud-budget:' + day
    db.execute(insert(Job).values(workspace_id=principal.workspace_id, kind='cloud_budget', status='internal',
        idempotency_key=budget_key, data={'day':day, 'tokens':0, 'calls':0})
        .on_conflict_do_nothing(index_elements=['workspace_id', 'idempotency_key']))
    budget = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == budget_key).with_for_update().execution_options(populate_existing=True))
    if (not budget or budget.kind != 'cloud_budget' or budget.status != 'internal'
            or not isinstance(budget.data, dict) or budget.data.get('day') != day
            or type(budget.data.get('tokens')) is not int or budget.data['tokens'] < 0
            or type(budget.data.get('calls')) is not int or budget.data['calls'] < 0):
        _deny()
    # No existing CloudCall is locked while the budget row is held; normal
    # settlement uses call -> budget. The new call belongs to this transaction.
    call = CloudCall(workspace_id=principal.workspace_id, capability_id=capability.id,
        reserved_tokens=token_bound, charged_tokens=token_bound,
        data={'day':day, 'actor_id':principal.user_id, 'request_hash':request_hash,
              'dispatch_options_hash':options_hash, 'scoped_extra':{'schema':'cloud-extra-call-v1',
                  'permit_id':permit.id, 'job_id':job.id, 'stage_id':manifest['stage_id'],
                  'scope_hash':manifest['scope_hash'], 'manifest_hash':data['manifest_hash']}})
    db.add(call); db.flush()
    dispatch = Job(workspace_id=principal.workspace_id, kind='cloud_extra_dispatch_v1', status='internal',
        idempotency_key=key, data={'schema':'cloud-extra-dispatch-v1', 'permit_id':permit.id,
            'job_id':job.id, 'stage_id':manifest['stage_id'], 'scope_hash':manifest['scope_hash'],
            'call_id':call.id, 'state':'reserved', 'manifest_hash':data['manifest_hash']})
    db.add(dispatch)
    budget.data = {**budget.data, 'tokens':budget.data['tokens'] + token_bound, 'calls':budget.data['calls'] + 1}
    permit.data = {**data, 'used_call_id':call.id}
    if binding is not None:
        binding.bind(db, call)
    db.flush()
    expected_permit, expected_budget = deepcopy(permit.data), deepcopy(budget.data)
    expected_dispatch, expected_call = deepcopy(dispatch.data), deepcopy(call.data)
    expected_audit, expected_capability = deepcopy(audit.data), deepcopy(capability.data)

    # A caller may delay the commit after this function returns. Recheck the
    # permit's DB-clock validity at the actual fenced commit, not just at the
    # beginning of a potentially long transaction or after a lock wait.
    def check_at_commit(session):
        if session.get_transaction() is transaction:
            current = session.scalar(select(func.clock_timestamp()))
            if (not fenced(session) or session.info.get('worker_actor') != principal
                    or not (_instant(manifest['not_before']) <= current < _instant(manifest['expires_at']))
                    or any(row in session.deleted or inspect(row).deleted or inspect(row).detached for row in
                           [job, permit, audit, capability, budget, dispatch, call, *users, *memberships])
                    or job.id != owner['job_id'] or job.workspace_id != principal.workspace_id
                    or job.kind != manifest['job_kind'] or job.status != 'running'
                    or job.lease_token != owner['lease_token']
                    or not isinstance(job.lease_until, datetime) or job.lease_until.tzinfo is None
                    or job.lease_until.utcoffset() is None
                    or job.lease_until.astimezone(timezone.utc) <= current.astimezone(timezone.utc)
                    or job.data != original_job_data or permit.data != expected_permit
                    or permit.id != permit_id or permit.workspace_id != principal.workspace_id
                    or permit.idempotency_key != permit_key(job.id, manifest['stage_id'])
                    or permit.status != 'internal' or permit.kind != 'cloud_extra_allowance_v1'
                    or audit.data != expected_audit or audit.actor_id != manifest['issuer_id']
                    or audit.id != data['audit_id'] or audit.workspace_id != principal.workspace_id
                    or audit.object_id != permit.id or audit.action != 'cloud.extra_allowance.issued_v1'
                    or capability.data != expected_capability or capability.content_hash != manifest['capability_hash']
                    or capability.id != capability_id or capability.workspace_id != principal.workspace_id
                    or capability.kind != 'foundation_model'
                    or capability.validated_hash != capability.content_hash or capability.status != 'published'
                    or budget.data != expected_budget or budget.status != 'internal' or budget.kind != 'cloud_budget'
                    or budget.workspace_id != principal.workspace_id or budget.idempotency_key != budget_key
                    or dispatch.data != expected_dispatch or dispatch.kind != 'cloud_extra_dispatch_v1'
                    or dispatch.workspace_id != principal.workspace_id or dispatch.idempotency_key != key
                    or dispatch.status != 'internal' or call.data != expected_call or call.status != 'reserved'
                    or call.workspace_id != principal.workspace_id
                    or call.capability_id != capability.id or call.reserved_tokens != token_bound
                    or call.charged_tokens != token_bound or any(not u.active for u in users)
                    or any(m.workspace_id != principal.workspace_id for m in memberships)
                    or {m.user_id:m.role for m in memberships} != roles
                    or (binding is not None and binding.check(session) is not True)):
                _deny()

    checks[transaction] = check_at_commit
    return call.id
