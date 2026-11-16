"""Graphical, workspace-scoped FTPS authorizations. No TLS bypass or credential files."""
from contextlib import nullcontext
import hashlib,json,os,ssl
from uuid import UUID
from cryptography.fernet import Fernet,InvalidToken
from fastapi import APIRouter,HTTPException,Request
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .contracts import digest
from .db import engine
from .models import Job
from .services import audit,require
from .setup_api import P,DB
from .managed_modbus_grants import in_ceiling,networks
from .managed_file_grants import _key,_key_guard,_mac,_safe_text,_uuid,KEY_MARKER
from .ftps_gateway_api import _current_admin

router=APIRouter(prefix='/api/ftps-authorizations',tags=['FTPS 目录授权'])
KIND='managed_ftps_grant_v1';OP='managed_ftps_operation_v1';STOP='managed_ftps_stop_v1';DENY='managed_ftps_deny_v1'
def enabled():return os.getenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','')=='1'
def lock_workspace(db,workspace,*,shared=False):
 if db.get_bind().dialect.name=='postgresql':
  n=int.from_bytes(hashlib.sha256(('managed-ftps\0'+workspace).encode()).digest()[:8],'big',signed=True)
  db.execute(text('SELECT pg_advisory_xact_lock_shared(:n)' if shared else 'SELECT pg_advisory_xact_lock(:n)'),{'n':n})
def _row(db,w,k):return db.scalar(select(Job).where(Job.workspace_id==w,Job.idempotency_key==k).execution_options(populate_existing=True))
def _op(actor,rid):return 'managed-ftps-op:'+str(actor)+':'+rid
def _grant(identity):return 'managed-ftps-grant:'+identity
def _target(g):return digest({'host':g['endpoint']['host'],'port':g['endpoint']['port'],'root':g['root']})
def _deny(target):return 'managed-ftps-deny:'+target
def _guard(db,key,w,establish=False):
 # A missing shared marker must never be recreated while FTPS ciphertext exists.
 if not db.scalar(select(Job.id).where(Job.kind==KEY_MARKER).limit(1)) and db.scalar(select(Job.id).where(Job.kind==KIND).limit(1)):
  raise HTTPException(503,'FTPS 原密钥身份缺失，请从备份恢复')
 _key_guard(db,key,w,establish=establish)
def _checked(row,w):
 try:
  d=row.data;g=d['grant'];rid=str(UUID(d['request_id']));identity=digest({'workspace':w,'protocol':'ftps','request_id':rid})
  if row.kind!=KIND or row.status!='internal' or not isinstance(d['enabled'],bool) or g['_workspace']!=w or g['_managed'] is not True or g['id']!=identity or row.idempotency_key!=_grant(identity) or d['target']!=_target(g) or not isinstance(d['cipher'],str):raise ValueError()
  return d
 except (ValueError,TypeError,KeyError):raise HTTPException(409,'FTPS 授权记录无法核对') from None
def _rows(db,w):return list(db.scalars(select(Job).where(Job.workspace_id==w,Job.kind==KIND,Job.status=='internal')))
def _denied(db,w,target):
 row=_row(db,w,_deny(target))
 if row is None:return False
 if row.kind!=DENY or row.status!='internal' or row.data!={'target':target,'denied':True}:raise HTTPException(409,'FTPS 撤销记录无法核对')
 return True
def merge(workspace,legacy,db=None):
 if not enabled() and db is None and not os.getenv('GA_DATABASE_URL'):return legacy
 with (nullcontext(db) if db is not None else Session(engine())) as session:
  out=[g for g in legacy if not _denied(session,workspace,_target(g))]
  if enabled():
   for row in _rows(session,workspace):
    data=_checked(row,workspace);g=data['grant']
    if data['enabled'] and in_ceiling(g['endpoint']['host']):out.append(g)
  return out
