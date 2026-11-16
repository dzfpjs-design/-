"""Independent data-ACK to immutable audio day versions and candidate notices.

This family owns no capture, inference, Task, object-state update or automatic
response. Pending items contain provenance only. Old day versions retain their
then-current states while later captures, results and ACKs may append versions.
"""
import copy
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, func, or_, text

from . import models as m
from .contracts import digest
from .services import enqueue_event
from .security import Principal

KIND = 'refresh_audio_stream_delivery_v255'
JOB_SCHEMA = 'audio-stream-delivery-job-v255'
ENQUEUED = 'audio.stream_delivery_queued_v255'
DAILY = 'audio.stream_daily_frozen_v255'
NOTIFIED = 'audio.stream_candidate_issued_v255'
RECEIVED = 'audio.stream_candidate_received_v255'
BLOCKED = 'audio.stream_delivery_blocked_v255'
DAILY_SCHEMA = 'gateway-audio-daily-v255'
CANDIDATE_SCHEMA = 'gateway-audio-candidate-v255'
DAY_PREFIX = 'audio-stream-daily:'
CANDIDATE_PREFIX = 'audio-stream-candidate:'
JOB_PREFIX = 'audio-stream-delivery:'
CHINA = ZoneInfo('Asia/Shanghai')
OPEN = {'queued', 'recording', 'reconnecting', 'paused', 'blocked'}
ERROR = '原音轨、独立收件或整理通知依据无法核对；原件与已有回执保留'
FROZEN = ('schema', 'mode', 'actor_id', 'queued_by', 'stream_id', 'observation_id', 'entity_id',
    'instance_id', 'release_id', 'business_id', 'binding_hash', 'capture_hash', 'result_hash', 'reception_hash', 'inventory_hash')
ITEM_KEYS = {'observation_id', 'capture_hash', 'binding_hash', 'evidence', 'registered_at', 'registration_day',
    'model_capability_id', 'model_capability_hash', 'processing_status', 'state', 'invocation_id', 'result_hash',
    'threshold', 'analysis_windows', 'candidate_classes', 'candidate_windows', 'candidates', 'reception',
    'candidate_only', 'source_clock_utc', 'field_truth_verified'}


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def _row(db, p, cls, identity):
    value = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(value is not None)
    return value


def _audits(db, p, action, identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(102)))


def _audit(db, p, action, identity, body):
    value = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action,
        object_id=identity, data=copy.deepcopy(body))
    db.add(value); db.flush()
    return value


def _base(db, p, row):
    from .audio_streams import observation_context
    context = observation_context(db, p, row, execution=False)
    need(type(context) is dict and set(context) == {'stream', 'binding', 'capture', 'evidence'})
    stream, binding, capture, evidence = (context[k] for k in ('stream', 'binding', 'capture', 'evidence'))
    stamp = datetime.fromisoformat(capture['registered_at'])
    need(stamp.tzinfo is not None and str(stamp.astimezone(CHINA).date()) == capture['registration_day']
        and row.workspace_id == stream.workspace_id == p.workspace_id
        and capture['observation_id'] == row.id and capture['stream_id'] == stream.id
        and capture['entity_id'] == row.entity_id and capture['evidence_id'] == evidence.id
        and row.source_id == binding['source_capability_id']
        and row.data['extractor_id'] == binding['model_capability_id']
        and capture['binding_hash'] == binding['binding_hash']
        and capture['input_sha256'] == evidence.sha256 and capture['size_bytes'] == evidence.size_bytes
        and capture['source_clock_utc'] == 'unknown' and capture['candidate_only'] is True
        and row.data.get('candidate_only') is True and row.observed_at is None)
    return context


def _data_event(db, p, row):
    rows = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + row.id).limit(2)))
    need(len(rows) <= 1)
    return rows[0] if rows else None


def _ack(db, p, row, *, required=False):
    from .instance_data_receipts import observation_receipt
    event = _data_event(db, p, row)
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        or_(m.Memory.data['event_id'].as_string() == (event.id if event else '__missing__'),
            m.Memory.data['observation_id'].as_string() == row.id),
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))
    marked = ('data_reception' in row.data or event is not None and 'data_reception' in event.data or memories)
    if event is None or event.status != 'received':
        need(not required and not marked, '音轨尚待独立收件或原收件凭据不完整，未采用候选数值')
        return None
    value = observation_receipt(db, p, row)
    receipt = value.get('data_reception') if type(value) is dict else None
    need(type(receipt) is dict and receipt.get('schema') == 'instance-data-reception-receipt-v1'
        and receipt.get('candidate_only') is True and receipt.get('declaration_status') == 'declared'
        and event.received_at is not None and len(memories) == 1)
    return {'event_id': event.id, 'memory_id': memories[0].id, 'receipt_hash': receipt['receipt_hash'],
        'received_at': event.received_at.isoformat()}


