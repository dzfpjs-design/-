"""Test-only v2 Start custody; the send job is parked and has no worker.

This creates no Invocation, CloudCall, financial hold or transport intent. It
cannot authorize a network call until those original versioned proofs exist.
"""
import copy
from datetime import datetime
from sqlalchemy import func, or_, select
from . import same_goal_advice_contracts_v302 as old
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_contracts_v2 as contracts
from . import same_goal_advice_unlimited_store_v2 as custody
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_file_intent_candidate_v2 as current_guard
from . import workspace_advice_nonce_candidate_v2 as nonces
from . import workspace_command_nonce_lock_v312 as nonce_records
from .contracts import digest
from .models import Audit, Capability, Job, Plan, uid
from .relations import lock_current_actor
from .temporal import instant

ACTION='same_goal_advice.unlimited_started_v2'
KIND='generate_same_goal_advice_unlimited_v2'
JOB_SCHEMA='same-goal-advice-unlimited-send-job-v2'
SCHEMA='same-goal-advice-unlimited-start-v2'
POINTER='advice_start_v2'
_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','actor_id','request','body_hash',
    'preparation_snapshot_hash','snapshot','preview_ref','job_ref','started_at','record_hash'}


def job_key(task_id,plan_id):return 'same-goal-advice-send-v2:'+task_id+':'+plan_id


def job_data(plan,prep,snapshot):
    return {'schema':JOB_SCHEMA,'plan_id':plan.id,'task_id':plan.task_id,'actor_id':prep['business_actor_id'],
        'execution_snapshot_hash':snapshot['record_hash'],'invocation_key':job_key(plan.task_id,plan.id)}


def _view(value):
    return {'schema':'same-goal-advice-unlimited-start-view-v2','plan_id':value['plan_id'],
        'request_id':value['request']['request_id'],'job_id':value['job_ref']['id'],
        'plan_version':value['request']['expected_plan_version']+1,
        'status':'awaiting_dispatch','cloud_send_authorized':False}


@store.checked
def verify(db,p,task,plan,prep,event,send_job,preview_events):
    value=event.data;store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA)
    body=old.Start.model_validate(value['request']).model_dump(mode='json')
    ptr=plan.data[POINTER];key=job_key(task.id,plan.id)
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    snapshot=contracts.check_plan(value['snapshot'],preparation=prep,manifest=advice.data)
    store.need(event.id==value['audit_id']==ptr['audit_id'] and event.action==ACTION
        and event.actor_id==value['actor_id'] and event.object_id==plan.id
        and value['workspace_id']==p.workspace_id and value['task_id']==task.id and value['plan_id']==plan.id
        and value['record_hash']==old.record_hash(value) and value['body_hash']==digest(body)
        and value['preparation_snapshot_hash']==prep['record_hash']
        and value['preview_ref']=={'id':ptr['preview_id'],'record_hash':ptr['preview_record_hash']}
        and value['job_ref']=={'id':send_job.id,'kind':KIND,'schema':JOB_SCHEMA,'key':key}
        and ptr=={'audit_id':event.id,'preview_id':value['preview_ref']['id'],
            'preview_record_hash':value['preview_ref']['record_hash'],'job_id':send_job.id}
        and send_job.workspace_id==p.workspace_id and send_job.kind==KIND and send_job.idempotency_key==key
        and send_job.status=='queued' and send_job.attempts==0 and send_job.lease_token is None
        and send_job.lease_until is None and send_job.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS
        and send_job.data==job_data(plan,prep,snapshot)
        and event.created_at==send_job.created_at==instant(value['started_at'])
        and plan.status=='awaiting_dispatch' and plan.version==body['expected_plan_version']+1)
    store.need(preview_events and len(preview_events)==max(e.data['preview_generation'] for e in preview_events))
    last=next(e for e in preview_events if e.data['preview_generation']==len(preview_events))
    store.need(last.id==value['preview_ref']['id'] and last.data['record_hash']==value['preview_ref']['record_hash']
        and last.data['plan_version_after']==body['expected_plan_version']
        and snapshot['preview']==last.data['preview_record'] and snapshot['source_record']==last.data['source_record']
        and snapshot['start_request']==body and body['preview_hash']==last.data['preview_record']['preview_hash'])
    nonce=body['request_id']
    events=store._rows(db,p,Audit,Audit.action.in_(nonce_records.ACTIONS | nonces.EXTRA_ACTIONS),or_(
        Audit.data['request_id'].as_string()==nonce,Audit.data['request']['request_id'].as_string()==nonce,
        Audit.data['body']['request_id'].as_string()==nonce))
    plans=store._rows(db,p,Plan,or_(Plan.data['operations'].contains([{'request_id':nonce}]),
        Plan.data['revisions'].contains([{'request_id':nonce}])))
    store.need(len(events)==1 and events[0].id==event.id and not plans)
    return value


