"""A finite user-authorized Daily/Frame receipt -> ordinary image Task adapter.

No capture, model invocation, old Job reset, signal or device authority is
created here. The ordinary Task helper owns all original creation gates.
"""
import copy
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .contracts import Contract, digest
from .models import (Audit, Capability, DomainInstance, Entity, Event, Evidence, InstanceRelease,
    Job, MediaChannel, MediaGateway, Membership, StreamSession, Task, User, VideoDaily, VideoRun,
    VideoReadReservation)
from .media_daily_task_models import DailyTaskIntent as Intent, DailyTaskSlot as Slot
from .security import Principal
from .services import scoped, require, audit, published, validate
from . import media_daily_delivery as delivery
from . import workflow_data_quality_v219 as originals

KEY = 'media_daily_task_v226'
STATE = 'media.daily_task.state_v226'
COMMAND = 'media.daily_task.command_v226'
MAX_BODY = 65536
MAX_ROWS = 2000
CHINA = ZoneInfo('Asia/Shanghai')
ERROR = '原每日任务授权、执行槽、来源或独立保存证明无法核对；原记录保留'


def need(value, message=ERROR, code=409):
    if not value: raise HTTPException(code, message)


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        need(parsed.tzinfo is not None and parsed.utcoffset() is not None, '截止时间必须包含时区', 422)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError): raise HTTPException(422, '截止时间格式无效') from None


def days(body):
    try:
        start, end = date.fromisoformat(body['start_day']), date.fromisoformat(body['end_day'])
        need(start.isoformat() == body['start_day'] and end.isoformat() == body['end_day'])
        need(0 <= (end-start).days < 7, '每日任务日期须为连续1至7日', 422)
        return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    except (ValueError, TypeError): raise HTTPException(422, '业务日期格式无效') from None


class Preview(Contract):
    schema: Literal['media-daily-task-preview-v226']
    instance_version: StrictInt = Field(ge=1)
    release_id: UUID
    configuration_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    business_id: StrictStr = Field(min_length=1, max_length=100)
    entity_id: UUID
    camera_id: UUID
    camera_version: StrictInt = Field(ge=1)
    camera_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    start_day: StrictStr
    end_day: StrictStr
    expires_at: StrictStr
    max_tasks: StrictInt = Field(ge=1, le=7)
    frame_rule: Literal['earliest_verified_frame']
    allow_incomplete: StrictBool

    @model_validator(mode='after')
    def finite(self):
        value = self.model_dump(mode='json')
        need(self.max_tasks <= len(days(value)), '总任务数不得超过授权业务日数量', 422)
        instant(self.expires_at)
        return self


class Enable(Preview):
    schema: Literal['media-daily-task-enable-v226']
    request_id: UUID
    preview_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_scope: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def consent(cls, value):
        need(type(value) is dict and value.get('confirm_scope') is True, '请明确确认有限自动发起范围', 422)
        return value


class Pause(Contract):
    schema: Literal['media-daily-task-pause-v226']
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    reason: StrictStr = Field(min_length=1, max_length=1000)


def parse(raw, model):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, '请求超过64KB或为空', 413)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        need(type(value) is dict and set(value) == set(model.model_fields), '请求字段不完整或含未知字段', 422)
        return model.model_validate(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '每日任务请求格式无效，未保存或发起') from None


def configuration(body):
    value = body.model_dump(mode='json') if hasattr(body, 'model_dump') else copy.deepcopy(body)
    return {key: 'media-daily-task-preview-v226' if key == 'schema' else value[key] for key in Preview.model_fields}


def clock(db):
    return db.scalar(select(func.clock_timestamp()))


def lock(db, key):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest(key)[:15], 16)})


def current_actor(db, p, *, write=False):
    query = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id)
    if write: query = query.with_for_update(read=True, of=[User, Membership])
    row = db.execute(query).first()
    need(row is not None and row[1].role in {'admin', 'operator', 'viewer'}
        and row[1].role == p.role, '当前账号或工作区权限已变化', 403)
    return row[1].role


def camera_hash(row):
    return digest({'gateway_id': row.gateway_id, 'path': row.path, 'data': row.data, 'status': row.status})