def _report(db, p, row):
    from .observation_integrity import checked_report
    report = checked_report(db, p, row, allow_candidates=True)
    output = report['extracted']
    need(type(output) is dict and set(output) == {'attributes', 'anomalies', 'environment_sound'}
        and type(output['attributes']) is dict and not output['attributes']
        and type(output['anomalies']) is list and not output['anomalies'])
    result = output['environment_sound']; classification = result['classification']
    need(result['schema'] == 'local-environment-sound-v1' and result['cloud_calls'] == 0
        and classification['inference_performed'] is True and classification['all_samples_covered'] is True
        and classification['asr_used'] is False and classification['vad_used'] is False)
    return result


def _item(db, p, row):
    context = _base(db, p, row); capture = context['capture']; binding = context['binding']; evidence = context['evidence']
    need(row.status in {'received', 'queued', 'running', 'processed', 'failed', 'cancelled'})
    value = {'observation_id': row.id, 'capture_hash': digest(capture), 'binding_hash': binding['binding_hash'],
        'evidence': {'id': evidence.id, 'sha256': evidence.sha256, 'size_bytes': evidence.size_bytes, 'media_type': evidence.media_type},
        'registered_at': capture['registered_at'], 'registration_day': capture['registration_day'],
        'model_capability_id': binding['model_capability_id'], 'model_capability_hash': binding['model_capability_hash'],
        'processing_status': row.status, 'state': 'failed' if row.status in {'failed', 'cancelled'} else 'processing',
        'invocation_id': None, 'result_hash': None, 'threshold': None, 'analysis_windows': None,
        'candidate_classes': None, 'candidate_windows': None, 'candidates': None, 'reception': None,
        'candidate_only': True, 'source_clock_utc': 'unknown', 'field_truth_verified': False}
    if row.status == 'processed':
        result = _report(db, p, row); reception = _ack(db, p, row)
        value['state'] = 'received' if reception else 'waiting_data'
        if reception:
            selected = result['classification']; candidates = selected['candidates']
            value.update(invocation_id=row.data['invocation_id'], result_hash=digest(result), threshold=selected['threshold'],
                analysis_windows=len(selected['windows']), candidate_classes=len(candidates),
                candidate_windows=len({index for candidate in candidates for index in candidate['window_indices']}),
                candidates=copy.deepcopy(candidates), reception=reception)
    else:
        need(_ack(db, p, row) is None)
    return value, context


def _inventory(db, p, stream):
    from .audio_streams import stream_observations
    rows = stream_observations(db, p, stream)
    need(type(rows) is list and len(rows) <= 100 and len({row.id for row in rows}) == len(rows))
    values = []
    for row in rows:
        item, context = _item(db, p, row)
        need(context['stream'].id == stream.id)
        values.append(item)
    return sorted(values, key=lambda item: (item['registered_at'], item['observation_id']))


def _counts(items):
    return {'captured_records': len(items), 'processing': sum(i['state'] == 'processing' for i in items),
        'failed': sum(i['state'] == 'failed' for i in items), 'waiting_data': sum(i['state'] == 'waiting_data' for i in items),
        'received': sum(i['state'] == 'received' for i in items),
        'analysis_windows': sum(i['analysis_windows'] for i in items if i['state'] == 'received'),
        'candidate_classes': sum(i['candidate_classes'] for i in items if i['state'] == 'received'),
        'candidate_windows': sum(i['candidate_windows'] for i in items if i['state'] == 'received')}


def _queue_anchor(job):
    return {'schema': 'audio-stream-delivery-queued-proof-v255', 'job_id': job.id,
        'workspace_id': job.workspace_id, 'key': job.idempotency_key,
        'frozen': {key: copy.deepcopy(job.data[key]) for key in FROZEN}}


def _checked_job(db, p, job):
    need(job.workspace_id == p.workspace_id and job.kind == KIND and job.data['schema'] == JOB_SCHEMA
        and job.data['mode'] in {'received', 'inventory'})
    proofs = _audits(db, p, ENQUEUED, job.id)
    need(len(proofs) == 1 and proofs[0].actor_id == job.data['queued_by']
        and digest(proofs[0].data) == digest(_queue_anchor(job)))
    row = _row(db, p, m.Observation, job.data['observation_id']); context = _base(db, p, row)
    item = None
    need(context['stream'].actor_id == job.data['actor_id']
        and context['stream'].id == job.data['stream_id'] and row.entity_id == job.data['entity_id']
        and all(context['capture'][key] == job.data[key] for key in ('instance_id', 'release_id', 'business_id'))
        and digest(context['capture']) == job.data['capture_hash']
        and context['binding']['binding_hash'] == job.data['binding_hash'])
    if job.data['mode'] == 'received':
        item, _ = _item(db, p, row)
        need(item['state'] == 'received' and job.idempotency_key == JOB_PREFIX + row.id
            and item['result_hash'] == job.data['result_hash']
            and item['reception']['receipt_hash'] == job.data['reception_hash'] and job.data['inventory_hash'] is None)
    else:
        inventory_hash = job.data['inventory_hash']
        need(type(inventory_hash) is str and len(inventory_hash) == 64 and not set(inventory_hash) - set('0123456789abcdef')
            and job.data['result_hash'] is None and job.data['reception_hash'] is None
            and job.idempotency_key == JOB_PREFIX + 'inventory:' + context['stream'].id + ':' + inventory_hash)
    return row, item, context


