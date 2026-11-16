"""Business-owned durable delegation; reading a goal never dispatches a tool."""
import copy
from fastapi import HTTPException
from sqlalchemy import select,func,or_
from .models import Task,Plan,DataRun,Invocation,Job,now
from .services import scoped,published,audit
from .contracts import DataRequest,digest
from .data_tools import frame,validate_steps


def request_for(db,p,task,*,authorization_task=None,lock_sources=True):
    spec=task.data['goal_spec'];query=spec['data_query']
    from .data_requirements import prepare_request
    value=DataRequest(question=spec['purpose'],entity_ids=spec['entity_ids'],fields=spec['required_fields'],mode='structured',
        evidence_requirement=spec['evidence'],relation_query=spec.get('relation_query'),tool_steps=query['tool_steps'],
        max_tool_calls=min(6,spec['max_tool_calls']),max_tool_seconds=query['max_tool_seconds'],
        allow_external_tools=query['allow_external_tools']).model_dump()
    from .temporal import instant
    authorized=authorization_task or (task if task.data.get('workflow_runtime') else None)
    from .data_tools import managed_task_kwargs
    value=prepare_request(db,p,value,reference=instant(task.data['goal_window']['reference_at']),_confirmed_plan=True,
        lock_sources=lock_sources,**managed_task_kwargs(authorized))
    value['resolved_time']=copy.deepcopy(task.data['goal_window'])
    return value


def call_count(db,p,task):
    from .workflow_graph_goal_guard_v133 import verify_reservations
    verify_reservations(db,p,task)
    plans=select(Plan.id).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id)
    query_calls=select(Invocation.id).join(DataRun,Invocation.idempotency_key.like('data-tool:'+DataRun.id+':%')).where(
        Invocation.workspace_id==p.workspace_id,DataRun.workspace_id==p.workspace_id,
        or_(DataRun.data['goal_delegation']['task_id'].as_string()==task.id,
            DataRun.data['native_task_query_contract']['task_id'].as_string()==task.id))
    workflow_jobs=select(Job.id).where(Job.workspace_id==p.workspace_id,
        Job.kind.in_(['execute_workflow_step','execute_full_semantic_graph_node']),Job.data['task_id'].as_string()==task.id)
    workflow_calls=select(Job.data['invocation_key'].as_string()).where(Job.workspace_id==p.workspace_id,
        Job.kind.in_(['execute_workflow_step','execute_full_semantic_graph_node']),Job.data['task_id'].as_string()==task.id)
    actual=db.scalar(select(func.count()).select_from(Invocation).where(Invocation.workspace_id==p.workspace_id,
        or_(Invocation.data['approval']['plan_id'].as_string().in_(plans),Invocation.id.in_(query_calls),
            Invocation.idempotency_key.in_(workflow_calls),Invocation.data['job_id'].as_string().in_(workflow_jobs)))) or 0
    from .workflow_graph_goal_guard_v133 import uninvoked_calls
    from .goal_evaluation_append_store_v297 import additional_budget_calls
    from .same_goal_advice_execution_v308 import budget_calls as advice_calls
    from .same_goal_continuation_execution_budget_v314 import additional_budget_calls as continuation_calls
    return actual+uninvoked_calls(db,p,task)+additional_budget_calls(db,p,task)+advice_calls(db,p,task)+continuation_calls(db,p,task)


