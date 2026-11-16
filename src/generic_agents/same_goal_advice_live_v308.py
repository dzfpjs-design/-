"""Actual send authority, distinct from immutable paid-answer custody.

No caller DTO, remembered permission or fabricated graph node grants a send.
The budget Session authenticates its typed server scope against the same Job;
the worker Session additionally needs its installed transaction fence.
"""
import copy

from sqlalchemy import func, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_execution_v308 as execution
from . import same_goal_advice_analysis_v305 as analysis
from . import same_goal_advice_frame_v305 as frames
from . import same_goal_advice_preview_v307 as previews
from . import same_goal_advice_quota_v304 as quota
from . import workflow_evidence_consent_wait_v199 as consent
from . import leases
from .contracts import digest
from .models import Capability, Invocation, Job, Plan, Task
from .security import Principal

need = store.need


def _row(db,p,kind,identity,*,lock=False):
    query=select(kind).where(kind.workspace_id==p.workspace_id,kind.id==identity)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True))
    need(row is not None)
    return row


def _history(db,p,cap,inputs,key,call=None):
    """Protected records only; no today's IAM, source IO, price or lease."""
    need(type(p) is Principal and type(cap) is Capability)
    inputs=c.InvocationInput.model_validate(inputs).model_dump(mode='json')
    plan=_row(db,p,Plan,inputs['plan_ref']['id'])
    task=_row(db,p,Task,plan.task_id)
    prep,prepare_job=store._saved_base(db,p,task,plan)
    value=execution.custody(db,p,task,plan,prep,prepare_job)
    need(value is not None and cap.id==prep['advice_capability_ref']['id']
        and store._pin(cap)==prep['advice_capability_ref']
        and inputs==execution.input_for(plan,value['snapshot'])
        and key==value['job'].idempotency_key)
    if call is not None:
        need(type(call) is Invocation and len(value['calls'])==1
            and value['calls'][0].id==call.id and call.workspace_id==p.workspace_id)
    return {'task':task,'plan':plan,'prep':prep,'prepare_job':prepare_job,**value}


@store.checked
def historical(db,p,cap,inputs,key,call):
    # Public cold_result performs its actual reader check before this internal
    # custody reader. The original actor can lose permission after a paid POST.
    with db.no_autoflush:
        return _history(db,p,cap,inputs,key,call)


def _lease(db,p,job,token):
    need(type(token) is str and bool(token) and job.data['actor_id']==p.user_id
        and job.kind==c.SEND_KIND and job.data['schema']==execution.JOB_SCHEMA
        and job.status=='running' and job.lease_token==token)
    need(db.scalar(select(Job.id).where(Job.id==job.id,Job.workspace_id==p.workspace_id,
        Job.kind==c.SEND_KIND,Job.status=='running',Job.lease_token==token,
        Job.lease_until>func.clock_timestamp()))==job.id)


def _owner(db,p,cap,inputs,key,*,scope=None):
    # Learn Task identity without taking a reverse Plan lock. Mutating send
    # paths then serialize Task -> Plan -> Job -> SDK, in that order.
    inputs=c.InvocationInput.model_validate(inputs).model_dump(mode='json')
    lookup=_row(db,p,Plan,inputs['plan_ref']['id'])
    task=_row(db,p,Task,lookup.task_id,lock=True)
    plan=_row(db,p,Plan,lookup.id,lock=True)
    prep,prepare_job=store._saved(db,p,task,plan)
    value=execution.checked(db,p,task,plan,prep,prepare_job)
    need(value is not None)
    job=_row(db,p,Job,value['job'].id,lock=True)
    if scope is None:
        owned=leases.ownership(db)
        need(leases.fenced(db) and set(owned)=={'job_id','lease_token'} and owned['job_id']==job.id)
        token=owned['lease_token']
    else:
        need(scope['workspace_id']==p.workspace_id and scope['actor_id']==p.user_id
            and scope['task_id']==task.id and scope['plan_id']==plan.id
            and scope['job_id']==job.id and scope['execution_hash']==value['snapshot']['record_hash'])
        token=scope['lease_token']
    _lease(db,p,job,token)
    store._reader(db,p,write=True)
    need(prep['business_actor_id']==p.user_id and plan.status=='generating'
        and cap.id==prep['advice_capability_ref']['id'] and store._pin(cap)==prep['advice_capability_ref']
        and inputs==execution.input_for(plan,value['snapshot']) and key==job.idempotency_key)
    calls=value['calls']
    if calls:
        sdk=_row(db,p,Invocation,calls[0].id,lock=True)
        if scope is not None:need(scope['invocation_id']==sdk.id)
    else:
        need(scope is None);sdk=None
    return {'task':task,'plan':plan,'prep':prep,'prepare_job':prepare_job,**value,
        'job':job,'invocation':sdk}


