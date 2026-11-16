"""Authenticated sibling routes; reads never dispatch or repair a question."""
from typing import Annotated
from fastapi import APIRouter,Query,Request
from starlette.concurrency import run_in_threadpool
from .setup_api import P,DB
from .pixel_question_contracts_v252 import Submit,PreviewInput,Command
from . import pixel_question_service_v252 as service

router=APIRouter(prefix='/instances/{instance_id}/pixel-questions')

@router.get('/options')
def options(instance_id:str,p:P,db:DB):return service.options(db,p,instance_id)

@router.post('/preview')
def preview(instance_id:str,body:PreviewInput,p:P,db:DB):return service.preview(db,p,instance_id,body)

@router.post('',status_code=201)
async def submit(instance_id:str,body:Submit,request:Request,p:P,db:DB):
    raw=await request.body()
    return await run_in_threadpool(service.create,db,p,instance_id,body,raw)

@router.get('/requests')
def recover(instance_id:str,p:P,db:DB,request_id:Annotated[str,Query(min_length=12,max_length=100)],
        expected_body_hash:Annotated[str,Query(pattern=r'^[a-f0-9]{64}$')]):
    return service.lookup(db,p,instance_id,request_id,expected_body_hash)

@router.get('')
def history(instance_id:str,p:P,db:DB,limit:Annotated[int,Query(ge=1,le=50)]=20,before_id:str|None=None):
    return service.history(db,p,instance_id,limit,before_id)

@router.get('/{query_id}')
def get(instance_id:str,query_id:str,p:P,db:DB):return service.get(db,p,instance_id,query_id)

@router.post('/{query_id}/cancel')
async def cancel(instance_id:str,query_id:str,body:Command,request:Request,p:P,db:DB):
    raw=await request.body()
    return await run_in_threadpool(service.command,db,p,instance_id,query_id,'cancel',body,raw)

@router.post('/{query_id}/retry')
async def retry(instance_id:str,query_id:str,body:Command,request:Request,p:P,db:DB):
    raw=await request.body()
    return await run_in_threadpool(service.command,db,p,instance_id,query_id,'retry',body,raw)

@router.get('/{query_id}/commands/requests')
def recover_command(instance_id:str,query_id:str,p:P,db:DB,request_id:Annotated[str,Query(min_length=12,max_length=100)],
        expected_body_hash:Annotated[str,Query(pattern=r'^[a-f0-9]{64}$')]):
    return service.command_lookup(db,p,instance_id,query_id,request_id,expected_body_hash)
