"""Incomplete original inputs and durable per-attempt outcomes, never state values."""
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Observation,Capability,Invocation,Event,Job,now
from .services import scoped,enqueue_event
from .observation_integrity import CHECK_ERRORS


def original(db,p,row):
    value=row.data.get('original_request')
    if not isinstance(value,dict) or digest(value)!=row.data.get('input_hash'):raise ValueError('原输入摘要无法核对')
    for key in ['source_id','source_record_id','processing_version','entity_id','business_day']:
        if value[key]!=getattr(row,key):raise ValueError('原输入对象或来源无法核对')
    clock=datetime.fromisoformat(value['observed_at']) if value.get('observed_at') else None
    if clock!=row.observed_at or (clock and clock.tzinfo is None):raise ValueError('原登记时间无法核对')
    if value['attributes']!=row.data.get('attributes') or value.get('evidence_ids',[])!=row.data.get('evidence_ids',[]) or value['extractor_id']!=row.data.get('extractor_id'):
        raise ValueError('原属性、文件或提取器无法核对')
    source=scoped(db,Capability,row.source_id,p)
    if source.kind!='source' or source.content_hash!=digest(source.data):raise ValueError('原来源摘要无法核对')
    return value,source


def job_for(db,p,row):return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='extract:'+row.id))


def issue(db,p,row):
    try:_,source=original(db,p,row);valid=True;label=source.data.get('description') or source.name
    except CHECK_ERRORS:valid=False;label='原来源待核对'
    job=job_for(db,p,row)
    return {'observation_id':row.id,'observation_version':row.version,'source_name':label,'registered_at':row.created_at.isoformat(),
        'reported_at':row.observed_at.isoformat() if row.observed_at else None,'stage':row.status,
        'input_integrity_verified':valid,'attempts':job.attempts if job else 0,
        'message':'原输入尚未处理完成，不能用上一成功登记代替这份结果' if row.status!='failed' else '处理失败，不能据此认定没有异常；上一成功登记仅供核查',
        'current_state_verified':False}


def incomplete(db,p,entity_id):
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.entity_id==entity_id,
        Observation.status.in_(['received','queued','running','failed','cancelled'])).order_by(Observation.created_at.desc(),Observation.id).limit(21)))
    from .observation_corrections import resolved,superseded
    pending=[];corrected=[]
    for row in rows[:20]:
        replacement=resolved(db,p,row) or superseded(db,p,row)
        if replacement:corrected.append({**issue(db,p,row),**replacement})
        else:pending.append(issue(db,p,row))
    return pending,corrected,len(rows)>20


def fail(db,p,row,attempt,error,kind,invocation_id=None):
    key='failure:'+row.id+('' if attempt==1 else ':attempt:'+str(attempt))
    previous=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.idempotency_key==key))
    if previous:return previous
    row.status='failed';row.version+=1
    row.data={**row.data,'processing_error':error,'failure_type':kind,'failed_invocation_id':invocation_id}
    payload={'observation_id':row.id,'entity_id':row.entity_id,'source_id':row.source_id,'attempt':attempt,
        'observation_version':row.version,'invocation_id':invocation_id,'error':error,'status':kind,'observed_at':row.observed_at.isoformat() if row.observed_at else None,
        'evidence_ids':row.data.get('evidence_ids',[]),'candidate_only':row.data.get('candidate_only',False)}
    return enqueue_event(db,p,'observation.failed',key,payload)


def extraction_receipt(db,p,row,event):
    call=db.get(Invocation,event.data.get('invocation_id')) if event.data.get('invocation_id') else None
    inputs={'attributes':row.data.get('attributes',{})}
    if row.data.get('evidence_ids'):inputs['evidence_ids']=row.data['evidence_ids']
    return bool(call and call.workspace_id==p.workspace_id and call.capability_id==row.data.get('extractor_id')
        and call.data.get('input_hash')==digest(inputs) and call.status not in {'running','succeeded'}
        and call.idempotency_key.startswith('observation:'+row.id+':attempt:')
        and event.data.get('source_id')==row.source_id and event.data.get('entity_id')==row.entity_id)


def view(db,p,row):
    current=issue(db,p,row);job=job_for(db,p,row)
    values=None
    try:request,_=original(db,p,row);values=request['attributes']
    except CHECK_ERRORS:pass
    failures=list(db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='observation.failed',
        Event.data['observation_id'].as_string()==row.id).order_by(Event.created_at,Event.id).limit(10)))
    local=False
    try:
        cap=scoped(db,Capability,row.data['extractor_id'],p);execution=cap.data['execution']
        local=cap.status=='published' and cap.content_hash==digest(cap.data) and cap.data['data_policy'].get('cloud_allowed') is False and not execution.get('side_effect')
        local=local and execution.get('adapter') in ['builtin','local_specialist','local_geospatial','local_geo_pair','local_learned_geo_pair','local_task_segmented_audio','local_segmented_audio','local_speech_activity','local_audio','local_sound_observation']
    except CHECK_ERRORS:pass
    retry_requests=row.data.get('retry_requests',[])
    journal_ok=not retry_requests or digest(retry_requests)==row.data.get('retry_requests_hash')
    from .observation_corrections import options,resolved,superseded
    correction=options(db,p,row)
    correction_result=resolved(db,p,row) or superseded(db,p,row)
    available=bool(values is not None and not row.data.get('correction_requests') and journal_ok and local and row.status=='failed' and job and job.status=='failed' and job.attempts<3)
    reason=None if available else '原输入、处理次数或本地处理授权尚不满足重试条件'
    if available:
        try:
            from .observation_candidate_dispatch import verify_candidate
            verify_candidate(db,p,row,row.data['extractor_id'],execution=True)
        except CHECK_ERRORS as error:
            available=False
            reason=str(error.detail) if isinstance(error,HTTPException) else '原输入或处理授权需核对，暂不能重新排队；原件保留'
    return {**current,'correction':correction,'correction_result':correction_result,'original_attributes':values,'retry_available':available,'retry_reason':reason,
        'retry_limit':3,'failure_history':[{'attempt':e.data.get('attempt',1),'at':e.created_at.isoformat(),'delivered':e.status=='received',
            'kind':'原处理未完成','has_extraction_receipt':extraction_receipt(db,p,row,e)} for e in failures],
        'retry_requests':retry_requests if journal_ok else [],'retry_receipts_verified':journal_ok,'cloud_calls':0,'physical_state_verified':False}
