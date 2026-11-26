"""Session-bound *candidate* delegation, with no worker or send authority.

Only a real authenticated HTTP actor can create it, in the transaction that
creates that actor's original Start. A future trusted worker-control/lease
protocol must verify this record under the same Task lock as the dispatch
permit. The inspections here authenticate an HTTP principal, not a worker.
This file has no route, worker, transport, migration or external side effect.
"""
import copy
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import event as sa_event, func, or_, select, text

from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_response_intent_gate_candidate_v2 as permit_origin
from . import same_goal_advice_start_safety_candidate_v2 as safety
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
from . import same_goal_advice_unlimited_contracts_v2 as unlimited
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_start_candidate_v2 as start
from . import same_goal_advice_unlimited_store_v2 as custody
from . import workspace_advice_nonce_candidate_v2 as nonces
from . import workspace_command_nonce_lock_v312 as workspace_nonces
from .contracts import digest
from .models import Audit, AuthSession, Capability, Job, Membership, Plan, Task, User, uid
from .relations import lock_current_actor
from .security import Principal
from .temporal import instant

AUTHORIZED='same_goal_advice.dispatch_delegation_candidate_v2'
REVOKED='same_goal_advice.dispatch_delegation_revoked_candidate_v2'
SCHEMA='same-goal-advice-dispatch-delegation-candidate-v2'
REVOKE_SCHEMA='same-goal-advice-dispatch-delegation-revoked-candidate-v2'
MAX_VALIDITY=timedelta(minutes=15)
_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','job_id','actor_id','role','session_id',
    'request_id','request','request_hash','start_ref','preview_ref','preview_hash','preparation_hash',
    'source_hash','goal_hash','issued_at','expires_at','record_hash'}
_REVOKE_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','delegation_id','actor_id',
    'role','session_id','request_id','revoked_at','record_hash'}


class Body(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: contracts.ID
    start: contracts.Start
    confirmed: bool

    @field_validator('confirmed',mode='before')
    @classmethod
    def explicit_confirmation(cls,value):
        if type(value) is not bool or value is not True:
            raise ValueError('需要明确确认')
        return value


def _real_http(p):
    old.need(type(p) is Principal and p.role in {'admin','operator'})
    old.need(type(p.session_id) is str and str(UUID(p.session_id))==p.session_id,
        '需要有效的用户登录会话')


def _lock_nonces(db,p,identities):
    old.existing._id(p.workspace_id)
    values=[old.existing._id(identity) for identity in identities]
    old.need(len(set(values))==len(values))
    for identity in sorted(values):
        db.execute(text(workspace_nonces.LOCK_SQL),
            {'workspace_command_nonce_key':workspace_nonces.key(p.workspace_id,identity)})


def _check_nonce(db,p,identity,action,plan_id):
    actions=workspace_nonces.ACTIONS | nonces.EXTRA_ACTIONS | {AUTHORIZED,REVOKED}
    rows=old._rows(db,p,Audit,Audit.action.in_(actions),or_(
        Audit.data['request_id'].as_string()==identity,Audit.data['request']['request_id'].as_string()==identity,
        Audit.data['body']['request_id'].as_string()==identity))
    plans=old._rows(db,p,Plan,or_(Plan.data['operations'].contains([{'request_id':identity}]),
        Plan.data['revisions'].contains([{'request_id':identity}])))
    old.need(len(rows)<=1 and not plans)
    if rows:
        old.need(rows[0].action==action and rows[0].object_id==plan_id and rows[0].actor_id==p.user_id,
            '原申请编号已被其他操作使用')


def _task(db,p,task_id):
    _real_http(p);old._reader(db,p,write=True)
    task=old.existing._task(db,p,task_id,lock=True)
    lock_current_actor(db,p)
    return task


def _plan(db,p,task_id,plan_id):
    return db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task_id,
        Plan.id==old.existing._id(plan_id)).with_for_update().execution_options(populate_existing=True))


def _events_scope(db,workspace_id,action,plan_id):
    rows=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,Audit.action==action,
        Audit.object_id==plan_id).limit(old.MAX_ROWS+1).with_for_update(read=True)))
    old.need(len(rows)<=old.MAX_ROWS)
    return rows


def _events(db,p,action,plan_id):
    return _events_scope(db,p.workspace_id,action,plan_id)


