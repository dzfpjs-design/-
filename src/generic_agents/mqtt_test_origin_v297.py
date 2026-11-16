"""Prospective test-source disclosures, separate from raw MQTT configuration.

A disclosure restricts interpretation; it never proves a physical measurement
or grants collection, processing, cloud use or business acceptance authority.
"""
import copy
from functools import wraps
from datetime import datetime
from uuid import UUID, uuid4
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy import func, or_, select, text

from .contracts import Contract, digest
from .models import Audit, Membership, MqttGateway, MqttReceipt, User
from .services import check_version, require, scoped
from .setup_api import P, DB
from .temporal import instant

ACTION = 'mqtt.test_origin.declared_v297'
SCHEMA = 'mqtt-test-origin-declaration-v297'
FLAGS = {'simulation_only': True, 'test_only': True, 'physical_device_connected': False,
    'source_authenticity_verified': False, 'field_verified': False}
ERROR = 'MQTT测试来源声明与原接入版本或接收时间无法核对，未更改原数据或认定现场真实'
router = APIRouter()


class DeclarationRequest(Contract):
    request_id: StrictStr = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    expected_gateway_version: StrictInt = Field(ge=1)
    expected_config_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    source_label: StrictStr = Field(min_length=1, max_length=200)
    basis_type: Literal['manufacturer_parameters', 'manual_test', 'protocol_fixture']
    basis_notes: StrictStr = Field(min_length=1, max_length=1000)


def need(value, detail=ERROR):
    if not value: raise HTTPException(409, detail)


def guarded(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError):
            raise HTTPException(409, ERROR) from None
    return wrapped


def _reader(db, p, *, lock=False):
    query = select(Membership.role).join(User, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator', 'viewer']))
    if lock: query = query.with_for_update(read=True, of=[User, Membership])
    actual = db.scalar(query.execution_options(populate_existing=True))
    # Internal receivers deliberately use a read-only viewer projection of
    # an operator/admin. Current membership must contain that effective role;
    # a stale or forged higher role can never obtain greater authority.
    ranks = {'viewer': 0, 'operator': 1, 'admin': 2}
    need(p.role in ranks and actual in ranks and ranks[actual] >= ranks[p.role])


@guarded
def _checked(row):
    value = row.data
    need(type(value) is dict and set(value) == {'schema', 'audit_id', 'workspace_id', 'gateway_id',
        'gateway_version', 'config_hash', 'actor_id', 'declared_at', 'body', 'body_hash', 'record_hash', *FLAGS}
        and row.action == ACTION and value['schema'] == SCHEMA and row.id == value['audit_id']
        and row.object_id == value['gateway_id'] and row.workspace_id == value['workspace_id']
        and row.actor_id == value['actor_id']
        and value['record_hash'] == digest({k:v for k,v in value.items() if k != 'record_hash'})
        and all(value[k] is expected for k,expected in FLAGS.items()))
    for key in ('audit_id', 'workspace_id', 'gateway_id', 'actor_id'):
        need(type(value[key]) is str and str(UUID(value[key])) == value[key])
    body = DeclarationRequest.model_validate(value['body']).model_dump(mode='json')
    need(digest(body) == digest(value['body']) and type(value['gateway_version']) is int
        and value['gateway_version'] == body['expected_gateway_version']
        and value['config_hash'] == body['expected_config_hash']
        and value['body_hash'] == digest({'gateway_id': row.object_id, 'body': body})
        and instant(value['declared_at']) == row.created_at)
    return copy.deepcopy(value)


def _view(row):
    value = _checked(row)
    return {'schema': SCHEMA, 'audit_id': row.id, 'declaration_hash': value['record_hash'],
        'gateway_id': row.object_id, 'gateway_version': value['gateway_version'],
        'config_hash': value['config_hash'], 'declared_at': value['declared_at'],
        'source_label': value['body']['source_label'], 'basis_type': value['body']['basis_type'],
        'basis_notes': value['body']['basis_notes'], **FLAGS}


def declare(db, p, gateway_id, body):
    require(p, 'admin'); _reader(db, p, lock=True)
    body = DeclarationRequest.model_validate(body).model_dump(mode='json')
    # Serialize a command nonce across gateways as well as the gateway itself.
    key = int.from_bytes(bytes.fromhex(digest({'workspace_id': p.workspace_id,
        'test_origin_request_id': body['request_id']}))[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    gateway = scoped(db, MqttGateway, gateway_id, p, lock=True)
    need(gateway.owner_id == p.user_id)
    signature = digest({'gateway_id': gateway_id, 'body': body})
    old = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.data['body']['request_id'].as_string() == body['request_id']).limit(2)))
    if old:
        need(len(old) == 1 and old[0].actor_id == p.user_id and old[0].object_id == gateway_id
            and _checked(old[0])['body_hash'] == signature,
            '此测试来源登记请求已有不同内容；请读取原记录，未覆盖声明')
        return _view(old[0])
    check_version(gateway, body['expected_gateway_version'])
    need(digest(gateway.data) == body['expected_config_hash'])
    prior = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.object_id == gateway_id,
        Audit.data['gateway_version'].as_string() == str(gateway.version)).limit(2)))
    need(not prior, '此接入版本已经声明测试来源；原声明保留，不能覆盖或取消测试标记')
    received = db.scalar(select(MqttReceipt.id).where(MqttReceipt.workspace_id == p.workspace_id,
        MqttReceipt.gateway_id == gateway.id,
        MqttReceipt.data['gateway_version'].as_string() == str(gateway.version)).limit(1))
    need(received is None, '此版本已有原始报文；测试声明须在接收前完成，请对新的接入版本登记')
    clock = db.scalar(select(func.clock_timestamp())); identity = str(uuid4())
    value = {'schema': SCHEMA, 'audit_id': identity, 'workspace_id': p.workspace_id,
        'gateway_id': gateway_id, 'gateway_version': gateway.version, 'config_hash': digest(gateway.data),
        'actor_id': p.user_id, 'declared_at': clock.isoformat(), 'body': body, 'body_hash': signature, **FLAGS}
    value['record_hash'] = digest(value)
    row = Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id, action=ACTION,
        object_id=gateway_id, created_at=clock, data=value)
    db.add(row); db.flush()
    return _view(row)


