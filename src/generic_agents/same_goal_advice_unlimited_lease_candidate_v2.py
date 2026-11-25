"""Disposable-database-only lease for the parked v2 send job.

A lease owns no provider invocation and grants no cloud dispatch. Existing or
expired originals are never replaced by a new token on an uncertain outcome.
"""
from datetime import timedelta
from sqlalchemy import event as sa_event, func, select
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_store_v2 as custody
from . import same_goal_advice_unlimited_start_candidate_v2 as start
from . import same_goal_advice_file_intent_candidate_v2 as current
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_start_safety_candidate_v2 as safety
from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
from .contracts import digest
from .models import Audit, Job, Plan, uid
from .relations import lock_current_actor
from .temporal import instant
from . import same_goal_advice_contracts_v302 as contracts

ACTION='same_goal_advice.unlimited_send_leased_v2'
SCHEMA='same-goal-advice-unlimited-send-lease-v2'
SECONDS=900
_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','job_id','start_audit_id','actor_id',
    'lease_token_hash','issued_at','expires_at','record_hash'}


def _unbind_commit_guard(db):
    prior=db.info.pop('v2_send_lease_commit_guard',None)
    if prior is not None and sa_event.contains(db,'before_commit',prior[1]):
        sa_event.remove(db,'before_commit',prior[1])


def _after_root(session,transaction):
    if transaction.parent is None:_unbind_commit_guard(session)


def _after_rollback(session,transaction):
    prior=session.info.get('v2_send_lease_commit_guard');current=prior[2] if prior else None
    while current is not None:
        if current is transaction:_unbind_commit_guard(session);return
        current=current.parent


def _bind_commit_guard(db,p,job_id,token):
    identity=(p.workspace_id,job_id,digest(token));prior=db.info.get('v2_send_lease_commit_guard')
    if prior is not None:
        store.need(prior[0]==identity and sa_event.contains(db,'before_commit',prior[1]));return
    def check(session):
        actual=session.scalar(select(Job.id).where(Job.id==job_id,Job.workspace_id==p.workspace_id,
            Job.status=='running',Job.lease_token==token,Job.lease_until>func.clock_timestamp()))
        store.need(actual==job_id,'原发送租约已到期；未提交租约')
    if not db.info.get('v2_send_lease_guard_cleanup_installed'):
        sa_event.listen(db,'after_transaction_end',_after_root)
        sa_event.listen(db,'after_soft_rollback',_after_rollback)
        db.info['v2_send_lease_guard_cleanup_installed']=True
    sa_event.listen(db,'before_commit',check)
    db.info['v2_send_lease_commit_guard']=(identity,check,db.get_nested_transaction() or db.get_transaction())


@store.checked
def verify(db,p,task,plan,send_job,start_event,event):
    value=event.data
    store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
        and event.action==ACTION and event.id==value['audit_id'] and event.object_id==plan.id
        and event.workspace_id==value['workspace_id']==p.workspace_id and value['task_id']==task.id
        and value['plan_id']==plan.id and value['job_id']==send_job.id and value['start_audit_id']==start_event.id
        and value['actor_id']==event.actor_id==start_event.actor_id and value['record_hash']==contracts.record_hash(value)
        and send_job.status=='running' and send_job.attempts==1 and type(send_job.lease_token) is str
        and value['lease_token_hash']==digest(send_job.lease_token)
        and event.created_at==instant(value['issued_at']) and send_job.lease_until==instant(value['expires_at'])
        and send_job.lease_until==event.created_at+timedelta(seconds=SECONDS))
    return value


@store.checked
def acquire(db,p,task_id,plan_id):
    guard.require_disposable_database(db);store._reader(db,p,write=True)
    task=store.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.id==store.existing._id(plan_id),Plan.workspace_id==p.workspace_id,
        Plan.task_id==task.id).with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None and plan.status=='awaiting_dispatch')
    prep,_=custody.read_started(db,p,task,plan)
    start_event=store._fresh(db,p,Audit,plan.data[start.POINTER]['audit_id'])
    store.need(start_event.actor_id==p.user_id)
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==plan.data[start.POINTER]['job_id'])
        .with_for_update().execution_options(populate_existing=True))
    preview=store._fresh(db,p,Audit,plan.data[start.POINTER]['preview_id'])
    store.need(preview.action==previews.ACTION)
    safety.lock_current(db,p,task,prep,preview.data['preview_record'])
    previews.verify_current_for_start(db,p,task,plan,prep,preview,started=True)
    current._check_current(db,p,task.id,prep['budget']['policy_ref'])
    now=db.scalar(select(func.clock_timestamp()))
    if job.status=='running':
        store.need(job.lease_until>now,'原发送租约已到期；保留原号等待核对')
        return {'job_id':job.id,'lease_token':job.lease_token,'expires_at':job.lease_until.isoformat()}
    store.need(job.status=='queued' and job.attempts==0)
    token=uid();end=now+timedelta(seconds=SECONDS)
    value={'schema':SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'job_id':job.id,'start_audit_id':start_event.id,'actor_id':p.user_id,'lease_token_hash':digest(token),
        'issued_at':now.isoformat(),'expires_at':end.isoformat()}
    value['record_hash']=contracts.record_hash(value)
    event=Audit(id=value['audit_id'],workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
        object_id=plan.id,created_at=now,data=value)
    previous_file=db.info.get('v2_file_intent_commit_guard');previous_goal=db.info.get('v2_start_goal_commit_guard')
    previous_lease=db.info.get('v2_send_lease_commit_guard')
    try:
        current._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'])
        safety.bind_commit_guard(db,p,task.id,prep,preview.data['preview_record'])
        with db.begin_nested():
            job.status='running';job.attempts=1;job.version+=1;job.lease_token=token;job.lease_until=end
            db.add(event);db.flush();_bind_commit_guard(db,p,job.id,token);custody.read_started(db,p,task,plan)
    except BaseException:
        if previous_file is None:current._unbind_commit_guard(db)
        if previous_goal is None:safety.unbind_commit_guard(db)
        if previous_lease is None:_unbind_commit_guard(db)
        raise
    return {'job_id':job.id,'lease_token':token,'expires_at':end.isoformat()}
