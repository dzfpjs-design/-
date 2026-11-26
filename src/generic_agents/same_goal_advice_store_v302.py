"""Durable local preparation only; no Start, SDK, cloud or human review writes.

The protected quota-policy Audit is a prerequisite, not minted here. Reader
paths authenticate custody without gathering facts or repairing documents.
Root must install the future real preparation worker before exposing an API.
"""
import copy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_sources_v302 as sources
from . import goal_evaluation_append_store_v297 as existing
from . import goal_evaluation_provider_v288 as provider
from .contracts import digest
from .goals import snapshot
from .models import Audit, Capability, Invocation, Job, Membership, Plan, Task, User, uid
from .relations import lock_current_actor
from .services import published, require
from .temporal import instant
from . import workspace_command_nonce_lock_v312 as command_nonce

ACCEPTED = 'same_goal_advice.preparation_accepted_v302'
CANCELLED = 'same_goal_advice.preparation_cancelled_v302'
QUOTA_POLICY = 'same_goal_advice.quota_policy_authorized_v302'
POLICY_SCHEMA = 'same-goal-advice-quota-policy-v302'
JOB_SCHEMA = 'same-goal-advice-prepare-job-v302'
EXECUTION_KEY='advice_execution_v308'
RESULT_KEY='advice_result_v308'
OPERATION = 'same-goal-advice-operation-v302'
DOCUMENT_PREPARATION = 'same-goal-advice-document-preparation-v304'
MAX_ROWS = 1000
need, checked = existing.need, existing.checked


def _rows(db,p,model,*where):
    rows=list(db.scalars(select(model).where(model.workspace_id==p.workspace_id,*where)
        .limit(MAX_ROWS+1).execution_options(populate_existing=True)))
    need(len(rows)<=MAX_ROWS);return rows


def _fresh(db,p,model,identity):
    rows=_rows(db,p,model,model.id==identity);need(len(rows)==1);return rows[0]


def _reader(db,p,*,write=False):
    existing._reader(db,p,write=write)


def _member(db,p,identity,*,admin=False):
    roles=['admin'] if admin else ['admin','operator']
    value=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
        User.id==identity,User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role.in_(roles))
        .with_for_update(read=True,of=[User,Membership]))
    need(value==identity,'原发布岗位或配额批准人的当前账号权限无法核对')


def _pin(cap):return {'id':cap.id,'content_hash':cap.content_hash,'release_version':cap.release_version}
def _key(task_id,plan_id):return 'same-goal-advice-prepare:'+task_id+':'+plan_id


def _prepare_actor_allowed(p,business_actor,data_actor):
    return p.role=='admin' or p.role=='operator' and p.user_id in {business_actor,data_actor}


def _preparation_open(plan):
    # Call only after _inventory authenticates the protected terminal records.
    return plan.status not in {'cancelled','reviewed'}


def _job_data(plan_id,prep,key):
    return {'schema':JOB_SCHEMA,'plan_id':plan_id,'task_id':prep['scope']['task_id'],
        'actor_id':prep['data_actor_id'],'preparation_snapshot_hash':prep['record_hash'],
        'invocation_key':key}


def _accepted(plan_id,prep,job_id,key):
    return {'schema':c.PREPARATION,'plan_id':plan_id,'task_id':prep['scope']['task_id'],
        'actor_id':prep['requested_by'],'request_id':prep['request']['request_id'],
        'body_hash':prep['request_hash'],'preparation_snapshot_hash':prep['record_hash'],
        'job_id':job_id,'job_key':key}


def _audit(db,p,action,identity,actor,body):
    rows=_rows(db,p,Audit,Audit.action==action,Audit.object_id==identity)
    need(len(rows)==1 and rows[0].actor_id==actor and digest(rows[0].data)==digest(body))
    return rows[0]


