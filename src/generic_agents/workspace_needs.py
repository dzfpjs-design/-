"""Business-owned existing-record checks, never new acquisition authorization."""
from fastapi import HTTPException
from pydantic import Field,StrictInt
from typing import Literal
from sqlalchemy import select,text
from .contracts import Contract,DataRequest,digest
from .models import DataRun,DataGap,Entity,InstanceRelease,Observation,Task,Job,Event,now
from .services import scoped,require,audit,query_evidence
from .instance_api import active
from .observation_integrity import checked_report,CHECK_ERRORS
KIND='check_workspace_data_need'

class NeedCreate(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    release_id:str
    business_id:str
    entity_id:str
    purpose:str=Field(min_length=1,max_length=2000)
    fields:list[str]=Field(min_length=1,max_length=30)
    source:Literal['archive','observations']='observations'
    confirmation:Literal['confirmed','unverified_extraction']='confirmed'
    max_age_hours:StrictInt=Field(default=24,ge=1,le=720)

class NeedCheck(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    expected_version:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)

def objects(db,p,identity):
    instance,release=active(db,p,identity)
    rows=list(db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.namespace==instance.namespace).order_by(Entity.created_at).limit(101)))
    from .entity_release_compat import require_entity
    usable=[]
    for row in rows[:100]:
        try:require_entity(db,p,instance,release,row)
        except HTTPException:continue
        usable.append(row)
    return instance,release,usable,len(rows)>100

def memory_kwargs(entity,release):
    """Definition compatibility does not promote another package's Memory."""
    return {'include_trusted_cases':False} if entity.schema_capability_id!=release.package_id else {}

def field_labels(entity,release):
    kind=next((x for x in release.data['snapshot']['object_types'] if x['key']==entity.entity_type),None)
    return {f['key']:f['label'] for f in (kind['fields'] if kind else [])}

def catalog(db,p,entity,release=None):
    kind=next((x for x in (release.data['snapshot']['object_types'] if release else []) if x['key']==entity.entity_type),None)
    fields=[f['key'] for f in kind['fields']] if kind else list(entity.data.keys())
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.entity_id==entity.id,
        Observation.status=='processed',Observation.data['candidate_only'].as_boolean().is_distinct_from(True)).order_by(Observation.created_at.desc()).limit(31)))
    for row in rows[:30]:
        try:fields.extend(checked_report(db,p,row)['attributes'])
        except CHECK_ERRORS:pass
    return list(dict.fromkeys(fields))[:30]

def context(row):
    value=row.data.get('workspace_need_context')
    if not isinstance(value,dict) or value.get('schema')!='workspace-existing-need-v1' or value.get('existing_records_only') is not True or digest(value)!=row.data.get('workspace_need_context_hash') or digest(row.data.get('request'))!=value.get('query_request_hash'):
        raise HTTPException(409,'原资料核查范围与请求摘要无法核对，历史保留')
    return value

def guard(db,p,row):
    c=context(row);instance,release=active(db,p,c['instance_id']);entity=scoped(db,Entity,c['entity_id'],p)
    if release.id!=c['release_id'] or c['business_id'] not in instance.data.get('enabled_business_ids',[]):
        raise HTTPException(409,'实例、业务或对象范围已变化，原核查保留，请核对新配置')
    from .entity_release_compat import require_entity
    definition=require_entity(db,p,instance,release,entity)
    if ('object_definition_hash' in c and c['object_definition_hash']!=digest(definition)
            or 'object_definition_hash' not in c and entity.schema_capability_id!=release.package_id):
        raise HTTPException(409,'原对象定义或来源已变化，旧核查不能取得新的跨版本授权')
    from .goal_queries import guard as goal_guard
    goal_guard(db,p,row)
    return c,instance,entity

def queue(db,p,row,key):
    c=context(row)
    from .data_gaps import queue as add_job
    add_job(db,p.workspace_id,KIND,'workspace-need:'+row.id+':'+key,
        {'query_id':row.id,'actor_id':p.user_id,'context_hash':digest(c),'existing_records_only':True})

