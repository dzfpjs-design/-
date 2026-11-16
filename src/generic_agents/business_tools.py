"""Registry-backed planning, server evidence bindings and approved tool calls."""
import copy
from fastapi import HTTPException
from .contracts import digest
from .models import Plan, Task, User, Membership
from .services import published, scoped, validate
from sqlalchemy import select

BINDINGS={'archive_records','observation_records','task_evidence','entity_ids','task_id','task_identifier','request_key'}


def validate_bindings(manifest):
    from .same_goal_continuation_grant_contracts_v313 import applies as continuation_grant_tool, validate_manifest as validate_continuation_grant_tool
    if continuation_grant_tool(manifest):
        try: validate_continuation_grant_tool(manifest)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise HTTPException(422, '本次办理工具的登记内容不符合固定只读范围，未授权或调用') from error
        return
    from .workflow_pixel_receipt_status_v263 import HANDLER as PIXEL_STATUS, validate_manifest as validate_pixel_status
    if (manifest.get('execution',{}).get('handler')==PIXEL_STATUS or manifest.get('namespace')==PIXEL_STATUS
            or manifest.get('name')==PIXEL_STATUS or manifest.get('specification',{}).get('schema')=='workflow-pixel-receipt-status-v263'):
        validate_pixel_status(manifest);return
    if manifest['execution'].get('query_tool'):
        from .data_tools import validate_manifest
        validate_manifest(manifest);return
    bindings=manifest['execution'].get('input_bindings',{})
    properties=manifest['input_schema'].get('properties',{})
    if not isinstance(bindings,dict) or any(k not in properties or v not in BINDINGS for k,v in bindings.items()):
        raise HTTPException(422,'工具证据绑定必须使用登记属性和服务器支持的来源')
    if bindings and manifest['input_schema'].get('type')!='object':
        raise HTTPException(422,'证据绑定需要对象输入Schema')


def choices(db,p,task):
    if task.data.get('workflow_runtime'):
        from .native_planning_grants import current
        return current(db,p,task)[3]
    package=published(db,p,task.package_id,'domain_package')
    result=[]
    for identifier in package.data['specification'].get('business_tool_ids',[]):
        cap=published(db,p,identifier)
        if cap.kind not in {'tool','specialist_model'} or cap.id not in package.data['dependencies']:
            raise HTTPException(409,'业务工具必须锁定已发布的工具或专业模型依赖')
        if cap.data['execution'].get('adapter') not in {'builtin','http','mcp'}:
            raise HTTPException(409,'业务能力适配器尚未支持执行')
        if not cap.data['allowed_agent_roles']:raise HTTPException(409,'业务工具未授权智能体角色')
        validate_bindings(cap.data);result.append(cap)
    if len(result)>12:raise HTTPException(409,'单业务包最多12个规划工具，请按目标配置能力包')
    return result


def arguments_schema(cap):
    schema=copy.deepcopy(cap.data['input_schema'])
    bindings=cap.data['execution'].get('input_bindings',{})
    if not bindings:return schema
    for field in bindings:schema.get('properties',{}).pop(field,None)
    schema['required']=[f for f in schema.get('required',[]) if f not in bindings]
    # Full input constraints are validated again after server binding.
    for key in ['allOf','anyOf','oneOf','minProperties','maxProperties','dependentRequired','dependentSchemas']:
        schema.pop(key,None)
    return schema


def planning_arguments_schema(cap,plan_schema):
    """Keep local JSON pointers scoped to the tool when embedding into a plan Schema."""
    schema=arguments_schema(cap)
    def has_refs(node):
        if isinstance(node,dict):return any(k in {'$ref','$dynamicRef','$recursiveRef'} or has_refs(v) for k,v in node.items())
        if isinstance(node,list):return any(has_refs(v) for v in node)
        return False
    if not has_refs(schema):return schema
    name='tool_'+cap.id.replace('-','_');prefix='#/$defs/'+name
    def relocate(node):
        if isinstance(node,dict):
            result={}
            for key,value in node.items():
                if key=='$id':continue
                if key in {'$ref','$dynamicRef','$recursiveRef'}:
                    result[key]=prefix+value[1:] if value=='#' or value.startswith('#/') else '#'+name+'_'+value[1:]
                elif key in {'$anchor','$dynamicAnchor'}:result[key]=name+'_'+value
                else:result[key]=relocate(value)
            return result
        if isinstance(node,list):return [relocate(v) for v in node]
        return node
    plan_schema.setdefault('$defs',{})[name]=relocate(schema)
    return {'$ref':prefix}


