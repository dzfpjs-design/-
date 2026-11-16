"""Explicitly bind a Modbus measurement to a scoped business-object field.

Device sample time is unavailable. Records are stored as unclocked candidates;
no event is emitted, no current object state is overwritten, and no alarm is
inferred from a transport read.
"""
from __future__ import annotations
import copy
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy import select
from .contracts import Contract, Manifest, digest
from .models import Capability, Entity, Job, Observation
from .setup_api import P, DB
from .services import audit, published, require, scoped
from .ftps_gateway_api import _current_admin, _lock

router=APIRouter(prefix='/api/modbus')
_BINDING='modbus_binding_v1'

class Binding(Contract):
    request_id: UUID
    version: StrictInt = Field(ge=1)
    entity_id: UUID
    field: StrictStr = Field(min_length=1,max_length=100)

def _connection(db,p,identity,version=None):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(identity),Job.kind=='modbus_connection_v1',Job.status=='internal'))
    if row is None: raise HTTPException(404,'未找到连接')
    if version is not None and row.version!=version: raise HTTPException(409,'连接已变化')
    return row

def _properties(db,p,entity):
    package=published(db,p,entity.schema_capability_id,'domain_package')
    schema=package.data.get('specification',{}).get('entity_schemas',{}).get(entity.entity_type,{})
    props=schema.get('properties',{}) if isinstance(schema,dict) else {}
    return package,props if isinstance(props,dict) else {}

def _validate(db,p,connection,entity_id,field):
    entity=scoped(db,Entity,str(entity_id),p);package,props=_properties(db,p,entity)
    spec=props.get(field)
    if not isinstance(spec,dict) or spec.get('type') not in ('number','integer'):
        raise HTTPException(422,'所选字段不是业务对象已发布的数值字段')
    if spec.get('unit') is not None and spec['unit']!=connection.data['unit_label']:
        raise HTTPException(422,'来源单位与业务字段不一致，请核对，不会自动换算')
    return entity,package,spec

def _binding(db,p,identity):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='modbus-binding:'+str(identity),Job.kind==_BINDING,Job.status=='internal').execution_options(populate_existing=True))

@router.get('/connections/{identity}/binding')
def options(identity:UUID,p:P,db:DB):
    require(p,'admin');connection=_connection(db,p,identity);binding=_binding(db,p,identity)
    entities=[]
    for entity in db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id).order_by(Entity.created_at.desc()).limit(101)):
        try:package,props=_properties(db,p,entity)
        except HTTPException:continue
        fields=[{'name':key,'label':value.get('title') or key,'unit':value.get('unit')} for key,value in props.items()
                if isinstance(value,dict) and value.get('type') in ('number','integer') and (value.get('unit') is None or value.get('unit')==connection.data['unit_label'])]
        if fields:entities.append({'id':entity.id,'label':entity.data.get('name') or entity.external_id,'type':entity.entity_type,'fields':fields})
    return {'binding':copy.deepcopy(binding.data) if binding else None,'entities':entities}

@router.post('/connections/{identity}/binding')
def bind(identity:UUID,body:Binding,p:P,db:DB):
    require(p,'admin')
    from .modbus_gateway_api import _not_stopped, _owned
    _not_stopped(db,p,body.request_id); connection=_connection(db,p,identity,body.version)
    _lock(db,p.workspace_id,'modbus-binding:'+str(identity));old=_binding(db,p,identity)
    if old:
        if old.data['request_id']==str(body.request_id) and old.data['connection_version']==body.version and old.data['entity_id']==str(body.entity_id) and old.data['field']==body.field and _owned(db,p,old,'modbus.binding'):
            return {'binding':copy.deepcopy(old.data)}
        raise HTTPException(409,'该连接已有已确认关联；如需其他对象或字段，请另建连接，原记录保留')
    entity,package,spec=_validate(db,p,connection,body.entity_id,body.field);_current_admin(db,p)
    manifest=Manifest(kind='source',namespace='platform_modbus',name='modbus_'+str(identity).replace('-',''),version='1.0.0',
       owner='系统',description='用户确认的 Modbus 只读寄存器来源',allowed_agent_roles=['perception','data'],
       execution={'adapter':'modbus_readonly'},specification={'connection_id':str(identity),'entity_id':entity.id,'field':body.field,'timezone':'Asia/Shanghai','measurement_time':'unknown'})
    source_data=manifest.model_dump(); fingerprint=digest(source_data)
    source=Capability(workspace_id=p.workspace_id,namespace=manifest.namespace,name=manifest.name,release_version=manifest.version,kind='source',status='published',content_hash=fingerprint,validated_hash=fingerprint,data=source_data)
    db.add(source);db.flush()
    data={'actor_id':p.user_id,'request_id':str(body.request_id),'connection_id':str(identity),'connection_version':connection.version,'entity_id':entity.id,'entity_version':entity.version,'field':body.field,
       'unit':connection.data['unit_label'],'field_schema':copy.deepcopy(spec),'package_id':package.id,'package_hash':digest(package.data),'source_id':source.id,'source_hash':fingerprint}
    row=Job(workspace_id=p.workspace_id,kind=_BINDING,status='internal',idempotency_key='modbus-binding:'+str(identity),data=data)
    db.add(row);db.flush();audit(db,p,'modbus.binding',row.id,{'entity_id':entity.id,'field':body.field,'source_id':source.id});db.commit()
    return {'binding':copy.deepcopy(data)}

def save_observation(db,p,connection,reading):
    binding=_binding(db,p,connection.id)
    if not binding:return None
    b=binding.data
    entity,package,spec=_validate(db,p,connection,b['entity_id'],b['field'])
    source=published(db,p,b['source_id'],'source')
    if (b['connection_version']!=connection.version or b['package_id']!=package.id or b['package_hash']!=digest(package.data)
        or b['source_hash']!=digest(source.data) or spec!=b['field_schema']):
        raise HTTPException(409,'对象字段、来源或连接已变化，原观测保留；需重新核对关联')
    value=reading.data['value']
    if spec.get('type')=='integer' and (not float(value).is_integer()):raise HTTPException(422,'读取值不是业务字段要求的整数')
    record=Observation(workspace_id=p.workspace_id,source_id=source.id,source_record_id='modbus:'+reading.id,processing_version='1.0.0',entity_id=entity.id,
        observed_at=None,business_day=None,status='extracted_unclocked',data={
          'schema':'modbus-unclocked-observation-v1','submitted_by':p.user_id,'candidate_only':True,
          'measurement_time_known':False,'server_read_at':reading.data['read_at'],'reading_id':reading.id,'binding_id':binding.id,
          'quality':'unverified','register_values':reading.data['register_values'],'unit':b['unit'],
          'extracted':{'attributes':{b['field']:int(value) if spec.get('type')=='integer' else value},'anomalies':[]},'evidence_ids':[]})
    db.add(record);db.flush();audit(db,p,'modbus.observation.saved',record.id,{'reading_id':reading.id,'measurement_time':'unknown'})
    return record
