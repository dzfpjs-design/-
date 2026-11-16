"""Explicit user authorization and human-readable sensor candidate inbox."""
from __future__ import annotations
import copy, math
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,HTTPException,Query
from pydantic import Field,StrictInt,StrictBool
from sqlalchemy import select
from .contracts import Contract,digest
from .models import Job,Observation,Memory,Membership,User,now
from .security import require
from .services import audit
from .setup_api import P,DB
from .ftps_gateway_api import _current_admin,_lock
from . import sensor_automation as service

router=APIRouter(prefix='/api/sensor-automation')

class Threshold(Contract):
    operator:Literal['above','below']
    value:float=Field(allow_inf_nan=False,ge=-1e12,le=1e12)
class Configure(Contract):
    request_id:UUID
    protocol:Literal['modbus','opcua']
    connection_id:UUID
    connection_version:StrictInt=Field(ge=1)
    expected_version:StrictInt=Field(ge=0)
    enabled:StrictBool
    interval_seconds:StrictInt=Field(ge=60,le=86400)
    max_age_seconds:StrictInt=Field(default=3600,ge=60,le=86400)
    threshold:Threshold|None=None
class Acknowledge(Contract):
    request_id:UUID
    version:StrictInt=Field(ge=1)

DATA_FIELDS=('schema','policy_id','policy_generation','configuration_request_id','connection_id','protocol','entity_id','source_id','observation_id','reading_id','value','unit','field','field_name','object_name','device_name','quality','freshness','source_timestamp','received_at')

def _admin(p):require(p,'admin')

def _policy(db,p,protocol,identity):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==service.POLICY,Job.status=='internal',Job.idempotency_key==service.policy_key(protocol,identity)).with_for_update().execution_options(populate_existing=True))

@router.get('/options')
def options(p:P,db:DB,page:int=Query(default=1,ge=1,le=10000)):
    _admin(p)
    connections,has_more=service.connection_options(db,p,page)
    return {'connections':connections,'page':page,'has_more':has_more}

@router.post('/policy')
def configure(body:Configure,p:P,db:DB):
    _admin(p);value=body.model_dump(mode='json');identity=str(body.connection_id)
    op_key='sensor-auto-config:'+str(body.request_id)
    _lock(db,p.workspace_id,op_key)
    original=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==op_key))
    fingerprint=digest({'actor':p.user_id,'request':value})
    if original:
        if original.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原请求与当前账号或内容不一致')
        if original.kind=='sensor_auto_config_cancel_v1':raise HTTPException(409,'原请求已安全结束，请创建新设置')
        if original.kind!='sensor_auto_config_v1':raise HTTPException(409,'原请求无法核对')
        return {'policy':copy.deepcopy(original.data['result'])}
    _lock(db,p.workspace_id,'sensor-auto:'+body.protocol+':'+identity)
    row=_policy(db,p,body.protocol,identity)
    if (row.data['generation'] if row else 0)!=body.expected_version:raise HTTPException(409,'自动采集设置已变化，请刷新后核对')
    # A pause does not require an available device, but still requires a current administrator.
    if body.enabled:
        conn,link,_=service._source(db,p,body.protocol,identity,body.connection_version)
        _lock(db,p.workspace_id,body.protocol+'-binding:'+identity)
        binding_id,binding_hash=link.id,digest(link.data)
    else:
        if row is None:raise HTTPException(409,'尚未设置自动采集')
        if row.data['connection_version']!=body.connection_version:raise HTTPException(409,'连接版本已变化')
        binding_id,binding_hash=row.data['binding_id'],row.data['binding_hash']
    _current_admin(db,p)
    old_generation=row.data['generation'] if row else 0
    data={'protocol':body.protocol,'connection_id':identity,'connection_version':body.connection_version,'binding_id':binding_id,
          'binding_hash':binding_hash,'approved_by':p.user_id,'approved_at':now().isoformat(),'enabled':body.enabled,
          'interval_seconds':body.interval_seconds,'max_age_seconds':body.max_age_seconds,'threshold':body.threshold.model_dump() if body.threshold else None,
          'generation':old_generation+1,'configuration_request_id':str(body.request_id),'next_due':now().isoformat(),'state':'active' if body.enabled else 'paused',
          'last_error':None,'last_read_at':row.data.get('last_read_at') if row else None,'last_reading_id':row.data.get('last_reading_id') if row else None}
    if row:row.data=data;row.version+=1
    else:row=Job(workspace_id=p.workspace_id,kind=service.POLICY,status='internal',idempotency_key=service.policy_key(body.protocol,identity),data=data);db.add(row);db.flush()
    result=service.policy_view(row)
    op=Job(workspace_id=p.workspace_id,kind='sensor_auto_config_v1',status='internal',idempotency_key=op_key,data={'fingerprint':fingerprint,'actor_id':p.user_id,'result':result});db.add(op)
    audit(db,p,'sensor.auto.configure',row.id,{'enabled':body.enabled,'generation':data['generation'],'interval_seconds':body.interval_seconds,'threshold':data['threshold']});db.commit()
    return {'policy':result}

