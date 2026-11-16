"""Graphical, encrypted OPC UA trust within the server's network ceiling."""
import base64, hashlib, hmac, ipaddress, json, os, stat
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
from cryptography import x509
from cryptography.x509.oid import ExtensionOID
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .contracts import Contract, digest
from .db import engine
from .device_gateway_store_v268 import member
from .ftps_gateway_api import _current_admin, _lock
from .managed_modbus_grants import in_ceiling, networks
from .models import Job
from .services import audit, require
from .setup_api import P, DB

router=APIRouter(prefix='/api/opcua/authorizations',tags=['OPC UA 授权'])
_KIND='managed_opcua_grant_v1';_OP='managed_opcua_grant_op_v1';_STOP='managed_opcua_stop_v1';_KEY_MARKER='managed_opcua_key_marker_v1'


def enabled():return os.getenv('GA_MANAGED_OPCUA_ENABLED','')=='1'


def _cipher():
    path=os.getenv('GA_OPCUA_VAULT_KEY_FILE','')
    if not path or not Path(path).is_absolute():raise HTTPException(503,'OPC UA 安全存储密钥未配置')
    try:
        from .config import settings
        if Path(path).parent.resolve(strict=True).is_relative_to(Path(settings().storage_dir).resolve()):raise ValueError()
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError()
            data=f.read(256)
        return Fernet(data),data
    except (OSError,ValueError):raise HTTPException(503,'OPC UA 安全存储密钥不可用，请从备份恢复') from None


def _key_guard(db,key,workspace,*,establish=False):
    if establish and db.get_bind().dialect.name=='postgresql':
        number=int.from_bytes(hashlib.sha256(b'managed-opcua-key-identity').digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':number})
    identity=hashlib.sha256(key).hexdigest()
    markers=list(db.scalars(select(Job).where(Job.kind==_KEY_MARKER,Job.status=='internal').limit(2)))
    if len(markers)>1 or markers and markers[0].data!={'key_fingerprint':identity}:
        raise HTTPException(503,'OPC UA 密钥与原授权不匹配，请从备份恢复')
    if not markers:
        if db.scalar(select(Job.id).where(Job.kind==_KIND).limit(1)):
            raise HTTPException(503,'OPC UA 原密钥身份缺失，请恢复备份')
        if establish:
            db.add(Job(workspace_id=workspace,kind=_KEY_MARKER,status='internal',idempotency_key='managed-opcua-key-marker',data={'key_fingerprint':identity}));db.flush()


def lock_workspace(db,workspace,*,shared=False):
    if db.get_bind().dialect.name=='postgresql':
        number=int.from_bytes(hashlib.sha256(('managed-opcua\0'+workspace).encode()).digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock_shared(:k)' if shared else 'SELECT pg_advisory_xact_lock(:k)'),{'k':number})


def _b64(value):
    try:
        result=base64.b64decode(value,validate=True)
        if not 0<len(result)<=32768:raise ValueError()
        return result
    except (ValueError,TypeError):raise HTTPException(422,'证书或私钥文件格式无效') from None


def _uri(cert):
    try:
        value=x509.load_pem_x509_certificate(cert) if cert.startswith(b'-----BEGIN') else x509.load_der_x509_certificate(cert)
        uris=value.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.UniformResourceIdentifier)
        if len(uris)!=1 or not 1<=len(uris[0])<=300:raise ValueError()
        return uris[0]
    except (ValueError,TypeError,x509.ExtensionNotFound):raise HTTPException(422,'证书必须包含唯一的设备应用标识 URI') from None


def _check_endpoint(host,port,path,node):
    try:
        if str(ipaddress.IPv4Address(host))!=host or not in_ceiling(host):raise ValueError()
        if type(port) is not int or not 1<=port<=65535:raise ValueError()
        if not isinstance(path,str) or not path.startswith('/') or len(path)>200 or any(x in path for x in ('?','#','@','\\','..')) or any(ord(c)<32 for c in path):raise ValueError()
        if not isinstance(node,str) or not 1<=len(node)<=300 or any(ord(c)<32 for c in node):raise ValueError()
    except (ValueError,TypeError):raise HTTPException(422,'设备地址、路径或节点不在允许范围内') from None


def _opkey(actor,req):return 'managed-opcua-op:'+actor+':'+str(req)
def _oplock(db,p,req):_lock(db,p.workspace_id,_opkey(p.user_id,req))
def _receipt(row):return {'request_id':row.data['request_id'],'kind':row.data['kind'],'state':row.data['state'],'result':row.data.get('result')}


def _rows(db,workspace):
    rows=list(db.scalars(select(Job).where(Job.workspace_id==workspace,Job.kind==_KIND,Job.status=='internal').limit(101)))
    if len(rows)>100:raise HTTPException(409,'OPC UA 授权过多，请联系管理员')
    return rows


def _verify(row,workspace):
    d=row.data
    try:
        public=d['public'];ident=public['id'];expected=digest({k:v for k,v in public.items() if k!='id'})
        if ident!=expected or public['workspace']!=workspace or type(d['enabled']) is not bool or not isinstance(d['ciphertext'],str) or not isinstance(d['name'],str):raise ValueError()
        return d
    except (KeyError,TypeError,ValueError):raise HTTPException(409,'OPC UA 授权记录无法核对') from None


def _decrypt(data,db,workspace):
    cipher,key=_cipher();_key_guard(db,key,workspace)
    try:
        value=json.loads(cipher.decrypt(data['ciphertext'].encode()))
        if set(value)!={'server_certificate','client_certificate','client_private_key'}:raise ValueError()
        return [_b64(value[k]) for k in ('server_certificate','client_certificate','client_private_key')]
    except (InvalidToken,ValueError,TypeError,KeyError,UnicodeError):raise HTTPException(409,'OPC UA 授权无法解密，请恢复原密钥') from None


def _materialize(data,db,workspace):
    from .opcua_readonly_transport import validate_materials,OpcuaError
    public=data['public']
    if not data['enabled']:return None,'已停用'
    if not in_ceiling(public['host']):return None,'超出允许的网络范围'
    try:
        credentials=validate_materials(public,_decrypt(data,db,workspace))
        if credentials['server_sha256']!=public['server_sha256'] or credentials['client_sha256']!=public['client_sha256']:return None,'证书摘要不一致'
        return {**public,'_credentials':credentials},None
    except (OpcuaError,HTTPException):
        return None,'证书或安全存储不可用，请核查此授权'


def managed_decisions(workspace,db=None):
    if not enabled() and db is None and not os.getenv('GA_DATABASE_URL'):return {}
    def collect(db):
        result={}
        for row in _rows(db,workspace):
            data=_verify(row,workspace);identity=data['public']['id']
            if identity in result:raise HTTPException(409,'OPC UA 授权记录重复')
            # A broken certificate must not prevent other devices from reading.
            result[identity]=_materialize(data,db,workspace)[0] if enabled() else None
        return result
    if db is not None:return collect(db)
    with Session(engine()) as session:return collect(session)


class Create(Contract):
    request_id:UUID
    name:StrictStr=Field(min_length=1,max_length=100)
    host:StrictStr=Field(min_length=7,max_length=15)
    port:StrictInt=Field(default=4840,ge=1,le=65535)
    path:StrictStr=Field(default='/',min_length=1,max_length=200)
    node_id:StrictStr=Field(min_length=1,max_length=300)
    server_certificate:StrictStr=Field(min_length=4,max_length=45000,repr=False)
    client_certificate:StrictStr=Field(min_length=4,max_length=45000,repr=False)
    client_private_key:StrictStr=Field(min_length=4,max_length=45000,repr=False)
    @model_validator(mode='after')
    def valid_name(self):
        if not self.name.strip() or any(ord(c)<32 or ord(c)==127 for c in self.name):raise ValueError('授权名称无效')
        return self


class Status(Contract):
    request_id:UUID
    version:StrictInt=Field(ge=1)
    enabled:StrictBool


class Resolve(Contract):
    kind:StrictStr=Field(pattern=r'^(create|status)$')


def _existing(db,p,request_id,kind,signature=None):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_opkey(p.user_id,request_id)))
    if row and (row.kind not in (_OP,_STOP) or row.status!='internal' or row.data.get('actor_id')!=p.user_id or row.data.get('kind')!=kind or row.kind==_OP and signature is not None and row.data.get('signature')!=signature):raise HTTPException(409,'原请求与当前账号或内容不一致')
    return row


