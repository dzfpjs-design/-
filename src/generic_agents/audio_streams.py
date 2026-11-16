"""Authorized audio stream controller primitives.

This family captures decoded-source audio into immutable Evidence and ordinary
Observation processing. It never manufactures a Task, upload receipt, ACK or
source UTC. The parent integration supplies the current binding/budget gates.
"""
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import HTTPException
from pydantic import ValidationError
from .contracts import digest

MAX_RAW = 32768
SCHEMA = 'audio-stream-v255'
OPEN = frozenset({'queued', 'recording', 'reconnecting', 'paused', 'blocked'})
LIMITS = {'max_raw_bytes': MAX_RAW, 'max_segment_bytes': 20 * 1024 * 1024,
    'track_index_min': 0, 'track_index_max': 31, 'segment_seconds_min': 1, 'segment_seconds_max': 60,
    'max_segments_min': 1, 'max_segments_max': 100, 'max_runtime_seconds_min': 1,
    'max_runtime_seconds_max': 3600, 'max_connections_min': 1, 'max_connections_max': 4,
    'channel_choices': ['mono', 'mix', '1', '2', '3', '4', '5', '6', '7', '8'], 'threshold_default': .5}
ERROR = '原音轨来源、采集申请或已保存原件无法核对，原记录保留'


def need(value, message=ERROR, code=409):
    if not value: raise HTTPException(code, message)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result: raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _safe_json(value):
    if isinstance(value, str):
        try: value.encode('utf-8')
        except UnicodeError: return False
        return '\x00' not in value
    if isinstance(value, list): return all(_safe_json(v) for v in value)
    if isinstance(value, dict): return all(_safe_json(k) and _safe_json(v) for k, v in value.items())
    if isinstance(value, float): return math.isfinite(value)
    return True


def parse(raw, operation):
    """Return original raw text, normalized typed body and two distinct hashes."""
    from .audio_stream_api import Start, Command, SourceRegister
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, '音轨申请正文过长或为空', 422)
        original = raw.decode('utf-8')
        value = json.loads(original, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
        need(_safe_json(value), '音轨申请不能包含无效字符或非有限数值', 422)
        body = {'start': Start, 'command': Command, 'source': SourceRegister}[operation].model_validate(value).model_dump(mode='json')
        return original, body, hashlib.sha256(raw).hexdigest(), digest(body)
    except (ValueError, TypeError, UnicodeError, KeyError, ValidationError):
        raise HTTPException(422, '请提交完整且无重复字段的音轨申请') from None


def remaining_limits(start, *, segments_used, connections_used, deadline, current):
    """Resume preserves the original deadline/counts rather than resetting them."""
    need(type(segments_used) is int and 0 <= segments_used <= start['max_segments']
        and type(connections_used) is int and 0 <= connections_used <= start['max_connections'])
    try:
        end = datetime.fromisoformat(deadline) if type(deadline) is str else deadline
        now = datetime.fromisoformat(current) if type(current) is str else current
        need(end.tzinfo is not None and now.tzinfo is not None)
        seconds = math.floor((end.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds())
    except (ValueError, TypeError, AttributeError): need(False)
    return {'segments': start['max_segments'] - segments_used,
        'connections': start['max_connections'] - connections_used,
        'runtime_seconds': max(0, seconds)}


def runner_request(start, *, url, directory, remaining):
    """Server-only URL: caller must already hold the registered source gate."""
    try:
        endpoint = urlsplit(url)
        need(endpoint.scheme == 'rtsp' and endpoint.hostname in {'127.0.0.1', 'localhost'}
            and endpoint.port is not None and 1 <= endpoint.port <= 65535 and not endpoint.fragment,
            '此入口只接收已登记的本机代理音轨；外部网关须先配置本节点代理', 422)
    except (ValueError, TypeError, AttributeError): need(False, '已登记音轨代理地址无效', 422)
    need(remaining['segments'] > 0 and remaining['connections'] > 0 and remaining['runtime_seconds'] >= 1,
        '原片段次数、连接次数或运行期限已用完，不能重置后继续')
    from .audio_stream_capture_runner import parse_request
    return parse_request(json.dumps({'schema': 'audio-stream-capture-v1', 'url': url, 'directory': directory,
        'audio_stream_index': start['track_index'], 'segment_seconds': start['segment_seconds'],
        'max_segments': remaining['segments'], 'max_runtime_seconds': min(start['max_runtime_seconds'], remaining['runtime_seconds']),
        'max_segment_bytes': LIMITS['max_segment_bytes']}).encode())



AUDIO_CODECS = frozenset({'MPEG4AUDIO', 'MPEG4AUDIOLATM', 'AAC', 'G711', 'PCMA', 'PCMU',
    'G711A', 'G711U', 'OPUS', 'MP3', 'MPEG1AUDIO', 'G722', 'LPCM', 'PCM', 'PCML16', 'PCML24'})
VIDEO_CODECS = frozenset({'H264', 'H265', 'VP8', 'VP9', 'AV1', 'MJPEG', 'MPEG4VIDEO', 'MPEG2VIDEO'})


def connection_hash(channel, gateway):
    return digest({'channel': {'id': channel.id, 'path': channel.path, 'gateway_id': channel.gateway_id,
        'generation': channel.data['generation']}, 'gateway': {'api_origin': gateway.data['api_origin'],
        'rtsp_origin': gateway.data['rtsp_origin'], 'generation': gateway.data['generation']}})


def _source_output_schema():
    nullable = {'type': ['string', 'null']}
    properties = {'schema': {'const': 'audio-stream-source-metadata-v255'},
        'capability_id': {'type': 'string'}, 'capability_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'channel_id': {'type': 'string'}, 'channel_version': {'type': 'integer', 'minimum': 1},
        'gateway_id': {'type': 'string'}, 'gateway_version': {'type': 'integer', 'minimum': 1},
        'source_identity_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'ready': {'type': 'boolean'},
        'tracks': {'type': 'array', 'maxItems': 32, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['index', 'codec'], 'properties': {'index': {'type': 'integer', 'minimum': 0, 'maximum': 31}, 'codec': {'type': 'string'}}}},
        'unknown_tracks': {'type': 'array', 'maxItems': 32, 'items': {'type': 'string'}},
        'source_id': nullable, 'source_type': nullable, 'ready_at': nullable, 'checked_at': nullable,
        'recording_started': {'const': False}, 'capture_authorized': {'const': False},
        'source_clock_utc': {'const': 'unknown'}, 'reason': nullable}
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def source_manifest(channel, gateway):
    from .contracts import Manifest
    signature = connection_hash(channel, gateway)
    need(channel.gateway_id == gateway.id and channel.workspace_id == gateway.workspace_id
        and type(channel.data['generation']) is int and channel.data['generation'] >= 1
        and type(gateway.data['generation']) is int and gateway.data['generation'] >= 1)
    return Manifest(kind='source', namespace='platform_gateway_audio',
        name='audio_channel_' + channel.id.replace('-', '_'), version='1.0.' + str(int(signature[:12], 16)),
        owner='已登记网关音轨目录', description='只读已登记通道的音轨目录元数据；不录音、不启动推理、不授音轨采集许可或现场事实资格。',
        allowed_agent_roles=['perception', 'data'],
        input_schema={'type': 'object', 'additionalProperties': False, 'maxProperties': 0}, output_schema=_source_output_schema(),
        execution={'adapter': 'audio_stream_source', 'channel_id': channel.id, 'gateway_id': gateway.id,
            'channel_generation': channel.data['generation'], 'gateway_generation': gateway.data['generation'],
            'source_identity_hash': signature, 'side_effect': False},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        specification={'directory_read_only': True, 'capture_authorized': False, 'raw_audio_cloud_allowed': False,
            'source_clock_utc': 'unknown', 'field_truth_verified': False, 'automatic_domain_use': False})


def validate_source_manifest(db, p, value):
    from .contracts import Manifest
    from .models import MediaChannel, MediaGateway
    from .services import scoped
    try:
        typed = value if isinstance(value, Manifest) else Manifest.model_validate(value)
        channel = scoped(db, MediaChannel, typed.execution['channel_id'], p)
        gateway = scoped(db, MediaGateway, typed.execution['gateway_id'], p)
        need(channel.gateway_id == gateway.id and channel.status in {'confirmed', 'validated'} and gateway.status == 'ready',
            '请先登记并确认当前本机网关通道，不借用视频验证作为音轨许可')
        need(digest(typed.model_dump(mode='json')) == digest(source_manifest(channel, gateway).model_dump(mode='json')),
            '音轨目录能力与当前已登记通道或网关代次不一致')
        return typed
    except HTTPException: raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(422, '音轨目录能力必须使用当前登记通道的固定只读契约') from None


def source_metadata(db, p, cap):
    from .models import MediaChannel, MediaGateway
    from .services import scoped
    from .media import inventory
    need(cap.workspace_id == p.workspace_id and cap.status == 'published' and cap.kind == 'source'
        and cap.content_hash == digest(cap.data) and cap.validated_hash == cap.content_hash)
    value = validate_source_manifest(db, p, cap.data)
    need(cap.namespace == value.namespace and cap.name == value.name and cap.release_version == value.version)
    channel = scoped(db, MediaChannel, value.execution['channel_id'], p)
    gateway = scoped(db, MediaGateway, value.execution['gateway_id'], p)
    directory = inventory(gateway)
    rows = [row for row in directory['channels'] if row['path'] == channel.path]
    need(len(rows) == 1, '当前网关目录无法唯一确认这条通道')
    row = rows[0]; codecs = row['tracks']
    need(type(codecs) is list and len(codecs) <= 32
        and all(type(codec) is str and 1 <= len(codec) <= 80 and not any(ord(c) < 32 for c in codec) for codec in codecs))
    tracks = []; unknown = []
    for codec in codecs:
        key = codec.upper().replace('-', '').replace('_', '').replace(' ', '')
        if key in AUDIO_CODECS: tracks.append({'index': len(tracks), 'codec': codec})
        elif key not in VIDEO_CODECS: unknown.append(codec)
    ready = row['ready'] is True and bool(tracks) and not unknown
    reason = (None if ready else '来源当前未就绪' if row['ready'] is not True
        else '目录存在未知轨道，不能猜测音轨序号' if unknown else '这条通道目录没有明确可选的音轨')
    def optional(key):
        result = row.get(key)
        need(result is None or type(result) is str and len(result) <= 200 and _safe_json(result))
        return result
    return {'schema': 'audio-stream-source-metadata-v255', 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'channel_id': channel.id, 'channel_version': channel.version, 'gateway_id': gateway.id, 'gateway_version': gateway.version,
        'source_identity_hash': value.execution['source_identity_hash'], 'ready': ready, 'tracks': tracks,
        'unknown_tracks': unknown, 'source_id': optional('source_id'), 'source_type': optional('source_type'),
        'ready_at': optional('ready_at'), 'checked_at': directory.get('checked_at'), 'recording_started': False,
        'capture_authorized': False, 'source_clock_utc': 'unknown', 'reason': reason}



SOURCE_REGISTER = 'audio.stream_source_registered_v255'
MODEL_REGISTER = 'audio.stream_model_registered_v255'
REQUEST_SAVED = 'audio.stream_request_saved_v255'
COMMAND_SAVED = 'audio.stream_command_saved_v255'
DEFINITION = 'audio.stream_definition_frozen_v255'
QUEUED = 'audio.stream_record_queued_v255'
CAPTURED = 'audio.stream_capture_saved_v255'
EXTRACT_QUEUED = 'audio.stream_observation_queued_v255'
KIND = 'record_audio_stream_v255'
JOB_SCHEMA = 'audio-stream-record-job-v255'
EXTRACT_SCHEMA = 'audio-stream-extract-job-v255'
CAPTURE_KEY = 'audio_stream_capture_v255'
CAPTURE_HASH = 'audio_stream_capture_hash_v255'
TERMINAL = frozenset({'stopped', 'completed', 'failed'})


def _audit(db, p, action, identity, data, *, stamp=None):
    from .models import Audit, now
    row = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity,
        data=copy.deepcopy(data), created_at=stamp or now())
    db.add(row); db.flush(); return row


