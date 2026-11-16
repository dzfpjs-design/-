"""Protected, append-only anonymous previews; no SDK, send or consent writes.

Each explicit command appends one chained Audit and advances only Plan.version.
Historical reads authenticate original bytes and price at the protected quote
time; they never gather new facts, fetch today's price or authorize a sender.
"""
import copy
from datetime import datetime
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import func,or_,select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_frame_v305 as frames
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_preparation_v304 as documents
from . import same_goal_advice_projection_v307 as projection
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_money_v305 as money
from . import cloud_business_reasoning_v197 as transport
from . import workflow_goal_evaluation_packet_v288 as packets
from . import evidence
from .workflow_evidence_consent_wait_v199 import _grant_hash
from .contracts import digest
from .gateway import ModelBlocked
from .models import Audit,Capability,Invocation,Job,Plan,Task,uid
from .relations import lock_current_actor
from .services import published
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

ACTION='same_goal_advice.preview_prepared_v307'
SCHEMA='same-goal-advice-preview-record-v307'
PUBLIC='same-goal-advice-preview-view-v307'
MAX_PREVIEWS=64
ERROR='原业务建议预览、准确文本、授权或价格原件无法核对；未重建、续价或发送模型'
_FIELDS={'schema','audit_id','workspace_id','task_id','plan_id','actor_id','request','request_hash',
    'preparation_snapshot_hash','preview_generation','previous_preview_record_hash',
    'plan_version_before','plan_version_after','prepared_at','frame_snapshot','source_record','preview_record',
    'evidence_parts','projection','prepared','model_snapshot','price_snapshot','price_basis_ref',
    'price_evidence_ref','money_quote','record_hash'}


class PreviewRequest(c.Closed):
    request_id:c.ID
    expected_plan_version:c.Positive
    expected_goal_hash:c.Hash
    expected_mature_head_hash:c.Hash
    reason:c.Text


def need(value):
    if not value:raise HTTPException(409,ERROR)


def checked(function):
    @wraps(function)
    def call(*args,**kwargs):
        try:return function(*args,**kwargs)
        except HTTPException:raise
        except (ValueError,TypeError,KeyError,AttributeError,IndexError,StopIteration,
                UnicodeError,RecursionError,OverflowError,OSError,ModelBlocked):
            raise HTTPException(409,ERROR) from None
    return call


def _request_hash(task_id,plan_id,body):
    return digest({'task_id':task_id,'plan_id':plan_id,'body':body})


def _plan(db,p,task_id,plan_id,*,lock=False):
    task=store.existing._task(db,p,task_id,lock=lock)
    query=select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id,
        Plan.id==store.existing._id(plan_id))
    if lock:query=query.with_for_update()
    plan=db.scalar(query.execution_options(populate_existing=True))
    if plan is None:raise HTTPException(404,'原业务建议计划不存在或不属于当前任务')
    prep,job=store._saved(db,p,task,plan)
    return task,plan,prep,job


def _chain(db,p,task,plan,prep):
    rows=store._rows(db,p,Audit,Audit.action==ACTION,or_(Audit.object_id==plan.id,
        Audit.data['plan_id'].as_string()==plan.id))
    need(len(rows)<=MAX_PREVIEWS)
    values=[]
    for row in rows:
        value=row.data;need(type(value) is dict and set(value)==_FIELDS)
        request=PreviewRequest.model_validate(value['request']).model_dump(mode='json')
        need(value['schema']==SCHEMA and row.id==value['audit_id'] and row.object_id==plan.id
            and row.workspace_id==value['workspace_id']==p.workspace_id
            and value['task_id']==task.id and value['plan_id']==plan.id
            and row.actor_id==value['actor_id'] and value['preparation_snapshot_hash']==prep['record_hash']
            and value['record_hash']==c.record_hash(value)
            and value['request_hash']==_request_hash(task.id,plan.id,request)
            and request['expected_goal_hash']==prep['scope']['goal_hash']
            and request['expected_mature_head_hash']==prep['mature_head']['head_hash']
            and type(value['preview_generation']) is int and value['preview_generation']>=1
            and type(value['plan_version_before']) is int and value['plan_version_before']>=1
            and value['plan_version_before']==request['expected_plan_version']
            and type(value['plan_version_after']) is int and value['plan_version_after']==value['plan_version_before']+1
            and row.created_at is not None and row.created_at.tzinfo is not None
            and row.created_at==instant(value['prepared_at']))
        values.append((row,value))
    values.sort(key=lambda entry:entry[1]['preview_generation'])
    need([value['preview_generation'] for _,value in values]==list(range(1,len(values)+1))
        and len({value['request']['request_id'] for _,value in values})==len(values))
    previous=None
    for row,value in values:
        need(value['previous_preview_record_hash']==(previous['record_hash'] if previous else None))
        if previous:need(value['plan_version_before']==previous['plan_version_after'])
        previous=value
    if values:need(plan.version>=values[-1][1]['plan_version_after'])
    return values


