"""Explicit, recoverable enrollment of one confirmed ONVIF candidate.

The operation writes an on-demand MediaMTX path, but does not automatically
authorize the path for a business instance or claim that live video works.
"""
import hashlib
import json
import os
import re
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool
from sqlalchemy import select, text
from .config import settings
from .connectors import headers
from .contracts import Contract, digest
from .models import Job, MediaGateway
from .services import audit, require, scoped
from .setup_api import DB, P
from .media import origin
from .onvif_camera_enrolment import EnrollmentError, REF, grant, stream_uri, configure_gateway, gateway_state

router=APIRouter(prefix='/api/media/onvif-enrollments')
KIND='onvif_camera_enrollment'

class Enrollment(Contract):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    scan_id:str=Field(min_length=1,max_length=36)
    candidate_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    gateway_id:str=Field(min_length=1,max_length=36)
    gateway_version:int=Field(ge=1)
    credential_ref:str=Field(pattern=r'^GA_ONVIF_CAMERA_SECRET_[A-Z0-9_]+$')
    confirm_onvif_read:StrictBool
    confirm_gateway_write_and_credential_storage:StrictBool

def _lock(db,p,request_id):
    v=int.from_bytes(hashlib.sha256((p.workspace_id+':onvif-enrollment:'+request_id).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':v})

def _key(request_id):return 'onvif-enrollment:'+request_id
def _receipt(db,p,request_id):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_key(request_id)))
def _view(row):
    if row.kind!=KIND or row.status!='internal' or row.data.get('state') not in ('pending','configured','cancelled'):
        raise HTTPException(409,'摄像头接入回执无法核对')
    d=row.data
    return {'request_id':d['request_id'],'state':d['state'],'gateway_id':d['gateway_id'],
            'scan_id':d['scan_id'],'candidate_id':d['candidate_id'],'gateway_path':d['gateway_path'],
            'read_authorized':False,'video_verified':False}

def _inputs(db,p,body,lock_gateway=False,gateway_generation=None):
    scan=scoped(db,Job,body.scan_id,p)
    if scan.kind!='onvif_discovery_scan' or scan.status!='internal' or not isinstance(scan.data,dict):
        raise HTTPException(409,'原发现记录无法核对')
    candidates=[x for x in scan.data.get('devices',[]) if isinstance(x,dict) and x.get('candidate_id')==body.candidate_id]
    if len(candidates)!=1 or body.candidate_id not in scan.data.get('confirmed_ids',[]):
        raise HTTPException(409,'请先核对并确认发现的摄像头')
    device=candidates[0]
    gateway=scoped(db,MediaGateway,body.gateway_id,p,lock_gateway)
    if gateway.status=='disabled' or (gateway_generation is None and gateway.version!=body.gateway_version) or (gateway_generation is not None and gateway.data.get('generation')!=gateway_generation):
        raise HTTPException(409,'视频网关状态已变化，请重新核对')
    api=origin(gateway.data.get('api_origin',''),'api')
    if body.credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_UI_'):
        from .camera_secure_config_api import audit_is_present
        try:
            if not audit_is_present(db,p,body.credential_ref):raise HTTPException(409,'设备授权审计待恢复，请联系管理员核对')
        except ValueError:raise HTTPException(409,'摄像头安全存储不可读取，请联系管理员恢复') from None
    try:grant(p.workspace_id,device['source_ip'],device['service_address'],body.credential_ref,gateway.id,api)
    except (KeyError,EnrollmentError) as exc:raise HTTPException(403,str(exc) if isinstance(exc,EnrollmentError) else '设备记录不完整') from None
    return device,gateway,api

def _configured_input(row):
    try:return Enrollment.model_validate({key:row.data[key] for key in Enrollment.model_fields})
    except (KeyError,ValueError,TypeError):raise HTTPException(409,'原接入请求不完整，无法安全核对') from None

def _gateway_auth():
    ref=settings().media_api_secret_ref
    return headers({'secret_ref':ref}) if ref else {}

