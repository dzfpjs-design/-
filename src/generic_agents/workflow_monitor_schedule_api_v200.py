"""Read actual monitoring-check schedules and stop a saved adjustment.

Neither endpoint starts a Task, approves a policy nor cancels an in-flight
device action. A stop result can be recovered by its exact original nonce.
"""
import copy
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from . import workflow_monitor_schedule_v200 as store
from .db import session
from .models import MonitorPolicy, Task
from .security import Principal, authenticated
from .services import scoped

router=APIRouter(prefix='/api/monitoring-schedules')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]
MAX_STOP_BYTES=16384


def query(request,required):
    store.need(set(request.query_params)==set(required)
        and all(len(request.query_params.getlist(key))==1 for key in required),status=422)
    return request.query_params


def identifier(value):
    try:return store.safe.identity(value)
    except (ValueError,TypeError,AttributeError):
        raise HTTPException(422,'请选择有效的任务、监测策略或原请求编号') from None


def list_schedules(db,p,task_id):
    identifier(task_id);scoped(db,Task,task_id,p)
    origins=list(db.scalars(select(store.Installation).where(store.Installation.workspace_id==p.workspace_id,
        store.Installation.parent_task_id==task_id)))
    store.need(len(origins)<=store.MAX_RECORDS,'本任务调度记录超过查询容量，未截断',422)
    result=[]
    for policy_id in sorted({row.policy_id for row in origins}):
        head,installs,cycles=store._checked(db,p,policy_id)
        store.need(head is not None)
        current=next(row for row in installs if row.id==head.data['active_installation_id'])
        profile=current.data['selected_profile']
        history=sorted([row for row in cycles if row.installation_id==current.id],key=lambda row:row.head_version)
        latest=history[-1].data if history else None
        policy=scoped(db,MonitorPolicy,policy_id,p)
        can_stop=p.role in {'admin','operator'} and policy.status=='active'
        if can_stop:
            from .perception import authorized_monitor
            try:authorized_monitor(db,p,policy)
            except HTTPException:can_stop=False
        result.append({'schema':'workflow-monitor-schedule-view-v200','policy_id':policy_id,
            'status':head.status,'version':head.version,'expected_schedule_pin':store.schedule_pin(current),
            'installed_for_task_id':current.parent_task_id,'current_from_this_task':current.parent_task_id==task_id,
            'interval_seconds':profile['interval_seconds'],'valid_until':profile['valid_until'],
            'priority_entity_ids':copy.deepcopy(profile['priority_entity_ids']),
            'next_check_at':head.next_due.isoformat() if head.next_due else None,
            'latest_check':None if latest is None else {'checked_at':latest['checked_at'],
                'outcome':latest['outcome'],'acquisition_count':len(latest['acquisition_ids']),
                'reason':latest['reason']},
            'simulation_only':current.data['source']['simulation_only'],
            'can_stop':can_stop,
            'hardware_sampling_rate_changed':False,'device_execution_cancelled':False})
    return {'schema':'workflow-monitor-schedule-list-v200','task_id':task_id,'items':result}


@router.get('')
async def list_route(request:Request,p:P,db:DB):
    params=query(request,{'task_id'})
    return await run_in_threadpool(list_schedules,db,p,params['task_id'])


@router.get('/{policy_id}/stop-requests/{request_id}')
async def original_stop_route(policy_id:str,request_id:str,request:Request,p:P,db:DB):
    params=query(request,{'expected_sha256'})
    identifier(policy_id);identifier(request_id)
    return await run_in_threadpool(store.original_stop_request,db,p,policy_id,request_id,
        params['expected_sha256'],path='/api/monitoring-schedules/'+policy_id+'/stop')


@router.post('/{policy_id}/stop')
async def stop_route(policy_id:str,request:Request,p:P,db:DB):
    query(request,set())
    identifier(policy_id)
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>MAX_STOP_BYTES:raise HTTPException(413,'停止请求超过16KiB，未截断或修改计划')
        chunks.append(chunk)
    raw=b''.join(chunks)
    try:body=json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeError,RecursionError):raise HTTPException(422,'请提交完整的停止请求') from None
    if type(body) is dict:
        identifier(body.get('request_id'))
        if type(body.get('expected_schedule_pin')) is dict:
            identifier(body['expected_schedule_pin'].get('installation_id'))
    def apply():
        value=store.stop_schedule(db,p,policy_id,body,raw,path=request.url.path)
        db.commit();return value
    return await run_in_threadpool(apply)
