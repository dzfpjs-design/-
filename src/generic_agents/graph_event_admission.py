"""Freeze real source receipts and serialize their typed admission decisions.

No function commits, dispatches a task/job, opens a wait, or performs source IO
while holding the workspace stream. ``admitted_at`` is the locked database
decision time; it is never claimed to be a PostgreSQL commit timestamp.
"""
import copy
import math
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from .models import Event, Observation
from .services import scoped
from .graph_event_models import (
    GraphEventProducer, GraphEventStream, GraphEventIntent, GraphEventAdmission,
)

MAX_SEQUENCE = (1 << 63) - 1
MAX_BYTES = 8 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_COMMAND = {'schema', 'request_id', 'label', 'adapter_id', 'source_id',
    'source_hash', 'extractor_id', 'extractor_hash'}


def fail(message='来源登记、原消息或接纳回执无法核对，原记录保留'):
    raise HTTPException(409, message)


def need(value, message=None):
    if not value:
        fail(message or '来源登记、原消息或接纳回执无法核对，原记录保留')


def _keys(value, names):
    return type(value) is dict and set(value) == set(names)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _integer(value, minimum=1):
    return type(value) is int and minimum <= value <= MAX_SEQUENCE


def _text(value, maximum=200):
    try:
        return (type(value) is str and 0 < len(value) <= maximum
            and '\x00' not in value and bool(value.encode('utf-8')))
    except UnicodeError:
        return False


def _safe(value, depth=0, remaining=None):
    remaining = [200000] if remaining is None else remaining
    remaining[0] -= 1
    need(remaining[0] >= 0)
    need(depth <= 64)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        return
    if type(value) is float:
        need(math.isfinite(value)); return
    if type(value) is str:
        need(not value or _text(value, MAX_BYTES)); return
    if type(value) is list:
        for item in value:
            _safe(item, depth + 1, remaining)
        return
    need(type(value) is dict)
    for key, item in value.items():
        need(_text(key, MAX_BYTES)); _safe(item, depth + 1, remaining)


def _sealed(value, field):
    _safe(value)
    need(_hash(value.get(field)))
    need(value[field] == digest({key: item for key, item in value.items() if key != field}))
    # Bound the exact JSON representation without retaining source text in logs.
    import json
    need(len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= MAX_BYTES)


def _seal(value, field):
    result = copy.deepcopy(value); result[field] = digest(result)
    _sealed(result, field)
    return result