def _check_start_original(db,workspace_id,task,plan,grant,original,latest,job):
    """Recheck fixed Start custody semantics without creating a Principal."""
    value=original.data;prep=plan.data['preparation_snapshot'];pointer=plan.data[start.POINTER]
    old.need(type(value) is dict and set(value)==start._FIELDS and value['schema']==start.SCHEMA)
    body=contracts.Start.model_validate(value['request']).model_dump(mode='json')
    advice=db.scalar(select(Capability).where(Capability.workspace_id==workspace_id,
        Capability.id==prep['advice_capability_ref']['id']).with_for_update(read=True))
    old.need(advice is not None and advice.content_hash==prep['advice_capability_ref']['content_hash']
        and digest(advice.data)==advice.content_hash and advice.release_version==prep['advice_capability_ref']['release_version'])
    snapshot=unlimited.check_plan(value['snapshot'],preparation=prep,manifest=advice.data)
    key=start.job_key(task.id,plan.id)
    old.need(original.id==value['audit_id']==pointer['audit_id'] and original.workspace_id==value['workspace_id']==workspace_id
        and original.object_id==value['plan_id']==plan.id and value['task_id']==task.id
        and original.actor_id==value['actor_id']==grant['actor_id'] and original.created_at==instant(value['started_at'])
        and value['record_hash']==contracts.record_hash(value) and value['body_hash']==digest(body)
        and value['preparation_snapshot_hash']==prep['record_hash']
        and value['preview_ref']=={'id':pointer['preview_id'],'record_hash':pointer['preview_record_hash']}
        and value['job_ref']=={'id':job.id,'kind':start.KIND,'schema':start.JOB_SCHEMA,'key':key}
        and pointer=={'audit_id':original.id,'preview_id':latest.id,
            'preview_record_hash':latest.data['record_hash'],'job_id':job.id}
        and job.workspace_id==workspace_id and job.kind==start.KIND and job.idempotency_key==key
        and job.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS
        and job.data==start.job_data(plan,prep,snapshot) and job.created_at==original.created_at
        and plan.status=='awaiting_dispatch' and plan.version==body['expected_plan_version']+1)
    events=_events_scope(db,workspace_id,previews.ACTION,plan.id)
    old.need(bool(events))
    generations=[]
    for item in events:
        row=item.data
        old.need(type(row) is dict and set(row)==previews._FIELDS and row['schema']==previews.SCHEMA
            and item.id==row['audit_id'] and item.workspace_id==row['workspace_id']==workspace_id
            and item.object_id==row['plan_id']==plan.id and row['task_id']==task.id
            and item.actor_id==row['actor_id'] and item.created_at==instant(row['prepared_at'])
            and row['record_hash']==contracts.record_hash(row) and type(row['preview_generation']) is int)
        generations.append(row['preview_generation'])
    old.need(sorted(generations)==list(range(1,len(events)+1)))
    last=next(item for item in events if item.data['preview_generation']==len(events))
    old.need(last.id==latest.id and last.data['record_hash']==pointer['preview_record_hash']
        and last.data['plan_version_after']==body['expected_plan_version']
        and snapshot['preview']==last.data['preview_record'] and snapshot['source_record']==last.data['source_record']
        and snapshot['start_request']==body and body['preview_hash']==last.data['preview_record']['preview_hash'])
    nonce=body['request_id'];actions=workspace_nonces.ACTIONS | nonces.EXTRA_ACTIONS
    nonce_events=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,Audit.action.in_(actions),or_(
        Audit.data['request_id'].as_string()==nonce,Audit.data['request']['request_id'].as_string()==nonce,
        Audit.data['body']['request_id'].as_string()==nonce)).limit(old.MAX_ROWS+1).with_for_update(read=True)))
    nonce_plans=list(db.scalars(select(Plan).where(Plan.workspace_id==workspace_id,or_(
        Plan.data['operations'].contains([{'request_id':nonce}]),Plan.data['revisions'].contains([{'request_id':nonce}]))
        ).limit(old.MAX_ROWS+1).with_for_update(read=True)))
    old.need(len(nonce_events)==1 and nonce_events[0].id==original.id and not nonce_plans)


