"""Stubbed delivery/receiver contracts, not PG, RTSP, inference or real ACK proof."""
import copy
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from generic_agents import audio_stream_delivery as subject
from generic_agents.contracts import digest


class MemoryDB:
    def __init__(self):
        self.rows = {}; self.jobs = []; self.proofs = []; self.events = []; self.signals = []
        self.memories = []; self.observations = []; self.contexts = {}; self.data_events = {}; self.info = {}
        self.receipt_error = False; self.receipt_reads = 0; self.actor_checks = 0

    def add(self, value):
        if not value.id: value.id = str(uuid4())
        name = type(value).__name__
        if name == 'Job':
            value.status = value.status or 'queued'; value.version = value.version or 1; self.jobs.append(value)
        elif name == 'Audit': self.proofs.append(value)
        elif name == 'PerceptionSignal':
            value.version = value.version or 1; self.signals.append(value)
        else: raise AssertionError('unexpected fixture write: ' + name)
        self.rows[value.id] = value

    def flush(self): pass

    def execute(self, query, parameters):
        assert 'pg_advisory_xact_lock' in str(query) and type(parameters['key']) is int

    def scalar(self, query):
        params = query.compile().params.values()
        ids = [value for value in params if isinstance(value, str) and value in self.rows]
        return self.rows[ids[0]] if ids else None

    def scalars(self, query):
        name = query.column_descriptions[0]['entity'].__name__
        params = list(query.compile().params.values())
        if name == 'Memory':
            return [m for m in self.memories if m.data['observation_id'] in params or m.data['event_id'] in params]
        if name == 'Job': return [j for j in self.jobs if j.idempotency_key in params]
        if name == 'Event': return [e for e in self.events if e.idempotency_key in params]
        if name == 'PerceptionSignal': return [s for s in self.signals if s.event_id in params]
        if name == 'Audit':
            values = [a for a in self.proofs if a.action in params]
            if 'received' in params:
                values = [a for a in values if a.data.get('frozen', {}).get('mode') == 'received']
            return [a for a in values if a.data.get('key') in params or a.data.get('event_id') in params
                or a.data.get('frozen', {}).get('observation_id') in params]
        raise AssertionError('unexpected fixture query: ' + name)


