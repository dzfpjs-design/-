"""Unrouted v2 anonymous preview with a durable, validated audit chain.

Only dedicated test databases may create records.  No Start, cloud call,
refund, provider retry, source acceptance or Goal acceptance is authorized.
The caller owns the transaction; unknown prior originals fail closed.
"""
import copy
from datetime import datetime
from sqlalchemy import func, or_, select
from . import same_goal_advice_analysis_v305 as analysis
from . import same_goal_advice_contracts_v302 as old_contracts
from . import same_goal_advice_file_intent_candidate_v2 as current_guard
from . import same_goal_advice_prepare_document_candidate_v2 as documents
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_preview_v307 as legacy
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_analysis_candidate_v2 as frame_builder
from . import same_goal_advice_unlimited_contracts_v2 as contracts
from . import same_goal_advice_unlimited_projection_v2 as projection
from . import same_goal_advice_unlimited_quote_v2 as money
from . import same_goal_advice_unlimited_store_v2 as custody
from . import workspace_advice_nonce_candidate_v2 as nonces
from . import workspace_command_nonce_lock_v312 as nonce_records
from . import evidence
from . import cloud_business_reasoning_v197 as transport
from . import workflow_evidence_consent_wait_v199 as consent
from .contracts import digest
from .models import Audit, Capability, Invocation, Job, Plan, Task, uid
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation
from .relations import lock_current_actor
from .temporal import instant

ACTION='same_goal_advice.unlimited_preview_prepared_v2'
SCHEMA='same-goal-advice-unlimited-preview-record-v2'
MAX_PREVIEWS=64
_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','actor_id','request','request_hash',
    'preparation_snapshot_hash','preview_generation','previous_preview_record_hash',
    'plan_version_before','plan_version_after','prepared_at','frame_snapshot','source_record',
    'preview_record','evidence_parts','projection','prepared','model_snapshot','money_quote','record_hash'}


def _document_from_receipt(prep, receipt):
    selection=prep['request']['document_source'];ref=receipt['evidence_ref']
    if selection['kind']=='approved_source_set':
        receipt_ref={'id':receipt['receipt_id'],'record_hash':receipt['record_hash']}
    else:
        receipt_ref={'id':selection['packet']['id'],
            'record_hash':receipt['original_packet_receipt']['producer_hash']}
    return {'selection':selection,'approved_selection':receipt['approved_selection'],
        'evidence_ref':ref,'receipt_ref':receipt_ref}


def _verify_evaluation(db,p,task,source):
    """Authenticate the original mature evaluation and its packet."""
    evaluation=store._fresh(db,p,Evaluation,source['mature_head']['evaluation_ref']['id'])
    store.need(evaluation.task_id==task.id and evaluation.record_hash==source['mature_head']['evaluation_ref']['record_hash']
        and evaluation.generation==source['evaluation_generation']
        and evaluation.data['original_query_ref']==source['original_query_ref']
        and evaluation.data['evaluation_query_ref']==source['evaluation_query_ref']
        and evaluation.data['cut_at']==source['evaluation_cut'])
    eval_job=store._fresh(db,p,Job,evaluation.job_id)
    call=store._fresh(db,p,Invocation,evaluation.invocation_id)
    eval_cap=store._fresh(db,p,Capability,call.capability_id)
    output=legacy.packets.saved_result(db,p,task,eval_job,call,eval_cap,eval_job.data['inputs'])
    store.need(output['evaluation_ref']==source['mature_head']['evaluation_ref']
        and output['packet']['evidence_id']==source['data_evidence_ref']['evidence_id'])


