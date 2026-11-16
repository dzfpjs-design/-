"""Learned-only DTOs over the existing native command inventory and locks.

No table/migration and no Audio DTO widening. A foreign-family row is a conflict,
never a not-found result that could authorize a replacement request.
"""
import copy
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from .contracts import Contract, digest
from .db import session
from .models import DomainInstance, uid
from .graph_event_native_producer_requests_v136 import GraphEventNativeProducerCommand, lock_original, _lock
from .graph_event_models import GraphEventProducer
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, scoped

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter()
Kind = Literal['register', 'revoke']
ERROR = '原生来源原请求与结束回执无法核对；原记录保留'


def _reason(value):
    if not value.strip() or value != value.strip() or '\x00' in value: raise ValueError('native_source_reason')
    value.encode('utf-8'); return value


class Revocation(Contract):
    schema: Literal['graph-event-native-learned-geo-producer-revocation-v1']
    request_id: UUID
    instance_id: UUID
    producer_id: UUID
    producer_version: int = Field(strict=True, ge=1)
    contract_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(strict=True, min_length=1, max_length=1000)

    @field_validator('request_id', 'instance_id', 'producer_id', mode='before')
    @classmethod
    def exact_identifier(cls, value):
        if type(value) is not str or str(UUID(value)) != value: raise ValueError('请保留原标准编号')
        return value

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value): return _reason(value)


def _typed_original(kind, raw):
    from .graph_event_request_recovery_v123 import _json
    _json(raw)
    if kind == 'register':
        from .graph_event_native_learned_geo_producer_api_v141 import Register
        typed = Register.model_validate(raw)
    elif kind == 'revoke': typed = Revocation.model_validate(raw)
    else: raise ValueError('native_source_kind')
    value = typed.model_dump(mode='json')
    if digest(value) != digest(raw): raise ValueError('native_source_normalization')
    return value


class Retirement(Contract):
    schema: Literal['graph-event-native-learned-geo-source-retirement-v1']
    request_id: UUID
    instance_id: UUID
    kind: Kind
    original: dict = Field(strict=True)
    reason: str = Field(strict=True, min_length=1, max_length=1000)

    @field_validator('request_id', 'instance_id', mode='before')
    @classmethod
    def exact_identifier(cls, value):
        if type(value) is not str or str(UUID(value)) != value: raise ValueError('请保留原标准编号')
        return value

    @model_validator(mode='before')
    @classmethod
    def safe_json(cls, value):
        from .graph_event_request_recovery_v123 import _json
        try: _json(value)
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            raise HTTPException(422, '原生来源命令须为有效UTF-8文字与有限JSON值') from None
        return value

    @field_validator('original')
    @classmethod
    def exact_original(cls, value, info): return _typed_original(info.data.get('kind'), value)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value): return _reason(value)

    @model_validator(mode='after')
    def independent(self):
        if (str(self.request_id) == self.original['request_id']
            or str(self.instance_id) != self.original['instance_id']): raise ValueError('native_source_original_scope')
        return self


def wire(value):
    from .report_full_semantic_graph_wire import canonical_wire
    return canonical_wire(value)


def _need(value):
    if not value: raise HTTPException(409, ERROR)


def _uuid(value):
    try: return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


def _instance(db, p, identity):
    return scoped(db, DomainInstance, str(identity), p)


