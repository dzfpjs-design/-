"""Idempotent registration receipts for explicitly authorised video gateways.

The original /gateways API remains unchanged.  Receipt and gateway are committed
in one transaction; the receipt is an internal, non-claimable Job, as for existing
durable request markers.  No network request or device control occurs here.
"""
import hashlib

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field
from sqlalchemy import select, text

from .contracts import digest
from .media import origin
from .media_api import Gateway, view
from .models import Job, MediaGateway
from .services import audit, require, scoped
from .setup_api import DB, P

router = APIRouter(prefix='/api/media/gateway-registrations')
KIND = 'media_gateway_registration'


class GatewayRegistration(Gateway):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')


def _key(request_id):
    return 'media-gateway-registration:' + request_id


def _receipt(db, p, request_id):
    return db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
                                      Job.idempotency_key == _key(request_id)))


def _current(db, p, receipt, request_id):
    if (receipt.kind != KIND or receipt.status != 'internal'
            or not isinstance(receipt.data, dict)
            or receipt.data.get('request_id') != request_id
            or not isinstance(receipt.data.get('gateway_id'), str)
            or not isinstance(receipt.data.get('submission_hash'), str)):
        raise HTTPException(409, '原登记回执无法核对，请联系管理员；不会重复创建')
    gateway = scoped(db, MediaGateway, receipt.data['gateway_id'], p)
    result = view(gateway)
    # The existing gateway may have been edited after this original request.
    current = {k: gateway.data.get(k) for k in ('name', 'api_origin', 'rtsp_origin')}
    result['registration_matches_current'] = digest(current) == receipt.data['submission_hash']
    return result


@router.get('/requests/{request_id}')
def lookup(request_id: str, p: P, db: DB):
    require(p, 'admin')
    if not 12 <= len(request_id) <= 100 or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in request_id):
        raise HTTPException(422, '原登记请求标识无效')
    record = _receipt(db, p, request_id)
    if not record:
        raise HTTPException(404, '尚未找到此网关登记请求，可按原内容重试')
    return _current(db, p, record, request_id)


@router.post('')
def create(body: GatewayRegistration, response: Response, p: P, db: DB):
    require(p, 'admin')
    # Stable per-workspace serialisation prevents concurrent duplicates.  The
    # exact key is namespaced and bounded before acquiring the PostgreSQL lock.
    lock = int.from_bytes(hashlib.sha256((p.workspace_id + ':' + _key(body.request_id)).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    submitted = {'name': body.name.strip(), 'api_origin': body.api_origin.rstrip('/'),
                 'rtsp_origin': body.rtsp_origin.rstrip('/')}
    submission_hash = digest(submitted)
    record = _receipt(db, p, body.request_id)
    if record:
        if not isinstance(record.data, dict) or record.data.get('submission_hash') != submission_hash:
            raise HTTPException(409, '同一登记请求的内容不同；原网关保留，请核对后使用新请求')
        response.status_code = 200
        return _current(db, p, record, body.request_id)
    # The runtime allowlist is always checked on first creation; no URL supplied
    # by a browser is ever contacted as part of this operation.
    submitted['api_origin'] = origin(submitted['api_origin'], 'api')
    submitted['rtsp_origin'] = origin(submitted['rtsp_origin'], 'rtsp')
    gateway = MediaGateway(workspace_id=p.workspace_id, data={**submitted, 'generation': 1,
                                                              'inventory': None, 'error': None})
    db.add(gateway)
    db.flush()
    db.add(Job(workspace_id=p.workspace_id, kind=KIND, status='internal',
               idempotency_key=_key(body.request_id), data={'request_id': body.request_id,
                   'gateway_id': gateway.id, 'submission_hash': submission_hash,
                   'actor_id': p.user_id}))
    audit(db, p, 'media.gateway.saved', gateway.id, {})
    db.commit()
    response.status_code = 201
    result = view(gateway)
    result['registration_matches_current'] = True
    return result
