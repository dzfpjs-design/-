"""Admin-approved, encrypted Cloud API 1 Dock onboarding configuration."""
import hashlib
import json
import os
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select, text

from .contracts import digest
from .ftps_gateway_api import _current_admin
from .managed_file_grants import _key, _key_guard, _mac, _uuid
from .managed_mqtt_grants import get_grant, lock_workspace
from .models import Job
from .security import require
from .services import audit
from .setup_api import P, DB
from .dji_onboarding_protocol import validate_config

router = APIRouter(prefix='/api/dji/onboarding', tags=['大疆 Cloud API 1 安全上云配置'])
KIND = 'dji_onboarding_v1'
OP = 'dji_onboarding_op_v1'
STOP = 'dji_onboarding_stop_v1'


def experimental_enabled():
    # Cloud API 1 identity/authentication and simulator compatibility have not
    # been verified. Never expose a credential-serving responder by default.
    return os.getenv('GA_DJI_ONBOARDING_EXPERIMENTAL', '') == '1'


def _op(p, rid): return f'dji-onboard-op:{p.user_id}:{rid}'
def _row(db, w, key):
    return db.scalar(select(Job).where(Job.workspace_id == w, Job.idempotency_key == key)
                     .execution_options(populate_existing=True))


def _public(row):
    data = row.data
    return {'id': row.id, 'version': row.version, 'dock_sn': data['dock_sn'],
            'aircraft_sn': data['aircraft_sn'], 'organization_name': data['organization_name'],
            'broker_authorization_id': data['broker_authorization_id'],
            'enabled': data['enabled'], 'bound_devices': data.get('bound_devices', []),
            'receiver_state': data.get('receiver_state', 'waiting'),
            'receiver_reported_at': data.get('receiver_reported_at'),
            'last_stage': data.get('last_stage'), 'last_reply_at': data.get('last_reply_at'),
            'notice': '仅支持 Cloud API 1；接收器状态为上次报告，回复已发送不代表设备已认证、上云成功或可飞。'}


def decrypt(db, row):
    try:
        key = _key(); _key_guard(db, key, row.workspace_id)
        value = json.loads(Fernet(key).decrypt(row.data['cipher'].encode('ascii')))
        if value['workspace_id'] != row.workspace_id or value['id'] != row.id:
            raise ValueError('scope mismatch')
        cfg = validate_config(value['config'])
        if (cfg['dock_sn'] != row.data['dock_sn'] or cfg['aircraft_sn'] != row.data['aircraft_sn']
                or cfg['organization_name'] != row.data['organization_name']):
            raise ValueError('configuration mismatch')
        return cfg
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(503, '上云凭据无法核对，请恢复原密钥') from None


async def _body(request):
    chunks = []; length = 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > 65536: raise HTTPException(413, '上云配置过大')
        chunks.append(chunk)
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ValueError()
            out[key] = value
        return out
    try:
        return json.loads(b''.join(chunks), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, '上云配置无法读取') from None


def _receipt(row, rid):
    if row.kind == STOP: return {'request_id': rid, 'state': 'cancelled'}
    if row.kind != OP: raise HTTPException(409, '原请求无法核对')
    return {'request_id': rid, 'state': 'succeeded', 'configuration': row.data['result']}


@router.get('/configurations')
def list_configurations(p: P, db: DB, response: Response):
    require(p, 'admin', 'operator', 'viewer'); response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
                                      Job.status == 'internal').order_by(Job.created_at.desc()).limit(100)).all()
    return {'configurations': [_public(row) for row in rows], 'experimental_enabled': experimental_enabled(),
            'notice': '受控测试功能默认关闭。Cloud API 1 不支持 Cloud API 2 官方模拟器；Broker ACL 和设备身份必须独立核验。'}


