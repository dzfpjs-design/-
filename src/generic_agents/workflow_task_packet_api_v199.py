"""Read saved local evidence packets; cloud consent remains the Evidence API."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from . import workflow_task_packet_v199 as packet
from .db import session
from .security import Principal, authenticated

router = APIRouter(prefix='/api/task-evidence-packets')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def query(request, required):
    packet.need(set(request.query_params) == set(required)
        and all(len(request.query_params.getlist(key)) == 1 for key in required), status=422)
    return request.query_params


@router.get('')
async def list_route(request:Request,p:P,db:DB):
    params=query(request,{'task_id'})
    return await run_in_threadpool(packet.list_packets,db,p,params['task_id'])


@router.get('/requests/original')
async def original_route(request:Request,p:P,db:DB):
    params=query(request,{'request_key','expected_sha256'})
    return await run_in_threadpool(packet.recover,db,p,params['request_key'],params['expected_sha256'])


@router.get('/{packet_id}')
async def detail_route(packet_id:str,request:Request,p:P,db:DB):
    query(request,set())
    return await run_in_threadpool(packet.get,db,p,packet_id)