def producer_command_hash(p, command):
    need(_uuid(p.user_id) and _uuid(p.workspace_id))
    need(_keys(command, _COMMAND) and command['schema'] == 'graph-event-producer-command-v1'
        and _uuid(command['request_id']) and _text(command['label']) and command['label'].strip()
        and _text(command['adapter_id']) and _uuid(command['source_id'])
        and _uuid(command['extractor_id']) and _hash(command['source_hash'])
        and _hash(command['extractor_hash']))
    return digest({'schema': 'graph-event-producer-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'command': copy.deepcopy(command)})


def check_producer(row):
    """Check the immutable registration; this alone grants no live permission."""
    from .graph_event_observation_sources import check_contract, registration_fields
    try:
        from .graph_event_source_families_v136 import record_family, NATIVE_AUDIO, NATIVE_LEARNED_GEO, check_native_producer
        if record_family(row) in {NATIVE_AUDIO, NATIVE_LEARNED_GEO}:
            return check_native_producer(row)
        need(row is not None and _uuid(row.id) and _uuid(row.workspace_id)
            and _uuid(row.registered_by) and _uuid(row.request_id)
            and _integer(row.version) and row.status in {'active', 'revoked'})
        value = row.data
        need(_keys(value, {'schema', 'label', 'contract', 'contract_hash', 'registration'})
            and value['schema'] == 'graph-event-producer-record-v1'
            and _text(value['label']) and value['label'].strip())
        _safe(value)
        contract = check_contract(value['contract'])
        need(contract == value['contract'])
        # The row pin hashes the complete contract, including its independent
        # internal contract_hash. These two hashes intentionally differ.
        need(_hash(row.contract_hash) and row.contract_hash == value['contract_hash'] == digest(contract))
        registration = value['registration']
        need(_keys(registration, {'request_id', 'actor_id', 'command_hash', 'command'})
            and registration['request_id'] == row.request_id
            and registration['actor_id'] == row.registered_by
            and registration['command_hash'] == row.command_hash and _hash(row.command_hash))
        command = registration['command']
        need(_keys(command, _COMMAND) and command['schema'] == 'graph-event-producer-command-v1'
            and command['request_id'] == row.request_id and command['label'] == value['label'])
        pins = registration_fields(contract)
        need(_keys(pins, {'adapter_id', 'source_id', 'source_hash', 'extractor_id', 'extractor_hash'})
            and all(command[key] == item for key, item in pins.items()))
        need(row.command_hash == digest({'schema': 'graph-event-producer-command-hash-v1',
            'actor_id': row.registered_by, 'workspace_id': row.workspace_id, 'command': command}))
        return copy.deepcopy(contract)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        fail()


def check_intent(row, producer):
    """Cold shape/identity checking, independent of current producer status."""
    from .graph_event_source_families_v136 import check_projection, projection_actor
    try:
        contract = check_producer(producer)
        need(row is not None and _uuid(row.id) and row.workspace_id == producer.workspace_id
            and row.producer_id == producer.id and _uuid(row.source_event_id)
            and _uuid(row.observation_id) and _uuid(row.actor_id) and _integer(row.version)
            and _text(row.source_key) and row.status in {'pending', 'admitted'}
            and ((row.status == 'pending' and row.receipt_id is None)
                or (row.status == 'admitted' and _uuid(row.receipt_id))))
        value = row.data
        need(_keys(value, {'schema', 'producer_id', 'contract_hash', 'source_event_id',
            'observation_id', 'source_key', 'actor_id', 'projection', 'projection_hash', 'intent_hash'})
            and value['schema'] == 'graph-event-source-intent-v1')
        _sealed(value, 'intent_hash')
        for key in ('producer_id', 'source_event_id', 'observation_id', 'source_key', 'actor_id'):
            need(value[key] == getattr(row, key))
        need(value['contract_hash'] == producer.contract_hash)
        projection = check_projection(value['projection'], contract)
        need(projection == value['projection'] and value['projection_hash'] == projection['projection_hash']
            and projection['source_event_id'] == row.source_event_id
            and projection['observation_id'] == row.observation_id
            and projection['source_key'] == row.source_key
            and projection['proof']['workspace_id'] == row.workspace_id
            and projection_actor(projection, contract) == row.actor_id)
        return copy.deepcopy(value)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        fail()


def _timestamp(value):
    need(isinstance(value, datetime) and value.tzinfo is not None
        and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def check_admission(row, intent, producer):
    """Bind every persisted receipt column to its actual immutable intent."""
    try:
        frozen = check_intent(intent, producer)
        need(row is not None and _uuid(row.id) and row.workspace_id == intent.workspace_id
            and row.producer_id == intent.producer_id and row.intent_id == intent.id
            and row.source_key == intent.source_key and _integer(row.sequence)
            and _integer(row.version) and intent.status == 'admitted' and intent.receipt_id == row.id)
        value = row.data
        need(_keys(value, {'schema', 'id', 'workspace_id', 'producer_id', 'intent_id', 'source_key',
            'contract_hash', 'source_intent_hash', 'projection', 'sequence', 'admitted_at', 'admission_hash'})
            and value['schema'] == 'graph-event-admission-v1')
        _sealed(value, 'admission_hash')
        for key in ('id', 'workspace_id', 'producer_id', 'intent_id', 'source_key', 'sequence'):
            need(value[key] == getattr(row, key))
        need(type(value['sequence']) is int
            and value['admitted_at'] == _timestamp(row.admitted_at)
            and value['contract_hash'] == producer.contract_hash
            and value['source_intent_hash'] == frozen['intent_hash']
            and value['projection'] == frozen['projection'])
        return copy.deepcopy(value)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        fail()


def _scope(row, p):
    need(row.workspace_id == p.workspace_id)
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原处理账号核对来源消息与接纳回执')


def _producer(db, p, identity, *, lock=False):
    query = select(GraphEventProducer).where(GraphEventProducer.id == identity,
        GraphEventProducer.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update(read=True)
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '来源登记不在当前工作区')
    check_producer(row)
    return row


def _rows(db, p, identity):
    intent = scoped(db, GraphEventIntent, identity, p)
    _scope(intent, p)
    producer = _producer(db, p, intent.producer_id)
    check_intent(intent, producer)
    linked = db.scalar(select(GraphEventAdmission).where(
        GraphEventAdmission.workspace_id == p.workspace_id, GraphEventAdmission.intent_id == intent.id))
    if linked is not None and intent.receipt_id != linked.id:
        # Under READ COMMITTED a concurrent admission may commit between these
        # reads. Refresh its atomically written pointer before judging it.
        intent = db.scalar(select(GraphEventIntent).where(GraphEventIntent.id == identity,
            GraphEventIntent.workspace_id == p.workspace_id).execution_options(populate_existing=True))
        need(intent is not None); _scope(intent, p); check_intent(intent, producer)
    need((intent.receipt_id is None and linked is None)
        or (linked is not None and intent.receipt_id == linked.id),
        '原消息与实际接纳指针不一致，原记录保留')
    return intent, producer


def _receipt(db, p, intent, producer):
    row = scoped(db, GraphEventAdmission, intent.receipt_id, p)
    check_admission(row, intent, producer)
    return row


def read_intent(db, p, identity):
    intent, producer = _rows(db, p, identity)
    if intent.receipt_id is not None:
        _receipt(db, p, intent, producer)
    frozen = check_intent(intent, producer)
    from .graph_event_source_families_v136 import verify_history
    try: verify_history(db, p, producer, frozen)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError, UnicodeError, OverflowError): fail()
    return frozen


def read_admission(db, p, identity):
    row = scoped(db, GraphEventAdmission, identity, p)
    intent, producer = _rows(db, p, row.intent_id)
    value = check_admission(row, intent, producer)
    from .graph_event_source_families_v136 import verify_history
    try: verify_history(db, p, producer, check_intent(intent, producer))
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError, UnicodeError, OverflowError): fail()
    return value


def _project(db, p, producer, event, observation):
    from .graph_event_source_families_v136 import project_source
    try:
        return project_source(db, p, producer, event, observation, live=True, lock=True)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError, UnicodeError, OverflowError):
        fail('实际来源、原处理账号或成功处理证明已变化，原消息保留')