def _queue_lock(db, p, key):
    number = int(digest({'workspace_id': p.workspace_id, 'audio_delivery_key': key})[:16], 16) - (1 << 63)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': number})


def enqueue_received(db, p, row):
    """ACK transaction hook; queues work but acquires no new capture authority."""
    from .audio_streams import observation_marker
    if not observation_marker(db, p, row):
        return None
    item, context = _item(db, p, row); need(item['state'] == 'received')
    key = JOB_PREFIX + row.id
    _queue_lock(db, p, key)
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key).limit(2)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ENQUEUED, m.Audit.data['frozen']['observation_id'].as_string() == row.id,
        m.Audit.data['frozen']['mode'].as_string() == 'received').limit(2)))
    if jobs or proofs:
        need(len(jobs) == len(proofs) == 1 and proofs[0].object_id == jobs[0].id)
        _checked_job(db, p, jobs[0]); return jobs[0]
    capture = context['capture']
    data = {'schema': JOB_SCHEMA, 'mode': 'received', 'actor_id': context['stream'].actor_id, 'queued_by': p.user_id,
        'stream_id': context['stream'].id, 'observation_id': row.id, 'entity_id': row.entity_id,
        **{key: capture[key] for key in ('instance_id', 'release_id', 'business_id')},
        'binding_hash': item['binding_hash'], 'capture_hash': item['capture_hash'], 'result_hash': item['result_hash'],
        'reception_hash': item['reception']['receipt_hash'], 'inventory_hash': None}
    job = m.Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key=key, data=data)
    db.add(job); db.flush(); _audit(db, p, ENQUEUED, job.id, _queue_anchor(job))
    return job


def enqueue_snapshot(db, p, stream, trigger_observation_id):
    """Freeze an inventory revision trigger, without adopting unreceived values."""
    row = _row(db, p, m.Observation, trigger_observation_id)
    context = _base(db, p, row); need(context['stream'].id == stream.id)
    items = _inventory(db, p, stream); need(any(item['observation_id'] == row.id for item in items))
    inventory_hash = digest({'stream_status': stream.status, 'items': items})
    key = JOB_PREFIX + 'inventory:' + stream.id + ':' + inventory_hash
    _queue_lock(db, p, key)
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key).limit(2)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ENQUEUED, m.Audit.data['key'].as_string() == key).limit(2)))
    if jobs or proofs:
        need(len(jobs) == len(proofs) == 1 and proofs[0].object_id == jobs[0].id)
        _checked_job(db, p, jobs[0]); return jobs[0]
    capture = context['capture']
    data = {'schema': JOB_SCHEMA, 'mode': 'inventory', 'actor_id': stream.actor_id, 'queued_by': p.user_id,
        'stream_id': stream.id, 'observation_id': row.id, 'entity_id': row.entity_id,
        **{key: capture[key] for key in ('instance_id', 'release_id', 'business_id')},
        'binding_hash': context['binding']['binding_hash'], 'capture_hash': digest(capture),
        'result_hash': None, 'reception_hash': None, 'inventory_hash': inventory_hash}
    job = m.Job(workspace_id=p.workspace_id, kind=KIND, idempotency_key=key, data=data)
    db.add(job); db.flush(); _audit(db, p, ENQUEUED, job.id, _queue_anchor(job))
    return job


def _owned(db, p, job):
    from .leases import ownership, LeaseLost
    lease = ownership(db)
    actual = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == job.id,
        m.Job.status == 'running', m.Job.lease_token == lease.get('lease_token'),
        m.Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    if (actual is None or not actual.lease_token or actual.lease_token != lease.get('lease_token')
            or lease.get('job_id') != job.id or job.data['actor_id'] != p.user_id):
        raise LeaseLost('音轨整理原作业租约或执行账号已变化')


def _event_anchor(event, job):
    return {'schema': 'audio-stream-delivery-event-proof-v255', 'event_id': event.id,
        'workspace_id': event.workspace_id, 'key': event.idempotency_key, 'job_id': job.id, 'mode': job.data['mode'],
        'payload_hash': digest(event.data)}


