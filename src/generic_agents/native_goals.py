"""Formal goals belong to the original configured workflow, not parallel tasks."""
import copy
from types import SimpleNamespace
from typing import Literal
from pydantic import Field,StrictInt,ValidationError
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Contract,GoalSpec,DataRequest,digest
from .models import Task,DataRun,DataGap,Job,Event,Memory,Entity,Capability,Acquisition,Invocation,DomainInstance,now
from .services import scoped,require,check_version,audit
from .goals import initialize,snapshot,query_goal,evaluate
from .instance_api import active
from .report_text_parse_scope import report_text_parse_scope
KIND='check_native_business_goal'

class GoalCommand(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    expected_version:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)
class GoalDefinition(GoalCommand):
    goal_spec:GoalSpec
class GoalRecovery(Contract):
    operation:Literal['define','check','accept','stop']
    original_request:dict
    expected_version:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)
    action:Literal['probe','abandon']='probe'

class GoalAcceptance(GoalCommand):
    basis_hash:str=Field(pattern=r'^[0-9a-f]{64}$')


def manage(p,task):
    require(p,'admin','operator')
    if p.role!='admin' and task.owner_id!=p.user_id:raise HTTPException(403,'请由原任务发起人或管理员管理目标')

def object_binding_pin(task,context=None,release=None):
    """Check saved proof linkage without turning a history read into execution."""
    from .entity_release_compat import verify_task_binding_snapshot
    bindings=verify_task_binding_snapshot(task,release)
    if bindings is None:
        if context is not None and 'object_definition_bindings_hash' in context:
            raise HTTPException(409,'原任务对象定义依据无法核对')
        return {}
    signature=task.data['object_definition_bindings_hash']
    pin={'object_definition_bindings_hash':signature}
    if context is not None and context.get('object_definition_bindings_hash')!=signature:
        raise HTTPException(409,'原目标与对象定义依据不一致')
    return pin

def saved_context(db,p,task):
    from .workspace_api import saved_execution
    _,release,_=saved_execution(db,p,task.id)
    instance=scoped(db,DomainInstance,release.instance_id,p)
    c=task.data.get('native_goal_context')
    if not c or digest(c)!=task.data.get('native_goal_context_hash') or c.get('goal_snapshot')!=snapshot(task):raise HTTPException(409,'正式目标与原任务关联无法核对')
    if c.get('instance_id')!=instance.id or c.get('release_id')!=release.id or c.get('business_id')!=task.data['business_id'] or c.get('entity_ids')!=task.data['entity_ids']:
        raise HTTPException(409,'原目标实例、业务、版本或对象关联改变')
    bindings=object_binding_pin(task,c,release)
    for identifier in c['entity_ids']:
        obj=scoped(db,Entity,identifier,p)
        if obj.namespace!=instance.namespace or not bindings and obj.schema_capability_id!=release.package_id:raise HTTPException(409,'原业务对象范围已改变')
    return c

def total_steps(db,p,task):
    from .native_step_execution import step_count
    from .native_goal_queries import step_count as query_step_count
    from .same_goal_advice_execution_v308 import budget_calls
    from .same_goal_continuation_execution_budget_v314 import step_count as continuation_steps
    return len(task.data['workflow_runtime']['receipts'])+step_count(task)+query_step_count(db,p,task)+budget_calls(db,p,task)+continuation_steps(db,p,task)


def guard(db,p,task,invocation_key=None,step=False,allow_expired=False,*,lock_sources=True):
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,lock_sources=lock_sources)
    c=task.data.get('native_goal_context')
    if not c or digest(c)!=task.data.get('native_goal_context_hash'):raise HTTPException(409,'正式目标与原任务关联无法核对')
    if 'graph_runtime' in task.data:
        from .workflow_graph_goal_guard_v133 import verify_reservations
        verify_reservations(db,p,task)
        from .signal_graph_routing_v134 import guard_current as graph_route_guard
        graph_route_guard(db,p,task,lock=lock_sources)
    if task.status=='workflow_cancelled' or task.data.get('native_goal_cancelled') or task.data.get('native_goal_acceptance'):raise HTTPException(409,'正式目标已取消或验收，不能继续旧协调')
    instance,release=active(db,p,c['instance_id'])
    if task.data['instance_release_id']!=release.id or c['release_id']!=release.id or c['business_id'] not in instance.data.get('enabled_business_ids',[]) or task.package_id!=release.package_id:
        raise HTTPException(409,'原实例、业务或配置版本已改变，未继续旧目标')
    object_binding_pin(task,c)
    from .entity_release_compat import verify_task_objects
    if lock_sources:verify_task_objects(db,p,task,release)
    else:verify_task_objects(db,p,task,release,lock_sources=False)
    pin=snapshot(task)
    if c['goal_snapshot']!=pin or c['entity_ids']!=task.data['entity_ids']:raise HTTPException(409,'原目标或对象范围已改变')
    from .temporal import instant
    from .native_goal_time import timeout
    expired=timeout(task)
    if not allow_expired and (expired or pin['spec']['deadline'] and now()>=instant(pin['spec']['deadline'])):raise HTTPException(409,'目标截止时间已到，未继续执行')
    if step and total_steps(db,p,task)>=pin['spec']['max_steps']:raise HTTPException(409,'正式目标步骤预算已用尽，保留已有进度')
    if invocation_key:
        from .models import Invocation
        from .goal_queries import call_count
        if not db.scalar(select(Invocation.id).where(Invocation.workspace_id==p.workspace_id,Invocation.idempotency_key==invocation_key)) and call_count(db,p,task)>=pin['spec']['max_tool_calls']:
            raise HTTPException(409,'正式目标累计能力调用预算已用尽，旧回执保留')
    return c


