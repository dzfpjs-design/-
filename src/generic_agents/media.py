"""Scoped media-node access, channel identities, capture jobs and source registration."""
import hashlib
import ipaddress
import json
import math
import os
import re
import tempfile
from pathlib import Path
from fractions import Fraction
from urllib.parse import urlsplit,quote
from sqlalchemy import select
from fastapi import HTTPException
import httpx
from .config import settings
from .models import MediaGateway,MediaChannel,MediaMonitor,StreamSession,VideoRun,Job,now
from .contracts import Manifest,digest
from .services import scoped,require,audit
from .local_video import child
from .evidence import save_video_evidence,evidence_bytes

CAPTURE_HASH=digest({'version':'rtsp-capture-v1','av':'16.1.0','code':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    for name in ['media_capture_runner.py','media.py','media_api.py','media_monitor.py','media_monitor_api.py']}})


def origin(value,kind):
    value=value.rstrip('/')
    try:
        parsed=urlsplit(value);address=ipaddress.ip_address(parsed.hostname or '');port=parsed.port
    except ValueError:raise HTTPException(422,'媒体节点必须使用已授权的IP地址及端口')
    schemes={'api':{'http','https'},'rtsp':{'rtsp'}}[kind]
    allowed={v.strip().rstrip('/') for v in settings().media_allowed_origins.split(',') if v.strip()}
    if parsed.scheme not in schemes or not port or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HTTPException(422,'媒体节点填写协议、IP和端口；不得包含路径或凭据')
    if value not in allowed or not (address.is_private or address.is_loopback) or address.is_multicast or address.is_unspecified or address.is_link_local:
        raise HTTPException(422,'该媒体节点未获运行环境授权；请由运维登记确切访问地址')
    return value


def valid_path(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]{0,149}',value) or any(x in {'','..','.'} for x in value.split('/')):
        raise HTTPException(422,'通道路径无效；不能包含跳转、凭据或查询参数')
    return value


def inventory(gateway):
    base=origin(gateway.data['api_origin'],'api');origin(gateway.data['rtsp_origin'],'rtsp')
    from .connectors import headers
    authentication=headers({'secret_ref':settings().media_api_secret_ref}) if settings().media_api_secret_ref else {}
    rows=[];truncated=False
    try:
        with httpx.Client(timeout=5,follow_redirects=False,trust_env=False) as c:
            for page in range(5):
                with c.stream('GET',base+'/v3/paths/list',params={'page':page,'itemsPerPage':100},headers=authentication) as r:
                    if 300<=r.status_code<400:raise ValueError('redirect forbidden')
                    r.raise_for_status();raw=b''
                    for chunk in r.iter_bytes():
                        raw+=chunk
                        if len(raw)>1024*1024:raise ValueError('directory too large')
                value=json.loads(raw)
                if not isinstance(value,dict) or not isinstance(value.get('items'),list) or len(value['items'])>100:raise ValueError('directory contract mismatch')
                for item in value['items']:
                    if not isinstance(item,dict):raise ValueError('invalid channel')
                    path=valid_path(item.get('name'))
                    source=item.get('source') or {}
                    tracks=item.get('tracks',[])
                    if not isinstance(source,dict) or not isinstance(tracks,list) or len(tracks)>10 or any(not isinstance(x,str) or len(x)>100 for x in tracks):raise ValueError('invalid channel tracks/source')
                    if any(x['path']==path for x in rows):raise ValueError('duplicate channel identity')
                    for key in ['id','type']:
                        if source.get(key) is not None and (not isinstance(source[key],str) or len(source[key])>150):raise ValueError('invalid source identity')
                    rows.append({'path':path,'ready':item.get('ready') is True,'tracks':tracks,
                                 'source_id':source.get('id'),'source_type':source.get('type'),'ready_at':item.get('readyTime')})
                pages=value.get('pageCount',1)
                if not isinstance(pages,int) or pages<0:raise ValueError('directory page count invalid')
                if page+1>=pages:break
                truncated=pages>5
    except (httpx.HTTPError,ValueError,TypeError,KeyError):raise HTTPException(409,'媒体网关目录读取失败；请检查地址、凭据和网关运行状态')
    return {'channels':rows,'truncated':truncated,'checked_at':now().isoformat()}