def create(db,p,identity,body):
    require(p,'admin','operator');instance,release,entities,_=objects(db,p,identity)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace_need':p.workspace_id,'request_id':body.request_id})[:16],16)-(1<<63)})
    old=db.scalar(select(DataRun).where(DataRun.workspace_id==p.workspace_id,DataRun.data['workspace_need_context']['request_id'].as_string()==body.request_id))
    if old:
        c=context(old)
        if c['body_hash']!=digest(body.model_dump()) or c['actor_id']!=p.user_id or c['instance_id']!=identity:raise HTTPException(409,'原请求不能替换内容、实例或提交者')
        return old
    entity=scoped(db,Entity,body.entity_id,p)
    from .entity_release_compat import require_entity
    definition=require_entity(db,p,instance,release,entity)
    if not entity or release.id!=body.release_id or body.business_id not in instance.data.get('enabled_business_ids',[]):raise HTTPException(409,'请选择当前实例已启用业务及其对象')
    if len(body.fields)!=len(set(body.fields)) or not set(body.fields).issubset(catalog(db,p,entity,release)):raise HTTPException(422,'请选择当前对象已定义或已保存登记的字段，不能猜造字段')
    from .data_requirements import prepare_request
    request=prepare_request(db,p,DataRequest(question=body.purpose,entity_ids=[entity.id],fields=body.fields,mode='structured',
        business_timezone='Asia/Shanghai',evidence_requirement={'source':body.source,'minimum_verification':'recorded' if body.source=='archive' else body.confirmation,
        'time':{'mode':'archive'} if body.source=='archive' else {'mode':'latest','max_age_seconds':body.max_age_hours*3600}}).model_dump())
    row=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=body.purpose,status='queued',data={'request':request});db.add(row);db.flush()
    from .agents import data_agent
    state=data_agent(db,p,request,row.id,cached_only=True,learning=False,**memory_kwargs(entity,release))
    kind=next((x for x in release.data['snapshot']['object_types'] if x['key']==entity.entity_type),None)
    object_name=next((str(entity.data[f['key']]) for f in (kind['fields'] if kind else []) if f.get('required') and f.get('kind')=='文本' and entity.data.get(f['key'])),'未命名业务对象')
    business_name=next(x['name'] for x in release.data['snapshot']['businesses'] if x['id']==body.business_id)
    from .workspace_need_request_recovery_v248 import creation_metadata
    c={**creation_metadata(),'field_labels':{f:field_labels(entity,release).get(f,f) for f in body.fields},'object_name':object_name,'business_name':business_name,'schema':'workspace-existing-need-v1',
        'instance_id':identity,'release_id':release.id,'business_id':body.business_id,
        'entity_id':entity.id,'actor_id':p.user_id,'request_id':body.request_id,'body_hash':digest(body.model_dump()),
        'query_request_hash':digest(state['request']),'existing_records_only':True,'object_definition_hash':digest(definition)}
    row.status=state['status'];row.data={**state,'workspace_need_context':c,'workspace_need_context_hash':digest(c)}
    ensure_gap(db,p,row,state)
    for identifier in row.data.get('data_gap_ids',[]):
        gap=scoped(db,DataGap,identifier,p)
        if gap.query_id!=row.id or gap.entity_id!=entity.id:raise HTTPException(409,'生成的缺口与原核查范围不一致')
        marker={'query_id':row.id,'context_hash':digest(c),'gap_request_hash':digest(gap.data['request'])}
        gap.data={**gap.data,'workspace_existing_check':marker,'workspace_existing_check_hash':digest(marker)}
    queue(db,p,row,'initial');audit(db,p,'workspace.need_created',row.id,{'existing_records_only':True,'field_count':len(body.fields)})
    from .workspace_need_request_recovery_v248 import save_original
    save_original(db,p,row,body)
    return row