def _saved_base(db,p,task,plan):
    data=plan.data
    fields={'schema','preparation_snapshot','jobs','operations','document_preparation','reviews'}
    if type(data) is dict and EXECUTION_KEY in data:
        fields.add(EXECUTION_KEY)
        if RESULT_KEY in data:fields.add(RESULT_KEY)
    need(plan.workspace_id==p.workspace_id==task.workspace_id and plan.task_id==task.id
        and type(data) is dict and set(data)==fields
        and data['schema']==c.PLAN and type(data['reviews']) is list)
    if RESULT_KEY not in data:need(data['reviews']==[])
    raw=data['preparation_snapshot'];cap=_fresh(db,p,Capability,raw['advice_capability_ref']['id'])
    need(cap.content_hash==cap.validated_hash==digest(cap.data)==raw['advice_capability_ref']['content_hash']
        and cap.release_version==raw['advice_capability_ref']['release_version'])
    prep=c.check_preparation(raw,manifest=cap.data)
    need(prep['scope']['workspace_id']==p.workspace_id and prep['scope']['task_id']==task.id
        and prep['scope']['analysis_plan_id']==plan.id)
    jobs=data['jobs'];need(type(jobs) is dict and set(jobs)=={'prepare'})
    pin=jobs['prepare'];need(set(pin)=={'id','kind','schema','key'} and pin['kind']==c.PREPARE_KIND and pin['schema']==JOB_SCHEMA
        and pin['key']==_key(task.id,plan.id))
    originals=_rows(db,p,Job,or_(Job.id==pin['id'],Job.idempotency_key==pin['key'],Job.data['plan_id'].as_string()==plan.id))
    original_prepares=[row for row in originals if row.id==pin['id'] or row.idempotency_key==pin['key']]
    need(len(original_prepares)==1);job=original_prepares[0];expected=_job_data(plan.id,prep,pin['key'])
    if EXECUTION_KEY not in data:need(len(originals)==1)
    need(job.id==pin['id'] and job.kind==c.PREPARE_KIND and job.idempotency_key==pin['key']
        and all(key in job.data and digest(job.data[key])==digest(value) for key,value in expected.items())
        and not {'activation_id','graph_hash','cancel_epoch','approval','inputs'} & set(job.data))
    marker=data['document_preparation']
    if marker is not None:
        need(type(marker) is dict and set(marker)=={'schema','receipt_ref','evidence_ref','prepare_job_id','preparation_snapshot_hash'}
            and marker['schema']==DOCUMENT_PREPARATION and marker['prepare_job_id']==job.id
            and marker['preparation_snapshot_hash']==prep['record_hash'])
        c.Ref.model_validate(marker['receipt_ref']);c.EvidenceRef.model_validate(marker['evidence_ref'])
    operations=data['operations'];need(type(operations) is list and 1<=len(operations)<=2)
    first=operations[0]
    need(set(first)=={'operation','request_id','body','body_hash','actor_id','audit_id'}
        and first['operation']=='prepare' and first['request_id']==prep['request']['request_id']
        and digest(first['body'])==digest(prep['request']) and first['body_hash']==prep['request_hash']
        and first['actor_id']==prep['requested_by'])
    event=_audit(db,p,ACCEPTED,plan.id,prep['requested_by'],_accepted(plan.id,prep,job.id,pin['key']))
    need(event.id==first['audit_id'])
    cancels=_rows(db,p,Audit,Audit.action==CANCELLED,Audit.object_id==plan.id)
    if len(operations)==1:
        allowed={'queued','running','failed'} if marker is None else {'awaiting_consent','failed'}
        if EXECUTION_KEY in data:
            allowed={'generating','ready_for_review','reviewed','needs_review','failed'}
        need(not cancels and plan.status in allowed)
    else:
        operation=operations[1];body=c.Cancel.model_validate(operation['body']).model_dump(mode='json')
        need(set(operation)==set(first) and operation['operation']=='cancel'
            and operation['request_id']==body['request_id'] and operation['body_hash']==digest(body)
            and operation['request_id']!=first['request_id'] and plan.status=='cancelled')
        expected={'schema':c.PLAN,'plan_id':plan.id,'task_id':task.id,'preparation_snapshot_hash':prep['record_hash'],
            'request_id':body['request_id'],'body_hash':digest(body),'job_id':job.id,'actor_id':operation['actor_id']}
        need(len(cancels)==1 and cancels[0].id==operation['audit_id'] and cancels[0].actor_id==operation['actor_id']
            and digest(cancels[0].data)==digest(expected))
    return prep,job


