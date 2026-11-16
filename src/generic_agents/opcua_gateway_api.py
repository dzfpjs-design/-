"""OPC UA authenticated registration, binding and explicit single-value read."""
from __future__ import annotations
import copy
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import select
from jsonschema import Draft202012Validator
from .contracts import Contract,Manifest,digest
from .models import Capability,Entity,Job,Observation
from .services import audit,published,require,scoped
from .setup_api import P,DB
from .ftps_gateway_api import _lock,_current_admin
from .opcua_readonly_transport import OpcuaError,grants,get_grant,public_grant,read_value

router=APIRouter(prefix='/api/opcua')

class Register(Contract):
    request_id:UUID
    name:StrictStr=Field(min_length=1,max_length=100)
    grant_id:StrictStr=Field(pattern=r'^[a-f0-9]{64}$')
    unit_label:StrictStr=Field(default='',max_length=40)
class Read(Contract):
    request_id:UUID
    version:StrictInt=Field(ge=1)
class Bind(Read):
    entity_id:UUID
    field:StrictStr=Field(min_length=1,max_length=100)
class Resolve(Contract):
    kind:Literal['register','bind','read']
    connection_id:UUID|None=None
    submission:dict

def _intent_key(p,request):return 'opcua-request:'+p.user_id+':'+str(request)
def _intent(db,p,kind,submission,connection_id=None):
    request=submission['request_id'];key=_intent_key(p,request);_lock(db,p.workspace_id,key)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
    actual={'actor_id':p.user_id,'kind':kind,'submission':submission,'connection_id':str(connection_id) if connection_id else None}
    if row:
        if row.kind!='opcua_request_v1' or row.status!='internal' or any(row.data.get(k)!=v for k,v in actual.items()):raise HTTPException(409,'原请求与当前账号或内容不一致')
        if row.data['state']=='cancelled':raise HTTPException(409,'原请求已结束，请发起新请求')
        if row.data['state']=='succeeded':return row,False
    return row,True

def _save_request(db,p,row,kind,submission,connection_id,state,result):
    data={'actor_id':p.user_id,'kind':kind,'submission':submission,'connection_id':str(connection_id) if connection_id else None,'state':state,'result':result}
    if row:row.data=data
    else:
        row=Job(workspace_id=p.workspace_id,kind='opcua_request_v1',status='internal',idempotency_key=_intent_key(p,submission['request_id']),data=data);db.add(row)
    db.flush();return row

def _connection(db,p,identity,version=None):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==str(identity),Job.kind=='opcua_connection_v1',Job.status=='internal'))
    if row is None:raise HTTPException(404,'连接不存在或不属于本工作区')
    if version is not None and row.version!=version:raise HTTPException(409,'连接已变化，请刷新后重试')
    return row

def _grant(workspace,identity,db=None):
    try:return get_grant(workspace,identity,db)
    except OpcuaError as exc:raise HTTPException(409,str(exc)) from None

def _binding(db,p,identity):return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='opcua-binding:'+str(identity),Job.kind=='opcua_binding_v1',Job.status=='internal').execution_options(populate_existing=True))
def _properties(db,p,entity):
    package=published(db,p,entity.schema_capability_id,'domain_package')
    spec=package.data.get('specification',{}).get('entity_schemas',{}).get(entity.entity_type,{})
    props=spec.get('properties',{}) if isinstance(spec,dict) else {}
    return package,props if isinstance(props,dict) else {}
def _field(db,p,connection,entity_id,field):
    entity=scoped(db,Entity,str(entity_id),p);package,props=_properties(db,p,entity);spec=props.get(field)
    if not isinstance(spec,dict) or spec.get('type') not in ('integer','number'):raise HTTPException(422,'请选择已发布的数值字段')
    if spec.get('unit') is not None and spec['unit']!=connection.data['unit_label']:raise HTTPException(422,'登记的单位与业务字段不一致，不会自动换算')
    return entity,package,spec

def _view(row):return {'id':row.id,'version':row.version,**copy.deepcopy(row.data),'created_at':row.created_at.isoformat()}
def _response(row):return {'request_id':row.data['submission']['request_id'],'state':row.data['state'],'kind':row.data['kind'],'result':copy.deepcopy(row.data['result'])}

@router.get('/grants')
def available(p:P,db:DB):
    require(p,'admin')
    try:return {'grants':[public_grant(g) for g in grants(p.workspace_id,db)]}
    except OpcuaError as exc:raise HTTPException(409,str(exc)) from None