def _files(db,p,context,model):
    preview=context['preview'].data;snapshot=context['snapshot'];pins=snapshot['preview']['evidence_pins']
    ids=[pin['evidence_id'] for pin in pins]
    files=previews.sources.cloud_documents(db,p,ids,model)
    need(files['model_pin']==context['prep']['model_pin'] and files['parts']==preview['evidence_parts']
        and len(files['evidence_pins'])==len(pins)==len(snapshot['preview']['consent_pins']))
    for current,pin,grant in zip(files['evidence_pins'],pins,snapshot['preview']['consent_pins']):
        need(set(current)==set(pin) and all(current[name]==pin[name] for name in pin if name!='version')
            and type(current['version']) is int and current['version']>=pin['version'])
        file,content=previews.evidence.evidence_bytes(db,p,pin['evidence_id'])
        need(file.sha256==pin['sha256'] and len(content)==pin['size_bytes']
            and file.version==current['version'])
        need({name:grant[name] for name in ('evidence_id','audit_id','audit_hash')}
            in consent._grants(db,file,model) and grant['model_profile_id']==model.id)
        previews._consent_original(db,p,grant,pin,pin,context['preview'].created_at)
    # Later legitimate grants/version increments do not rewrite the displayed
    # request. Byte equality and the ORIGINAL still-valid selected grant do.
    return copy.deepcopy(pins),copy.deepcopy(files['parts'])