def _saved(db,p,task,plan):
    prep,job=_saved_base(db,p,task,plan)
    data=plan.data;marker=data['document_preparation']
    if EXECUTION_KEY in data:
        from .same_goal_advice_execution_v308 import checked as checked_execution
        state=checked_execution(db,p,task,plan,prep,job);need(state is not None and marker is not None)
        if RESULT_KEY in data:
            result=data[RESULT_KEY]
            need(type(result) is dict and set(result)=={'schema','result_ref','send_job_id','ready_audit_ref'}
                and result['schema']=='same-goal-advice-result-ready-v308'
                and result['send_job_id']==state['job'].id and len(state['calls'])==1)
            c.Ref.model_validate(result['result_ref']);c.Ref.model_validate(result['ready_audit_ref'])
            sdk=state['calls'][0]
            need(result['result_ref']=={'id':sdk.id,'record_hash':digest(sdk.data.get('output'))})
            ready=_rows(db,p,Audit,Audit.id==result['ready_audit_ref']['id'],
                Audit.action=='same_goal_advice.result_ready_v308',Audit.object_id==plan.id)
            need(len(ready)==1 and digest(ready[0].data)==result['ready_audit_ref']['record_hash'])
        if plan.status in {'ready_for_review','reviewed'}:need(RESULT_KEY in data)
    from .same_goal_advice_review_v308 import checked as checked_reviews
    checked_reviews(db,p,task,plan,prep)
    return prep,job


def _discover_reserved_history_test_only(db,p,task):
    """Identify one candidate, then let common inventory verify every original.

    This detector is not an authority. It can only run in the isolated test
    database and cannot authorize creation, sending, or a retry. Unknown,
    duplicate, inconsistent and post-permit states remain closed.
    """
    from .same_goal_advice_cloud_claim_test_v2 import ACTION as reservation_action, require_test_database
    from .same_goal_advice_transport_intent_candidate_v2 import ACTION as intent_action
    from .same_goal_advice_unlimited_store_v2 import PLAN as versioned_plan
    require_test_database(db)
    plans=_rows(db,p,Plan,Plan.data['schema'].as_string()==versioned_plan,or_(
        Plan.task_id==task.id,Plan.data['preparation_snapshot']['scope']['task_id'].as_string()==task.id))
    ids=[plan.id for plan in plans]
    if not ids:return None,False
    events=_rows(db,p,Audit,Audit.action.in_([reservation_action,intent_action]),Audit.object_id.in_(ids))
    reserved=[event for event in events if event.action==reservation_action]
    intents=[event for event in events if event.action==intent_action]
    need(len(reserved)<=1 and len(intents)<=1)
    if not reserved:
        need(not intents)
        return None,False
    plan_id=reserved[0].object_id
    need(not intents or intents[0].object_id==plan_id)
    return plan_id,bool(intents)


