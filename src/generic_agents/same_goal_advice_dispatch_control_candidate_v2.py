"""One-claim local send-control candidate; never dispatches a model request.

There is deliberately no route, scheduler, or production database support.
A worker that owns the returned lease must separately revalidate the user
confirmation, current sources, reservation, intent, and one-shot permit.
"""
import copy
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import event as sa_event, func, select

from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as custody
from .contracts import digest
from .models import Audit, Job, uid
from .security import Principal
from .temporal import instant

KIND='advice_v2_single_dispatch_control'
ACTION='same_goal_advice.single_dispatch_control_candidate_v2'
SCHEMA='same-goal-advice-single-dispatch-control-v2'
LEASE_SECONDS=900
FIELDS={'schema','target','actor_id','control_id'}
TARGET_FIELDS={'workspace_id','task_id','plan_id','job_id','delegation_id','delegation_hash'}


def _control(db, control_id, *, lock):
    try: old.need(str(UUID(control_id))==control_id)
    except (ValueError,TypeError,AttributeError): old.need(False)
    row=db.scalar(select(Job).where(Job.id==control_id).execution_options(populate_existing=True))
    old.need(row is not None and row.kind==KIND and type(row.data) is dict and set(row.data)==FIELDS)
    data=copy.deepcopy(row.data)
    target=data['target'];old.need(type(target) is dict and set(target)==TARGET_FIELDS)
    old.need(data['schema']==SCHEMA and data['control_id']==row.id and target['workspace_id']==row.workspace_id
        and row.idempotency_key=='advice-v2-dispatch-control:'+target['delegation_id']
        and row.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS)
    checked=delegation.verify_original(db,row.workspace_id,target['task_id'],target['plan_id'],target['delegation_id'])
    old.need(checked['job_id']==target['job_id'] and checked['actor_id']==data['actor_id'])
    query=select(Job).where(Job.id==control_id,Job.workspace_id==row.workspace_id,Job.kind==KIND)
    if lock: query=query.with_for_update()
    current=db.scalar(query.execution_options(populate_existing=True))
    old.need(current is not None and current.data==data and current.idempotency_key==row.idempotency_key)
    grant=db.scalar(select(Audit).where(Audit.workspace_id==row.workspace_id,Audit.id==target['delegation_id'],
        Audit.action==delegation.AUTHORIZED).with_for_update(read=True))
    old.need(grant is not None and digest(grant.data)==target['delegation_hash'])
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==row.workspace_id,
        Audit.object_id==row.id,Audit.action==ACTION).limit(old.MAX_ROWS+1).with_for_update(read=True)))
    old.need(len(events)==1 and events[0].actor_id==data['actor_id']
        and events[0].data=={'schema':SCHEMA,'control_hash':digest(data)})
    return current,checked

def _remove_guard(db):
    prior=db.info.pop('v2_dispatch_control_guard',None)
    if prior is not None and sa_event.contains(db,'before_commit',prior[1]):sa_event.remove(db,'before_commit',prior[1])


def _after_transaction(db,transaction):
    if transaction.parent is None:_remove_guard(db)


def _after_rollback(db,transaction):
    prior=db.info.get('v2_dispatch_control_guard');current=prior[2] if prior else None
    while current is not None:
        if current is transaction:_remove_guard(db);return
        current=current.parent


def _bind_guard(db,workspace,control_id,token):
    identity=(workspace,control_id,token);prior=db.info.get('v2_dispatch_control_guard')
    if prior is not None:
        old.need(prior[0]==identity and sa_event.contains(db,'before_commit',prior[1]));return
    def check(session):
        found=session.scalar(select(Job.id).where(Job.workspace_id==workspace,Job.id==control_id,Job.kind==KIND,
            Job.status=='running',Job.attempts==1,Job.lease_token==token,Job.lease_until>func.clock_timestamp()))
        old.need(found==control_id,'处理权限已变化，未提交')
    if not db.info.get('v2_dispatch_control_cleanup'):
        sa_event.listen(db,'after_transaction_end',_after_transaction)
        sa_event.listen(db,'after_soft_rollback',_after_rollback)
        db.info['v2_dispatch_control_cleanup']=True
    sa_event.listen(db,'before_commit',check)
    db.info['v2_dispatch_control_guard']=(identity,check,db.get_nested_transaction() or db.get_transaction())