def _live(db,p,cap,context):
    from .goal_queries import call_count
    from .native_goals import total_steps
    task,plan,prep=context['task'],context['plan'],context['prep']
    if context['invocation'] is None:
        from .same_goal_reasoning_bridge_v339 import inspect as inspect_bridge
        inspect_bridge(db,p,task,plan,prep,context['prepare_job'],for_send=True)
    configured=quota._configuration_base(db,p,task,prep['request'])
    need(call_count(db,p,task)<=configured['goal']['spec']['max_tool_calls']
        and total_steps(db,p,task)<=configured['goal']['spec']['max_steps'])
    selected=analysis._match_scope(db,p,task,plan,prep,configured)
    resolved=analysis.verified_sources(db,p,task,plan,prep,context['prepare_job'],selected)
    frame=frames.from_resolved(db,p,task,plan,resolved)
    original=context['preview'].data;snapshot=context['snapshot']
    need(digest(frame)==digest(original['frame_snapshot'])
        and frame['source_record']==snapshot['source_record'])
    model=previews._model(db,p,prep,live=True)
    need(digest(model.data)==digest(original['model_snapshot']))
    pins,parts=_files(db,p,context,model)
    projected=previews.projection.derive(frame,prep,evidence_parts=parts,evidence_pins=pins)
    prepared=previews.transport.prepare(model,previews.projection.messages(projected),c.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    need(digest(projected)==digest(original['projection'])
        and projected['cloud_request']==snapshot['preview']['cloud_request']
        and digest(prepared)==digest(original['prepared']))
    price=previews.prices.current(db,p,prep['model_pin'],basis_ref=original['price_basis_ref'])
    need(price['basis_ref']==original['price_basis_ref'] and price['evidence_ref']==original['price_evidence_ref']
        and digest(price['price'])==digest(original['price_snapshot']))
    previews.money.check_quote(original['money_quote'],price['price'],now=quota._clock(db),
        prepared=prepared,model_pin=prep['model_pin'],model_snapshot=model.data,budget=prep['budget'])
    return {**context,'model':model,'prepared':prepared,'quote':copy.deepcopy(original['money_quote']),
        'price_ref':copy.deepcopy(original['price_basis_ref'])}


@store.checked
def authorize_gateway(db,p,cap,role,inputs,key,approval):
    need(role=='business')
    with db.no_autoflush:
        context=_owner(db,p,cap,inputs,key)
        need(approval==execution.approval_for(context['plan'],context['snapshot'],context['job']))
        # Existing SDKs can only enter original-answer recovery. Fresh-source
        # expiry cannot turn recovery into permission for a second POST.
        if context['invocation'] is None:_live(db,p,cap,context)


@store.checked
def current(db,p,cap,inputs,key):
    with db.no_autoflush:
        context=_live(db,p,cap,_owner(db,p,cap,inputs,key))
        return cap,context['model'],context['task'],context['job'],context['plan'],context['snapshot']


def _owned_context(db,p):
    owner=leases.ownership(db)
    need(leases.fenced(db) and set(owner)=={'job_id','lease_token'})
    job=_row(db,p,Job,owner['job_id'])
    need(job.kind==c.SEND_KIND and job.data['schema']==execution.JOB_SCHEMA)
    plan=_row(db,p,Plan,job.data['plan_id'])
    task=_row(db,p,Task,job.data['task_id'])
    prep,prepare_job=store._saved(db,p,task,plan)
    value=execution.checked(db,p,task,plan,prep,prepare_job);need(value is not None)
    cap=_row(db,p,Capability,prep['advice_capability_ref']['id'])
    return cap,_owner(db,p,cap,execution.input_for(plan,value['snapshot']),job.idempotency_key)


@store.checked
def qualify(db,p,task,inputs):
    with db.no_autoflush:
        cap,context=_owned_context(db,p)
        need(task.id==context['task'].id and inputs==execution.input_for(context['plan'],context['snapshot']))
        _live(db,p,cap,context)


@store.checked
def evidence(db,p,ids,model):
    with db.no_autoflush:
        _cap,context=_owned_context(db,p)
        need(store._pin(model)==context['prep']['model_pin']
            and ids==[pin['evidence_id'] for pin in context['snapshot']['preview']['evidence_pins']])
        return _files(db,p,context,model)


@store.checked
def spending_context(db,p,model,token_bound,request_hash):
    from . import same_goal_advice_spending_v308 as spending
    from . import same_goal_advice_runtime_v308 as runtime
    scope=spending.current_scope();need(scope is not None)
    with db.no_autoflush:
        plan=_row(db,p,Plan,scope['plan_id'])
        task=_row(db,p,Task,scope['task_id'])
        prep,prepare_job=store._saved(db,p,task,plan)
        value=execution.checked(db,p,task,plan,prep,prepare_job);need(value is not None)
        cap=_row(db,p,Capability,prep['advice_capability_ref']['id'])
        inputs=execution.input_for(plan,value['snapshot']);key=value['job'].idempotency_key
        context=_live(db,p,cap,_owner(db,p,cap,inputs,key,scope=scope))
        need(store._pin(model)==prep['model_pin'] and request_hash==context['prepared']['request_hash']
            and token_bound==context['prepared']['token_bound'])
        call=context['invocation'];need(call is not None)
        state=runtime._checked_state(db,p,cap,inputs,key,call)
        context['frozen_request_hash']=state['request']['request_hash']
        return context