def target(db, p, identity, body, *, locked=False):
    from .instance_api import active
    from .entity_release_compat import require_entity
    from .instance_semantic_runtime_dispatch import modern
    from .graph_task_inputs_v120 import describe
    from .runtime_task_documents import ports
    instance, release = active(db, p, identity, locked)
    need(instance.version == body['instance_version'] and release.id == body['release_id']
        and release.configuration_hash == body['configuration_hash'], '实例发布版本已变化，请重新预览')
    snapshot = release.data['snapshot']
    need(snapshot.get('profile', {}).get('simulation_v176') is True, '每日自动任务首片仅开放明确标记的技术试运行实例', 422)
    business = next((b for b in snapshot['businesses'] if b['id'] == body['business_id']), None)
    need(business and business['available'] and business['id'] in instance.data.get('enabled_business_ids', [])
        and business.get('execution_mode') == 'confirmed_graph_workflow', '请选择已启用的普通完整流程', 422)
    runtime = modern(snapshot)
    runtime.verify_release(db, p, release, business_name=business['name'])
    binding = runtime.business_binding(release, business)
    from .business_flow_execution_runtime import require_task_start
    need(binding.get('schema') == 'business-flow-execution-binding-v1', '当前仅支持已核对的普通单图业务流程', 422)
    require_task_start(binding, verified_anomaly_handoff=False)
    graph = binding['graph']; metadata = describe(db, p, identity, release.id, business['id'])
    validate(metadata['user_input_schema'], {})
    need(not ports(graph), '此流程需要额外资料集，不能由每日图片自动入口代填', 422)
    props = graph['input_schema'].get('properties', {})
    need('task_image_ids' in props and not any(name in props for name in
        ('task_audio_ref','task_sound_ref', 'task_scene_refs', 'task_comparison_refs')), '此流程不是普通单图任务入口', 422)
    validate(props['task_image_ids'], [str(UUID(int=1))])
    caps = []
    for node in graph['nodes']:
        if node['kind'] != 'ability': continue
        cap = published(db, p, node['execution']['capability_id'])
        need(cap.content_hash == node['execution']['capability_hash']
            and cap.data['data_policy'].get('cloud_allowed') is False
            and cap.data['execution'].get('side_effect') is False,
            '此自动入口不授权云调用、设备控制或外部写入；请使用普通任务独立审批', 422)
        caps.append({'id': cap.id, 'hash': cap.content_hash, 'release': cap.release_version})
    entity = scoped(db, Entity, body['entity_id'], p)
    definition = require_entity(db, p, instance, release, entity, lock_sources=False)
    camera = scoped(db, MediaChannel, body['camera_id'], p)
    gateway = scoped(db, MediaGateway, camera.gateway_id, p)
    need(camera.status == 'validated' and camera.version == body['camera_version']
        and camera_hash(camera) == body['camera_hash'], '摄像头配置已变化或尚未确认，请重新预览')
    pin = {'instance_id': instance.id, 'instance_version': instance.version,
        'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'business_id': business['id'], 'business_name': business['name'],
        'entity_id': entity.id, 'entity_version': entity.version, 'entity_hash': digest({'data': entity.data, 'definition': definition}),
        'camera_id': camera.id, 'camera_version': camera.version, 'camera_hash': camera_hash(camera),
        'gateway_id': gateway.id, 'gateway_version': gateway.version, 'gateway_hash': digest(gateway.data),
        'binding_hash': binding['binding_hash'], 'graph_hash': digest(graph),
        'capabilities_hash': digest(sorted(caps, key=lambda x: x['id'])), 'roles_hash': digest(binding['role_bindings'])}
    return pin


def scope_lines(body, pin, entity_label, camera_label):
    return [pin['business_name']+'：仅发起内部技术整理。',
        '本次授权对象：'+str(entity_label)[:200]+'；摄像头：'+str(camera_label)[:200]+'。',
        body['start_day']+' 至 '+body['end_day']+'（中国业务日），每天最多1次，总计最多'+str(body['max_tasks'])+'次。',
        '仅选择按平台接收顺序最早且已真实处理、独立接收的一张画面；不代表全天或现场状态。',
        '资料未齐时仅整理已收到的真实画面，保留全部未齐限制。' if body['allow_incomplete'] else '资料未齐时等待，不发起任务。',
        '原人工审核、能力预算和任务审批保持；不授权设备控制或云调用。']


def one(db, p, action, object_id):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id).limit(2)))
    need(len(rows) == 1)
    return rows[0]


def checked_camera_frame(db, p, proof, record, camera_id):
    try: return _checked_camera_frame(db, p, proof, record, camera_id)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OSError):
        raise HTTPException(409, '真实每日资料、画面原件或接收证明无法核对；未采用该来源') from None


