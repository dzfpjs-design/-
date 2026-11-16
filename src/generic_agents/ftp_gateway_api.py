"""Explicit, read-only FTP ingestion into initialization materials.

No background scan or remote mutation is performed. Every user request has a
workspace/actor-scoped immutable receipt and a serialized cancellation fence.
"""
from __future__ import annotations
import copy
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import select, text
from .contracts import Contract, digest
from .gateway_history_paging import page as gateway_page
from .models import Evidence, Job, SetupMaterial, User, Membership, AuthSession, now
from .services import audit, require
from .setup_api import P, DB, MEDIA, SUFFIXES, material_view, queue_parse
from .config import settings
from .evidence import evidence_bytes
from .ftp_transport import FTPError, find_grant, grants, list_files, public_grant, read_file

router=APIRouter(prefix='/api/ftp')
REQUEST='ftp_request_v1'; CONNECTION='ftp_connection_v1'; STOP='ftp_stop_v1'

class Connect(Contract):
    request_id: UUID
    name: StrictStr = Field(min_length=1,max_length=100)
    grant_id: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
class Listing(Contract):
    request_id: UUID
    version: StrictInt = Field(ge=1)
class Import(Contract):
    request_id: UUID
    version: StrictInt = Field(ge=1)
    listing_id: UUID
    file_id: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
class Cancel(Contract):
    kind: Literal['connect','listing','import']
    submission: dict


def _key(request):return 'ftp-request:'+str(request)
def _lock(db,workspace,value):
    number=int.from_bytes(hashlib.sha256((workspace+'\0'+value).encode()).digest()[:8],'big',signed=True)
    if db.get_bind().dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':number})
def _admin(p):require(p,'admin')
def _current_admin(db,p,lock=True):
    """Lock and re-check the live identity before committing network results."""
    query=(select(User,Membership,AuthSession).join(Membership,Membership.user_id==User.id)
           .join(AuthSession,AuthSession.user_id==User.id)
           .where(User.id==p.user_id,User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role=='admin',
                  AuthSession.id==p.session_id,AuthSession.revoked.is_(False),AuthSession.expires_at>now()))
    if lock:query=query.with_for_update(read=True,of=[User,Membership,AuthSession])
    query=query.execution_options(populate_existing=True)
    if db.execute(query).first() is None:raise HTTPException(403,'当前账号已停用或管理员权限已变化，原请求保留')
def _get(db,p,identity,kind=None):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(identity)))
    if not row or row.kind!=kind or row.status!='internal':raise HTTPException(404,'本工作区未找到该 FTP 记录')
    return row

def _connection(db,p,identity,version=None):
    from .managed_file_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    row=_get(db,p,identity,CONNECTION)
    if version is not None and row.version!=version:raise HTTPException(409,'连接已变化，请重新读取')
    if row.data.get('status')!='active':raise HTTPException(409,'连接已不可用')
    try:grant=find_grant(p.workspace_id,row.data['grant_id'],db=db)
    except (FTPError,KeyError) as exc:raise HTTPException(409,str(exc)) from None
    if grant.get('_managed'):grant={**grant,'_db':db}
    return row,grant

def _connection_view(row):return {'id':row.id,'name':row.data['name'],'grant_id':row.data['grant_id'],'version':row.version,'status':row.data['status']}
def _receipt(row):
    value=row.data
    return {'request_id':value['request_id'],'state':value['state'],'kind':value['kind'],
            'submission':copy.deepcopy(value['submission']),'result':copy.deepcopy(value.get('result')),
            **({'error':value['error']} if value.get('error') else {})}
def _find_request(db,p,request):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_key(request)).execution_options(populate_existing=True))
def _validate_record(row,p,kind,submission):
    if (row.kind not in (REQUEST,STOP) or row.status!='internal' or row.data.get('actor_id')!=p.user_id
            or row.data.get('kind')!=kind or row.data.get('submission')!=submission or row.data.get('submission_hash')!=digest(submission)):
        raise HTTPException(409,'原 FTP 请求与当前账号或内容不一致，请核对原记录')

def _canonical_submission(kind,value):
    """Cancellation must never persist arbitrary caller-provided fields or secrets."""
    try:
        if not isinstance(value,dict):raise ValueError()
        raw=dict(value)
        if kind=='connect':
            parsed=Connect.model_validate(raw).model_dump(mode='json');parsed['name']=parsed['name'].strip()
            if not parsed['name'] or any(ord(c)<32 for c in parsed['name']):raise ValueError()
            return parsed
        identity=str(UUID(raw.pop('connection_id')))
        parsed=(Listing if kind=='listing' else Import).model_validate(raw).model_dump(mode='json')
        return {**parsed,'connection_id':identity}
    except (TypeError,ValueError,KeyError,ValidationError):
        raise HTTPException(422,'原请求内容格式无法核对') from None

