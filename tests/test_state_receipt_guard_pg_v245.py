"""Normal generated instance, local numeric extraction and real PG data ACK.

Faults deliberately damage isolated persisted links. No cloud/device/physical
truth is claimed, and no reception checker or custody adapter is mocked.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (Capability, CloudCall, Entity, Event,
                                  InstanceRelease, Invocation, Memory, Observation, ObjectState)
from generic_agents.worker import deliver_events
from tests.test_mixed_evidence import setup


@pytest.fixture
def scene(client):
    release, entity, observation, body = setup(client)
    with Session(engine()) as db:
        original = db.get(Observation, observation['id'])
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + original.id,
            Event.workspace_id == original.workspace_id))
        assert original.status == 'processed' and 'data_reception' not in original.data
        assert event.status == 'pending_delivery' and event.received_at is None
        assert 'data_requirements' in db.get(InstanceRelease, release['id']).data['snapshot']
        return SimpleNamespace(release=release, entity=entity, observation=observation,
            body=body, event_id=event.id, original_data=copy.deepcopy(original.data),
            original_version=original.version, invocation_id=original.data['invocation_id'],
            workspace_id=original.workspace_id)


def state(client, scene):
    response = client.get('/api/workspace/instances/' + scene.release['instance_id']
        + '/objects/' + scene.entity['id'] + '/state')
    assert response.status_code == 200, response.text
    return response.json()['state']


def receive(scene):
    deliver_events(workspace_id=scene.workspace_id,
        observation_id=scene.observation['id'], event_kind='observation.data')
    with Session(engine()) as db:
        event = db.get(Event, scene.event_id)
        observation = db.get(Observation, scene.observation['id'])
        assert event.status == 'received' and event.received_at is not None
        assert observation.data['data_reception'] == event.data['data_reception']
        memories = list(db.scalars(select(Memory).where(Memory.workspace_id == event.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1 and memories[0].data['data_reception'] == event.data['data_reception']
        return memories[0].id


def assert_excluded(value, *, integrity=False):
    assert value['status'] == 'insufficient' and value['fields'] == {} and value['evidence_ids'] == []
    assert value['physical_state'] == 'unknown' and value['current_state_verified'] is False
    assert value['excluded_records'][0]['reason'] == (
        'data_reception_integrity_failed' if integrity else 'awaiting_data_reception')
    if integrity:
        assert value['excluded_records'][0]['status'] == 'blocked_integrity'


def test_processed_result_waits_then_real_independent_reception_admits_value_and_survives_reload(client, scene):
    before = state(client, scene)
    assert_excluded(before)
    again = state(client, scene)
    assert again['state_hash'] == before['state_hash'] and again['state_version'] == before['state_version']
    with Session(engine()) as db:
        original = db.get(Observation, scene.observation['id'])
        assert original.data == scene.original_data and original.version == scene.original_version
        assert db.get(Invocation, scene.invocation_id).status == 'succeeded'
        assert db.get(Event, scene.event_id).status == 'pending_delivery'
        assert db.scalar(select(Memory.id).where(Memory.data['event_id'].as_string() == scene.event_id)) is None
    memory_id = receive(scene)
    after = state(client, scene)
    assert after['status'] == 'observed' and after['fields']['公开登记值']['value'] == 27.5
    candidate = after['fields']['公开登记值']['candidates'][0]
    assert candidate['data_reception_verified'] is True and len(candidate['data_reception_receipt_hash']) == 64
    assert after['state_version'] == before['state_version'] + 1
    assert after['history'][-1]['state_hash'] == before['state_hash']
    deliver_events(workspace_id=scene.workspace_id, observation_id=scene.observation['id'],
        event_kind='observation.data')
    assert state(client, scene)['state_hash'] == after['state_hash']
    with Session(engine()) as db:
        assert db.get(Memory, memory_id) is not None
        saved = db.scalar(select(ObjectState).where(ObjectState.entity_id == scene.entity['id']))
        assert saved.data['state_hash'] == after['state_hash'] and saved.version == after['state_version']
        assert len(list(db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == scene.event_id)))) == 1
        assert db.get(Entity, scene.entity['id']).version == scene.entity['version']
        assert db.scalar(select(CloudCall.id)) is None


def test_receiver_refusal_cannot_adopt_processed_values_or_retry_during_state_read(client, scene):
    with Session(engine()) as db:
        event = db.get(Event, scene.event_id)
        event.status = 'blocked_delivery'
        event.data = {**event.data, 'delivery_error': 'isolated receiver refusal'}
        db.commit()
    assert_excluded(state(client, scene))
    with Session(engine()) as db:
        event = db.get(Event, scene.event_id)
        assert event.status == 'blocked_delivery' and event.attempts == 0
        assert db.get(Observation, scene.observation['id']).data == scene.original_data


@pytest.mark.parametrize('damage', ['received_flag_only', 'missing_memory', 'bad_memory_receipt',
    'bad_memory_payload', 'duplicate_memory', 'withdrawn_event', 'missing_observation_receipt', 'missing_event'])
def test_received_claim_with_missing_or_damaged_independent_link_is_explicitly_blocked(client, scene, damage):
    memory_id = None
    if damage != 'received_flag_only':
        memory_id = receive(scene)
        assert state(client, scene)['fields']['公开登记值']['value'] == 27.5
    with Session(engine()) as db:
        event = db.get(Event, scene.event_id)
        if damage == 'received_flag_only':
            event.status = 'received'
        elif damage == 'missing_memory': db.delete(db.get(Memory, memory_id))
        elif damage in {'bad_memory_receipt', 'bad_memory_payload'}:
            memory = db.get(Memory, memory_id)
            value = copy.deepcopy(memory.data)
            if damage == 'bad_memory_receipt': value['data_reception']['receipt_hash'] = 'f' * 64
            else: value['extracted']['attributes']['公开登记值'] = 999
            memory.data = value
        elif damage == 'duplicate_memory':
            original = db.get(Memory, memory_id)
            db.add(Memory(workspace_id=original.workspace_id, status=original.status,
                text='isolated duplicate recipient', data=copy.deepcopy(original.data)))
        elif damage == 'withdrawn_event': event.status = 'pending_delivery'
        elif damage == 'missing_observation_receipt':
            observation = db.get(Observation, scene.observation['id'])
            value = copy.deepcopy(observation.data)
            del value['data_reception']
            observation.data = value
        elif damage == 'missing_event': db.delete(event)
        db.commit()
    broken = state(client, scene)
    assert_excluded(broken, integrity=True)
    with Session(engine()) as db:
        assert db.get(Observation, scene.observation['id']).status == 'processed'
        assert db.get(Invocation, scene.invocation_id).status == 'succeeded'
        if memory_id is not None and damage == 'missing_memory':
            assert db.get(Memory, memory_id) is None


@pytest.mark.parametrize('damage', ['missing_plan', 'missing_package_marker', 'destination_context',
                                  'all_markers_deleted_and_rehashed', 'missing_release_association'])
def test_managed_contract_damage_cannot_downgrade_to_legacy_state_admission(client, scene, damage):
    with Session(engine()) as db:
        release = db.get(InstanceRelease, scene.release['id'])
        package = db.get(Capability, release.package_id)
        if damage == 'destination_context':
            observation = db.get(Observation, scene.observation['id'])
            observation.data = {**observation.data, 'local_instance': {
                'id': scene.release['instance_id'], 'release_id': str(uuid4())}}
        elif damage == 'missing_release_association':
            # Keep the generated package/namespace, remove only the reverse
            # association; it must not turn into a global unversioned entity.
            release.package_id = None
        else:
            value = copy.deepcopy(release.data)
            manifest = copy.deepcopy(package.data)
            if damage == 'missing_plan': del value['snapshot']['data_requirements']
            elif damage == 'missing_package_marker': del manifest['specification']['data_requirements_hash']
            else:
                for container in (value, value['snapshot'], manifest['specification']):
                    for key in ('data_requirements', 'data_requirements_schema', 'data_requirements_hash'):
                        container.pop(key, None)
                release.configuration_hash = digest(value['snapshot'])
                manifest['specification']['configuration_hash'] = release.configuration_hash
            release.data = value
            package.data = manifest
            package.content_hash = package.validated_hash = digest(manifest)
        db.commit()
    # The standalone state read must enforce the same custody gate even when
    # the managed route independently refuses the damaged release itself.
    response = client.get('/api/entities/' + scene.entity['id'] + '/perception-state')
    assert response.status_code == 200, response.text
    assert_excluded(response.json(), integrity=True)


def test_human_withdrawal_after_receipt_keeps_independent_ack_and_original_values(client, scene):
    memory_id = receive(scene)
    accepted = state(client, scene)
    with Session(engine()) as db:
        observation = db.get(Observation, scene.observation['id'])
        receipt = copy.deepcopy(observation.data['data_reception'])
        version = observation.version
    response = client.post('/api/observations/' + scene.observation['id'] + '/verify', json={
        'expected_version': version, 'decision': 'rejected', 'reason': '孤立测试撤回登记，不判断现场状态'})
    assert response.status_code == 200, response.text
    withdrawn = state(client, scene)
    assert withdrawn['fields'] == {} and withdrawn['excluded_records'][0]['reason'] == 'human_rejected'
    assert withdrawn['state_version'] == accepted['state_version'] + 1
    with Session(engine()) as db:
        observation = db.get(Observation, scene.observation['id'])
        assert observation.data['attributes'] == scene.original_data['attributes']
        assert observation.data['data_reception'] == receipt == db.get(Memory, memory_id).data['data_reception']


def test_candidate_and_unknown_clock_stay_excluded_even_if_data_was_received(client, scene):
    receive(scene)
    with Session(engine()) as db:
        observation = db.get(Observation, scene.observation['id'])
        observation.status = 'extracted_unclocked'
        observation.observed_at = None
        observation.data = {**observation.data, 'candidate_only': True}
        db.commit()
    value = state(client, scene)
    assert value['fields'] == {} and value['evidence_ids'] == []
    assert value['unclocked_candidates'][0]['observation_id'] == scene.observation['id']