def _inventory(db,p,task,*,history_compatible=False,reserved_plan_test_only=None,intent_test_only=False):
    # v2 remains in a separate experimental release. History may ignore it
    # only after full custody verification, with a Task lock preventing a
    # cooperative writer from adding it between verification and filtering.
    need(type(intent_test_only) is bool and (not intent_test_only or reserved_plan_test_only is not None))
    if reserved_plan_test_only is not None:
        # This is a proof experiment, not an input to a production API.
        # It does not authorize a mutation, provider call, or retry.
        from .same_goal_advice_cloud_claim_test_v2 import require_test_database
        require_test_database(db)
        need(history_compatible and type(reserved_plan_test_only) is str)
    has_v2=False
    if history_compatible:
        from .same_goal_advice_unlimited_store_v2 import PLAN as V2_PLAN
        locked=db.scalar(select(Task.id).where(Task.id==task.id,Task.workspace_id==p.workspace_id).with_for_update(read=True))
        need(locked is not None)
        has_v2=bool(db.scalar(select(Plan.id).where(Plan.workspace_id==p.workspace_id,
            Plan.data['schema'].as_string()==V2_PLAN,or_(Plan.task_id==task.id,
                Plan.data['preparation_snapshot']['scope']['task_id'].as_string()==task.id)).limit(1)))
        if has_v2:
            from .same_goal_advice_common_inventory_v2 import read as read_versions
            from .config import settings
            if (reserved_plan_test_only is None and intent_test_only is False
                    and settings().v2_history_compat_test_enabled):
                reserved_plan_test_only,intent_test_only=_discover_reserved_history_test_only(db,p,task)
            if reserved_plan_test_only is None and intent_test_only is False:
                read_versions(db,p,task)
            else:
                read_versions(db,p,task,reserved_plan_test_only=reserved_plan_test_only,
                    intent_test_only=intent_test_only)
    if reserved_plan_test_only is not None:
        need(has_v2, '指定的原始预留不存在，未按空历史读取')
    plans=_rows(db,p,Plan,or_(
        (Plan.task_id==task.id)&(Plan.data['schema'].as_string()==c.PLAN),
        Plan.data['preparation_snapshot']['scope']['task_id'].as_string()==task.id))
    if has_v2:
        plans=[plan for plan in plans if plan.data.get('schema')==c.PLAN]
    ids=[plan.id for plan in plans];prefix='same-goal-advice-prepare:'+task.id+':'
    jobs=_rows(db,p,Job,or_(
        Job.kind.in_([c.PREPARE_KIND,c.SEND_KIND])&(Job.data['task_id'].as_string()==task.id),
        Job.idempotency_key.startswith(prefix,autoescape=True),Job.data['plan_id'].as_string().in_(ids)))
    audits=_rows(db,p,Audit,Audit.action.in_([ACCEPTED,CANCELLED]),or_(
        Audit.data['task_id'].as_string()==task.id,Audit.object_id.in_(ids),
        Audit.data['job_key'].as_string().startswith(prefix,autoescape=True)))
    values=[(plan,*_saved(db,p,task,plan)) for plan in plans]
    from .same_goal_advice_execution_v308 import checked as checked_execution
    execution_states=[checked_execution(db,p,task,plan,prep,job) for plan,prep,job in values]
    sends=[state['job'] for state in execution_states if state is not None]
    need(len({plan.id for plan in plans})==len(plans)
        and {job.id for job in jobs}=={job.id for _,_,job in values}|{job.id for job in sends}
        and {event.id for event in audits}=={op['audit_id'] for plan,_,_ in values for op in plan.data['operations']})
    calls=_rows(db,p,Invocation,or_(Invocation.data['job_id'].as_string().in_([job.id for job in jobs]),
        Invocation.data['approval']['plan_ref']['id'].as_string().in_(ids),
        Invocation.idempotency_key.startswith(prefix,autoescape=True)))
    need({call.id for call in calls}=={call.id for state in execution_states if state is not None for call in state['calls']},
        '原准备操作出现无正式Start的调用或矛盾库存；保留原号核查，未另建操作')
    return values


def _policy_starts(db,p,task,budget):
    """Actual allocations of this one-call policy, never inferred refunds.

    A normally cancelled Plan with no SDK cannot later submit a model call.
    Every other original Start retains its policy allocation, including a
    completed paid answer. New analysis needs a separately valid policy.
    """
    from .same_goal_advice_execution_v308 import checked as checked_execution
    occupied=[];reference=budget['quota_policy_ref']
    for plan,prep,job in _inventory(db,p,task):
        original=prep['budget']['quota_policy_ref']
        if original['id']!=reference['id']:continue
        need(original==reference)
        state=checked_execution(db,p,task,plan,prep,job)
        if state is not None and (plan.status!='cancelled' or state['calls']):
            occupied.append((plan,state))
    return occupied