def _start(db,p,kind,submission):
    request=submission['request_id'];_lock(db,p.workspace_id,_key(request));row=_find_request(db,p,request)
    if row:
        _validate_record(row,p,kind,submission)
        if row.data['state'] in ('succeeded','cancelled'):return row,False
        if row.data['state'] not in ('pending','failed'):raise HTTPException(409,'原 FTP 请求状态无法核对')
        row.data={**row.data,'state':'pending','error':None};db.commit();return row,True
    row=Job(workspace_id=p.workspace_id,kind=REQUEST,status='internal',idempotency_key=_key(request),data={
        'request_id':request,'kind':kind,'submission':submission,'submission_hash':digest(submission),
        'actor_id':p.user_id,'state':'pending','result':None})
    db.add(row);db.commit();return row,True

def _resume(db,p,kind,submission):
    _lock(db,p.workspace_id,_key(submission['request_id']));row=_find_request(db,p,submission['request_id'])
    if not row:raise HTTPException(409,'原请求尚未可靠保存')
    _validate_record(row,p,kind,submission)
    return row

def _fail(db,p,row,error):
    # Only explicit transport errors are safe to show; never expose provider replies or credentials.
    message=str(error) if isinstance(error,FTPError) else 'FTP 操作未完成，请核对授权和连接后按原请求重试'
    kind=row.data['kind'];submission=copy.deepcopy(row.data['submission'])
    # Discard any partially flushed Evidence/material rows before recording the
    # failure in a fresh transaction. The request intent was committed first.
    db.rollback();row=_resume(db,p,kind,submission)
    if row.data['state']!='pending':return _receipt(row)
    _current_admin(db,p)
    row.data={**row.data,'state':'failed','error':message};db.commit();return _receipt(row)

@router.get('/grants')
def available_grants(p:P,db:DB):
    _admin(p)
    try:return {'grants':[public_grant(x) for x in grants(p.workspace_id,db=db)]}
    except FTPError as exc:raise HTTPException(409,str(exc)) from None

@router.get('/connections')
def connections(p:P,db:DB,cursor:str|None=None):
    _admin(p);rows,next_cursor=gateway_page(db,[Job.workspace_id==p.workspace_id,Job.kind==CONNECTION,Job.status=='internal'],cursor)
    return {'connections':[_connection_view(x) for x in rows],'next_cursor':next_cursor}

@router.get('/connections/{identity}')
def exact_connection(identity:UUID,p:P,db:DB):
    """Recover a specific saved connection without needing a live grant."""
    _admin(p);row=_get(db,p,identity,CONNECTION)
    try:
        connection=_connection_view(row)
        try:find_grant(p.workspace_id,row.data['grant_id'],db=db);available=True
        except FTPError:available=False
    except (KeyError,TypeError):raise HTTPException(409,'FTP 连接记录无法核对') from None
    return {'connection':connection,'source_available':available}

@router.post('/connections')
def connect(body:Connect,p:P,db:DB):
    _admin(p);submission=body.model_dump(mode='json');submission['name']=submission['name'].strip()
    if not submission['name'] or any(ord(c)<32 for c in submission['name']):raise HTTPException(422,'请填写有效连接名称')
    from .managed_file_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    try:find_grant(p.workspace_id,submission['grant_id'],db=db)
    except FTPError as exc:raise HTTPException(409,str(exc)) from None
    row,perform=_start(db,p,'connect',submission)
    if not perform:return _receipt(row)
    row=_resume(db,p,'connect',submission)
    if row.data['state']!='pending':return _receipt(row)
    try:
        lock_workspace(db,p.workspace_id,shared=True);_current_admin(db,p);find_grant(p.workspace_id,submission['grant_id'],db=db)
        connection=Job(workspace_id=p.workspace_id,kind=CONNECTION,status='internal',idempotency_key='ftp-connection:'+submission['request_id'],data={'name':submission['name'],'grant_id':submission['grant_id'],'status':'active'})
        db.add(connection);db.flush();result=_connection_view(connection)
        row.data={**row.data,'state':'succeeded','result':result};audit(db,p,'ftp.connection.created',connection.id,{'grant_id':submission['grant_id']});db.commit();return _receipt(row)
    except FTPError as exc:return _fail(db,p,row,exc)