def _checked_camera_frame(db, p, proof, record, camera_id):
    """Generic capture proof. Never borrow a native Watch INPUT/RESULT grant."""
    from .native_video_provenance_v164 import _frames
    from .media import validate_completion
    from .video_read_boundary import checked as checked_reservation
    actual = delivery.checked(db, p, scoped(db, Event, proof['event_id'], p))
    need(set(proof) == set(actual) | {'event_id'} and all(proof[key] == value for key, value in actual.items()))
    need(proof['body']['modality'] == 'video' and proof['state']['status'] == 'received'
        and proof['body']['source_key'] == 'channel:'+camera_id
        and sum(item == record for item in proof['body']['summary']['records']) == 1)
    session = scoped(db, StreamSession, record['id'], p)
    need(record.get('mode') != 'upload' and session.channel_id == camera_id
        and session.status == 'captured' and session.video_run_id == record.get('run_id'))
    run = scoped(db, VideoRun, session.video_run_id, p)
    job = scoped(db, Job, run.data['job_id'], p)
    capture = scoped(db, Job, session.data['job_id'], p)
    need(run.status == job.status == capture.status == 'succeeded'
        and job.kind == 'process_video' and job.data.get('run_id') == run.id
        and job.idempotency_key == 'video:'+run.id
        and capture.kind == 'capture_media' and capture.data.get('session_id') == session.id
        and run.evidence_id == session.evidence_id and run.evidence_id is not None)
    sessions = list(db.scalars(select(StreamSession.id).where(StreamSession.workspace_id == p.workspace_id,
        StreamSession.video_run_id == run.id).limit(2)))
    jobs = list(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'process_video', Job.data['run_id'].as_string() == run.id).limit(2)))
    captures = list(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'capture_media', Job.data['session_id'].as_string() == session.id).limit(2)))
    need(sessions == [session.id] and jobs == [job.id] and captures == [capture.id])
    reservation = checked_reservation(scoped(db, VideoReadReservation, session.data['read_reservation_id'], p))
    request = reservation.data['receipt']['request']
    need(reservation.data['receipt_hash'] == session.data['read_reservation_hash']
        and reservation.channel_id == camera_id and reservation.actor_id == capture.data['actor_id']
        and reservation.request_key == request['request_key'] == 'clip:'+session.id
        and request['kind'] == 'clip' and request['seconds'] == session.data['seconds'])
    source = run.data['source_context']; result = run.data['result']; completion = session.data['receipt']
    required_result = {'schema', 'backend', 'cloud_calls', 'audio_processed', 'tracking_identity',
        'sample_hz', 'duration_seconds', 'sample_count'}
    need(type(result) is dict and required_result <= set(result)
        and type(result['schema']) is str and type(result['backend']) is str
        and type(result['tracking_identity']) is str and type(result['cloud_calls']) is int
        and type(result['audio_processed']) is bool and type(result['sample_hz']) in {int, float}
        and type(result['sample_count']) is int and type(result['duration_seconds']) in {int, float},
        '画面处理结果缺少必要信息，暂不能作为任务输入')
    need(type(source) is dict and source['session_id'] == session.id and source['channel_id'] == camera_id
        and source.get('partial') is False and source['evidence_id'] == run.evidence_id
        and source['capture_hash'] == session.data['capture_hash']
        and source['gateway_generation'] == session.data['gateway_generation']
        and source['channel_generation'] == session.data['channel_generation']
        and source['source_clock_utc'] == 'unknown'
        and source.get('domain_binding') == session.data.get('domain_binding')
        and run.data['configuration'] == session.data['configuration']
        and run.data['backend'] == session.data['backend']
        and run.installation_id == session.data['installation_id']
        and run.data['installation_generation'] == session.data['installation_generation']
        and type(result) is dict and result['schema'] == 'local-video-v1'
        and result['cloud_calls'] == 0 and type(result['cloud_calls']) is int
        and result['backend'] == run.data['backend'] and result['audio_processed'] is False
        and result['tracking_identity'] == 'session_and_segment_only'
        and result['sample_hz'] == run.data['configuration']['sample_hz']
        and digest(result) == run.data['result_hash'])
    expected_key = digest({'input': run.data['input_sha256'], 'configuration': run.data['configuration'],
        'pipeline': run.data['pipeline_hash'], 'installation': run.installation_id,
        'generation': run.data['installation_generation'], 'backend': run.data['backend'],
        'retry_of': run.data['retry_of'], 'actor': job.data['actor_id'], 'source_context': source})
    need(run.request_key == expected_key and run.data['retry_of'] is None)
    validate_completion(completion, session.data['seconds'])
    need(completion['partial'] is False and type(completion['cloud_calls']) is int and completion['cloud_calls'] == 0
        and all(source[k] == completion[k] for k in ('received_start_unix', 'received_end_unix'))
        and source['captured_duration_seconds'] == completion['duration_seconds']
        and type(result['duration_seconds']) in {int, float}
        and 0 < result['duration_seconds'] < 86400
        and abs(result['duration_seconds'] - completion['duration_seconds']) <= .5)
    original = scoped(db, Evidence, run.evidence_id, p)
    raw_video = originals.read_original(originals.settings().storage_dir, p.workspace_id, original)
    need(hashlib.sha256(raw_video).hexdigest() == run.data['input_sha256'] == session.data['input_sha256'])
    frames, pins = _frames(db, p, run)
    completed = one(db, p, 'video.completed', run.id)
    need(completed.actor_id == job.data['actor_id'] and completed.data == {'samples': len(frames), 'cloud_calls': 0})
    frame, frame_pin = frames[0], pins[0]
    evidence = scoped(db, Evidence, frame.evidence_id, p)
    content = originals.read_original(originals.settings().storage_dir, p.workspace_id, evidence)
    need(evidence.media_type in {'image/png', 'image/jpeg'} and evidence.sha256 == frame_pin['sha256'])
    need(originals.format_check(content, evidence.media_type)[0] == 'valid')
    frozen = {'daily_id': proof['body']['daily_id'], 'revision': proof['body']['daily_revision'],
        'day': proof['body']['day'], 'event_id': proof['event_id'],
        'payload_hash': proof['body']['payload_hash'], 'ack_memory_id': proof['state']['memory_id'],
        'ack_memory_hash': proof['state']['memory_hash'], 'record_hash': digest(record),
        'session_id': session.id, 'session_hash': digest(session.data), 'run_id': run.id,
        'run_hash': digest(run.data), 'job_id': job.id, 'job_key': job.idempotency_key,
        'capture_job_id': capture.id, 'capture_job_key': capture.idempotency_key,
        'read_reservation_id': reservation.id, 'read_reservation_hash': reservation.data['receipt_hash'],
        'video_evidence_id': original.id, 'video_sha256': original.sha256,
        'frame': frame_pin, 'completion_audit_id': completed.id, 'completion_audit_hash': digest(completed.data)}
    return frozen, evidence, content


def candidate(db, p, body, day, *, target_pin):
    try: return _candidate(db, p, body, day, target_pin=target_pin)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OSError):
        raise HTTPException(409, '当日资料或原接收证明无法完整核对；未发起任务') from None