@pytest.fixture
def scene(monkeypatch):
    import generic_agents
    from generic_agents import observation_integrity, instance_data_receipts, relations
    db = MemoryDB(); now = datetime(2026, 10, 30, 3, tzinfo=timezone.utc)
    p = SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()), role='operator', session_id='unit-worker')
    stream = SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id, status='recording')
    binding = {'binding_hash': 'b' * 64, 'source_capability_id': str(uuid4()),
        'model_capability_id': str(uuid4()), 'model_capability_hash': 'c' * 64}
    scope = {'instance_id': str(uuid4()), 'release_id': str(uuid4()), 'business_id': 'dynamic-business', 'entity_id': str(uuid4())}
    result = {'schema': 'local-environment-sound-v1', 'cloud_calls': 0, 'classification': {
        'inference_performed': True, 'all_samples_covered': True, 'asr_used': False, 'vad_used': False,
        'threshold': .5, 'windows': [{'index': 0}, {'index': 1}],
        'candidates': [{'class_index': 1, 'label': 'unit-only sound', 'max_score': .8, 'window_indices': [0, 1]}]}}
    def new_row(*, status='processed', ack=False):
        identity = str(uuid4()); raw = b'unit fixture, not a real recording'
        evidence = SimpleNamespace(id=str(uuid4()), sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw), media_type='audio/wav')
        row = SimpleNamespace(id=identity, workspace_id=p.workspace_id, entity_id=scope['entity_id'],
            source_id=binding['source_capability_id'], observed_at=None, status=status,
            data={'candidate_only': True, 'extractor_id': binding['model_capability_id'], 'invocation_id': str(uuid4())})
        capture = {'registered_at': (now + timedelta(seconds=len(db.observations))).isoformat(), 'registration_day': '2026-10-30',
            'observation_id': identity, 'stream_id': stream.id, **scope, 'evidence_id': evidence.id,
            'binding_hash': binding['binding_hash'], 'input_sha256': evidence.sha256, 'size_bytes': evidence.size_bytes,
            'source_clock_utc': 'unknown', 'candidate_only': True}
        db.rows[row.id] = row; db.observations.append(row)
        db.contexts[row.id] = {'stream': stream, 'binding': binding, 'capture': capture, 'evidence': evidence}
        if status == 'processed':
            event = SimpleNamespace(id=str(uuid4()), status='pending_delivery', data={}, received_at=None)
            db.data_events[row.id] = event
            if ack: set_ack(row)
        return row
    def set_ack(row):
        event = db.data_events[row.id]; event.status = 'received'; event.received_at = now
        db.memories.append(SimpleNamespace(id=str(uuid4()), data={'event_id': event.id,
            'observation_id': row.id, 'type': 'observation_evidence'}))
    def receipt(database, principal, row):
        assert database is db and principal.workspace_id == p.workspace_id
        db.receipt_reads += 1
        if db.receipt_error: raise HTTPException(409, 'injected invalid independent receipt')
        return {'data_reception': {'schema': 'instance-data-reception-receipt-v1', 'candidate_only': True,
            'declaration_status': 'declared', 'receipt_hash': digest({'observation_id': row.id})}}
    def marker(database, principal, row): return row.id in db.contexts
    bridge = SimpleNamespace(observation_context=lambda database, principal, row, execution=False: db.contexts[row.id],
        observation_marker=marker, stream_observations=lambda database, principal, current: list(db.observations))
    monkeypatch.setitem(sys.modules, 'generic_agents.audio_streams', bridge)
    monkeypatch.setattr(generic_agents, 'audio_streams', bridge, raising=False)
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', receipt)
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda database, principal, row, allow_candidates=False:
        {'extracted': {'attributes': {}, 'anomalies': [], 'environment_sound': copy.deepcopy(result)}})
    monkeypatch.setattr(subject, '_data_event', lambda database, principal, row: db.data_events.get(row.id))
    monkeypatch.setattr(subject, '_row', lambda database, principal, cls, identity: db.rows[identity])
    monkeypatch.setattr(subject, '_audits', lambda database, principal, action, identity:
        [a for a in db.proofs if a.action == action and a.object_id == identity])
    monkeypatch.setattr(relations, 'lock_current_actor', lambda database, principal: setattr(db, 'actor_checks', db.actor_checks + 1))
    def owned(database, principal, job):
        assert database is db and principal.user_id == job.data['actor_id'] and job.status == 'running'
    monkeypatch.setattr(subject, '_owned', owned)
    def issue(database, principal, kind, key, payload):
        event = SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, kind=kind, idempotency_key=key,
            data=copy.deepcopy(payload), status='pending_delivery', attempts=0, version=1, received_at=None)
        db.events.append(event); db.rows[event.id] = event
        return event
    monkeypatch.setattr(subject, 'enqueue_event', issue)
    row = new_row(ack=True)
    return SimpleNamespace(db=db, p=p, stream=stream, binding=binding, row=row, result=result,
        new_row=new_row, set_ack=set_ack, bridge=bridge, now=now)


def run_delivery(s):
    job = subject.enqueue_received(s.db, s.p, s.row); job.status = 'running'
    result = subject.process(s.db, s.p, job); job.status = 'succeeded'
    return job, result


def test_received_event_alone_is_not_an_independent_ack_and_bad_receipt_is_never_repaired(scene):
    s = scene; s.db.memories.clear()
    with pytest.raises(HTTPException): subject.enqueue_received(s.db, s.p, s.row)
    assert not s.db.jobs and not s.db.proofs
    s.set_ack(s.row); s.db.receipt_error = True
    with pytest.raises(HTTPException): subject.enqueue_received(s.db, s.p, s.row)
    assert not s.db.jobs and s.db.data_events[s.row.id].status == 'received'


def test_queue_original_replay_is_idempotent_and_audit_loss_cannot_recreate_it(scene):
    s = scene; job = subject.enqueue_received(s.db, s.p, s.row)
    assert subject.enqueue_received(s.db, s.p, s.row) is job
    assert len(s.db.jobs) == len(s.db.proofs) == 1 and s.db.receipt_reads >= 2
    s.db.proofs.clear()
    with pytest.raises(HTTPException): subject.enqueue_received(s.db, s.p, s.row)
    assert s.db.jobs == [job]


def test_process_uses_full_inventory_but_only_received_numeric_values_and_no_task_or_inference(scene):
    s = scene; pending = s.new_row(); failed = s.new_row(status='failed')
    _, result = run_delivery(s)
    assert s.db.actor_checks == 1 and result['task_created'] is False
    daily = next(e for e in s.db.events if e.kind == 'perception.daily')
    counts = daily.data['counts']
    assert counts == {'captured_records': 3, 'processing': 0, 'failed': 1, 'waiting_data': 1,
        'received': 1, 'analysis_windows': 2, 'candidate_classes': 1, 'candidate_windows': 2}
    for item in daily.data['items']:
        if item['observation_id'] in {pending.id, failed.id}:
            assert item['analysis_windows'] is None and item['threshold'] is None and item['candidates'] is None
    assert {e.kind for e in s.db.events} == {'perception.daily', 'perception.audio_candidate'}
    assert daily.data['source_clock_utc'] == 'unknown'
    assert daily.data['date_basis'] == 'audio_original_registered'