@router.post('/configurations')
async def create_configuration(request: Request, p: P, db: DB):
    require(p, 'admin')
    if not experimental_enabled(): raise HTTPException(409, '受控上云测试未启用，不能登记凭据')
    body = await _body(request)
    if (not isinstance(body, dict) or set(body) != {'request_id', 'broker_authorization_id', 'config', 'confirm', 'broker_acl_confirmed'}
            or body['confirm'] is not True or body['broker_acl_confirmed'] is not True):
        raise HTTPException(422, '请确认设备及 MQTT 服务已限制上云主题读写权限')
    rid = _uuid(body['request_id']); cfg = validate_config(body['config']); grant_id = body['broker_authorization_id']
    if not isinstance(grant_id, str) or len(grant_id) != 64:
        raise HTTPException(422, '请选择已授权 MQTT 服务')
    lock_workspace(db, p.workspace_id); _current_admin(db, p)
    key = _key(); _key_guard(db, key, p.workspace_id, establish=True)
    grant = get_grant(p.workspace_id, grant_id, db)
    fingerprint = _mac(key, {'request_id': rid, 'broker_authorization_id': grant_id, 'config': cfg})
    prior = _row(db, p.workspace_id, _op(p, rid))
    if prior:
        if prior.kind == STOP: return _receipt(prior, rid)
        if prior.kind != OP or prior.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他配置')
        return _receipt(prior, rid)
    # Two workspaces must not independently acknowledge the same dock at a broker.
    n = int.from_bytes(hashlib.sha256(('dji-onboard\0'+grant['broker']+'\0'+cfg['dock_sn']).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:n)'), {'n': n})
    duplicate = db.scalar(select(Job.id).where(Job.kind == KIND, Job.status == 'internal',
        Job.data['broker'].as_string() == grant['broker'], Job.data['dock_sn'].as_string() == cfg['dock_sn']).limit(1))
    if duplicate: raise HTTPException(409, '该服务上的机场已有上云配置，请先核对原配置')
    existing = db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
        Job.status == 'internal').limit(101)).all()
    if len(existing) >= 100: raise HTTPException(409, '上云配置已达数量上限')
    row = Job(workspace_id=p.workspace_id, kind=KIND, status='internal',
        idempotency_key='dji-onboard-config:'+rid, data={'dock_sn': cfg['dock_sn'], 'aircraft_sn': cfg['aircraft_sn'],
        'organization_name': cfg['organization_name'], 'broker': grant['broker'], 'broker_authorization_id': grant_id,
        'actor_id': p.user_id, 'enabled': False, 'bound_devices': [], 'last_stage': None, 'receiver_state':'waiting', 'cipher': ''})
    db.add(row); db.flush()
    value = {'workspace_id': p.workspace_id, 'id': row.id, 'config': cfg}
    row.data = {**row.data, 'cipher': Fernet(key).encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()}; db.flush()
    result = _public(row)
    db.add(Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_op(p, rid),
               data={'actor_id': p.user_id, 'fingerprint': fingerprint, 'result': result}))
    audit(db, p, 'dji.onboarding.created', row.id, {'dock_sn': cfg['dock_sn']}); db.commit()
    return {'request_id': rid, 'state': 'succeeded', 'configuration': result}


@router.post('/configurations/{identity}/status')
async def status(identity: str, request: Request, p: P, db: DB):
    require(p, 'admin'); body = await _body(request)
    if (not isinstance(body, dict) or set(body) != {'request_id', 'version', 'enabled', 'confirm'}
            or type(body['version']) is not int or type(body['enabled']) is not bool or body['confirm'] is not True):
        raise HTTPException(422, '请确认上云配置状态')
    rid = _uuid(body['request_id']); lock_workspace(db, p.workspace_id); _current_admin(db, p)
    prior = _row(db, p.workspace_id, _op(p, rid)); fingerprint = digest({'id': identity, 'version': body['version'], 'enabled': body['enabled']})
    if prior:
        if prior.kind == STOP: return _receipt(prior, rid)
        if prior.kind != OP or prior.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他操作')
        return _receipt(prior, rid)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == p.workspace_id,
        Job.kind == KIND, Job.status == 'internal').with_for_update())
    if not row: raise HTTPException(404, '未找到上云配置')
    if row.version != body['version']: raise HTTPException(409, '上云配置已变化，请重新核对')
    if body['enabled']:
        if not experimental_enabled(): raise HTTPException(409, '受控上云测试未启用，不能启动')
        active = db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
            Job.status == 'internal', Job.data['enabled'].as_boolean().is_(True), Job.id != row.id).limit(4)).all()
        if len(active) >= 4: raise HTTPException(409, '本工作区最多同时运行四个上云接入，请先暂停其他接入')
        get_grant(p.workspace_id, row.data['broker_authorization_id'], db); decrypt(db, row)
    row.data = {**row.data, 'enabled': body['enabled']}; row.version += 1; db.flush()
    result = _public(row)
    db.add(Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_op(p, rid),
               data={'actor_id': p.user_id, 'fingerprint': fingerprint, 'result': result}))
    audit(db, p, 'dji.onboarding.status', row.id, {'enabled': body['enabled']}); db.commit()
    return {'request_id': rid, 'state': 'succeeded', 'configuration': result}


@router.get('/operations/{request_id}')
def operation(request_id: str, p: P, db: DB):
    rid = _uuid(request_id); row = _row(db, p.workspace_id, _op(p, rid))
    if not row: raise HTTPException(404, '尚未找到原请求')
    return _receipt(row, rid)


@router.post('/operations/{request_id}/cancel')
def cancel(request_id: str, p: P, db: DB):
    rid = _uuid(request_id); lock_workspace(db, p.workspace_id)
    prior = _row(db, p.workspace_id, _op(p, rid))
    if prior: return _receipt(prior, rid)
    db.add(Job(workspace_id=p.workspace_id, kind=STOP, status='internal', idempotency_key=_op(p, rid), data={'actor_id': p.user_id})); db.commit()
    return {'request_id': rid, 'state': 'cancelled'}
