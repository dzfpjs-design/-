"""Explicit reviewed device identity, source bindings and durable shared occupancy."""
import unicodedata
from typing import Literal
from fastapi import HTTPException
from pydantic import Field,model_validator
from sqlalchemy import select,text
from sqlalchemy.dialects.postgresql import insert
from .contracts import Contract,digest
from .models import SensorDevice,SensorDeviceEvent,AssetDeviceBinding,DeviceReservation,SensorAsset,SensorAssetEvent,Acquisition,SensorReservation,SourceReservation,Capability,User,Membership
from .services import scoped,check_version,as_dict,audit
from .security import require

class DeviceCreate(Contract):
    title:str=Field(min_length=1,max_length=200)
    identity_scheme:Literal['manufacturer_serial','inventory_tag','supplier_device_id']
    identity_issuer:str=Field(min_length=1,max_length=100)
    identity_reference:str=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=2000)
    @model_validator(mode='after')
    def identity(self):
        self.title=self.title.strip()
        if not self.title:raise ValueError('设备名称不能为空')
        for field in ['identity_issuer','identity_reference']:
            value=unicodedata.normalize('NFKC',getattr(self,field)).strip()
            if not value or len(value)>(100 if field=='identity_issuer' else 200) or any(unicodedata.category(c).startswith('C') for c in value):raise ValueError('设备标识不能为空或包含控制字符')
            setattr(self,field,value)
        return self

def lock(db,p):
    # Held only for identity mutation / dispatch preparation, never across external HTTP.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'sensor-identity:'+p.workspace_id})

def binding(db,p,asset_id):
    return db.scalar(select(AssetDeviceBinding).where(AssetDeviceBinding.workspace_id==p.workspace_id,AssetDeviceBinding.asset_id==asset_id))

def active_admin(db,p,user_id):
    return bool(db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(User.id==user_id,
        User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role=='admin')))

def checked(db,p,asset_id):
    link=binding(db,p,asset_id)
    if not link:raise HTTPException(409,'接口资产尚未绑定已核查统一设备，禁止新的设备动作')
    device=scoped(db,SensorDevice,link.device_id,p)
    if device.status!='enabled' or not active_admin(db,p,device.approved_by) or not active_admin(db,p,link.approved_by):
        raise HTTPException(409,'统一设备未确认、已暂停或核查权限已撤销')
    return link,device

def occupied(db,p,device_id):
    return db.scalar(select(DeviceReservation).where(DeviceReservation.workspace_id==p.workspace_id,DeviceReservation.device_id==device_id))

def reservation(db,p,acq,create=False):
    identifier=acq.data.get('sensor_device_id')
    if not identifier:return None
    if create:db.execute(insert(DeviceReservation).values(workspace_id=p.workspace_id,device_id=identifier)
        .on_conflict_do_nothing(index_elements=['workspace_id','device_id']))
    return db.scalar(select(DeviceReservation).where(DeviceReservation.workspace_id==p.workspace_id,
        DeviceReservation.device_id==identifier).with_for_update())

def release(db,p,acq):
    row=reservation(db,p,acq)
    if row and row.acquisition_id==acq.id:row.acquisition_id=None

def event(db,p,device,operation,reason,before=None):
    db.add(SensorDeviceEvent(workspace_id=p.workspace_id,device_id=device.id,actor_id=p.user_id,
        data={'operation':operation,'reason':reason,'before':before,'after':as_dict(device)}))
    audit(db,p,'sensor.device_'+operation,device.id,{'reason':reason})

def create(db,p,body):
    require(p,'admin','operator');lock(db,p)
    exists=db.scalar(select(SensorDevice.id).where(SensorDevice.workspace_id==p.workspace_id,
        SensorDevice.identity_scheme==body.identity_scheme,SensorDevice.identity_issuer==body.identity_issuer,
        SensorDevice.identity_reference==body.identity_reference))
    if exists:raise HTTPException(409,'此明确标识已有统一设备，请核查并将接口绑定到原设备')
    device=SensorDevice(workspace_id=p.workspace_id,title=body.title,identity_scheme=body.identity_scheme,
        identity_issuer=body.identity_issuer,identity_reference=body.identity_reference,data={'submitted_by':p.user_id,
            'identity_basis':body.reason,'verification_basis':'operator_registered_identity_not_hardware_probe'})
    db.add(device);db.flush();event(db,p,device,'registered',body.reason);return device

