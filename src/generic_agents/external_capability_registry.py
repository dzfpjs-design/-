"""Workspace-scoped recovery for external capability registration.

The immutable namespace/name/version identity is backed by the existing unique
constraint.  Repeating the same manifest returns the same record; a different
manifest for that identity is an explicit conflict.  No schema migration or
change to the legacy registration endpoint is required.
"""
import hashlib
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .contracts import Manifest, digest
from .models import Capability, Job
from .services import as_dict, register
from .setup_api import DB, P

router = APIRouter(prefix='/api/external-capability-registrations')

# A tombstone and a successful operation use the same lock and receipt. This
# is essential: a 404 alone cannot prove that an earlier request is not still
# on its way to the application.
class RegistrationAttempt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    manifest: Manifest

def _attempt_key(request_id):
    return 'external-capability-attempt:' + request_id

def _attempt_lock(db, p, request_id):
    value = int.from_bytes(hashlib.sha256((p.workspace_id + ':' + _attempt_key(request_id)).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': value})

def _attempt(db, p, request_id):
    return db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == _attempt_key(request_id)))

def _attempt_view(db, p, receipt, request_id):
    data = receipt.data if isinstance(receipt.data, dict) else {}
    if receipt.kind != 'external_registration_attempt' or receipt.status != 'internal' or data.get('request_id') != request_id or data.get('actor_id') != p.user_id:
        raise HTTPException(409, '原请求回执无法核对，请联系管理员')
    if data.get('state') == 'cancelled':
        return {'request_id': request_id, 'state': 'cancelled'}
    if data.get('state') != 'created' or not isinstance(data.get('capability_id'), str):
        raise HTTPException(409, '原请求回执无法核对，请联系管理员')
    row = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.id == data['capability_id']))
    if not row or row.content_hash != data.get('content_hash'):
        raise HTTPException(409, '原登记记录无法核对，请联系管理员')
    return {'request_id': request_id, 'state': 'created', 'record': as_dict(row)}

@router.post('/attempts')
def submit_attempt(body: RegistrationAttempt, p: P, db: DB):
    from .security import require
    require(p, 'admin', 'operator')
    manifest = body.manifest
    if manifest.kind not in {'tool', 'specialist_model'} or manifest.execution.get('adapter') not in {'http', 'mcp'}:
        raise HTTPException(422, '此入口只登记 HTTP JSON 或 MCP 专业模型与工具')
    _attempt_lock(db, p, body.request_id)
    receipt = _attempt(db, p, body.request_id)
    if receipt:
        view = _attempt_view(db, p, receipt, body.request_id)
        if view['state'] == 'created' and receipt.data.get('content_hash') != digest(manifest.model_dump(mode='json')):
            raise HTTPException(409, '同一登记请求的内容不同，请核对原记录')
        return view
    old = _row(db, p, manifest.namespace, manifest.name, manifest.version)
    if old:
        row = _same(old, manifest)
    else:
        try:
            with db.begin_nested():
                row = register(db, p, manifest)
        except IntegrityError:
            old = _row(db, p, manifest.namespace, manifest.name, manifest.version)
            if not old:
                raise HTTPException(409, '能力登记发生冲突，请稍后核对') from None
            row = _same(old, manifest)
    db.add(Job(workspace_id=p.workspace_id, kind='external_registration_attempt', status='internal',
               idempotency_key=_attempt_key(body.request_id), data={'request_id': body.request_id,
               'actor_id': p.user_id, 'state': 'created', 'capability_id': row.id, 'content_hash': row.content_hash}))
    db.commit()
    return {'request_id': body.request_id, 'state': 'created', 'record': as_dict(row)}

@router.post('/attempts/{request_id}/cancel')
def cancel_attempt(request_id: str, p: P, db: DB):
    from .security import require
    require(p, 'admin', 'operator')
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', request_id):
        raise HTTPException(422, '登记请求标识无效')
    _attempt_lock(db, p, request_id)
    receipt = _attempt(db, p, request_id)
    if receipt:
        return _attempt_view(db, p, receipt, request_id)
    db.add(Job(workspace_id=p.workspace_id, kind='external_registration_attempt', status='internal',
               idempotency_key=_attempt_key(request_id), data={'request_id': request_id,
               'actor_id': p.user_id, 'state': 'cancelled'}))
    db.commit()
    return {'request_id': request_id, 'state': 'cancelled'}


def _row(db, p, namespace, name, version):
    return db.scalar(select(Capability).where(
        Capability.workspace_id == p.workspace_id,
        Capability.namespace == namespace,
        Capability.name == name,
        Capability.release_version == version,
    ))


def _same(row, body):
    if row.kind != body.kind or row.content_hash != digest(body.model_dump(mode='json')):
        raise HTTPException(409, '同一工作区的能力标识和版本已存在，内容不同；请核对原记录或使用新版本')
    return row


@router.get('/lookup')
def lookup(p: P, db: DB,
           namespace: str = Query(pattern=r'^[a-z][a-z0-9_]{0,63}$'),
           name: str = Query(pattern=r'^[a-z][a-z0-9_]{0,63}$'),
           version: str = Query(pattern=r'^\d+\.\d+\.\d+$')):
    row = _row(db, p, namespace, name, version)
    if not row:
        raise HTTPException(404, '当前工作区没有这个外部能力版本')
    data = row.data if isinstance(row.data, dict) else {}
    execution = data.get('execution') if isinstance(data.get('execution'), dict) else {}
    if row.kind not in {'tool', 'specialist_model'} or execution.get('adapter') not in {'http', 'mcp'}:
        raise HTTPException(409, '此标识和版本已由其他能力占用，请保留原记录并使用新版本')
    return as_dict(row)


@router.post('')
def create(body: Manifest, response: Response, p: P, db: DB):
    if body.kind not in {'tool', 'specialist_model'} or body.execution.get('adapter') not in {'http', 'mcp'}:
        raise HTTPException(422, '此入口只登记 HTTP JSON 或 MCP 专业模型与工具')
    # The existing service enforces the operator/admin registration role.  Do
    # so even for retries, which otherwise could reveal an existing record.
    from .security import require
    require(p, 'admin', 'operator')
    old = _row(db, p, body.namespace, body.name, body.version)
    if old:
        response.status_code = 200
        return as_dict(_same(old, body))
    try:
        # A savepoint contains a uniqueness race without poisoning the outer
        # session.  After the competing transaction commits, re-read by the
        # exact identity rather than issuing another insert.
        with db.begin_nested():
            row = register(db, p, body)
        db.commit()
        response.status_code = 201
        return as_dict(row)
    except IntegrityError:
        old = _row(db, p, body.namespace, body.name, body.version)
        if not old:
            raise HTTPException(409, '能力登记发生冲突，请稍后按原标识核对') from None
        response.status_code = 200
        return as_dict(_same(old, body))
