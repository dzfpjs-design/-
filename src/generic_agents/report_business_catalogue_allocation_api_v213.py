"""Human batch-allocation context; new paid routes are registered separately."""
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from . import report_business_catalogue_store_v211 as store
from . import report_business_catalogue_allocation_source_v213 as source

router = APIRouter(prefix=store.PREFIX)
consumer_router = APIRouter(prefix='/api/setup/report-saved-map-designs')


@router.get('/drafts/{draft_id}/allocation-context')
def allocation_context(draft_id: str, p: P, db: DB):
    try:
        store.identity(draft_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '请保留原目录编号，不新造或猜修编号') from None
    try:
        return source.context(db, p, draft_id)
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        raise HTTPException(409, '目录、原来源或命名处理记录尚无法核对；人工稿保留，未准备模型调用') from None


async def selection_wire(request, model):
    from .report_saved_map_design_consumer_v176 import MAX_WIRE_BYTES, read_wire
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_WIRE_BYTES:
            raise HTTPException(413, '所选建议申请超过当前容量，原选择保留')
        chunks.append(chunk)
    raw = b''.join(chunks)
    body, _, _ = read_wire(raw, model)
    return raw, body


@consumer_router.post('/{consumer_id}/preview-allocation-apply')
async def preview_selection(consumer_id: str, request: Request, p: P, db: DB):
    from . import report_business_catalogue_allocation_apply_v213 as adoption
    from .report_business_catalogue_api_v211 import identity, safe
    _, body = await selection_wire(request, adoption.AllocationSelectionBody)
    return await run_in_threadpool(safe, adoption.preview_selection, db, p, identity(consumer_id), body)


@consumer_router.post('/{consumer_id}/apply-allocation')
async def apply_selection(consumer_id: str, request: Request, p: P, db: DB):
    from . import report_business_catalogue_allocation_apply_v213 as adoption
    from .report_business_catalogue_api_v211 import identity, safe
    raw, _ = await selection_wire(request, adoption.ApplyAllocationBody)
    consumer_id = identity(consumer_id)

    def commit():
        try:
            result = safe(adoption.apply_selection, db, p, consumer_id, raw,
                actual_method=request.method, actual_path=request.url.path)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
    return await run_in_threadpool(commit)


@consumer_router.post('/{consumer_id}/preview-current-allocation-apply')
async def preview_current_selection(consumer_id: str, request: Request, p: P, db: DB):
    from . import catalogue_current_allocation_apply_v260 as adoption
    from .report_business_catalogue_api_v211 import identity, safe
    _, body = await selection_wire(request, adoption.SelectionBody)
    return await run_in_threadpool(safe, adoption.preview_selection, db, p, identity(consumer_id), body)


@consumer_router.post('/{consumer_id}/apply-current-allocation')
async def apply_current_selection(consumer_id: str, request: Request, p: P, db: DB):
    from . import catalogue_current_allocation_apply_v260 as adoption
    from .report_business_catalogue_api_v211 import identity, safe
    raw, _ = await selection_wire(request, adoption.ApplyBody)
    consumer_id = identity(consumer_id)
    def commit():
        try:
            result = safe(adoption.apply_selection, db, p, consumer_id, raw,
                actual_method=request.method, actual_path=request.url.path)
            db.commit(); return result
        except BaseException:
            db.rollback(); raise
    return await run_in_threadpool(commit)


@consumer_router.get('/{consumer_id}/current-allocation-end-context')
def current_end_context(consumer_id: str, p: P, db: DB):
    from .catalogue_current_allocation_end_v260 import context
    from .report_business_catalogue_api_v211 import identity, safe
    return safe(context, db, p, identity(consumer_id))


@consumer_router.post('/{consumer_id}/end-current-interrupted')
async def end_current_interrupted(consumer_id: str, request: Request, p: P, db: DB):
    from . import catalogue_current_allocation_end_v260 as ending
    from .report_business_catalogue_api_v211 import identity, safe
    from .report_saved_map_design_consumer_v176 import read_wire
    raw, body = await selection_wire(request, ending.EndInterruptedBody)
    body, exact, sha = read_wire(raw, ending.EndInterruptedBody)
    consumer_id = identity(consumer_id)
    if request.method != 'POST' or request.url.path != '/api/setup/report-saved-map-designs/' + consumer_id + '/' + ending.OPERATION:
        raise HTTPException(422, '结束路径或方法不符合原申请')
    def commit():
        try:
            result = safe(ending.end, db, p, consumer_id, body, exact, sha)
            db.commit(); return result
        except BaseException:
            db.rollback(); raise
    return await run_in_threadpool(commit)
