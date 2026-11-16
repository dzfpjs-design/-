"""Saved association versions, not traffic, participate in confirmed instance inventory."""
import copy
from uuid import uuid4

from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import InstanceRelease, CloudCall
from generic_agents.mqtt_inbox import capture
from tests.test_mqtt_observations import prepared_mqtt, bind, save as packet_save
from tests.test_instance_sensor_sources import checked, generate
from tests.test_vertical_slice import manifest, publish


def test_association_revision_invalidates_check_and_keeps_original_release(client, prepared_mqtt):
    gateway, obj, rule, principal = prepared_mqtt
    binding = bind(client, prepared_mqtt)
    body, before = checked(client)
    assert before['sensor_sources'][0]['associations'][0]['binding_id'] == binding['id']
    result = generate(client, body, before)
    assert result.status_code == 201, result.text
    original = result.json(); frozen = copy.deepcopy(original['snapshot']['sensor_sources'])
    next_rule = publish(client, manifest('tool', name='replacement_local_threshold', allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, data_policy={'cloud_allowed': False},
        specification={'rules': [{'id': 'hot', 'field': 'temperature', 'operator': 'gt', 'threshold': 45}]}))
    response = client.put('/api/mqtt/routes/' + binding['id'], json={'request_id': str(uuid4()),
        'expected_version': 1, 'topic': 'sensor/one', 'entity_id': obj['id'],
        'extractor_id': next_rule['id'], 'field_links': {'temperature': 'temperature'}, 'include_existing': False})
    assert response.status_code == 200, response.text
    assert generate(client, body, before).status_code == 409
    after = client.post('/api/instances/check', json=body)
    assert after.status_code == 200, after.text
    after = after.json()
    assert after['configuration_hash'] != before['configuration_hash']
    summary = after['sensor_sources'][0]
    assert len(summary['associations']) == 1 and summary['associations'][0]['revision'] == 2
    assert summary['association_scope'] == 'saved_workspace_inventory_not_execution_authority'
    with Session(engine()) as db:
        assert db.get(InstanceRelease, original['id']).data['snapshot']['sensor_sources'] == frozen
    # Reception duplicates and last-message metadata do not change the saved config proof.
    packet, raw = packet_save(prepared_mqtt, 'signature-unchanged')
    with Session(engine()) as db:
        capture(db, principal, gateway['id'], gateway['version'], 'sensor/one', raw, 1, duplicate=True); db.commit()
    again = client.post('/api/instances/check', json=body)
    assert again.status_code == 200
    assert again.json()['configuration_hash'] == after['configuration_hash']
    assert again.json()['sensor_sources'] == after['sensor_sources']
    with Session(engine()) as db:
        assert not db.query(CloudCall).count()


def test_single_route_pause_invalidates_check_without_changing_gateway(client, prepared_mqtt):
    gateway, obj, rule, principal = prepared_mqtt
    binding = bind(client, prepared_mqtt)
    body, before = checked(client)
    result = client.post('/api/mqtt/routes/' + binding['id'] + '/status', json={
        'request_id': str(uuid4()), 'expected_version': 1, 'status': 'paused', 'include_existing': False})
    assert result.status_code == 200, result.text
    response = client.post('/api/instances/check', json=body)
    assert response.status_code == 200, response.text
    after = response.json(); source = after['sensor_sources'][0]
    assert source['version'] == gateway['version'] and source['status'] == 'active'
    assert source['binding_status'] == 'unbound' and source['associations'][0]['status'] == 'paused'
    assert after['configuration_hash'] != before['configuration_hash']
