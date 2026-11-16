"""Retire one unsaved retry command, never its source message or delivery Job."""
import copy
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from . import graph_event_delivery_recovery_v124 as delivery
from .contracts import Contract, digest
from .db import session
from .models import Base, Payload, Row, Scoped, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit


SCHEMA = 'graph-event-intent-retry-retirement-receipt-v1'
COMMAND_SCHEMA = 'graph-event-intent-retry-retirement-v1'
STATE_SCHEMA = 'graph-event-intent-retry-request-state-v1'
_FIELDS = {'schema', 'id', 'request_id', 'actor_id', 'intent_id', 'producer_id', 'job_id', 'intent_hash',
    'original_request_id', 'original_command_hash', 'command_hash', 'command', 'result', 'receipt_hash'}
_ERROR = '原恢复申请与退休记录无法完整核对，原记录保留'


class GraphEventIntentRetryRetirement(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_intent_retry_retirements'
    __table_args__ = (UniqueConstraint('workspace_id', 'intent_id', 'original_request_id'),
        UniqueConstraint('workspace_id', 'intent_id', 'request_id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    intent_id: Mapped[str] = mapped_column(ForeignKey('graph_event_intents.id'), index=True)
    producer_id: Mapped[str] = mapped_column(ForeignKey('graph_event_producers.id'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    original_request_id: Mapped[str] = mapped_column(String(36))
    request_id: Mapped[str] = mapped_column(String(36))
    original_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64))


class Retirement(Contract):
    schema: Literal['graph-event-intent-retry-retirement-v1']
    request_id: UUID
    original: dict = Field(strict=True)
    reason: str = Field(strict=True, min_length=1, max_length=1000)

    @field_validator('original')
    @classmethod
    def exact_original(cls, value):
        return delivery._command(value)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('请填写有实际内容且不含空字符的退休原因')
        value.encode('utf-8')
        return value

    @model_validator(mode='after')
    def independent_nonce(self):
        if str(self.request_id) == self.original['request_id']:
            raise ValueError('请为退休申请保留独立的新编号')
        return self


P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/graph-event-producers', route_class=delivery.RetryInputRoute)


def _fail(message=_ERROR):
    raise HTTPException(409, message)


def _need(value):
    if not value: _fail()


def command_hash(p, identity, command):
    return digest({'schema': 'graph-event-intent-retry-retirement-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'intent_id': identity, 'command': command})


def lock_original(db, p, identity, nonce):
    _need(delivery._uuid(identity) and delivery._uuid(nonce))
    delivery._lock_nonce(db, p, identity, nonce)


def _lock_retirement(db, p, identity, nonce):
    key = int(digest({'scope': 'graph-event-intent-retry-retirement-v1',
        'workspace_id': p.workspace_id, 'intent_id': identity, 'request_id': nonce})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))


def _by_request(db, p, identity, nonce):
    return db.scalar(select(GraphEventIntentRetryRetirement).where(
        GraphEventIntentRetryRetirement.workspace_id == p.workspace_id,
        GraphEventIntentRetryRetirement.intent_id == identity,
        GraphEventIntentRetryRetirement.request_id == nonce).execution_options(populate_existing=True))


def _by_original(db, p, identity, nonce):
    return db.scalar(select(GraphEventIntentRetryRetirement).where(
        GraphEventIntentRetryRetirement.workspace_id == p.workspace_id,
        GraphEventIntentRetryRetirement.intent_id == identity,
        GraphEventIntentRetryRetirement.original_request_id == nonce).execution_options(populate_existing=True))


def _checked_value(row, p, *, identity=None, nonce=None, original_nonce=None):
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原申请账号核对这项退休记录')
    try:
        _need(all(delivery._uuid(value) for value in (row.id, row.workspace_id, row.actor_id,
            row.intent_id, row.producer_id, row.job_id, row.request_id, row.original_request_id)))
        _need(row.workspace_id == p.workspace_id and type(row.version) is int and row.version == 1
            and row.request_id != row.original_request_id
            and (identity is None or row.intent_id == identity) and (nonce is None or row.request_id == nonce)
            and (original_nonce is None or row.original_request_id == original_nonce))
        value = row.data
        _need(type(value) is dict and set(value) == _FIELDS and value['schema'] == SCHEMA)
        delivery._json(value)
        for key in ('id', 'request_id', 'actor_id', 'intent_id', 'producer_id', 'job_id',
                'original_request_id', 'command_hash', 'receipt_hash'):
            _need(value[key] == getattr(row, key))
        typed = Retirement.model_validate(value['command'])
        command = typed.model_dump(mode='json')
        _need(digest(command) == digest(value['command']) and str(typed.request_id) == row.request_id
            and typed.original['request_id'] == row.original_request_id and delivery._hash(value['intent_hash'])
            and delivery._hash(row.original_hash) and value['original_command_hash'] == row.original_hash
            and row.original_hash == delivery.command_hash(p, row.intent_id, typed.original)
            and delivery._hash(row.command_hash) and row.command_hash == command_hash(p, row.intent_id, command)
            and delivery._hash(row.receipt_hash)
            and row.receipt_hash == digest({key: item for key, item in value.items() if key != 'receipt_hash'}))
        result = value['result']
        _need(type(result) is dict and set(result) == {'outcome', 'original_request_id', 'original_command_hash'}
            and result == {'outcome': 'retired', 'original_request_id': row.original_request_id,
                'original_command_hash': row.original_hash})
        return copy.deepcopy(value)
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        _fail()


def checked_receipt(db, p, row, *, identity=None, nonce=None, original_nonce=None, frozen=None):
    value = _checked_value(row, p, identity=identity, nonce=nonce, original_nonce=original_nonce)
    if frozen is None: _, frozen = delivery._intent(db, p, row.intent_id)
    _need(frozen['actor_id'] == p.user_id and frozen['producer_id'] == row.producer_id
        and frozen['intent_hash'] == value['intent_hash'])
    # The actual Job was checked/locked at acceptance. Its current health,
    # version, role and source publication are not historical authority.
    return value


def _saved(db, p, identity, nonce, frozen):
    row = delivery._find(db, p, identity, nonce)
    return None if row is None else delivery.checked_receipt(db, p, row,
        identity=identity, nonce=nonce, frozen=frozen)


def _no_saved_conflict(db, p, identity, receipt, frozen):
    if _saved(db, p, identity, receipt['original_request_id'], frozen) is not None:
        _fail('原恢复申请同时存在已保存与退休记录，停止操作并保留全部记录')


def _exact(receipt, signature, command):
    if receipt['command_hash'] != signature or receipt['command'] != command:
        _fail('该退休编号已有另一份原命令，原记录保留')


def guard_not_retired(db, p, identity, raw_original):
    """Readonly both before cold fast paths and inside the original nonce lock."""
    _need(type(raw_original) is dict and delivery._uuid(identity)
        and delivery._uuid(raw_original.get('request_id')))
    row = _by_original(db, p, identity, raw_original['request_id'])
    if row is not None:
        receipt = checked_receipt(db, p, row, identity=identity, original_nonce=raw_original['request_id'])
        # Never acquire a retirement nonce or Job lock from an original retry.
        _no_saved_conflict(db, p, identity, receipt, None)
        _fail('该原恢复申请已经退休，迟到提交不能再恢复或修改原投递任务')


@router.get('/intents/{identity}/retry-command-requests/{request_id}')
def state(identity: UUID, request_id: UUID, p: P, db: DB):
    identity, nonce = str(identity), str(request_id)
    intent, frozen = delivery._intent(db, p, identity)
    lock_original(db, p, identity, nonce)
    saved = _saved(db, p, identity, nonce, frozen)
    retired = _by_original(db, p, identity, nonce)
    receipt = None if retired is None else checked_receipt(db, p, retired,
        identity=identity, original_nonce=nonce, frozen=frozen)
    if saved is not None and receipt is not None:
        _fail('原恢复申请同时存在已保存与退休记录，停止操作并保留全部记录')
    base = {'schema': STATE_SCHEMA, 'intent_id': identity, 'request_id': nonce, 'outcome': 'not_found',
        'command_hash': None, 'saved': None, 'legacy': None, 'retirement': None}
    if saved is not None:
        return {**base, 'outcome': 'saved', 'command_hash': saved['command_hash'], 'saved': saved}
    if receipt is not None:
        # Its acceptance already proved legacy absence under Job/original locks;
        # historical reads do not re-authorize that decision from mutable Job.
        return {**base, 'outcome': 'retired', 'command_hash': receipt['original_command_hash'], 'retirement': receipt}
    job = delivery._job(db, p, identity)
    if job is None: _fail('缺少原投递任务，无法核对旧申请是否保存；原输入保留')
    history = delivery._legacy_history(job, p, intent, frozen)
    old = next((item for item in history if item['request_id'] == nonce), None)
    if old is not None:
        return {**base, 'outcome': 'saved', 'command_hash': old['command_hash'],
            'legacy': delivery._legacy_value(job, intent, old)}
    return base


@router.post('/intents/{identity}/retry-retirements')
def retire(identity: UUID, body: Retirement, p: P, db: DB):
    identity, nonce = str(identity), str(body.request_id)
    original_nonce = body.original['request_id']; raw = body.model_dump(mode='json')
    signature = command_hash(p, identity, raw)
    intent, frozen = delivery._intent(db, p, identity)
    prior = _by_request(db, p, identity, nonce)
    if prior is not None:
        receipt = checked_receipt(db, p, prior, identity=identity, nonce=nonce, frozen=frozen)
        _exact(receipt, signature, raw); _no_saved_conflict(db, p, identity, receipt, frozen)
        return {'retirement': receipt, 'replayed': True}

    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    _lock_retirement(db, p, identity, nonce)
    prior = _by_request(db, p, identity, nonce)
    if prior is not None:
        receipt = checked_receipt(db, p, prior, identity=identity, nonce=nonce, frozen=frozen)
        _exact(receipt, signature, raw); _no_saved_conflict(db, p, identity, receipt, frozen)
        return {'retirement': receipt, 'replayed': True}
    lock_original(db, p, identity, original_nonce)
    if _saved(db, p, identity, original_nonce, frozen) is not None:
        _fail('原恢复申请已经保存，请核对原结果，不能冒认为本次退休成功')
    previous = _by_original(db, p, identity, original_nonce)
    if previous is not None:
        checked_receipt(db, p, previous, identity=identity, original_nonce=original_nonce, frozen=frozen)
        _fail('这份原恢复申请已有另一个退休编号，请核对原退休记录')
    # A missing/broken Job is not proof that its historical legacy queue is
    # empty. Take the original worker order before making an absence decision.
    job = delivery._job(db, p, identity, lock=True)
    if job is None: _fail('缺少原投递任务，不能把无法核对的旧申请当成未保存')
    intent, frozen = delivery._intent(db, p, identity, lock=True)
    history = delivery._legacy_history(job, p, intent, frozen)
    if any(item['request_id'] == original_nonce for item in history):
        _fail('原恢复申请已有真实旧队列记录，不能退休已保存的原操作')
    source_hash = delivery.command_hash(p, identity, body.original)
    value = {'schema': SCHEMA, 'id': uid(), 'request_id': nonce, 'actor_id': p.user_id,
        'intent_id': identity, 'producer_id': intent.producer_id, 'job_id': job.id,
        'intent_hash': frozen['intent_hash'], 'original_request_id': original_nonce,
        'original_command_hash': source_hash, 'command_hash': signature, 'command': raw,
        'result': {'outcome': 'retired', 'original_request_id': original_nonce, 'original_command_hash': source_hash}}
    value['receipt_hash'] = digest(value)
    row = GraphEventIntentRetryRetirement(id=value['id'], workspace_id=p.workspace_id, actor_id=p.user_id,
        intent_id=identity, producer_id=intent.producer_id, job_id=job.id, request_id=nonce,
        original_request_id=original_nonce, original_hash=source_hash, command_hash=signature,
        receipt_hash=value['receipt_hash'], data=value)
    db.add(row); db.flush()
    receipt = checked_receipt(db, p, row, identity=identity, nonce=nonce, frozen=frozen)
    audit(db, p, 'graph_event.intent_retry_retired', identity,
        {'request_id': nonce, 'original_request_id': original_nonce, 'receipt_id': row.id,
            'command_hash': signature, 'original_command_hash': source_hash, 'reason': body.reason})
    db.commit()
    return {'retirement': receipt, 'replayed': False}


@router.get('/intents/{identity}/retry-retirements/requests/{request_id}')
def request(identity: UUID, request_id: UUID, p: P, db: DB):
    identity, nonce = str(identity), str(request_id)
    _, frozen = delivery._intent(db, p, identity)
    row = _by_request(db, p, identity, nonce)
    if row is None: return {'found': False, 'retirement': None}
    receipt = checked_receipt(db, p, row, identity=identity, nonce=nonce, frozen=frozen)
    _no_saved_conflict(db, p, identity, receipt, frozen)
    return {'found': True, 'retirement': receipt}