@router.get('/policy-requests/{request_id}')
def policy_request(request_id:UUID,p:P,db:DB):
    require(p,'admin','operator','viewer');row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='sensor-auto-config:'+str(request_id),Job.kind=='sensor_auto_config_v1'))
    if not row or row.data.get('actor_id')!=p.user_id:raise HTTPException(404,'未找到本人原请求')
    return {'policy':copy.deepcopy(row.data['result'])}

@router.post('/policy-requests/{request_id}/cancel')
def cancel_policy_request(request_id:UUID,body:Configure,p:P,db:DB):
    require(p,'admin','operator','viewer')
    if body.request_id!=request_id:raise HTTPException(422,'原请求编号不一致')
    value=body.model_dump(mode='json');fingerprint=digest({'actor':p.user_id,'request':value});key='sensor-auto-config:'+str(request_id)
    _lock(db,p.workspace_id,key)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
    if row:
        if row.data.get('fingerprint')!=fingerprint or row.data.get('actor_id')!=p.user_id:raise HTTPException(409,'原请求与当前账号或内容不一致')
        if row.kind=='sensor_auto_config_v1':return {'request_id':str(request_id),'state':'succeeded','policy':copy.deepcopy(row.data['result'])}
        if row.kind=='sensor_auto_config_cancel_v1':return {'request_id':str(request_id),'state':'cancelled'}
        raise HTTPException(409,'原请求无法核对')
    row=Job(workspace_id=p.workspace_id,kind='sensor_auto_config_cancel_v1',status='internal',idempotency_key=key,data={'fingerprint':fingerprint,'actor_id':p.user_id});db.add(row);db.flush()
    audit(db,p,'sensor.auto.config_cancel',row.id);db.commit();return {'request_id':str(request_id),'state':'cancelled'}

