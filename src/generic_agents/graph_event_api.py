"""Explicit source registration and original receipts; no graph execution grant."""
import copy
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, select

from .contracts import Contract, digest
from .models import uid, Job, now, Capability
from .relations import lock_current_actor
from .services import audit, require, scoped
from .setup_api import P, DB
from .graph_event_models import GraphEventProducer, GraphEventIntent, GraphEventAdmission
from .graph_event_admission import check_producer, producer_command_hash, read_intent, read_admission
from .graph_event_observation_sources import build_contract

router = APIRouter(prefix='/graph-event-producers')


class Register(Contract):
    schema: Literal['graph-event-producer-command-v1']
    request_id: UUID
    label: str = Field(strict=True, min_length=1, max_length=200)
    adapter_id: Literal['processed-observation-v1', 'anomaly-candidate-v1']
    source_id: str = Field(strict=True, min_length=1, max_length=36)
    source_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    extractor_id: str = Field(strict=True, min_length=1, max_length=36)
    extractor_hash: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')


def command(body):
    return body.model_dump(mode='json')


def proposed(db, p, body):
    try:
        value = build_contract(db, p, body.source_id, body.extractor_id, body.adapter_id)
    except ValueError:
        raise HTTPException(409, '来源或提取能力当前无法核对，请恢复发布版本及权限后重新预览；原登记保留') from None
    if scoped(db, Capability, body.source_id, p).namespace == 'platform_native_receipts':
        raise HTTPException(409, '原生处理结果须使用独立的本地语音来源登记，不得填写提取器冒充处理回执')
    if value['source_hash'] != body.source_hash or value['extractor_hash'] != body.extractor_hash:
        raise HTTPException(409, '来源或提取能力版本已变化，请重新读取后登记')
    return value


def public(row):
    contract = check_producer(row)
    return {'id': row.id, 'label': row.data['label'], 'status': row.status,
        'version': row.version, 'contract_hash': row.contract_hash,
        'contract': contract, 'registration': copy.deepcopy(row.data['registration']),
        'execution_granted': False, 'physical_state_verified': False}


@router.post('/preview')
def preview(body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p)
    value = proposed(db, p, body)
    result = {'contract': value, 'contract_hash': digest(value),
        'command_hash': producer_command_hash(p, command(body)), 'execution_granted': False,
        'meaning': '登记真实处理结果的消息来源；业务流程仍需单独确认来源、对象范围和等待策略。'}
    db.commit(); return result


@router.post('', status_code=201)
def register(body: Register, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p)
    value = command(body); signature = producer_command_hash(p, value)
    # Serialize this workspace's exact original registration nonce. No source
    # or caller-controlled SQL identifier is interpolated into the statement.
    key = int(digest({'scope': 'graph-event-registration-v1', 'workspace': p.workspace_id,
        'request': str(body.request_id)})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))
    from .graph_event_request_recovery_v123 import guard_not_retired
    guard_not_retired(db, p, 'register', value)
    from .graph_event_native_producer_requests_v136 import guard_foreign_registration_nonce
    guard_foreign_registration_nonce(db, p, str(body.request_id))
    prior = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.request_id == str(body.request_id)))
    if prior is not None:
        if prior.registered_by != p.user_id or prior.command_hash != signature:
            raise HTTPException(409, '该登记编号已有另一份原命令；原来源记录保留')
        result = public(prior); db.commit(); return {'producer': result, 'created': False}
    contract = proposed(db, p, body)
    row = GraphEventProducer(id=uid(), workspace_id=p.workspace_id, registered_by=p.user_id,
        request_id=str(body.request_id), command_hash=signature, contract_hash=digest(contract),
        status='active', data={'schema': 'graph-event-producer-record-v1', 'label': body.label,
            'contract': contract, 'contract_hash': digest(contract), 'registration': {
                'request_id': str(body.request_id), 'actor_id': p.user_id,
                'command_hash': signature, 'command': value}})
    db.add(row); db.flush(); result = public(row)
    audit(db, p, 'graph_event.producer_registered', row.id, {'contract_hash': row.contract_hash})
    db.commit(); return {'producer': result, 'created': True}


@router.get('')
def list_producers(p: P, db: DB):
    rows = db.scalars(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id)
        .order_by(GraphEventProducer.created_at, GraphEventProducer.id)).all()
    from .graph_event_source_families_v136 import record_family, PROCESSING
    result = []
    for row in rows:
        value = public(row)
        if record_family(row) == PROCESSING: result.append(value)
    return result


@router.get('/requests/{request_id}')
def registration_request(request_id: UUID, p: P, db: DB):
    row = db.scalar(select(GraphEventProducer).where(GraphEventProducer.workspace_id == p.workspace_id,
        GraphEventProducer.request_id == str(request_id), GraphEventProducer.registered_by == p.user_id))
    if row is not None:
        from .graph_event_source_families_v136 import record_family, PROCESSING
        if record_family(row) != PROCESSING: raise HTTPException(409, '此编号属于独立原生来源请求，请核对原入口')
    return {'found': row is not None, 'producer': public(row) if row else None}


class Revoke(Contract):
    version: int = Field(strict=True, ge=1)
    reason: str = Field(strict=True, min_length=1, max_length=1000)


@router.post('/{identity}/revoke')
def revoke(identity: str, body: Revoke, p: P, db: DB):
    require(p, 'admin'); lock_current_actor(db, p)
    row = scoped(db, GraphEventProducer, identity, p, True); check_producer(row)
    from .graph_event_source_families_v136 import record_family, PROCESSING
    if record_family(row) != PROCESSING: raise HTTPException(409, '请从原生来源入口停止此来源')
    if row.version != body.version:
        raise HTTPException(409, '来源状态已有变化，请重新读取')
    if row.status == 'active':
        row.status = 'revoked'; row.version += 1
        audit(db, p, 'graph_event.producer_revoked', row.id, {'reason': body.reason,
            'contract_hash': row.contract_hash})
    result = public(row); db.commit(); return result


@router.get('/{identity}/intents')
def intents(identity: str, p: P, db: DB, limit: int = Query(50, ge=1, le=200)):
    scoped(db, GraphEventProducer, identity, p)
    rows = db.scalars(select(GraphEventIntent).where(GraphEventIntent.workspace_id == p.workspace_id,
        GraphEventIntent.producer_id == identity, GraphEventIntent.actor_id == p.user_id)
        .order_by(GraphEventIntent.created_at.desc(), GraphEventIntent.id.desc())
        .limit(limit)).all()
    return [{'id': row.id, 'status': row.status, 'receipt_id': row.receipt_id,
        'intent': read_intent(db, p, row.id), 'delivery': delivery(db, p, row)} for row in rows]


@router.get('/admissions/{identity}')
def admission(identity: str, p: P, db: DB):
    return read_admission(db, p, identity)


def delivery(db, p, intent):
    from .graph_event_delivery_recovery_v124 import current_delivery
    return current_delivery(db, p, intent)


class RetryIntent(Contract):
    request_id: UUID
    job_version: int = Field(strict=True, ge=1)
    reason: str = Field(strict=True, min_length=1, max_length=1000)


from .graph_event_delivery_recovery_v124 import RetryInputRoute
_retry_router = APIRouter(route_class=RetryInputRoute)


@_retry_router.post('/intents/{identity}/retry')
def retry_intent(identity: str, body: RetryIntent, p: P, db: DB):
    from .graph_event_delivery_recovery_v124 import retry_intent as issue_retry
    return issue_retry(identity, body, p, db)


router.include_router(_retry_router)
