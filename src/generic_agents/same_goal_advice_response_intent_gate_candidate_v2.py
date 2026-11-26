"""Test-only fixed historical custody check; no session or send authority.

This validates the fixed synthetic v2 record chain under cooperative PostgreSQL
locks, not an HTTP transmission or resistance to an arbitrary database owner.
Only the dedicated disposable test database is accepted. No route installs it.
"""
from datetime import timedelta, timezone
from sqlalchemy import or_, select, text
from . import same_goal_advice_cloud_claim_test_v2 as claims
from . import same_goal_advice_cloud_reservation_candidate_v2 as reservation
from . import same_goal_advice_contracts_v302 as old
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_transport_intent_candidate_v2 as transport_intent
from . import same_goal_advice_unlimited_contracts_v2 as v2contracts
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_lease_candidate_v2 as lease
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview
from . import same_goal_advice_unlimited_start_candidate_v2 as start
from .contracts import digest
from .models import Audit, CloudCall, Job, Plan, Task
from .temporal import instant

ACTION=transport_intent.ACTION
SCHEMA=transport_intent.SCHEMA
FIELDS=transport_intent._FIELDS
PERMIT_ACTION='same_goal_advice.unlimited_dispatch_permit_test_v2'
PERMIT_SCHEMA='same-goal-advice-unlimited-dispatch-permit-test-v2'
PERMIT_FIELDS={'schema','audit_id','workspace_id','actor_id','task_id','plan_id','job_id','cloud_call_id',
    'intent_ref','reservation_ref','start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref',
    'request_hash','dispatch_options_hash','token_bound','issued_at','send_before','irrevocable_after_commit','record_hash'}
RESERVATION_FIELDS={'schema','workspace_id','actor_id','task_id','plan_id','job_id','call_id','audit_id',
    'start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref','request_hash',
    'dispatch_options_hash','token_bound','source_recheck_started_at','source_recheck_completed_at',
    'accounting_checked_at','transport_authorized','send_outcome','usage_unavailable','day','reserved_at'}


def _row(db,model,workspace,identity):
    row=db.scalar(select(model).where(model.workspace_id==workspace,model.id==identity)
        .with_for_update(read=True,nowait=True).execution_options(populate_existing=True))
    store.need(row is not None, '历史原件缺失；保留结果，不得重发')
    return row


def _audit(db,workspace,reference,action):
    store.need(type(reference) is dict and set(reference)=={'id','hash'})
    row=_row(db,Audit,workspace,reference['id'])
    store.need(row.action==action and digest(row.data)==reference['hash'] and row.version==1)
    return row


