"""Normal authenticated, bounded, thread-pooled finite Daily Task intents."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import Principal, authenticated
from . import media_daily_task_adapter as service

router = APIRouter(prefix='/api/workspace/instances/{instance_id}/daily-task-intents')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


async def raw_body(request):
    result = bytearray()
    async for part in request.stream():
        if len(result)+len(part) > service.MAX_BODY:
            raise HTTPException(413, '请求超过64KB，未核验或执行')
        result.extend(part)
    return bytes(result)


def transaction(db, function, *args, **kwargs):
    try: return function(db, *args, **kwargs)
    except BaseException:
        db.rollback()
        raise


@router.get('/options')
def options(instance_id:UUID, p:P, db:DB):
    return service.options(db, p, str(instance_id))


@router.post('/preview')
async def preview(instance_id:UUID, request:Request, p:P, db:DB):
    raw = await raw_body(request)
    def call():
        return transaction(db, service.preview, p, str(instance_id), service.parse(raw, service.Preview))
    return await run_in_threadpool(call)


@router.post('/enable', status_code=201)
async def enable(instance_id:UUID, request:Request, p:P, db:DB):
    raw = await raw_body(request)
    return await run_in_threadpool(transaction, db, service.command, p, str(instance_id), raw)


@router.get('')
def listing(instance_id:UUID, p:P, db:DB):
    return service.listing(db, p, str(instance_id))


@router.get('/requests/{request_id}')
def request_result(instance_id:UUID, request_id:UUID, request:Request, p:P, db:DB):
    query = request.query_params
    service.need(set(query) == {'expected_sha256'} and len(query.getlist('expected_sha256')) == 1,
        '请携带唯一原请求摘要', 422)
    return service.recover(db, p, str(instance_id), str(request_id), query['expected_sha256'])


@router.post('/{intent_id}/pause')
async def pause(instance_id:UUID, intent_id:UUID, request:Request, p:P, db:DB):
    raw = await raw_body(request)
    return await run_in_threadpool(transaction, db, service.command, p, str(instance_id), raw, intent_id=str(intent_id))
