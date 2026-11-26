"""Disposable-only v2 accounting reservation; no provider dispatch or route.

Cooperating callers use one identity-unique database claim. Legacy/direct DB
writers outside this protocol are not constrained; this is not a production
sender or proof that any request was sent or unsent.
"""
import copy
from datetime import datetime, timezone
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from . import cloud_budget_policy as policy
from . import same_goal_advice_cloud_claim_test_v2 as claims
from . import same_goal_advice_file_intent_candidate_v2 as current
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_start_safety_candidate_v2 as safety
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_lease_candidate_v2 as leases
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview
from . import same_goal_advice_unlimited_start_candidate_v2 as start
from . import same_goal_advice_unlimited_store_v2 as custody
from .contracts import digest
from .models import Audit,Capability,CloudCall,Job,Plan,uid
from .relations import lock_current_actor

SCHEMA='same-goal-advice-unlimited-cloud-reservation-test-v2'
KEY='same_goal_advice_unlimited_v2'


def _utc_day(clock):
    store.need(type(clock) is datetime and clock.tzinfo is not None and clock.utcoffset() is not None)
    return clock.astimezone(timezone.utc).date().isoformat()


def _accounting_clock(db):
    """Always a database timestamp; replace only in isolated boundary tests."""
    return db.scalar(select(func.clock_timestamp()))


def _scalar_pin(row):
    """Capture all mapped scalar columns; payload is checked separately."""
    result={}
    for prop in inspect(row).mapper.column_attrs:
        if prop.key=='data':continue  # separately checked by canonical digest
        value=getattr(row,prop.key)
        store.need(value is None or type(value) in (str,int,bool,datetime),
            '映射字段类型未知；未接受云账本预留')
        result[prop.key]=(type(value),value)
    return result


def _unbind(db):
    previous=db.info.pop('v2_cloud_reservation_test_guard',None)
    if previous is not None and event.contains(db,'before_commit',previous):
        event.remove(db,'before_commit',previous)


def _ended(db,transaction):
    if transaction.parent is None:_unbind(db)


def _bind(db,p,task_id,plan_id,job_id,lease_token,lease_id,start_id,preview_id,call_id,audit_id,expected,*,worker_proof=None):
    store.need(db.info.get('v2_cloud_reservation_test_guard') is None)
    def check(session):
        # Fail closed on caller changes made after our explicit flush.
        store.need(not session.new and not session.dirty and not session.deleted)
        current._check_current(session,p,task_id,expected['policy_ref'],**leases._worker_kwargs(worker_proof))
        task=store.existing._task(session,p,task_id,lock=True)
        plan=store._fresh(session,p,Plan,plan_id)
        job=session.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job_id)
            .with_for_update().execution_options(populate_existing=True))
        initial=store._fresh(session,p,Audit,start_id)
        lease_event=store._fresh(session,p,Audit,lease_id)
        saved_preview=store._fresh(session,p,Audit,preview_id)
        store.need(job is not None and job.lease_token==lease_token and job.lease_until>session.scalar(select(func.clock_timestamp()))
            and digest(job.data)==expected['job_data_hash'] and digest(task.data)==expected['task_data_hash']
            and plan.status=='awaiting_dispatch'
            and digest(plan.data)==expected['plan_data_hash'])
        leases.verify(session,p,task,plan,job,initial,lease_event)
        actual=store._fresh(session,p,Audit,audit_id);call=store._fresh(session,p,CloudCall,call_id)
        daily=store._fresh(session,p,Job,expected['daily_id'])
        for name,row in {'task':task,'plan':plan,'job':job,'start':initial,'lease':lease_event,
                         'preview':saved_preview,'audit':actual,'cloud':call,'daily':daily}.items():
            store.need(_scalar_pin(row)==expected['scalar_pins'][name])
        store.need(digest(initial.data)==expected['start_hash'] and digest(lease_event.data)==expected['lease_hash']
            and digest(saved_preview.data)==expected['preview_hash'] and digest(actual.data)==expected['receipt_hash']
            and actual.action==claims.ACTION and actual.object_id==plan_id and actual.actor_id==p.user_id
            and actual.created_at==expected['audit_created_at']
            and call.status=='reserved' and call.capability_id==expected['model_id']
            and call.created_at==expected['call_created_at']
            and call.reserved_tokens==call.charged_tokens==expected['token_bound']
            and digest(call.data)==expected['cloud_data_hash'] and daily.kind=='cloud_budget'
            and daily.status=='internal' and digest(daily.data)==expected['daily_data_hash'])
        row=session.execute(text('SELECT workspace_id,job_id,plan_id,cloud_call_id,audit_id FROM '
            'public.ga_test_advice_v2_dispatch_claims WHERE workspace_id=:workspace AND job_id=:job'),
            {'workspace':p.workspace_id,'job':job_id}).one_or_none()
        store.need(row is not None and tuple(row)==(p.workspace_id,job_id,plan_id,call_id,audit_id))
        # The transaction holds both table fences, so a concurrent insert
        # cannot appear between this recheck and commit. Same-transaction raw
        # inserts are checked here rather than trusted as cooperative writes.
        claims._no_orphans(session)
    if not db.info.get('v2_cloud_reservation_test_cleanup'):
        event.listen(db,'after_transaction_end',_ended)
        db.info['v2_cloud_reservation_test_cleanup']=True
    event.listen(db,'before_commit',check);db.info['v2_cloud_reservation_test_guard']=check


