"""One formal Start and its actual Job/SDK custody; no send or commit here.

The execution snapshot is a new immutable object. Preparation, document
receipts, previews and existing operations remain unchanged. ``checked`` is
a historical custody reader, not a live source, money or sending grant.
"""
import copy
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import func, or_, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_preview_v307 as previews
from . import workflow_evidence_consent_wait_v199 as consent
from .contracts import digest
from .models import Audit, Capability, CloudCall, Invocation, Job, Plan, uid
from .relations import lock_current_actor
from .temporal import instant

KEY = 'advice_execution_v308'
JOB_SCHEMA = 'same-goal-advice-send-job-v308'
STARTED = 'same_goal_advice.started_v308'
SCHEMA = 'same-goal-advice-start-record-v308'
POINTER = 'same-goal-advice-execution-pointer-v308'
PUBLIC = 'same-goal-advice-start-view-v308'
_FIELDS = {'schema','audit_id','workspace_id','task_id','plan_id','actor_id','request','body_hash',
    'preparation_snapshot_hash','snapshot','preview_ref','job_ref','started_at','record_hash'}
_JOB_PROGRESS = {'lease_recoveries','recovery_only','claimed_at','finished_at','error','failure_diagnostic_v289'}
_SDK_ACTIONS = {'same_goal_advice.prepared_v308','same_goal_advice.reserved_v308',
    'same_goal_advice.captured_v308','same_goal_advice.sdk_reconciled_v308'}
_PLAN_ACTIONS = {'same_goal_advice.result_ready_v308','same_goal_advice.money_reserved_v308',
    'same_goal_advice.money_transport_entered_v308'}
_SPENDING = 'same_goal_advice_spending_v308'
_RESULT = 'advice_result_v308'
need, guarded = previews.need, previews.checked


def job_key(task_id, plan_id):
    return 'same-goal-advice-send:' + task_id + ':' + plan_id


def input_for(plan, snapshot):
    return c.InvocationInput.model_validate({'schema':c.INPUT,
        'plan_ref':{'id':plan.id,'record_hash':snapshot['record_hash']},
        'preview_hash':snapshot['preview']['preview_hash']}).model_dump(mode='json')


def approval_for(plan, snapshot, job):
    return {'schema':c.APPROVAL,'plan_ref':{'id':plan.id,'record_hash':snapshot['record_hash']},
        'preview_hash':snapshot['preview']['preview_hash'],'job_id':job.id}


def _job_data(plan, prep, snapshot):
    return {'schema':JOB_SCHEMA,'plan_id':plan.id,'task_id':prep['scope']['task_id'],
        'actor_id':prep['business_actor_id'],'execution_snapshot_hash':snapshot['record_hash'],
        'invocation_key':job_key(prep['scope']['task_id'],plan.id)}


def _job_ref(job):
    return {'id':job.id,'kind':c.SEND_KIND,'schema':JOB_SCHEMA,'key':job.idempotency_key}


def _inventories(db,p,task,plan,prepare_job):
    key=job_key(task.id,plan.id)
    starts=store._rows(db,p,Audit,Audit.action==STARTED,or_(Audit.object_id==plan.id,
        Audit.data['plan_id'].as_string()==plan.id,Audit.data['job_ref']['key'].as_string()==key))
    jobs=store._rows(db,p,Job,or_(Job.idempotency_key==key,
        Job.data['plan_id'].as_string()==plan.id,
        Job.data['execution_snapshot_hash'].as_string()==plan.data.get(KEY,{}).get('snapshot_hash','')))
    # The original local preparation Job is the only other legitimate Job for
    # this Plan. Do not let its identity conceal another kind/key/payload.
    originals=[row for row in jobs if row.id==prepare_job.id]
    need(len(originals)==1 and originals[0].kind==c.PREPARE_KIND)
    sends=[row for row in jobs if row.id!=prepare_job.id]
    calls=store._rows(db,p,Invocation,or_(Invocation.idempotency_key==key,
        Invocation.data['job_id'].as_string().in_([row.id for row in jobs]),
        Invocation.data['approval']['plan_ref']['id'].as_string()==plan.id,
        Invocation.data['inputs']['plan_ref']['id'].as_string()==plan.id))
    return starts,sends,calls


