"""Media gateway directory -> explicitly authorized channel -> real capture proof."""
import json
from fastapi import APIRouter,HTTPException
from pydantic import Field,field_validator
from sqlalchemy import select
from .setup_api import P,DB
from .contracts import Contract
from .models import MediaGateway,MediaChannel,StreamSession,Job,VideoRun,Capability,now
from .services import scoped,require,audit,check_version,register,validate_capability,publish
from .specialist_api import Version
from .media import origin,valid_path,inventory,CAPTURE_HASH,validated_channel,channel_manifest
from .video_api import Configuration
from .local_specialists import ready_detection_install as ready_install

router=APIRouter(prefix='/api/media')


def view(row):
    return {'id':row.id,'version':row.version,'status':row.status,'created_at':row.created_at.isoformat(),**row.data}


@router.get('/gateways')
def gateways(p:P,db:DB):
    return [view(x) for x in db.scalars(select(MediaGateway).where(MediaGateway.workspace_id==p.workspace_id).order_by(MediaGateway.created_at.desc()))]


class Gateway(Contract):
    name:str=Field(min_length=1,max_length=150)
    api_origin:str=Field(max_length=200)
    rtsp_origin:str=Field(max_length=200)
    @field_validator('name')
    @classmethod
    def nonempty_name(cls,value):
        if not value.strip():raise ValueError('名称不能为空')
        return value.strip()


@router.post('/gateways',status_code=201)
def create(body:Gateway,p:P,db:DB):
    require(p,'admin')
    row=MediaGateway(workspace_id=p.workspace_id,data={'name':body.name.strip(),'api_origin':origin(body.api_origin,'api'),
        'rtsp_origin':origin(body.rtsp_origin,'rtsp'),'generation':1,'inventory':None,'error':None})
    db.add(row);db.flush();audit(db,p,'media.gateway.saved',row.id,{});db.commit();return view(row)


class GatewayEdit(Gateway):
    version:int=Field(ge=1)


@router.put('/gateways/{object_id}')
def edit(object_id:str,body:GatewayEdit,p:P,db:DB):
    require(p,'admin');row=scoped(db,MediaGateway,object_id,p,True);check_version(row,body.version)
    old={k:row.data[k] for k in ['name','api_origin','rtsp_origin','generation']}
    row.data={**row.data,'name':body.name.strip(),'api_origin':origin(body.api_origin,'api'),'rtsp_origin':origin(body.rtsp_origin,'rtsp'),
        'generation':row.data['generation']+1,'inventory':None,'error':None,'history':[*row.data.get('history',[]),old]}
    for channel in db.scalars(select(MediaChannel).where(MediaChannel.gateway_id==row.id,MediaChannel.workspace_id==p.workspace_id)):
        channel.status='disabled';channel.version+=1;channel.data={**channel.data,'read_authorized':False,'generation':channel.data['generation']+1}
    row.status='draft';row.version+=1;audit(db,p,'media.gateway.changed',row.id,{});db.commit();return view(row)


@router.post('/gateways/{object_id}/discover')
def discover(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,MediaGateway,object_id,p,True);check_version(row,body.version)
    try:
        result=inventory(row);row.status='ready';row.data={**row.data,'inventory':result,'error':None}
    except HTTPException as error:
        row.status='failed';row.data={**row.data,'error':error.detail,'inventory':None}
    row.version+=1;audit(db,p,'media.directory.checked',row.id,{'status':row.status});db.commit();return view(row)


class Channel(Contract):
    gateway_id:str
    gateway_version:int=Field(ge=1)
    path:str=Field(max_length=150)
    name:str=Field(min_length=1,max_length=150)
    read_authorized:bool=False
    @field_validator('name')
    @classmethod
    def nonempty_name(cls,value):
        if not value.strip():raise ValueError('名称不能为空')
        return value.strip()


@router.post('/channels',status_code=201)
def confirm(body:Channel,p:P,db:DB):
    require(p,'admin');gateway=scoped(db,MediaGateway,body.gateway_id,p,True);check_version(gateway,body.gateway_version)
    path=valid_path(body.path)
    if not body.read_authorized:raise HTTPException(422,'需要明确确认此通道的读取授权')
    if gateway.status!='ready' or not any(x['path']==path for x in (gateway.data.get('inventory') or {}).get('channels',[])):
        raise HTTPException(409,'通道不在当前读取的目录中，请重新检查网关目录')
    old=db.scalar(select(MediaChannel).where(MediaChannel.workspace_id==p.workspace_id,MediaChannel.gateway_id==gateway.id,MediaChannel.path==path))
    if old:return view(old)
    row=MediaChannel(workspace_id=p.workspace_id,gateway_id=gateway.id,path=path,data={'name':body.name.strip(),
        'read_authorized':True,'authorized_by':p.user_id,'authorized_at':now().isoformat(),'generation':1,'capability_id':None})
    db.add(row);db.flush();audit(db,p,'media.channel.confirmed',row.id,{'path':path});db.commit();return view(row)


@router.get('/channels')
def channels(p:P,db:DB):
    result=[]
    for row in db.scalars(select(MediaChannel).where(MediaChannel.workspace_id==p.workspace_id).order_by(MediaChannel.created_at.desc())):
        try:validated_channel(db,p,row.id);current=True
        except HTTPException:current=False
        result.append({**view(row),'path':row.path,'gateway_id':row.gateway_id,'validation_current':current})
    return result


