"""Human-readable isolated device integration, with exact request recovery."""
import hashlib
import json
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import Field, StrictInt
from sqlalchemy import select, text
from .contracts import Contract, GoalSpec
from .db import session
from .models import DomainInstance, Capability, Observation, Audit, Job, now, uid
from .security import Principal, authenticated
from .services import scoped, require, check_version, audit
from .simulation_models_v176 import (SimulationEnvironmentV176 as Environment,
    SimulationDefinitionV176 as Definition, SimulationSampleV176 as Sample,
    SimulationDailyV176 as Daily, SimulationCommandV176 as Command)
from . import simulation_loop_v176 as store

router = APIRouter(prefix='/api/workspace/simulation-v176')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


class RequestId(Contract):
    request_id: str


class EnvironmentBody(RequestId):
    instance_id: str
    release_id: str
    note: str = Field(min_length=1, max_length=1000)
    confirm_simulation: bool


class DefinitionBody(RequestId):
    environment_id: str
    entity_id: str
    source_id: str
    extractor_id: str
    gateway_id: str
    business_id: str
    fields: list[str] = Field(min_length=1, max_length=4)
    interval_seconds: StrictInt = Field(ge=60, le=86400)
    valid_until: str
    goal_spec: GoalSpec | None = None


class ActionBody(RequestId):
    expected_version: StrictInt = Field(ge=1)


class DailyBody(RequestId):
    environment_id: str


def view(row, db=None):
    if isinstance(row, Environment):
        return {'id': row.id, 'version': row.version, **row.data}
    if isinstance(row, Definition):
        return {'id': row.id, 'version': row.version, 'environment_id': row.environment_id,
            'status': row.status, 'next_due': row.next_due.isoformat() if row.next_due else None, **row.data}
    if isinstance(row, Sample):
        recovery = row.data.get('recovery') or {}
        recovery_job = db.get(Job, recovery.get('job_id')) if db is not None and recovery.get('job_id') else None
        return {'id': row.id, 'version': row.version, **store.MARK, 'definition_id': row.definition_id,
            'actor_id': row.actor_id, 'recovery_job_id': recovery.get('job_id'),
            'recovery_status': recovery_job.status if recovery_job is not None else recovery.get('status'),
            'status': row.status, 'invocation_id': row.invocation_id, 'observation_id': row.observation_id,
            'received_at': row.data.get('received_at'), 'task_id': row.data.get('task_id'),
            'signal_id': row.data.get('signal_id'), 'issue': row.data.get('issue') or row.data.get('handoff_issue'),
            'receipt_evidence_id': row.data.get('receipt_evidence_id'),
            'created_at': row.created_at.isoformat()}
    return {'id': row.id, 'version': row.version, **row.data}


async def command(request, body, operation, p, db, apply):
    try: store.need(str(UUID(body.request_id)) == body.request_id, '请保留原小写UUID请求号', 422)
    except ValueError: raise HTTPException(422, '请求号格式不正确') from None
    wire = await request.body()
    store.need(len(wire) <= 65536, '请求超过64KB', 413)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            store.need(key not in result, '请求含重复字段，请保留原请求并核对', 422)
            result[key] = value
        return result
    try:
        decoded = json.loads(wire.decode('utf-8'), object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite')))
    except (ValueError, UnicodeError): raise HTTPException(422, '请求正文不是完整JSON') from None
    store.need(type(decoded) is dict and decoded.get('request_id') == body.request_id, '请求号与正文不一致', 422)
    sha = hashlib.sha256(wire).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': int(hashlib.sha256((p.workspace_id + body.request_id).encode()).hexdigest()[:15], 16)})
    prior = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == body.request_id))
    if prior:
        checked_command(db, p, prior)
        store.need(prior.actor_id == p.user_id, '请由原发起账号核对请求', 403)
        store.need(prior.body_sha256 == sha and prior.operation == operation and prior.exact_body == wire.decode('utf-8'), '原请求号已有不同正文，未重复执行')
        return prior.data['result']
    saved = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'simulation.command.saved_v176', Audit.data['request_id'].as_string() == body.request_id))
    store.need(saved is None, '原请求仍有独立保存回执，主记录缺失；禁止重新执行，请恢复原记录')
    from .relations import lock_current_actor
    lock_current_actor(db, p); require(p, 'admin', 'operator')
    result = apply()
    row = Command(id=uid(), workspace_id=p.workspace_id, request_id=body.request_id, actor_id=p.user_id,
        operation=operation, exact_body=wire.decode('utf-8'), body_sha256=sha, data={'result': result})
    from .contracts import digest
    db.add(row); audit(db, p, 'simulation.command.saved_v176', row.id, {'request_id': body.request_id,
        'body_sha256': sha, 'operation': operation, 'result_hash': digest(result)})
    db.commit(); return result