def journal(db,p,task,body,operation):
    entries=task.data.get('native_goal_commands',[])
    if entries and digest(entries)!=task.data.get('native_goal_commands_hash'):raise HTTPException(409,'原目标操作历史无法核对')
    old=next((e for e in entries if e['request_id']==body.request_id),None)
    if old:
        if old.get('status')=='abandoned':raise HTTPException(409,'此原目标请求已明确作废，请保留草稿并采用新请求')
        if old['body_hash']!=digest(body.model_dump()) or old['actor_id']!=p.user_id or old['operation']!=operation:raise HTTPException(409,'原目标请求不能更换内容、操作或提交者')
        return False
    if len(entries)>=100:raise HTTPException(409,'目标操作已达100次上限，历史保留')
    entries=[*entries,{'request_id':body.request_id,'body_hash':digest(body.model_dump()),'actor_id':p.user_id,'operation':operation,'reason':body.reason,'at':now().isoformat()}]
    task.data={**task.data,'native_goal_commands':entries,'native_goal_commands_hash':digest(entries)}
    return True


def queue(db,p,task,key,**extra):
    from .data_gaps import queue as add_job
    c=task.data['native_goal_context']
    request_key='native-goal:'+task.id+':'+digest(c)+':'+key
    add_job(db,p.workspace_id,KIND,request_key,
        {'task_id':task.id,'actor_id':p.user_id,'context_hash':digest(c),**extra})
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,
        Job.idempotency_key==request_key).execution_options(populate_existing=True))
    if (not job or task.workspace_id!=p.workspace_id or job.workspace_id!=p.workspace_id
        or job.kind!=KIND or job.idempotency_key!=request_key or not isinstance(job.data,dict)
        or job.data.get('task_id')!=task.id or job.data.get('actor_id')!=p.user_id
        or job.data.get('context_hash')!=digest(c)):
        raise HTTPException(409,'原目标后台请求与当前工作区和固定上下文不一致')
    pin={'job_id':job.id,'idempotency_key':request_key,'context_hash':digest(c),'actor_id':p.user_id}
    task.data={**task.data,'native_goal_check_job_pin':pin,'native_goal_check_job_pin_hash':digest(pin)}


def define(db,p,task,body,*,_approval_start=False):
    if _approval_start:
        from .layered_approval_store_v175 import approval_starter
        approval_starter(db,p,task)
    else:
        manage(p,task)
        from .layered_approval_store_v175 import old_definition_guard
        old_definition_guard(db,p,task)
    if not journal(db,p,task,body,'define'):return
    check_version(task,body.expected_version)
    if task.status!='workflow_waiting':raise HTTPException(409,'请在原流程等待人工处理时确定目标；执行中或已结束记录不能改写')
    if task.data.get('native_goal_context'):raise HTTPException(409,'原目标已固定；变更目标请保留原任务并发起新任务')
    instance,release=active(db,p,task.data['instance_id'])
    if task.data['instance_release_id']!=release.id or task.data['business_id'] not in instance.data.get('enabled_business_ids',[]):raise HTTPException(409,'原业务版本尚未启用')
    from .entity_release_compat import verify_task_objects
    verify_task_objects(db,p,task,release)
    spec=body.goal_spec.model_dump()
    from .goal_evaluation_provider_v288 import definition_spec,definition_pin
    spec=definition_spec(db,p,task,spec)
    if spec.get('deadline'):
        from .temporal import instant
        if instant(spec['deadline'])<=now():raise HTTPException(422,'目标截止时间必须在当前时间之后')
    if any(c['kind'] not in {'evidence_sufficient','field_value','human_confirmation','capability_success','tool_value','query_value'} for c in spec['criteria']):
        raise HTTPException(422,'原流程目标包含尚未接通的完成标准，不能静默省略')
    if not any(c['kind']=='evidence_sufficient' for c in spec['criteria']):raise HTTPException(422,'正式目标必须明确要求原资料充分')
    initialize(db,p,task,spec,body.reason)
    c={'schema':'native-business-goal-v1','instance_id':instance.id,'release_id':release.id,'business_id':task.data['business_id'],
        'entity_ids':task.data['entity_ids'],'actor_id':p.user_id,'goal_snapshot':snapshot(task),**object_binding_pin(task),**definition_pin(db,p,task)}
    task.data={**task.data,'native_goal_context':c,'native_goal_context_hash':digest(c),'native_goal_query_id':None};task.version+=1
    from .native_query_proposals import formal_definition_provenance
    query_origin=formal_definition_provenance(db,p,task,task.data['goal_spec'])
    if query_origin:
        task.data={**task.data,'native_goal_query_origin':query_origin}
    from .native_goal_time import arm
    arm(db,p,task)
    queue(db,p,task,'definition');audit(db,p,'native_goal.defined',task.id,{'request_id':body.request_id,'goal_hash':task.data['goal_hash']})


def request_check(db,p,task,body):
    manage(p,task)
    if not journal(db,p,task,body,'check'):return
    guard(db,p,task);check_version(task,body.expected_version)
    task.version+=1;queue(db,p,task,'manual:'+body.request_id,native_query_refresh=body.request_id)


def source_changed(db,p,entity_id,trigger):
    rows=list(db.scalars(select(Task).where(Task.workspace_id==p.workspace_id,Task.data['native_goal_context'].is_not(None),
        Task.data['entity_ids'].contains([entity_id]),Task.status!='workflow_cancelled')))
    from .security import Principal
    for task in rows:
        if not task.data.get('native_goal_context') or task.data.get('native_goal_acceptance') or task.data.get('native_goal_cancelled') or task.data.get('native_goal_timed_out'):continue
        owner=Principal(task.data['native_goal_context']['actor_id'],p.workspace_id,p.role,'native-goal-source')
        queue(db,owner,task,'source:'+digest(trigger)[:24])


def basis(task,evidence,tool_basis=None):
    from .business_tools import evidence_hash
    value={'goal_snapshot':snapshot(task),'evidence_hash':evidence_hash(evidence),'evidence_status':evidence['status'],
        'flow_status':task.status,'workflow_receipts':task.data['workflow_runtime']['receipts'],'current_step_id':task.data['workflow_runtime']['current_step_id']}
    if tool_basis is not None:value['tool_results']=tool_basis
    if evidence.get('goal_evaluation_provider_v288') is not None:
        value['goal_evaluation_provider_v288']=copy.deepcopy(evidence['goal_evaluation_provider_v288'])
    return digest(value)


