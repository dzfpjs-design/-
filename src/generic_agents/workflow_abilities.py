"""Pinned registered abilities and explicit service-delegation eligibility.

Legacy workflow execution remains read-only. Service eligibility is a separate
registration gate, not an execution route, approval or successful receipt.
"""
import copy,json
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select,func
from .contracts import digest
from .models import Capability,Job,DomainInstance,InstanceRelease,Invocation,Memory,Audit,now
from .services import scoped,published,validate,audit

ROLES={'业务智能体':'business','多模感知智能体':'perception','数据管理智能体':'data'}
ACTIONS={'observe','state','data','supplement','write','learn'}
SOURCES={'object_records','object_attributes','original_evidence_ids','task_image_ids','task_scene_refs','task_comparison_refs','task_pixel_pair_refs','task_audio_ref','task_sound_ref','perception_frame_ids','previous_output','previous_output_object','object_ids','task_identifier','task_context','request_key','observation_records'}
MANIFEST_BINDINGS={'archive_records':'object_records','observation_records':'observation_records','entity_ids':'object_ids','task_id':'task_identifier','task_evidence':'task_context','task_scene_refs':'task_scene_refs','task_comparison_refs':'task_comparison_refs','task_pixel_pair_refs':'task_pixel_pair_refs','task_audio_ref':'task_audio_ref','task_sound_ref':'task_sound_ref','task_identifier':'task_identifier','request_key':'request_key'}
ADAPTERS={'builtin','http','mcp','local_specialist','local_geospatial','local_geo_pair','local_learned_geo_pair','local_pixel_change','local_audio','local_environment_sound','local_task_segmented_audio','cloud_extractor','business_reasoning','business_decision_v276','business_decision_v284','business_goal_ready_v288'}


def eligible(cap,role,*,allow_task_archive=False,allow_task_packet=False,allow_document_packet=False):
    if cap.kind not in {'tool','specialist_model'} or role not in cap.data['allowed_agent_roles']:
        raise HTTPException(409,'该能力类型或智能体角色不符合当前节点')
    if cap.data['execution'].get('adapter') == 'task_archive':
        from .workflow_task_archive_v198 import validate_manifest
        if not allow_task_archive or role != 'data' or cap.status != 'published' or cap.content_hash != digest(cap.data) or cap.validated_hash != cap.content_hash:
            raise HTTPException(409,'归档能力的当前角色或发布版本无法核对')
        validate_manifest(cap.data)
        return
    if cap.data['execution'].get('adapter') == 'task_evidence_packet':
        from .workflow_task_packet_v199 import validate_manifest
        if not allow_task_packet or role != 'data' or cap.status != 'published' or cap.content_hash != digest(cap.data) or cap.validated_hash != cap.content_hash:
            raise HTTPException(409,'任务资料包的当前角色、已采用流程或发布版本无法核对')
        validate_manifest(cap.data)
        return
    if cap.data['execution'].get('adapter') == 'runtime_document_packet':
        from .workflow_document_packet_v207 import validate_manifest
        if not allow_document_packet or role != 'data' or cap.status != 'published' or cap.content_hash != digest(cap.data) or cap.validated_hash != cap.content_hash:
            raise HTTPException(409,'运行期资料包的角色、已采用流程或发布版本无法核对')
        validate_manifest(cap.data)
        return
    if cap.data['execution'].get('adapter') not in ADAPTERS:
        raise HTTPException(409,'此能力尚未接入实际流程执行器')
    if cap.data['execution'].get('side_effect'):
        raise HTTPException(409,'设备动作或外部写入需要已批准执行计划，不能由流程节点直接调用')
    if cap.data['execution'].get('query_tool'):
        raise HTTPException(409,'此数据查询工具需要专用查询范围，请通过查询计划执行')
    attempts=cap.data['budget'].get('max_attempts',2)
    if not isinstance(attempts,int) or isinstance(attempts,bool) or not 1<=attempts<=5:
        raise HTTPException(409,'当前能力节点需要1—5次的明确尝试上限，请核对登记预算')