def _value(db,workspace_id,task,plan,identity):
    event=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==old.existing._id(identity),
        Audit.object_id==plan.id,Audit.action==AUTHORIZED).with_for_update(read=True).execution_options(populate_existing=True))
    old.need(event is not None and type(event.data) is dict and set(event.data)==_FIELDS)
    value=event.data
    old.need(value['schema']==SCHEMA and value['audit_id']==event.id and value['record_hash']==contracts.record_hash(value)
        and event.workspace_id==value['workspace_id']==workspace_id and value['task_id']==task.id
        and event.object_id==value['plan_id']==plan.id and event.actor_id==value['actor_id']
        and event.created_at==instant(value['issued_at']) and value['role'] in {'admin','operator'}
        and str(UUID(value['session_id']))==value['session_id'] and value['request_hash']==digest(value['request']))
    body=Body.model_validate(value['request']).model_dump(mode='json')
    old.need(body['request_id']==value['request_id'] and body['confirmed'] is True)
    pointer=plan.data.get(start.POINTER);old.need(type(pointer) is dict)
    original=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==value['start_ref']['id'],
        Audit.object_id==plan.id,Audit.action==start.ACTION).with_for_update(read=True))
    preview=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==value['preview_ref']['id'],
        Audit.object_id==plan.id,Audit.action==previews.ACTION).with_for_update(read=True))
    job=db.scalar(select(Job).where(Job.workspace_id==workspace_id,Job.id==value['job_id']).with_for_update(read=True))
    old.need(original is not None and preview is not None and job is not None and original.actor_id==event.actor_id
        and original.data['record_hash']==contracts.record_hash(original.data)
        and value['start_ref']=={'id':original.id,'record_hash':digest(original.data)}
        and value['preview_ref']=={'id':preview.id,'record_hash':digest(preview.data)}
        and preview.data['record_hash']==contracts.record_hash(preview.data)
        and pointer['audit_id']==original.id and pointer['preview_id']==preview.id and pointer['job_id']==job.id
        and original.data['job_ref']['id']==job.id and original.data['request']==body['start']
        and original.data['preview_ref']=={'id':preview.id,'record_hash':preview.data['record_hash']}
        and original.data['snapshot']['record_hash']==contracts.record_hash(original.data['snapshot'])
        and original.data['snapshot']['source_record']==preview.data['source_record']
        and original.data['snapshot']['preview']==preview.data['preview_record']
        and original.data['snapshot']['source_record']['record_hash']==value['source_hash']
        and value['source_hash']==contracts.record_hash(original.data['snapshot']['source_record'])
        and original.data['snapshot']['preview']['preview_hash']==value['preview_hash']==body['start']['preview_hash']
        and original.data['preparation_snapshot_hash']==value['preparation_hash']==plan.data['preparation_snapshot']['record_hash']
        and plan.data['preparation_snapshot']['record_hash']==contracts.record_hash(plan.data['preparation_snapshot'])
        and original.data['snapshot']['scope']['goal_hash']==value['goal_hash']==task.data.get('goal_hash')
        and plan.status=='awaiting_dispatch' and job.kind==start.KIND
        and job.idempotency_key==start.job_key(task.id,plan.id)
        and job.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS
        and job.data==start.job_data(plan,plan.data['preparation_snapshot'],original.data['snapshot'])
        and ((job.status=='queued' and job.attempts==0 and job.lease_token is None and job.lease_until is None)
            or (job.status=='running' and job.attempts==1 and job.lease_token is not None and job.lease_until is not None)))
    _check_start_original(db,workspace_id,task,plan,value,original,preview,job)
    policy_ref=plan.data['preparation_snapshot']['budget']['policy_ref']
    policy_row=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==policy_ref['id'],
        Audit.object_id==task.id,Audit.action==policy.POLICY).with_for_update(read=True))
    old.need(policy_row is not None and digest(policy_row.data)==policy_ref['record_hash'])
    policy_value,_=policy.policy_shape(policy_row,db.scalar(select(func.clock_timestamp())))
    old.need(instant(value['expires_at'])>instant(value['issued_at'])
        and instant(value['expires_at'])<=instant(value['issued_at'])+MAX_VALIDITY
        and instant(value['expires_at'])<=instant(policy_value['expires_at']))
    return event,value


def _revocation(db,workspace_id,plan,value):
    events=_events_scope(db,workspace_id,REVOKED,plan.id)
    old.need(len(events)<=1)
    if events:old.need(type(events[0].data) is dict and events[0].data.get('delegation_id')==value['audit_id'])
    if not events:return None
    event=events[0];saved=event.data
    old.need(set(saved)==_REVOKE_FIELDS and saved['schema']==REVOKE_SCHEMA and saved['audit_id']==event.id
        and saved['record_hash']==contracts.record_hash(saved) and event.workspace_id==saved['workspace_id']==workspace_id
        and event.object_id==saved['plan_id']==plan.id and event.actor_id==saved['actor_id']
        and saved['task_id']==plan.task_id and saved['delegation_id']==value['audit_id']
        and event.created_at==instant(saved['revoked_at']) and saved['role'] in {'admin','operator'}
        and (saved['role']=='admin' or saved['actor_id']==value['actor_id'])
        and str(UUID(saved['session_id']))==saved['session_id'] and str(UUID(saved['request_id']))==saved['request_id'])
    return {'state':'revoked','delegation_id':value['audit_id']}


