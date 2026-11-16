"""Read original processing-time bytes; never infer a historical timezone.

The SQL timestamptz proves the instant. The actual outbox event, or an already
frozen candidate Signal, supplies the original spelling used by the old hash.
Readers write no receipt, timestamp, hash, source, membership or execution
grant. record_candidate_anchor appends one audit in the real check transaction.
"""
import copy
from datetime import datetime, timezone

from sqlalchemy import select

from .contracts import digest
from .models import Audit, Event, Invocation, Job, Observation, PerceptionSignal
from .services import audit, scoped

SCHEMA = 'candidate-observation-time-anchor-v135'
AUDIT_ACTION = 'candidate_watch.time_anchor_v135'
_PROCESSING_FIELDS = ('observation_id', 'entity_id', 'source_id', 'invocation_id',
    'extracted', 'evidence_ids', 'model_receipt', 'observed_at', 'candidate_only')


def _need(value):
    if not value:
        raise ValueError('原处理事件、冻结时间或来源关联无法核对，原记录保留')


def same_observation_instant(value, observed_at):
    """Validate one original ISO spelling, returning it without normalization."""
    if value is None:
        _need(observed_at is None)
        return None
    _need(type(value) is str and 1 <= len(value) <= 100 and '\x00' not in value)
    try:
        value.encode('utf-8', 'strict')
        original = datetime.fromisoformat(value)
        _need(original.tzinfo is not None and original.utcoffset() is not None
            and isinstance(observed_at, datetime) and observed_at.tzinfo is not None
            and observed_at.utcoffset() is not None
            and original.astimezone(timezone.utc) == observed_at.astimezone(timezone.utc))
    except (ValueError, TypeError, OverflowError):
        _need(False)
    return value


def _observation(db, p, row):
    actual = scoped(db, Observation, row.id, p)
    _need(actual.workspace_id == p.workspace_id and row.workspace_id == p.workspace_id
        and actual.status == row.status == 'processed'
        and all(getattr(actual, key) == getattr(row, key) for key in
            ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day', 'observed_at'))
        and digest(actual.data) == digest(row.data))
    return actual


def _processing_event(db, p, row, event):
    """Validate the actual original processing identity, not a caller string."""
    _need(event.workspace_id == p.workspace_id and event.kind in {'observation.data', 'perception.anomaly'}
        and event.idempotency_key == ('data:' if event.kind == 'observation.data' else 'anomaly:') + row.id
        and type(event.data) is dict and 'observed_at' in event.data)
    expected = {'observation_id': row.id, 'entity_id': row.entity_id, 'source_id': row.source_id,
        'invocation_id': row.data['invocation_id'], 'extracted': row.data['extracted']}
    _need(all(key in event.data and digest(event.data[key]) == digest(value) for key, value in expected.items()))
    _need(digest(event.data.get('evidence_ids', [])) == digest(row.data.get('evidence_ids', [])))
    if 'candidate_only' in event.data:
        _need(type(event.data['candidate_only']) is bool
            and event.data['candidate_only'] is row.data.get('candidate_only', False))
    if 'model_receipt' in event.data:
        _need(digest(event.data['model_receipt']) == digest(row.data.get('model_receipt', {})))
    call = scoped(db, Invocation, row.data['invocation_id'], p)
    inputs = {'attributes': row.data.get('attributes', {})}
    if row.data.get('evidence_ids'): inputs['evidence_ids'] = row.data['evidence_ids']
    _need(call.status == 'succeeded' and call.capability_id == row.data['extractor_id']
        and call.data.get('input_hash') == digest(inputs)
        and digest(call.data.get('output')) == digest(row.data['extracted']))
    if event.kind == 'perception.anomaly':
        _need(type(row.data['extracted'].get('anomalies')) is list and bool(row.data['extracted']['anomalies']))
    return same_observation_instant(event.data['observed_at'], row.observed_at)


def processing_anchor(db, p, row, *, event_id=None):
    """Prefer the actual anomaly event; an explicit event stays explicitly bound.

    Independently committed processing events may use different offsets for the
    same instant. Check each event, without substituting one event's raw bytes
    for another or trying multiple spellings against an old hash.
    """
    row = _observation(db, p, row)
    values = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.idempotency_key.in_(['data:' + row.id, 'anomaly:' + row.id])).limit(3)))
    _need(1 <= len(values) <= 2 and len({event.idempotency_key for event in values}) == len(values))
    for event in values: _processing_event(db, p, row, event)
    if event_id is None:
        event = next((event for event in values if event.kind == 'perception.anomaly'), values[0])
    else:
        matches = [event for event in values if event.id == event_id]
        if matches:
            _need(len(matches) == 1); event = matches[0]
        else:
            # Older receivers admitted independently stored duplicate anomaly
            # notifications. They still need the real original processing
            # event: compare field presence and exact raw processing bytes,
            # never substitute an arbitrary same-instant timestamp spelling.
            notice = scoped(db, Event, event_id, p)
            original = [event for event in values if event.kind == 'perception.anomaly']
            _need(len(original) == 1 and notice.kind == 'perception.anomaly'
                and notice.workspace_id == p.workspace_id and type(notice.data) is dict)
            event = original[0]
            _need(digest({key: notice.data[key] for key in _PROCESSING_FIELDS if key in notice.data})
                == digest({key: event.data[key] for key in _PROCESSING_FIELDS if key in event.data}))
    # Receivers append reception/delivery annotations to the outbox JSON.
    # Those are not the original processing payload. Preserve every original
    # processing field's presence and bytes, plus actual event identity.
    signature = digest({'schema': 'observation-processing-event-time-v135',
        'event_id': event.id, 'workspace_id': event.workspace_id, 'kind': event.kind,
        'idempotency_key': event.idempotency_key,
        'processing': {key: event.data[key] for key in _PROCESSING_FIELDS if key in event.data}})
    return {'schema': SCHEMA, 'source_event_id': event.id,
        'source_event_hash': signature, 'observed_at': event.data['observed_at']}


