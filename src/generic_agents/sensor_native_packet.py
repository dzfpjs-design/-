"""Immutable task-scoped local sensor evidence packets, never cloud evidence."""
from __future__ import annotations
import copy,json
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Job,Memory,Task,Observation,Capability,Membership,User
from .security import require
from .services import scoped
from .sensor_automation import DATA,ALERT
from . import sensor_agent_context as sensor

PACKET='sensor_native_packet_v1';STOP='sensor_native_packet_stop_v1'
NOTICE='设备自动采集读数和阈值线索待核实，不代表现场结论；仅保存在本地，未发送云端，未更新对象状态。'

def _task_record(db,p,task,kind,identity):
    record=sensor._read(db,p,task,kind,identity,for_cloud=False)
    # Prefer the human-readable field captured when this particular task was
    # created; generated external IDs are not meaningful business names.
    snapshot=next((x for x in task.data.get('object_snapshots',[]) if x.get('id')==record['provenance']['entity_id']),None)
    if isinstance(snapshot,dict):
        attrs=snapshot.get('attributes',{});fields=snapshot.get('fields',[])
        for field in fields:
            label=str(field.get('label','')).strip().lower()
            if label not in {'名称','标题','名字','name','title'}:continue
            value=attrs.get(field.get('key'))
            if isinstance(value,str) and value.strip():
                record={**record,'object_name':value.strip()};break
    return record

def task_access(db,p,task_id,*,live=False,lock=False):
    require(p,'admin','operator')
    if live:
        from .workspace_api import execution
        task,release,business=execution(db,p,task_id,lock,lock_sources=lock)
    else:
        from .workspace_api import saved_execution
        from .entity_release_compat import verify_task_objects
        if not lock:
            first=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id).with_for_update(read=True).execution_options(populate_existing=True))
            if not first:raise HTTPException(404,'任务不存在')
        task,release,business=saved_execution(db,p,task_id,lock)
        verify_task_objects(db,p,task,release,lock_sources=lock)
    sensor._membership(db,p)
    if task.owner_id!=p.user_id:raise HTTPException(403,'仅任务负责人可整理本任务传感器证据')
    if not task.data.get('entity_ids'):raise HTTPException(409,'本任务没有指定对象')
    return task,release,business

def history_access(db,p,task_id,*,allow_admin_read=False):
    require(p,'admin','operator','viewer');task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task_id).with_for_update(read=True).execution_options(populate_existing=True))
    if not task:raise HTTPException(404,'任务不存在')
    member=db.scalar(select(Membership.role).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,
        Membership.role.in_(['admin','operator','viewer']),User.active.is_(True)).with_for_update(read=True))
    if not member or member!=p.role:raise HTTPException(403,'当前账号无工作区访问权限')
    if not task.data.get('workflow_runtime') or (task.owner_id!=p.user_id and not (allow_admin_read and member=='admin')):raise HTTPException(403,'无权查看此任务证据')
    return task

def key(identity):return 'sensor-native-packet:'+identity

def checked(row,p,task_id,*,admin_read=False):
    data=row.data
    if (row.kind!=PACKET or row.status!='internal' or row.workspace_id!=p.workspace_id or not isinstance(data.get('actor_id'),str) or (data.get('actor_id')!=p.user_id and not (admin_read and p.role=='admin')) or data.get('task_id')!=task_id
        or not isinstance(data.get('packet'),dict) or digest(data['packet'])!=data.get('packet_hash')):
        raise HTTPException(409,'原证据包完整性无法核对，记录保留')
    packet=data['packet']
    if packet.get('task_id')!=task_id or packet.get('workspace_id')!=p.workspace_id or packet.get('actor_id')!=data['actor_id']:
        raise HTTPException(409,'原证据包所属任务无法核对')
    return {'id':row.id,'created_at':row.created_at.isoformat(),'records':copy.deepcopy(packet['records']),
        'notice':NOTICE,'task_version':packet['task_version'],'packet_hash':data['packet_hash']}

def candidates(db,p,task):
    entities=task.data['entity_ids'];out=[];skipped=0;has_more=False
    for kind,query in [('data',select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['type'].as_string()==DATA,Memory.data['entity_id'].as_string().in_(entities))),
                       ('alert',select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==ALERT,Job.status=='internal',Job.data['entity_id'].as_string().in_(entities)))]:
        table=Memory if kind=='data' else Job
        rows=list(db.scalars(query.order_by(table.created_at.desc(),table.id.desc()).limit(51)))
        has_more=has_more or len(rows)>50
        for row in rows[:50]:
            try:
                record=_task_record(db,p,task,kind,row.id)
                out.append({**record,'record_hash':digest(record)})
            except (HTTPException,KeyError,TypeError,ValueError):skipped+=1
    return out,skipped,has_more

def read_selection(db,p,task,selection):
    if not 1<=len(selection)<=20 or len({(x['kind'],x['id']) for x in selection})!=len(selection):raise HTTPException(422,'请选择 1 至 20 条不同的记录')
    initial=[_task_record(db,p,task,x['kind'],x['id']) for x in selection]
    # Acquire deterministic shared locks, then re-read and verify the exact
    # values the operator saw. Source, configuration and evidence cannot be
    # replaced while this packet is committed.
    for kind in ('data','alert'):
        ids=sorted(x['id'] for x in selection if x['kind']==kind)
        if ids:
            table=Memory if kind=='data' else Job
            list(db.scalars(select(table).where(table.workspace_id==p.workspace_id,table.id.in_(ids)).order_by(table.id).with_for_update(read=True).execution_options(populate_existing=True)))
    provenance=[r['provenance'] for r in initial]
    for table,ids in [(Observation,{x['observation_id'] for x in provenance}),
                      (Job,{x['reading_id'] for x in provenance}),
                      (Capability,{x['source_id'] for x in provenance})]:
        list(db.scalars(select(table).where(table.workspace_id==p.workspace_id,table.id.in_(sorted(ids))).order_by(table.id).with_for_update(read=True).execution_options(populate_existing=True)))
    config_keys=sorted('sensor-auto-config:'+x['configuration_request_id'] for x in provenance)
    list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key.in_(config_keys)).order_by(Job.id).with_for_update(read=True).execution_options(populate_existing=True)))
    rows=[_task_record(db,p,task,x['kind'],x['id']) for x in selection]
    if any(digest(row)!=item['record_hash'] for row,item in zip(rows,selection)):
        raise HTTPException(409,'所选记录或来源已变化，请刷新后重新核对')
    rows=[{**row,'record_hash':digest(row)} for row in rows]
    if len(json.dumps(rows,ensure_ascii=False).encode())>32000:raise HTTPException(422,'所选证据过大，请减少记录')
    return rows
