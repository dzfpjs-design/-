"""Manual second evaluation with genuine isolated PG records and software data.

No real MQTT broker, PUBACK, cloud service or field device is used. The
closed automatic-evaluation switch remains closed; append uses the public
manual command with its normal authority, lease, receipt and lineage gates.
"""
import copy
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import goal_evaluation_append_store_v297 as append_store
from generic_agents import native_goal_scoped_worker_v355 as goal_worker
from generic_agents import workflow_goal_evaluation_contracts_v288 as evaluation_contracts
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY
from generic_agents.mqtt_inbox import capture
from generic_agents.security import Principal
from generic_agents.worker import deliver_events, process_once
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from tests.test_mqtt_binding_routes import process_all
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network, managed
from tests.test_device_gateway_v268 import scene
from tests.test_authored_service_workflow_pg_v270 import good
from tests.test_vertical_slice import manifest, publish
import tests.auto_evaluation_journey_fixture_helpers_v357 as helpers
import tests.test_authored_service_workflow_pg_v270 as authored_fixture
import tests.test_auto_evaluation_real_journey_fixture_v356 as journey


@pytest.fixture
def deepseek_3600_publication(managed, monkeypatch):
    model = copy.deepcopy(authored_fixture.model_manifest())
    model['name'] = 'synthetic_deepseek_manual_append'
    model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    monkeypatch.setattr(authored_fixture, 'model_manifest', lambda: copy.deepcopy(model))
    original_policy = journey.policy
    def policy():
        value = original_policy()
        value['max_age_seconds'] = 3600
        value['policy_hash'] = evaluation_contracts.policy_hash(value)
        return value
    monkeypatch.setattr(journey, 'policy', policy)
    publication = journey.published_goal_data.__wrapped__(managed)
    assert publication.model['data']['execution']['provider'] == 'deepseek'
    assert publication.preview['binding']['preparation_policy']['max_age_seconds'] == 3600
    return publication