def _audits(db, p, action, identity):
    from .models import Audit
    from sqlalchemy import select
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(1001)))


def _nonce(db, p, request_id):
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest({'audio_stream_nonce': p.workspace_id, 'request_id': request_id})[:15], 16)})


def _cap_ref(cap):
    return {'id': cap.id, 'version': cap.version, 'namespace': cap.namespace, 'name': cap.name,
        'release_version': cap.release_version, 'content_hash': cap.content_hash, 'status': 'published'}


def _register(db, p, value):
    from sqlalchemy import select, text
    from . import models as m
    from .services import register, validate_capability, publish
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest({'audio_cap': p.workspace_id, 'namespace': value.namespace, 'name': value.name, 'version': value.version})[:15], 16)})
    row = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.namespace == value.namespace, m.Capability.name == value.name, m.Capability.release_version == value.version))
    if row is None:
        row = register(db, p, value); validate_capability(db, p, row.id, row.version)
        publish(db, p, row.id, row.version, '固定已登记网关音轨目录或本地声音候选能力；不授现场事实或录音权限')
    need(row.status == 'published' and row.content_hash == digest(row.data)
        and row.validated_hash == row.content_hash and digest(row.data) == digest(value.model_dump(mode='json')))
    return row


def _registered_cap(db, p, value):
    from sqlalchemy import select
    from .models import Capability
    rows = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == value.namespace, Capability.name == value.name, Capability.release_version == value.version).limit(2)))
    need(len(rows) <= 1)
    if not rows: return None
    row = rows[0]
    need(row.content_hash == digest(row.data) == digest(value.model_dump(mode='json')) and row.validated_hash == row.content_hash)
    return row if row.status == 'published' else None


def _directory(db, p, channel, gateway):
    from .media import inventory
    raw = inventory(gateway); rows = [v for v in raw['channels'] if v['path'] == channel.path]
    need(len(rows) == 1, '目录无法唯一确认当前已登记通道')
    value = rows[0]; codecs = value['tracks']
    need(type(codecs) is list and len(codecs) <= 32 and all(type(v) is str and 1 <= len(v) <= 80
        and _safe_json(v) and not any(ord(c) < 32 for c in v) for v in codecs))
    tracks = []; unknown = []
    for codec in codecs:
        key = codec.upper().replace('-', '').replace('_', '').replace(' ', '')
        if key in AUDIO_CODECS: tracks.append({'index': len(tracks), 'codec': codec})
        elif key not in VIDEO_CODECS: unknown.append(codec)
    ready = value['ready'] is True and bool(tracks) and not unknown
    return {'ready': ready, 'tracks': tracks, 'unknown_tracks': unknown, 'checked_at': raw.get('checked_at'),
        'reason': None if ready else '来源当前未就绪' if value['ready'] is not True else
            '存在未知轨道，不能猜测音轨序号' if unknown else '此通道没有明确音轨'}


def _channel_choice(db, p, channel, gateway):
    available = channel.status in {'confirmed', 'validated'} and gateway.status == 'ready'
    cap = _registered_cap(db, p, source_manifest(channel, gateway))
    return {'id': channel.id, 'name': channel.data.get('name') or channel.path, 'version': channel.version,
        'generation': channel.data['generation'], 'gateway_id': gateway.id,
        'gateway_name': gateway.data.get('name') or '已登记本机网关', 'gateway_version': gateway.version,
        'gateway_generation': gateway.data['generation'], 'available': available,
        'source': _cap_ref(cap) if cap is not None else None, 'can_register': p.role == 'admin' and available,
        'reason': None if available else '请确认当前通道与本机网关，未启动音轨读取'}


def catalog(db, p):
    from sqlalchemy import select
    from .models import MediaChannel, MediaGateway, SpecialistInstallation
    from .local_sound import PACK, trace, checked_trace
    from .audio_observation_model import manifest
    channels = []
    for channel, gateway in db.execute(select(MediaChannel, MediaGateway).join(MediaGateway, MediaGateway.id == MediaChannel.gateway_id)
            .where(MediaChannel.workspace_id == p.workspace_id, MediaGateway.workspace_id == p.workspace_id)
            .order_by(MediaChannel.id).limit(101)):
        need(len(channels) < 100, '当前通道超过目录100条限制，请收窄配置范围')
        channels.append(_channel_choice(db, p, channel, gateway))
    installations = []
    for row in db.scalars(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id == p.workspace_id,
            SpecialistInstallation.pack_id == PACK).order_by(SpecialistInstallation.created_at.desc()).limit(100)):
        item = {'id': row.id, 'version': row.version, 'pack_id': PACK, 'generation': row.data.get('generation', 1),
            'status': row.status, 'model_trace': None, 'capability': None, 'can_register': False, 'reason': None}
        if row.status == 'ready':
            try:
                current = trace(row); checked_trace(db, p, current, live=True)
                cap = _registered_cap(db, p, manifest(current)); item.update(model_trace=current,
                    capability=_cap_ref(cap) if cap is not None else None, can_register=p.role == 'admin')
            except (HTTPException, KeyError, ValueError, TypeError): item['reason'] = '当前本地声音安装或原检查依据尚不可用于新登记'
        else: item['reason'] = '本地声音安装尚未就绪'
        installations.append(item)
    return {'schema': 'audio-stream-catalog-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'channels': channels, 'installations': installations, 'limits': copy.deepcopy(LIMITS)}


def source_options(db, p, channel_id):
    from .models import MediaChannel, MediaGateway
    from .services import scoped
    channel = scoped(db, MediaChannel, channel_id, p); gateway = scoped(db, MediaGateway, channel.gateway_id, p)
    choice = _channel_choice(db, p, channel, gateway)
    directory = _directory(db, p, channel, gateway) if choice['available'] else {
        'ready': False, 'tracks': [], 'unknown_tracks': [], 'checked_at': None, 'reason': choice['reason']}
    return {'schema': 'audio-source-options-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'channel': choice, 'directory': directory, 'can_register': choice['can_register'], 'reason': choice['reason']}


def _source_original(db, p, channel_id, request_id, raw_hash=None):
    from sqlalchemy import select
    from .models import Audit, Capability
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == SOURCE_REGISTER,
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    need(len(proofs) <= 1)
    if not proofs: return None
    proof = proofs[0]; value = proof.data; receipt = value['receipt']
    _, body, rh, bh = parse(value['original_raw'].encode(), 'source')
    need(proof.actor_id == value['actor_id'] == receipt['actor_id'] and proof.object_id == value['channel_id']
        and body['request_id'] == value['request_id'] and value['raw_sha256'] == rh and value['body_hash'] == bh
        and receipt['raw_sha256'] == rh and receipt['body_hash'] == bh
        and receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'}))
    if value['actor_id'] != p.user_id or value['channel_id'] != channel_id: raise HTTPException(404, '当前账号或通道没有该原登记申请')
    need(raw_hash is None or raw_hash == rh, '原登记正文摘要不一致')
    if receipt['capability'] is not None:
        cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.id == receipt['capability']['id']))
        need(cap is not None and digest(cap.data) == cap.content_hash == receipt['capability']['content_hash'])
    return value


def register_source(db, p, channel_id, raw):
    from .models import MediaChannel, MediaGateway, now
    from .services import scoped, require
    from .relations import lock_current_actor
    original, body, rh, bh = parse(raw, 'source')
    old = _source_original(db, p, channel_id, body['request_id'], rh)
    if old:
        need(old['original_raw'] == original and old['body_hash'] == bh)
        return {'schema': 'audio-source-registration-result-v255', 'receipt': copy.deepcopy(old['receipt'])}
    require(p, 'admin'); lock_current_actor(db, p); _nonce(db, p, body['request_id'])
    old = _source_original(db, p, channel_id, body['request_id'], rh)
    if old: return {'schema': 'audio-source-registration-result-v255', 'receipt': copy.deepcopy(old['receipt'])}
    channel = scoped(db, MediaChannel, channel_id, p, True); gateway = scoped(db, MediaGateway, channel.gateway_id, p, True)
    cap = None; outcome = 'rejected'; reason = None; signature = None
    try:
        need(channel.version == body['channel_version'] and gateway.version == body['gateway_version'], '通道或网关已更新，请保留原申请并核对当前目录')
        value = source_manifest(channel, gateway); validate_source_manifest(db, p, value)
        with db.begin_nested(): cap = _register(db, p, value)
        signature = connection_hash(channel, gateway); outcome = 'registered'
    except HTTPException as error:
        if error.status_code == 403: raise
        reason = str(error.detail)
    receipt = {'schema': 'audio-source-registration-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'request_id': body['request_id'], 'channel_id': channel.id, 'gateway_id': gateway.id,
        'channel_version': body['channel_version'], 'gateway_version': body['gateway_version'], 'outcome': outcome,
        'capability': _cap_ref(cap) if cap is not None else None, 'source_identity_hash': signature,
        'reason': reason, 'raw_sha256': rh, 'body_hash': bh, 'created_at': now().isoformat()}
    receipt['receipt_hash'] = digest(receipt)
    _audit(db, p, SOURCE_REGISTER, channel.id, {'actor_id': p.user_id, 'channel_id': channel.id,
        'request_id': body['request_id'], 'original_raw': original, 'raw_sha256': rh, 'body_hash': bh, 'receipt': receipt})
    return {'schema': 'audio-source-registration-result-v255', 'receipt': receipt}


def recover_source(db, p, channel_id, request_id, raw_sha256):
    value = _source_original(db, p, channel_id, request_id, raw_sha256)
    return {'schema': 'audio-source-registration-recovery-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'channel_id': channel_id, 'request_id': request_id, 'raw_sha256': raw_sha256, 'found': value is not None,
        'receipt': copy.deepcopy(value['receipt']) if value else None, 'original_raw': value['original_raw'] if value else None,
        'body_hash': value['body_hash'] if value else None}


def model_registration(db, p, installation_id, expected_version):
    from sqlalchemy import select
    from .models import Audit, Capability
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == MODEL_REGISTER,
        Audit.data['installation_id'].as_string() == installation_id,
        Audit.data['installation_version'].as_integer() == expected_version, Audit.actor_id == p.user_id).limit(2)))
    need(len(rows) <= 1)
    if not rows: return None
    value = rows[0].data
    need(value['actor_id'] == p.user_id and value['receipt_hash'] == digest({k: v for k, v in value.items() if k != 'receipt_hash'}))
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.id == value['capability']['id']))
    need(cap is not None and cap.content_hash == digest(cap.data) == value['capability']['content_hash'])
    return copy.deepcopy(value)