def source_state(gateway,channel):
    result=inventory(gateway)
    item=next((x for x in result['channels'] if x['path']==channel.path),None)
    if not item or not item['ready']:raise HTTPException(409,'通道当前没有可读取的视频；已保存记录仍可查看')
    if not item['source_id']:raise HTTPException(409,'网关未提供来源会话标识，无法核对读取期间的来源变化')
    if 'H264' not in item['tracks'] and 'H.264' not in item['tracks']:
        raise HTTPException(409,'通道尚未验证H.264读取；不自动改用其他编码或云模型')
    return item


def context_guard(db,p,context):
    channel=scoped(db,MediaChannel,context['channel_id'],p);db.refresh(channel)
    gateway=scoped(db,MediaGateway,channel.gateway_id,p);db.refresh(gateway)
    session=scoped(db,StreamSession,context['session_id'],p);db.refresh(session)
    if channel.status=='disabled' or not channel.data.get('read_authorized') or gateway.status=='disabled' or channel.data['generation']!=context['channel_generation'] or gateway.data['generation']!=context['gateway_generation']:
        raise HTTPException(409,'视频来源读取授权或连接配置已变化，未继续处理')
    if session.evidence_id!=context['evidence_id']:raise HTTPException(409,'视频会话原件关联不一致')
    return channel,gateway,session


def capture_guard(db,p,session_id):
    db.expire_all();row=scoped(db,StreamSession,session_id,p,True)
    channel=scoped(db,MediaChannel,row.channel_id,p);gateway=scoped(db,MediaGateway,channel.gateway_id,p)
    if row.status=='cancelled' or not channel.data.get('read_authorized') or channel.status=='disabled' or gateway.status=='disabled' or row.data['channel_generation']!=channel.data['generation'] or row.data['gateway_generation']!=gateway.data['generation']:
        raise ValueError('读取已停止，或通道授权/网关配置已变化')
    if row.data['capture_hash']!=CAPTURE_HASH:raise ValueError('视频读取版本已变化，请重新验证')
    from .video_read_boundary import guard as read_guard
    if not row.data.get('read_reservation_id'):raise ValueError('读取预算回执缺失，不能继续读取')
    reservation=read_guard(db,p,row.data['read_reservation_id'])
    if reservation.channel_id!=row.channel_id or reservation.data['receipt_hash']!=row.data.get('read_reservation_hash'):
        raise ValueError('读取请求与保存的预算回执不一致')
    if row.data.get('monitor_id'):
        monitor=scoped(db,MediaMonitor,row.data['monitor_id'],p)
        from .media_monitor import clock
        from datetime import datetime
        if monitor.status!='active' or monitor.data.get('current_session_id')!=row.id or clock(db)>=datetime.fromisoformat(monitor.data['deadline']):
            raise ValueError('自动读取计划已停止、过期或不再持有此读取请求')
    db.commit();return row,channel,gateway


def stream_url(gateway,channel):
    base=origin(gateway.data['rtsp_origin'],'rtsp');path=valid_path(channel.path)
    ref=settings().media_rtsp_secret_ref
    if ref:
        if not ref.startswith('GA_MEDIA_RTSP_'):raise ValueError('视频凭据引用不符合运行节点约定')
        value=os.getenv(ref)
        if not value:raise ValueError('视频读取凭据尚未由运维配置')
        try:
            secret=json.loads(value);user=secret['username'];password=secret['password']
            if not isinstance(user,str) or not isinstance(password,str):raise ValueError()
        except (ValueError,KeyError,TypeError):raise ValueError('视频读取凭据格式无效')
        scheme,rest=base.split('://',1);base=scheme+'://'+quote(user,safe='')+':'+quote(password,safe='')+'@'+rest
    return base+'/'+path


def validate_completion(value,seconds):
    if not isinstance(value,dict) or value.get('schema')!='rtsp-capture-v1' or value.get('cloud_calls')!=0 or value.get('file')!='stream.mp4' or value.get('requested_seconds')!=seconds:
        raise ValueError('视频读取完成回执不一致')
    if value.get('codec')!='h264' or value.get('audio_captured') is not False or value.get('source_clock_utc')!='unknown' or value.get('decoder_version')!='16.1.0' or value.get('probe_buffer_replayed') is not False or not isinstance(value.get('partial'),bool):raise ValueError('视频读取格式与时间契约不一致')
    finite=lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    duration=value.get('duration_seconds');start=value.get('received_start_unix');end=value.get('received_end_unix')
    if not finite(duration) or not 0<=duration<=seconds+5 or not finite(start) or not finite(end) or not 0<start<=end or end-start>seconds+25:raise ValueError('视频读取时间回执无效')
    if not value['partial'] and duration<seconds-.1:raise ValueError('视频读取不足请求时长，不能标记完整')
    if not isinstance(value.get('packets'),int) or isinstance(value['packets'],bool) or not 0<value['packets']<=100000:raise ValueError('视频读取包数无效')
    if not isinstance(value.get('first_source_pts'),int) or isinstance(value['first_source_pts'],bool):raise ValueError('视频来源PTS无效')
    try:
        basis=Fraction(value.get('source_time_base',''))
        if basis<=0:raise ValueError()
    except (ValueError,ZeroDivisionError,TypeError):raise ValueError('视频来源时间基准无效')