def test_manual_append_second_generation_with_real_receipt_and_no_cloud(deepseek_3600_publication, monkeypatch):
    publication = deepseek_3600_publication
    seen = []
    setup = helpers.setup_simulated_scope
    def tracked_setup(value):
        result = setup(value); seen.append(result); return result
    monkeypatch.setattr(helpers, 'setup_simulated_scope', tracked_setup)
    journey.test_real_published_six_port_task_with_simulated_scope_and_two_independent_approvals(publication, monkeypatch)
    assert len(seen) == 1
    scoped = seen[0]; s = scoped.scene; client = s.client; workspace = s.workspace_id
    release = scoped.release
    with Session(engine()) as db:
        tasks = list(db.scalars(select(m.Task).where(m.Task.workspace_id == workspace,
            m.Task.data['instance_release_id'].as_string() == release['id'])))
        assert len(tasks) == 1
        task = tasks[0]; task_id = task.id; first_head = copy.deepcopy(task.data[KEY])
        goal_hash = task.data['goal_hash']; q0 = task.data['native_goal_query_id']
        assert first_head['generation'] == 1
    print('INITIAL_EVALUATION_SAVED', flush=True)

    # Explicit software packet input; no TCP connection to any MQTT broker.
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    rule = publish(client, manifest('tool', name='mqtt_manual_' + uuid4().hex[:12], allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False}, specification={'rules': []}))
    config = {'broker': 'mqtt://127.0.0.1:1883', 'topics': {'sensor/one': 'device-one'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': '°C'}, 'h': {'attribute': 'humidity', 'unit': '%'}},
        'fields': {}, 'username_ref': '', 'password_ref': ''}
    gateway = good(client.post('/api/mqtt/gateways', json={'name': '合成软件观测源', 'configuration': config}), 201)
    gateway = good(client.post('/api/mqtt/gateways/' + gateway['id'] + '/status', json={
        'expected_version': gateway['version'], 'status': 'active'}))
    good(client.post('/api/mqtt/gateways/' + gateway['id'] + '/test-origin', json={
        'request_id': str(uuid4()), 'expected_gateway_version': gateway['version'],
        'expected_config_hash': gateway['config_hash'], 'source_label': '软件模拟非现场数据',
        'basis_type': 'manufacturer_parameters', 'basis_notes': '只生成软件数据，不连接物理设备或真实 broker。'}), 201)
    good(client.post('/api/mqtt/gateways/' + gateway['id'] + '/routes', json={
        'expected_version': gateway['version'], 'request_id': str(uuid4()), 'topic': 'sensor/one',
        'entity_id': scoped.entity['id'], 'extractor_id': rule['id'],
        'field_links': {'temperature': scoped.fields['气温'], 'humidity': scoped.fields['相对湿度']},
        'include_existing': False}), 201)
    packet = {'record_id': 'manual-goal-' + uuid4().hex, 'device_id': 'device-one',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'values': {'t': 26.5, 'h': 65.0},
        'units': {'t': '°C', 'h': '%'}}
    principal = Principal(s.actor_id, workspace, 'admin', 'isolated-manual-append')
    with Session(engine()) as db:
        item = capture(db, principal, gateway['id'], gateway['version'], 'sensor/one',
            json.dumps(packet, ensure_ascii=False).encode(), 1)
        db.flush(); receipt_id = item.id; db.commit()
    observation_ids = process_all(receipt_id)
    assert len(observation_ids) == 1
    observation_id = observation_ids[0]
    deliver_events(workspace_id=workspace, observation_id=observation_id, event_kind='observation.data')
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation_id))
        assert observation.entity_id == scoped.entity['id'] and event and event.status == 'received'
        from generic_agents.instance_data_receipts import observation_receipt
        actual_receipt = observation_receipt(db, principal, observation)['data_reception']
        assert actual_receipt
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id == workspace)) == 0
    print('INDEPENDENT_DATA_RECEIPT_VERIFIED', flush=True)

    # The normal API requires an authentic mature head; process only the task's
    # own issued goal-check/candidate/promotion jobs while waiting for it.
    for _ in range(40):
        try:
            with Session(engine()) as db:
                task = db.get(m.Task, task_id)
                selected = append_store.provider.binding(db, principal, task)
                mature = append_store._mature_head(db, principal, task, selected)
                assert mature['head']['head_hash'] == first_head['head_hash']
                break
        except (HTTPException, ValueError):
            goal_worker.tick(workspace, release['instance_id'], task_id)
    else:
        pytest.fail('首代评价尚未通过真实成熟核对；未提交追加')
    request = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
        'expected_mature_head_hash': first_head['head_hash'], 'reason': '人工核对已独立接收的新合成观测'}
    response = client.post('/api/workspace/tasks/' + task_id + '/goal-evaluations/append', json=request)
    assert response.status_code == 201, response.text
    original = response.json()
    assert client.post('/api/workspace/tasks/' + task_id + '/goal-evaluations/append', json=request).json() == original
    print('MANUAL_APPEND_ACCEPTED', flush=True)
    assert process_once(original['job_id'], kinds=['append_goal_evaluation_v297'],
                        deliver=False, workspace_id=workspace)
    print('APPEND_PRODUCER_FINISHED', flush=True)
    for _ in range(40):
        goal_worker.tick(workspace, release['instance_id'], task_id)
        with Session(engine()) as db:
            task = db.get(m.Task, task_id); head = task.data.get(KEY)
            if head and head['generation'] > first_head['generation']:
                break
        time.sleep(.25)
    else:
        pytest.fail('追加仍未自然形成下一代成熟评价')
    with Session(engine()) as db:
        task = db.get(m.Task, task_id); head = task.data[KEY]
        assert head['generation'] == first_head['generation'] + 1 and head['previous_hash'] == first_head['head_hash']
        assert task.data['native_goal_query_id'] == q0
        record = db.get(GoalEvaluationV288, head['evaluation_ref']['id'])
        assert record and record.record_hash == head['evaluation_ref']['record_hash']
        assert any(x.get('observation_id') == observation_id for x in record.data['evaluation_result']['result']['observation_evidence'])
        selected = append_store.provider.binding(db, principal, task)
        assert append_store._mature_head(db, principal, task, selected)['head']['head_hash'] == head['head_hash']
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id == workspace)) == 0
    print('SECOND_EVALUATION_MATURED', flush=True)