def check_anchor(db, p, row, value):
    _need(type(value) is dict and set(value) == {'schema', 'source_event_id', 'source_event_hash', 'observed_at'}
        and value['schema'] == SCHEMA)
    actual = processing_anchor(db, p, row, event_id=value['source_event_id'])
    _need(digest(value) == digest(actual))
    return copy.deepcopy(actual)


def _candidate_identity(db, p, row, identity):
    event = scoped(db, Event, identity, p)
    _need(event.workspace_id == p.workspace_id and event.kind == 'perception.candidate'
        and type(event.data) is dict
        and event.idempotency_key == 'candidate:' + event.data['watch_id'] + ':' + row.id
        and event.data['observation_id'] == row.id and event.data['entity_id'] == row.entity_id
        and event.data['source_id'] == row.source_id and event.data['candidate_only'] is True
        and digest(event.data.get('evidence_ids', [])) == digest(row.data.get('evidence_ids', []))
        and digest(event.data['watch_definition']) == event.data['definition_hash'])
    return event


def _audit_data(job, result, actor_id):
    return {'schema': 'candidate-observation-time-audit-v135', 'workspace_id': job.workspace_id,
        'actor_id': actor_id, 'job_id': job.id, 'event_id': result.get('event_id'),
        'watch_id': job.data['watch_id'], 'observation_id': job.data['observation_id'],
        'definition_hash': job.data['definition_hash'], 'anchor_hash': digest(result['time_anchor']),
        'machine_hash': result['machine_hash'], 'selection_hash': digest(result['selection']),
        'result_hash': digest(result)}


def _job_inventory(db, p, job, result):
    records = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == AUDIT_ACTION, Audit.object_id == job.id).limit(2)))
    if result is None or 'time_anchor' not in result:
        _need(not records)
        return
    _need(len(records) == 1 and type(records[0].data) is dict)
    record = records[0]
    _need(digest(record.data) == digest(_audit_data(job, result, record.actor_id)))


def _candidate_event(db, p, row, identity):
    event = _candidate_identity(db, p, row, identity)
    # A new Event cannot lose its marker and silently become an old protocol:
    # actual completed check results independently retain this Event binding.
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'check_candidate_watch', Job.data['result']['event_id'].as_string() == event.id)))
    if 'time_anchor' in event.data: _need(bool(jobs))
    for job in jobs:
        result = job.data['result']
        _job_inventory(db, p, job, result)
        _need(job.data['watch_id'] == event.data['watch_id'] and job.data['observation_id'] == row.id
            and job.data['definition_hash'] == event.data['definition_hash']
            and job.idempotency_key == 'candidate-check:' + job.data['watch_id'] + ':' + str(job.data['watch_version']) + ':' + row.id
            and result['machine_hash'] == event.data['machine_hash']
            and digest(result['selection']) == digest(event.data['selection'])
            and result['matched'] is True and result['candidate_only'] is True
            and type(result['cloud_calls']) is int and result['cloud_calls'] == 0
            and ('time_anchor' in result) == ('time_anchor' in event.data))
        if 'time_anchor' in result:
            _need(digest(result['time_anchor']) == digest(event.data['time_anchor']))
    records = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == AUDIT_ACTION, Audit.data['event_id'].as_string() == event.id)))
    expected = {job.id for job in jobs if 'time_anchor' in job.data['result']}
    _need(len(records) == len(expected) and {record.object_id for record in records} == expected)
    return event


def _frozen_candidate_time(db, p, row, event):
    signals = list(db.scalars(select(PerceptionSignal).where(PerceptionSignal.workspace_id == p.workspace_id,
        PerceptionSignal.event_id == event.id).limit(2)))
    _need(len(signals) <= 1)
    if not signals: return None
    signal = signals[0]; basis = signal.data.get('basis')
    if basis is None:
        _need(signal.data.get('basis_hash') is None)
        return None
    _need(type(basis) is dict and digest(basis) == signal.data['basis_hash']
        and basis['schema'] == 'local-model-candidate-signal-v1' and basis['candidate_only'] is True
        and basis['event_hash'] == digest(event.data) and basis['observation_id'] == row.id
        and basis['entity_id'] == row.entity_id == signal.entity_id
        and basis['instance_id'] == signal.instance_id
        and basis['observation_hash'] == event.data['machine_hash'] and 'reported_at' in basis)
    return {'observed_at': same_observation_instant(basis['reported_at'], row.observed_at)}


