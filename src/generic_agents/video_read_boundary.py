"""Shared local-video read authority and immutable per-request budget receipts."""
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from sqlalchemy import select
from fastapi import HTTPException
from .contracts import digest
from .models import (VideoReadReservation, StreamSession, ContinuousStream,
                     VideoDomainBinding, MediaChannel, MediaGateway, Entity,
                     InstanceRelease, Membership, User)
from .services import scoped, require, audit, published

CHINA = ZoneInfo('Asia/Shanghai')
POLICY_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

class ReadBudgetWait(HTTPException):
    def __init__(self, message, retry_at):
        super().__init__(409, message)
        self.retry_at = retry_at


def endpoint(gateway, channel):
    """Configured endpoint identity, not an inferred physical-device identity."""
    url = urlsplit(gateway.data['rtsp_origin'])
    host = (url.hostname or '').lower()
    if host == 'localhost': host = '127.0.0.1'
    return {'scheme': url.scheme.lower(), 'host': host,
            'port': url.port or 554, 'path': channel.path}


def physical(db, channel, gateway):
    identity = endpoint(gateway, channel)
    pairs = db.execute(select(MediaChannel, MediaGateway).join(
        MediaGateway, MediaChannel.gateway_id == MediaGateway.id).where(
        MediaChannel.path == channel.path)).all()
    if len(pairs) > 1000:
        raise HTTPException(409, '同路径来源超过当前核对上限，请核对设备身份')
    return digest(identity), [c for c, g in pairs if endpoint(g, c) == identity]


def bound(rule, enabled, field, low, high):
    if enabled in rule and type(rule[enabled]) is not bool:
        raise HTTPException(409, '已发布的读取限制开关无效，请回配置核对')
    if not rule.get(enabled): return None
    value = rule.get(field)
    if type(value) is not int or not low <= value <= high:
        raise HTTPException(409, '已发布的读取次数、间隔或时长无效，请回配置核对')
    return value


def limits(policy, equipment, resource, mode):
    if not isinstance(policy,dict) or not isinstance(policy.get('rules',{}),dict):
        raise HTTPException(409, '已发布业务读取规则无效')
    rule = policy.get('rules', {}).get('observe', {})
    if not isinstance(rule,dict) or not isinstance(rule.get('scope',''),str):
        raise HTTPException(409, '已发布业务读取范围无效')
    if mode == 'automatic' and rule.get('mode') != '自动运行':
        raise HTTPException(409, '已发布业务未允许自动读取，请使用人工确认一次或回配置核对')
    if isinstance(equipment,dict) and (not isinstance(equipment.get('types',{}),dict) or not isinstance(equipment.get('devices',{}),dict)):
        raise HTTPException(409, '已发布设备读取规则无效')
    if not isinstance(equipment, dict) or not isinstance(equipment.get('types', {}).get('camera'), dict):
        # Historical explicit channel consent permits one human read, never auto.
        if mode == 'automatic':
            raise HTTPException(409, '旧实例没有设备读取边界，不能继承自动读取权限')
        return {'daily_limit': bound(rule, 'limitsEnabled', 'limit', 1, 10000),
                'cooldown_minutes': bound(rule, 'limitsEnabled', 'cooldown', 0, 1440) or 0,
                'max_read_seconds': None, 'legacy_manual_channel_consent': True}
    device = equipment.get('devices', {}).get(resource['id'], {}) if resource else {}
    if not isinstance(device,dict) or ('custom' in device and type(device['custom']) is not bool):
        raise HTTPException(409, '已发布单台设备规则无效')
    if device.get('type', 'camera') != 'camera':
        raise HTTPException(409, '视频只读入口不能代替无人机或其他设备的执行适配器')
    rules = [equipment['types']['camera']]
    if device.get('custom'): rules.append(device.get('rule'))
    if any(not isinstance(r, dict) or r.get('read') is not True for r in rules):
        raise HTTPException(409, '已发布设备类型或单台规则不允许读取')
    if rule.get('scope', '').strip() or any(r.get('readScopeEnabled') for r in rules):
        raise HTTPException(409, '附加文字范围尚无可验证的机器边界，请回配置核对确切来源')
    counts = [bound(rule, 'limitsEnabled', 'limit', 1, 10000)] + [
        bound(r, 'limitEnabled', 'limit', 1, 10000) for r in rules]
    intervals = [bound(rule, 'limitsEnabled', 'cooldown', 0, 1440)] + [
        bound(r, 'cooldownEnabled', 'cooldown', 0, 1440) for r in rules] + [
        bound(r, 'refreshOverride', 'interval', 1, 1440) for r in rules]
    durations = [bound(r, 'durationEnabled', 'duration', 1, 1440) for r in rules]
    return {'daily_limit': min(v for v in counts if v is not None) if any(v is not None for v in counts) else None,
            'cooldown_minutes': max([v for v in intervals if v is not None] + [0]),
            'max_read_seconds': min(v * 60 for v in durations if v is not None) if any(v is not None for v in durations) else None,
            'legacy_manual_channel_consent': False}


