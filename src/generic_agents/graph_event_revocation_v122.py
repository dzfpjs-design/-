"""Original-command receipts for stopping a registered graph event producer.

Registration and revocation grant no graph execution authority. The receipt is
an immutable result of this command, not a view of the producer's latest state.
"""
import copy
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import Contract, digest
from .db import session
from .graph_event_admission import check_producer
from .graph_event_models import GraphEventProducer
from .models import Base, Payload, Row, Scoped, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, scoped


class GraphEventProducerRevocation(Row, Scoped, Payload, Base):
    __tablename__ = 'graph_event_producer_revocations'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    producer_id: Mapped[str] = mapped_column(ForeignKey('graph_event_producers.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    command_hash: Mapped[str] = mapped_column(String(64))


class Revocation(Contract):
    schema: Literal['graph-event-producer-revocation-v2']
    request_id: UUID
    producer_id: UUID
    producer_version: int = Field(strict=True, ge=1)
    contract_hash: str = Field(strict=True, min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(strict=True, min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def exact_reason(cls, value):
        if value != value.strip() or '\x00' in value:
            raise ValueError('请填写裁剪前后相同且不含空字符的停止原因')
        value.encode('utf-8')
        return value


# Avoid importing setup_api here: migrate imports this additive model before
# router initialization. These are the same authentication/session dependencies.
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/graph-event-producers')
_HASH = re.compile(r'[a-f0-9]{64}\Z')


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def command_hash(p, command):
    return digest({'schema': 'graph-event-producer-revocation-command-hash-v2',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'command': command})


def checked_receipt(row, p, *, request_id=None):
    """Cold-check only the saved command/result; never repair or consult live IO."""
    # Retain private-record actor precedence even if the stored body is corrupt.
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原停止账号核对这项请求')
    try:
        if (not all(_uuid(value) for value in (row.id, row.workspace_id, row.actor_id,
                row.producer_id, row.request_id)) or row.workspace_id != p.workspace_id
                or (request_id is not None and row.request_id != request_id)
                or type(row.version) is not int or row.version != 1
                or type(row.data) is not dict or set(row.data) != {'command', 'result'}):
            raise ValueError('revocation_record')
        raw, result = row.data['command'], row.data['result']
        if type(raw) is not dict or type(result) is not dict:
            raise ValueError('revocation_body')
        json.dumps(row.data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        typed = Revocation.model_validate(raw)
        frozen = typed.model_dump(mode='json')
        if (digest(frozen) != digest(raw) or str(typed.request_id) != row.request_id
                or str(typed.producer_id) != row.producer_id
                or type(row.command_hash) is not str or _HASH.fullmatch(row.command_hash) is None
                or row.command_hash != command_hash(p, raw)
                or set(result) != {'status', 'version', 'contract_hash'}
                or result['status'] != 'revoked' or type(result['version']) is not int
                or result['version'] != typed.producer_version + 1
                or type(result['contract_hash']) is not str
                or result['contract_hash'] != typed.contract_hash):
            raise ValueError('revocation_pin')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, '原停止请求与回执无法完整核对，原记录保留') from None
    return {'id': row.id, 'request_id': row.request_id, 'actor_id': row.actor_id,
        'producer_id': row.producer_id, 'command_hash': row.command_hash,
        'command': copy.deepcopy(raw), 'result': copy.deepcopy(result)}


def find(db, p, request_id):
    return db.scalar(select(GraphEventProducerRevocation).where(
        GraphEventProducerRevocation.workspace_id == p.workspace_id,
        GraphEventProducerRevocation.request_id == request_id))


@router.post('/revocations')
def revoke(body: Revocation, p: P, db: DB):
    require(p, 'admin')
    lock_current_actor(db, p)
    request_id = str(body.request_id)
    raw = body.model_dump(mode='json')
    signature = command_hash(p, raw)
    # Serialize only this workspace's revocation nonce, before taking a producer
    # lock. No Task/Job/source/extractor or workspace-wide mutex is involved.
    key = int(digest({'scope': 'graph-event-producer-revocation-v2',
        'workspace_id': p.workspace_id, 'request_id': request_id})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))
    from .graph_event_request_recovery_v123 import guard_not_retired
    guard_not_retired(db, p, 'revoke', raw)
    prior = find(db, p, request_id)
    if prior is not None:
        receipt = checked_receipt(prior, p, request_id=request_id)
        if receipt['command_hash'] != signature or receipt['command'] != raw:
            raise HTTPException(409, '该停止编号已有另一份原命令，原回执保留')
        db.commit()
        return {'receipt': receipt, 'replayed': True}

    producer = scoped(db, GraphEventProducer, str(body.producer_id), p, True)
    check_producer(producer)
    from .graph_event_source_families_v136 import record_family, PROCESSING
    if record_family(producer) != PROCESSING:
        raise HTTPException(409, '请从独立原生来源入口核对停止命令')
    if producer.version != body.producer_version or producer.contract_hash != body.contract_hash:
        raise HTTPException(409, '来源版本或完整合同已变化，请重新核对后停止')
    if producer.status != 'active':
        raise HTTPException(409, '来源已停止；该编号没有原停止回执，不能冒认本次成功')
    result = {'status': 'revoked', 'version': producer.version + 1,
        'contract_hash': producer.contract_hash}
    producer.status = 'revoked'
    producer.version += 1
    row = GraphEventProducerRevocation(id=uid(), workspace_id=p.workspace_id,
        actor_id=p.user_id, producer_id=producer.id, request_id=request_id,
        command_hash=signature, data={'command': raw, 'result': result})
    db.add(row)
    db.flush()
    receipt = checked_receipt(row, p, request_id=request_id)
    audit(db, p, 'graph_event.producer_revoked_v2', producer.id,
        {'request_id': request_id, 'receipt_id': row.id, 'command_hash': signature,
            'contract_hash': body.contract_hash, 'reason': body.reason})
    db.commit()
    return {'receipt': receipt, 'replayed': False}


@router.get('/revocations/requests/{request_id}')
def request(request_id: UUID, p: P, db: DB):
    row = find(db, p, str(request_id))
    return {'found': row is not None, 'receipt': checked_receipt(row, p,
        request_id=str(request_id)) if row is not None else None}
