"""Closed, one-shot admission for a prebound delegated map response.

This module performs no network IO and is not connected to the gateway. The
production dispatch gate stays closed. A committed intent means the request
may have been sent; it is never a proof of non-delivery or a retry permit.
"""
from __future__ import annotations

import copy
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from .contracts import digest
from .leases import ExecutionUnauthorized, LeaseLost, checkpoint, fenced, ownership
from .models import Audit, Capability, CloudCall, Job, Membership, User
from .security import Principal


class PreboundTransportDenied(ValueError):
    """Do not send, and roll back the current worker transaction."""


_WITNESS = object()
_MAX_INTENT_DELAY_SECONDS = 30


def _monotonic():
    return time.monotonic()


class CommittedTransportIntent:
    """One in-process handoff after a committed, non-repeatable intent.

    It does not itself send HTTP or establish authority for another gateway.
    After a crash no new witness can be minted from the existing intent.
    """
    def __init__(self, seal, principal, capability_id, stage_id, call_id, messages, options, sink, context, intent_hash, deadline):
        if seal is not _WITNESS:
            _deny()
        self._principal = principal
        self._capability_id = capability_id
        self._stage_id = stage_id
        self._call_id = call_id
        self._messages = copy.deepcopy(messages)
        self._options = copy.deepcopy(options)
        self._sink = sink
        self._context = copy.deepcopy(context)
        self._intent_hash = intent_hash
        self._deadline = deadline
        self._used = False
        self._lock = threading.Lock()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, _memo):
        _deny()

    def __reduce_ex__(self, _protocol):
        _deny()

    def consume(self, principal, capability_id, stage_id, call_id, messages, options, sink):
        """Return exact frozen transport inputs once, or fail closed."""
        from . import report_evidence_http_capture_v1 as capture
        with self._lock:
            if self._used:
                _deny()
            # Intent was already committed. Any handoff attempt, including a
            # mismatched or broken local sink, is terminal for this witness.
            self._used = True
            if (_monotonic() >= self._deadline or principal != self._principal or capability_id != self._capability_id
                    or stage_id != self._stage_id or call_id != self._call_id or sink is not self._sink
                    or digest(messages) != digest(self._messages)
                    or digest(options) != digest(self._options)
                    or type(sink) is not capture.NativeHttpCapture or digest(sink.context) != digest(self._context)):
                _deny()
            try:
                sink._active()
                if os.fstat(sink._body).st_size != 0 or set(os.listdir(sink._directory)) != {'slot.json', 'body.bin'}:
                    _deny()
            except (OSError, ValueError):
                _deny()
            return {'call_id': self._call_id, 'intent_hash': self._intent_hash,
                    'messages': copy.deepcopy(self._messages), 'options': copy.deepcopy(self._options),
                    'sink': self._sink}


def _deny():
    raise PreboundTransportDenied('原预留、任务或派发状态无法安全核对；未授权发送')


def _locked(db, statement):
    # A competing transaction must not make a held worker lease wait in a
    # lock-order cycle. The surrounding caller must roll back on any error.
    return db.scalar(statement.with_for_update(nowait=True).execution_options(populate_existing=True))


def _call_snapshot(session, item):
    """Freeze every persisted ORM column and require this exact live identity."""
    state = inspect(item)
    if (not state.persistent or state.session is not session or state.deleted
            or state.key is None or session.identity_map.get(state.key) is not item
            or item in session.deleted or state.expired_attributes or state.unloaded):
        _deny()
    return (item, state.key, {prop.key: copy.deepcopy(getattr(item, prop.key))
                              for prop in state.mapper.column_attrs})


def _check_call_snapshot(session, snapshot):
    item, identity, columns = snapshot
    state = inspect(item)
    if (not state.persistent or state.session is not session or state.deleted
            or state.key != identity or session.identity_map.get(identity) is not item
            or item in session.deleted or state.expired_attributes or state.unloaded
            or set(columns) != {prop.key for prop in state.mapper.column_attrs}):
        _deny()
    for name, value in columns.items():
        actual = getattr(item, name)
        if (type(actual) is not type(value) or actual != value
                or isinstance(value, (dict, list)) and digest(actual) != digest(value)):
            _deny()


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _deny()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _deny()