def checked_command(db, p, row):
    from .contracts import digest
    store.need(row.workspace_id == p.workspace_id and hashlib.sha256(row.exact_body.encode('utf-8')).hexdigest() == row.body_sha256)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'simulation.command.saved_v176')))
    store.need(len(events) == 1 and events[0].actor_id == row.actor_id and events[0].data == {
        'request_id': row.request_id, 'body_sha256': row.body_sha256, 'operation': row.operation,
        'result_hash': digest(row.data['result'])}, '原命令与独立保存回执不一致')


@router.get('')
def index(p: P, db: DB):
    def rows(model):
        return [view(x, db) for x in db.scalars(select(model).where(model.workspace_id == p.workspace_id).order_by(model.created_at.desc(), model.id).limit(100))]
    return {**store.MARK, 'environments': rows(Environment), 'definitions': rows(Definition), 'samples': rows(Sample), 'daily': rows(Daily)}


@router.post('/environments', status_code=201)
async def create_environment(body: EnvironmentBody, request: Request, p: P, db: DB):
    def apply():
        store.need(body.confirm_simulation is True, '请确认这是独立模拟环境', 422)
        return view(store.create_environment(db, p, body.model_dump()))
    return await command(request, body, 'environment.create', p, db, apply)


@router.post('/definitions', status_code=201)
async def create_definition(body: DefinitionBody, request: Request, p: P, db: DB):
    return await command(request, body, 'definition.create', p, db,
        lambda: view(store.create_definition(db, p, body.model_dump())))


@router.post('/definitions/{identity}/{action}')
async def action_definition(identity: str, action: str, body: ActionBody, request: Request, p: P, db: DB):
    store.need(action in {'enable', 'pause', 'sample'}, '不支持的接入操作', 404)
    def apply():
        row = scoped(db, Definition, identity, p, True); check_version(row, body.expected_version)
        store.need(row.actor_id == p.user_id or p.role == 'admin', '请由接入计划负责人管理', 403)
        if action == 'pause':
            row.status = 'paused'; row.next_due = None; row.version += 1; return view(row)
        store.checked_definition(db, p, row.id)
        if action == 'enable':
            row.status = 'enabled'; row.next_due = now(); row.version += 1; return view(row)
        return view(store.enqueue_sample(db, p, row, body.request_id))
    return await command(request, body, 'definition.' + identity + '.' + action, p, db, apply)


@router.get('/requests/{request_id}')
def original_request(request_id: str, p: P, db: DB, expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': int(hashlib.sha256((p.workspace_id + request_id).encode()).hexdigest()[:15], 16)})
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id, Command.request_id == request_id))
    if row is None:
        saved = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'simulation.command.saved_v176', Audit.data['request_id'].as_string() == request_id))
        store.need(saved is None, '原请求保存回执仍在，主记录缺失；不能认定未保存')
        return {'found': False, 'request_id': request_id}
    checked_command(db, p, row)
    store.need(row.actor_id == p.user_id, '请由原发起账号核对请求', 403)
    store.need(row.body_sha256 == expected_sha256, '原请求正文摘要不一致')
    return {'found': True, 'request_id': row.request_id, 'operation': row.operation, 'result': row.data['result']}


@router.get('/samples/{identity}')
def sample_detail(identity: str, p: P, db: DB):
    row = scoped(db, Sample, identity, p)
    result = view(row, db)
    if row.observation_id:
        obs = scoped(db, Observation, row.observation_id, p)
        from .simulation_origin_v176 import guard
        result.update(origin=guard(db, p, obs), observed_at=obs.observed_at.isoformat() if obs.observed_at else None,
            processing_status=obs.status, attributes=obs.data['attributes'], field_units=obs.data.get('field_units', {}),
            extracted=obs.data.get('extracted'), time_semantics='simulator_sample_time', source_time_verified=False)
    return result


@router.post('/daily/reconcile')
async def reconcile_daily(body: DailyBody, request: Request, p: P, db: DB):
    def apply():
        store.reconcile_daily(db, p, body.environment_id)
        return {'rows': [view(x) for x in db.scalars(select(Daily).where(Daily.environment_id == body.environment_id,
            Daily.workspace_id == p.workspace_id).order_by(Daily.business_day.desc()))], **store.MARK}
    return await command(request, body, 'daily.reconcile', p, db, apply)