def _view(row,p,db):
    d=_verify(row,p.workspace_id);public=d['public'];available=False;reason=None
    if enabled() and d['enabled']:
        value,reason=_materialize(d,db,p.workspace_id);available=value is not None
    elif not d['enabled']:
        reason=None if in_ceiling(public['host']) else '超出允许的网络范围'
        available=enabled() and reason is None
    else:reason='图形授权未启用'
    return {'id':public['id'],'name':d['name'],'host':public['host'],'port':public['port'],'path':public['path'],'node_id':public['node_id'],'server_sha256':public['server_sha256'],'client_sha256':public['client_sha256'],'enabled':d['enabled'],'available':available,'reason':reason,'version':row.version}


@router.get('')
def listing(p:P,db:DB):
    require(p,'admin')
    ready=False
    if enabled():
        try:
            _,key=_cipher();_key_guard(db,key,p.workspace_id);ready=True
        except HTTPException:pass
    return {'enabled':enabled(),'vault_ready':ready,'networks':[str(n) for n in networks()] if enabled() else [],'authorizations':[_view(row,p,db) for row in _rows(db,p.workspace_id)]}


def _create(body:Create,p:P,db:DB):
    require(p,'admin');request=str(body.request_id);_oplock(db,p,request)
    cipher,key=_cipher();raw=body.model_dump(mode='json');raw['name']=raw['name'].strip()
    signature=hmac.new(key,json.dumps(raw,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
    previous=_existing(db,p,request,'create',signature)
    if previous:return _receipt(previous)
    lock_workspace(db,p.workspace_id);_key_guard(db,key,p.workspace_id,establish=True)
    if not enabled():raise HTTPException(403,'系统尚未启用 OPC UA 图形授权')
    _check_endpoint(body.host,body.port,body.path,body.node_id)
    files=[_b64(body.server_certificate),_b64(body.client_certificate),_b64(body.client_private_key)]
    entry={'workspace':p.workspace_id,'host':body.host,'port':body.port,'path':body.path,'node_id':body.node_id,'server_app_uri':_uri(files[0]),'client_app_uri':_uri(files[1])}
    from .opcua_readonly_transport import validate_materials,OpcuaError
    try:creds=validate_materials(entry,files)
    except OpcuaError as exc:raise HTTPException(422,str(exc)) from None
    public={**entry,'server_sha256':creds['server_sha256'],'client_sha256':creds['client_sha256']};public['id']=digest(public)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='managed-opcua-grant:'+public['id']).with_for_update());_current_admin(db,p)
    if row is None:
        sealed=cipher.encrypt(json.dumps({k:raw[k] for k in ('server_certificate','client_certificate','client_private_key')},separators=(',',':')).encode()).decode()
        row=Job(workspace_id=p.workspace_id,kind=_KIND,status='internal',idempotency_key='managed-opcua-grant:'+public['id'],data={'public':public,'name':raw['name'],'enabled':True,'ciphertext':sealed,'actor_id':p.user_id});db.add(row);db.flush();audit(db,p,'opcua.authorization.create',row.id,{'grant_id':public['id']})
    elif row.kind!=_KIND or row.status!='internal':raise HTTPException(409,'授权记录无法核对')
    elif not _verify(row,p.workspace_id)['enabled']:raise HTTPException(409,'已有停用授权，请在列表中恢复')
    result=_view(row,p,db);op=Job(workspace_id=p.workspace_id,kind=_OP,status='internal',idempotency_key=_opkey(p.user_id,request),data={'request_id':request,'actor_id':p.user_id,'kind':'create','signature':signature,'state':'succeeded','result':result});db.add(op);db.commit();return _receipt(op)


