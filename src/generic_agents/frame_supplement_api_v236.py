"""Normal authenticated Task UI routes; synchronous proofs stay off event loop."""
import hashlib
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import Principal, authenticated
from . import frame_supplement_v236 as service
from . import frame_supplement_source_v236 as source

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/api/workspace/tasks/{task_id}/frame-supplements')


async def body_bytes(request):
    parts = []; size = 0
    async for part in request.stream():
        size += len(part)
        service.need(size <= service.MAX_BODY, '补充请求超过16KB，未执行', 413)
        parts.append(part)
    return b''.join(parts)


def mutate(db, p, task_id, raw, operation, plan_id=None):
    try:
        if operation == 'propose':
            result, created = service.propose(db, p, task_id, raw)
        else:
            function = service.approve if operation == 'approve' else service.cancel
            result = function(db, p, task_id, plan_id, raw); created = False
        db.commit()
        return result, created
    except BaseException:
        db.rollback()
        raise


@router.post('')
async def propose(task_id: str, request: Request, response: Response, p: P, db: DB):
    raw = await body_bytes(request); service.parse(raw, 'propose')
    result, created = await run_in_threadpool(mutate, db, p, task_id, raw, 'propose')
    response.status_code = 201 if created else 200
    return result


@router.post('/{plan_id}/approve')
async def approve(task_id: str, plan_id: str, request: Request, p: P, db: DB):
    raw = await body_bytes(request); service.parse(raw, 'approve')
    result, _ = await run_in_threadpool(mutate, db, p, task_id, raw, 'approve', plan_id)
    return result


@router.post('/{plan_id}/cancel')
async def cancel(task_id: str, plan_id: str, request: Request, p: P, db: DB):
    raw = await body_bytes(request); service.parse(raw, 'cancel')
    result, _ = await run_in_threadpool(mutate, db, p, task_id, raw, 'cancel', plan_id)
    return result


@router.get('/requests/{request_id}')
def recover(task_id: str, request_id: str, expected_sha256: str, p: P, db: DB):
    return service.recover(db, p, task_id, request_id, expected_sha256)


@router.get('')
def rows(task_id: str, p: P, db: DB, cursor: str | None = None):
    values = sorted(service.inventory(db, p, task_id), key=lambda row: (row.created_at, row.id), reverse=True)
    if cursor is not None:
        positions = [index for index, row in enumerate(values) if row.id == cursor]
        service.need(len(positions) == 1, '分页位置不属于此任务', 422)
        values = values[positions[0]+1:]
    selected = values[:service.PAGE_SIZE]
    return {'schema': 'frame-supplement-list-v236', 'task_id': task_id,
        'items': [service.plan_view(db, p, row) for row in selected],
        'next_cursor': selected[-1].id if len(values) > len(selected) else None}


@router.get('/{plan_id}')
def get(task_id: str, plan_id: str, p: P, db: DB):
    return service.plan_view(db, p, service.checked_plan(db, p, task_id, plan_id))


@router.get('/{plan_id}/content')
def content(task_id: str, plan_id: str, expected_sha256: str, p: P, db: DB,
        frame: Literal['original', 'supplement']):
    row = service.checked_plan(db, p, task_id, plan_id)
    bundle = source.read_original(db, p, row) if frame == 'original' else source.read_followup(db, p, row)
    service.need(bundle['frame_sha256'] == expected_sha256 and hashlib.sha256(bundle['content']).hexdigest() == expected_sha256,
        '所选画面摘要已变化，未显示替代画面')
    from .daily_frame_quality_v233 import MAX_BYTES, MAX_PIXELS
    image = bundle['view']['image']
    service.need(0 < len(bundle['content']) <= MAX_BYTES and 0 < image['width']*image['height'] <= MAX_PIXELS)
    return Response(bundle['content'], media_type=image['media_type'], headers={
        'X-Content-SHA256': expected_sha256, 'Content-Length': str(len(bundle['content'])),
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@router.get('/{plan_id}/freshness')
def freshness(task_id: str, plan_id: str, p: P, db: DB):
    row = service.checked_plan(db, p, task_id, plan_id)
    valid = False; error = None
    try:
        source.read_original(db, p, row)
        if row.data.get('received_source'): source.read_followup(db, p, row)
        valid = True
    except (HTTPException, ValueError, KeyError, TypeError, OSError):
        error = '原固定输入、画面或独立收件已改变/缺失；原方案保留，没有重新读取'
    return {'schema': 'frame-supplement-freshness-v236', 'plan_id': plan_id,
        'originals_rechecked': valid, 'unchanged': valid, 'current_source_checked': False, 'error': error}
