"""Read-only, explicitly authorized sensor evidence for legacy business planning.

This is separate from device control. It never promotes candidates to facts.
"""
from __future__ import annotations
import copy
from fastapi import HTTPException
from sqlalchemy import select
from .models import Job, Memory, Observation, Membership, User, Task
from .contracts import digest
from .services import published
from .business_tools import scope_hash
from .sensor_automation import DATA, ALERT
from .sensor_automation_api import DATA_FIELDS, _checked_alert

KIND='sensor_agent_authorization_v1'
NOTICE='设备读数及阈值线索均待核实，不代表现场结论；设备时间未知时不得推断观测时间。'

def _membership(db,p):
    row=db.scalar(select(Membership.id).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,
        Membership.role.in_(['admin','operator']),User.active.is_(True)).with_for_update(read=True))
    if not row:raise HTTPException(403,'当前账号无业务证据使用权限')

def check_task(db,p,task):
    _membership(db,p)
    if task.workspace_id!=p.workspace_id or task.owner_id!=p.user_id:raise HTTPException(403,'仅任务负责人可授权任务证据')
    if task.data.get('workflow_runtime'):raise HTTPException(409,'此任务使用另一执行引擎，尚不支持此授权')
    if not task.data.get('entity_ids'):raise HTTPException(409,'请先确定任务对象范围')

def _read(db,p,task,kind,identity,*,for_cloud=True):
    if kind=='data':
        row=db.scalar(select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.id==identity,Memory.data['type'].as_string()==DATA))
        if not row:raise HTTPException(404,'未找到所选数据')
        d=row.data
        try: payload={k:d[k] for k in DATA_FIELDS}
        except KeyError:raise HTTPException(409,'采集记录不完整')
        obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==d.get('observation_id')))
        reading=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==d.get('reading_id')))
        if (digest(payload)!=d.get('evidence_hash') or not obs or not reading or obs.entity_id!=d.get('entity_id') or obs.source_id!=d.get('source_id')
            or obs.data.get('reading_id')!=reading.id or reading.data.get('observation_id')!=obs.id or reading.data.get('automatic') is not True
            or reading.data.get('connection_id')!=d.get('connection_id') or reading.data.get('value')!=d.get('value')
            or reading.data.get('quality','unverified')!=d.get('quality') or reading.data.get('source_timestamp')!=d.get('source_timestamp')
            or (reading.data.get('read_at') or reading.data.get('received_at'))!=d.get('received_at')):
            raise HTTPException(409,'采集记录与来源不一致')
        status='设备读数待核实';threshold=None
    else:
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==identity,Job.kind==ALERT,Job.status=='internal'))
        if not row:raise HTTPException(404,'未找到所选线索')
        d=_checked_alert(db,p,row);payload={k:d[k] for k in DATA_FIELDS}
        obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==d['observation_id']))
        reading=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==d['reading_id']))
        if (not obs or not reading or obs.data.get('reading_id')!=reading.id or reading.data.get('automatic') is not True
            or reading.data.get('connection_id')!=d['connection_id'] or reading.data.get('source_timestamp')!=d['source_timestamp']
            or (reading.data.get('read_at') or reading.data.get('received_at'))!=d['received_at']):
            raise HTTPException(409,'线索时间或来源与原自动读数不一致')
        threshold=d['threshold'];status='阈值线索待核实'
    if payload['entity_id'] not in task.data['entity_ids']:raise HTTPException(403,'所选记录不在任务对象范围')
    source=published(db,p,payload['source_id'],'source')
    policy=source.data.get('data_policy',{})
    if for_cloud and (not isinstance(policy,dict) or ('cloud_allowed' in policy and policy['cloud_allowed'] is not True)):
        raise HTTPException(403,'来源云策略禁止或无法核对，不能发送云端')
    fields=('object_name','device_name','field_name','value','unit','quality','freshness','source_timestamp','received_at')
    return {'kind':kind,'id':row.id,**{k:payload[k] for k in fields},'status':status,'threshold':threshold,'notice':NOTICE,
        'freshness_note':'时效判断只反映接收时的情况，当前是否过期需重新核查',
        'evidence_hash':digest(payload),'source_hash':source.content_hash,
        **({'provenance':{k:payload[k] for k in ('entity_id','source_id','observation_id','reading_id','connection_id','policy_id','policy_generation','configuration_request_id')}} if not for_cloud else {})}

