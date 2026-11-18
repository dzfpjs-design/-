"""Default-closed, one-shot transport intent for a separately scoped reduce.

No network calls happen here. A committed intent is an unknown delivery, never
an instruction to retry. All map evidence is revalidated before authorization.
"""
from __future__ import annotations

import copy
from datetime import timedelta

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from .contracts import digest
from .leases import ExecutionUnauthorized, LeaseLost, checkpoint, fenced, ownership
from .models import Audit, Capability, CloudCall, Job, Membership, User
from .security import Principal
from . import report_evidence_prebound_transport as common


class ReduceTransportDenied(ValueError):
    pass


def _deny():
    raise ReduceTransportDenied('汇总原预留、证据或派发状态无法安全核对；未授权发送')


def _snapshots(db):
    # Do not implicitly reload expired/detached data and thereby mask mutation.
    result = []
    for item in list(db.identity_map.values()):
        state = inspect(item)
        if (not state.persistent or state.session is not db or state.deleted
                or state.key is None or db.identity_map.get(state.key) is not item
                or item in db.deleted or state.expired_attributes or state.unloaded):
            _deny()
        result.append((item, state.key, {col.key: copy.deepcopy(getattr(item, col.key))
                                      for col in state.mapper.column_attrs}))
    return result


def _check_snapshots(db, values):
    for item, key, columns in values:
        state = inspect(item)
        if (not state.persistent or state.session is not db or state.deleted
                or state.key != key or db.identity_map.get(key) is not item
                or item in db.deleted or state.expired_attributes or state.unloaded
                or set(columns) != {col.key for col in state.mapper.column_attrs}):
            _deny()
        for name, expected in columns.items():
            actual = getattr(item, name)
            if (type(actual) is not type(expected) or actual != expected
                    or isinstance(expected, (dict, list)) and digest(actual) != digest(expected)):
                _deny()


def _check_database_snapshots(connection, snapshots):
    """Read the final SQL transaction state immediately before DB commit.

    A late Session.before_commit listener may issue SQL directly, which does
    not dirty ORM objects and may therefore skip after_flush_postexec entirely.
    Compare every validated persistent row on the same connection; absence or
    any column drift fails the entire intent transaction closed.
    """
    for item, _key, columns in snapshots:
        mapper = inspect(item).mapper
        properties = list(mapper.column_attrs)
        selected = [prop.columns[0] for prop in properties]
        predicates = []
        for column in mapper.primary_key:
            name = mapper.get_property_by_column(column).key
            predicates.append(column == columns[name])
        if not predicates:
            _deny()
        fetched = connection.execute(select(*selected).where(*predicates)).one_or_none()
        if fetched is None:
            _deny()
        for prop, actual in zip(properties, fetched):
            expected = columns[prop.key]
            if (type(actual) is not type(expected) or actual != expected
                    or isinstance(expected, (dict, list)) and digest(actual) != digest(expected)):
                _deny()