def test_old_pending_daily_version_survives_later_ack_and_new_version_is_appended(scene):
    s = scene; pending = s.new_row(); _, first = run_delivery(s)
    old = next(e for e in s.db.events if e.id == first['daily_event_id']); saved = copy.deepcopy(old.data)
    assert subject.validate_daily(s.db, old) == saved
    s.set_ack(pending)
    assert subject.validate_daily(s.db, old) == saved and old.data == saved
    job = subject.enqueue_received(s.db, s.p, pending); job.status = 'running'
    second = subject.process(s.db, s.p, job); job.status = 'succeeded'
    assert first['daily_event_id'] != second['daily_event_id'] and first['revision_hash'] != second['revision_hash']
    assert old.data == saved


def test_zero_candidates_do_not_generate_a_notification_or_a_no_event_fact(scene):
    s = scene; s.result['classification']['candidates'] = []
    _, result = run_delivery(s)
    assert result['candidate_event_id'] is None and len(s.db.events) == 1
    assert s.db.events[0].data['counts']['candidate_classes'] == 0
    assert s.db.events[0].data['candidate_only'] is True and s.db.events[0].data['entity_state_updated'] is False


def test_all_failed_inventory_has_a_day_revision_without_ack_or_candidate_values_and_later_received_job_is_distinct(scene):
    s = scene
    # Configure a failed-input unit fixture before the first tested operation.
    # No real received Event, Memory, model or capture was created by this test.
    s.row.status = 'failed'; s.row.data.pop('invocation_id')
    s.db.data_events.clear(); s.db.memories.clear(); s.stream.status = 'failed'
    job = subject.enqueue_snapshot(s.db, s.p, s.stream, s.row.id)
    assert job.data['mode'] == 'inventory' and job.data['result_hash'] is None and job.data['reception_hash'] is None
    assert subject.enqueue_snapshot(s.db, s.p, s.stream, s.row.id) is job and len(s.db.jobs) == 1
    job.status = 'running'; result = subject.process(s.db, s.p, job); job.status = 'succeeded'
    old = s.db.rows[result['daily_event_id']]
    assert old.data['counts']['received'] == 0 and old.data['counts']['failed'] == 1
    assert old.data['items'][0]['candidates'] is None and result['candidate_event_id'] is None
    assert subject.validate_daily(s.db, old) == old.data
    saved = copy.deepcopy(old.data)
    # Later progress is another explicit unit frame, not an end-to-end retry.
    s.row.status = 'processed'; s.row.data['invocation_id'] = str(uuid4())
    s.db.data_events[s.row.id] = SimpleNamespace(id=str(uuid4()), status='pending_delivery', data={}, received_at=None)
    s.set_ack(s.row)
    received = subject.enqueue_received(s.db, s.p, s.row)
    assert received.id != job.id and received.data['mode'] == 'received' and len(s.db.jobs) == 2
    assert subject.validate_daily(s.db, old) == saved


def test_inventory_revision_partitions_every_original_registration_day_without_emitting_notifications(scene):
    s = scene; last = s.new_row(status='failed'); capture = s.db.contexts[last.id]['capture']
    capture['registered_at'] = (s.now + timedelta(days=1)).isoformat(); capture['registration_day'] = '2026-10-31'
    s.stream.status = 'completed'
    job = subject.enqueue_snapshot(s.db, s.p, s.stream, last.id); job.status = 'running'
    result = subject.process(s.db, s.p, job); job.status = 'succeeded'
    assert len(result['daily_event_ids']) == 2 and set(result['revision_hashes']) == {'2026-10-30', '2026-10-31'}
    assert result['candidate_event_id'] is None and all(event.kind == 'perception.daily' for event in s.db.events)
    for identity in result['daily_event_ids']:
        value = subject.validate_daily(s.db, s.db.rows[identity])
        assert all(item['registration_day'] == value['registration_day'] for item in value['items'])
        assert value['date_basis'] == 'audio_original_registered'