@store.checked
def reserve(db,p,task_id,plan_id,job_id,lease_token,*,worker_proof=None):
    guard.require_disposable_database(db)
    store.need(db.get_nested_transaction() is None and not db.new and not db.dirty and not db.deleted)
    leases._worker_scope(db,p,task_id,plan_id,job_id,worker_proof)
    store._reader(db,p,write=True)
    task=store.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.id==plan_id,Plan.task_id==task.id)
        .with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None and plan.status=='awaiting_dispatch')
    prep,_=custody.read_started(db,p,task,plan)
    start_event=store._fresh(db,p,Audit,plan.data[start.POINTER]['audit_id'])
    saved_preview=store._fresh(db,p,Audit,plan.data[start.POINTER]['preview_id'])
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job_id)
        .with_for_update().execution_options(populate_existing=True))
    events=store._rows(db,p,Audit,Audit.action==leases.ACTION,Audit.object_id==plan.id)
    store.need(job is not None and job.id==plan.data[start.POINTER]['job_id'] and len(events)==1
        and type(lease_token) is str and job.lease_token==lease_token
        and job.lease_until>db.scalar(select(func.clock_timestamp())) and start_event.actor_id==p.user_id)
    lease_event=events[0];leases.verify(db,p,task,plan,job,start_event,lease_event)
    recheck_started=db.scalar(select(func.clock_timestamp()))
    safety.lock_current(db,p,task,prep,saved_preview.data['preview_record'])
    preview.verify_current_for_start(db,p,task,plan,prep,saved_preview,started=True,**leases._worker_kwargs(worker_proof))
    current._check_current(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
    recheck_completed=db.scalar(select(func.clock_timestamp()))
    store.need(type(recheck_started) is datetime and type(recheck_completed) is datetime
        and recheck_started.tzinfo is not None and recheck_completed.tzinfo is not None
        and recheck_started<=recheck_completed)
    claims.lock_and_check(db)
    duplicate=db.scalar(text('SELECT EXISTS(SELECT 1 FROM public.ga_test_advice_v2_dispatch_claims '
        'WHERE workspace_id=:workspace AND (job_id=:job OR plan_id=:plan))'),
        {'workspace':p.workspace_id,'job':job.id,'plan':plan.id})
    store.need(not duplicate, '原云账本已存在；保留原记录等待核对')
    model=store._fresh(db,p,Capability,prep['model_pin']['id'])
    prepared=copy.deepcopy(saved_preview.data['prepared'])
    store.need(model.id==prep['model_pin']['id'] and model.content_hash==prep['model_pin']['content_hash']
        and model.data['execution']['provider']=='deepseek' and type(prepared['token_bound']) is int
        and prepared['token_bound']>0 and prepared['request_hash']==digest(prepared['messages'])
        and prepared['dispatch_options_hash']==digest(prepared['options']))
    provider=policy._provider(db,p,model,lock=True);store.need(provider=='deepseek')
    accounting_started=_accounting_clock(db)
    day=_utc_day(accounting_started);key='cloud-budget:'+day
    db.execute(insert(Job).values(workspace_id=p.workspace_id,kind='cloud_budget',status='internal',
        idempotency_key=key,data={'day':day,'tokens':0,'calls':0}).on_conflict_do_nothing(
        index_elements=['workspace_id','idempotency_key']))
    daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key)
        .with_for_update().execution_options(populate_existing=True))
    accounting_checked=_accounting_clock(db)
    store.need(_utc_day(accounting_checked)==day, '日账本等待期间日期变化；保留申请稍后核对')
    admission=policy.budget_status(db,p,model,prepared['token_bound'],day=day)
    store.need(admission['available'] and admission['unlimited'] and admission['provider']=='deepseek')
    daily.data={**daily.data,'tokens':daily.data['tokens']+prepared['token_bound'],'calls':daily.data['calls']+1}
    call_id,audit_id=uid(),uid();clock=_accounting_clock(db)
    store.need(_utc_day(clock)==day and job.lease_until>clock, '日账本日期或租约已变化；未创建云账本')
    details={'schema':SCHEMA,'workspace_id':p.workspace_id,'actor_id':p.user_id,'task_id':task.id,
        'plan_id':plan.id,'job_id':job.id,'call_id':call_id,'audit_id':audit_id,
        'start_ref':{'id':start_event.id,'hash':digest(start_event.data)},
        'lease_ref':{'id':lease_event.id,'hash':digest(lease_event.data)},
        'preview_ref':{'id':saved_preview.id,'hash':digest(saved_preview.data)},
        'lease_token_hash':digest(lease_token),'model_pin':copy.deepcopy(prep['model_pin']),
        'policy_ref':copy.deepcopy(prep['budget']['policy_ref']),'request_hash':prepared['request_hash'],
        'dispatch_options_hash':prepared['dispatch_options_hash'],'token_bound':prepared['token_bound'],
        'source_recheck_started_at':recheck_started.astimezone(timezone.utc).isoformat(),
        'source_recheck_completed_at':recheck_completed.astimezone(timezone.utc).isoformat(),
        'accounting_checked_at':accounting_checked.astimezone(timezone.utc).isoformat(),
        'transport_authorized':False,'send_outcome':'unknown','usage_unavailable':True,
        'day':day,'reserved_at':clock.astimezone(timezone.utc).isoformat()}
    data={'day':day,'request_hash':prepared['request_hash'],'dispatch_options_hash':prepared['dispatch_options_hash'],
        'actor_id':p.user_id,policy.MARKER_KEY:policy.DEEPSEEK_UNLIMITED,KEY:copy.deepcopy(details)}
    call=CloudCall(id=call_id,workspace_id=p.workspace_id,capability_id=model.id,status='reserved',
        reserved_tokens=prepared['token_bound'],charged_tokens=prepared['token_bound'],data=data)
    receipt=Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,action=claims.ACTION,
        object_id=plan.id,created_at=clock,data=copy.deepcopy(details))
    db.add_all([call,receipt]);db.flush()
    db.execute(text('INSERT INTO public.ga_test_advice_v2_dispatch_claims '
        '(workspace_id,job_id,plan_id,cloud_call_id,audit_id) VALUES (:w,:j,:p,:c,:a)'),
        {'w':p.workspace_id,'j':job.id,'p':plan.id,'c':call_id,'a':audit_id})
    current._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'],**leases._worker_kwargs(worker_proof))
    safety.bind_commit_guard(db,p,task.id,prep,saved_preview.data['preview_record'])
    expected={'policy_ref':copy.deepcopy(prep['budget']['policy_ref']),'start_hash':digest(start_event.data),
        'lease_hash':digest(lease_event.data),'preview_hash':digest(saved_preview.data),
        'receipt_hash':digest(details),'cloud_data_hash':digest(data),'job_data_hash':digest(job.data),
        'plan_data_hash':digest(plan.data),'task_data_hash':digest(task.data),
        'model_id':model.id,'token_bound':prepared['token_bound'],
        'daily_id':daily.id,'daily_data_hash':digest(daily.data),'call_created_at':call.created_at,
        'audit_created_at':receipt.created_at,'scalar_pins':{name:_scalar_pin(row) for name,row in
            {'task':task,'plan':plan,'job':job,'start':start_event,'lease':lease_event,
             'preview':saved_preview,'audit':receipt,'cloud':call,'daily':daily}.items()}}
    _bind(db,p,task.id,plan.id,job.id,lease_token,lease_event.id,start_event.id,saved_preview.id,call_id,audit_id,expected,**leases._worker_kwargs(worker_proof))
    return {'cloud_call_id':call_id,'reservation_audit_id':audit_id,'transport_authorized':False,
        'send_outcome':'unknown','usage_unavailable':True}