def freeze_intent(db, p, producer, event, observation):
    """Called only by the real processing transaction after its source flush.

    Source validation/IO happens here, never under the stream cursor. A generic
    old Event cannot enter ``admit``: that entry accepts an actual intent ID.
    The caller owns atomic capture and must not swallow an active capture error.
    """
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    producer = _producer(db, p, producer.id, lock=True)
    need(producer.status == 'active', '来源登记已撤销，未新增消息或接纳序号')
    event = scoped(db, Event, event.id, p)
    observation = scoped(db, Observation, observation.id, p)
    projection = _project(db, p, producer, event, observation)
    from .graph_event_source_families_v136 import projection_actor
    need(projection_actor(projection, check_producer(producer)) == p.user_id)
    value = _seal({'schema': 'graph-event-source-intent-v1', 'producer_id': producer.id,
        'contract_hash': producer.contract_hash, 'source_event_id': event.id,
        'observation_id': observation.id, 'source_key': projection['source_key'], 'actor_id': p.user_id,
        'projection': projection, 'projection_hash': projection['projection_hash']}, 'intent_hash')
    identity = str(uuid4())
    created = db.scalar(insert(GraphEventIntent).values(id=identity, workspace_id=p.workspace_id,
        producer_id=producer.id, source_event_id=event.id, observation_id=observation.id,
        source_key=value['source_key'], actor_id=p.user_id, status='pending', receipt_id=None,
        data=value, version=1).on_conflict_do_nothing(
            index_elements=['workspace_id', 'producer_id', 'source_key']).returning(GraphEventIntent.id))
    row = db.scalar(select(GraphEventIntent).where(GraphEventIntent.workspace_id == p.workspace_id,
        GraphEventIntent.producer_id == producer.id, GraphEventIntent.source_key == value['source_key'])
        .execution_options(populate_existing=True))
    need(row is not None)
    _scope(row, p)
    need(check_intent(row, producer) == value, '同一来源编号已有不同正文，原消息保留，不能重新编号')
    if row.receipt_id is not None:
        _receipt(db, p, row, producer)
    return row, created is not None