def ensure_gap(db,p,row,state):
    if state['status'] not in {'insufficient','conflict'} or row.data.get('data_gap_ids'):return
    c=context(row)
    gap=DataGap(workspace_id=p.workspace_id,query_id=row.id,entity_id=c['entity_id'],data={
        'actor_id':row.owner_id,'request':row.data['request'],'missing_fields':row.data['request']['fields'],
        'reason':'原资料要求尚未满足，需要核查已有记录','original_status':state['status'],'evidence_refs':[],'detected_at':now().isoformat()})
    db.add(gap);db.flush()
    from .services import enqueue_event
    event=enqueue_event(db,p,'data.gap','data-gap:'+gap.id,{'gap_id':gap.id,'query_id':row.id,'entity_id':gap.entity_id,'actor_id':row.owner_id})
    marker={'query_id':row.id,'context_hash':digest(c),'gap_request_hash':digest(gap.data['request'])}
    gap.data={**gap.data,'event_id':event.id,'workspace_existing_check':marker,'workspace_existing_check_hash':digest(marker)}
    row.data={**row.data,'data_gap_ids':[gap.id]}
    from .data_gaps import record
    record(db,p,gap,'detected',reason=gap.data['reason'])

def scoped_gap(db,p,row,identifier):
    gap=scoped(db,DataGap,identifier,p,True);marker=gap.data.get('workspace_existing_check')
    expected={'query_id':row.id,'context_hash':digest(context(row)),'gap_request_hash':digest(gap.data['request'])}
    if marker!=expected or digest(marker)!=gap.data.get('workspace_existing_check_hash') or gap.query_id!=row.id or gap.entity_id!=context(row)['entity_id']:
        raise HTTPException(409,'原缺口关联或需求被改变，不能据此协调')
    return gap

def coordinate(db,p,gap):
    from .services import enqueue_event
    from .data_gaps import coordinate as deliver
    event=enqueue_event(db,p,'data.gap_coordination','gap-coordination:'+gap.id,
        {'gap_id':gap.id,'entity_id':gap.entity_id,'query_id':gap.query_id,'actor_id':gap.data['actor_id']})
    if event.status=='pending_delivery':
        if not deliver(db,event):raise HTTPException(409,'业务协调尚未接收原缺口')
        event.status='received';event.received_at=now();event.attempts+=1

def close_coordination(db,p,gap):
    identifier=gap.data.get('business_task_id')
    if not identifier:return
    task=scoped(db,Task,identifier,p,True)
    if task.status=='draft' and task.data.get('trigger')=='data.gap_coordination' and task.data.get('data_gap_id')==gap.id:
        task.status='coordination_completed';task.version+=1
        task.data={**task.data,'coordination_resolution':{'gap_id':gap.id,'query_id':gap.data.get('latest_recheck_query_id'),
            'at':now().isoformat(),'reason':'本次资料缺口已满足原要求，未确认其它业务目标或现场状态'}}

def reopen_coordination(db,p,gap):
    identifier=gap.data.get('business_task_id')
    if not identifier:return
    task=scoped(db,Task,identifier,p,True)
    if task.status=='coordination_completed' and task.data.get('trigger')=='data.gap_coordination' and task.data.get('data_gap_id')==gap.id:
        task.status='draft';task.version+=1
        task.data={**task.data,'coordination_reopened_at':now().isoformat(),'coordination_reopened_reason':'按原资料要求再次核对后仍缺有效记录；保留此前满足要求的历史'}

