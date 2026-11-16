"""Personal human work orders. No endpoint invokes a capability or approves a Goal."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import workflow_task_dispatch_v198 as store
from .db import session
from .security import Principal, authenticated


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


@router.get('/work-orders-v198')
def list_route(request: Request, p: P, db: DB):
    allowed = {'mode', 'parent_task_id'}
    store.need(set(request.query_params) <= allowed
        and all(len(request.query_params.getlist(key)) == 1 for key in request.query_params), status=422)
    return store.list_orders(db, p, mode=request.query_params.get('mode', 'assigned'),
        parent_task_id=request.query_params.get('parent_task_id'))


@router.get('/work-orders-v198/requests/{request_id}')
def request_route(request_id: str, request: Request, p: P, db: DB):
    store.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, status=422)
    return store.recover(db, p, request_id, request.query_params['expected_sha256'])


@router.get('/work-orders-v198/{item_id}')
def detail_route(item_id: str, request: Request, p: P, db: DB):
    store.need(not request.query_params, status=422)
    return store.get_order(db, p, item_id)


async def _command(item_id, operation, request, p, db):
    store.need(not request.query_params, status=422)
    chunks = []; size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > store.MAX_COMMAND_BYTES:
            raise HTTPException(413, '工作单原请求超过128KiB；未截断或保存不完整说明')
        chunks.append(chunk)
    raw = b''.join(chunks)
    def apply():
        result = store.perform(db, p, item_id, operation, raw, method=request.method, path=request.url.path)
        db.commit()
        return result
    return await run_in_threadpool(apply)


@router.post('/work-orders-v198/{item_id}/progress')
async def progress_route(item_id: str, request: Request, p: P, db: DB):
    return await _command(item_id, 'progress', request, p, db)


@router.post('/work-orders-v198/{item_id}/submit')
async def submit_route(item_id: str, request: Request, p: P, db: DB):
    return await _command(item_id, 'submit', request, p, db)


@router.post('/work-orders-v198/{item_id}/return')
async def return_route(item_id: str, request: Request, p: P, db: DB):
    return await _command(item_id, 'return', request, p, db)


@router.post('/work-orders-v198/{item_id}/cancel')
async def cancel_route(item_id: str, request: Request, p: P, db: DB):
    return await _command(item_id, 'cancel', request, p, db)