def verify_observation_history(obs):
    """当前依据和正式验收复用同一人工核查历史完整性守卫。"""
    if obs.data.get('verification_history_hash') and digest(obs.data.get('verification_history',[]))!=obs.data['verification_history_hash']:
        raise HTTPException(409,'原登记核查历史摘要不一致，不能作为当前核查或目标验收依据')


def assessment(db,p,task,accepted=False,*,lock_sources=True,graph_supplement=None):
    guard(db,p,task,lock_sources=lock_sources)
    evidence=query_goal(db,p,task,learning=False,lock_sources=lock_sources)
    if accepted and lock_sources and task.data['goal_spec'].get('relation_query'):
        # The instance SHARE lock prevents generated edge changes/phantoms.
        # Originals and endpoint records can change independently, so lock all
        # actual relationship inputs before rebuilding the formal decision.
        from .models import Observation,Relation,SetupMaterial
        edges=evidence.get('relations',{}).get('relation_evidence',[])
        entity_ids=set(task.data['entity_ids']);observation_ids=set();material_ids=set();source_ids={task.package_id}
        for edge in edges:
            for ref in edge.get('evidence',[]):
                if ref['kind']=='entity':entity_ids.add(ref['id'])
                elif ref['kind']=='observation':
                    observation_ids.add(ref['id'])
                    entity_ids.add(ref['snapshot']['entity_id'])
            material_ids.update(ref['material_id'] for ref in edge.get('document_evidence',[]))
        for identity in sorted(entity_ids):scoped(db,Entity,identity,p,True)
        for identity in sorted(observation_ids):
            observation=scoped(db,Observation,identity,p,True)
            source_ids.add(observation.source_id)
        for identity in sorted(source_ids):scoped(db,Capability,identity,p,True)
        for identity in sorted({edge['relation_id'] for edge in edges}):scoped(db,Relation,identity,p,True)
        for identity in sorted(material_ids):scoped(db,SetupMaterial,identity,p,True)
        evidence=query_goal(db,p,task,learning=False,lock_sources=lock_sources)
    if accepted and task.data['goal_spec'].get('data_query'):
        from .native_goal_queries import gate as query_gate
        query_gate(db,p,task,evidence,lock=lock_sources,lock_sources=lock_sources)
    from .models import Observation
    for item in evidence.get('observation_evidence',[]):
        obs=scoped(db,Observation,item['observation_id'],p)
        verify_observation_history(obs)
    from .native_goal_tools import workflow_goal_receipts
    from .native_step_execution import goal_gate
    workflow_tools=workflow_goal_receipts(db,p,task,lock_sources=lock_sources)
    execution_gate=goal_gate(db,p,task,accepted=accepted and lock_sources)
    from .same_goal_continuation_execution_goal_v314 import goal_gate as continuation_goal_gate
    continuation_gate=continuation_goal_gate(db,p,task)
    all_tools=[*workflow_tools,*execution_gate['receipts']]
    has_tools=bool(workflow_tools or execution_gate['runs'] or any(c['kind'] in {'capability_success','tool_value'} for c in task.data['goal_spec']['criteria']))
    tool_basis={'workflow_results':workflow_tools,'execution_basis':execution_gate['basis_hash']} if has_tools else None
    if continuation_gate['runs']:
        tool_basis={**(tool_basis or {}),'continuation_basis':continuation_gate['basis_hash']}
    from .workflow_graph_human_budget import verify as verify_human_budget
    human_budget_hash=verify_human_budget(db,p,task)
    if human_budget_hash is not None:
        tool_basis={**(tool_basis or {}),'human_budget_basis':human_budget_hash}
    if graph_supplement is not None:
        tool_basis={**(tool_basis or {}),'graph_supplement_basis':graph_supplement['basis_hash']}
    from .formal_continuation_store_v345 import goal_guard as formal_continuation_guard
    formal_guard=formal_continuation_guard(db,p,task,lock_sources=lock_sources)
    if formal_guard is not None:
        tool_basis={**(tool_basis or {}),'formal_continuation_basis':formal_guard['basis_hash']}
    result=evaluate(task,SimpleNamespace(id='native-flow:'+task.id),evidence,accepted=accepted,tool_receipts=all_tools)
    from .data_gaps import receipts_ready
    data_received=receipts_ready(db,p,{'result':evidence})
    open_actions=[]
    run_id=task.data.get('native_goal_query_id')
    if run_id:
        for gap in db.scalars(select(DataGap).where(DataGap.workspace_id==p.workspace_id,DataGap.query_id==run_id)):
            for identifier in gap.data.get('acquisition_ids',[]):
                action=scoped(db,Acquisition,identifier,p)
                if action.status in {'authorized','dispatching','unknown','awaiting_observation'}:open_actions.append({'id':action.id,'status':action.status})
    settled=data_received and not open_actions
    flow_finished=(task.status=='workflow_completed' or graph_supplement is not None or bool(formal_guard and formal_guard['formal_completion_verified'])) and task.data['workflow_runtime']['current_step_id'] is None and bool(task.data['workflow_runtime']['receipts'])
    result={**result,'flow_finished':flow_finished,'data_received':data_received,'open_device_actions':open_actions,'ready_for_acceptance':result['ready_for_acceptance'] and flow_finished and settled and execution_gate['all_reviewed'] and continuation_gate['all_reviewed'],
        'completed':result['completed'] and flow_finished and settled and execution_gate['all_reviewed'] and continuation_gate['all_reviewed'],'basis_hash':basis(task,evidence,tool_basis),'physical_state_verified':False,
        'tool_execution':{'all_reviewed':execution_gate['all_reviewed'],'runs':execution_gate['runs'],'basis_hash':execution_gate['basis_hash']},'tool_results':all_tools}
    result['continuation_execution']=continuation_gate
    if graph_supplement is not None:
        result['graph_supplement_basis']=graph_supplement['basis_hash']
    if formal_guard is not None:
        result['formal_continuation']=formal_guard
        result['ready_for_acceptance']=result['ready_for_acceptance'] and formal_guard['all_settled']
        result['completed']=result['completed'] and formal_guard['all_settled']
    from .native_cases import gate
    extra=gate(db,p,task,evidence,result['basis_hash'],lock=accepted and lock_sources)
    result={**result,'experience':extra,'ready_for_acceptance':result['ready_for_acceptance'] and extra['all_confirmed'],
        'completed':result['completed'] and extra['all_confirmed']}
    if task.data.get('sound_snapshots'):
        from .workflow_sound import require_task_received as require_sound_received
        sound_gate={'allowed':True,'reason':None}
        try:require_sound_received(db,p,task)
        except HTTPException as error:
            if error.status_code!=409:raise
            sound_gate={'allowed':False,'reason':str(error.detail)}
        result={**result,'sound_data_gate':sound_gate,
            'ready_for_acceptance':result['ready_for_acceptance'] and sound_gate['allowed'],
            'completed':result['completed'] and sound_gate['allowed']}
    if task.data.get('pixel_pair_snapshot') is not None:
        from .native_pixel_receipts_v250 import task_reception_gate
        pixel_gate=task_reception_gate(db,p,task)
        if pixel_gate['allowed']:
            from .native_pixel_provider_v250 import verify_task_humans
            try:verify_task_humans(db,p,task)
            except HTTPException as error:
                if error.status_code==403:raise
                pixel_gate={'allowed':False,'reason':str(error.detail)}
        result={**result,'pixel_data_gate':pixel_gate,
            'ready_for_acceptance':result['ready_for_acceptance'] and pixel_gate['allowed'],
            'completed':result['completed'] and pixel_gate['allowed']}
    return evidence,result


