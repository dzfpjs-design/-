"""Actual candidate Jobs/Event/Audit and receivers; synthetic model transport.

The existing local-specialist fixture replaces inference/download transport,
never registration, source authority, receipts, Invocation or candidate logic.
Root alone executes the PostgreSQL and isolated-geo paths in this file.
"""
import copy
from contextlib import contextmanager
import re
from uuid import uuid4

import pytest
from sqlalchemy import event as sql_event, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.worker import deliver_events, process_once
from tests.test_candidate_watches import candidate_runtime, watch
from tests.test_local_observations import setup, mocked_runtime
from tests.test_native_receipts import setup_geo, bridge, scenes, pairs
from tests.test_geo_codec import pytestmark as geo_runtime_mark
from tests.test_observation_time_compat_v135 import (
    session_timezone, database_snapshot, instant, cross_read, ZONES, LOS_ANGELES,
)
from tests.test_typed_payload_journey_v131 import _canonical


AUDIT_ACTION = 'candidate_watch.time_anchor_v135'
ANCHOR_SCHEMA = 'candidate-observation-time-anchor-v135'


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def local_scene(client, candidate_runtime):
    release, obj, base, body, initial = setup(client)
    confirmed, _ = watch(client, base, obj, 'local:' + body['extractor_id'])
    me = ok(client.get('/api/me'))
    return {'client': client, 'release': release, 'object': obj, 'base': base,
        'body': body, 'watch': confirmed, 'initial_id': initial['id'],
        'workspace_id': me['workspace_id'], 'actor_id': me['user_id']}


@contextmanager
def hold_real_data_delivery(scene, observation_id):
    """Hold the actual committed outbox row before the worker's receiver runs.

    The real receiver uses SKIP LOCKED. A second PG transaction takes only the
    actual data Event lock after its producer commit, so no production method
    is patched and no Job/lease/authority is replaced. The lock is released
    before explicitly running the actual data receiver.
    """
    holders = []

    def committed(session):
        if holders: return
        locker = Session(engine())
        try:
            event = locker.scalar(select(m.Event).where(m.Event.workspace_id == scene['workspace_id'],
                m.Event.idempotency_key == 'data:' + observation_id).with_for_update())
            if event is None:
                locker.close(); return
            assert event.status == 'pending_delivery' and 'data_reception' not in event.data
            holders.append(locker)
        except BaseException:
            locker.close()
            raise

    sql_event.listen(Session, 'after_commit', committed)
    try:
        yield holders
    finally:
        sql_event.remove(Session, 'after_commit', committed)
        for locker in holders:
            locker.rollback(); locker.close()


def new_candidate(scene):
    """Actual upload reference -> extraction -> candidate marker, before data ACK."""
    client = scene['client']; base = scene['base']
    created = ok(client.post(base + '/local-observations', json={**scene['body'],
        'request_id': uuid4().hex}), 201)
    identity = created['id']
    with Session(engine()) as db:
        extract = db.scalar(select(m.Job).where(m.Job.workspace_id == scene['workspace_id'],
            m.Job.idempotency_key == 'extract:' + identity))
        assert extract is not None and extract.kind == 'process_local_observation'
        extract_id = extract.id
    with session_timezone(LOS_ANGELES), hold_real_data_delivery(scene, identity) as holders:
        assert process_once(extract_id, deliver=False)
        assert len(holders) == 1
        with Session(engine()) as db:
            row = db.get(m.Observation, identity)
            data_event = db.scalar(select(m.Event).where(m.Event.workspace_id == scene['workspace_id'],
                m.Event.idempotency_key == 'data:' + identity))
            job = db.scalar(select(m.Job).where(m.Job.workspace_id == scene['workspace_id'],
                m.Job.kind == 'check_candidate_watch', m.Job.data['observation_id'].as_string() == identity,
                m.Job.data['watch_id'].as_string() == scene['watch']['id']))
            assert row.status == 'processed' and row.observed_at is None
            assert 'native_instance_id' not in row.data
            assert data_event.status == 'pending_delivery' and 'data_reception' not in data_event.data
            assert job is not None and job.status == 'queued'
            data_event_id, check_id = data_event.id, job.id
            raw_processing = copy.deepcopy(data_event.data)
            call = db.get(m.Invocation, row.data['invocation_id'])
            assert call.status == 'succeeded' and call.data['output'] == row.data['extracted']
            invocation_id = call.id
        assert process_once(check_id, deliver=False)
        with Session(engine()) as db:
            assert db.get(m.Job, check_id).status == 'succeeded'
            data_event = db.get(m.Event, data_event_id)
            assert data_event.status == 'pending_delivery' and data_event.data == raw_processing
            candidate = db.scalar(select(m.Event).where(m.Event.workspace_id == scene['workspace_id'],
                m.Event.idempotency_key == 'candidate:' + scene['watch']['id'] + ':' + identity))
            assert candidate is not None and candidate.status == 'pending_delivery'
            assert db.scalar(select(m.PerceptionSignal.id).where(m.PerceptionSignal.event_id == candidate.id)) is None
            candidate_id = candidate.id
    proof = {'observation_id': identity, 'extract_job_id': extract_id, 'check_id': check_id,
        'candidate_id': candidate_id, 'data_event_id': data_event_id, 'invocation_id': invocation_id,
        'raw_processing': raw_processing}
    proof['anchor'], proof['audit_id'] = assert_real_anchor(scene, proof)
    return proof


