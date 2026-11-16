"""Human-approved coordination of saved A/B imagery, with exact-wire recovery."""
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import DB, P
from .saved_imagery_contracts_v163 import (
    CreateTask, Draft, ERROR, Reassess, Stop, Update, parse_wire,
)
from . import saved_imagery_store_v163 as store

router = APIRouter(prefix='/instances/{identity}/saved-imagery-reassessments')


async def _mutate(request, p, db, identity, operation, plan_id, model):
    raw = await request.body()
    if len(raw) > 16384:
        raise HTTPException(413, '本次补充处理设置过长，请保留输入并缩短说明')
    try:
        wire = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        raise HTTPException(422, '请求文字不是有效 UTF-8，请保留原输入') from None
    try:
        body = parse_wire(wire, model)
    except ValueError:
        raise HTTPException(422, ERROR) from None
    def save():
        value = store.mutate(db, p, identity, operation, plan_id, body, wire,
                             request.method, request.url.path)
        db.commit()
        return value
    return await run_in_threadpool(save)


@router.get('/options')
def options(identity: str, assessment_id: str, p: P, db: DB):
    return store.options(db, p, identity, assessment_id)


@router.get('/requests/{request_id}')
def original_request(identity: str, request_id: str, p: P, db: DB,
                     expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_result(db, p, identity, request_id, expected_sha256)


@router.get('/{plan_id}')
def detail(identity: str, plan_id: str, p: P, db: DB):
    return store.detail(db, p, identity, plan_id)


@router.post('', status_code=201)
async def create(identity: str, request: Request, p: P, db: DB):
    return await _mutate(request, p, db, identity, 'create', None, Draft)


@router.put('/{plan_id}')
async def update(identity: str, plan_id: str, request: Request, p: P, db: DB):
    return await _mutate(request, p, db, identity, 'update', plan_id, Update)


@router.post('/{plan_id}/create-task')
async def create_task(identity: str, plan_id: str, request: Request, p: P, db: DB):
    return await _mutate(request, p, db, identity, 'create_task', plan_id, CreateTask)


@router.post('/{plan_id}/reassess')
async def reassess(identity: str, plan_id: str, request: Request, p: P, db: DB):
    return await _mutate(request, p, db, identity, 'reassess', plan_id, Reassess)


@router.post('/{plan_id}/stop')
async def stop(identity: str, plan_id: str, request: Request, p: P, db: DB):
    return await _mutate(request, p, db, identity, 'stop', plan_id, Stop)
