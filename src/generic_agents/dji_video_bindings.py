"""User-confirmed DJI to pre-authorized media-channel association.

Does not start, stop, authenticate, or control a DJI livestream. The association
is a user's claim, not independently verified device identity.
"""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import StrictBool, StrictInt
from sqlalchemy import select
from .contracts import Contract, digest
from .models import AuthSession, Job, MediaChannel, MediaGateway, Membership, User, now
from .services import audit, require, scoped
from .setup_api import P, DB
from .ftps_gateway_api import _lock, _current_admin
from .dji_cloud_api import _source, read_authorized_dock_status
from .instance_live_video import readonly_proof

router=APIRouter(prefix='/api/dji/video-bindings',tags=['大疆已授权视频通道'])
KIND='dji_video_binding_v1';OP='dji_video_binding_op_v1';STOP='dji_video_binding_stop_v1'
NOTICE='仅查看由管理员关联的已有媒体通道；关联未证明画面来自该设备，不会启动或停止大疆推流。'

class Create(Contract):
    request_id:UUID
    connection_id:UUID
    channel_id:UUID
    expected_connection_version:StrictInt
    expected_channel_version:StrictInt
    confirmed:StrictBool

class State(Contract):
    request_id:UUID
    expected_version:StrictInt
    enabled:StrictBool


def _op(p,rid):return 'dji-video-operation:'+p.user_id+':'+str(rid)
def _pair(connection_id,channel_id):return 'dji-video-pair:'+digest({'connection':connection_id,'channel':channel_id})
def _record(db,p,rid):return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_op(p,rid)).execution_options(populate_existing=True))
def _result(record):
    if record.kind==STOP:return {'state':'cancelled','request_id':record.data['request_id']}
    if record.kind!=OP or record.status!='internal':raise HTTPException(409,'原请求无法核对')
    return {'state':'succeeded','request_id':record.data['request_id'],'binding':record.data['result']}

def _member(db,p):
    row=db.execute(select(User.id).join(Membership,Membership.user_id==User.id).join(AuthSession,AuthSession.user_id==User.id)
        .where(User.id==p.user_id,User.active.is_(True),Membership.workspace_id==p.workspace_id,
               Membership.role==p.role,Membership.role.in_(['admin','operator']),AuthSession.id==p.session_id,
               AuthSession.revoked.is_(False),AuthSession.expires_at>now())
        .with_for_update(read=True,of=[User,Membership,AuthSession])).first()
    if not row:raise HTTPException(403,'登录或工作区授权已失效')

def _record_binding(db,p,identity,lock=False):
    row=scoped(db,Job,str(identity),p,lock)
    if row.kind!=KIND or row.status!='internal' or not isinstance(row.data,dict):raise HTTPException(404,'未找到视频关联')
    return row

def _proof(db,p,connection_id,channel_id):
    gateway,registration=_source(db,p,connection_id)
    dji=read_authorized_dock_status(db,p,gateway.id,registration.data['device_sn'])
    candidate=scoped(db,MediaChannel,channel_id,p)
    # Media gateway edits lock gateway then channel. Hold matching shared locks
    # through the binding transaction so source revocation cannot commit first.
    db.scalar(select(MediaGateway).where(MediaGateway.workspace_id==p.workspace_id,MediaGateway.id==candidate.gateway_id).with_for_update(read=True))
    db.scalar(select(MediaChannel).where(MediaChannel.workspace_id==p.workspace_id,MediaChannel.id==channel_id).with_for_update(read=True).execution_options(populate_existing=True))
    db.expire(candidate)
    proof=readonly_proof(db,p,channel_id)
    channel=scoped(db,MediaChannel,channel_id,p)
    return {'connection_id':gateway.id,'device_sn':registration.data['device_sn'],
            'dji_config_hash':dji['connection']['configuration_hash'],'channel':proof},gateway,channel

def _checked(db,p,row):
    _member(db,p)
    data=row.data
    if data.get('enabled') is not True:raise HTTPException(409,'关联已暂停')
    proof,gateway,channel=_proof(db,p,data['connection_id'],data['channel_id'])
    if proof!=data.get('proof') or digest(proof)!=data.get('proof_hash'):raise HTTPException(409,'机场或视频来源授权已变化，请重新核对关联')
    media_gateway=scoped(db,MediaGateway,channel.gateway_id,p)
    return proof,gateway,channel,media_gateway

def _view(row,gateway,channel):
    return {'id':row.id,'version':row.version,'enabled':row.data.get('enabled') is True,
            'connection_id':gateway.id,'connection_name':gateway.name,'device_sn':row.data['device_sn'],
            'channel_id':channel.id,'channel_name':channel.data.get('name','视频通道'),'notice':NOTICE}

