"""Closed, unrouted test-only transport intent; never performs network I/O.

Requires the strict isolated v2 reservation reader; unknown originals fail
closed. A durable intent means the outcome is unknown, not that a request was
sent or unsent, and never grants permission to retry. A historical verifier is
still required before saved intent can be read after session revocation.
"""
import copy
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import event as sa_event, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_file_intent_candidate_v2 as current
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_start_safety_candidate_v2 as safety
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_lease_candidate_v2 as leases
from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
from . import same_goal_advice_unlimited_store_v2 as custody
from .contracts import digest
from .models import Audit, CloudCall, Job, Plan, uid
from .relations import lock_current_actor
from .temporal import instant

ACTION='same_goal_advice.unlimited_transport_intent_test_v2'
SCHEMA='same-goal-advice-unlimited-transport-intent-test-v2'
_FIELDS={'schema','audit_id','workspace_id','actor_id','task_id','plan_id','job_id','cloud_call_id',
    'reservation_ref','start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref',
    'request_hash','dispatch_options_hash','token_bound','created_at','record_hash'}


def _verify_live_reservation(db,p,task,plan,job,lease_token,call_id,*,worker_proof=None):
    # This fixed test-only reader checks original Start, lease, document,
    # preview, v2 claim, CloudCall, receipt and daily ledger. It accepts no
    # caller-provided verifier and is not a transport grant.
    prep,_,ref=custody.read_reserved_test_only(db,p,task,plan)
    store.need(ref['cloud_call_id']==call_id and job.id==plan.data['advice_start_v2']['job_id']
        and job.lease_token==lease_token and job.lease_until>db.scalar(select(func.clock_timestamp())))
    reservation=store._fresh(db,p,Audit,ref['reservation_audit_id'])
    preview=store._fresh(db,p,Audit,reservation.data['preview_ref']['id'])
    call=store._fresh(db,p,CloudCall,call_id)
    safety.lock_current(db,p,task,prep,preview.data['preview_record'])
    previews.verify_current_for_start(db,p,task,plan,prep,preview,started=True,reserved_test_only=True,**leases._worker_kwargs(worker_proof))
    current._check_current(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
    return {'prep':prep,'preview':preview,'reservation':reservation,'call':call}


def _view(value,*,replayed):
    return {'intent_audit_id':value['audit_id'],'cloud_call_id':value['cloud_call_id'],
        'replayed':replayed,'send_outcome':'unknown','transport_authorized':False}


@store.checked
def verify_saved_links(db,p,task,plan,job,prep,preview,reservation,call,intent):
    """Check intent only after its caller has checked all v2 custody originals."""
    value=intent.data;data=reservation.data
    store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
        and intent.action==ACTION and intent.id==value['audit_id'] and intent.actor_id==value['actor_id']
        and intent.workspace_id==value['workspace_id']==p.workspace_id and intent.object_id==plan.id
        and value['task_id']==task.id and value['plan_id']==plan.id and value['job_id']==job.id
        and value['cloud_call_id']==call.id and value['actor_id']==reservation.actor_id
        and value['reservation_ref']=={'id':reservation.id,'hash':digest(data)}
        and value['preview_ref']=={'id':preview.id,'hash':digest(preview.data)}
        and all(value[key]==data[key] for key in ('start_ref','lease_ref','preview_ref','lease_token_hash',
            'model_pin','policy_ref','request_hash','dispatch_options_hash','token_bound'))
        and value['policy_ref']==prep['budget']['policy_ref'] and value['model_pin']==prep['model_pin']
        and value['lease_token_hash']==digest(job.lease_token) and call.id==data['call_id']
        and call.data.get('same_goal_advice_unlimited_v2')==data and call.status=='reserved'
        and intent.created_at==instant(value['created_at']) and intent.created_at>=reservation.created_at
        and intent.created_at<job.lease_until and value['record_hash']==contracts.record_hash(value))
    return {'schema':SCHEMA,'intent_audit_id':intent.id,'cloud_call_id':call.id,
        'intent_hash':digest(value),'transport_authorized':False,'send_outcome':'unknown'}