def assert_real_anchor(scene, proof):
    with Session(engine()) as db:
        job = db.get(m.Job, proof['check_id']); event = db.get(m.Event, proof['candidate_id'])
        result = job.data['result']; anchor = event.data['time_anchor']
        assert set(anchor) == {'schema', 'source_event_id', 'source_event_hash', 'observed_at'}
        assert anchor['schema'] == ANCHOR_SCHEMA and anchor['source_event_id'] == proof['data_event_id']
        assert re.fullmatch(r'[a-f0-9]{64}', anchor['source_event_hash']) and anchor['observed_at'] is None
        assert _canonical(result['time_anchor']) == _canonical(anchor)
        assert result['matched'] is True and result['event_id'] == event.id
        assert result['machine_hash'] == event.data['machine_hash'] and result['candidate_only'] is True
        assert result['cloud_calls'] == 0
        records = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == scene['workspace_id'],
            m.Audit.action == AUDIT_ACTION, m.Audit.object_id == job.id)))
        assert len(records) == 1
        record = records[0]
        assert record.actor_id == job.data['actor_id'] == scene['actor_id']
        assert record.data == {'schema': 'candidate-observation-time-audit-v135',
            'workspace_id': scene['workspace_id'], 'actor_id': scene['actor_id'], 'job_id': job.id,
            'event_id': event.id, 'watch_id': scene['watch']['id'],
            'observation_id': proof['observation_id'], 'definition_hash': job.data['definition_hash'],
            'anchor_hash': digest(anchor), 'machine_hash': result['machine_hash'],
            'selection_hash': digest(result['selection']), 'result_hash': digest(result)}
        return copy.deepcopy(anchor), record.id


def receive_original_data(scene, proof):
    deliver_events(workspace_id=scene['workspace_id'], observation_id=proof['observation_id'],
        event_kind='observation.data')
    with Session(engine()) as db:
        event = db.get(m.Event, proof['data_event_id']); row = db.get(m.Observation, proof['observation_id'])
        assert event.status == 'received' and event.data['data_reception']
        assert row.data['data_reception'] == event.data['data_reception']
        assert all(_canonical(event.data[key]) == _canonical(value)
            for key, value in proof['raw_processing'].items())
        assert digest(event.data) != digest(proof['raw_processing'])
        assert 'data_reception' not in proof['raw_processing']
        assert db.get(m.Event, proof['candidate_id']).status == 'pending_delivery'
        assert db.scalar(select(m.Memory.id).where(m.Memory.workspace_id == scene['workspace_id'],
            m.Memory.data['event_id'].as_string() == event.id)) is not None
    assert assert_real_anchor(scene, proof) == (proof['anchor'], proof['audit_id'])


def receive_candidate(scene, proof):
    deliver_events(workspace_id=scene['workspace_id'], observation_id=proof['observation_id'],
        event_kind='perception.candidate')
    with Session(engine()) as db:
        event = db.get(m.Event, proof['candidate_id'])
        signal = db.scalar(select(m.PerceptionSignal).where(m.PerceptionSignal.event_id == event.id))
        assert event.status == 'received' and signal is not None
        return signal.id


def export_path(scene, signal_id):
    return scene['base'] + '/signals/' + signal_id + '/export'


def stable_work():
    with Session(engine()) as db:
        return {model.__tablename__: {row.id: digest({'data': row.data,
            'status': getattr(row, 'status', None)}) for row in db.scalars(select(model))}
            for model in (m.Task, m.Job, m.Invocation, m.CloudCall, m.Memory)}


