"""Original-wire endpoints for explicitly adopting an independently confirmed design."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from . import report_object_adoption_v169 as store

router = APIRouter(prefix='/report-evidence-readings/{report_id}/object-adoption-v169')


@router.get('')
def source(report_id: str, p: P, db: DB):
    return store.source_view(db, p, report_id)


@router.post('/preview')
def preview(report_id: str, body: store.PreviewCommand, p: P, db: DB):
    store.require(p, 'admin', 'operator')
    store.full.old().nonce_lock(db, p, body.request_id)
    if store.full.old().request_row(db, p, body.request_id) is not None:
        store.fail('object_adoption_existing_nonce', '此编号已有原采用请求，请先核对原回执')
    _, value, _ = store.prepare(db, p, report_id, body)
    return value


@router.post('/commit')
async def commit(report_id: str, request: Request, p: P, db: DB):
    raw = await request.body()
    if len(raw) > store.MAX_WIRE:
        raise HTTPException(413, '采用请求超过16KiB上限；原稿与配置保留')
    return await run_in_threadpool(store.commit, db, p, report_id, raw)


@router.get('/requests/{request_id}')
def original(report_id: str, request_id: UUID, p: P, db: DB,
             expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_result(db, p, report_id, request_id, expected_sha256)