def eligible_service(cap,role,*,service_actor,operation):
    """Check a real published service capability; caller still owns IAM/fences.

    The graph keeps its original ten execution fields. The independent V195
    binding retains the source service actor and operation, pinned by the actual
    capability hash. This function does not dispatch, approve, or recover a call.
    """
    from .business_flow_nested_execution_v195 import service_delegation
    from .full_semantic_graph_v1 import _schema
    from .contracts import Manifest
    try:
        delegation=service_delegation(service_actor,operation)
        data=cap.data
        if (type(data) is not dict or cap.kind!='tool' or data.get('kind')!='tool'
            or role!=delegation['agent_role'] or cap.status!='published'
            or cap.content_hash!=digest(data) or cap.validated_hash!=cap.content_hash):
            raise ValueError('service_publication')
        # Normalize schema errors to the same closed eligibility rejection
        # before Manifest's validator can raise jsonschema.SchemaError directly.
        _schema(data['input_schema']);_schema(data['output_schema'])
        manifest=Manifest.model_validate(data)
        execution=data['execution']
        if (type(execution) is not dict
            or set(execution)!={'adapter','service_actor','operation','side_effect'}
            or execution['adapter']!='workflow_service'
            or execution['service_actor']!=delegation['service_actor']
            or execution['operation']!=delegation['operation']
            or execution['side_effect'] is not True
            or role not in manifest.allowed_agent_roles):
            raise ValueError('service_registration')
        attempts=data['budget'].get('max_attempts',2)
        if type(attempts) is not int or not 1<=attempts<=5:
            raise ValueError('service_attempts')
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError,OverflowError):
        raise HTTPException(409,'服务职责、真实发布能力或有副作用的执行合同无法核对；未取得执行权限') from None


def external(cap):
    execution = cap.data['execution']
    return (execution.get('adapter') in {'http','mcp','cloud_extractor','business_reasoning','business_decision_v276','business_decision_v284','business_goal_ready_v288'}
        or execution.get('adapter') == 'workflow_service'
        and execution.get('operation') == 'execute_registered_tool_plan')


def require_binding_reviewed(step):
    """Retained bindings are evidence to review, never renewed authority."""
    if 'bindingReview' in step and (step.get('execution') or step.get('planning_tools')):
        raise HTTPException(409,'此步骤的能力绑定需要重新核对；请核对并保存执行能力或规划工具后再生成版本')


