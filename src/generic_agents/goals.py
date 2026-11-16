"""Confirmed, versioned business intent and deterministic completion checks."""
import copy
import json
import math
from sqlalchemy import select, func
from fastapi import HTTPException
from .contracts import GoalSpec, digest
from pydantic import ValidationError
from .models import Task, Plan, Job, Invocation, now
from .services import scoped, published, query_evidence, audit
from .temporal import resolve, instant, question_time
from .gateway import cloud_json, ModelBlocked


def validate_goal(db, p, task, value, *, reference=None):
    spec = GoalSpec.model_validate(value).model_dump()
    if set(spec['entity_ids']) != set(task.data['entity_ids']):
        raise HTTPException(422,'目标对象必须与已保存任务范围一致；扩范围请创建新任务')
    package = published(db,p,task.package_id,'domain_package')
    from .data_tools import managed_task_kwargs
    managed=bool(task.data.get('workflow_runtime')) and bool(managed_task_kwargs(task))
    if managed:
        from .entity_release_compat import verify_task_objects
        verify_task_objects(db,p,task)
    allowed_fields = set()
    from .models import Entity
    for identifier in spec['entity_ids']:
        entity = scoped(db,Entity,identifier,p)
        if not managed and entity.schema_capability_id != package.id: raise HTTPException(422,'对象不属于目标业务包')
        allowed_fields.update(package.data['specification']['entity_schemas'][entity.entity_type].get('properties',{}))
    if set(spec['required_fields']) - allowed_fields: raise HTTPException(422,'目标字段不在业务包Schema内')
    if spec.get('relation_query'):
        from .relations import validate_query
        if spec['relation_query']['package_id']!=package.id: raise HTTPException(422,'目标关系查询不能引用包外版本')
        spec['relation_query']=validate_query(db,p,spec['relation_query'],spec['entity_ids'])
    if spec.get('data_query'):
        from .data_tools import validate_steps
        from .goal_queries import request_for
        # Validate against the proposed contract without changing the saved task.
        from types import SimpleNamespace
        temporary=SimpleNamespace(data={**task.data,'goal_spec':spec,'goal_window':resolve(spec['evidence']['time'],reference)})
        prepared=request_for(db,p,temporary,**({'authorization_task':task} if managed else {}))
        if prepared['clarifications']:raise HTTPException(422,'；'.join(prepared['clarifications']))
        spec['data_query']['tool_steps']=validate_steps(db,p,prepared,spec['data_query']['tool_steps'],**managed_task_kwargs(task))
    if task.data.get('workflow_runtime') and (spec.get('data_query') or spec.get('relation_query')):
        from .native_goal_queries import validate_spec
        validate_spec(db,p,task,spec)
    allowed_caps = set(package.data['specification'].get('business_tool_ids',[])) & set(package.data['dependencies'])
    if task.data.get('workflow_runtime'):
        from .native_goal_tools import validate_criteria
        allowed_caps=set(validate_criteria(db,p,task,spec))
    for criterion in spec['criteria']:
        kind = criterion['kind']
        if kind in {'capability_success','tool_value'}:
            if criterion['capability_id'] not in allowed_caps: raise HTTPException(422,'完成标准引用了包外工具')
            cap = published(db,p,criterion['capability_id'])
            if kind == 'tool_value' and criterion['field'] not in cap.data['output_schema'].get('properties',{}):
                raise HTTPException(422,'工具值标准必须引用登记输出Schema的属性')
        if kind == 'field_value' and criterion['field'] not in spec['required_fields']:
            raise HTTPException(422,'字段标准必须引用目标所需字段')
        if kind=='query_value':
            if not spec.get('data_query'):raise HTTPException(422,'派生值标准需要目标委托查询方案')
            if task.data.get('workflow_runtime'):
                from .native_query_catalog import output_schema_for_steps
                fields=output_schema_for_steps(db,p,task,spec)
            else:
                final=published(db,p,spec['data_query']['tool_steps'][-1]['capability_id'])
                fields=final.data['output_schema']['properties']['records']['items']['properties']['values'].get('properties',{})
            if criterion['field'] not in fields:raise HTTPException(422,'派生值标准必须引用最后一步登记的输出字段')
            if task.data.get('workflow_runtime'):
                prop=fields[criterion['field']];field_type=prop.get('type');expected=criterion['expected']
                if isinstance(field_type,list):field_type=next((x for x in field_type if x!='null'),None)
                if (field_type=='boolean' and not isinstance(expected,bool)
                    or field_type=='string' and not isinstance(expected,str)
                    or field_type in {'number','integer'} and (isinstance(expected,bool) or not isinstance(expected,(int,float)))
                    or field_type=='integer' and isinstance(expected,(int,float)) and not float(expected).is_integer()
                    or field_type in {'string','boolean'} and criterion['operator']!='eq'
                    or field_type not in {'number','integer','string','boolean'}):
                    raise HTTPException(422,'派生结果完成标准的类型或比较方式不符合实际字段声明')
        if kind in {'field_value','tool_value','query_value'}:
            if not criterion['operator'] or criterion['expected'] is None:
                raise HTTPException(422,'值比较标准需要操作符及期望值')
            if isinstance(criterion['expected'],float) and not math.isfinite(criterion['expected']):
                raise HTTPException(422,'期望值必须是有限数值')
    if not spec['stop_on_conflict']:
        raise HTTPException(422,'当前执行器要求遇到证据冲突停止；自动消解冲突尚未接入')
    if spec['deadline']: instant(spec['deadline'])
    window = resolve(spec['evidence']['time'], reference)
    return spec, window


