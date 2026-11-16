"""Human-readable archive queries/revisions; no formal-confirmation endpoint."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import workflow_task_archive_v198 as archive
from .db import session
from .security import Principal, authenticated

router = APIRouter(prefix='/api/task-archives')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def query(request, required, optional=()):
    names = set(request.query_params)
    archive.need(set(required) <= names <= set(required) | set(optional)
        and all(len(request.query_params.getlist(name)) == 1 for name in names),status=422)
    return request.query_params


@router.get('')
async def list_route(request:Request,p:P,db:DB):
    params=query(request,{'task_id'},{'record_type','q'})
    return await run_in_threadpool(archive.list_archives,db,p,params['task_id'],
        record_type=params.get('record_type'),q=params.get('q'))


@router.get('/requests/original')
async def original_route(request:Request,p:P,db:DB):
    params=query(request,{'request_key','expected_sha256'})
    return await run_in_threadpool(archive.recover,db,p,params['request_key'],params['expected_sha256'])


@router.get('/{archive_id}')
async def detail_route(archive_id:str,request:Request,p:P,db:DB):
    query(request,set())
    return await run_in_threadpool(archive.get,db,p,archive_id)


@router.get('/{archive_id}/versions/{version}')
async def version_route(archive_id:str,version:int,request:Request,p:P,db:DB):
    query(request,set());archive.need(type(version) is int and version >= 1,status=422)
    return await run_in_threadpool(archive.get,db,p,archive_id,version)


@router.post('/{archive_id}/revisions')
async def revision_route(archive_id:str,request:Request,p:P,db:DB):
    query(request,set());chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>archive.MAX_COMMAND_BYTES:
            raise HTTPException(413,'档案修订内容超过容量；原输入保留，未截断提交')
        chunks.append(chunk)
    return await run_in_threadpool(archive.revise,db,p,archive_id,b''.join(chunks))