def _model(db,p,prep,*,live):
    model=(published(db,p,prep['model_pin']['id'],'foundation_model') if live
        else store._fresh(db,p,Capability,prep['model_pin']['id']))
    need(store._pin(model)==prep['model_pin'] and model.kind=='foundation_model'
        and model.content_hash==model.validated_hash==digest(model.data))
    return model


def _originals(db,p,task,plan,source):
    """Historical output/bytes ownership, without a fresh head or consent grant."""
    row=store._fresh(db,p,Evaluation,source['mature_head']['evaluation_ref']['id'])
    need(row.task_id==task.id and row.record_hash==source['mature_head']['evaluation_ref']['record_hash']
        and row.data['original_query_ref']==source['original_query_ref']
        and row.data['evaluation_query_ref']==source['evaluation_query_ref']
        and row.data['cut_at']==source['evaluation_cut'] and row.generation==source['evaluation_generation'])
    job=store._fresh(db,p,Job,row.job_id);sdk=store._fresh(db,p,Invocation,row.invocation_id)
    cap=store._fresh(db,p,Capability,sdk.capability_id)
    output=packets.saved_result(db,p,task,job,sdk,cap,job.data['inputs'])
    need(output['evaluation_ref']==source['mature_head']['evaluation_ref']
        and output['packet']['evidence_id']==source['data_evidence_ref']['evidence_id'])
    doc=documents.cold_read(db,p,task,plan)
    need(doc is not None and digest(doc['document'])==digest(source['document']))
    return doc


def _consent_original(db,p,grant,pin,ref,before):
    rows=store._rows(db,p,Audit,Audit.id==grant['audit_id'],
        Audit.action.in_(['evidence.upload','evidence.cloud_authorize']))
    need(len(rows)==1);row=rows[0];body=row.data
    need(row.object_id==ref['evidence_id']==grant['evidence_id']
        and _grant_hash(row)==grant['audit_hash'] and body.get('sha256')==ref['sha256']
        and grant['model_profile_id']==pin['model_profile_id']
        and row.created_at is not None and row.created_at.tzinfo is not None and row.created_at<=before)
    upload=(row.action=='evidence.upload' and body.get('consent') is True
        and body.get('cloud_model_id')==pin['model_profile_id'] and body.get('media_type')==ref['media_type'])
    explicit=(row.action=='evidence.cloud_authorize' and body.get('model_id')==pin['model_profile_id']
        and type(body.get('version')) is int and 1<=body['version']<=pin['version'])
    need(upload or explicit)