@store.checked
def validate_chain(db,p,task,plan,prep,document_receipt,events):
    """Authenticate append-only previews after v2 document custody checks.

    This is not a sender or a substitute for a fresh source/consent check.
    """
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    store.need(store._pin(advice)==prep['advice_capability_ref'])
    values=[]
    for event in events:
        value=event.data
        store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA)
        request=legacy.PreviewRequest.model_validate(value['request']).model_dump(mode='json')
        store.need(event.action==ACTION and event.id==value['audit_id'] and event.object_id==plan.id
            and event.workspace_id==value['workspace_id']==p.workspace_id
            and event.actor_id==value['actor_id'] and value['task_id']==task.id and value['plan_id']==plan.id
            and value['record_hash']==old_contracts.record_hash(value)
            and value['request_hash']==legacy._request_hash(task.id,plan.id,request)
            and value['preparation_snapshot_hash']==prep['record_hash']
            and request['expected_goal_hash']==prep['scope']['goal_hash']
            and request['expected_mature_head_hash']==prep['mature_head']['head_hash']
            and type(value['preview_generation']) is int and value['preview_generation']>=1
            and type(value['plan_version_before']) is int and value['plan_version_before']>=2
            and value['plan_version_before']==request['expected_plan_version']
            and value['plan_version_after']==value['plan_version_before']+1
            and event.created_at is not None and event.created_at.tzinfo is not None
            and event.created_at==instant(value['prepared_at'])
            and event.created_at==instant(value['money_quote']['quoted_at']))
        request_id=request['request_id']
        nonce_events=store._rows(db,p,Audit,Audit.action.in_(nonce_records.ACTIONS | nonces.EXTRA_ACTIONS),or_(
            Audit.data['request_id'].as_string()==request_id,Audit.data['request']['request_id'].as_string()==request_id,
            Audit.data['body']['request_id'].as_string()==request_id))
        nonce_plans=store._rows(db,p,Plan,or_(Plan.data['operations'].contains([{'request_id':request_id}]),
            Plan.data['revisions'].contains([{'request_id':request_id}])))
        store.need(len(nonce_events)==1 and nonce_events[0].id==event.id and not nonce_plans,
            '原预览编号与其他申请冲突，保留记录等待核对')
        source=value['source_record'];preview=value['preview_record']
        contracts.check_preview(preview,preparation=prep,source=source,manifest=advice.data)
        # Authenticate the original mature evaluation and its packet, not a
        # self-consistent client or cached frame. The document has already
        # passed the fixed v2 receipt verifier in the caller's custody read.
        _verify_evaluation(db,p,task,source)
        store.need(digest(source['document'])==digest(_document_from_receipt(prep,document_receipt))
            and value['frame_snapshot']['source_record']==source)
        refs=[source['data_evidence_ref'],source['document']['evidence_ref']]
        parts=value['evidence_parts'];pins=preview['evidence_pins'];grants=preview['consent_pins']
        store.need(len(parts)==len(refs)*2 and len(pins)==len(grants)==len(refs))
        for i,(ref,pin,grant) in enumerate(zip(refs,pins,grants)):
            file,raw=evidence.evidence_bytes(db,p,ref['evidence_id'])
            store.need(file.workspace_id==p.workspace_id and file.sha256==ref['sha256']
                and file.size_bytes==ref['size_bytes']==len(raw) and file.media_type==ref['media_type']
                and file.version>=pin['version'] and parts[i*2+1]=={'type':'text','text':raw.decode('utf-8-sig')})
            legacy._consent_original(db,p,grant,pin,ref,event.created_at)
        model=legacy._model(db,p,prep,live=False)
        store.need(digest(value['model_snapshot'])==digest(model.data))
        projected=projection.derive(value['frame_snapshot'],prep,evidence_parts=parts,evidence_pins=pins)
        store.need(digest(projected)==digest(value['projection']) and projected['cloud_request']==preview['cloud_request'])
        prepared=transport.prepare(model,legacy.projection.messages(projected),old_contracts.model_value_schema(),
            output_token_limit=prep['budget']['max_tokens'])
        store.need(digest(prepared)==digest(value['prepared'])
            and prepared['dispatch_options_hash']==preview['model_options_hash'])
        money.check_quote(value['money_quote'],None,now=event.created_at,prepared=prepared,
            model_pin=prep['model_pin'],model_snapshot=model.data,limits=prep['budget'])
        values.append((event,value))
    values.sort(key=lambda item:item[1]['preview_generation'])
    store.need([v['preview_generation'] for _,v in values]==list(range(1,len(values)+1))
        and len({v['request']['request_id'] for _,v in values})==len(values))
    previous=None
    for event,value in values:
        store.need(value['previous_preview_record_hash']==(previous['record_hash'] if previous else None)
            and value['plan_version_before']==(2 if previous is None else previous['plan_version_after']))
        previous=value
    store.need(plan.version==(previous['plan_version_after'] if previous else 2))
    return values


