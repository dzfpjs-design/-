"""Workspace map grants, bounded by the operator's pre-authorised endpoints.

An explicit workspace decision overrides a legacy environment grant.  Both the
decision and the idempotency receipt are stored in the existing jobs table.
"""
import hashlib
import json
import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool
from sqlalchemy import select, text

from .config import settings
from .contracts import Contract, digest
from .models import Job
from .services import audit, require
from .setup_api import DB, P
from .wms_protocol import normalize_endpoint

router = APIRouter(prefix='/api/map-workspace-grants')
KIND = 'map_workspace_grant'
RECEIPT = 'map_workspace_grant_receipt'
STOP = 'map_workspace_grant_cancelled'


def key(workspace, endpoint, secret_ref):
    return hashlib.sha256((workspace + '\0' + endpoint + '\0' + secret_ref).encode()).hexdigest()


def lock(db, workspace, endpoint, secret_ref, shared=False):
    value = int.from_bytes(bytes.fromhex(key(workspace, endpoint, secret_ref))[:8], 'big', signed=True)
    # These transactions use PostgreSQL in the deployed platform.
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock_shared(:k)' if shared else 'SELECT pg_advisory_xact_lock(:k)'), {'k': value})


def decision(db, workspace, endpoint, secret_ref, *, hold=False):
    if hold:
        lock(db, workspace, endpoint, secret_ref, shared=True)
    row = db.scalar(select(Job).where(Job.workspace_id == workspace,
                    Job.idempotency_key == 'map-grant:' + key(workspace, endpoint, secret_ref)).execution_options(populate_existing=True))
    if row is None:
        return None
    data = row.data
    if (row.kind != KIND or row.status != 'internal' or data.get('endpoint') != endpoint or data.get('secret_ref') != secret_ref
            or type(data.get('allowed')) is not bool):
        raise HTTPException(409, '地图工作区授权记录无法核对，请联系管理员')
    return data['allowed']


def eligible(endpoint, workspace):
    """Allow only an exact configured endpoint and a non-special literal IP."""
    try:
        value = normalize_endpoint(endpoint)
        allowed = {v.strip() for v in settings().wms_allowed_endpoints.split(',') if v.strip()}
        scoped = json.loads(settings().wms_self_service_access)
        if not isinstance(scoped, dict):
            raise ValueError()
        permitted = scoped.get(workspace, [])
        if not isinstance(permitted, list) or any(not isinstance(x,str) or normalize_endpoint(x)!=x for x in permitted):
            raise ValueError()
        address = ipaddress.ip_address(urlsplit(value).hostname)
        if (value not in allowed or value not in permitted or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_unspecified or address.is_reserved):
            raise ValueError()
        # Public endpoints must use TLS, as in the existing WMS boundary.
        if not address.is_private and urlsplit(value).scheme != 'https':
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, '此地址不符合运行环境授权或地址安全要求，请联系运维核对') from None


class Change(Contract):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    endpoint: str = Field(min_length=1, max_length=1800)
    secret_ref: str = Field(default='', pattern=r'^(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?$')
    allowed: StrictBool
    version: int = Field(ge=0)


def _legacy(workspace):
    from .wms import grants
    return grants(workspace)


def _view(row, legacy=False):
    return {'endpoint': row.data['endpoint'], 'secret_ref': row.data['secret_ref'],
            'allowed': row.data['allowed'], 'version': row.version,
            'origin': 'workspace', 'legacy_grant': legacy}


@router.get('')
def list_grants(p: P, db: DB):
    require(p, 'admin')
    legacy = _legacy(p.workspace_id)
    result = {(x['endpoint'], x['secret_ref']): {**x, 'allowed': True, 'version': 0, 'origin': 'legacy', 'legacy_grant': True} for x in legacy}
    rows = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == KIND).limit(1001)))
    if len(rows) > 1000:
        raise HTTPException(409, '地图授权记录过多，请联系管理员处理')
    for row in rows:
        d = row.data
        # Fail closed rather than silently exposing or accepting corrupt state.
        if row.status != 'internal' or not isinstance(d, dict) or not isinstance(d.get('endpoint'), str) or not isinstance(d.get('secret_ref'), str) or type(d.get('allowed')) is not bool:
            raise HTTPException(409, '地图工作区授权记录无法核对，请联系管理员')
        pair = (d['endpoint'], d['secret_ref'])
        result[pair] = _view(row, pair in result)
    return {'grants': sorted(result.values(), key=lambda x: (x['endpoint'], x['secret_ref']))}


