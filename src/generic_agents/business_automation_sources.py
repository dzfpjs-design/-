"""Registered received-media units; no sensor read, model call or implicit ACK.

Discovery checks independent reception metadata. Each original Run is a unit,
not each Daily revision. Only checked_unit/materialize_input claim a physical
check, and they follow the exact saved revision rather than today's head.
"""
import copy
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, exists, func, or_, select
from fastapi import HTTPException

from . import models as m, media_daily_delivery as delivery
from .contracts import digest
from .services import scoped, published
from .temporal import instant as _string_instant
from .media_daily_task_adapter import current_actor

PROVIDER = 'received_camera_frame_v1'
SCHEMA = 'business-automation-camera-unit-v1'
KEY = 'business_automation_input_v1'
LIMIT = 100
UNIT_KEYS = {'unit_id', 'run_id', 'source_pin', 'source_hash', 'received_at',
             'source_label', 'target_hash', 'limitations'}
LIMITATIONS = ['仅使用已完整读取、处理并独立接收的保存画面；不读取设备。',
               '平台接收新近不证明现场拍摄时间；单张画面不证明全天覆盖或对象正常。']


def need(value, message='自动入口的原接收资料、对象或保存画面无法核对', status=409):
    if not value: raise HTTPException(status, message)


def instant(value):
    """Normalize real DB clocks and saved offset timestamps without guessing."""
    try:
        if isinstance(value, datetime):
            if value.utcoffset() is None: raise ValueError('offset required')
            return value.astimezone(timezone.utc)
        if type(value) is not str: raise ValueError('timestamp required')
        return _string_instant(value)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HTTPException(422, '时间必须是带偏移量的时间戳，不能猜定数据库时钟的时区') from None


def _copy(value): return copy.deepcopy(value)


def _scope(selection, instance_id):
    return {'instance_id': instance_id, **{key: selection[key] for key in
        ('release_id', 'business_id', 'entity_id', 'camera_id', 'provider_id')}}