def _local_primary_request(request):
    """This handler may only evaluate local structured data, never call tools."""
    if (type(request) is not dict or request.get('mode') != 'structured'
            or 'model_profile_id' not in request or request['model_profile_id'] is not None
            or type(request.get('tool_steps')) is not list or request['tool_steps'] != []
            or request.get('allow_external_tools') is not False):
        raise HTTPException(409, '正式目标本地核对只接受结构化原资料；云模型、工具步骤或外发请求须走独立授权流程')
    return request




def fork_conflict_observation(db,p,task,original,gap,link):
    """Create a genuine local query branch, never copy an old query result.

    The caller holds Task/Gap/Policy locks and a savepoint. No cloud/tool path
    is permitted; the exact original native request/delegation is retained.
    """
    from .agents import data_agent
    from .data_gaps import publish,receive,record
    from .goal_conflict_observation_service_v259 import KEY
    c=guard(db,p,task)
    request=copy.deepcopy(_local_primary_request(original.data['request']))
    if (original.id!=task.data.get('native_goal_query_id') or gap.query_id!=original.id
        or original.data.get('native_goal_task_id')!=task.id
        or original.data.get('native_goal_context_hash')!=digest(c)):
        raise HTTPException(409,'原Goal查询分支关联已变化，未准备新观测')
    row=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=original.question,status='queued',data={
        'request':request,'goal_delegation':copy.deepcopy(original.data['goal_delegation']),
        'native_goal_task_id':task.id,'native_goal_context_hash':digest(c),'native_goal_request_hash':digest(request),
        'parent_query_id':original.id,KEY:copy.deepcopy(link)})
    db.add(row);db.flush()
    state=data_agent(db,p,_local_primary_request(request),row.id,cached_only=True,emit_gaps=False,learning=False)
    if state.get('status')!='conflict':raise HTTPException(409,'当前原Goal已非原冲突，请先核对，未新增设备动作')
    row.status=state['status'];row.data={**row.data,**state}
    identifiers=publish(db,p,row.id,state,include_records=True)
    selected=None
    for identifier in identifiers:
        child=scoped(db,DataGap,identifier,p,True)
        event=scoped(db,Event,child.data['event_id'],p,True)
        if event.kind!='data.gap' or event.data.get('query_id')!=row.id or not receive(db,event):
            raise HTTPException(409,'新查询的真实缺口未被原Goal接收')
        event.status='received';event.received_at=now();event.attempts+=1
        child.status='needs_review';child.version+=1
        child.data={**child.data,'decision_reason':'新查询仍有原同刻冲突，等待已明确的一次最新观测；旧冲突保留'}
        record(db,p,child,'verification_failed',query_id=row.id,result_status=state['status'],reason=child.data['decision_reason'])
        if child.entity_id==gap.entity_id:
            if selected is not None:raise HTTPException(409,'新查询同对象出现重复缺口')
            child.data={**child.data,KEY:copy.deepcopy(link)};selected=child
    if selected is None:raise HTTPException(409,'新查询没有与原冲突对象对应的真实缺口')
    row.data={**row.data,'result':copy.deepcopy(state),'data_gap_ids':identifiers,
        'native_goal_result_hash':digest(state),'native_goal_result_history':[
            {'status':state['status'],'evaluated_at':state.get('evaluated_at'),'result_hash':digest(state)}]}
    row.data={**row.data,'native_goal_result_history_hash':digest(row.data['native_goal_result_history'])}
    task.data={**task.data,'native_goal_query_id':row.id};task.version+=1
    db.flush();return row,selected