def register_model(db, p, body):
    from .models import SpecialistInstallation, now
    from .services import require, scoped
    from .relations import lock_current_actor
    from .local_sound import trace, checked_trace, PACK
    from .audio_observation_model import manifest
    prior = model_registration(db, p, body['installation_id'], body['version'])
    if prior: return prior
    require(p, 'admin'); lock_current_actor(db, p)
    _nonce(db, p, 'model:' + body['installation_id'] + ':' + str(body['version']))
    prior = model_registration(db, p, body['installation_id'], body['version'])
    if prior: return prior
    installation = scoped(db, SpecialistInstallation, body['installation_id'], p, True)
    need(installation.version == body['version'] and installation.status == 'ready' and installation.pack_id == PACK,
        '请核对当前已就绪的独立声音安装版本')
    selected = trace(installation); checked_trace(db, p, selected, live=True); cap = _register(db, p, manifest(selected))
    receipt = {'schema': 'audio-model-registration-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'installation_id': installation.id, 'installation_version': installation.version,
        'capability': _cap_ref(cap), 'model_trace': selected, 'created_at': now().isoformat()}
    receipt['receipt_hash'] = digest(receipt); _audit(db, p, MODEL_REGISTER, cap.id, receipt)
    return receipt



def _row(db, p, cls, identity, *, lock=False):
    from sqlalchemy import select
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock: query = query.with_for_update()
    value = db.scalar(query.execution_options(populate_existing=True))
    need(value is not None)
    return value


def _checked_base(db, p, stream):
    from .models import Audit
    from sqlalchemy import select
    proof = _audits(db, p, REQUEST_SAVED, stream.id)
    need(len(proof) == 1)
    original = proof[0].data
    _, body, rh, bh = parse(original['original_raw'].encode(), 'start')
    need(proof[0].actor_id == stream.actor_id == original['actor_id']
        and original['workspace_id'] == stream.workspace_id == p.workspace_id
        and body['instance_id'] == stream.instance_id == original['instance_id']
        and body['entity_id'] == stream.entity_id and original['channel_id'] == stream.channel_id
        and original['raw_sha256'] == rh and original['body_hash'] == bh
        and stream.data['request_id'] == body['request_id'] and stream.data['request_hash'] == bh
        and original['request_id'] == body['request_id'] and original['deadline'] == stream.data['deadline'])
    receipt = original['receipt']
    need(receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
        and receipt['operation'] == 'start' and receipt['action'] == 'start' and receipt['outcome'] == 'saved'
        and receipt['workspace_id'] == stream.workspace_id and receipt['actor_id'] == stream.actor_id
        and receipt['instance_id'] == stream.instance_id and receipt['channel_id'] == stream.channel_id
        and receipt['stream_id'] == stream.id and receipt['request_id'] == body['request_id']
        and receipt['raw_sha256'] == rh and receipt['body_hash'] == bh and receipt['version_before'] is None
        and receipt['version_after'] == 1 and receipt['status_at_commit'] == 'blocked' and receipt['job_id'] is None)
    need(stream.data['definition'] is None and stream.data['definition_hash'] is None
        or type(stream.data['definition']) is dict and digest(stream.data['definition']) == stream.data['definition_hash'])
    definitions = _audits(db, p, DEFINITION, stream.id)
    if stream.data['definition'] is None: need(not definitions and not stream.data['read_reservations'])
    else:
        definition = stream.data['definition']
        need(len(definitions) == 1 and definitions[0].actor_id == stream.actor_id
            and digest(definitions[0].data) == digest({'definition': definition, 'definition_hash': stream.data['definition_hash']})
            and definition['workspace_id'] == stream.workspace_id and definition['actor_id'] == stream.actor_id
            and definition['channel_id'] == stream.channel_id and digest(definition['start']) == digest(body))
    return body, original


def _checked(db, p, stream):
    """Check immutable request/definition plus command and queue inventories."""
    from sqlalchemy import select
    from .models import Audit, Job
    try:
        body, original = _checked_base(db, p, stream)
        commands = _audits(db, p, COMMAND_SAVED, stream.id)
        need(len(commands) <= 1000)
        accepted = 0; nonces = {body['request_id']}
        for proof in commands:
            value = proof.data
            _, command_body, rh, bh = parse(value['original_raw'].encode(), 'command')
            receipt = value['receipt']
            need(proof.actor_id == value['actor_id'] == receipt['actor_id']
                and value['workspace_id'] == stream.workspace_id and value['stream_id'] == stream.id
                and value['instance_id'] == stream.instance_id and value['channel_id'] == stream.channel_id
                and value['raw_sha256'] == rh == receipt['raw_sha256'] and value['body_hash'] == bh == receipt['body_hash']
                and value['request_id'] == command_body['request_id'] == receipt['request_id']
                and receipt['operation'] == 'command' and receipt['action'] == command_body['action']
                and receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
                and command_body['request_id'] not in nonces)
            nonces.add(command_body['request_id'])
            if receipt['outcome'] == 'accepted':
                need(receipt['version_before'] == command_body['version']
                    and receipt['version_after'] == receipt['version_before'] + 1)
                accepted += 1
            else:
                need(receipt['outcome'] == 'rejected' and receipt['version_after'] == receipt['version_before'])
        need(type(stream.data['generation']) is int and stream.data['generation'] == 1 + accepted)
        queued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == QUEUED, Audit.data['stream_id'].as_string() == stream.id).limit(1001)))
        jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.kind == KIND, Job.data['stream_id'].as_string() == stream.id).limit(1001)))
        need(len(queued) == len(jobs) == stream.data['job_sequence'] <= 1000
            and {v.object_id for v in queued} == {v.id for v in jobs}
            and sorted(v.data['sequence'] for v in jobs) == list(range(1, len(jobs) + 1)))
        for job in jobs:
            _record_job(db, p, job, stream)
            need(type(job.data['generation']) is int and 1 <= job.data['generation'] <= stream.data['generation'])
        need((stream.data['job_id'] is None and not jobs) or
            stream.data['job_id'] in {v.id for v in jobs} and stream.data['queue_audit_id'] in {v.id for v in queued})
        return body, original
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise HTTPException(409, '原音轨申请、读取或保存依据无法核对，原记录未改变') from None


def _request_original(db, p, instance_id, request_id, raw_hash=None, stream_id=None):
    from .models import Audit
    from .audio_stream_models import AudioStream
    from sqlalchemy import select
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([REQUEST_SAVED, COMMAND_SAVED]), Audit.data['request_id'].as_string() == request_id).limit(2)))
    streams = list(db.scalars(select(AudioStream).where(AudioStream.workspace_id == p.workspace_id,
        AudioStream.data['request_id'].as_string() == request_id).limit(2)))
    if not proofs and not streams: return None
    need(len(proofs) == 1)
    proof = proofs[0]; value = proof.data
    row = _row(db, p, AudioStream, value['stream_id']); _checked(db, p, row)
    operation = 'start' if proof.action == REQUEST_SAVED else 'command'
    _, body, rh, bh = parse(value['original_raw'].encode(), operation)
    need(proof.actor_id == value['actor_id'] and value['raw_sha256'] == rh and value['body_hash'] == bh
        and value['request_id'] == body['request_id'] and value['workspace_id'] == p.workspace_id
        and value['instance_id'] == row.instance_id and value['channel_id'] == row.channel_id
        and value['receipt']['receipt_hash'] == digest({k: v for k, v in value['receipt'].items() if k != 'receipt_hash'}))
    if value['actor_id'] != p.user_id or row.instance_id != instance_id or stream_id is not None and row.id != stream_id:
        raise HTTPException(404, '当前账号或实例没有该原音轨申请')
    need(raw_hash is None or rh == raw_hash, '原音轨申请摘要不一致')
    return row, value


def _request_receipt(db, p, stream, operation, action, raw, body, before, outcome, reason=None, job_id=None):
    from .models import now
    original, _, rh, bh = parse(raw, operation)
    receipt = {'schema': 'audio-stream-request-receipt-v255', 'operation': operation, 'action': action,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'instance_id': stream.instance_id,
        'channel_id': stream.channel_id, 'stream_id': stream.id, 'request_id': body['request_id'],
        'outcome': outcome, 'version_before': before, 'version_after': stream.version, 'status_at_commit': stream.status,
        'job_id': job_id, 'reason': reason, 'raw_sha256': rh, 'body_hash': bh, 'created_at': now().isoformat()}
    receipt['receipt_hash'] = digest(receipt)
    return {'stream_id': stream.id, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': stream.instance_id, 'channel_id': stream.channel_id, 'request_id': body['request_id'],
        'original_raw': original, 'raw_sha256': rh, 'body_hash': bh, 'receipt': receipt,
        **({'deadline': stream.data['deadline']} if operation == 'start' else {})}


def _scope(db, p, stream, *, capture=False, start_check=False):
    from . import models as m
    from .services import require, published
    from .relations import lock_current_actor
    from .entity_release_compat import require_entity
    from .audio_stream_data_plan import require_binding
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    need(p.user_id == stream.actor_id, '只有原启动人可继续本次音轨采集或分析', 403)
    instance = _row(db, p, m.DomainInstance, stream.instance_id, lock=True)
    need(instance.status == 'partially_active', '业务实例未启用或已暂停')
    body, _ = _checked(db, p, stream)
    need(instance.active_release_id == body['release_id'], '业务版本已变化，原申请不自动改签')
    release = _row(db, p, m.InstanceRelease, body['release_id'])
    entity = _row(db, p, m.Entity, stream.entity_id)
    proof = require_entity(db, p, instance, release, entity)
    channel = _row(db, p, m.MediaChannel, stream.channel_id)
    gateway = _row(db, p, m.MediaGateway, channel.gateway_id)
    cap = published(db, p, body['model_capability_id'], 'specialist_model')
    business = next((b for b in release.data['snapshot']['businesses'] if b['id'] == body['business_id']), None)
    need(business is not None)
    binding = require_binding(db, p, instance, release, business, channel, cap)
    if start_check: need(channel.version == body['channel_version'], '通道已更新，请保留原申请并核对当前目录')
    if stream.data['definition'] is not None:
        need(digest(binding) == digest(stream.data['definition']['binding'])
            and digest(proof) == stream.data['definition']['entity_definition_hash'], '原音轨业务、对象或模型范围已变化')
    metadata = None
    if capture:
        source = published(db, p, binding['source_capability_id'], 'source')
        metadata = source_metadata(db, p, source)
        need(metadata['ready'] and any(v['index'] == body['track_index'] for v in metadata['tracks']), '当前来源没有已确认的指定音轨')
    return instance, release, entity, channel, gateway, binding, proof, metadata


