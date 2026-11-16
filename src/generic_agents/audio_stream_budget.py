"""Source-level audio read accounting shared with existing video limits.

This module never grants audio consent or opens a connection.  The controller
owns live instance/model authority; every attempted connection receives an
independent immutable audit before transport, including failed attempts.
"""
import math
from datetime import datetime,timedelta
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import Audit

ACTION='audio.stream_read_reserved_v255'
SCHEMA='audio-stream-read-reservation-v255'
OPEN={'queued','recording','reconnecting','paused','blocked'}
ERROR='音轨来源的共享读取预算或原回执无法核对，请保留记录后检查'

def need(value,reason=ERROR):
    if not value:raise HTTPException(409,reason)

def _source(db,channel,gateway):
    from .video_read_boundary import physical
    return physical(db,channel,gateway)

def _definition(stream):
    d=stream.data;value=d['definition']
    need(type(value) is dict and digest(value)==d['definition_hash'])
    binding=value['binding'];bounds=binding['read_bounds']
    need(type(bounds) is dict and set(bounds)=={'daily_limit','cooldown_minutes','max_read_seconds'})
    n=bounds['daily_limit'];cool=bounds['cooldown_minutes'];maximum=bounds['max_read_seconds']
    need(n is None or type(n) is int and 1<=n<=10000)
    need(type(cool) in {int,float} and math.isfinite(cool) and 0<=cool<=1440)
    need(maximum is None or type(maximum) in {int,float} and math.isfinite(maximum) and 1<=maximum<=86400)
    return value,bounds

def _checked(receipt,stream,proof):
    need(type(receipt) is dict and receipt.get('schema')==SCHEMA)
    value={k:v for k,v in receipt.items() if k!='receipt_hash'}
    need(receipt.get('receipt_hash')==digest(value) and receipt['controller_id']==stream.id
         and receipt['workspace_id']==stream.workspace_id and receipt['actor_id']==stream.actor_id
         and receipt['channel_id']==stream.channel_id and receipt['definition_hash']==stream.data['definition_hash']
         and receipt['binding_hash']==stream.data['definition']['binding']['binding_hash'])
    stamp=datetime.fromisoformat(receipt['reserved_at']);need(stamp.tzinfo is not None)
    need(type(receipt['generation']) is int and receipt['generation']>=1
         and type(receipt['connection_id']) is str and bool(receipt['connection_id'])
         and type(receipt['requested_seconds']) in {int,float} and math.isfinite(receipt['requested_seconds'])
         and 1<=receipt['requested_seconds']<=3600)
    need(proof.object_id==stream.id and proof.workspace_id==stream.workspace_id
         and proof.actor_id==stream.actor_id and digest(proof.data)==digest(receipt) and proof.created_at==stamp)
    return stamp

def read_times(db,channel,gateway,current):
    """Historical attempts count even after cancellation, revocation or failure."""
    from .audio_stream_models import AudioStream
    key,channels=_source(db,channel,gateway);ids=[c.id for c in channels]
    rows=list(db.scalars(select(AudioStream).where(AudioStream.channel_id.in_(ids)).limit(10001)))
    need(len(rows)<=10000)
    proofs=list(db.scalars(select(Audit).where(Audit.action==ACTION,Audit.data['source_key'].as_string()==key).limit(10001)))
    need(len(proofs)<=10000)
    by_hash={}
    for proof in proofs:
        signature=proof.data.get('receipt_hash');need(type(signature) is str and signature not in by_hash);by_hash[signature]=proof
    times=[];seen=set()
    for stream in rows:
        entries=stream.data.get('read_reservations');need(type(entries) is list and len(entries)<=100)
        if not entries:continue
        _definition(stream)
        for receipt in entries:
            if receipt.get('source_key')!=key:continue
            signature=receipt.get('receipt_hash');need(signature not in seen and signature in by_hash)
            times.append(_checked(receipt,stream,by_hash[signature]));seen.add(signature)
    need(seen==set(by_hash))
    return times