@store.checked
def check_original(db, workspace_id, cloud_call_id, intent_audit_id):
    """Return only frozen origin metadata, never permission or absence proof.

    Historical receipt recovery intentionally does not check active users,
    sessions, policy expiry or the current lease token. It checks actual
    database rows and original receipt links, including original lease times.
    """
    # The versioned inventory binds an isolated Session to the already-checked
    # transaction Connection. Check that Connection's actual cluster identity.
    claims.require_test_database(db)
    # Local import avoids a module cycle: the permit writer calls the separate
    # pre-permit local-absence check from the storage module.
    from . import same_goal_advice_dispatch_permit_candidate_v2 as permit_contract
    store.need((PERMIT_ACTION,PERMIT_SCHEMA,PERMIT_FIELDS)==
        (permit_contract.ACTION,permit_contract.SCHEMA,permit_contract.FIELDS))
    claims.lock_and_check(db)
    intent=_row(db,Audit,workspace_id,intent_audit_id); v=intent.data
    store.need(intent.action==ACTION and type(v) is dict and set(v)==FIELDS and v['schema']==SCHEMA
        and v['record_hash']==old.record_hash(v) and v['audit_id']==intent.id
        and v['workspace_id']==workspace_id and v['cloud_call_id']==cloud_call_id
        and intent.actor_id==v['actor_id'] and intent.object_id==v['plan_id']
        and intent.version==1 and intent.created_at==instant(v['created_at']))
    # A second record naming any part of this one-shot identity is ambiguous.
    intents=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,
        or_(Audit.action==ACTION,Audit.data['schema'].as_string()==SCHEMA),or_(
        Audit.object_id==v['plan_id'],Audit.data['cloud_call_id'].as_string()==cloud_call_id,
        Audit.data['job_id'].as_string()==v['job_id'])).limit(2).execution_options(populate_existing=True)))
    store.need(len(intents)==1 and intents[0].id==intent.id)
    call=_row(db,CloudCall,workspace_id,cloud_call_id)
    task=_row(db,Task,workspace_id,v['task_id']);plan=_row(db,Plan,workspace_id,v['plan_id'])
    job=_row(db,Job,workspace_id,v['job_id'])
    store.need(plan.task_id==task.id and job.kind==start.KIND
        and job.data.get('task_id')==task.id and job.data.get('plan_id')==plan.id)
    claim=db.execute(text('SELECT workspace_id,job_id,plan_id,cloud_call_id,audit_id FROM '
        'public.ga_test_advice_v2_dispatch_claims WHERE workspace_id=:w AND job_id=:j'),
        {'w':workspace_id,'j':job.id}).one_or_none()
    store.need(claim is not None and tuple(claim)==(workspace_id,job.id,plan.id,call.id,v['reservation_ref']['id']))
    allocated=_audit(db,workspace_id,v['reservation_ref'],claims.ACTION); r=allocated.data
    store.need(type(r) is dict and set(r)==RESERVATION_FIELDS and r['schema']==reservation.SCHEMA
        and allocated.object_id==plan.id and allocated.actor_id==v['actor_id']
        and all(r[k]==v[k] for k in ('workspace_id','actor_id','task_id','plan_id','job_id',
            'lease_token_hash','model_pin','policy_ref','request_hash','dispatch_options_hash','token_bound',
            'start_ref','lease_ref','preview_ref'))
        and r['call_id']==call.id and r['audit_id']==allocated.id
        and r['transport_authorized'] is False and r['send_outcome']=='unknown' and r['usage_unavailable'] is True
        and allocated.created_at==instant(r['reserved_at']))
    initial=_audit(db,workspace_id,v['start_ref'],start.ACTION); sv=initial.data
    leased=_audit(db,workspace_id,v['lease_ref'],lease.ACTION); lv=leased.data
    shown=_audit(db,workspace_id,v['preview_ref'],preview.ACTION); pv=shown.data
    store.need(all(row.actor_id==v['actor_id'] and row.object_id==plan.id for row in (initial,leased))
        and shown.object_id==plan.id
        and type(sv) is dict and set(sv)==start._FIELDS and sv['schema']==start.SCHEMA
        and sv['record_hash']==old.record_hash(sv)
        and sv['workspace_id']==workspace_id and sv['task_id']==task.id and sv['plan_id']==plan.id
        and sv['audit_id']==initial.id and sv['actor_id']==v['actor_id']
        and sv['job_ref']=={'id':job.id,'kind':start.KIND,'schema':start.JOB_SCHEMA,'key':start.job_key(task.id,plan.id)}
        and sv['preview_ref']=={'id':shown.id,'record_hash':pv['record_hash']}
        and initial.created_at==instant(sv['started_at']))
    body=old.Start.model_validate(sv['request']).model_dump(mode='json')
    store.need(sv['body_hash']==digest(body) and body['preview_hash']==pv['preview_record']['preview_hash']
        and sv['snapshot']['start_request']==body and sv['snapshot']['start_request_hash']==digest(body))
    store.need(type(lv) is dict and set(lv)==lease._FIELDS and lv['record_hash']==old.record_hash(lv)
        and lv['schema']==lease.SCHEMA and lv['audit_id']==leased.id and lv['workspace_id']==workspace_id
        and lv['actor_id']==v['actor_id'] and lv['task_id']==task.id and lv['plan_id']==plan.id
        and lv['job_id']==job.id and lv['start_audit_id']==initial.id
        and lv['lease_token_hash']==v['lease_token_hash'] and leased.created_at==instant(lv['issued_at'])
        and instant(lv['expires_at'])==leased.created_at+timedelta(seconds=lease.SECONDS))
    store.need(type(pv) is dict and set(pv)==preview._FIELDS and pv['schema']==preview.SCHEMA
        and pv['record_hash']==old.record_hash(pv) and pv['audit_id']==shown.id
        and pv['workspace_id']==workspace_id and pv['task_id']==task.id and pv['plan_id']==plan.id
        and pv['actor_id']==shown.actor_id and shown.created_at==instant(pv['prepared_at'])
        and sv['snapshot']['preview']==pv['preview_record'])
    prep=plan.data['preparation_snapshot']; snap=sv['snapshot']; pre=pv['preview_record']
    store.need(prep['schema']==v2contracts.PREPARATION and prep['record_hash']==old.record_hash(prep)
        and prep['scope']['workspace_id']==workspace_id and prep['scope']['task_id']==task.id
        and prep['scope']['analysis_plan_id']==plan.id and prep['model_pin']==v['model_pin']
        and prep['budget']['policy_ref']==v['policy_ref']
        and sv['preparation_snapshot_hash']==pv['preparation_snapshot_hash']==prep['record_hash']
        and snap['schema']==v2contracts.EXECUTION and snap['record_hash']==old.record_hash(snap)
        and snap['scope']==prep['scope'] and snap['preparation_snapshot_hash']==prep['record_hash']
        and snap['source_record']==pv['source_record']
        and pre['schema']==v2contracts.PREVIEW and pre['preview_hash']==digest(
            {k:value for k,value in pre.items() if k!='preview_hash'})
        and pre['preparation_snapshot_hash']==prep['record_hash'] and pre['model_pin']==v['model_pin'])
    prepared=pv['prepared']; model=pv['model_snapshot']
    store.need(v['request_hash']==prepared['request_hash']==digest(prepared['messages'])
        and v['dispatch_options_hash']==prepared['dispatch_options_hash']==digest(prepared['options'])
        and type(v['token_bound']) is int and v['token_bound']>0 and v['token_bound']==prepared['token_bound']
        and type(v['model_pin']) is dict and digest(model)==v['model_pin']['content_hash']
        and model['execution']['provider']=='deepseek' and type(model['execution']['model']) is str
        and bool(model['execution']['model']) and call.capability_id==v['model_pin']['id']
        and call.reserved_tokens==v['token_bound'] and type(call.charged_tokens) is int and call.charged_tokens>=0
        and call.status in {'reserved','succeeded','failed'} and call.data.get(reservation.KEY)==r
        and call.data.get('actor_id')==v['actor_id'] and call.data.get('request_hash')==v['request_hash']
        and call.data.get('dispatch_options_hash')==v['dispatch_options_hash'] and call.data.get('day')==r['day'])
    begin=instant(r['source_recheck_started_at']); end=instant(r['source_recheck_completed_at'])
    accounting=instant(r['accounting_checked_at']); reserved=instant(r['reserved_at']); when=instant(v['created_at'])
    store.need(initial.created_at<=leased.created_at<=begin<=end<=accounting<=reserved<=when<instant(lv['expires_at'])
        and reserved.astimezone(timezone.utc).date().isoformat()==r['day'])
    # The irreversible permit must be unique and committed before the one
    # dispatch. It is a historical record, never a right to send again.
    permits=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,
        or_(Audit.action==PERMIT_ACTION,Audit.data['schema'].as_string()==PERMIT_SCHEMA),or_(
        Audit.object_id==plan.id,Audit.data['cloud_call_id'].as_string()==call.id,
        Audit.data['job_id'].as_string()==job.id,Audit.data['intent_ref']['id'].as_string()==intent.id))
        .limit(2).execution_options(populate_existing=True)))
    store.need(len(permits)==1, '原派发许可缺失或不唯一；保留原答，不得重发')
    permit=permits[0]; permit_value=permit.data
    store.need(type(permit_value) is dict and set(permit_value)==PERMIT_FIELDS
        and permit.action==PERMIT_ACTION and permit_value['schema']==PERMIT_SCHEMA
        and permit_value['record_hash']==old.record_hash(permit_value)
        and permit_value['audit_id']==permit.id and permit.workspace_id==workspace_id
        and permit.actor_id==v['actor_id'] and permit.object_id==plan.id and permit.version==1
        and permit_value['intent_ref']=={'id':intent.id,'hash':digest(v)}
        and all(permit_value[key]==v[key] for key in ('workspace_id','actor_id','task_id','plan_id','job_id',
            'cloud_call_id','reservation_ref','start_ref','lease_ref','preview_ref','lease_token_hash',
            'model_pin','policy_ref','request_hash','dispatch_options_hash','token_bound'))
        and permit_value['irrevocable_after_commit'] is True
        and permit.created_at==instant(permit_value['issued_at']))
    permit_until=instant(permit_value['send_before'])
    store.need(when<=permit.created_at<permit_until<=instant(lv['expires_at'])
        and permit_until<=permit.created_at+timedelta(seconds=permit_contract.MAX_SECONDS))
    policy_event=_row(db,Audit,workspace_id,v['policy_ref']['id'])
    store.need(policy_event.action==policy.POLICY and policy_event.object_id==task.id
        and policy_event.data['schema']==policy.POLICY_SCHEMA
        and policy_event.data['record_hash']==old.record_hash(policy_event.data)
        and digest(policy_event.data)==v['policy_ref']['record_hash']
        and instant(policy_event.data['issued_at'])<=permit.created_at
        and permit_until<=instant(policy_event.data['expires_at']))
    # Hash only stable original records; mutable status/usage and current roles
    # must not make a paid response unrecoverable after settlement or logout.
    anchor=digest({'workspace':workspace_id,'intent_id':intent.id,'intent_hash':v['record_hash'],
        'reservation_hash':digest(r),'start_hash':digest(sv),'lease_hash':digest(lv),
        'preview_hash':digest(pv),'permit_id':permit.id,
        'permit_hash':permit_value['record_hash'],'call_id':call.id})
    return {'actor_id':v['actor_id'],'anchor_hash':anchor,
        'provider':'deepseek','model':model['execution']['model']}
