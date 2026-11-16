"""Explicit preview and one-use consent for native sensor business analysis."""
from uuid import UUID
from fastapi import APIRouter,HTTPException,Query
from pydantic import Field,StrictBool,StrictInt
from sqlalchemy import select
from .contracts import Contract,digest
from .models import Job,Capability
from .services import audit
from .setup_api import P,DB
from .ftps_gateway_api import _lock
from . import sensor_native_packet as packets
from . import sensor_native_analysis as svc
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_store_v302 as advice_store

router=APIRouter(prefix='/api/sensor-native')
class Preview(Contract):
    request_id:UUID
    packet_id:UUID
    model_id:UUID
    max_output_tokens:StrictInt=Field(ge=128,le=8192)
    max_cost_fen:StrictInt=Field(ge=1,le=5000)
class Send(Contract):
    request_id:UUID
    preview_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    consent:StrictBool
    acknowledge_prior_uncertain:StrictBool=False
class Price(Contract):
    request_id:UUID
    model_id:UUID
    reason:str=Field(min_length=1,max_length=2000)

def find(db,p,key):return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
def same(row,p,task_id):
    if row.data.get('actor_id')!=p.user_id or row.data.get('task_id')!=task_id:raise HTTPException(409,'原请求属于其他任务或账号')

def outcome(row,p,task_id):
    same(row,p,task_id)
    if row.kind==svc.STOP:return {'state':'cancelled'}
    if row.kind!=svc.ANALYSIS:raise HTTPException(409,'原请求类型无法核对')
    result=svc.public_analysis(row);return {'state':result['state'],'analysis':result}

def uncertain(db,p,identity):
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==svc.ANALYSIS,
        Job.data['task_id'].as_string()==identity,Job.data['actor_id'].as_string()==p.user_id,
        Job.data['state'].as_string().in_(['dispatching','response_unknown']))))
    return sorted(row.data['request_id'] for row in rows if svc.public_analysis(row)['state']=='response_unknown')

@router.get('/tasks/{task_id}/analyses/context')
def context(task_id:UUID,p:P,db:DB,page:int=Query(default=1,ge=1,le=10000)):
    identity=str(task_id);scope=packets.history_access(db,p,identity,allow_admin_read=True)
    if scope.owner_id!=p.user_id:
        return {'models':[],'analyses':[],'page':page,'has_more':False,'cloud_analysis_supported':True,'read_only':True}
    task,_,_=svc._task(db,p,identity);models=[]
    rows=db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.kind=='foundation_model',Capability.status=='published').order_by(Capability.created_at.desc()).limit(100)).all()
    for cap in rows:
        try:svc.model(db,p,cap.id)
        except HTTPException:continue
        try:
            value=prices.current(db,p,advice_store._pin(cap))['price'];available=True
            price={'source':value['official_original']['url'],'checked_at':value['checked_at'],'expires_at':value['expires_at'],'currency':'CNY',
                'input_rate_fen_per_million':value['input_cache_miss_minor_per_million'],'output_rate_fen_per_million':value['output_minor_per_million']}
        except HTTPException:available=False;price=None
        models.append({'id':cap.id,'name':cap.name,'provider':'deepseek','model':cap.data['execution']['model'],'version':cap.release_version,'price_available':available,'price':price,'max_output_tokens':cap.data['execution']['max_tokens']})
    history=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==svc.ANALYSIS,Job.data['task_id'].as_string()==identity,Job.data['actor_id'].as_string()==p.user_id).order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*50).limit(51)))
    return {'models':models,'analyses':[svc.public_analysis(x) for x in history[:50]],'page':page,'has_more':len(history)>50,'cloud_analysis_supported':True,'read_only':False}

@router.post('/tasks/{task_id}/analyses/price')
def register_price(task_id:UUID,body:Price,p:P,db:DB):
    task,_,_=svc._task(db,p,str(task_id),lock=True)
    # Price registration needs a write lock; acquire it directly instead of
    # upgrading a shared model lock while another registration is waiting.
    db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.id==str(body.model_id)).with_for_update().execution_options(populate_existing=True))
    cap=svc.model(db,p,str(body.model_id))
    result=prices.authorize(db,p,{'request_id':str(body.request_id),'model_pin':advice_store._pin(cap),'validity_seconds':3600,'reason':body.reason})
    db.commit();price=result['price']
    return {'model_id':cap.id,'source':price['official_original']['url'],'checked_at':price['checked_at'],'expires_at':price['expires_at'],
        'currency':'CNY','input_rate_fen_per_million':price['input_cache_miss_minor_per_million'],
        'output_rate_fen_per_million':price['output_minor_per_million'],'provider_price_locked':False}