def selected(db,p,task,ids):
    check_task(db,p,task)
    if len(ids)<1 or len(ids)>20 or len({(x['kind'],x['id']) for x in ids})!=len(ids):raise HTTPException(422,'请选择 1 至 20 条不重复记录')
    return [_read(db,p,task,x['kind'],x['id']) for x in ids]

def model(db,p,identity):
    cap=published(db,p,identity,'foundation_model')
    if (cap.data.get('execution',{}).get('provider')!='deepseek'
        or cap.data.get('data_policy',{}).get('cloud_allowed') is not True
        or 'business' not in cap.data.get('allowed_agent_roles',[])):
        raise HTTPException(409,'请选择已发布、允许业务智能体云端调用的 DeepSeek 模型')
    return cap

def task_scope(task):
    return digest({'scope':scope_hash(task),'title':task.title,'owner_id':task.owner_id,'window':task.data.get('goal_window')})

def active(db,p,task,lock=False):
    query=select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal',Job.idempotency_key=='sensor-agent-task:'+task.id).execution_options(populate_existing=True)
    if lock:query=query.with_for_update(read=True)
    return db.scalar(query)

def read_authorized(db,p,task,model_profile_id=None):
    grant=active(db,p,task)
    if not grant or not grant.data.get('enabled'):return None
    # Serialize cancellation/revocation with the outbound model call. Both
    # changes lock the task first; keep these shared locks until the caller's
    # transaction ends, including after the planning checkpoint commits.
    locked=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==task.id).with_for_update(read=True).execution_options(populate_existing=True))
    if not locked or locked.status=='cancelled':raise HTTPException(409,'任务已取消，不能使用传感器证据')
    task=locked;check_task(db,p,task);grant=active(db,p,task,lock=True)
    if not grant or not grant.data.get('enabled'):raise HTTPException(409,'传感器证据授权已撤回')
    d=grant.data
    if d.get('actor_id')!=p.user_id or d.get('task_scope')!=task_scope(task):raise HTTPException(409,'任务或授权人已变化，请重新授权证据')
    if d.get('plan_id') and d['plan_id']!=task.data.get('current_plan_id'):
        raise HTTPException(409,'证据授权仅适用于原规划，请重新授权')
    if model_profile_id is not None and d['model_id']!=model_profile_id:raise HTTPException(409,'所选模型与证据授权不一致')
    if model_profile_id is not None and (not d.get('plan_id') or d['plan_id']!=task.data.get('current_plan_id')):
        raise HTTPException(409,'本次云端发送尚未绑定已确认的规划')
    cap=model(db,p,d['model_id'])
    if cap.content_hash!=d['model_hash']:raise HTTPException(409,'模型已变化，请重新授权证据')
    records=selected(db,p,task,d['selection'])
    if digest(records)!=d['records_hash']:raise HTTPException(409,'已授权证据或来源已变化，请重新核查')
    return {'authorization_id':grant.id,'model_id':cap.id,'records':records,'notice':NOTICE}

def claim_for_plan(db,p,task,plan_id,model_profile_id):
    grant=active(db,p,task)
    if not grant or not grant.data.get('enabled'):return
    # The caller already holds the Task write lock, which serializes changes.
    read_authorized(db,p,task)
    grant=active(db,p,task,lock=True)
    if grant.data.get('model_id')!=model_profile_id:raise HTTPException(409,'所选模型与证据授权不一致')
    if grant.data.get('plan_id'):
        raise HTTPException(409,'本次云端发送授权已用于原规划，请重新核对并授权')
    grant.data={**grant.data,'plan_id':plan_id};grant.version+=1
