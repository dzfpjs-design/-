"""Explicit wait-source pins and task-scoped reads of actual admission receipts.

Registration is not a subscription grant. The caller verifies the actual release
ledger/live authority before using these helpers, and verifies source bindings
before taking a cut. Public original-actor receipt APIs remain unchanged. A cut
uses a separate connection and never locks a Task, Job, actor or source record.
Admission time remains v1's database decision time, not a commit timestamp.
"""
import copy
import math
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import digest
from .models import AuthSession, Capability, InstanceRelease, Membership, Task, User
from .services import require
from . import full_semantic_graph_v1 as pure
from . import graph_event_admission as admission
from . import graph_event_source_families_v136 as sources
from .graph_event_models import GraphEventAdmission, GraphEventIntent, GraphEventProducer


POLICY = {
    'schema': 'graph-wait-source-policy-v1',
    'arrival_basis': 'durable_admission',
    'sequence': {'open': 'exclusive', 'closed_cut': 'inclusive', 'order': 'ascending'},
    'deadline': {'clock': 'database_clock_timestamp', 'boundary': 'exclusive',
        'basis': 'admission_decision_time'},
    'transition': 'buffer_complete_batch_before_advance',
    'clock_regression': 'block',
    'result_scope': 'candidate_only',
    'source_time_verified': False,
    'physical_state_verified': False,
    'revocation': {'new_admission': 'block', 'new_execution': 'block',
        'history': 'preserve', 'draft': 'preserve', 'cancel': 'preserve'},
}
_POLICY_BYTES = pure._safe(POLICY)
_BINDING_KEYS = {'producer_id', 'contract_hash', 'contract', 'scope'}
_SCOPE_KEYS = {'entity_policy', 'correlation_ref', 'source_policy'}


def _need(ok, code):
    if not ok:
        raise ValueError('graph_wait_sources_v118_' + code)


def _same(left, right):
    return pure._safe(left) == pure._safe(right)


def _uuid(value):
    _need(type(value) is str and str(UUID(value)) == value, 'identity')
    return value


def _sequence(value):
    _need(type(value) is int and 0 <= value <= admission.MAX_SEQUENCE, 'sequence')
    return value


def _epoch(value):
    _need(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'clock')
    return value


def _stamp(value):
    _need(isinstance(value, datetime) and value.tzinfo is not None
        and value.utcoffset() is not None, 'database_clock')
    return _epoch(value.timestamp())


def _cut(value):
    _need(type(value) is dict and set(value) == {'sequence', 'at'}, 'cut')
    _sequence(value['sequence']); _epoch(value['at'])
    return value


def _failure(error):
    raise HTTPException(409, '等待来源、任务范围或接纳序列无法核对；原记录保留') from error


def check_policy(value):
    """Check the complete, versioned policy without granting live authority."""
    _need(pure._safe(value) == _POLICY_BYTES, 'policy')
    return copy.deepcopy(value)


def check_bindings(graph, bindings):
    """Exact subscription inventory and intact producer output envelopes."""
    graph = pure.validate_graph(graph); pure._safe(bindings)
    subscriptions = {item['id']: item for item in graph['subscriptions']}
    _need(type(bindings) is dict and set(bindings) == set(subscriptions), 'binding_inventory')
    for identity, subscription in subscriptions.items():
        value = bindings[identity]
        _need(type(value) is dict and set(value) == _BINDING_KEYS, 'binding_shape')
        _uuid(value['producer_id'])
        contract = sources.check_contract(value['contract'])
        _need(_same(contract, value['contract']) and value['contract_hash'] == digest(contract), 'contract_pin')
        scope = value['scope']
        _need(type(scope) is dict and set(scope) == _SCOPE_KEYS
            and scope == {'entity_policy': 'frozen_task_entities',
                'correlation_ref': subscription['correlation_ref'],
                'source_policy': 'exact_registered_source'}, 'binding_scope')
        reference = graph['references'][subscription['correlation_ref']]
        _need(reference['kind'] == 'string' and contract['correlation_kind'] == 'entity_id', 'correlation_type')
        _need(subscription['event_type'] == contract['event_kind']
            and _same(subscription['output_schema'], contract['output_schema']), 'subscription_contract')
    return copy.deepcopy(bindings)


