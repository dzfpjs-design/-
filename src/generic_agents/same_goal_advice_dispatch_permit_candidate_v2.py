"""One irreversible, short-lived dispatch permit for disposable tests only.

Intent alone never authorizes dispatch. Here the permit transaction holds the
current originals, then commits the explicit irreversible dispatch boundary.
If revocation commits first it blocks a permit; if this permit commits first,
that one process may start exactly one loopback POST before send_before. It is
never reissued or resumed after an uncertain outcome. No route/worker uses it.
"""
import copy
import time
from datetime import timedelta
from sqlalchemy import event,func,or_,select
from . import same_goal_advice_file_intent_candidate_v2 as current
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_start_safety_candidate_v2 as safety
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_transport_intent_candidate_v2 as intents
from . import same_goal_advice_unlimited_lease_candidate_v2 as leases
from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
from . import same_goal_advice_unlimited_store_v2 as custody
from .contracts import digest
from .models import Audit,AuthSession,CloudCall,Job,Plan,uid
from .relations import lock_current_actor
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288

ACTION='same_goal_advice.unlimited_dispatch_permit_test_v2'
SCHEMA='same-goal-advice-unlimited-dispatch-permit-test-v2'
MAX_SECONDS=10
FIELDS={'schema','audit_id','workspace_id','actor_id','task_id','plan_id','job_id','cloud_call_id',
    'intent_ref','reservation_ref','start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref',
    'request_hash','dispatch_options_hash','token_bound','issued_at','send_before','irrevocable_after_commit','record_hash'}


def _source_expiry(db,p,prep):
    row=old._fresh(db,p,GoalEvaluationV288,prep['mature_head']['evaluation_ref']['id'])
    value=row.data;policy=value['preparation_policy'];deadlines=[]
    if policy['time_basis']=='rolling_latest':
        age=timedelta(seconds=policy['max_age_seconds']);deadlines.append(instant(value['cut_at'])+age)
        report=value['evaluation_result']['result']
        for item in report['evidence']+report['observation_evidence']:
            old.need(item.get('observed_at'))
            deadlines.append(instant(item['observed_at'])+age)
            if item.get('valid_until'):deadlines.append(instant(item['valid_until']))
    return min(deadlines) if deadlines else None


def issue(db,p,task_id,plan_id,job_id,lease_token,call_id,intent_id,*,worker_proof=None):
    guard.require_disposable_database(db)
    old.need(db.get_nested_transaction() is None and not db.new and not db.dirty and not db.deleted)
    worker=leases._worker_scope(db,p,task_id,plan_id,job_id,worker_proof)
    old._reader(db,p,write=True)
    task=old.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.id==plan_id,Plan.task_id==task.id)
        .with_for_update().execution_options(populate_existing=True))
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job_id)
        .with_for_update().execution_options(populate_existing=True))
    old.need(plan is not None and job is not None and job.status=='running' and job.lease_token==lease_token)
    prep,_,reservation,checked=custody.read_intent_test_only(db,p,task,plan)
    old.need(checked['intent_audit_id']==intent_id and checked['cloud_call_id']==call_id
        and reservation['cloud_call_id']==call_id)
    intent=old._fresh(db,p,Audit,intent_id);data=intent.data
    preview=old._fresh(db,p,Audit,data['preview_ref']['id'])
    safety.lock_current(db,p,task,prep,preview.data['preview_record'])
    previews.verify_current_for_start(db,p,task,plan,prep,preview,started=True,
        reserved_test_only=True,intent_test_only=True,**leases._worker_kwargs(worker_proof))
    # This is only a locked local absence check, not an unsent proof or grant.
    storage.check_no_local_response_for_permit(db,p.workspace_id,call_id,intent_id)
    current._check_current(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
    safety.check_goal(db,p,task.id,prep);safety.check_freshness(db,p,prep)
    safety.check_evidence_bytes(db,p,preview.data['preview_record'])
    policy=db.get(Audit,prep['budget']['policy_ref']['id'])
    if worker is None:
        session=db.get(AuthSession,p.session_id)
        control_until=None;grant_until=None
    else:
        from . import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
        original=delegation.verify_original(db,p.workspace_id,task.id,plan.id,worker['delegation_id'])
        old.need(original['actor_id']==p.user_id and original['job_id']==job.id)
        session=db.get(AuthSession,original['session_id'])
        control_job=db.get(Job,worker_proof['control_id'])
        old.need(session is not None and session.user_id==p.user_id and control_job is not None
            and control_job.workspace_id==p.workspace_id and control_job.lease_until is not None)
        control_until=control_job.lease_until;grant_until=instant(original['expires_at'])
    old.need(session is not None and policy is not None)
    now=db.scalar(select(func.clock_timestamp()));tick=time.monotonic()
    deadlines=[now+timedelta(seconds=MAX_SECONDS),job.lease_until,session.expires_at,instant(policy.data['expires_at'])]
    if control_until is not None:deadlines.append(control_until)
    if grant_until is not None:deadlines.append(grant_until)
    if task.data['goal_spec'].get('deadline'):deadlines.append(instant(task.data['goal_spec']['deadline']))
    source_expiry=_source_expiry(db,p,prep)
    if source_expiry:deadlines.append(source_expiry)
    send_before=min(deadlines);old.need(send_before>now)
    originals=old._rows(db,p,Audit,Audit.action==ACTION,or_(Audit.object_id==plan.id,
        Audit.data['cloud_call_id'].as_string()==call_id,Audit.data['job_id'].as_string()==job.id))
    old.need(not originals,'该原申请已进入派发，不能重领或重复发送')
    value={'schema':SCHEMA,'audit_id':uid(),'workspace_id':p.workspace_id,'actor_id':p.user_id,
        'task_id':task.id,'plan_id':plan.id,'job_id':job.id,'cloud_call_id':call_id,
        'intent_ref':{'id':intent.id,'hash':digest(intent.data)}}
    for key in ('reservation_ref','start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref',
        'request_hash','dispatch_options_hash','token_bound'):value[key]=copy.deepcopy(data[key])
    value.update(issued_at=now.isoformat(),send_before=send_before.isoformat(),irrevocable_after_commit=True)
    value['record_hash']=contracts.record_hash(value)
    permit=Audit(id=value['audit_id'],workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
        object_id=plan.id,created_at=now,data=value)
    current._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
    safety.bind_commit_guard(db,p,task.id,prep,preview.data['preview_record'])
    leases._bind_commit_guard(db,p,job.id,lease_token)
    call=old._fresh(db,p,CloudCall,call_id);allocated=old._fresh(db,p,Audit,data['reservation_ref']['id'])
    initial=old._fresh(db,p,Audit,data['start_ref']['id']);leased=old._fresh(db,p,Audit,data['lease_ref']['id'])
    db.add(permit);db.flush()
    rows=[task,plan,job,call,allocated,preview,initial,leased,intent,permit]
    intents._bind(db,p,rows,{(type(row),row.id):intents._fingerprint(row) for row in rows})
    def deadline_guard(session):
        old.need(send_before>session.scalar(select(func.clock_timestamp())), '原派发许可已到期；未提交许可')
    event.listen(db,'before_commit',deadline_guard,once=True)
    return {'permit_audit_id':permit.id,'send_before':send_before.isoformat(),
        'monotonic_send_before':tick+(send_before-now).total_seconds(),
        'prepared':copy.deepcopy(preview.data['prepared']),
        'model':preview.data['model_snapshot']['execution']['model']}