def process_capture(db,p,job):
    require(p,'admin','operator');row=scoped(db,StreamSession,job.data['session_id'],p,True)
    if row.status in {'captured','partial','cancelled'}:return
    session_id=row.id
    try:
        row,channel,gateway=capture_guard(db,p,session_id)
        if row.data.get('monitor_id') and row.status in {'connecting','capturing','failed'}:
            # An interrupted live window cannot be replayed. The scheduler may reserve
            # a new, separately counted session; never consume unaccounted reads here.
            row.data={**row.data,'attempt_history':[*row.data.get('attempt_history',[]),
                {'started_at':row.data.get('started_at'),'state':'interrupted_unsaved_window'}]}
            db.commit()
            raise ValueError('自动读取片段中断，未补回历史画面；下一次读取须重新计入计划上限')
        initial=source_state(gateway,channel)
        prior=[*row.data.get('attempt_history',[])]
        if row.status in {'connecting','capturing'}:prior.append({'started_at':row.data.get('started_at'),'progress':row.data.get('progress'),'state':'interrupted_unsaved_window'})
        row.status='connecting';row.version+=1;row.data={**row.data,'attempt_history':prior,'initial_source':initial,'started_at':now().isoformat(),'error':None};db.commit()
        completion=None
        with tempfile.TemporaryDirectory(prefix='ga-media-capture-') as directory:
            temp=Path(directory)
            request={'url':stream_url(gateway,channel),'seconds':row.data['seconds'],'output_dir':str(temp),'parent_pid':os.getpid(),'lock_fd':None}
            def guard():return capture_guard(db,p,session_id)
            def receive(value,_):
                nonlocal completion
                current,_,_=guard()
                if value.get('kind')=='capture.failed':
                    current.data={**current.data,'failure_code':value.get('error_code')};db.commit();raise ValueError(value['reason'])
                if value.get('kind')=='capture.progress':
                    elapsed=value.get('seconds')
                    if not isinstance(elapsed,(int,float)) or not 0<=elapsed<=current.data['seconds']+5:raise ValueError('视频读取进度无效')
                    current.status='capturing';current.version+=1;current.data={**current.data,'progress':value};db.commit()
                elif value.get('kind')=='capture.complete':completion=value
                else:raise ValueError('视频读取回执无效')
            child('media_capture_runner.py',request,temp,guard,receive)
            current,channel,gateway=capture_guard(db,p,session_id)
            validate_completion(completion,current.data['seconds'])
            save_capture(db,p,current,channel,gateway,initial,completion,(temp/'stream.mp4').read_bytes())
    except (HTTPException,ValueError) as error:
        db.rollback();current=scoped(db,StreamSession,session_id,p,True)
        if current.status not in {'cancelled','captured','partial'}:
            current.status='failed';current.version+=1;current.data={**current.data,'error':str(error.detail if isinstance(error,HTTPException) else error)};db.commit()
        raise


def channel_manifest(channel,gateway):
    from .local_video import PIPELINE_HASH
    release=int(digest({'capture':CAPTURE_HASH,'pipeline':PIPELINE_HASH})[:12],16)
    return Manifest(kind='source',namespace='media',name='channel_'+channel.id.replace('-',''),version=f'1.{channel.data["generation"]}.{release}',
        owner='已确认媒体通道',description=channel.data['name']+'；读取已保存的视频及候选提取结果，不授予控制权限',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object','additionalProperties':False},
        output_schema={'type':'object','required':['sessions','not_authoritative'],'additionalProperties':False,
                       'properties':{'sessions':{'type':'array'},'not_authoritative':{'const':True}}},
        execution={'adapter':'media_source','channel_id':channel.id,'channel_generation':channel.data['generation'],
                   'gateway_generation':gateway.data['generation'],'capture_hash':CAPTURE_HASH,'pipeline_hash':PIPELINE_HASH,'side_effect':False},
        data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},
        specification={'timezone':'Asia/Shanghai','physical_device_identity':'unconfirmed','automatic_domain_use':False})


