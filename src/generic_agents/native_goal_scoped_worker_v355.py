"""Task-bound local Goal consumer: no graph, cloud or device dispatch.

It selects the current ordinary check, the actual current due timer, and
independently issued evaluation candidates/promotions. Older ordinary triggers
are left untouched. All authority and lease checks remain in the real worker.
"""
import signal,threading
from contextlib import nullcontext
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select,and_,or_,func,false
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from . import models as m
from .contracts import digest
from .db import engine
from .security import Principal
from .native_goals import KIND,saved_context,guard
from . import goal_evaluation_maturity_v299 as maturity
from .goal_evaluation_provider_v288 import _goal_job,job_ready
from .native_goal_time import timer,admitted
from .workflow_task_consumer_v265 import _created,_principal


def _uuid(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def scope(workspace,instance,task):
    if not all(_uuid(x) for x in (workspace,instance,task)):
        raise ValueError('目标处理器必须指定标准工作区、实例和任务编号')


def _available():
    return and_(m.Job.available_at<=func.clock_timestamp(),or_(m.Job.status=='queued',
        and_(m.Job.status=='running',or_(m.Job.lease_until.is_(None),m.Job.lease_until<func.clock_timestamp()))))


def _pin(task):
    context=task.data.get('native_goal_context');pin=task.data.get('native_goal_check_job_pin')
    if type(context) is not dict or digest(context)!=task.data.get('native_goal_context_hash'):
        raise HTTPException(409,'原目标上下文无法核对；原任务保留')
    if pin is None:return None
    if (type(pin) is not dict or set(pin)!={'job_id','idempotency_key','context_hash','actor_id'}
        or digest(pin)!=task.data.get('native_goal_check_job_pin_hash') or pin.get('context_hash')!=digest(context)
        or not _uuid(pin.get('job_id')) or not _uuid(pin.get('actor_id'))):
        raise HTTPException(409,'原目标检查请求无法核对；原任务保留')
    return pin


def _rows(db,workspace,task,pin,clock_timer,after,*,lane=None,horizon=None,limit=50):
    ids=[x for x in (pin.get('job_id') if pin else None,clock_timer.get('job_id') if clock_timer else None) if x]
    candidate_audit=select(m.Audit.id).where(m.Audit.workspace_id==workspace,m.Audit.action==maturity.QUEUED,
        m.Audit.object_id==m.Job.id,m.Audit.data['task_id'].as_string()==task.id).correlate(m.Job).exists()
    promoter_audit=select(m.Audit.id).where(m.Audit.workspace_id==workspace,m.Audit.action==maturity.OUTBOX,
        m.Audit.data['promoter_job_id'].as_string()==m.Job.id,m.Audit.data['task_id'].as_string()==task.id).correlate(m.Job).exists()
    from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356 as AutoOrigin
    from .goal_evaluation_append_contracts_v297 import KIND as APPEND_KIND
    from .auto_evaluation_policy_api_v356 import automatic_runner_available
    auto_origin=(select(AutoOrigin.id).where(AutoOrigin.workspace_id==workspace,AutoOrigin.task_id==task.id,
        AutoOrigin.job_id==m.Job.id).correlate(m.Job).exists() if automatic_runner_available() else false())
    candidate_prefix='native-goal:'+task.id+':'+task.data['native_goal_context_hash']+':candidate-v299:'
    query=select(m.Job.id,m.Job.available_at).where(m.Job.workspace_id==workspace,m.Job.kind.in_([KIND,maturity.KIND,APPEND_KIND]),_available(),
        or_(m.Job.id.in_(ids),candidate_audit,promoter_audit,
            and_(m.Job.kind==APPEND_KIND,auto_origin),
            and_(m.Job.kind==KIND,m.Job.status=='running',m.Job.data['task_id'].as_string()==task.id),
            and_(m.Job.kind==KIND,m.Job.idempotency_key.startswith(candidate_prefix))))
    if lane is not None:
        ordinary_id=pin.get('job_id') if pin else None
        timer_id=clock_timer.get('job_id') if clock_timer else None
        if lane=='recovery_timer':
            query=query.where(m.Job.kind==KIND,or_(m.Job.status=='running',m.Job.id==timer_id))
        elif lane=='ordinary':
            query=query.where(m.Job.kind==KIND,m.Job.status=='queued',m.Job.id==ordinary_id,
                ~or_(candidate_audit,m.Job.idempotency_key.startswith(candidate_prefix)))
        elif lane=='candidate':
            query=query.where(m.Job.kind==KIND,m.Job.status=='queued',
                or_(candidate_audit,m.Job.idempotency_key.startswith(candidate_prefix)))
        elif lane=='promotion':query=query.where(m.Job.kind==maturity.KIND)
        elif lane=='auto_append':query=query.where(m.Job.kind==APPEND_KIND)
        else:raise ValueError('invalid scoped scheduling lane')
    if horizon is not None:query=query.where(m.Job.created_at<=horizon,m.Job.available_at<=horizon)
    if after:query=query.where(or_(m.Job.available_at>after[0],and_(m.Job.available_at==after[0],m.Job.id>after[1])))
    return list(db.execute(query.order_by(m.Job.available_at,m.Job.id).limit(limit)))


def _prove(db,workspace,instance,task_id,identity):
    from .goal_evaluation_append_contracts_v297 import KIND as APPEND_KIND
    task=db.scalar(select(m.Task).where(m.Task.workspace_id==workspace,m.Task.id==task_id))
    if task is None:raise HTTPException(404,'原业务任务不存在')
    release,business=_created(db,workspace,instance,task,None)
    job=db.scalar(select(m.Job).where(m.Job.workspace_id==workspace,m.Job.id==identity))
    if job is None or job.data.get('task_id')!=task.id or job.kind not in {KIND,maturity.KIND,APPEND_KIND}:
        raise HTTPException(409,'后台检查与原任务范围不一致')
    try:p=_principal(db,workspace,job.data['actor_id'])
    except HTTPException:
        # A revoked actor may not authorize new work, but an expired original
        # lease still needs the ordinary worker's failure/recovery handling.
        if job.status!='running' or not _uuid(job.data.get('actor_id')):raise
        p=Principal(job.data['actor_id'],workspace,'viewer','goal-scoped-recovery')
    context=saved_context(db,p,task)
    if context!=task.data['native_goal_context'] or digest(context)!=task.data['native_goal_context_hash']:
        raise HTTPException(409,'原目标已变化；检查请求保留')
    pin=_pin(task);clock_timer=timer(task)
    if job.kind==KIND:
        _goal_job(task,job);candidate=maturity.candidate_scope(db,p,task,job)
        if candidate is None:
            if job.data.get('time_trigger'):
                if not clock_timer or clock_timer.get('job_id')!=job.id or not admitted(task,job):
                    if job.status!='running':raise HTTPException(409,'原目标定时检查已被替代')
                    family,proof='superseded_recovery',digest({'job_id':job.id,'context_hash':job.data['context_hash']})
                else:family,proof='timer',digest(clock_timer)
            elif not pin or pin['job_id']!=job.id or pin['idempotency_key']!=job.idempotency_key or pin['actor_id']!=job.data['actor_id']:
                if job.status!='running':raise HTTPException(409,'原目标检查已被新请求替代')
                family,proof='superseded_recovery',digest({'job_id':job.id,'context_hash':job.data['context_hash']})
            else:family,proof='ordinary',digest(pin)
        else:family,proof='candidate',digest(candidate)
        if not job_ready(db,p,job):return None
    elif job.kind==APPEND_KIND:
        # Scope proof is necessary before claim; the actual handler still
        # checks lease, live authorization and original evaluation again.
        from .auto_evaluation_policy_api_v356 import automatic_runner_available
        if not automatic_runner_available():raise HTTPException(409,'自动评价处理链尚未接通')
        from .goal_evaluation_append_store_v297 import runner_authority
        from .auto_evaluation_trigger_v356 import authority as auto_authority
        task=db.scalar(select(m.Task).where(m.Task.workspace_id==workspace,m.Task.id==task_id).with_for_update())
        checked=runner_authority(db,p,job,require_lease=False)
        if checked['task'].id!=task.id or checked['job'].id!=job.id or auto_authority(db,p,task,job) is None:
            raise HTTPException(409,'自动评价来源无法核对；原请求保留')
        family,proof='auto_append',None
    else:
        parent=db.scalar(select(m.Job).where(m.Job.workspace_id==workspace,m.Job.id==job.data.get('goal_check_job_id')))
        if parent is None or parent.status!='succeeded' or parent.data.get('task_id')!=task.id:
            raise HTTPException(409,'原评价检查尚未完成；候选保留')
        maturity._outbox(db,p,task,parent,job)
        family,proof='promotion',None
    # The pre-claim check is not an authorization grant: process_once repeats
    # the live approval, permissions, pinned sources and lease checks.
    # An expired running job must enter ordinary recovery even if approval has
    # since closed; that path records the interruption rather than resuming IO.
    if job.status=='queued':guard(db,p,task,allow_expired=True,lock_sources=False)
    return job.kind,family,proof


def tick(workspace,instance,task_id,stop=None,after=None):
    from .worker import process_once
    scope(workspace,instance,task_id);stop=stop if stop is not None else threading.Event()
    if stop.is_set():return {'processed':0,'waiting':0,'blocked':0,'after':after}
    with Session(engine()) as db:
        task=db.scalar(select(m.Task).where(m.Task.workspace_id==workspace,m.Task.id==task_id))
        if task is None:raise HTTPException(404,'原业务任务不存在')
        _created(db,workspace,instance,task,None);pin=_pin(task);clock_timer=timer(task)
        rows=_rows(db,workspace,task,pin,clock_timer,after)
    result={'processed':0,'waiting':0,'blocked':0,'after':None if not rows else after}
    for identity,available_at in rows:
        if stop.is_set():break
        result['after']=(available_at,identity)
        try:
            with Session(engine()) as db:proof=_prove(db,workspace,instance,task_id,identity)
            if proof is None:result['waiting']+=1;continue
            kind,family,value=proof
            from .native_goal_scoped_authority_v355 import selected
            context=selected(workspace,task_id,identity,family,value) if kind==KIND else nullcontext()
            with context:
                if process_once(job_id=identity,kinds=[kind],deliver=False,workspace_id=workspace,_stop_event=stop):result['processed']+=1
        except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
            result['blocked']+=1
    return result


def scan_once(workspace,instance,task_id,after=None):
    """Scan only this task; disabled gate performs no database operation."""
    scope(workspace,instance,task_id)
    from .auto_evaluation_policy_api_v356 import automatic_runner_available
    if not automatic_runner_available():return {'queued':0,'blocked':0,'unknown':0,'unrecorded':0,'superseded':0,'after':None}
    from .auto_evaluation_scanner_v356 import tick as scan
    return scan(workspace,instance,task_id,after=after)


def run(workspace,instance,task_id,*,fair=False):
    scope(workspace,instance,task_id);stop=threading.Event();previous={};after=None;scan_after=None
    def halt(*_):stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM):previous[sig]=signal.getsignal(sig);signal.signal(sig,halt)
    print('目标处理器已启动：仅检查指定业务任务，不执行其他图节点或设备命令',flush=True)
    try:
        while not stop.is_set():
            try:
                if fair:
                    from .native_goal_fair_worker_v358 import fair_tick
                    result=fair_tick(workspace,instance,task_id,stop)
                else:
                    result=tick(workspace,instance,task_id,stop,after=after);after=result['after']
                queued=0;scan_failed=False
                if not stop.is_set():
                    try:
                        scan=scan_once(workspace,instance,task_id,after=scan_after)
                        scan_after=scan['after'];queued=scan['queued']
                    except (DBAPIError,HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                        scan_failed=True
                        print('新资料自动评价扫描暂不可核对；原接收及任务保留',flush=True)
                if fair:
                    stop.wait(1 if result['retry_mode']=='scheduled_rescan' else 0.05)
                elif not result['processed'] and not queued:stop.wait(2 if result['blocked'] or scan_failed else 1)
            except (DBAPIError,HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError,RuntimeError):
                print('原目标、权限或数据库暂不可核对；请求保留，未扩大处理范围',flush=True);stop.wait(2)
        return 0
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
        print('目标处理器已停止领取；原任务及回执保留',flush=True)


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--workspace-id',required=True);p.add_argument('--instance-id',required=True);p.add_argument('--task-id',required=True)
    p.add_argument('--fair',action='store_true',help='启用已迁移的任务范围公平调度')
    args=p.parse_args();return run(args.workspace_id,args.instance_id,args.task_id,fair=args.fair)

if __name__=='__main__':raise SystemExit(main())
