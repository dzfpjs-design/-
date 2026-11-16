"""Explicit once-only conflict observation; history GETs never start work."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from .security import Principal
from .setup_api import P, DB
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import goal_conflict_observation_service_v259 as store

ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
router = APIRouter(prefix='/api/workspace/tasks/{task_id}/goal/conflict-observations')


@router.get('/context')
def context(task_id: str, gap_id: str, p: ReadP, db: ReadDB):
    return store.context(db,p,task_id,gap_id)


@router.get('/requests')
def recovery(task_id: str, p: ReadP, db: ReadDB,
             request_id: str = Query(min_length=12,max_length=100),
             raw_sha256: str = Query(pattern=r'^[0-9a-f]{64}$')):
    return store.recovery(db,p,task_id,request_id,raw_sha256)


@router.post('',status_code=201)
async def submit(task_id: str, request: Request, p: P, db: DB):
    raw=bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw)>store.MAX_RAW:raise HTTPException(413,'单次复测申请超过原文大小上限')
    return await run_in_threadpool(_submit_tx,db,p,task_id,bytes(raw))


def _submit_tx(db,p,task_id,raw):
    try:
        row=store.submit(db,p,task_id,raw)
        result=store.recovery(db,p,task_id,row.request_id,row.raw_sha256)
        db.commit()
    except Exception:
        db.rollback()
        raise
    # A durable refusal is returned only after its transaction committed.
    if result['outcome']=='rejected':raise HTTPException(409,detail=result)
    return result['receipt']


@router.get('/history')
def history(task_id: str, p: ReadP, db: ReadDB,
            before_id: str | None = None, limit: int = Query(default=20,ge=1,le=50)):
    return store.history(db,p,task_id,before_id=before_id,limit=limit)


@router.get('/{command_id}')
def detail(task_id: str, command_id: str, p: ReadP, db: ReadDB):
    return store.detail(db,p,task_id,command_id)