def _check(db, principal, stage_id, call_id, messages, options, sink, *, require_capture):
    from . import cloud_report_plan_allowance as plans
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_dispatch_gate as gate
    from . import report_evidence_delegated_reduce_preflight as preflight
    from . import report_evidence_delegated_reduce_start as start
    from . import report_evidence_pipeline as evidence
    from . import report_reading_drafts as legacy
    from .cloud_evidence_profiles import evidence_options
    from .cloud_provider_policy import provider_enabled
    from .gateway import cloud_json_options, prepare_cloud_json

    if (not isinstance(db, Session) or not fenced(db)
            or type(principal) is not Principal or principal.role != 'admin'
            or db.info.get('worker_actor') != principal or stage_id != 'reduce'
            or type(call_id) is not str or not call_id
            or type(messages) is not list or type(options) is not dict):
        _deny()
    owner = ownership(db)
    if set(owner) != {'job_id', 'lease_token'} or not owner['job_id'] or not owner['lease_token']:
        _deny()
    locked = common._locked
    job = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id, Job.id == owner['job_id']))
    permit = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == scoped.permit_key(owner['job_id'], stage_id)))
    clock = db.scalar(select(func.clock_timestamp()))
    if (not job or job.status != 'running' or job.kind != legacy.KIND or job.lease_token != owner['lease_token']
            or job.lease_until is None or job.lease_until.tzinfo is None or job.lease_until <= clock
            or type(job.data) is not dict or job.data.get('actor_id') != principal.user_id
            or type(job.data.get('report_draft_id')) is not str
            or not permit or permit.kind != 'cloud_extra_allowance_v1' or permit.status != 'internal'):
        _deny()
    legacy.workspace_draft(db, principal)
    row = evidence.owned(db, principal, job.data['report_draft_id'], True)
    if (not row.request_id.startswith('fl_') or row.status != 'running'
            or row.data.get('phase') != 'reduce' or row.data.get('job_id') != job.id):
        _deny()
    grant = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == 'evidence-reduce-allowance-v1:' + digest({'job_id': job.id})))
    dispatch = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == scoped.dispatch_key(job.id, stage_id)))
    if (not grant or grant.kind != 'cloud_report_reduce_allowance_v1' or grant.status != 'internal'
            or not dispatch or dispatch.kind != 'cloud_extra_dispatch_v1' or dispatch.status != 'internal'):
        _deny()
    gd, pd, dd = grant.data, permit.data, dispatch.data
    if (type(gd) is not dict or set(gd) != {'schema','manifest','manifest_hash','audit_id','used_call_id'}
            or gd['schema'] != 'delegated-reduce-allowance-record-v1' or gd['manifest_hash'] != digest(gd['manifest'])
            or type(pd) is not dict or set(pd) != {'schema','manifest','manifest_hash','audit_id','used_call_id'}
            or pd['schema'] != 'cloud-extra-allowance-record-v1' or pd['manifest_hash'] != digest(pd['manifest'])
            or type(dd) is not dict or dd.get('schema') != 'cloud-extra-dispatch-v1' or dd.get('state') != 'reserved'
            or gd['used_call_id'] != call_id or pd['used_call_id'] != call_id):
        _deny()
    gm, pm = gd['manifest'], pd['manifest']
    if (type(gm) is not dict or type(pm) is not dict or type(gm.get('preflight')) is not dict
            or frozenset(pm) != scoped._MANIFEST_KEYS
            or set(gm) != {'schema','workspace_id','actor_id','draft_id','job_id','original_job_id',
                'preflight','preflight_hash','source_hash','map_results_hash','reduction_preview_hash',
                'reduction_preparation_hash','model_id','model_hash','request_hash','options_hash',
                'policy_hash','token_bound','max_calls','not_before','expires_at','scope_hash','user_confirmed_business'}
            or gm.get('schema') != 'delegated-reduce-allowance-v1'):
        _deny()
    cap = evidence._fresh_cap(db, principal, row)
    cap = locked(db, select(Capability).where(Capability.workspace_id == principal.workspace_id, Capability.id == cap.id))
    user = locked(db, select(User).where(User.id == principal.user_id))
    member = locked(db, select(Membership).where(Membership.workspace_id == principal.workspace_id,
        Membership.user_id == principal.user_id))
    if (not cap or cap.kind != 'foundation_model' or cap.status != 'published'
            or cap.validated_hash != cap.content_hash or digest(cap.data) != cap.content_hash
            or cap.data.get('execution', {}).get('provider') != 'deepseek'
            or cap.data.get('data_policy', {}).get('cloud_allowed') is not True
            or not provider_enabled('deepseek') or not user or not user.active or not member or member.role != 'admin'):
        _deny()
    if (gm.get('workspace_id') != principal.workspace_id or gm.get('actor_id') != principal.user_id
            or gm.get('draft_id') != row.id or gm.get('job_id') != job.id or gm.get('model_id') != cap.id
            or gm.get('model_hash') != cap.content_hash or gm.get('max_calls') != 1
            or gm.get('user_confirmed_business') is not False or type(gm.get('token_bound')) is not int
            or type(gm.get('max_calls')) is not int or gm['token_bound'] < 1
            or gm.get('preflight_hash') != gm['preflight'].get('preflight_hash')
            or gm.get('original_job_id') != gm['preflight'].get('original_job_id')
            or gm.get('source_hash') != gm['preflight'].get('source_hash')
            or gm.get('map_results_hash') != gm['preflight'].get('map_results_hash')
            or gm.get('reduction_preview_hash') != gm['preflight'].get('reduction_preview_hash')
            or gm.get('reduction_preparation_hash') != row.data['reduction']['preview']['reduction_preparation']['preparation_hash']
            or gm.get('token_bound') != gm['preflight'].get('token_bound')):
        _deny()
    scope = start.scope_hash(principal, row, job.id, gm['preflight'])
    if (gm.get('scope_hash') != scope or pm.get('scope_kind') != start.SCOPE or pm.get('scope_hash') != scope
            or pm.get('workspace_id') != principal.workspace_id or pm.get('actor_id') != principal.user_id
            or pm.get('issuer_id') != principal.user_id or pm.get('job_id') != job.id
            or pm.get('job_kind') != job.kind or pm.get('permit_id') != permit.id
            or pm.get('stage_id') != stage_id or pm.get('capability_id') != cap.id
            or pm.get('capability_hash') != cap.content_hash or type(pm.get('max_calls')) is not int or pm.get('max_calls') != 1
            or type(pm.get('token_bound')) is not int or type(pm.get('max_tokens')) is not int
            or pm.get('token_bound') != gm['token_bound'] or pm.get('max_tokens') != gm['token_bound']
            or pm.get('request_hash') != gm.get('request_hash') or pm.get('options_hash') != gm.get('options_hash')
            or dd != {'schema':'cloud-extra-dispatch-v1','permit_id':permit.id,'job_id':job.id,
                'stage_id':stage_id,'scope_hash':scope,'call_id':call_id,'state':'reserved','manifest_hash':pd['manifest_hash']}):
        _deny()
    if not (common._instant(gm.get('not_before')) <= clock < common._instant(gm.get('expires_at'))
            and common._instant(pm.get('not_before')) <= clock < common._instant(pm.get('expires_at'))):
        _deny()
    old_job = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id, Job.id == gm.get('original_job_id')))
    if (not old_job or old_job.status != 'succeeded' or old_job.lease_until and old_job.lease_until > clock):
        _deny()
    activation=None
    if not gate.ready():
        # This row remains locked until the intent transaction commits.
        from . import report_evidence_scoped_activation as activation_module
        activation=activation_module.for_job(db,principal,row,map_job_id=old_job.id,lock=True)
    ids = [s.get('reserved_call_id') for s in row.data['maps']]
    if len(ids) != 14 or len(set(ids)) != 14 or any(type(i) is not str or not i for i in ids):
        _deny()
    db.scalars(select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id, CloudCall.id.in_(ids))
        .order_by(CloudCall.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    map_keys = [plans.plan_key(old_job.id), *[key for s in row.data['maps'] for key in
        (scoped.permit_key(old_job.id,s['chunk_id']),scoped.dispatch_key(old_job.id,s['chunk_id']))]]
    map_records = db.scalars(select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key.in_(map_keys)).order_by(Job.id).with_for_update(nowait=True)
        .execution_options(populate_existing=True)).all()
    if len(map_records) != 29:
        _deny()
    db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
        Audit.object_id.in_([grant.id,permit.id,*[r.id for r in map_records]])).order_by(Audit.id)
        .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    for record, action, expected in ((grant, 'cloud.report_reduce_allowance.issued_v1',
            {'grant_id':grant.id,'manifest_hash':gd['manifest_hash']}),
        (permit, 'cloud.extra_allowance.issued_v1', {'schema':'cloud-extra-allowance-issue-v1',
            'permit_id':permit.id,'manifest_hash':pd['manifest_hash']})):
        audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
            Audit.object_id == record.id, Audit.action == action).limit(2)).all()
        if len(audits) != 1 or audits[0].id != record.data['audit_id'] or audits[0].actor_id != principal.user_id or audits[0].data != expected:
            _deny()
    stage = evidence._stage(row, stage_id)
    if (stage['status'] != 'running' or stage['inflight'] is not True
            or stage['reserved_call_id'] != call_id or stage['journal'] is not None):
        _deny()
    call = locked(db, select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id, CloudCall.id == call_id))
    if (not call or call.status != 'reserved' or call.capability_id != cap.id
            or call.reserved_tokens != gm['token_bound'] or call.charged_tokens != gm['token_bound']
            or call.data.get('actor_id') != principal.user_id or call.data.get('request_hash') != gm['request_hash']
            or call.data.get('dispatch_options_hash') != gm['options_hash'] or call.data.get('input_policy_hash') != gm['policy_hash']
            or call.data.get('scoped_extra') != {'schema':'cloud-extra-call-v1','permit_id':permit.id,
                'job_id':job.id,'stage_id':stage_id,'scope_hash':scope,'manifest_hash':pd['manifest_hash']}):
        _deny()
    day = call.data.get('day')
    budget = locked(db, select(Job).where(Job.workspace_id == principal.workspace_id, Job.idempotency_key == 'cloud-budget:' + str(day)))
    if (not budget or budget.kind != 'cloud_budget' or budget.status != 'internal' or type(budget.data) is not dict
            or budget.data.get('day') != day or type(budget.data.get('tokens')) is not int
            or budget.data['tokens'] < gm['token_bound'] or type(budget.data.get('calls')) is not int or budget.data['calls'] < 1):
        _deny()
    command = evidence.ReducePreviewCommand.model_validate(row.data['reduction']['preview']['body'])
    if preflight.preview(db, principal, row, command, _rechecking=True) != gm['preflight']:
        _deny()
    req, spec = evidence._request(row, stage_id), evidence._spec(row, stage_id)
    actual, _, images = prepare_cloud_json(cap, req['messages'][1:], legacy.canonical_schema(spec['output_schema']))
    actual_options = evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), spec['evidence_stage'])
    if (images or digest(actual) != digest(req['messages']) or digest(messages) != gm['request_hash']
            or digest(actual) != gm['request_hash'] or digest(options) != gm['options_hash']
            or digest(actual_options) != gm['options_hash'] or digest(spec['input_policy']) != gm['policy_hash']):
        _deny()
    if require_capture:
        common._capture_ready(row, stage_id, sink)
    return row, job, cap, grant, permit, dispatch, call, budget, scope, command, activation


