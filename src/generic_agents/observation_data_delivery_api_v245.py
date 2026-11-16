"""Explicit original-event release; acceptance is not a data reception ACK."""
from typing import Annotated
from fastapi import APIRouter,Depends,Request,HTTPException
from starlette.concurrency import run_in_threadpool
from .db import session
from .security import authenticated,Principal
from .layered_approval_contract_v175 import ID,SHA
from . import observation_data_delivery_v245 as service

router=APIRouter(prefix='/api/workspace/instances/{instance_id}/observations/{observation_id}/data-delivery-v245')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]


@router.get('')
def context(instance_id:ID,observation_id:ID,p:P,db:DB):
    return service.context(db,p,instance_id,observation_id)


def transaction(db,p,instance_id,observation_id,raw):
    try:
        result=service.retry(db,p,instance_id,observation_id,raw);db.commit();return result
    except BaseException:db.rollback();raise


@router.post('/retry')
async def retry(instance_id:ID,observation_id:ID,request:Request,p:P,db:DB):
    parts=[];size=0
    async for part in request.stream():
        size+=len(part)
        if size>service.MAX_BODY:raise HTTPException(413,'数据投递命令超过16KiB，未执行')
        parts.append(part)
    raw=b''.join(parts);service.parse(raw)
    return await run_in_threadpool(transaction,db,p,instance_id,observation_id,raw)


@router.get('/requests/{request_id}')
def recover(instance_id:ID,observation_id:ID,request_id:ID,expected_sha256:SHA,p:P,db:DB):
    result=service.recover(db,p,instance_id,observation_id,request_id,expected_sha256)
    db.commit();return result