def guard(db,p,row,key=None):
    pin=row.data.get('goal_delegation')
    if pin and 'native_task_query_contract' in row.data:
        raise HTTPException(409,'补充查询不能混用另一类目标委托关联')
    if row.data.get('native_goal_task_id'):
        if not pin or pin.get('task_id')!=row.data['native_goal_task_id']:raise HTTPException(409,'正式业务目标委托关联缺失或改变')
        parent=scoped(db,Task,pin['task_id'],p,True)
        if row.data.get('native_goal_context_hash')!=digest(parent.data.get('native_goal_context')) or digest(row.data['request'])!=row.data.get('native_goal_request_hash'):raise HTTPException(409,'正式目标原资料范围无法核对')
    if not pin:
        # Supplementary native queries deliberately have no goal_delegation.
        # Serialize their pre-Invocation budget gate with graph reservations,
        # retaining the Task lock until gateway.invoke commits its real call.
        # The original final before_dispatch fence remains independently run.
        from .native_task_queries import MARKER
        if MARKER in row.data:
            from .native_task_queries import _guard,_budget,_linked,LIVE,JOB_KIND
            contract=row.data[MARKER]
            fields={'schema','task_id','actor_id','context_hash','session_id','goal_snapshot',
                'proposal_id','adoption','request','source_hash','capabilities'}
            if (type(contract) is not dict or set(contract) not in (fields,fields|{'object_definition_bindings_hash'})
                or contract.get('schema')!='native-task-query-v1' or row.workspace_id!=p.workspace_id
                or type(contract.get('task_id')) is not str):
                raise HTTPException(409,'补充查询与原任务的冻结关联无法核对')
            parent=scoped(db,Task,contract['task_id'],p,True)
            row=scoped(db,DataRun,row.id,p,True)
            if digest(row.data.get(MARKER))!=digest(contract):
                raise HTTPException(409,'补充查询原冻结关联已变化，未创建新调用')
            _guard(db,p,parent,key);_linked(db,p,parent,row)
            if key is not None:
                parts=key.split(':') if type(key) is str else []
                steps=contract['request'].get('tool_steps',[])
                job=db.get(Job,row.data.get('native_query_job_id'))
                if (len(parts)!=3 or parts[:2]!=['data-tool',row.id]
                    or len([step for step in steps if step.get('id')==parts[2]])!=1
                    or row.status not in LIVE or row.owner_id!=p.user_id
                    or job is None or job.workspace_id!=p.workspace_id or job.kind!=JOB_KIND
                    or job.data.get('task_id')!=parent.id or job.data.get('query_id')!=row.id
                    or job.data.get('actor_id')!=row.owner_id or job.data.get('contract_hash')!=digest(contract)
                    or db.info.get('worker_lease',{}).get('job_id')!=job.id):
                    raise HTTPException(409,'补充查询原调用编号或实际后台关联无法核对')
                existing=db.scalar(select(Invocation.id).where(Invocation.workspace_id==p.workspace_id,
                    Invocation.idempotency_key==key))
                _budget(db,p,parent,additional=int(existing is None))
        return
    task=scoped(db,Task,pin['task_id'],p,True)
    from .goals import snapshot
    if task.data.get('workflow_runtime'):
        from .native_goals import guard as native_guard
        native_guard(db,p,task,key)
    from .temporal import instant
    if task.status in {'cancelled','completed'} or snapshot(task)!=pin['goal_snapshot']:
        raise HTTPException(409,'委托目标已结束或版本改变，保留查询回执，不继续旧业务')
    spec=task.data['goal_spec']
    if spec['deadline'] and now()>=instant(spec['deadline']):raise HTTPException(409,'委托目标截止时间已到，未继续调用')
    if key and not db.scalar(select(Invocation.id).where(Invocation.workspace_id==p.workspace_id,Invocation.idempotency_key==key)):
        if call_count(db,p,task)>=spec['max_tool_calls']:raise HTTPException(409,'任务累计工具调用预算已耗尽，未继续委托查询')