@report_text_parse_scope()
def process(db,p,job):
    require(p,'admin','operator');task=scoped(db,Task,job.data['task_id'],p,True)
    from .native_goal_scoped_authority_v355 import before_locked_task
    before_locked_task(db,p,task,job)
    from .goal_conflict_observation_service_v259 import _verify_task,refresh_owned_gap
    _verify_task(db,p,task)
    from .goal_evaluation_maturity_v299 import process_candidate
    if process_candidate(db,p,task,job):return
    from .native_goal_time import admitted,expire,arm,timeout
    if task.data.get('native_goal_acceptance') or task.data.get('native_goal_cancelled') or task.status=='workflow_cancelled' or timeout(task):
        job.data={**job.data,'ended_goal_skipped':True};return
    if not admitted(task,job):job.data={**job.data,'superseded_timer_skipped':True};return
    c=guard(db,p,task,allow_expired=True)
    if job.data['context_hash']!=digest(c):raise HTTPException(409,'后台请求不属于原目标版本')
    if expire(db,p,task):job.data={**job.data,'goal_deadline_recorded':True};return
    from .goal_evaluation_provider_v288 import process_check
    if process_check(db,p,task,job):return
    from .data_requirements import prepare_request
    from .agents import data_agent
    from .data_gaps import publish,record,route,coordinate
    run_id=task.data.get('native_goal_query_id')
    if run_id:
        run=scoped(db,DataRun,run_id,p,True)
        if digest(run.data['request'])!=run.data.get('native_goal_request_hash'):raise HTTPException(409,'原目标资料请求摘要已改变')
        if run.data['goal_delegation']!={'task_id':task.id,'goal_snapshot':snapshot(task)}:raise HTTPException(409,'原资料需求与正式目标关联改变')
    else:
        spec=task.data['goal_spec']
        source_question='读取原任务已确认范围内的原资料' if spec.get('data_query') else spec['purpose']
        request=prepare_request(db,p,DataRequest(question=source_question,entity_ids=spec['entity_ids'],fields=spec['required_fields'],mode='structured',
            business_timezone='Asia/Shanghai',evidence_requirement=spec['evidence'],relation_query=spec.get('relation_query')).model_dump(),_confirmed_plan=True)
        request['resolved_time']=copy.deepcopy(task.data['goal_window'])
        run=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=spec['purpose'],status='queued',data={'request':request,
            'goal_delegation':{'task_id':task.id,'goal_snapshot':snapshot(task)},'native_goal_task_id':task.id,'native_goal_context_hash':digest(c),'native_goal_request_hash':digest(request),
            **({'original_purpose':spec['purpose']} if spec.get('data_query') else {})})
        db.add(run);db.flush()
        state=data_agent(db,p,_local_primary_request(request),run.id,cached_only=True,emit_gaps=False,learning=False)
        run.status=state['status'];run.data={**run.data,**state};run.data={**run.data,'data_gap_ids':publish(db,p,run.id,state,include_records=True)}
        task.data={**task.data,'native_goal_query_id':run.id}
    current=data_agent(db,p,_local_primary_request(run.data['request']),run.id,cached_only=True,emit_gaps=False,learning=False)
    if run.data.get('native_goal_result_hash') and digest(run.data['result'])!=run.data['native_goal_result_hash']:raise HTTPException(409,'原目标上次资料核对摘要不一致')
    previous=run.data.get('native_goal_result_history',[])
    if run.data.get('native_goal_result_history_hash') and digest(previous)!=run.data['native_goal_result_history_hash']:raise HTTPException(409,'原目标资料核对历史摘要不一致')
    history=[*previous,{'status':current['status'],'evaluated_at':current.get('evaluated_at'),'result_hash':digest(current)}][-100:]
    run.data={**run.data,'result':current,'native_goal_result_hash':digest(current),'native_goal_result_history':history,'native_goal_result_history_hash':digest(history)};run.version+=1
    if current['status'] in {'insufficient','conflict'} and not run.data.get('data_gap_ids'):
        run.data={**run.data,'data_gap_ids':publish(db,p,run.id,current,include_records=True)}
    gaps=[]
    for identifier in run.data.get('data_gap_ids',[]):
        gap=scoped(db,DataGap,identifier,p,True)
        if gap.query_id!=run.id or gap.entity_id not in task.data['entity_ids']:raise HTTPException(409,'原目标缺口与对象不一致')
        if gap.status=='resolved' and (current['status']!='sufficient'):
            gap.status='received';gap.version+=1;record(db,p,gap,'reopened',reason='正式目标原要求仍未满足；旧检查保留')
        if gap.status=='pending_delivery':
            event=scoped(db,Event,gap.data['event_id'],p,True)
            if event.kind!='data.gap' or event.data.get('query_id')!=run.id or event.data.get('gap_id')!=gap.id:raise HTTPException(409,'原缺口回执不匹配')
            gap.status='received';gap.version+=1;event.status='received';event.received_at=now();event.attempts+=1;record(db,p,gap,'received',reason='原业务目标已接收资料缺口')
        from .native_goal_decision_handoff_v279 import defer_route
        decision_handoff=defer_route(db,p,task,run,gap,current)
        if not decision_handoff and not refresh_owned_gap(db,p,task,run,gap,current):route(db,p,gap.id)
        from .services import enqueue_event
        if not decision_handoff and gap.status not in {'resolved','cancelled','awaiting_observation'}:
            event=enqueue_event(db,p,'data.gap_coordination','gap-coordination:'+gap.id,{'gap_id':gap.id,'entity_id':gap.entity_id,'query_id':run.id,'actor_id':run.owner_id})
            if event.status=='pending_delivery':
                if not coordinate(db,event):raise HTTPException(409,'原业务尚未接收资料缺口')
                event.status='received';event.received_at=now();event.attempts+=1
        gaps.append({'id':gap.id,'status':gap.status,'reason':gap.data.get('resolution_reason') if gap.status=='resolved' else gap.data.get('decision_reason'),
            'business_task_id':gap.data.get('business_task_id'),'acquisition_ids':gap.data.get('acquisition_ids',[])})
    task=scoped(db,Task,task.id,p,True)
    if task.data['goal_spec'].get('data_query') and current['status']=='sufficient':
        from .native_goal_queries import ensure as ensure_query
        ensure_query(db,p,task,refresh=job.data.get('native_query_refresh',False))
    evidence,result=assessment(db,p,task)
    report={'schema':'native-goal-check-v1','checked_at':now().isoformat(),'goal_snapshot':snapshot(task),'evidence':evidence,'evaluation':result,'gaps':gaps,
        'cloud_calls':0,'new_model_inferences':0,'device_actions_performed':False,'device_execution_status':'requires_actual_receipt'}
    receipt=Memory(workspace_id=p.workspace_id,status='candidate',text='原业务目标资料与完成标准核对',data={'type':'native_goal_check','task_id':task.id,'report':report,'report_hash':digest(report)})
    db.add(receipt);db.flush();task.data={**task.data,'native_goal_check_id':receipt.id,'native_goal_error':None};job.data={**job.data,'goal_check_id':receipt.id}
    arm(db,p,task,evidence,generation=receipt.id)


