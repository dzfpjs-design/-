"""Explicit, admin-only gateway writes and passive workspace inventory reads."""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import Field, SecretStr, StrictInt, field_validator
from sqlalchemy import select
from .contracts import Contract, digest
from .db import session
from .security import Principal, authenticated
from .models import Audit, Job, SensorDevice, uid
from .services import audit, scoped
from . import device_gateway_store_v268 as store
from .device_gateway_models_v268 import (DeviceGatewayConnectionV268 as Connection,
    DeviceGatewayCommandV268 as Command, DeviceGatewayDeviceGrantV268 as Grant)

class SafeGatewayRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default errors include raw input, including bearer
                # credentials or malformed JSON. Never return that payload.
                raise HTTPException(422, '网关请求格式不正确，请核对字段、版本和原请求标识') from None
        return safe_handler


router = APIRouter(prefix='/device-gateways', route_class=SafeGatewayRoute)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


class RequestBody(Contract):
    request_id: UUID

    @field_validator('request_id', mode='before')
    @classmethod
    def exact_nonce(cls, value):
        store.canonical_uuid(value)
        return value


class Create(RequestBody):
    name: str = Field(min_length=1, max_length=200)
    origin: str = Field(min_length=1, max_length=300)
    token: SecretStr

    @field_validator('name')
    @classmethod
    def title(cls, value):
        return store.text_value(value).strip()

    @field_validator('token')
    @classmethod
    def token_valid(cls, value):
        secret = value.get_secret_value()
        if not 8 <= len(secret) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
            raise ValueError('凭据应为非空ASCII bearer token')
        return value


class VersionRequest(RequestBody):
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def reason_valid(cls, value):
        return store.text_value(value, 2000).strip()


class Discover(VersionRequest):
    pass


class GrantRequest(VersionRequest):
    discovery_job_id: UUID
    device_external_id: str = Field(min_length=1, max_length=128)
    actions: list[Literal['get_status', 'read_telemetry', 'set_ptz', 'start_mission', 'return_to_dock', 'cancel_mission']] = Field(min_length=1, max_length=6)

    @field_validator('actions')
    @classmethod
    def unique_actions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('每个动作只能选择一次')
        return sorted(value)


class StatusRequest(VersionRequest):
    status: Literal['enabled', 'paused']


class CredentialRequest(VersionRequest):
    token: SecretStr
    token_valid = field_validator('token')(Create.token_valid.__func__)


@router.get('')
def connections(p: P, db: DB):
    store.member(db, p)
    rows = list(db.scalars(select(Connection).where(Connection.workspace_id == p.workspace_id)
        .order_by(Connection.created_at.desc(), Connection.id.desc()).limit(51)))
    return {'connections': [store.connection_view(db, row) for row in rows[:50]], 'has_more': len(rows) > 50}


@router.get('/requests/{request_id}')
def request_receipt(request_id: str, p: P, db: DB):
    store.member(db, p); store.canonical_uuid(request_id)
    cmd = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == request_id))
    if not cmd:
        return {'found': False, 'request_id': request_id, 'result': None}
    return {'found': True, 'request_id': request_id, 'result': store.command_result(db, p, cmd)}


@router.get('/{connection_id}')
def connection(connection_id: str, p: P, db: DB):
    store.member(db, p)
    return store.connection_view(db, scoped(db, Connection, store.canonical_uuid(connection_id), p))


@router.get('/{connection_id}/discoveries/{job_id}')
def discovery(connection_id: str, job_id: str, p: P, db: DB):
    store.member(db, p)
    row = scoped(db, Connection, store.canonical_uuid(connection_id), p)
    job = scoped(db, Job, store.canonical_uuid(job_id), p)
    if job.kind != store.KIND or job.data.get('connection_id') != row.id:
        raise HTTPException(404, '此连接没有该目录发现任务')
    return store.discovery_view(db, row, job)


@router.post('')
def create_connection(body: Create, p: P, db: DB):
    from .gateway_credentials_v268 import seal, validate_origin
    cmd, prior = store.command(db, p, 'create', body)
    if prior is not None:
        return prior
    origin = validate_origin(body.origin)
    row = Connection(id=uid(), workspace_id=p.workspace_id, name=body.name, origin=origin,
        protocol=store.PROTOCOL, status='enabled', version=1, credential_revision=1,
        data={'protocol_scope': 'simulated_equipment_catalogue_only'})
    row.token_cipher = seal(p.workspace_id, row.id, 1, body.token.get_secret_value())
    db.add(row); db.flush(); store.accept_command(db, p, cmd, row)
    return store.finish_command(db, p, cmd, row)