def bind_inputs(cap,arguments,task,evidence,key):
    bindings=cap.data['execution'].get('input_bindings',{})
    if not isinstance(arguments,dict) or any(k in arguments for k in bindings):
        raise HTTPException(422,'模型参数不能替代服务器证据绑定')
    validate(arguments_schema(cap),arguments)
    sources={'archive_records':evidence['evidence'],'observation_records':evidence.get('observation_evidence',[]),
        'task_evidence':evidence,'entity_ids':evidence.get('selected_entity_ids',task.data.get('entity_ids',[])),
        'task_id':task.id,'task_identifier':task.id,'request_key':key}
    inputs={**arguments,**{field:copy.deepcopy(sources[source]) for field,source in bindings.items()}}
    validate(cap.data['input_schema'],inputs)
    if cap.data['execution'].get('adapter') in {'http','mcp'} and not cap.data['data_policy'].get('external_allowed'):
        raise HTTPException(409,'工具没有明确的数据外发策略批准')
    return inputs


def prepare_step(db,p,task,step,evidence,index,*,request_scope_key=None):
    cap=next((c for c in choices(db,p,task) if c.id==step.get('capability_id')),None)
    if not cap or step['agent'] not in cap.data['allowed_agent_roles']:
        raise HTTPException(403,'计划选择了业务包之外或角色无权使用的能力')
    key=(request_scope_key or 'task:'+task.id+':plan-version:'+str(task.version))+':step:'+str(index)
    inputs=bind_inputs(cap,step['arguments'],task,evidence,key)
    return {**step,'title':'调用 '+cap.data['description'],'capability_hash':cap.content_hash,
        'release_version':cap.release_version,'input_snapshot':inputs,'input_hash':digest(inputs),
        'request_key':key,'side_effect':bool(cap.data['execution'].get('side_effect')),
        'scope_hash':scope_hash(task),'evidence_hash':evidence_hash(evidence)}


def object_definition_pin(task):
    """Keep new private source proof in internal pins, without changing legacy hashes."""
    if not any(key in task.data for key in ('object_definition_bindings','object_definition_bindings_hash')):
        return {}
    from .entity_release_compat import verify_task_binding_snapshot
    verify_task_binding_snapshot(task)
    return {'object_definition_bindings_hash':task.data['object_definition_bindings_hash']}


def scope_hash(task):
    scope={'package_id':task.package_id,'entity_ids':task.data.get('entity_ids',[]),
        'fields':task.data.get('fields',[]),'observation_mode':task.data.get('observation_mode','auto')}
    if task.data.get('goal_hash'):scope['goal_hash']=task.data['goal_hash']
    scope.update(object_definition_pin(task))
    return digest(scope)


def relation_bindings(evidence):
    return {k:evidence['relations'][k] for k in ['relation_evidence','relation_gaps',
        'relation_conflicts','relation_excluded','entity_ids','spec','truncated']}


def evidence_hash(evidence):
    value={'evidence':evidence['evidence'],'observations':evidence.get('observation_evidence',[])}
    if evidence.get('relations'):
        value['relations']=relation_bindings(evidence)
    if evidence.get('data_query'):
        value['data_query']=evidence['data_query'];value['derived_records']=evidence.get('derived_records',[])
    if evidence.get('sensor_agent_context'):
        value['sensor_agent_context']=evidence['sensor_agent_context']
    return digest(value)