@router.post('/tasks/{task_id}/analyses/preview')
def preview(task_id:UUID,body:Preview,p:P,db:DB):
    identity=str(task_id);rid=str(body.request_id);key=svc.preview_key(rid);_lock(db,p.workspace_id,key)
    task,_,_=svc._task(db,p,identity,lock=True);fingerprint=digest({'actor_id':p.user_id,'task_id':identity,**body.model_dump(mode='json')})
    original=find(db,p,key)
    if original:
        same(original,p,identity)
        if original.kind!=svc.PREVIEW or original.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原预览请求内容不一致')
        return svc.public_preview(original)
    packet=svc._packet(db,p,task,str(body.packet_id));cap=svc.model(db,p,str(body.model_id))
    snapshot=svc.prepare(db,p,task,packet,cap,body.max_output_tokens,body.max_cost_fen)
    snapshot['uncertain_request_ids']=uncertain(db,p,identity)
    row=Job(workspace_id=p.workspace_id,kind=svc.PREVIEW,status='internal',idempotency_key=key,data={'task_id':identity,'actor_id':p.user_id,'fingerprint':fingerprint,'snapshot':snapshot,'snapshot_hash':digest(snapshot),'used_by':None})
    db.add(row);db.flush();audit(db,p,'sensor.native.analysis_preview',task.id,{'preview_id':row.id,'snapshot_hash':row.data['snapshot_hash']});db.commit();return svc.public_preview(row)

@router.get('/tasks/{task_id}/analyses/previews/requests/{request_id}')
def preview_lookup(task_id:UUID,request_id:UUID,p:P,db:DB):
    identity=str(task_id);packets.history_access(db,p,identity);row=find(db,p,svc.preview_key(str(request_id)))
    if not row:return {'state':'not_found'}
    same(row,p,identity)
    if row.kind!=svc.PREVIEW or digest(row.data.get('snapshot'))!=row.data.get('snapshot_hash'):raise HTTPException(409,'原预览无法核对')
    return {'state':'saved','preview':svc.public_preview(row)}

@router.post('/tasks/{task_id}/analyses/{preview_id}/send')
def send(task_id:UUID,preview_id:UUID,body:Send,p:P,db:DB):
    if not body.consent:raise HTTPException(422,'请先明确同意将预览数据发送至 DeepSeek')
    identity=str(task_id);rid=str(body.request_id);key=svc.send_key(rid);_lock(db,p.workspace_id,key)
    task,_,_=svc._task(db,p,identity,lock=True)
    original=find(db,p,key);fingerprint=digest({'actor_id':p.user_id,'task_id':identity,'preview_id':str(preview_id),**body.model_dump(mode='json')})
    if original:
        same(original,p,identity)
        if original.kind==svc.STOP:raise HTTPException(409,'原发送请求已安全停止')
        if original.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原发送请求与当前内容不一致')
        return outcome(original,p,identity)
    prior=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==svc.ANALYSIS,
        Job.data['task_id'].as_string()==identity,Job.data['actor_id'].as_string()==p.user_id,
        Job.data['state'].as_string().in_(['queued','dispatching','response_saved','response_unknown']))))
    unknown=False
    for prior_row in prior:
        state=svc.public_analysis(prior_row)['state']
        if state in {'queued','dispatching','response_saved'}:raise HTTPException(409,'已有分析仍在处理，请先核对原请求')
        if state=='response_unknown':unknown=True
    if unknown and not body.acknowledge_prior_uncertain:
        raise HTTPException(409,'前次分析结果或费用未知；再次发送可能重复收费，请明确确认风险')
    preview=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(preview_id),Job.kind==svc.PREVIEW).with_for_update().execution_options(populate_existing=True))
    if not preview:raise HTTPException(404,'未找到原预览')
    same(preview,p,identity);d=preview.data
    if d.get('snapshot_hash')!=body.preview_hash or digest(d.get('snapshot'))!=body.preview_hash:raise HTTPException(409,'预览摘要无法核对')
    if d.get('used_by'):raise HTTPException(409,'此预览已经用于一次发送，请重新预览并确认')
    if d['snapshot'].get('uncertain_request_ids',[])!=uncertain(db,p,identity):raise HTTPException(409,'前次发送状态已变化，请重新预览风险')
    svc.check_current(db,p,task,d['snapshot'])
    value={'task_id':identity,'actor_id':p.user_id,'request_id':rid,'preview_id':preview.id,'fingerprint':fingerprint,'state':'queued','packet_id':d['snapshot']['packet_id'],'model_id':d['snapshot']['model_pin']['id'],'snapshot':d['snapshot'],'snapshot_hash':body.preview_hash,'result':None,'receipt':None,'acknowledge_prior_uncertain':body.acknowledge_prior_uncertain}
    row=Job(workspace_id=p.workspace_id,kind=svc.ANALYSIS,status='internal',idempotency_key=key,data=value);db.add(row);db.flush()
    preview.data={**d,'used_by':rid};preview.version+=1;audit(db,p,'sensor.native.analysis_consented',task.id,{'analysis_id':row.id,'preview_id':preview.id,'snapshot_hash':body.preview_hash});db.commit()
    return outcome(row,p,identity)