@router.get('/options')
def options(p:P,db:DB,response:Response,connection_page:int=Query(default=1,ge=1,le=10000),channel_page:int=Query(default=1,ge=1,le=10000)):
    require(p,'admin','operator');response.headers['Cache-Control']='no-store';sources=[];channels=[]
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='dji_cloud_osd_source_v1',Job.status=='internal').order_by(Job.created_at.desc(),Job.id.desc()).offset((connection_page-1)*50).limit(51)).all()
    for row in rows[:50]:
        try:
            gateway,reg=_source(db,p,row.data['gateway_id']);read_authorized_dock_status(db,p,gateway.id,reg.data['device_sn'])
            sources.append({'id':gateway.id,'name':gateway.name,'version':gateway.version,'device_sn':reg.data['device_sn']})
        except (HTTPException,KeyError,TypeError,ValueError):continue
    video_rows=db.scalars(select(MediaChannel).where(MediaChannel.workspace_id==p.workspace_id).order_by(MediaChannel.created_at.desc(),MediaChannel.id.desc()).offset((channel_page-1)*50).limit(51)).all()
    for channel in video_rows[:50]:
        try:readonly_proof(db,p,channel.id)
        except HTTPException:continue
        channels.append({'id':channel.id,'name':channel.data.get('name','视频通道'),'version':channel.version})
    return {'connections':sources,'channels':channels,'connection_page':connection_page,'channel_page':channel_page,'has_more_connections':len(rows)>50,'has_more_channels':len(video_rows)>50,'notice':NOTICE}

@router.get('')
def listing(p:P,db:DB,response:Response,page:int=Query(default=1,ge=1,le=10000)):
    require(p,'admin','operator');response.headers['Cache-Control']='no-store';rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal').order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*50).limit(51)).all();items=[]
    for row in rows[:50]:
        try:
            gateway,reg=_source(db,p,row.data['connection_id']);channel=scoped(db,MediaChannel,row.data['channel_id'],p);item=_view(row,gateway,channel)
            try:_checked(db,p,row);item['available']=True
            except HTTPException:item['available']=False
            items.append(item)
        except (HTTPException,KeyError,TypeError,ValueError):continue
    return {'bindings':items,'page':page,'has_more':len(rows)>50,'notice':NOTICE}

@router.post('')
def create(body:Create,p:P,db:DB):
    require(p,'admin')
    if not body.confirmed:raise HTTPException(422,'请确认来源关联及读取授权')
    rid=str(body.request_id);connection_id=str(body.connection_id);channel_id=str(body.channel_id)
    fingerprint=digest(body.model_dump(mode='json'));_lock(db,p.workspace_id,_op(p,rid));old=_record(db,p,rid)
    if old:
        if old.kind==STOP:return _result(old)
        if old.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原请求已绑定其他内容')
        return _result(old)
    _lock(db,p.workspace_id,_pair(connection_id,channel_id))
    proof,gateway,channel=_proof(db,p,connection_id,channel_id);_member(db,p);_current_admin(db,p)
    if gateway.version!=body.expected_connection_version or channel.version!=body.expected_channel_version:raise HTTPException(409,'机场或通道已变化，请重新选择')
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_pair(connection_id,channel_id)).with_for_update())
    if row:
        if row.kind!=KIND:raise HTTPException(409,'关联记录无法核对')
        history=list(row.data.get('history',[]));history.append({'proof_hash':row.data.get('proof_hash'),'enabled':row.data.get('enabled'),'at':now().isoformat()})
        row.data={**row.data,'enabled':True,'proof':proof,'proof_hash':digest(proof),'history':history[-100:]};row.version+=1
    else:
        row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=_pair(connection_id,channel_id),data={'connection_id':connection_id,'channel_id':channel_id,'device_sn':proof['device_sn'],'enabled':True,'proof':proof,'proof_hash':digest(proof),'actor_id':p.user_id,'history':[]});db.add(row);db.flush()
    result=_view(row,gateway,channel);db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op(p,rid),data={'request_id':rid,'fingerprint':fingerprint,'result':result,'actor_id':p.user_id}));audit(db,p,'dji.video.bound',row.id,{'proof_hash':digest(proof)});db.commit();return {'state':'succeeded','request_id':rid,'binding':result}

@router.post('/{identity}/pause')
def pause(identity:UUID,body:State,p:P,db:DB):
    require(p,'admin');rid=str(body.request_id);fp=digest({'id':str(identity),**body.model_dump(mode='json')});_lock(db,p.workspace_id,_op(p,rid));old=_record(db,p,rid)
    if old:
        if old.kind==STOP:return _result(old)
        if old.data.get('fingerprint')!=fp:raise HTTPException(409,'原请求已绑定其他内容')
        return _result(old)
    if body.enabled:raise HTTPException(422,'请重新核对来源后再次关联')
    row=_record_binding(db,p,identity,True);_member(db,p);_current_admin(db,p)
    if row.version!=body.expected_version:raise HTTPException(409,'关联版本已变化')
    row.data={**row.data,'enabled':False};row.version+=1;gateway,_=_source(db,p,row.data['connection_id']);channel=scoped(db,MediaChannel,row.data['channel_id'],p);result=_view(row,gateway,channel)
    db.add(Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op(p,rid),data={'request_id':rid,'fingerprint':fp,'result':result,'actor_id':p.user_id}));audit(db,p,'dji.video.paused',row.id,{});db.commit();return {'state':'succeeded','request_id':rid,'binding':result}