def _shared(db, model, identity, p):
    """Fence actual source edits until commit, refreshing the identity map."""
    row = db.scalar(select(model).where(model.id == identity, model.workspace_id == p.workspace_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    need(row is not None, '记录不存在或不在当前工作区', 404)
    return row


def _camera(db, p, identity, *, write=False):
    from .media import CAPTURE_HASH
    from .local_video import PIPELINE_HASH
    if write:
        # Gateway edits hold Gateway EX then update Channels. Locate without
        # authority, then take the SAME order; Channel->Gateway would deadlock.
        gateway_id = db.scalar(select(m.MediaChannel.gateway_id).where(
            m.MediaChannel.id == identity, m.MediaChannel.workspace_id == p.workspace_id))
        need(gateway_id is not None, '记录不存在或不在当前工作区', 404)
        gateway = _shared(db, m.MediaGateway, gateway_id, p)
        camera = _shared(db, m.MediaChannel, identity, p)
        need(camera.gateway_id == gateway.id, '摄像头所属网关已变化，请重新核对来源')
    else:
        camera = scoped(db, m.MediaChannel, identity, p)
        gateway = scoped(db, m.MediaGateway, camera.gateway_id, p)
    need(camera.status == 'validated' and camera.data.get('read_authorized') is True
         and gateway.status != 'disabled', '所选摄像头尚未验证或已停用；原保存资料保留')
    cg, gg = camera.data.get('generation'), gateway.data.get('generation')
    need(type(cg) is int and cg > 0 and type(gg) is int and gg > 0)
    validation = camera.data.get('validation', {})
    need(validation.get('channel_generation') == cg and validation.get('gateway_generation') == gg
         and validation.get('capture_hash') == CAPTURE_HASH and validation.get('pipeline_hash') == PIPELINE_HASH,
         '通道或本地处理配置已变化，请重新核对来源')
    if write: _shared(db, m.Capability, camera.data.get('capability_id'), p)
    cap = published(db, p, camera.data.get('capability_id'), 'source')
    execution = cap.data['execution']
    need(execution.get('adapter') == 'media_source' and execution.get('channel_id') == camera.id
         and execution.get('channel_generation') == cg and execution.get('gateway_generation') == gg
         and execution.get('capture_hash') == CAPTURE_HASH and execution.get('pipeline_hash') == PIPELINE_HASH)
    # Directory probes change gateway.version/status/inventory; they are health
    # observations, not source-configuration generations or new authority.
    camera_config = {key: value for key, value in camera.data.items()
                     if key not in {'error', 'health', 'last_seen_at', 'validation'}}
    # A later successful capture updates the last validation's session/run IDs.
    # Those are evidence of work, not a new camera configuration generation.
    camera_config['validation'] = {key: validation[key] for key in
        ('channel_generation', 'gateway_generation', 'capture_hash', 'pipeline_hash')}
    gateway_config = {key: value for key, value in gateway.data.items()
                      if key not in {'inventory', 'error', 'health', 'last_seen_at'}}
    return camera, {'camera_id': camera.id, 'camera_generation': cg,
        'camera_hash': digest({'gateway_id': camera.gateway_id, 'path': camera.path, 'data': camera_config}),
        'gateway_id': gateway.id, 'gateway_generation': gg, 'gateway_hash': digest(gateway_config),
        'source_capability_id': cap.id, 'source_capability_hash': cap.content_hash,
        'source_capability_release': cap.release_version}


def validate_selection(db, p, instance, release, business, selection, *, write=False):
    from .entity_release_compat import require_entity
    current_actor(db, p, write=write)
    need(type(selection) is dict and selection.get('provider_id') == PROVIDER
         and selection.get('release_id') == release.id and selection.get('business_id') == business['id']
         and instance.workspace_id == release.workspace_id == p.workspace_id
         and release.instance_id == instance.id and instance.active_release_id == release.id
         and instance.status == 'partially_active' and business['id'] in instance.data.get('enabled_business_ids', []))
    entity = (_shared(db, m.Entity, selection['entity_id'], p) if write
              else scoped(db, m.Entity, selection['entity_id'], p))
    definition = require_entity(db, p, instance, release, entity, lock_sources=write)
    camera, camera_pin = _camera(db, p, selection['camera_id'], write=write)
    return {**_scope(selection, instance.id), 'configuration_hash': release.configuration_hash,
        'entity_version': entity.version, 'entity_hash': digest({'data': entity.data, 'definition': definition}),
        **camera_pin}


def catalog(db, p, instance_id, release_id, business_id):
    from .instance_api import active
    from .entity_release_compat import require_entity
    current_actor(db, p)
    instance, release = active(db, p, instance_id)
    need(release.id == release_id and any(item['id'] == business_id for item in release.data['snapshot']['businesses']))
    entities = []; cameras = []; limited = False
    rows = list(db.scalars(select(m.Entity).where(m.Entity.workspace_id == p.workspace_id)
        .order_by(m.Entity.created_at.desc(), m.Entity.id).limit(201)))
    limited |= len(rows) > 200
    for row in rows[:200]:
        try: require_entity(db, p, instance, release, row, lock_sources=False)
        except HTTPException as error:
            if error.status_code not in {404, 409}: raise
            continue
        entities.append({'id': row.id, 'label': row.external_id})
    rows = list(db.scalars(select(m.MediaChannel).where(m.MediaChannel.workspace_id == p.workspace_id)
        .order_by(m.MediaChannel.created_at.desc(), m.MediaChannel.id).limit(201)))
    limited |= len(rows) > 200
    for row in rows[:200]:
        reason = None
        try: _camera(db, p, row.id)
        except HTTPException as error:
            if error.status_code not in {404, 409, 422}: raise
            reason = '来源配置或已发布能力尚不能核对，原保存资料不受影响'
        cameras.append({'id': row.id, 'label': row.data.get('name') or '已登记摄像头',
            'available': reason is None, 'reason': reason, 'limitations': list(LIMITATIONS)})
    return {'provider_id': PROVIDER, 'entities': entities, 'cameras': cameras, 'limited': limited,
        'limitations': [*LIMITATIONS, *(['目录最多列出200个对象和200个通道，不表示全量。'] if limited else [])]}


def _ref(row, fields):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'version': row.version,
        **{field: _copy(getattr(row, field)) for field in fields}, 'data_hash': digest(row.data)}


def _metadata(db, p, event, record, target):
    proof = delivery.checked(db, p, event)
    need(proof['state']['status'] == 'received' and proof['body']['modality'] == 'video'
         and proof['body']['source_key'] == 'channel:'+target['camera_id']
         and sum(item == record for item in proof['body']['summary']['records']) == 1)
    delivery.validate_sources(db, p, proof['body'])
    need(record.get('mode') != 'upload' and record.get('capture_status') == 'captured'
         and record.get('processing_status') == 'succeeded')
    session = scoped(db, m.StreamSession, record['id'], p)
    run = scoped(db, m.VideoRun, record['run_id'], p)
    need(session.status == 'captured' and session.channel_id == target['camera_id']
         and session.video_run_id == run.id and run.status == 'succeeded')
    context = run.data['source_context']
    for name, saved in (('channel_generation', target['camera_generation']),
                        ('gateway_generation', target['gateway_generation'])):
        need(type(context.get(name)) is int and type(session.data.get(name)) is int
             and context[name] == session.data[name] == saved)
    declared = record.get('business_target')
    if declared:
        need(all(declared.get(key) == target[field] for key, field in (
            ('instance_id', 'instance_id'), ('instance_release_id', 'release_id'),
            ('business_id', 'business_id'), ('entity_id', 'entity_id'))),
            '原资料已属于另一业务或对象，不能由自动入口改签')
    frame = db.scalar(select(m.VideoFrame).where(m.VideoFrame.workspace_id == p.workspace_id,
        m.VideoFrame.run_id == run.id).order_by(m.VideoFrame.sample_index, m.VideoFrame.id).limit(1))
    need(frame is not None, '已完成资料没有可核对的保存画面')
    evidence = scoped(db, m.Evidence, frame.evidence_id, p)
    received = proof['state']['received_at']; need(instant(received) == instant(event.received_at))
    pin = {'schema': SCHEMA, 'scope': {key: target[key] for key in
        ('instance_id', 'release_id', 'business_id', 'entity_id', 'camera_id', 'provider_id')},
        'event_id': event.id, 'payload_hash': proof['body']['payload_hash'], 'record_hash': digest(record),
        'received_at': received, 'session': _ref(session, ('channel_id', 'video_run_id', 'evidence_id', 'status')),
        'run': _ref(run, ('evidence_id', 'installation_id', 'status', 'request_key')),
        'frame': _ref(frame, ('run_id', 'evidence_id', 'sample_index')),
        'image': _ref(evidence, ('sha256', 'size_bytes', 'media_type', 'storage_key')),
        'camera_generation': target['camera_generation'], 'gateway_generation': target['gateway_generation'],
        'scene_time_trust': 'unknown'}
    return pin


def _query(db, p, selection, target):
    # JSON containment is a typed SQL expression, never a caller SQL/module.
    # Aggregate over ALL qualified reception revisions, then filter the first
    # timestamp. Re-publication cannot make an old Run new after activation.
    record = func.jsonb_build_array(func.jsonb_build_object('id', m.StreamSession.id,
        'run_id', m.VideoRun.id, 'capture_status', 'captured', 'processing_status', 'succeeded'))
    first = func.min(m.Event.received_at)
    return select(m.VideoRun.id, first.label('first_received')).select_from(m.VideoRun).join(
        m.StreamSession, and_(m.StreamSession.workspace_id == p.workspace_id,
                             m.StreamSession.video_run_id == m.VideoRun.id)).join(m.Event, and_(
        m.Event.workspace_id == p.workspace_id, m.Event.kind == delivery.KIND, m.Event.status == 'received',
        m.Event.data['modality'].as_string() == 'video',
        m.Event.data['source_key'].as_string() == 'channel:'+selection['camera_id'],
        m.Event.data['summary']['records'].op('@>')(record))).where(
        m.VideoRun.workspace_id == p.workspace_id, m.StreamSession.channel_id == selection['camera_id'],
        m.StreamSession.status == 'captured', m.VideoRun.status == 'succeeded',
        m.StreamSession.data['channel_generation'].as_integer() == target['camera_generation'],
        m.StreamSession.data['gateway_generation'].as_integer() == target['gateway_generation']).group_by(m.VideoRun.id)


def _unit(db, p, selection, target, identity, received):
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.kind == delivery.KIND, m.Event.status == 'received',
        m.Event.data['modality'].as_string() == 'video',
        m.Event.data['source_key'].as_string() == 'channel:'+selection['camera_id'],
        m.Event.data['summary']['records'].contains([{'run_id': identity, 'capture_status': 'captured',
                                                  'processing_status': 'succeeded'}]))
        .order_by(m.Event.received_at, m.Event.id).limit(1)))
    need(len(events) == 1 and events[0].received_at == received)
    event = events[0]; proof = delivery.checked(db, p, event)
    records = [record for record in proof['body']['summary']['records'] if record.get('run_id') == identity
               and record.get('capture_status') == 'captured' and record.get('processing_status') == 'succeeded']
    need(len(records) == 1)
    pin = _metadata(db, p, event, records[0], target)
    camera = scoped(db, m.MediaChannel, selection['camera_id'], p)
    return {'unit_id': identity, 'run_id': identity, 'source_pin': pin, 'source_hash': digest(pin),
        'received_at': pin['received_at'], 'source_label': (camera.data.get('name') or '已登记摄像头')+'的一次已接收资料',
        'target_hash': digest(target), 'limitations': list(LIMITATIONS)}