def validate_step(db,p,task,step):
    cap=published(db,p,step['capability_id'])
    if cap not in choices(db,p,task) or cap.content_hash!=step['capability_hash'] or digest(cap.data)!=step['capability_hash']:
        raise HTTPException(409,'工具版本或业务包依赖已变化，请重新规划')
    if step['agent'] not in cap.data['allowed_agent_roles'] or digest(step['input_snapshot'])!=step['input_hash']:
        raise HTTPException(409,'工具角色或输入快照不符合批准内容')
    if scope_hash(task)!=step['scope_hash']:
        raise HTTPException(409,'目标范围已变化，请重新规划')
    from .goals import query_goal
    latest=query_goal(db,p,task,learning=False)
    if evidence_hash(latest)!=step['evidence_hash']:
        raise HTTPException(409,'工具分析所依据的证据已变化，请重新规划')
    validate(cap.data['input_schema'],step['input_snapshot']);return cap


def validate_call_approval(db,p,cap,role,inputs,key,approval):
    if type(approval) is dict and approval.get('schema') == 'same-goal-continuation-read-approval-v314':
        from .same_goal_continuation_execution_runtime_v314 import authorize_gateway
        authorize_gateway(db,p,cap,role,inputs,key,approval);return
    if approval.get('schema') == 'goal-evaluation-append-approval-v297':
        from .workflow_goal_evaluation_append_v299 import validate_call_approval as validate_append
        return validate_append(db,p,cap,role,inputs,key,approval)
    if approval.get('schema') == 'workflow-registered-tool-child-approval-v201':
        from .workflow_registered_tool_plan_v201 import validate_child_approval
        return validate_child_approval(db,p,cap,role,inputs,key,approval)
    if approval.get('schema') == 'workflow-task-evidence-packet-approval-v199':
        from .workflow_task_packet_v199 import validate_call_approval as validate_packet
        return validate_packet(db,p,cap,role,inputs,key,approval)
    if approval.get('schema') == 'workflow-document-packet-approval-v207':
        from .workflow_document_packet_v207 import validate_call_approval as validate_document_packet
        return validate_document_packet(db,p,cap,role,inputs,key,approval)
    if approval.get('schema') == 'workflow-task-archive-approval-v198':
        from .workflow_task_archive_v198 import validate_call_approval as validate_archive
        return validate_archive(db,p,cap,role,inputs,key,approval)
    if approval.get('schema') == 'workflow-service-approval-v195':
        from .workflow_service_catalog_v197 import family
        return family(cap).validate_call_approval(db,p,cap,role,inputs,key,approval)
    if approval.get('native_step_execution') is True:
        from .native_step_execution import validate_call_approval as validate_native
        return validate_native(db,p,cap,role,inputs,key,approval)
    plan=scoped(db,Plan,approval['plan_id'],p)
    task=scoped(db,Task,plan.task_id,p)
    if plan.status not in {'approved','running'} or plan.approved_hash!=digest(plan.data) or task.status=='cancelled':
        raise HTTPException(409,'工具调用计划批准已失效')
    step=plan.data['result']['plan']['steps'][approval['step_index']]
    validate_step(db,p,task,step)
    attempt=task.data.get('tool_attempts',{}).get(plan.id+':'+str(approval['step_index']),0)
    expected_key=step['request_key']+(':retry:'+str(attempt) if attempt else '')
    if step['action']!='invoke_capability' or step['capability_id']!=cap.id or role!=step['agent'] or digest(inputs)!=step['input_hash'] or key!=expected_key:
        raise HTTPException(409,'工具调用不匹配批准步骤')
    user=db.get(User,plan.approved_by)
    member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==plan.approved_by))
    needed={'admin'} if cap.data['execution'].get('approval_policy')=='admin' else {'admin','operator'}
    if not user or not user.active or not member or member.role not in needed:
        raise HTTPException(403,'工具计划批准人的权限已撤回')
    if task.data.get('goal_spec'):
        from .goals import check_plan_goal
        from .models import Invocation
        from sqlalchemy import func
        check_plan_goal(task,plan,allow_legacy=True)
        plans=select(Plan.id).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id)
        from .goal_queries import call_count
        count=call_count(db,p,task)
        if count>=task.data['goal_spec']['max_tool_calls']:
            raise HTTPException(409,'目标工具调用预算已耗尽，请核查已有结果后修改目标预算')
