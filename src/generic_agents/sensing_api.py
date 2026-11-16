from typing import Annotated,Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import Field
from sqlalchemy import select
from .db import session
from .security import authenticated,Principal,require
from .contracts import Contract,VersionAction
from .models import SensorAsset,SensorAssetEvent,ObservationPlan,MonitorPolicy,SensorDevice,SensorDeviceEvent,AssetDeviceBinding,DeviceReservation,Acquisition
from .services import scoped,check_version,as_dict,audit
from . import sensing_selection as sensing
router=APIRouter(prefix='/api/sensing')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]
class AssetCreate(Contract):
    source_id:str
    external_id:str=Field(min_length=1,max_length=200)
    title:str=Field(min_length=1,max_length=200)
    specification:sensing.AssetSpec
    reason:str=Field(min_length=1,max_length=2000)
class AssetUpdate(VersionAction):
    title:str=Field(min_length=1,max_length=200)
    status:Literal['enabled','disabled']='enabled'
    specification:sensing.AssetSpec
@router.get('')
def overview(p:P,db:DB,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    return {name:[as_dict(r) for r in db.scalars(select(model).where(model.workspace_id==p.workspace_id)
        .order_by(model.created_at.desc(),model.id).offset(offset).limit(limit))]
        for name,model in [('assets',SensorAsset),('plans',ObservationPlan)]} | {'offset':offset,'limit':limit}
@router.post('/assets',status_code=201)
def create(body:AssetCreate,p:P,db:DB):
    require(p,'admin','operator');value=body.specification.model_dump();sensing.validate_asset(db,p,body.source_id,value)
    if db.scalar(select(SensorAsset.id).where(SensorAsset.workspace_id==p.workspace_id,
        SensorAsset.source_id==body.source_id,SensorAsset.external_id==body.external_id)):
        raise HTTPException(409,'此来源中的设备已登记，请更新原设备')
    row=SensorAsset(workspace_id=p.workspace_id,source_id=body.source_id,external_id=body.external_id,title=body.title,data=value)
    db.add(row);db.flush();db.add(SensorAssetEvent(workspace_id=p.workspace_id,asset_id=row.id,actor_id=p.user_id,
        data={'before':None,'after':as_dict(row),'reason':body.reason}))
    audit(db,p,'sensor.asset_registered',row.id,{'reason':body.reason});db.commit();return as_dict(row)
@router.patch('/assets/{identifier}')
def update(identifier:str,body:AssetUpdate,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,SensorAsset,identifier,p,True);check_version(row,body.expected_version)
    value=body.specification.model_dump();sensing.validate_asset(db,p,row.source_id,value);before=as_dict(row)
    row.title=body.title;row.status=body.status;row.data=value;row.version+=1
    db.add(SensorAssetEvent(workspace_id=p.workspace_id,asset_id=row.id,actor_id=p.user_id,
        data={'before':before,'after':as_dict(row),'reason':body.reason}))
    audit(db,p,'sensor.asset_updated',row.id,{'reason':body.reason});db.commit();return as_dict(row)
@router.get('/assets/{identifier}/history')
def asset_history(identifier:str,p:P,db:DB):
    scoped(db,SensorAsset,identifier,p)
    return [as_dict(r) for r in db.scalars(select(SensorAssetEvent).where(SensorAssetEvent.workspace_id==p.workspace_id,
        SensorAssetEvent.asset_id==identifier).order_by(SensorAssetEvent.created_at.desc()).limit(100))]
@router.get('/plans/{identifier}')
def plan(identifier:str,p:P,db:DB):return as_dict(scoped(db,ObservationPlan,identifier,p))
@router.post('/plans/{identifier}/retry')
def retry(identifier:str,body:VersionAction,p:P,db:DB):
    require(p,'admin','operator')
    row=scoped(db,ObservationPlan,identifier,p)
    from .observation_requests import guard
    if row.data.get('observation_request'):guard(db,p,row.data['observation_request'])
    policy=scoped(db,MonitorPolicy,row.policy_id,p,True);row=scoped(db,ObservationPlan,identifier,p,True)
    check_version(row,body.expected_version)
    if row.status not in {'escalated','waiting_resource','awaiting_collection'}:raise HTTPException(409,'计划当前不需要恢复')
    created=sensing.refresh(db,p,policy,row,retry=True);sensing.history(row,'retry','人工核对后在原授权内恢复：'+body.reason)
    audit(db,p,'sensor.plan_retried',row.id,{'reason':body.reason});db.commit()
    return {'plan':as_dict(row),'acquisition_ids':created}


from . import device_identity as identity
class DeviceBinding(VersionAction):
    device_id:str
    device_expected_version:int=Field(ge=1)
    reconfirm:bool=False
@router.get('/devices')
def devices(p:P,db:DB,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    rows=[]
    for device in db.scalars(select(SensorDevice).where(SensorDevice.workspace_id==p.workspace_id)
        .order_by(SensorDevice.created_at.desc(),SensorDevice.id).offset(offset).limit(limit)):
        hold=identity.occupied(db,p,device.id);acq=scoped(db,Acquisition,hold.acquisition_id,p) if hold and hold.acquisition_id else None
        links=db.scalars(select(AssetDeviceBinding).where(AssetDeviceBinding.workspace_id==p.workspace_id,
            AssetDeviceBinding.device_id==device.id).order_by(AssetDeviceBinding.created_at).limit(101)).all()
        rows.append({**as_dict(device),'bindings':[as_dict(link) for link in links[:100]],'bindings_truncated':len(links)>100,
            'occupancy':{'acquisition_id':acq.id,'status':acq.status,'reason':acq.data.get('reason'),
                'action_terminal':acq.data.get('device_action_terminal',False),'plan_id':acq.data.get('observation_plan_id'),'entity_id':acq.entity_id,'asset_id':acq.data.get('sensor_asset_id')} if acq else None})
    return {'devices':rows,'offset':offset,'limit':limit}
@router.post('/devices',status_code=201)
def create_device(body:identity.DeviceCreate,p:P,db:DB):
    row=identity.create(db,p,body);db.commit();return as_dict(row)
@router.get('/devices/{identifier}')
def device(identifier:str,p:P,db:DB):return as_dict(scoped(db,SensorDevice,identifier,p))
@router.get('/devices/{identifier}/history')
def device_history(identifier:str,p:P,db:DB):
    scoped(db,SensorDevice,identifier,p)
    return [as_dict(r) for r in db.scalars(select(SensorDeviceEvent).where(SensorDeviceEvent.workspace_id==p.workspace_id,
        SensorDeviceEvent.device_id==identifier).order_by(SensorDeviceEvent.created_at.desc()).limit(100))]
@router.post('/devices/{identifier}/approve')
def approve_device(identifier:str,body:VersionAction,p:P,db:DB):
    row=identity.change_status(db,p,identifier,body,'approve');db.commit();return as_dict(row)
@router.post('/devices/{identifier}/pause')
def pause_device(identifier:str,body:VersionAction,p:P,db:DB):
    row=identity.change_status(db,p,identifier,body,'pause');db.commit();return as_dict(row)
@router.post('/devices/{identifier}/resume')
def resume_device(identifier:str,body:VersionAction,p:P,db:DB):
    row=identity.change_status(db,p,identifier,body,'resume');db.commit();return as_dict(row)
@router.get('/assets/{identifier}/device')
def asset_device(identifier:str,p:P,db:DB):
    scoped(db,SensorAsset,identifier,p);link=identity.binding(db,p,identifier)
    return {'binding':as_dict(link) if link else None,'device':as_dict(scoped(db,SensorDevice,link.device_id,p)) if link else None}
@router.post('/assets/{identifier}/device')
def bind_device(identifier:str,body:DeviceBinding,p:P,db:DB):
    row=identity.bind(db,p,identifier,body.device_id,body.expected_version,body.device_expected_version,body.reason,reconfirm=body.reconfirm)
    db.commit();return as_dict(row)

# Independent policy scheduling preference; this router already owns /api/sensing.
from .monitor_scheduling_api_v253 import router as monitor_scheduling_router_v253
router.include_router(monitor_scheduling_router_v253)
