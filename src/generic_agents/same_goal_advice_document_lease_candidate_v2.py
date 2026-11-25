"""Isolated experiment: a fenced two-phase wrapper around v2 document work.

No route or general worker installs this module. A real data-role session is
required at every phase; this does not provide durable offline delegation.
"""
import copy
from datetime import timedelta
from uuid import UUID, uuid4
from sqlalchemy import event, func, select
from . import leases
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_prepare_document_candidate_v2 as document
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as v2
from .contracts import digest
from .models import Audit, Job, Plan, Task
from .security import Principal

KIND='advice_v2_document_fence_test'
SCHEMA='same-goal-advice-document-fenced-candidate-v2'
REGISTERED='same_goal_advice.document_fence_registered_v2'


def _actor(db,p):
    old.need(type(p) is Principal and p.role in {'admin','operator'})
    try: UUID(p.session_id)
    except (ValueError,TypeError,AttributeError): old.need(False,'需要资料岗位的有效登录会话')
    v2._lock_current_reader(db,p)


def _bind_session_guard(db,p):
    identity=(p.workspace_id,p.user_id,p.role,p.session_id)
    existing=db.info.get('v2_document_session_guard')
    if existing is not None:
        old.need(existing[0]==identity and event.contains(db,'before_commit',existing[1]))
        return
    def check_session_before_commit(session): _actor(session,p)
    event.listen(db,'before_commit',check_session_before_commit)
    db.info['v2_document_session_guard']=(identity,check_session_before_commit)


def _control(db,p,control_id,*,lock=True):
    q=select(Job).where(Job.workspace_id==p.workspace_id,Job.id==control_id,Job.kind==KIND)
    if lock:q=q.with_for_update()
    row=db.scalar(q.execution_options(populate_existing=True));old.need(row is not None)
    d=row.data
    old.need(type(d) is dict and set(d)=={'schema','actor_id','target'} and d['schema']==SCHEMA
        and d['actor_id']==p.user_id and type(d['target']) is dict
        and set(d['target'])=={'task_id','job_id','plan_id','preparation_hash'}
        and row.idempotency_key=='advice-v2-fence:'+d['target']['job_id']
        and row.available_at==guard.UNAVAILABLE_TO_GENERAL_WORKERS)
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==row.id,Audit.action==REGISTERED)))
    old.need(len(events)==1 and events[0].actor_id==p.user_id and events[0].data=={'schema':SCHEMA,'control_hash':digest(d)})
    return row


@old.checked
def register(db,p,task_id,job_id):
    guard.require_disposable_database(db);_actor(db,p);_bind_session_guard(db,p)
    # Existing controls are locked before the Task, matching process/settle.
    # On first creation no control exists. Recheck with NOWAIT under Task lock
    # to avoid waiting in reverse order if a concurrent creator has committed.
    key='advice-v2-fence:'+job_id
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).with_for_update())
    if row: row=_control(db,p,row.id)
    task=old.existing._task(db,p,task_id,lock=True)
    if row is None:
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).with_for_update(nowait=True))
        if row: row=_control(db,p,row.id)
    job=db.scalar(select(Job).where(Job.id==job_id,Job.workspace_id==p.workspace_id).with_for_update())
    old.need(job is not None and job.kind==v2.PREPARE_KIND)
    plan=db.scalar(select(Plan).where(Plan.id==job.data['plan_id'],Plan.task_id==task.id,
        Plan.workspace_id==p.workspace_id).with_for_update());old.need(plan is not None)
    prep,checked=(v2.read_prepared(db,p,task,plan) if job.status=='succeeded' else v2.read_queued(db,p,task,plan))
    old.need(checked.id==job.id and prep['data_actor_id']==p.user_id)
    target={'task_id':task.id,'job_id':job.id,'plan_id':plan.id,'preparation_hash':prep['record_hash']}
    if row:
        row=_control(db,p,row.id);old.need(row.data['target']==target);return row.id
    old.need(job.status=='queued')
    row=Job(workspace_id=p.workspace_id,kind=KIND,status='queued',attempts=0,
        available_at=guard.UNAVAILABLE_TO_GENERAL_WORKERS,idempotency_key=key,
        data={'schema':SCHEMA,'actor_id':p.user_id,'target':target})
    db.add(row);db.flush()
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,object_id=row.id,action=REGISTERED,
        data={'schema':SCHEMA,'control_hash':digest(row.data)}));db.flush();return row.id