@router.get('/options')
def options(scan_id:str,candidate_id:str,gateway_id:str,p:P,db:DB):
    require(p,'admin')
    scan=scoped(db,Job,scan_id,p);gateway=scoped(db,MediaGateway,gateway_id,p)
    if scan.kind!='onvif_discovery_scan' or scan.status!='internal' or candidate_id not in scan.data.get('confirmed_ids',[]):
        raise HTTPException(409,'请先确认发现的摄像头')
    found=[x for x in scan.data.get('devices',[]) if isinstance(x,dict) and x.get('candidate_id')==candidate_id]
    if len(found)!=1:raise HTTPException(409,'设备记录无法核对')
    try:
        config=json.loads(os.environ.get('GA_ONVIF_ENROLLMENT_GRANTS','{}'));entries=config.get(p.workspace_id,[])
        if not isinstance(entries,list):raise ValueError()
        refs=sorted({e['credential_ref'] for e in entries if isinstance(e,dict) and e.get('source_ip')==found[0].get('source_ip')
            and e.get('service_address')==found[0].get('service_address') and e.get('gateway_id')==gateway.id
            and e.get('gateway_origin')==gateway.data.get('api_origin') and isinstance(e.get('credential_ref'),str) and REF.fullmatch(e['credential_ref'])})
    except (ValueError,TypeError,AttributeError,KeyError):raise HTTPException(409,'摄像头接入授权配置无效') from None
    from .camera_secure_store import matching,revoked,StoreError
    from .camera_secure_config_api import ensure_revoked_audit,audit_is_present
    try:
        graphical=matching(p.workspace_id,found[0],gateway,include_revoked=True)
        for ref in graphical:
            if revoked(ref):ensure_revoked_audit(db,p,ref)
            elif audit_is_present(db,p,ref):refs.append(ref)
        refs=sorted(set(refs))
    except StoreError:raise HTTPException(409,'摄像头安全存储不可读取，请联系管理员恢复') from None
    return {'credential_references':refs,'gateway_version':gateway.version,'https_onvif_required':True}

@router.get('/requests/{request_id}')
def lookup(request_id:str,p:P,db:DB):
    require(p,'admin'); row=_receipt(db,p,request_id)
    if not row:raise HTTPException(404,'尚未找到此摄像头接入请求')
    result=_view(row)
    if result['state']=='cancelled':return result
    body=_configured_input(row);device,_,api=_inputs(db,p,body,gateway_generation=row.data.get('gateway_generation'))
    try:
        uri=stream_uri(device['service_address'],device['source_ip'],body.credential_ref)
        result['gateway_state']=gateway_state(api,row.data['gateway_path'],uri,body.credential_ref,_gateway_auth())
    except EnrollmentError as exc:raise HTTPException(409,str(exc)) from None
    return result

@router.post('/requests/{request_id}/cancel')
def cancel(request_id:str,body:Enrollment,p:P,db:DB):
    require(p,'admin')
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',request_id):raise HTTPException(422,'请求标识无效')
    if body.request_id!=request_id:raise HTTPException(422,'原请求标识不一致')
    submission=body.model_dump();sh=digest(submission)
    _lock(db,p,request_id);row=_receipt(db,p,request_id)
    if not row:
        path='onvif_'+hashlib.sha256((p.workspace_id+':'+body.gateway_id+':'+body.candidate_id).encode()).hexdigest()[:32]
        row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=_key(request_id),
            data={**submission,'submission_hash':sh,'gateway_path':path,'state':'cancelled'})
        db.add(row);db.flush();audit(db,p,'media.onvif.enrollment_cancelled',row.id,{});db.commit()
        return _view(row)
    if row.kind!=KIND or row.data.get('submission_hash')!=sh:raise HTTPException(409,'原接入内容与取消请求不一致')
    result=_view(row)
    if result['state']=='pending':
        if row.data.get('gateway_write_started'):
            # A timed-out POST can still be executing in MediaMTX after the
            # application transaction ends. A missing path is NOT proof that
            # this external write will never arrive.
            original=_configured_input(row);device,_,gateway_api=_inputs(db,p,original,gateway_generation=row.data.get('gateway_generation'))
            try:
                uri=stream_uri(device['service_address'],device['source_ip'],original.credential_ref)
                actual=gateway_state(gateway_api,row.data['gateway_path'],uri,original.credential_ref,_gateway_auth())
            except EnrollmentError as exc:raise HTTPException(409,str(exc)) from None
            if actual!='matches':
                raise HTTPException(409,'网关写入结果仍不确定；不能安全结束，请按原请求核对或重试')
            row.data={**row.data,'state':'configured'};row.version+=1
            audit(db,p,'media.onvif.gateway_path_reconciled',row.id,{'gateway_id':row.data['gateway_id']});db.commit()
            return _view(row)
        row.data={**row.data,'state':'cancelled'};row.version+=1
        audit(db,p,'media.onvif.enrollment_cancelled',row.id,{});db.commit()
        return _view(row)
    # Already configured means a physical gateway side effect exists; this
    # button does not remove it and must not claim that it was cancelled.
    return result