def _candidate(db, p, body, day, *, target_pin):
    # Only current selection has this gate. The historical checker deliberately
    # follows its original captured pins after later device reconfiguration.
    camera = scoped(db, MediaChannel, body['camera_id'], p)
    gateway = scoped(db, MediaGateway, camera.gateway_id, p)
    need(camera.status == 'validated' and camera.version == body['camera_version'] == target_pin['camera_version']
        and camera.id == target_pin['camera_id'] and camera_hash(camera) == body['camera_hash'] == target_pin['camera_hash']
        and gateway.id == target_pin['gateway_id'] and gateway.version == target_pin['gateway_version']
        and digest(gateway.data) == target_pin['gateway_hash'], '已核对的摄像头或网关配置已变化，请重新预览')
    current_generations = (camera.data['generation'], gateway.data['generation'])
    need(all(type(value) is int and value > 0 for value in current_generations), '当前摄像头或网关配置代次无法核对')
    daily = db.scalar(select(VideoDaily).where(VideoDaily.workspace_id == p.workspace_id,
        VideoDaily.source_key == 'channel:'+body['camera_id'], VideoDaily.business_day == day))
    if daily is None: return None, '等待摄像头的真实每日资料'
    events = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == delivery.KIND, Event.data['daily_id'].as_string() == daily.id,
        Event.data['daily_revision'].as_integer() == daily.version).limit(2)))
    if not events: return None, '等待每日资料冻结发布和独立接收'
    need(len(events) == 1)
    proof = delivery.checked(db, p, events[0]); proof['event_id'] = events[0].id
    if proof['state']['status'] != 'received': return None, '等待数据管理独立接收；原失败或待接收状态未修改'
    delivery.validate_sources(db, p, proof['body'])
    summary = proof['body']['summary']
    gaps = bool(summary.get('provisional') or summary.get('pending_ids') or summary.get('failed_or_partial_ids'))
    if gaps and not body['allow_incomplete']: return None, '当日资料未齐，按授权等待；可在技术试运行中另行明确选择仅整理已收到画面'
    records = summary['records']; need(type(records) is list and len(records) <= 100)
    choices = []; old_generations = 0
    for record in records:
        if record.get('processing_status') != 'succeeded' or record.get('capture_status') != 'captured': continue
        session = scoped(db, StreamSession, record['id'], p)
        need(session.channel_id == body['camera_id'] and session.video_run_id == record['run_id'])
        run = scoped(db, VideoRun, session.video_run_id, p)
        context = run.data['source_context']
        need(type(context) is dict and context['session_id'] == session.id and context['channel_id'] == camera.id)
        captured_generations = (context['channel_generation'], context['gateway_generation'])
        session_generations = (session.data['channel_generation'], session.data['gateway_generation'])
        need(all(type(value) is int and value > 0 for value in captured_generations)
            and all(type(value) is int and value > 0 for value in session_generations)
            and captured_generations == session_generations)
        if captured_generations != current_generations:
            old_generations += 1
            continue
        choices.append((session.created_at, session.id, record))
    if not choices:
        return None, ('当日已收画面来自旧配置代次；等待当前摄像头和网关代次的真实已接收画面'
            if old_generations else '尚无处理成功且已独立接收的完整画面')
    record = sorted(choices, key=lambda item: (item[0], item[1]))[0][2]
    target = record.get('business_target')
    if target:
        need(all(target.get(k) == body[f] for k, f in
            (('instance_release_id', 'release_id'), ('business_id', 'business_id'), ('entity_id', 'entity_id'))),
            '原资料已声明另一业务目标，不能由自动入口改签')
    pin, evidence, content = checked_camera_frame(db, p, proof, record, body['camera_id'])
    pin['limitations'] = (['当日资料未齐，仅整理已收到的单张画面；未覆盖全部尝试。'] if gaps else [])
    return (pin, evidence, content), '已找到真实处理、独立接收并可核验原件的画面'


def source_view(pin):
    return None if pin is None else {key: pin[key] for key in ('daily_id', 'revision', 'day', 'event_id', 'run_id')} | {
        'ack_status': 'received', 'frame_id': pin['frame']['id'], 'frame_sha256': pin['frame']['sha256'],
        'limitations': copy.deepcopy(pin['limitations'])}


def checked_saved_source(db, p, pin, camera_id):
    """Historical original pins, never current camera health or configuration."""
    event = scoped(db, Event, pin['event_id'], p)
    proof = delivery.checked(db, p, event); proof['event_id'] = event.id
    need(proof['state']['status'] == 'received')
    delivery.validate_sources(db, p, proof['body'])
    records = [item for item in proof['body']['summary']['records'] if digest(item) == pin['record_hash']]
    need(len(records) == 1)
    checked, _, _ = checked_camera_frame(db, p, proof, records[0], camera_id)
    summary = proof['body']['summary']
    gaps = bool(summary.get('provisional') or summary.get('pending_ids') or summary.get('failed_or_partial_ids'))
    checked['limitations'] = ['当日资料未齐，仅整理已收到的单张画面；未覆盖全部尝试。'] if gaps else []
    need(checked == pin)


def preview(db, p, identity, body):
    require(p, 'admin', 'operator'); current_actor(db, p)
    value = configuration(body); now = clock(db); expiry = instant(value['expires_at'])
    need(now < expiry <= now+timedelta(days=7), '授权须在未来七日内到期', 422)
    pin = target(db, p, identity, value)
    entity = scoped(db, Entity, value['entity_id'], p)
    camera = scoped(db, MediaChannel, value['camera_id'], p)
    sealed = {'schema': 'media-daily-task-preview-result-v226', 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'command': value, 'pins': pin,
        'scope_lines': scope_lines(value, pin, entity.external_id, camera.data.get('name') or '已确认摄像头')}
    selected_day = next((d for d in days(value) if d <= now.astimezone(CHINA).date().isoformat()), None)
    found, reason = candidate(db, p, value, selected_day, target_pin=pin) if selected_day else (None, '等待授权业务日到来')
    return {**sealed, 'availability': {'status': 'ready' if found else 'waiting_data', 'reason': reason,
        'day': selected_day, 'source': source_view(found[0]) if found else None}, 'can_enable': True,
        'preview_hash': digest(sealed)}


def row_snapshot(row):
    keys = ('instance_id', 'actor_id', 'status') if isinstance(row, Intent) else (
        'intent_id', 'instance_id', 'business_id', 'entity_id', 'camera_id', 'day', 'status', 'task_id')
    return {'kind': 'intent' if isinstance(row, Intent) else 'slot', 'id': row.id, 'workspace_id': row.workspace_id,
        'version': row.version, **{key: getattr(row, key) for key in keys}, 'data': copy.deepcopy(row.data)}


