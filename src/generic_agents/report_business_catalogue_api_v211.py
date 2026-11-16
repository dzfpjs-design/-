"""Normal current-session API for complete, recoverable human catalogue work."""
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from . import report_business_catalogue_store_v211 as store

# Registered on setup_api's final unprefixed composite, after setup_routes was
# exported. Keep the actual route identical to the original command path.
router = APIRouter(prefix=store.PREFIX)


def identity(value):
    try:
        return store.identity(value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '请保留原小写UUID编号，不新造或猜修原编号') from None


def safe(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, store.ERROR) from None


def pagination(request, offset, limit):
    for name, value in (('offset', offset), ('limit', limit)):
        values = request.query_params.getlist(name)
        if values and values != [str(value)]:
            raise HTTPException(422, '分页参数须为单个十进制整数，不能传布尔、浮点或重复值')


@router.get('/drafts')
def list_drafts(request: Request, p: P, db: DB, consumer_id: str,
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    pagination(request, offset, limit)
    return safe(store.list_drafts, db, p, identity(consumer_id), offset=offset, limit=limit)


@router.get('/drafts/{draft_id}')
def detail(draft_id: str, p: P, db: DB):
    return safe(store.detail, db, p, identity(draft_id))


@router.get('/drafts/{draft_id}/history/{version}')
def history(draft_id: str, version: str, p: P, db: DB):
    if (not 1 <= len(version) <= 3 or any(char not in '0123456789' for char in version)
            or version != str(int(version))):
        raise HTTPException(422, '历史版本须为单个十进制严格整数，不接受浮点、布尔或前导零')
    return safe(store.history_version, db, p, identity(draft_id), int(version))


@router.get('/drafts/{draft_id}/inputs')
def inputs(draft_id: str, request: Request, p: P, db: DB,
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    pagination(request, offset, limit)
    return safe(store.inputs, db, p, identity(draft_id), offset=offset, limit=limit)


@router.get('/drafts/{draft_id}/business-flow-source')
def business_flow_source(draft_id: str, p: P, db: DB,
                         scope_id: str = Query(min_length=1, max_length=128)):
    return safe(store.business_flow_source, db, p, identity(draft_id), scope_id)


@router.get('/requests/{request_id}')
def recover(request_id: str, p: P, db: DB,
            expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return safe(store.request_result, db, p, identity(request_id), expected_sha256)


async def write(draft_id, operation, request, p, db):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > store.MAX_WIRE_BYTES:
            raise HTTPException(413, '完整人工目录超过当前保存容量，原输入未裁剪，请保留本机稿')
        chunks.append(chunk)
    raw = b''.join(chunks)
    try:
        store.parse_wire(raw, operation)
        if draft_id is not None: identity(draft_id)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '目录原请求、严格版本或完整字段无法核对；原输入保留') from None

    def commit():
        try:
            result = safe(store.mutate, db, p, draft_id, operation, raw,
                actual_method=request.method, actual_path=request.url.path)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
    return await run_in_threadpool(commit)


@router.post('/drafts', status_code=201)
async def create(request: Request, p: P, db: DB):
    return await write(None, 'create', request, p, db)


@router.post('/drafts/{draft_id}/save')
async def save(draft_id: str, request: Request, p: P, db: DB):
    return await write(draft_id, 'save', request, p, db)


@router.post('/drafts/{draft_id}/confirm')
async def confirm(draft_id: str, request: Request, p: P, db: DB):
    return await write(draft_id, 'confirm', request, p, db)