def _policy(db,p,task,scope,source_cap,advice_cap,model):
    """Read only a real protected, admin-authored policy; never synthesize one."""
    now=db.scalar(select(func.clock_timestamp()));need(type(now) is datetime and now.tzinfo is not None)
    rows=_rows(db,p,Audit,Audit.action==QUOTA_POLICY,Audit.object_id==task.id)
    need(rows,'尚未配置可核对的服务器业务建议配额政策；未创建准备操作')
    candidates=[]
    expected_scope={key:value for key,value in scope.items() if key!='analysis_plan_id'}
    fields={'schema','scope','source_business_capability_ref','advice_capability_ref','model_pin',
        'issued_at','expires_at','limits','record_hash'}
    for event in rows:
        value=event.data
        need(type(value) is dict and set(value)==fields and value['schema']==POLICY_SCHEMA
            and value['record_hash']==c.record_hash(value) and type(value['limits']) is dict)
        if value['scope']!=expected_scope:continue
        issued,expires=instant(value['issued_at']),instant(value['expires_at'])
        need(event.created_at is not None and event.created_at.tzinfo is not None and issued<=event.created_at<=now and issued<expires)
        if now>=expires:continue
        need(value['source_business_capability_ref']==_pin(source_cap)
            and value['advice_capability_ref']==_pin(advice_cap) and value['model_pin']==_pin(model))
        _member(db,p,event.actor_id,admin=True)
        limit=c.Budget.model_validate({**value['limits'],'quota_policy_ref':{'id':event.id,'record_hash':digest(value)}}).model_dump(mode='json')
        need(set(value['limits'])==set(c.Budget.model_fields)-{'quota_policy_ref'}
            and limit['provider']==model.data['execution']['provider']
            and limit['max_tokens']<=model.data['execution'].get('max_tokens',1024)
            and limit['goal_max_tool_calls']==task.data['goal_spec']['max_tool_calls']
            and limit['goal_max_steps']==task.data['goal_spec']['max_steps'])
        candidates.append(limit)
    need(len(candidates)==1,'服务器业务建议配额政策缺失、已过期或不唯一；未创建准备操作')
    return candidates[0]


def _scope(db,p,task,body,plan_id):
    from .workspace_api import saved_execution
    from .native_goals import guard,saved_context
    guard(db,p,task,lock_sources=False);saved_context(db,p,task)
    actual,release,business=saved_execution(db,p,task.id)
    selected=provider.binding(db,p,task);need(selected is not None)
    need(actual.id==task.id and release.id==selected['release'].id and business['execution_mode']=='confirmed_graph_workflow')
    nodes={node['id']:node for node in selected['binding']['graph']['nodes']}
    business_node,data_node=nodes['reasoning'],nodes['packet']
    need(business_node['kind']==data_node['kind']=='ability' and business_node['execution']['agent_role']=='business'
        and data_node['execution']['agent_role']=='data')
    business_actor,data_actor=business_node['actor_id'],data_node['actor_id']
    _member(db,p,business_actor);_member(db,p,data_actor)
    need(_prepare_actor_allowed(p,business_actor,data_actor),
        '请由本任务的业务岗位、资料岗位或管理员发起资料整理')
    source_cap=published(db,p,business_node['execution']['capability_id'],'tool')
    need(source_cap.content_hash==source_cap.validated_hash==digest(source_cap.data)==business_node['execution']['capability_hash']
        and source_cap.release_version==business_node['execution']['release_version'])
    expected=c.build_manifest(source_cap.data,_pin(source_cap))
    caps=_rows(db,p,Capability,Capability.kind=='tool',Capability.status=='published',
        Capability.data['execution']['adapter'].as_string()==c.ADAPTER,
        Capability.data['specification']['source_business_capability_ref']['id'].as_string()==source_cap.id)
    need(len(caps)==1,'未登记唯一且版本固定的同目标建议能力；未创建准备操作')
    advice=caps[0]
    need(advice.content_hash==advice.validated_hash==digest(advice.data)==digest(expected)
        and advice.release_version==expected['version'])
    model=published(db,p,source_cap.data['execution']['model_profile_id'],'foundation_model')
    need(model.content_hash==model.validated_hash==digest(model.data)==source_cap.data['execution']['model_profile_hash']
        and 'business' in model.data['allowed_agent_roles']
        and model.data['execution']['provider'] in {'qwen','deepseek'}
        and model.data['execution'].get('task')!='embedding' and model.data['execution'].get('report_evidence_output') is None
        and model.data['data_policy'].get('cloud_allowed') is True)
    mature=existing._mature_head(db,p,task,selected)  # Historical completion, no freshness promotion.
    goal=snapshot(task)
    need(body['expected_goal_hash']==goal['hash'] and body['expected_mature_head_hash']==mature['head']['head_hash'])
    scope={'workspace_id':p.workspace_id,'task_id':task.id,'analysis_plan_id':plan_id,
        'instance_id':task.data['instance_id'],'release_id':release.id,'package_id':task.package_id,'business_id':business['id'],
        'goal_version':goal['version'],'goal_hash':goal['hash'],'goal_snapshot_hash':digest(goal),
        'native_goal_context_hash':task.data['native_goal_context_hash'],'source_binding_hash':selected['binding']['binding_hash']}
    budget=_policy(db,p,task,scope,source_cap,advice,model)
    need(not _policy_starts(db,p,task,budget),
        '本次分析额度已分配给原申请，请核对原记录；新的分析需要有效的新额度')
    # Resolve only the original approved local excerpts. None is not an empty packet.
    need(body['document_source'] is not None)
    sources.resolve_documents(db,p,task,body['document_source'])
    value=mature['record']
    return {'scope':scope,'source_business_capability_ref':_pin(source_cap),'advice_capability_ref':_pin(advice),
        'model_pin':_pin(model),'data_actor_id':data_actor,'business_actor_id':business_actor,
        'original_query_ref':value['original_query_ref'],'mature_head':{'head_hash':mature['head']['head_hash'],
            'evaluation_ref':mature['head']['evaluation_ref'],'goal_check_job_id':mature['goal_check_job'].id,
            'memory_id':mature['memory'].id,'report_hash':mature['report_hash']},
        'evaluation_query_ref':value['evaluation_query_ref'],'evaluation_cut':value['cut_at'],
        'instruction':source_cap.data['specification']['instruction'],'budget':budget,'manifest':advice.data}