@router.get('/connections')
def connections(p:P,db:DB):
    require(p,'admin');rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='opcua_connection_v1',Job.status=='internal').order_by(Job.created_at.desc()).limit(101)))
    if len(rows)>100:raise HTTPException(409,'连接过多，请联系管理员')
    return {'connections':[_view(x) for x in rows]}

@router.post('/connections')
def register(body:Register,p:P,db:DB):
    require(p,'admin');data=body.model_dump(mode='json');data['name']=data['name'].strip();data['unit_label']=data['unit_label'].strip()
    if not data['name'] or any(ord(c)<32 for c in data['name']+data['unit_label']):raise HTTPException(422,'名称或单位无效')
    row,perform=_intent(db,p,'register',data)
    if not perform:return _response(row)
    from .managed_opcua_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    grant=_grant(p.workspace_id,data['grant_id'],db);_current_admin(db,p)
    connection=Job(workspace_id=p.workspace_id,kind='opcua_connection_v1',status='internal',idempotency_key='opcua-connection:'+p.user_id+':'+str(body.request_id),data={'name':data['name'],'grant_id':data['grant_id'],'unit_label':data['unit_label'],'source':public_grant(grant)})
    db.add(connection);db.flush();row=_save_request(db,p,row,'register',data,None,'succeeded',{'connection_id':connection.id});audit(db,p,'opcua.register',connection.id);db.commit();return _response(row)

@router.get('/connections/{identity}/binding')
def binding_options(identity:UUID,p:P,db:DB):
    require(p,'admin');connection=_connection(db,p,identity);binding=_binding(db,p,identity);entities=[]
    for entity in db.scalars(select(Entity).where(Entity.workspace_id==p.workspace_id).order_by(Entity.created_at.desc()).limit(100)):
        try:package,props=_properties(db,p,entity)
        except HTTPException:continue
        fields=[{'name':key,'label':spec.get('title') or key,'unit':spec.get('unit')} for key,spec in props.items() if isinstance(spec,dict) and spec.get('type') in ('integer','number') and (spec.get('unit') is None or spec['unit']==connection.data['unit_label'])]
        if fields:entities.append({'id':entity.id,'label':entity.data.get('name') or entity.external_id,'type':entity.entity_type,'fields':fields})
    return {'binding':copy.deepcopy(binding.data) if binding else None,'entities':entities}

@router.post('/connections/{identity}/binding')
def bind(identity:UUID,body:Bind,p:P,db:DB):
    require(p,'admin');submission=body.model_dump(mode='json');row,perform=_intent(db,p,'bind',submission,identity)
    if not perform:return _response(row)
    from .managed_opcua_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    _lock(db,p.workspace_id,'opcua-binding:'+str(identity));connection=_connection(db,p,identity,body.version)
    if _binding(db,p,identity):raise HTTPException(409,'此连接已有对象关联；需要更改时请新建连接')
    _grant(p.workspace_id,connection.data['grant_id'],db);entity,package,spec=_field(db,p,connection,body.entity_id,body.field);_current_admin(db,p)
    manifest=Manifest(kind='source',namespace='platform_opcua',name='opcua_'+str(identity).replace('-',''),version='1.0.0',owner='系统',description='用户确认的 OPC UA 只读数据来源',allowed_agent_roles=['perception','data'],execution={'adapter':'opcua_readonly'},specification={'connection_id':str(identity),'entity_id':entity.id,'field':body.field,'timestamps':'reported_unverified'})
    source_data=manifest.model_dump();fingerprint=digest(source_data)
    source=Capability(workspace_id=p.workspace_id,namespace=manifest.namespace,name=manifest.name,release_version=manifest.version,kind='source',status='published',content_hash=fingerprint,validated_hash=fingerprint,data=source_data);db.add(source);db.flush()
    b={'entity_id':entity.id,'field':body.field,'unit':connection.data['unit_label'],'package_id':package.id,'package_hash':digest(package.data),'field_schema':copy.deepcopy(spec),'source_id':source.id,'source_hash':fingerprint,'connection_version':connection.version}
    link=Job(workspace_id=p.workspace_id,kind='opcua_binding_v1',status='internal',idempotency_key='opcua-binding:'+str(identity),data=b);db.add(link);db.flush()
    row=_save_request(db,p,row,'bind',submission,identity,'succeeded',{'binding_id':link.id});audit(db,p,'opcua.bind',link.id);db.commit();return _response(row)

@router.get('/connections/{identity}/readings')
def history(identity:UUID,p:P,db:DB):
    require(p,'admin');_connection(db,p,identity)
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='opcua_reading_v1',Job.status=='internal',Job.data['connection_id'].astext==str(identity)).order_by(Job.created_at.desc()).limit(50)))
    return {'readings':[_view(x) for x in rows]}

