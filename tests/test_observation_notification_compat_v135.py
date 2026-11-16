"""Real notification carriers plus explicitly labelled pure receipt compatibility.

Carriers are independent outbox rows, as supported by the old routing sender.
They acquire no authority from their own nonce: the actual canonical producer
Events, Observation and Invocation must remain independently verifiable.
Root alone runs these tests; no historical hash is re-signed or repaired.
"""
import copy
from datetime import timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import instance_data_receipts as data_receipts
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Event, PerceptionSignal
from generic_agents.worker import deliver_events
from tests.test_observation_time_compat_v135 import (
    cheap_scene, registered_report, original_anchor, signal_path, cross_read,
    session_timezone, database_snapshot, instant, ZONES, LOS_ANGELES,
)
from tests.test_signal_graph_routing_v134 import effects, reply, _signals
from tests.test_instance_data_receipts_pure import (
    scene as legacy_receipt_scene, receive, acknowledge,
)
from tests.test_typed_payload_journey_v131 import _canonical


PROCESSING_FIELDS = ('observation_id', 'entity_id', 'source_id', 'invocation_id',
    'extracted', 'evidence_ids', 'model_receipt', 'observed_at', 'candidate_only')


def notification(value, observation_id, canonical_id, *, fault=None):
    """Persist a real carrier without replacing its canonical source rows."""
    with Session(engine()) as db:
        canonical = db.get(Event, canonical_id)
        assert canonical.kind == 'perception.anomaly' and canonical.status == 'received'
        assert canonical.idempotency_key == 'anomaly:' + observation_id
        payload = copy.deepcopy(canonical.data)
        if fault == 'raw_offset':
            original = payload['observed_at']
            payload['observed_at'] = instant(original).astimezone(timezone.utc).isoformat()
            assert original != payload['observed_at'] and instant(original) == instant(payload['observed_at'])
        elif fault == 'payload':
            field = next(iter(payload['extracted']['attributes']))
            payload['extracted']['attributes'][field] += 1
        elif fault == 'missing_time':
            payload.pop('observed_at')
        elif fault == 'missing_base_field':
            assert 'model_receipt' in payload
            payload.pop('model_receipt')
        if fault is None or fault == 'no_canonical':
            assert _canonical({key: payload[key] for key in PROCESSING_FIELDS if key in payload}) == _canonical(
                {key: canonical.data[key] for key in PROCESSING_FIELDS if key in canonical.data})
        carrier = Event(workspace_id=value['workspace_id'], kind='perception.anomaly',
            idempotency_key='v135_duplicate_notification:' + uuid4().hex,
            status='pending_delivery', data=payload)
        db.add(carrier); db.flush(); identity = carrier.id
        if fault == 'no_canonical':
            source = db.scalar(select(Event).where(Event.workspace_id == value['workspace_id'],
                Event.idempotency_key == 'data:' + observation_id))
            assert source is not None
            # Keep both original rows/payloads/hashes. Only remove their real
            # canonical identities; the carrier cannot make new identities up.
            canonical.idempotency_key = 'v135_missing_original_anomaly:' + uuid4().hex
            source.idempotency_key = 'v135_missing_original_data:' + uuid4().hex
        db.commit()
    return identity


def receive_notification(value, observation_id, carrier_id):
    deliver_events(workspace_id=value['workspace_id'], observation_id=observation_id,
        event_kind='perception.anomaly')
    with Session(engine()) as db:
        carrier = db.get(Event, carrier_id)
        signal = db.scalar(select(PerceptionSignal).where(PerceptionSignal.workspace_id == value['workspace_id'],
            PerceptionSignal.event_id == carrier.id))
        assert carrier.status == 'received' and signal is not None
        return signal.id


def test_real_duplicate_notification_uses_original_chain_and_keeps_carrier_receipt_cross_timezone(client):
    value = cheap_scene(client)
    with session_timezone(LOS_ANGELES):
        observation, canonical_signal = registered_report(value)
    anchor = original_anchor(value, observation['id'], canonical_signal)
    original_path = signal_path(value, canonical_signal)
    original = reply(client.get(original_path))
    carrier_id = notification(value, observation['id'], anchor['event_id'])
    before = effects()
    with session_timezone(LOS_ANGELES):
        signal_id = receive_notification(value, observation['id'], carrier_id)
    assert effects() == before
    assert signal_id != canonical_signal
    with Session(engine()) as db:
        carrier = db.get(Event, carrier_id); signal = db.get(PerceptionSignal, signal_id)
        assert signal.event_id == carrier_id and signal.status == 'unassigned'
        assert signal.data['basis']['event_hash'] == digest(carrier.data)
        assert signal.data['raw_event_hash'] == digest(carrier.data)
        assert signal.data['basis']['observation_hash'] == anchor['observation_hash']
        assert signal.data['basis']['reported_at'] == anchor['reported_at']
        assert signal.data['basis_hash'] == digest(signal.data['basis'])
        assert signal.data['task_ids'] == []
    endpoint = signal_path(value, signal_id)
    readbacks = cross_read(client, [original_path, endpoint])
    assert _canonical(readbacks[original_path]) == _canonical(original)
    assert readbacks[endpoint]['signal']['integrity_issue'] is None
    assert readbacks[endpoint]['basis']['observation_hash'] == anchor['observation_hash']
    assert original_anchor(value, observation['id'], canonical_signal) == anchor
    unchanged = database_snapshot()
    receive_notification(value, observation['id'], carrier_id)
    assert database_snapshot() == unchanged and effects() == before