def validated_channel(db,p,channel_id):
    from .local_video import PIPELINE_HASH
    channel=scoped(db,MediaChannel,channel_id,p);gateway=scoped(db,MediaGateway,channel.gateway_id,p)
    if channel.status!='validated' or not channel.data.get('read_authorized') or gateway.status!='ready':raise HTTPException(409,'通道读取和本地处理尚未通过验证，或已停用')
    proof=channel.data.get('validation',{})
    if proof.get('channel_generation')!=channel.data['generation'] or proof.get('gateway_generation')!=gateway.data['generation'] or proof.get('pipeline_hash')!=PIPELINE_HASH or proof.get('capture_hash')!=CAPTURE_HASH:
        raise HTTPException(409,'通道或处理版本变化，请重新读取视频验证')
    return channel,gateway


def registered_read(db,p,execution):
    channel,gateway=validated_channel(db,p,execution['channel_id'])
    expected=channel_manifest(channel,gateway).execution
    if execution!=expected:raise HTTPException(409,'媒体来源能力版本失效，请重新登记')
    sessions=db.scalars(select(StreamSession).where(StreamSession.workspace_id==p.workspace_id,StreamSession.channel_id==channel.id,
        StreamSession.status.in_(['captured','partial'])).order_by(StreamSession.created_at.desc()).limit(10)).all()
    rows=[]
    for session in sessions:
        ev,_=evidence_bytes(db,p,session.evidence_id);run=scoped(db,VideoRun,session.video_run_id,p)
        rows.append({'session_id':session.id,'original_evidence_id':ev.id,'sha256':ev.sha256,'partial':session.status=='partial',
            'video_run_id':run.id,'processing_status':run.status,'result':run.data.get('result'),'source_context':run.data.get('source_context'),
            'historical_configuration':session.data['channel_generation']!=channel.data['generation'] or session.data['gateway_generation']!=gateway.data['generation'],
            'not_authoritative':True})
    return {'sessions':rows,'not_authoritative':True}


def save_capture(db,p,current,channel,gateway,initial,completion,raw,extra_context=None):
    ev=save_video_evidence(db,p,raw,'video/mp4')
    try:
        final=source_state(gateway,channel);changed=final['source_id']!=initial['source_id']
    except HTTPException:changed=True
    if changed:completion={**completion,'partial':True,'error':'读取期间来源状态或会话身份变化；完整性待核查'}
    current.evidence_id=ev.id;current.status='partial' if completion['partial'] else 'captured';current.version+=1
    current.data={**current.data,'receipt':completion,'error':completion.get('error'),'saved_at':now().isoformat(),'input_sha256':ev.sha256}
    db.flush()
    from .video_api import queue
    # Preserve the actual model snapshot chosen before reading. The model worker
    # verifies it again; missing/changed weights must not lose the captured original.
    from types import SimpleNamespace
    installation=SimpleNamespace(id=current.data['installation_id'],data={
        'generation':current.data['installation_generation'],'backend':current.data['backend']})
    context={'channel_id':channel.id,'session_id':current.id,'gateway_generation':gateway.data['generation'],
        'channel_generation':channel.data['generation'],'evidence_id':ev.id,'capture_hash':CAPTURE_HASH,
        'partial':completion['partial'],'source_clock_utc':'unknown','received_start_unix':completion['received_start_unix'],
        'received_end_unix':completion['received_end_unix'],'source_path':channel.path,'captured_duration_seconds':completion['duration_seconds']}
    if current.data.get('monitor_id'):
        context={**context,'monitor_id':current.data['monitor_id'],'monitor_sequence':current.data['monitor_sequence']}
    if current.data.get('domain_binding'):
        context={**context,'domain_binding':current.data['domain_binding']}
    context={**context,**(extra_context or {})}
    if context.get('continuous_id'):
        from .continuous_media import mark_saved
        mark_saved(db,p,current,completion,context)
    queue(db,p,installation,ev,channel.data['name']+' · 视频读取.mp4',current.data['configuration'],source_context=context)
    audit(db,p,'media.capture.saved',current.id,{'partial':completion['partial'],'cloud_calls':0});db.commit()