@router.post('/connections/{identity}/readings')
def read(identity:UUID,body:Read,p:P,db:DB):
    require(p,'admin');submission=body.model_dump(mode='json');row,perform=_intent(db,p,'read',submission,identity)
    if not perform:return _response(row)
    from .managed_opcua_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    _lock(db,p.workspace_id,'opcua-binding:'+str(identity));connection=_connection(db,p,identity,body.version)
    grant=_grant(p.workspace_id,connection.data['grant_id'],db);_current_admin(db,p)
    try:result=read_value(grant);state='succeeded'
    except OpcuaError as exc:result={'error':str(exc)};state='failed'
    _current_admin(db,p);_grant(p.workspace_id,connection.data['grant_id'],db)
    if state=='succeeded':
        reading=Job(workspace_id=p.workspace_id,kind='opcua_reading_v1',status='internal',idempotency_key='opcua-reading:'+p.user_id+':'+str(body.request_id),data={'connection_id':str(identity),'request_id':str(body.request_id),'unit':connection.data['unit_label'],**result})
        db.add(reading);db.flush();binding=_binding(db,p,identity)
        if binding:
            b=binding.data
            try:
                entity,package,spec=_field(db,p,connection,b['entity_id'],b['field']);source=published(db,p,b['source_id'],'source')
                if b['connection_version']!=connection.version or b['package_hash']!=digest(package.data) or b['source_hash']!=digest(source.data) or b['field_schema']!=spec:raise HTTPException(409,'对象字段或来源已变化')
                val=result['value'];eligible=result['quality']=='good' and val is not None and (spec.get('type')!='integer' or float(val).is_integer())
                converted=int(val) if eligible and spec.get('type')=='integer' else val
                eligible=eligible and Draft202012Validator(spec).is_valid(converted)
                attrs={b['field']:converted} if eligible else {}
                observation=Observation(workspace_id=p.workspace_id,source_id=source.id,source_record_id='opcua:'+reading.id,processing_version='1.0.0',entity_id=entity.id,observed_at=None,business_day=None,status='extracted_unclocked',data={'schema':'opcua-unverified-observation-v1','submitted_by':p.user_id,'candidate_only':True,'source_reported_timestamp':result['source_timestamp'],'server_reported_timestamp':result['server_timestamp'],'received_at':result['received_at'],'reading_id':reading.id,'quality':result['quality'],'status_code':result['status_code'],'unit':b['unit'],'extracted':{'attributes':attrs,'anomalies':[]},'evidence_ids':[]})
                db.add(observation);db.flush();reading.data={**reading.data,'observation_id':observation.id};audit(db,p,'opcua.observation.saved',observation.id)
            except HTTPException as exc:reading.data={**reading.data,'association_error':str(exc.detail)}
        result={'reading_id':reading.id,**reading.data}
    row=_save_request(db,p,row,'read',submission,identity,state,result);audit(db,p,'opcua.read',row.id,{'state':state});db.commit();return _response(row)

@router.post('/operations/{request_id}/resolve')
def resolve(request_id:UUID,body:Resolve,p:P,db:DB):
    submission=body.submission
    if not isinstance(submission,dict) or str(submission.get('request_id'))!=str(request_id) or len(str(submission))>4096 or ((body.kind=='register')!=(body.connection_id is None)):raise HTTPException(422,'原请求内容无法核对')
    try:
        submission={'register':Register,'read':Read,'bind':Bind}[body.kind].model_validate(submission).model_dump(mode='json')
        if body.kind=='register':submission['name']=submission['name'].strip();submission['unit_label']=submission['unit_label'].strip()
    except (ValueError,TypeError,ValidationError):
        pass
    key=_intent_key(p,request_id);_lock(db,p.workspace_id,key)
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key).execution_options(populate_existing=True))
    if row:
        if row.kind!='opcua_request_v1' or row.status!='internal' or row.data.get('actor_id')!=p.user_id or row.data.get('kind')!=body.kind or row.data.get('submission')!=submission or row.data.get('connection_id')!=(str(body.connection_id) if body.connection_id else None):raise HTTPException(409,'原请求与当前账号或内容不一致')
        if row.data['state']=='succeeded':return _response(row)
        if row.data['state']=='cancelled':return _response(row)
        if row.data['state']!='failed':raise HTTPException(409,'原请求状态无法核对')
    row=_save_request(db,p,row,body.kind,submission,body.connection_id,'cancelled',None);audit(db,p,'opcua.operation.stop',row.id);db.commit();return _response(row)
