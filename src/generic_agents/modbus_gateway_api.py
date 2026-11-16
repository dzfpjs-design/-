"""Workspace-scoped registration and explicit read-only Modbus acquisitions."""
from __future__ import annotations
import copy
import json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr, model_validator
from sqlalchemy import select
from .contracts import Contract, digest
from .models import Job, Audit, now
from .setup_api import P, DB
from .services import audit, require
from .ftps_gateway_api import _current_admin, _lock
from .modbus_readonly_transport import ModbusError, grants, get_grant, read_registers, decode_registers

router = APIRouter(prefix='/api/modbus')
_CONNECTION = 'modbus_connection_v1'
_READING = 'modbus_reading_v1'

class Register(Contract):
    request_id: UUID
    name: StrictStr = Field(min_length=1, max_length=100)
    grant_id: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    function: Literal[3,4]
    address: StrictInt = Field(ge=0, le=65535)
    value_type: Literal['uint16','int16','uint32','int32','float32']
    word_order: Literal['big','little'] = 'big'
    scale: float = Field(default=1, allow_inf_nan=False, ge=-1e12, le=1e12)
    offset: float = Field(default=0, allow_inf_nan=False, ge=-1e12, le=1e12)
    unit_label: StrictStr = Field(default='', max_length=40)

    @model_validator(mode='after')
    def valid_text(self):
        if not self.name.strip() or any(ord(c)<32 for c in self.name+self.unit_label): raise ValueError('请填写有效名称和单位')
        return self

class Read(Contract):
    request_id: UUID
    version: StrictInt = Field(ge=1)

def _admin(p): require(p, 'admin')
def _find(db,p,identity,kind):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(identity),Job.kind==kind,Job.status=='internal'))
    if row is None: raise HTTPException(404,'本工作区未找到对应记录')
    return row

def _authorized(workspace, data, db=None):
    try:
        grant=get_grant(workspace,data['grant_id'],db)
        count=1 if data['value_type'].endswith('16') else 2
        if data['function'] not in grant['functions'] or data['address']<grant['start'] or data['address']+count-1>grant['end']:
            raise ModbusError('寄存器读取超出授权范围')
        return grant,count
    except (ModbusError,KeyError) as e: raise HTTPException(409,str(e) if isinstance(e,ModbusError) else '连接记录无法核对') from None

def _view(row):
    return {'id':row.id,'version':row.version, **copy.deepcopy(row.data), 'created_at':row.created_at.isoformat()}


_STOP='modbus_operation_stop_v1'

class Resolve(Contract):
    kind: Literal['register','bind','read']
    connection_id: UUID | None = None
    submission: dict

def _operation_lock(db,p,request_id):
    key='modbus-operation:'+p.user_id+':'+str(request_id)
    _lock(db,p.workspace_id,key)
    return key

def _not_stopped(db,p,request_id):
    key=_operation_lock(db,p,request_id)
    stop=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
    if stop: raise HTTPException(409,'原请求已结束，不能再次执行；请发起新请求')

def _owned(db,p,row,action):
    actor=row.data.get('actor_id')
    if actor is not None:return actor==p.user_id
    return db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==action,Audit.actor_id==p.user_id).limit(1)) is not None

@router.post('/operations/{request_id}/resolve')
def resolve_operation(request_id:UUID,body:Resolve,p:P,db:DB):
    # This operation can only stop the caller's own request and never grants
    # device access. It remains available after an administrator is demoted.
    raw=body.submission
    if not isinstance(raw,dict) or len(json.dumps(raw,ensure_ascii=True))>4096 or str(raw.get('request_id'))!=str(request_id):
        raise HTTPException(422,'原请求内容无法核对')
    if (body.kind=='register') != (body.connection_id is None):raise HTTPException(422,'原请求范围无法核对')
    key=_operation_lock(db,p,request_id)
    stopped=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
    envelope={'kind':body.kind,'connection_id':str(body.connection_id) if body.connection_id else None,'submission':raw}
    fingerprint=digest(envelope)
    if stopped:
        if stopped.kind!=_STOP or stopped.status!='internal' or stopped.data.get('actor_id')!=p.user_id or stopped.data.get('fingerprint')!=fingerprint:
            raise HTTPException(409,'原请求与已结束记录不一致')
        return {'request_id':str(request_id),'kind':body.kind,'state':'cancelled'}
    if body.kind=='register':
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='modbus-register:'+str(request_id)).execution_options(populate_existing=True))
        if row:
            try:
                expected=Register.model_validate(raw).model_dump(mode='json');expected['name']=expected['name'].strip();expected['unit_label']=expected['unit_label'].strip()
                actual={k:v for k,v in row.data.items() if k!='actor_id'}
                valid=row.kind==_CONNECTION and row.status=='internal' and actual==expected and _owned(db,p,row,'modbus.register')
            except (ValueError,TypeError):valid=False
            if not valid:raise HTTPException(409,'原登记记录与当前账号或内容不一致')
            return {'request_id':str(request_id),'kind':body.kind,'state':'succeeded','result_id':row.id}
    elif body.kind=='read':
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='modbus-read:'+str(request_id)).execution_options(populate_existing=True))
        if row:
            try:
                expected=Read.model_validate(raw)
                valid=(row.kind==_READING and row.status=='internal' and row.data.get('connection_id')==str(body.connection_id)
                    and row.data.get('version')==expected.version and row.data.get('actor_id')==p.user_id)
            except (ValueError,TypeError):valid=False
            if not valid:raise HTTPException(409,'原采集记录与当前账号或内容不一致')
            if row.data.get('state')=='succeeded':return {'request_id':str(request_id),'kind':body.kind,'state':'succeeded','result_id':row.id}
            if row.data.get('state')!='failed':raise HTTPException(409,'原采集状态无法核对')
    else:
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='modbus-binding:'+str(body.connection_id)).execution_options(populate_existing=True))
        if row and row.data.get('request_id')==str(request_id):
            from .modbus_binding import Binding
            try:
                expected=Binding.model_validate(raw)
                valid=(row.kind=='modbus_binding_v1' and row.status=='internal' and row.data.get('connection_version')==expected.version
                    and row.data.get('entity_id')==str(expected.entity_id) and row.data.get('field')==expected.field and _owned(db,p,row,'modbus.binding'))
            except (ValueError,TypeError):valid=False
            if not valid:raise HTTPException(409,'原关联记录与当前账号或内容不一致')
            return {'request_id':str(request_id),'kind':body.kind,'state':'succeeded','result_id':row.id}
    # Same request lock is held by each executor until its transaction finishes;
    # this marker is committed before any future retry can enter.
    stop=Job(workspace_id=p.workspace_id,kind=_STOP,status='internal',idempotency_key=key,
             data={'actor_id':p.user_id,'fingerprint':fingerprint,'kind':body.kind})
    db.add(stop);db.flush();audit(db,p,'modbus.operation.stop',stop.id,{'kind':body.kind});db.commit()
    return {'request_id':str(request_id),'kind':body.kind,'state':'cancelled'}