def test_real_carrier_cannot_replace_raw_time_payload_or_missing_canonical_authority(client):
    value = cheap_scene(client)
    for fault in ('raw_offset', 'payload', 'missing_time', 'missing_base_field', 'no_canonical'):
        with session_timezone(LOS_ANGELES):
            observation, canonical_signal = registered_report(value)
        anchor = original_anchor(value, observation['id'], canonical_signal)
        with Session(engine()) as db:
            original_signal = copy.deepcopy(db.get(PerceptionSignal, canonical_signal).data)
            original_event = copy.deepcopy(db.get(Event, anchor['event_id']).data)
            original_data_event = copy.deepcopy(db.get(Event, anchor['data_event_id']).data)
        carrier_id = notification(value, observation['id'], anchor['event_id'], fault=fault)
        before = effects()
        with session_timezone(LOS_ANGELES):
            signal_id = receive_notification(value, observation['id'], carrier_id)
        assert effects() == before
        with Session(engine()) as db:
            blocked = db.get(PerceptionSignal, signal_id)
            assert blocked.status == 'blocked_evidence'
            assert blocked.data['basis'] is None and blocked.data['basis_hash'] is None
            assert blocked.data['receipt_issue'] and blocked.data['task_ids'] == []
            assert db.get(PerceptionSignal, canonical_signal).data == original_signal
            assert db.get(Event, anchor['event_id']).data == original_event
            assert db.get(Event, anchor['data_event_id']).data == original_data_event
        damaged = database_snapshot()
        for zone in ZONES:
            with session_timezone(zone):
                response = client.get(signal_path(value, signal_id))
                assert response.status_code == 409, (fault, zone, response.text)
                current = next(row for row in _signals(value) if row['id'] == signal_id)
                assert current['integrity_issue'] and current['basis'] is None
            assert database_snapshot() == damaged
        assert effects() == before


@pytest.mark.parametrize('present', [False, True])
def test_pure_legacy_receipt_omission_and_present_raw_time_have_separate_readonly_contracts(legacy_receipt_scene, present):
    """Existing fake-store adapter contract; this function is not a PG proof."""
    scene = legacy_receipt_scene
    assert scene.observation.observed_at is not None and 'observed_at' not in scene.event.data
    if present:
        scene.event.data['observed_at'] = scene.observation.observed_at.astimezone(
            timezone(timedelta(hours=8))).isoformat()
    receipt = receive(scene); acknowledge(scene, receipt)
    before = copy.deepcopy((scene.observation.data, scene.event.data, scene.db.memories[0].data))
    assert data_receipts.observation_receipt(scene.db, scene.p, scene.observation) == {'data_reception': receipt}
    assert ('observed_at' in scene.event.data) is present
    assert ('observed_at' in scene.db.memories[0].data) is present
    assert (scene.observation.data, scene.event.data, scene.db.memories[0].data) == before


@pytest.mark.parametrize('present,side,change', [
    (True, 'event', 'offset'), (True, 'memory', 'offset'),
    (True, 'event', 'omit'), (True, 'memory', 'omit'),
    (False, 'event', 'add'), (False, 'memory', 'add'),
])
def test_pure_receipt_memory_and_event_must_keep_identical_time_presence_and_raw_spelling(
        legacy_receipt_scene, present, side, change):
    """A same-instant substitution must fail even where its receipt hash ties."""
    scene = legacy_receipt_scene
    if present: scene.event.data['observed_at'] = scene.observation.observed_at.isoformat()
    receipt = receive(scene); acknowledge(scene, receipt)
    target = scene.event.data if side == 'event' else scene.db.memories[0].data
    if change == 'offset':
        original = target['observed_at']
        target['observed_at'] = instant(original).astimezone(timezone(timedelta(hours=8))).isoformat()
        assert target['observed_at'] != original and instant(target['observed_at']) == instant(original)
    elif change == 'omit': target.pop('observed_at')
    else: target['observed_at'] = scene.observation.observed_at.isoformat()
    damaged = copy.deepcopy((scene.observation.data, scene.event.data, scene.db.memories[0].data))
    with pytest.raises(HTTPException) as rejected:
        data_receipts.observation_receipt(scene.db, scene.p, scene.observation)
    assert rejected.value.status_code == 409
    assert (scene.observation.data, scene.event.data, scene.db.memories[0].data) == damaged