@router.get('/operations/{rid}')
def operation(rid:UUID,p:P,db:DB):
    require(p,'admin','operator','viewer');row=_record(db,p,str(rid))
    if not row:raise HTTPException(404,'尚未找到原请求')
    return _result(row)

@router.post('/operations/{rid}/cancel')
def cancel(rid:UUID,p:P,db:DB):
    require(p,'admin','operator','viewer');rid=str(rid);_lock(db,p.workspace_id,_op(p,rid));old=_record(db,p,rid)
    if old:return _result(old)
    db.add(Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_op(p,rid),data={'request_id':rid,'actor_id':p.user_id}));db.commit();return {'state':'cancelled','request_id':rid}


def _frames(lease, first, gateway, channel, initial_source):
    """Read frames without holding database locks during decoder or network I/O."""
    import time
    from .media import source_state
    from .instance_live_video import _part, _read_frame, MAX_SECONDS
    proc=lease.proc;started=time.monotonic();last_source=started
    try:
        yield _part(first)
        while True:
            clock=time.monotonic()
            if clock-started>=MAX_SECONDS:break
            frame=_read_frame(proc,min(started+MAX_SECONDS,clock+5));clock=time.monotonic()
            if clock-started>=MAX_SECONDS:break
            if clock-last_source>=10:
                if source_state(gateway,channel).get('source_id')!=initial_source:break
                last_source=clock
            yield _part(frame)
    except (Exception,GeneratorExit):pass
    finally:lease.close()


def _lock_frame(p,identity,expected):
    """Hold the authorization rows only until this frame has been handed to ASGI."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from .db import engine
    db=sessionmaker(engine(),expire_on_commit=False)()
    try:
        if db.bind.dialect.name=='postgresql':db.execute(text("SET LOCAL lock_timeout = '1s'"))
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==identity)
                      .with_for_update(read=True).execution_options(populate_existing=True))
        if not row or row.kind!=KIND or row.status!='internal':raise ValueError('binding unavailable')
        if _checked(db,p,row)[0]!=expected:raise ValueError('authorization changed')
        return db
    except BaseException:
        db.close();raise


def _response(lease, frames, p, identity, proof):
    """Serialize each ASGI send with revocation, bounded by a short timeout.

    Bytes already accepted by a server, proxy or client cannot be recalled.
    A subsequent frame cannot be sent after the revocation transaction commits.
    """
    import asyncio
    import anyio
    from .instance_live_video import _LiveResponse
    class GuardedResponse(_LiveResponse):
        async def stream_response(self, send):
            await send({'type':'http.response.start','status':self.status_code,'headers':self.raw_headers})
            async for chunk in self.body_iterator:
                try:
                    db=await anyio.to_thread.run_sync(_lock_frame,p,identity,proof)
                except Exception:
                    break
                try:
                    await asyncio.wait_for(send({'type':'http.response.body','body':chunk,'more_body':True}),timeout=1)
                except (TimeoutError, OSError):
                    break
                finally:
                    db.close()
            await send({'type':'http.response.body','body':b'','more_body':False})
    return GuardedResponse(lease,frames,media_type='multipart/x-mixed-replace; boundary=frame',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','X-Accel-Buffering':'no'})

@router.get('/{identity}/live')
def live(identity:UUID,p:P,db:DB):
    """Bounded MJPEG preview of the already authorised MediaMTX RTSP stream."""
    import copy,json,os,subprocess,time
    from pathlib import Path
    from types import SimpleNamespace
    from .config import settings
    from .instance_live_video import _slots,_Lease,_read_frame
    from .media import source_state,stream_url
    require(p,'admin','operator');identity=str(identity);row=_record_binding(db,p,identity)
    proof,_,channel,gateway=_checked(db,p,row)
    # Never retain the request transaction while connecting or streaming.
    gateway=SimpleNamespace(data=copy.deepcopy(gateway.data));channel=SimpleNamespace(path=channel.path)
    db.rollback()
    if not _slots.acquire(blocking=False):raise HTTPException(429,'实时画面连接数已满，请稍后重试')
    lease=_Lease()
    try:
        state=source_state(gateway,channel);executable=settings().specialist_python.absolute()
        if not executable.is_file():raise RuntimeError('decoder unavailable')
        proc=subprocess.Popen([str(executable),'-u',str(Path(__file__).with_name('instance_live_video_runner.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True,
            env={'PATH':os.defpath,'LANG':'C.UTF-8'});lease.proc=proc
        proc.stdin.write(json.dumps({'url':stream_url(gateway,channel),'parent_pid':os.getpid()}).encode()+b'\n');proc.stdin.close()
        first=_read_frame(proc,time.monotonic()+12);fresh=source_state(gateway,channel)
        if fresh.get('source_id')!=state['source_id']:raise ValueError('source changed')
        return _response(lease,_frames(lease,first,gateway,channel,state['source_id']),p,identity,proof)
    except Exception:
        lease.close();raise HTTPException(409,'实时画面暂不可用，请检查已授权通道和接收状态') from None
