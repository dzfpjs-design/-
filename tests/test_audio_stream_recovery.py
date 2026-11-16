"""Pure recovery contracts with explicit source/DB stubs, not PG/RTSP/ACK proof."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import audio_stream_recovery as subject
from generic_agents import audio_stream_delivery as delivery
from generic_agents import models as m
from generic_agents.contracts import digest


def identity():
    return str(uuid4())


class StubDB:
    """Only the bounded recovery inventory queries; no real persistence claim."""
    def __init__(self):
        self.rows = {}; self.audits = []; self.jobs = []; self.streams = []; self.events = []
        self.observations = []; self.memories = []; self.signals = []; self.actor_ok = True
        self.actor_checks = 0; self.nonces = []

    def add(self, value):
        assert isinstance(value, m.Audit)
        self.audits.append(value); self.rows[value.id] = value

    def flush(self):
        pass

    def scalar(self, query):
        params = list(query.compile().params.values())
        found = [value for value in params if isinstance(value, str) and value in self.rows]
        return self.rows[found[0]] if found else None

    def execute(self, query):
        assert query.column_descriptions[0]['entity'] is m.User
        return SimpleNamespace(first=lambda: (object(), object()) if self.actor_ok else None)

    def scalars(self, query):
        entity = query.column_descriptions[0]['entity']
        params = list(query.compile().params.values())
        requests = [v[0]['request_id'] for v in params if type(v) is list and v and type(v[0]) is dict and 'request_id' in v[0]]
        stream_ids = [v[0]['stream_id'] for v in params if type(v) is list and v and type(v[0]) is dict and 'stream_id' in v[0]]
        if entity is m.Audit:
            values = [a for a in self.audits if a.action in params]
            return [a for a in values if a.object_id in params or a.data.get('request_id') in params
                or a.data.get('event_id') in params or a.data.get('key') in params]
        if entity is subject.AudioStream:
            return [s for s in self.streams if any(v['request_id'] in requests for v in s.data.get(subject.MARKERS, []))]
        if entity is m.Job:
            if requests:
                return [j for j in self.jobs if any(v['request_id'] in requests for v in j.data.get(subject.HISTORY, []))]
            return [j for j in self.jobs if j.data.get('stream_id') in params or j.idempotency_key in params
                or any(v['stream_id'] in stream_ids for v in j.data.get(subject.HISTORY, []))]
        if entity is m.Memory:
            return [v for v in self.memories if v.data['event_id'] in params]
        if entity is m.PerceptionSignal:
            return [v for v in self.signals if v.event_id in params]
        if entity is m.Event:
            return [v for v in self.events if v.idempotency_key in params
                or v.kind == 'observation.data' and v.data.get('observation_id') in params]
        raise AssertionError('unexpected query: ' + str(entity))


@pytest.fixture
def scene(monkeypatch):
    from generic_agents import relations
    db = StubDB(); now = datetime(2026, 10, 30, 9, tzinfo=timezone.utc)
    p = SimpleNamespace(user_id=identity(), workspace_id=identity(), role='operator', session_id='unit')
    instance = m.DomainInstance(id=identity(), workspace_id=p.workspace_id)
    stream = subject.AudioStream(id=identity(), workspace_id=p.workspace_id, instance_id=instance.id,
        actor_id=p.user_id, channel_id=identity(), entity_id=identity(), status='stopped', version=3, data={'immutable_definition': 'unit-only'})
    job = m.Job(id=identity(), workspace_id=p.workspace_id, kind=delivery.KIND, idempotency_key='unit-source-key',
        status='failed', attempts=1, version=4, available_at=now - timedelta(seconds=1), lease_token='old-token', lease_until=None,
        data={'schema': delivery.JOB_SCHEMA, 'mode': 'received', 'stream_id': stream.id, 'actor_id': p.user_id,
            'queued_by': p.user_id, 'observation_id': identity(), 'error': 'unit-only safe failure', 'machine_result': {'untouched': 0}})
    observation = m.Observation(id=identity(), workspace_id=p.workspace_id, version=2, status='processed', data={})
    event = m.Event(id=identity(), workspace_id=p.workspace_id, kind='perception.daily', version=5,
        status='blocked_delivery', attempts=2, received_at=None, data={'unit_original': {'candidate_only': True}})
    queue = m.Audit(id=identity(), workspace_id=p.workspace_id, actor_id=p.user_id, action=delivery.ENQUEUED,
        object_id=job.id, data={'unit_source': True})
    issued = m.Audit(id=identity(), workspace_id=p.workspace_id, actor_id=p.user_id, action=delivery.DAILY,
        object_id=event.id, data={'unit_source': True})
    db.audits.extend([queue, issued]); db.jobs.append(job); db.streams.append(stream); db.events.append(event)
    db.observations.append(observation)
    for row in [instance, stream, job, observation, event]: db.rows[row.id] = row
    context = {'stream': stream, 'capture': {'unit_capture': observation.id}}
    def read_stream(database, principal, iid, sid):
        assert database is db
        if iid != instance.id or sid != stream.id: raise HTTPException(404, 'unit-scope')
        return stream
    def checked_job(database, principal, value):
        assert value.kind == delivery.KIND and value.data['stream_id'] == stream.id
        return observation, None, context
    def checked_event(database, principal, value, action, **kwargs):
        assert value.id == event.id and action == delivery.DAILY
        return job, context
    def actor(database, principal):
        assert principal.role in {'operator', 'admin'}
        db.actor_checks += 1
    monkeypatch.setattr(subject.streams, '_read_stream', read_stream)
    monkeypatch.setattr(subject.streams, '_checked', lambda *args: None)
    monkeypatch.setattr(subject.streams, '_nonce', lambda database, principal, key: db.nonces.append(key))
    monkeypatch.setattr(subject.streams, 'observation_context', lambda *args, **kwargs: context)
    monkeypatch.setattr(subject.streams, 'stream_observations', lambda *args: [])
    monkeypatch.setattr(delivery, '_checked_job', checked_job)
    monkeypatch.setattr(delivery, '_checked_event', checked_event)
    monkeypatch.setattr(delivery, 'handles_daily', lambda *args: True)
    monkeypatch.setattr(delivery, 'handles_event', lambda *args: False)
    monkeypatch.setattr(delivery, 'event_delivery_state', lambda *args: 'ready')
    monkeypatch.setattr(delivery, 'validate_daily', lambda *args: copy.deepcopy(event.data))
    monkeypatch.setattr(delivery, '_item', lambda *args: ({'state': 'received'}, context))
    monkeypatch.setattr(subject, '_now', lambda *args: now)
    monkeypatch.setattr(relations, 'lock_current_actor', actor)
    return SimpleNamespace(db=db, p=p, now=now, stream=stream, instance=instance, job=job, event=event,
        observation=observation, queue=queue, issued=issued, context=context, monkeypatch=monkeypatch)


def command(s, action='retry_job', *, request_id=None, version=None, subject_id=None):
    row = s.job if action == 'retry_job' else s.event if action == 'retry_event' else s.observation
    body = {'schema': subject.SCHEMA, 'request_id': request_id or '原申请编号-音轨-0001', 'action': action,
        'subject_id': subject_id or row.id, 'version': row.version if version is None else version}
    return json.dumps(body, ensure_ascii=False, indent=2).encode('utf-8')


def send(s, raw):
    return subject.recover(s.db, s.p, s.instance.id, s.stream.id, raw)['receipt']


def test_raw_unicode_is_retained_and_typed_hash_is_not_raw_sha(scene):
    raw = command(scene)
    original, body, rh, bh = subject.parse(raw)
    assert original.encode() == raw and rh == hashlib.sha256(raw).hexdigest()
    assert bh == digest(body) and rh != bh
    alternate = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
    assert subject.parse(alternate)[2] != rh and subject.parse(alternate)[3] == bh


@pytest.mark.parametrize('change', ['bool', 'extra', 'nul', 'duplicate', 'nonfinite', 'large'])
def test_invalid_raw_does_not_create_a_receipt(scene, change):
    raw = command(scene); value = json.loads(raw)
    if change == 'bool': value['version'] = True
    if change == 'extra': value['unexpected'] = None
    if change == 'nul': value['request_id'] += '\x00'
    if change == 'duplicate': raw = raw.replace(b'"version": 4', b'"version": 4, "version": 4')
    elif change == 'nonfinite': raw = raw.replace(b'"version": 4', b'"version": NaN')
    elif change == 'large': raw = b' ' * 16385
    else: raw = json.dumps(value).encode()
    with pytest.raises(HTTPException) as error: send(scene, raw)
    assert error.value.status_code == 422
    assert scene.stream.data == {'immutable_definition': 'unit-only'} and len(scene.db.audits) == 2


def test_retry_keeps_original_attempt_result_and_error_and_records_failure(scene):
    before = copy.deepcopy(scene.job.data); receipt = send(scene, command(scene))
    assert receipt['outcome'] == 'accepted' and (receipt['version_before'], receipt['version_after']) == (4, 5)
    assert scene.job.status == 'queued' and scene.job.attempts == 1
    assert scene.job.lease_token is None and scene.job.lease_until is None
    assert {k: v for k, v in scene.job.data.items() if k != subject.HISTORY} == before
    proof = scene.db.audits[-1]
    assert proof.data['previous']['job_data_hash'] == digest(before)
    assert proof.data['previous']['lease_token'] == 'old-token'
    assert len(scene.job.data[subject.HISTORY]) == len(scene.stream.data[subject.MARKERS]) == 1
    assert scene.db.actor_checks == 1 and len(scene.db.events) == 1 and len(scene.db.jobs) == 1
    assert subject._ledger(scene.db, scene.p, scene.stream) == [proof]


def test_known_original_is_readonly_after_current_role_change(scene):
    raw = command(scene); receipt = send(scene, raw)
    scene.p.role = 'viewer'; checks = scene.db.actor_checks
    assert send(scene, raw) == receipt
    assert scene.db.actor_checks == checks and scene.job.version == 5
    lookup = subject.lookup(scene.db, scene.p, scene.instance.id, scene.stream.id,
        receipt['request_id'], receipt['raw_sha256'])
    assert lookup['found'] and lookup['receipt'] == receipt and lookup['original_raw'].encode() == raw


def test_cas_rejection_is_permanent_for_original_nonce(scene):
    raw = command(scene, version=3); receipt = send(scene, raw)
    assert receipt['outcome'] == 'rejected' and receipt['version_after'] == receipt['version_before'] == 4
    scene.job.version = 3
    assert send(scene, raw) == receipt and scene.job.status == 'failed'


@pytest.mark.parametrize('blocker', ['queued', 'running', 'succeeded', 'attempt_limit', 'issuer', 'lease'])
def test_legal_blockers_save_rejected_receipt_without_requeue(scene, blocker):
    if blocker in {'queued', 'running', 'succeeded'}: scene.job.status = blocker
    elif blocker == 'attempt_limit': scene.job.attempts = 3
    elif blocker == 'issuer': scene.db.actor_ok = False
    else: scene.job.lease_until = scene.now + timedelta(seconds=30)
    before = (scene.job.status, scene.job.version, scene.job.attempts, copy.deepcopy(scene.job.data))
    receipt = send(scene, command(scene))
    assert receipt['outcome'] == 'rejected' and receipt['reason']
    assert (scene.job.status, scene.job.version, scene.job.attempts, scene.job.data) == before
    assert subject.HISTORY not in scene.job.data


def test_event_retry_preserves_signed_payload_attempts_and_independent_ack(scene):
    payload = copy.deepcopy(scene.event.data)
    receipt = send(scene, command(scene, 'retry_event'))
    assert receipt['outcome'] == 'accepted' and scene.event.status == 'pending_delivery'
    assert scene.event.data == payload and scene.event.version == 6 and scene.event.attempts == 2
    assert scene.event.received_at is None and not scene.db.memories and not scene.db.signals
    assert scene.db.audits[-1].data['previous']['payload_hash'] == digest(payload)


def test_pending_producer_cannot_be_retried_as_ready(scene):
    scene.monkeypatch.setattr(delivery, 'event_delivery_state', lambda *args: 'pending_producer')
    receipt = send(scene, command(scene, 'retry_event'))
    assert receipt['outcome'] == 'rejected' and scene.event.status == 'blocked_delivery'


def test_orphan_receiver_memory_is_integrity_error_not_requeue(scene):
    scene.db.memories.append(SimpleNamespace(data={'event_id': scene.event.id}))
    with pytest.raises(HTTPException) as error: send(scene, command(scene, 'retry_event'))
    assert error.value.status_code == 409 and scene.event.status == 'blocked_delivery'
    assert len(scene.db.audits) == 2 and subject.MARKERS not in scene.stream.data


def test_unreceived_original_cannot_be_queued_as_received(scene):
    scene.monkeypatch.setattr(subject, '_existing_queue', lambda *args, **kwargs: None)
    scene.monkeypatch.setattr(delivery, '_item', lambda *args: ({'state': 'waiting_data'}, scene.context))
    receipt = send(scene, command(scene, 'enqueue_received'))
    assert receipt['outcome'] == 'rejected' and len(scene.db.jobs) == 1


@pytest.mark.parametrize('action', ['enqueue_received', 'enqueue_snapshot'])
def test_enqueue_uses_existing_typed_helper_and_original_observation_version(scene, action):
    calls = []
    scene.monkeypatch.setattr(subject, '_existing_queue', lambda *args, **kwargs: None)
    def enqueue(*args):
        calls.append(args)
        return SimpleNamespace(id=identity())
    scene.monkeypatch.setattr(delivery, action, enqueue)
    receipt = send(scene, command(scene, action))
    assert receipt['outcome'] == 'accepted' and receipt['version_before'] == receipt['version_after'] == 2
    assert len(calls) == 1 and receipt['job_id'] and not scene.db.memories and not scene.db.signals
    if action == 'enqueue_snapshot': assert calls[0][-2:] == (scene.stream, scene.observation.id)
    else: assert calls[0][-1] is scene.observation


@pytest.mark.parametrize('damage', ['audit', 'marker', 'job_link', 'duplicate_audit', 'duplicate_marker', 'typed_bool'])
def test_saved_request_inventory_cannot_downgrade_to_new_request(scene, damage):
    raw = command(scene); receipt = send(scene, raw); proof = scene.db.audits[-1]
    if damage == 'audit': scene.db.audits.remove(proof)
    elif damage == 'marker': scene.stream.data[subject.MARKERS] = []
    elif damage == 'job_link': scene.job.data[subject.HISTORY] = []
    elif damage == 'duplicate_audit': scene.db.audits.append(copy.copy(proof))
    elif damage == 'duplicate_marker': scene.stream.data[subject.MARKERS] *= 2
    else:
        proof.data['receipt']['version_after'] = True
        proof.data['receipt']['receipt_hash'] = digest({k: v for k, v in proof.data['receipt'].items() if k != 'receipt_hash'})
        scene.stream.data[subject.MARKERS] = [subject._marker(proof)]
        scene.job.data[subject.HISTORY] = [subject._job_link(proof)]
    with pytest.raises(HTTPException) as error:
        subject.lookup(scene.db, scene.p, scene.instance.id, scene.stream.id, receipt['request_id'], receipt['raw_sha256'])
    assert error.value.status_code == 409 and scene.job.version == 5


def test_original_cross_actor_and_changed_raw_are_not_reusable(scene):
    raw = command(scene); receipt = send(scene, raw)
    with pytest.raises(HTTPException) as changed:
        send(scene, raw + b' ')
    assert changed.value.status_code == 409
    scene.p.user_id = identity()
    with pytest.raises(HTTPException) as other:
        subject.lookup(scene.db, scene.p, scene.instance.id, scene.stream.id, receipt['request_id'], receipt['raw_sha256'])
    assert other.value.status_code == 404


def test_unknown_lookup_never_writes_or_resets_a_failed_job(scene):
    view = subject.lookup(scene.db, scene.p, scene.instance.id, scene.stream.id, 'unknown-original-0001', 'a' * 64)
    assert view['found'] is False and view['receipt'] is view['original_raw'] is view['body_hash'] is None
    assert scene.job.status == 'failed' and scene.db.actor_checks == 0 and len(scene.db.audits) == 2


def test_all_audit_and_stream_marker_loss_still_has_job_reverse_link(scene):
    raw = command(scene); receipt = send(scene, raw)
    scene.db.audits.pop(); scene.stream.data.pop(subject.MARKERS)
    with pytest.raises(HTTPException) as error:
        subject.lookup(scene.db, scene.p, scene.instance.id, scene.stream.id, receipt['request_id'], receipt['raw_sha256'])
    assert error.value.status_code == 409 and scene.job.version == 5


def test_inventory_limit_is_explicit_failure_not_first_page_success(scene):
    scene.db.audits.extend(m.Audit(id=identity(), workspace_id=scene.p.workspace_id, action=subject.ACTION,
        object_id=scene.stream.id, actor_id=scene.p.user_id, data={}) for _ in range(subject.LIMIT + 1))
    with pytest.raises(HTTPException) as error: subject._ledger(scene.db, scene.p, scene.stream)
    assert error.value.status_code == 409 and scene.job.status == 'failed'


def test_options_expose_full_real_inventory_and_cannot_repair_bad_sources(scene):
    scene.monkeypatch.setattr(delivery, 'summary', lambda *args: {'integrity_issue': None,
        'delivery_jobs': [{'job_id': scene.job.id}], 'daily_records': [{'event_id': scene.event.id}],
        'notifications': [], 'items': [{'observation_id': scene.observation.id, 'state': 'waiting_data'}]})
    scene.monkeypatch.setattr(subject, '_existing_queue', lambda *args, **kwargs: None)
    view = subject.options(scene.db, scene.p, scene.instance.id, scene.stream.id)
    assert len(view['options']) == 4 and view['integrity_issue'] is None
    assert {v['action'] for v in view['options'] if v['available']} == {'retry_job', 'retry_event', 'enqueue_snapshot'}
    assert scene.db.actor_checks == 0 and scene.job.status == 'failed'
    scene.monkeypatch.setattr(delivery, 'summary', lambda *args: {'integrity_issue': 'unit bad inventory'})
    blocked = subject.options(scene.db, scene.p, scene.instance.id, scene.stream.id)
    assert blocked['options'] == [] and blocked['integrity_issue']


@pytest.fixture
def primary_scene(scene, monkeypatch):
    """SDK/bytes are explicit source stubs; use the real _event_matches contract."""
    from generic_agents import observation_inputs, observation_integrity
    s = scene; row = s.observation; extractor_id = identity(); evidence_id = identity()
    output = {'attributes': {}, 'anomalies': [], 'environment_sound': {'unit_fixture_only': True}}
    extract = m.Job(id=identity(), workspace_id=s.p.workspace_id, kind='process_observation',
        idempotency_key='extract:' + row.id, status='succeeded', version=2, attempts=1,
        data={'schema': subject.streams.EXTRACT_SCHEMA, 'actor_id': s.stream.actor_id,
            'observation_id': row.id, 'extractor_id': extractor_id})
    call = m.Invocation(id=identity(), workspace_id=s.p.workspace_id, capability_id=extractor_id,
        idempotency_key='observation:' + row.id + ':attempt:1', status='succeeded', data={
            'job_id': extract.id, 'input_hash': digest({'attributes': {}, 'evidence_ids': [evidence_id]}),
            'output': copy.deepcopy(output)})
    row.entity_id = s.stream.entity_id; row.source_id = identity(); row.observed_at = None
    row.data = {'submitted_by': s.stream.actor_id, 'candidate_only': True, 'invocation_id': call.id,
        'audio_stream_extract_job_id': extract.id, 'extractor_id': extractor_id, 'attributes': {},
        'evidence_ids': [evidence_id], 'extracted': copy.deepcopy(output), 'model_receipt': {'unit_only': True}}
    data_event = m.Event(id=identity(), workspace_id=s.p.workspace_id, kind='observation.data',
        idempotency_key='data:' + row.id, status='blocked_delivery', attempts=1, version=7, received_at=None,
        data={'observation_id': row.id, 'entity_id': row.entity_id, 'source_id': row.source_id,
            'invocation_id': call.id, 'extracted': copy.deepcopy(output), 'evidence_ids': [evidence_id],
            'observed_at': None, 'candidate_only': True, 'model_receipt': {'unit_only': True},
            'delivery_error': '旧送达错误保持原文，不当作模型输出'})
    proof = m.Audit(id=identity(), workspace_id=s.p.workspace_id, actor_id=s.stream.actor_id,
        action=subject.streams.EXTRACT_QUEUED, object_id=extract.id, data={'unit_queue_only': True})
    s.db.jobs.append(extract); s.db.events.append(data_event); s.db.audits.append(proof)
    for value in [extract, call, data_event]: s.db.rows[value.id] = value
    s.source_readable = True
    def context(database, principal, observation, *, execution=False):
        assert execution is False  # Both GET and this historical retry use no execution lock.
        if not s.source_readable: raise HTTPException(409, 'unit original bytes unavailable')
        return s.context
    def report(database, principal, observation, *, allow_candidates=False):
        assert allow_candidates is True
        return {'invocation_id': observation.data['invocation_id'], 'extracted': copy.deepcopy(observation.data['extracted'])}
    monkeypatch.setattr(subject.streams, 'observation_context', context)
    monkeypatch.setattr(subject.streams, 'stream_observations', lambda *args: [row])
    monkeypatch.setattr(observation_inputs, 'job_for', lambda *args: extract)
    monkeypatch.setattr(observation_integrity, 'checked_report', report)
    monkeypatch.setattr(delivery, 'handles_daily', lambda database, principal, event: event.id == s.event.id)
    s.extract = extract; s.call = call; s.data_event = data_event; s.extract_queue = proof
    return s


def data_command(s, *, version=None):
    return command(s, 'retry_event', subject_id=s.data_event.id,
        version=s.data_event.version if version is None else version)


def test_primary_data_retry_keeps_sdk_payload_and_old_delivery_error(primary_scene):
    s = primary_scene; raw = data_command(s)
    payload = copy.deepcopy(s.data_event.data); call = copy.deepcopy(s.call.data)
    row_data = copy.deepcopy(s.observation.data); jobs = [v.id for v in s.db.jobs]
    receipt = send(s, raw)
    assert receipt['outcome'] == 'accepted' and receipt['event_id'] == s.data_event.id
    assert (receipt['version_before'], receipt['version_after']) == (7, 8)
    assert s.data_event.status == 'pending_delivery' and s.data_event.attempts == 1
    assert s.data_event.data == payload and s.call.data == call and s.observation.data == row_data
    assert s.call.status == s.extract.status == 'succeeded' and [v.id for v in s.db.jobs] == jobs
    assert s.db.audits[-1].data['previous']['payload_hash'] == digest(payload)
    assert s.db.audits[-1].data['previous']['producer_audit_id'] == s.extract_queue.id
    assert s.db.audits[-1].data['previous']['producer_audit_hash'] == digest(s.extract_queue.data)
    assert send(s, raw) == receipt and s.data_event.version == 8
    assert s.data_event.received_at is None and not s.db.memories and not s.db.signals


@pytest.mark.parametrize('status', ['running', 'failed', 'cancelled'])
def test_primary_data_does_not_adopt_non_successful_extract_job(primary_scene, status):
    s = primary_scene; s.extract.status = status
    receipt = send(s, data_command(s))
    assert receipt['outcome'] == 'rejected' and s.data_event.status == 'blocked_delivery'
    assert s.data_event.version == 7 and s.call.status == 'succeeded'


@pytest.mark.parametrize('damage', ['output', 'source', 'object', 'call', 'evidence', 'candidate', 'time',
    'model_receipt', 'key', 'kind', 'job_actor', 'call_job', 'call_status', 'queue', 'scope'])
def test_primary_data_scope_sdk_or_queue_mismatch_cannot_requeue(primary_scene, damage):
    s = primary_scene; event = s.data_event
    if damage == 'output': event.data['extracted'] = {'attributes': {'invented_fact': 2}}
    elif damage == 'source': event.data['source_id'] = identity()
    elif damage == 'object': event.data['entity_id'] = identity()
    elif damage == 'call': event.data['invocation_id'] = identity()
    elif damage == 'evidence': event.data['evidence_ids'] = [identity()]
    elif damage == 'candidate': event.data['candidate_only'] = False
    elif damage == 'time': event.data['observed_at'] = s.now.isoformat()
    elif damage == 'model_receipt': event.data['model_receipt'] = {'changed': True}
    elif damage == 'key': event.idempotency_key = 'data:' + identity()
    elif damage == 'kind': event.kind = 'perception.anomaly'
    elif damage == 'job_actor': s.extract.data['actor_id'] = identity()
    elif damage == 'call_job': s.call.data['job_id'] = identity()
    elif damage == 'call_status': s.call.status = 'failed'
    elif damage == 'queue': s.db.audits.remove(s.extract_queue)
    else: s.context = {**s.context, 'stream': SimpleNamespace(id=identity(), actor_id=s.stream.actor_id)}
    with pytest.raises(HTTPException) as error: send(s, data_command(s))
    assert error.value.status_code == 409 and event.version == 7 and event.status == 'blocked_delivery'
    assert subject.MARKERS not in s.stream.data


@pytest.mark.parametrize('ack', ['event', 'observation', 'memory'])
def test_primary_partial_ack_is_integrity_failure_not_a_repeat_delivery(primary_scene, ack):
    s = primary_scene
    if ack == 'event': s.data_event.data['data_reception'] = {'unit_partial': True}
    elif ack == 'observation': s.observation.data['data_reception'] = {'unit_partial': True}
    else:
        s.db.memories.append(SimpleNamespace(data={'event_id': s.data_event.id,
            'observation_id': s.observation.id, 'type': 'observation_evidence'}))
    with pytest.raises(HTTPException) as error: send(s, data_command(s))
    assert error.value.status_code == 409 and s.data_event.status == 'blocked_delivery'


def test_primary_received_or_pending_data_cannot_be_requeued(primary_scene):
    s = primary_scene
    for index, status in enumerate(['received', 'pending_delivery']):
        s.data_event.status = status
        receipt = send(s, command(s, 'retry_event', subject_id=s.data_event.id, version=7,
            request_id='already-terminal-000' + str(index)))
        assert receipt['outcome'] == 'rejected' and s.data_event.status == status and s.data_event.version == 7


def test_primary_data_options_require_readable_source_and_complete_inventory(primary_scene):
    s = primary_scene
    s.monkeypatch.setattr(delivery, 'summary', lambda *args: {'integrity_issue': None,
        'delivery_jobs': [], 'daily_records': [], 'notifications': [],
        'items': [{'observation_id': s.observation.id, 'state': 'waiting_data'}]})
    s.monkeypatch.setattr(subject, '_existing_queue', lambda *args, **kwargs: None)
    view = subject.options(s.db, s.p, s.instance.id, s.stream.id)
    data = [row for row in view['options'] if row['subject_id'] == s.data_event.id]
    assert len(data) == 1 and data[0]['available'] and data[0]['label'] == '重试原数据送达'
    s.source_readable = False
    blocked = subject.options(s.db, s.p, s.instance.id, s.stream.id)
    assert blocked['integrity_issue'] and blocked['options'] == []
    with pytest.raises(HTTPException): send(s, data_command(s))
    s.source_readable = True
    assert subject.options(s.db, s.p, s.instance.id, s.stream.id)['integrity_issue'] is None
    assert send(s, data_command(s))['outcome'] == 'accepted'


def test_primary_data_extra_event_cannot_be_hidden_by_inventory_first_match(primary_scene):
    s = primary_scene
    extra = m.Event(id=identity(), workspace_id=s.p.workspace_id, kind='observation.data',
        idempotency_key='different-key', data=copy.deepcopy(s.data_event.data))
    s.db.events.append(extra)
    with pytest.raises(HTTPException) as error: subject._primary_data_events(s.db, s.p, s.stream)
    assert error.value.status_code == 409
