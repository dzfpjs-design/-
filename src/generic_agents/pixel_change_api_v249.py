"""Normal workspace API for explicit pixel-only paired PNG analysis."""
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from fastapi.responses import Response
from .setup_api import P, DB
from .pixel_change_contracts_v249 import Create, Command
from . import pixel_change_runs_v249 as runs
from .evidence import evidence_bytes

router = APIRouter(prefix='/api/workspace/instances/{instance_id}/pixel-change')


@router.get('/options')
def options(instance_id: str, p: P, db: DB):
    return runs.options(db, p, instance_id)


@router.post('/runs', status_code=201)
def create(instance_id: str, body: Create, p: P, db: DB):
    row = runs.create(db, p, instance_id, body)
    db.commit()
    return runs.view(row)


@router.get('/requests')
def recover(instance_id: str, p: P, db: DB,
            request_id: Annotated[str, Query(min_length=12, max_length=100)],
            expected_body_hash: Annotated[str, Query(pattern=r'^[a-f0-9]{64}$')]):
    return runs.recover(db, p, instance_id, request_id, expected_body_hash)


@router.get('/runs')
def listing(instance_id: str, p: P, db: DB, limit: Annotated[int, Query(ge=1, le=50)] = 20, before_id: str | None = None):
    return runs.listing(db, p, instance_id, limit, before_id)


@router.get('/runs/{run_id}')
def detail(instance_id: str, run_id: str, p: P, db: DB):
    return runs.view(runs.get(db, p, instance_id, run_id))


def response(ev, raw, filename):
    return Response(raw, media_type=ev.media_type, headers={'X-Content-SHA256': ev.sha256,
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
        'Content-Disposition': 'attachment; filename="' + filename + '"'})


@router.get('/runs/{run_id}/originals/{side}')
def original(instance_id: str, run_id: str, side: Literal['before', 'after'], p: P, db: DB):
    row = runs.get(db, p, instance_id, run_id)
    ev, raw = evidence_bytes(db, p, row.data['sources'][side]['id'])
    return response(ev, raw, 'pixel-change-' + side + '.png')


@router.get('/runs/{run_id}/artifacts/{kind}')
def artifact(instance_id: str, run_id: str, kind: str, p: P, db: DB):
    row = runs.get(db, p, instance_id, run_id)
    runs.need(row.status == 'succeeded', '分析尚未完成，没有可下载的结果')
    runs.need(kind in row.data['result']['artifacts'], '没有此类分析产物', 404)
    ev, raw = runs._artifact(db, p, row, kind)
    filename = ('pixel-change-manifest.json' if kind == 'manifest' else
                'pixel-change-' + kind + ('.npy' if kind == 'probability_data' else '.png'))
    return response(ev, raw, filename)


@router.post('/runs/{run_id}/cancel')
def cancel(instance_id: str, run_id: str, body: Command, p: P, db: DB):
    result = runs.command(db, p, instance_id, run_id, 'cancel', body); db.commit()
    return result


@router.post('/runs/{run_id}/retry')
def retry(instance_id: str, run_id: str, body: Command, p: P, db: DB):
    result = runs.command(db, p, instance_id, run_id, 'retry', body); db.commit()
    return result