def _enqueue(db, p, stream):
    from . import models as m
    sequence = stream.data.get('job_sequence', 0) + 1
    job = m.Job(workspace_id=p.workspace_id, kind=KIND,
        idempotency_key='audio-stream-record:' + stream.id + ':' + str(stream.data['generation']) + ':' + str(sequence),
        data={'schema': JOB_SCHEMA, 'actor_id': stream.actor_id, 'stream_id': stream.id, 'generation': stream.data['generation'],
            'definition_hash': stream.data['definition_hash'], 'sequence': sequence})
    db.add(job); db.flush()
    proof = {'schema': 'audio-stream-record-queued-v255', 'job_id': job.id, 'workspace_id': p.workspace_id,
        'actor_id': stream.actor_id, 'stream_id': stream.id, 'instance_id': stream.instance_id, 'channel_id': stream.channel_id,
        'generation': stream.data['generation'], 'definition_hash': stream.data['definition_hash'], 'sequence': sequence}
    queued = _audit(db, p, QUEUED, job.id, proof)
    stream.data = {**stream.data, 'job_id': job.id, 'queue_audit_id': queued.id, 'job_sequence': sequence}
    return job


def _prepare(db, p, stream, *, resume=False):
    from .models import now
    from datetime import timedelta
    _, _, _, channel, gateway, binding, entity_proof, metadata = _scope(db, p, stream, capture=True,
        start_check=stream.data['definition'] is None)
    body, _ = _checked(db, p, stream)
    maximum = binding['read_bounds']['max_read_seconds']
    need(maximum is None or body['max_runtime_seconds'] <= maximum
        and body['max_segments'] * body['segment_seconds'] <= maximum, '申请时长或片段内容上限超过已发布读取范围')
    remaining = remaining_limits(body, segments_used=len(stream.data['capture_inventory']),
        connections_used=stream.data['connections_used'], deadline=stream.data['deadline'], current=now())
    need(all(v > 0 for v in remaining.values()), '原次数或期限已到上限，不能重置后恢复')
    if stream.data['definition'] is None:
        definition = {'schema': 'audio-stream-definition-v255', 'workspace_id': p.workspace_id, 'actor_id': stream.actor_id,
            'channel_id': channel.id, 'gateway_id': gateway.id, 'channel_generation': channel.data['generation'],
            'gateway_generation': gateway.data['generation'], 'start': body, 'binding': copy.deepcopy(binding),
            'entity_definition_hash': digest(entity_proof)}
        stream.data = {**stream.data, 'definition': definition, 'definition_hash': digest(definition)}
        _audit(db, p, DEFINITION, stream.id, {'definition': definition, 'definition_hash': digest(definition)})
    if resume: stream.data = {**stream.data, 'generation': stream.data['generation'] + 1}
    stream.status = 'queued'; stream.version += 1
    stream.data = {**stream.data, 'display': {**stream.data['display'], 'business_name': binding['business_name']}, 'reason': '音轨申请已核对，等待本地读取；尚未保存新的声音片段', 'next_attempt_at': None}
    return _enqueue(db, p, stream)


def start_stream(db, p, instance_id, channel_id, raw):
    from . import models as m
    from .audio_stream_models import AudioStream
    from .services import scoped, require
    from .relations import lock_current_actor
    from datetime import timedelta
    original, body, rh, bh = parse(raw, 'start')
    old = _request_original(db, p, instance_id, body['request_id'], rh)
    if old:
        need(old[1]['original_raw'] == original and old[0].channel_id == channel_id)
        return {'schema': 'audio-stream-request-result-v255', 'receipt': old[1]['receipt'], 'stream': stream_view(db, p, old[0])}
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    instance = scoped(db, m.DomainInstance, instance_id, p, True)
    need(body['instance_id'] == instance_id)
    entity = scoped(db, m.Entity, body['entity_id'], p)
    need(entity.namespace == instance.namespace)
    channel = scoped(db, m.MediaChannel, channel_id, p)
    scoped(db, m.MediaGateway, channel.gateway_id, p)
    _nonce(db, p, body['request_id'])
    old = _request_original(db, p, instance_id, body['request_id'], rh)
    if old: return {'schema': 'audio-stream-request-result-v255', 'receipt': old[1]['receipt'], 'stream': stream_view(db, p, old[0])}
    stamp = m.now(); stream = AudioStream(workspace_id=p.workspace_id, instance_id=instance_id, channel_id=channel_id,
        entity_id=entity.id, actor_id=p.user_id, status='blocked', version=1, data={'request_id': body['request_id'],
            'request_hash': bh, 'definition': None, 'definition_hash': None, 'generation': 1,
            'deadline': (stamp + timedelta(seconds=body['max_runtime_seconds'])).isoformat(), 'capture_inventory': [],
            'connections_used': 0, 'connections': [], 'read_reservations': [], 'job_id': None,
            'job_sequence': 0, 'reason': '原申请已保存，尚未核对采集条件；没有开始录音', 'next_attempt_at': None,
            'display': {'channel_name': channel.data.get('name') or channel.path, 'entity_label': next((str(v) for v in entity.data.values() if isinstance(v, str)), '业务对象'),
                'business_name': '所选业务'}})
    db.add(stream); db.flush()
    record = _request_receipt(db, p, stream, 'start', 'start', raw, body, None, 'saved')
    _audit(db, p, REQUEST_SAVED, stream.id, record)
    db.commit()  # Persist the real request before any directory I/O/dispatch.
    try:
        with db.begin_nested():
            stream = _write_stream(db, p, instance_id, stream.id)
            if stream.status == 'blocked' and stream.version == 1: _prepare(db, p, stream)
    except HTTPException as error:
        if error.status_code not in {403, 404, 409, 422}: raise
        stream = _row(db, p, AudioStream, stream.id, lock=True)
        stream.data = {**stream.data, 'reason': str(error.detail)}
    return {'schema': 'audio-stream-request-result-v255', 'receipt': record['receipt'], 'stream': stream_view(db, p, stream)}


def command(db, p, instance_id, stream_id, raw):
    from .audio_stream_models import AudioStream
    from .models import DomainInstance
    from .relations import lock_current_actor
    from .services import require, scoped
    original, body, rh, bh = parse(raw, 'command')
    old = _request_original(db, p, instance_id, body['request_id'], rh, stream_id)
    if old:
        need(old[1]['original_raw'] == original)
        return {'schema': 'audio-stream-request-result-v255', 'receipt': old[1]['receipt'], 'stream': stream_view(db, p, old[0])}
    require(p, 'admin', 'operator'); lock_current_actor(db, p); scoped(db, DomainInstance, instance_id, p, True)
    _nonce(db, p, body['request_id']); stream = _row(db, p, AudioStream, stream_id, lock=True); _checked(db, p, stream)
    need(stream.instance_id == instance_id and (p.user_id == stream.actor_id or p.role == 'admin'), code=403)
    old = _request_original(db, p, instance_id, body['request_id'], rh, stream_id)
    if old: return {'schema': 'audio-stream-request-result-v255', 'receipt': old[1]['receipt'], 'stream': stream_view(db, p, old[0])}
    before = stream.version; outcome = 'rejected'; reason = None; job = None
    try:
        with db.begin_nested():
            need(before == body['version'], '原控制版本已更新，原申请保存但未改变采集')
            if body['action'] == 'resume':
                need(stream.status in {'paused', 'blocked'}, '仅暂停或受阻的原申请可明确恢复')
                need(p.user_id == stream.actor_id, '请由原启动人核对后恢复', 403)
                job = _prepare(db, p, stream, resume=True)
            else:
                need(stream.status not in TERMINAL, '这份读取已经结束，原件和后续分析保留')
                stream.status = 'paused' if body['action'] == 'pause' else 'stopped'; stream.version += 1
                stream.data = {**stream.data, 'generation': stream.data['generation'] + 1,
                    'reason': '已停止本次录制，未保存空洞不补造；合法已存片段仍可独立分析与接收', 'next_attempt_at': None}
            outcome = 'accepted'
    except HTTPException as error:
        reason = str(error.detail); stream = _row(db, p, AudioStream, stream_id, lock=True)
    record = _request_receipt(db, p, stream, 'command', body['action'], raw, body, before, outcome, reason, job.id if job else None)
    _audit(db, p, COMMAND_SAVED, stream.id, record)
    return {'schema': 'audio-stream-request-result-v255', 'receipt': record['receipt'], 'stream': stream_view(db, p, stream)}



def _write_stream(db, p, instance_id, stream_id):
    from .models import DomainInstance
    from .audio_stream_models import AudioStream
    from .services import require, scoped
    from .relations import lock_current_actor
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    scoped(db, DomainInstance, instance_id, p, True)
    row = _row(db, p, AudioStream, stream_id, lock=True)
    need(row.instance_id == instance_id)
    return row



def observation_marker(db, p, row):
    """Bounded family recognition only: no directory/network or guessed fields."""
    from sqlalchemy import select
    from .models import Audit, Capability
    if CAPTURE_KEY in row.data or CAPTURE_HASH in row.data or (type(row.source_record_id) is str and row.source_record_id.startswith('AudioStreamCapture:')): return True
    if db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == CAPTURED, Audit.object_id == row.id).limit(1)) is not None: return True
    return db.scalar(select(Capability.id).where(Capability.workspace_id == p.workspace_id,
        Capability.id == row.source_id, Capability.data['execution']['adapter'].as_string() == 'audio_stream_source').limit(1)) is not None


def _record_job(db, p, job, stream):
    proof = _audits(db, p, QUEUED, job.id)
    expected = {'schema': 'audio-stream-record-queued-v255', 'job_id': job.id, 'workspace_id': p.workspace_id,
        'actor_id': stream.actor_id, 'stream_id': stream.id, 'instance_id': stream.instance_id, 'channel_id': stream.channel_id,
        'generation': job.data['generation'], 'definition_hash': stream.data['definition_hash'], 'sequence': job.data['sequence']}
    need(len(proof) == 1 and proof[0].actor_id == stream.actor_id and digest(proof[0].data) == digest(expected)
        and job.workspace_id == p.workspace_id and job.kind == KIND and job.data['schema'] == JOB_SCHEMA
        and job.data['actor_id'] == stream.actor_id and job.data['stream_id'] == stream.id
        and job.data['definition_hash'] == stream.data['definition_hash']
        and job.idempotency_key == 'audio-stream-record:' + stream.id + ':' + str(job.data['generation']) + ':' + str(job.data['sequence']))
    return proof[0]


