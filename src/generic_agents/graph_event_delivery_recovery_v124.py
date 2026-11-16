"""Immutable retry outcomes beside the original source delivery Job.

The legacy command/hash/POST shapes stay intact. New commands never append to
the worker's legacy history, and neither cold receipts nor GETs repair a Job.
"""
import copy
import json
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import digest
from .db import session
from .graph_event_admission import check_producer, read_admission, read_intent
from .graph_event_models import GraphEventIntent, GraphEventProducer
from .models import Base, Entity, Job, Payload, Row, Scoped, now, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, scoped


SCHEMA = 'graph-event-intent-retry-receipt-v1'
_FIELDS = {'schema', 'id', 'request_id', 'actor_id', 'intent_id', 'producer_id', 'job_id',
    'intent_hash', 'command_hash', 'command', 'result', 'receipt_hash'}
_ADMISSION = {'schema', 'id', 'workspace_id', 'producer_id', 'intent_id', 'source_key', 'contract_hash',
    'source_intent_hash', 'projection', 'sequence', 'admitted_at', 'admission_hash'}
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ERROR = '原投递恢复命令或回执无法完整核对，原记录保留'
_STATUSES = {'queued', 'running', 'succeeded', 'failed', 'blocked', 'cancelled'}
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/graph-event-producers')


