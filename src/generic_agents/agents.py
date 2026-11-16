"""Bounded LangGraph workflows. Services own authorization and durable state."""
import json
import copy
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .services import query_evidence, published, scoped, enqueue_event, validate
from fastapi import HTTPException
from .models import Entity, Capability
from .contracts import digest
from .leases import checkpoint
from .gateway import cloud_json, ModelBlocked, invoke
from .query_needs import NEED_SCHEMA


class AgentState(TypedDict, total=False):
    request: dict
    need: dict
    result: dict
    status: str
    error: str
    model_receipt: dict
    invocation_id: str
    goal_snapshot: dict
    resumed_model_result: bool


def data_agent(db, p, request, context_key=None, *, cached_only=False, emit_gaps=True, learning=True, task=None, include_trusted_cases=None, _goal_evaluation_cut=None):
    if context_key and _goal_evaluation_cut is None:
        from .models import DataRun
        from .goal_evaluation_query_guard_v288 import reject_generic_write as reject_evaluation_write
        original_evaluation = scoped(db, DataRun, context_key, p)
        reject_evaluation_write(db, p, original_evaluation)
    if _goal_evaluation_cut is not None:
        from .goal_evaluation_query_v288 import _agent_clock, need as _cut_need
        _agent_clock(db,p,request,context_key,task,_goal_evaluation_cut,cached_only=cached_only,
            emit_gaps=emit_gaps,learning=learning,include_trusted_cases=include_trusted_cases)
    from . import multimodal_question_service_v262 as multimodal_questions
    if context_key and multimodal_questions.has_trace(db,p,context_key):
        if _goal_evaluation_cut is not None: _cut_need(False)
        original=multimodal_questions._load(db,p,context_key)
        multimodal_questions.need(multimodal_questions.same(request,multimodal_questions.checked(db,p,original)['request']))
        from .leases import ownership
        from .models import Job
        job=multimodal_questions._ref(db,p,Job,ownership(db).get('job_id'))
        multimodal_questions.process(db,p,job)
        return {'status':original.status}
    if request.get('schema')=='multimodal-question-submit-v262':
        multimodal_questions.need(False,'统一成果问题必须使用已保存原请求及其专属后台查询')
    from . import pixel_question_service_v252 as pixel_questions
    if context_key and pixel_questions.has_trace(db,p,context_key):
        if _goal_evaluation_cut is not None: _cut_need(False)
        original=pixel_questions._load(db,p,context_key)
        pixel_questions.need(pixel_questions.same(request,pixel_questions.checked(db,p,original)['request']))
        from .leases import ownership
        from .models import Job
        job=pixel_questions._ref(db,p,Job,ownership(db).get('job_id'))
        pixel_questions.process(db,p,job)
        return {'status':original.status}
    if request.get('schema')=='pixel-question-submit-v252':
        pixel_questions.need(False,'影像问题必须使用已保存原请求的后台查询处理')
    from . import source_query_service as source_queries
    from .source_query_contracts import need as source_need, uuid as source_uuid
    try: source_uuid(context_key); source_identity = True
    except (ValueError, TypeError, AttributeError): source_identity = False
    if source_identity:
        from .models import DataRun
        from sqlalchemy import select
        original_query = db.scalar(select(DataRun).where(DataRun.id==context_key,DataRun.workspace_id==p.workspace_id))
        if original_query is not None and source_queries.is_v2(db, original_query):
            if _goal_evaluation_cut is not None: _cut_need(False)
            source_need(request == original_query.data.get('request'))
            return source_queries.execute(db, p, context_key, cached_only=cached_only)
    if request.get('schema') in {'data-query-create-v2','document-question-create-v1'}:
        source_need(False, 'V2资料查询必须使用已保存原请求执行，不能降级为字段查询')
    if context_key:
        from .native_candidate_queries_v139 import has_trace, execute_agent
        if has_trace(db, p, context_key):
            if _goal_evaluation_cut is not None: _cut_need(False)
            return execute_agent(db, p, request, context_key, cached_only=cached_only)
    allowed_ids = request["entity_ids"]
    def parse(state):
        if request.get('clarifications'):
            return {'status':'needs_clarification','error':'；'.join(request['clarifications'])}
        if request["mode"] == "structured":
            return {"need": {"entity_ids": allowed_ids, "fields": request["fields"],
                             "purpose": request["question"],"tool_steps":request.get('tool_steps',[])}, "status": "querying"}
        if not request.get("model_profile_id"):
            return {"status": "blocked_model", "error": "未选择已发布的云模型配置"}
        try:
            from .query_needs import interpret
            return interpret(db,p,request,context_key,cloud_json,cached_only=cached_only)
        except ModelBlocked as error:
            return {"status": "blocked_model", "error": str(error)}
    def query(state):
        if state["status"] in {"blocked_model","needs_clarification","needs_confirmation","cancelled"}:
            return {}
        def read(learn):
            from .data_tools import managed_include_trusted_cases
            include_cases=managed_include_trusted_cases(db,p,task) and include_trusted_cases is not False
            return query_evidence(db, p, state["need"]["entity_ids"], state["need"]["fields"],
                                context=request["question"], route_key=context_key,learning=learn,
                                requirement=request.get('evidence_requirement'),fixed_window=request.get('resolved_time'),
                                relation_query=state['need'].get('relation_query') or request.get('relation_query'),
                                **({'include_trusted_cases':False} if not include_cases else {}),
                                **({'_goal_evaluation_cut':_goal_evaluation_cut} if _goal_evaluation_cut is not None else {}))
        result=read(learning)
        tool_steps=state['need'].get('tool_steps') or request.get('tool_steps',[])
        if tool_steps:
            from .data_tools import execute,managed_task_kwargs
            try:result=execute(db,p,{**request,'fields':state['need']['fields']},result,tool_steps,context_key,lambda:read(False),cached_only=cached_only,**managed_task_kwargs(task))
            except HTTPException as error:
                result={**result,'status':'blocked_evidence','tool_blocker':str(error.detail)}
        return {"result": {**result, "data_need_spec": state["need"], "query_plan": {
            "executor": "scoped_resource_query", "entity_ids": state["need"]["entity_ids"],
            "fields": state["need"]["fields"],"steps":([
                {'id':'relations','executor':'scoped_relation_traversal','depends_on':[],
                 'input':request['relation_query'],'read_limit':50},
                {'id':'fields','executor':'scoped_resource_query','depends_on':['relations'],
                 'entity_ids':result.get('selected_entity_ids',[]),'fields':state['need']['fields']}
            ] if request.get('relation_query') else [])}, "reasoning_mode": request["mode"]},
            "status": result["status"]}
    graph = StateGraph(AgentState); graph.add_node("parse_need", parse); graph.add_node("query_and_assess", query)
    graph.add_edge(START, "parse_need"); graph.add_edge("parse_need", "query_and_assess"); graph.add_edge("query_and_assess", END)
    state=graph.compile().invoke({"request": request})
    if emit_gaps and context_key:
        from .data_gaps import publish
        state['data_gap_ids']=publish(db,p,context_key,state)
    return state


