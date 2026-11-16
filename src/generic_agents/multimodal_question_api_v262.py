"""Dedicated typed routes; GET is RR/RO and never dispatches, installs or repairs."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from .security import Principal
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import multimodal_question_contracts_v262 as c
from . import multimodal_question_service_v262 as service

router = APIRouter(prefix='/instances/{instance_id}/multimodal-questions')
ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[Session, Depends(read_session)]


async def _body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        service.need(len(raw) <= c.MAX_RAW, '请求正文超过128KiB', 422)
    return bytes(raw)


def _write(db, function, *args):
    try: return function(db, *args)
    except Exception:
        db.rollback(); raise


@router.get('/options')
def options(instance_id: str, p: ReadP, db: ReadDB, task_id: str | None = None, entity_id: str | None = None):
    return service.options(db, p, instance_id, task_id, entity_id)


@router.post('/preview')
async def preview(instance_id: str, request: Request, p: P, db: DB):
    raw = await _body(request); body = service._wire(raw, c.PreviewInput)
    return await run_in_threadpool(_write, db, service.preview, p, instance_id, body)


@router.post('', status_code=201)
async def submit(instance_id: str, request: Request, p: P, db: DB):
    raw = await _body(request); body = c.Submit.model_validate(service._wire(raw, c.Submit))
    return await run_in_threadpool(_write, db, service.create, p, instance_id, body, raw)


@router.get('/requests')
def recover(instance_id: str, p: ReadP, db: ReadDB,
        request_id: Annotated[str, Query(min_length=12, max_length=100)],
        expected_body_hash: Annotated[str, Query(pattern=c.HASH)]):
    return service.lookup(db, p, instance_id, request_id, expected_body_hash)


@router.get('')
def history(instance_id: str, p: ReadP, db: ReadDB,
        limit: Annotated[int, Query(ge=1, le=20)] = 20, before_id: str | None = None):
    return service.history(db, p, instance_id, limit, before_id)


@router.get('/{query_id}')
def get(instance_id: str, query_id: str, p: ReadP, db: ReadDB):
    return service.get(db, p, instance_id, query_id)


@router.post('/{query_id}/{operation}')
async def command(instance_id: str, query_id: str, operation: str, request: Request, p: P, db: DB):
    service.need(operation in {'cancel', 'retry'}, '未支持的原问题操作', 404)
    raw = await _body(request); body = c.Command.model_validate(service._wire(raw, c.Command))
    return await run_in_threadpool(_write, db, service.command, p, instance_id, query_id, operation, body, raw)


@router.get('/{query_id}/commands/requests')
def recover_command(instance_id: str, query_id: str, p: ReadP, db: ReadDB,
        request_id: Annotated[str, Query(min_length=12, max_length=100)],
        expected_body_hash: Annotated[str, Query(pattern=c.HASH)]):
    return service.command_lookup(db, p, instance_id, query_id, request_id, expected_body_hash)