def verify_workflow_inputs(db,p,task,*,lock_sources=True):
    """Prove frozen originals and technical receipts without accepting a Goal.

    Internal assessment reads these sources before the first evaluation exists
    and while its own Goal-check Job is running. It must not recursively demand
    that same Job's completed report. Final acceptance has a separate gate.
    """
    from .native_pixel_receipts_v250 import require_task_received
    require_task_received(db, p, task)
    from .workflow_sound import require_task_received as require_sound_received
    require_sound_received(db,p,task)
    from .native_pixel_provider_v250 import verify_task_humans
    verify_task_humans(db, p, task)
    from .instance_compiler import verify_material_snapshots
    verify_material_snapshots(db,p,task.data.get('material_snapshots',[]),allow_archived=True)
    from .workflow_images import verify,verify_masks
    verify(db,p,task);verify_masks(db,p,task)
    from .workflow_scenes import verify_masks as verify_scene_masks
    verify_scene_masks(db,p,task)
    from .workflow_audio import verify_receipts
    verify_receipts(db,p,task)
    from .workflow_geo_pairs import verify_receipts as verify_pairs
    verify_pairs(db,p,task)
    from .workspace_api import verify_perception_evidence
    verify_perception_evidence(db,p,task,lock_sources=lock_sources)
    for receipt in task.data['workflow_runtime']['receipts']:
        for key in ('material_snapshots','image_snapshots','scene_snapshots','comparison_scene_snapshots','audio_snapshots','sound_snapshots'):
            if key in receipt and receipt[key]!=task.data.get(key,[]):
                raise HTTPException(409,'原任务输入与流程冻结原件不一致，不能验收或复用')
        if receipt.get('execution_mode')=='registered_ability':
            call=scoped(db,Invocation,receipt['invocation_id'],p)
            cap=scoped(db,Capability,receipt['capability_id'],p)
            if (call.status!='succeeded' or call.capability_id!=cap.id or cap.content_hash!=receipt['capability_hash'] or digest(cap.data)!=cap.content_hash
                or call.data.get('capability_hash')!=receipt['capability_hash'] or digest(receipt['input_snapshot'])!=receipt['input_hash']
                or call.data.get('input_hash')!=receipt['input_hash'] or call.data.get('output')!=receipt['output']):
                raise HTTPException(409,'原注册能力调用、输入版本或流程回执不一致，不能验收')


def verify_acceptance_inputs(db,p,task,*,lock_sources=True):
    """Final acceptance requires both the mature evaluation and source proof."""
    from .goal_evaluation_provider_v288 import require_current
    require_current(db,p,task,lock_sources=lock_sources)
    verify_workflow_inputs(db,p,task,lock_sources=lock_sources)


def accept(db,p,task,body):
    manage(p,task)
    if not journal(db,p,task,body,'accept'):return
    from .layered_approval_store_v175 import old_acceptance_guard
    old_acceptance_guard(db,p,task)
    # 正式写入不能继承同事务只读核查缓存中的未锁定上游授权。
    db.info.pop('native_experience_future_work',None)
    guard(db,p,task);check_version(task,body.expected_version)
    # The instance remains current until the acceptance transaction commits.
    db.execute(select(DomainInstance).where(DomainInstance.id==task.data['instance_id'],DomainInstance.workspace_id==p.workspace_id)
        .with_for_update(read=True).execution_options(populate_existing=True)).scalar_one()
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    guard(db,p,task)
    # Re-evaluate now without models/tools; cached receipt alone cannot authorize completion.
    verify_acceptance_inputs(db,p,task)
    evidence,result=assessment(db,p,task,accepted=True)
    if body.basis_hash!=result['basis_hash']:raise HTTPException(409,'原目标依据或流程进度已变化，请重新核对后验收')
    if not result['completed']:raise HTTPException(409,'业务步骤、资料或完成标准尚未全部满足，不能验收')
    value={'request_id':body.request_id,'actor_id':p.user_id,'reason':body.reason,'at':now().isoformat(),'goal_snapshot':snapshot(task),'basis_hash':body.basis_hash,
        'evaluation':result,'evidence':evidence,'physical_state_verified':False,'learning_auto_published':False}
    if result['experience']['required']:
        value['experience_proof']=copy.deepcopy(result['experience'])
        value['experience_proof_hash']=digest(value['experience_proof'])
    receipt=Memory(workspace_id=p.workspace_id,status='candidate',text='原业务目标已保存人工验收结果',data={
        'type':'native_goal_outcome','task_id':task.id,'instance_release_id':task.data['instance_release_id'],'package_id':task.package_id,
        'goal_snapshot':snapshot(task),'acceptance':value,'acceptance_hash':digest(value),'workflow_receipts':copy.deepcopy(task.data['workflow_runtime']['receipts']),
        'entity_state_updated':False,'auto_publish':False,'physical_state_verified':False})
    db.add(receipt);db.flush()
    from .native_goal_time import cancel
    cancel(db,p,task,'原业务目标已保存正式验收')
    task.data={**task.data,'native_goal_acceptance':value,'native_goal_acceptance_hash':digest(value),'native_goal_data_receipt_id':receipt.id};task.version+=1
    audit(db,p,'native_goal.accepted',task.id,{'request_id':body.request_id,'basis_hash':body.basis_hash})
    from .native_method_suggestions import acceptance_hook
    acceptance_hook(db,p,task)