def mark(db, p, row, data, status=None):
    previous = row.record_hash or None
    if previous: row.version += 1
    row.data = copy.deepcopy(data)
    if status: row.status = status
    db.flush(); snap = row_snapshot(row); row.record_hash = digest(snap)
    audit(db, p, STATE, row.id, {'previous_hash': previous, 'snapshot': snap, 'record_hash': row.record_hash})


def inventory(db, p, identity):
    try: return _inventory(db, p, identity)
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OSError):
        raise HTTPException(409, ERROR) from None


def _inventory(db, p, identity):
    intents = list(db.scalars(select(Intent).where(Intent.workspace_id == p.workspace_id, Intent.instance_id == identity)
        .execution_options(populate_existing=True)))
    slots = list(db.scalars(select(Slot).where(Slot.workspace_id == p.workspace_id, Slot.instance_id == identity)
        .execution_options(populate_existing=True)))
    traces = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == STATE)))
    need(len(intents)+len(slots) <= MAX_ROWS and len(traces) <= MAX_ROWS*20)
    rows = {row.id: row for row in [*intents, *slots]}
    selected = [event for event in traces if event.data['snapshot']['instance_id'] == identity]
    need({event.object_id for event in selected} == set(rows))
    for row in rows.values():
        history = sorted([event for event in selected if event.object_id == row.id],
            key=lambda event: event.data['snapshot']['version'])
        previous = None
        for number, event in enumerate(history, 1):
            value = event.data; snap = value['snapshot']
            need(snap['id'] == row.id and snap['workspace_id'] == p.workspace_id and snap['version'] == number
                and value['previous_hash'] == previous and digest(snap) == value['record_hash'])
            previous = value['record_hash']
        need(history and row.record_hash == previous == digest(row_snapshot(row))
            and row.version == len(history))
    by_intent = {row.id: row for row in intents}
    commands = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == COMMAND, Audit.data['instance_id'].as_string() == identity)))
    need(len(commands) <= MAX_ROWS and {item.object_id for item in commands} == set(by_intent))
    for intent in intents:
        own = [slot for slot in slots if slot.intent_id == intent.id]
        body = configuration(Preview.model_validate(intent.data['configuration']))
        need(len(own) == len(days(body)) and {slot.day for slot in own} == set(days(body))
            and sorted(slot.id for slot in own) == intent.data['slot_ids'])
        records = [item for item in commands if item.object_id == intent.id]
        nonces = {'enable': intent.data['enable_request_id']}
        if intent.status == 'paused': nonces['pause'] = intent.data['pause_request_id']
        need(len(records) == len(nonces))
        for operation, nonce in nonces.items():
            matches = [item for item in records if item.data['request_id'] == nonce]
            need(len(matches) == 1)
            event = matches[0]; value = event.data; result = value['result']
            raw = value['exact_body'].encode('utf-8'); parsed = parse(raw, Enable if operation == 'enable' else Pause)
            need(event.actor_id == intent.actor_id and value['operation'] == operation
                and str(parsed.request_id) == nonce and value['body_sha256'] == hashlib.sha256(raw).hexdigest()
                and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'})
                and result['schema'] == 'media-daily-task-command-result-v226'
                and result['request_id'] == nonce and result['body_sha256'] == value['body_sha256']
                and result['operation'] == operation and result['saved_version'] == result['intent']['version']
                and result['saved_version'] <= intent.version and result['intent']['id'] == intent.id
                and result['intent']['actor_id'] == intent.actor_id and result['intent']['configuration'] == body
                and sorted(item['id'] for item in result['intent']['slots']) == intent.data['slot_ids'])
            need(operation != 'enable' or configuration(parsed) == body)
    for slot in slots:
        need(slot.intent_id in by_intent)
        owner = by_intent[slot.intent_id]; body = owner.data['configuration']
        need(slot.day in days(body) and slot.business_id == body['business_id']
            and slot.entity_id == body['entity_id'] and slot.camera_id == body['camera_id'])
        need(all(event.actor_id == owner.actor_id for event in selected if event.object_id in {slot.id, owner.id}))
        if slot.task_id:
            checked_saved_source(db, p, slot.data['source'], slot.camera_id)
            task = scoped(db, Task, slot.task_id, p)
            need(slot.status == 'created' and task.data.get(KEY) == {'slot_id': slot.id,
                'intent_id': slot.intent_id, 'origin_hash': digest(slot.data['source'])}
                and task.data.get('creation_request_id') == slot.data['body']['request_id'])
            from .workspace_api import NewTask, creation_body_hash
            need(task.data['creation_hash'] == creation_body_hash(NewTask.model_validate(slot.data['body'])))
            release = scoped(db, InstanceRelease, body['release_id'], p)
            need(slot.data['body']['entity_ids'] == task.data['entity_ids'] == [slot.entity_id]
                and slot.data['body']['release_id'] == task.data['instance_release_id'] == body['release_id']
                and release.instance_id == identity and task.package_id == release.package_id
                and slot.data['body']['business_id'] == slot.business_id)
            evidence = scoped(db, Evidence, slot.data['image_id'], p)
            need(evidence.data.get(KEY) == {'slot_id': slot.id, 'intent_id': slot.intent_id,
                'origin': slot.data['source']} and evidence.sha256 == slot.data['source']['frame']['sha256'])
            from .workflow_images import view
            need(evidence.data['instance_id'] == identity and evidence.data['purpose'] == 'workflow_input_image'
                and task.data['image_snapshots'] == [view(evidence)]
                and task.owner_id == owner.actor_id and task.data['creation_hash'] == slot.data['body_hash']
                and slot.data['body']['image_ids'] == [evidence.id]
                and task.data['instance_id'] == identity and task.data['business_id'] == slot.business_id)
            originals.read_original(originals.settings().storage_dir, p.workspace_id, evidence)
            saved = one(db, p, 'workflow.image_saved', evidence.id)
            made = one(db, p, 'workflow.created', task.id)
            need(saved.actor_id == made.actor_id == owner.actor_id
                and saved.data == {'instance': identity, 'sha256': evidence.sha256, 'cloud_calls': 0}
                and made.data == {'release': body['release_id'], 'business': slot.business_id})
        else: need(slot.status != 'created' and not slot.data.get('image_id') and not slot.data.get('body'))
    # Reverse successful traces cannot disappear into an empty list.
    for model in (Evidence, Task):
        marked = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
            model.data[KEY].is_not(None), model.data['instance_id'].as_string() == identity)))
        for item in marked:
            linked = rows.get(item.data[KEY]['slot_id'])
            need(isinstance(linked, Slot) and linked.task_id is not None
                and (linked.task_id if model is Task else linked.data['image_id']) == item.id)
    return intents, slots