def pin(db,p,step,policy):
    require_binding_reviewed(step)
    spec=copy.deepcopy(step.get('execution') or {})
    if not isinstance(spec,dict) or not spec.get('capability_id'):
        raise HTTPException(409,'自动节点尚未绑定能力，请选择已发布执行能力')
    if step.get('agent') not in ROLES:raise HTTPException(422,'负责智能体无效')
    cap=published(db,p,spec['capability_id']);eligible(cap,ROLES[step['agent']])
    action=spec.get('automation_action')
    if action not in ACTIONS:raise HTTPException(422,'请指定该节点对应的自动化动作')
    if action in {'supplement','write'}:
        raise HTTPException(409,'设备补采或业务写入需要已批准执行计划，当前只读能力节点不能冒充该动作')
    rule=(policy or {}).get('rules',{}).get(action,{})
    if rule.get('mode') not in {'自动运行','执行前确认'}:
        raise HTTPException(409,'此动作当前仅给出建议；需核对自动化范围后才能执行')
    if external(cap) and (spec.get('allow_external') is not True or not cap.data['data_policy'].get('cloud_allowed' if cap.data['execution'].get('adapter') in {'cloud_extractor','business_reasoning','business_decision_v276','business_decision_v284','business_goal_ready_v288'} else 'external_allowed')):
        raise HTTPException(409,'此能力会传送输入到外部服务，请先明确批准范围和数据外发')
    arguments=spec.get('arguments',{});bindings=spec.get('bindings',{})
    properties=cap.data['input_schema'].get('properties',{})
    if cap.data['input_schema'].get('type')!='object' or not properties:
        raise HTTPException(409,'当前流程节点需要具有命名输入字段的能力契约')
    if not isinstance(arguments,dict) or not isinstance(bindings,dict) or set(arguments)&set(bindings) or (set(arguments)|set(bindings))-set(properties):
        raise HTTPException(422,'能力参数与服务器输入来源重复或未在契约中登记')
    for field,source in cap.data['execution'].get('input_bindings',{}).items():
        forced={'source':MANIFEST_BINDINGS[source]}
        if field in arguments or field in bindings and bindings[field]!=forced:
            raise HTTPException(422,'能力契约锁定的输入来源不能被参数或模型替换')
        bindings[field]=forced
    spec['arguments']=arguments;spec['bindings']=bindings
    if len(json.dumps(spec,ensure_ascii=False).encode())>32000:raise HTTPException(422,'节点参数超过上限')
    for field,binding in bindings.items():
        if not isinstance(binding,dict) or not isinstance(binding.get('source'),str) or binding['source'] not in SOURCES:
            raise HTTPException(422,'能力输入只能绑定已保存对象、原件或前序真实结果')
        if binding['source']=='previous_output' and (not binding.get('step_id') or not isinstance(binding.get('field'),str)):
            raise HTTPException(422,'前序结果需指定步骤和输出字段')
        if binding['source']=='previous_output_object' and (set(binding)!={'source','step_id'}
                or type(binding['step_id']) is not str or not binding['step_id'].strip()):
            raise HTTPException(422,'前序完整结果只能明确绑定原步骤，不接受字段、常量或额外参数')
    if cap.data['execution']['adapter']=='local_specialist' and bindings.get('evidence_ids',{}).get('source') not in {'task_image_ids','perception_frame_ids'}:
        raise HTTPException(422,'本地检测必须明确使用任务选中的图片或关联视频的保存画面；文档和常量不能冒充原图')
    if set(cap.data['input_schema'].get('required',[]))-(set(arguments)|set(bindings)):
        raise HTTPException(422,'请补齐能力必需的输入参数或来源')
    branch=spec.get('branch_field','')
    if step.get('condition'):
        out=cap.data['output_schema'].get('properties',{})
        if not branch or out.get(branch,{}).get('type')!='boolean' or branch not in cap.data['output_schema'].get('required',[]):
            raise HTTPException(422,'自动分支必须绑定能力返回的必需布尔字段，不能猜测条件')
    for field,value in arguments.items():
        from jsonschema import Draft202012Validator
        try:Draft202012Validator(cap.data['input_schema']).evolve(schema=properties[field]).validate(value)
        except Exception:raise HTTPException(422,'能力参数“'+field+'”不符合输入契约')
    return {**spec,'capability_hash':cap.content_hash,'capability_name':cap.data['description'],
        'output_schema':cap.data['output_schema'],'release_version':cap.release_version,'agent_role':ROLES[step['agent']],'mode':rule['mode'],'adapter':cap.data['execution']['adapter']}


def attributes(obj):
    return {f['label']:copy.deepcopy(obj['attributes'][f['key']]) for f in obj['fields'] if f['key'] in obj['attributes']}