@router.post('')
async def create(request:Request,p:P,db:DB):
    # Do not let framework validation echo the submitted private key in a 422.
    chunks=bytearray()
    async for chunk in request.stream():
        if len(chunks)+len(chunk)>150000:raise HTTPException(413,'证书请求过大，未保存授权')
        chunks.extend(chunk)
    try:body=Create.model_validate_json(bytes(chunks))
    except (ValidationError,ValueError):raise HTTPException(422,'授权字段或证书格式无效，未保存授权') from None
    return await run_in_threadpool(_create,body,p,db)


@router.post('/{grant_id}/status')
def status(grant_id:str,body:Status,p:P,db:DB):
    require(p,'admin');request=str(body.request_id);_oplock(db,p,request);signature=digest({'grant_id':grant_id,'body':body.model_dump(mode='json')});previous=_existing(db,p,request,'status',signature)
    if previous:return _receipt(previous)
    lock_workspace(db,p.workspace_id);row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='managed-opcua-grant:'+grant_id,Job.kind==_KIND,Job.status=='internal').with_for_update())
    if row is None:raise HTTPException(404,'本工作区未找到此授权')
    d=_verify(row,p.workspace_id)
    if row.version!=body.version:raise HTTPException(409,'授权已变化，请刷新后核对')
    if body.enabled:
        if not enabled() or not in_ceiling(d['public']['host']):raise HTTPException(403,'设备已超出允许网络范围')
        from .opcua_readonly_transport import validate_materials,OpcuaError
        try:credentials=validate_materials(d['public'],_decrypt(d,db,p.workspace_id))
        except OpcuaError:raise HTTPException(409,'证书已失效或身份不符，请重新配置') from None
        if credentials['server_sha256']!=d['public']['server_sha256'] or credentials['client_sha256']!=d['public']['client_sha256']:raise HTTPException(409,'证书摘要不一致，请重新配置')
    _current_admin(db,p);row.data={**d,'enabled':body.enabled};row.version+=1;audit(db,p,'opcua.authorization.status',row.id,{'enabled':body.enabled})
    result=_view(row,p,db);op=Job(workspace_id=p.workspace_id,kind=_OP,status='internal',idempotency_key=_opkey(p.user_id,request),data={'request_id':request,'actor_id':p.user_id,'kind':'status','signature':signature,'state':'succeeded','result':result});db.add(op);db.commit();return _receipt(op)


@router.post('/operations/{request_id}/resolve')
def resolve(request_id:UUID,body:Resolve,p:P,db:DB):
    _oplock(db,p,request_id);row=_existing(db,p,str(request_id),body.kind)
    if row:return _receipt(row)
    member(db,p,locked=True)
    row=Job(workspace_id=p.workspace_id,kind=_STOP,status='internal',idempotency_key=_opkey(p.user_id,request_id),data={'request_id':str(request_id),'actor_id':p.user_id,'kind':body.kind,'signature':None,'state':'cancelled','result':None});db.add(row);db.flush();audit(db,p,'opcua.authorization.cancel',row.id,{'kind':body.kind});db.commit();return _receipt(row)
