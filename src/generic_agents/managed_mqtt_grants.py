"""Workspace-scoped MQTT broker credentials, bounded by a private-IP ceiling.

Only MQTT over TLS with the system CA and the IP address present in the server
certificate is supported. Existing server-managed brokers remain separate.
"""
from contextlib import nullcontext
import hashlib,json,os,re
from cryptography.fernet import Fernet,InvalidToken
from fastapi import APIRouter,HTTPException,Request
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .contracts import digest
from .db import engine
from .ftps_gateway_api import _current_admin
from .managed_file_grants import _key,_key_guard,_mac,_safe_text,_uuid,KEY_MARKER
from .managed_modbus_grants import in_ceiling,networks
from .models import Job
from .services import audit,require
from .setup_api import P,DB

router=APIRouter(prefix='/api/mqtt/authorizations',tags=['MQTT broker authorization'])
KIND='managed_mqtt_grant_v1';OP='managed_mqtt_grant_op_v1';STOP='managed_mqtt_grant_stop_v1'
REF=re.compile(r'^GA_MQTT_VAULT_([0-9A-F]{64})_(USER|PASS)$')

def enabled():return os.getenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','')=='1'
def lock_workspace(db,workspace,*,shared=False):
    if db.get_bind().dialect.name=='postgresql':
        n=int.from_bytes(hashlib.sha256(('managed-mqtt\0'+workspace).encode()).digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock_shared(:n)' if shared else 'SELECT pg_advisory_xact_lock(:n)'),{'n':n})

def _guard(db,key,workspace,*,establish=False):
    if establish and db.get_bind().dialect.name=='postgresql':
        n=int.from_bytes(hashlib.sha256(b'managed-file-key-identity').digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:n)'),{'n':n})
    if not db.scalar(select(Job.id).where(Job.kind==KEY_MARKER).limit(1)) and db.scalar(select(Job.id).where(Job.kind==KIND).limit(1)):
        raise HTTPException(503,'MQTT 原加密密钥身份缺失，请从备份恢复')
    _key_guard(db,key,workspace,establish=establish)

def _row(db,w,k):return db.scalar(select(Job).where(Job.workspace_id==w,Job.idempotency_key==k).execution_options(populate_existing=True))
def _op(actor,rid):return 'managed-mqtt-op:'+str(actor)+':'+rid
def _grant(identity):return 'managed-mqtt-grant:'+identity
def _refs(identity):return ('GA_MQTT_VAULT_'+identity.upper()+'_USER','GA_MQTT_VAULT_'+identity.upper()+'_PASS')
def ref_id(user,passwd):
    a=REF.fullmatch(user) if isinstance(user,str) else None;b=REF.fullmatch(passwd) if isinstance(passwd,str) else None
    if not a and not b:return None
    if not a or not b or a.group(1)!=b.group(1) or a.group(2)!='USER' or b.group(2)!='PASS':raise HTTPException(422,'MQTT 图形授权引用不匹配')
    return a.group(1).lower()
def _checked(row,w):
    try:
        d=row.data;g=d['grant'];identity=digest({'workspace':w,'request_id':d['request_id'],'kind':'mqtt'})
        if row.kind!=KIND or row.status!='internal' or type(d['enabled']) is not bool or not isinstance(d['cipher'],str) or g['id']!=identity or g['workspace_id']!=w or row.idempotency_key!=_grant(identity):raise ValueError()
        if not re.fullmatch(r'mqtts://[0-9.]+:[0-9]+',g['broker']):raise ValueError()
        return d
    except (ValueError,TypeError,KeyError):raise HTTPException(409,'MQTT 授权记录无法核对') from None

def get_grant(workspace,grant_id,db=None):
    with (nullcontext(db) if db is not None else Session(engine())) as session:
        if not enabled():raise HTTPException(403,'MQTT 图形授权未启用')
        row=_row(session,workspace,_grant(grant_id))
        if not row:raise HTTPException(403,'MQTT 授权不存在')
        d=_checked(row,workspace);g=d['grant']
        from urllib.parse import urlsplit
        host=urlsplit(g['broker']).hostname
        if not d['enabled'] or not in_ceiling(host):raise HTTPException(403,'MQTT 授权已停用或不在允许范围')
        key=_key();_guard(session,key,workspace)
        return {**g,'version':row.version,'enabled':True}

def _decrypt(d,workspace,grant_id):
    try:
        secret=json.loads(Fernet(_key()).decrypt(d['cipher'].encode()))
        if secret.get('workspace_id')!=workspace or secret.get('id')!=grant_id or secret.get('grant_hash')!=digest(d['grant']) or not _safe_text(secret.get('username'),256) or not _safe_text(secret.get('password'),256):raise ValueError()
        return secret['username'],secret['password']
    except (InvalidToken,ValueError,KeyError,TypeError,UnicodeError):raise HTTPException(503,'MQTT 凭据无法读取，请恢复原加密密钥') from None

def credentials(workspace,grant_id,db=None):
    with (nullcontext(db) if db is not None else Session(engine())) as session:
        get_grant(workspace,grant_id,session);row=_row(session,workspace,_grant(grant_id));d=_checked(row,workspace)
        return _decrypt(d,workspace,grant_id)

def _public(d,version):
    g=d['grant'];user,password=_refs(g['id'])
    return {**g,'username_ref':user,'password_ref':password,'enabled':d['enabled'],'version':version}

async def _body(request):
    chunks=[];n=0
    async for chunk in request.stream():
        n+=len(chunk)
        if n>8192:raise HTTPException(413,'请求过大')
        chunks.append(chunk)
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError()
            result[k]=v
        return result
    try:return json.loads(b''.join(chunks),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (UnicodeError,ValueError,RecursionError):raise HTTPException(422,'授权内容无效') from None

@router.get('')
def listing(p:P,db:DB):
    require(p,'admin');ready=False
    if enabled():
        try:_guard(db,_key(),p.workspace_id);ready=True
        except HTTPException:pass
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal').order_by(Job.created_at.desc(),Job.id.desc())))
    return {'enabled':enabled(),'key_ready':ready,'networks':[str(n) for n in networks()] if enabled() else [],'authorizations':[_public(_checked(r,p.workspace_id),r.version) for r in rows]}

@router.post('')
async def create(request:Request,p:P,db:DB):
    require(p,'admin')
    if not enabled():raise HTTPException(409,'MQTT 图形授权未启用')
    body=await _body(request)
    if not isinstance(body,dict) or set(body)!={'request_id','name','host','port','username','password'}:raise HTTPException(422,'授权字段不完整')
    rid=_uuid(body['request_id']);host=body['host'];port=body['port']
    if not in_ceiling(host) or type(port) is not int or not 1<=port<=65535 or not all(_safe_text(body[k],n) for k,n in [('name',100),('username',256),('password',256)]):raise HTTPException(422,'名称、地址或凭据无效')
    canonical={**body,'name':body['name'].strip()};key=_key();fingerprint=_mac(key,canonical);lock_workspace(db,p.workspace_id);_current_admin(db,p);_guard(db,key,p.workspace_id,establish=True)
    prior=_row(db,p.workspace_id,_op(p.user_id,rid))
    if prior:
        if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if prior.kind!=OP or prior.data.get('fingerprint')!=fingerprint or prior.data.get('kind')!='create':raise HTTPException(409,'原请求已绑定其他内容')
        return {'request_id':rid,'state':'succeeded','authorization':prior.data['result']}
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal')))
    if sum(_checked(r,p.workspace_id)['enabled'] for r in rows)>=100:raise HTTPException(409,'有效 MQTT 授权已达上限')
    identity=digest({'workspace':p.workspace_id,'request_id':rid,'kind':'mqtt'});g={'id':identity,'workspace_id':p.workspace_id,'name':canonical['name'],'broker':f'mqtts://{host}:{port}'}
    secret={'workspace_id':p.workspace_id,'id':identity,'grant_hash':digest(g),'username':body['username'],'password':body['password']}
    cipher=Fernet(key).encrypt(json.dumps(secret,ensure_ascii=False).encode()).decode()
    row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=_grant(identity),data={'request_id':rid,'enabled':True,'grant':g,'cipher':cipher});db.add(row);db.flush();result=_public(row.data,row.version)
    db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op(p.user_id,rid),data={'kind':'create','actor_id':p.user_id,'fingerprint':fingerprint,'result':result}));audit(db,p,'mqtt.authorization.created',row.id,{'grant_id':identity});db.commit()
    return {'request_id':rid,'state':'succeeded','authorization':result}