def initialize(db,p,task,value,reason):
    spec,window = validate_goal(db,p,task,value)
    revision = task.data.get('goal_version',0)+1
    previous = task.data.get('goal_spec')
    history = list(task.data.get('goal_history',[]))
    if previous and task.data.get('goal_version'): history.append({'version':task.data['goal_version'],'spec':previous,
        'hash':task.data['goal_hash'],'confirmed_by':task.data['goal_confirmed_by'],
        'confirmed_at':task.data['goal_confirmed_at'],'time':task.data['goal_window'],'reason':reason})
    task.data = {**task.data,'goal_spec':spec,'goal_version':revision,'goal_hash':digest(spec),
        'goal_window':window,'goal_confirmed_by':p.user_id,'goal_confirmed_at':now().isoformat(),
        'goal_history':history,'fields':spec['required_fields'],'observation_mode':spec['observation_mode'],
        'goal_clarifications':[],'goal_error':None,'goal_candidate':None,'goal_draft':None,
        'data_query_history':[*task.data.get('data_query_history',[]),*([{'query_id':task.data['data_query_run_id'],'goal_version':revision-1}] if task.data.get('data_query_run_id') else [])],
        'data_query_run_id':None}
    audit(db,p,'goal.confirm',task.id,{'version':revision,'hash':digest(spec),'reason':reason})


def confirm(db,p,task,expected,reason,value):
    from .services import check_version
    from .security import require
    require(p,'admin','operator');check_version(task,expected)
    if task.status in {'queued','running','completed','cancelled','awaiting_acceptance','interpreting_goal','planning','waiting_data'}:
        raise HTTPException(409,'执行中或已结束目标不能改写；先停止活动任务或创建新目标')
    if any(r.get('step',{}).get('side_effect') and r.get('result',{}).get('status') in {'running','unknown'}
        for r in task.data.get('step_receipts',{}).values()):
        raise HTTPException(409,'未知外部动作尚未核对，不能修改目标绕过原动作')
    plans = select(Plan.id).where(Plan.task_id==task.id,Plan.workspace_id==p.workspace_id)
    live = db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.status=='running',
        Job.lease_until > func.clock_timestamp(),Job.data['plan_id'].as_string().in_(plans)).limit(1))
    if live: raise HTTPException(409,'旧执行仍有有效租约，请等待结束后修改目标')
    current = task.data.get('current_plan_id')
    if current:
        plan = scoped(db,Plan,current,p,True)
        if plan.status in {'review','blocked_model','failed','executed','waiting_tool','waiting_data','blocked_evidence'}:
            plan.status='superseded';plan.version+=1
    initialize(db,p,task,value,reason)
    task.data={**task.data,'current_plan_id':None};task.status='draft';task.version+=1
    return task