def _issue(db, p, job, kind, key, payload, action):
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == key).limit(2)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.data['key'].as_string() == key).limit(2)))
    if events or proofs:
        need(len(events) == len(proofs) == 1 and proofs[0].object_id == events[0].id
            and digest(events[0].data) == digest(payload))
        _checked_event(db, p, events[0], action, allow_running_job=job.id, allow_pending=True)
        return events[0]
    event = enqueue_event(db, p, kind, key, payload)
    _audit(db, p, action, event.id, _event_anchor(event, job))
    return event


def process(db, p, job):
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    _owned(db, p, job)
    row, item, context = _checked_job(db, p, job)
    need(p.user_id == job.data['actor_id'])
    stream, capture, binding = context['stream'], context['capture'], context['binding']
    inventory = _inventory(db, p, stream)
    need(any(value['observation_id'] == row.id for value in inventory))
    days = sorted({value['registration_day'] for value in inventory}) if job.data['mode'] == 'inventory' else [capture['registration_day']]
    daily_ids = []; revisions = {}; primary = None
    for registration_day in days:
        items = [value for value in inventory if value['registration_day'] == registration_day]
        need(items)
        day = {'schema': DAILY_SCHEMA, 'workspace_id': p.workspace_id, 'stream_id': stream.id,
            **{key: capture[key] for key in ('instance_id', 'release_id', 'business_id', 'entity_id')},
            'source_id': row.source_id, 'producer_actor_id': p.user_id,
            'observation_id': min(value['observation_id'] for value in items), 'registration_day': registration_day,
            'stream_status': stream.status, 'items': items, 'counts': _counts(items),
            'candidate_only': True, 'source_clock_utc': 'unknown', 'date_basis': 'audio_original_registered',
            'not_authoritative': True, 'entity_state_updated': False}
        day['revision_hash'] = digest(day)
        daily = _issue(db, p, job, 'perception.daily', DAY_PREFIX + stream.id + ':' + registration_day + ':' + day['revision_hash'], day, DAILY)
        daily_ids.append(daily.id); revisions[registration_day] = day['revision_hash']
        if registration_day == capture['registration_day']: primary = daily.id
    need(primary is not None)
    notification = None
    if job.data['mode'] == 'received' and item['candidate_classes']:
        value = {'schema': CANDIDATE_SCHEMA, 'workspace_id': p.workspace_id, 'stream_id': stream.id,
            **{key: capture[key] for key in ('instance_id', 'release_id', 'business_id', 'entity_id')},
            'producer_actor_id': p.user_id, 'source_id': row.source_id, 'item': item,
            'observation_id': row.id, 'binding_hash': binding['binding_hash'],
            'candidate_only': True, 'notification_only': True, 'source_clock_utc': 'unknown',
            'not_authoritative': True, 'entity_state_updated': False}
        notification = _issue(db, p, job, 'perception.audio_candidate', CANDIDATE_PREFIX + row.id, value, NOTIFIED)
    return {'schema': 'audio-stream-delivery-result-v255', 'observation_id': row.id,
        'daily_event_id': primary, 'daily_event_ids': daily_ids, 'candidate_event_id': notification.id if notification else None,
        'revision_hash': revisions[capture['registration_day']], 'revision_hashes': revisions,
        'candidate_only': True, 'task_created': False}


def _handles(db, p, event, schema, prefix, action):
    return (event.data.get('schema') == schema or event.idempotency_key.startswith(prefix)
        or bool(_audits(db, p, action, event.id)))


def handles_daily(db, p, event):
    return _handles(db, p, event, DAILY_SCHEMA, DAY_PREFIX, DAILY)


def handles_event(db, p, event):
    return _handles(db, p, event, CANDIDATE_SCHEMA, CANDIDATE_PREFIX, NOTIFIED)


def _checked_event(db, p, event, action, *, allow_running_job=None, allow_pending=False):
    daily = action == DAILY
    need(event.workspace_id == p.workspace_id and event.kind == ('perception.daily' if daily else 'perception.audio_candidate')
        and event.data.get('schema') == (DAILY_SCHEMA if daily else CANDIDATE_SCHEMA))
    proofs = _audits(db, p, action, event.id); need(len(proofs) == 1)
    job = _row(db, p, m.Job, proofs[0].data['job_id']); _, _, context = _checked_job(db, p, job)
    need(proofs[0].actor_id == job.data['actor_id'] == event.data['producer_actor_id']
        and digest(proofs[0].data) == digest(_event_anchor(event, job))
        and (job.status == 'succeeded' or allow_pending and job.status in {'queued', 'running'}
            or allow_running_job == job.id and job.status == 'running'))
    need(event.data['stream_id'] == context['stream'].id
        and all(event.data[key] == context['capture'][key] for key in ('instance_id', 'release_id', 'business_id', 'entity_id'))
        and event.data['source_id'] == context['binding']['source_capability_id'])
    need(daily or job.data['mode'] == 'received')
    return job, context