def _previous_output_object(db,p,task,prior,visit):
    """Only a real same-Task registered SDK result may supply a whole object."""
    message='前序完整结果的任务、原步骤、后台任务或成功SDK依据无法核对'
    def need(value):
        if not value:raise HTTPException(409,message)
    try:
        need(db is not None and p is not None and task.workspace_id==p.workspace_id
            and prior.get('execution_mode')=='registered_ability' and type(prior.get('output')) is dict)
        need(all(type(prior.get(key)) is str and str(UUID(prior[key]))==prior[key]
            for key in ('request_id','invocation_id','capability_id','actor')))
        job=db.scalar(select(Job).where(Job.id==prior.get('request_id'),Job.workspace_id==p.workspace_id)
            .execution_options(populate_existing=True))
        call=db.scalar(select(Invocation).where(Invocation.id==prior.get('invocation_id'),Invocation.workspace_id==p.workspace_id))
        cap=db.scalar(select(Capability).where(Capability.id==prior.get('capability_id'),Capability.workspace_id==p.workspace_id))
        need(job is not None and call is not None and cap is not None
            and job.kind=='execute_workflow_step' and job.data.get('task_id')==task.id)
        scope=job.data['scope'];inputs=prior['input_snapshot']
        need(type(scope) is dict and type(scope.get('visit')) is int and scope['visit']==visit
            and type(scope.get('attempt')) is int and scope['attempt']>=1
            and scope.get('step_id')==prior['step_id'] and job.data.get('actor_id')==prior['actor']
            and digest(inputs)==prior['input_hash']==scope.get('input_hash')
            and digest(scope.get('inputs'))==prior['input_hash'])
        key='workflow:'+task.id+':'+str(visit)+':'+str(scope['attempt'])
        need(job.idempotency_key==job.data.get('invocation_key')==call.idempotency_key==key
            and call.status=='succeeded' and call.capability_id==cap.id
            and cap.content_hash==cap.validated_hash==digest(cap.data)==prior['capability_hash']
            and cap.release_version==prior['release_version']
            and call.data.get('job_id')==job.id and call.data.get('role')==ROLES[prior['role']]
            and call.data.get('capability_hash')==cap.content_hash
            and call.data.get('input_hash')==prior['input_hash']
            and digest(call.data.get('output'))==digest(prior['output']))
        release=db.scalar(select(InstanceRelease).where(InstanceRelease.id==task.data['instance_release_id'],
            InstanceRelease.workspace_id==p.workspace_id))
        need(release is not None and release.instance_id==task.data['instance_id']
            and release.package_id==task.package_id and digest(release.data['snapshot'])==release.configuration_hash)
        business=next(value for value in release.data['snapshot']['businesses'] if value['id']==task.data['business_id'])
        steps=business['flow']['steps'];index=next(i for i,value in enumerate(steps) if value['id']==prior['step_id'])
        original=steps[index];execution=original['execution']
        need(original['agent']==prior['role'] and original['name']==prior['step_name']
            and original['output']==prior['output_name'] and execution['capability_id']==cap.id
            and execution['capability_hash']==cap.content_hash and execution['release_version']==cap.release_version
            and execution['agent_role']==call.data['role'])
        branch=prior['output'].get(execution.get('branch_field')) if original.get('condition') else None
        need(prior.get('branch')==branch and (not original.get('condition') or type(branch) is bool))
        next_id=original.get('elseId','end') if branch is False else original.get('nextId','auto')
        if next_id=='auto':next_id=steps[index+1]['id'] if index+1<len(steps) else 'end'
        queued=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==task.id,
            Audit.action=='workflow.ability_queued',Audit.data['job'].as_string()==job.id).limit(2)))
        completed=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==task.id,
            Audit.action=='workflow.ability_completed',Audit.data['invocation'].as_string()==call.id).limit(2)))
        sdk=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==call.id,
            Audit.action=='capability.invoke').limit(2)))
        need(len(queued)==len(completed)==len(sdk)==1
            and all(value.actor_id==prior['actor'] for value in (queued[0],completed[0],sdk[0]))
            and queued[0].data=={'step':prior['step_id'],'job':job.id,'attempt':scope['attempt']}
            and completed[0].data=={'step':prior['step_id'],'invocation':call.id,'next':next_id}
            and sdk[0].data=={'status':'succeeded'})
        if job.status!='succeeded':
            # continue_flow schedules the successor in the producer's original
            # transaction, before the worker atomically marks that Job terminal.
            # No other reader/executor may treat an arbitrary running Job as done.
            from .leases import ownership
            owner=ownership(db)
            need(job.status=='running' and owner.get('job_id')==job.id
                and owner.get('lease_token') is not None and owner['lease_token']==job.lease_token
                and job.lease_until is not None and job.lease_until>db.scalar(select(func.clock_timestamp())))
        validate(cap.data['input_schema'],inputs);validate(cap.data['output_schema'],prior['output'])
        return copy.deepcopy(prior['output'])
    except HTTPException:
        raise
    except (KeyError,TypeError,ValueError,AttributeError,StopIteration):
        raise HTTPException(409,message) from None