def stop(db,p,task,body):
    manage(p,task)
    if not journal(db,p,task,body,'stop'):return
    saved_context(db,p,task)
    if task.status=='workflow_cancelled' or task.data.get('native_goal_cancelled') or task.data.get('native_goal_acceptance'):raise HTTPException(409,'正式目标已取消或验收，不能继续旧协调')
    check_version(task,body.expected_version)
    from .native_goal_time import cancel
    cancel(db,p,task,'原目标已明确停止')
    from .native_goal_acquisitions import stop_coordination
    stop_coordination(db,p,task,'原业务目标已明确停止')
    from .native_step_execution import stop_all
    stop_all(db,p,task,'原业务目标已明确停止')
    from .native_goal_queries import stop_all as stop_queries
    stop_queries(db,p,task,'原业务目标已明确停止')
    task.data={**task.data,'native_goal_cancelled':{'request_id':body.request_id,'reason':body.reason,'actor_id':p.user_id,'at':now().isoformat()}};task.version+=1
    audit(db,p,'native_goal.stopped',task.id,{'reason':body.reason})


def recover(db,p,task,body):
    manage(p,task)
    model={'define':GoalDefinition,'check':GoalCommand,'accept':GoalAcceptance,'stop':GoalCommand}[body.operation]
    try:original=model.model_validate(body.original_request)
    except ValidationError:raise HTTPException(422,'原待核请求结构不完整，草稿保留，不能猜测或作废')
    hash_value=digest(original.model_dump())
    entries=task.data.get('native_goal_commands',[])
    if entries and digest(entries)!=task.data.get('native_goal_commands_hash'):raise HTTPException(409,'原目标操作历史无法核对')
    old=next((r for r in entries if r['request_id']==original.request_id),None)
    if old:
        if old['body_hash']!=hash_value or old['actor_id']!=p.user_id or old['operation']!=body.operation:raise HTTPException(409,'原请求编号属于不同内容或提交者，不能恢复或作废')
        return {'saved':old.get('status')!='abandoned','abandoned':old.get('status')=='abandoned','version':task.version}
    if body.action=='probe':return {'saved':False,'abandoned':False,'version':task.version}
    check_version(task,body.expected_version)
    if len(entries)>=100:raise HTTPException(409,'目标操作已达上限，不能丢弃原记录')
    entries=[*entries,{'request_id':original.request_id,'body_hash':hash_value,'actor_id':p.user_id,'operation':body.operation,
        'status':'abandoned','reason':body.reason,'at':now().isoformat()}]
    task.data={**task.data,'native_goal_commands':entries,'native_goal_commands_hash':digest(entries)};task.version+=1
    audit(db,p,'native_goal.request_abandoned',task.id,{'request_id':original.request_id,'reason':body.reason})
    return {'saved':False,'abandoned':True,'version':task.version}


