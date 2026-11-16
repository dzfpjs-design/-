"""Authenticated real reference generation; reads never process native jobs."""
from uuid import UUID
from fastapi import APIRouter, Request, Query
from starlette.concurrency import run_in_threadpool
from .setup_api import P, DB
from .layered_case_api_v209 import raw_body, safe
from . import layered_case_uses_v210 as store

router = APIRouter(prefix=store.PREFIX)


@router.get('/tasks/{identity}/context')
def context(identity: UUID, p: P, db: DB): return safe(store.context, db, p, str(identity))


@router.get('/tasks/{identity}/uses')
def task_uses(identity: UUID, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    return safe(lambda: store.list_uses(db, p, task_id=str(identity), offset=offset))


@router.get('/tasks/{identity}/approved-cases')
def approved(identity: UUID, p: P, db: DB, q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=100000)):
    return safe(store.approved_cases, db, p, str(identity), q, offset)


@router.get('/tasks/{identity}/approved-cases/{case_id}')
def reference(identity: UUID, case_id: UUID, p: P, db: DB):
    return safe(store.reference_case, db, p, str(identity), str(case_id))


@router.post('/tasks/{identity}/preview')
async def preview(identity: UUID, request: Request, p: P, db: DB):
    raw = await raw_body(request)
    body, _, _ = store.parse(raw, 'preview')
    return await run_in_threadpool(safe, lambda: store.selection(db, p, str(identity), body)[0])


@router.post('/tasks/{identity}/uses', status_code=201)
async def create(identity: UUID, request: Request, p: P, db: DB):
    raw = await raw_body(request)
    def apply():
        result = safe(store.create, db, p, str(identity), raw); db.commit(); return result
    return await run_in_threadpool(apply)


@router.get('/uses/{identity}')
def detail(identity: UUID, p: P, db: DB): return safe(store.get_use, db, p, str(identity))


@router.get('/uses/{identity}/consumption')
def consumption(identity: UUID, p: P, db: DB): return safe(store.get_consumption, db, p, str(identity))


@router.post('/uses/{identity}/withdraw')
async def withdraw(identity: UUID, request: Request, p: P, db: DB):
    raw = await raw_body(request)
    def apply():
        result = safe(store.withdraw, db, p, str(identity), raw); db.commit(); return result
    return await run_in_threadpool(apply)


@router.post('/uses/{identity}/feedback')
async def feedback(identity: UUID, request: Request, p: P, db: DB):
    raw = await raw_body(request)
    def apply():
        result = safe(store.feedback, db, p, str(identity), raw); db.commit(); return result
    return await run_in_threadpool(apply)


@router.get('/requests/{identity}')
def recover(identity: UUID, expected_sha256: store.Hash, p: P, db: DB):
    return safe(store.recover, db, p, str(identity), expected_sha256)


@router.get('/cases/{identity}/uses')
def case_uses(identity: UUID, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    return safe(lambda: store.list_uses(db, p, case_id=str(identity), offset=offset))


@router.get('/cases/{identity}/feedback')
def case_feedback(identity: UUID, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    return safe(store.list_feedback, db, p, str(identity), offset)