def perception_agent(db, p, observation, extractor_id, attempt=1, invocation_key=None):
    def extract(state):
        from .simulation_observation_origin_v177 import guard as simulation_guard
        simulation_guard(db,p,observation,execution=True)
        from .threshold_policy_execution_v153 import extract_result as policy_extract
        policy_key,saved_call=policy_extract(db,p,observation,extractor_id)
        from .original_acquisition_reception_v127 import context as original_context
        if original_context(db,p,observation) is not None:
            # Historical domain reception grants no new source execution.
            published(db,p,observation.source_id,'source')
        if observation.data.get('mqtt_receipt_id'):
            from .mqtt_observations import guard
            guard(db,p,observation,execution=True)
        if observation.data.get('reported_rule_contract'):
            from .perception_signals import guard_rule
            guard_rule(db,p,observation)
        from .observation_candidate_dispatch import verify_candidate
        verify_candidate(db,p,observation,extractor_id)
        inputs = {"attributes": observation.data["attributes"]}
        if observation.data.get("evidence_ids"):
            inputs["evidence_ids"] = observation.data["evidence_ids"]
        result = saved_call if saved_call is not None else invoke(db, p, extractor_id, "perception", inputs,
                        policy_key or invocation_key or "observation:" + observation.id + ":attempt:" + str(attempt))
        return {"status": result.status, "result": result.data.get("output", {}),
                "error": result.data.get("error", ""), "invocation_id": result.id,
                "model_receipt": result.data.get("model_receipt", {})}
    def route(state):
        from .simulation_observation_origin_v177 import guard as simulation_guard
        simulation_origin = simulation_guard(db,p,observation,execution=True,
            result=state['result'] if state['status']=='succeeded' else None)
        from .threshold_policy_execution_v153 import before_publish as policy_publish
        policy_publish(db,p,observation,state)
        if observation.data.get('mqtt_receipt_id'):
            from .mqtt_observations import guard
            guard(db,p,observation,execution=True,result=state['result'] if state['status']=='succeeded' else None)
        if observation.data.get('reported_rule_contract'):
            from .perception_signals import guard_rule
            guard_rule(db,p,observation,result=state["result"] if state["status"]=="succeeded" else None)
        from .observation_candidate_dispatch import verify_candidate
        audio_candidate=verify_candidate(db,p,observation,extractor_id)
        payload = {"observation_id": observation.id, "entity_id": observation.entity_id,
                   "source_id": observation.source_id, "evidence_ids": observation.data.get("evidence_ids", []),
                   "invocation_id": state["invocation_id"], "model_receipt": state.get("model_receipt", {}),
                   "observed_at": observation.observed_at.isoformat() if observation.observed_at else None,
                   "candidate_only":observation.data.get('candidate_only',False),"extracted": state["result"]}
        if simulation_origin is not None:
            payload.update(simulation=True,origin_kind='simulated_equipment',field_verified=False,
                           simulation_origin=copy.deepcopy(simulation_origin))
        if state["status"] != "succeeded":
            from .observation_inputs import fail as fail_input
            fail_input(db,p,observation,attempt,state.get('error'),state['status'],state['invocation_id'])
            return {}
        observation.status = "processed"
        observation.data = {**observation.data, "extracted": state["result"], "processing_error": None,
                            "failure_type": None, "invocation_id": state["invocation_id"],
                            "model_receipt": state.get("model_receipt", {})}
        observation.version += 1
        from .original_acquisition_reception_v127 import hold
        reception_only = hold(db, p, observation, remember=True)
        # The original output/event remains evidence. A stopped destination
        # does not turn that evidence into new alerts, messages or work.
        source_events = [enqueue_event(db, p, "observation.data", "data:" + observation.id, payload)]
        if not reception_only and not audio_candidate:
            if state["result"].get("anomalies"):
                source_events.append(enqueue_event(db, p, "perception.anomaly", "anomaly:" + observation.id, payload))
            from .graph_event_dispatch import capture_source
            for source_event in source_events:
                capture_source(db, p, observation, source_event)
            from .candidate_watches import enqueue_new
            enqueue_new(db,p,observation)
            from .quality import changed
            changed(db,p,scoped(db,Entity,observation.entity_id,p).schema_capability_id,
                'observation:'+observation.id+':'+str(observation.version))
        return {"status": "processed"}
    graph = StateGraph(AgentState); graph.add_node("extract", extract); graph.add_node("route", route)
    graph.add_edge(START, "extract"); graph.add_edge("extract", "route"); graph.add_edge("route", END)
    return graph.compile().invoke({})


PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "steps"],
    "properties": {"summary": {"type": "string", "maxLength": 2000}, "steps": {"type": "array",
        "minItems": 1, "maxItems": 12, "items": {"type": "object", "additionalProperties": False,
        "required": ["title", "agent", "action"], "properties": {
            "title": {"type": "string", "maxLength": 300},
            "agent": {"enum": ["data", "perception", "business"]},
            "action": {"enum": ["query_evidence", "inspect_observation", "request_observation", "report"]}}}}}}
PLAN_SCHEMA["properties"]["steps"]["items"]["oneOf"] = [
    {"properties": {"agent": {"const": agent}, "action": {"const": action}}}
    for agent, action in [("data", "query_evidence"), ("perception", "inspect_observation"),
                         ("perception", "request_observation"), ("business", "report")]]

ACTION_TITLES = {
    "query_evidence": "读取资源档案和观测证据",
    "inspect_observation": "核对既有观测和原始文件完整性（不发起采集）",
    "request_observation": "提出新设备观测请求（需要设备授权）",
    "report": "整理证据报告和待核查限制",
}


def business_agent(db, p, task, model_profile_id, *, before_model=None, checkpoint_result=None, cached=None, request_key=None):
    from .goals import query_goal, snapshot
    goal_snapshot=snapshot(task)
    def gather(state):
        return {"result": copy.deepcopy(cached['evidence']) if cached else query_goal(db, p, task, context=task.title,
                                       route_key="task:"+task.id+":plan:"+str(task.version)), "status": "planning"}
    def read_sensor_evidence(state):
        # Explicit read-only tool node. It may only return the operator's
        # frozen selection and performs no acquisition or device operation.
        from .sensor_agent_context import read_authorized
        actual=read_authorized(db,p,task,model_profile_id)
        if actual!=state['result'].get('sensor_agent_context'):
            raise HTTPException(409,'任务设备证据授权已变化，请重新核对')
        return {}
    def plan(state):
        if not model_profile_id:
            return {"status": "blocked_model", "error": "请配置云模型后生成业务计划"}
        try:
            cap = published(db, p, model_profile_id, "foundation_model")
            if state['result'].get('sensor_agent_context'):
                from .sensor_agent_context import read_authorized
                current=read_authorized(db,p,task,model_profile_id)
                if current!=state['result']['sensor_agent_context']:
                    raise HTTPException(409,'传感器证据授权或内容已变化')
            schema = copy.deepcopy(PLAN_SCHEMA)
            from .business_tools import choices, arguments_schema, planning_arguments_schema, prepare_step
            tools=choices(db,p,task)
            available = {"query_evidence", "report", "request_observation"}
            if state["result"].get("observation_evidence"):
                available.add("inspect_observation")
            if task.data.get("observation_mode") == "existing_only" or state['result'].get('relations',{}).get('relation_gaps'):
                available.discard("request_observation")
            step_schema = schema["properties"]["steps"]["items"]
            step_schema["properties"]["action"]["enum"] = sorted(available)
            step_schema["oneOf"] = [pair for pair in step_schema["oneOf"]
                if pair["properties"]["action"]["const"] in available]
            if tools:
                available.add('invoke_capability')
                builtins=copy.deepcopy(step_schema)
                branches=[builtins]
                for tool in tools:
                    branches.append({'type':'object','additionalProperties':False,
                        'required':['title','agent','action','capability_id','arguments'],
                        'properties':{'title':{'type':'string','maxLength':300},
                            'agent':{'enum':tool.data['allowed_agent_roles']},'action':{'const':'invoke_capability'},
                            'capability_id':{'const':tool.id},'arguments':planning_arguments_schema(tool,schema)}})
                schema['properties']['steps']['items']={'oneOf':branches}
            signature=digest({'goal':goal_snapshot,'scope':task.data.get('observation_mode','auto'),
                'model_hash':cap.content_hash,'schema':schema,'tools':{t.id:t.content_hash for t in tools}})
            if cached and signature!=cached['signature']:
                raise ModelBlocked('规划检查点的目标、模型或工具版本已变化，请核查后重新规划')
            if not cached:
                if before_model:before_model(signature,state['result'])
                else:checkpoint(db)
                # Check again after the durable planning checkpoint released locks.
                # Keep the membership read lock through the provider call.
                if state['result'].get('sensor_agent_context'):
                    current=read_authorized(db,p,task,model_profile_id)
                    if current!=state['result']['sensor_agent_context']:
                        raise HTTPException(409,'传感器证据授权或内容已变化')
            messages=[{"role": "user", "content": json.dumps({
                "goal": task.title, 'confirmed_goal':goal_snapshot, "evidence": state["result"],
                "available_actions": sorted(available), "observation_mode": task.data.get("observation_mode", "auto"),
                "registered_tools":[{'capability_id':t.id,'description':t.data['description'],
                    'release_version':t.release_version,'arguments_schema':arguments_schema(t),
                    'server_input_bindings':t.data['execution'].get('input_bindings',{}),
                    'side_effect':bool(t.data['execution'].get('side_effect'))} for t in tools],
                "instruction": "制定尚未执行的计划：summary描述将要做什么，不能声称已经核实或执行。"
                    "先验证证据，对任务确实需要的观测缺口提出补采，对结果生成报告；"
                    "若任务明确仅说明历史档案，不要求实时状态，不为缺失的历史时间编造观测或安排无意义补采。"
                    "区分资源档案和带时间的观测提取值；unverified_extraction仅为待核查证据，不能宣称已确认。"
                    "sensor_agent_context 是用户专门授权的设备读数与候选阈值线索，只能作为待核实依据；不能当成现场结论，设备时间未知时不能推断发生时间。"
                    "action inspect_observation用于读取既有观测及校验保存文件完整性，不发起采集；"
                    "action request_observation仅用于提出新设备观测。目标明确不补采时不得使用request_observation。"
                    "固定action只列一次。注册工具用invoke_capability、登记capability_id及arguments；真实证据由服务器绑定，不编造记录。"
                    "需要计算、分析或专业服务时，调用适用的已登记工具，并将报告放在工具之后。"
                    "已确认目标中的data_query由数据智能体事先执行；其derived_records是带来源的已保存推导，不再次调用该查询链或把数值当设备真实状态。"
                    "关系查询先由确定性取证链选出目标对象；字段读取/补采针对选出的对象，关系依据不足须由数据责任人核查，不能以新设备观测代替关系确认。"
                    "执行必须通过服务端授权，无法满足的要求必须保留阻塞。"
            }, ensure_ascii=False)}]
            if cached:
                value,receipt=copy.deepcopy(cached['value']),copy.deepcopy(cached['receipt'])
            else:
                value,receipt=cloud_json(cap,messages,schema,p)
                if checkpoint_result:checkpoint_result(value,receipt,state['result'],signature)
            validate(schema, value)
            if any(s['action']=='invoke_capability' for s in value['steps']) and value['steps'][-1]['action']!='report':
                raise HTTPException(409,'调用工具的计划必须在工具完成后生成报告')
            # Execution supports a fixed scoped operation per action. Do not present arbitrary model titles
            # as capabilities or repeat the same full-scope operation under different labels.
            seen, steps = set(), []
            for index,step in enumerate(value["steps"]):
                if step['action']=='invoke_capability':
                    steps.append(prepare_step(db,p,task,step,state['result'],index,request_scope_key=request_key))
                elif step["action"] not in seen:
                    steps.append({**step, "title": ACTION_TITLES[step["action"]]})
                    seen.add(step["action"])
            required_caps={c['capability_id'] for c in goal_snapshot['spec']['criteria'] if c['kind'] in {'capability_success','tool_value'}}
            if required_caps - {s.get('capability_id') for s in steps}:
                raise HTTPException(409,'计划没有覆盖目标要求的登记工具')
            if len(steps)>goal_snapshot['spec']['max_steps'] or sum(s['action']=='invoke_capability' for s in steps)>goal_snapshot['spec']['max_tool_calls']:
                raise HTTPException(409,'计划超过目标步骤或调用预算')
            for index,step in enumerate(steps):
                step['id']='step_'+str(index+1)
                step['depends_on']=['step_'+str(index)] if index else []
                step['goal_version']=goal_snapshot['version']
                step['verification_criteria']=[c['id'] for c in goal_snapshot['spec']['criteria'] if c['kind']!='human_confirmation']
                step['subgoal_ids']=[s['id'] for s in goal_snapshot['spec']['subgoals']]
                step['preconditions']=['confirmed_goal_unchanged','deadline_and_budget_valid','scoped_evidence_valid']
            normalized = {**value, "steps": steps}
            return {"status": "review", "result": {"plan": normalized, "model_proposal": value,
                    "evidence": state["result"], "capability_scoped": True,
                    "constraints": {"observation_mode": task.data.get("observation_mode", "auto")}},
                    "model_receipt": receipt,'goal_snapshot':goal_snapshot}
        except ModelBlocked as error:
            return {"status": "blocked_model", "error": str(error)}
        except HTTPException:
            return {"status": "blocked_model", "error": "计划不符合目标约束或当前可执行能力，未批准执行"}
    graph = StateGraph(AgentState); graph.add_node("gather_evidence", gather); graph.add_node("read_sensor_evidence",read_sensor_evidence); graph.add_node("plan", plan)
    graph.add_edge(START, "gather_evidence"); graph.add_edge("gather_evidence", "read_sensor_evidence"); graph.add_edge("read_sensor_evidence", "plan"); graph.add_edge("plan", END)
    return graph.compile().invoke({})
