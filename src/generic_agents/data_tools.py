"""Registry-bound read-only transformations of already authorized evidence."""
import copy,math,json
from datetime import datetime,timedelta
from fastapi import HTTPException
from pydantic import ValidationError
from .contracts import DataToolStep,digest
from .models import DataRun,Entity,Capability,Task,now
from .services import scoped,published,validate,audit
from .leases import checkpoint

BINDINGS={'data_records','data_question','data_scope','request_key'}
HANDLERS={'data_record_aggregate','data_keyword_filter','data_fulltext_search'}


def validate_manifest(value):
    from .multimodal_question_tool_v262 import applies as multimodal_facet, validate_manifest as validate_multimodal_facet
    if multimodal_facet(value):return validate_multimodal_facet(value)
    from .pixel_question_catalog_v252 import applies as pixel_facet, validate_manifest as validate_pixel_facet
    if pixel_facet(value):return validate_pixel_facet(value)
    execution=value['execution'];bindings=execution.get('input_bindings',{})
    if not isinstance(bindings,dict):raise HTTPException(422,'查询工具证据绑定必须是键值目录')
    if execution.get('query_tool') is not True or value['kind'] not in {'tool','specialist_model'} or 'data' not in value['allowed_agent_roles']:
        raise HTTPException(422,'查询工具必须是获准data角色的工具或专业模型')
    if execution.get('side_effect') is not False or execution.get('adapter') not in {'builtin','http','mcp','cloud_retrieval'}:
        raise HTTPException(422,'查询工具必须显式只读并使用已实现适配器')
    if execution['adapter']=='builtin' and execution.get('handler') not in HANDLERS:
        raise HTTPException(422,'查询工具未使用已实现的数据处理器')
    props=value['input_schema'].get('properties',{})
    if execution['adapter']=='builtin':
        keys={'records','field','text' if execution['handler'] in {'data_keyword_filter','data_fulltext_search'} else 'operation'}
        if execution['handler']=='data_fulltext_search':keys|={'top_k','min_score','match_mode'}
        if bindings.get('records')!='data_records' or not keys.issubset(props) or not keys.issubset(value['input_schema'].get('required',[])):
            raise HTTPException(422,'内置查询处理器的记录、字段及参数必须按实现契约登记')
        if execution['handler']=='data_record_aggregate':
            operations=props['operation'].get('enum')
            if not isinstance(operations,list) or not operations or not all(isinstance(x,str) and x in {'mean','sum','min','max','count'} for x in operations):
                raise HTTPException(422,'聚合操作须显式登记非空的已实现操作目录')
        if execution['handler']=='data_fulltext_search':
            modes=props['match_mode'].get('enum')
            if not isinstance(modes,list) or not modes or not all(isinstance(x,str) and x in {'all_terms','any_terms'} for x in modes):
                raise HTTPException(422,'全文词项要求须显式登记已实现的全部或任意匹配方式')
    if not isinstance(bindings,dict) or 'data_records' not in bindings.values() or any(
        k not in props or v not in BINDINGS for k,v in bindings.items()):
        raise HTTPException(422,'查询工具须声明服务器证据记录绑定，不接受模型提供原始记录')
    records=value['output_schema'].get('properties',{}).get('records',{})
    if value['output_schema'].get('type')!='object' or records.get('type')!='array' or not {
        'values','entity_ids','evidence_refs'}.issubset(records.get('items',{}).get('required',[])):
        raise HTTPException(422,'查询输出必须声明有来源/对象引用的派生记录')
    if execution['adapter']=='cloud_retrieval':
        keys={'records','field','text','top_k','min_score'}
        if bindings.get('records')!='data_records' or not keys.issubset(props) or not keys.issubset(value['input_schema'].get('required',[])):
            raise HTTPException(422,'语义排序参数和服务器记录须按已实现契约登记')
    if execution.get('handler')=='data_fulltext_search' or execution['adapter']=='cloud_retrieval':
        if props['top_k'].get('type')!='integer' or props['top_k'].get('minimum',0)<1 or props['top_k'].get('maximum',201)>20:
            raise HTTPException(422,'检索返回数量必须登记为1至20的有界整数')
        if props['min_score'].get('type')!='number' or props['min_score'].get('minimum',-1)<0 or props['min_score'].get('maximum',51)>(1 if execution['adapter']=='cloud_retrieval' else 50):
            raise HTTPException(422,'检索最低分须显式有界登记')
    if execution['adapter'] in {'http','mcp','cloud_retrieval'} and not value['data_policy'].get('external_allowed'):
        raise HTTPException(422,'外部查询工具须明确数据出域授权')


