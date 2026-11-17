"""Workspace-isolated Cloud API 2 connection configuration.

The connection record is not evidence of a live DJI platform. The SDK runs
outside this process, one connection per isolated SDK process. Helpers below
fence all runtime observations by configuration generation.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select, text

from .contracts import digest
from .dji_onboarding_api import _body
from .dji_cloud2_process import init_frame, BridgeError
from .dji_cloud2_auth_epoch import actor_identity
from .ftps_gateway_api import _current_admin
from .managed_file_grants import _key, _key_guard, _mac, _uuid, _safe_text
from .managed_modbus_grants import in_ceiling
from .models import Job, now
from .security import require
from .services import audit
from .setup_api import P, DB

router = APIRouter(prefix='/api/dji/cloud2', tags=['大疆 Cloud API 2 连接'])
KIND = 'dji_cloud2_connection_v1'
OP = 'dji_cloud2_operation_v1'
STOP = 'dji_cloud2_stop_v1'
_HEADER = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
_FORBIDDEN = {'host', 'connection', 'content-length', 'transfer-encoding', 'cookie', 'set-cookie',
              'proxy-authorization', 'proxy-authenticate', 'upgrade', 'te', 'trailer', 'keep-alive',
              'origin', 'accept-encoding', 'auth_keys'}
_STATES = {'not_verified', 'connecting', 'disconnected', 'authentication_failed', 'sdk_unavailable', 'connected', 'revoked'}
_WS_STATES = {'pending', 'connected', 'auth_401', 'auth_403', 'disconnected'}


class _AuthorizationLost(HTTPException):
    def __init__(self):
        super().__init__(403, '授权人身份或权限代次已变化，请撤销并重新新增连接')


class _AuthorizationUnavailable(HTTPException):
    def __init__(self, detail='安全校验暂不可用，请保留记录并联系管理员恢复原配置'):
        super().__init__(503, detail)


def _lock(db, workspace, *, shared=False):
    if db.get_bind().dialect.name != 'postgresql':
        raise HTTPException(503, 'Cloud API 2 连接需要 PostgreSQL 事务隔离')
    number = int.from_bytes(hashlib.sha256(('dji-cloud2\0'+workspace).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock_shared(:n)' if shared else 'SELECT pg_advisory_xact_lock(:n)'), {'n': number})


def _row(db, workspace, key):
    return db.scalar(select(Job).where(Job.workspace_id == workspace, Job.idempotency_key == key)
                     .execution_options(populate_existing=True))


def _operation_key(actor, rid): return 'dji-cloud2-op:'+str(actor)+':'+rid


def _endpoint_allowed(host, port):
    # Two operator-managed boundaries: private CIDR and exact host:port.
    raw = os.getenv('GA_DJI_CLOUD2_ENDPOINTS', '')
    endpoints = raw.split(',') if raw else []
    return len(endpoints) <= 100 and in_ceiling(host) and f'{host}:{port}' in endpoints


def _header(name, value):
    if (not isinstance(name, str) or not _HEADER.fullmatch(name) or name.lower() in _FORBIDDEN
            or name.lower().startswith('sec-websocket-')
            or not _safe_text(value, 2048) or not value.isascii() or ';' in value):
        raise HTTPException(422, '鉴权请求头名称或内容无效')
    return {'name': name, 'value': value}


def _headers(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise HTTPException(422, 'HTTP 和 WebSocket 各需要 1 至 8 个鉴权请求头')
    result = []
    for item in values:
        if not isinstance(item, dict) or set(item) != {'name', 'value'}:
            raise HTTPException(422, '鉴权请求头字段无效')
        result.append(_header(item['name'], item['value']))
    if len({h['name'].lower() for h in result}) != len(result):
        raise HTTPException(422, '同一类鉴权请求头名称不能重复')
    return result


def _names(data, prefix):
    return data.get(prefix+'_header_names', [data[prefix+'_header_name']])


def _binding(row, workspace, d):
    value = {'id': row.id, 'workspace': workspace, 'host': d['host'], 'port': d['port'],
             'http_header_name': d['http_header_name'], 'ws_header_name': d['ws_header_name'],
             'actor_id': d['actor_id']}
    if 'http_header_names' in d:
        value.update(http_header_names=d['http_header_names'], ws_header_names=d['ws_header_names'])
    if 'authorization' in d:
        value['authorization'] = d['authorization']
    return value


def _public(row):
    data = row.data
    state = 'revoked' if not data['enabled'] else data.get('runtime_state', 'not_verified')
    if state not in _STATES: state = 'not_verified'
    if state == 'connected':
        try:
            age = (now() - datetime.fromisoformat(data['last_checked_at'])).total_seconds()
            if age < 0 or age > 90: state = 'disconnected'
        except (KeyError, ValueError, TypeError):
            state = 'disconnected'
    ws_last = data.get('websocket_state') if data.get('websocket_state') in _WS_STATES else None
    ws_state = 'unknown'
    if data['enabled'] and state == 'connected' and ws_last:
        try:
            age = (now() - datetime.fromisoformat(data['websocket_checked_at'])).total_seconds()
            if 0 <= age <= 30: ws_state = ws_last
        except (KeyError, ValueError, TypeError):
            pass
    return {'id': row.id, 'version': row.version, 'name': data['name'], 'host': data['host'],
            'port': data['port'], 'http_header_name': data['http_header_name'],
            'ws_header_name': data['ws_header_name'], 'http_header_names': _names(data, 'http'),
            'ws_header_names': _names(data, 'ws'), 'enabled': data['enabled'], 'status': state,
            'device_count': data.get('device_count') if state == 'connected' else None,
            'websocket_status': ws_state,
            'websocket_last_failure': (data.get('websocket_last_failure') or
                                       (ws_last if ws_last in ('auth_401', 'auth_403') else None)) if data['enabled'] else None,
            'websocket_checked_at': data.get('websocket_checked_at'),
            'last_checked_at': data.get('last_checked_at'),
            'notice': (('上次设备列表 HTTP 查询成功；设备控制尚未验证。' if ws_state != 'unknown'
                        else '上次仅设备列表 HTTP 查询成功；消息连接和设备控制尚未验证。') if state == 'connected'
                       else '本工作区的连接授权已撤销。' if state == 'revoked'
                       else '已保存配置不代表大疆底座、鉴权或设备已验证。')}


def _receipt(row, rid):
    if row.kind == STOP: return {'request_id': rid, 'state': 'cancelled'}
    if row.kind != OP: raise HTTPException(409, '原请求无法核对')
    return {'request_id': rid, 'state': 'succeeded', 'connection': row.data['result']}


def _current_receipt(db, workspace, row, rid):
    """Keep request identity while exposing current, recoverable status."""
    receipt = _receipt(row, rid)
    if row.kind == OP and receipt['connection'].get('enabled') is True:
        result = receipt['connection']
        current = db.scalar(select(Job).where(Job.id == result['id'], Job.workspace_id == workspace,
                            Job.kind == KIND, Job.status == 'internal').execution_options(populate_existing=True))
        if current is not None and current.data.get('enabled') is False:
            receipt['connection'] = _public(current)
        else:
            issue = ('authorization_unavailable' if current is None else
                     _authorization_issue(db, workspace, current.id, current.version))
            if issue:
                receipt['connection'] = _with_authorization_issue(result, issue)
    return receipt


def _checked(row, workspace):
    try:
        d = row.data
        if (row.workspace_id != workspace or row.kind != KIND or row.status != 'internal'
                or type(d['enabled']) is not bool or not _safe_text(d['name'], 100)
                or type(d['port']) is not int or not 1 <= d['port'] <= 65535
                or not isinstance(d['cipher'], str) or not isinstance(d['host'], str) or not isinstance(d['actor_id'], str)
                or not _HEADER.fullmatch(d['http_header_name']) or not _HEADER.fullmatch(d['ws_header_name'])):
            raise ValueError()
        for prefix in ('http', 'ws'):
            names = _names(d, prefix)
            parsed = _headers([{'name': name, 'value': 'x'} for name in names])
            if [v['name'] for v in parsed] != names or names[0] != d[prefix+'_header_name']:
                raise ValueError()
        return d
    except (KeyError, ValueError, TypeError, HTTPException):
        raise HTTPException(409, '连接配置无法核对，请联系系统管理员') from None


def acquire_snapshot(db, workspace, identity):
    """Within a short DB transaction, return active credentials + generation.

    The caller must release this transaction before any external operation.
    An already-started operation cannot be unsent; results MUST go through
    publish_state after completing, and revoked processes must be terminated.
    """
    _lock(db, workspace, shared=True)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == workspace,
                    Job.kind == KIND, Job.status == 'internal').execution_options(populate_existing=True))
    if not row: raise HTTPException(404, '未找到 Cloud API 2 连接')
    d = _checked(row, workspace)
    if not d['enabled']:
        raise _AuthorizationLost()
    if not _endpoint_allowed(d['host'], d['port']):
        raise _AuthorizationUnavailable('允许的底座地址暂不可用，请保留记录并联系管理员恢复原配置')
    # Legacy configurations have no durable identity generation and cannot
    # safely become active again after a permission loss and restoration.
    expected = d.get('authorization')
    if not isinstance(expected, dict):
        raise _AuthorizationLost()
    try:
        current = actor_identity(db, workspace, d['actor_id'])
    except HTTPException as error:
        if error.status_code == 403:
            raise _AuthorizationLost() from None
        raise _AuthorizationUnavailable() from None
    if current != expected:
        raise _AuthorizationLost()
    try:
        key = _key(); _key_guard(db, key, workspace)
        secret = json.loads(Fernet(key).decrypt(d['cipher'].encode()))
        if secret['binding'] != digest(_binding(row, workspace, d)): raise ValueError()
        if 'http_headers' in secret:
            http = _headers(secret['http_headers']); ws = _headers(secret['ws_headers'])
        else:
            http = _headers([{'name': d['http_header_name'], 'value': secret['http_value']}])
            ws = _headers([{'name': d['ws_header_name'], 'value': secret['ws_value']}])
        if [v['name'] for v in http] != _names(d, 'http') or [v['name'] for v in ws] != _names(d, 'ws'):
            raise ValueError()
        return {'id': row.id, 'workspace_id': workspace, 'version': row.version,
                'host': d['host'], 'port': d['port'], 'http_headers': http, 'ws_headers': ws}
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError, HTTPException):
        raise _AuthorizationUnavailable('连接凭据暂无法核对，请保留记录并联系管理员恢复原密钥') from None


def publish_state(db, workspace, identity, expected_version, state, device_count=None):
    """Fence late worker results against revocation or changed scope."""
    if state not in _STATES - {'revoked'}: raise ValueError('unsupported state')
    if state == 'connected' and (type(device_count) is not int or not 0 <= device_count <= 100000): raise ValueError('invalid device count')
    if state != 'connected' and device_count is not None: raise ValueError('unexpected device count')
    _lock(db, workspace)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == workspace,
                    Job.kind == KIND, Job.status == 'internal').with_for_update())
    if not row: return False
    d = _checked(row, workspace)
    if not d['enabled'] or row.version != expected_version or not _endpoint_allowed(d['host'], d['port']): return False
    try:
        if acquire_snapshot(db, workspace, identity)['version'] != expected_version: return False
    except HTTPException:
        return False
    updated = {**d, 'runtime_state': state, 'device_count': device_count, 'last_checked_at': now().isoformat()}
    if state != 'connected':
        # A stopped/restarted child cannot inherit an earlier process's OnOpen.
        if d.get('websocket_state') in ('auth_401', 'auth_403'):
            updated['websocket_last_failure'] = d['websocket_state']
        updated.pop('websocket_state', None)
        updated.pop('websocket_checked_at', None)
    row.data = updated
    db.flush()
    return True


def publish_websocket_state(db, workspace, identity, expected_version, state, last_failure=None):
    """Save only a fixed SDK callback state after the same authorization fence."""
    if (state, last_failure) not in {('pending', None), ('connected', None), ('disconnected', None),
                                    ('disconnected', 'auth_401'), ('disconnected', 'auth_403')}:
        raise ValueError('unsupported websocket state')
    _lock(db, workspace)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == workspace,
                    Job.kind == KIND, Job.status == 'internal').with_for_update())
    if not row:
        return False
    data = _checked(row, workspace)
    if not data['enabled'] or row.version != expected_version or not _endpoint_allowed(data['host'], data['port']):
        return False
    try:
        if acquire_snapshot(db, workspace, identity)['version'] != expected_version:
            return False
    except HTTPException:
        return False
    updated = {**data, 'websocket_state': state, 'websocket_checked_at': now().isoformat()}
    if last_failure is not None:
        updated['websocket_last_failure'] = last_failure
    elif state == 'connected':
        updated.pop('websocket_last_failure', None)
    row.data = updated
    db.flush()
    return True


def is_current(db, workspace, identity, expected_version):
    try:
        return acquire_snapshot(db, workspace, identity)['version'] == expected_version
    except HTTPException:
        return False


def _authorization_issue(db, workspace, identity, expected_version):
    try:
        snapshot = acquire_snapshot(db, workspace, identity)
        # A concurrent change may be temporary; do not tell the user to
        # destroy a potentially recoverable record on that basis alone.
        return None if snapshot['version'] == expected_version else 'authorization_unavailable'
    except _AuthorizationLost:
        return 'authorization_lost'
    except HTTPException:
        return 'authorization_unavailable'


def _with_authorization_issue(item, issue):
    notice = ('授权人身份或权限已变化，请先撤销旧连接，再重新新增。'
              if issue == 'authorization_lost' else
              '安全校验、凭据或允许地址暂不可用，请保留记录并联系管理员恢复原配置。')
    return {**item, 'status': issue, 'device_count': None, 'websocket_status': 'unknown',
            'websocket_last_failure': None, 'notice': notice}


@router.get('/connections')
def list_connections(p: P, db: DB, response: Response):
    require(p, 'admin', 'operator', 'viewer'); response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
                                        Job.status == 'internal').order_by(Job.created_at.desc()).limit(100)).all()
    items = []
    for row in rows:
        item = _public(row)
        issue = _authorization_issue(db, p.workspace_id, row.id, row.version) if item['enabled'] else None
        if issue:
            item = _with_authorization_issue(item, issue)
        items.append(item)
    return {'connections': items,
            'notice': 'Cloud API 2 底座和官方模拟器尚需许可证与实际环境验证。'}


@router.post('/connections')
async def create_connection(request: Request, p: P, db: DB):
    require(p, 'admin'); body = await _body(request)
    common = {'request_id', 'name', 'host', 'port', 'confirm'}
    legacy = {'http_header_name', 'http_header_value', 'ws_header_name', 'ws_header_value'}
    multi = {'http_headers', 'ws_headers'}
    if not isinstance(body, dict) or set(body) not in (common | legacy, common | multi) or body['confirm'] is not True:
        raise HTTPException(422, '请填写并确认连接配置')
    rid = _uuid(body['request_id'])
    if not _safe_text(body['name'], 100) or type(body['port']) is not int or not 1 <= body['port'] <= 65535:
        raise HTTPException(422, '连接名称或端口无效')
    if not _endpoint_allowed(body['host'], body['port']): raise HTTPException(422, '地址或端口不在系统管理员允许的范围内')
    if 'http_headers' in body:
        http = _headers(body['http_headers']); ws = _headers(body['ws_headers'])
    else:
        http = _headers([{'name': body['http_header_name'], 'value': body['http_header_value']}])
        ws = _headers([{'name': body['ws_header_name'], 'value': body['ws_header_value']}])
    try:
        init_frame(body['host'], body['port'], {h['name']: h['value'] for h in http},
                   {h['name']: h['value'] for h in ws})
    except BridgeError:
        raise HTTPException(422, '鉴权请求头总长度超过连接器支持范围') from None
    _lock(db, p.workspace_id); _current_admin(db, p)
    key = _key(); _key_guard(db, key, p.workspace_id, establish=True)
    fingerprint = _mac(key, {k: v for k, v in body.items() if k != 'confirm'})
    prior = _row(db, p.workspace_id, _operation_key(p.user_id, rid))
    if prior:
        if prior.kind == STOP: return _receipt(prior, rid)
        if prior.kind != OP or prior.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他配置')
        return _current_receipt(db, p.workspace_id, prior, rid)
    authorization = actor_identity(db, p.workspace_id, p.user_id)
    count = len(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
                                                Job.status == 'internal').limit(100)).all())
    if count >= 100: raise HTTPException(409, '连接配置已达数量上限')
    active = len(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
                     Job.status == 'internal', Job.data['enabled'].as_boolean().is_(True)).limit(8)).all())
    if active >= 8: raise HTTPException(409, '本工作区最多允许 8 个有效连接，请先撤销其他连接')
    row = Job(workspace_id=p.workspace_id, kind=KIND, status='internal', idempotency_key='dji-cloud2:'+str(p.user_id)+':'+rid,
              data={'name': body['name'].strip(), 'host': body['host'], 'port': body['port'],
                    'http_header_name': http[0]['name'], 'ws_header_name': ws[0]['name'],
                    'http_header_names': [v['name'] for v in http], 'ws_header_names': [v['name'] for v in ws],
                    'enabled': True, 'actor_id': p.user_id, 'authorization': authorization,
                    'runtime_state': 'not_verified', 'cipher': ''})
    db.add(row); db.flush()
    secret = {'binding': digest(_binding(row, p.workspace_id, row.data)), 'http_headers': http, 'ws_headers': ws}
    row.data = {**row.data, 'cipher': Fernet(key).encrypt(json.dumps(secret).encode()).decode()}; db.flush()
    result = _public(row)
    db.add(Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_operation_key(p.user_id, rid),
               data={'actor_id': p.user_id, 'fingerprint': fingerprint, 'result': result}))
    audit(db, p, 'dji.cloud2.created', row.id, {'name': body['name'].strip()}); db.commit()
    return {'request_id': rid, 'state': 'succeeded', 'connection': result}


@router.post('/connections/{identity}/revoke')
async def revoke_connection(identity: str, request: Request, p: P, db: DB):
    require(p, 'admin'); body = await _body(request)
    if (not isinstance(body, dict) or set(body) != {'request_id', 'version', 'confirm'}
            or type(body['version']) is not int or body['confirm'] is not True):
        raise HTTPException(422, '请确认撤销连接授权')
    rid = _uuid(body['request_id']); _lock(db, p.workspace_id); _current_admin(db, p)
    prior = _row(db, p.workspace_id, _operation_key(p.user_id, rid))
    fingerprint = digest({'identity': identity, 'version': body['version'], 'op': 'revoke'})
    if prior:
        if prior.kind == STOP: return _receipt(prior, rid)
        if prior.kind != OP or prior.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他操作')
        return _receipt(prior, rid)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == p.workspace_id,
                    Job.kind == KIND, Job.status == 'internal').with_for_update())
    if not row: raise HTTPException(404, '未找到连接配置')
    d = _checked(row, p.workspace_id)
    if row.version != body['version']: raise HTTPException(409, '连接配置已变化，请重新核对')
    row.data = {**d, 'enabled': False, 'runtime_state': 'revoked', 'cipher': ''}; row.version += 1; db.flush()
    result = _public(row)
    db.add(Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_operation_key(p.user_id, rid),
               data={'actor_id': p.user_id, 'fingerprint': fingerprint, 'result': result}))
    audit(db, p, 'dji.cloud2.revoked', row.id); db.commit()
    return {'request_id': rid, 'state': 'succeeded', 'connection': result}


@router.get('/operations/{request_id}')
def operation(request_id: str, p: P, db: DB):
    rid = _uuid(request_id); row = _row(db, p.workspace_id, _operation_key(p.user_id, rid))
    if not row: raise HTTPException(404, '尚未找到原请求')
    return _current_receipt(db, p.workspace_id, row, rid)


@router.post('/operations/{request_id}/cancel')
def cancel(request_id: str, p: P, db: DB):
    rid = _uuid(request_id); _lock(db, p.workspace_id)
    prior = _row(db, p.workspace_id, _operation_key(p.user_id, rid))
    if prior: return _current_receipt(db, p.workspace_id, prior, rid)
    db.add(Job(workspace_id=p.workspace_id, kind=STOP, status='internal', idempotency_key=_operation_key(p.user_id, rid), data={'actor_id': p.user_id})); db.commit()
    return {'request_id': rid, 'state': 'cancelled'}
