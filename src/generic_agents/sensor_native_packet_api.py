"""Local packets for formal workflow tasks; no cloud dispatch or device action."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import Field,StrictInt
from sqlalchemy import select
from .contracts import Contract,digest
from .models import Job
from .services import audit
from .setup_api import P,DB
from .ftps_gateway_api import _lock
from . import sensor_native_packet as svc

router=APIRouter(prefix='/api/sensor-native')
class Item(Contract):
    kind:Literal['data','alert']
    id:UUID
    record_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
class Create(Contract):
    request_id:UUID
    expected_task_version:StrictInt=Field(ge=1)
    selection:list[Item]=Field(min_length=1,max_length=20)

def find(db,p,request_id):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==svc.key(str(request_id))).execution_options(populate_existing=True))

def result(db,p,task_id,row):
    if row.data.get('task_id')!=task_id or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求属于其他任务或账号')
    if row.kind==svc.STOP:return {'state':'cancelled'}
    return {'state':'saved','packet':svc.checked(row,p,task_id)}

@router.get('/tasks/{task_id}/context')
def context(task_id:UUID,p:P,db:DB):
    identity=str(task_id);task=svc.history_access(db,p,identity,allow_admin_read=True)
    if task.owner_id!=p.user_id:
        return {'task_version':task.version,'records':[],'packets':[],'has_more':False,'packets_has_more':False,'skipped_count':0,'cloud_analysis_supported':False,'read_only':True}
    task,release,business=svc.task_access(db,p,identity)
    records,skipped,has_more=svc.candidates(db,p,task)
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==svc.PACKET,Job.data['task_id'].as_string()==identity,Job.data['actor_id'].as_string()==p.user_id).order_by(Job.created_at.desc(),Job.id.desc()).limit(51)))
    packets=[]
    for row in rows[:50]:
        try:packets.append(svc.checked(row,p,identity))
        except (HTTPException,KeyError,TypeError,ValueError):skipped+=1
    return {'task_version':task.version,'records':records,'packets':packets,'has_more':has_more,'packets_has_more':len(rows)>50,'skipped_count':skipped,'cloud_analysis_supported':False}

@router.get('/tasks/{task_id}/packets')
def history(task_id:UUID,p:P,db:DB,page:int=Query(default=1,ge=1,le=10000)):
    identity=str(task_id);task=svc.history_access(db,p,identity,allow_admin_read=True)
    admin_read=p.role=='admin' and task.owner_id!=p.user_id
    filters=[Job.workspace_id==p.workspace_id,Job.kind==svc.PACKET,Job.data['task_id'].as_string()==identity]
    if not admin_read:filters.append(Job.data['actor_id'].as_string()==p.user_id)
    rows=list(db.scalars(select(Job).where(*filters).order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*50).limit(51)))
    records=[];skipped=0
    for row in rows[:50]:
        try:records.append(svc.checked(row,p,identity,admin_read=admin_read))
        except (HTTPException,KeyError,TypeError,ValueError):skipped+=1
    return {'packets':records,'page':page,'has_more':len(rows)>50,'skipped_count':skipped}

@router.get('/tasks/{task_id}/packets/{packet_id}')
def history_item(task_id:UUID,packet_id:UUID,p:P,db:DB):
    identity=str(task_id);task=svc.history_access(db,p,identity,allow_admin_read=True)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(packet_id),Job.kind==svc.PACKET))
    if not row:raise HTTPException(404,'未找到证据包')
    return {'packet':svc.checked(row,p,identity,admin_read=p.role=='admin' and task.owner_id!=p.user_id)}

@router.post('/tasks/{task_id}/packets')
def create(task_id:UUID,body:Create,p:P,db:DB):
    identity=str(task_id);rid=str(body.request_id);fingerprint=digest({'actor_id':p.user_id,'task_id':identity,**body.model_dump(mode='json')})
    _lock(db,p.workspace_id,svc.key(rid))
    task,release,business=svc.task_access(db,p,identity,live=True,lock=True)
    original=find(db,p,rid)
    if original:
        if original.data.get('actor_id')!=p.user_id or original.data.get('task_id')!=identity:raise HTTPException(409,'原请求属于其他任务或账号')
        if original.kind==svc.STOP:raise HTTPException(409,'原请求已安全停止，请重新选择')
        if original.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原请求内容不一致')
        return result(db,p,identity,original)
    if task.status in {'workflow_cancelled','workflow_completed','cancelled','completed'}:raise HTTPException(409,'任务已结束，不能新增证据包')
    if task.version!=body.expected_task_version:raise HTTPException(409,'任务状态已变化，请刷新后核对原选择')
    selection=[{'kind':i.kind,'id':str(i.id),'record_hash':i.record_hash} for i in body.selection]
    records=svc.read_selection(db,p,task,selection)
    packet={'schema':'sensor-native-packet-v1','workspace_id':p.workspace_id,'actor_id':p.user_id,'task_id':identity,
        'task_version':task.version,'instance_id':task.data['instance_id'],'release_id':release.id,'release_hash':release.configuration_hash,
        'entity_ids':list(task.data['entity_ids']),'object_snapshots':task.data.get('object_snapshots',[]),
        'object_binding_hash':task.data.get('object_definition_bindings_hash'),'selection':selection,'records':records,
        'notice':svc.NOTICE,'cloud_authorized':False,'physical_state_verified':False}
    row=Job(workspace_id=p.workspace_id,kind=svc.PACKET,status='internal',idempotency_key=svc.key(rid),data={'task_id':identity,'actor_id':p.user_id,'fingerprint':fingerprint,'packet':packet,'packet_hash':digest(packet)})
    db.add(row);db.flush();audit(db,p,'sensor.native.packet_created',identity,{'packet_id':row.id,'record_count':len(records),'packet_hash':row.data['packet_hash']});db.commit()
    return result(db,p,identity,row)

@router.get('/tasks/{task_id}/requests/{request_id}')
def lookup(task_id:UUID,request_id:UUID,p:P,db:DB):
    identity=str(task_id);svc.history_access(db,p,identity);row=find(db,p,request_id)
    return result(db,p,identity,row) if row else {'state':'not_found'}

@router.post('/tasks/{task_id}/requests/{request_id}/cancel')
def cancel(task_id:UUID,request_id:UUID,p:P,db:DB):
    identity=str(task_id);rid=str(request_id);_lock(db,p.workspace_id,svc.key(rid));svc.history_access(db,p,identity)
    row=find(db,p,rid)
    if row:return result(db,p,identity,row)
    row=Job(workspace_id=p.workspace_id,kind=svc.STOP,status='internal',idempotency_key=svc.key(rid),data={'task_id':identity,'actor_id':p.user_id});db.add(row);db.flush();audit(db,p,'sensor.native.packet_request_stopped',identity,{'request_id':rid});db.commit();return {'state':'cancelled'}
