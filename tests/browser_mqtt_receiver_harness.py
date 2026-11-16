"""Opt-in disposable browser + actual local broker + persistent intake workers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from hashlib import sha256

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Base, CloudCall, Event, MqttGateway, MqttReceipt, Observation, Membership, User
from generic_agents.security import password_hash
from tests.test_instance_workspace import configuration, save
from tests.test_mqtt_observations import prepared_mqtt, bind
from tests.test_mqtt_supervisor_protocol import LoopbackBroker, packet, publish, receiver_state as receiver, wait_for, stop_child

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/evidence/mqtt-receiver-v07'
ACCESS = ROOT/'.runtime/mqtt-receiver-browser-access.json'
CONTROL = ROOT/'.runtime/mqtt-receiver-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/(name+'.json')).write_text(json.dumps(value, indent=2)+'\n')


def durable():
    with engine().connect() as connection:
        return {table.name: {'count': len(rows), 'digest': digest(sorted(rows))}
            for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name)
            for rows in [[digest(json.loads(json.dumps(dict(row._mapping), default=str)))
                          for row in connection.execute(select(table))]]}


def test_browser_receiver(client, account, monkeypatch, prepared_mqtt, tmp_path):
    if os.getenv('GA_MQTT_RECEIVER_BROWSER') != '1':
        pytest.skip('Explicit disposable browser verification only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); broker_port = bound.getsockname()[1]
    broker = LoopbackBroker(broker_port, tmp_path/'browser-protocol.jsonl')
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', broker.origin)
    gateway, obj, rule, principal = prepared_mqtt
    source = client.put('/api/mqtt/gateways/'+gateway['id'], json={'name': '隔离核对来源',
        'expected_version': gateway['version'], 'configuration': {**gateway['configuration'], 'broker': broker.origin}})
    assert source.status_code == 200
    gateway = source.json()
    bind(client, (gateway, obj, rule, principal))
    config = configuration(); config['mqttSelection'] = gateway['id']
    save(client, config)
    with Session(engine()) as db:
        viewer_name, viewer_password = 'viewer_'+os.urandom(5).hex(), os.urandom(16).hex()
        viewer = User(username=viewer_name, password_hash=password_hash(viewer_password))
        db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id, workspace_id=account[2], role='viewer')); db.commit()
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); api_port = bound.getsockname()[1]
    url = 'http://localhost:'+str(api_port)
    env = {**os.environ, 'GA_DATABASE_URL': settings().database_url, 'GA_STORAGE_DIR': str(settings().storage_dir),
        'GA_MQTT_ALLOWED_BROKERS': broker.origin, 'GA_ALLOWED_ORIGINS': url,
        'GA_MQTT_RECEIVER_LEASE_SECONDS': '5', 'GA_MQTT_RECEIVER_POLL_SECONDS': '.2',
        'GA_MQTT_RECEIVER_CONNECT_TIMEOUT_SECONDS': '1', 'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}
    children, logs = [], []
    def start(command):
        log = open(ROOT/('.runtime/mqtt-receiver-browser-'+command+'.log'), 'a'); logs.append(log)
        args = [sys.executable, '-m', 'generic_agents.cli', command]
        if command == 'serve': args += ['--host', '127.0.0.1', '--port', str(api_port)]
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log)
        child.protocol_log = Path(log.name)
        children.append(child)
        if command == 'serve':
            def ready():
                try: return httpx.get(url+'/health', trust_env=False, timeout=1).is_success
                except httpx.HTTPError: return False
            wait_for(ready, [child], timeout=15, description='disposable browser API readiness')
        return child
    originals = {}
    def send(record):
        raw = packet(record, device='device-one')
        publish(broker, [('sensor/one', raw)])
        def done():
            with Session(engine()) as db:
                receipt = db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway['id'],
                    MqttReceipt.input_sha256 == sha256(raw).hexdigest()))
                if not receipt or not receipt.data.get('observation_id'): return False
                obs = db.get(Observation, receipt.data['observation_id'])
                delivered = db.scalar(select(Event).where(Event.kind == 'perception.sensor_packet',
                    Event.data['receipt_id'].as_string() == receipt.id))
                return (receipt.id, receipt.input_sha256) if (obs.status == 'processed' and delivered.status == 'received') else False
        identifier, checksum = wait_for(done, [mqtt, setup], timeout=25, description='automatic local extraction and data delivery')
        originals[record] = {'id': identifier, 'sha256': checksum}
        write(record, {'real_broker': True, 'local_numeric_extraction': True, 'data_delivery': True, **originals[record]})
    try:
        broker.start(); api = start('serve'); mqtt = start('mqtt-worker'); setup = start('setup-worker')
        with os.fdopen(os.open(ACCESS, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w') as file:
            json.dump({'url': url, 'username': account[0], 'password': account[1],
                'viewer_username': viewer_name, 'viewer_password': viewer_password, 'gateway_id': gateway['id']}, file)
        print('Disposable actual MQTT browser runtime ready', flush=True)
        sequence, before = 0, None
        deadline = time.monotonic()+1200
        while time.monotonic() < deadline:
            assert api.poll() is None
            try:
                command = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except json.JSONDecodeError:
                # A replaced controller file is retried; no command is assumed.
                time.sleep(.2); continue
            if command.get('sequence', 0) <= sequence: time.sleep(.2); continue
            sequence = command['sequence']; action = command['command']
            if action == 'first':
                wait_for(lambda: (state if (state := receiver(gateway['id'])) and state['status']=='receiving' else None),
                    [mqtt], description='browser-approved actual subscription')
                send('ui-first')
            elif action == 'disconnect':
                broker.stop()
                wait_for(lambda: (state if (state := receiver(gateway['id'])) and state['status']=='backoff' else None),
                    [mqtt], description='actual broker disconnect reported')
                write('disconnected', {'broker_exit': broker.process.returncode, 'state': 'backoff'})
            elif action == 'reconnect':
                broker.start()
                wait_for(lambda: (state if (state := receiver(gateway['id'])) and state['status']=='receiving' else None),
                    [mqtt], description='broker reconnect and new subscription')
                send('ui-second')
            elif action == 'pause':
                with Session(engine()) as db: assert db.get(MqttGateway, gateway['id']).status == 'paused'
                raw = packet('ui-paused', device='device-one'); publish(broker, [('sensor/one', raw)])
                time.sleep(1.5)
                with Session(engine()) as db:
                    assert not db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway['id'],
                        MqttReceipt.input_sha256 == sha256(raw).hexdigest()))
                write('paused', {'browser_paused': True, 'new_receipts': 0, 'historical_originals': len(originals)})
            elif action == 'restart-receiver':
                previous = receiver(gateway['id'])['lease_token']; stop_child(mqtt, crash=True); mqtt = start('mqtt-worker')
                wait_for(lambda: (state if (state := receiver(gateway['id'])) and state['status']=='receiving'
                    and state['lease_token'] != previous else None), [mqtt], description='fresh persistent worker takeover')
                send('ui-third')
            elif action == 'restart-api':
                with Session(engine()) as db: assert db.get(MqttGateway, gateway['id']).status == 'paused'
                stop_child(mqtt); stop_child(setup)
                before = durable(); write('before-restart', before)
                stop_child(api); api = start('serve'); write('restarted', {'real_api_restart': True})
            elif action == 'finish':
                assert before is not None and durable() == before
                with Session(engine()) as db: assert not db.scalars(select(CloudCall)).all()
                write('after-restart', durable())
                write('verification', {'classification': 'verified_progress', 'full_goal': 'active',
                    'browser_authorized_and_paused': True, 'actual_broker': True, 'actual_persistent_worker': True,
                    'broker_disconnect_and_resume': True, 'worker_crash_and_resume': True,
                    'automatic_extraction_and_data_delivery': True, 'all_table_digests_after_api_restart': True,
                    'cloud_calls': 0, 'real_field_device': False, 'production': False, 'originals': originals})
                print('Browser recovery and durable original pipeline verified', flush=True)
                return
        raise AssertionError('Browser verification timed out')
    finally:
        for child in children: stop_child(child)
        broker.stop(crash=False)
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