def new_units(db, p, selection, target_pin, *, authorized_at, at, limit=20, after=None,
              exclude_occurrence_for=None):
    need(type(limit) is int and 1 <= limit <= LIMIT)
    start, end = instant(authorized_at), instant(at)
    query = _query(db, p, selection, target_pin)
    first = func.min(m.Event.received_at)
    query = query.having(first > start, first <= end)
    if exclude_occurrence_for is not None:
        need(type(exclude_occurrence_for) is dict and set(exclude_occurrence_for) ==
             {'subscription_id', 'authorization_hash'})
        from .business_automation_contracts import JOB_KIND, uuid, sha
        uuid(exclude_occurrence_for['subscription_id'])
        sha(exclude_occurrence_for['authorization_hash'])
        # This is an anti-join over real occurrence origins, not a timestamp
        # checkpoint. A late-committed receipt can therefore still be found.
        # The caller must independently validate its saved occurrences; this
        # read does not authenticate or repair any excluded Job.
        registered = exists(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
            m.Job.kind == JOB_KIND,
            m.Job.data['subscription_id'].as_string() == exclude_occurrence_for['subscription_id'],
            m.Job.data['authorization_hash'].as_string() == exclude_occurrence_for['authorization_hash'],
            m.Job.data['origin']['trigger_id'].as_string() == 'new_data',
            m.Job.data['origin']['unit_id'].as_string() == m.VideoRun.id))
        query = query.where(~registered)
    if after is not None:
        need(type(after) is dict and set(after) == {'received_at', 'unit_id'})
        cursor = instant(after['received_at'])
        query = query.having(or_(first > cursor, and_(first == cursor, m.VideoRun.id > after['unit_id'])))
    rows = list(db.execute(query.order_by(first, m.VideoRun.id).limit(limit)))
    return [_unit(db, p, selection, target_pin, identity, received) for identity, received in rows]