def authority(db, p, channel, gateway, mode):
    require(p, 'admin', 'operator')
    if mode not in {'manual', 'automatic'}: raise HTTPException(422, '读取方式无效')
    if not channel.data.get('read_authorized') or channel.status == 'disabled' or gateway.status != 'ready':
        raise HTTPException(409, '视频通道或网关没有当前读取授权')
    key, channels = physical(db, channel, gateway)
    bindings = list(db.scalars(select(VideoDomainBinding).where(
        VideoDomainBinding.workspace_id == p.workspace_id,
        VideoDomainBinding.channel_id.in_([c.id for c in channels]))))
    binding = next((b for b in bindings if b.channel_id == channel.id and b.status == 'active'), None)
    managed = any(b.data.get('instance_id') for b in bindings)
    if managed and (not binding or not binding.data.get('instance_id')):
        raise HTTPException(409, '这个来源已关联实例业务，请确认本通道的对象与业务；不能删除关联或复制来源绕过设备边界')
    value = {'source_key': key, 'channel_id': channel.id,
             'channel_generation': channel.data['generation'],
             'gateway_generation': gateway.data['generation'], 'mode': mode,
             'policy_hash': POLICY_HASH, 'camera_time_window_required': False,
             'control_authorized': False, 'raw_media_cloud_allowed': False}
    if not binding or not binding.data.get('instance_id'):
        return {**value, 'scope': 'standalone_source_validation', 'daily_limit': None,
                'cooldown_minutes': 0, 'max_read_seconds': None}
    entity = scoped(db, Entity, binding.entity_id, p)
    allowed = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == binding.actor_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])))
    if not allowed or entity.version != binding.data['entity_version'] or channel.data['generation'] != binding.data['channel_generation']:
        raise HTTPException(409, '对象、来源关联或确认人的权限已变化，请重新核对')
    if channel.data.get('capability_id') != binding.data['source_id']:
        raise HTTPException(409, '来源登记已变化，请先重新确认对象与业务关联')
    package = published(db, p, binding.data['package_id'], 'domain_package')
    if package.content_hash != binding.data['package_hash']:
        raise HTTPException(409, '对象业务包版本已变化，请重新核对')
    from .instance_domain import resolve_target
    target = resolve_target(db, p, entity, binding.data['instance_release_id'], binding.data['business_id'])
    if not target or any(binding.data.get(k) != v for k, v in target.items()):
        raise HTTPException(409, '实例或业务目的地已变化')
    release = scoped(db, InstanceRelease, target['instance_release_id'], p)
    snap = release.data['snapshot']; business = next(b for b in snap['businesses'] if b['id'] == target['business_id'])
    policy = snap['automation']['policies'][business['name']]
    resource = next((r for r in snap['resources'] if r.get('category') == 'video'
                     and r.get('details', {}).get('channel_id') == channel.id
                     and r.get('details', {}).get('capability_id') == binding.data['source_id']), None)
    if mode == 'automatic' and (not resource or business['name'] not in resource.get('businesses', [])
        or resource['id'] not in policy.get('rules', {}).get('observe', {}).get('resources', [])
        or not any(s.get('execution', {}).get('automation_action') == 'observe' and s.get('agent') != '人工确认' for s in business['flow']['steps'])):
        raise HTTPException(409, '此来源没有对应业务的真实自动读取入口与已确认资源范围')
    return {**value, **target, 'scope': 'published_instance', 'business_name': business['name'],
            'binding_id': binding.id, 'binding_generation': binding.data['generation'],
            'entity_id': entity.id, 'entity_version': entity.version,
            **limits(policy, snap.get('equipment'), resource, mode)}