def _stream(db, p):
    db.execute(insert(GraphEventStream).values(id=str(uuid4()), workspace_id=p.workspace_id,
        last_sequence=0).on_conflict_do_nothing(index_elements=['workspace_id']))
    row = db.scalar(select(GraphEventStream).where(GraphEventStream.workspace_id == p.workspace_id)
        .with_for_update().execution_options(populate_existing=True))
    need(row is not None and row.workspace_id == p.workspace_id and _integer(row.last_sequence, 0))
    return row


def admit(db, p, intent_id):
    """Allocate at most one actual receipt; the caller must commit or rollback.

    Historical exact recovery precedes fresh authorization. All source, account
    and producer checks precede the short stream critical section. No legacy
    timestamp, delivery acknowledgement or caller timestamp is admitted.
    """
    from .relations import lock_current_actor
    intent, producer = _rows(db, p, intent_id)
    if intent.receipt_id is not None:
        return _receipt(db, p, intent, producer), False
    lock_current_actor(db, p)
    event = scoped(db, Event, intent.source_event_id, p)
    observation = scoped(db, Observation, intent.observation_id, p)
    # Validate/lock source records before the producer/intent/cursor locks; an
    # actual processing transaction can already own its Observation row.
    try:
        projection = _project(db, p, producer, event, observation)
    except HTTPException:
        # A peer may have committed the original receipt while this duplicate
        # was checking a now-revoked live source. Recover that actual immutable
        # receipt, never manufacture a fresh authorization or sequence.
        latest, registered = _rows(db, p, intent_id)
        if latest.receipt_id is not None:
            return _receipt(db, p, latest, registered), False
        raise
    producer = _producer(db, p, intent.producer_id, lock=True)
    intent = scoped(db, GraphEventIntent, intent_id, p, True)
    _scope(intent, p)
    frozen = check_intent(intent, producer)
    if intent.receipt_id is not None:
        return _receipt(db, p, intent, producer), False
    need(producer.status == 'active', '来源登记已撤销，原消息保留，未新增接纳序号')
    need(db.scalar(select(GraphEventAdmission.id).where(GraphEventAdmission.intent_id == intent.id)) is None,
        '原消息已有未绑定的接纳回执，未新增序号')
    need(projection == frozen['projection'], '原成功处理或来源证明已变化，原消息保留，未重新编号')
    identity = str(uuid4())
    db.flush()  # No unrelated unit-of-work writes may first flush under cursor.
    stream = _stream(db, p)
    latest = db.scalar(select(func.max(GraphEventAdmission.sequence)).where(
        GraphEventAdmission.workspace_id == p.workspace_id))
    need((latest is None and stream.last_sequence == 0)
        or (_integer(latest) and latest == stream.last_sequence),
        '接纳序列与实际消息回执不一致，原消息保留')
    need(stream.last_sequence < MAX_SEQUENCE, '消息接纳序号已达上限，原消息保留')
    stamp = db.scalar(select(func.clock_timestamp()))
    last_stamp = db.scalar(select(func.max(GraphEventAdmission.admitted_at)).where(
        GraphEventAdmission.workspace_id == p.workspace_id))
    need(last_stamp is None or stamp >= last_stamp,
        '数据库时钟暂时回退，原消息保留；未分配序号，请恢复时钟后核对原请求')
    timestamp = _timestamp(stamp)
    sequence = stream.last_sequence + 1
    value = _seal({'schema': 'graph-event-admission-v1', 'id': identity,
        'workspace_id': p.workspace_id, 'producer_id': producer.id, 'intent_id': intent.id,
        'source_key': intent.source_key, 'contract_hash': producer.contract_hash,
        'source_intent_hash': frozen['intent_hash'], 'projection': frozen['projection'],
        'sequence': sequence, 'admitted_at': timestamp}, 'admission_hash')
    row = GraphEventAdmission(id=identity, workspace_id=p.workspace_id,
        producer_id=producer.id, intent_id=intent.id, source_key=intent.source_key,
        sequence=sequence, admitted_at=stamp, data=value, version=1)
    db.add(row); db.flush()
    stream.last_sequence = sequence
    intent.status = 'admitted'; intent.receipt_id = row.id; intent.version += 1
    db.flush()
    check_admission(row, intent, producer)
    return row, True
