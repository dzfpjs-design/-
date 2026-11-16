"""Thin authenticated routes for independent native-video human opinions."""
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from .native_video_review_contracts_v165 import Draft, Submit, ERROR, parse_wire
from . import native_video_review_store_v165 as store

router = APIRouter(prefix='/instances/{identity}/native-video-reviews')


@router.get('/requests/{request_id}')
def original(identity: str, request_id: str, p: P, db: DB,
             expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_result(db, p, identity, request_id, expected_sha256)


@router.get('/observations/{observation_id}')
def detail(identity: str, observation_id: str, p: P, db: DB):
    return store.detail(db, p, identity, observation_id)


async def _save(identity, observation_id, operation, request, p, db):
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(413, '核查说明过长，请保留原稿并缩短说明')
    try:
        body = parse_wire(raw, Draft if operation == 'draft' else Submit)
    except ValueError:
        raise HTTPException(422, ERROR) from None
    def commit():
        value = store.mutate(db, p, identity, observation_id, operation, body,
            raw, request.method, request.url.path)
        db.commit()
        return value
    return await run_in_threadpool(commit)


@router.post('/observations/{observation_id}/draft')
async def draft(identity: str, observation_id: str, request: Request, p: P, db: DB):
    return await _save(identity, observation_id, 'draft', request, p, db)


@router.post('/observations/{observation_id}/submit')
async def submit(identity: str, observation_id: str, request: Request, p: P, db: DB):
    return await _save(identity, observation_id, 'submit', request, p, db)