def task_blocked(db, p, task):
    if task is None: return False
    terminal = {'failed', 'blocked', 'cancelled', 'stopped', 'failed_timeout'}
    if any(word in task.status for word in terminal): return True
    transports = task.data.get('graph_jobs', {})
    need(type(transports) is dict and len(transports) <= 2000)
    for item in transports.values():
        need(type(item) is dict and type(item.get('job_id')) is str)
        job = scoped(db, Job, item['job_id'], p)
        need(job.data.get('task_id') == task.id)
        if item.get('phase') == 'blocked' or job.status in terminal: return True
    return task.data.get('graph_runtime', {}).get('status') in terminal


def slot_view(db, p, row):
    from .workflow_images import view
    image = scoped(db, Evidence, row.data['image_id'], p) if row.data.get('image_id') else None
    task = scoped(db, Task, row.task_id, p) if row.task_id else None
    failed = task_blocked(db, p, task)
    label = {'waiting_data': '等待资料', 'verifying': '核验中', 'created': '已创建任务', 'blocked': '受阻'}[row.status]
    return {'id': row.id, 'version': row.version, 'day': row.day, 'status': row.status,
        'label': '任务受阻，请查看原任务' if failed else label,
        'reason': '原任务步骤未能完成，请进入原任务核对；不会自动换号或补建。' if failed else row.data.get('reason', ''),
        'source': source_view(row.data.get('source')), 'image': view(image) if image else None,
        'task': {'id': task.id, 'status': task.status, 'url': '/api/workspace/tasks/'+task.id} if task else None,
        'task_request_id': row.data.get('body', {}).get('request_id')}


def intent_view(db, p, row, slots, at):
    own = [s for s in slots if s.intent_id == row.id]; made = sum(s.task_id is not None for s in own)
    views = [slot_view(db, p, s) for s in sorted(own, key=lambda s: s.day)]
    failed = any(item['label'] == '任务受阻，请查看原任务' for item in views)
    state = ('paused' if row.status == 'paused' else 'expired' if at >= instant(row.data['configuration']['expires_at'])
        else 'blocked' if failed or any(s.status == 'blocked' for s in own)
        else 'limit_reached' if made >= row.data['configuration']['max_tasks']
        else 'verifying' if any(s.status == 'verifying' for s in own) else 'created' if made else 'waiting_data')
    return {'id': row.id, 'version': row.version, 'status': row.status, 'state': state,
        'label': {'paused': '已暂停', 'expired': '已到期', 'limit_reached': '已达到次数上限', 'blocked': '受阻',
            'verifying': '核验中', 'created': '已有任务', 'waiting_data': '等待资料'}[state],
        'actor_id': row.actor_id, 'created_at': row.created_at.isoformat(),
        'configuration': copy.deepcopy(row.data['configuration']), 'scope_lines': copy.deepcopy(row.data['scope_lines']),
        'created_count': made, 'permissions': {'can_pause': row.status == 'enabled' and row.actor_id == p.user_id and p.role in {'admin', 'operator'}},
        'slots': views}


def listing(db, p, identity):
    current_actor(db, p); scoped(db, DomainInstance, identity, p)
    intents, slots = inventory(db, p, identity); at = clock(db)
    return {'schema': 'media-daily-task-list-v226', 'instance_id': identity,
        'intents': [intent_view(db, p, row, slots, at) for row in sorted(intents, key=lambda row: (row.created_at, row.id), reverse=True)]}