def has_open_source(db,channel,gateway,exclude_id=None):
    from .audio_stream_models import AudioStream
    _,channels=_source(db,channel,gateway)
    query=select(AudioStream.id).where(AudioStream.channel_id.in_([c.id for c in channels]),AudioStream.status.in_(OPEN))
    if exclude_id is not None:query=query.where(AudioStream.id!=exclude_id)
    return db.scalar(query.limit(1)) is not None

def reserve(db,p,stream,channel,gateway,seconds,connection_id,generation):
    from .media_monitor import node_lock,clock
    from .video_read_boundary import usage,ReadBudgetWait
    from .models import StreamSession,ContinuousStream
    from .video_read_boundary import CHINA
    node_lock(db);definition,bounds=_definition(stream)
    need(stream.workspace_id==p.workspace_id and stream.actor_id==p.user_id and stream.channel_id==channel.id
         and stream.status in {'queued','recording','reconnecting'} and stream.data['generation']==generation)
    need(type(seconds) in {int,float} and math.isfinite(seconds) and 1<=seconds<=3600)
    need(type(connection_id) is str and 1<=len(connection_id)<=100 and '\0' not in connection_id)
    for prior in stream.data['read_reservations']:
        if prior['connection_id']==connection_id:
            need(prior['generation']==generation and prior['requested_seconds']==seconds)
            guard(db,p,stream,prior,channel,gateway);return prior
    key,channels=_source(db,channel,gateway);ids=[c.id for c in channels];current=clock(db)
    if has_open_source(db,channel,gateway,stream.id):raise ReadBudgetWait('来源已有未结束的音轨读取，请先完成或停止原读取',current+timedelta(seconds=5))
    video=db.scalar(select(StreamSession.id).where(StreamSession.channel_id.in_(ids),StreamSession.status.in_(['queued','connecting','capturing'])).limit(1))
    continuous=db.scalar(select(ContinuousStream.id).where(ContinuousStream.channel_id.in_(ids),ContinuousStream.status.in_(['queued','recording','reconnecting','paused','blocked'])).limit(1))
    if video or continuous:raise ReadBudgetWait('来源已有未结束的视频读取，请先完成或停止原读取',current+timedelta(seconds=5))
    state=usage(db,channel,gateway,current)
    if bounds['daily_limit'] is not None and state['used']>=bounds['daily_limit']:
        retry=current.astimezone(CHINA).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        raise ReadBudgetWait('来源今日共享读取次数已用完；音轨、视频和失败连接共用次数',retry)
    if state['last_read_at'] and current<datetime.fromisoformat(state['last_read_at'])+timedelta(minutes=bounds['cooldown_minutes']):
        raise ReadBudgetWait('仍在来源共享读取间隔内，切换音轨与视频不会重置',datetime.fromisoformat(state['last_read_at'])+timedelta(minutes=bounds['cooldown_minutes']))
    need(bounds['max_read_seconds'] is None or seconds<=bounds['max_read_seconds'],'本次音轨读取超过已发布设备时长边界')
    value={'schema':SCHEMA,'controller_id':stream.id,'workspace_id':p.workspace_id,'actor_id':p.user_id,
           'channel_id':channel.id,'source_key':key,'generation':generation,'connection_id':connection_id,
           'requested_seconds':seconds,'definition_hash':stream.data['definition_hash'],
           'binding_hash':definition['binding']['binding_hash'],'reserved_at':current.isoformat(),
           'usage_before':state}
    receipt={**value,'receipt_hash':digest(value)}
    stream.data={**stream.data,'read_reservations':[*stream.data['read_reservations'],receipt]}
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
                 object_id=stream.id,data=receipt,created_at=current))
    db.flush();return receipt

def guard(db,p,stream,receipt,channel,gateway):
    _definition(stream);need(stream.workspace_id==p.workspace_id and stream.actor_id==p.user_id)
    key,_=_source(db,channel,gateway);need(receipt['source_key']==key and receipt['generation']==stream.data['generation'])
    proofs=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==ACTION,Audit.object_id==stream.id,Audit.data['receipt_hash'].as_string()==receipt['receipt_hash']).limit(2)))
    need(len(proofs)==1 and any(digest(item)==digest(receipt) for item in stream.data['read_reservations']));_checked(receipt,stream,proofs[0])