def event_delivery_state(db, p, event):
    action = DAILY if handles_daily(db, p, event) else NOTIFIED
    proofs = _audits(db, p, action, event.id); need(len(proofs) == 1)
    job = _row(db, p, m.Job, proofs[0].data['job_id'])
    if job.status in {'queued', 'running'}:
        _checked_event(db, p, event, action, allow_pending=True)
        return 'pending_producer'
    _checked_event(db, p, event, action)
    return 'ready'


def _verify_item(db, p, frozen, rows):
    need(type(frozen) is dict and set(frozen) == ITEM_KEYS)
    row = rows.get(frozen['observation_id']); need(row is not None)
    context = _base(db, p, row)
    capture, evidence, binding = context['capture'], context['evidence'], context['binding']
    need(frozen['capture_hash'] == digest(capture) and frozen['binding_hash'] == binding['binding_hash']
        and frozen['registered_at'] == capture['registered_at'] and frozen['registration_day'] == capture['registration_day']
        and digest(frozen['evidence']) == digest({'id': evidence.id, 'sha256': evidence.sha256,
            'size_bytes': evidence.size_bytes, 'media_type': evidence.media_type})
        and frozen['model_capability_id'] == binding['model_capability_id']
        and frozen['model_capability_hash'] == binding['model_capability_hash']
        and frozen['candidate_only'] is True and frozen['source_clock_utc'] == 'unknown'
        and frozen['field_truth_verified'] is False)
    if frozen['state'] == 'received':
        current, _ = _item(db, p, row); need(digest(current) == digest(frozen))
    else:
        need(frozen['state'] in {'processing', 'failed', 'waiting_data'}
            and all(frozen[key] is None for key in ('invocation_id', 'result_hash', 'threshold', 'analysis_windows',
                'candidate_classes', 'candidate_windows', 'candidates', 'reception')))
        expected_statuses = {'processing': {'received', 'queued', 'running'},
            'failed': {'failed', 'cancelled'}, 'waiting_data': {'processed'}}
        need(frozen['processing_status'] in expected_statuses[frozen['state']])
    return context


def validate_daily(db, event):
    p = Principal(event.data.get('producer_actor_id', ''), event.workspace_id, 'viewer', 'audio-daily-receiver')
    job, context = _checked_event(db, p, event, DAILY)
    value = event.data; items = value['items']
    need(set(value) == {'schema', 'workspace_id', 'stream_id', 'instance_id', 'release_id', 'business_id', 'entity_id',
        'source_id', 'producer_actor_id', 'observation_id', 'registration_day', 'stream_status', 'items', 'counts',
        'candidate_only', 'source_clock_utc', 'date_basis', 'not_authoritative', 'entity_state_updated', 'revision_hash'}
        and value['workspace_id'] == p.workspace_id and value['candidate_only'] is True
        and value['source_clock_utc'] == 'unknown' and value['date_basis'] == 'audio_original_registered'
        and value['not_authoritative'] is True and value['entity_state_updated'] is False
        and value['stream_status'] in OPEN | {'stopped', 'completed', 'failed'})
    need(type(items) is list and 1 <= len(items) <= 100 and len({i['observation_id'] for i in items}) == len(items))
    from .audio_streams import stream_observations
    rows = {row.id: row for row in stream_observations(db, p, context['stream'])}
    for item in items:
        checked = _verify_item(db, p, item, rows)
        need(checked['stream'].id == value['stream_id'] and item['registration_day'] == value['registration_day']
            and all(checked['capture'][key] == value[key] for key in ('instance_id', 'release_id', 'business_id', 'entity_id')))
    need((job.data['mode'] == 'inventory' or any(item['observation_id'] == context['capture']['observation_id']
             and item['state'] == 'received' for item in items))
        and value['observation_id'] == min(item['observation_id'] for item in items))
    need(digest(value['counts']) == digest(_counts(items))
        and value['revision_hash'] == digest({k: v for k, v in value.items() if k != 'revision_hash'})
        and event.idempotency_key == DAY_PREFIX + value['stream_id'] + ':' + value['registration_day'] + ':' + value['revision_hash'])
    return copy.deepcopy(value)


def _reference(event):
    value = event.data; item = value['item']
    path = '/api/workspace/instances/' + value['instance_id'] + '/audio-streams/' + value['stream_id'] + '/captures/' + item['observation_id']
    return {'schema': 'gateway-audio-candidate-reference-v255', 'stream_id': value['stream_id'],
        'observation_id': item['observation_id'], 'evidence_id': item['evidence']['id'],
        'input_sha256': item['evidence']['sha256'], 'size_bytes': item['evidence']['size_bytes'], 'media_type': item['evidence']['media_type'],
        'result_hash': item['result_hash'], 'invocation_id': item['invocation_id'],
        'model_capability_id': item['model_capability_id'], 'model_capability_hash': item['model_capability_hash'],
        'threshold': item['threshold'], 'candidate_classes': item['candidate_classes'],
        'candidate_windows': item['candidate_windows'], 'candidates': copy.deepcopy(item['candidates']),
        'registration_day': item['registration_day'], 'source_clock_utc': 'unknown',
        'capture_path': path, 'content_path': path + '/content',
        'candidate_only': True, 'notification_only': True, 'field_truth_verified': False}


