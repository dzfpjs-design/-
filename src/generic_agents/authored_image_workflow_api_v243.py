"""Explicit fixed image recipe and local registry commands, never execution."""
from typing import Annotated
from fastapi import APIRouter,Depends,Request,Query,HTTPException
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import authenticated,Principal
from .report_text_parse_scope import report_text_parse_scope
from .authored_workflow_store_v208 import ID,Hash
from . import authored_image_workflow_store_v243 as store,authored_image_tools_v243 as tools
from .authored_image_workflow_contracts_v243 import ToolsCommand,parse

router=APIRouter(prefix=store.PREFIX)
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]


async def raw_body(request,limit=65536):
    parts=[];size=0
    async for part in request.stream():
        size+=len(part)
        if size>limit:raise HTTPException(413,'图像编制请求超过合同大小，未执行')
        parts.append(part)
    return b''.join(parts)


def operation(db,p,function,*args,commit=False):
    try:
        with report_text_parse_scope():
            result=function(db,p,*args)
            if commit:db.commit()
            return result
    except BaseException:db.rollback();raise


@router.get('/context')
def context(p:P,db:DB,business_name:str=Query(min_length=1,max_length=100)):
    with report_text_parse_scope():return store.context(db,p,business_name)


@router.post('/preview')
async def preview(request:Request,p:P,db:DB):
    body=store.parse(await raw_body(request),store.Preview)[0]
    return await run_in_threadpool(operation,db,p,store.preview,body)


@router.post('/adopt',status_code=201)
async def adopt(request:Request,p:P,db:DB):
    raw=await raw_body(request)
    return await run_in_threadpool(operation,db,p,store.adopt,raw)


@router.get('/requests/{identity}')
def recover(identity:ID,expected_hash:Hash,p:P,db:DB):
    return operation(db,p,store.recover,identity,expected_hash,commit=True)


@router.get('/adoptions/{identity}')
def get_adoption(identity:ID,p:P,db:DB):
    with report_text_parse_scope():return store.get_adoption(db,p,identity)


@router.post('/tools/prepare')
async def prepare_tools(request:Request,p:P,db:DB):
    raw=await raw_body(request,16384);parse(raw,ToolsCommand)
    return await run_in_threadpool(operation,db,p,tools.prepare,raw,commit=True)


@router.get('/tools/requests/{identity}')
def recover_tools(identity:ID,expected_hash:Hash,p:P,db:DB):
    return operation(db,p,tools.recover,identity,expected_hash,commit=True)