def _managed_scope(db,p,task,entity_ids=None,*,lock_sources=True):
    """Only a server-held saved Task can authorize a managed query scope."""
    if not isinstance(task,Task) or not task.id or task.workspace_id!=p.workspace_id:
        raise HTTPException(409,'查询授权需要原已保存业务任务，不能使用临时目标或客户端范围')
    from .entity_release_compat import verify_task_objects
    if lock_sources:verify_task_objects(db,p,task)
    else:verify_task_objects(db,p,task,lock_sources=False)
    allowed=task.data.get('entity_ids',[])
    if entity_ids is not None and (len(entity_ids)!=len(set(entity_ids)) or set(entity_ids)!=set(allowed)):
        raise HTTPException(403,'查询对象必须保持原任务完整授权范围')
    return task


def managed_task_kwargs(task):
    """Legacy callers keep their exact seam; only new server proofs select this path."""
    return ({'task':task} if task is not None and any(key in task.data
        for key in ('object_definition_bindings','object_definition_bindings_hash')) else {})


def managed_include_trusted_cases(db,p,task,*,lock_sources=True):
    """Object compatibility grants no permission to reuse another package's Memory."""
    if task is None or not any(key in task.data for key in ('object_definition_bindings','object_definition_bindings_hash')):
        return True
    if lock_sources:_managed_scope(db,p,task)
    else:_managed_scope(db,p,task,lock_sources=False)
    return all(scoped(db,Entity,identity,p).schema_capability_id==task.package_id for identity in task.data['entity_ids'])


def choices(db,p,entity_ids,*,task=None,lock_sources=True):
    if managed_task_kwargs(task):
        _managed_scope(db,p,task,entity_ids,lock_sources=lock_sources)
        from .native_query_catalog import catalog_for_task
        result=catalog_for_task(db,p,task,lock_sources=lock_sources)
        for cap in result:validate_manifest(cap.data)
        return result
    packages={scoped(db,Entity,x,p).schema_capability_id for x in entity_ids}
    allowed=None
    for identifier in packages:
        package=published(db,p,identifier,'domain_package')
        ids=set(package.data['specification'].get('data_tool_ids',[]))
        if not ids.issubset(package.data['dependencies']):raise HTTPException(409,'查询工具缺少业务包依赖锁定')
        allowed=ids if allowed is None else allowed&ids
    result=[]
    for identifier in sorted(allowed or []):
        cap=published(db,p,identifier);validate_manifest(cap.data)
        from .pixel_question_catalog_v252 import applies as pixel_facet
        from .multimodal_question_tool_v262 import applies as multimodal_facet
        if not pixel_facet(cap.data) and not multimodal_facet(cap.data):result.append(cap)
    if len(result)>12:raise HTTPException(409,'单范围最多12项登记查询工具')
    return result