def interpret(db,p,task_id,request):
    task=scoped(db,Task,task_id,p)
    if task.status=='cancelled':return
    if task.data.get('goal_interpretation_request',{}).get('request_id')!=request['request_id']:return
    package=published(db,p,task.package_id,'domain_package')
    from .models import Entity
    entities=[scoped(db,Entity,x,p) for x in task.data['entity_ids']]
    catalog={key:schema.get('title',key) for entity in entities for key,schema in
        package.data['specification']['entity_schemas'][entity.entity_type].get('properties',{}).items()}
    goal_schema=GoalSpec.model_json_schema();definitions=goal_schema.pop('$defs',{})
    goal_schema['properties']['entity_ids']['items']={'enum':task.data['entity_ids']}
    goal_schema['properties']['required_fields']['items']={'enum':sorted(catalog)}
    schema={'type':'object','additionalProperties':False,'required':['goal','clarifications'],
        '$defs':definitions,'properties':{'goal':{'anyOf':[goal_schema,{'type':'null'}]},
            'clarifications':{'type':'array','maxItems':8,'items':{'type':'string','maxLength':500}}}}
    trusted_timezone=request.get('business_timezone') or package.data['specification'].get('timezone')
    try:
        model=published(db,p,request['model_profile_id'],'foundation_model')
        from .leases import checkpoint
        checkpoint(db)
        value,receipt=cloud_json(model,[{'role':'user','content':json.dumps({
            'purpose':task.title,'entity_ids':task.data['entity_ids'],'field_catalog':catalog,
            'requested_fields':task.data.get('fields',[]),'business_timezone':trusted_timezone,
            'server_reference_at':request['requested_at'],'observation_constraint':task.data.get('observation_mode','auto'),
            'tool_ids':package.data['specification'].get('business_tool_ids',[]),
            'instruction':'形成待人核对的目标契约。不可扩大对象、权限或猜定时区。缺少关键业务条件、日期或完成标准时返回简短clarifications。'
                '昨天/今天/本周/上周必须使用time.mode=relative和相应relative枚举；服务器按business_timezone解析，模型不得计算日期。'
                '绝对时间必须带偏移量；日期范围可用YYYY-MM-DD（结束日含当天），禁止无偏移量日期时间。'
                '实时状态不能使用无时间要求的档案来代替；历史档案说明与观测核查分开。'
                '完成标准使用可核验字段、登记工具结果和人的确认。未实现的行业处置不得用报告已生成代替完成。'
                '不得添加未登记工具、任意SQL或脚本；字段和数值只是候选，由服务器核验并等待人确认。'},ensure_ascii=False)}],schema,p)
        candidate=value['goal'];questions=list(value['clarifications'])
        if candidate:
            candidate=GoalSpec.model_validate(candidate).model_dump()
            t=candidate['evidence']['time']
            if t['timezone']!=trusted_timezone and t['mode']!='archive':
                t['timezone']=trusted_timezone
                questions.append('请核对或指定业务时区；模型不能猜定日期上下文')
            try:
                _,window=validate_goal(db,p,task,candidate,reference=instant(request['requested_at']))
                hint=question_time(task.title)
                if hint:
                    expected=resolve({**hint,'timezone':trusted_timezone},instant(request['requested_at']))
                    if (window['start'],window['end']) != (expected['start'],expected['end']):
                        questions.append('候选时间窗与目标中的业务日期不一致，请在确认前校正日期')
                if any(word in task.title for word in ['当前','现在','最新','实时']) and t['mode']=='archive':
                    questions.append('实时目标需明确观测时效，不能以无时间要求档案代替')
                if t['mode']=='relative':
                    candidate['evidence']['time']={'mode':'range','start':window['start'],'end':window['end'],'timezone':window['timezone']}
            except HTTPException as error:
                questions.append(str(error.detail))
        if not candidate and not questions:questions=['目的或完成标准尚不明确，请补充后再次解析']
        error=None
    except (ModelBlocked,HTTPException,ValidationError) as failure:
        candidate=None;questions=[('模型候选不符合目标契约，请补充条件或手动编辑' if isinstance(failure,ValidationError)
            else str(failure.detail) if isinstance(failure,HTTPException) else str(failure))]
        receipt=None;error='目标解析未形成可确认契约'
    task=scoped(db,Task,task_id,p,True)
    if task.status=='cancelled' or task.data.get('goal_interpretation_request',{}).get('request_id')!=request['request_id']:
        return
    history=list(task.data.get('goal_proposals',[]))
    history.append({'request_id':request['request_id'],'candidate':candidate,'clarifications':questions,
        'model_receipt':receipt,'saved_at':now().isoformat()})
    task.data={**task.data,'goal_candidate':candidate,'goal_clarifications':questions,'goal_error':error,
        'goal_proposals':history,'goal_draft':candidate or task.data.get('goal_draft')}
    task.status='needs_clarification' if questions else 'goal_review';task.version+=1
    audit(db,p,'goal.interpreted',task.id,{'status':task.status,'request_id':request['request_id']})