def test_candidate_anchor_precedes_real_data_annotations_and_remains_readonly_cross_timezone(local_scene):
    scene = local_scene; client = scene['client']; proof = new_candidate(scene)
    receive_original_data(scene, proof)
    signal_id = receive_candidate(scene, proof)
    endpoint = export_path(scene, signal_id)
    exported = cross_read(client, [endpoint])[endpoint]
    assert exported['signal']['integrity_issue'] is None
    assert exported['basis']['reported_at'] is None and exported['basis']['candidate_only'] is True
    assert exported['basis']['observation_hash'] == exported['signal']['basis']['observation_hash']
    assert assert_real_anchor(scene, proof) == (proof['anchor'], proof['audit_id'])
    before = database_snapshot()
    for zone in ZONES:
        with session_timezone(zone):
            local = ok(client.get(scene['base'] + '/local-observations/' + proof['observation_id']))
            assert local['status'] == 'processed' and local['data_received'] and local['integrity_error'] is None
            assert local['source_clock'] is None and local['candidate_only'] is True
            records = ok(client.get(scene['base'] + '/candidate-watches/' + scene['watch']['id'] + '/checks'))['records']
            current = next(row for row in records if row['id'] == proof['check_id'])
            assert current['integrity_issue'] is None and current['status'] == 'succeeded'
            assert current['result']['time_anchor'] == proof['anchor']
            assert current['result']['delivery_status'] == 'received'
            with Session(engine()) as db:
                assert instant(current['created_at']) == db.get(m.Job, proof['check_id']).created_at
                call = db.get(m.Invocation, proof['invocation_id'])
                assert local['output'] == call.data['output']
        assert database_snapshot() == before


def test_candidate_event_job_and_audit_markers_cannot_downgrade_before_first_signal(local_scene):
    scene = local_scene; client = scene['client']
    faults = ('event_marker_missing', 'job_marker_missing', 'both_markers_missing',
        'audit_missing', 'audit_wrong_binding', 'audit_wrong_object', 'source_processing_changed')
    for fault in faults:
        proof = new_candidate(scene)
        receive_original_data(scene, proof)
        with Session(engine()) as db:
            event = db.get(m.Event, proof['candidate_id']); job = db.get(m.Job, proof['check_id'])
            record = db.get(m.Audit, proof['audit_id'])
            assert db.scalar(select(m.PerceptionSignal.id).where(m.PerceptionSignal.event_id == event.id)) is None
            if fault in {'event_marker_missing', 'both_markers_missing'}:
                value = copy.deepcopy(event.data); value.pop('time_anchor'); event.data = value
            if fault in {'job_marker_missing', 'both_markers_missing'}:
                value = copy.deepcopy(job.data); value['result'].pop('time_anchor'); job.data = value
            if fault == 'audit_missing': db.delete(record)
            elif fault == 'audit_wrong_binding':
                value = copy.deepcopy(record.data); value['job_id'] = proof['extract_job_id']; record.data = value
            elif fault == 'audit_wrong_object': record.object_id = proof['extract_job_id']
            elif fault == 'source_processing_changed':
                source_event = db.get(m.Event, proof['data_event_id'])
                value = copy.deepcopy(source_event.data); value['observed_at'] = '2026-01-01T00:00:00+00:00'
                source_event.data = value
            db.commit()
        before = stable_work()
        signal_id = receive_candidate(scene, proof)
        assert stable_work() == before
        with Session(engine()) as db:
            signal = db.get(m.PerceptionSignal, signal_id)
            assert signal.status == 'blocked_evidence' and signal.data['basis'] is None
            assert signal.data['basis_hash'] is None and signal.data['receipt_issue']
            if fault not in {'audit_missing', 'audit_wrong_binding', 'audit_wrong_object'}:
                assert db.get(m.Audit, proof['audit_id']).data['anchor_hash'] == digest(proof['anchor'])
        damaged = database_snapshot()
        for zone in ZONES:
            with session_timezone(zone):
                response = client.get(export_path(scene, signal_id))
                assert response.status_code == 409, (fault, zone, response.text)
                checks = ok(client.get(scene['base'] + '/candidate-watches/' + scene['watch']['id'] + '/checks'))['records']
                checked = next(row for row in checks if row['id'] == proof['check_id'])
                assert checked['integrity_issue'] and checked['result'] is None
            assert database_snapshot() == damaged
        assert stable_work() == before