@router.post('/{identity}/status')
async def status(identity:str,request:Request,p:P,db:DB):
    require(p,'admin');body=await _body(request)
    if not enabled():raise HTTPException(409,'MQTT 图形授权未启用')
    if not isinstance(body,dict) or set(body)!={'request_id','version','enabled'} or type(body['version']) is not int or type(body['enabled']) is not bool or not re.fullmatch(r'[a-f0-9]{64}',identity):raise HTTPException(422,'授权状态请求无效')
    rid=_uuid(body['request_id']);fp=digest({'id':identity,'version':body['version'],'enabled':body['enabled']});lock_workspace(db,p.workspace_id);_current_admin(db,p)
    prior=_row(db,p.workspace_id,_op(p.user_id,rid))
    if prior:
        if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if prior.kind!=OP or prior.data.get('fingerprint')!=fp or prior.data.get('kind')!='status':raise HTTPException(409,'原请求已绑定其他内容')
        return {'request_id':rid,'state':'succeeded','authorization':prior.data['result']}
    row=_row(db,p.workspace_id,_grant(identity))
    if not row:raise HTTPException(404,'授权不存在')
    d=_checked(row,p.workspace_id)
    if row.version!=body['version']:raise HTTPException(409,'授权状态已变化，请刷新')
    if body['enabled']:
        key=_key();_guard(db,key,p.workspace_id)
        from urllib.parse import urlsplit
        if not in_ceiling(urlsplit(d['grant']['broker']).hostname):raise HTTPException(403,'地址已超出允许网络')
        _decrypt(d,p.workspace_id,identity)
    row.data={**d,'enabled':body['enabled']};row.version+=1;db.flush();result=_public(row.data,row.version)
    db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op(p.user_id,rid),data={'kind':'status','actor_id':p.user_id,'fingerprint':fp,'result':result}));audit(db,p,'mqtt.authorization.status',row.id,{'enabled':body['enabled']});db.commit()
    return {'request_id':rid,'state':'succeeded','authorization':result}

@router.get('/operations/{request_id}')
def operation(request_id:str,p:P,db:DB):
    rid=_uuid(request_id);row=_row(db,p.workspace_id,_op(p.user_id,rid))
    if not row:raise HTTPException(404,'原授权请求尚未找到')
    if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
    if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求无法核对')
    return {'request_id':rid,'state':'succeeded','authorization':row.data['result']}

@router.post('/operations/{request_id}/cancel')
def cancel(request_id:str,p:P,db:DB):
    rid=_uuid(request_id);lock_workspace(db,p.workspace_id);row=_row(db,p.workspace_id,_op(p.user_id,rid))
    if row:
        if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
        if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求无法核对')
        return {'request_id':rid,'state':'succeeded','authorization':row.data['result']}
    db.add(Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_op(p.user_id,rid),data={'actor_id':p.user_id}));db.commit()
    return {'request_id':rid,'state':'cancelled'}