def validate_steps(db,p,request,steps,*,task=None,lock_sources=True):
    try:values=[DataToolStep.model_validate(s).model_dump() for s in steps]
    except ValidationError:raise HTTPException(422,'查询步骤结构不符合登记契约')
    if len(values)>request.get('max_tool_calls',6):raise HTTPException(422,'步骤超过本次查询调用预算')
    allowed={c.id:c for c in choices(db,p,request['entity_ids'],lock_sources=lock_sources,
        **managed_task_kwargs(task))};seen={'evidence'};outputs={}
    for step in values:
        if step['id'] in seen or step['input_from'] not in seen:raise HTTPException(422,'步骤只能读取证据或前序结果，不能重复、循环或引用未来步骤')
        if step['capability_id'] not in allowed:raise HTTPException(403,'查询工具不在全部授权对象的业务包依赖内')
        cap=allowed[step['capability_id']];parameters=step['parameters']
        if len(json.dumps(parameters,ensure_ascii=False).encode())>16000:raise HTTPException(422,'单步查询参数超过保存上限')
        if set(parameters)&set(cap.data['execution']['input_bindings']):raise HTTPException(422,'查询参数不能覆盖服务器证据绑定')
        def safe(v):
            if isinstance(v,dict):
                if any(str(k).lower().replace('_','').replace('-','') in {'sql','script','code','command','endpoint','workspaceid','entityids','authorization','password','apikey','secret','token','accesstoken'} for k in v):
                    raise HTTPException(422,'查询参数不能提供代码、连接端点、凭证或额外对象范围')
                for x in v.values():safe(x)
            elif isinstance(v,list):
                for x in v:safe(x)
        safe(parameters)
        fields=set(request['fields'])|outputs.get(step['input_from'],set())
        if parameters.get('field') and parameters['field'] not in fields:
            raise HTTPException(422,'查询字段不在本次明确读取范围')
        from .business_tools import arguments_schema
        validate(arguments_schema(cap),parameters);seen.add(step['id'])
        outputs[step['id']]=set(cap.data['output_schema']['properties']['records']['items'].get('properties',{}).get('values',{}).get('properties',{}))
    return values


def frame(result):
    records={}
    for row in result.get('evidence',[])+result.get('observation_evidence',[]):
        reference=('observation:'+row['observation_id'] if row.get('observation_id') else 'entity:'+row['entity_id'])+':'+str(row['version'])
        records[reference]={'values':copy.deepcopy(row['attributes']),'entity_ids':[row['entity_id']],'evidence_refs':[reference]}
    return list(records.values())


def builtin(handler,inputs):
    if handler=='data_fulltext_search':
        from .retrieval import fulltext
        return fulltext(inputs)
    records=inputs['records'];field=inputs['field']
    if handler=='data_keyword_filter':
        text=inputs['text'].casefold()
        selected=[r for r in records if isinstance(r['values'].get(field),str) and text in r['values'][field].casefold()]
        return {'records':selected,'summary':{'method':'registered_field_keyword_filter','scanned_records':len(records),'matched_records':len(selected)}}
    operation=inputs['operation'];values=[r['values'].get(field) for r in records]
    if operation=='count':
        if any(v is None or isinstance(v,float) and not math.isfinite(v) for v in values):
            raise HTTPException(422,'计数输入含缺项或非有限数值，不能静默跳过后当作完整统计')
    elif any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in values):
        raise HTTPException(422,'聚合输入含缺项或非有限数值，不能静默跳过后当作完整统计')
    if not values:raise HTTPException(422,'没有满足原范围的证据记录，不能生成统计事实')
    value=len(values) if operation=='count' else math.fsum(values)/len(values) if operation=='mean' else (
        math.fsum(values) if operation=='sum' else min(values) if operation=='min' else max(values) if operation=='max' else None)
    if value is None:raise HTTPException(422,'聚合操作未在内置契约中实现')
    return {'records':[{'values':{'value':value,'sample_count':len(values),'field':field,'operation':operation},
        'entity_ids':sorted({e for r in records for e in r['entity_ids']}),
        'evidence_refs':sorted({e for r in records for e in r['evidence_refs']})}],
        'summary':{'method':'registered_evidence_record_aggregate','sample_unit':'evidence_records'}}


def check_output(output,records):
    if not isinstance(output,dict) or not isinstance(output.get('records'),list) or len(output['records'])>200:
        raise HTTPException(422,'查询输出缺少有界记录')
    refs={x:{e for r in records if x in r['evidence_refs'] for e in r['entity_ids']} for r in records for x in r['evidence_refs']}
    for row in output['records']:
        if not isinstance(row,dict) or set(row)!={'values','entity_ids','evidence_refs'} or not isinstance(row['values'],dict):
            raise HTTPException(422,'派生输出结构不符合来源契约')
        if not isinstance(row['evidence_refs'],list) or not row['evidence_refs'] or not all(isinstance(x,str) and x in refs for x in row['evidence_refs']):
            raise HTTPException(422,'工具结果含未提供或无效的证据来源')
        entities={e for ref in row['evidence_refs'] for e in refs[ref]}
        if not isinstance(row['entity_ids'],list) or not row['entity_ids'] or not all(isinstance(x,str) and x in entities for x in row['entity_ids']):
            raise HTTPException(422,'工具结果含来源无法证明或越界的对象')


