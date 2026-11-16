"""Workspace-owned Modbus read authorizations within a server network ceiling.

The network ceiling is process-owned.  A workspace administrator can only
select a single literal address and a bounded register range inside it.
"""
import hashlib
import ipaddress
import json
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .contracts import Contract, digest
from .db import engine
from .ftps_gateway_api import _current_admin, _lock
from .models import Job
from .services import audit, require
from .setup_api import P, DB

router = APIRouter(prefix='/api/modbus/authorizations', tags=['Modbus 授权'])
_KIND = 'managed_modbus_grant_v1'
_OP = 'managed_modbus_grant_op_v1'
_STOP = 'managed_modbus_grant_stop_v1'
_PRIVATE = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def enabled():
    return os.getenv('GA_MANAGED_MODBUS_ENABLED', '') == '1'


def networks():
    raw = os.getenv('GA_MANAGED_GATEWAY_NETWORKS', '')
    if not raw:
        return []
    try:
        values = [ipaddress.IPv4Network(x.strip(), strict=True) for x in raw.split(',')]
        if len(values)>32 or any(not any(n.subnet_of(parent) for parent in _PRIVATE) or n.prefixlen < 16 for n in values):
            raise ValueError()
        return values
    except (TypeError, ValueError):
        raise HTTPException(503, '设备授权网络范围配置无效，请由系统管理员检查') from None


def canonical(workspace, host, port, unit, start, end, functions):
    ip = ipaddress.IPv4Address(host)
    value = {'workspace': workspace, 'host': str(ip), 'port': port, 'unit': unit,
             'start': start, 'end': end, 'functions': sorted(set(functions))}
    value['id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def in_ceiling(host):
    try:
        ip = ipaddress.IPv4Address(host)
        return str(ip) == host and any(ip in n and ip not in (n.network_address,n.broadcast_address) for n in networks())
    except (ValueError, TypeError):
        return False


def lock_workspace(db, workspace, *, shared=False):
    """Serialize a managed grant change with each actual network read."""
    if db.get_bind().dialect.name == 'postgresql':
        number = int.from_bytes(hashlib.sha256(('managed-modbus\0'+workspace).encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock_shared(:k)' if shared else 'SELECT pg_advisory_xact_lock(:k)'), {'k': number})


def _rows(db, workspace):
    rows = list(db.scalars(select(Job).where(Job.workspace_id == workspace, Job.kind == _KIND, Job.status == 'internal').limit(101)))
    if len(rows) > 100:
        raise HTTPException(409, '设备授权过多，请联系管理员处理')
    return rows


def _checked(row, workspace):
    data = row.data
    try:
        grant = data['grant']
        expected = canonical(workspace, grant['host'], grant['port'], grant['unit'], grant['start'], grant['end'], grant['functions'])
        if grant != expected or type(data['enabled']) is not bool or not isinstance(data['name'], str):
            raise ValueError()
        return data
    except (KeyError, TypeError, ValueError):
        raise HTTPException(409, '设备授权记录无法核对，请联系管理员') from None


def managed_decisions(workspace, db=None):
    # Persisted denials must continue to override legacy environment grants
    # when the self-service feature is switched off. Pure transport tests with
    # no configured DB and no supplied session retain their original behavior.
    if not enabled() and db is None and not os.getenv('GA_DATABASE_URL'):return {}
    def collect(db):
        result = {}
        for row in _rows(db, workspace):
            data = _checked(row, workspace)
            grant=data['grant']
            if grant['id'] in result:raise HTTPException(409,'设备授权记录重复，请联系管理员')
            result[grant['id']] = grant if enabled() and data['enabled'] and in_ceiling(grant['host']) else None
        return result
    if db is not None:return collect(db)
    with Session(engine()) as session:return collect(session)


class Create(Contract):
    request_id: UUID
    name: StrictStr = Field(min_length=1, max_length=100)
    host: StrictStr = Field(min_length=7, max_length=15)
    port: StrictInt = Field(default=502, ge=1, le=65535)
    unit: StrictInt = Field(default=1, ge=1, le=247)
    start: StrictInt = Field(ge=0, le=65535)
    end: StrictInt = Field(ge=0, le=65535)
    functions: list[StrictInt] = Field(min_length=1, max_length=2)

    @model_validator(mode='after')
    def validate_scope(self):
        if self.start > self.end or any(f not in (3,4) for f in self.functions) or len(set(self.functions)) != len(self.functions):
            raise ValueError('寄存器范围或读取类型无效')
        if not self.name.strip() or any(ord(c)<32 or ord(c)==127 for c in self.name):
            raise ValueError('授权名称无效')
        return self


class Status(Contract):
    request_id: UUID
    version: StrictInt = Field(ge=1)
    enabled: StrictBool


class Resolve(Contract):
    kind: StrictStr = Field(pattern=r'^(create|status)$')
    submission: dict
    grant_id: StrictStr | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def _op_key(actor_id, request_id):
    return 'managed-modbus-op:'+actor_id+':'+str(request_id)


def _lock_op(db,p,request_id):
    _lock(db,p.workspace_id,_op_key(p.user_id,request_id))


def _receipt(row):
    return {'request_id':row.data['request_id'], 'kind':row.data['kind'],
            'state':row.data['state'], 'result':row.data.get('result')}


def _existing(db,p,kind,submission,grant_id):
    request_id=submission['request_id']
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_op_key(p.user_id,request_id)))
    if row is not None:
        if row.kind not in (_OP,_STOP) or row.status!='internal' or row.data.get('actor_id')!=p.user_id or row.data.get('kind')!=kind or row.data.get('submission')!=submission or row.data.get('grant_id')!=grant_id:
            raise HTTPException(409, '原请求与当前账号或内容不一致')
    return row


def _view(row,p):
    data=_checked(row,p.workspace_id)
    return {'id':data['grant']['id'],'name':data['name'],'host':data['grant']['host'], 'port':data['grant']['port'],
            'unit':data['grant']['unit'],'start':data['grant']['start'],'end':data['grant']['end'],
            'functions':data['grant']['functions'],'enabled':data['enabled'],'available':enabled() and in_ceiling(data['grant']['host']), 'version':row.version}


@router.get('')
def list_authorizations(p:P,db:DB):
    require(p,'admin')
    return {'enabled':enabled(), 'networks':[str(x) for x in networks()] if enabled() else [],
            'authorizations':[_view(row,p) for row in _rows(db,p.workspace_id)]}


@router.post('')
def create(body:Create,p:P,db:DB):
    require(p,'admin'); submission=body.model_dump(mode='json'); submission['name']=submission['name'].strip()
    _lock_op(db,p,submission['request_id']);previous=_existing(db,p,'create',submission,None)
    if previous:return _receipt(previous)
    lock_workspace(db,p.workspace_id)
    if not enabled() or not in_ceiling(body.host):raise HTTPException(403,'此设备地址不在系统允许的内网范围，未创建授权')
    grant=canonical(p.workspace_id,body.host,body.port,body.unit,body.start,body.end,body.functions)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='managed-modbus-grant:'+grant['id']).with_for_update())
    _current_admin(db,p)
    if row is None:
        row=Job(workspace_id=p.workspace_id,kind=_KIND,status='internal',idempotency_key='managed-modbus-grant:'+grant['id'],data={'grant':grant,'name':submission['name'],'enabled':True,'actor_id':p.user_id})
        db.add(row);db.flush();audit(db,p,'modbus.authorization.create',row.id,{'grant_id':grant['id']})
    elif row.kind!=_KIND or row.status!='internal':raise HTTPException(409,'授权记录无法核对')
    elif not _checked(row,p.workspace_id)['enabled']:raise HTTPException(409,'已有停用授权，请在列表中恢复')
    result=_view(row,p)
    op=Job(workspace_id=p.workspace_id,kind=_OP,status='internal',idempotency_key=_op_key(p.user_id,submission['request_id']),data={'request_id':submission['request_id'],'actor_id':p.user_id,'kind':'create','submission':submission,'grant_id':None,'state':'succeeded','result':result})
    db.add(op);db.commit();return _receipt(op)


