"""Retire an exact unsaved source command without racing a delayed original POST.

The original mutation and retirement share the original command's nonce lock.
Neither historical lookup nor retirement checks current source health. A stopped
registration and a retired unsaved request are different durable outcomes.
"""
import copy
import json
import math
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import Contract, digest
from .db import session
from .graph_event_models import GraphEventProducer
from .graph_event_revocation_v122 import GraphEventProducerRevocation
from .models import Base, Payload, Row, Scoped, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit


Kind = Literal['register', 'revoke']
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ERROR = '原来源请求与退休回执无法完整核对，原记录保留'


class GraphEventSourceRetirement(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_source_retirements'
    __table_args__ = (UniqueConstraint('workspace_id', 'kind', 'original_request_id'),
        UniqueConstraint('workspace_id', 'request_id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(10))
    original_request_id: Mapped[str] = mapped_column(String(36))
    request_id: Mapped[str] = mapped_column(String(36))
    original_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))


def _json(value):
    """Validate actual JSON before legacy DTO/hash code sees unsafe strings."""
    remaining = [1000]

    def visit(item, depth):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError('source_command_json_limit')
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            if not math.isfinite(item): raise ValueError('source_command_number')
            return
        if type(item) is str:
            if '\x00' in item: raise ValueError('source_command_text')
            item.encode('utf-8')
            return
        if type(item) is list:
            for child in item: visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str: raise ValueError('source_command_key')
                visit(key, depth + 1); visit(child, depth + 1)
            return
        raise ValueError('source_command_json')

    visit(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 64 * 1024:
        raise ValueError('source_command_json_limit')


def _typed_original(kind, raw):
    if type(raw) is not dict:
        raise ValueError('source_command_shape')
    _json(raw)
    if kind == 'register':
        # Import only at validation time: migrate must not initialize setup_api.
        from .graph_event_api import Register
        typed = Register.model_validate(raw)
    elif kind == 'revoke':
        from .graph_event_revocation_v122 import Revocation
        typed = Revocation.model_validate(raw)
    else:
        raise ValueError('source_command_kind')
    value = typed.model_dump(mode='json')
    if digest(value) != digest(raw):
        raise ValueError('source_command_normalization')
    return value


class Retirement(Contract):
    schema: Literal['graph-event-source-retirement-v1']
    request_id: UUID
    kind: Kind
    original: dict = Field(strict=True)
    reason: str = Field(strict=True, min_length=1, max_length=1000)

    @model_validator(mode='before')
    @classmethod
    def safe_json_body(cls, value):
        try:
            _json(value)
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            # A normal validation error echoes its rejected input. Lone UTF-16
            # surrogates or NaN in that echo can themselves break JSONResponse.
            raise HTTPException(422, '退休命令须为有限数值及有效UTF-8文字组成的有界JSON正文') from None
        return value

    @field_validator('original')
    @classmethod
    def exact_original(cls, value, info):
        return _typed_original(info.data.get('kind'), value)

    @field_validator('reason')
    @classmethod
    def exact_reason(cls, value):
        if value != value.strip() or '\x00' in value:
            raise ValueError('请填写裁剪前后相同且不含空字符的退休原因')
        value.encode('utf-8')
        return value

    @model_validator(mode='after')
    def independent_nonce(self):
        if str(self.request_id) == self.original['request_id']:
            raise ValueError('退休请求须使用独立的新编号')
        return self


P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/graph-event-producers')


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _nonce(value):
    value = str(value)
    if not _uuid(value):
        raise HTTPException(409, _ERROR)
    return value


def original_hash(p, kind, raw):
    try:
        value = _typed_original(kind, raw)
        if kind == 'register':
            from .graph_event_admission import producer_command_hash
            return producer_command_hash(p, value)
        from .graph_event_revocation_v122 import command_hash as revocation_hash
        return revocation_hash(p, value)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, _ERROR) from None


def command_hash(p, command):
    return digest({'schema': 'graph-event-source-retirement-command-hash-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'command': command})


def lock_original(db, p, kind, nonce):
    """Use exactly the existing mutation key, not a new authority or mutex."""
    nonce = _nonce(nonce)
    if kind == 'register':
        value = {'scope': 'graph-event-registration-v1', 'workspace': p.workspace_id, 'request': nonce}
    elif kind == 'revoke':
        value = {'scope': 'graph-event-producer-revocation-v2',
            'workspace_id': p.workspace_id, 'request_id': nonce}
    else:
        raise HTTPException(409, _ERROR)
    db.execute(select(func.pg_advisory_xact_lock(int(digest(value)[:15], 16))))


def _lock_retirement(db, p, nonce):
    value = {'scope': 'graph-event-source-retirement-v1',
        'workspace_id': p.workspace_id, 'request_id': _nonce(nonce)}
    db.execute(select(func.pg_advisory_xact_lock(int(digest(value)[:15], 16))))


def _by_request(db, p, nonce):
    return db.scalar(select(GraphEventSourceRetirement).where(
        GraphEventSourceRetirement.workspace_id == p.workspace_id,
        GraphEventSourceRetirement.request_id == nonce).execution_options(populate_existing=True))


def _by_original(db, p, kind, nonce):
    return db.scalar(select(GraphEventSourceRetirement).where(
        GraphEventSourceRetirement.workspace_id == p.workspace_id,
        GraphEventSourceRetirement.kind == kind,
        GraphEventSourceRetirement.original_request_id == nonce).execution_options(populate_existing=True))


def checked_receipt(row, p, *, request_id=None, kind=None, original_request_id=None):
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原请求账号核对这项退休记录')
    try:
        if (not all(_uuid(value) for value in (row.id, row.workspace_id, row.actor_id,
                row.request_id, row.original_request_id)) or row.workspace_id != p.workspace_id
                or row.kind not in {'register', 'revoke'} or type(row.version) is not int or row.version != 1
                or (request_id is not None and row.request_id != request_id)
                or (kind is not None and row.kind != kind)
                or (original_request_id is not None and row.original_request_id != original_request_id)
                or type(row.data) is not dict or set(row.data) != {'command', 'result'}):
            raise ValueError('source_retirement_record')
        raw, result = row.data['command'], row.data['result']
        _json(row.data)
        if type(raw) is not dict or type(result) is not dict:
            raise ValueError('source_retirement_data')
        typed = Retirement.model_validate(raw)
        frozen = typed.model_dump(mode='json')
        if (digest(frozen) != digest(raw) or str(typed.request_id) != row.request_id
                or typed.kind != row.kind or typed.original['request_id'] != row.original_request_id
                or type(row.original_hash) is not str or _HASH.fullmatch(row.original_hash) is None
                or row.original_hash != original_hash(p, row.kind, typed.original)
                or type(row.command_hash) is not str or _HASH.fullmatch(row.command_hash) is None
                or row.command_hash != command_hash(p, raw)
                or set(result) != {'outcome', 'kind', 'original_request_id', 'original_command_hash'}
                or result != {'outcome': 'retired', 'kind': row.kind,
                    'original_request_id': row.original_request_id, 'original_command_hash': row.original_hash}):
            raise ValueError('source_retirement_pin')
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, _ERROR) from None
    return {'id': row.id, 'request_id': row.request_id, 'actor_id': row.actor_id, 'kind': row.kind,
        'original_request_id': row.original_request_id, 'original_command_hash': row.original_hash,
        'command_hash': row.command_hash, 'command': copy.deepcopy(raw), 'result': copy.deepcopy(result)}


def _saved_row(db, p, kind, nonce):
    model = GraphEventProducer if kind == 'register' else GraphEventProducerRevocation
    return db.scalar(select(model).where(model.workspace_id == p.workspace_id,
        model.request_id == nonce).execution_options(populate_existing=True))


def _saved_value(row, p, kind):
    actor_id = row.registered_by if kind == 'register' else row.actor_id
    if actor_id != p.user_id:
        raise HTTPException(403, '请由原请求账号核对这项来源记录')
    if kind == 'register':
        from .graph_event_api import public
        saved = public(row)
        if row.command_hash != original_hash(p, kind, saved['registration']['command']):
            raise HTTPException(409, _ERROR)
    else:
        from .graph_event_revocation_v122 import checked_receipt as checked_revocation
        saved = checked_revocation(row, p, request_id=row.request_id)
    return row.command_hash, saved


def _state(db, p, kind, nonce):
    """Caller holds only this original nonce; never acquire a new nonce lock here."""
    saved_row = _saved_row(db, p, kind, nonce)
    retired_row = _by_original(db, p, kind, nonce)
    if saved_row is not None:
        saved_hash, saved = _saved_value(saved_row, p, kind)
    if retired_row is not None:
        receipt = checked_receipt(retired_row, p, kind=kind, original_request_id=nonce)
    if saved_row is not None and retired_row is not None:
        raise HTTPException(409, '原请求同时存在保存与退休记录，停止操作并保留全部记录')
    base = {'schema': 'graph-event-source-request-state-v1', 'kind': kind, 'request_id': nonce,
        'outcome': 'not_found', 'command_hash': None, 'saved': None, 'retirement': None}
    if saved_row is not None:
        return {**base, 'outcome': 'saved', 'command_hash': saved_hash, 'saved': saved}
    if retired_row is not None:
        return {**base, 'outcome': 'retired', 'command_hash': receipt['original_command_hash'], 'retirement': receipt}
    return base


def guard_not_retired(db, p, kind, raw_original):
    """Called after the original mutation lock, before prior/live source checks."""
    if kind not in {'register', 'revoke'} or type(raw_original) is not dict:
        raise HTTPException(409, _ERROR)
    nonce = _nonce(raw_original.get('request_id'))
    row = _by_original(db, p, kind, nonce)
    if row is not None:
        checked_receipt(row, p, kind=kind, original_request_id=nonce)
        raise HTTPException(409, '这份原请求已经退休，迟到的原提交不能再创建或停止来源')


@router.get('/command-requests/{kind}/{request_id}')
def state(kind: Kind, request_id: UUID, p: P, db: DB):
    nonce = str(request_id)
    lock_original(db, p, kind, nonce)
    return _state(db, p, kind, nonce)


@router.post('/command-retirements')
def retire(body: Retirement, p: P, db: DB):
    require(p, 'admin')
    lock_current_actor(db, p)
    raw = body.model_dump(mode='json')
    nonce, original_nonce = str(body.request_id), body.original['request_id']
    _lock_retirement(db, p, nonce)
    prior = _by_request(db, p, nonce)
    signature = command_hash(p, raw)
    prior_receipt = None
    if prior is not None:
        prior_receipt = checked_receipt(prior, p, request_id=nonce)
        if prior_receipt['command_hash'] != signature or prior_receipt['command'] != raw:
            raise HTTPException(409, '该退休编号已有另一份原命令，原记录保留')
    lock_original(db, p, body.kind, original_nonce)
    if body.kind == 'register':
        from .graph_event_native_producer_requests_v136 import guard_foreign_registration_nonce
        guard_foreign_registration_nonce(db, p, original_nonce)
    current = _state(db, p, body.kind, original_nonce)
    if prior_receipt is not None:
        if current['outcome'] != 'retired' or current['retirement']['id'] != prior_receipt['id']:
            raise HTTPException(409, _ERROR)
        db.commit()
        return {'retirement': prior_receipt, 'replayed': True}
    if current['outcome'] == 'saved':
        raise HTTPException(409, '原来源请求已经保存，请核对原结果，不能冒认为本次退休成功')
    if current['outcome'] == 'retired':
        raise HTTPException(409, '这份原请求已有另一个退休编号，请核对原退休回执')
    source_hash = original_hash(p, body.kind, body.original)
    result = {'outcome': 'retired', 'kind': body.kind, 'original_request_id': original_nonce,
        'original_command_hash': source_hash}
    row = GraphEventSourceRetirement(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        kind=body.kind, original_request_id=original_nonce, request_id=nonce,
        original_hash=source_hash, command_hash=signature, data={'command': raw, 'result': result})
    db.add(row); db.flush()
    receipt = checked_receipt(row, p, request_id=nonce)
    audit(db, p, 'graph_event.source_request_retired', row.id,
        {'kind': body.kind, 'request_id': nonce, 'original_request_id': original_nonce,
            'original_command_hash': source_hash, 'command_hash': signature, 'reason': body.reason})
    db.commit()
    return {'retirement': receipt, 'replayed': False}


@router.get('/command-retirements/requests/{request_id}')
def request(request_id: UUID, p: P, db: DB):
    nonce = str(request_id)
    _lock_retirement(db, p, nonce)
    row = _by_request(db, p, nonce)
    if row is None:
        return {'found': False, 'retirement': None}
    receipt = checked_receipt(row, p, request_id=nonce)
    lock_original(db, p, row.kind, row.original_request_id)
    current = _state(db, p, row.kind, row.original_request_id)
    if current['outcome'] != 'retired' or current['retirement']['id'] != row.id:
        raise HTTPException(409, _ERROR)
    return {'found': True, 'retirement': receipt}