def _basis(db, p, event):
    job, context = _checked_event(db, p, event, NOTIFIED)
    row = _row(db, p, m.Observation, event.data['observation_id']); item, source = _item(db, p, row)
    need(set(event.data) == {'schema', 'workspace_id', 'stream_id', 'instance_id', 'release_id', 'business_id', 'entity_id',
        'producer_actor_id', 'source_id', 'item', 'observation_id', 'binding_hash', 'candidate_only', 'notification_only',
        'source_clock_utc', 'not_authoritative', 'entity_state_updated'}
        and event.data['workspace_id'] == p.workspace_id and event.data['candidate_only'] is True
        and event.data['source_clock_utc'] == 'unknown' and event.data['not_authoritative'] is True
        and event.data['entity_state_updated'] is False
        and digest(event.data['item']) == digest(item) and item['state'] == 'received' and item['candidate_classes'] > 0
        and source['stream'].id == event.data['stream_id'] == context['stream'].id
        and event.data['binding_hash'] == item['binding_hash'] and event.idempotency_key == CANDIDATE_PREFIX + row.id
        and event.data['notification_only'] is True)
    capture = source['capture']
    need(all(event.data[key] == capture[key] for key in ('instance_id', 'release_id', 'business_id', 'entity_id')))
    return {'schema': 'gateway-audio-candidate-basis-v255', 'instance_id': capture['instance_id'],
        'release_id': capture['release_id'], 'business_id': capture['business_id'], 'entity_id': row.entity_id,
        'observation_id': row.id, 'event_hash': digest(event.data), 'observation_hash': item['result_hash'],
        'original_attributes': {}, 'extracted': {'attributes': {}, 'anomalies': []},
        'original_model_result': _reference(event), 'evidence_ids': [item['evidence']['id']],
        'reported_at': None, 'business_day': item['registration_day'], 'candidate_only': True,
        'notification_only': True, 'source_time_verified': False, 'not_authoritative': True, 'entity_state_updated': False}


def _signal_anchor(signal):
    return {'schema': 'gateway-audio-candidate-received-v255', 'signal_id': signal.id,
        'event_id': signal.event_id, 'instance_id': signal.instance_id, 'entity_id': signal.entity_id,
        'basis_hash': signal.data['basis_hash'], 'event_hash': signal.data['raw_event_hash'],
        'received_at': signal.data['received_at']}


def receive(db, event):
    p = Principal(event.data.get('producer_actor_id', ''), event.workspace_id, 'viewer', 'audio-candidate-receiver')
    basis = _basis(db, p, event)
    rows = list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id == p.workspace_id,
        m.PerceptionSignal.event_id == event.id).limit(2)))
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == RECEIVED, m.Audit.data['event_id'].as_string() == event.id).limit(2)))
    if rows or proofs:
        need(len(rows) == len(proofs) == 1 and proofs[0].object_id == rows[0].id
            and digest(rows[0].data['basis']) == digest(basis)
            and digest(proofs[0].data) == digest(_signal_anchor(rows[0])))
        return True
    signal = m.PerceptionSignal(workspace_id=p.workspace_id, event_id=event.id,
        instance_id=basis['instance_id'], entity_id=basis['entity_id'], status='unassigned', data={
            'schema': 'gateway-audio-signal-receipt-v255', 'basis': basis, 'basis_hash': digest(basis),
            'raw_event_hash': digest(event.data), 'received_at': m.now().isoformat(), 'note': '',
            'note_requests': [], 'task_ids': [], 'notification_only': True})
    db.add(signal); db.flush(); _audit(db, p, RECEIVED, signal.id, _signal_anchor(signal))
    return True


def is_signal(db, p, signal):
    basis = signal.data.get('basis')
    reference = basis.get('original_model_result') if type(basis) is dict else None
    if (signal.data.get('schema') == 'gateway-audio-signal-receipt-v255'
            or type(reference) is dict and reference.get('schema') == 'gateway-audio-candidate-reference-v255'
            or bool(_audits(db, p, RECEIVED, signal.id))):
        return True
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id, m.Event.id == signal.event_id))
    return event is not None and handles_event(db, p, event)


def checked_signal(db, p, signal, **kwargs):
    event = _row(db, p, m.Event, signal.event_id)
    need(event.status == 'received')
    basis = _basis(db, p, event); proofs = _audits(db, p, RECEIVED, signal.id)
    need(signal.instance_id == basis['instance_id'] and signal.entity_id == basis['entity_id']
        and signal.data.get('notification_only') is True and signal.data.get('task_ids') == []
        and digest(signal.data['basis']) == signal.data['basis_hash'] == digest(basis)
        and signal.data['raw_event_hash'] == digest(event.data) and len(proofs) == 1
        and proofs[0].actor_id == event.data['producer_actor_id']
        and digest(proofs[0].data) == digest(_signal_anchor(signal)))
    return copy.deepcopy(basis)