@router.post('/{connection_id}/discover')
def discover_connection(connection_id: str, body: Discover, p: P, db: DB):
    store.canonical_uuid(connection_id)
    cmd, prior = store.command(db, p, 'discover', body, connection_id)
    if prior is not None:
        return prior
    row = store.checked_connection(db, p, connection_id, body.expected_version)
    if row.status != 'enabled':
        raise HTTPException(409, '连接已暂停，先明确恢复连接')
    store.accept_command(db, p, cmd, row)
    job = Job(id=uid(), workspace_id=p.workspace_id, kind=store.KIND,
        idempotency_key='device-gateway-v268:' + cmd.id, status='queued', data={
            'schema': 'device-gateway-discovery-job-v268', 'connection_id': row.id,
            'actor_id': p.user_id, 'command_id': cmd.id, 'request_id': cmd.request_id,
            'connection_version': row.version, 'credential_revision': row.credential_revision,
            'origin': row.origin, 'protocol': row.protocol, 'payload_hmac': cmd.payload_hmac})
    db.add(job); db.flush()
    audit(db, p, store.QUEUED, job.id, store.admission(job))
    db.flush()
    return store.finish_command(db, p, cmd, row, discovery=job)


@router.post('/{connection_id}/grant')
def grant_device(connection_id: str, body: GrantRequest, p: P, db: DB):
    store.canonical_uuid(connection_id)
    cmd, prior = store.command(db, p, 'grant', body, connection_id)
    if prior is not None:
        return prior
    row = store.checked_connection(db, p, connection_id, body.expected_version)
    job = scoped(db, Job, str(body.discovery_job_id), p)
    if job.kind != store.KIND or job.data.get('connection_id') != row.id:
        raise HTTPException(409, '目录发现不属于当前连接')
    view = store.discovery_view(db, row, job)
    if not view['effective']:
        raise HTTPException(409, '目录发现未成功、已过期或完整性核对失败，请明确重新发现')
    device = next((d for d in view['devices'] if d['external_id'] == body.device_external_id), None)
    if not device or not set(body.actions) <= set(device['actions']):
        raise HTTPException(422, '只能选择本次明确目录中该设备声明的动作')
    store.accept_command(db, p, cmd, row)
    issuer = 'gateway:' + row.id
    sensor = db.scalar(select(SensorDevice).where(SensorDevice.workspace_id == p.workspace_id,
        SensorDevice.identity_scheme == 'supplier_device_id', SensorDevice.identity_issuer == issuer,
        SensorDevice.identity_reference == device['external_id']))
    if not sensor:
        from .device_identity import create, DeviceCreate
        sensor = create(db, p, DeviceCreate(title=device['name'], identity_scheme='supplier_device_id',
            identity_issuer=issuer, identity_reference=device['external_id'], reason=body.reason))
    grant = db.scalar(select(Grant).where(Grant.workspace_id == p.workspace_id,
        Grant.connection_id == row.id, Grant.device_external_id == device['external_id']))
    same = (grant is not None and grant.discovery_job_id == job.id and grant.data.get('actions') == body.actions
        and grant.connection_version == row.version and grant.credential_revision == row.credential_revision
        and grant.catalogue_digest == view['catalogue_digest'] and store.grant_view(db, row, grant)['effective'])
    if not same:
        if grant:
            grant.version += 1
        else:
            grant = Grant(id=uid(), workspace_id=p.workspace_id, connection_id=row.id,
                device_external_id=device['external_id'], sensor_device_id=sensor.id, version=1)
            db.add(grant)
        grant.actor_id = p.user_id; grant.discovery_job_id = job.id
        grant.connection_version = row.version; grant.credential_revision = row.credential_revision
        grant.catalogue_digest = view['catalogue_digest']; grant.data = {'actions': body.actions}
        proof = store.grant_proof(grant)
        grant.data = {**grant.data, 'proof': proof}
        audit(db, p, 'device.gateway_granted_v268', grant.id, proof)
    db.flush()
    return store.finish_command(db, p, cmd, row, grant=grant)


@router.post('/{connection_id}/status')
def change_status(connection_id: str, body: StatusRequest, p: P, db: DB):
    store.canonical_uuid(connection_id)
    cmd, prior = store.command(db, p, 'status', body, connection_id)
    if prior is not None:
        return prior
    row = store.checked_connection(db, p, connection_id, body.expected_version)
    store.accept_command(db, p, cmd, row)
    row.status = body.status; row.version += 1
    return store.finish_command(db, p, cmd, row)


@router.post('/{connection_id}/credential')
def change_credential(connection_id: str, body: CredentialRequest, p: P, db: DB):
    from .gateway_credentials_v268 import seal
    store.canonical_uuid(connection_id)
    cmd, prior = store.command(db, p, 'credential', body, connection_id)
    if prior is not None:
        return prior
    row = store.checked_connection(db, p, connection_id, body.expected_version)
    store.accept_command(db, p, cmd, row)
    row.credential_revision += 1; row.version += 1
    row.token_cipher = seal(p.workspace_id, row.id, row.credential_revision, body.token.get_secret_value())
    return store.finish_command(db, p, cmd, row)