def _history(db,p,task,plan,prep,event,*,latest):
    value=event.data;preview=value['preview_record'];source=value['source_record']
    need(p.role=='admin' or p.user_id in {value['actor_id'],prep['requested_by'],prep['business_actor_id']})
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    c.check_preview(preview,preparation=prep,source=source,manifest=advice.data)
    _originals(db,p,task,plan,source)
    model=_model(db,p,prep,live=False)
    need(digest(value['model_snapshot'])==digest(model.data)
        and value['frame_snapshot']['source_record']==source)
    refs=[source['data_evidence_ref']]+([source['document']['evidence_ref']] if source['document'] else [])
    for index,(ref,pin,grant) in enumerate(zip(refs,preview['evidence_pins'],preview['consent_pins'])):
        file,content=evidence.evidence_bytes(db,p,ref['evidence_id'])
        need(file.workspace_id==p.workspace_id and file.sha256==ref['sha256']
            and file.size_bytes==ref['size_bytes']==len(content) and file.media_type==ref['media_type']
            and file.version>=pin['version']
            and value['evidence_parts'][index*2+1]=={'type':'text','text':content.decode('utf-8-sig')})
        _consent_original(db,p,grant,pin,ref,event.created_at)
    expected_projection=projection.derive(value['frame_snapshot'],prep,
        evidence_parts=value['evidence_parts'],evidence_pins=preview['evidence_pins'])
    need(digest(expected_projection)==digest(value['projection'])
        and expected_projection['cloud_request']==preview['cloud_request'])
    prepared=transport.prepare(model,projection.messages(expected_projection),c.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    need(digest(prepared)==digest(value['prepared']) and prepared['dispatch_options_hash']==preview['model_options_hash'])
    priced_at=instant(value['money_quote']['quoted_at'])
    need(priced_at==event.created_at)
    price=prices.checked_original(db,p,prep['model_pin'],basis_ref=value['price_basis_ref'],priced_at=priced_at)
    need(price['basis_ref']==value['price_basis_ref'] and price['evidence_ref']==value['price_evidence_ref']
        and digest(price['price'])==digest(value['price_snapshot']))
    money.check_quote(value['money_quote'],price['price'],now=priced_at,prepared=prepared,
        model_pin=prep['model_pin'],model_snapshot=model.data,budget=prep['budget'])
    return _public(plan,prep,value,latest=latest)


def _public(plan,prep,value,*,latest):
    quote=value['money_quote'];model=value['model_snapshot']
    return {'schema':PUBLIC,'plan_id':plan.id,'request_id':value['request']['request_id'],
        'preview_generation':value['preview_generation'],'latest_for_plan':latest,'plan_version':plan.version,
        'plan_status':plan.status,'phase':('cancelled_preview' if plan.status=='cancelled' else 'preview_saved'
            if latest else 'historical_preview'),'preview_hash':value['preview_record']['preview_hash'],
        'messages':copy.deepcopy(value['prepared']['messages']),'options':copy.deepcopy(value['prepared']['options']),
        'model':{'id':prep['model_pin']['id'],'name':model['description'],'provider':quote['provider'],
            'model':quote['model'],'release_version':prep['model_pin']['release_version']},
        'currency':quote['currency'],'maximum_cost_minor_units':quote['maximum_cost_minor_units'],
        'configured_ceiling_minor_units':quote['configured_ceiling_minor_units'],
        'input_token_bound':quote['input_token_bound'],'output_token_bound':quote['output_token_bound'],
        'price_checked_at':value['price_snapshot']['checked_at'],'price_expires_at':value['price_snapshot']['expires_at'],
        'evidence_ids':[pin['evidence_id'] for pin in value['preview_record']['evidence_pins']],
        'can_start':False,'start_endpoint_available':True,'requires_current_checks':True,
        'cloud_send_authorized':False,'preview_generated_cloud_calls':0,
        'message':'匿名发送预览已保存；开始分析前会重新核对数据、授权、费用和任务状态，读取预览不会发送'}


@checked
def prepare(db,p,task_id,plan_id,body):
    body=PreviewRequest.model_validate(body).model_dump(mode='json');store._reader(db,p,write=True)
    store.command_nonce.acquire(db,p,action=ACTION,task_id=task_id,body=body,subject_id=plan_id)
    task,plan,prep,job=_plan(db,p,task_id,plan_id,lock=True);lock_current_actor(db,p)
    chain=_chain(db,p,task,plan,prep)
    originals=[entry for entry in chain if entry[1]['request']['request_id']==body['request_id']]
    if originals:
        event,value=originals[0]
        need(value['actor_id']==p.user_id and value['request_hash']==_request_hash(task.id,plan.id,body))
        return _history(db,p,task,plan,prep,event,latest=event.id==chain[-1][0].id)
    need(len(chain)<MAX_PREVIEWS and plan.status=='awaiting_consent' and job.status=='succeeded' and job.lease_until is None
        and plan.version==body['expected_plan_version']
        and (p.role=='admin' or p.user_id==prep['business_actor_id'])
        and body['expected_goal_hash']==prep['scope']['goal_hash']
        and body['expected_mature_head_hash']==prep['mature_head']['head_hash'])
    if chain:need(plan.version==chain[-1][1]['plan_version_after'])
    # Reject a nonce already attached to another Plan in this same Task.
    collisions=store._rows(db,p,Audit,Audit.action==ACTION,Audit.data['task_id'].as_string()==task.id,
        Audit.data['request']['request_id'].as_string()==body['request_id'])
    need(not collisions)
    frame=frames.build_current(db,p,task,plan)
    need(frame['source_record']['plan_id']==plan.id and frame['model_pin']==prep['model_pin'])
    model=_model(db,p,prep,live=True)
    files=sources.cloud_documents(db,p,frame['evidence_ids'],model)
    need(files['model_pin']==prep['model_pin'])
    projected=projection.derive(frame,prep,evidence_parts=files['parts'],evidence_pins=files['evidence_pins'])
    prepared=transport.prepare(model,projection.messages(projected),c.model_value_schema(),
        output_token_limit=prep['budget']['max_tokens'])
    price=prices.current(db,p,prep['model_pin'])
    now=db.scalar(select(func.clock_timestamp()));need(type(now) is datetime and now.tzinfo is not None)
    quote=money.quote(price['price'],now=now,prepared=prepared,model_pin=prep['model_pin'],
        model_snapshot=model.data,budget=prep['budget'])
    advice=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    preview=c.seal_preview({'schema':c.PREVIEW,'plan_id':plan.id,'preparation_snapshot_hash':prep['record_hash'],
        'source_hash':frame['source_record']['record_hash'],'advice_capability_ref':prep['advice_capability_ref'],
        'model_pin':prep['model_pin'],'budget':prep['budget'],'evidence_pins':files['evidence_pins'],
        'consent_pins':files['consent_pins'],'cloud_request':projected['cloud_request'],
        'cloud_request_hash':digest(projected['cloud_request']),'model_options_hash':prepared['dispatch_options_hash']},
        preparation=prep,source=frame['source_record'],manifest=advice.data)
    identity=uid();before=plan.version
    value={'schema':SCHEMA,'audit_id':identity,'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'actor_id':p.user_id,'request':body,'request_hash':_request_hash(task.id,plan.id,body),
        'preparation_snapshot_hash':prep['record_hash'],'preview_generation':len(chain)+1,
        'previous_preview_record_hash':chain[-1][1]['record_hash'] if chain else None,
        'plan_version_before':before,'plan_version_after':before+1,'prepared_at':now.isoformat(),
        'frame_snapshot':copy.deepcopy(frame),'source_record':copy.deepcopy(frame['source_record']),
        'preview_record':preview,'evidence_parts':copy.deepcopy(files['parts']),'projection':projected,
        'prepared':prepared,'model_snapshot':copy.deepcopy(model.data),'price_snapshot':price['price'],
        'price_basis_ref':price['basis_ref'],'price_evidence_ref':price['evidence_ref'],'money_quote':quote}
    value['record_hash']=c.record_hash(value)
    event=Audit(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=plan.id,
        created_at=now,data=value)
    with db.begin_nested():
        db.add(event);plan.version=before+1;db.flush()
        actual=_chain(db,p,task,plan,prep);need(len(actual)==len(chain)+1 and actual[-1][0].id==identity)
        return _public(plan,prep,value,latest=True)


@checked
def get(db,p,task_id,plan_id,request_id=None):
    store._reader(db,p)
    with db.no_autoflush:
        task,plan,prep,_job=_plan(db,p,task_id,plan_id)
        chain=_chain(db,p,task,plan,prep)
        found=([entry for entry in chain if entry[1]['request']['request_id']==store.existing._id(request_id)]
            if request_id is not None else chain[-1:])
        if not found:raise HTTPException(404,'原业务建议发送预览尚未保存或不属于此计划')
        need(len(found)==1)
        return _history(db,p,task,plan,prep,found[0][0],latest=found[0][0].id==chain[-1][0].id)
