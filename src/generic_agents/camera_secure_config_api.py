"""Administrator-created, device-scoped camera trust and credential binding."""
import hashlib, json
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StrictBool
from .contracts import Contract
from sqlalchemy import select, text
from .models import Job, MediaGateway, Audit
from .services import scoped, require, audit
from .setup_api import P, DB
from .media import origin
from .onvif_camera_enrolment import device_url, EnrollmentError
from .camera_secure_store import reference, save, read, cancel, revoke, revoked, revocation_actor, audited, mark_audited, matching, StoreError

router=APIRouter(prefix='/api/media/camera-security')

def ensure_created_audit(db,p,ref,item):
    key=int.from_bytes(hashlib.sha256((p.workspace_id+':camera-security-audit:'+ref).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
    existing=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.created',Audit.object_id==ref))
    if existing and existing.actor_id!=item['actor_id']:raise HTTPException(409,'授权审计与原操作者不一致，请联系管理员')
    if not existing:db.add(Audit(workspace_id=p.workspace_id,actor_id=item['actor_id'],action='media.camera_security.created',object_id=ref,data={'candidate_id':item['candidate_id'],'gateway_id':item['gateway_id']}))
    db.commit()
    mark_audited(ref)

def audit_is_present(db,p,ref):
    item=read(ref)
    row=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.created',Audit.object_id==ref))
    stopped=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.revoked',Audit.object_id==ref))
    return bool(audited(ref) and row and row.actor_id==item.get('actor_id') and not stopped)

def audit_recovery_needed(db,p,ref):
    item=read(ref)
    created=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.created',Audit.object_id==ref))
    stopped=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.revoked',Audit.object_id==ref))
    return not (audited(ref) and created and created.actor_id==item.get('actor_id') and bool(stopped)==revoked(ref))

def ensure_revoked_audit(db,p,ref):
    if not revoked(ref):return
    actor_id=revocation_actor(ref)
    key=int.from_bytes(hashlib.sha256((p.workspace_id+':camera-security-revoke:'+ref).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
    existing=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.revoked',Audit.object_id==ref))
    if not existing:db.add(Audit(workspace_id=p.workspace_id,actor_id=actor_id,action='media.camera_security.revoked',object_id=ref,data={}))
    db.commit()

class CameraSecurity(Contract):
    request_id:str=Field(min_length=36,max_length=36)
    scan_id:str=Field(min_length=1,max_length=36)
    candidate_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    gateway_id:str=Field(min_length=1,max_length=36)
    username:str=Field(min_length=1,max_length=256,repr=False)
    password:str=Field(min_length=1,max_length=256,repr=False)
    certificate:str=Field(min_length=1,max_length=65536,repr=False)
    confirm_identity:StrictBool
    confirm_trust:StrictBool

@router.post('')
async def create(request:Request,p:P,db:DB):
    require(p,'admin')
    # Local HTTP is allowed for this node's development console only.
    if request.url.scheme!='https' and (not request.client or request.client.host not in {'127.0.0.1','::1'}):
        raise HTTPException(403,'请通过 HTTPS 配置摄像头凭据')
    try:
        raw=await request.body()
        if len(raw)>70000:raise ValueError()
        body=CameraSecurity.model_validate(json.loads(raw))
    except (ValueError,TypeError,UnicodeDecodeError):raise HTTPException(422,'摄像头安全配置格式无效，请核对输入') from None
    if not body.confirm_identity or not body.confirm_trust:raise HTTPException(422,'请核实设备身份并确认可信证书')
    scan=scoped(db,Job,body.scan_id,p);gateway=scoped(db,MediaGateway,body.gateway_id,p)
    if scan.kind!='onvif_discovery_scan' or scan.status!='internal' or body.candidate_id not in scan.data.get('confirmed_ids',[]):raise HTTPException(409,'请先确认发现的摄像头')
    found=[x for x in scan.data.get('devices',[]) if isinstance(x,dict) and x.get('candidate_id')==body.candidate_id]
    if len(found)!=1 or gateway.status=='disabled':raise HTTPException(409,'设备或网关状态已变化')
    try:
        device=found[0];device_url(device['service_address'],device['source_ip']);api=origin(gateway.data['api_origin'],'api')
        ref=reference(p.workspace_id,body.request_id)
        payload={'workspace_id':p.workspace_id,'actor_id':p.user_id,'candidate_id':body.candidate_id,'source_ip':device['source_ip'],'service_address':device['service_address'],
          'gateway_id':gateway.id,'gateway_origin':api,'username':body.username,'password':body.password,'certificate':body.certificate}
        state=save(ref,payload)
    except (StoreError,EnrollmentError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
    if state=='saved':ensure_created_audit(db,p,ref,payload)
    return {'request_id':body.request_id,'state':state,'reference':ref,'gateway_id':gateway.id,'candidate_id':body.candidate_id,'certificate_configured':state=='saved'}

@router.get('/requests/{request_id}')
def lookup(request_id:str,p:P,db:DB):
    require(p,'admin')
    try:
        ref=reference(p.workspace_id,request_id);item=read(ref)
    except StoreError as exc:
        if str(exc)=='未找到此摄像头授权':raise HTTPException(404,'没有找到此授权请求') from None
        raise HTTPException(409,'摄像头安全存储不可读取，请联系管理员恢复') from None
    if item.get('actor_id')!=p.user_id or item.get('workspace_id')!=p.workspace_id:raise HTTPException(404,'没有找到此授权请求')
    state='cancelled' if item.get('state')=='cancelled' else 'saved'
    if state=='saved':ensure_created_audit(db,p,ref,item);ensure_revoked_audit(db,p,ref)
    return {'request_id':request_id,'state':state,'reference':ref,'gateway_id':item.get('gateway_id'),'candidate_id':item.get('candidate_id'),'certificate_configured':state=='saved'}

class Stop(Contract):
    gateway_id:str=Field(min_length=1,max_length=36)
    candidate_id:str=Field(pattern=r'^[0-9a-f]{32}$')

@router.get('/pending-audits')
def pending_audits(scan_id:str,candidate_id:str,gateway_id:str,p:P,db:DB):
    require(p,'admin')
    scan=scoped(db,Job,scan_id,p);gateway=scoped(db,MediaGateway,gateway_id,p)
    if scan.kind!='onvif_discovery_scan' or scan.status!='internal' or candidate_id not in scan.data.get('confirmed_ids',[]):raise HTTPException(409,'请先确认发现的摄像头')
    found=[x for x in scan.data.get('devices',[]) if isinstance(x,dict) and x.get('candidate_id')==candidate_id]
    if len(found)!=1:raise HTTPException(409,'设备记录无法核对')
    try:
        refs=matching(p.workspace_id,found[0],gateway,include_revoked=True,include_unaudited=True)
        return {'records':[{'reference':ref,'gateway_id':gateway.id,'candidate_id':candidate_id,'revoked':revoked(ref)} for ref in refs if audit_recovery_needed(db,p,ref)]}
    except StoreError:raise HTTPException(409,'摄像头安全存储不可读取，请联系管理员恢复') from None

@router.post('/references/{ref}/recover-audit')
def recover_audit(ref:str,p:P,db:DB):
    require(p,'admin')
    try:
        item=read(ref)
        if item.get('workspace_id')!=p.workspace_id or item.get('state')=='cancelled':raise StoreError('未找到此授权')
        stopped=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action=='media.camera_security.revoked',Audit.object_id==ref))
        if stopped and not revoked(ref):raise HTTPException(409,'授权与停用记录不一致，请从一致的备份恢复')
        ensure_created_audit(db,p,ref,item)
        ensure_revoked_audit(db,p,ref)
    except StoreError:raise HTTPException(404,'未找到此授权') from None
    return {'reference':ref,'audit_recovered':True,'revoked':revoked(ref)}

@router.post('/requests/{request_id}/cancel')
def stop(request_id:str,body:Stop,p:P,db:DB):
    require(p,'admin')
    try:
        ref=reference(p.workspace_id,request_id)
        state=cancel(ref,p.workspace_id,p.user_id,body.gateway_id,body.candidate_id)
    except StoreError as exc:raise HTTPException(409,str(exc)) from None
    if state=='saved':ensure_created_audit(db,p,ref,read(ref))
    return {'request_id':request_id,'state':state,'reference':ref,'gateway_id':body.gateway_id,'candidate_id':body.candidate_id,'certificate_configured':state=='saved'}

@router.post('/references/{ref}/revoke')
def revoke_authorization(ref:str,p:P,db:DB):
    require(p,'admin')
    try:revoke(ref,p.workspace_id,p.user_id)
    except StoreError as exc:raise HTTPException(409,str(exc)) from None
    ensure_revoked_audit(db,p,ref)
    return {'reference':ref,'revoked':True}
