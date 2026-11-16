"""Explicit, immutable human feedback beside the original pixel comparison."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .security import Principal
from .setup_api import P, DB
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import pixel_annotations_v256 as store

ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
router = APIRouter(prefix='/api/workspace/instances/{instance_id}/pixel-change/runs/{run_id}')


@router.get('/annotations/options')
def options(instance_id: str, run_id: str, p: ReadP, db: ReadDB):
    return store.options(db, p, instance_id, run_id)


@router.get('/annotations')
def listing(instance_id: str, run_id: str, p: ReadP, db: ReadDB, limit: int = Query(10, ge=1, le=20)):
    return store.listing(db, p, instance_id, run_id, limit)


@router.get('/annotations/history')
def history(instance_id: str, run_id: str, p: ReadP, db: ReadDB,
            before_id: str | None = None, limit: int = Query(10, ge=1, le=20)):
    return store.history(db, p, instance_id, run_id, before_id=before_id, limit=limit)


@router.post('/annotations', status_code=201)
async def save(instance_id: str, run_id: str, request: Request, p: P, db: DB):
    # Bound before materializing a potentially large body. Chunked requests are
    # independently counted; Content-Length is never a trusted replacement.
    raw = bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw) > 131072:
            raise HTTPException(413, '标注申请超过大小上限；原稿保留')
    command = store.save(db, p, instance_id, run_id, bytes(raw))
    result = store.recovery(db, p, instance_id, run_id, command.request_id, command.raw_sha256)
    db.commit()
    if result['outcome'] == 'rejected':
        # A durable rejection is a positive recovery result. The original raw
        # request and nonce remain inspectable after a lost HTTP response.
        raise HTTPException(409, detail=result)
    return result['annotation']


@router.get('/annotation-requests')
def recover(instance_id: str, run_id: str, p: ReadP, db: ReadDB,
            request_id: str = Query(min_length=12, max_length=100),
            raw_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    return store.recovery(db, p, instance_id, run_id, request_id, raw_sha256)


@router.get('/annotations/{annotation_id}')
def detail(instance_id: str, run_id: str, annotation_id: str, p: ReadP, db: ReadDB):
    return store.view(store.get(db, p, instance_id, run_id, annotation_id))


@router.get('/annotations/{annotation_id}/artifacts/{kind}')
def artifact(instance_id: str, run_id: str, annotation_id: str, kind: Literal['labels', 'known_mask'],
             p: ReadP, db: ReadDB):
    row = store.get(db, p, instance_id, run_id, annotation_id)
    ev, raw = store._artifact(db, p, row, kind)
    return Response(raw, media_type=ev.media_type, headers={'X-Content-SHA256': ev.sha256,
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
        'Content-Disposition': 'attachment; filename="pixel-annotation-' + kind + '.png"'})