@router.post('/channels/{object_id}/disable')
def disable(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,MediaChannel,object_id,p,True);check_version(row,body.version)
    row.status='disabled';row.version+=1;row.data={**row.data,'read_authorized':False,'generation':row.data['generation']+1}
    audit(db,p,'media.channel.disabled',row.id,{});db.commit();return view(row)


@router.post('/channels/{object_id}/reconfirm')
def reconfirm(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,MediaChannel,object_id,p,True);check_version(row,body.version)
    gateway=scoped(db,MediaGateway,row.gateway_id,p)
    if gateway.status!='ready' or not any(x['path']==row.path for x in (gateway.data.get('inventory') or {}).get('channels',[])):raise HTTPException(409,'请先重新核对网关目录及此通道')
    row.status='confirmed';row.version+=1;row.data={**row.data,'read_authorized':True,'generation':row.data['generation']+1,
        'authorized_by':p.user_id,'authorized_at':now().isoformat(),'validation':None,'capability_id':None}
    audit(db,p,'media.channel.reconfirmed',row.id,{});db.commit();return view(row)


class Capture(Contract):
    channel_version:int=Field(ge=1)
    installation_id:str
    seconds:int=Field(default=6,ge=4,le=12)
    configuration:Configuration=Field(default_factory=Configuration)


@router.post('/channels/{object_id}/capture',status_code=201)
def capture(object_id:str,body:Capture,p:P,db:DB):
    from .media_monitor import node_lock
    require(p,'admin','operator');node_lock(db);channel=scoped(db,MediaChannel,object_id,p,True);check_version(channel,body.channel_version)
    gateway=scoped(db,MediaGateway,channel.gateway_id,p)
    if not channel.data.get('read_authorized') or channel.status=='disabled' or gateway.status!='ready':raise HTTPException(409,'通道读取尚未授权或网关尚未验证')
    installation=ready_install(db,p,body.installation_id)
    from .continuous_media import reject_source_conflict
    reject_source_conflict(db,channel,gateway)
    if body.configuration.recorded_at:raise HTTPException(422,'实时流没有校准的拍摄UTC；此验证不能手填日期冒充来源时间')
    active=db.scalar(select(StreamSession).where(StreamSession.channel_id==channel.id,StreamSession.status.in_(['queued','connecting','capturing'])))
    if active:
        if active.data['seconds']!=body.seconds or active.data['configuration']!=body.configuration.model_dump(mode='json') or active.data['installation_id']!=body.installation_id:raise HTTPException(409,'此通道正在验证，不能静默替换已保存的设置；请先停止或等待完成')
        return view(active)
    from .media_monitor import queue_capture, OPEN_MONITOR
    from .models import MediaMonitor
    if db.scalar(select(MediaMonitor.id).where(MediaMonitor.channel_id==channel.id,MediaMonitor.status.in_(OPEN_MONITOR))):
        raise HTTPException(409,'此通道已有自动读取计划；请先停止计划再手动验证')
    row=queue_capture(db,p,channel,gateway,installation,body.seconds,body.configuration.model_dump(mode='json'))
    from .manual_video_provenance_v348 import declare
    declare(db,p,row)
    db.commit();return view(row)


@router.get('/sessions')
def sessions(p:P,db:DB):
    rows=db.scalars(select(StreamSession).where(StreamSession.workspace_id==p.workspace_id).order_by(StreamSession.created_at.desc()).limit(50)).all()
    result=[]
    for row in rows:
        run=scoped(db,VideoRun,row.video_run_id,p) if row.video_run_id else None
        result.append({**view(row),'channel_id':row.channel_id,'evidence_id':row.evidence_id,'video_run_id':row.video_run_id,
                       'processing_status':run.status if run else None})
    return result


@router.post('/sessions/{object_id}/cancel')
def cancel(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,StreamSession,object_id,p,True)
    # This ID is an immutable read request; progress increments its version.
    # A stop intent applies to this request even if fresh progress arrived.
    if body.version>row.version:raise HTTPException(409,'记录版本无效，请重新读取')
    if row.status=='cancelled':return view(row)
    if row.status not in {'queued','connecting','capturing'}:raise HTTPException(409,'读取已结束；保存记录保留')
    row.status='cancelled';row.version+=1;audit(db,p,'media.capture.cancelled',row.id,{});db.commit();return view(row)


@router.post('/channels/{object_id}/register')
def register_channel(object_id:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,MediaChannel,object_id,p,True);check_version(row,body.version)
    row,gateway=validated_channel(db,p,row.id)
    manifest=channel_manifest(row,gateway)
    if row.data.get('capability_id'):
        previous=scoped(db,Capability,row.data['capability_id'],p)
        if previous.status=='published' and previous.data==manifest.model_dump(mode='json'):return view(row)
    cap=register(db,p,manifest);validate_capability(db,p,cap.id,cap.version)
    publish(db,p,cap.id,cap.version,'实际通道读取与本地处理样例通过；设备身份和领域自动化未验证')
    row.data={**row.data,'capability_id':cap.id};row.version+=1;db.commit();return view(row)
