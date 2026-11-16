"""Workspace-managed FTP/SMB permissions, bounded by a server CIDR ceiling.

Credentials are Fernet-encrypted in the database. The server key is explicitly
provisioned and is never created or replaced by this module.
"""
from __future__ import annotations
from contextlib import nullcontext
import base64, hashlib, hmac, json, os, stat
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .contracts import digest
from .db import engine
from .ftps_gateway_api import _current_admin
from .managed_modbus_grants import in_ceiling, networks
from .models import Job
from .services import audit, require
from .setup_api import P, DB

router = APIRouter(prefix='/api/file-gateway-authorizations', tags=['文件网关授权'])
KIND='managed_file_grant_v1'; OP='managed_file_grant_operation_v1'; STOP='managed_file_grant_stop_v1'; DENY='managed_file_legacy_deny_v1'; KEY_MARKER='managed_file_key_marker_v1'
# These protocols share the same encryption key and server-wide identity marker.
# Marker recreation must therefore fail if *any* protocol still has ciphertext.
SHARED_KEY_ENCRYPTED_KINDS=(KIND,'managed_ftps_grant_v1','managed_mqtt_grant_v1')
MAX_ACTIVE_GRANTS=100


def enabled():return os.getenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','')=='1'


def lock_workspace(db,workspace,*,shared=False):
    # The same lock fences both legacy and managed access against revoke.
    if enabled() and db.get_bind().dialect.name=='postgresql':
        n=int.from_bytes(hashlib.sha256(('managed-file\0'+workspace).encode()).digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock_shared(:n)' if shared else 'SELECT pg_advisory_xact_lock(:n)'),{'n':n})


def _key():
    raw=os.getenv('GA_FILE_GATEWAY_KEY_FILE','')
    if not raw or not Path(raw).is_absolute():raise HTTPException(503,'文件网关加密密钥未配置，请联系系统管理员')
    try:
        from .config import settings
        parent=Path(raw).parent.resolve(strict=True)
        if parent.is_relative_to(Path(settings().storage_dir).resolve()):raise ValueError()
        fd=os.open(raw,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError()
            value=f.read(256)
        Fernet(value)
        return value
    except (OSError,ValueError):raise HTTPException(503,'文件网关加密密钥不可用，请从备份恢复；未创建或覆盖密钥') from None


def _mac(key,value):
    return hmac.new(base64.urlsafe_b64decode(key),json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()


def _key_guard(db,key,workspace,*,establish=False):
    # Server-wide identity stops a replacement key from silently creating an
    # incompatible generation while older workspace ciphertext still exists.
    if establish and db.get_bind().dialect.name=='postgresql':
        n=int.from_bytes(hashlib.sha256(b'managed-file-key-identity').digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:n)'),{'n':n})
    markers=list(db.scalars(select(Job).where(Job.kind==KEY_MARKER,Job.status=='internal').limit(2)))
    identity=hashlib.sha256(key).hexdigest()
    if len(markers)>1 or markers and markers[0].data!={'key_fingerprint':identity}:
        raise HTTPException(503,'文件网关密钥与原授权不匹配，请从备份恢复，未覆盖原记录')
    if not markers:
        if db.scalar(select(Job.id).where(Job.kind.in_(SHARED_KEY_ENCRYPTED_KINDS)).limit(1)):
            raise HTTPException(503,'共享网关原密钥身份缺失，请恢复备份')
        if establish:
            db.add(Job(workspace_id=workspace,kind=KEY_MARKER,status='internal',idempotency_key='managed-file-key-marker',data={'key_fingerprint':identity}));db.flush()


def _target(protocol,endpoint,root):
    # Passive port range/credential changes do not silently undo a revocation.
    return digest({'protocol':protocol,'host':endpoint['host'],'port':endpoint['port'],
                   'root':root.casefold() if protocol=='smb' else root})


def _safe_text(value,maximum):
    return isinstance(value,str) and bool(value.strip()) and len(value)<=maximum and not any(ord(c)<32 or ord(c)==127 for c in value)


def _uuid(value):
    try:
        s=str(UUID(value))
        if s!=value:raise ValueError()
        return s
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请求编号格式无效') from None


def _canon(body):
    if not isinstance(body,dict):raise HTTPException(422,'授权内容无效')
    protocol=body.get('protocol')
    common={'request_id','protocol','name','host','port','username','password'}
    extra={'root','data_port_min','data_port_max','server_chroot_confirmed','plaintext_risk_confirmed'} if protocol=='ftp' else {'share','share_root_confirmed'} if protocol=='smb' else set()
    if not extra or set(body)!=common|extra:raise HTTPException(422,'授权字段不完整或包含未支持的内容')
    rid=_uuid(body['request_id']);name=body['name'];host=body['host'];port=body['port'];user=body['username'];password=body['password']
    if not _safe_text(name,100) or not _safe_text(user,256) or not _safe_text(password,256):raise HTTPException(422,'授权名称或凭据格式无效')
    if type(port) is not int or not 1<=port<=65535 or not isinstance(host,str) or not in_ceiling(host):raise HTTPException(422,'地址不在系统管理员允许的网络范围内')
    try:
        if protocol=='ftp':
            from .ftp_transport import _endpoint,_path
            endpoint=_endpoint({'host':host,'port':port,'data_port_min':body['data_port_min'],'data_port_max':body['data_port_max']})
            root=_path(body['root'])
            if body['server_chroot_confirmed'] is not True or body['plaintext_risk_confirmed'] is not True:raise ValueError()
        else:
            from .smb_transport import _endpoint
            endpoint=_endpoint({'host':host,'port':port,'share':body['share']});root=endpoint['share']
            if body['share_root_confirmed'] is not True:raise ValueError()
    except (ValueError,TypeError,KeyError):raise HTTPException(422,'服务地址、端口、目录或授权确认无效') from None
    return {'request_id':rid,'protocol':protocol,'name':name.strip(),'endpoint':endpoint,'root':root,'username':user,'password':password}


def _op_key(actor,rid):return 'managed-file-op:'+str(actor)+':'+str(rid)

def _grant_key(identity):return 'managed-file-grant:'+identity

def _deny_key(protocol,target):return 'managed-file-deny:'+protocol+':'+target


def _row(db,workspace,key):
    return db.scalar(select(Job).where(Job.workspace_id==workspace,Job.idempotency_key==key).execution_options(populate_existing=True))


def _identity(workspace,protocol,rid):return digest({'workspace':workspace,'protocol':protocol,'request_id':rid})


def _checked(row,workspace):
    try:
        value=row.data;grant=value['grant'];protocol=value['protocol']
        if row.kind!=KIND or row.status!='internal' or protocol not in {'ftp','smb'} or type(value['enabled']) is not bool:raise ValueError()
        if not isinstance(grant,dict) or grant['id']!=_identity(workspace,protocol,value['request_id']) or not isinstance(value['cipher'],str):raise ValueError()
        if value['target']!=_target(protocol,grant['endpoint'],value['root']):raise ValueError()
        if grant.get('_managed') is not True or grant.get('_workspace')!=workspace:raise ValueError()
        return value
    except (KeyError,TypeError,ValueError):raise HTTPException(409,'文件网关授权记录无法核对，请联系管理员') from None


def _managed_rows(db,workspace):
    # Historical over-limit data and revoked records must remain readable, or
    # an operator could no longer inspect/revoke them or use existing sources.
    return list(db.scalars(select(Job).where(Job.workspace_id==workspace,Job.kind==KIND,Job.status=='internal')))


def _denied(db,workspace,protocol,target):
    row=_row(db,workspace,_deny_key(protocol,target))
    if row is None:return False
    if row.kind!=DENY or row.status!='internal' or row.data!={'protocol':protocol,'target':target,'denied':True}:raise HTTPException(409,'历史授权撤销记录无法核对')
    return True


def merge_grants(workspace,protocol,legacy,db=None):
    with (nullcontext(db) if db is not None else Session(engine())) as db:
        out=[]
        for item in legacy:
            root=item['root'] if protocol=='ftp' else item['endpoint']['share']
            # A durable deny survives disabling the self-service feature.
            if (not enabled() or in_ceiling(item['endpoint']['host'])) and not _denied(db,workspace,protocol,_target(protocol,item['endpoint'],root)):out.append(item)
        if enabled():
            for row in _managed_rows(db,workspace):
                data=_checked(row,workspace);grant=data['grant']
                if data['protocol']==protocol and data['enabled'] and in_ceiling(grant['endpoint']['host']):out.append(grant)
        return out


def credentials(grant):
    db=grant.get('_db');grant={k:v for k,v in grant.items() if k!='_db'}
    workspace=grant.get('_workspace');identity=grant.get('id')
    with (nullcontext(db) if db is not None else Session(engine())) as db:
        row=_row(db,workspace,_grant_key(identity))
        if not row:raise ValueError('文件网关授权不存在')
        data=_checked(row,workspace)
        if not data['enabled'] or data['grant']!=grant or not in_ceiling(grant['endpoint']['host']):raise ValueError('文件网关授权已撤销或超出网络范围')
        try:
            key=_key();_key_guard(db,key,workspace)
            value=json.loads(Fernet(key).decrypt(data['cipher'].encode()))
            if value.get('workspace')!=workspace or value.get('id')!=identity or value.get('grant_hash')!=digest(grant) or not _safe_text(value.get('username'),256) or not _safe_text(value.get('password'),256):raise ValueError()
            return value['username'],value['password']
        except (InvalidToken,ValueError,TypeError,KeyError):raise ValueError('文件网关凭据无法解密，请恢复原密钥') from None


def _legacy(workspace,protocol):
    if protocol=='ftp':
        from .ftp_transport import legacy_grants
    else:
        from .smb_transport import legacy_grants
    return legacy_grants(workspace)


def _public(protocol,grant,origin,available,version):
    ep=grant['endpoint'];root=grant.get('root') if protocol=='ftp' else ep['share']
    return {'id':grant['id'],'protocol':protocol,'name':grant['name'],'host':ep['host'],'port':ep['port'],
            'root':root,'origin':origin,'enabled':available,'version':version}


def _view(db,p,protocol):
    result=[]
    for g in _legacy(p.workspace_id,protocol):
        root=g['root'] if protocol=='ftp' else g['endpoint']['share']
        result.append(_public(protocol,g,'server',in_ceiling(g['endpoint']['host']) and not _denied(db,p.workspace_id,protocol,_target(protocol,g['endpoint'],root)),0))
    for row in _managed_rows(db,p.workspace_id):
        data=_checked(row,p.workspace_id)
        if data['protocol']==protocol:result.append(_public(protocol,data['grant'],'workspace',data['enabled'] and in_ceiling(data['grant']['endpoint']['host']),row.version))
    return result


async def _body(request):
    if 'application/json' not in request.headers.get('content-type','').lower():raise HTTPException(415,'请提交 JSON 授权')
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>8192:raise HTTPException(413,'授权内容超过大小限制')
        chunks.append(chunk)
    def pairs(items):
        result={}
        for key,val in items:
            if key in result:raise ValueError()
            result[key]=val
        return result
    try:return json.loads(b''.join(chunks),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (UnicodeError,ValueError,RecursionError):raise HTTPException(422,'授权请求格式无效') from None


@router.get('/grants')
def list_grants(p:P,db:DB):
    require(p,'admin'); active=enabled();available=False
    if active:
        try:key=_key();_key_guard(db,key,p.workspace_id);available=True
        except HTTPException:pass
    return {'enabled':active,'key_ready':available,'networks':[str(n) for n in networks()] if active else [],
            'grants':_view(db,p,'ftp')+_view(db,p,'smb') if active else []}


@router.post('/grants')
async def create_grant(request:Request,p:P,db:DB):
    require(p,'admin')
    if not enabled():raise HTTPException(409,'文件网关图形授权未启用')
    body=await _body(request);value=_canon(body);key=_key();rid=value['request_id'];fingerprint=_mac(key,value)
    lock_workspace(db,p.workspace_id);_current_admin(db,p);_key_guard(db,key,p.workspace_id,establish=True)
    prior=_row(db,p.workspace_id,_op_key(p.user_id,rid))
    if prior:
        if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if prior.kind!=OP or prior.data.get('fingerprint')!=fingerprint or prior.data.get('kind')!='create':raise HTTPException(409,'此请求已绑定其他授权内容')
        return {'request_id':rid,'state':'succeeded','grant':prior.data['result']}
    # Workspace lock serializes the check with creation and revocation. Keep
    # revoked records as audit history without consuming the active quota.
    if sum(_checked(row,p.workspace_id)['enabled'] for row in _managed_rows(db,p.workspace_id))>=MAX_ACTIVE_GRANTS:
        raise HTTPException(409,'当前有效授权已达上限，请先撤销不再使用的授权')
    protocol=value['protocol'];identity=_identity(p.workspace_id,protocol,rid);ep=value['endpoint'];target=_target(protocol,ep,value['root'])
    if protocol=='ftp':
        grant={'id':identity,'name':value['name'],'endpoint':ep,'root':value['root'],'username_ref':'GA_FTP_USERNAME_UI_'+identity.upper(),
          'password_ref':'GA_FTP_PASSWORD_UI_'+identity.upper(),'server_chroot_confirmed':True,'plaintext_risk_confirmed':True,'_managed':True,'_workspace':p.workspace_id}
    else:
        grant={'id':identity,'name':value['name'],'endpoint':ep,'username_ref':'GA_SMB_USERNAME_UI_'+identity.upper(),
          'password_ref':'GA_SMB_PASSWORD_UI_'+identity.upper(),'share_root_confirmed':True,'_managed':True,'_workspace':p.workspace_id}
    cipher=Fernet(key).encrypt(json.dumps({'workspace':p.workspace_id,'id':identity,'grant_hash':digest(grant),'username':value['username'],'password':value['password']},ensure_ascii=False).encode()).decode()
    row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=_grant_key(identity),data={'request_id':rid,'protocol':protocol,'root':value['root'],'target':target,'enabled':True,'grant':grant,'cipher':cipher})
    db.add(row);db.flush();result=_public(protocol,grant,'workspace',True,row.version)
    db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op_key(p.user_id,rid),data={'kind':'create','request_id':rid,'actor_id':p.user_id,'fingerprint':fingerprint,'result':result}))
    audit(db,p,'file.gateway.authorization.created',row.id,{'protocol':protocol,'grant_id':identity});db.commit()
    return {'request_id':rid,'state':'succeeded','grant':result}


@router.post('/grants/{identity}/revoke')
async def revoke_grant(identity:str,request:Request,p:P,db:DB):
    require(p,'admin')
    if not enabled():raise HTTPException(409,'文件网关图形授权未启用')
    body=await _body(request)
    if not isinstance(body,dict) or set(body)!={'request_id','protocol','version'} or body['protocol'] not in {'ftp','smb'} or type(body['version']) is not int or body['version']<0:raise HTTPException(422,'撤权请求格式无效')
    rid=_uuid(body['request_id']);protocol=body['protocol'];fingerprint=digest({'identity':identity,'protocol':protocol,'version':body['version']})
    if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):raise HTTPException(422,'授权标识无效')
    lock_workspace(db,p.workspace_id);_current_admin(db,p);prior=_row(db,p.workspace_id,_op_key(p.user_id,rid))
    if prior:
        if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if prior.kind!=OP or prior.data.get('fingerprint')!=fingerprint or prior.data.get('kind')!='revoke':raise HTTPException(409,'此请求已绑定其他操作')
        return {'request_id':rid,'state':'succeeded','grant':prior.data['result']}
    row=_row(db,p.workspace_id,_grant_key(identity));grant=None
    if row:
        data=_checked(row,p.workspace_id)
        if data['protocol']!=protocol or row.version!=body['version']:raise HTTPException(409,'授权已变化，请刷新后操作')
        grant=data['grant'];target=data['target'];row.data={**data,'enabled':False};row.version+=1;version=row.version;origin='workspace'
    else:
        grant=next((g for g in _legacy(p.workspace_id,protocol) if g['id']==identity),None)
        if grant is None or body['version']!=0:raise HTTPException(404,'授权不存在或已变化')
        root=grant['root'] if protocol=='ftp' else grant['endpoint']['share'];target=_target(protocol,grant['endpoint'],root);version=0;origin='server'
    deny=_row(db,p.workspace_id,_deny_key(protocol,target))
    if not deny:db.add(Job(workspace_id=p.workspace_id,kind=DENY,status='internal',idempotency_key=_deny_key(protocol,target),data={'protocol':protocol,'target':target,'denied':True}))
    elif not _denied(db,p.workspace_id,protocol,target):raise HTTPException(409,'原撤权记录无法核对')
    result=_public(protocol,grant,origin,False,version)
    db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op_key(p.user_id,rid),data={'kind':'revoke','request_id':rid,'actor_id':p.user_id,'fingerprint':fingerprint,'result':result}))
    audit(db,p,'file.gateway.authorization.revoked',identity,{'protocol':protocol});db.commit()
    return {'request_id':rid,'state':'succeeded','grant':result}


@router.get('/operations/{request_id}')
def operation(request_id:str,p:P,db:DB):
    rid=_uuid(request_id);row=_row(db,p.workspace_id,_op_key(p.user_id,rid))
    if not row:raise HTTPException(404,'原授权请求尚未找到')
    if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
    if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原授权记录无法核对')
    return {'request_id':rid,'state':'succeeded','grant':row.data['result']}


@router.post('/operations/{request_id}/cancel')
def cancel(request_id:str,p:P,db:DB):
    rid=_uuid(request_id);lock_workspace(db,p.workspace_id);row=_row(db,p.workspace_id,_op_key(p.user_id,rid))
    if row:
        if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求无法核对')
        return {'request_id':rid,'state':'succeeded','grant':row.data['result']}
    db.add(Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_op_key(p.user_id,rid),data={'request_id':rid,'actor_id':p.user_id}))
    db.commit();return {'request_id':rid,'state':'cancelled'}