def process(db,p,job):
    require(p,'admin','operator');row=scoped(db,DataRun,job.data['query_id'],p,True);c,_,entity=guard(db,p,row)
    if job.data.get('context_hash')!=digest(c) or job.data.get('existing_records_only') is not True:raise HTTPException(409,'后台核对请求不属于保存的只读范围')
    from .data_gaps import recheck,record,receipts_ready
    from .agents import data_agent
    release=scoped(db,InstanceRelease,c['release_id'],p)
    current=data_agent(db,p,row.data['request'],row.id,cached_only=True,emit_gaps=False,learning=False,**memory_kwargs(entity,release))
    ensure_gap(db,p,row,current)
    gaps=[]
    for identifier in row.data.get('data_gap_ids',[]):
        gap=scoped_gap(db,p,row,identifier)
        if gap.status=='resolved' and (current['status']!='sufficient' or not receipts_ready(db,p,current)):
            gap.status='received';gap.version+=1;reopen_coordination(db,p,gap)
            record(db,p,gap,'reopened',reason='按原资料要求再次核对未满足；此前完成记录保留')
        if gap.status=='pending_delivery':
            event=scoped(db,Event,gap.data['event_id'],p,True)
            if event.kind!='data.gap' or event.data.get('gap_id')!=gap.id or event.data.get('query_id')!=row.id or event.data.get('entity_id')!=gap.entity_id:
                raise HTTPException(409,'原缺口送达回执不匹配')
            gap.status='received';gap.version+=1;event.status='received';event.received_at=now();event.attempts+=1
            record(db,p,gap,'received',reason='业务工作区仅核对已存资料，未授权新采集')
        gap=recheck(db,p,gap.id,'workspace_existing_check',existing_only=True)
        if gap.status=='resolved':close_coordination(db,p,gap)
        elif gap.status not in {'cancelled','awaiting_collection'}:
            if gap.status!='needs_review':gap.status='needs_existing_evidence';gap.version+=1
            gap.data={**gap.data,'decision_reason':gap.data.get('decision_reason') or '原需求仍缺有效记录，本次仅核对已存资料，未启动新设备动作'}
            coordinate(db,p,gap)
        gaps.append({'gap_id':gap.id,'status':gap.status,'version':gap.version})
    result={'checked_at':now().isoformat(),'gaps':gaps,'existing_records_only':True,'device_actions':0,'cloud_calls':0,'parent_goal_completed':False,'result_status':current['status']}
    row=scoped(db,DataRun,row.id,p,True);row.data={**row.data,'workspace_check':result,'workspace_check_hash':digest(result)}
    job.data={**job.data,'need_result':result,'need_result_hash':digest(result)}

def request_check(db,p,row,body):
    require(p,'admin','operator');guard(db,p,row)
    entries=row.data.get('workspace_check_requests',[])
    if entries and digest(entries)!=row.data.get('workspace_check_requests_hash'):raise HTTPException(409,'原核对请求回执无法核实')
    old=next((e for e in entries if e['request_id']==body.request_id),None)
    if old:
        if old['body_hash']!=digest(body.model_dump()) or old['actor_id']!=p.user_id:raise HTTPException(409,'原核对请求不能换条件或提交者')
        return
    from .services import check_version
    check_version(row,body.expected_version)
    if len(entries)>=50:raise HTTPException(409,'已达到本次核查请求上限，原记录保留')
    entries=[*entries,{'request_id':body.request_id,'body_hash':digest(body.model_dump()),'actor_id':p.user_id,'reason':body.reason,'at':now().isoformat()}]
    row.data={**row.data,'workspace_check_requests':entries,'workspace_check_requests_hash':digest(entries)};row.version+=1
    queue(db,p,row,body.request_id)