def _view(db,p,task,plan,prep,job):
    from .same_goal_reasoning_bridge_v339 import inspect as inspect_bridge
    bridge=inspect_bridge(db,p,task,plan,prep,job)
    phase='cancelled' if plan.status=='cancelled' else 'needs_review' if plan.status=='failed' or job.status in {'failed','cancelled'} else 'preparing' if job.status=='running' else 'queued'
    marker=plan.data['document_preparation'];document=None
    if marker is not None and job.status=='succeeded':
        from .same_goal_advice_preparation_v304 import cold_read
        receipt=cold_read(db,p,task,plan)
        need(receipt['marker']==marker)
        document=copy.deepcopy(marker)
        if phase not in {'cancelled','needs_review'}:phase='awaiting_consent'
    send=None;result=None;reviews=[];source_review_scope=None;send_state=None
    if EXECUTION_KEY in plan.data:
        from .same_goal_advice_execution_v308 import checked as execution_state
        state=execution_state(db,p,task,plan,prep,job);need(state is not None)
        send_state=state
        actual=state['job'];send={'id':actual.id,'status':actual.status}
        if plan.status=='cancelled':phase='cancelled'
        elif plan.status in {'failed','needs_review'} or actual.status in {'failed','cancelled'}:phase='needs_review'
        elif actual.status=='succeeded' and actual.lease_until is None and RESULT_KEY in plan.data:
            from .same_goal_advice_runtime_v308 import cold_result
            result=cold_result(db,p,task,plan)
            ready=_fresh(db,p,Audit,plan.data[RESULT_KEY]['ready_audit_ref']['id'])
            source_review_scope=copy.deepcopy(ready.data.get('source_review_scope'))
            from .same_goal_advice_review_v308 import checked as checked_reviews
            reviews=checked_reviews(db,p,task,plan,prep)
            phase='reviewed' if reviews else 'ready_for_review'
        elif RESULT_KEY in plan.data:phase='closing'
        elif actual.status=='running':phase='generating'
        else:phase='send_queued'
    cancel_available=(p.role in {'admin','operator'}
        and (p.role=='admin' or p.user_id in {prep['requested_by'],prep['data_actor_id'],prep['business_actor_id']})
        and plan.status in {'queued','running','failed','awaiting_consent','generating'}
        and len(plan.data['operations'])==1 and (send_state is None or not send_state['calls']))
    return {'schema':OPERATION,'plan_id':plan.id,'task_id':plan.task_id,'version':plan.version,'status':plan.status,
        'continues_stopped_reasoning':bridge is not None,
        'stopped_reasoning_unlinked':bridge is None and bool(task.data.get('graph_reasoning_retirements_v337')),
        'phase':phase,'request_id':prep['request']['request_id'],'preparation_snapshot_hash':prep['record_hash'],
        'data_as_of':prep['evaluation_cut'],
        'prepare_job_id':job.id,'document_preparation':document,'reviews':reviews,'cloud_send_available':False,
        'start_endpoint_available':True,'requires_current_checks':True,'cancel_available':cancel_available,
        'send_job':send,'result':result,'source_review_scope':source_review_scope,
        'human_review_available':phase=='ready_for_review',
        'message':{'cancelled':'准备已取消，原资料和操作记录保留','needs_review':'原准备操作需要核查，请保留原号处理',
            'preparing':'正在核对原批准资料或完成后台收尾','queued':'资料准备请求已保存，等待后台处理',
            'awaiting_consent':'本地资料已保存；请核对实际文字后逐项授权给指定模型',
            'send_queued':'分析申请已保存，等待后台处理','generating':'正在分析，请保留原申请查看进度',
            'closing':'回答已保存，正在完成后台核对','ready_for_review':'回答与费用记录已保存，可以审核建议',
            'reviewed':'审核结论已保存；执行操作仍按业务审批流程办理'}[phase]}