def _checked_alert(db,p,row):
    d=row.data;obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==d.get('observation_id')))
    reading=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==d.get('reading_id')))
    if not obs or not reading or obs.source_id!=d.get('source_id') or obs.entity_id!=d.get('entity_id') or reading.data.get('observation_id')!=obs.id:
        raise HTTPException(409,'线索的原读数或观测无法核对；原记录保留')
    payload={k:d[k] for k in DATA_FIELDS}
    if digest(payload)!=d.get('evidence_hash') or reading.data.get('value')!=d.get('value') or reading.data.get('quality','unverified')!=d.get('quality'):
        raise HTTPException(409,'线索与原读数不一致；原记录保留')
    config=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='sensor-auto-config:'+d['configuration_request_id'],Job.kind=='sensor_auto_config_v1'))
    saved=config.data.get('result') if config else None
    if (not isinstance(saved,dict) or saved.get('id')!=d['policy_id'] or saved.get('generation')!=d['policy_generation']
        or saved.get('threshold')!=d.get('threshold') or saved.get('enabled') is not True
        or obs.data.get('extracted',{}).get('attributes',{}).get(d['field'])!=d['value']):
        raise HTTPException(409,'线索与原批准阈值或观测不一致；原记录保留')
    evidence={'protocol':d['protocol'],'source_timestamp':reading.data.get('source_timestamp'),
        'server_timestamp':reading.data.get('server_timestamp'),'received_at':d['received_at'],
        'raw_registers':copy.deepcopy(reading.data.get('register_values')) if d['protocol']=='modbus' else None,
        'status_name':reading.data.get('status_name') if d['protocol']=='opcua' else None}
    return {'id':row.id,'version':row.version,'created_at':row.created_at.isoformat(),**copy.deepcopy(d),'evidence':evidence}

@router.get('/data')
def data_receipts(p:P,db:DB,connection_id:UUID|None=None):
    require(p,'admin','operator','viewer')
    query=select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['type'].as_string()==service.DATA)
    if connection_id:query=query.where(Memory.data['connection_id'].as_string()==str(connection_id))
    rows=db.scalars(query.order_by(Memory.created_at.desc()).limit(51)).all();result=[];unverified=0
    for row in rows[:50]:
        try:
            data=row.data;payload={key:data[key] for key in DATA_FIELDS}
            obs=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.id==data['observation_id']))
            reading=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==data['reading_id']))
            if (digest(payload)!=data['evidence_hash'] or not obs or not reading or obs.entity_id!=data['entity_id']
                or obs.source_id!=data['source_id'] or reading.data.get('observation_id')!=obs.id
                or reading.data.get('value')!=data['value'] or reading.data.get('quality','unverified')!=data['quality']):raise ValueError()
            result.append({**payload,'created_at':row.created_at.isoformat()})
        except (KeyError,TypeError,ValueError):unverified+=1
    return {'records':result,'unverified_count':unverified,'has_more':len(rows)>50}

@router.get('/alerts')
def alerts(p:P,db:DB):
    require(p,'admin','operator','viewer')
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==service.ALERT,Job.status=='internal').order_by(Job.created_at.desc()).limit(101)).all()
    result=[];unverified=0
    for row in rows[:100]:
        try:result.append(_checked_alert(db,p,row))
        except (HTTPException,KeyError,TypeError,ValueError):unverified+=1
    return {'alerts':result,'unverified_count':unverified,'has_more':len(rows)>100}

@router.post('/alerts/{identity}/acknowledge')
def acknowledge(identity:UUID,body:Acknowledge,p:P,db:DB):
    require(p,'admin','operator');_lock(db,p.workspace_id,'sensor-auto-alert:'+str(identity))
    member=db.scalar(select(Membership.id).join(User,User.id==Membership.user_id).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,Membership.role.in_(['admin','operator']),User.active.is_(True)).with_for_update(read=True))
    if not member:raise HTTPException(403,'当前账号已无权确认线索')
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(identity),Job.kind==service.ALERT,Job.status=='internal').with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'未找到该线索')
    result=_checked_alert(db,p,row)
    if row.data.get('ack_request_id')==str(body.request_id) and row.data.get('acknowledged_by')==p.user_id:return {'alert':result}
    if row.version!=body.version:raise HTTPException(409,'线索已变化，请刷新后核对')
    if row.data.get('status')!='unconfirmed':raise HTTPException(409,'线索已确认')
    row.data={**row.data,'status':'acknowledged','ack_request_id':str(body.request_id),'acknowledged_by':p.user_id,'acknowledged_at':now().isoformat()};row.version+=1
    audit(db,p,'sensor.auto.acknowledge',row.id,{'observation_id':row.data['observation_id']});db.commit();return {'alert':_checked_alert(db,p,row)}