def view(db,p,row,detail=False):
    c=context(row);valid=True
    try:_,instance,entity=guard(db,p,row)
    except HTTPException:
        valid=False;instance=None;entity=scoped(db,Entity,c['entity_id'],p)
    check=row.data.get('workspace_check')
    if check and digest(check)!=row.data.get('workspace_check_hash'):raise HTTPException(409,'原后台核对回执无法核实')
    gaps=[scoped_gap(db,p,row,k) for k in row.data.get('data_gap_ids',[])]
    latest=gaps[0] if gaps else None
    result={'id':row.id,'version':row.version,'entity_id':entity.id,'object_name':c['object_name'],'business_name':c['business_name'],
        'field_labels':c['field_labels'],'purpose':row.question,'fields':row.data['request']['fields'],'requirement':row.data['request'].get('evidence_requirement'),
        'status':latest.status if latest else check.get('result_status',row.status) if check else row.status,'scope_valid':valid,'business_received':bool(latest and latest.data.get('business_received_at')),
        'reason':latest.data.get('resolution_reason') or latest.data.get('decision_reason') if latest else None,
        'background':check,'created_at':row.created_at.isoformat(),'existing_records_only':True,'parent_goal_completed':False}
    # Delivery and coordination status remains the existing public `status`.
    # Keep the saved query and latest read-only evidence check visible beside it.
    result['query_status']=row.status
    result['result_status']=check.get('result_status',row.status) if check else row.status
    clarifications=row.data['request'].get('clarifications') or []
    result['clarifications']=[value for value in clarifications if type(value) is str and value]
    result['error']=row.data.get('error') if type(row.data.get('error')) is str and row.data['error'] else None
    if not result['reason']:
        result['reason']='；'.join(result['clarifications']) or result['error']
    from .workspace_need_copy_v246 import descriptor
    result['copy_descriptor']=descriptor(db,p,row,c,entity)
    if detail and not valid:
        result['records']=[]
        result['reason']='原实例或业务范围已改变；保留历史核对，不读取新范围证据'
    elif detail:
        release=scoped(db,InstanceRelease,c['release_id'],p)
        req=row.data['request'].get('evidence_requirement')
        if req and req['source']!='archive':
            data=query_evidence(db,p,[entity.id],row.data['request']['fields'],learning=False,
                requirement={**req,'minimum_verification':'unverified_extraction'},fixed_window=row.data['request'].get('resolved_time'),**memory_kwargs(entity,release))
            records=[]
            for item in data.get('observation_evidence',[])[:10]:
                obs=scoped(db,Observation,item['observation_id'],p)
                try:
                    report=checked_report(db,p,obs)
                    history=obs.data.get('verification_history',[])
                    if obs.data.get('verification_history_hash') and digest(history)!=obs.data['verification_history_hash']:raise ValueError('原核查历史摘要不一致')
                    review=obs.data.get('human_verification') if report['review'] in {'confirmed','rejected'} else None
                    history=[{k:r.get(k) for k in ('decision','reason','reviewed_at','physical_state_verified')} for r in history]
                    from .mqtt_unit_evidence_v342 import KEY as MQTT_UNIT_KEY
                    units=obs.data.get(MQTT_UNIT_KEY,{}).get('fields',{}) if obs.data.get('mqtt_receipt_id') else {}
                    records.append({'observation_id':obs.id,'version':obs.version,'source_name':report['source_name'],'original_values':obs.data.get('original_request',{}).get('attributes'),
                        'extracted_values':report['attributes'],'reported_at':obs.observed_at.isoformat(),'review':report['review'],'review_receipt':{k:review.get(k) for k in ('decision','reason','reviewed_at','physical_state_verified')} if review else None,
                        'measurement_units':{key:units[key] for key in row.data['request']['fields'] if key in units},
                        'review_history':history[-10:],'review_history_truncated':len(history)>10,
                        'review_input_hash':digest({'extracted':obs.data['extracted'],'evidence_ids':obs.data.get('evidence_ids',[]),'invocation_id':obs.data['invocation_id']}),'physical_state_verified':False})
                except CHECK_ERRORS:continue
            result['records']=records
        else:
            data=query_evidence(db,p,[entity.id],row.data['request']['fields'],learning=False,requirement=req,fixed_window=row.data['request'].get('resolved_time'),**memory_kwargs(entity,release))
            result['records']=[]
            result['archive_evidence']=[{'fields':x.get('attributes',{}),'version':x.get('version'),'observed_at':x.get('observed_at')} for x in data.get('evidence',[])[:10]]
    return result