def credentials(grant):
 db=grant.get('_db');g={k:v for k,v in grant.items() if k!='_db'};w=g['_workspace']
 with (nullcontext(db) if db is not None else Session(engine())) as session:
  row=_row(session,w,_grant(g['id']))
  if not row:raise ValueError('授权不存在')
  d=_checked(row,w)
  if not enabled() or not d['enabled'] or d['grant']!=g or not in_ceiling(g['endpoint']['host']):raise ValueError('授权已撤销')
  try:
   key=_key();_guard(session,key,w);v=json.loads(Fernet(key).decrypt(d['cipher'].encode()))
   if v.get('workspace')!=w or v.get('id')!=g['id'] or v.get('grant_hash')!=digest(g) or not _safe_text(v.get('username'),256) or not _safe_text(v.get('password'),256) or not isinstance(v.get('ca_pem'),str):raise ValueError()
   return v['username'],v['password'],v['ca_pem']
  except (InvalidToken,ValueError,KeyError,TypeError):raise ValueError('FTPS 凭据无法读取，请恢复原密钥') from None
def _public(g,origin,available,version):
 e=g['endpoint'];return {'id':g['id'],'protocol':'ftps','name':g['name'],'host':e['host'],'port':e['port'],'tls_name':e['tls_name'],'root':g['root'],'origin':origin,'enabled':available,'version':version}
def _views(db,w):
 from .ftps_transport import legacy_grants
 out=[_public(g,'server',not _denied(db,w,_target(g)),0) for g in legacy_grants(w)]
 for row in _rows(db,w):
  d=_checked(row,w);g=d['grant'];out.append(_public(g,'workspace',d['enabled'] and in_ceiling(g['endpoint']['host']),row.version))
 return out