def query_goal(db,p,task,*,lock_sources=True,**kwargs):
    def add_sensor_context(result):
        from .sensor_agent_context import read_authorized
        context=read_authorized(db,p,task)
        if context:
            result={**result,'sensor_agent_context':context}
        return result
    from .goal_evaluation_provider_v288 import query_evidence as saved_goal_evidence
    saved = saved_goal_evidence(db, p, task, lock_sources=lock_sources)
    if saved is not None:return add_sensor_context(saved)
    spec=task.data.get('goal_spec')
    from .data_tools import managed_include_trusted_cases
    trusted=(managed_include_trusted_cases(db,p,task) if lock_sources else
        managed_include_trusted_cases(db,p,task,lock_sources=False))
    if not trusted:kwargs['include_trusted_cases']=False
    result=query_evidence(db,p,task.data['entity_ids'],task.data.get('fields',[]),
        requirement=spec['evidence'] if spec else None,fixed_window=task.data.get('goal_window'),
        relation_query=spec.get('relation_query') if spec else None,**kwargs)
    if spec and spec.get('data_query'):
        if task.data.get('workflow_runtime'):
            from .native_goal_queries import read
            result=read(db,p,task,result,lock_sources=lock_sources)
        else:
            from .goal_queries import read
            result=read(db,p,task,result)
    return add_sensor_context(result)


def snapshot(task):
    if not task.data.get('goal_confirmed_by') or not task.data.get('goal_spec'):
        raise HTTPException(409,'请先保存并确认目标、证据要求与完成标准')
    if digest(task.data['goal_spec']) != task.data['goal_hash']:
        raise HTTPException(409,'目标契约哈希不一致，请重新核对目标')
    return {'version':task.data['goal_version'],'hash':task.data['goal_hash'],
        'spec':copy.deepcopy(task.data['goal_spec']),'time':copy.deepcopy(task.data['goal_window'])}


def check_plan_goal(task,plan,*,allow_legacy=False):
    pinned=plan.data.get('goal_snapshot')
    if not pinned:
        if allow_legacy and plan.approved_by: return
        raise HTTPException(409,'该计划缺少已确认目标快照，请确认目标后重新规划')
    current=snapshot(task)
    if pinned != current: raise HTTPException(409,'目标或日期范围已变化，旧计划不能继续或验收')