@checked
def prepare(db,p,task_id,body,*,reserved_plan_test_only=None,intent_test_only=False):
    body=c.Prepare.model_validate(body).model_dump(mode='json');_reader(db,p,write=True)
    command_nonce.acquire(db,p,action=ACCEPTED,task_id=task_id,body=body)
    task=existing._task(db,p,task_id,lock=True);lock_current_actor(db,p);inventory=_inventory(db,p,task,history_compatible=True,
        reserved_plan_test_only=reserved_plan_test_only,intent_test_only=intent_test_only)
    previous=[value for value in inventory if any(op['request_id']==body['request_id'] for op in value[0].data['operations'])]
    need(len(previous)<=1)
    if previous:
        plan,prep,job=previous[0]
        need(prep['request']['request_id']==body['request_id'] and prep['request_hash']==digest(body) and prep['requested_by']==p.user_id)
        return _view(db,p,task,plan,prep,job)
    # Only a proved original may replay across versions.  Preserve the strict
    # v1 inventory for every new mutation until all writers migrate together.
    _inventory(db,p,task)
    need(not any(_preparation_open(plan) for plan,_,_ in inventory),
        '原分析申请尚未结清，请先核对原进度或完成审核')
    plan_id,job_id,audit_id=uid(),uid(),uid();context=_scope(db,p,task,body,plan_id)
    manifest=context.pop('manifest')
    prep=c.seal_preparation({'schema':c.PREPARATION,**context,'request':body,'request_hash':digest(body),
        'requested_by':p.user_id},manifest=manifest)
    key=_key(task.id,plan_id);clock=db.scalar(select(func.clock_timestamp()))
    need(type(clock) is datetime and clock.tzinfo is not None)
    plan=Plan(id=plan_id,workspace_id=p.workspace_id,task_id=task.id,status='queued',version=1,created_at=clock,
        data={'schema':c.PLAN,'preparation_snapshot':prep,'jobs':{'prepare':{'id':job_id,'kind':c.PREPARE_KIND,'schema':JOB_SCHEMA,'key':key}},
            'operations':[{'operation':'prepare','request_id':body['request_id'],'body':body,'body_hash':digest(body),
                'actor_id':p.user_id,'audit_id':audit_id}],'document_preparation':None,'reviews':[]})
    job=Job(id=job_id,workspace_id=p.workspace_id,kind=c.PREPARE_KIND,status='queued',version=1,
        attempts=0,idempotency_key=key,data=_job_data(plan.id,prep,key))
    event=Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,action=ACCEPTED,object_id=plan.id,
        created_at=clock,data=_accepted(plan.id,prep,job.id,key))
    with db.begin_nested():
        db.add(plan);db.flush();db.add(job);db.add(event);db.flush()
        from .same_goal_reasoning_bridge_v339 import create as link_reasoning
        link_reasoning(db,p,task,plan,prep,job)
        saved,actual_job=_saved(db,p,task,plan)
        return _view(db,p,task,plan,saved,actual_job)


@checked
def listing(db,p,task_id,*,reserved_plan_test_only=None,intent_test_only=False):
    _reader(db,p);task=existing._task(db,p,task_id)
    return {'schema':OPERATION+'/list','task_id':task.id,
        'records':[_view(db,p,task,plan,prep,job) for plan,prep,job in _inventory(db,p,task,history_compatible=True,
            reserved_plan_test_only=reserved_plan_test_only,intent_test_only=intent_test_only)]}