def _event_inventory(db, p, stream, action):
    jobs = select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ENQUEUED, m.Audit.data['frozen']['stream_id'].as_string() == stream.id)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.data['job_id'].as_string().in_(jobs)).limit(1001)))
    need(len(proofs) <= 1000)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        or_(m.Event.id.in_([proof.object_id for proof in proofs]),
            (m.Event.data['stream_id'].as_string() == stream.id) &
            (m.Event.data['schema'].as_string() == (DAILY_SCHEMA if action == DAILY else CANDIDATE_SCHEMA))))
        .order_by(m.Event.created_at, m.Event.id).limit(1001)))
    need(len(events) <= 1000 and len(events) == len(proofs)
        and {event.id for event in events} == {proof.object_id for proof in proofs})
    values = []
    for event in events:
        state = event_delivery_state(db, p, event)
        need(event.data['stream_id'] == stream.id)
        signal_id = None; memory_id = None
        if action == DAILY:
            if state == 'ready': validate_daily(db, event)
            memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
                m.Memory.data['event_id'].as_string() == event.id,
                m.Memory.data['type'].as_string() == 'daily_summary').limit(2)))
            if event.status == 'received':
                need(len(memories) == 1 and memories[0].status == 'candidate' and memories[0].task_id is None
                    and event.received_at is not None
                    and digest(memories[0].data) == digest({**event.data, 'event_id': event.id, 'type': 'daily_summary'}))
                memory_id = memories[0].id
            else:
                need(not memories)
        elif state == 'ready':
            _basis(db, p, event)
            signals = list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id == p.workspace_id,
                m.PerceptionSignal.event_id == event.id).limit(2)))
            if event.status == 'received':
                need(len(signals) == 1); checked_signal(db, p, signals[0]); signal_id = signals[0].id
            else:
                need(not signals)
        values.append({'event_id': event.id, 'observation_id': event.data['observation_id'],
            'registration_day': event.data.get('registration_day', event.data.get('item', {}).get('registration_day')),
            'revision_hash': event.data.get('revision_hash'), 'status': event.status,
            'producer_state': state, 'signal_id': signal_id, 'memory_id': memory_id,
            'received_at': event.received_at.isoformat() if event.status == 'received' and event.received_at else None})
    return values


def _job_inventory(db, p, stream, items):
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ENQUEUED, m.Audit.data['frozen']['stream_id'].as_string() == stream.id).limit(1001)))
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        or_(m.Job.id.in_([proof.object_id for proof in proofs]),
            (m.Job.kind == KIND) & (m.Job.data['stream_id'].as_string() == stream.id))).limit(1001)))
    need(len(jobs) <= 1000 and len(proofs) <= 1000 and len(jobs) == len(proofs)
        and {job.id for job in jobs} == {proof.object_id for proof in proofs})
    records = []
    for job in jobs:
        _checked_job(db, p, job)
        records.append({'observation_id': job.data['observation_id'], 'job_id': job.id,
            'mode': job.data['mode'], 'status': job.status})
    for item in items:
        if item['state'] == 'received' and not any(job.data['mode'] == 'received'
                and job.data['observation_id'] == item['observation_id'] for job in jobs):
            records.append({'observation_id': item['observation_id'], 'job_id': None, 'mode': 'received', 'status': 'not_queued'})
    return records


def signal_view(db, p, signal):
    basis = None; issue = None
    try:
        basis = checked_signal(db, p, signal)
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError, OSError):
        issue = ERROR
    return {'id': signal.id, 'version': signal.version, 'status': signal.status,
        'instance_id': signal.instance_id, 'entity_id': signal.entity_id, 'received_at': signal.data.get('received_at'),
        'basis_hash': signal.data.get('basis_hash'), 'basis': basis, 'note': signal.data.get('note', ''),
        'tasks': [], 'integrity_issue': issue, 'candidate_only': True, 'notification_only': True,
        'entity_state_updated': False, 'audio_stream_reference': basis['original_model_result'] if basis else None}


