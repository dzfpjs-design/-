"""Explicit per-task cloud evidence preview and authorization."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field,StrictBool
from sqlalchemy import select
from .contracts import Contract,digest
from .models import Job,Memory,Task
from .services import scoped,audit
from .setup_api import P,DB
from .security import require
from . import sensor_agent_context as svc
from .sensor_automation import DATA,ALERT

router=APIRouter(prefix='/api/sensor-agent-context')
class Selection(Contract):
    kind:Literal['data','alert']
    id:UUID
class Request(Contract):
    task_id:UUID
    model_id:UUID
    selection:list[Selection]=Field(min_length=1,max_length=20)
class Authorization(Request):
    preview_hash:str=Field(min_length=64,max_length=64)
    consent:StrictBool

def _preview(db,p,body,lock=False):
    require(p,'admin','operator');task=scoped(db,Task,str(body.task_id),p,lock);svc.check_task(db,p,task)
    if task.status in {'planning','running','executing'}:raise HTTPException(409,'任务进行中，不能更改证据授权')
    cap=svc.model(db,p,str(body.model_id));ids=[{'kind':x.kind,'id':str(x.id)} for x in body.selection]
    records=svc.selected(db,p,task,ids)
    data={'task_id':task.id,'task_scope':svc.task_scope(task),'actor_id':p.user_id,'model_id':cap.id,'model_hash':cap.content_hash,'selection':ids,'records':records,'notice':svc.NOTICE}
    return task,data

@router.get('/tasks/{task_id}/candidates')
def candidates(task_id:UUID,p:P,db:DB):
    require(p,'admin','operator');task=scoped(db,Task,str(task_id),p);svc.check_task(db,p,task)
    entities=task.data['entity_ids'];result=[]
    rows=list(db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['type'].as_string()==DATA,Memory.data['entity_id'].as_string().in_(entities)).order_by(Memory.created_at.desc()).limit(51)))
    alerts=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==ALERT,Job.status=='internal',Job.data['entity_id'].as_string().in_(entities)).order_by(Job.created_at.desc()).limit(51)))
    skipped=0
    for kind,items in [('data',rows[:50]),('alert',alerts[:50])]:
        for row in items:
            try:result.append(svc._read(db,p,task,kind,row.id))
            except (HTTPException,KeyError,TypeError,ValueError):skipped+=1
    active=svc.active(db,p,task);authorization=None;issue=None
    if active and active.data.get('enabled'):
        try:
            svc.read_authorized(db,p,task)
            authorization={'model_id':active.data['model_id'],'selection':active.data['selection']}
        except HTTPException as error:issue=str(error.detail)
    return {'records':result,'skipped_count':skipped,'has_more':len(rows)>50 or len(alerts)>50,
        'authorized':authorization is not None,'authorization':authorization,'authorization_issue':issue}

@router.post('/preview')
def preview(body:Request,p:P,db:DB):
    _,data=_preview(db,p,body);return {**data,'preview_hash':digest(data)}

@router.post('/authorize')
def authorize(body:Authorization,p:P,db:DB):
    if not body.consent:raise HTTPException(422,'请先确认所选记录将发送至指定云模型')
    task,data=_preview(db,p,body,True)
    if digest(data)!=body.preview_hash:raise HTTPException(409,'预览内容已变化，请重新核对')
    grant=svc.active(db,p,task)
    value={'enabled':True,'actor_id':p.user_id,'task_scope':data['task_scope'],'model_id':data['model_id'],'model_hash':data['model_hash'],'selection':data['selection'],'records_hash':digest(data['records']),'preview_hash':body.preview_hash}
    if grant:grant.data=value;grant.version+=1
    else:grant=Job(workspace_id=p.workspace_id,kind=svc.KIND,status='internal',idempotency_key='sensor-agent-task:'+task.id,data=value);db.add(grant);db.flush()
    audit(db,p,'sensor.agent.authorize',task.id,{'record_count':len(data['records']),'model_id':data['model_id'],'preview_hash':body.preview_hash});db.commit()
    return {'authorized':True,'record_count':len(data['records']),'model_id':data['model_id']}

@router.post('/tasks/{task_id}/revoke')
def revoke(task_id:UUID,p:P,db:DB):
    require(p,'admin','operator');task=scoped(db,Task,str(task_id),p,True);svc.check_task(db,p,task)
    if task.status in {'planning','running','executing'}:raise HTTPException(409,'任务进行中，先取消或等待结束再撤回授权')
    grant=svc.active(db,p,task)
    if grant:grant.data={**grant.data,'enabled':False};grant.version+=1;audit(db,p,'sensor.agent.revoke',task.id);db.commit()
    return {'authorized':False}