@router.get('/tasks/{task_id}/analyses/requests/{request_id}')
def lookup(task_id:UUID,request_id:UUID,p:P,db:DB):
    identity=str(task_id);packets.history_access(db,p,identity);row=find(db,p,svc.send_key(str(request_id)))
    return outcome(row,p,identity) if row else {'state':'not_found'}

@router.get('/tasks/{task_id}/analyses/history')
def history(task_id:UUID,p:P,db:DB,page:int=Query(default=1,ge=1,le=10000)):
    identity=str(task_id);task=packets.history_access(db,p,identity,allow_admin_read=True)
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==svc.ANALYSIS,Job.data['task_id'].as_string()==identity,Job.data['actor_id'].as_string()==task.owner_id).order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*50).limit(51)))
    return {'analyses':[svc.public_analysis(row) for row in rows[:50]],'page':page,'has_more':len(rows)>50}

@router.post('/tasks/{task_id}/analyses/requests/{request_id}/cancel')
def cancel(task_id:UUID,request_id:UUID,p:P,db:DB):
    identity=str(task_id);rid=str(request_id);key=svc.send_key(rid);_lock(db,p.workspace_id,key)
    packets.history_access(db,p,identity);row=find(db,p,key)
    if row:
        same(row,p,identity)
        if row.kind==svc.ANALYSIS and row.data.get('state')=='queued':
            row=db.scalar(select(Job).where(Job.id==row.id).with_for_update().execution_options(populate_existing=True))
            if row.data.get('state')=='queued':row.data={**row.data,'state':'cancelled'};row.version+=1;audit(db,p,'sensor.native.analysis_cancelled',identity,{'analysis_id':row.id});db.commit()
        return outcome(row,p,identity)
    row=Job(workspace_id=p.workspace_id,kind=svc.STOP,status='internal',idempotency_key=key,data={'task_id':identity,'actor_id':p.user_id});db.add(row);db.flush();audit(db,p,'sensor.native.analysis_stopped',identity,{'request_id':rid});db.commit();return {'state':'cancelled'}

@router.get('/tasks/{task_id}/analyses/{analysis_id}')
def detail(task_id:UUID,analysis_id:UUID,p:P,db:DB):
    identity=str(task_id);task=packets.history_access(db,p,identity,allow_admin_read=True)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(analysis_id),Job.kind==svc.ANALYSIS))
    if not row:raise HTTPException(404,'未找到原分析')
    if row.data.get('task_id')!=identity or row.data.get('actor_id')!=task.owner_id:raise HTTPException(404,'未找到原分析')
    return {'analysis':svc.public_analysis(row)}

@router.get('/tasks/{task_id}/analyses/{analysis_id}/original')
def original(task_id:UUID,analysis_id:UUID,p:P,db:DB):
    import base64
    identity=str(task_id);task=packets.history_access(db,p,identity,allow_admin_read=True)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(analysis_id),Job.kind==svc.ANALYSIS))
    if not row or row.data.get('task_id')!=identity or row.data.get('actor_id')!=task.owner_id:raise HTTPException(404,'未找到原分析')
    capture=row.data.get('capture')
    if not isinstance(capture,dict) or not isinstance(capture.get('raw_base64'),str):raise HTTPException(409,'尚无可核对的原首答')
    try:raw=base64.b64decode(capture['raw_base64'],validate=True)
    except (TypeError,ValueError):raise HTTPException(409,'原首答完整性无法核对')
    from hashlib import sha256
    if sha256(raw).hexdigest()!=capture.get('raw_sha256') or digest(capture.get('receipt'))!=capture.get('receipt_hash'):raise HTTPException(409,'原首答完整性无法核对')
    return {'raw_base64':capture['raw_base64'],'sha256':capture['raw_sha256'],'encoding':'base64','media_type':'application/json',
        'notice':'这是服务商返回的未经核实原文，不代表现场结论；不得作为操作指令执行。'}