def _committed_permit(db,p,plan,value):
    existing=_events(db,p,permits.ACTION,plan.id)
    old.need(len(existing)<=1)
    if not existing:return False
    permit=existing[0];record=permit.data
    old.need(type(record) is dict and record.get('plan_id')==plan.id and record.get('start_ref')==
        {'id':value['start_ref']['id'],'hash':value['start_ref']['record_hash']})
    checked=permit_origin.check_original(db,p.workspace_id,record['cloud_call_id'],record['intent_ref']['id'])
    old.need(checked['actor_id']==value['actor_id'])
    return True


def _view(value):
    return {'state':'authorized','delegation_id':value['audit_id'],'job_id':value['job_id'],
        'expires_at':value['expires_at'],'cloud_send_authorized':False}


def _unbind_guard(db):
    prior=db.info.pop('v2_delegation_commit_guard',None)
    if prior is not None and sa_event.contains(db,'before_commit',prior[1]):
        sa_event.remove(db,'before_commit',prior[1])


def _after_root(session,transaction):
    if transaction.parent is None:
        _unbind_guard(session)
        prior=session.info.pop('v2_delegation_original_guard',None)
        if prior is not None and sa_event.contains(session,'before_commit',prior[1]):
            sa_event.remove(session,'before_commit',prior[1])


def _after_rollback(session,transaction):
    for key in ('v2_delegation_commit_guard','v2_delegation_original_guard'):
        prior=session.info.get(key);current=prior[2] if prior else None
        while current is not None:
            if current is transaction:
                session.info.pop(key,None)
                if sa_event.contains(session,'before_commit',prior[1]):
                    sa_event.remove(session,'before_commit',prior[1])
                break
            current=current.parent


def _commit_guard(db,p,expires):
    identity=(p.workspace_id,p.user_id,p.role,p.session_id,expires)
    prior=db.info.get('v2_delegation_commit_guard')
    if prior is not None:
        old.need(prior[0]==identity and sa_event.contains(db,'before_commit',prior[1]));return
    def before_commit(session):
        lock_current_actor(session,p)
        old.need(session.scalar(select(func.clock_timestamp()))<expires,'确认已过期，请重新核对')
    if not db.info.get('v2_delegation_guard_cleanup_installed'):
        sa_event.listen(db,'after_transaction_end',_after_root)
        sa_event.listen(db,'after_soft_rollback',_after_rollback)
        db.info['v2_delegation_guard_cleanup_installed']=True
    sa_event.listen(db,'before_commit',before_commit)
    db.info['v2_delegation_commit_guard']=(identity,before_commit,db.get_nested_transaction() or db.get_transaction())


