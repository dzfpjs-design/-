"""Explicit local delivery recovery; never records, infers or fabricates ACK.

The request, its decision and the prior failed state are an immutable Audit.
Stream markers and Job recovery links prevent a missing Audit becoming a fresh
request. All writes are in the caller's transaction; GET performs no repair.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, StrictInt, StrictStr, ValidationError, field_validator
from sqlalchemy import func, or_, select

from . import audio_stream_delivery as delivery
from . import audio_streams as streams
from . import models as m
from .audio_stream_models import AudioStream
from .contracts import Contract, digest
from .db import session
from .security import Principal, authenticated, require
from .signal_dual_read_session import read_session
from .snapshot_authentication_v141 import snapshot_authenticated

ACTION = 'audio.stream_delivery_recovery_v255'
SCHEMA = 'audio-stream-delivery-recovery-v255'
MARKERS = 'delivery_recovery_requests'
HISTORY = 'delivery_recovery_history'
LIMIT = 1000
ERROR = '原整理恢复申请或独立依据无法核对；原件和已有回执保留'
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


class RecoveryCommand(Contract):
    schema: Literal['audio-stream-delivery-recovery-v255']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    action: Literal['retry_job', 'retry_event', 'enqueue_received', 'enqueue_snapshot']
    subject_id: StrictStr = Field(pattern=UUID_PATTERN)
    version: StrictInt = Field(ge=1)

    @field_validator('request_id')
    @classmethod
    def no_nul(cls, value):
        if '\x00' in value:
            raise ValueError('申请编号不能含 NUL')
        return value


def need(value, message=ERROR, code=409):
    if not value:
        raise HTTPException(code, message)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse(raw):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= 16384, '恢复申请正文无效或过大', 422)
        original = raw.decode('utf-8')
        value = json.loads(original, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        body = RecoveryCommand.model_validate(value).model_dump(mode='json')
        return original, body, hashlib.sha256(raw).hexdigest(), digest(body)
    except HTTPException:
        raise
    except (ValueError, TypeError, UnicodeError, ValidationError):
        raise HTTPException(422, '请保留原申请；恢复正文必须是固定字段、有效编号及整数版本') from None


def _lookup_input(request_id, raw_sha256):
    need(type(request_id) is str and 12 <= len(request_id) <= 100 and '\x00' not in request_id
        and type(raw_sha256) is str and len(raw_sha256) == 64
        and not set(raw_sha256) - set('0123456789abcdef'), '原申请编号或摘要无效', 422)


def _row(db, p, cls, identity, *, lock=False):
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None)
    return row


def _proofs(db, p, stream_id):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.object_id == stream_id).limit(LIMIT + 1)))
    need(len(rows) <= LIMIT, '恢复记录超过本次完整核对上限，请保留原记录联系管理员')
    return rows


def _marker(proof):
    value = proof.data
    return {'audit_id': proof.id, 'request_id': value['request_id'], 'raw_sha256': value['raw_sha256'],
        'body_hash': value['body_hash'], 'receipt_hash': value['receipt']['receipt_hash'],
        'proof_hash': digest(value)}


def _job_link(proof):
    return {'audit_id': proof.id, 'request_id': proof.data['request_id'],
        'stream_id': proof.object_id, 'proof_hash': digest(proof.data)}


def _checked_proof(p, stream, proof):
    value = proof.data
    need(type(value) is dict and set(value) == {'schema', 'workspace_id', 'actor_id', 'instance_id', 'stream_id',
        'request_id', 'original_raw', 'raw_sha256', 'body', 'body_hash', 'receipt', 'previous'})
    try:
        original, body, rh, bh = parse(value['original_raw'].encode('utf-8'))
    except HTTPException:
        raise HTTPException(409, ERROR) from None
    receipt = value['receipt']
    need(value['schema'] == 'audio-stream-delivery-recovery-proof-v255'
        and value['workspace_id'] == stream.workspace_id == p.workspace_id == proof.workspace_id
        and value['actor_id'] == proof.actor_id and value['instance_id'] == stream.instance_id
        and value['stream_id'] == stream.id == proof.object_id and value['request_id'] == body['request_id']
        and value['original_raw'] == original and value['raw_sha256'] == rh and value['body_hash'] == bh
        and digest(value['body']) == digest(body))
    need(type(receipt) is dict and set(receipt) == {'schema', 'workspace_id', 'actor_id', 'instance_id', 'stream_id',
        'request_id', 'raw_sha256', 'body_hash', 'action', 'subject_id', 'version_before', 'version_after',
        'outcome', 'reason', 'job_id', 'event_id', 'recorded_at', 'receipt_hash'}
        and receipt['schema'] == 'audio-stream-delivery-recovery-receipt-v255'
        and all(receipt[key] == value[key] for key in
            ('workspace_id', 'actor_id', 'instance_id', 'stream_id', 'request_id', 'raw_sha256', 'body_hash'))
        and receipt['action'] == body['action'] and receipt['subject_id'] == body['subject_id']
        and receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
        and type(receipt['version_before']) is int and receipt['version_before'] >= 1
        and type(receipt['version_after']) is int and receipt['outcome'] in {'accepted', 'rejected'})
    stamp = datetime.fromisoformat(receipt['recorded_at'])
    need(stamp.tzinfo is not None and proof.created_at is not None
        and stamp == proof.created_at)
    if receipt['outcome'] == 'accepted':
        need(receipt['version_before'] == body['version'] and receipt['reason'] is None
            and receipt['version_after'] == receipt['version_before'] + (1 if body['action'].startswith('retry_') else 0))
    else:
        need(type(receipt['reason']) is str and bool(receipt['reason'])
            and receipt['version_after'] == receipt['version_before'])
    for key in ('job_id', 'event_id'):
        need(receipt[key] is None or str(UUID(receipt[key])) == receipt[key])
    previous = value['previous']
    need(type(previous) is dict and previous.get('subject_id') == body['subject_id'])
    if body['action'] == 'retry_job':
        need(set(previous) == {'subject_id', 'status', 'error', 'attempts', 'available_at', 'lease_token',
            'lease_until', 'job_data_hash', 'queue_audit_id', 'queue_audit_hash'}
            and receipt['job_id'] == body['subject_id'] and receipt['event_id'] is None)
    elif body['action'] == 'retry_event':
        need(set(previous) == {'subject_id', 'status', 'attempts', 'payload_hash', 'producer_audit_id',
            'producer_audit_hash'} and receipt['event_id'] == body['subject_id'] and receipt['job_id'] is None)
    else:
        need(set(previous) == {'subject_id', 'capture_hash'} and receipt['event_id'] is None)
    return receipt


def _ledger(db, p, stream):
    """Both directions, including orphan Job links; never repair either side."""
    try:
        proofs = _proofs(db, p, stream.id)
        markers = stream.data.get(MARKERS, [])
        need(type(markers) is list and len(markers) == len(proofs) <= LIMIT)
        need(len({v.id for v in proofs}) == len(proofs)
            and len({v.data['request_id'] for v in proofs}) == len(proofs))
        expected = {value.id: _marker(value) for value in proofs}
        need(all(type(v) is dict and v.get('audit_id') in expected
            and digest(v) == digest(expected[v['audit_id']]) for v in markers)
            and len({v['audit_id'] for v in markers}) == len(markers))
        for proof in proofs:
            _checked_proof(p, stream, proof)
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
            or_(m.Job.data['stream_id'].as_string() == stream.id,
                m.Job.data[HISTORY].contains([{'stream_id': stream.id}]))).limit(LIMIT + 1)))
        need(len(jobs) <= LIMIT)
        expected_jobs = {}
        for proof in proofs:
            receipt = proof.data['receipt']
            if receipt['action'] == 'retry_job' and receipt['outcome'] == 'accepted':
                expected_jobs.setdefault(receipt['subject_id'], []).append(_job_link(proof))
        actual_jobs = {}
        for job in jobs:
            history = job.data.get(HISTORY, [])
            need(type(history) is list and len(history) <= LIMIT)
            if history:
                need(job.data.get('stream_id') == stream.id and job.kind == delivery.KIND)
                delivery._checked_job(db, p, job)
                need(len({v['audit_id'] for v in history}) == len(history))
                actual_jobs[job.id] = sorted(history, key=lambda v: v['audit_id'])
        need(digest(actual_jobs) == digest({key: sorted(value, key=lambda v: v['audit_id'])
            for key, value in expected_jobs.items()}))
        return proofs
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None


def _original(db, p, instance_id, stream_id, request_id, raw_hash):
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    marked = list(db.scalars(select(AudioStream).where(AudioStream.workspace_id == p.workspace_id,
        AudioStream.data[MARKERS].contains([{'request_id': request_id}])).limit(2)))
    linked = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.data[HISTORY].contains([{'request_id': request_id}])).limit(2)))
    if not proofs and not marked and not linked:
        return None
    need(len(proofs) == 1)
    proof = proofs[0]
    stream = _row(db, p, AudioStream, proof.object_id)
    streams._checked(db, p, stream)
    inventory = _ledger(db, p, stream)
    need(proof.id in {value.id for value in inventory})
    receipt = _checked_proof(p, stream, proof)
    need(len(marked) == 1 and marked[0].id == stream.id)
    expected_links = [receipt['job_id']] if receipt['action'] == 'retry_job' and receipt['outcome'] == 'accepted' else []
    need(sorted(row.id for row in linked) == sorted(expected_links))
    need(proof.actor_id == p.user_id and stream.id == stream_id and stream.instance_id == instance_id,
        '当前账号或实例没有这份原恢复申请', 404)
    need(proof.data['raw_sha256'] == raw_hash, '原恢复申请正文摘要不一致')
    return proof


def lookup(db, p, instance_id, stream_id, request_id, raw_sha256):
    _lookup_input(request_id, raw_sha256)
    stream = streams._read_stream(db, p, instance_id, stream_id)
    _ledger(db, p, stream)
    proof = _original(db, p, instance_id, stream_id, request_id, raw_sha256)
    return {'schema': 'audio-stream-delivery-recovery-lookup-v255', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'stream_id': stream_id,
        'request_id': request_id, 'raw_sha256': raw_sha256, 'found': proof is not None,
        'receipt': copy.deepcopy(proof.data['receipt']) if proof else None,
        'original_raw': proof.data['original_raw'] if proof else None,
        'body_hash': proof.data['body_hash'] if proof else None}


def _issuer_available(db, p, actor_id, *, lock=False):
    query = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == actor_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role.in_(['admin', 'operator']))
    if lock:
        query = query.with_for_update(read=True, of=[m.User, m.Membership])
    return db.execute(query.execution_options(populate_existing=True)).first() is not None


def _now(db):
    value = db.scalar(select(func.clock_timestamp()))
    need(type(value) is datetime and value.tzinfo is not None)
    return value.astimezone(timezone.utc)


def _job_reason(job, current, issuer):
    if not issuer:
        return '原整理执行账号当前已无操作权限；保留原作业，不替换执行人'
    if job.status not in {'failed', 'cancelled'}:
        return '只能重试明确失败或已停止的整理作业；处理中或已完成的作业保持原状'
    if type(job.attempts) is not int or not 0 <= job.attempts < 3:
        return '原作业已达到三次处理上限，或尝试计数无法核对'
    if job.lease_until is not None and (job.lease_until.tzinfo is None or job.lease_until > current):
        return '原作业仍有有效租约，请等待处理结果'
    return None


def _subject(db, p, stream, action, identity, *, lock=False):
    cls = m.Job if action == 'retry_job' else m.Event if action == 'retry_event' else m.Observation
    subject = _row(db, p, cls, identity, lock=lock)
    need(type(subject.version) is int and subject.version >= 1)
    if action == 'retry_job':
        _, _, context = delivery._checked_job(db, p, subject)
        need(context['stream'].id == stream.id)
    elif action == 'retry_event':
        if subject.kind == 'observation.data':
            _, context, _, _, _ = _primary_data_context(db, p, subject)
        else:
            daily = delivery.handles_daily(db, p, subject)
            notice = delivery.handles_event(db, p, subject)
            need(daily != notice)
            _, context = delivery._checked_event(db, p, subject, delivery.DAILY if daily else delivery.NOTIFIED,
                allow_pending=True)
        need(context['stream'].id == stream.id)
    else:
        context = streams.observation_context(db, p, subject, execution=False)
        need(context['stream'].id == stream.id)
    return subject


def _primary_data_context_base(db, p, event):
    """Saved stream input and real SDK result; no current capture permission.

    The primary outbox has no daily/notification producer Audit. Its producer
    anchor is the original independently frozen extraction queue, whose exact
    Capture/Observation/Evidence links are verified by observation_context.
    """
    from .instance_data_receipts import _event_matches
    from .observation_integrity import checked_report
    from .observation_inputs import job_for
    required = {'observation_id', 'entity_id', 'source_id', 'invocation_id', 'extracted',
        'evidence_ids', 'model_receipt', 'candidate_only', 'observed_at'}
    need(event.workspace_id == p.workspace_id and event.kind == 'observation.data'
        and type(event.data) is dict and required <= set(event.data))
    row = _row(db, p, m.Observation, event.data['observation_id'])
    context = streams.observation_context(db, p, row, execution=False)
    need(row.status == 'processed' and row.data.get('candidate_only') is True
        and event.data['candidate_only'] is True and event.data['observed_at'] is None)
    report = checked_report(db, p, row, allow_candidates=True)
    _event_matches(event, row)
    job = job_for(db, p, row)
    need(job is not None and job.workspace_id == p.workspace_id
        and job.id == row.data['audio_stream_extract_job_id'] and job.kind == 'process_observation'
        and job.idempotency_key == 'extract:' + row.id
        and job.data['observation_id'] == row.id
        and job.data['actor_id'] == row.data['submitted_by'] == context['stream'].actor_id
        and job.data['extractor_id'] == row.data['extractor_id'])
    call = _row(db, p, m.Invocation, row.data['invocation_id'])
    need(call.status == 'succeeded' and call.data['job_id'] == job.id
        and call.id == report['invocation_id'] and call.capability_id == row.data['extractor_id']
        and digest(call.data['output']) == digest(row.data['extracted']))
    proofs = delivery._audits(db, p, streams.EXTRACT_QUEUED, job.id)
    need(len(proofs) == 1 and proofs[0].actor_id == context['stream'].actor_id)
    return row, context, job, call, proofs[0]


def _primary_data_context(db, p, event):
    try:
        return _primary_data_context_base(db, p, event)
    except HTTPException as error:
        if error.status_code in {401, 403, 409}:
            raise
        raise HTTPException(409, ERROR) from None
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise HTTPException(409, ERROR) from None


def _primary_data_events(db, p, stream):
    """Find all original primary data Events from the complete capture inventory."""
    values = []
    for row in streams.stream_observations(db, p, stream):
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            or_(m.Event.idempotency_key == 'data:' + row.id,
                (m.Event.kind == 'observation.data') & (m.Event.data['observation_id'].as_string() == row.id))).limit(2)))
        need(len(events) <= 1 and (row.status != 'processed' or len(events) == 1))
        for event in events:
            original, context, _, _, _ = _primary_data_context(db, p, event)
            need(original.id == row.id and context['stream'].id == stream.id)
            values.append(event)
    return values


def _no_receiver_ack(db, p, event, *, observation=None):
    condition = m.Memory.data['event_id'].as_string() == event.id
    if observation is not None:
        condition = or_(condition, (m.Memory.data['observation_id'].as_string() == observation.id)
            & (m.Memory.data['type'].as_string() == 'observation_evidence'))
        need('data_reception' not in event.data and 'data_reception' not in observation.data)
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id, condition).limit(1)))
    signals = list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id == p.workspace_id,
        m.PerceptionSignal.event_id == event.id).limit(1)))
    completed = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == delivery.RECEIVED, m.Audit.data['event_id'].as_string() == event.id).limit(1)))
    need(not memories and not signals and not completed and event.received_at is None)


def _event_reason(db, p, event):
    if event.kind == 'observation.data':
        row, _, job, _, _ = _primary_data_context(db, p, event)
        if job.status != 'succeeded':
            return '原声音分析作业尚未成功终结，不能重投数据；原结果和作业保留'
        if event.status != 'blocked_delivery':
            return '只重试明确受阻的原数据送达；待投递或已接收的记录保持原状'
        _no_receiver_ack(db, p, event, observation=row)
        return None
    if delivery.event_delivery_state(db, p, event) != 'ready':
        return '原整理作业尚未完成，不能先投递每日记录或通知'
    if delivery.handles_daily(db, p, event):
        delivery.validate_daily(db, event)
    else:
        delivery._basis(db, p, event)
    if event.status != 'blocked_delivery':
        return '只重试明确受阻的原投递；待投递、处理中或已接收记录保持原状'
    # A partial/orphan receiver receipt is not permission to re-deliver it.
    _no_receiver_ack(db, p, event)
    return None


def _existing_queue(db, p, stream, row, action, *, items=None):
    if action == 'enqueue_received':
        key = delivery.JOB_PREFIX + row.id
    else:
        inventory = delivery._inventory(db, p, stream) if items is None else items
        revision = digest({'stream_status': stream.status, 'items': inventory})
        key = delivery.JOB_PREFIX + 'inventory:' + stream.id + ':' + revision
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.idempotency_key == key).limit(2)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == delivery.ENQUEUED, m.Audit.data['key'].as_string() == key).limit(2)))
    if not jobs and not proofs:
        return None
    need(len(jobs) == len(proofs) == 1 and jobs[0].id == proofs[0].object_id)
    delivery._checked_job(db, p, jobs[0])
    return jobs[0]


def _previous(db, p, action, subject):
    if action == 'retry_job':
        audits = delivery._audits(db, p, delivery.ENQUEUED, subject.id)
        need(len(audits) == 1)
        return {'subject_id': subject.id, 'status': subject.status, 'error': copy.deepcopy(subject.data.get('error')),
            'attempts': subject.attempts, 'available_at': subject.available_at.isoformat() if subject.available_at else None,
            'lease_token': subject.lease_token, 'lease_until': subject.lease_until.isoformat() if subject.lease_until else None,
            'job_data_hash': digest(subject.data), 'queue_audit_id': audits[0].id, 'queue_audit_hash': digest(audits[0].data)}
    if action == 'retry_event':
        if subject.kind == 'observation.data':
            _, _, _, _, producer = _primary_data_context(db, p, subject)
        else:
            action_name = delivery.DAILY if delivery.handles_daily(db, p, subject) else delivery.NOTIFIED
            audits = delivery._audits(db, p, action_name, subject.id)
            need(len(audits) == 1)
            producer = audits[0]
        return {'subject_id': subject.id, 'status': subject.status, 'attempts': subject.attempts,
            'payload_hash': digest(subject.data), 'producer_audit_id': producer.id, 'producer_audit_hash': digest(producer.data)}
    context = streams.observation_context(db, p, subject, execution=False)
    return {'subject_id': subject.id, 'capture_hash': digest(context['capture'])}


def _write_scope(db, p, instance_id, stream_id):
    _row(db, p, m.DomainInstance, instance_id, lock=True)
    stream = _row(db, p, AudioStream, stream_id, lock=True)
    need(stream.instance_id == instance_id, '该实例没有这份音轨申请', 404)
    need(stream.actor_id == p.user_id or p.role == 'admin', '仅原申请人或管理员可以恢复这份整理与投递', 403)
    streams._checked(db, p, stream)
    _ledger(db, p, stream)
    return stream


def recover(db, p, instance_id, stream_id, raw):
    original, body, rh, bh = parse(raw)
    streams._read_stream(db, p, instance_id, stream_id)
    prior = _original(db, p, instance_id, stream_id, body['request_id'], rh)
    if prior:
        return {'schema': 'audio-stream-delivery-recovery-result-v255', 'receipt': copy.deepcopy(prior.data['receipt'])}
    require(p, 'admin', 'operator')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    streams._nonce(db, p, body['request_id'])
    prior = _original(db, p, instance_id, stream_id, body['request_id'], rh)
    if prior:
        return {'schema': 'audio-stream-delivery-recovery-result-v255', 'receipt': copy.deepcopy(prior.data['receipt'])}
    # Lock the original delivery actor before Instance/Stream/Job; recovery never
    # replaces a revoked issuer with the requesting administrator.
    original_stream = _row(db, p, AudioStream, stream_id)
    issuer = _issuer_available(db, p, original_stream.actor_id, lock=True)
    stream = _write_scope(db, p, instance_id, stream_id)
    subject = _subject(db, p, stream, body['action'], body['subject_id'], lock=True)
    before = subject.version; current = _now(db)
    previous = _previous(db, p, body['action'], subject)
    reason = '记录版本已变化，请保留原申请，读取当前版本后明确重新拟定' if before != body['version'] else None
    job_id = subject.id if body['action'] == 'retry_job' else None
    event_id = subject.id if body['action'] == 'retry_event' else None
    if reason is None:
        if body['action'] == 'retry_job':
            reason = _job_reason(subject, current, issuer)
            if reason is None:
                subject.status = 'queued'; subject.available_at = current
                subject.lease_until = None; subject.lease_token = None; subject.version += 1
        elif body['action'] == 'retry_event':
            reason = _event_reason(db, p, subject)
            if reason is None:
                subject.status = 'pending_delivery'; subject.version += 1
        else:
            if not issuer:
                reason = '原整理执行账号当前已无操作权限；不会另换执行人创建整理作业'
            elif _existing_queue(db, p, stream, subject, body['action']) is not None:
                reason = '已有这份原整理作业，请核对其结果；失败时重试原作业，不另建重复队列'
            elif body['action'] == 'enqueue_received':
                item, _ = delivery._item(db, p, subject)
                if item['state'] != 'received':
                    reason = '这段原音尚未由数据管理智能体独立接收，不能补排已收件整理'
                else:
                    job_id = delivery.enqueue_received(db, p, subject).id
            else:
                job_id = delivery.enqueue_snapshot(db, p, stream, subject.id).id
    receipt = {'schema': 'audio-stream-delivery-recovery-receipt-v255', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'stream_id': stream_id, 'request_id': body['request_id'],
        'raw_sha256': rh, 'body_hash': bh, 'action': body['action'], 'subject_id': subject.id,
        'version_before': before, 'version_after': subject.version, 'outcome': 'rejected' if reason else 'accepted',
        'reason': reason, 'job_id': job_id, 'event_id': event_id, 'recorded_at': current.isoformat()}
    receipt['receipt_hash'] = digest(receipt)
    proof = m.Audit(id=m.uid(), created_at=current, workspace_id=p.workspace_id, actor_id=p.user_id, action=ACTION,
        object_id=stream.id, data={'schema': 'audio-stream-delivery-recovery-proof-v255', 'workspace_id': p.workspace_id,
            'actor_id': p.user_id, 'instance_id': instance_id, 'stream_id': stream_id, 'request_id': body['request_id'],
            'original_raw': original, 'raw_sha256': rh, 'body': body, 'body_hash': bh,
            'receipt': copy.deepcopy(receipt), 'previous': previous})
    need(len(stream.data.get(MARKERS, [])) < LIMIT, '恢复记录已达到完整核对上限')
    if body['action'] == 'retry_job' and reason is None:
        subject.data = {**subject.data, HISTORY: [*copy.deepcopy(subject.data.get(HISTORY, [])), _job_link(proof)]}
    stream.data = {**stream.data, MARKERS: [*copy.deepcopy(stream.data.get(MARKERS, [])), _marker(proof)]}
    stream.version += 1
    db.add(proof); db.flush()
    return {'schema': 'audio-stream-delivery-recovery-result-v255', 'receipt': receipt}


def options(db, p, instance_id, stream_id):
    stream = streams._read_stream(db, p, instance_id, stream_id)
    result = {'schema': 'audio-stream-delivery-recovery-options-v255', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'stream_id': stream_id,
        'options': [], 'integrity_issue': None}
    try:
        _ledger(db, p, stream)
        view = delivery.summary(db, p, stream)
        need(view['integrity_issue'] is None)
        current = _now(db); issuer = _issuer_available(db, p, stream.actor_id)
        access = None if p.role in {'admin', 'operator'} and (stream.actor_id == p.user_id or p.role == 'admin') else '当前账号可以核对历史，无权恢复这份整理与投递'
        values = []
        for record in view['delivery_jobs']:
            if record['job_id'] is None:
                continue
            job = _subject(db, p, stream, 'retry_job', record['job_id'])
            reason = access or _job_reason(job, current, issuer)
            values.append({'action': 'retry_job', 'subject_id': job.id, 'version': job.version,
                'available': reason is None, 'reason': reason, 'label': '重试原整理作业', 'attempts': job.attempts, 'limit': 3})
        for record in [*view['daily_records'], *view['notifications']]:
            event = _subject(db, p, stream, 'retry_event', record['event_id'])
            reason = access or _event_reason(db, p, event)
            values.append({'action': 'retry_event', 'subject_id': event.id, 'version': event.version,
                'available': reason is None, 'reason': reason, 'label': '重新投递原记录或通知', 'attempts': event.attempts, 'limit': None})
        for event in _primary_data_events(db, p, stream):
            reason = access or _event_reason(db, p, event)
            values.append({'action': 'retry_event', 'subject_id': event.id, 'version': event.version,
                'available': reason is None, 'reason': reason, 'label': '重试原数据送达', 'attempts': event.attempts, 'limit': None})
        for item in view['items']:
            row = _subject(db, p, stream, 'enqueue_snapshot', item['observation_id'])
            for action, label in [('enqueue_received', '补排已接收成果整理'), ('enqueue_snapshot', '整理当前完整片段状态')]:
                reason = access or (None if issuer else '原整理执行账号当前已无操作权限')
                if action == 'enqueue_received' and reason is None and item['state'] != 'received':
                    reason = '尚未独立接收，不可作为已收件成果整理'
                if reason is None and _existing_queue(db, p, stream, row, action, items=view['items']) is not None:
                    reason = '已有原整理作业，请核对其结果或重试原作业'
                values.append({'action': action, 'subject_id': row.id, 'version': row.version,
                    'available': reason is None, 'reason': reason, 'label': label, 'attempts': None, 'limit': None})
        result['options'] = values
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError, OSError):
        result['integrity_issue'] = ERROR
    return result


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
BASE = '/api/workspace/instances/{instance_id}/audio-streams/{stream_id}'


@router.get(BASE + '/delivery-recovery')
def recovery_options(instance_id: str, stream_id: str, p: ReadP, db: ReadDB):
    return options(db, p, instance_id, stream_id)


@router.post(BASE + '/delivery-recovery')
async def recovery_command(instance_id: str, stream_id: str, request: Request, p: P, db: DB):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        need(len(raw) <= 16384, '恢复申请正文过大', 422)
    value = recover(db, p, instance_id, stream_id, bytes(raw))
    db.commit()
    return value


@router.get(BASE + '/delivery-requests')
def recovery_lookup(instance_id: str, stream_id: str, request_id: str, raw_sha256: str, p: ReadP, db: ReadDB):
    return lookup(db, p, instance_id, stream_id, request_id, raw_sha256)