def _read_evidence(db, p, evidence):
    import os, stat
    from .config import settings
    need(evidence.workspace_id == p.workspace_id and evidence.media_type == 'audio/wav'
        and evidence.data.get('purpose') == 'audio_stream_original_v255' and evidence.data.get('cloud_model_ids') == []
        and evidence.storage_key == p.workspace_id + '/' + evidence.sha256 + '.bin'
        and type(evidence.size_bytes) is int and 44 < evidence.size_bytes <= LIMITS['max_segment_bytes'])
    directory = settings().storage_dir / p.workspace_id
    try:
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(evidence.sha256 + '.bin', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(descriptor); need(stat.S_ISREG(info.st_mode) and info.st_size == evidence.size_bytes)
                with os.fdopen(descriptor, 'rb', closefd=False) as source: content = source.read(LIMITS['max_segment_bytes'] + 1)
            finally: os.close(descriptor)
        finally: os.close(parent)
    except (OSError, ValueError): raise HTTPException(409, '原音轨文件缺失或不能安全读取，未补造原件') from None
    need(len(content) == evidence.size_bytes and hashlib.sha256(content).hexdigest() == evidence.sha256)
    return content


def _validate_wav(content, segment):
    import io, wave
    try:
        with wave.open(io.BytesIO(content), 'rb') as source:
            need(source.getsampwidth() == 2 and source.getcomptype() == 'NONE'
                and source.getframerate() == segment['sample_rate'] and source.getnchannels() == segment['channels']
                and source.getnframes() == segment['sample_count'])
            pcm = source.readframes(source.getnframes() + 1)
            need(len(pcm) == segment['sample_count'] * segment['channels'] * 2)
    except (EOFError, OSError, ValueError, wave.Error): need(False)
    need(segment['schema'] == 'audio-stream-capture-event-v1' and segment['kind'] == 'segment.saved'
        and segment['capture_representation'] == 'decoded_source_pcm16' and segment['codec'] == 'pcm_s16le'
        and segment['source_encoding_preserved'] is False and segment['source_clock_utc'] == 'unknown'
        and segment['channel_mixing'] is False and segment['sample_rate_conversion'] is False
        and type(segment['sample_count']) is int and segment['sample_count'] > 0
        and segment['duration_seconds'] == segment['sample_count'] / segment['sample_rate']
        and (segment['complete'] is True or segment['complete'] is False and segment['sample_count'] * 4 >= segment['sample_rate'])
        and segment['bytes'] == len(content) and segment['sha256'] == hashlib.sha256(content).hexdigest())


def _verify_capture_release(db, p, stream, release_id, binding):
    """Historical declaration only: no current release/model/directory authority."""
    from .models import InstanceRelease
    from .instance_data_receipts import verify_release
    release = _row(db, p, InstanceRelease, release_id)
    need(release.instance_id == stream.instance_id)
    plan = verify_release(db, p, release)
    need(type(plan) is dict and type(plan.get('audio_sources')) is list
        and sum(type(value) is dict and digest(value) == digest(binding) for value in plan['audio_sources']) == 1)
    return release


def _capture_context_base(db, p, row, *, deep=False):
    from . import models as m
    from .audio_stream_models import AudioStream
    packet = row.data[CAPTURE_KEY]
    need(packet['schema'] == 'audio-stream-capture-origin-v255' and digest(packet) == row.data[CAPTURE_HASH]
        and packet['workspace_id'] == p.workspace_id == row.workspace_id and packet['observation_id'] == row.id)
    stream = _row(db, p, AudioStream, packet['stream_id']); _checked(db, p, stream)
    definition = stream.data['definition']; need(definition is not None)
    binding = definition['binding']; proof = _row(db, p, m.Audit, packet['capture_audit_id'])
    need(proof.action == CAPTURED and proof.object_id == row.id and proof.actor_id == stream.actor_id
        and digest(proof.data) == digest(packet) and packet['definition_hash'] == stream.data['definition_hash']
        and packet['binding_hash'] == binding['binding_hash'] and packet['source_capability_id'] == row.source_id == binding['source_capability_id']
        and packet['source_capability_hash'] == binding['source_capability_hash']
        and packet['model_capability_id'] == row.data['extractor_id'] == binding['model_capability_id']
        and packet['model_capability_hash'] == binding['model_capability_hash']
        and packet['entity_id'] == row.entity_id == stream.entity_id and packet['actor_id'] == stream.actor_id == row.data['submitted_by']
        and packet['instance_id'] == stream.instance_id and packet['release_id'] == definition['start']['release_id']
        and packet['business_id'] == definition['start']['business_id'] and packet['source_clock_utc'] == 'unknown'
        and packet['candidate_only'] is True and packet['field_truth_verified'] is False and row.observed_at is None
        and row.business_day == packet['registration_day'] and row.created_at.isoformat() == packet['registered_at'])
    job = _row(db, p, m.Job, packet['capture_job_id']); queued = _record_job(db, p, job, stream)
    need(queued.id == packet['queue_audit_id'] and job.data['generation'] == packet['capture_generation']
        and packet['read_receipt'] in stream.data['read_reservations'])
    receipt = packet['read_receipt']; budget_proof = [a for a in _audits(db, p, 'audio.stream_read_reserved_v255', stream.id)
        if a.data.get('receipt_hash') == receipt['receipt_hash']]
    need(len(budget_proof) == 1 and budget_proof[0].actor_id == stream.actor_id and digest(budget_proof[0].data) == digest(receipt)
        and receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
        and receipt['connection_id'] == packet['connection_id'] and receipt['generation'] == packet['capture_generation'])
    source = _row(db, p, m.Capability, row.source_id); model = _row(db, p, m.Capability, row.data['extractor_id'])
    need(source.content_hash == digest(source.data) == binding['source_capability_hash']
        and model.content_hash == digest(model.data) == binding['model_capability_hash'])
    from .observation_inputs import original
    original(db, p, row)
    evidence = _row(db, p, m.Evidence, packet['evidence_id'])
    need(evidence.sha256 == packet['input_sha256'] and evidence.size_bytes == packet['size_bytes']
        and evidence.media_type == packet['media_type'] == 'audio/wav'
        and row.data['evidence_ids'] == [evidence.id] and evidence.data.get('purpose') == 'audio_stream_original_v255'
        and evidence.data.get('stream_id') == stream.id and evidence.data.get('uploaded_by') == stream.actor_id)
    _verify_capture_release(db, p, stream, packet['release_id'], binding)
    extract = _row(db, p, m.Job, row.data['audio_stream_extract_job_id'])
    extraction = _audits(db, p, EXTRACT_QUEUED, extract.id)
    expected = {'schema': 'audio-stream-extract-queued-v255', 'job_id': extract.id, 'workspace_id': p.workspace_id,
        'actor_id': stream.actor_id, 'stream_id': stream.id, 'observation_id': row.id, 'evidence_id': evidence.id,
        'capture_hash': digest(packet), 'extractor_id': model.id}
    need(len(extraction) == 1 and extraction[0].actor_id == stream.actor_id and digest(extraction[0].data) == digest(expected)
        and extract.kind == 'process_observation' and extract.idempotency_key == 'extract:' + row.id
        and extract.data.get('schema') == EXTRACT_SCHEMA and extract.data.get('actor_id') == stream.actor_id
        and extract.data.get('observation_id') == row.id)
    if deep: _validate_wav(_read_evidence(db, p, evidence), packet['segment'])
    return {'stream': stream, 'binding': copy.deepcopy(binding), 'capture': copy.deepcopy(packet), 'evidence': evidence}


def _capture_context(db, p, row, *, deep=False):
    try: return _capture_context_base(db, p, row, deep=deep)
    except HTTPException as error:
        if error.status_code in {401, 403, 409}: raise
        raise HTTPException(409, ERROR) from None
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise HTTPException(409, ERROR) from None


def _stream_observations(db, p, stream):
    from .models import Observation, Audit, Evidence
    from sqlalchemy import select, or_
    _checked(db, p, stream); ids = stream.data['capture_inventory']
    need(type(ids) is list and len(ids) <= 100 and len(ids) == len(set(ids)))
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == CAPTURED,
        Audit.data['stream_id'].as_string() == stream.id).limit(101)))
    rows = list(db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
        (Observation.id.in_(ids)) | (Observation.data[CAPTURE_KEY]['stream_id'].as_string() == stream.id)
        | (Observation.id.in_([v.object_id for v in proofs]))).limit(101)))
    need(len(rows) == len(proofs) == len(ids) and {v.id for v in rows} == set(ids)
        and {v.object_id for v in proofs} == set(ids))
    extraction = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == EXTRACT_QUEUED, Audit.data['stream_id'].as_string() == stream.id).limit(101)))
    evidence = list(db.scalars(select(Evidence).where(Evidence.workspace_id == p.workspace_id,
        Evidence.data['purpose'].as_string() == 'audio_stream_original_v255',
        Evidence.data['stream_id'].as_string() == stream.id).limit(101)))
    prefixed = list(db.scalars(select(Observation.id).where(Observation.workspace_id == p.workspace_id,
        Observation.source_record_id.startswith('AudioStreamCapture:' + stream.id + ':')).limit(101)))
    need(len(extraction) == len(evidence) == len(ids) and set(prefixed) == set(ids)
        and {v.data['observation_id'] for v in extraction} == set(ids)
        and {v.data[CAPTURE_KEY]['evidence_id'] for v in rows} == {v.id for v in evidence}
        and {v.data['audio_stream_extract_job_id'] for v in rows} == {v.object_id for v in extraction})
    by_id = {v.id: v for v in rows}
    for identity in ids: _capture_context(db, p, by_id[identity], deep=False)
    return [by_id[identity] for identity in ids]


def stream_observations(db, p, stream):
    """Complete <=100 capture catalog and dual queue/audit inventory, no PCM scan."""
    try: return _stream_observations(db, p, stream)
    except HTTPException as error:
        if error.status_code in {401, 403, 409}: raise
        raise HTTPException(409, ERROR) from None
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise HTTPException(409, ERROR) from None


def observation_context(db, p, row, *, execution=False, verify_bytes=True):
    context = _capture_context(db, p, row, deep=verify_bytes)
    stream_observations(db, p, context['stream'])
    if execution:
        _scope(db, p, context['stream'], capture=False)
    return context


def observation_binding(db, p, row):
    return observation_context(db, p, row, execution=False)['binding']


def check_observation(db, p, row, extractor_id, *, execution=True):
    context = observation_context(db, p, row, execution=execution)
    need(extractor_id == context['binding']['model_capability_id'])
    return context['evidence']


def check_capture_input(db, p, evidence_id, cap_id, cap_hash, key=None, execution=True):
    from . import models as m
    from sqlalchemy import select
    rows = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.data[CAPTURE_KEY]['evidence_id'].as_string() == evidence_id).limit(2)))
    need(len(rows) == 1)
    row = rows[0]; context = observation_context(db, p, row, execution=execution)
    need(cap_id == context['binding']['model_capability_id'] and cap_hash == context['binding']['model_capability_hash'])
    if execution:
        from .leases import ownership, checkpoint
        owner = ownership(db); checkpoint(db)
        job = _row(db, p, m.Job, owner['job_id'])
        need(job.id == row.data['audio_stream_extract_job_id'] and job.kind == 'process_observation'
            and job.status == 'running' and job.data['actor_id'] == p.user_id == context['stream'].actor_id
            and job.data['observation_id'] == row.id)
        if key is not None:
            calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
                m.Invocation.idempotency_key == key).limit(2)))
            need(len(calls) == 1 and calls[0].capability_id == cap_id and calls[0].data['capability_hash'] == cap_hash
                and calls[0].data['job_id'] == job.id and calls[0].data['lease_token'] == owner['lease_token']
                and calls[0].data['input_hash'] == digest({'attributes': {}, 'evidence_ids': [evidence_id]}))
    proof = {'schema': 'audio-stream-model-source-proof-v255', 'capture': context['capture'],
        'capture_hash': digest(context['capture']), 'binding_hash': context['binding']['binding_hash']}
    return {'evidence': context['evidence'], 'content': _read_evidence(db, p, context['evidence']),
        'configuration': copy.deepcopy(context['stream'].data['definition']['start']['configuration']), 'source_proof': proof}