def checked(row):
    try:
        if not isinstance(row.data,dict): raise ValueError('payload')
        receipt=row.data['receipt']
        if digest(receipt) != row.data.get('receipt_hash'):
            raise HTTPException(409, '读取预算回执与保存摘要不一致，请保留记录核对')
        if not isinstance(receipt,dict): raise ValueError('receipt')
        request=receipt['request'];authority=receipt['authority']
        if not isinstance(request,dict) or not isinstance(authority,dict): raise ValueError('fields')
        reserved=datetime.fromisoformat(receipt['reserved_at'])
        seconds=request['seconds']
        if (reserved.tzinfo is None or type(seconds) not in {int,float} or not math.isfinite(seconds) or seconds<1
            or request['mode'] not in {'manual','automatic'} or request['kind'] not in {'clip','continuous'}):
            raise ValueError('request or clock')
        if (request['channel_id']!=row.channel_id or request['actor_id']!=row.actor_id
            or request['workspace_id']!=row.workspace_id or request['request_key']!=row.request_key
            or authority['source_key']!=row.source_key
            or reserved.astimezone(timezone.utc)!=row.created_at.astimezone(timezone.utc)):
            raise HTTPException(409, '读取预算回执的来源、确认人或计次时间不一致，请保留记录核对')
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        raise HTTPException(409, '读取预算回执格式无效，请保留记录并由管理员核对恢复，不能跳过回执继续读取') from None
    return row