def view(db,p,task):
    from .workspace_api import saved_execution
    _,release,_=saved_execution(db,p,task.id)
    from .layered_approval_store_v175 import detail as approval_detail
    approval=approval_detail(db,p,task,with_basis=False)
    if approval is not None and (approval.get('schema')!='layered-approval-task-view-v175' or approval.get('task_id')!=task.id):
        raise HTTPException(409,'原独立验收所属任务或来源无法核对')
    approval_accepted=approval is not None and approval['status']=='accepted'
    if approval_accepted:
        accepted=approval.get('acceptance')
        accepted_basis=accepted.get('basis') if type(accepted) is dict else None
        if (type(accepted) is not dict or accepted.get('decision')!='accept'
            or type(accepted_basis) is not dict or accepted_basis.get('schema')!='layered-approval-acceptance-basis-v175'
            or accepted_basis.get('task_id')!=task.id
            or accepted_basis.get('submission_hash')!=approval['submission_hash']):
            raise HTTPException(409,'原独立验收记录或提交依据无法核对')
    package=scoped(db,Capability,task.package_id,p)
    schemas=package.data['specification']['entity_schemas'];fields={};field_kinds={};field_units={}
    for identifier in task.data['entity_ids']:
        obj=scoped(db,Entity,identifier,p)
        kind=next((x for x in release.data['snapshot']['object_types'] if x['key']==obj.entity_type),None)
        field_kinds.update({key:value.get('type') for key,value in schemas[obj.entity_type].get('properties',{}).items()})
        fields.update({f['key']:f['label'] for f in (kind['fields'] if kind else []) if f['key'] in schemas[obj.entity_type].get('properties',{})})
        field_units.update({f['key']:f.get('unit') for f in (kind['fields'] if kind else [])})
    result={'task_id':task.id,'version':task.version,'fields':fields,'field_kinds':field_kinds,'field_units':field_units,'entity_ids':task.data['entity_ids'],'can_manage':p.role=='admin' or p.role=='operator' and task.owner_id==p.user_id,
        'goal':task.data.get('goal_spec'),'goal_confirmed':bool(task.data.get('native_goal_context')),'cancelled':task.data.get('native_goal_cancelled'),'acceptance':task.data.get('native_goal_acceptance'),
        'data_received':bool(task.data.get('native_goal_data_receipt_id')),'task_status':task.status,'purpose_default':task.title,'check':None,'current_gaps':[],'scope_error':None}
    result['acceptance_source']=('layered_approval_v175' if approval_accepted else
        'native_goal' if approval is None and result['acceptance'] else None)
    if approval is not None:
        result['approval_completion']={'schema':'native-goal-approval-completion-v175',
            'managed_id':approval['managed_id'],'version':approval['version'],
            'status':approval['status'],'submission_hash':approval['submission_hash'],
            'accepted':approval_accepted,'acceptance':copy.deepcopy(approval['acceptance']) if approval_accepted else None,
            'historical':approval_accepted,'can_execute':False,'physical_state_verified':False}
        result['check_historical']=False
    if approval_accepted:
        # The independent receipt is not a legacy native-goal receipt and
        # cannot authorize another Goal command or a new execution.
        result['can_manage']=False
    from .workflow_worker_status import check as check_worker
    from .goal_approval_claim_barrier_v354 import project as project_approval_hold
    result['check_worker']=project_approval_hold(check_worker(db,p,task),approval)
    try:
        from .native_goal_tools import for_task as tool_metadata
        available=tool_metadata(db,p,task)
        result['goal_tools']=available['tools'];result['goal_tools_error']=None
        result.update({key:available[key] for key in ['query_tools','query_tools_error']})
        result.update({key:available[key] for key in ['relation_types','relation_types_error']})
    except HTTPException as error:
        result['goal_tools']=[];result['goal_tools_error']=str(error.detail)
        result['query_tools']=[];result['query_tools_error']=str(error.detail)
        result['relation_types']=[];result['relation_types_error']=str(error.detail)
    from .native_goal_queries import view as query_view
    result['data_query']=query_view(db,p,task)
    if approval_accepted and result['data_query'].get('id'):
        result['data_query']={**result['data_query'],'historical':True,
            'closed_reason':'独立验收已保存，原查询仅供回看'}
    from .instance_relation_types import relation_metadata
    frozen_entities=relation_metadata(package,task.data.get('object_snapshots',[]))
    result['package_id']=package.id;result['entities']=frozen_entities['entities']
    result['entity_names']={item['id']:item['title'] for item in result['entities']}
    result['can_stop']=False
    if result['goal_confirmed'] and result['can_manage'] and not result['acceptance'] and not result['cancelled'] and task.status!='workflow_cancelled':
        try:saved_context(db,p,task);result['can_stop']=True
        except HTTPException:pass
    if result['acceptance'] and digest(result['acceptance'])!=task.data.get('native_goal_acceptance_hash'):raise HTTPException(409,'原业务验收回执摘要不一致')
    if result['goal_confirmed'] and not result['acceptance'] and not approval_accepted:
        try:guard(db,p,task,lock_sources=False)
        except HTTPException as e:result['scope_error']=str(e.detail)
    identifier=task.data.get('native_goal_check_id')
    if identifier:
        receipt=scoped(db,Memory,identifier,p)
        if receipt.data.get('task_id')!=task.id or digest(receipt.data['report'])!=receipt.data.get('report_hash'):raise HTTPException(409,'原目标核对依据无法核实')
        result['check']=copy.deepcopy(receipt.data['report'])
        from .goal_conflict_observation_service_v259 import historical_check_gaps
        branch_gaps=historical_check_gaps(db,p,task,result['check'])
        if approval_accepted or branch_gaps is not None:result['check_historical']=True
        if not result['acceptance'] and not result['scope_error'] and not approval_accepted and branch_gaps is None:
            # Show today's additional gate without rewriting the frozen check receipt.
            try:
                current_evidence,current_result=assessment(db,p,task,lock_sources=False)
                result['check']['evidence']=current_evidence
                result['check']['evaluation']=current_result
                result['check']['current_basis_changed']=receipt.data['report']['evaluation']['basis_hash']!=current_result['basis_hash']
            except HTTPException as error:
                result['check']['evaluation']={**result['check']['evaluation'],'ready_for_acceptance':False,'completed':False}
                result['check']['current_gate_error']=str(error.detail)
            result['check']['current_gate_checked_at']=now().isoformat()
        if branch_gaps is not None:
            result['current_gaps']=branch_gaps
        else:
            for item in result['check'].get('gaps',[]):
                gap=scoped(db,DataGap,item['id'],p)
                if gap.query_id!=task.data.get('native_goal_query_id') or gap.entity_id not in task.data['entity_ids']:raise HTTPException(409,'原目标缺口关联无法核实')
                result['current_gaps'].append({**item,'status':gap.status,'reason':gap.data.get('resolution_reason') if gap.status=='resolved' else gap.data.get('decision_reason')})
    from .native_goal_time import timeout,view as time_view
    result['timed_out']=timeout(task);result['next_time_check']=time_view(db,p,task)
    result['reviewable_records']=[]
    if result['goal_confirmed'] and not result['scope_error'] and not result['acceptance'] and not result['cancelled'] and not approval_accepted and task.data['goal_spec']['evidence']['source']!='archive':
        from .services import query_evidence
        from .models import Observation
        from .observation_integrity import checked_report,CHECK_ERRORS
        spec=task.data['goal_spec']
        query=query_evidence(db,p,spec['entity_ids'],spec['required_fields'],learning=False,requirement={**spec['evidence'],'minimum_verification':'unverified_extraction'},fixed_window=task.data['goal_window'])
        for item in query.get('observation_evidence',[])[:10]:
            obs=scoped(db,Observation,item['observation_id'],p)
            try:
                report=checked_report(db,p,obs)
                if not any(report['attributes'].get(field) is not None for field in spec['required_fields']):continue
                verify_observation_history(obs)
                from .mqtt_unit_evidence_v342 import KEY as MQTT_UNIT_KEY
                units=obs.data.get(MQTT_UNIT_KEY,{}).get('fields',{}) if obs.data.get('mqtt_receipt_id') else {}
                result['reviewable_records'].append({'id':obs.id,'version':obs.version,'source_name':report['source_name'],'observed_at':obs.observed_at.isoformat(),
                    'original_values':obs.data.get('original_request',{}).get('attributes'),'extracted_values':report['attributes'],'review':report['review'],
                    'measurement_units':{key:units[key] for key in spec['required_fields'] if key in units},
                    'input_hash':digest({'extracted':obs.data['extracted'],'evidence_ids':obs.data.get('evidence_ids',[]),'invocation_id':obs.data['invocation_id']}),
                    'saved_review':obs.data.get('human_verification') if report['review'] in {'confirmed','rejected'} else None})
            except CHECK_ERRORS:continue
    result['error']=task.data.get('native_goal_error') or (result['check'] or {}).get('current_gate_error')
    return result