def select_unit(db, p, selection, target_pin, *, at, not_before=None):
    end = instant(at)
    start = max(end-timedelta(hours=24), instant(not_before)) if not_before is not None else end-timedelta(hours=24)
    first = func.min(m.Event.received_at)
    row = db.execute(_query(db, p, selection, target_pin).having(first >= start, first <= end)
        .order_by(first.desc(), m.VideoRun.id).limit(1)).first()
    return _unit(db, p, selection, target_pin, *row) if row else None


def checked_unit(db, p, unit, target_pin=None):
    try: return _checked_unit(db, p, unit, target_pin)
    except HTTPException as error:
        if error.status_code != 404: raise
        # This is a reference inside an already saved unit, not a directory
        # lookup. Missing/foreign original ACK, Frame or evidence means broken
        # integrity; reveal neither its identity nor another workspace's data.
        raise HTTPException(409, '原接收资料或保存原件无法核对；未补造输入或继续派发') from None
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OSError):
        raise HTTPException(409, '原接收资料或保存原件无法核对；未补造输入或继续派发') from None


def _checked_unit(db, p, unit, target_pin=None):
    from .media_daily_task_adapter import checked_camera_frame
    need(type(unit) is dict and set(unit) == UNIT_KEYS and unit['unit_id'] == unit['run_id']
         and unit['source_hash'] == digest(unit['source_pin']))
    pin = unit['source_pin']; need(pin['schema'] == SCHEMA and pin['run']['id'] == unit['run_id']
        and pin['received_at'] == unit['received_at'] and pin['scene_time_trust'] == 'unknown')
    if target_pin is not None: need(unit['target_hash'] == digest(target_pin))
    event = scoped(db, m.Event, pin['event_id'], p)
    proof = delivery.checked(db, p, event)
    records = [record for record in proof['body']['summary']['records'] if digest(record) == pin['record_hash']]
    need(len(records) == 1)
    target = {**pin['scope'], 'camera_generation': pin['camera_generation'], 'gateway_generation': pin['gateway_generation']}
    actual = _metadata(db, p, event, records[0], target)
    need(actual == pin)
    proof['event_id'] = event.id
    checked, evidence, content = checked_camera_frame(db, p, proof, records[0], pin['scope']['camera_id'])
    need(checked['run_id'] == unit['run_id'] and checked['frame']['id'] == pin['frame']['id']
         and evidence.id == pin['image']['id'] and evidence.sha256 == pin['image']['sha256'])
    return _copy(pin), evidence, content


def materialize_input(db, p, instance, unit):
    from .workflow_quality_archive_v220 import workspace_directory, _directory_unchanged
    from .workflow_data_quality_v219 import settings
    from .evidence import save_immutable, inspect_content
    pin, original, content = checked_unit(db, p, unit)
    need(pin['scope']['instance_id'] == instance.id and instance.workspace_id == p.workspace_id)
    with workspace_directory(settings().storage_dir, p.workspace_id, create=False) as fd:
        image = save_immutable(db, p, content, original.media_type, inspect_content(content, original.media_type),
                               existing_directory_fd=fd)
        image.data = {**image.data, 'purpose': 'workflow_input_image', 'instance_id': instance.id,
            'name': unit['source_label'][:200]+'（拍摄时间未核实）', KEY: {'unit': _copy(unit)}}
        db.flush(); _directory_unchanged(settings().storage_dir, p.workspace_id, fd)
    return image
