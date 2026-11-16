"""Continuous rules are explicit commands; reads never tick or dispatch."""
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from starlette.concurrency import run_in_threadpool
from .security import authenticated, Principal
from .db import session
from . import business_automation_service as service
from .business_automation_contracts import MAX_BODY, need, uuid, parse
from .report_text_parse_scope import report_text_parse_scope

router = APIRouter(prefix='/api/v2/instances/{instance_id}/business-automations')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def identity(value):
    try: return uuid(value)
    except (ValueError, TypeError): raise HTTPException(422, '编号格式无效') from None


async def read_body(request):
    parts = []; size = 0
    async for part in request.stream():
        size += len(part); need(size <= MAX_BODY, '请求超过24KiB，未执行', 413); parts.append(part)
    return b''.join(parts)


def transaction(db, p, instance_id, raw, operation, subscription_id=None):
    try:
        with report_text_parse_scope():
            result = service.command(db, p, instance_id, raw, operation, subscription_id)
            db.commit(); return result
    except BaseException: db.rollback(); raise


def preview_operation(db, p, instance_id, body):
    # Reuse only pure parsing of identical bytes in this synchronous operation.
    # IAM, DB/source freshness, physical reads and admission still run normally.
    with report_text_parse_scope():
        return service.preview(db,p,instance_id,body)


@router.get('/options')
def options(instance_id: str, release_id: str, business_id: str, p: P, db: DB):
    with report_text_parse_scope():
        return service.options(db,p,identity(instance_id),identity(release_id),business_id)


@router.post('/preview')
async def preview(instance_id: str, request: Request, p: P, db: DB):
    raw = await read_body(request)
    body = parse(raw,'preview')
    return await run_in_threadpool(preview_operation,db,p,identity(instance_id),body)


@router.get('/requests/{request_id}')
def recover(instance_id: str, request_id: str, expected_sha256: str, p: P, db: DB):
    try: return service.recover(db,p,identity(instance_id),identity(request_id),expected_sha256)
    except ValueError: raise HTTPException(422, '原请求摘要格式无效') from None


@router.post('')
async def enable(instance_id: str, request: Request, response: Response, p: P, db: DB):
    raw = await read_body(request); parse(raw,'enable')
    result, created = await run_in_threadpool(transaction,db,p,identity(instance_id),raw,'enable')
    response.status_code = 201 if created else 200
    return result


@router.get('')
def history(instance_id: str, p: P, db: DB, cursor: str | None = None):
    return service.history(db,p,identity(instance_id),cursor)


@router.get('/{subscription_id}')
def get(instance_id: str, subscription_id: str, p: P, db: DB):
    return service.view(db,p,service.get(db,p,identity(instance_id),identity(subscription_id)))


@router.get('/{subscription_id}/occurrences')
def occurrences(instance_id: str, subscription_id: str, p: P, db: DB, cursor: str | None = None):
    row = service.get(db,p,identity(instance_id),identity(subscription_id))
    return service.occurrences(db,p,row,cursor)


@router.post('/{subscription_id}/pause')
async def pause(instance_id: str, subscription_id: str, request: Request, p: P, db: DB):
    raw = await read_body(request); parse(raw,'pause')
    result, _ = await run_in_threadpool(transaction,db,p,identity(instance_id),raw,'pause',identity(subscription_id))
    return result


@router.post('/{subscription_id}/resume')
async def resume(instance_id: str, subscription_id: str, request: Request, response: Response, p: P, db: DB):
    raw = await read_body(request); parse(raw,'resume')
    result, created = await run_in_threadpool(transaction,db,p,identity(instance_id),raw,'resume',identity(subscription_id))
    response.status_code = 201 if created else 200
    return result