def _capture_ready(row, key, sink):
    from . import report_evidence_pipeline as evidence
    from . import report_evidence_delegated_wire_v1 as wire
    from . import report_evidence_http_capture_v1 as capture
    if (type(sink) is not capture.NativeHttpCapture
            or digest(sink.context) != digest(wire.context(evidence, row, key))):
        _deny()
    try:
        sink._active()
        if os.fstat(sink._body).st_size != 0 or set(os.listdir(sink._directory)) != {'slot.json', 'body.bin'}:
            _deny()
    except (OSError, ValueError):
        _deny()


def _check(db, principal, stage_id, call_id, messages, options, sink, *, require_capture=True):
    from . import cloud_report_plan_allowance as plans
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_origin as origin
    from . import report_evidence_pipeline as evidence
    from . import report_reading_drafts as legacy
    from .cloud_evidence_profiles import evidence_options
    from .cloud_provider_policy import provider_enabled
    from .gateway import cloud_json_options, prepare_cloud_json

    if (not isinstance(db, Session) or not fenced(db) or type(principal) is not Principal
            or db.info.get('worker_actor') != principal or principal.role not in ('admin', 'operator')
            or type(stage_id) is not str or type(call_id) is not str or not call_id
            or type(messages) is not list or type(options) is not dict):
        _deny()
    own = ownership(db)
    if set(own) != {'job_id', 'lease_token'} or not own['job_id'] or not own['lease_token']:
        _deny()
    job = _locked(db, select(Job).where(Job.workspace_id == principal.workspace_id, Job.id == own['job_id']))
    clock = db.scalar(select(func.clock_timestamp()))
    from .report_evidence_worker_isolation import isolated
    if (not isolated(job) or job.status != 'running' or job.lease_token != own['lease_token']
            or not isinstance(job.lease_until, datetime) or job.lease_until.tzinfo is None
            or job.lease_until <= clock or not isinstance(job.data, dict)
            or job.data.get('actor_id') != principal.user_id or not job.data.get('report_draft_id')):
        _deny()
    legacy.workspace_draft(db, principal)
    row = evidence.owned(db, principal, job.data['report_draft_id'], True)
    if (not row.request_id.startswith('fl_') or row.status != 'running' or row.data.get('phase') != 'map'
            or row.data.get('job_id') != job.id or stage_id == 'reduce'):
        _deny()
    link = origin.verify_link(db, principal, row, require_current=True)
    from .report_evidence_epoch_handoff import assert_worker_epoch
    epoch_records = assert_worker_epoch(db, principal, job, row)
    from .report_evidence_delegated_dispatch_gate import ready
    if not ready():
        # Held through commit of this transaction. Revocation locks this same
        # row; whichever commits first defines the in-flight boundary.
        from . import report_evidence_scoped_activation as activation
        scoped_record, scoped_audit, _ = activation.for_job(db,principal,row,map_job_id=job.id,lock=True)
        epoch_records = [*epoch_records, scoped_record, scoped_audit]
    cap = evidence._fresh_cap(db, principal, row, map_source=True)
    cap = _locked(db, select(Capability).where(Capability.id == cap.id,
        Capability.workspace_id == principal.workspace_id))
    if (not cap or cap.kind != 'foundation_model' or cap.status != 'published'
            or cap.validated_hash != cap.content_hash or digest(cap.data) != cap.content_hash
            or cap.data.get('execution', {}).get('provider') != 'deepseek'
            or cap.data.get('data_policy', {}).get('cloud_allowed') is not True
            or not provider_enabled('deepseek')):
        _deny()
    user = _locked(db, select(User).where(User.id == principal.user_id))
    member = _locked(db, select(Membership).where(Membership.workspace_id == principal.workspace_id,
        Membership.user_id == principal.user_id))
    if not user or not user.active or not member or member.role != principal.role:
        _deny()

    grant = _locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == plans.plan_key(job.id)))
    permit = _locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == scoped.permit_key(job.id, stage_id)))
    dispatch = _locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == scoped.dispatch_key(job.id, stage_id)))
    if (not grant or grant.kind != 'cloud_report_plan_allowance_v1' or grant.status != 'internal'
            or not permit or permit.kind != 'cloud_extra_allowance_v1' or permit.status != 'internal'
            or not dispatch or dispatch.kind != 'cloud_extra_dispatch_v1' or dispatch.status != 'internal'):
        _deny()
    gd, pd, dd = grant.data, permit.data, dispatch.data
    if (type(gd) is not dict or type(pd) is not dict or type(dd) is not dict
            or set(gd) != {'schema', 'manifest', 'manifest_hash', 'audit_id', 'consumed'}
            or set(pd) != {'schema', 'manifest', 'manifest_hash', 'audit_id', 'used_call_id'}
            or set(dd) != {'schema', 'permit_id', 'job_id', 'stage_id', 'scope_hash', 'call_id', 'state', 'manifest_hash'}
            or gd['schema'] != plans.RECORD or gd['manifest_hash'] != digest(gd['manifest'])
            or pd['schema'] != 'cloud-extra-allowance-record-v1' or pd['manifest_hash'] != digest(pd['manifest'])
            or dd['schema'] != 'cloud-extra-dispatch-v1' or dd['state'] != 'reserved'):
        _deny()
    gm, pm, used = gd['manifest'], pd['manifest'], gd['consumed']
    stages = plans.plan_from_preview(row.data['preview'])
    if (type(gm) is not dict or type(pm) is not dict or type(used) is not list or not used
            or len(used) > len(stages) or gm.get('stage_plan') != stages
            or gm.get('workspace_id') != principal.workspace_id or gm.get('actor_id') != principal.user_id
            or gm.get('issuer_id') != principal.user_id or gm.get('draft_id') != row.id or gm.get('job_id') != job.id
            or gm.get('origin_hash') != link['origin_hash'] or gm.get('source_hash') != row.data['preview']['source_hash']
            or gm.get('preview_hash') != row.data['preview']['preview_hash'] or gm.get('model_id') != cap.id
            or gm.get('model_hash') != cap.content_hash or gm.get('max_calls') != len(stages)
            or gm.get('max_tokens') != sum(item['token_bound'] for item in stages)):
        _deny()
    index = len(used) - 1
    entry = stages[index]
    scope = plans.scope_hash(principal.workspace_id, principal.user_id, row.id, job.id,
        gm['source_hash'], gm['preview_hash'], link['origin_hash'], stage_id)
    if (entry['stage_id'] != stage_id or used[-1] != {'stage_id': stage_id, 'call_id': call_id,
            'token_bound': entry['token_bound']} or pm.get('scope_kind') != plans.SCOPE
            or pm.get('scope_hash') != scope or pm.get('stage_id') != stage_id
            or pm.get('workspace_id') != principal.workspace_id or pm.get('actor_id') != principal.user_id
            or pm.get('issuer_id') != principal.user_id or pm.get('job_id') != job.id
            or pm.get('job_kind') != job.kind or pm.get('permit_id') != permit.id
            or pm.get('capability_id') != cap.id or pm.get('capability_hash') != cap.content_hash
            or pm.get('request_hash') != entry['request_hash'] or pm.get('options_hash') != entry['options_hash']
            or pm.get('token_bound') != entry['token_bound'] or pm.get('max_tokens') != entry['token_bound']
            or pm.get('max_calls') != 1 or pd.get('used_call_id') != call_id
            or dd != {'schema': 'cloud-extra-dispatch-v1', 'permit_id': permit.id, 'job_id': job.id,
                      'stage_id': stage_id, 'scope_hash': scope, 'call_id': call_id,
                      'state': 'reserved', 'manifest_hash': pd['manifest_hash']}):
        _deny()
    if not (_instant(gm.get('not_before')) <= clock < _instant(gm.get('expires_at'))
            and _instant(pm.get('not_before')) <= clock < _instant(pm.get('expires_at'))):
        _deny()

    checked_audits = []
    for record, action, expected in (
        (grant, 'cloud.report_plan_allowance.issued_v1',
         {'schema': 'delegated-evidence-plan-issue-v1', 'grant_id': grant.id,
          'manifest_hash': gd['manifest_hash'], 'allowance_preview_hash': gm['allowance_preview_hash']}),
        (permit, 'cloud.extra_allowance.issued_v1',
         {'schema': 'cloud-extra-allowance-issue-v1', 'permit_id': permit.id, 'manifest_hash': pd['manifest_hash']}),
    ):
        audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
            Audit.object_id == record.id, Audit.action == action).limit(2)
            .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
        if len(audits) != 1 or audits[0].id != record.data['audit_id'] or audits[0].actor_id != principal.user_id or audits[0].data != expected:
            _deny()
        checked_audits.append(audits[0])

    maps = row.data['maps']
    if ([item['chunk_id'] for item in maps] != [item['stage_id'] for item in stages]
            or any(item['status'] != 'succeeded' for item in maps[:index])
            or any(item['status'] != 'pending' or item['inflight'] or item['reserved_call_id'] is not None
                   for item in maps[index + 1:])):
        _deny()
    current = maps[index]
    if (current['chunk_id'] != stage_id or current['status'] != 'running' or current['inflight'] is not True
            or current['reserved_call_id'] != call_id or current['journal'] is not None):
        _deny()
    previous_ids = [item['call_id'] for item in used[:-1]]
    prior = db.scalars(select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id,
        CloudCall.id.in_(previous_ids)).order_by(CloudCall.id).with_for_update(nowait=True)
        .execution_options(populate_existing=True)).all() if previous_ids else []
    plans.validate_progress(stages, used[:-1], {item.id: item for item in prior},
        workspace_id=principal.workspace_id, actor_id=principal.user_id, capability_id=cap.id, job_id=job.id)
    call = _locked(db, select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id, CloudCall.id == call_id))
    if (not call or call.status != 'reserved' or call.capability_id != cap.id
            or call.reserved_tokens != entry['token_bound'] or call.charged_tokens != entry['token_bound']
            or call.data.get('actor_id') != principal.user_id or call.data.get('request_hash') != entry['request_hash']
            or call.data.get('dispatch_options_hash') != entry['options_hash']
            or call.data.get('input_policy_hash') != entry['input_policy_hash']
            or call.data.get('scoped_extra') != {'schema': 'cloud-extra-call-v1', 'permit_id': permit.id,
                'job_id': job.id, 'stage_id': stage_id, 'scope_hash': scope, 'manifest_hash': pd['manifest_hash']}):
        _deny()
    day = call.data.get('day')
    budget = _locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == 'cloud-budget:' + str(day)))
    if (not budget or budget.kind != 'cloud_budget' or budget.status != 'internal'
            or type(budget.data) is not dict or budget.data.get('day') != day
            or type(budget.data.get('tokens')) is not int or budget.data['tokens'] < entry['token_bound']
            or type(budget.data.get('calls')) is not int or budget.data['calls'] < 1):
        _deny()
    req, spec = evidence._request(row, stage_id), evidence._spec(row, stage_id)
    schema = legacy.canonical_schema(spec['output_schema'])
    actual, _, images = prepare_cloud_json(cap, req['messages'][1:], schema)
    actual_options = evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), spec['evidence_stage'])
    if (images or digest(actual) != digest(req['messages']) or digest(messages) != entry['request_hash']
            or digest(actual) != entry['request_hash'] or digest(options) != entry['options_hash']
            or digest(actual_options) != entry['options_hash']):
        _deny()
    if require_capture:
        _capture_ready(row, stage_id, sink)
    return row, job, cap, user, member, grant, permit, dispatch, call, budget, prior, checked_audits, entry, scope, epoch_records


