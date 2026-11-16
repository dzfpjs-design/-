"""Append-only corrections for failed manual local numeric reports."""
import math
from fastapi import HTTPException
from pydantic import Field,StrictInt,StrictFloat
from sqlalchemy import select
from .contracts import Contract,digest,ObservationCreate
from .models import Observation,Job,Event,now
from .services import scoped,require,check_version,audit
from .observation_integrity import checked_report,CHECK_ERRORS

class Correction(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    expected_version:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)
    values:dict[str,StrictInt|StrictFloat]=Field(min_length=1,max_length=30)

def ledger(row):
    entries=row.data.get('correction_requests',[])
    if entries and digest(entries)!=row.data.get('correction_requests_hash'):
        raise HTTPException(409,'原更正关联未通过摘要核对，旧登记保留')
    return entries

def linked(db,p,parent,entry):
    child=scoped(db,Observation,entry['observation_id'],p)
    link=child.data.get('correction_of')
    if not isinstance(link,dict) or digest(link)!=child.data.get('correction_of_hash') or link!={k:entry[k] for k in entry if k!='observation_id'}:
        raise HTTPException(409,'更正登记与原失败的关联无法核对')
    if link['original_observation_id']!=parent.id or link['original_input_hash']!=parent.data['input_hash']:
        raise HTTPException(409,'原输入不能被另一份更正替代')
    for field in ['source_id','entity_id','observed_at','business_day','processing_version']:
        if getattr(child,field)!=getattr(parent,field):raise HTTPException(409,'更正登记范围或时间不一致')
    from .observation_inputs import original
    original(db,p,child)
    request=child.data['correction_request']
    if digest(request)!=link['request_hash'] or child.data['submitted_by']!=link['actor_id']:
        raise HTTPException(409,'原更正请求或提交者无法核对')
    expected={**parent.data['original_request']['attributes'],**request['values']}
    if child.data['attributes']!=expected or child.data['extractor_id']!=parent.data['extractor_id']:
        raise HTTPException(409,'更正内容或提取器无法核对')
    return child

def resolved(db,p,row):
    from .observation_inputs import original
    try:
        original(db,p,row);entries=ledger(row)
        if not entries:return None
        child=linked(db,p,row,entries[-1])
        report=checked_report(db,p,child)
        if report['review']=='rejected':return None
        delivered=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='observation.data',
            Event.idempotency_key=='data:'+child.id,Event.status=='received'))
        if not delivered:return None
        return {'original_observation_id':row.id,'correction_observation_id':child.id,'reason':entries[-1]['reason'],
            'reported_at':child.observed_at.isoformat(),'new_values':child.data['extracted']['attributes'],
            'data_received':True,'physical_state_verified':False}
    except CHECK_ERRORS:return None

def superseded(db,p,row):
    try:
        link=row.data.get('correction_of')
        if not link:return None
        parent=scoped(db,Observation,link['original_observation_id'],p)
        entries=ledger(parent);previous=next((e for e in entries if e['observation_id']==row.id),None)
        if not previous:return None
        linked(db,p,parent,previous)
        current=resolved(db,p,parent)
        if not current or current['correction_observation_id']==row.id:return None
        return {**current,'original_observation_id':row.id,'superseded_correction':True}
    except CHECK_ERRORS:return None


def options(db,p,row):
    from .observation_inputs import original
    result={'available':False,'fields':[],'limit':3,'saved':None,'message':'仅已失败的人工本地数值登记可另存更正；其它输入需回原处理入口核查'}
    try:
        original(db,p,row);entries=ledger(row)
        if row.data.get('correction_of'):
            result['message']='这份是另存的更正登记；请核查原失败输入，或按原值重试这份更正'
            return result
        if entries:
            child=linked(db,p,row,entries[-1]);result['saved']={'observation_id':child.id,'status':child.status,'resolved':bool(resolved(db,p,row))}
            rejected=False
            if child.status=='processed':
                rejected=checked_report(db,p,child)['review']=='rejected'
                result['saved']['review_rejected']=rejected
            if child.status!='failed' and not rejected:return result
        if row.status!='failed' or not row.data.get('reported_rule_contract') or len(entries)>=3:return result
        from .perception_signals import guard_rule
        guard_rule(db,p,row)
        from .models import Capability
        cap=scoped(db,Capability,row.data['extractor_id'],p)
        fields=list(dict.fromkeys(r['field'] for r in cap.data['specification']['rules'] if r['field'] in row.data['attributes']))
        result.update(available=bool(fields),fields=fields,message='另存更正值；旧输入和失败回执保留，成功归集后才采用新结果')
    except CHECK_ERRORS:pass
    return result

def create(db,p,row,body):
    require(p,'admin','operator');entries=ledger(row);request=body.model_dump();request_hash=digest(request)
    old=next((e for e in entries if e['request_id']==body.request_id),None)
    if old:
        if old['request_hash']!=request_hash or old['actor_id']!=p.user_id:raise HTTPException(409,'原更正请求不能替换内容、理由或提交者')
        return linked(db,p,row,old)
    check_version(row,body.expected_version)
    allowed=options(db,p,row)
    if not allowed['available']:raise HTTPException(409,'原登记不满足更正条件，或已有更正正在处理/已成功')
    if not set(body.values).issubset(allowed['fields']) or any(not math.isfinite(v) for v in body.values.values()):
        raise HTTPException(422,'更正只能填写原规则中的登记字段，且必须是有限数值')
    collision=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.source_id==row.source_id,
        Observation.processing_version==row.processing_version,Observation.source_record_id=='correction:'+body.request_id))
    if collision:raise HTTPException(409,'原更正编号已用于另一份登记，原记录保留，不能覆盖')
    from .api import ingest_observation_record
    value={**row.data['original_request'],'source_record_id':'correction:'+body.request_id,
        'attributes':{**row.data['attributes'],**body.values}}
    result=ingest_observation_record(ObservationCreate.model_validate(value),p,db,
        reported_contract=row.data['reported_rule_contract'],commit=False)
    child=scoped(db,Observation,result['id'],p)
    link={'request_id':body.request_id,'request_hash':request_hash,'actor_id':p.user_id,'reason':body.reason,
        'original_observation_id':row.id,'original_input_hash':row.data['input_hash'],'original_version':body.expected_version,'at':now().isoformat()}
    child.data={**child.data,'correction_of':link,'correction_of_hash':digest(link),'correction_request':request}
    entries=[*entries,{**link,'observation_id':child.id}]
    row.data={**row.data,'correction_requests':entries,'correction_requests_hash':digest(entries)};row.version+=1
    audit(db,p,'observation.correction_saved',row.id,{'correction_observation_id':child.id,'reason':body.reason,'old_input_unchanged':True})
    return child
