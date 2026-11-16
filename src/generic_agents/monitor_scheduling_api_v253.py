"""Instance-scoped scheduling preferences; no acquisition or model call here."""
from typing import Annotated
from fastapi import APIRouter,Depends,Query,Request,HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import authenticated,Principal
from .models import MonitorPolicy
from .services import scoped
from .monitor_scheduling_contracts_v253 import ModeCommand,RetireCommand
from . import monitor_scheduling_service_v253 as store

router=APIRouter()
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]

@router.get('/monitor-policy-scheduling')
def directory(p:P,db:DB,instance_id:str,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=50)):
    return store.catalog(db,p,instance_id,offset,limit)

@router.get('/monitor-policies/{policy_id}/scheduling-control')
def read(policy_id:str,instance_id:str,p:P,db:DB):
    return store.view(db,p,scoped(db,MonitorPolicy,policy_id,p),instance_id)

@router.post('/monitor-policies/{policy_id}/scheduling-control')
async def change(policy_id:str,body:ModeCommand,request:Request,p:P,db:DB):
    raw=await request.body()
    if len(raw)>32768:raise HTTPException(413,'监测方式申请过大')
    try:raw=raw.decode('utf8')
    except UnicodeError:raise HTTPException(422,'申请需要UTF-8编码') from None
    result,created=await run_in_threadpool(store.set_mode,db,p,policy_id,body,raw)
    await run_in_threadpool(db.commit)
    return JSONResponse(result,status_code=201 if created else 200)

@router.get('/monitor-policies/{policy_id}/scheduling-control/requests')
def original(policy_id:str,p:P,db:DB,request_id:str=Query(min_length=12,max_length=100),
        raw_sha256:str=Query(pattern=r'^[0-9a-f]{64}$')):
    return store.recovery(db,p,policy_id,request_id,raw_sha256)


@router.post('/monitor-policies/{policy_id}/scheduling-control/retire')
async def retire(policy_id:str,body:RetireCommand,request:Request,p:P,db:DB):
    raw=await request.body()
    if len(raw)>262144:raise HTTPException(413,'结束申请过大')
    try:raw=raw.decode('utf8')
    except UnicodeError:raise HTTPException(422,'申请需要UTF-8编码') from None
    result,created=await run_in_threadpool(store.retire_request,db,p,policy_id,body,raw)
    await run_in_threadpool(db.commit)
    return JSONResponse(result,status_code=201 if created else 200)