def prepare_prebound_capture(db, principal, stage_id, call_id, messages, options):
    """Prepare one exact response sink for a committed prebound call.

    This checks the already recorded call instead of reserving again. The
    worker transaction is committed before local FS creation; the separate
    transport-intent function must recheck every permission and binding.
    The sink alone grants no authority and cannot be used for a retry.
    """
    from . import report_evidence_pipeline as evidence
    from . import report_evidence_delegated_wire_v1 as wire
    from . import report_evidence_http_capture_v1 as capture
    try:
        row, *_ = _check(db, principal, stage_id, call_id, messages, options, None,
                         require_capture=False)
        context = wire.context(evidence, row, stage_id)
        checkpoint(db)
        return capture.prepare(wire.root(), context)
    except (LeaseLost, ExecutionUnauthorized):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise PreboundTransportDenied('原预留或响应保存位置无法安全核对；未授权发送') from None


def commit_transport_intent(db, principal, stage_id, call_id, messages, options, sink):
    """Commit one conservative send-intent; no HTTP and no retry authority.

    A gateway integration must recompute and compare the exact transport body
    after this returns, must use only this call and prepared sink, and must
    never route a delegated task to the ordinary reservation path. That
    integration does not yet exist; production dispatch is still disabled.
    """
    from . import cloud_scoped_extra_reservation as scoped
    try:
        (row, job, cap, user, member, grant, permit, dispatch, call, budget,
         prior, checked_audits, entry, scope, epoch_records) = _check(db, principal, stage_id, call_id, messages, options, sink)
        transaction = db.get_transaction()
        checks = scoped._session_checks(db)
        if transaction is None or transaction in checks:
            _deny()
        gm = grant.data['manifest']; pm = permit.data['manifest']
        monotonic_start = _monotonic()
        issued_at = db.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)
        lease_until = job.lease_until.astimezone(timezone.utc)
        not_after = min(issued_at + timedelta(seconds=_MAX_INTENT_DELAY_SECONDS), lease_until,
                        _instant(gm['expires_at']), _instant(pm['expires_at']))
        for protected_record in epoch_records:
            if isinstance(protected_record, Job) and protected_record.kind == 'fl_scoped_activation_v1':
                not_after = min(not_after, _instant(protected_record.data['manifest']['expires_at']))
        if not_after <= issued_at:
            _deny()
        deadline = monotonic_start + (not_after-issued_at).total_seconds()
        intent = {'schema': 'delegated-evidence-transport-intent-v1',
            'workspace_id': principal.workspace_id, 'actor_id': principal.user_id,
            'draft_id': row.id, 'job_id': job.id, 'stage_id': stage_id, 'call_id': call_id,
            'permit_id': permit.id, 'scope_hash': scope, 'request_hash': entry['request_hash'],
            'options_hash': entry['options_hash'], 'token_bound': entry['token_bound'],
            'issued_at':issued_at.isoformat(),'not_after':not_after.isoformat(),'lease_until':lease_until.isoformat()}
        dispatch.data = {**dispatch.data, 'state': 'transport_intent', 'intent': intent, 'intent_hash': digest(intent)}
        intent_audit = Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id,
            object_id=dispatch.id, action='cloud.delegated_evidence.transport_intent_v1',
            data={'schema': intent['schema'], 'intent_hash': digest(intent)})
        db.add(intent_audit)
        db.flush()
        snapshot = copy.deepcopy(dispatch.data)
        row_hash, grant_hash, permit_hash = digest(row.data), digest(grant.data), digest(permit.data)
        call_hash, budget_hash = digest(call.data), digest(budget.data)
        job_hash, cap_hash = digest(job.data), digest(cap.data)
        call_snapshots = [_call_snapshot(db, item) for item in [call, *prior]]
        for item in epoch_records:
            if inspect(item).unloaded:db.refresh(item)
        epoch_snapshots = [_call_snapshot(db,item) for item in epoch_records]
        protected = [row, job, cap, user, member, grant, permit, dispatch, budget,
                     *checked_audits, intent_audit]
        for item in protected:
            if inspect(item).unloaded:db.refresh(item)
        protected_snapshots = [_call_snapshot(db,item) for item in protected]
        audit_snapshot = [(item, item.id, item.actor_id, item.object_id,
                           item.action, digest(item.data)) for item in [*checked_audits, intent_audit]]
        row_version, cap_content_hash = row.version, cap.content_hash
        grant_id, permit_id, dispatch_id, budget_id = grant.id, permit.id, dispatch.id, budget.id
        frozen_messages, frozen_options = copy.deepcopy(messages), copy.deepcopy(options)
        messages_hash, options_hash = digest(frozen_messages), digest(frozen_options)
        not_before = _instant(grant.data['manifest']['not_before'])
        expires_at = _instant(grant.data['manifest']['expires_at'])
        permit_not_before = _instant(permit.data['manifest']['not_before'])
        permit_expires_at = _instant(permit.data['manifest']['expires_at'])
        lease = ownership(db)
        transaction_id = db.get_transaction()
        def before_commit(session):
            # Check ORM lifecycle before any attribute access can implicitly
            # refresh an expired identity and conceal the intervening change.
            for call_snapshot in call_snapshots:
                _check_call_snapshot(session, call_snapshot)
            for epoch_snapshot in epoch_snapshots:
                _check_call_snapshot(session, epoch_snapshot)
            for protected_snapshot in protected_snapshots:
                _check_call_snapshot(session, protected_snapshot)
            clock = session.scalar(select(func.clock_timestamp()))
            if (session.get_transaction() is not transaction_id or not fenced(session)
                    or session.info.get('worker_actor') != principal or ownership(session) != lease
                    or any(item in session.deleted for item in [row, job, cap, user, member, grant, permit, dispatch, call, budget, *prior, *checked_audits, intent_audit])
                    or job.id != lease['job_id'] or job.workspace_id != principal.workspace_id
                    or job.status != 'running' or job.lease_token != lease['lease_token']
                    or not job.lease_until or job.lease_until <= clock or digest(job.data) != job_hash
                    or row.workspace_id != principal.workspace_id or row.actor_id != principal.user_id
                    or row.status != 'running' or row.version != row_version
                    or user.id != principal.user_id or not user.active
                    or member.user_id != principal.user_id or member.workspace_id != principal.workspace_id
                    or member.role != principal.role or cap.status != 'published'
                    or cap.workspace_id != principal.workspace_id or cap.content_hash != cap_content_hash
                    or cap.validated_hash != cap_content_hash or digest(cap.data) != cap_hash
                    or grant.id != grant_id or permit.id != permit_id or dispatch.id != dispatch_id
                    or budget.id != budget_id or call.id != call_id
                    or not (not_before <= clock < expires_at and permit_not_before <= clock < permit_expires_at)
                    or clock >= not_after or _monotonic() >= deadline
                    or digest(messages) != messages_hash or digest(options) != options_hash
                    or digest(row.data) != row_hash or digest(grant.data) != grant_hash
                    or digest(permit.data) != permit_hash or digest(call.data) != call_hash
                    or digest(budget.data) != budget_hash or dispatch.data != snapshot
                    or call.status != 'reserved'):
                _deny()
            for item, identity, actor, obj, action, data_hash in audit_snapshot:
                if (item.id != identity or item.actor_id != actor or item.object_id != obj
                        or item.action != action or digest(item.data) != data_hash):
                    _deny()
            _capture_ready(row, stage_id, sink)
        checks[transaction] = before_commit
        sink_context = sink.context
        capability_id = cap.id
        # A later before_commit listener may dirty an ORM instance after our
        # first check. The commit flush must pass the same immutable snapshot.
        def after_flush_postexec(session, _context):
            for call_snapshot in call_snapshots:
                _check_call_snapshot(session, call_snapshot)
            for snapshot in [*epoch_snapshots,*protected_snapshots]:
                _check_call_snapshot(session,snapshot)
        event.listen(db, 'after_flush_postexec', after_flush_postexec)
        try:
            checkpoint(db)
        finally:
            event.remove(db, 'after_flush_postexec', after_flush_postexec)
        # The intent is now durable. A changed caller-owned input is an
        # uncertain/terminal outcome, never a reason to recreate a witness.
        if _monotonic() >= deadline or digest(messages) != messages_hash or digest(options) != options_hash:
            _deny()
        return CommittedTransportIntent(_WITNESS, principal, capability_id, stage_id, call_id,
                                        frozen_messages, frozen_options, sink, sink_context, digest(intent),deadline)
    except (LeaseLost, ExecutionUnauthorized):
        db.rollback()
        raise
    except Exception:
        # A lock conflict or any malformed/changed state is a denial. Roll back
        # here so a partially prepared intent cannot be committed by a caller.
        db.rollback()
        raise PreboundTransportDenied('原预留、任务或派发状态无法安全核对；未授权发送') from None