@old.checked
def claim(db,p,control_id):
    guard.require_disposable_database(db);_actor(db,p);_bind_session_guard(db,p);row=_control(db,p,control_id)
    # Recheck the original under the same Task lock used by cancellation.
    # A durable cancellation receipt makes read_queued fail closed.
    target=row.data['target'];task=old.existing._task(db,p,target['task_id'],lock=True)
    original=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==target['job_id'])
        .with_for_update().execution_options(populate_existing=True))
    plan=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id,
        Plan.id==target['plan_id']).with_for_update().execution_options(populate_existing=True))
    old.need(original is not None and plan is not None)
    prep,checked=(v2.read_prepared(db,p,task,plan) if original.status=='succeeded'
        else v2.read_queued(db,p,task,plan))
    old.need(checked.id==original.id and prep['record_hash']==target['preparation_hash']
        and prep['data_actor_id']==p.user_id)
    if original.status!='succeeded':
        from .same_goal_advice_file_intent_candidate_v2 import _check_current, _bind_commit_guard
        _check_current(db,p,task.id,prep['budget']['policy_ref'])
        _bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'])
    clock=db.scalar(select(func.clock_timestamp()))
    old.need(row.status=='queued' or (row.status=='running' and row.lease_until is not None and row.lease_until<clock),
        '处理任务尚未到可领取状态')
    if row.status=='queued':
        old.need(row.attempts==0 and row.lease_token is None and row.lease_until is None)
    else:
        old.need(row.attempts>=1 and bool(row.lease_token))
    token=str(uuid4());row.status='running';row.lease_token=token
    row.lease_until=clock+timedelta(seconds=leases.LEASE_SECONDS);row.attempts+=1;row.version+=1
    db.flush();return token


def process(db,p,control_id,token):
    """Caller attaches v1's lease fence before beginning this transaction."""
    guard.require_disposable_database(db);_actor(db,p);_bind_session_guard(db,p)
    old.need(leases.fenced(db) and leases.ownership(db)=={'job_id':control_id,'lease_token':token})
    row=_control(db,p,control_id);old.need(row.status=='running' and row.lease_token==token
        and row.lease_until>db.scalar(select(func.clock_timestamp())))
    target=copy.deepcopy(row.data['target']); result=document.process(db,p,target['task_id'],target['job_id'])
    old.need(result['plan_id']==target['plan_id'] and result['job_id']==target['job_id'])
    plan=db.get(Plan,target['plan_id']);old.need(plan.data['preparation_snapshot']['record_hash']==target['preparation_hash'])
    return result


@old.checked
def settle(db,p,control_id,token):
    guard.require_disposable_database(db);_actor(db,p);row=_control(db,p,control_id)
    old.need(row.status=='running' and row.lease_token==token and row.lease_until>db.scalar(select(func.clock_timestamp())))
    t=row.data['target'];task=old.existing._task(db,p,t['task_id'],lock=True)
    plan=db.get(Plan,t['plan_id']);old.need(plan is not None)
    prep,job=v2.read_prepared(db,p,task,plan)
    old.need(prep['data_actor_id']==p.user_id and prep['record_hash']==t['preparation_hash'] and job.id==t['job_id'])
    _actor(db,p)
    old.need(row.lease_until>db.scalar(select(func.clock_timestamp())), '租约已过期；原回执保留，等待重新领取')
    row.status='succeeded';row.lease_token=None;row.lease_until=None;row.version+=1;db.flush()
    return {'control_id':row.id,'job_id':job.id}