@store.checked
def begin(db,p,task_id,plan_id,body):
    guard.require_disposable_database(db)
    body=old.Start.model_validate(body).model_dump(mode='json');store._reader(db,p,write=True)
    nonces.acquire(db,p,action=ACTION,task_id=task_id,body=body,subject_id=plan_id)
    task=store.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.id==store.existing._id(plan_id),Plan.workspace_id==p.workspace_id,
        Plan.task_id==task.id).with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None)
    if plan.status=='awaiting_dispatch':
        prep,_=custody.read_started(db,p,task,plan)
        event=store._fresh(db,p,Audit,plan.data[POINTER]['audit_id'])
        store.need(event.actor_id==p.user_id and digest(event.data['request'])==digest(body))
        return _view(event.data)
    prep,_=custody.read_prepared(db,p,task,plan)
    store.need(p.role=='admin' or p.user_id==prep['business_actor_id'])
    events=store._rows(db,p,Audit,Audit.action==preview.ACTION,Audit.object_id==plan.id)
    store.need(bool(events))
    event=next(e for e in events if e.data['preview_generation']==len(events))
    store.need(plan.version==body['expected_plan_version']==event.data['plan_version_after']
        and body['preview_hash']==event.data['preview_record']['preview_hash']
        and body['expected_goal_hash']==prep['scope']['goal_hash']
        and body['expected_mature_head_hash']==prep['mature_head']['head_hash'])
    frame=preview.verify_current_for_start(db,p,task,plan,prep,event)
    current_guard._check_current(db,p,task.id,prep['budget']['policy_ref'])
    current_guard._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'])
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    snapshot=contracts.seal_plan({'schema':contracts.EXECUTION,'scope':copy.deepcopy(prep['scope']),
        'preparation_snapshot_hash':prep['record_hash'],'source_record':copy.deepcopy(frame['source_record']),
        'preview':copy.deepcopy(event.data['preview_record']),'start_request':body,'start_request_hash':digest(body)},
        preparation=prep,manifest=advice.data)
    now=db.scalar(select(func.clock_timestamp()));store.need(type(now) is datetime and now.tzinfo is not None)
    key=job_key(task.id,plan.id);job=Job(id=uid(),workspace_id=p.workspace_id,kind=KIND,idempotency_key=key,
        status='queued',attempts=0,created_at=now,available_at=custody.UNAVAILABLE_TO_GENERAL_WORKERS,
        data=job_data(plan,prep,snapshot))
    value={'schema':SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'actor_id':p.user_id,'request':body,'body_hash':digest(body),'preparation_snapshot_hash':prep['record_hash'],
        'snapshot':snapshot,'preview_ref':{'id':event.id,'record_hash':event.data['record_hash']},
        'job_ref':{'id':job.id,'kind':KIND,'schema':JOB_SCHEMA,'key':key},'started_at':now.isoformat()}
    value['record_hash']=old.record_hash(value)
    audit=Audit(id=value['audit_id'],workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
        object_id=plan.id,created_at=now,data=value)
    with db.begin_nested():
        db.add_all([job,audit]);plan.data={**plan.data,POINTER:{'audit_id':audit.id,'preview_id':event.id,
            'preview_record_hash':event.data['record_hash'],'job_id':job.id}}
        plan.status='awaiting_dispatch';plan.version+=1;db.flush()
        custody.read_started(db,p,task,plan)
    return _view(value)