def read(db,p,task,base):
    """Only adopt a completed, unchanged query. Never recompute or charge from a read."""
    row_id=task.data.get('data_query_run_id')
    summary={'id':row_id,'status':'not_requested','reason':'尚未取得本目标委托查询的结果'}
    def blocked(reason,status='blocked_evidence'):
        return {**base,'status':status,'data_query':{**summary,'status':status,'reason':reason}}
    if not row_id:return blocked(summary['reason'],'insufficient')
    row=scoped(db,DataRun,row_id,p);summary.update(status=row.status,reason=row.data.get('error'))
    from .goals import snapshot
    try:
        if row.data.get('goal_delegation',{}).get('goal_snapshot')!=snapshot(task) or row.data['request']!=request_for(db,p,task):
            return blocked('委托查询不属于当前已确认目标及冻结日期，旧结果保留')
        result=row.data.get('result')
        if not result:return blocked('委托查询仍在后台处理或已经受阻',row.status)
        if digest(result)!=row.data.get('goal_result_hash'):return blocked('委托结果完整性不符，不能作为业务依据')
        if row.status in {'insufficient','conflict'} and base['status']!='sufficient':
            return blocked('原范围证据仍不足或冲突，需补证及业务核查；没有执行登记计算或云规划',row.status)
        context=row.data.get('tool_context',{})
        from .business_tools import relation_bindings
        relation_frame=lambda value:relation_bindings(value) if value.get('relations') else None
        if base['status']!='sufficient' or frame(base)!=context.get('source_records') or relation_frame(base)!=relation_frame(result):
            return blocked('委托查询的原来源或关系依据已变化，请重新规划与取证')
        from .data_tools import managed_task_kwargs
        validate_steps(db,p,row.data['request'],result.get('query_tool_steps') or row.data['request']['tool_steps'],**managed_task_kwargs(task))
        for item in result.get('tool_trace',[]):
            cap=published(db,p,item['capability_id']);call=scoped(db,Invocation,item['invocation_id'],p)
            if cap.content_hash!=item['capability_hash'] or call.status!=item['status'] or call.data.get('output')!=item.get('output'):
                return blocked('委托查询的能力版本或保存回执已变化，请重新核查')
            if cap.data['execution']['adapter']=='cloud_retrieval':
                from .retrieval import index_current
                index_current(db,p,cap,item['output']['summary'])
        if row.status!='sufficient':
            return blocked(result.get('tool_blocker') or '委托查询尚未充分或未经核查',row.status)
        if result.get('derivation_verification') not in {'deterministic_registered_computation','human_confirmed_extension','source_preserving_semantic_ranking'}:
            return blocked('委托推导尚无有效确认依据')
        from .data_gaps import receipts_ready
        if not receipts_ready(db,p,{'result':result}):return blocked('所用观测尚未取得数据归集回执，未进入业务规划','awaiting_collection')
        adopted={k:copy.deepcopy(result[k]) for k in ['derived_records','tool_trace','derivation_verification','query_tool_steps']}
        return {**base,**adopted,'data_query':{**summary,'reason':None,'version':row.version,
            'result_hash':row.data['goal_result_hash'],'review':copy.deepcopy(row.data.get('derived_review'))}}
    except HTTPException as error:return blocked(str(error.detail))


def ensure(db,p,task,plan):
    """Pin and enqueue one child request before any paid business planning."""
    if not task.data['goal_spec'].get('data_query'):return True
    from .goals import query_goal,snapshot
    latest=query_goal(db,p,task,learning=False)
    if latest['status']=='sufficient':
        plan.data={**plan.data,'data_query_id':task.data['data_query_run_id']};return True
    previous=db.get(DataRun,task.data.get('data_query_run_id')) if task.data.get('data_query_run_id') else None
    prepared=request_for(db,p,task)
    if previous and previous.workspace_id==p.workspace_id and previous.status in {'queued','running','needs_review'} and (
        previous.data.get('goal_delegation',{}).get('goal_snapshot')==snapshot(task) and previous.data['request']==prepared):
        row=previous
    else:
        if call_count(db,p,task)+len(prepared['tool_steps'])>task.data['goal_spec']['max_tool_calls']:
            raise HTTPException(409,'任务剩余累计调用预算不足以发起新委托，请核查历史调用后确认目标预算')
        row=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=task.data['goal_spec']['purpose'],status='queued',
            data={'request':prepared,'goal_delegation':{'task_id':task.id,'plan_id':plan.id,'goal_snapshot':snapshot(task)},
                'human_confirmation':{'actor_id':task.data['goal_confirmed_by'],'reason':'采用已确认目标中的登记查询方案','source_question':task.title}})
        db.add(row);db.flush()
        db.add(Job(workspace_id=p.workspace_id,kind='data_query',idempotency_key='query:'+row.id,
            data={'query_id':row.id,'actor_id':p.user_id}))
        task.data={**task.data,'data_query_run_id':row.id};task.version+=1
        audit(db,p,'business.data_delegated',task.id,{'query_id':row.id,'plan_id':plan.id})
    plan.status='waiting_data';plan.version+=1
    plan.data={**plan.data,'data_query_id':row.id,'progress':{'stage':'waiting_data','saved_at':now().isoformat()}}
    task.status='waiting_data' if row.status in {'queued','running'} else 'waiting_evidence';task.version+=1
    task.data={**task.data,'execution_error':None if task.status=='waiting_data' else latest.get('data_query',{}).get('reason')}
    return False