def _runner_hash():
    from pathlib import Path
    return hashlib.sha256(Path(__file__).with_name('audio_stream_capture_runner.py').read_bytes()).hexdigest()


def _guard_record(db, p, job, stream_id, generation, receipt=None):
    from .audio_stream_models import AudioStream
    from . import models as m
    from .leases import checkpoint, ownership
    checkpoint(db); db.expire_all()
    initial = _row(db, p, AudioStream, stream_id)
    stream = _write_stream(db, p, initial.instance_id, stream_id)
    job = _row(db, p, m.Job, job.id); _record_job(db, p, job, stream)
    owner = ownership(db)
    need(owner['job_id'] == job.id and owner['lease_token'] == job.lease_token
        and job.status == 'running' and stream.data['job_id'] == job.id)
    need(stream.data['generation'] == generation and stream.status in {'queued', 'recording', 'reconnecting'},
        '这份录制已暂停、停止或由明确恢复接管')
    instance, release, entity, channel, gateway, binding, proof, metadata = _scope(db, p, stream, capture=True)
    need(datetime.now(timezone.utc) < datetime.fromisoformat(stream.data['deadline']), '本次音轨运行期限已到，原额度不重置')
    if receipt is not None:
        from .audio_stream_budget import guard
        guard(db, p, stream, receipt, channel, gateway)
    return stream, channel, gateway, metadata


def _save_segment(db, p, job, stream, connection, receipt, metadata, segment, content, received_started_at):
    import io, wave
    from zoneinfo import ZoneInfo
    from sqlalchemy import select
    from . import models as m
    from .leases import checkpoint, ownership
    from .evidence import save_immutable
    checkpoint(db)
    need(stream.status == 'recording' and stream.data['generation'] == job.data['generation'])
    _validate_wav(content, segment)
    source_record = 'AudioStreamCapture:' + stream.id + ':' + connection + ':' + str(segment['index'])
    old = db.scalar(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.source_record_id == source_record))
    if old is not None:
        context = _capture_context(db, p, old, deep=True)
        need(digest(context['capture']['segment']) == digest(segment)); return old
    need(len(stream.data['capture_inventory']) < stream.data['definition']['start']['max_segments'])
    evidence = save_immutable(db, p, content, 'audio/wav', {'purpose': 'audio_stream_original_v255', 'modality': 'audio',
        'stream_id': stream.id, 'instance_id': stream.instance_id, 'capture_representation': 'decoded_source_pcm16',
        'source_clock_utc': 'unknown', 'source_encoding_preserved': False})
    need(_read_evidence(db, p, evidence) == content, '原音文件未完整落盘，未登记为已保存片段')
    stamp = m.now(); binding = stream.data['definition']['binding']; body = stream.data['definition']['start']
    observation = m.Observation(workspace_id=p.workspace_id, source_id=binding['source_capability_id'],
        source_record_id=source_record, processing_version=digest({'record': source_record, 'sha': evidence.sha256,
            'definition_hash': stream.data['definition_hash']})[:40], entity_id=stream.entity_id, observed_at=None,
        business_day=str(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date()), status='received', created_at=stamp, data={})
    db.add(observation); db.flush()
    capture_audit_id = m.uid(); queue_proof = _record_job(db, p, job, stream)
    packet = {'schema': 'audio-stream-capture-origin-v255', 'workspace_id': p.workspace_id, 'stream_id': stream.id,
        'instance_id': stream.instance_id, 'release_id': body['release_id'], 'business_id': body['business_id'],
        'entity_id': stream.entity_id, 'actor_id': stream.actor_id, 'channel_id': stream.channel_id,
        'gateway_id': binding['gateway_id'], 'channel_generation': binding['channel_generation'], 'gateway_generation': binding['gateway_generation'],
        'definition_hash': stream.data['definition_hash'], 'binding_hash': binding['binding_hash'],
        'source_capability_id': binding['source_capability_id'], 'source_capability_hash': binding['source_capability_hash'],
        'model_capability_id': binding['model_capability_id'], 'model_capability_hash': binding['model_capability_hash'],
        'capture_job_id': job.id, 'capture_generation': job.data['generation'], 'connection_id': connection,
        'read_receipt': copy.deepcopy(receipt), 'source_metadata_hash': digest({k: v for k, v in metadata.items() if k != 'checked_at'}),
        'observation_id': observation.id, 'evidence_id': evidence.id, 'input_sha256': evidence.sha256,
        'size_bytes': evidence.size_bytes, 'media_type': 'audio/wav', 'runner_sha256': _runner_hash(), 'segment': copy.deepcopy(segment),
        'received_started_at': received_started_at, 'received_completed_at': stamp.isoformat(), 'registered_at': stamp.isoformat(),
        'registration_day': observation.business_day, 'source_clock_utc': 'unknown', 'candidate_only': True,
        'field_truth_verified': False, 'capture_audit_id': capture_audit_id, 'queue_audit_id': queue_proof.id}
    original = {'source_id': observation.source_id, 'source_record_id': observation.source_record_id,
        'processing_version': observation.processing_version, 'entity_id': observation.entity_id,
        'business_day': observation.business_day, 'observed_at': None, 'attributes': {},
        'evidence_ids': [evidence.id], 'extractor_id': binding['model_capability_id']}
    observation.data = {'original_request': original, 'input_hash': digest(original), 'attributes': {}, 'evidence_ids': [evidence.id],
        'extractor_id': binding['model_capability_id'], 'submitted_by': stream.actor_id,
        'candidate_only': True, CAPTURE_KEY: packet, CAPTURE_HASH: digest(packet)}
    db.add(m.Audit(id=capture_audit_id, workspace_id=p.workspace_id, actor_id=stream.actor_id,
        action=CAPTURED, object_id=observation.id, data=copy.deepcopy(packet), created_at=stamp))
    extract = m.Job(workspace_id=p.workspace_id, kind='process_observation', idempotency_key='extract:' + observation.id,
        data={'schema': EXTRACT_SCHEMA, 'observation_id': observation.id, 'actor_id': stream.actor_id,
            'extractor_id': binding['model_capability_id']})
    db.add(extract); db.flush(); observation.data = {**observation.data, 'audio_stream_extract_job_id': extract.id}
    _audit(db, p, EXTRACT_QUEUED, extract.id, {'schema': 'audio-stream-extract-queued-v255', 'job_id': extract.id,
        'workspace_id': p.workspace_id, 'actor_id': stream.actor_id, 'stream_id': stream.id,
        'observation_id': observation.id, 'evidence_id': evidence.id, 'capture_hash': digest(packet),
        'extractor_id': binding['model_capability_id']})
    stream.version += 1; stream.data = {**stream.data, 'capture_inventory': [*stream.data['capture_inventory'], observation.id]}
    from .observation_inputs import original as check_original
    check_original(db, p, observation)
    db.commit(); return observation


def _child_events(request, callback, guard):
    import os, selectors, subprocess, sys, tempfile, time
    from pathlib import Path
    from .local_sound import paths, clean_environment
    need(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(), '本节点未验证音轨网络隔离，不转云或放宽网络')
    selected = paths(); need(selected.python.is_file(), '请先准备独立声音运行环境，录制不要求ASR')
    runner = Path(__file__).with_name('audio_stream_capture_runner.py')
    endpoint = urlsplit(request['url']); port = endpoint.port
    profile = '(version 1)(allow default)(deny network*)(allow network-outbound (remote ip "localhost:' + str(port) + '"))'
    command = ['/usr/bin/sandbox-exec', '-p', profile, str(selected.python), '-I', '-B', str(runner)]
    total = 0; completed = False; started = time.monotonic()
    child = subprocess.Popen(command, cwd=request['directory'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=clean_environment(Path(request['directory'])))
    try:
        child.stdin.write(json.dumps(request, allow_nan=False).encode()); child.stdin.close()
        selector = selectors.DefaultSelector(); selector.register(child.stdout, selectors.EVENT_READ)
        try:
            while True:
                guard()
                need(time.monotonic() - started <= request['max_runtime_seconds'] + 10, '本次录制进程超时，未保存空洞不能补造')
                if not selector.select(.1):
                    if child.poll() is not None: break
                    continue
                raw = child.stdout.readline(65537)
                if not raw:
                    if child.poll() is not None: break
                    continue
                total += len(raw); need(len(raw) <= 65536 and total <= 2 * 1024 * 1024 and raw.endswith(b'\n'), '音轨进程回执过长或不完整')
                try: value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError): need(False, '音轨进程未返回完整安全回执')
                need(type(value) is dict and value.get('schema') == 'audio-stream-capture-event-v1')
                callback(value)
                if value['kind'] in {'stream.completed', 'stream.failed'}: completed = True
            need(completed, '录制进程结束但未返回终态，原件与未观察时段保留待核对')
            child.wait(timeout=5)
        finally: selector.close()
    finally:
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)