@old.checked
def begin_with_delegation(db,p,task_id,plan_id,body):
    guard.require_disposable_database(db);_real_http(p)
    request=Body.model_validate(body).model_dump(mode='json')
    _lock_nonces(db,p,[request['request_id'],request['start']['request_id']])
    _check_nonce(db,p,request['request_id'],AUTHORIZED,plan_id)
    nonces.acquire(db,p,action=start.ACTION,task_id=task_id,body=request['start'],subject_id=plan_id)
    task=_task(db,p,task_id);plan=_plan(db,p,task.id,plan_id);old.need(plan is not None)
    originals=_events(db,p,AUTHORIZED,plan.id)
    old.need(len(originals)<=1)
    if originals:
        event,value=_value(db,p.workspace_id,task,plan,originals[0].id)
        old.need(value['actor_id']==p.user_id and value['session_id']==p.session_id
            and value['role']==p.role and value['request_hash']==digest(request))
        old.need(_revocation(db,p.workspace_id,plan,value) is None)
        if _committed_permit(db,p,plan,value):
            return {'state':'dispatch_started_non_retractable','delegation_id':value['audit_id'],
                'job_id':value['job_id'],'cloud_send_authorized':False}
        if db.scalar(select(func.clock_timestamp()))>=instant(value['expires_at']):
            return {'state':'expired','delegation_id':value['audit_id'],'cloud_send_authorized':False}
        return _view(value)
    # This condition guarantees that a separately committed old Start cannot
    # acquire a new session merely by copying its actor ID and body.
    old.need(plan.status=='awaiting_consent')
    started=start.begin(db,p,task.id,plan.id,request['start'])
    prep,_=custody.read_started(db,p,task,plan)
    original=old._fresh(db,p,Audit,plan.data[start.POINTER]['audit_id'])
    preview=old._fresh(db,p,Audit,plan.data[start.POINTER]['preview_id'])
    old.need(original.actor_id==p.user_id and original.data['request']==request['start']
        and original.data['job_ref']['id']==started['job_id'])
    session=db.get(AuthSession,p.session_id);policy=db.get(Audit,prep['budget']['policy_ref']['id'])
    old.need(session is not None and policy is not None)
    now=db.scalar(select(func.clock_timestamp()))
    deadlines=[now+MAX_VALIDITY,session.expires_at,instant(policy.data['expires_at'])]
    if task.data.get('goal_spec',{}).get('deadline'):
        deadlines.append(instant(task.data['goal_spec']['deadline']))
    expires=min(deadlines);old.need(expires>now)
    value={'schema':SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'job_id':started['job_id'],'actor_id':p.user_id,'role':p.role,'session_id':p.session_id,
        'request_id':request['request_id'],'request':copy.deepcopy(request),'request_hash':digest(request),
        'start_ref':{'id':original.id,'record_hash':digest(original.data)},
        'preview_ref':{'id':preview.id,'record_hash':digest(preview.data)},'preview_hash':request['start']['preview_hash'],
        'preparation_hash':prep['record_hash'],'source_hash':original.data['snapshot']['source_record']['record_hash'],
        'goal_hash':prep['scope']['goal_hash'],'issued_at':now.isoformat(),'expires_at':expires.isoformat()}
    value['record_hash']=contracts.record_hash(value)
    event=Audit(id=value['audit_id'],workspace_id=p.workspace_id,object_id=plan.id,actor_id=p.user_id,
        action=AUTHORIZED,created_at=now,data=value)
    db.add(event);db.flush();_commit_guard(db,p,expires)
    _value(db,p.workspace_id,task,plan,event.id)
    return _view(value)


def _locked_actor(db,value):
    # Query the original real session and actual membership directly. Never
    # manufacture a Principal from an audit event or a caller-supplied role.
    row=db.execute(select(User.id,Membership.role,AuthSession.id).join(
        Membership,Membership.user_id==User.id).join(AuthSession,AuthSession.user_id==User.id).where(
        User.id==value['actor_id'],User.active.is_(True),Membership.workspace_id==value['workspace_id'],
        Membership.role==value['role'],Membership.role.in_(['admin','operator']),
        AuthSession.id==value['session_id'],AuthSession.revoked.is_(False),
        AuthSession.expires_at>func.clock_timestamp()).with_for_update(read=True,of=[User,Membership,AuthSession])).first()
    old.need(row is not None,'原确认账号、岗位或会话已失效；不能继续')


def _original_locked(db,workspace_id,task_id,plan_id,delegation_id):
    task=db.scalar(select(Task).where(Task.workspace_id==workspace_id,Task.id==old.existing._id(task_id))
        .with_for_update().execution_options(populate_existing=True))
    old.need(task is not None)
    plan=db.scalar(select(Plan).where(Plan.workspace_id==workspace_id,Plan.task_id==task.id,
        Plan.id==old.existing._id(plan_id)).with_for_update().execution_options(populate_existing=True))
    old.need(plan is not None)
    events=_events_scope(db,workspace_id,AUTHORIZED,plan.id)
    old.need(len(events)==1 and events[0].id==delegation_id)
    _,value=_value(db,workspace_id,task,plan,delegation_id)
    _locked_actor(db,value)
    old.need(_revocation(db,workspace_id,plan,value) is None)
    old.need(db.scalar(select(func.clock_timestamp()))<instant(value['expires_at']))
    return task,plan,value