def bind(task,step,db,p,request_key):
    spec=step['execution'];result=copy.deepcopy(spec.get('arguments',{}))
    for field,binding in spec.get('bindings',{}).items():
        source=binding['source'];objects=task.data['object_snapshots']
        if source=='object_records':
            result[field]=[{'entity_id':x['id'],'version':x['version'],'attributes':attributes(x)} for x in objects]
        elif source=='object_attributes':
            if len(objects)!=1:raise HTTPException(409,'此能力需单个对象，请拆分任务或使用多对象能力')
            result[field]=attributes(objects[0])
        elif source=='original_evidence_ids':
            result[field]=list(dict.fromkeys([*(m['evidence_id'] for m in task.data.get('material_snapshots',[])),*task.data.get('evidence_ids',[])]))
        elif source=='task_image_ids':result[field]=[x['id'] for x in task.data.get('image_snapshots',[])]
        elif source=='task_scene_refs':
            from .workflow_scenes import refs
            result[field]=refs(task)
        elif source=='task_comparison_refs':
            from .workflow_geo_pairs import refs
            result[field]=refs(task)
        elif source=='task_pixel_pair_refs':
            from .workflow_pixel_sources_v250 import refs
            result[field]=refs(task)
        elif source=='task_sound_ref':
            from .workflow_sound import ref
            result[field]=ref(task)
        elif source=='task_audio_ref':
            from .workflow_audio import ref
            result[field]=ref(task)
        elif source=='perception_frame_ids':
            value=task.data.get('perception_event') or {}
            if not value.get('frame_evidence_id'):raise HTTPException(409,'任务尚未关联可核对的视频画面，不能编造原图')
            result[field]=[value['frame_evidence_id']]
        elif source=='object_ids':result[field]=[x['id'] for x in objects]
        elif source=='task_identifier':result[field]=task.id
        elif source=='request_key':
            if not request_key:raise HTTPException(409,'未提供冻结的调用请求编号')
            result[field]=request_key
        elif source=='task_context':
            result[field]={'task_title':task.title,'object_snapshots':copy.deepcopy(objects),'material_snapshots':copy.deepcopy(task.data.get('material_snapshots',[])),
                'perception_event':copy.deepcopy(task.data.get('perception_event')),'source_clock':'not_inferred'}
        elif source=='observation_records':
            from .signal_observation_provider_v247 import records as received_records
            received=received_records(db,p,task)
            if received is not None:
                result[field]=received
                continue
            from .models import Observation
            if not task.data.get('observation_id'):raise HTTPException(409,'本任务尚未保存可用的观测记录，不能编造输入')
            row=scoped(db,Observation,task.data['observation_id'],p)
            if row.entity_id not in task.data['entity_ids']:raise HTTPException(409,'所引用的观测不属于任务对象')
            result[field]=[{'observation_id':row.id,'entity_id':row.entity_id,'version':row.version,'attributes':copy.deepcopy(row.attributes),
                'candidate_extraction':copy.deepcopy(row.data.get('extracted')),'source_clock_utc':'unknown','not_authoritative':True}]
        elif source=='previous_output_object':
            if set(binding)!={'source','step_id'} or type(binding['step_id']) is not str or not binding['step_id'].strip():
                raise HTTPException(422,'前序完整结果绑定格式不符')
            receipts=task.data['workflow_runtime']['receipts']
            if type(receipts) is not list or any(type(value) is not dict for value in receipts):
                raise HTTPException(409,'前序完整结果清单无法核对')
            selected=next(((i,r) for i,r in reversed(list(enumerate(receipts)))
                if r.get('step_id')==binding['step_id'] and 'output' in r),None)
            if selected is None or type(selected[1]['output']) is not dict:
                raise HTTPException(409,'前序能力尚未保存完整对象结果，不能编造输入')
            result[field]=_previous_output_object(db,p,task,selected[1],selected[0])
        else:
            receipts=task.data['workflow_runtime']['receipts']
            prior=next((r for r in reversed(receipts) if r['step_id']==binding['step_id'] and 'output' in r),None)
            if not prior or not isinstance(prior['output'],dict) or binding['field'] not in prior['output']:
                raise HTTPException(409,'前序能力尚未提供所需结果，不能编造输入')
            result[field]=copy.deepcopy(prior['output'][binding['field']])
    return result