def _unbind(db):
    previous=db.info.pop('v2_transport_intent_test_guard',None)
    if previous is not None and sa_event.contains(db,'before_commit',previous):
        sa_event.remove(db,'before_commit',previous)


def _ended(db,transaction):
    if transaction.parent is None:_unbind(db)


def _fingerprint(row):
    result={'id':row.id,'workspace_id':row.workspace_id,'data':row.data}
    if type(row) is Audit:
        result.update(actor_id=row.actor_id,action=row.action,object_id=row.object_id,
            created_at=row.created_at.isoformat())
    elif type(row) is CloudCall:
        result.update(capability_id=row.capability_id,status=row.status,reserved_tokens=row.reserved_tokens,
            charged_tokens=row.charged_tokens,created_at=row.created_at.isoformat())
    elif type(row) is Job:
        result.update(kind=row.kind,status=row.status,version=row.version,attempts=row.attempts,
            idempotency_key=row.idempotency_key,lease_token=row.lease_token,
            lease_until=row.lease_until.isoformat() if row.lease_until else None,
            available_at=row.available_at.isoformat() if row.available_at else None)
    elif type(row) is Plan:
        result.update(task_id=row.task_id,status=row.status,version=row.version)
    else:
        result.update(owner_id=row.owner_id,package_id=row.package_id,status=row.status,title=row.title)
    return digest(result)


def _bind(db,p,rows,expected):
    store.need(db.info.get('v2_transport_intent_test_guard') is None)
    def check(session):
        # Refuse local edits before populate_existing could overwrite them.
        store.need(not any(session.is_modified(row,include_collections=True) or row in session.deleted for row in rows))
        for row in rows:
            current_row=store._fresh(session,p,type(row),row.id)
            store.need(_fingerprint(current_row)==expected[(type(row),row.id)])
        call=next(row for row in rows if type(row) is CloudCall)
        job=next(row for row in rows if type(row) is Job)
        store.need(call.status=='reserved' and job.status=='running'
            and job.lease_until>session.scalar(select(func.clock_timestamp())))
    if not db.info.get('v2_transport_intent_test_cleanup'):
        sa_event.listen(db,'after_transaction_end',_ended)
        db.info['v2_transport_intent_test_cleanup']=True
    sa_event.listen(db,'before_commit',check);db.info['v2_transport_intent_test_guard']=check