@guarded
def checked_declaration(db, p, receipt, *, cut=None):
    """Original-version disclosure only, independent of mutable gateway status."""
    _reader(db, p)
    need(receipt.workspace_id == p.workspace_id)
    version = receipt.data['gateway_version']; config_hash = receipt.data['config_hash']
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION,
        or_(Audit.object_id == receipt.gateway_id, Audit.data['gateway_id'].as_string() == receipt.gateway_id),
        Audit.data['gateway_version'].as_string() == str(version)).limit(2)))
    if not rows: return None
    need(len(rows) == 1)
    value = _checked(rows[0])
    if isinstance(cut, datetime):
        need(cut.tzinfo is not None and cut.utcoffset() is not None)
    elif cut is not None:
        cut = instant(cut)
    need(value['gateway_id'] == receipt.gateway_id and value['gateway_version'] == version
        and value['config_hash'] == config_hash == digest(receipt.data['config_snapshot'])
        and rows[0].created_at <= receipt.created_at
        and rows[0].created_at <= instant(receipt.data['received_at'])
        and (cut is None or rows[0].created_at <= cut))
    return {'simulation_only': True, 'test_only': True,
        'test_origin': {'audit_id': rows[0].id, 'declaration_hash': value['record_hash']}}


@router.post('/gateways/{identifier}/test-origin', status_code=201)
def create_declaration(identifier: str, body: DeclarationRequest, p: P, db: DB):
    value = declare(db, p, identifier, body.model_dump(mode='json'))
    db.commit()
    return value


@router.get('/gateways/{identifier}/test-origin')
def read_declaration(identifier: str, p: P, db: DB):
    _reader(db, p); gateway = scoped(db, MqttGateway, identifier, p)
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.object_id == gateway.id,
        Audit.data['gateway_version'].as_string() == str(gateway.version)).limit(2)))
    need(len(rows) <= 1)
    return {'gateway_id': gateway.id, 'gateway_version': gateway.version,
        'config_hash': digest(gateway.data), 'declaration': _view(rows[0]) if rows else None}