def candidate_time(db, p, row, *, event_id=None, job_id=None):
    """Resolve an explicit candidate protocol; old records are never upgraded.

    An old candidate with neither a marker nor a frozen Signal has no durable
    original timestamp spelling. Keep its original environment-dependent
    algorithm, conditional on the original saved machine hash matching. This
    explicit legacy path cannot claim timezone-independent recovery.
    """
    row = _observation(db, p, row)
    _need(row.data.get('candidate_only') is True and not row.data.get('native_instance_id'))
    job = None; result = None
    if job_id is not None:
        job = scoped(db, Job, job_id, p)
        _need(job.kind == 'check_candidate_watch' and job.data['observation_id'] == row.id
            and job.idempotency_key == 'candidate-check:' + job.data['watch_id'] + ':' + str(job.data['watch_version']) + ':' + row.id)
        result = job.data.get('result')
        _need(result is None or type(result) is dict and 'machine_hash' in result)
        _job_inventory(db, p, job, result)
        if result is not None and result.get('event_id') is not None:
            _need(event_id is None or event_id == result['event_id']); event_id = result['event_id']
    if event_id is not None:
        event = _candidate_event(db, p, row, event_id)
        if job is not None: _need(job.data['watch_id'] == event.data['watch_id'])
        if result is not None: _need(result['machine_hash'] == event.data['machine_hash'])
        if 'time_anchor' in event.data:
            anchor = check_anchor(db, p, row, event.data['time_anchor'])
            if result is not None:
                _need('time_anchor' in result and digest(result['time_anchor']) == digest(anchor))
            return {'observed_at': anchor['observed_at'], 'time_anchor': anchor,
                'protocol': 'processing_event', 'expected_hash': event.data['machine_hash']}
        _need(result is None or 'time_anchor' not in result)
        frozen = _frozen_candidate_time(db, p, row, event)
        if frozen is not None:
            return {**frozen, 'time_anchor': None, 'protocol': 'frozen_basis', 'expected_hash': event.data['machine_hash']}
        return {'observed_at': row.observed_at.isoformat() if row.observed_at else None,
            'time_anchor': None, 'protocol': 'legacy_environment', 'expected_hash': event.data['machine_hash']}
    if result is not None:
        if 'time_anchor' in result:
            anchor = check_anchor(db, p, row, result['time_anchor'])
            return {'observed_at': anchor['observed_at'], 'time_anchor': anchor,
                'protocol': 'processing_event', 'expected_hash': result['machine_hash']}
        return {'observed_at': row.observed_at.isoformat() if row.observed_at else None,
            'time_anchor': None, 'protocol': 'legacy_environment', 'expected_hash': result['machine_hash']}
    anchor = processing_anchor(db, p, row)
    return {'observed_at': anchor['observed_at'], 'time_anchor': anchor,
        'protocol': 'processing_event', 'expected_hash': None}


def record_candidate_anchor(db, p, job, result):
    """Append only for a genuinely new, recomputed check, never to mend history."""
    original = scoped(db, Job, job.id, p)
    _need(original.workspace_id == p.workspace_id and original.kind == 'check_candidate_watch'
        and original.data['actor_id'] == p.user_id and type(result) is dict)
    previous = original.data.get('result')
    _job_inventory(db, p, original, previous)
    if 'time_anchor' not in result:
        _need(previous is None or 'time_anchor' not in previous)
        return
    if previous is not None:
        _need('time_anchor' in previous and digest(previous) == digest(result))
        return
    # This repeats the actual read-only selection over the original result.
    # It does not call a model, tool, receiver, or task creation function.
    from . import candidate_watches as watches
    from .models import CandidateWatch
    watch = scoped(db, CandidateWatch, original.data['watch_id'], p)
    value = watches.verify_definition(db, p, watch, enabled=True)
    _need(watch.version == original.data['watch_version']
        and watch.data['definition_hash'] == original.data['definition_hash'])
    row = scoped(db, Observation, original.data['observation_id'], p)
    report = watches.source(db, p, row, job_id=original.id)
    from .perception_signals import _machine_basis_for_anchor
    report = {**report, **_machine_basis_for_anchor(db, p, row, result['time_anchor'])}
    selected = watches.selection(value, row, report)
    expected = {'matched': selected['matched'], 'selection': selected, 'machine_hash': report['machine_hash'],
        'candidate_only': True, 'cloud_calls': 0, 'time_anchor': report['time_anchor']}
    if selected['matched']:
        event = _candidate_identity(db, p, row, result['event_id'])
        _need(event.data['watch_id'] == watch.id and event.data['watch_definition'] == value
            and digest(event.data['selection']) == digest(selected)
            and event.data['machine_hash'] == report['machine_hash']
            and digest(event.data['time_anchor']) == digest(report['time_anchor']))
        expected['event_id'] = event.id
    _need(digest(expected) == digest(result))
    audit(db, p, AUDIT_ACTION, original.id, _audit_data(original, result, p.user_id))