def _paid_inventory(db,p,task,plan,prep,job,calls,*,snapshot=None,start=None):
    """Route positive paid-family originals to the ONE actual SDK and Job.

    This is deliberately not a receipt, Blob, price or sending validator. A
    missing SDK alongside any original must never become a first-send path.
    """
    ids=[call.id for call in calls];jobs=[job.id] if job is not None else []
    events=store._rows(db,p,Audit,Audit.action.in_(_SDK_ACTIONS|_PLAN_ACTIONS),or_(
        Audit.object_id.in_([plan.id,*ids]),Audit.data['plan_id'].as_string()==plan.id,
        Audit.data['job_id'].as_string().in_(jobs),Audit.data['send_job_id'].as_string().in_(jobs),
        Audit.data['invocation_id'].as_string().in_(ids)))
    cloud_ids=[]
    for event in events:
        for name in ('cloud_call_id','call_id'):
            identity=event.data.get(name)
            if identity is not None:cloud_ids.append(TypeAdapter(c.ID).validate_python(identity))
    for call in calls:
        state=call.data.get('same_goal_advice_v308')
        if type(state) is dict and state.get('call_id') is not None:
            cloud_ids.append(TypeAdapter(c.ID).validate_python(state['call_id']))
    ledgers=store._rows(db,p,CloudCall,or_(CloudCall.id.in_(cloud_ids),
        CloudCall.data['gateway_dispatch']['identity']['job_id'].as_string().in_(jobs),
        CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string().in_(ids),
        CloudCall.data[_SPENDING]['plan_id'].as_string()==plan.id))
    if not calls:
        need(not events and not ledgers and plan.data.get(_RESULT) is None);return
    need(len(calls)==1 and job is not None and snapshot is not None and start is not None
        and len(ledgers)<=1 and len({event.action for event in events})==len(events))
    sdk=calls[0]
    for event in events:
        value=event.data;job_field='send_job_id' if event.action in {
            'same_goal_advice.sdk_reconciled_v308','same_goal_advice.result_ready_v308'} else 'job_id'
        hash_field='execution_hash' if event.action in {
            'same_goal_advice.money_reserved_v308','same_goal_advice.money_transport_entered_v308'} else 'execution_snapshot_hash'
        need(event.object_id==(sdk.id if event.action in _SDK_ACTIONS else plan.id)
            and event.actor_id==value.get('actor_id')==prep['business_actor_id']
            and value.get('workspace_id')==p.workspace_id and value.get('task_id')==task.id
            and value.get('plan_id')==plan.id and value.get('invocation_id')==sdk.id
            and value.get(job_field)==job.id and value.get(hash_field)==snapshot['record_hash'])
        for name in ('job_id','send_job_id'):
            if name in value:need(value[name]==job.id)
        if 'start_ref' in value:need(value['start_ref']=={'id':start.id,'record_hash':digest(start.data)})
    for ledger in ledgers:
        data=ledger.data;identity=data['gateway_dispatch']['identity']
        need(ledger.capability_id==prep['model_pin']['id'] and type(identity) is dict
            and identity.get('workspace_id')==p.workspace_id and identity.get('actor_id')==prep['business_actor_id']
            and identity.get('capability_id')==ledger.capability_id and identity.get('task_id')==task.id
            and identity.get('job_id')==job.id and identity.get('suggestion_id')==sdk.id)
        if _SPENDING in data:
            need(type(data[_SPENDING]) is dict and data[_SPENDING].get('plan_id')==plan.id)
    # An actual row reference may not silently disappear from the inventory.
    need(set(cloud_ids)<={ledger.id for ledger in ledgers})