def prepare_prebound_capture(db, principal, stage_id, call_id, messages, options):
    from . import report_evidence_pipeline as evidence
    from . import report_evidence_delegated_wire_v1 as wire
    from . import report_evidence_http_capture_v1 as capture
    try:
        row, *_ = _check(db, principal, stage_id, call_id, messages, options, None, require_capture=False)
        context = wire.context(evidence, row, stage_id)
        checkpoint(db)
        return capture.prepare(wire.root(), context)
    except (LeaseLost, ExecutionUnauthorized):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise ReduceTransportDenied('汇总原预留或响应保存位置无法安全核对；未授权发送') from None


def commit_transport_intent(db, principal, stage_id, call_id, messages, options, sink):
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_reduce_preflight as preflight
    try:
        # Session identity maps use weak references.  Keep every object loaded
        # by authorization and evidence validation alive until the snapshots
        # are taken, including users, memberships, origin records and all map
        # ledger/audit rows.  Otherwise a late before_commit listener could
        # reload and mutate a checked row that silently fell out of the map.
        retained = list(db.identity_map.values())
        def retain_loaded(_session, instance):
            retained.append(instance)
        event.listen(db, 'loaded_as_persistent', retain_loaded)
        try:
            row, job, cap, grant, permit, dispatch, call, budget, scope, command, activation = _check(
                db, principal, stage_id, call_id, messages, options, sink, require_capture=True)
        finally:
            event.remove(db, 'loaded_as_persistent', retain_loaded)
        transaction = db.get_transaction()
        checks = scoped._session_checks(db)
        if transaction is None or transaction in checks:
            _deny()
        gm = grant.data['manifest']
        # Bind the handoff to the shortest remaining worker lease, allowance,
        # permit, and 30 second lifetime. The monotonic anchor is taken before
        # the database clock read so any intervening delay is conservative.
        monotonic_start = common._monotonic()
        issued_at = db.scalar(select(func.clock_timestamp()))
        not_after = min(issued_at + timedelta(seconds=common._MAX_INTENT_DELAY_SECONDS), job.lease_until,
                        common._instant(gm['expires_at']),
                        common._instant(permit.data['manifest']['expires_at']))
        if activation is not None:
            not_after = min(not_after, activation[2])
        deadline = monotonic_start + (not_after - issued_at).total_seconds()
        if not_after <= issued_at or common._monotonic() >= deadline:
            _deny()
        intent = {'schema':'delegated-evidence-reduce-transport-intent-v1',
            'workspace_id':principal.workspace_id,'actor_id':principal.user_id,'draft_id':row.id,
            'job_id':job.id,'stage_id':stage_id,'call_id':call_id,'permit_id':permit.id,
            'scope_hash':scope,'request_hash':gm['request_hash'],'options_hash':gm['options_hash'],
            'token_bound':gm['token_bound'],'preflight_hash':gm['preflight_hash'],
            'issued_at':issued_at.isoformat(),'not_after':not_after.isoformat(),
            'lease_until':job.lease_until.isoformat()}
        dispatch.data = {**dispatch.data, 'state':'transport_intent','intent':intent,'intent_hash':digest(intent)}
        audit = Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,object_id=dispatch.id,
            action='cloud.delegated_evidence.reduce_transport_intent_v1',
            data={'schema':intent['schema'],'intent_hash':digest(intent)})
        db.add(audit);db.flush()
        snapshots = _snapshots(db)
        expected_preflight = copy.deepcopy(gm['preflight'])
        frozen_messages, frozen_options = copy.deepcopy(messages), copy.deepcopy(options)
        messages_hash, options_hash = digest(frozen_messages), digest(frozen_options)
        lease = ownership(db)
        not_before, expires_at = common._instant(gm['not_before']), common._instant(gm['expires_at'])
        permit_start, permit_end = common._instant(permit.data['manifest']['not_before']), common._instant(permit.data['manifest']['expires_at'])
        def check_at_commit(session):
            _check_snapshots(session, snapshots)
            clock = session.scalar(select(func.clock_timestamp()))
            if (session.get_transaction() is not transaction or not fenced(session)
                    or session.info.get('worker_actor') != principal or ownership(session) != lease
                    or job.status != 'running' or job.lease_token != lease['lease_token']
                    or not job.lease_until or job.lease_until <= clock
                    or not (not_before <= clock < expires_at and permit_start <= clock < permit_end)
                    or clock >= not_after or common._monotonic() >= deadline
                    or digest(messages) != messages_hash or digest(options) != options_hash):
                _deny()
            with session.no_autoflush:
                if preflight.preview(session, principal, row, command, _rechecking=True) != expected_preflight:
                    _deny()
            _check_snapshots(session, snapshots)
            common._capture_ready(row, stage_id, sink)
        checks[transaction] = check_at_commit
        def after_flush_postexec(session, _context):
            _check_snapshots(session, snapshots)
        # The connection hook runs after all Session before_commit listeners
        # and the final ORM flush, but before the database commit. It catches
        # same-transaction direct SQL changes to the protected rows as well.
        connection = db.connection()
        def check_database_at_commit(conn):
            if conn.execute(select(func.clock_timestamp())).scalar_one() >= not_after or common._monotonic() >= deadline:
                _deny()
            _check_snapshots(db, snapshots)
            _check_database_snapshots(conn, snapshots)
        event.listen(db,'after_flush_postexec',after_flush_postexec)
        event.listen(connection,'commit',check_database_at_commit)
        try:
            checkpoint(db)
        finally:
            event.remove(db,'after_flush_postexec',after_flush_postexec)
            event.remove(connection,'commit',check_database_at_commit)
        if common._monotonic() >= deadline or digest(messages) != messages_hash or digest(options) != options_hash:
            _deny()
        return common.CommittedTransportIntent(common._WITNESS, principal, cap.id, stage_id, call_id,
            frozen_messages, frozen_options, sink, sink.context, digest(intent), deadline)
    except (LeaseLost, ExecutionUnauthorized):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise ReduceTransportDenied('汇总原预留、证据或派发状态无法安全核对；未授权发送') from None
