"""Durable local state reconciliation; never inference, capture, or physical truth."""
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from .contracts import digest
from .models import Job, Entity, DomainInstance, InstanceRelease, Observation, now
from .services import scoped, audit

KIND = 'refresh_object_state'
SCHEMA = 'local-state-refresh-v1'

def lock(db, workspace, entity):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':int(digest({'state_refresh':workspace,'entity':entity})[:16],16)-(1<<63)})

def target(db, p, entity):
    from .instance_domain import current_context
    context=current_context(db,p,entity)
    if context is None:return None
    instance,release,definition=context
    return {'entity_id':entity.id, 'entity_version':entity.version, 'instance_id':instance.id, 'release_id':release.id,
        'object_definition_hash':digest(definition)}

def checked(job):
    request = job.data.get('state_request')
    if not isinstance(request,dict) or request.get('schema') != SCHEMA or digest(request) != job.data.get('state_request_hash') or request.get('authorized_by') != job.data.get('actor_id'):
        raise HTTPException(409,'后台状态核对范围与保存摘要不一致，原记录保留')
    result = job.data.get('state_result')
    if result is not None and digest(result) != job.data.get('state_result_hash'):
        raise HTTPException(409,'后台状态核对回执不一致，原记录保留')
    return request

def enqueue(db, p, entity, reason, key, *, observation=None, available_at=None, due_state_hash=None):
    frozen = target(db,p,entity)
    if not frozen:return None
    lock(db,p.workspace_id,entity.id)
    request = {'schema':SCHEMA, **frozen, 'authorized_by':p.user_id, 'reason':reason, 'trigger_observation_id':observation.id if observation else None,
        'trigger_observation_version':observation.version if observation else None, 'due_state_hash':due_state_hash}
    content = {'actor_id':p.user_id,'state_request':request,'state_request_hash':digest(request)}
    key = 'state-refresh:'+key
    db.execute(insert(Job).values(workspace_id=p.workspace_id,kind=KIND,idempotency_key=key,
        available_at=available_at or now(),data=content).on_conflict_do_nothing(index_elements=['workspace_id','idempotency_key']))
    job = db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    # A repeated trigger must never substitute another subject or authorization.
    if checked(job) != request or job.data['actor_id'] != p.user_id:raise HTTPException(409,'原后台核对请求不能更换对象或发起人')
    return job

def source_changed(db, p, observation, reason='新登记处理结果已保存'):
    from .original_acquisition_reception_v127 import hold
    if hold(db,p,observation,remember=True):return None
    entity = scoped(db,Entity,observation.entity_id,p)
    return enqueue(db,p,entity,reason,observation.id+':'+str(observation.version)+':'+digest(reason)[:12],observation=observation)

def process(db, p, job):
    request = checked(job)
    entity = scoped(db,Entity,request['entity_id'],p)
    lock(db,p.workspace_id,entity.id)
    from .instance_api import active
    active(db,p,request['instance_id'])
    current = target(db,p,entity)
    keys=['entity_id','entity_version','instance_id','release_id']
    if 'object_definition_hash' in request:keys.append('object_definition_hash')
    if current is None or {k:current.get(k) for k in keys} != {k:request[k] for k in keys}:
        raise HTTPException(409,'对象、实例或启用版本已改变，后台核对已停止；原资料保留')
    oid = request.get('trigger_observation_id')
    observation = scoped(db,Observation,oid,p) if oid else None
    if observation and observation.entity_id != entity.id:raise HTTPException(409,'原触发登记已不属于此对象')
    from .perception import object_state
    state = object_state(db,p,entity.id)
    result = {'schema':SCHEMA,'entity_id':entity.id,'instance_id':request['instance_id'],
        'state_hash':state['state_hash'],'state_version':state['state_version'],'status':state['status'],
        'checked_at':state['checked_at'],'current_observation_version':observation.version if observation else None,
        'trigger_reason':request['reason'],'cloud_calls':0,'device_actions':0,'entity_updated':False,'physical_state_verified':False}
    job.data = {**job.data,'state_result':result,'state_result_hash':digest(result)}
    checked_at = datetime.fromisoformat(state['checked_at']);expiry=[]
    for value in state['fields'].values():
        if value['conflicted']:continue
        due = datetime.fromisoformat(value['observed_at']) + timedelta(seconds=value['freshness_seconds'],microseconds=1)
        if due > checked_at:expiry.append(due)
    queued=None
    if expiry:
        due=min(expiry)
        queued=enqueue(db,p,entity,'登记依据时效到期','expiry:'+entity.id+':'+state['state_hash']+':'+job.id,
            available_at=due,due_state_hash=state['state_hash'])
        job.data={**job.data,'next_expiry_job_id':queued.id,'next_expiry_at':due.isoformat()}
    # Keep this durable expiry on lease recovery; cancel only superseded queued checks.
    for old in db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='queued',
        Job.id!=job.id,Job.data['state_request']['entity_id'].as_string()==entity.id,
        Job.data['state_request']['reason'].as_string()=='登记依据时效到期').with_for_update(skip_locked=True)):
        if queued and old.id==queued.id:continue
        checked(old);old.status='cancelled';old.data={**old.data,'finished_at':now().isoformat(),'cancel_reason':'已由新的本地核对重新安排到期检查'}
    audit(db,p,'object_state.background_checked',entity.id,{'job_id':job.id,'state_version':state['state_version'],'state_hash':state['state_hash']})


def view(job):
    try:request=checked(job);issue=None
    except HTTPException as error:request={};issue=error.detail
    return {'id':job.id,'status':'blocked_integrity' if issue else job.status,'available_at':job.available_at.isoformat(),'created_at':job.created_at.isoformat(),
        'reason':request.get('reason'),'result':job.data.get('state_result') if not issue else None,
        'error':issue or job.data.get('error'),'cancel_reason':job.data.get('cancel_reason')}

def overview(db,p,entity_id):
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,
        Job.data['state_request']['entity_id'].as_string()==entity_id).order_by(Job.created_at.desc(),Job.id.desc()).limit(30)))
    return {'latest':next((view(j) for j in rows if j.status!='cancelled' and not (j.status=='queued' and j.data.get('state_request',{}).get('reason')=='登记依据时效到期')),None),
        'next_expiry':next((view(j) for j in rows if j.status in ['queued','running'] and j.data.get('state_request',{}).get('reason')=='登记依据时效到期'),None),
        'history':[view(j) for j in rows],'history_limit':30,'cloud_calls':0,'device_actions':0}
