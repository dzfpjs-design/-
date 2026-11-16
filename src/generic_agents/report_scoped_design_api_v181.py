"""Normal closed-wire owner plan API; contains no Attempt or provider action."""
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from . import report_scoped_design_store_v181 as store

# setup_api.router already carries /api/setup. Root registers this relative
# router before exporting its composite; command paths always use full PREFIX.
router = APIRouter(prefix='/report-scoped-designs/v181')


def _identity(value):
    try: return store.identity(value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '请保留原小写UUID编号，不新造或猜修原编号') from None


@router.get('/proposal')
def proposal(p: P, db: DB, producer_id: str, revision: int = Query(ge=1)):
    return store.proposal(db, p, _identity(producer_id), revision)


@router.get('/plans')
def list_plans(p: P, db: DB, producer_id: str):
    return store.list_plans(db, p, _identity(producer_id))


@router.get('/plans/{plan_id}')
def detail(plan_id: str, p: P, db: DB):
    return store.detail(db, p, _identity(plan_id))


@router.get('/plans/{plan_id}/inputs')
def inputs(plan_id: str, request: Request, p: P, db: DB,
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), query: str | None = None):
    # Query parameters are text. Do not let integer coercion silently accept
    # floats, booleans or ambiguous repeated pagination parameters.
    for name, value in (('offset', offset), ('limit', limit)):
        supplied = request.query_params.getlist(name)
        if supplied and supplied != [str(value)]:
            raise HTTPException(422, '分页参数必须是单个十进制整数，不接受浮点、布尔或重复参数')
    supplied = request.query_params.getlist('query')
    if supplied and supplied != [query]:
        raise HTTPException(422, '搜索词只能提供一次，不能使用重复参数')
    search = {} if query is None else {'query': query}
    return store.plan_inputs(db, p, _identity(plan_id), offset=offset, limit=limit, **search)


@router.get('/requests/{request_id}')
def original(request_id: str, p: P, db: DB,
             expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_result(db, p, _identity(request_id), expected_sha256)


async def _write(plan_id, operation, request, p, db):
    raw = await request.body()
    if len(raw) > store.MAX_WIRE_BYTES:
        raise HTTPException(413, '范围计划超过保存上限；原稿未裁剪，请保留原输入')
    try:
        store.parse_wire(raw, operation)
        if plan_id is not None: store.identity(plan_id)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(422, '范围计划的字段、严格版本或原始JSON无法核对；原输入保留') from None
    def commit():
        try:
            result = store.mutate(db, p, plan_id, operation, raw,
                actual_method=request.method, actual_path=request.url.path)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
    return await run_in_threadpool(commit)


@router.post('/plans', status_code=201)
async def create(request: Request, p: P, db: DB):
    return await _write(None, 'create', request, p, db)


@router.post('/plans/{plan_id}/save')
async def save(plan_id: str, request: Request, p: P, db: DB):
    return await _write(plan_id, 'save', request, p, db)


@router.post('/plans/{plan_id}/confirm')
async def confirm(plan_id: str, request: Request, p: P, db: DB):
    return await _write(plan_id, 'confirm', request, p, db)
