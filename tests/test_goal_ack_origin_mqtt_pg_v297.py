"""Actual PG capture/managed delivery/SDK/independent observation reception.

Uses existing normal MQTT capture and worker fixtures, with no origin/receiver
spies. The Task-scope argument is an explicit target stub, not a published
V288 Task/Goal proof; the fixture calls capture, not a real broker/PUBACK.
Root separately verifies the real broker and same-Task evaluation journey.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import goal_ack_origin_mqtt_v297 as subject
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Event, Memory, MqttReceipt, Observation
from generic_agents.mqtt_inbox import capture
from generic_agents.worker import deliver_events
from tests.test_mqtt_binding_routes import managed_pair, process_all
from tests.test_mqtt_observations import save


def original(client, account, monkeypatch, *, declare_test=False):
    fixture, destinations, routes = managed_pair(client, account, monkeypatch)
    declaration = None
    if declare_test:
        gateway = fixture[0]
        response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/test-origin', json={
            'request_id': str(uuid4()), 'expected_gateway_version': gateway['version'],
            'expected_config_hash': gateway['config_hash'], 'source_label': '厂家参数软件模拟传感器',
            'basis_type': 'manufacturer_parameters',
            'basis_notes': '仅验证协议和参数处理，数值由软件产生；未验证现场或连接物理设备。'})
        assert response.status_code == 201, response.text
        declaration = response.json()
    receipt, raw = save(fixture, record='mqtt_goal_ack_v297')
    identities = process_all(receipt.id)
    p = fixture[3]; release, _instance, obj, _field, _rule = destinations[0]
    with Session(engine()) as db:
        selected = next(db.get(Observation, identity) for identity in identities
            if db.get(Observation, identity).entity_id == obj['id'])
        identity = selected.id
    deliver_events(workspace_id=p.workspace_id, observation_id=identity, event_kind='observation.data')
    target = SimpleNamespace(id='explicit_scope_only_not_a_persisted_task', workspace_id=p.workspace_id,
        package_id=release['package_id'], data={'entity_ids': [obj['id']],
            'instance_id': release['instance_id'], 'instance_release_id': release['id']})
    return SimpleNamespace(fixture=fixture, receipt_id=receipt.id, raw=raw, p=p,
        identity=identity, target=target, declaration=declaration)


@pytest.mark.parametrize('redeliver', [False, True])
def test_real_mqtt_cold_origin_keeps_packet_and_business_ack_without_acquisition(client, account, monkeypatch, redeliver):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        obs = db.get(Observation, s.identity); cut = db.scalar(select(func.clock_timestamp()))
        before = subject.checked_origin(db, s.p, obs, task=s.target, cut=cut)
        saved = copy.deepcopy((obs.data, db.get(MqttReceipt, s.receipt_id).data))
        assert before['origin_kind'] == subject.KIND and before['origin_task_id'] is None
        assert before['simulation_only'] is False and before['test_only'] is False
        assert subject.handles(db, s.p, obs) is True and not db.new and not db.dirty and not db.deleted
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    if redeliver:
        gateway = s.fixture[0]
        with Session(engine()) as db:
            duplicate = capture(db, s.p, gateway['id'], gateway['version'], 'sensor/one', s.raw, 1, duplicate=True)
            assert duplicate.id == s.receipt_id
            db.commit()
    with Session(engine()) as db:
        obs = db.get(Observation, s.identity)
        after = subject.checked_origin(db, s.p, obs, task=s.target, cut=cut)
        assert after == before and obs.data == saved[0]
        assert db.get(MqttReceipt, s.receipt_id).data['deliveries'] == (2 if redeliver else 1)
        event = db.scalar(select(Event).where(Event.workspace_id == s.p.workspace_id,
            Event.kind == 'observation.data', Event.idempotency_key == 'data:' + obs.id))
        memories = list(db.scalars(select(Memory).where(Memory.workspace_id == s.p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')))
        assert event.status == 'received' and len(memories) == 1
        assert not db.new and not db.dirty and not db.deleted


def test_real_mqtt_business_receipt_after_cut_does_not_backdate_knowledge(client, account, monkeypatch):
    s = original(client, account, monkeypatch)
    with Session(engine()) as db:
        obs = db.get(Observation, s.identity)
        event = db.scalar(select(Event).where(Event.workspace_id == s.p.workspace_id,
            Event.idempotency_key == 'data:' + obs.id))
        with pytest.raises(HTTPException):
            subject.checked_origin(db, s.p, obs, task=s.target, cut=event.received_at - timedelta(microseconds=1))
        assert not db.new and not db.dirty and not db.deleted


def test_normal_conflicting_packet_quarantine_cannot_reuse_old_mqtt_origin(client, account, monkeypatch):
    s = original(client, account, monkeypatch)
    save(s.fixture, record='mqtt_goal_ack_v297', value=1)
    with Session(engine()) as db:
        obs = db.get(Observation, s.identity)
        assert db.get(MqttReceipt, s.receipt_id).status == 'quarantined'
        with pytest.raises(HTTPException):
            subject.checked_origin(db, s.p, obs, task=s.target, cut=db.scalar(select(func.clock_timestamp())))
        assert not db.new and not db.dirty and not db.deleted


def test_prospective_normal_api_declaration_is_cold_verified_with_managed_observation_ack(client, account, monkeypatch):
    s = original(client, account, monkeypatch, declare_test=True)
    with Session(engine()) as db:
        obs = db.get(Observation, s.identity); receipt = db.get(MqttReceipt, s.receipt_id)
        before = copy.deepcopy((obs.data, receipt.data))
        value = subject.checked_origin(db, s.p, obs, task=s.target, cut=db.scalar(select(func.clock_timestamp())))
        assert value['simulation_only'] is True and value['test_only'] is True
        assert value['mqtt_origin']['test_origin'] == {'audit_id': s.declaration['audit_id'],
            'declaration_hash': s.declaration['declaration_hash']}
        assert (Audit, s.declaration['audit_id']) in subject.lock_rows(value)
        assert s.declaration['physical_device_connected'] is False and s.declaration['field_verified'] is False
        assert (obs.data, receipt.data) == before and not db.new and not db.dirty and not db.deleted
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