def test_129_real_public_rechecks_reuse_one_event_without_new_inference(local_scene):
    scene = local_scene; client = scene['client']; proof = new_candidate(scene)
    receive_original_data(scene, proof); signal_id = receive_candidate(scene, proof)
    watch_value = scene['watch']; identities = {proof['check_id']}
    with Session(engine()) as db:
        original_invocations = set(db.scalars(select(m.Invocation.id)))
        original_cloud = set(db.scalars(select(m.CloudCall.id)))
    for _ in range(128):
        status_path = scene['base'] + '/candidate-watches/' + watch_value['id'] + '/status'
        watch_value = ok(client.put(status_path, json={'version': watch_value['version'], 'status': 'paused'}))
        watch_value = ok(client.put(status_path, json={'version': watch_value['version'], 'status': 'enabled'}))
        queued = ok(client.post(scene['base'] + '/candidate-watches/' + watch_value['id'] + '/check-existing',
            json={'version': watch_value['version']}))
        assert not queued['has_more'] and len(queued['job_ids']) == 1
        identity = queued['job_ids'][0]; assert identity not in identities; identities.add(identity)
        assert process_once(identity, deliver=False)
        with Session(engine()) as db:
            job = db.get(m.Job, identity)
            assert job.status == 'succeeded' and job.data['result']['event_id'] == proof['candidate_id']
            assert job.data['result']['time_anchor'] == proof['anchor']
    assert len(identities) == 129
    with Session(engine()) as db:
        assert set(db.scalars(select(m.Invocation.id))) == original_invocations
        assert set(db.scalars(select(m.CloudCall.id))) == original_cloud
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == scene['workspace_id'],
            m.Event.kind == 'perception.candidate', m.Event.data['observation_id'].as_string() == proof['observation_id'])))
        assert [row.id for row in events] == [proof['candidate_id']]
        records = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == scene['workspace_id'],
            m.Audit.action == AUDIT_ACTION, m.Audit.data['event_id'].as_string() == proof['candidate_id'])))
        assert {row.object_id for row in records} == identities and len(records) == 129
        assert list(db.scalars(select(m.PerceptionSignal.id).where(m.PerceptionSignal.event_id == proof['candidate_id']))) == [signal_id]
    endpoint = export_path(scene, signal_id)
    cross_read(client, [endpoint])
    checks = ok(client.get(scene['base'] + '/candidate-watches/' + watch_value['id'] + '/checks'))
    assert checks['has_more'] and len(checks['records']) == 50
    assert all(row['integrity_issue'] is None and row['result']['event_id'] == proof['candidate_id']
        for row in checks['records'])


@geo_runtime_mark
def test_native_unknown_time_packet_is_not_upgraded_to_local_candidate_anchor(client, pairs):
    _, _, release, body, base, _ = setup_geo(client, pairs)
    with Session(engine()) as db:
        obj = db.get(m.Entity, body['entity_ids'][0]); object_id = obj.id
    confirmed, _ = watch(client, base, {'id': object_id}, 'native:GeoPair', threshold=5)
    _, _ = bridge(client, base)
    with Session(engine()) as db:
        row = db.scalar(select(m.Observation).where(m.Observation.data['native_instance_id'].as_string() == release['instance_id']))
        assert row.observed_at is None and row.data['native_packet']['candidate_only'] is True
        identity = row.id; original_packet = copy.deepcopy(row.data['native_packet'])
        jobs = list(db.scalars(select(m.Job).where(m.Job.kind == 'check_candidate_watch',
            m.Job.data['observation_id'].as_string() == row.id)))
        assert len(jobs) == 1; check_id = jobs[0].id
        invocations = set(db.scalars(select(m.Invocation.id)))
        cloud = set(db.scalars(select(m.CloudCall.id)))
    assert process_once(check_id, deliver=False)
    deliver_events(observation_id=identity, event_kind='perception.candidate')
    with Session(engine()) as db:
        job = db.get(m.Job, check_id); assert job.status == 'succeeded'
        event = db.get(m.Event, job.data['result']['event_id'])
        assert 'time_anchor' not in job.data['result'] and 'time_anchor' not in event.data
        assert db.scalar(select(m.Audit.id).where(m.Audit.action == AUDIT_ACTION, m.Audit.object_id == job.id)) is None
        signal = db.scalar(select(m.PerceptionSignal).where(m.PerceptionSignal.event_id == event.id))
        assert signal is not None and signal.data['basis']['reported_at'] is None
        signal_id = signal.id
    endpoint = base + '/signals/' + signal_id + '/export'
    exported = cross_read(client, [endpoint])[endpoint]
    assert exported['signal']['integrity_issue'] is None and exported['basis']['reported_at'] is None
    with Session(engine()) as db:
        assert db.get(m.Observation, identity).data['native_packet'] == original_packet
        assert set(db.scalars(select(m.Invocation.id))) == invocations
        assert set(db.scalars(select(m.CloudCall.id))) == cloud