@guarded
def custody(db,p,task,plan,prep,prepare_job):
    """Return actual immutable Start custody, or None only for empty inventory.

    Caller authenticates original preparation custody before entering; this
    function deliberately never calls store._saved/_inventory or _history.
    Unknown/failed SDKs are returned as such, never erased or replayed.
    """
    need(task.workspace_id==plan.workspace_id==prepare_job.workspace_id==p.workspace_id
        and plan.task_id==task.id and prep['scope']['task_id']==task.id
        and prep['scope']['analysis_plan_id']==plan.id)
    starts,jobs,calls=_inventories(db,p,task,plan,prepare_job)
    pointer=plan.data.get(KEY)
    if pointer is None:
        need(not starts and not jobs and not calls)
        return None
    need(type(pointer) is dict and set(pointer)=={'schema','start_ref','snapshot_hash','job_ref'}
        and pointer['schema']==POINTER and len(starts)==len(jobs)==1 and len(calls)<=1)
    c.Ref.model_validate(pointer['start_ref'])
    start=starts[0];value=start.data
    need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
        and value['record_hash']==c.record_hash(value) and start.id==value['audit_id']==pointer['start_ref']['id']
        and pointer['start_ref']['record_hash']==digest(value) and start.object_id==plan.id
        and start.workspace_id==value['workspace_id']==p.workspace_id
        and value['task_id']==task.id and value['plan_id']==plan.id and start.actor_id==value['actor_id']
        and value['preparation_snapshot_hash']==prep['record_hash']
        and start.created_at is not None and start.created_at.tzinfo is not None
        and instant(value['started_at'])==start.created_at)
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    need(store._pin(advice)==prep['advice_capability_ref']
        and advice.content_hash==advice.validated_hash==digest(advice.data))
    snapshot=c.check_plan(value['snapshot'],preparation=prep,manifest=advice.data)
    body=c.Start.model_validate(value['request']).model_dump(mode='json')
    need(snapshot['record_hash']==pointer['snapshot_hash']
        and body==snapshot['start_request'] and value['body_hash']==snapshot['start_request_hash']==digest(body))
    chain=previews._chain(db,p,task,plan,prep)
    matches=[(row,data) for row,data in chain if row.id==value['preview_ref'].get('id')]
    need(len(matches)==1);preview,original=matches[0]
    need(set(value['preview_ref'])=={'id','record_hash'}
        and value['preview_ref']['record_hash']==original['record_hash']
        and preview.id==chain[-1][0].id and preview.created_at<=start.created_at
        and original['preview_record']==snapshot['preview'] and original['source_record']==snapshot['source_record']
        and body['expected_plan_version']==original['plan_version_after']
        and plan.version>=body['expected_plan_version']+1)
    job=jobs[0];key=job_key(task.id,plan.id)
    expected=_job_data(plan,prep,snapshot)
    need(job.workspace_id==p.workspace_id and job.kind==c.SEND_KIND and job.idempotency_key==key
        and type(job.data) is dict and set(expected)<=set(job.data)<=set(expected)|_JOB_PROGRESS
        and all(digest(job.data[name])==digest(content) for name,content in expected.items())
        and value['job_ref']==pointer['job_ref']==_job_ref(job)
        and job.created_at is not None and job.created_at.tzinfo is not None and job.created_at==start.created_at)
    for call in calls:
        data=call.data
        need(call.workspace_id==p.workspace_id and call.idempotency_key==key
            and call.capability_id==advice.id and type(data) is dict
            and data.get('job_id')==job.id and data.get('role')=='business'
            and data.get('capability_hash')==advice.content_hash
            and digest(data.get('capability_snapshot'))==digest(advice.data)
            and data.get('inputs')==input_for(plan,snapshot) and data.get('input_hash')==digest(input_for(plan,snapshot))
            and data.get('approval')==approval_for(plan,snapshot,job)
            and type(data.get('side_effect')) is bool
            and data['side_effect']==bool(advice.data['execution'].get('side_effect'))
            and call.created_at is not None and call.created_at.tzinfo is not None and call.created_at>=start.created_at)
        # The SDK token is original, never compared to a reclaimed Job token.
        TypeAdapter(c.ID).validate_python(data['lease_token'])
    return {'start':start,'snapshot':copy.deepcopy(snapshot),'preview':preview,'job':job,'calls':calls}


@guarded
def checked(db,p,task,plan,prep,prepare_job):
    value=custody(db,p,task,plan,prep,prepare_job)
    if value is None:
        # Even an otherwise empty Start inventory cannot erase orphan money
        # or transport evidence and thereby create permission for a new SDK.
        _paid_inventory(db,p,task,plan,prep,None,[])
    else:
        _paid_inventory(db,p,task,plan,prep,value['job'],value['calls'],
            snapshot=value['snapshot'],start=value['start'])
    return value


