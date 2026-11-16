"""Explicit V2 namespace; V1 DTO, routes and synchronous semantics are untouched."""
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import authenticated, Principal
from . import source_query_service as service
from .source_query_contracts import MAX_BODY, need, parse, uuid, day

router=APIRouter(prefix='/api/v2/data/queries')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]


async def read_body(request):
    size=0;parts=[]
    async for part in request.stream():
        size+=len(part);need(size<=MAX_BODY,'请求超过24KiB，未执行',413);parts.append(part)
    return b''.join(parts)


def transaction(db,p,operation,raw,identity=None):
    try:
        if operation=='create':result,created=service.create(db,p,raw)
        else:
            fn=service.cancel if operation=='cancel' else service.note
            result=fn(db,p,identity,raw);created=False
        db.commit();return result,created
    except BaseException:
        db.rollback();raise


@router.get('/catalog')
def catalog(instance_id:str,batch_day:str,p:P,db:DB):
    try:uuid(instance_id);day(batch_day)
    except ValueError:need(False,'实例或批次日期格式无效',422)
    return service.catalog(db,p,instance_id,batch_day)


@router.post('')
async def create(request:Request,response:Response,p:P,db:DB):
    raw=await read_body(request);parse(raw,'create')
    result,created=await run_in_threadpool(transaction,db,p,'create',raw)
    response.status_code=201 if created else 200
    return result


@router.get('/requests/{request_id}')
def recover(request_id:str,expected_sha256:str,p:P,db:DB):
    try:uuid(request_id)
    except ValueError:need(False,'原请求编号格式无效',422)
    return service.recover(db,p,request_id,expected_sha256)


@router.get('')
def history(instance_id:str,p:P,db:DB,cursor:str|None=None):
    try:uuid(instance_id)
    except ValueError:need(False,'实例编号格式无效',422)
    rows=sorted(service.inventory(db,p,instance_id),key=lambda row:(row.created_at,row.id),reverse=True)
    if cursor is not None:
        matches=[index for index,row in enumerate(rows) if row.id==cursor]
        need(len(matches)==1,'分页位置不属于本实例',422);rows=rows[matches[0]+1:]
    selected=rows[:service.PAGE_SIZE]
    return {'schema':'data-query-list-v2','instance_id':instance_id,'items':[service.view(db,p,row) for row in selected],
        'next_cursor':selected[-1].id if len(rows)>len(selected) else None}


@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return service.view(db,p,service.get(db,p,identity))


@router.post('/{identity}/cancel')
async def cancel(identity:str,request:Request,p:P,db:DB):
    raw=await read_body(request);parse(raw,'cancel')
    result,_=await run_in_threadpool(transaction,db,p,'cancel',raw,identity);return result


@router.post('/{identity}/notes')
async def note(identity:str,request:Request,p:P,db:DB):
    raw=await read_body(request);parse(raw,'note')
    result,_=await run_in_threadpool(transaction,db,p,'note',raw,identity);return result


@router.get('/{identity}/freshness')
def freshness(identity:str,p:P,db:DB):return service.freshness(db,p,identity)