def stop_reason(db,p,task,plan):
    spec=task.data.get('goal_spec')
    if not spec: return None
    if spec['deadline'] and now() >= instant(spec['deadline']): return '目标截止时间已到，保留成果并等待新目标'
    fresh=query_goal(db,p,task,learning=False)
    if spec.get('data_query') and fresh['status']!='sufficient':
        return fresh.get('data_query',{}).get('reason') or '目标委托查询未就绪或依据失效，停止执行并等待重新核查'
    if spec['stop_on_conflict'] and fresh['conflicts']:
        return '目标依据存在同一时点的来源冲突，停止执行并保留记录等待核查'
    if spec.get('relation_query'):
        from .business_tools import relation_bindings
        pinned=plan.data.get('result',{}).get('evidence',{})
        if not pinned.get('relations') or relation_bindings(fresh)!=relation_bindings(pinned):
            return '关系依据、核查版本或对象链已变化，须重新规划并核查'
    ids=select(Plan.id).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id)
    from .goal_queries import call_count
    calls=call_count(db,p,task)
    if calls > spec['max_tool_calls']: return '目标工具调用预算已耗尽，不能继续调用'
    return None


def compare(value,op,expected):
    if op=='eq':return type(value) is type(expected) and value==expected or (
        isinstance(value,(int,float)) and not isinstance(value,bool) and isinstance(expected,(int,float))
        and not isinstance(expected,bool) and math.isfinite(value) and math.isfinite(expected) and value==expected)
    if isinstance(value,bool) or isinstance(expected,bool) or not isinstance(value,(int,float)) or not isinstance(expected,(int,float)):
        return False
    if not math.isfinite(value) or not math.isfinite(expected):return False
    return {'gt':value>expected,'gte':value>=expected,'lt':value<expected,'lte':value<=expected}.get(op,False)


def evaluate(task,plan,evidence,*,accepted=False,tool_receipts=None):
    spec=task.data.get('goal_spec')
    if not spec:return {'scope':'legacy_evidence_workflow','business_goal_completion':'not_verified'}
    receipts=[r['result'] for k,r in task.data.get('step_receipts',{}).items() if k.startswith(plan.id+':')]
    tools=tool_receipts if tool_receipts is not None else [r for r in receipts if r.get('method')=='registered_capability']
    checks=[]
    for criterion in spec['criteria']:
        kind=criterion['kind'];details=[]
        if kind=='evidence_sufficient':passed=evidence['status']=='sufficient'
        elif kind=='human_confirmation':passed=accepted
        elif kind=='capability_success':passed=any(r.get('capability_id')==criterion['capability_id'] and r['status']=='succeeded' for r in tools)
        elif kind=='tool_value':
            matching=[r for r in tools if r.get('capability_id')==criterion['capability_id'] and r['status']=='succeeded']
            passed=bool(matching) and all(compare(r.get('output',{}).get(criterion['field']),criterion['operator'],criterion['expected']) for r in matching)
        elif kind=='query_value':
            values=[r['values'].get(criterion['field']) for r in evidence.get('derived_records',[])]
            passed=evidence['status']=='sufficient' and bool(values) and all(compare(v,criterion['operator'],criterion['expected']) for v in values)
            details=[{'value':v,'passed':compare(v,criterion['operator'],criterion['expected'])} for v in values]
        else:
            rows=[*evidence['evidence'],*evidence.get('observation_evidence',[])]
            details=[{'entity_id':identifier,'passed':bool(values) and all(compare(v,criterion['operator'],criterion['expected']) for v in values)}
                for identifier in evidence.get('selected_entity_ids',spec['entity_ids']) for values in [[r['attributes'][criterion['field']] for r in rows
                    if r['entity_id']==identifier and criterion['field'] in r['attributes']]]]
            passed=bool(details) and all(d['passed'] for d in details)
        checks.append({'id':criterion['id'],'description':criterion['description'],'kind':kind,'passed':passed,'details':details})
    objective_checks=[c for c in checks if c['kind']!='human_confirmation']
    return {'scope':'confirmed_goal','goal_version':task.data['goal_version'],'checks':checks,
        'ready_for_acceptance':all(c['passed'] for c in objective_checks),
        'completed':accepted and all(c['passed'] for c in checks),
        'subgoals':[{'id':s['id'],'description':s['description'],'passed':all(c['passed'] for c in checks if c['id'] in s['criterion_ids'])} for s in spec['subgoals']]}