def usage(db, channel, gateway, current):
    key, channels = physical(db, channel, gateway)
    start = current.astimezone(CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
    from sqlalchemy import or_
    entries = list(db.scalars(select(VideoReadReservation).where(or_(
        VideoReadReservation.source_key == key,
        VideoReadReservation.data['receipt']['authority']['source_key'].as_string()==key)).limit(10001)))
    legacy = list(db.scalars(select(StreamSession).where(
        StreamSession.channel_id.in_([c.id for c in channels]), StreamSession.created_at >= start,
        StreamSession.data['read_reservation_id'].as_string().is_(None)).limit(10001)))
    if len(entries) > 10000 or len(legacy) > 10000:
        raise HTTPException(409, '来源今日读取超过当前预算核对上限，不能跳过历史继续读取')
    legacy_units = {}
    for s in legacy:
        unit = ('continuous:' + str(s.data['continuous_id']) + ':' + str(s.data.get('connection_id'))
                if s.data.get('continuous_id') else 'clip:' + s.id)
        legacy_units.setdefault(unit, s.created_at)
    streams = list(db.scalars(select(ContinuousStream).where(
        ContinuousStream.channel_id.in_([c.id for c in channels]),
        ContinuousStream.created_at >= start).limit(1001)))
    if len(streams) > 1000:
        raise HTTPException(409, '来源今日持续连接超过预算核对上限')
    for stream in streams:
        # Old failures before segment.open have no StreamSession. Conservatively
        # retain their connection count; new controllers persist each reservation.
        if 'read_reservation_ids' in stream.data: continue
        prefix = 'continuous:' + stream.id + ':'
        known = sum(1 for unit in legacy_units if unit.startswith(prefix))
        for i in range(max(0, stream.data.get('connections', 0) - known)):
            legacy_units['legacy-connection:' + stream.id + ':' + str(i)] = stream.created_at
    validated=[checked(r) for r in entries]
    from .audio_stream_budget import read_times as audio_read_times
    audio_times=audio_read_times(db,channel,gateway,current)
    times = ([r.created_at for r in validated if r.created_at >= start]
             + list(legacy_units.values()) + [stamp for stamp in audio_times if stamp>=start])
    return {'business_day': start.date().isoformat(), 'used': len(times),
            'last_read_at': max(times).isoformat() if times else None,
            'legacy_units': len(legacy_units)}


def available(db, p, channel, gateway, mode, seconds, current, controller_id=None):
    value = authority(db, p, channel, gateway, mode)
    state = usage(db, channel, gateway, current)
    if value['daily_limit'] is not None and state['used'] >= value['daily_limit']:
        start = current.astimezone(CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
        raise ReadBudgetWait('这个来源今日读取次数已用完；手动、定时、持续与补充读取共用次数，失败也计一次', start + timedelta(days=1))
    if state['last_read_at'] and current < datetime.fromisoformat(state['last_read_at']) + timedelta(minutes=value['cooldown_minutes']):
        raise ReadBudgetWait('仍在已发布的最短读取间隔内，请等候；切换读取入口不会重置间隔', datetime.fromisoformat(state['last_read_at']) + timedelta(minutes=value['cooldown_minutes']))
    if value['max_read_seconds'] is not None and seconds > value['max_read_seconds']:
        raise HTTPException(409, '本次读取时长超过已发布设备边界，请缩短读取或回配置核对')
    _,channels=physical(db,channel,gateway);ids=[c.id for c in channels]
    active=db.scalar(select(StreamSession.id).where(StreamSession.channel_id.in_(ids),StreamSession.status.in_(['queued','connecting','capturing'])).limit(1))
    streams=select(ContinuousStream.id).where(ContinuousStream.channel_id.in_(ids),ContinuousStream.status.in_(['queued','recording','reconnecting','paused','blocked']))
    if controller_id:streams=streams.where(ContinuousStream.id!=controller_id)
    from .audio_stream_budget import has_open_source as audio_source_open
    if active or db.scalar(streams.limit(1)) or audio_source_open(db,channel,gateway):
        raise ReadBudgetWait('这个来源已有未结束的读取，请等待完成或停止原读取；不额外计次', current+timedelta(seconds=5))
    return value, state


def reserve(db, p, channel, gateway, mode, seconds, request_key, kind='clip', controller_id=None, controller_generation=None):
    from .media_monitor import node_lock, clock
    node_lock(db)
    if kind=='continuous':
        controller=scoped(db,ContinuousStream,controller_id,p,True);db.refresh(controller)
        if (controller.actor_id!=p.user_id or controller.channel_id!=channel.id
            or controller.status!='recording' or controller.data['generation']!=controller_generation):
            raise HTTPException(409, '持续读取已暂停、停止或由新请求接管，不能启动原连接')
    prior = db.scalar(select(VideoReadReservation).where(
        VideoReadReservation.workspace_id == p.workspace_id, VideoReadReservation.request_key == request_key))
    request = {'channel_id': channel.id, 'actor_id': p.user_id, 'workspace_id':p.workspace_id,
               'request_key':request_key,'mode': mode, 'seconds': seconds, 'kind': kind,
               'controller_id':controller_id,'controller_generation':controller_generation}
    if prior:
        if checked(prior).data['receipt']['request'] != request:
            raise HTTPException(409, '原读取预算请求不能更换来源、方式或时长')
        return prior
    current = clock(db); value, state = available(db, p, channel, gateway, mode, seconds, current, controller_id)
    receipt = {'request': request, 'authority': value, 'reserved_at': current.isoformat(), 'usage_before': state}
    row = VideoReadReservation(workspace_id=p.workspace_id, channel_id=channel.id,
        actor_id=p.user_id, source_key=value['source_key'], request_key=request_key,
        created_at=current, data={'receipt': receipt, 'receipt_hash': digest(receipt)})
    db.add(row); db.flush()
    audit(db, p, 'media.read_budget.reserved', row.id, {'kind': kind, 'mode': mode})
    return row


def guard(db, p, identity):
    from .media_monitor import clock
    row = checked(scoped(db, VideoReadReservation, identity, p))
    if row.actor_id != p.user_id: raise HTTPException(409, '读取回执确认人与执行人不一致')
    receipt = row.data['receipt']; old = receipt['authority']
    channel = scoped(db, MediaChannel, row.channel_id, p); gateway = scoped(db, MediaGateway, channel.gateway_id, p)
    if authority(db, p, channel, gateway, old['mode']) != old:
        raise HTTPException(409, '已发布的业务、设备、对象或读取权限已变化，原读取不能继续')
    if clock(db) >= row.created_at + timedelta(seconds=receipt['request']['seconds'] + 30):
        raise HTTPException(409, '原读取授权时长已到上限，未保存窗口停止；旧原件保留')
    from .frame_supplement_v236 import execution_guard
    execution_guard(db, p, reservation=row)
    return row