def execute(db,p,request,result,steps,query_id,refresh_evidence,*,cached_only=False,task=None):
    steps=validate_steps(db,p,request,steps,**managed_task_kwargs(task))
    trace=[];base=frame(result)
    if result['status']!='sufficient':return {**result,'query_tool_steps':steps,'tool_trace':[],
        'tool_blocker':'原数据不足、冲突或读取截断，未调用查询工具'}
    if not query_id:raise HTTPException(409,'查询工具需要持久请求及恢复上下文')
    row=scoped(db,DataRun,query_id,p,True)
    capabilities={c.id:c for c in choices(db,p,request['entity_ids'],**managed_task_kwargs(task))}
    from .business_tools import relation_bindings
    relation_frame=lambda value:relation_bindings(value) if value.get('relations') else None
    signature=digest({'records':base,'steps':steps,'capabilities':{s['capability_id']:capabilities[s['capability_id']].content_hash for s in steps},
        'requirement':request.get('evidence_requirement'),'relations':relation_frame(result)})
    context=row.data.get('tool_context')
    if context and context['signature']!=signature:
        return {**result,'status':'blocked_evidence','tool_trace':row.data.get('tool_trace',[]),
            'tool_blocker':'已保存工具输入的来源、范围或能力版本变化；保留原回执，请核查后新建查询'}
    if not context:
        context={'signature':signature,'started_at':now().isoformat(),'source_records':base,'steps':steps}
        row.data={**row.data,'tool_context':context,'tool_trace':[]};checkpoint(db)
    frames={'evidence':base};deadline=datetime.fromisoformat(context['started_at'])+timedelta(seconds=request.get('max_tool_seconds',60))
    for step in steps:
        checkpoint(db);row=scoped(db,DataRun,query_id,p,True)
        if row.status=='cancelled':return {**result,'status':'cancelled','tool_trace':trace}
        if row.data.get('gap_recheck'):
            from .models import DataGap
            from .data_gaps import locked_gap
            gap=locked_gap(db,p,row.data['gap_recheck']['gap_id'])
            if gap.status=='cancelled':return {**result,'status':'cancelled','tool_trace':trace}
        cap=published(db,p,step['capability_id']);validate_manifest(cap.data)
        if now()>deadline:return {**result,'status':'failed','tool_trace':trace,'tool_blocker':'本次工具执行时间预算已耗尽，请核查已保存结果后新建查询'}
        if cap.data['execution']['adapter'] in {'http','mcp','cloud_retrieval'} and not request.get('allow_external_tools'):
            return {**result,'status':'blocked_evidence','tool_trace':trace,'tool_blocker':'本次查询未批准把所选证据交外部查询服务，未调用该服务'}
        records=frames[step['input_from']]
        if not records:return {**result,'status':'insufficient','tool_trace':trace,'tool_blocker':'前序检索没有可用结果，未调用后续工具'}
        if len(records)>200:return {**result,'status':'insufficient','tool_trace':trace,'tool_blocker':'证据超过单步200条上限，请拆分范围；不以截断样本回答总体统计'}
        key='data-tool:'+query_id+':'+step['id']
        if cached_only:
            from sqlalchemy import select
            from .models import Invocation
            saved=db.scalar(select(Invocation).where(Invocation.workspace_id==p.workspace_id,Invocation.idempotency_key==key))
            if not saved or saved.status!='succeeded':return {**result,'status':'failed','tool_trace':trace,
                'tool_blocker':'恢复次数已耗尽且缺少已保存成功回执，未发出新的工具调用'}
        bound={'data_records':records,'data_question':request['question'],'data_scope':{'entity_ids':request['entity_ids'],
            'fields':request['fields'],'evidence_requirement':request.get('evidence_requirement')},'request_key':key}
        inputs={**step['parameters'],**{k:bound[v] for k,v in cap.data['execution']['input_bindings'].items()}}
        validate(cap.data['input_schema'],inputs)
        item={'step':step,'capability_id':cap.id,'capability_hash':cap.content_hash,'title':cap.data['description'],
            'release_version':cap.release_version,'input_hash':digest(inputs),'inputs':inputs,'status':'running'}
        row.data={**row.data,'tool_trace':trace+[item]};checkpoint(db)
        from .gateway import invoke
        from .goal_queries import guard
        guard(db,p,row,key)
        call=invoke(db,p,cap.id,'data',inputs,key)
        item={**item,'invocation_id':call.id,'status':call.status,'output':call.data.get('output'),'error':call.data.get('error')}
        trace.append(item);row=scoped(db,DataRun,query_id,p,True)
        row.data={**row.data,'tool_trace':copy.deepcopy(trace)};checkpoint(db)
        if now()>deadline:return {**result,'status':'failed','tool_trace':trace,'tool_blocker':'响应到达时本次工具时间预算已耗尽，回执保留，未采纳为当前答案'}
        if call.status!='succeeded':return {**result,'status':'failed','tool_trace':trace,'tool_blocker':call.data.get('error','查询工具失败，原输入与回执保留')}
        try:
            validate(cap.data['output_schema'],call.data['output'])
            check_output(call.data['output'],records)
            if call.data.get('capability_hash')!=cap.content_hash:raise HTTPException(409,'回执能力版本与当前查询不一致')
            if cap.data['execution']['adapter']=='builtin' and digest(call.data['output'])!=digest(builtin(cap.data['execution']['handler'],inputs)):
                raise HTTPException(409,'已保存的确定性计算回执与原输入不一致')
            if cap.data['execution']['adapter']=='cloud_retrieval':
                from .retrieval import semantic
                cached=semantic(db,p,cap,inputs,key,cached_only=True)
                saved=call.data['output']
                if cached['records']!=saved['records'] or any(cached['summary'][k]!=saved['summary'].get(k) for k in ['ranking','model_hash','dimensions','index_ids']):
                    raise HTTPException(409,'语义排序回执与有效向量索引不一致，不能复用')
        except HTTPException as error:return {**result,'status':'blocked_evidence','tool_trace':trace,'tool_blocker':str(error.detail)}
        db.expire_all()
        guard(db,p,scoped(db,DataRun,query_id,p))
        fresh=refresh_evidence()
        if fresh['status']!='sufficient' or frame(fresh)!=base or relation_frame(fresh)!=relation_frame(result):
            return {**result,'status':'blocked_evidence','tool_trace':trace,'tool_blocker':'工具执行期间原证据或关系依据改变，原回执保留，不能据此给出当前结论'}
        db.refresh(cap);published(db,p,cap.id)
        if cap.content_hash!=item['capability_hash']:return {**result,'status':'blocked_evidence','tool_trace':trace,'tool_blocker':'查询能力版本已变化，请重新查询'}
        frames[step['id']]=call.data['output']['records']
    result={**result,'query_tool_steps':steps,'tool_trace':trace,'derived_records':frames[steps[-1]['id']],
        'derivation_verification':'deterministic_registered_computation' if all(capabilities[s['capability_id']].data['execution']['adapter']=='builtin' for s in steps) else (
            'source_preserving_semantic_ranking' if all(capabilities[s['capability_id']].data['execution']['adapter'] in {'builtin','cloud_retrieval'} for s in steps) else 'unverified_registered_extension')}
    if not result['derived_records']:result={**result,'status':'insufficient','tool_blocker':'登记检索条件未找到结果；不能将其解释为范围外不存在对象'}
    elif result['derivation_verification']=='unverified_registered_extension':
        result={**result,'status':'needs_review','tool_blocker':'外部服务推导已保存；来源与结构核验不等于专业正确性，请核查后确认或拒绝'}
    audit(db,p,'data.tools_complete',query_id,{'step_count':len(steps),'status':result['status']})
    return result
