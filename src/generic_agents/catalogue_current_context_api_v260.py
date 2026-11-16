"""M1 explicit current-context catalogue copy; no paid or confirm endpoint."""
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from starlette.concurrency import run_in_threadpool
from .security import Principal
from .setup_api import P,DB
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import catalogue_current_context_store_v260 as store
from .catalogue_current_context_contracts_v260 import MAX_RAW,identity

ReadP=Annotated[Principal,Depends(snapshot_authenticated)]
ReadDB=Annotated[object,Depends(read_session)]
router=APIRouter(prefix=store.PREFIX)


def safe(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError):
        raise HTTPException(409,store.ERROR) from None


def _identity(value):
    try:return identity(value)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请使用原小写UUID编号') from None


@router.get('/source-context')
def source_context(draft_id:str,p:P,db:DB):
    # Fresh source qualification uses the existing actor/source locking gate.
    # No rows are committed; history endpoints below use a read-only snapshot.
    return safe(store.source_context,db,p,_identity(draft_id))


@router.get('/history')
def history(p:ReadP,db:ReadDB,source_draft_id:str|None=None,before_id:str|None=None,
            limit:int=Query(default=20,ge=1,le=50)):
    return safe(store.history,db,p,source_draft_id=_identity(source_draft_id) if source_draft_id is not None else None,
        before_id=_identity(before_id) if before_id is not None else None,limit=limit)


@router.get('/requests/{request_id}')
def recovery(request_id:str,p:ReadP,db:ReadDB,raw_sha256:str=Query(pattern=r'^[0-9a-f]{64}$')):
    return safe(store.request_result,db,p,_identity(request_id),raw_sha256)


@router.get('/working-copies/{working_draft_id}')
def working_copy(working_draft_id:str,p:ReadP,db:ReadDB):
    return safe(store.binding_for_working,db,p,_identity(working_draft_id))


@router.get('/bindings/{binding_id}')
def binding(binding_id:str,p:ReadP,db:ReadDB):
    return safe(store.binding_view,db,p,_identity(binding_id))


@router.get('/bindings/{binding_id}/allocation-context')
def allocation_context(binding_id:str,p:P,db:DB):
    from .catalogue_current_allocation_v260 import context
    return safe(context,db,p,_identity(binding_id))


def _fork_tx(db,p,raw):
    try:
        result=store.fork(db,p,raw)
        recovery=store.request_result(db,p,result['request_id'],result['raw_sha256'])
        db.commit()
    except Exception:
        db.rollback();raise
    if result['outcome']=='rejected':
        raise HTTPException(409,detail=recovery)
    return result


@router.post('/forks',status_code=201)
async def fork(request:Request,p:P,db:DB):
    raw=bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw)>MAX_RAW:raise HTTPException(413,'目录复用申请超过原文上限')
    return await run_in_threadpool(_fork_tx,db,p,bytes(raw))