def revocation_hash(p, command):
    return digest({'schema': 'graph-event-native-learned-geo-producer-revocation-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'command': command})


def original_hash(p, kind, raw):
    value = _typed_original(kind, raw)
    if kind == 'register':
        from .graph_event_source_families_v136 import native_learned_command_hash
        return native_learned_command_hash(p, value)
    return revocation_hash(p, value)


def retirement_hash(p, command):
    return digest({'schema': 'graph-event-native-learned-geo-source-retirement-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'command': command})


def _find(db, p, nonce):
    return db.scalar(select(GraphEventNativeProducerCommand).where(
        GraphEventNativeProducerCommand.workspace_id == p.workspace_id,
        GraphEventNativeProducerCommand.request_id == nonce).execution_options(populate_existing=True))


def _retired(db, p, kind, nonce):
    return db.scalar(select(GraphEventNativeProducerCommand).where(
        GraphEventNativeProducerCommand.workspace_id == p.workspace_id,
        GraphEventNativeProducerCommand.kind == 'retire',
        GraphEventNativeProducerCommand.original_kind == kind,
        GraphEventNativeProducerCommand.original_request_id == nonce).execution_options(populate_existing=True))


def checked_receipt(row, p, instance_id, *, kind=None, nonce=None):
    if row.actor_id != p.user_id: raise HTTPException(403, '请由原操作账号核对本机来源请求')
    try:
        _need(all(_uuid(value) for value in (row.id, row.workspace_id, row.actor_id, row.instance_id, row.request_id))
            and row.workspace_id == p.workspace_id and row.instance_id == instance_id and row.version == 1
            and type(row.version) is int and row.kind in {'revoke', 'retire'}
            and (kind is None or row.kind == kind) and (nonce is None or row.request_id == nonce)
            and type(row.data) is dict and set(row.data) == {'command', 'result'})
        raw, result = row.data['command'], row.data['result']
        typed = (Revocation if row.kind == 'revoke' else Retirement).model_validate(raw)
        command = typed.model_dump(mode='json')
        _need(digest(command) == digest(raw) and command['request_id'] == row.request_id
            and command['instance_id'] == row.instance_id)
        if row.kind == 'revoke':
            _need(row.producer_id == command['producer_id'] and _uuid(row.producer_id)
                and row.original_kind is row.original_request_id is row.original_hash is None
                and row.command_hash == revocation_hash(p, command)
                and digest(result) == digest({'status': 'revoked', 'version': command['producer_version'] + 1,
                    'contract_hash': command['contract_hash']}))
            return {'schema': 'graph-event-native-learned-geo-producer-revocation-receipt-v1',
                'id': row.id, 'request_id': row.request_id, 'actor_id': row.actor_id,
                'instance_id': row.instance_id, 'producer_id': row.producer_id,
                'command_hash': row.command_hash, 'command': copy.deepcopy(raw), 'result': copy.deepcopy(result)}
        _need(row.producer_id is None and row.original_kind == command['kind']
            and row.original_request_id == command['original']['request_id']
            and row.original_hash == original_hash(p, row.original_kind, command['original'])
            and row.command_hash == retirement_hash(p, command)
            and digest(result) == digest({'outcome': 'retired', 'kind': row.original_kind,
                'original_request_id': row.original_request_id, 'original_command_hash': row.original_hash}))
        return {'schema': 'graph-event-native-learned-geo-source-retirement-receipt-v1',
            'id': row.id, 'request_id': row.request_id, 'actor_id': row.actor_id, 'instance_id': row.instance_id,
            'kind': row.original_kind, 'original_request_id': row.original_request_id,
            'original_command_hash': row.original_hash, 'command_hash': row.command_hash,
            'command': copy.deepcopy(raw), 'result': copy.deepcopy(result)}
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, ERROR) from None


def _saved(db, p, instance_id, kind, nonce):
    if kind == 'register':
        row = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
            GraphEventProducer.request_id == nonce).execution_options(populate_existing=True))
        if row is None: return None
        if row.registered_by != p.user_id: raise HTTPException(403, '请由原登记账号核对本机来源请求')
        from .graph_event_native_learned_geo_producer_api_v141 import public
        value = public(db, p, instance_id, row)
        return row.command_hash, value
    row = _find(db, p, nonce)
    if row is None: return None
    value = checked_receipt(row, p, instance_id, kind='revoke', nonce=nonce)
    return row.command_hash, value


def state_value(db, p, instance_id, kind, nonce):
    _need(kind in {'register', 'revoke'} and _uuid(nonce))
    saved = _saved(db, p, instance_id, kind, nonce); retired = _retired(db, p, kind, nonce)
    _need(not (saved is not None and retired is not None))
    value = {'schema': 'graph-event-native-learned-geo-source-request-state-v1', 'instance_id': instance_id,
        'kind': kind, 'request_id': nonce, 'outcome': 'not_found', 'command_hash': None, 'saved': None, 'retirement': None}
    if saved is not None: value.update(outcome='saved', command_hash=saved[0], saved=saved[1])
    if retired is not None:
        receipt = checked_receipt(retired, p, instance_id, kind='retire')
        value.update(outcome='retired', command_hash=receipt['original_command_hash'], retirement=receipt)
    return value


def guard_not_retired(db, p, instance_id, kind, command):
    raw = _typed_original(kind, command); _need(raw['instance_id'] == instance_id)
    row = _retired(db, p, kind, raw['request_id'])
    if row is not None:
        checked_receipt(row, p, instance_id, kind='retire')
        raise HTTPException(409, '这次原生来源操作已安全结束，迟到的原提交不会再执行')


def guard_foreign_registration_nonce(db, p, nonce):
    """Processing mutations share the Producer nonce but not native DTOs."""
    row = _retired(db, p, 'register', nonce)
    if row is not None:
        checked_receipt(row, p, row.instance_id, kind='retire')
        raise HTTPException(409, '此编号已属于结束的原生来源请求，不能改换处理协议')


@router.post('/revocations')
def revoke(identity: UUID, body: Revocation, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    instance_id = str(identity); raw = body.model_dump(mode='json'); _need(raw['instance_id'] == instance_id)
    lock_original(db, p, 'revoke', raw['request_id']); guard_not_retired(db, p, instance_id, 'revoke', raw)
    prior = _find(db, p, raw['request_id'])
    if prior is not None:
        receipt = checked_receipt(prior, p, instance_id, kind='revoke')
        _need(receipt['command_hash'] == revocation_hash(p, raw) and digest(receipt['command']) == digest(raw))
        db.commit(); return wire({'receipt': receipt, 'replayed': True})
    from .graph_event_native_learned_geo_producer_api_v141 import public
    producer = scoped(db, GraphEventProducer, raw['producer_id'], p, True)
    public(db, p, instance_id, producer)
    _need(producer.version == body.producer_version and producer.contract_hash == body.contract_hash
        and producer.status == 'active')
    result = {'status': 'revoked', 'version': producer.version + 1, 'contract_hash': producer.contract_hash}
    producer.status = 'revoked'; producer.version += 1
    row = GraphEventNativeProducerCommand(workspace_id=p.workspace_id, actor_id=p.user_id,
        instance_id=instance_id, producer_id=producer.id, kind='revoke', request_id=raw['request_id'],
        command_hash=revocation_hash(p, raw), data={'command': raw, 'result': result})
    db.add(row); db.flush(); receipt = checked_receipt(row, p, instance_id, kind='revoke')
    audit(db, p, 'graph_event.native_learned_geo_producer_revoked_v141', producer.id,
        {'request_id': row.request_id, 'receipt_id': row.id, 'command_hash': row.command_hash, 'reason': body.reason})
    db.commit(); return wire({'receipt': receipt, 'replayed': False})


@router.get('/revocations/requests/{request_id}')
def revocation_request(identity: UUID, request_id: UUID, p: P, db: DB):
    _instance(db, p, identity); row = _find(db, p, str(request_id))
    return wire({'found': row is not None, 'receipt': None if row is None else
        checked_receipt(row, p, str(identity), kind='revoke', nonce=str(request_id))})


@router.get('/command-requests/{kind}/{request_id}')
def original_request(identity: UUID, kind: Kind, request_id: UUID, p: P, db: DB):
    _instance(db, p, identity); lock_original(db, p, kind, str(request_id))
    return wire(state_value(db, p, str(identity), kind, str(request_id)))


@router.post('/command-retirements')
def retire(identity: UUID, body: Retirement, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p); _instance(db, p, identity)
    raw = body.model_dump(mode='json'); instance_id = str(identity); _need(raw['instance_id'] == instance_id)
    _lock(db, 'graph-event-native-audio-source-retirement-v1', p, raw['request_id'])
    prior = _find(db, p, raw['request_id'])
    receipt = None if prior is None else checked_receipt(prior, p, instance_id, kind='retire')
    if receipt is not None: _need(receipt['command_hash'] == retirement_hash(p, raw) and digest(receipt['command']) == digest(raw))
    lock_original(db, p, body.kind, body.original['request_id'])
    if body.kind == 'register':
        from .graph_event_request_recovery_v123 import guard_not_retired as old_retired
        old_retired(db, p, 'register', body.original)
    current = state_value(db, p, instance_id, body.kind, body.original['request_id'])
    if receipt is not None:
        _need(current['outcome'] == 'retired' and current['retirement']['id'] == receipt['id'])
        db.commit(); return wire({'retirement': receipt, 'replayed': True})
    if current['outcome'] != 'not_found':
        raise HTTPException(409, '原操作已保存或已有结束记录，请只核对原结果，不能冒认为本次结束成功')
    source_hash = original_hash(p, body.kind, body.original)
    result = {'outcome': 'retired', 'kind': body.kind, 'original_request_id': body.original['request_id'],
        'original_command_hash': source_hash}
    row = GraphEventNativeProducerCommand(workspace_id=p.workspace_id, actor_id=p.user_id,
        instance_id=instance_id, producer_id=None, kind='retire', request_id=raw['request_id'],
        command_hash=retirement_hash(p, raw), original_kind=body.kind,
        original_request_id=body.original['request_id'], original_hash=source_hash, data={'command': raw, 'result': result})
    db.add(row); db.flush(); receipt = checked_receipt(row, p, instance_id, kind='retire')
    audit(db, p, 'graph_event.native_learned_geo_source_request_retired_v141', row.id,
        {'request_id': row.request_id, 'original_request_id': row.original_request_id,
            'original_command_hash': source_hash, 'command_hash': row.command_hash, 'reason': body.reason})
    db.commit(); return wire({'retirement': receipt, 'replayed': False})


@router.get('/command-retirements/requests/{request_id}')
def retirement_request(identity: UUID, request_id: UUID, p: P, db: DB):
    _instance(db, p, identity); nonce = str(request_id)
    _lock(db, 'graph-event-native-audio-source-retirement-v1', p, nonce); row = _find(db, p, nonce)
    if row is None: return wire({'found': False, 'retirement': None})
    receipt = checked_receipt(row, p, str(identity), kind='retire', nonce=nonce)
    lock_original(db, p, receipt['kind'], receipt['original_request_id'])
    current = state_value(db, p, str(identity), receipt['kind'], receipt['original_request_id'])
    _need(current['outcome'] == 'retired' and current['retirement']['id'] == receipt['id'])
    return wire({'found': True, 'retirement': receipt})