@old.checked
def enqueue(db,p,task_id,plan_id,delegation_id):
    """Called only in the confirmed user's transaction; no outbound authority."""
    guard.require_disposable_database(db)
    old.need(type(p) is Principal)
    delegation.inspect_authorization(db,p,task_id,plan_id,delegation_id)
    checked=delegation.verify_original(db,p.workspace_id,task_id,plan_id,delegation_id)
    old.need(checked['actor_id']==p.user_id and checked['session_id']==p.session_id)
    grant=db.get(Audit,delegation_id);old.need(grant is not None)
    key='advice-v2-dispatch-control:'+delegation_id
    rows=old._rows(db,p,Job,Job.idempotency_key==key)
    old.need(len(rows)<=1)
    if rows:
        row,_=_control(db,rows[0].id,lock=True)
        waiting = row.status=='queued' and row.attempts==0 and row.lease_token is None and row.lease_until is None
        return {'control_id':row.id,'state':'waiting' if waiting else 'needs_attention'}
    control_id=uid();data={'schema':SCHEMA,'target':{'workspace_id':p.workspace_id,'task_id':task_id,'plan_id':plan_id,
        'job_id':checked['job_id'],'delegation_id':delegation_id,'delegation_hash':digest(grant.data)},
        'actor_id':p.user_id,'control_id':control_id}
    row=Job(id=control_id,workspace_id=p.workspace_id,kind=KIND,status='queued',attempts=0,
        idempotency_key=key,available_at=custody.UNAVAILABLE_TO_GENERAL_WORKERS,data=data)
    db.add(row);db.add(Audit(workspace_id=p.workspace_id,object_id=control_id,actor_id=p.user_id,
        action=ACTION,data={'schema':SCHEMA,'control_hash':digest(data)}));db.flush()
    return {'control_id':control_id,'state':'waiting'}


@old.checked
def claim(db,control_id):
    """A single isolated worker may claim once; expired controls never reissue."""
    guard.require_disposable_database(db)
    row,checked=_control(db,control_id,lock=True)
    old.need(row.status=='queued' and row.attempts==0 and row.lease_token is None and row.lease_until is None,
        '原申请已开始或需要核对，不能重复处理')
    now=db.scalar(select(func.clock_timestamp()));until=min(now+timedelta(seconds=LEASE_SECONDS),instant(checked['expires_at']))
    old.need(until>now)
    token=str(uuid4());row.status='running';row.attempts=1;row.lease_token=token;row.lease_until=until;row.version+=1
    db.flush();_bind_guard(db,row.workspace_id,row.id,token)
    return {'control_id':row.id,'lease_token':token}


@old.checked
def verify_worker(db,proof):
    """Recheck original grant and live lease, not a model-dispatch permit."""
    guard.require_disposable_database(db)
    old.need(type(proof) is dict and set(proof)=={'control_id','lease_token'} and type(proof['lease_token']) is str)
    row,checked=_control(db,proof['control_id'],lock=True)
    old.need(row.status=='running' and row.attempts==1 and row.lease_token==proof['lease_token']
        and row.lease_until is not None and row.lease_until>db.scalar(select(func.clock_timestamp())))
    _bind_guard(db,row.workspace_id,row.id,proof['lease_token'])
    return {'control_id':row.id,'delegation_id':checked['delegation_id'],'workspace_id':row.workspace_id,
        'task_id':checked['task_id'],'plan_id':checked['plan_id'],'job_id':checked['job_id'],
        'actor_id':checked['actor_id'],'transport_authorized':False}