@router.get('/grants')
def available(p:P,db:DB):
    _admin(p)
    try: return {'grants':grants(p.workspace_id,db)}
    except ModbusError as e: raise HTTPException(409,str(e)) from None

@router.get('/connections')
def connections(p:P,db:DB):
    _admin(p)
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==_CONNECTION,Job.status=='internal').order_by(Job.created_at.desc()).limit(101)))
    if len(rows)>100: raise HTTPException(409,'连接过多，请联系管理员')
    return {'connections':[_view(row) for row in rows]}

@router.post('/connections')
def register(body:Register,p:P,db:DB):
    _admin(p); data=body.model_dump(mode='json'); data['name']=data['name'].strip(); data['unit_label']=data['unit_label'].strip(); _not_stopped(db,p,body.request_id)
    from .managed_modbus_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    _authorized(p.workspace_id,data,db)
    key='modbus-register:'+data['request_id']; _lock(db,p.workspace_id,key)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    if row:
        if row.kind!=_CONNECTION or {k:v for k,v in row.data.items() if k!='actor_id'}!=data or row.status!='internal' or not _owned(db,p,row,'modbus.register'): raise HTTPException(409,'登记请求编号已用于其他内容')
        return {'connection':_view(row)}
    _current_admin(db,p)
    row=Job(workspace_id=p.workspace_id,kind=_CONNECTION,status='internal',idempotency_key=key,data={**data,'actor_id':p.user_id})
    db.add(row); db.flush(); audit(db,p,'modbus.register',row.id); db.commit()
    return {'connection':_view(row)}

@router.get('/connections/{identity}/readings')
def readings(identity:UUID,p:P,db:DB):
    _admin(p); _find(db,p,identity,_CONNECTION)
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==_READING,Job.status=='internal',Job.data['connection_id'].astext==str(identity)).order_by(Job.created_at.desc()).limit(50)))
    return {'readings':[_view(row) for row in rows]}

@router.post('/connections/{identity}/readings')
def read(identity:UUID,body:Read,p:P,db:DB):
    _admin(p); _not_stopped(db,p,body.request_id); key='modbus-read:'+str(body.request_id); _lock(db,p.workspace_id,key)
    from .managed_modbus_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    _lock(db,p.workspace_id,'modbus-binding:'+str(identity))
    saved=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
    if saved:
        if saved.kind!=_READING or saved.data.get('connection_id')!=str(identity) or saved.data.get('version')!=body.version or saved.data.get('actor_id')!=p.user_id:
            raise HTTPException(409,'采集请求编号已用于其他内容')
        if saved.data.get('state')=='succeeded': return {'reading':_view(saved)}
    connection=_find(db,p,identity,_CONNECTION)
    if connection.version!=body.version: raise HTTPException(409,'连接已变化，请刷新后重试')
    config=copy.deepcopy(connection.data); grant,count=_authorized(p.workspace_id,config,db)
    _current_admin(db,p)
    try:
        values=read_registers(grant['host'],grant['port'],grant['unit'],config['function'],config['address'],count)
        value=decode_registers(values,config['value_type'],config['word_order'],config['scale'],config['offset'])
        data={'connection_id':str(identity),'request_id':str(body.request_id),'version':body.version,'actor_id':p.user_id,'state':'succeeded',
              'register_values':values,'value':value,'unit_label':config['unit_label'],'read_at':now().isoformat(),
              'time_note':'采集时间为服务器读取时间，设备未提供观测时间', 'quality':'unverified',
              'source':{'host':grant['host'],'port':grant['port'],'unit':grant['unit']}, 'interpretation':config}
    except ModbusError as e:
        data={'connection_id':str(identity),'request_id':str(body.request_id),'version':body.version,'actor_id':p.user_id,'state':'failed','error':str(e)}
    _current_admin(db,p); _authorized(p.workspace_id,config,db)
    if saved: saved.data=data; row=saved
    else:
        row=Job(workspace_id=p.workspace_id,kind=_READING,status='internal',idempotency_key=key,data=data); db.add(row)
    db.flush()
    if data['state']=='succeeded':
        from .modbus_binding import save_observation
        try:
            observation=save_observation(db,p,connection,row)
            if observation: row.data={**row.data,'observation_id':observation.id}
        except HTTPException as exc:
            row.data={**row.data,'association_error':str(exc.detail)}
    audit(db,p,'modbus.read',row.id,{'state':data['state']}); db.commit()
    return {'reading':_view(row)}