@router.post('')
def change(body: Change, p: P, db: DB):
    require(p, 'admin')
    try:
        target = normalize_endpoint(body.endpoint)
    except ValueError:
        raise HTTPException(422, '服务地址格式无效') from None
    submission = {**body.model_dump(), 'endpoint': target}
    receipt_key = 'map-grant-op:' + body.request_id
    # A request identifier serialises retries, even if their content differs.
    op_key = int.from_bytes(hashlib.sha256((p.workspace_id + receipt_key).encode()).digest()[:8], 'big', signed=True)
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': op_key})
    receipt = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == receipt_key))
    if receipt:
        if receipt.kind != RECEIPT or receipt.data.get('actor_id') != p.user_id or receipt.data.get('submission_hash') != digest(submission) or receipt.data.get('submission') != submission:
            raise HTTPException(409, '此操作编号已用于其他请求，原记录保留')
        return receipt.data['result']
    lock(db, p.workspace_id, target, body.secret_ref)
    record_key = 'map-grant:' + key(p.workspace_id, target, body.secret_ref)
    row = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == record_key).with_for_update())
    if row and (row.kind != KIND or row.status != 'internal' or row.data.get('endpoint') != target or row.data.get('secret_ref') != body.secret_ref):
        raise HTTPException(409, '地图工作区授权记录无法核对')
    if (row.version if row else 0) != body.version:
        raise HTTPException(409, '授权已被其他管理员更新，请刷新后核对')
    legacy = {'endpoint': target, 'secret_ref': body.secret_ref} in _legacy(p.workspace_id)
    if body.allowed:
        if not legacy:
            eligible(target, p.workspace_id)
        if body.secret_ref and not legacy:
            raise HTTPException(403, '此工作区未获该服务和凭据组合授权，请联系运维')
    elif not row and not legacy:
        raise HTTPException(404, '此工作区没有该授权')
    if row:
        row.data = {**row.data, 'allowed': body.allowed, 'actor_id': p.user_id}
        row.version += 1
    else:
        row = Job(workspace_id=p.workspace_id, kind=KIND, status='internal', idempotency_key=record_key,
                  data={'endpoint': target, 'secret_ref': body.secret_ref, 'allowed': body.allowed, 'actor_id': p.user_id})
        db.add(row)
        db.flush()
    result = {**_view(row, legacy), 'request_id': body.request_id}
    db.add(Job(workspace_id=p.workspace_id, kind=RECEIPT, status='internal', idempotency_key=receipt_key,
               data={'submission_hash': digest(submission), 'submission': submission, 'result': result, 'actor_id': p.user_id}))
    audit(db, p, 'map_workspace_grant.changed', row.id, {'allowed': body.allowed, 'version': row.version})
    db.commit()
    return result


@router.post('/requests/{request_id}/cancel')
def cancel_request(request_id: str, p: P, db: DB):
    """Close an uncertain request without allowing its late arrival to apply."""
    require(p, 'admin')
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', request_id):
        raise HTTPException(422, '授权操作编号无效')
    receipt_key = 'map-grant-op:' + request_id
    op_key = int.from_bytes(hashlib.sha256((p.workspace_id + receipt_key).encode()).digest()[:8], 'big', signed=True)
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': op_key})
    receipt = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == receipt_key))
    if receipt:
        if receipt.status != 'internal' or receipt.data.get('actor_id') != p.user_id:
            raise HTTPException(409, '原授权操作记录无法核对')
        if receipt.kind == STOP:
            return {'state': 'cancelled', 'request_id': request_id}
        if (receipt.kind == RECEIPT and isinstance(receipt.data.get('result'), dict)
                and isinstance(receipt.data.get('submission'), dict)
                and receipt.data.get('submission_hash') == digest(receipt.data['submission'])
                and receipt.data['submission'].get('request_id') == request_id):
            return {'state': 'applied', 'request_id': request_id, 'submission': receipt.data['submission'], 'result': receipt.data['result']}
        raise HTTPException(409, '原授权操作记录无法核对')
    receipt = Job(workspace_id=p.workspace_id, kind=STOP, status='internal', idempotency_key=receipt_key,
                  data={'request_id': request_id, 'actor_id': p.user_id})
    db.add(receipt)
    db.flush()
    audit(db, p, 'map_workspace_grant.request_cancelled', receipt.id, {})
    db.commit()
    return {'state': 'cancelled', 'request_id': request_id}