@checked
def get(db,p,task_id,plan_id,*,reserved_plan_test_only=None,intent_test_only=False):
    _reader(db,p);task=existing._task(db,p,task_id)
    found=[value for value in _inventory(db,p,task,history_compatible=True,
        reserved_plan_test_only=reserved_plan_test_only,intent_test_only=intent_test_only) if value[0].id==existing._id(plan_id)]
    if not found:raise HTTPException(404,'原资料准备操作不存在或不属于当前任务')
    need(len(found)==1);return _view(db,p,task,*found[0])


@checked
def get_operation(db,p,task_id,request_id,*,reserved_plan_test_only=None,intent_test_only=False):
    _reader(db,p);task=existing._task(db,p,task_id);request_id=existing._id(request_id)
    found=[value for value in _inventory(db,p,task,history_compatible=True,
        reserved_plan_test_only=reserved_plan_test_only,intent_test_only=intent_test_only) if any(op['request_id']==request_id for op in value[0].data['operations'])]
    if not found:raise HTTPException(404,'原资料准备请求不存在或不属于当前任务')
    need(len(found)==1);return _view(db,p,task,*found[0])


@checked
def cancel(db,p,task_id,plan_id,body,*,reserved_plan_test_only=None,intent_test_only=False):
    body=c.Cancel.model_validate(body).model_dump(mode='json');_reader(db,p,write=True)
    command_nonce.acquire(db,p,action=CANCELLED,task_id=task_id,body=body,subject_id=plan_id)
    task=existing._task(db,p,task_id,lock=True);lock_current_actor(db,p);inventory=_inventory(db,p,task,history_compatible=True,
        reserved_plan_test_only=reserved_plan_test_only,intent_test_only=intent_test_only)
    found=[value for value in inventory if value[0].id==existing._id(plan_id)]
    need(len(found)==1);plan,prep,job=found[0]
    need(p.role=='admin' or p.user_id in {prep['requested_by'],prep['data_actor_id'],prep['business_actor_id']})
    operations=plan.data['operations'];prior=[op for row,_,_ in inventory for op in row.data['operations'] if op['request_id']==body['request_id']]
    if prior:
        need(len(prior)==1 and prior[0] in operations and prior[0]['operation']=='cancel'
            and prior[0]['actor_id']==p.user_id and prior[0]['body_hash']==digest(body))
        return _view(db,p,task,plan,prep,job)
    # A replay has no side effects; a new cancellation retains the old
    # fail-closed gate when v2 records exist.
    _inventory(db,p,task)
    if EXECUTION_KEY in plan.data:
        from .same_goal_advice_execution_v308 import checked as execution_state
        state=execution_state(db,p,task,plan,prep,job);need(state is not None)
        actual=db.scalar(select(Job).where(Job.id==state['job'].id,Job.workspace_id==p.workspace_id)
            .with_for_update().execution_options(populate_existing=True))
        need(actual is not None and not state['calls'],
            '分析已提交，不能撤回已发生的模型调用；请等待原回答，未重新发送或退还未知费用')
    need(plan.version==body['expected_plan_version'] and plan.status in {'queued','running','failed','awaiting_consent','generating'}
        and len(operations)==1,'原准备操作状态已变化或已取消；未删除资料、发送或变更费用')
    audit_id=uid();expected={'schema':c.PLAN,'plan_id':plan.id,'task_id':task.id,
        'preparation_snapshot_hash':prep['record_hash'],'request_id':body['request_id'],
        'body_hash':digest(body),'job_id':job.id,'actor_id':p.user_id}
    with db.begin_nested():
        plan.status='cancelled';plan.version+=1
        plan.data={**plan.data,'operations':[*operations,{'operation':'cancel','request_id':body['request_id'],
            'body':body,'body_hash':digest(body),'actor_id':p.user_id,'audit_id':audit_id}]}
        db.add(Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,action=CANCELLED,object_id=plan.id,data=expected))
        db.flush();saved,actual_job=_saved(db,p,task,plan)
        return _view(db,p,task,plan,saved,actual_job)
