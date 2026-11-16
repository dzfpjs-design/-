"""Normal authenticated case editing/review, no learning/model side effects."""
from uuid import UUID
from fastapi import APIRouter, Request, Query, HTTPException
from starlette.concurrency import run_in_threadpool
from .setup_api import P, DB
from . import layered_cases_v209 as store

router = APIRouter(prefix=store.PREFIX)


async def raw_body(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 65536: raise HTTPException(413, '案例请求超过64KiB；原输入保留')
        chunks.append(chunk)
    return b''.join(chunks)


def safe(call, *args):
    try: return call(*args)
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '案例原来源、不可变历史或独立证明无法核对；原记录保留且未重建') from None


@router.get('/tasks/{identity}/context')
def context(identity: UUID, p: P, db: DB): return safe(store.context, db, p, str(identity))


@router.post('/tasks/{identity}/cases', status_code=201)
async def create(identity: UUID, request: Request, p: P, db: DB):
    raw = await raw_body(request)
    def apply():
        result = safe(store.create, db, p, str(identity), raw); db.commit(); return result
    return await run_in_threadpool(apply)


@router.get('/cases/{identity}')
def detail(identity: UUID, p: P, db: DB): return safe(store.get_case, db, p, str(identity))


@router.get('/cases/{identity}/revisions')
def revisions(identity: UUID, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    return safe(store.revisions, db, p, str(identity), offset)


async def change(identity, request, p, db, operation):
    raw = await raw_body(request)
    def apply():
        result = safe(store.perform, db, p, str(identity), operation, raw); db.commit(); return result
    return await run_in_threadpool(apply)


@router.post('/cases/{identity}/revise')
async def revise(identity: UUID, request: Request, p: P, db: DB): return await change(identity, request, p, db, 'revise')


@router.post('/cases/{identity}/submit')
async def submit(identity: UUID, request: Request, p: P, db: DB): return await change(identity, request, p, db, 'submit')


@router.post('/cases/{identity}/withdraw')
async def withdraw(identity: UUID, request: Request, p: P, db: DB): return await change(identity, request, p, db, 'withdraw')


@router.post('/cases/{identity}/review')
async def review(identity: UUID, request: Request, p: P, db: DB): return await change(identity, request, p, db, 'review')


@router.get('/requests/{identity}')
def recover(identity: UUID, expected_sha256: store.Hash, p: P, db: DB):
    return safe(store.recover, db, p, str(identity), expected_sha256)


@router.get('/search')
def search(p: P, db: DB, q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=100000)):
    return safe(store.search, db, p, q, offset)