def _public(plan,prep,value):
    return {'schema':'same-goal-advice-unlimited-preview-view-v2','plan_id':plan.id,
        'request_id':value['request']['request_id'],'preview_hash':value['preview_record']['preview_hash'],
        'plan_version':value['plan_version_after'],'messages':copy.deepcopy(value['prepared']['messages']),
        'options':copy.deepcopy(value['prepared']['options']),'provider':'deepseek',
        'model':value['model_snapshot']['execution']['model'],'price_state':'unknown','estimate_minor_units':None,
        'can_start':False,'cloud_send_authorized':False,'preview_generated_cloud_calls':0}


@store.checked
def verify_current_for_start(db,p,task,plan,prep,event):
    """Recheck the saved preview against current originals, without starting.

    Callers must authenticate the preview chain and hold the task/actor locks.
    A successful return is not a dispatch grant or a substitute for commit-time
    session, policy, worker lease, and durable intent checks.
    """
    checked,_=custody.read_prepared(db,p,task,plan)
    store.need(digest(checked)==digest(prep) and (p.role=='admin' or p.user_id==prep['business_actor_id']))
    events=store._rows(db,p,Audit,Audit.action==ACTION,Audit.object_id==plan.id)
    store.need(bool(events) and event.action==ACTION and event.object_id==plan.id)
    saved_event=next((row for row in events if row.id==event.id),None)
    store.need(saved_event is not None and digest(saved_event.data)==digest(event.data)
        and saved_event.data['preview_generation']==len(events))
    original=saved_event.data
    current_guard._check_current(db,p,task.id,prep['budget']['policy_ref'])
    frame=frame_builder.build_current(db,p,task,plan)
    store.need(digest(frame)==digest(original['frame_snapshot'])
        and frame['source_record']==original['source_record'] and frame['model_pin']==prep['model_pin'])
    model=legacy._model(db,p,prep,live=True)
    store.need(digest(model.data)==digest(original['model_snapshot']))
    files=sources.cloud_documents(db,p,frame['evidence_ids'],model)
    saved=original['preview_record']
    store.need(files['model_pin']==prep['model_pin'] and files['parts']==original['evidence_parts']
        and len(files['evidence_pins'])==len(saved['evidence_pins'])==len(saved['consent_pins']))
    for current,pin,grant in zip(files['evidence_pins'],saved['evidence_pins'],saved['consent_pins']):
        store.need(set(current)==set(pin) and all(current[key]==pin[key] for key in pin if key!='version')
            and type(current['version']) is int and current['version']>=pin['version'])
        file,raw=evidence.evidence_bytes(db,p,pin['evidence_id'])
        store.need(file.workspace_id==p.workspace_id and file.sha256==pin['sha256']
            and file.size_bytes==pin['size_bytes']==len(raw) and file.version==current['version'])
        target={key:grant[key] for key in ('evidence_id','audit_id','audit_hash')}
        store.need(target in consent._grants(db,file,model) and grant['model_profile_id']==model.id)
        legacy._consent_original(db,p,grant,pin,pin,saved_event.created_at)
    projected=projection.derive(frame,prep,evidence_parts=files['parts'],evidence_pins=saved['evidence_pins'])
    prepared=transport.prepare(model,legacy.projection.messages(projected),old_contracts.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    store.need(digest(projected)==digest(original['projection']) and projected['cloud_request']==saved['cloud_request']
        and digest(prepared)==digest(original['prepared'])
        and prepared['dispatch_options_hash']==saved['model_options_hash'])
    money.check_quote(original['money_quote'],None,now=db.scalar(select(func.clock_timestamp())),prepared=prepared,
        model_pin=prep['model_pin'],model_snapshot=model.data,limits=prep['budget'])
    current_guard._check_current(db,p,task.id,prep['budget']['policy_ref'])
    return frame


@store.checked
def prepare(db,p,task_id,plan_id,body):
    guard.require_disposable_database(db)
    request=legacy.PreviewRequest.model_validate(body).model_dump(mode='json')
    store._reader(db,p,write=True)
    nonces.acquire(db,p,action=ACTION,task_id=task_id,body=request,subject_id=plan_id)
    task=store.existing._task(db,p,task_id,lock=True);lock_current_actor(db,p)
    plan=db.scalar(select(Plan).where(Plan.id==store.existing._id(plan_id),Plan.workspace_id==p.workspace_id,
        Plan.task_id==task.id).with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None)
    prep,job=custody.read_prepared(db,p,task,plan)
    store.need(p.role=='admin' or p.user_id==prep['business_actor_id'])
    events=store._rows(db,p,Audit,Audit.action==ACTION,or_(Audit.object_id==plan.id,
        Audit.data['plan_id'].as_string()==plan.id))
    receipt=store._rows(db,p,Audit,Audit.action==custody.DOCUMENT_PREPARED,Audit.object_id==plan.id)
    store.need(len(receipt)==1)
    chain=validate_chain(db,p,task,plan,prep,receipt[0].data,events)
    originals=[(event,value) for event,value in chain if value['request']['request_id']==request['request_id']]
    if originals:
        event,value=originals[0]
        store.need(value['actor_id']==p.user_id and digest(value['request'])==digest(request))
        return _public(plan,prep,value)
    store.need(len(chain)<MAX_PREVIEWS and plan.status=='awaiting_consent' and job.status=='succeeded'
        and job.lease_until is None and plan.version==request['expected_plan_version']
        and request['expected_goal_hash']==prep['scope']['goal_hash']
        and request['expected_mature_head_hash']==prep['mature_head']['head_hash'])
    frame=frame_builder.build_current(db,p,task,plan)
    model=legacy._model(db,p,prep,live=True)
    files=sources.cloud_documents(db,p,frame['evidence_ids'],model)
    store.need(files['model_pin']==prep['model_pin'])
    projected=projection.derive(frame,prep,evidence_parts=files['parts'],evidence_pins=files['evidence_pins'])
    prepared=transport.prepare(model,legacy.projection.messages(projected),old_contracts.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    current_guard._check_current(db,p,task.id,prep['budget']['policy_ref'])
    current_guard._bind_commit_guard(db,p,task.id,prep['budget']['policy_ref'])
    now=db.scalar(select(func.clock_timestamp()));store.need(type(now) is datetime and now.tzinfo is not None)
    quote=money.quote(None,now=now,prepared=prepared,model_pin=prep['model_pin'],
        model_snapshot=model.data,limits=prep['budget'])
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    preview=contracts.seal_preview({'schema':contracts.PREVIEW,'plan_id':plan.id,
        'preparation_snapshot_hash':prep['record_hash'],'source_hash':frame['source_record']['record_hash'],
        'advice_capability_ref':prep['advice_capability_ref'],'model_pin':prep['model_pin'],'budget':prep['budget'],
        'evidence_pins':files['evidence_pins'],'consent_pins':files['consent_pins'],
        'cloud_request':projected['cloud_request'],'cloud_request_hash':digest(projected['cloud_request']),
        'model_options_hash':prepared['dispatch_options_hash']},preparation=prep,
        source=frame['source_record'],manifest=advice.data)
    identity=uid();before=plan.version
    value={'schema':SCHEMA,'audit_id':identity,'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'actor_id':p.user_id,'request':request,'request_hash':legacy._request_hash(task.id,plan.id,request),
        'preparation_snapshot_hash':prep['record_hash'],'preview_generation':len(chain)+1,
        'previous_preview_record_hash':chain[-1][1]['record_hash'] if chain else None,
        'plan_version_before':before,'plan_version_after':before+1,'prepared_at':now.isoformat(),
        'frame_snapshot':copy.deepcopy(frame),'source_record':copy.deepcopy(frame['source_record']),
        'preview_record':preview,'evidence_parts':copy.deepcopy(files['parts']),'projection':projected,
        'prepared':prepared,'model_snapshot':copy.deepcopy(model.data),'money_quote':quote}
    value['record_hash']=old_contracts.record_hash(value)
    event=Audit(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
        object_id=plan.id,created_at=now,data=value)
    with db.begin_nested():
        db.add(event);plan.version=before+1;db.flush()
        custody.read_prepared(db,p,task,plan)
    return _public(plan,prep,value)