def process(db, p, job):
    import os, stat, tempfile
    from pathlib import Path
    from uuid import uuid4
    from .audio_stream_models import AudioStream
    from .models import now
    from .leases import LeaseLost, checkpoint
    from .audio_stream_budget import reserve
    from .video_read_boundary import ReadBudgetWait
    stream = _row(db, p, AudioStream, job.data['stream_id']); _checked(db, p, stream); _record_job(db, p, job, stream)
    if stream.status in TERMINAL | {'paused', 'blocked'} or stream.data['generation'] != job.data['generation']: return 'cancelled'
    connection = str(uuid4()); generation = job.data['generation']; receipt = None
    try:
        stream, channel, gateway, metadata = _guard_record(db, p, job, stream.id, generation)
        body, _ = _checked(db, p, stream)
        remaining = remaining_limits(body, segments_used=len(stream.data['capture_inventory']),
            connections_used=stream.data['connections_used'], deadline=stream.data['deadline'], current=now())
        need(all(value > 0 for value in remaining.values()), '本次录制原次数或期限已到上限')
        seconds = remaining['runtime_seconds']  # User-authorized wall time includes connection/startup overhead.
        receipt = reserve(db, p, stream, channel, gateway, seconds, connection, generation)
        stream.status = 'recording'; stream.version += 1
        stream.data = {**stream.data, 'connections_used': stream.data['connections_used'] + 1,
            'connections': [*stream.data['connections'], {'id': connection, 'generation': generation, 'job_id': job.id,
                'receipt_hash': receipt['receipt_hash'], 'state': 'running', 'metadata_hash': digest({k: v for k, v in metadata.items() if k != 'checked_at'})}],
            'reason': '正在读取指定音轨，已保存片段由独立本地处理器继续分析'}
        db.commit()
        outcome = [None]; received_start = [None]; index = [0]
        with tempfile.TemporaryDirectory(prefix='ga-audio-stream-') as temporary:
            directory = Path(temporary).resolve(); os.chmod(directory, 0o700)
            from .media import stream_url
            request = runner_request(body, url=stream_url(gateway, channel), directory=str(directory), remaining={**remaining, 'runtime_seconds': seconds})
            def guard():
                current, _, _, actual = _guard_record(db, p, job, stream.id, generation, receipt)
                need(digest({k: v for k, v in actual.items() if k != 'checked_at'}) == digest({k: v for k, v in metadata.items() if k != 'checked_at'}),
                    '来源目录或音轨身份已变化，旧连接不能继续采纳')
                db.commit()
            def receive(value):
                guard(); current = _write_stream(db, p, stream.instance_id, stream.id)
                if value['kind'] == 'started':
                    need(received_start[0] is None and type(value['received_start_unix']) in {int, float}
                        and math.isfinite(value['received_start_unix']) and value['source_clock_utc'] == 'unknown')
                    received_start[0] = datetime.fromtimestamp(value['received_start_unix'], timezone.utc).isoformat()
                elif value['kind'] == 'segment.saved':
                    need(received_start[0] is not None and value['index'] == index[0]
                        and value['file'] == 'audio-' + str(index[0]).zfill(6) + '.wav')
                    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        fd = os.open(value['file'], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            info = os.fstat(fd); need(stat.S_ISREG(info.st_mode) and info.st_size == value['bytes'] <= LIMITS['max_segment_bytes'])
                            with os.fdopen(fd, 'rb', closefd=False) as source: content = source.read(LIMITS['max_segment_bytes'] + 1)
                        finally: os.close(fd)
                    finally: os.close(parent)
                    _save_segment(db, p, job, current, connection, receipt, metadata, value, content, received_start[0]); index[0] += 1
                elif value['kind'] == 'gap':
                    current.data = {**current.data, 'gaps': [*current.data.get('gaps', []), copy.deepcopy(value)]}
                    need(len(current.data['gaps']) <= 500); db.commit()
                elif value['kind'] in {'stream.completed', 'stream.failed'}:
                    need(outcome[0] is None and value['segments'] == index[0]); outcome[0] = copy.deepcopy(value)
                else: need(False, '音轨进程回执类型不受支持')
            _child_events(request, receive, guard)
        stream = _write_stream(db, p, stream.instance_id, stream.id)
        need(stream.data['generation'] == generation)
        result = outcome[0]; need(result is not None)
        stream.version += 1
        if result['kind'] == 'stream.completed': stream.status = 'completed'; reason = '本次片段上限已完成，声音分析和独立接收分别继续'
        else:
            allowed = result['error_code'] in {'read_timeout', 'connection_failed', 'stream_ended', 'decode_failed'}
            remaining = remaining_limits(body, segments_used=len(stream.data['capture_inventory']), connections_used=stream.data['connections_used'],
                deadline=stream.data['deadline'], current=now())
            stream.status = 'reconnecting' if allowed and all(v > 0 for v in remaining.values()) else 'failed'
            reason = '来源读取中断，已存片段保留；未保存时段不能补造' if stream.status == 'reconnecting' else '本次录制未完整完成，原片段、失败与空洞记录保留'
        from datetime import timedelta
        stream.data = {**stream.data, 'reason': reason, 'last_outcome': result,
            'connections': [{**c, 'state': 'closed'} if c['id'] == connection else c for c in stream.data['connections']],
            'next_attempt_at': (now() + timedelta(seconds=15)).isoformat() if stream.status == 'reconnecting' else None}
        db.commit(); return 'waiting' if stream.status == 'reconnecting' else 'succeeded' if stream.status == 'completed' else 'failed'
    except ReadBudgetWait as error:
        db.rollback(); stream = _write_stream(db, p, stream.instance_id, stream.id)
        stream.status = 'queued'; stream.data = {**stream.data, 'reason': str(error.detail), 'next_attempt_at': error.retry_at.isoformat()}
        db.commit(); return 'waiting'
    except LeaseLost: raise
    except (HTTPException, ValueError, OSError, KeyError, TypeError) as error:
        db.rollback(); current = _row(db, p, AudioStream, stream.id)
        if current.data['generation'] != generation or current.status in {'paused', 'stopped'}: return 'cancelled'
        current = _write_stream(db, p, current.instance_id, current.id)
        current.status = 'blocked'; current.version += 1
        current.data = {**current.data, 'reason': '原音轨读取或当前授权未通过核对，原申请与已存片段保留；请核对后明确恢复',
            'next_attempt_at': None, 'last_error_kind': type(error).__name__}
        db.commit(); return 'failed'


def poll_once(workspace_id):
    from uuid import UUID
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from .db import engine
    from .audio_stream_models import AudioStream
    from .models import Job, now
    from .worker import actor
    need(type(workspace_id) is str and str(UUID(workspace_id)) == workspace_id, '音轨调度必须明确工作区', 422)
    count = 0
    with Session(engine()) as db:
        ids = list(db.scalars(select(AudioStream.id).where(AudioStream.workspace_id == workspace_id,
            AudioStream.status.in_(['queued', 'reconnecting']), AudioStream.data['definition'].is_not(None))
            .order_by(AudioStream.created_at).limit(20)))
    for identity in ids:
        with Session(engine()) as db:
            initial = db.get(AudioStream, identity)
            if initial is None or initial.workspace_id != workspace_id: continue
            p = actor(db, workspace_id, initial.actor_id); stream = _write_stream(db, p, initial.instance_id, identity)
            _checked(db, p, stream)
            if stream.status not in {'queued', 'reconnecting'} or stream.data['definition'] is None: continue
            due = stream.data.get('next_attempt_at')
            if due is None or datetime.fromisoformat(due) > now(): continue
            prior = db.get(Job, stream.data.get('job_id'))
            if prior is not None and prior.status in {'queued', 'running'}: continue
            try:
                _scope(db, p, stream, capture=True)
                need(datetime.fromisoformat(stream.data['deadline']) > now())
                stream.data = {**stream.data, 'next_attempt_at': None}; _enqueue(db, p, stream); count += 1; db.commit()
            except HTTPException:
                db.rollback()
    return count


# Public projections use saved evidence. Reading them never starts recording,
# inference, collection, notification or a new paid request.
def _read_stream(db, p, instance_id, stream_id):
    from .audio_stream_models import AudioStream
    from .models import DomainInstance
    from .services import scoped
    scoped(db, DomainInstance, instance_id, p)
    stream = scoped(db, AudioStream, stream_id, p)
    need(stream.instance_id == instance_id, '该实例没有这份音轨申请', 404)
    _checked(db, p, stream)
    return stream


def _processing(db, p, row, *, values=False):
    from .models import Job
    job = _row(db, p, Job, row.data['audio_stream_extract_job_id'])
    status = ('processed' if row.status == 'processed' else 'failed' if row.status in {'failed', 'cancelled'}
        or job.status in {'failed', 'cancelled'} else 'processing' if job.status == 'running' else 'pending')
    result = None; result_hash = None
    if row.status == 'processed' and values:
        from .observation_integrity import checked_report
        report = checked_report(db, p, row, allow_candidates=True)
        output = report['extracted']
        need(set(output) == {'attributes', 'anomalies', 'environment_sound'} and output['attributes'] == {} and output['anomalies'] == [])
        result = output['environment_sound']; result_hash = digest(result)
    return {'status': status, 'job_id': job.id, 'attempts': job.attempts,
        'error': '声音分析尚未完成，请保留原记录核对处理结果' if status == 'failed' else None,
        'result': copy.deepcopy(result), 'result_hash': result_hash}


def _reception(db, p, row):
    from sqlalchemy import select, or_
    from .models import Event, Memory
    from .instance_data_receipts import observation_receipt
    try:
        events = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
            Event.idempotency_key == 'data:' + row.id).limit(2)))
        need(len(events) <= 1)
        event = events[0] if events else None
        memories = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['type'].as_string() == 'observation_evidence',
            or_(Memory.data['observation_id'].as_string() == row.id,
                Memory.data['event_id'].as_string() == (event.id if event else '__missing__'))).limit(2)))
        marked = 'data_reception' in row.data or bool(memories) or event is not None and 'data_reception' in event.data
        if event is None or event.status != 'received':
            need(not marked)
            if event is not None and event.status == 'blocked_delivery':
                return {'state': 'blocked_integrity', 'event_id': event.id, 'memory_id': None, 'receipt': None,
                    'integrity_issue': '原数据交接受阻，原模型结果保留；恢复依据后在交付恢复中重试'}
            return {'state': 'waiting_data', 'event_id': event.id if event else None,
                'memory_id': None, 'receipt': None, 'integrity_issue': None}
        value = observation_receipt(db, p, row); receipt = value.get('data_reception')
        need(type(receipt) is dict and receipt.get('schema') == 'instance-data-reception-receipt-v1'
            and receipt.get('candidate_only') is True and event.received_at is not None and len(memories) == 1)
        return {'state': 'received', 'event_id': event.id, 'memory_id': memories[0].id,
            'receipt': copy.deepcopy(receipt), 'integrity_issue': None}
    except HTTPException as error:
        if error.status_code in {401, 403}: raise
        return {'state': 'blocked_integrity', 'event_id': None, 'memory_id': None, 'receipt': None,
            'integrity_issue': '原音或独立接收记录无法核对，未把声音候选作为已接收数据'}


def summary(db, p, stream):
    body, _ = _checked(db, p, stream)
    display = stream.data['display']
    return {'id': stream.id, 'version': stream.version, 'status': stream.status,
        'request_id': body['request_id'], 'actor_id': stream.actor_id, 'channel_id': stream.channel_id,
        'channel_name': display['channel_name'], 'entity_id': stream.entity_id, 'entity_label': display['entity_label'],
        'business_id': body['business_id'], 'business_name': display['business_name'], 'created_at': stream.created_at.isoformat(),
        'deadline': stream.data['deadline'], 'generation': stream.data['generation'], 'reason': stream.data.get('reason'),
        'capture_count': len(stream.data['capture_inventory']), 'connections_used': stream.data['connections_used'],
        'has_definition': stream.data['definition'] is not None}


def _notification_count(db, p, stream_id):
    """Count saved notice records, not delivery acknowledgements.

    The immutable issued Audit stores job_id; stream_id belongs to the original
    producer Job. Do not invent a new field in the signed event-proof layout.
    """
    from sqlalchemy import select, func
    from .models import Audit, Job
    return db.scalar(select(func.count(Audit.id)).select_from(Audit)
        .join(Job, Job.id == Audit.data['job_id'].as_string())
        .where(Audit.workspace_id == p.workspace_id, Job.workspace_id == p.workspace_id,
            Audit.action == 'audio.stream_candidate_issued_v255',
            Job.kind == 'refresh_audio_stream_delivery_v255', Job.data['stream_id'].as_string() == stream_id)) or 0