@router.post('/{grant_id}/status')
def set_status(grant_id:str,body:Status,p:P,db:DB):
    require(p,'admin');submission=body.model_dump(mode='json');_lock_op(db,p,submission['request_id']);previous=_existing(db,p,'status',submission,grant_id)
    if previous:return _receipt(previous)
    lock_workspace(db,p.workspace_id)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='managed-modbus-grant:'+grant_id,Job.kind==_KIND,Job.status=='internal').with_for_update())
    if row is None:raise HTTPException(404,'本工作区未找到此授权')
    data=_checked(row,p.workspace_id)
    if row.version!=body.version:raise HTTPException(409,'授权已变化，请刷新后核对')
    if body.enabled and (not enabled() or not in_ceiling(data['grant']['host'])):raise HTTPException(403,'设备地址已超出允许的内网范围，不能恢复')
    _current_admin(db,p);row.data={**data,'enabled':body.enabled};row.version+=1
    audit(db,p,'modbus.authorization.status',row.id,{'enabled':body.enabled})
    result=_view(row,p);op=Job(workspace_id=p.workspace_id,kind=_OP,status='internal',idempotency_key=_op_key(p.user_id,submission['request_id']),data={'request_id':submission['request_id'],'actor_id':p.user_id,'kind':'status','submission':submission,'grant_id':grant_id,'state':'succeeded','result':result})
    db.add(op);db.commit();return _receipt(op)


@router.post('/operations/{request_id}/resolve')
def resolve(request_id:UUID,body:Resolve,p:P,db:DB):
    # A former administrator may recover or fence only their own prior intent.
    raw=body.submission
    try:
        submission=(Create if body.kind=='create' else Status).model_validate(raw).model_dump(mode='json')
        if body.kind=='create':submission['name']=submission['name'].strip()
    except Exception:
        raise HTTPException(422,'原请求格式无法核对') from None
    if submission['request_id']!=str(request_id) or (body.kind=='create') != (body.grant_id is None):raise HTTPException(422,'原请求身份无法核对')
    _lock_op(db,p,request_id);row=_existing(db,p,body.kind,submission,body.grant_id)
    if row:return _receipt(row)
    from .device_gateway_store_v268 import member
    member(db,p,locked=True)
    # This can only fence the caller's prior request; no new network access.
    row=Job(workspace_id=p.workspace_id,kind=_STOP,status='internal',idempotency_key=_op_key(p.user_id,request_id),data={'request_id':str(request_id),'actor_id':p.user_id,'kind':body.kind,'submission':submission,'grant_id':body.grant_id,'state':'cancelled','result':None})
    db.add(row);db.flush();audit(db,p,'modbus.authorization.cancel',row.id,{'kind':body.kind});db.commit();return _receipt(row)
