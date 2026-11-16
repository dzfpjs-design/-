"""Authenticated private draft endpoints; all synchronous SQL is offloaded."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import business_flow_nested_configuration_store as store
from .db import session
from .security import Principal, authenticated


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


@router.get('/qualifications/{qualification_id}/nested-draft')
async def get_route(qualification_id: str, request: Request, p: P, db: DB):
    store.need(not request.query_params, status=422)
    return await run_in_threadpool(store.get, db, p, qualification_id)


@router.post('/nested-drafts/save')
async def save_route(request: Request, p: P, db: DB):
    chunks = []; size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > store.MAX_BYTES:
            raise HTTPException(413, '递归执行草稿超过2MiB原请求容量；原输入保留，未截断保存')
        chunks.append(chunk)
    return await run_in_threadpool(store.save, db, p, b''.join(chunks))


@router.get('/nested-draft-requests/{request_id}')
async def recover_route(request_id: str, request: Request, p: P, db: DB):
    store.need(set(request.query_params) == {'expected_sha256'}
        and len(request.query_params.getlist('expected_sha256')) == 1, status=422)
    return await run_in_threadpool(store.recover, db, p, request_id, request.query_params['expected_sha256'])
