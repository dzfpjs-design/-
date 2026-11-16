"""Closed, exact-wire endpoints for a new private human semantic revision."""
import hashlib
import json
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from .typed_payload_codec_v131 import canonical_json
from . import report_object_revision_store_v166 as store

router = APIRouter(prefix='/report-evidence-readings/{report_id}/object-revisions-v166')
MAX_WIRE_BYTES = 1024 * 1024
ERROR = '新结构稿请求字段或原始文字无法核对；原输入与原请求保留'
_NONCE = re.compile(r'^[A-Za-z0-9_-]{12,100}$')
_SHA = re.compile(r'^[a-f0-9]{64}$')


def identity(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError(ERROR)
    return value


def command_path(report_id, operation):
    identity(report_id)
    if operation not in {'save', 'confirm'}:
        raise ValueError(ERROR)
    return '/api/workspace/report-evidence-readings/' + report_id + '/object-revisions-v166/' + operation


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(ERROR)
        result[key] = value
    return result


def parse_wire(raw):
    """Return the original four fields, without filling defaults or coercion."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES:
            raise ValueError(ERROR)
        def constant(_):
            raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
            parse_constant=constant)
        canonical_json(body)
        if (type(body) is not dict or set(body) != {'request_id', 'expected_version', 'basis_hash', 'value'}
                or type(body['request_id']) is not str or _NONCE.fullmatch(body['request_id']) is None
                or type(body['expected_version']) is not int or body['expected_version'] < 0
                or type(body['basis_hash']) is not str or _SHA.fullmatch(body['basis_hash']) is None
                or type(body['value']) is not dict):
            raise ValueError(ERROR)
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def wire_sha256(raw):
    if type(raw) is not bytes:
        raise ValueError(ERROR)
    raw.decode('utf-8', errors='strict')
    return hashlib.sha256(raw).hexdigest()


@router.get('')
def detail(report_id: str, p: P, db: DB):
    return store.detail(db, p, report_id)


@router.get('/requests/{request_id}')
def original(report_id: str, request_id: str, p: P, db: DB,
             expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_result(db, p, report_id, request_id, expected_sha256)


async def _write(report_id, operation, request, p, db):
    raw = await request.body()
    if len(raw) > MAX_WIRE_BYTES:
        raise HTTPException(413, '新结构稿超过保存上限；原稿保留，请缩减后明确保存')
    try:
        body = parse_wire(raw)
        identity(report_id)
    except ValueError:
        raise HTTPException(422, ERROR) from None
    def commit():
        result = store.mutate(db, p, report_id, operation, body, raw,
            request.method, request.url.path)
        db.commit()
        return result
    return await run_in_threadpool(commit)


@router.post('/save')
async def save(report_id: str, request: Request, p: P, db: DB):
    return await _write(report_id, 'save', request, p, db)


@router.post('/confirm')
async def confirm(report_id: str, request: Request, p: P, db: DB):
    return await _write(report_id, 'confirm', request, p, db)