@router.post('/connections/{identity}/listings')
def listing(identity:str,body:Listing,p:P,db:DB):
    _admin(p);submission={**body.model_dump(mode='json'),'connection_id':identity};_connection(db,p,identity,body.version)
    row,perform=_start(db,p,'listing',submission)
    if not perform:return _receipt(row)
    row=_resume(db,p,'listing',submission)
    if row.data['state']!='pending':return _receipt(row)
    try:
        _current_admin(db,p,lock=False);connection,grant=_connection(db,p,identity,body.version);facts=list_files(grant)
        facts=[x for x in facts if Path(x['name']).suffix.lower() in (SUFFIXES - {'.tif', '.tiff'}) and x['size']<=settings().max_upload_bytes]
        by_id={digest(x):x for x in facts}
        if len(by_id)!=len(facts):raise FTPError('FTP 目录存在无法区分的重复文件')
        # Grant may have been removed on a restarted process; no result is committed without checking it again.
        _current_admin(db,p);_connection(db,p,identity,body.version)
        files=[{'id':key,'name':x['name'],'size':x['size'],'modified':x['modified']} for key,x in by_id.items()]
        row.data={**row.data,'state':'succeeded','facts':by_id,'result':{'listing_id':row.id,'files':files}}
        audit(db,p,'ftp.directory.read',connection.id,{'count':len(files)});db.commit();return _receipt(row)
    except HTTPException as exc:
        if exc.status_code==403:db.rollback();raise
        return _fail(db,p,row,exc)
    except FTPError as exc:return _fail(db,p,row,exc)


def _material(db,p,name,content,sha):
    # Match setup_api.upload_material exactly so a concurrent upload of the
    # same bytes and this import cannot create competing evidence records.
    number=int.from_bytes(hashlib.sha256((p.workspace_id+sha).encode()).digest()[:8],'big',signed=True)
    if db.get_bind().dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':number})
    existing=db.scalar(select(SetupMaterial).where(SetupMaterial.workspace_id==p.workspace_id,SetupMaterial.sha256==sha))
    if existing:
        _,raw=evidence_bytes(db,p,existing.evidence_id)
        if hashlib.sha256(raw).hexdigest()!=sha:raise FTPError('已有原件无法核对，未建立来源记录')
        return existing,True
    key=p.workspace_id+'/'+sha+'.bin';path=settings().storage_dir/key;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:os.chmod(tmp,0o600);f.write(content);f.flush();os.fsync(f.fileno())
        if hashlib.sha256(Path(tmp).read_bytes()).hexdigest()!=sha:raise FTPError('保存原件摘要不一致')
        os.replace(tmp,path);directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(tmp).unlink(missing_ok=True)
    ev=Evidence(workspace_id=p.workspace_id,sha256=sha,size_bytes=len(content),storage_key=key,media_type=MEDIA[Path(name).suffix.lower()],data={'modality':'document','cloud_model_ids':[],'uploaded_by':p.user_id})
    db.add(ev);db.flush()
    # Verify the final path, not just the temporary file, before creating any
    # material or parsing task. Refuse a symlink at the final component.
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=len(content):raise FTPError('保存原件无法核对')
            final=f.read(len(content)+1)
        if len(final)!=len(content) or hashlib.sha256(final).hexdigest()!=sha:raise FTPError('保存原件摘要不一致')
        _,checked=evidence_bytes(db,p,ev.id)
        if len(checked)!=len(content):raise FTPError('保存原件长度无法核对')
    except OSError:raise FTPError('保存原件无法安全读回') from None
    material=SetupMaterial(workspace_id=p.workspace_id,name=name,sha256=sha,evidence_id=ev.id,data={});db.add(material);db.flush();queue_parse(db,p,material)
    return material,False