def completed(db,p,row):
    pin=row.data.get('goal_delegation')
    if not pin:return
    result=row.data.get('result')
    if result:row.data={**row.data,'goal_result_hash':digest(result)}
    task=scoped(db,Task,pin['task_id'],p,True)
    from .goals import snapshot,query_goal
    if task.status in {'cancelled','completed'} or snapshot(task)!=pin['goal_snapshot']:return
    plan_id=task.data.get('current_plan_id')
    if not plan_id:return
    plan=scoped(db,Plan,plan_id,p,True)
    if plan.status!='waiting_data' or plan.data['request']['goal_snapshot']!=pin['goal_snapshot']:return
    # A gap recheck can supply a new immutable result for the same request.
    task.data={**task.data,'data_query_run_id':row.id};task.version+=1
    plan.data={**plan.data,'data_query_id':row.id}
    latest=query_goal(db,p,task,learning=False)
    if latest['status']=='sufficient':
        plan.status='queued';plan.version+=1;task.status='planning';task.version+=1
        task.data={**task.data,'execution_error':None}
        plan.data={**plan.data,'progress':{'stage':'data_ready','saved_at':now().isoformat()}}
        db.add(Job(workspace_id=p.workspace_id,kind='generate_plan',idempotency_key='planning:'+plan.id+':data:'+row.id,
            data={'plan_id':plan.id,'actor_id':plan.data['request']['actor_id']}))
    else:
        task.status='waiting_evidence';task.version+=1
        task.data={**task.data,'execution_error':latest.get('data_query',{}).get('reason') or '委托查询尚未达到业务证据要求'}
    audit(db,p,'business.data_received',task.id,{'query_id':row.id,'status':latest['status']})


def source_received(db,p,observation_id,entity_id):
    rows=db.scalars(select(DataRun).join(Task,Task.data['data_query_run_id'].as_string()==DataRun.id)
        .join(Plan,Task.data['current_plan_id'].as_string()==Plan.id).where(
            DataRun.workspace_id==p.workspace_id,Task.workspace_id==p.workspace_id,Plan.workspace_id==p.workspace_id,
            DataRun.status=='sufficient',Plan.status=='waiting_data',
            DataRun.data['request']['entity_ids'].contains([entity_id]))).all()
    for row in rows:
        if any(r['observation_id']==observation_id for r in row.data.get('result',{}).get('observation_evidence',[])):
            completed(db,p,row)


def case_valid(db,p,evidence):
    saved=evidence.get('data_query')
    if not saved:return True
    from .models import Entity,Observation
    try:
        row=scoped(db,DataRun,saved['id'],p);result=row.data.get('result',{})
        if row.status!='sufficient' or digest(result)!=saved['result_hash'] or row.data.get('goal_result_hash')!=saved['result_hash']:return False
        for item in result.get('tool_trace',[]):
            cap=published(db,p,item['capability_id']);call=scoped(db,Invocation,item['invocation_id'],p)
            if cap.content_hash!=item['capability_hash'] or call.data.get('output')!=item.get('output') or call.status!='succeeded':return False
            if cap.data['execution']['adapter']=='cloud_retrieval':
                from .retrieval import index_current
                index_current(db,p,cap,item['output']['summary'])
        for item in evidence.get('evidence',[]):
            source=scoped(db,Entity,item['entity_id'],p)
            if source.version!=item['version'] or source.valid_until and source.valid_until<=now():return False
        for item in evidence.get('observation_evidence',[]):
            source=scoped(db,Observation,item['observation_id'],p)
            if source.status!='processed' or source.version!=item['version']:return False
        return True
    except HTTPException:return False
