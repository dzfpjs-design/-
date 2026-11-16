"""Explicit freeze, then compute: no inference, adoption or implicit retries."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .security import Principal
from .setup_api import P, DB
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import pixel_diagnostics_v257 as store

ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
router = APIRouter(prefix='/api/workspace/instances/{instance_id}/pixel-change/runs/{run_id}/annotations/{annotation_id}/diagnostics')


async def _raw(request):
    body = bytearray()
    async for block in request.stream():
        body.extend(block)
        if len(body) > store.MAX_RAW:
            raise HTTPException(413, '诊断申请超过大小上限；原稿保留')
    return bytes(body)


def _finish(db, p, instance_id, run_id, annotation_id, command):
    result = store.request_lookup(db, p, instance_id, run_id, annotation_id, command.request_id, command.raw_sha256)
    # Freeze must finish this transaction before any separate Evaluate request.
    # Rejections are durable original requests, never a suggestion to repost.
    db.commit()
    if result['outcome'] == 'rejected':
        raise HTTPException(409, detail=result)
    return result['receipt']


@router.get('/options')
def options(instance_id: str, run_id: str, annotation_id: str, p: ReadP, db: ReadDB):
    return store.options(db, p, instance_id, run_id, annotation_id)


@router.get('/requests')
def request_lookup(instance_id: str, run_id: str, annotation_id: str, p: ReadP, db: ReadDB,
                   request_id: str = Query(min_length=12, max_length=100),
                   raw_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.request_lookup(db, p, instance_id, run_id, annotation_id, request_id, raw_sha256)


@router.get('')
def listing(instance_id: str, run_id: str, annotation_id: str, p: ReadP, db: ReadDB,
            before_id: str | None = None, limit: int = Query(10, ge=10, le=20)):
    return store.listing(db, p, instance_id, run_id, annotation_id, before_id=before_id, limit=limit)


@router.post('', status_code=201)
async def freeze(instance_id: str, run_id: str, annotation_id: str, request: Request, p: P, db: DB):
    command = store.freeze(db, p, instance_id, run_id, annotation_id, await _raw(request))
    return _finish(db, p, instance_id, run_id, annotation_id, command)


@router.get('/{study_id}/evaluation')
def evaluation_lookup(instance_id: str, run_id: str, annotation_id: str, study_id: str, p: ReadP, db: ReadDB):
    return store.evaluation_lookup(db, p, instance_id, run_id, annotation_id, study_id)


@router.get('/{study_id}/evaluation/artifact')
def artifact(instance_id: str, run_id: str, annotation_id: str, study_id: str, p: ReadP, db: ReadDB):
    ev, raw = store.result_artifact(db, p, instance_id, run_id, annotation_id, study_id)
    return Response(raw, media_type='application/json', headers={
        'X-Content-SHA256': ev.sha256, 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store',
        'Content-Disposition': 'attachment; filename="pixel-diagnostic-result.json"'})


@router.post('/{study_id}/evaluate', status_code=201)
async def evaluate(instance_id: str, run_id: str, annotation_id: str, study_id: str,
                   request: Request, p: P, db: DB):
    command = store.evaluate(db, p, instance_id, run_id, annotation_id, study_id, await _raw(request))
    return _finish(db, p, instance_id, run_id, annotation_id, command)


@router.get('/{study_id}')
def detail(instance_id: str, run_id: str, annotation_id: str, study_id: str, p: ReadP, db: ReadDB):
    return store.study_view(store.get(db, p, instance_id, run_id, annotation_id, study_id))