@router.post('/connections/{identity}/imports')
def import_file(identity:str,body:Import,p:P,db:DB):
    _admin(p);submission={**body.model_dump(mode='json'),'connection_id':identity};_connection(db,p,identity,body.version)
    row,perform=_start(db,p,'import',submission)
    if not perform:return _receipt(row)
    row=_resume(db,p,'import',submission)
    if row.data['state']!='pending':return _receipt(row)
    try:
        _current_admin(db,p,lock=False);connection,grant=_connection(db,p,identity,body.version);snapshot=_get(db,p,body.listing_id,REQUEST)
        if snapshot.data.get('state')!='succeeded' or snapshot.data.get('kind')!='listing' or snapshot.data.get('submission',{}).get('connection_id')!=identity:raise FTPError('原目录快照不可用，请重新读取')
        facts=snapshot.data.get('facts',{}).get(body.file_id)
        if not isinstance(facts,dict) or digest(facts)!=body.file_id:raise FTPError('原文件快照无法核对，请重新读取')
        if Path(facts['name']).suffix.lower() not in (SUFFIXES - {'.tif', '.tiff'}) or facts['size']>settings().max_upload_bytes:raise FTPError('此文件不符合初始化资料的格式或大小限制')
        content,sha=read_file(grant,facts);_current_admin(db,p);_connection(db,p,identity,body.version)
        material,reused=_material(db,p,facts['name'],content,sha)
        # The source identity is per import, independent of content-addressed material deduplication.
        result={'material_id':material.id,'name':facts['name'],'status':material.status,'reused':reused,
                'source':{'connection_id':connection.id,'listing_id':snapshot.id,'file_id':body.file_id,'name':facts['name'],
                          'size':facts['size'],'modified':facts['modified'],'sha256':sha}}
        row.data={**row.data,'state':'succeeded','result':result,'source':{'connection_id':connection.id,'listing_id':snapshot.id,'file_id':body.file_id,'grant_id':grant['id'],'file':facts,'sha256':sha,'material_id':material.id}}
        audit(db,p,'ftp.material.imported',material.id,{'request_id':submission['request_id'],'sha256':sha,'reused':reused});db.commit()
        return _receipt(row)
    except HTTPException as exc:
        if exc.status_code==403:db.rollback();raise
        return _fail(db,p,row,exc)
    except (FTPError,KeyError) as exc:return _fail(db,p,row,exc)

@router.get('/requests/{request_id}')
def request_status(request_id:UUID,p:P,db:DB):
    _admin(p);row=_find_request(db,p,str(request_id))
    if not row or row.kind not in (REQUEST,STOP) or row.data.get('actor_id')!=p.user_id:raise HTTPException(404,'未找到当前账号的 FTP 原请求')
    return _receipt(row)

@router.post('/requests/{request_id}/cancel')
def cancel(request_id:UUID,body:Cancel,p:P,db:DB):
    _admin(p);request=str(request_id);submission=_canonical_submission(body.kind,body.submission)
    if submission.get('request_id')!=request:raise HTTPException(422,'原请求编号与提交内容不一致')
    _lock(db,p.workspace_id,_key(request));_current_admin(db,p);row=_find_request(db,p,request)
    if row:
        _validate_record(row,p,body.kind,submission)
        if row.data['state']=='succeeded':return _receipt(row)
        row.kind=STOP;row.data={**row.data,'state':'cancelled','error':None};db.commit();return _receipt(row)
    row=Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_key(request),data={'request_id':request,'kind':body.kind,'submission':submission,'submission_hash':digest(submission),'actor_id':p.user_id,'state':'cancelled','result':None})
    db.add(row);audit(db,p,'ftp.request.cancelled',request,{'kind':body.kind});db.commit();return _receipt(row)

def _history(identity,p,db,kind,key,cursor=None):
    _admin(p);connection=_get(db,p,identity,CONNECTION)
    # Saved receipts remain visible even after the source is revoked.
    try:find_grant(p.workspace_id,connection.data['grant_id'],db=db);available=True
    except (FTPError,KeyError):available=False
    filters=[Job.workspace_id==p.workspace_id,Job.kind==REQUEST,Job.status=='internal',
             Job.data['state'].as_string()=='succeeded',Job.data['kind'].as_string()==kind,
             Job.data['submission']['connection_id'].as_string()==identity]
    rows,next_cursor=gateway_page(db,filters,cursor)
    return {key:[_receipt(x) for x in rows], 'source_available':available,'next_cursor':next_cursor}

@router.get('/connections/{identity}/listings')
def listing_history(identity:str,p:P,db:DB,cursor:str|None=None):return _history(identity,p,db,'listing','listings',cursor)

@router.get('/connections/{identity}/listings/{listing_id}')
def exact_listing(identity:str,listing_id:UUID,p:P,db:DB):
    """Recover a specific saved listing even when it is outside the history page."""
    _admin(p);connection=_get(db,p,identity,CONNECTION);row=_get(db,p,listing_id,REQUEST)
    submission=row.data.get('submission');result=row.data.get('result')
    if (row.data.get('state')!='succeeded' or row.data.get('kind')!='listing'
            or not isinstance(submission,dict) or submission.get('connection_id')!=identity
            or not isinstance(result,dict) or result.get('listing_id')!=row.id):
        raise HTTPException(404,'本工作区未找到该 FTP 目录快照')
    try:find_grant(p.workspace_id,connection.data['grant_id'],db=db);available=True
    except (FTPError,KeyError):available=False
    return {'listing':_receipt(row),'source_available':available}

@router.get('/connections/{identity}/imports')
def import_history(identity:str,p:P,db:DB,cursor:str|None=None):return _history(identity,p,db,'import','imports',cursor)