def checked(db,p,task,release,business,step,request_key=None):
    from .signal_observation_provider_v247 import verify_task as verify_received_origin
    verify_received_origin(db,p,task,lock_sources=True)
    from .entity_release_compat import verify_task_objects
    verify_task_objects(db,p,task,release)
    if task.data.get('native_goal_context'):
        from .native_goals import guard
        guard(db,p,task,request_key,step=True)
    from .instance_compiler import verify_material_snapshots
    from .workspace_api import verify_perception_evidence
    instance=scoped(db,DomainInstance,task.data['instance_id'],p)
    if instance.status!='partially_active' or business['id'] not in instance.data.get('enabled_business_ids',[]):
        raise HTTPException(409,'实例或此业务已暂停，处理记录保留')
    cap=published(db,p,step['execution']['capability_id']);eligible(cap,ROLES[step['agent']])
    package=published(db,p,release.package_id,'domain_package')
    if cap.id not in package.data['dependencies'] or cap.content_hash!=step['execution']['capability_hash']:
        raise HTTPException(409,'已确认的能力版本已变化，请核对新实例版本；旧结果保留')
    policy=release.data['snapshot']['automation']['policies'][business['name']]
    if pin(db,p,step,policy)!=step['execution']:raise HTTPException(409,'流程执行设置与冻结版本不一致')
    verify_material_snapshots(db,p,task.data.get('material_snapshots',[]),allow_archived=True)
    verify_perception_evidence(db,p,task)
    from .workflow_audio import verify_receipts as verify_audio_receipts
    verify_audio_receipts(db,p,task)
    from .workflow_sound import verify_receipts as verify_sound_receipts
    verify_sound_receipts(db,p,task)
    from .workflow_images import verify,verify_masks,local_inputs
    verify(db,p,task);verify_masks(db,p,task)
    inputs=bind(task,step,db,p,request_key)
    from .workflow_audio import local_inputs as audio_inputs
    from .workflow_segmented_audio import local_inputs as segment_inputs
    segment_inputs(db,p,task,step,inputs)
    audio_inputs(db,p,task,step,inputs)
    from .workflow_sound import local_inputs as sound_inputs
    sound_inputs(db,p,task,step,inputs)
    validate(cap.data['input_schema'],inputs)
    local_inputs(db,p,task,step,inputs)
    from .workflow_scenes import local_inputs as scene_inputs,verify_masks as scene_masks
    scene_inputs(db,p,task,step,inputs);scene_masks(db,p,task)
    from .workflow_geo_pairs import local_inputs as pair_inputs,verify_receipts
    pair_inputs(db,p,task,step,inputs);verify_receipts(db,p,task)
    from .workflow_learned_geo_pairs import local_inputs as learned_inputs
    learned_inputs(db,p,task,step,inputs)
    from .workflow_pixel_sources_v250 import local_inputs as pixel_inputs
    pixel_inputs(db,p,task,step,inputs)
    return cap,inputs


def schedule(db,p,task,release,business,*,confirmed=False,retry=False,request_id=None):
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    runtime=copy.deepcopy(task.data['workflow_runtime'])
    step=next((x for x in business['flow']['steps'] if x['id']==runtime['current_step_id']),None)
    if not step or step['agent']=='人工确认':return False
    if step['execution']['mode']=='执行前确认' and not confirmed:
        task.status='workflow_waiting';return False
    if task.status in {'workflow_completed','workflow_cancelled'}:raise HTTPException(409,'任务已经结束')
    previous=runtime.get('automatic',{});visit=len(runtime['receipts'])
    if previous.get('visit')==visit and previous.get('step_id')==step['id']:
        if task.status in {'workflow_queued','workflow_running'}:return True
        if not retry:raise HTTPException(409,'原执行未完成，请核查后明确重试')
        attempt=previous['attempt']+1
    else:attempt=1
    key='workflow:'+task.id+':'+str(visit)+':'+str(attempt)
    cap,inputs=checked(db,p,task,release,business,step,key)
    maximum=min(5,int(cap.data['budget'].get('max_attempts',2)))
    if attempt>maximum:raise HTTPException(409,'已达到该能力允许的重试次数，原结果保留')
    if visit>=runtime['max_steps']:raise HTTPException(409,'流程执行已达到次数上限，请核对流程')
    scope={'step_id':step['id'],'visit':visit,'attempt':attempt,'inputs':inputs,'input_hash':digest(inputs),'request_id':request_id}
    job=Job(workspace_id=p.workspace_id,kind='execute_workflow_step',idempotency_key=key,
        data={'task_id':task.id,'actor_id':p.user_id,'scope':scope,'invocation_key':key})
    db.add(job);db.flush()
    from .workflow_job_dependencies_v264 import bind_compact
    bind_compact(db,p,task,job)
    runtime['automatic']={**scope,'actor_id':p.user_id,'job_id':job.id,'phase':'queued','error':None,'invocation_id':None}
    task.status='workflow_queued';task.version+=1;task.data={**task.data,'workflow_runtime':runtime}
    audit(db,p,'workflow.ability_queued',task.id,{'step':step['id'],'job':job.id,'attempt':attempt})
    return True


