"""Opt-in actual browser/loopback MQTT/OS worker daily-list verification.

Provisioning uses real APIs. Browser actions are driven separately through CUA.
Every account, original, job and service belongs to the disposable ga_test DB.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import (Base, CloudCall, Evidence, Membership, MqttGateway,
    MqttReceipt, MqttReceiptDelivery, Observation, User)
from generic_agents.security import password_hash
from tests.mqtt_reuse_fixture import generated_sensor_instance, local_rule, route_body, OBJECT_TYPES
from tests.test_instance_workspace import save
from tests.test_mqtt_supervisor_protocol import LoopbackBroker, publish, subscribed, wait_for, stop_child, receiver_state

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/mqtt-daily-full-v09/browser'
ACCESS = ROOT / '.runtime/mqtt-daily-browser-access.json'
CONTROL = ROOT / '.runtime/mqtt-daily-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')


def durable():
    with engine().connect() as connection:
        result = {}
        for table in sorted(Base.metadata.tables.values(), key=lambda value: value.name):
            rows = [digest(json.loads(json.dumps(dict(row._mapping), default=str)))
                for row in connection.execute(select(table))]
            result[table.name] = {'count': len(rows), 'digest': digest(sorted(rows))}
        return result


def test_browser_daily_full(client, account, monkeypatch, tmp_path):
    if os.getenv('GA_MQTT_DAILY_BROWSER') != '1':
        pytest.skip('Explicit disposable daily-list browser verification only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    instances = [generated_sensor_instance(client, '每日清单合成业务甲'),
        generated_sensor_instance(client, '每日清单合成业务乙')]
    rules = [local_rule(client, item, '合成本地温度规则' + str(index), 38)
        for index, item in enumerate(instances)]
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); broker_port = bound.getsockname()[1]
    broker = LoopbackBroker(broker_port, tmp_path / 'daily-protocol.jsonl', lifetime=1800,
        audit_limit=10000, commit_audit_limit=16)
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', broker.origin)
    response = client.post('/api/mqtt/gateways', json={'name': '完整每日传感清单验收来源',
        'configuration': {'broker': broker.origin,
            'topics': {'sensor/one': 'device-one', 'sensor/raw': 'device-raw'},
            'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}}, 'fields': {},
            'username_ref': '', 'password_ref': ''}})
    assert response.status_code == 201, response.text
    gateway = response.json()
    for item, rule in zip(instances, rules):
        response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/routes',
            json=route_body(gateway, item, rule))
        assert response.status_code == 201, response.text
    config = instances[-1]['configuration']; config['mqttSelection'] = gateway['id']
    config['instanceObjects'] = OBJECT_TYPES; save(client, config)
    response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/status',
        json={'expected_version': gateway['version'], 'status': 'active'})
    assert response.status_code == 200, response.text
    gateway = response.json()
    captured = datetime.now(timezone.utc) - timedelta(days=1)
    business_day = captured.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    def packet(identity, device='device-raw', observed=captured.isoformat()):
        return json.dumps({'record_id': identity, 'device_id': device, 'observed_at': observed,
            'values': {'t': 39.5}, 'units': {'t': 'C'}}, separators=(',', ':')).encode()
    with Session(engine()) as db:
        viewer_name, viewer_password = 'viewer_' + os.urandom(5).hex(), os.urandom(16).hex()
        viewer = User(username=viewer_name, password_hash=password_hash(viewer_password))
        db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id, workspace_id=account[2], role='viewer')); db.commit()
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); api_port = bound.getsockname()[1]
    # Cookies are scoped to hosts, not ports: isolate disposable browser login
    # from the normal application's 127.0.0.1 session. Binding stays loopback.
    url = 'http://localhost:' + str(api_port)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
        'GA_SETUP_BUILD_DIR': str(ROOT / '.runtime/builds/mqtt-daily-full'),
        'GA_ALLOWED_ORIGINS': url, 'GA_COOKIE_SECURE': 'false',
        'GA_DATABASE_URL': settings().database_url, 'GA_STORAGE_DIR': str(settings().storage_dir),
        'GA_MQTT_ALLOWED_BROKERS': broker.origin, 'GA_MQTT_RECEIVER_LEASE_SECONDS': '5',
        'GA_MQTT_RECEIVER_POLL_SECONDS': '.2', 'GA_MQTT_RECEIVER_CONNECT_TIMEOUT_SECONDS': '1',
        'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}
    children, logs, first_files, before = [], [], {}, None
    def start(command):
        path = ROOT / ('.runtime/mqtt-daily-browser-' + command + '.log')
        log = path.open('ab'); logs.append(log)
        args = [sys.executable, '-m', 'generic_agents.cli', command]
        if command == 'serve': args += ['--host', '127.0.0.1', '--port', str(api_port)]
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log)
        child.protocol_log = path; children.append(child)
        if command == 'serve':
            def ready():
                try: return httpx.get(url + '/health', trust_env=False, timeout=1).is_success
                except httpx.HTTPError: return False
            wait_for(ready, [child], timeout=15, description='disposable daily API')
        return child
    def days():
        response = client.get('/api/mqtt/gateways/' + gateway['id'] + '/days')
        assert response.status_code == 200, response.text
        return response.json()['records']
    def summary():
        return next((row for row in days() if row['business_day'] == business_day), None)
    def full_records(ledger, revision=None, instance=None, kind='original'):
        params = {'revision': revision or ledger['revision'], 'kind': kind, 'status': 'all', 'limit': 100}
        if instance: params['instance_id'] = instance
        rows, after, fixed = [], '', None
        while True:
            response = client.get('/api/mqtt/daily/' + ledger['id'] + '/records',
                params={**params, **({'after': after} if after else {})})
            assert response.status_code == 200, response.text
            value = response.json(); fixed = fixed or value['revision_hash']
            assert value['revision_hash'] == fixed
            rows.extend(value['records']); after = value['next_cursor']
            if not after: break
        assert len(rows) == value['total']
        assert len({(row.get('id'), row.get('receipt_id')) for row in rows}) == len(rows)
        return rows
    try:
        broker.start(); api = start('serve'); mqtt = start('mqtt-worker'); setup = start('setup-worker')
        subscribed(gateway['id'], mqtt)
        messages = [('sensor/raw', packet('full-' + str(index))) for index in range(1005)]
        messages.extend([('sensor/one', packet('shared-first', device='device-one')),
            ('sensor/raw', packet('unknown-clock', observed=None)), ('sensor/raw', b'\xff')])
        began = time.monotonic(); publish(broker, messages)
        # Duplicate delivery retains a single original. Broker QoS ACK is not yet a data-agent receipt.
        publish(broker, [messages[0]])
        def collected():
            row = summary()
            if not row or row['counts']['raw_total'] != 1006 or row['counts']['data_received'] != 1006:
                return False
            return row if row['counts']['processing_succeeded'] == 2 else False
        first = wait_for(collected, [mqtt, setup], timeout=180, description='1006 dated originals and two independent local results')
        unknown = next(row for row in days() if row['business_day'] is None)
        assert unknown['counts']['raw_total'] == 2
        rows = full_records(first); assert len(rows) == 1006
        first_revision = first['revision']
        for item in instances:
            scoped_rows = full_records(first, instance=item['release']['instance_id'])
            assert len(scoped_rows) == 1
            scoped_processing = full_records(first, instance=item['release']['instance_id'], kind='processing')
            assert len(scoped_processing) == 1
        with Session(engine()) as db:
            for evidence in db.scalars(select(Evidence)):
                path = settings().storage_dir / evidence.storage_key
                first_files[evidence.id] = sha256(path.read_bytes()).hexdigest()
            assert not db.query(CloudCall).count()
        write('initial-full', {'ledger_id': first['id'], 'revision': first_revision,
            'counts': first['counts'], 'unknown_counts': unknown['counts'], 'paged_originals': len(rows),
            'download_record_labels_hash': digest(sorted(row['record_label'] for row in rows)),
            'download_unique_record_labels': len({row['record_label'] for row in rows}),
            'real_loopback_packets': 1008, 'duplicate_input_did_not_add_original': True,
            'elapsed_receive_and_process_seconds': round(time.monotonic() - began, 3)})
        with os.fdopen(os.open(ACCESS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
            json.dump({'url': url, 'username': account[0], 'password': account[1],
                'viewer_username': viewer_name, 'viewer_password': viewer_password,
                'workspace_id': account[2], 'gateway_id': gateway['id'], 'ledger_id': first['id'],
                'revision': first_revision, 'business_day': business_day,
                'revision_hash': first['revision_hash'],
                'instances': [item['release']['instance_id'] for item in instances]}, file)
        print('Disposable full daily browser runtime ready', flush=True)
        sequence = 0; deadline = time.monotonic() + 1400; api_expected_offline = False
        while time.monotonic() < deadline:
            if not api_expected_offline:
                assert api.poll() is None
            try:
                if os.getenv('GA_MQTT_DAILY_PROTOCOL_ONLY') == '1':
                    command = {'sequence': sequence + 1,
                        'command': ['late', 'restart', 'resume-workers', 'finish'][sequence]}
                else:
                    command = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except json.JSONDecodeError: time.sleep(.2); continue
            if command.get('sequence', 0) <= sequence: time.sleep(.2); continue
            sequence = command['sequence']; action = command['command']
            if action == 'late':
                publish(broker, [('sensor/raw', packet('late-original'))])
                wait_for(lambda: (row if (row := summary()) and row['counts']['raw_total'] == 1007 else None),
                    [mqtt, setup], timeout=25, description='late original updates its reported day')
                old_rows = full_records(first, revision=first_revision)
                assert len(old_rows) == 1006
                write('late-ready', {'old_revision_originals': len(old_rows), 'current': summary(),
                    'fixed_snapshot_excludes_late_original': True})
            elif action == 'offline':
                stop_child(mqtt); stop_child(setup)
                before = durable(); write('before-offline', before)
                stop_child(api); api_expected_offline = True
                write('offline', {'actual_api_stopped': True, 'workers_stopped': True})
            elif action == 'restart':
                stop_child(mqtt); stop_child(setup)
                before = durable(); write('before-restart', before)
                stop_child(api); api = start('serve')
                api_expected_offline = False
                assert durable() == before; write('after-restart', durable())
                write('restarted', {'actual_api_restart_all_table_digests_equal': True,
                    'workers_stopped_for_comparison': True})
            elif action == 'resume-workers':
                assert before is not None
                mqtt = start('mqtt-worker'); setup = start('setup-worker')
                try:
                    subscribed(gateway['id'], mqtt)
                except Exception:
                    write('worker-restart-failure', {'state': json.loads(json.dumps(receiver_state(gateway['id']), default=str)),
                        'broker_exit_code': broker.process.poll(), 'worker_exit_code': mqtt.poll(),
                        'protocol_events': broker.events(gateway['id'])[-20:]})
                    raise
                publish(broker, [('sensor/one', packet('shared-after-restart', device='device-one'))])
                def resumed():
                    row = summary()
                    return row if row and row['counts']['raw_total'] == 1008 and row['counts']['processing_succeeded'] == 4 else None
                wait_for(resumed, [mqtt, setup], timeout=45, description='actual workers restart and resume local processing')
                with Session(engine()) as db:
                    for identity, original_hash in first_files.items():
                        evidence = db.get(Evidence, identity)
                        assert sha256((settings().storage_dir / evidence.storage_key).read_bytes()).hexdigest() == original_hash
                    assert not db.query(CloudCall).count()
                write('workers-resumed', {'actual_workers_restart_and_new_ingestion': True,
                    'old_original_bytes_unchanged': True, 'current': summary()})
            elif action == 'finish':
                with Session(engine()) as db:
                    assert db.query(MqttReceipt).count() == 1010
                    assert db.query(MqttReceiptDelivery).count() == 4
                    assert db.query(Observation).filter_by(status='processed').count() == 4
                    assert not db.query(CloudCall).count()
                write('verification', {'classification': 'protocol_verified_progress', 'full_goal': 'active',
                    'originals': 1010, 'independent_processing_records': 4,
                    'full_page_and_fixed_revision_api': True, 'instance_scoped_originals': True,
                    'api_table_digest_restart': True, 'actual_worker_restart_then_ingestion': True,
                    'cloud_calls': 0, 'actual_loopback_mqtt': True, 'field_devices': False,
                    'browser_acceptance': 'separate_CUA_evidence_required',
                    'independent_commit_ack_audit_limit': 16,
                    'model_quality': 'synthetic_local_numeric_rules_only', 'production': False})
                print('Full daily protocol and durable worker recovery verified; browser acceptance is separate', flush=True); return
        raise AssertionError('Daily browser verification timed out')
    finally:
        for child in children: stop_child(child)
        broker.stop(crash=False)
        output_path = broker.trace.with_suffix('.stdout.log')
        if output_path.exists():
            (OUT / 'broker-output.log').write_text(output_path.read_text()[-18000:])
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