def test_running_producer_is_pending_without_weakening_kind_or_audit_guards(scene):
    s = scene; job, result = run_delivery(s); event = s.db.rows[result['daily_event_id']]
    job.status = 'running'
    assert subject.event_delivery_state(s.db, s.p, event) == 'pending_producer'
    assert event.attempts == 0 and event.status == 'pending_delivery'
    event.kind = 'perception.anomaly'
    with pytest.raises(HTTPException): subject.event_delivery_state(s.db, s.p, event)


def test_notice_received_once_links_only_original_audio_and_preserves_human_note_when_source_breaks(scene):
    s = scene; _, result = run_delivery(s); event = s.db.rows[result['candidate_event_id']]
    assert subject.receive(s.db, event) is True
    assert subject.receive(s.db, event) is True and len(s.db.signals) == 1
    event.status = 'received'; event.received_at = s.now
    signal = s.db.signals[0]; signal.data = {**signal.data, 'note': '独立人工说明保留'}
    basis = subject.checked_signal(s.db, s.p, signal)
    reference = basis['original_model_result']
    assert reference['stream_id'] == s.stream.id and reference['observation_id'] == s.row.id
    assert reference['content_path'].endswith('/captures/' + s.row.id + '/content')
    assert signal.data['task_ids'] == [] and reference['notification_only'] is True
    s.db.receipt_error = True
    view = subject.signal_view(s.db, s.p, signal)
    assert view['basis'] is None and view['audio_stream_reference'] is None and view['integrity_issue']
    assert view['note'] == '独立人工说明保留' and view['tasks'] == []


def test_stripped_markers_still_select_family_from_independent_producer_and_receive_audits(scene):
    s = scene; _, result = run_delivery(s); event = s.db.rows[result['candidate_event_id']]
    subject.receive(s.db, event); signal = s.db.signals[0]
    event.data.pop('schema'); event.idempotency_key = 'stripped-event-prefix'
    signal.data.pop('schema'); signal.data.pop('basis')
    assert subject.handles_event(s.db, s.p, event) is True
    assert subject.is_signal(s.db, s.p, signal) is True
    with pytest.raises((HTTPException, KeyError)): subject.checked_signal(s.db, s.p, signal)


def test_block_preserves_signed_payload_uses_real_issuer_and_does_not_repeat_or_ack(scene):
    s = scene; _, result = run_delivery(s); event = s.db.rows[result['candidate_event_id']]
    event.data['producer_actor_id'] = 'untrusted-changed-payload'
    before = copy.deepcopy(event.data)
    subject.block_delivery(s.db, event, '原路由缺失')
    assert event.status == 'blocked_delivery' and event.attempts == 1 and event.data == before
    blocked = [p for p in s.db.proofs if p.action == subject.BLOCKED]
    assert len(blocked) == 1 and blocked[0].actor_id == s.p.user_id and blocked[0].data['received'] is False
    subject.block_delivery(s.db, event, '重复调用')
    assert event.attempts == 1 and len([p for p in s.db.proofs if p.action == subject.BLOCKED]) == 1
    assert not s.db.signals


def test_block_without_a_trustworthy_issuer_does_not_manufacture_an_audit_identity(scene):
    s = scene; event = SimpleNamespace(id=str(uuid4()), workspace_id=s.p.workspace_id,
        kind='perception.audio_candidate', data={}, status='pending_delivery', attempts=0, version=1)
    s.db.rows[event.id] = event
    subject.block_delivery(s.db, event, '未知原件')
    assert event.status == 'blocked_delivery' and not s.db.proofs
    assert s.db.info['audio_delivery_blocked']['event_id'] == event.id and event.data == {}


def test_summary_separates_data_ack_from_daily_and_notification_delivery(scene, monkeypatch):
    s = scene; s.stream.status = 'completed'
    monkeypatch.setattr(subject, '_job_inventory', lambda *args: [])
    monkeypatch.setattr(subject, '_event_inventory', lambda *args: [])
    value = subject.summary(s.db, s.p, s.stream)
    assert value['state'] == 'data_received' and value['counts']['received'] == 1
    assert value['daily_ack_is_separate'] is True and value['candidate_notification_delivery_is_separate'] is True
    s.new_row(status='failed')
    assert subject.summary(s.db, s.p, s.stream)['state'] == 'incomplete'


@pytest.mark.parametrize('workspace', [None, '', 'other', str(uuid4()).upper()])
def test_delivery_never_interprets_missing_or_invalid_scope_as_all_workspaces(workspace):
    with pytest.raises(HTTPException): subject.deliver_pending(workspace)