def _original_commit_guard(db,workspace_id,task_id,plan_id,delegation_id):
    identity=(workspace_id,task_id,plan_id,delegation_id)
    prior=db.info.get('v2_delegation_original_guard')
    if prior is not None:
        old.need(prior[0]==identity and sa_event.contains(db,'before_commit',prior[1]));return
    def before_commit(session):
        _original_locked(session,workspace_id,task_id,plan_id,delegation_id)
    if not db.info.get('v2_delegation_guard_cleanup_installed'):
        sa_event.listen(db,'after_transaction_end',_after_root)
        sa_event.listen(db,'after_soft_rollback',_after_rollback)
        db.info['v2_delegation_guard_cleanup_installed']=True
    sa_event.listen(db,'before_commit',before_commit)
    db.info['v2_delegation_original_guard']=(identity,before_commit,db.get_nested_transaction() or db.get_transaction())


@old.checked
def verify_original(db,workspace_id,task_id,plan_id,delegation_id,*,lock=True):
    """Internal original-record check, not worker or cloud authorization.

    The calling permit transaction must authenticate its own service identity,
    control Job/lease and revalidate the current source and preview. No HTTP
    principal is manufactured and no transport right is returned here.
    """
    guard.require_disposable_database(db);old.need(lock is True)
    workspace_id=old.existing._id(workspace_id)
    task,plan,value=_original_locked(db,workspace_id,task_id,plan_id,delegation_id)
    old.need(not _events_scope(db,workspace_id,permits.ACTION,plan.id),
        '原申请已进入派发；不再重新授权')
    _original_commit_guard(db,workspace_id,task.id,plan.id,value['audit_id'])
    return {'state':'authorized','workspace_id':workspace_id,'task_id':task.id,'plan_id':plan.id,
        'job_id':value['job_id'],'actor_id':value['actor_id'],'session_id':value['session_id'],
        'delegation_id':value['audit_id'],'expires_at':value['expires_at'],
        'source_revalidated':False,'transport_authorized':False}


@old.checked
def inspect_authorization(db,p,task_id,plan_id,delegation_id):
    """Authenticated inspection only; deliberately grants no transport."""
    guard.require_disposable_database(db);task=_task(db,p,task_id);plan=_plan(db,p,task.id,plan_id);old.need(plan is not None)
    _,value=_value(db,p.workspace_id,task,plan,delegation_id)
    old.need(p.user_id==value['actor_id'] and p.session_id==value['session_id'] and p.role==value['role'])
    old.need(_revocation(db,p.workspace_id,plan,value) is None)
    old.need(db.scalar(select(func.clock_timestamp()))<instant(value['expires_at']))
    prep,_=custody.read_started(db,p,task,plan)
    event=old._fresh(db,p,Audit,plan.data[start.POINTER]['preview_id'])
    safety.lock_current(db,p,task,prep,event.data['preview_record'])
    previews.verify_current_for_start(db,p,task,plan,prep,event,started=True)
    old.need(not _events(db,p,permits.ACTION,plan.id))
    _commit_guard(db,p,instant(value['expires_at']))
    return {'state':'authorized','delegation_id':value['audit_id'],'transport_authorized':False}


@old.checked
def revoke(db,p,task_id,plan_id,delegation_id,request_id):
    guard.require_disposable_database(db);_real_http(p);old.need(type(request_id) is str and str(UUID(request_id))==request_id)
    _lock_nonces(db,p,[request_id]);_check_nonce(db,p,request_id,REVOKED,plan_id)
    task=_task(db,p,task_id);plan=_plan(db,p,task.id,plan_id);old.need(plan is not None)
    _,value=_value(db,p.workspace_id,task,plan,delegation_id)
    old.need(p.role=='admin' or p.user_id==value['actor_id'])
    prior=_revocation(db,p.workspace_id,plan,value)
    if prior:return prior
    if _committed_permit(db,p,plan,value):
        return {'state':'dispatch_started_non_retractable','delegation_id':value['audit_id']}
    now=db.scalar(select(func.clock_timestamp()))
    saved={'schema':REVOKE_SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'delegation_id':value['audit_id'],'actor_id':p.user_id,'role':p.role,'session_id':p.session_id,
        'request_id':request_id,'revoked_at':now.isoformat()}
    saved['record_hash']=contracts.record_hash(saved)
    actor_session=db.get(AuthSession,p.session_id);old.need(actor_session is not None)
    _commit_guard(db,p,actor_session.expires_at)
    db.add(Audit(id=saved['audit_id'],workspace_id=p.workspace_id,object_id=plan.id,actor_id=p.user_id,
        action=REVOKED,created_at=now,data=saved));db.flush()
    return {'state':'revoked','delegation_id':value['audit_id']}