def recover(db, p, identity, request_id, expected_sha256):
    current_actor(db, p); scoped(db, DomainInstance, identity, p)
    need(originals.SHA.fullmatch(expected_sha256), '原请求摘要无效', 422)
    records = list(db.scalars(select(Audit).where(Audit.action == COMMAND,
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    need(len(records) <= 1)
    if not records:
        inventory(db, p, identity)
        return {'schema': 'media-daily-task-request-v226', 'request_id': request_id,
            'body_sha256': expected_sha256, 'found': False, 'result': None}
    record = records[0]; value = record.data
    need(record.workspace_id == p.workspace_id and record.actor_id == p.user_id
        and value['instance_id'] == identity and value['body_sha256'] == expected_sha256)
    need(hashlib.sha256(value['exact_body'].encode()).hexdigest() == expected_sha256
        and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'}))
    intents, _ = inventory(db, p, identity)
    need(any(row.id == record.object_id for row in intents))
    return {'schema': 'media-daily-task-request-v226', 'request_id': request_id,
        'body_sha256': expected_sha256, 'found': True, 'result': copy.deepcopy(value['result'])}


def command(db, p, identity, raw, *, intent_id=None):
    operation = 'pause' if intent_id else 'enable'; body = parse(raw, Pause if intent_id else Enable)
    nonce = str(body.request_id); sha = hashlib.sha256(raw).hexdigest()
    lock(db, {'media_daily_task_nonce': nonce})
    prior = recover(db, p, identity, nonce, sha)
    if prior['found']:
        need(prior['result']['operation'] == operation
            and (intent_id is None or prior['result']['intent']['id'] == intent_id), '原请求属于另一个授权或操作')
        return prior['result']
    require(p, 'admin', 'operator'); scoped(db, DomainInstance, identity, p, True)
    intents, slots = inventory(db, p, identity)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    if operation == 'enable':
        checked = preview(db, p, identity, body)
        need(checked['preview_hash'] == body.preview_hash, '范围或当前来源已变化，请重新预览')
        value = checked['command']
        need(len(intents) < 200)
        need(not any(s.business_id == value['business_id'] and s.entity_id == value['entity_id']
            and s.camera_id == value['camera_id'] and s.day in days(value) for s in slots),
            '同一对象、业务、摄像头业务日已有执行槽；暂停或换授权不能重复发起')
        row = Intent(workspace_id=p.workspace_id, instance_id=identity, actor_id=p.user_id, status='enabled')
        db.add(row); db.flush()
        created_slots = []
        for day in days(value):
            slot = Slot(workspace_id=p.workspace_id, instance_id=identity, intent_id=row.id,
                business_id=value['business_id'], entity_id=value['entity_id'], camera_id=value['camera_id'], day=day)
            db.add(slot); db.flush(); created_slots.append(slot)
        mark(db, p, row, {'configuration': value, 'pins': checked['pins'], 'scope_lines': checked['scope_lines'],
            'enable_request_id': nonce, 'slot_ids': sorted(slot.id for slot in created_slots)})
        for slot in created_slots:
            mark(db, p, slot, {'reason': '等待真实日资料独立接收', 'source': None}); slots.append(slot)
    else:
        row = next((r for r in intents if r.id == intent_id), None)
        need(row is not None, '授权不存在', 404)
        need(row.actor_id == p.user_id, '请由原授权作者暂停', 403)
        need(row.version == body.expected_version and row.status == 'enabled', '授权版本或状态已变化')
        need(body.reason.strip(), '请说明暂停原因', 422)
        mark(db, p, row, {**row.data, 'pause_reason': body.reason, 'pause_request_id': nonce}, 'paused')
    result = {'schema': 'media-daily-task-command-result-v226', 'operation': operation,
        'request_id': nonce, 'body_sha256': sha, 'saved_version': row.version,
        'intent': intent_view(db, p, row, slots, clock(db))}
    saved = {'request_id': nonce, 'instance_id': identity, 'operation': operation, 'exact_body': raw.decode(),
        'body_sha256': sha, 'result': result}
    saved['record_hash'] = digest(saved)
    audit(db, p, COMMAND, row.id, saved); db.commit()
    return copy.deepcopy(result)


def options(db, p, identity):
    from .instance_api import active
    current_actor(db, p); instance, release = active(db, p, identity)
    snapshot = release.data['snapshot']
    entities = list(db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id,
        Entity.namespace == instance.namespace).limit(201)))
    cameras = list(db.scalars(select(MediaChannel).where(MediaChannel.workspace_id == p.workspace_id,
        MediaChannel.status == 'validated').limit(201)))
    need(len(entities) <= 200 and len(cameras) <= 200, '选择目录超过当前完整容量，未截断', 422)
    return {'schema': 'media-daily-task-options-v226', 'instance': {'id': instance.id,
        'version': instance.version, 'name': instance.data.get('name', ''), 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'trial': snapshot.get('profile', {}).get('simulation_v176') is True},
        'businesses': [{'id': b['id'], 'name': b['name']} for b in snapshot['businesses']
            if b['id'] in instance.data.get('enabled_business_ids', []) and b['available']],
        'entities': [{'id': row.id, 'label': row.external_id, 'version': row.version} for row in entities],
        'cameras': [{'id': row.id, 'label': str(row.data.get('name') or '已确认摄像头')[:200],
            'version': row.version, 'hash': camera_hash(row)} for row in cameras],
        'constraints': {'timezone': 'Asia/Shanghai', 'max_days': 7, 'max_tasks': 7, 'per_day': 1,
            'frame_rule': 'earliest_verified_frame'}, 'permissions': {'can_manage': p.role in {'admin', 'operator'}}}


def process_slot(db, workspace_id, slot_id):
    initial = db.scalar(select(Slot).where(Slot.workspace_id == workspace_id, Slot.id == slot_id))
    if initial is None: return False
    # Same order as the ordinary Task helper: Instance before actor or slot.
    instance = db.scalar(select(DomainInstance).where(DomainInstance.id == initial.instance_id,
        DomainInstance.workspace_id == workspace_id).with_for_update(skip_locked=True))
    if instance is None: return False
    row = db.scalar(select(Intent).where(Intent.id == initial.intent_id).with_for_update())
    slot = db.scalar(select(Slot).where(Slot.id == slot_id).with_for_update())
    user = db.get(User, row.actor_id)
    membership = db.scalar(select(Membership).where(Membership.user_id == row.actor_id,
        Membership.workspace_id == workspace_id))
    p = Principal(row.actor_id, workspace_id, membership.role if membership else 'viewer', 'media-daily-task-worker-v226')
    inventory(db, p, instance.id)
    if slot.task_id or slot.status == 'blocked' or row.status != 'enabled': return False
    now = clock(db); body = row.data['configuration']
    if now >= instant(body['expires_at']) or slot.day > now.astimezone(CHINA).date().isoformat(): return False
    try:
        need(user is not None and user.active and membership is not None and membership.role in {'admin', 'operator'},
            '原授权账号已停用或撤权，未创建任务')
        current_actor(db, p, write=True)
        pin = target(db, p, instance.id, body, locked=True)
        need(pin == row.data['pins'], '实例、对象、能力或摄像头配置已变化，请查看原授权')
        made = db.scalar(select(func.count()).select_from(Slot).where(
            Slot.intent_id == row.id, Slot.task_id.is_not(None)))
        if made >= body['max_tasks']: return False
        found, reason = candidate(db, p, body, slot.day, target_pin=pin)
        if found is None:
            if slot.data['reason'] != reason: mark(db, p, slot, {**slot.data, 'reason': reason})
            db.commit(); return False
        source, evidence, content = found
        need(clock(db) < instant(body['expires_at']), '原授权已到期，未创建任务')
        from .evidence import inspect_content, save_immutable
        from .workflow_quality_archive_v220 import workspace_directory, _directory_unchanged
        from .workflow_images import snapshots
        from .workspace_api import create_task, NewTask, creation_body_hash
        with db.begin_nested(), workspace_directory(originals.settings().storage_dir, p.workspace_id, create=False) as fd:
            # The genuine source image already has this immutable SHA blob.
            # Register a new, separately authorized ordinary image Evidence;
            # never rewrite its original bytes or follow a storage symlink.
            image = save_immutable(db, p, content, evidence.media_type, inspect_content(content, evidence.media_type),
                existing_directory_fd=fd)
            image.data = {**image.data, 'purpose': 'workflow_input_image', 'instance_id': instance.id,
                'name': slot.day+'每日资料'+('（资料未齐，仅已收画面）' if source['limitations'] else '（已收单张画面）'),
                KEY: {'slot_id': slot.id, 'intent_id': row.id, 'origin': source}}
            db.flush(); snapshots(db, p, instance.id, [image.id])
            audit(db, p, 'workflow.image_saved', image.id, {'instance': instance.id, 'sha256': image.sha256, 'cloud_calls': 0})
            request_id = 'daily_task_'+digest({'slot_id': slot.id})
            task_body = NewTask(release_id=body['release_id'], business_id=body['business_id'],
                entity_ids=[body['entity_id']], image_ids=[image.id], title=slot.day+'每日观测资料内部技术整理', request_id=request_id)
            task = create_task(instance.id, task_body, p, db)
            need(clock(db) < instant(body['expires_at']), '正常创建核验期间授权已到期，未保存任务')
            task.data = {**task.data, KEY: {'slot_id': slot.id, 'intent_id': row.id, 'origin_hash': digest(source)}}
            slot.task_id = task.id
            mark(db, p, slot, {'reason': '已按原有限授权创建正常任务，人工审核和运行结果请进入原任务查看',
                'source': source, 'image_id': image.id, 'body': task_body.model_dump(mode='json'),
                'body_hash': creation_body_hash(task_body)}, 'created')
            _directory_unchanged(originals.settings().storage_dir, p.workspace_id, fd)
        db.commit(); return True
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, OSError):
        # Only the creation SAVEPOINT rolled back; no tool executes inline.
        # Integrity failures before it create no Evidence/Task. No original Job
        # is retried or reset; this slot becomes visibly terminal blocked.
        if not db.is_active: raise
        mark(db, p, slot, {**slot.data, 'reason': '当前授权、接收证明、原件或正常任务创建未通过核对；原资料未修改'}, 'blocked')
        db.commit(); return False


def consume_once(workspace_id):
    need(str(UUID(workspace_id)) == workspace_id, '须明确指定工作区', 422)
    from .db import engine
    with Session(engine()) as db:
        at = clock(db); today = at.astimezone(CHINA).date().isoformat()
        rows = list(db.execute(select(Slot, Intent).join(Intent, Intent.id == Slot.intent_id).where(
            Slot.workspace_id == workspace_id, Intent.workspace_id == workspace_id,
            Intent.status == 'enabled', Slot.task_id.is_(None),
            Slot.status.in_(['waiting_data', 'verifying']), Slot.day <= today).limit(MAX_ROWS+1)))
        need(len(rows) <= MAX_ROWS, '当前等待范围超过完整调度容量，未截断或派发', 422)
        intent_ids = list({intent.id for _, intent in rows})
        made = dict(db.execute(select(Slot.intent_id, func.count()).where(
            Slot.workspace_id == workspace_id, Slot.intent_id.in_(intent_ids),
            Slot.task_id.is_not(None)).group_by(Slot.intent_id)).all()) if intent_ids else {}
        eligible = []
        for slot, intent in rows:
            try:
                body = intent.data['configuration']
                if at >= instant(body['expires_at']) or made.get(intent.id, 0) >= body['max_tasks']: continue
            except (HTTPException, KeyError, TypeError, ValueError):
                # Malformed data gets the original integrity gate, never a
                # scheduling-derived success or a silent authority grant.
                pass
            eligible.append(slot)
        eligible_ids = [row.id for row in eligible]
        selected = list(db.scalars(select(Slot).where(Slot.workspace_id == workspace_id,
            Slot.id.in_(eligible_ids), Slot.task_id.is_(None), Slot.status.in_(['waiting_data', 'verifying']))
            .order_by(Slot.last_polled_at.asc().nullsfirst(), Slot.day, Slot.id).limit(50)
            .with_for_update(skip_locked=True, of=Slot).execution_options(populate_existing=True))) if eligible_ids else []
        ids = [row.id for row in selected]
        for row in selected: row.last_polled_at = at
        # Persist only the fair cursor. Do not hold Slot locks while acquiring
        # the ordinary Instance -> Intent -> Slot -> current actor gates.
        db.commit()
    created = blocked = 0
    for identity in ids:
        with Session(engine()) as db:
            try: created += int(process_slot(db, workspace_id, identity))
            except (HTTPException, ValueError, KeyError, TypeError, AttributeError, OSError):
                db.rollback(); blocked += 1
    return {'created': created, 'integrity_blocked': blocked}
