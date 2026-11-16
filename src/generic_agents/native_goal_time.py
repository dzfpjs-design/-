"""Persistent original-goal time checks; no model calls or sensor execution."""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .contracts import digest
from .models import Job,Observation,now
from .services import scoped,audit
from .temporal import instant

def timer(task):
    value=task.data.get('native_goal_timer')
    if digest(value)!=task.data.get('native_goal_timer_hash') and (value is not None or task.data.get('native_goal_timer_hash') is not None):
        raise HTTPException(409,'原目标到期检查摘要不一致')
    return value

def timeout(task):
    value=task.data.get('native_goal_timed_out')
    if value and digest(value)!=task.data.get('native_goal_timed_out_hash'):raise HTTPException(409,'原目标截止回执摘要不一致')
    return value

def cancel(db,p,task,reason):
    previous=timer(task)
    if previous:
        job=scoped(db,Job,previous['job_id'],p,True)
        if job.data.get('task_id')!=task.id or job.data.get('time_trigger')!=previous['trigger']:raise HTTPException(409,'原目标到期Job关联不一致')
        if job.status=='queued':job.status='cancelled';job.data={**job.data,'finished_at':now().isoformat(),'cancel_reason':reason}
    task.data={**task.data,'native_goal_timer':None,'native_goal_timer_hash':digest(None)}

def arm(db,p,task,evidence=None,generation='definition'):
    from .native_goals import KIND
    cancel(db,p,task,'已由原目标当前依据重新安排到期检查')
    spec=task.data['goal_spec'];current=now();dates=[]
    if spec.get('deadline'):dates.append((instant(spec['deadline']),'目标截止时间已到'))
    if evidence:
        records=evidence.get('evidence',[])+evidence.get('observation_evidence',[])
        if spec['evidence']['time']['mode']=='latest':
            from .observation_integrity import checked_report,CHECK_ERRORS
            for item in evidence.get('excluded_evidence',[]):
                if not item.get('observation_id'):continue
                obs=scoped(db,Observation,item['observation_id'],p)
                try:
                    report=checked_report(db,p,obs)
                    if any(report['attributes'].get(key) is not None for key in spec['required_fields']):records.append({'observed_at':obs.observed_at.isoformat()})
                except CHECK_ERRORS:continue
        for record in records:
            if spec['evidence']['time']['mode']=='latest' and record.get('observed_at'):
                dates.append((instant(record['observed_at'])+timedelta(seconds=spec['evidence']['time']['max_age_seconds'],microseconds=1),'原目标资料有效期已到'))
            if record.get('valid_until'):dates.append((instant(record['valid_until'])+timedelta(microseconds=1),'原目标档案有效期已到'))
    future=[item for item in dates if item[0]>current]
    if not future:return
    due,reason=min(future,key=lambda item:item[0]);context_hash=digest(task.data['native_goal_context'])
    frozen={'schema':'native-goal-time-v1','due_at':due.isoformat(),'reason':reason,'context_hash':context_hash,'generation':generation}
    key='native-goal-time:'+task.id+':'+digest(frozen)
    data={'task_id':task.id,'actor_id':p.user_id,'context_hash':context_hash,'time_trigger':frozen,'time_trigger_hash':digest(frozen)}
    db.execute(insert(Job).values(workspace_id=p.workspace_id,kind=KIND,idempotency_key=key,available_at=due,data=data).on_conflict_do_nothing(index_elements=['workspace_id','idempotency_key']))
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    value={'job_id':job.id,'trigger':frozen}
    task.data={**task.data,'native_goal_timer':value,'native_goal_timer_hash':digest(value)}

def admitted(task,job):
    trigger=job.data.get('time_trigger')
    if not trigger:return True
    if digest(trigger)!=job.data.get('time_trigger_hash') or instant(trigger['due_at'])!=job.available_at:raise HTTPException(409,'原目标到期触发依据不一致')
    saved=timer(task)
    if not saved or saved['job_id']!=job.id or saved['trigger']!=trigger:return False
    if now()<instant(trigger['due_at']):raise HTTPException(409,'原目标到期检查时点尚未到')
    return True

def expire(db,p,task):
    deadline=task.data['goal_spec'].get('deadline')
    if not deadline or now()<instant(deadline):return False
    if timeout(task):return True
    cancel(db,p,task,'业务目标已到截止时间')
    from .native_goal_acquisitions import stop_coordination
    stop_coordination(db,p,task,'原业务目标已到截止时间')
    from .native_step_execution import stop_all
    stop_all(db,p,task,'原业务目标已到截止时间')
    from .native_goal_queries import stop_all as stop_queries
    stop_queries(db,p,task,'原业务目标已到截止时间')
    value={'schema':'native-goal-timeout-v1','deadline':deadline,'at':now().isoformat(),'context_hash':digest(task.data['native_goal_context']),
        'reason':'目标截止时间已到，原证据和人工说明保留，不再继续旧协调','physical_state_verified':False}
    task.data={**task.data,'native_goal_timed_out':value,'native_goal_timed_out_hash':digest(value),'native_goal_error':None}
    audit(db,p,'native_goal.timed_out',task.id,{'deadline':deadline})
    return True

def view(db,p,task):
    value=timer(task)
    if not value:return None
    job=scoped(db,Job,value['job_id'],p)
    if job.data.get('time_trigger')!=value['trigger']:raise HTTPException(409,'原目标到期Job关联不一致')
    return {'due_at':value['trigger']['due_at'],'reason':value['trigger']['reason'],'status':job.status}
