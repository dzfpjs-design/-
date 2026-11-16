"""Opt-in actual broker/browser/OS workers with two generated business instances."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (Base, CloudCall, Event, MqttGateway, MqttReceipt,
    MqttBindingRoute, MqttBindingRevision, MqttReceiptDelivery, Observation, Membership, User,
    PerceptionSignal, ObjectState)
from generic_agents.security import password_hash
from tests.test_instance_workspace import save
from tests.mqtt_reuse_fixture import generated_sensor_instance, local_rule, OBJECT_TYPES
from tests.test_mqtt_supervisor_protocol import LoopbackBroker, packet, publish, receiver_state, wait_for, stop_child

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/mqtt-source-reuse-v08/browser'
ACCESS = ROOT / '.runtime/mqtt-reuse-browser-access.json'
CONTROL = ROOT / '.runtime/mqtt-reuse-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')


def durable():
    with engine().connect() as connection:
        return {table.name: {'count': len(rows), 'digest': digest(sorted(rows))}
            for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name)
            for rows in [[digest(json.loads(json.dumps(dict(row._mapping), default=str)))
                          for row in connection.execute(select(table))]]}


def test_browser_source_reuse(client, account, monkeypatch, tmp_path):
    if os.getenv('GA_MQTT_REUSE_BROWSER') != '1':
        pytest.skip('Explicit disposable multi-instance browser verification only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    instances = [generated_sensor_instance(client, '合成实例甲'), generated_sensor_instance(client, '合成实例乙')]
    rules = [local_rule(client, instances[0], '甲原规则：温度大于38', 38),
             local_rule(client, instances[1], '乙原规则：温度大于35', 35)]
    replacement = local_rule(client, instances[0], '甲新规则：温度大于45', 45)
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); broker_port = bound.getsockname()[1]
    broker = LoopbackBroker(broker_port, tmp_path / 'reuse-protocol.jsonl', lifetime=1800)
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', broker.origin)
    response = client.post('/api/mqtt/gateways', json={'name': '隔离多业务复用来源',
        'configuration': {'broker': broker.origin, 'topics': {'sensor/one': 'device-one'},
            'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}}, 'fields': {},
            'username_ref': '', 'password_ref': ''}})
    assert response.status_code == 201, response.text
    gateway = response.json()
    config = instances[1]['configuration']; config['mqttSelection'] = gateway['id']; config['instanceObjects'] = OBJECT_TYPES
    save(client, config)
    with Session(engine()) as db:
        viewer_name, viewer_password = 'viewer_' + os.urandom(5).hex(), os.urandom(16).hex()
        viewer = User(username=viewer_name, password_hash=password_hash(viewer_password)); db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id, workspace_id=account[2], role='viewer')); db.commit()
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); api_port = bound.getsockname()[1]
    url = 'http://localhost:' + str(api_port)
    env = {**os.environ, 'GA_DATABASE_URL': settings().database_url, 'GA_STORAGE_DIR': str(settings().storage_dir),
        'GA_MQTT_ALLOWED_BROKERS': broker.origin, 'GA_ALLOWED_ORIGINS': url,
        'GA_MQTT_RECEIVER_LEASE_SECONDS': '5', 'GA_MQTT_RECEIVER_POLL_SECONDS': '.2',
        'GA_MQTT_RECEIVER_CONNECT_TIMEOUT_SECONDS': '1', 'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}
    children, logs, originals, first_observations = [], [], {}, {}
    held_connection, held_transaction, a_route_id = None, None, None
    def start(command):
        log = open(ROOT / ('.runtime/mqtt-reuse-browser-' + command + '.log'), 'a'); logs.append(log)
        args = [sys.executable, '-m', 'generic_agents.cli', command]
        if command == 'serve': args += ['--host', '127.0.0.1', '--port', str(api_port)]
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log); child.protocol_log = Path(log.name)
        children.append(child)
        if command == 'serve':
            def ready():
                try: return httpx.get(url + '/health', trust_env=False, timeout=1).is_success
                except httpx.HTTPError: return False
            wait_for(ready, [child], timeout=15, description='disposable actual multi-instance API')
        return child
    def observation_fingerprints(db, identities):
        return {identity: digest(json.loads(json.dumps(dict(db.execute(select(Observation.__table__).where(
            Observation.id == identity)).one()._mapping), default=str))) for identity in identities}
    def send(record, expected_objects):
        raw = packet(record, device='device-one', value=39.5); publish(broker, [('sensor/one', raw)])
        def done():
            with Session(engine()) as db:
                receipt = db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway['id'],
                    MqttReceipt.input_sha256 == sha256(raw).hexdigest()))
                if not receipt: return False
                deliveries = db.scalars(select(MqttReceiptDelivery).where(MqttReceiptDelivery.receipt_id == receipt.id)).all()
                if len(deliveries) != len(expected_objects) or any(not row.observation_id for row in deliveries): return False
                observations = [db.get(Observation, row.observation_id) for row in deliveries]
                if {row.entity_id for row in observations} != expected_objects or any(row.status != 'processed' for row in observations): return False
                events = db.scalars(select(Event).where(Event.kind == 'observation.data',
                    Event.data['observation_id'].as_string().in_([row.id for row in observations]))).all()
                if len(events) != len(expected_objects) or any(row.status != 'received' for row in events): return False
                for instance in instances:
                    if instance['object']['id'] not in expected_objects: continue
                    state = db.scalar(select(ObjectState).where(ObjectState.entity_id == instance['object']['id']))
                    if not state or state.data.get('fields', {}).get(instance['field'], {}).get('value') != 39.5: return False
                report = {'receipt_id': receipt.id, 'sha256': receipt.input_sha256, 'evidence_id': receipt.evidence_id,
                    'observations': [{'id': row.id, 'entity_id': row.entity_id, 'source_id': row.source_id,
                        'extractor_id': row.data['extractor_id'], 'anomalies': row.data['extracted'].get('anomalies', [])}
                        for row in observations]}
                assert all(row.data['evidence_ids'] == [receipt.evidence_id] for row in observations)
                signals = db.scalars(select(PerceptionSignal).where(PerceptionSignal.entity_id.in_(expected_objects))).all()
                report['signal_count_by_object'] = {identity: sum(row.entity_id == identity for row in signals)
                    for identity in expected_objects}
                return report
        result = wait_for(done, [mqtt, setup], timeout=35, description='one original -> independent instance extraction and data')
        originals[record] = result; write(record, result)
        return result
    try:
        broker.start(); api = start('serve'); mqtt = start('mqtt-worker'); setup = start('setup-worker')
        with os.fdopen(os.open(ACCESS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
            json.dump({'url': url, 'username': account[0], 'password': account[1],
                'viewer_username': viewer_name, 'viewer_password': viewer_password, 'gateway_id': gateway['id'],
                'instances': [{'id': x['release']['instance_id'], 'object_id': x['object']['id'], 'field': x['field']}
                    for x in instances], 'rules': [x['id'] for x in rules], 'replacement_rule': replacement['id']}, file)
        print('Disposable multi-instance browser runtime ready', flush=True)
        sequence, before = 0, None
        deadline = time.monotonic() + 1500
        while time.monotonic() < deadline:
            assert api.poll() is None
            try: command = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except json.JSONDecodeError: time.sleep(.2); continue
            if command.get('sequence', 0) <= sequence: time.sleep(.2); continue
            sequence = command['sequence']; action = command['command']
            if action == 'first':
                wait_for(lambda: (state if (state := receiver_state(gateway['id'])) and state['status'] == 'receiving' else None),
                    [mqtt], description='browser-approved continuous source')
                with Session(engine()) as db: assert db.query(MqttBindingRoute).filter_by(gateway_id=gateway['id']).count() == 2
                result = send('reuse-first', {x['object']['id'] for x in instances})
                assert all(x['anomalies'] for x in result['observations'])
                with Session(engine()) as db: first_observations = observation_fingerprints(db, [x['id'] for x in result['observations']])
                write('first-ready', {'received': True})
            elif action == 'concurrent-change':
                rows = client.get('/api/mqtt/gateways/' + gateway['id'] + '/routes').json()
                row = next(x for x in rows if x['entity_id'] == instances[0]['object']['id'])
                a_route_id = row['id']
                body = {key: row[key] for key in ('topic', 'entity_id', 'extractor_id', 'field_links')}
                body.update(expected_version=row['version'], request_id=str(uuid4()), include_existing=False)
                response = client.put('/api/mqtt/routes/' + row['id'], json=body)
                assert response.status_code == 200, response.text
                assert response.json()['version'] == 2
                write('concurrent-change', {'actual_concurrent_admin_save': True, 'version': 2})
            elif action == 'hold-route':
                assert held_connection is None
                held_connection = engine().connect(); held_transaction = held_connection.begin()
                held_connection.execute(select(MqttGateway).where(MqttGateway.id == gateway['id']).with_for_update()).one()
                write('held-route', {'actual_database_lock': True})
            elif action == 'release-route':
                assert held_transaction is not None
                held_transaction.rollback(); held_connection.close()
                held_connection, held_transaction = None, None
                def submitted():
                    with Session(engine()) as db:
                        route = db.get(MqttBindingRoute, a_route_id)
                        return route if route and route.version == 3 else None
                # Stopping browser wait must neither cancel nor duplicate a committed request.
                wait_for(submitted, [api], timeout=20, description='original request commits after browser stops waiting')
                write('released-route', {'original_request_committed': True, 'version': 3})
            elif action == 'verify-retry':
                with Session(engine()) as db:
                    route = db.get(MqttBindingRoute, a_route_id)
                    assert route.version == 3
                    assert db.query(MqttBindingRevision).filter_by(route_id=route.id).count() == 3
                write('retry-verified', {'exact_uuid_retry_no_duplicate_revision': True, 'version': 3})
            elif action == 'second':
                result = send('reuse-second', {x['object']['id'] for x in instances})
                by_object = {x['entity_id']: x for x in result['observations']}
                assert by_object[instances[0]['object']['id']]['extractor_id'] == replacement['id']
                assert not by_object[instances[0]['object']['id']]['anomalies']
                assert by_object[instances[1]['object']['id']]['extractor_id'] == rules[1]['id']
                assert by_object[instances[1]['object']['id']]['anomalies']
                with Session(engine()) as db: assert observation_fingerprints(db, first_observations) == first_observations
                write('history-preserved', {'first_observations_unchanged': True, 'only_one_rule_changed': True})
            elif action == 'third':
                with Session(engine()) as db:
                    assert db.query(MqttBindingRoute).filter_by(gateway_id=gateway['id'], status='paused').count() == 1
                send('reuse-third', {instances[1]['object']['id']})
                with Session(engine()) as db:
                    assert db.query(MqttReceipt).filter_by(gateway_id=gateway['id']).count() == 3
                    assert db.query(MqttReceiptDelivery).count() == 5
                write('paused-route', {'one_paused_route': True, 'originals': 3, 'independent_processing_records': 5})
            elif action == 'restart':
                with Session(engine()) as db: assert db.get(MqttGateway, gateway['id']).status == 'paused'
                stop_child(mqtt); stop_child(setup); before = durable(); write('before-restart', before)
                stop_child(api); api = start('serve'); write('restarted', {'actual_api_restart': True})
            elif action == 'finish':
                assert before is not None and durable() == before
                with Session(engine()) as db: assert not db.query(CloudCall).count()
                write('after-restart', durable())
                write('verification', {'classification': 'verified_progress', 'full_goal': 'active', 'cloud_calls': 0,
                    'actual_broker': True, 'actual_os_workers': True, 'two_generated_instances': True,
                    'one_original_shared_by_independent_processing': True, 'only_one_rule_changed': True,
                    'historical_observations_unchanged': True, 'single_route_pause_other_continues': True,
                    'all_table_digests_after_api_restart': True, 'real_field_device': False,
                    'model_quality': 'not_evaluated_synthetic_numeric_rules', 'production': False, 'originals': originals})
                print('Multi-instance source reuse and durable recovery verified', flush=True); return
        raise AssertionError('Multi-instance browser verification timed out')
    finally:
        if held_transaction is not None: held_transaction.rollback()
        if held_connection is not None: held_connection.close()
        for child in children: stop_child(child)
        broker.stop(crash=False)
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