def _live_preview(db,p,task,plan,prep,event,now):
    """Authenticate the exact displayed input at current source/consent/price.

    Later consent versions and additional legal grants may exist. The frozen
    chosen grant must still be legal; it need not be today's chosen minimum.
    """
    original=event.data
    frame=previews.frames.build_current(db,p,task,plan)
    need(digest(frame)==digest(original['frame_snapshot'])
        and frame['source_record']==original['source_record'] and frame['model_pin']==prep['model_pin'])
    model=previews._model(db,p,prep,live=True)
    need(digest(model.data)==digest(original['model_snapshot']))
    files=previews.sources.cloud_documents(db,p,frame['evidence_ids'],model)
    preview=original['preview_record']
    need(files['model_pin']==prep['model_pin'] and files['parts']==original['evidence_parts']
        and len(files['evidence_pins'])==len(preview['evidence_pins'])==len(preview['consent_pins']))
    for current,pin,grant in zip(files['evidence_pins'],preview['evidence_pins'],preview['consent_pins']):
        need(set(current)==set(pin) and all(current[key]==pin[key] for key in pin if key!='version')
            and type(current['version']) is int and current['version']>=pin['version'])
        file,content=previews.evidence.evidence_bytes(db,p,pin['evidence_id'])
        need(file.workspace_id==p.workspace_id and file.sha256==pin['sha256']
            and file.size_bytes==pin['size_bytes']==len(content) and file.version==current['version'])
        actual=consent._grants(db,file,model)
        target={key:grant[key] for key in ('evidence_id','audit_id','audit_hash')}
        need(target in actual and grant['model_profile_id']==model.id)
        previews._consent_original(db,p,grant,pin,pin,event.created_at)
    projected=previews.projection.derive(frame,prep,evidence_parts=files['parts'],evidence_pins=preview['evidence_pins'])
    prepared=previews.transport.prepare(model,previews.projection.messages(projected),c.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    need(digest(projected)==digest(original['projection']) and projected['cloud_request']==preview['cloud_request']
        and digest(prepared)==digest(original['prepared'])
        and prepared['dispatch_options_hash']==preview['model_options_hash'])
    price=previews.prices.current(db,p,prep['model_pin'],basis_ref=original['price_basis_ref'])
    need(price['basis_ref']==original['price_basis_ref'] and price['evidence_ref']==original['price_evidence_ref']
        and digest(price['price'])==digest(original['price_snapshot']))
    previews.money.check_quote(original['money_quote'],price['price'],now=now,prepared=prepared,
        model_pin=prep['model_pin'],model_snapshot=model.data,budget=prep['budget'])
    return frame


def _view(plan,value):
    return {'schema':PUBLIC,'plan_id':plan.id,'request_id':value['start'].data['request']['request_id'],
        'plan_version':plan.version,'status':plan.status,'send_job_id':value['job'].id,
        'send_job_status':value['job'].status,'execution_snapshot_hash':value['snapshot']['record_hash'],
        'preview_hash':value['snapshot']['preview']['preview_hash'],
        'invocation_created':bool(value['calls']),
        'message':'启动申请已保存，实际执行进度以原发送任务和调用记录为准'}


def _budget_calls(db,p,task):
    """One reservation per genuine Start, including unknown/terminal originals.

    SDK creation never adds a second reservation. Reverse inventories detect
    missing Plans/Start rows instead of silently freeing their paid capacity.
    This cold reader neither checks today's head nor grants a new call.
    """
    prefix='same-goal-advice-send:'+task.id+':'
    plans=store._rows(db,p,Plan,or_((Plan.task_id==task.id)&(Plan.data['schema'].as_string()==c.PLAN),
        Plan.data['preparation_snapshot']['scope']['task_id'].as_string()==task.id))
    ids=[plan.id for plan in plans]
    starts=store._rows(db,p,Audit,Audit.action==STARTED,or_(Audit.object_id.in_(ids),
        Audit.data['task_id'].as_string()==task.id,
        Audit.data['job_ref']['key'].as_string().startswith(prefix,autoescape=True)))
    jobs=store._rows(db,p,Job,or_(Job.idempotency_key.startswith(prefix,autoescape=True),
        (Job.kind==c.SEND_KIND)&(Job.data['task_id'].as_string()==task.id),
        (Job.kind==c.SEND_KIND)&Job.data['plan_id'].as_string().in_(ids)))
    calls=store._rows(db,p,Invocation,or_(Invocation.idempotency_key.startswith(prefix,autoescape=True),
        Invocation.data['job_id'].as_string().in_([job.id for job in jobs]),
        Invocation.data['approval']['plan_ref']['id'].as_string().in_(ids),
        Invocation.data['inputs']['plan_ref']['id'].as_string().in_(ids)))
    saved=[]
    for plan in plans:
        prep,prepare_job=store._saved(db,p,task,plan)
        value=checked(db,p,task,plan,prep,prepare_job)
        if value is not None:saved.append(value)
    need(len({plan.id for plan in plans})==len(plans)
        and {row.id for row in starts}=={value['start'].id for value in saved}
        and {row.id for row in jobs}=={value['job'].id for value in saved}
        and {row.id for row in calls}=={row.id for value in saved for row in value['calls']})
    return len(saved)


@guarded
def budget_calls(db,p,task):
    # GoalCounter may enter outside a surrounding no-autoflush context.
    with db.no_autoflush:return _budget_calls(db,p,task)


@guarded
def start(db,p,task_id,plan_id,body):
    body=c.Start.model_validate(body).model_dump(mode='json');store._reader(db,p,write=True)
    store.command_nonce.acquire(db,p,action=STARTED,task_id=task_id,body=body,subject_id=plan_id)
    task,plan,prep,prepare_job=previews._plan(db,p,task_id,plan_id,lock=True)
    lock_current_actor(db,p)
    need(p.role=='admin' or p.user_id==prep['business_actor_id'])
    original=checked(db,p,task,plan,prep,prepare_job)
    if original is not None:
        need(original['start'].actor_id==p.user_id and original['start'].data['request']==body)
        return _view(plan,original)
    from .same_goal_reasoning_bridge_v339 import inspect as inspect_bridge
    inspect_bridge(db,p,task,plan,prep,prepare_job,for_send=True)
    chain=previews._chain(db,p,task,plan,prep)
    need(chain and plan.status=='awaiting_consent' and prepare_job.status=='succeeded'
        and prepare_job.lease_until is None and plan.version==body['expected_plan_version']==chain[-1][1]['plan_version_after']
        and body['preview_hash']==chain[-1][1]['preview_record']['preview_hash']
        and body['expected_goal_hash']==prep['scope']['goal_hash']
        and body['expected_mature_head_hash']==prep['mature_head']['head_hash'])
    collisions=store._rows(db,p,Audit,Audit.action==STARTED,Audit.data['task_id'].as_string()==task.id,
        Audit.data['request']['request_id'].as_string()==body['request_id'])
    need(not collisions)
    now=db.scalar(select(func.clock_timestamp()));need(type(now) is datetime and now.tzinfo is not None)
    event,displayed=chain[-1];need(event.created_at<=now)
    frame=_live_preview(db,p,task,plan,prep,event,now)
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    snapshot=c.seal_plan({'schema':c.PLAN,'scope':copy.deepcopy(prep['scope']),
        'preparation_snapshot_hash':prep['record_hash'],'source_record':copy.deepcopy(frame['source_record']),
        'preview':copy.deepcopy(displayed['preview_record']),'start_request':body,'start_request_hash':digest(body)},
        preparation=prep,manifest=advice.data)
    key=job_key(task.id,plan.id);identity=uid();job=Job(id=uid(),workspace_id=p.workspace_id,
        kind=c.SEND_KIND,idempotency_key=key,status='queued',attempts=0,created_at=now,available_at=now,
        data=_job_data(plan,prep,snapshot))
    value={'schema':SCHEMA,'audit_id':identity,'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'actor_id':p.user_id,'request':body,'body_hash':digest(body),'preparation_snapshot_hash':prep['record_hash'],
        'snapshot':snapshot,'preview_ref':{'id':event.id,'record_hash':displayed['record_hash']},
        'job_ref':_job_ref(job),'started_at':now.isoformat()}
    value['record_hash']=c.record_hash(value)
    audit=Audit(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,action=STARTED,
        object_id=plan.id,created_at=now,data=value)
    with db.begin_nested():
        db.add(job);db.add(audit)
        plan.data={**plan.data,KEY:{'schema':POINTER,'start_ref':{'id':identity,'record_hash':digest(value)},
            'snapshot_hash':snapshot['record_hash'],'job_ref':_job_ref(job)}}
        plan.status='generating';plan.version+=1;db.flush()
        return _view(plan,checked(db,p,task,plan,prep,prepare_job))