@store.checked
def issue(db,p,task_id,plan_id,job_id,lease_token,call_id,*,worker_proof=None):
    guard.require_disposable_database(db)
    store.need(db.get_nested_transaction() is None and not db.new and not db.dirty and not db.deleted)
    leases._worker_scope(db,p,task_id,plan_id,job_id,worker_proof)
    store._reader(db,p,write=True)
    task=store.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.id==plan_id,Plan.task_id==task.id)
        .with_for_update().execution_options(populate_existing=True))
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job_id)
        .with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None and job is not None and job.data.get('plan_id')==plan.id
        and job.status=='running' and job.lease_token==lease_token
        and job.lease_until>db.scalar(select(func.clock_timestamp())))
    try:
        with db.begin_nested():
            db.execute(text('LOCK TABLE public.cloud_model_calls, public.audit_events IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    except DBAPIError as error:
        if getattr(error.orig,'sqlstate',None)!='55P03':raise
        raise HTTPException(409,'原云账本正在修改，请保留原记录稍后核对') from error
    proof=_verify_live_reservation(db,p,task,plan,job,lease_token,call_id,**leases._worker_kwargs(worker_proof))
    store.need(type(proof) is dict and set(proof)=={'prep','preview','reservation','call'} )
    prep,preview,reservation,call=(proof[k] for k in ('prep','preview','reservation','call'))
    store.need(type(call) is CloudCall and call.id==call_id and call.workspace_id==p.workspace_id
        and call.status=='reserved' and type(reservation) is Audit and reservation.workspace_id==p.workspace_id
        and reservation.actor_id==p.user_id and reservation.object_id==plan.id
        and type(preview) is Audit and preview.workspace_id==p.workspace_id and preview.object_id==plan.id)
    data=reservation.data
    store.need(data['workspace_id']==p.workspace_id and data['actor_id']==p.user_id
        and data['task_id']==task.id and data['plan_id']==plan.id and data['job_id']==job.id
        and data['call_id']==call.id and data['audit_id']==reservation.id
        and data['preview_ref']=={'id':preview.id,'hash':digest(preview.data)}
        and data['lease_token_hash']==digest(lease_token) and data['policy_ref']==prep['budget']['policy_ref']
        and call.data.get('same_goal_advice_unlimited_v2')==data)
    original_start=store._fresh(db,p,Audit,data['start_ref']['id'])
    original_lease=store._fresh(db,p,Audit,data['lease_ref']['id'])
    store.need(digest(original_start.data)==data['start_ref']['hash']
        and digest(original_lease.data)==data['lease_ref']['hash'])
    originals=store._rows(db,p,Audit,Audit.action==ACTION,or_(Audit.object_id==plan.id,
        Audit.data['cloud_call_id'].as_string()==call.id,Audit.data['job_id'].as_string()==job.id))
    store.need(len(originals)<=1)
    if originals:
        value=originals[0].data
        store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
            and originals[0].id==value['audit_id'] and originals[0].actor_id==p.user_id
            and originals[0].object_id==plan.id and value['workspace_id']==p.workspace_id
            and value['actor_id']==p.user_id and value['task_id']==task.id and value['plan_id']==plan.id
            and value['cloud_call_id']==call.id and value['job_id']==job.id
            and value['reservation_ref']=={'id':reservation.id,'hash':digest(data)}
            and all(value[key]==data[key] for key in ('start_ref','lease_ref','preview_ref','lease_token_hash',
                'model_pin','policy_ref','request_hash','dispatch_options_hash','token_bound'))
            and originals[0].created_at==instant(value['created_at'])
            and value['record_hash']==contracts.record_hash(value))
        return _view(value,replayed=True)
    now=db.scalar(select(func.clock_timestamp()))
    store.need(type(now) is datetime and now.tzinfo is not None and job.lease_until>now)
    value={'schema':SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'actor_id':p.user_id,
        'task_id':task.id,'plan_id':plan.id,'job_id':job.id,'cloud_call_id':call.id,
        'reservation_ref':{'id':reservation.id,'hash':digest(data)},'start_ref':copy.deepcopy(data['start_ref']),
        'lease_ref':copy.deepcopy(data['lease_ref']),'preview_ref':copy.deepcopy(data['preview_ref']),
        'lease_token_hash':data['lease_token_hash'],'model_pin':copy.deepcopy(data['model_pin']),
        'policy_ref':copy.deepcopy(data['policy_ref']),'request_hash':data['request_hash'],
        'dispatch_options_hash':data['dispatch_options_hash'],'token_bound':data['token_bound'],
        'created_at':now.isoformat()}
    value['record_hash']=contracts.record_hash(value)
    audit=Audit(id=value['audit_id'],workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
        object_id=plan.id,created_at=now,data=value)
    old_file=db.info.get('v2_file_intent_commit_guard');old_goal=db.info.get('v2_start_goal_commit_guard')
    old_lease=db.info.get('v2_send_lease_commit_guard')
    try:
        current._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
        safety.bind_commit_guard(db,p,task.id,prep,preview.data['preview_record'])
        leases._bind_commit_guard(db,p,job.id,lease_token)
        db.add(audit);db.flush()
        rows=[task,plan,job,call,reservation,preview,original_start,original_lease,audit]
        _bind(db,p,rows,{(type(row),row.id):_fingerprint(row) for row in rows})
    except BaseException:
        if old_file is None:current._unbind_commit_guard(db)
        if old_goal is None:safety.unbind_commit_guard(db)
        if old_lease is None:leases._unbind_commit_guard(db)
        _unbind(db)
        raise
    return _view(value,replayed=False)