class WorkflowBlocked(Exception):
    def __init__(self,reason):
        self.reason=reason[:2000]
        super().__init__(self.reason)


def process(db,p,job):
    try:_process(db,p,job)
    except HTTPException as error:raise WorkflowBlocked(str(error.detail)) from None


def _process(db,p,job):
    from .workspace_api import execution
    from .gateway import invoke
    task,release,business=execution(db,p,job.data['task_id'],True)
    runtime=copy.deepcopy(task.data['workflow_runtime']);auto=runtime.get('automatic',{})
    if task.status not in {'workflow_queued','workflow_running'} or auto.get('job_id')!=job.id:return
    from .workflow_job_dependencies_v264 import require_ready
    require_ready(db,p,task,job)
    step=next(x for x in business['flow']['steps'] if x['id']==auto['step_id'])
    cap,inputs=checked(db,p,task,release,business,step,job.data['invocation_key'])
    if digest(inputs)!=auto['input_hash'] or len(runtime['receipts'])!=auto['visit']:
        raise HTTPException(409,'执行输入或流程位置已变化，不能继续原任务')
    task.status='workflow_running';task.version+=1
    task.data={**task.data,'workflow_runtime':{**runtime,'automatic':{**auto,'phase':'running'}}};db.commit()
    call=invoke(db,p,cap.id,step['execution']['agent_role'],inputs,job.data['invocation_key'])
    db.expire_all();task,release,business=execution(db,p,task.id,True)
    runtime=copy.deepcopy(task.data['workflow_runtime']);auto=runtime.get('automatic',{})
    if task.status!='workflow_running' or auto.get('job_id')!=job.id:return
    checked(db,p,task,release,business,step,job.data['invocation_key'])
    if call.status!='succeeded':
        task.status='workflow_failed';task.version+=1
        task.data={**task.data,'workflow_runtime':{**runtime,'automatic':{**auto,'phase':'failed','invocation_id':call.id,'error':call.data.get('error') or '能力未完成，记录保留'}}}
        audit(db,p,'workflow.ability_failed',task.id,{'invocation':call.id});return
    output=copy.deepcopy(call.data['output'])
    from .workflow_images import local_result
    local_result(db,p,step,inputs,output)
    from .workflow_scenes import local_result as scene_result
    scene_result(db,p,task,step,inputs,output)
    from .workflow_audio import local_result as audio_result
    audio_result(db,p,task,step,inputs,output)
    from .workflow_sound import local_result as sound_result
    sound_result(db,p,task,step,inputs,output)
    from .workflow_segmented_audio import local_result as segment_result
    segment_result(db,p,task,step,inputs,output)
    from .workflow_geo_pairs import local_result as pair_result
    pair_result(db,p,task,step,inputs,output)
    from .workflow_learned_geo_pairs import local_result as learned_result
    learned_result(db,p,task,step,inputs,output)
    from .workflow_pixel_runs_v250 import local_result as pixel_result
    pixel_result(db,p,task,step,inputs,output)
    branch=output.get(step['execution'].get('branch_field')) if step.get('condition') and isinstance(output,dict) else None
    if step.get('condition') and not isinstance(branch,bool):raise HTTPException(409,'能力未提供可核查的分支结果')
    steps=business['flow']['steps'];index=next(i for i,x in enumerate(steps) if x['id']==step['id'])
    next_id=step.get('elseId','end') if branch is False else step.get('nextId','auto')
    if next_id=='auto':next_id=steps[index+1]['id'] if index+1<len(steps) else 'end'
    if next_id=='end' and task.data.get('perception_event'):
        raise HTTPException(409,'设备线索需要最终业务异常核查结论，请使用含明确结果核查步骤的流程')
    receipt={'request_id':job.id,'step_id':step['id'],'step_name':step['name'],'output_name':step['output'],
        'note':'已通过注册能力完成处理；请核对结果及来源。','actor':p.user_id,'role':step['agent'],'at':now().isoformat(),
        'execution_mode':'registered_ability','invocation_id':call.id,'capability_id':cap.id,'capability_name':step['execution']['capability_name'],
        'capability_hash':cap.content_hash,'release_version':cap.release_version,'input_hash':auto['input_hash'],'input_snapshot':inputs,
        'output':output,'branch':branch,'object_snapshots':task.data['object_snapshots'],'material_snapshots':task.data.get('material_snapshots',[]),
        'image_snapshots':task.data.get('image_snapshots',[]),'scene_snapshots':task.data.get('scene_snapshots',[]),**({'comparison_scene_snapshots':task.data['comparison_scene_snapshots']} if task.data.get('comparison_scene_snapshots') else {}),**({'audio_snapshots':task.data['audio_snapshots']} if task.data.get('audio_snapshots') else {}),'candidate_only':step['execution']['adapter'] in {'local_specialist','local_geospatial','local_geo_pair','local_learned_geo_pair','local_pixel_change','local_audio','local_task_segmented_audio'},
        'source_clock':'not_inferred','entity_state_updated':False}
    runtime['receipts'].append(receipt);runtime['current_step_id']=None if next_id=='end' else next_id
    runtime['automatic']={**auto,'phase':'succeeded','invocation_id':call.id,'error':None}
    task.status='workflow_completed' if next_id=='end' else 'workflow_waiting';task.version+=1
    if next_id=='end':
        memory=Memory(workspace_id=p.workspace_id,status='candidate',text='业务流程“'+business['name']+'”已按注册能力保存结果',
            data={'type':'workflow_result','task_id':task.id,'instance_release_id':release.id,'receipts':runtime['receipts'],
                'object_snapshots':task.data['object_snapshots'],'material_snapshots':task.data.get('material_snapshots',[]),'image_snapshots':task.data.get('image_snapshots',[]),'scene_snapshots':task.data.get('scene_snapshots',[]),
                **({'comparison_scene_snapshots':task.data['comparison_scene_snapshots']} if task.data.get('comparison_scene_snapshots') else {}),**({'audio_snapshots':task.data['audio_snapshots']} if task.data.get('audio_snapshots') else {}),'source_clock':'not_inferred','entity_state_updated':False,'auto_publish':False})
        db.add(memory);db.flush();runtime['data_receipt_id']=memory.id
    task.data={**task.data,'workflow_runtime':runtime}
    audit(db,p,'workflow.ability_completed',task.id,{'step':step['id'],'invocation':call.id,'next':next_id})
    if task.data.get('native_goal_context'):
        from .native_goals import queue
        queue(db,p,task,'flow:'+str(len(runtime['receipts'])))
    if next_id!='end':continue_flow(db,p,task,release,business)


def continue_flow(db,p,task,release,business):
    """Keep prior human/automatic receipts even when a successor cannot start."""
    step=next((x for x in business['flow']['steps'] if x['id']==task.data['workflow_runtime']['current_step_id']),None)
    if not step or step['agent']=='人工确认':return
    try:schedule(db,p,task,release,business)
    except HTTPException as error:
        rt=copy.deepcopy(task.data['workflow_runtime']);task.status='workflow_blocked';task.version+=1
        rt['automatic']={'step_id':step['id'],'visit':len(rt['receipts']),'attempt':0,'phase':'blocked','error':str(error.detail),'job_id':None,'invocation_id':None}
        task.data={**task.data,'workflow_runtime':rt}


def fail(db,p,job,reason):
    from .models import Task
    task=scoped(db,Task,job.data['task_id'],p,True);rt=copy.deepcopy(task.data['workflow_runtime'])
    if task.status in {'workflow_queued','workflow_running'} and rt.get('automatic',{}).get('job_id')==job.id:
        task.status='workflow_blocked';task.version+=1
        task.data={**task.data,'workflow_runtime':{**rt,'automatic':{**rt['automatic'],'phase':'blocked','error':reason}}}