@router.post('')
def enroll(body:Enrollment,p:P,db:DB):
    require(p,'admin')
    if not body.confirm_onvif_read or not body.confirm_gateway_write_and_credential_storage:
        raise HTTPException(422,'请确认只读媒体请求及网关保存摄像头凭据的范围')
    submission=body.model_dump();sh=digest(submission)
    _lock(db,p,body.request_id);row=_receipt(db,p,body.request_id)
    if row:
        if row.kind!=KIND or row.data.get('submission_hash')!=sh:
            raise HTTPException(409,'此请求已用于不同的摄像头或网关')
        if row.data.get('state')=='cancelled':return _view(row)
    else:
        _,gateway,_=_inputs(db,p,body)
        path='onvif_'+hashlib.sha256((p.workspace_id+':'+body.gateway_id+':'+body.candidate_id).encode()).hexdigest()[:32]
        row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=_key(body.request_id),
          data={**submission,'submission_hash':sh,'gateway_path':path,'gateway_generation':gateway.data.get('generation'),'state':'pending'})
        db.add(row);db.flush();audit(db,p,'media.onvif.enrollment_started',row.id,{'gateway_id':body.gateway_id});db.commit()
    # A durable pending receipt exists before any network action. Both retries
    # and the initial call re-acquire the same operation lock after commit.
    _lock(db,p,body.request_id);row=_receipt(db,p,body.request_id)
    if row.data.get('state')=='cancelled':return _view(row)
    device,_,api=_inputs(db,p,body,False,row.data.get('gateway_generation'))
    try:
        uri=stream_uri(device['service_address'],device['source_ip'],body.credential_ref)
    except EnrollmentError as exc:
        db.rollback();raise HTTPException(409,str(exc)) from None
    # Commit this conservative fence BEFORE any gateway write. From this point
    # a timeout/missing path can never safely be treated as cancellation.
    if not row.data.get('gateway_write_started'):
        row.data={**row.data,'gateway_write_started':True};row.version+=1;db.commit()
        _lock(db,p,body.request_id);row=_receipt(db,p,body.request_id)
    if row.data.get('state')=='cancelled':return _view(row)
    from contextlib import nullcontext
    from .camera_secure_store import locked
    guard=locked(body.credential_ref) if body.credential_ref.startswith('GA_ONVIF_CAMERA_SECRET_UI_') else nullcontext()
    try:
        with guard:
            device,gateway,api=_inputs(db,p,body,True,row.data.get('gateway_generation'))
            configure_gateway(api,row.data['gateway_path'],uri,body.credential_ref,_gateway_auth())
    except EnrollmentError as exc:
        db.rollback();raise HTTPException(409,str(exc)) from None
    row.data={**row.data,'state':'configured'};row.version+=1
    audit(db,p,'media.onvif.gateway_path_configured',row.id,{'gateway_id':gateway.id,'path':row.data['gateway_path']});db.commit()
    return _view(row)