async def _body(request):
 chunks=[];size=0
 async for chunk in request.stream():
  size+=len(chunk)
  if size>262144:raise HTTPException(413,'授权内容超过大小限制')
  chunks.append(chunk)
 def pairs(items):
  result={}
  for k,v in items:
   if k in result:raise ValueError()
   result[k]=v
  return result
 try:return json.loads(b''.join(chunks),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
 except (UnicodeError,ValueError,RecursionError):raise HTTPException(422,'授权内容无效') from None
def _canonical(body):
 from .ftps_transport import _endpoint,_path,FTPSError
 fields={'request_id','name','host','port','tls_name','root','data_port_min','data_port_max','username','password','ca_pem','server_chroot_confirmed'}
 if not isinstance(body,dict) or set(body)!=fields:raise HTTPException(422,'授权字段不完整')
 rid=_uuid(body['request_id'])
 if not all(_safe_text(body.get(k),n) for k,n in [('name',100),('username',256),('password',256)]) or not in_ceiling(body.get('host')):raise HTTPException(422,'名称、凭据或设备地址无效')
 try:
  ep=_endpoint({k:body[k] for k in ('host','port','tls_name','data_port_min','data_port_max')});root=_path(body['root']);ca=body['ca_pem']
  if body['server_chroot_confirmed'] is not True or not isinstance(ca,str) or len(ca)>131072 or 'PRIVATE KEY' in ca:raise ValueError()
  if ca:ssl.create_default_context(cadata=ca)
 except (FTPSError,ValueError,TypeError,ssl.SSLError):raise HTTPException(422,'地址、TLS 证书、目录或授权确认无效') from None
 return {'request_id':rid,'name':body['name'].strip(),'endpoint':ep,'root':root,'username':body['username'],'password':body['password'],'ca_pem':ca}
@router.get('/grants')
def list_grants(p:P,db:DB):
 require(p,'admin');ready=False
 if enabled():
  try:key=_key();_guard(db,key,p.workspace_id);ready=True
  except HTTPException:pass
 return {'enabled':enabled(),'key_ready':ready,'networks':[str(x) for x in networks()] if enabled() else [],'grants':_views(db,p.workspace_id) if enabled() else []}
@router.post('/grants')
async def create_grant(request:Request,p:P,db:DB):
 require(p,'admin')
 if not enabled():raise HTTPException(409,'FTPS 图形授权未启用')
 v=_canonical(await _body(request));key=_key();rid=v['request_id'];fingerprint=_mac(key,v);w=p.workspace_id
 lock_workspace(db,w);_current_admin(db,p);_guard(db,key,w,True);prior=_row(db,w,_op(p.user_id,rid))
 if prior:
  if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
  if prior.kind!=OP or prior.data.get('fingerprint')!=fingerprint or prior.data.get('kind')!='create':raise HTTPException(409,'此请求已绑定其他授权')
  return {'request_id':rid,'state':'succeeded','grant':prior.data['result']}
 if sum(_checked(r,w)['enabled'] for r in _rows(db,w))>=100:raise HTTPException(409,'授权数量已达上限')
 identity=digest({'workspace':w,'protocol':'ftps','request_id':rid});g={'id':identity,'name':v['name'],'endpoint':v['endpoint'],'root':v['root'],'_managed':True,'_workspace':w};cipher=Fernet(key).encrypt(json.dumps({'workspace':w,'id':identity,'grant_hash':digest(g),'username':v['username'],'password':v['password'],'ca_pem':v['ca_pem']},ensure_ascii=False).encode()).decode()
 row=Job(workspace_id=w,kind=KIND,status='internal',idempotency_key=_grant(identity),data={'request_id':rid,'target':_target(g),'enabled':True,'grant':g,'cipher':cipher});db.add(row);db.flush();result=_public(g,'workspace',True,row.version)
 db.add(Job(workspace_id=w,kind=OP,status='internal',idempotency_key=_op(p.user_id,rid),data={'kind':'create','request_id':rid,'actor_id':p.user_id,'fingerprint':fingerprint,'result':result}));audit(db,p,'ftps.authorization.created',row.id,{'grant_id':identity});db.commit();return {'request_id':rid,'state':'succeeded','grant':result}
@router.post('/grants/{identity}/revoke')
async def revoke(identity:str,request:Request,p:P,db:DB):
 from .ftps_transport import legacy_grants
 require(p,'admin')
 if not enabled():raise HTTPException(409,'FTPS 图形授权未启用')
 body=await _body(request)
 if not isinstance(body,dict) or set(body)!={'request_id','version'} or type(body['version']) is not int or body['version']<0 or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):raise HTTPException(422,'撤权请求无效')
 rid=_uuid(body['request_id']);fp=digest({'id':identity,'version':body['version']});w=p.workspace_id;lock_workspace(db,w);_current_admin(db,p);prior=_row(db,w,_op(p.user_id,rid))
 if prior:
  if prior.kind==STOP:return {'request_id':rid,'state':'cancelled'}
  if prior.kind!=OP or prior.data.get('fingerprint')!=fp or prior.data.get('kind')!='revoke':raise HTTPException(409,'此请求已绑定其他操作')
  return {'request_id':rid,'state':'succeeded','grant':prior.data['result']}
 row=_row(db,w,_grant(identity))
 if row:
  d=_checked(row,w)
  if row.version!=body['version']:raise HTTPException(409,'授权已变化')
  g=d['grant'];row.data={**d,'enabled':False};row.version+=1;version=row.version;origin='workspace'
 else:
  g=next((x for x in legacy_grants(w) if x['id']==identity),None)
  if g is None or body['version']!=0:raise HTTPException(404,'授权不存在或已变化')
  version=0;origin='server'
 target=_target(g)
 if not _denied(db,w,target):db.add(Job(workspace_id=w,kind=DENY,status='internal',idempotency_key=_deny(target),data={'target':target,'denied':True}))
 result=_public(g,origin,False,version);db.add(Job(workspace_id=w,kind=OP,status='internal',idempotency_key=_op(p.user_id,rid),data={'kind':'revoke','request_id':rid,'actor_id':p.user_id,'fingerprint':fp,'result':result}));audit(db,p,'ftps.authorization.revoked',identity,{'grant_id':identity});db.commit();return {'request_id':rid,'state':'succeeded','grant':result}
@router.get('/operations/{request_id}')
def operation(request_id:str,p:P,db:DB):
 rid=_uuid(request_id);row=_row(db,p.workspace_id,_op(p.user_id,rid))
 if not row:raise HTTPException(404,'原授权请求尚未找到')
 if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
 if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求无法核对')
 return {'request_id':rid,'state':'succeeded','grant':row.data['result']}
@router.post('/operations/{request_id}/cancel')
def cancel(request_id:str,p:P,db:DB):
 rid=_uuid(request_id);lock_workspace(db,p.workspace_id);_current_admin(db,p);row=_row(db,p.workspace_id,_op(p.user_id,rid))
 if row:
  if row.kind==STOP:return {'request_id':rid,'state':'cancelled'}
  if row.kind!=OP or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求无法核对')
  return {'request_id':rid,'state':'succeeded','grant':row.data['result']}
 db.add(Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_op(p.user_id,rid),data={'request_id':rid,'actor_id':p.user_id}));db.commit();return {'request_id':rid,'state':'cancelled'}