class RetryInputRoute(APIRoute):
    """Only the retry POST uses a validation error without rejected input."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def safe_input(request):
            try:
                return await original(request)
            except RequestValidationError:
                # A rejected surrogate may already be in Pydantic's input
                # detail before the unchanged RetryIntent reaches _command.
                return JSONResponse(status_code=422,
                    content={'detail': '恢复请求格式无法核对，原输入与原消息保留，未提交恢复操作'})
        return safe_input


class GraphEventIntentRetryReceipt(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_intent_retry_receipts'
    __table_args__ = (UniqueConstraint('workspace_id', 'intent_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    intent_id: Mapped[str] = mapped_column(ForeignKey('graph_event_intents.id'), index=True)
    producer_id: Mapped[str] = mapped_column(ForeignKey('graph_event_producers.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    command_hash: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(30))


def _fail(message=_ERROR):
    raise HTTPException(409, message)


def _need(value):
    if not value: _fail()


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _integer(value, minimum=1):
    return type(value) is int and value >= minimum


def _json(value):
    # Preserve legacy reason whitespace. Reject nonfinite numbers/lone
    # surrogates before hashing; _command also rejects PostgreSQL-invalid NUL.
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 12 * 1024 * 1024: raise ValueError('retry_json_limit')


def _command(raw):
    from .graph_event_api import RetryIntent
    _need(type(raw) is dict)
    _json(raw)
    typed = RetryIntent.model_validate(raw)
    value = typed.model_dump(mode='json')
    _need('\x00' not in value['reason'] and digest(value) == digest(raw))
    return value


def command_hash(p, identity, command):
    # This is the original graph_event_api hash, without new fields or schema.
    return digest({'scope': 'graph-event-retry-v1', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'intent_id': identity, 'command': command})


def _intent(db, p, identity, *, lock=False):
    row = scoped(db, GraphEventIntent, identity, p, lock)
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原处理账号核对来源消息与恢复回执')
    return row, read_intent(db, p, identity)


def _find(db, p, identity, nonce):
    return db.scalar(select(GraphEventIntentRetryReceipt).where(
        GraphEventIntentRetryReceipt.workspace_id == p.workspace_id,
        GraphEventIntentRetryReceipt.intent_id == identity,
        GraphEventIntentRetryReceipt.request_id == nonce).execution_options(populate_existing=True))


def _lock_nonce(db, p, identity, nonce):
    key = int(digest({'scope': 'graph-event-intent-retry-receipt-v1',
        'workspace_id': p.workspace_id, 'intent_id': identity, 'request_id': nonce})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))


def _checked_value(row, p, *, identity=None, nonce=None):
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原恢复账号核对这项回执')
    try:
        _need(all(_uuid(value) for value in (row.id, row.workspace_id, row.actor_id,
            row.intent_id, row.producer_id, row.job_id, row.request_id)))
        _need(row.workspace_id == p.workspace_id and row.version == 1 and type(row.version) is int
            and (identity is None or identity == row.intent_id) and (nonce is None or nonce == row.request_id))
        value = row.data
        _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == SCHEMA)
        _json(value)
        for key in ('id', 'request_id', 'actor_id', 'intent_id', 'producer_id', 'job_id', 'command_hash', 'receipt_hash'):
            _need(value[key] == getattr(row, key))
        command = _command(value['command'])
        _need(command['request_id'] == row.request_id and _hash(value['intent_hash'])
            and _hash(row.command_hash) and command_hash(p, row.intent_id, command) == row.command_hash
            and _hash(row.receipt_hash)
            and digest({key: item for key, item in value.items() if key != 'receipt_hash'}) == row.receipt_hash)
        result = value['result']
        _need(type(result) is dict and set(result) == {'outcome', 'accepted_job_version', 'reconciled', 'admission'}
            and result['outcome'] == row.outcome and row.outcome in {'queued', 'already_admitted'}
            and _integer(result['accepted_job_version']) and type(result['reconciled']) is bool)
        if row.outcome == 'queued':
            _need(result['accepted_job_version'] == command['job_version'] + 1
                and result['reconciled'] is False and result['admission'] is None)
        else:
            admission = result['admission']
            _need(type(admission) is dict and set(admission) == _ADMISSION
                and admission['schema'] == 'graph-event-admission-v1' and _uuid(admission['id'])
                and admission['workspace_id'] == row.workspace_id and admission['intent_id'] == row.intent_id
                and admission['producer_id'] == row.producer_id and admission['source_intent_hash'] == value['intent_hash']
                and _integer(admission['sequence']) and _hash(admission['admission_hash'])
                and admission['admission_hash'] == digest({key: item for key, item in admission.items() if key != 'admission_hash'}))
        return copy.deepcopy(value)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        _fail()


def checked_receipt(db, p, row, *, identity=None, nonce=None, frozen=None):
    value = _checked_value(row, p, identity=identity, nonce=nonce)
    if frozen is None:
        _, frozen = _intent(db, p, row.intent_id)
    _need(frozen['actor_id'] == p.user_id and frozen['producer_id'] == row.producer_id
        and frozen['intent_hash'] == value['intent_hash'])
    if row.outcome == 'already_admitted':
        admission = value['result']['admission']
        actual = read_admission(db, p, admission['id'])
        _need(digest(actual) == digest(admission))
    # Deliberately do not load the mutable Job, source/extractor or live role.
    return value


def _job(db, p, identity, *, lock=False):
    query = select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'typed-graph-source:' + identity)
    if lock: query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def _job_check(job, p, intent, frozen):
    from .graph_event_dispatch import KIND, job_body
    _need(job is not None and _uuid(job.id) and job.workspace_id == p.workspace_id and job.kind == KIND
        and job.idempotency_key == 'typed-graph-source:' + intent.id and job.status in _STATUSES
        and _integer(job.version) and _integer(job.attempts, 0) and type(job.data) is dict)
    _need(job_body(job) == {'actor_id': p.user_id, 'intent_id': intent.id,
        'producer_id': intent.producer_id, 'intent_hash': frozen['intent_hash']})


def _legacy_history(job, p, intent, frozen):
    _job_check(job, p, intent, frozen)
    try:
        history = job.data.get('graph_event_retries', [])
        _need(type(history) is list and len(history) <= 32)
        seen, last_version = set(), 0
        for item in history:
            _need(type(item) is dict and set(item) == {'request_id', 'actor_id', 'command_hash', 'command', 'accepted_job_version'})
            raw = _command(item['command'])
            _need(item['request_id'] == raw['request_id'] and item['request_id'] not in seen
                and item['actor_id'] == p.user_id and _hash(item['command_hash'])
                and item['command_hash'] == command_hash(p, intent.id, raw)
                and _integer(item['accepted_job_version'])
                and item['accepted_job_version'] == raw['job_version'] + 1
                and last_version < item['accepted_job_version'] <= job.version)
            seen.add(item['request_id']); last_version = item['accepted_job_version']
        return copy.deepcopy(history)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        _fail()


def _legacy_value(job, intent, item):
    return {'origin': 'job_history_v1', 'job_id': job.id, 'intent_id': intent.id,
        'producer_id': intent.producer_id, 'retry': copy.deepcopy(item)}


def _queued_nonces(db, p, intent, frozen, history):
    rows = list(db.scalars(select(GraphEventIntentRetryReceipt).where(
        GraphEventIntentRetryReceipt.workspace_id == p.workspace_id,
        GraphEventIntentRetryReceipt.intent_id == intent.id,
        GraphEventIntentRetryReceipt.outcome == 'queued').limit(33)))
    _need(len(rows) <= 32)
    commands = {item['request_id']: item['command_hash'] for item in history}
    for row in rows:
        value = checked_receipt(db, p, row, identity=intent.id, frozen=frozen)
        previous = commands.get(row.request_id)
        _need(previous is None or previous == value['command_hash'])
        commands[row.request_id] = value['command_hash']
    _need(len(commands) <= 32)
    return set(commands)


def _legacy_delivery(db, p, intent, frozen):
    # Mutable compatibility projection only. A broken current Job cannot erase
    # a separately saved receipt or turn a cold replay into a fresh operation.
    try:
        job = _job(db, p, intent.id)
        if job is None: return None
        history = _legacy_history(job, p, intent, frozen)
        count = len(_queued_nonces(db, p, intent, frozen, history))
        error = job.data.get('error')
        return {'status': job.status, 'version': job.version, 'attempts': job.attempts,
            'error': error if type(error) is str else None,
            'can_retry': job.status in {'failed', 'blocked'} and intent.receipt_id is None and count < 32}
    except HTTPException:
        return None


def current_delivery(db, p, intent):
    """Original delivery DTO with the combined legacy/new queue budget."""
    current, frozen = _intent(db, p, intent.id)
    return _legacy_delivery(db, p, current, frozen)


def _retry_projection(value):
    return {'request_id': value['request_id'], 'actor_id': value['actor_id'],
        'command_hash': value['command_hash'], 'command': copy.deepcopy(value['command']),
        'accepted_job_version': value['result']['accepted_job_version']}


def _response(db, p, intent, frozen, value, replayed):
    delivery = current_delivery(db, p, intent)
    if value['result']['outcome'] == 'queued':
        return {'replayed': replayed, 'retry': _retry_projection(value), 'delivery': delivery}
    return {'already_admitted': True, 'admission': copy.deepcopy(value['result']['admission']), 'delivery': delivery}


def _exact(value, signature, raw):
    if value['command_hash'] != signature or value['command'] != raw:
        _fail('该恢复编号已有另一份原命令，原回执保留')


def retry_intent(identity, body, p, db):
    """The unchanged legacy POST delegates here; reads precede fresh authority."""
    try:
        raw = _command(body.model_dump(mode='json'))
    except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
        _fail('恢复正文含无法保存的文字或数值，未提交恢复操作')
    identity, nonce = str(identity), raw['request_id']
    signature = command_hash(p, identity, raw)
    intent, frozen = _intent(db, p, identity)
    from .graph_event_delivery_retirement_v125 import guard_not_retired
    guard_not_retired(db, p, identity, raw)
    prior = _find(db, p, identity, nonce)
    if prior is not None:
        value = checked_receipt(db, p, prior, identity=identity, nonce=nonce, frozen=frozen)
        _exact(value, signature, raw)
        return _response(db, p, intent, frozen, value, True)
    job = _job(db, p, identity)
    if job is not None:
        history = _legacy_history(job, p, intent, frozen)
        old = next((item for item in history if item['request_id'] == nonce), None)
        if old is not None:
            _exact(old, signature, raw)
            return {'replayed': True, 'retry': old, 'delivery': _legacy_delivery(db, p, intent, frozen)}

    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    _lock_nonce(db, p, identity, nonce)
    guard_not_retired(db, p, identity, raw)
    prior = _find(db, p, identity, nonce)
    if prior is not None:
        value = checked_receipt(db, p, prior, identity=identity, nonce=nonce)
        _exact(value, signature, raw)
        return _response(db, p, intent, frozen, value, True)
    # Same lease order as the worker: Job before intent. No source IO/cursor
    # lock is introduced by this transport-only command.
    job = _job(db, p, identity, lock=True)
    if job is None: _fail('缺少原消息后台任务，不能另造任务替代')
    intent, frozen = _intent(db, p, identity, lock=True)
    history = _legacy_history(job, p, intent, frozen)
    old = next((item for item in history if item['request_id'] == nonce), None)
    if old is not None:
        _exact(old, signature, raw)
        return {'replayed': True, 'retry': old, 'delivery': _legacy_delivery(db, p, intent, frozen)}
    reconciled, admission = False, None
    if intent.receipt_id is not None:
        admission = read_admission(db, p, intent.receipt_id)
        if job.status in {'failed', 'blocked'}:
            reconciled = True; job.status = 'succeeded'; job.lease_token = None; job.lease_until = None
            job.version += 1
            job.data = {key: item for key, item in {**job.data, 'finished_at': now().isoformat()}.items() if key != 'error'}
        outcome = 'already_admitted'
    else:
        if job.version != raw['job_version']: _fail('原消息任务已有变化，请重新读取状态')
        if job.status not in {'failed', 'blocked'}: _fail('原消息仍在等待或处理中，请查询原结果')
        producer = scoped(db, GraphEventProducer, intent.producer_id, p)
        check_producer(producer)
        if producer.status != 'active': _fail('消息来源已撤销，原记录可查，不能重新接纳')
        if len(_queued_nonces(db, p, intent, frozen, history)) >= 32:
            _fail('已达到这条原消息的恢复上限，请核查来源和权限')
        job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None
        job.version += 1
        job.data = {key: item for key, item in job.data.items() if key not in {'error', 'finished_at'}}
        outcome = 'queued'
    value = {'schema': SCHEMA, 'id': uid(), 'request_id': nonce, 'actor_id': p.user_id,
        'intent_id': identity, 'producer_id': intent.producer_id, 'job_id': job.id,
        'intent_hash': frozen['intent_hash'], 'command_hash': signature, 'command': raw,
        'result': {'outcome': outcome, 'accepted_job_version': job.version,
            'reconciled': reconciled, 'admission': admission}}
    value['receipt_hash'] = digest(value)
    row = GraphEventIntentRetryReceipt(id=value['id'], workspace_id=p.workspace_id, actor_id=p.user_id,
        intent_id=identity, producer_id=intent.producer_id, job_id=job.id, request_id=nonce,
        command_hash=signature, receipt_hash=value['receipt_hash'], outcome=outcome, data=value)
    db.add(row); db.flush()
    value = checked_receipt(db, p, row, identity=identity, nonce=nonce, frozen=frozen)
    audit(db, p, 'graph_event.intent_retry_recorded', identity,
        {'request_id': nonce, 'receipt_id': row.id, 'command_hash': signature, 'outcome': outcome})
    db.commit()
    return _response(db, p, intent, frozen, value, False)


@router.get('/intents/{identity}/retry-requests/{request_id}')
def request(identity: UUID, request_id: UUID, p: P, db: DB):
    identity, nonce = str(identity), str(request_id)
    intent, frozen = _intent(db, p, identity)
    row = _find(db, p, identity, nonce)
    if row is not None:
        return {'found': True, 'receipt': checked_receipt(db, p, row, identity=identity,
            nonce=nonce, frozen=frozen), 'legacy': None}
    job = _job(db, p, identity)
    if job is not None:
        history = _legacy_history(job, p, intent, frozen)
        old = next((item for item in history if item['request_id'] == nonce), None)
        if old is not None:
            return {'found': True, 'receipt': None, 'legacy': _legacy_value(job, intent, old)}
    return {'found': False, 'receipt': None, 'legacy': None}


def _text(value, fallback):
    if type(value) is not str or not value.strip() or '\x00' in value: return fallback
    try: value.encode('utf-8')
    except UnicodeError: return fallback
    return value.strip()[:200]


def _action(db, p, producer, contract, intent, job, remaining):
    code, kind, allowed = 'in_progress', None, False
    if job is None: code = 'missing_job'
    elif p.role not in {'admin', 'operator'}: code = 'no_permission'
    elif intent.receipt_id is not None:
        if job.status in {'failed', 'blocked'}: code, kind, allowed = 'receipt_available', 'reconcile_receipt', True
        else: code = 'already_admitted'
    elif job.status in {'failed', 'blocked'}:
        if remaining == 0: code = 'retry_limit'
        else:
            from .graph_event_catalog_v122 import current_availability
            availability = current_availability(db, p, producer, contract)
            if availability['available']: code, kind, allowed = 'retry_available', 'retry_admission', True
            else: code = availability['reason_code']
    labels = {'in_progress': '原消息仍在等待或处理中，可刷新核对。', 'missing_job': '缺少原投递任务，历史保留，请核查后台记录。',
        'no_permission': '当前账号可读历史，不能提交恢复。', 'receipt_available': '实际接纳回执已保存，可核对并恢复原投递状态。',
        'already_admitted': '消息已有真实接纳回执，无需再次接纳。', 'retry_limit': '已达到原消息的恢复上限，请核查来源和权限。',
        'retry_available': '可明确恢复原消息投递；不会重新调用模型或生成新的处理结果。',
        'registration_stopped': '来源登记已停止，历史保留，不能重新接纳。',
        'no_execution_permission': '当前账号不能重新接纳；原处理结果与历史保留。',
        'source_unavailable': '来源或处理能力已变化；请恢复原发布版本及权限后再核对。'}
    return {'allowed': allowed, 'kind': kind, 'reason_code': code, 'label': labels[code], 'remaining_retries': remaining}


def delivery_record(db, p, identity, *, include_result=True):
    intent, frozen = _intent(db, p, identity)
    producer = scoped(db, GraphEventProducer, intent.producer_id, p); contract = check_producer(producer)
    job = _job(db, p, identity)
    history = [] if job is None else _legacy_history(job, p, intent, frozen)
    remaining = 32 - len(_queued_nonces(db, p, intent, frozen, history))
    output = frozen['projection']['output']
    entity = scoped(db, Entity, output['entity_id'], p)
    attributes = entity.data.get('attributes', {}) if type(entity.data) is dict else {}
    name = attributes.get('name') if type(attributes) is dict else None
    admission = None
    if intent.receipt_id is not None:
        saved = read_admission(db, p, intent.receipt_id)
        admission = {key: saved[key] for key in ('id', 'sequence', 'admitted_at')}
    result = None
    if include_result:
        result = {'schema': copy.deepcopy(contract['result_schema']), 'value': copy.deepcopy(output['result'])}
        _json(result)
    return {'schema': 'graph-event-delivery-record-v1', 'intent_id': intent.id, 'producer_id': producer.id,
        'actor_id': p.user_id, 'intent_hash': frozen['intent_hash'], 'source_label': _text(producer.data['label'], '已登记的处理结果'),
        'object': {'id': entity.id, 'name': _text(name, _text(entity.external_id, '本次关联对象'))},
        'observation_id': intent.observation_id, 'observed_at': output['observed_at'],
        'candidate_only': output['candidate_only'], 'source_time_verified': False, 'physical_state_verified': False,
        'intent_status': intent.status, 'admission': admission,
        'delivery': None if job is None else {'job_id': job.id, 'status': job.status, 'version': job.version, 'attempts': job.attempts},
        'action': _action(db, p, producer, contract, intent, job, remaining), 'result': result}


@router.get('/{identity}/delivery-records')
def records(identity: UUID, p: P, db: DB, after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    producer = scoped(db, GraphEventProducer, str(identity), p); check_producer(producer)
    query = select(GraphEventIntent.id).where(GraphEventIntent.workspace_id == p.workspace_id,
        GraphEventIntent.producer_id == str(identity), GraphEventIntent.actor_id == p.user_id)
    if after is not None: query = query.where(GraphEventIntent.id > str(after))
    rows = list(db.scalars(query.order_by(GraphEventIntent.id).limit(limit + 1)))
    selected = rows[:limit]
    return {'schema': 'graph-event-delivery-page-v1', 'producer_id': str(identity),
        'items': [delivery_record(db, p, selected_id, include_result=False) for selected_id in selected],
        'next_after': selected[-1] if len(rows) > limit else None}


@router.get('/intents/{identity}/delivery-record')
def detail(identity: UUID, p: P, db: DB):
    return delivery_record(db, p, str(identity))