@store.checked
def verify_saved_links(db,p,task,plan,prep,job,start_event,lease_event,preview_events,audit,call):
    """Fixed test-only accounting proof after the caller checked original custody.

    Never supplies a current authorization, transport intent, absence proof,
    or a right to call the provider. It is not a public callback hook.
    """
    from datetime import date
    from .temporal import instant
    claims.require_test_database(db)
    claims.lock_and_check(db)
    details=audit.data
    fields={'schema','workspace_id','actor_id','task_id','plan_id','job_id','call_id','audit_id',
        'start_ref','lease_ref','preview_ref','lease_token_hash','model_pin','policy_ref','request_hash',
        'dispatch_options_hash','token_bound','source_recheck_started_at','source_recheck_completed_at',
        'accounting_checked_at','transport_authorized','send_outcome','usage_unavailable','day','reserved_at'}
    store.need(type(details) is dict and set(details)==fields and details['schema']==SCHEMA
        and details['workspace_id']==p.workspace_id==call.workspace_id==audit.workspace_id
        and details['task_id']==task.id and details['plan_id']==plan.id and details['job_id']==job.id
        and details['call_id']==call.id and details['audit_id']==audit.id
        and details['actor_id']==audit.actor_id==start_event.actor_id and audit.action==claims.ACTION
        and audit.object_id==plan.id and audit.created_at==instant(details['reserved_at'])
        and details['transport_authorized'] is False and details['send_outcome']=='unknown'
        and details['usage_unavailable'] is True
        and details['start_ref']=={'id':start_event.id,'hash':digest(start_event.data)}
        and details['lease_ref']=={'id':lease_event.id,'hash':digest(lease_event.data)}
        and details['lease_token_hash']==digest(job.lease_token)
        and details['model_pin']==prep['model_pin'] and details['policy_ref']==prep['budget']['policy_ref'])
    matches=[event for event in preview_events if event.id==details['preview_ref'].get('id')]
    store.need(len(matches)==1 and details['preview_ref']=={'id':matches[0].id,'hash':digest(matches[0].data)}
        and matches[0].id==start_event.data['preview_ref']['id'])
    prepared=matches[0].data['prepared']
    store.need(details['request_hash']==prepared['request_hash']==digest(prepared['messages'])
        and details['dispatch_options_hash']==prepared['dispatch_options_hash']==digest(prepared['options'])
        and type(details['token_bound']) is int and details['token_bound']>0
        and details['token_bound']==prepared['token_bound'])
    started=instant(details['source_recheck_started_at']);done=instant(details['source_recheck_completed_at'])
    accounting=instant(details['accounting_checked_at']);reserved=instant(details['reserved_at'])
    store.need(started<=done<=accounting<=reserved and type(details['day']) is str
        and date.fromisoformat(details['day']).isoformat()==details['day']
        and _utc_day(accounting)==_utc_day(reserved)==details['day'])
    expected_data={'day':details['day'],'request_hash':details['request_hash'],
        'dispatch_options_hash':details['dispatch_options_hash'],'actor_id':details['actor_id'],
        policy.MARKER_KEY:policy.DEEPSEEK_UNLIMITED,KEY:copy.deepcopy(details)}
    store.need(call.status=='reserved' and call.capability_id==details['model_pin']['id']
        and call.reserved_tokens==call.charged_tokens==details['token_bound']
        and digest(call.data)==digest(expected_data))
    linked=db.execute(text('SELECT workspace_id,job_id,plan_id,cloud_call_id,audit_id FROM '
        'public.ga_test_advice_v2_dispatch_claims WHERE workspace_id=:workspace AND job_id=:job'),
        {'workspace':p.workspace_id,'job':job.id}).one_or_none()
    store.need(linked is not None and tuple(linked)==(p.workspace_id,job.id,plan.id,call.id,audit.id))
    daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget',
        Job.idempotency_key=='cloud-budget:'+details['day']).execution_options(populate_existing=True))
    store.need(daily is not None and daily.status=='internal' and daily.data['day']==details['day']
        and type(daily.data['tokens']) is int and daily.data['tokens']>=call.reserved_tokens
        and type(daily.data['calls']) is int and daily.data['calls']>=1)
    return {'schema':SCHEMA,'cloud_call_id':call.id,'reservation_audit_id':audit.id,
        'reservation_hash':digest(details),'transport_authorized':False,'send_outcome':'unknown'}
