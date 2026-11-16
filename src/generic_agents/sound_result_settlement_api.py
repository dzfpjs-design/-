"""Explicit, original-request sound handoff recovery; GET never executes."""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from .sound_api import P, DB
from . import sound_result_settlement as store

router = APIRouter()


@router.get('/api/sounds/runs/{identity}/settlement')
def current(identity: str, p: P, db: DB):
    return store.status(db, p, identity)


@router.post('/api/sounds/runs/{identity}/settlements')
async def request(identity: str, raw: Request, p: P, db: DB):
    result, created = store.request(db, p, identity, await raw.body()); db.commit()
    return JSONResponse(result, status_code=201 if created else 200)


@router.get('/api/sounds/runs/{identity}/settlements/requests')
def recover(identity: str, p: P, db: DB, request_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{12,100}$'),
        raw_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'), body_hash: str = Query(pattern=r'^[0-9a-f]{64}$'),
        expected_task_version: int = Query(ge=1), expected_run_version: int = Query(ge=1)):
    return store.recover(db, p, identity, request_id, raw_sha256, body_hash, expected_task_version, expected_run_version)