def summary(db, p, stream):
    try:
        items = _inventory(db, p, stream); counts = _counts(items)
        jobs = _job_inventory(db, p, stream, items)
        days = _event_inventory(db, p, stream, DAILY)
        notices = _event_inventory(db, p, stream, NOTIFIED)
        terminal = stream.status in {'stopped', 'completed', 'failed'}
        complete = stream.status == 'completed' and bool(items) and counts['received'] == len(items)
        failed = any(job['status'] in {'failed', 'cancelled', 'not_queued'} for job in jobs)
        state = 'incomplete' if failed else 'data_received' if complete else 'incomplete' if terminal else 'pending'
        return {'schema': 'gateway-audio-delivery-summary-v255', 'stream_id': stream.id, 'state': state,
            'stream_status': stream.status, 'counts': counts, 'items': items, 'integrity_issue': None,
            'delivery_jobs': jobs, 'daily_records': days, 'notifications': notices,
            'candidate_only': True, 'source_clock_utc': 'unknown', 'date_basis': 'audio_original_registered',
            'daily_ack_is_separate': True, 'candidate_notification_delivery_is_separate': True}
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError, OSError):
        return {'schema': 'gateway-audio-delivery-summary-v255', 'stream_id': stream.id, 'state': 'blocked_integrity',
            'stream_status': stream.status, 'counts': None, 'items': [], 'integrity_issue': ERROR,
            'delivery_jobs': [], 'daily_records': [], 'notifications': [],
            'candidate_only': True, 'source_clock_utc': 'unknown', 'date_basis': 'audio_original_registered',
            'daily_ack_is_separate': True, 'candidate_notification_delivery_is_separate': True}


def block_delivery(db, event, reason):
    """Block one original without changing the signed payload or claiming ACK."""
    current = db.scalar(select(m.Event).where(m.Event.workspace_id == event.workspace_id,
        m.Event.id == event.id).with_for_update().execution_options(populate_existing=True))
    if current is None or current.status != 'pending_delivery':
        return None
    actor = None; original_hash = None
    p = Principal('', current.workspace_id, 'viewer', 'audio-block-receiver')
    try:
        proofs = [*_audits(db, p, DAILY, current.id), *_audits(db, p, NOTIFIED, current.id)]
        need(len(proofs) == 1)
        proof = proofs[0]; job = _row(db, p, m.Job, proof.data['job_id'])
        queued = _audits(db, p, ENQUEUED, job.id)
        key = (JOB_PREFIX + job.data['observation_id'] if job.data.get('mode') == 'received' else
            JOB_PREFIX + 'inventory:' + job.data['stream_id'] + ':' + job.data['inventory_hash'])
        need(job.kind == KIND and job.data['schema'] == JOB_SCHEMA
            and job.data['mode'] in {'received', 'inventory'} and job.idempotency_key == key
            and len(queued) == 1 and queued[0].actor_id == job.data['queued_by']
            and digest(queued[0].data) == digest(_queue_anchor(job))
            and proof.actor_id == job.data['actor_id'] and proof.data['event_id'] == current.id
            and proof.data['workspace_id'] == current.workspace_id)
        actor = proof.actor_id; original_hash = proof.data['payload_hash']
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        pass
    current.status = 'blocked_delivery'; current.attempts += 1; current.version += 1
    if actor:
        p = Principal(actor, current.workspace_id, 'viewer', 'audio-block-receiver')
        _audit(db, p, BLOCKED, current.id, {'schema': 'audio-stream-delivery-blocked-proof-v255',
            'event_id': current.id, 'kind': current.kind, 'original_payload_hash': original_hash,
            'current_payload_hash': digest(current.data), 'reason': str(reason)[:1000],
            'attempts': current.attempts, 'received': False})
    elif hasattr(db, 'info'):
        db.info['audio_delivery_blocked'] = {'event_id': current.id, 'reason': ERROR}
    return None


def deliver_pending(workspace_id):
    """Explicit-scope existing receiver calls; never starts a new consumer."""
    try:
        need(type(workspace_id) is str and str(UUID(workspace_id)) == workspace_id, '音轨整理接收必须明确限定工作区')
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, '音轨整理接收必须明确限定工作区') from None
    from sqlalchemy.orm import Session
    from .db import engine
    from .worker import deliver_events
    with Session(engine()) as db:
        reverse = select(m.Audit.object_id).where(m.Audit.workspace_id == workspace_id,
            m.Audit.action.in_([DAILY, NOTIFIED]))
        rows = list(db.scalars(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.status == 'pending_delivery', or_(m.Event.data['schema'].as_string().in_([DAILY_SCHEMA, CANDIDATE_SCHEMA]),
                m.Event.idempotency_key.startswith(DAY_PREFIX), m.Event.idempotency_key.startswith(CANDIDATE_PREFIX),
                m.Event.id.in_(reverse))).order_by(m.Event.created_at, m.Event.id).limit(100)))
        groups = set()
        for row in rows:
            if type(row.data.get('observation_id')) is not str or not row.data['observation_id']:
                block_delivery(db, row, '原音轨事件缺少原观测路由，未创建接收回执')
            else:
                groups.add((row.kind, row.data['observation_id']))
        db.commit()
    for kind, observation_id in groups:
        deliver_events(workspace_id=workspace_id, observation_id=observation_id, event_kind=kind)
    return len(groups)