def change_status(db,p,identifier,body,action):
    require(p,'admin');lock(db,p);device=scoped(db,SensorDevice,identifier,p,True);check_version(device,body.expected_version)
    accepted={'approve':{'review'},'pause':{'enabled'},'resume':{'disabled'}}
    if device.status not in accepted[action]:raise HTTPException(409,'设备状态已变化，请刷新核查')
    before=as_dict(device);device.status='disabled' if action=='pause' else 'enabled';device.version+=1
    if action!='pause':device.approved_by=p.user_id
    device.data={**device.data,action+'_reason':body.reason};event(db,p,device,action,body.reason,before);return device

def unresolved(db,p,asset_id,device_id=None):
    query=select(Acquisition.id).where(Acquisition.workspace_id==p.workspace_id,
        Acquisition.status.in_(['dispatching','unknown','awaiting_observation']))
    if device_id:
        query=query.where((Acquisition.data['sensor_asset_id'].as_string()==asset_id)|
            (Acquisition.data['sensor_device_id'].as_string()==device_id))
    else:query=query.where(Acquisition.data['sensor_asset_id'].as_string()==asset_id)
    return bool(db.scalar(query.limit(1)))

def bind(db,p,asset_id,device_id,expected_asset,expected_device,reason,*,reconfirm=False):
    require(p,'admin');lock(db,p)
    asset=scoped(db,SensorAsset,asset_id,p,True);check_version(asset,expected_asset)
    device=scoped(db,SensorDevice,device_id,p,True);check_version(device,expected_device)
    if device.status!='enabled' or not active_admin(db,p,device.approved_by):raise HTTPException(409,'先确认或恢复统一设备，再核查接口绑定')
    from .sensing_selection import validate_asset
    source=validate_asset(db,p,asset.source_id,asset.data)
    link=binding(db,p,asset_id);old_device=link.device_id if link else None
    for identifier in set(filter(None,[old_device,device_id])):
        hold=occupied(db,p,identifier)
        if hold and hold.acquisition_id or unresolved(db,p,asset_id,identifier):raise HTTPException(409,'设备或接口有尚未结束/未知动作，核对永久结束前禁止改绑')
    source_hold=db.scalar(select(SourceReservation).where(SourceReservation.workspace_id==p.workspace_id,SourceReservation.source_id==asset.source_id))
    if source_hold and source_hold.acquisition_id:raise HTTPException(409,'来源仍有旧控制动作占用，先核对原请求，不能猜测迁移身份')
    legacy=db.scalar(select(SensorReservation).where(SensorReservation.workspace_id==p.workspace_id,SensorReservation.asset_id==asset_id))
    if legacy and legacy.acquisition_id:raise HTTPException(409,'原接口占用尚未释放，不能迁移未知动作')
    before=as_dict(link) if link else None
    if link and link.device_id==device_id and not reconfirm:raise HTTPException(409,'此接口已绑定该设备；如需重核原归属，请明确选择重新核查')
    if reconfirm and (not link or link.device_id!=device_id):raise HTTPException(422,'重新核查只能保留原设备归属，不能用于改绑')
    if link:link.device_id=device_id;link.approved_by=p.user_id;link.version+=1
    else:
        link=AssetDeviceBinding(workspace_id=p.workspace_id,asset_id=asset_id,device_id=device_id,approved_by=p.user_id);db.add(link)
    link.data={'reason':reason,'asset_source_hash':source.content_hash,
        'device_version_at_review':device.version}
    asset.version+=1;db.flush()
    db.add(SensorAssetEvent(workspace_id=p.workspace_id,asset_id=asset.id,actor_id=p.user_id,data={
        'operation':'device_bound','reason':reason,'binding_before':before,'binding_after':as_dict(link),'after':as_dict(asset)}))
    audit(db,p,'sensor.device_bound',asset.id,{'device_id':device.id,'reason':reason});return link


def unidentified_inflight(db,p):
    return bool(db.scalar(select(Acquisition.id).join(Capability,Capability.id==Acquisition.capability_id).where(
        Acquisition.workspace_id==p.workspace_id,Capability.workspace_id==p.workspace_id,
        Acquisition.status.in_(['dispatching','unknown','awaiting_observation']),
        Acquisition.data['sensor_device_id'].as_string().is_(None),
        Capability.data['execution']['side_effect'].as_boolean().is_(True)).limit(1)))


def scope_snapshot(db,p,asset_ids):
    result={}
    for identifier in asset_ids:
        link,device=checked(db,p,identifier)
        result[identifier]={'binding_id':link.id,'device_id':device.id,'identity_hash':identity_hash(device)}
    return result


def identity_hash(device):
    return digest({'scheme':device.identity_scheme,'issuer':device.identity_issuer,'reference':device.identity_reference})