def stream_view(db, p, stream):
    from sqlalchemy import select, func
    from .models import Audit, DomainInstance, now
    body, _ = _checked(db, p, stream)
    rows = stream_observations(db, p, stream)
    counts = {'saved_segments': len(rows), 'model_pending': 0, 'model_processed': 0, 'model_failed': 0,
        'received': 0, 'pending_reception': 0, 'blocked_reception': 0, 'notifications': 0}
    for row in rows:
        stage = _processing(db, p, row)['status']
        counts['model_processed' if stage == 'processed' else 'model_failed' if stage == 'failed' else 'model_pending'] += 1
        # Only received data is cold checked in depth; pending inventories use
        # immutable catalog and dual audit anchors, not repeated PCM reads.
        reception = _reception(db, p, row)
        counts['received' if reception['state'] == 'received' else 'blocked_reception'
            if reception['state'] == 'blocked_integrity' else 'pending_reception'] += 1
    counts['notifications'] = _notification_count(db, p, stream.id)
    current = db.get(DomainInstance, stream.instance_id)
    owner = p.user_id == stream.actor_id; authorized = p.role in {'admin', 'operator'} and (owner or p.role == 'admin')
    can_control = authorized and stream.status in OPEN
    limits = remaining_limits(body, segments_used=len(rows), connections_used=stream.data['connections_used'],
        deadline=stream.data['deadline'], current=now())
    can_resume = (owner and authorized and stream.status in {'paused', 'blocked'} and all(v > 0 for v in limits.values())
        and current is not None and current.status == 'partially_active' and current.active_release_id == body['release_id'])
    base = '/api/workspace/instances/' + stream.instance_id + '/audio-streams/' + stream.id
    definition = stream.data['definition']; binding = definition['binding'] if definition else None
    return {'schema': 'audio-stream-view-v255', 'workspace_id': p.workspace_id, 'instance_id': stream.instance_id,
        'stream': summary(db, p, stream), 'request': copy.deepcopy(body), 'definition_hash': stream.data['definition_hash'],
        'binding_hash': binding['binding_hash'] if binding else None, 'model_trace': copy.deepcopy(binding['model_trace']) if binding else None,
        'read_bounds': copy.deepcopy(binding['read_bounds']) if binding else None, 'counters': counts,
        'permissions': {'can_pause': can_control and stream.status != 'paused', 'can_stop': can_control,
            'can_resume': bool(can_resume), 'reason': None if can_control else '当前账号或读取状态不可变更；原申请和原件仍可读取'},
        'captures_path': base + '/captures', 'daily_path': base + '/daily'}


def list_streams(db, p, instance_id, *, limit=20, before_id=None):
    from sqlalchemy import select
    from .audio_stream_models import AudioStream
    from .models import DomainInstance
    from .services import scoped
    scoped(db, DomainInstance, instance_id, p)
    query = select(AudioStream).where(AudioStream.workspace_id == p.workspace_id, AudioStream.instance_id == instance_id)
    if before_id is not None: query = query.where(AudioStream.id < before_id)
    rows = list(db.scalars(query.order_by(AudioStream.id.desc()).limit(limit + 1)))
    return {'schema': 'audio-stream-list-v255', 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'records': [summary(db, p, row) for row in rows[:limit]], 'has_more': len(rows) > limit,
        'next_before_id': rows[limit - 1].id if len(rows) > limit else None}


def recovery(db, p, instance_id, request_id, raw_sha256, *, stream_id=None, channel_id=None):
    from .models import DomainInstance
    from .services import scoped
    scoped(db, DomainInstance, instance_id, p)
    need(type(request_id) is str and 12 <= len(request_id) <= 100 and _safe_json(request_id)
        and type(raw_sha256) is str and len(raw_sha256) == 64 and all(v in '0123456789abcdef' for v in raw_sha256), code=422)
    old = _request_original(db, p, instance_id, request_id, raw_sha256, stream_id)
    if old is not None:
        need(channel_id is None or channel_id == old[0].channel_id, '该通道没有这份原申请', 404)
    return {'schema': 'audio-stream-request-recovery-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'channel_id': old[0].channel_id if old else channel_id,
        'stream_id': old[0].id if old else stream_id, 'request_id': request_id, 'raw_sha256': raw_sha256, 'found': old is not None,
        'receipt': copy.deepcopy(old[1]['receipt']) if old else None, 'stream': stream_view(db, p, old[0]) if old else None,
        'original_raw': old[1]['original_raw'] if old else None, 'body_hash': old[1]['body_hash'] if old else None}


def options(db, p, instance_id, *, channel_id=None):
    from sqlalchemy import select
    from .models import DomainInstance, InstanceRelease, MediaChannel, MediaGateway, Capability, Entity
    from .services import scoped
    from .audio_stream_data_plan import verify, require_binding
    from .entity_release_compat import require_entity
    instance = scoped(db, DomainInstance, instance_id, p)
    release = scoped(db, InstanceRelease, instance.active_release_id, p) if instance.active_release_id else None
    active = instance.status == 'partially_active' and release is not None and p.role in {'admin', 'operator'}
    channels = []; objects = []
    if release is not None:
        snapshot = release.data['snapshot']; need(digest(snapshot) == release.configuration_hash)
        plan = verify(snapshot)
        for entity in db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id, Entity.namespace == instance.namespace).order_by(Entity.id).limit(101)):
            need(len(objects) < 100, '对象目录超过100项，请收窄业务范围')
            try:
                proof = require_entity(db, p, instance, release, entity, lock_sources=False)
                label = next((str(v) for v in entity.data.values() if type(v) is str), '业务对象')
                objects.append({'id': entity.id, 'label': label, 'version': entity.version, 'definition_hash': digest(proof)})
            except HTTPException: continue
        by_channel = {}
        for frozen in plan['bindings'] if plan else []:
            if channel_id is not None and frozen['channel_id'] != channel_id: continue
            channel = scoped(db, MediaChannel, frozen['channel_id'], p); gateway = scoped(db, MediaGateway, channel.gateway_id, p)
            if channel.id not in by_channel:
                directory = None
                try: directory = _directory(db, p, channel, gateway)
                except HTTPException: directory = {'ready': False, 'tracks': [], 'unknown_tracks': [], 'checked_at': None, 'reason': '暂时无法核对网关音轨目录'}
                item = {'channel': {'id': channel.id, 'name': channel.data.get('name') or channel.path, 'version': channel.version, 'status': channel.status},
                    'gateway': {'id': gateway.id, 'name': gateway.data.get('name') or '已登记本机网关', 'version': gateway.version, 'status': gateway.status},
                    'bindings': [], 'directory': directory, 'available': False, 'reason': None}
                channels.append(item); by_channel[channel.id] = item
            item = by_channel[channel.id]; reason = None; available = active
            source = scoped(db, Capability, frozen['source_capability_id'], p); cap = scoped(db, Capability, frozen['model_capability_id'], p)
            business = next((v for v in snapshot['businesses'] if v['id'] == frozen['business_id']), None)
            try:
                need(active, '业务实例未启用或当前账号不可启动新读取')
                require_binding(db, p, instance, release, business, channel, cap)
                need(item['directory']['ready'], '网关当前没有明确就绪的音轨')
            except HTTPException as error: available = False; reason = str(error.detail)
            item['bindings'].append({'business_id': frozen['business_id'], 'business_name': frozen['business_name'],
                'resource_id': frozen['resource_id'], 'source_capability': _cap_ref(source), 'model_capability': _cap_ref(cap),
                'model_trace': copy.deepcopy(frozen['model_trace']), 'binding_hash': frozen['binding_hash'],
                'read_bounds': copy.deepcopy(frozen['read_bounds']), 'available': bool(available), 'reason': reason})
            item['available'] = item['available'] or bool(available)
            item['reason'] = None if item['available'] else reason
    can_start = active and bool(objects) and any(v['available'] for v in channels)
    return {'schema': 'audio-stream-options-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance.id, 'instance_version': instance.version, 'release_id': release.id if release else None,
        'configuration_hash': release.configuration_hash if release else None, 'channels': channels, 'objects': objects,
        'limits': copy.deepcopy(LIMITS), 'can_start': bool(can_start), 'reason': None if can_start else
            '当前业务版本尚无可启动的已发布音轨范围，或实例、网关、模型、对象未就绪；旧申请可继续读取'}


def _capture_paths(stream, row):
    base = '/api/workspace/instances/' + stream.instance_id + '/audio-streams/' + stream.id + '/captures/' + row.id
    return base, base + '/content'


def capture_summary(db, p, stream, row):
    context = _capture_context(db, p, row, deep=False); packet = context['capture']; segment = packet['segment']
    detail_path, content_path = _capture_paths(stream, row)
    return {'observation_id': row.id, 'evidence_id': packet['evidence_id'], 'name': segment['file'],
        'sha256': packet['input_sha256'], 'size_bytes': packet['size_bytes'], 'media_type': 'audio/wav',
        'registered_at': packet['registered_at'], 'registration_day': packet['registration_day'],
        'complete': segment['complete'], 'continuity_verified': segment['continuity_verified'],
        'sample_count': segment['sample_count'], 'sample_rate': segment['sample_rate'], 'channels': segment['channels'],
        'processing_status': _processing(db, p, row)['status'], 'content_path': content_path, 'detail_path': detail_path}


def capture_list(db, p, stream, *, limit=20, before_id=None):
    rows = sorted(stream_observations(db, p, stream), key=lambda row: row.id, reverse=True)
    if before_id is not None: rows = [row for row in rows if row.id < before_id]
    return {'schema': 'audio-stream-capture-list-v255', 'workspace_id': p.workspace_id, 'instance_id': stream.instance_id,
        'stream_id': stream.id, 'records': [capture_summary(db, p, stream, row) for row in rows[:limit]],
        'has_more': len(rows) > limit, 'next_before_id': rows[limit - 1].id if len(rows) > limit else None}


def _selected_capture(db, p, stream, observation_id):
    rows = stream_observations(db, p, stream)
    row = next((v for v in rows if v.id == observation_id), None)
    need(row is not None, '此音轨申请没有这份原音', 404)
    return row, observation_context(db, p, row, execution=False)


def capture_view(db, p, stream, observation_id):
    row, context = _selected_capture(db, p, stream, observation_id); packet = context['capture']
    _, content_path = _capture_paths(stream, row)
    return {'schema': 'audio-stream-capture-view-v255', 'workspace_id': p.workspace_id, 'instance_id': stream.instance_id,
        'stream_id': stream.id, 'observation_id': row.id,
        'evidence': {'id': context['evidence'].id, 'sha256': context['evidence'].sha256,
            'size_bytes': context['evidence'].size_bytes, 'media_type': 'audio/wav', 'content_path': content_path},
        'segment': copy.deepcopy(packet['segment']), 'configuration': copy.deepcopy(context['stream'].data['definition']['start']['configuration']),
        'model_trace': copy.deepcopy(context['binding']['model_trace']), 'registered_at': packet['registered_at'],
        'registration_day': packet['registration_day'], 'received_started_at': packet['received_started_at'],
        'received_completed_at': packet['received_completed_at'], 'processing': _processing(db, p, row, values=True),
        'reception': _reception(db, p, row), 'candidate_only': True, 'field_truth_verified': False, 'recording_time_verified': False}
