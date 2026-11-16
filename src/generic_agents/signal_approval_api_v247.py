"""Explicit authorization and original-byte recovery, never automatic approval."""
from uuid import UUID
from fastapi import APIRouter,Request,Query,HTTPException
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .setup_api import P,DB
from .models import DomainInstance
from .services import scoped
from . import signal_approval_routing_v247 as service

router=APIRouter()
PREFIX='/instances/{identity}/signal-approval-routing'


@router.get(PREFIX+'/options')
def options(identity:UUID,p:P,db:DB):
    return service.options(db,p,scoped(db,DomainInstance,str(identity),p))


@router.get(PREFIX)
def routes(identity:UUID,p:P,db:DB):return service.listing(db,p,str(identity))


@router.get(PREFIX+'/requests/{request_id}')
def recover(identity:UUID,request_id:UUID,p:P,db:DB,raw_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
    return service.recover(db,p,str(identity),str(request_id),raw_sha256)


@router.get(PREFIX+'/occurrences')
def occurrences(identity:UUID,p:P,db:DB,limit:int=Query(20,ge=1,le=50),after:UUID|None=None):
    return service.occurrences(db,p,str(identity),limit=limit,after=str(after) if after else None)


@router.get(PREFIX+'/occurrences/{occurrence_id}')
def occurrence(identity:UUID,occurrence_id:UUID,p:P,db:DB):
    return service.occurrence_detail(db,p,str(identity),str(occurrence_id))


def _transaction(db,p,identity,operation,body,raw,route_id=None,occurrence_id=None):
    result=service.command(db,p,identity,operation,body,raw,route_id=route_id,occurrence_id=occurrence_id)
    db.commit();return result


async def _command(request,db,p,identity,operation,route_id=None,occurrence_id=None):
    raw=await request.body()
    if len(raw)>65536:raise HTTPException(413,'原请求超过范围')
    try:
        original=raw.decode('utf-8');body=service.parse_raw(operation,original)
    except (ValueError,UnicodeError,ValidationError,KeyError,TypeError):
        raise HTTPException(422,'请选择完整范围、期限、额度并明确只建立审批草稿') from None
    return await run_in_threadpool(_transaction,db,p,str(identity),operation,body,original,
        str(route_id) if route_id else None,str(occurrence_id) if occurrence_id else None)


@router.post(PREFIX+'/enable',status_code=201)
async def enable(identity:UUID,request:Request,p:P,db:DB):return await _command(request,db,p,identity,'enable')


@router.post(PREFIX+'/{route_id}/pause')
async def pause(identity:UUID,route_id:UUID,request:Request,p:P,db:DB):
    return await _command(request,db,p,identity,'pause',route_id=route_id)


@router.post(PREFIX+'/occurrences/{occurrence_id}/retry')
async def retry(identity:UUID,occurrence_id:UUID,request:Request,p:P,db:DB):
    return await _command(request,db,p,identity,'retry',occurrence_id=occurrence_id)


@router.get('/tasks/{task_id}/signal-approval-source')
def source(task_id:UUID,p:P,db:DB):return service.task_source(db,p,str(task_id))