def _actor(db, p, *, lock=False):
    require(p, 'admin', 'operator'); _uuid(p.user_id); _uuid(p.workspace_id)
    if lock:
        from .relations import lock_current_actor
        lock_current_actor(db, p)
        return
    query = select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator']))
    try:
        session_id = str(UUID(p.session_id))
    except (ValueError, TypeError, AttributeError):
        session_id = None
    if session_id is not None:
        query = query.join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.id == session_id, AuthSession.revoked.is_(False),
            AuthSession.expires_at > func.clock_timestamp())
    if db.scalar(query) is None:
        raise HTTPException(403, '当前账号、工作区权限或登录会话已变化；原记录保留')


def _row(db, model, identity, p, *, lock=False):
    _uuid(identity)
    query = select(model).where(model.id == identity, model.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update(read=True)
    row = db.scalar(query.execution_options(populate_existing=True))
    _need(row is not None and row.workspace_id == p.workspace_id, 'workspace_row')
    return row


def _producer_pin(row, binding):
    contract = admission.check_producer(row)
    _need(row.id == binding['producer_id'] and row.contract_hash == binding['contract_hash']
        and _same(contract, binding['contract']), 'producer_pin')
    return contract


def verify_bindings(db, p, graph, bindings, *, lock=False):
    """Verify current actor and actual registered/live source pins before cut.

    This reuses an existing registration; it neither registers a new producer
    nor acts as its original registrar/processing actor. ``lock=True`` retains
    shared authority locks in the caller's transaction, before any cut cursor.
    """
    try:
        _need(type(lock) is bool, 'lock'); checked = check_bindings(graph, bindings)
        _actor(db, p, lock=lock)
        contracts = []
        for identity in sorted(checked):
            binding = checked[identity]
            producer = _row(db, GraphEventProducer, binding['producer_id'], p, lock=lock)
            contract = _producer_pin(producer, binding)
            _need(producer.status == 'active', 'producer_revoked')
            contracts.append((contract, producer))
        for contract, producer in contracts:
            sources.verify_contract_current(db, p, contract, producer=producer, lock=lock)
        return checked
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        _failure(None)


def take_cut(db, p):
    """Commit a short independent READ COMMITTED stream-only cut transaction."""
    try:
        require(p, 'admin', 'operator'); _uuid(p.user_id); _uuid(p.workspace_id)
        # A caller may be bound to a Connection that already holds a Task/Job.
        # Bind the new Session to its Engine, never to that existing connection.
        bind = db.get_bind(); bind = getattr(bind, 'engine', bind)
        with Session(bind.execution_options(isolation_level='READ COMMITTED'), expire_on_commit=False) as reader:
            stream = admission._stream(reader, p)
            count, minimum, maximum, latest_time = reader.execute(select(
                func.count(GraphEventAdmission.id), func.min(GraphEventAdmission.sequence),
                func.max(GraphEventAdmission.sequence), func.max(GraphEventAdmission.admitted_at))
                .where(GraphEventAdmission.workspace_id == p.workspace_id)).one()
            sequence = _sequence(stream.last_sequence)
            _need((sequence == 0 and count == 0 and minimum is maximum is latest_time is None)
                or (sequence > 0 and minimum == 1 and maximum == count == sequence), 'stream_prefix')
            at = _stamp(reader.scalar(select(func.clock_timestamp())))
            if latest_time is not None:
                _need(_stamp(latest_time) <= at, 'clock_regression')
            result = {'sequence': sequence, 'at': at}
            reader.commit()
        return result
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        _failure(None)


def _task_scope(db, p, task, graph, bindings):
    from .instance_semantic_runtime_dispatch import modern
    actual = _row(db, Task, task.id, p)
    _need(task.workspace_id == p.workspace_id and _same(task.data, actual.data), 'task_scope')
    release = _row(db, InstanceRelease, actual.data['instance_release_id'], p)
    _need(release.instance_id == actual.data['instance_id'] and release.package_id == actual.package_id,
        'task_release')
    businesses = [item for item in release.data['snapshot']['businesses']
        if item['id'] == actual.data['business_id']]
    _need(len(businesses) == 1, 'task_business')
    frozen = modern(release.data['snapshot']).business_binding(release, businesses[0])
    _need(frozen.get('schema') in {'report-full-semantic-graph-binding-v4', 'report-object-graph-binding-v171'}
        and _same(frozen['graph'], graph) and _same(frozen['event_source_bindings'], bindings), 'task_binding')
    check_policy(frozen['event_admission_policy'])
    for binding in bindings.values():
        sources.require_instance(binding['contract'], release.instance_id)
    allowed = {actual.owner_id} | {node['actor_id'] for node in graph['nodes']
        if node['kind'] in {'human', 'ability'}}
    if p.role != 'admin' and p.user_id not in allowed:
        raise HTTPException(403, '此账号未获该任务的消息处理范围；原消息历史保留')
    entities = actual.data['entity_ids']; snapshots = actual.data['object_snapshots']
    _need(type(entities) is list and bool(entities) and len(entities) == len(set(entities))
        and type(snapshots) is list and all(type(item) is dict for item in snapshots), 'task_entities')
    for identity in entities:
        _uuid(identity)
    snapshot_ids = [item['id'] for item in snapshots]
    _need(len(snapshot_ids) == len(set(snapshot_ids)) and set(snapshot_ids) == set(entities)
        and actual.data['graph_wait_authority']['scope_hash'] == digest(snapshots), 'task_snapshot_scope')
    _cut(actual.data['graph_wait_authority']['cursor'])
    return set(entities)


def _prefix(db, p, cut, after, producer_ids, *, floor_at=None):
    """Prove the whole closed prefix and clock order before producer filtering."""
    expected = 1; previous_at = None; relevant = []
    query = select(GraphEventAdmission.sequence, GraphEventAdmission.admitted_at,
        GraphEventAdmission.producer_id).where(GraphEventAdmission.workspace_id == p.workspace_id,
            GraphEventAdmission.sequence <= cut['sequence']).order_by(GraphEventAdmission.sequence)
    for sequence, stamp, producer_id in db.execute(query.execution_options(yield_per=1000)):
        _need(type(sequence) is int and sequence == expected, 'prefix_gap')
        at = _stamp(stamp)
        _need(at <= cut['at'] and (previous_at is None or previous_at <= at), 'clock_regression')
        if sequence > after and floor_at is not None:
            _need(at >= floor_at, 'clock_regression')
        _uuid(producer_id)
        if sequence > after and producer_id in producer_ids:
            relevant.append(sequence)
        previous_at = at; expected += 1
    _need(expected == cut['sequence'] + 1, 'prefix_incomplete')
    return relevant


def read_receipts(db, p, task, graph, bindings, *, after, cut):
    """Read approved task projections across actors, never their private proof.

    The outer caller already holds Task/live authority and verified source pins
    before its independently released cut. This reader takes no cursor or source
    lock and does not call source IO, a producer, or the original-actor APIs.
    """
    try:
        checked = check_bindings(graph, bindings); _sequence(after); _cut(cut)
        _need(after <= cut['sequence'], 'cut_regression'); _actor(db, p)
        entities = _task_scope(db, p, task, graph, checked)
        cursor = _cut(task.data['graph_wait_authority']['cursor'])
        _need(after == cursor['sequence'] and cut['at'] >= cursor['at'], 'task_cursor')
        _need(cut['at'] <= _stamp(db.scalar(select(func.clock_timestamp()))), 'future_cut')
        pins = {item['producer_id']: item for item in checked.values()}
        expected = _prefix(db, p, cut, after, pins, floor_at=cursor['at'])
        rows = list(db.scalars(select(GraphEventAdmission).where(
            GraphEventAdmission.workspace_id == p.workspace_id,
            GraphEventAdmission.sequence > after, GraphEventAdmission.sequence <= cut['sequence'],
            GraphEventAdmission.producer_id.in_(sorted(pins))).order_by(GraphEventAdmission.sequence)))
        _need([row.sequence for row in rows] == expected, 'prefix_changed')
        producers = {}; result = []
        for row in rows:
            if row.producer_id not in producers:
                producer = _row(db, GraphEventProducer, row.producer_id, p)
                _producer_pin(producer, pins[row.producer_id])
                _need(producer.status == 'active', 'producer_revoked')
                producers[row.producer_id] = producer
            producer = producers[row.producer_id]
            intent = _row(db, GraphEventIntent, row.intent_id, p)
            frozen = admission.check_admission(row, intent, producer)
            projection = frozen['projection']
            if projection['correlation'] not in entities:
                continue
            _need(projection['output']['entity_id'] == projection['correlation'], 'projection_entity')
            result.append({'event_id': row.id, 'event_type': producer.data['contract']['event_kind'],
                'correlation': projection['correlation'], 'output': copy.deepcopy(projection['output']),
                'sequence': row.sequence, 'received_at': _stamp(row.admitted_at),
                'producer_id': row.producer_id, 'contract_hash': frozen['contract_hash']})
        return result
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        _failure(None)
