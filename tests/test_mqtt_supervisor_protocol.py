"""Opt-in real MQTT worker / PostgreSQL / loopback broker recovery evidence.

Run only with GA_MQTT_SUPERVISOR_REAL=1. conftest owns a disposable ga_test_*
database. No field device, cloud model, command publisher, or normal app DB is used.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.evidence import evidence_bytes
from generic_agents.models import CloudCall, Job, Membership, MqttGateway, MqttReceipt, Observation, User
from generic_agents.mqtt_inbox import create_gateway, set_status
from generic_agents.security import Principal
from tests.test_mqtt_observations import prepared_mqtt, bind as confirm_binding
from tests.test_mqtt_protocol import ready_line

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.getenv('GA_MQTT_SUPERVISOR_REAL') != '1',
    reason='真实长驻MQTT验证仅在GA_MQTT_SUPERVISOR_REAL=1时运行；需要隔离PG和AMQTT。',
)


def stop_child(process, *, crash=False):
    if process.poll() is None:
        process.kill() if crash else process.terminate()
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=3)


class LoopbackBroker:
    def __init__(self, port, trace, *, lifetime=240, audit_limit=2000, commit_audit_limit=None):
        assert 1 <= lifetime <= 1800
        assert 2000 <= audit_limit <= 12000
        assert commit_audit_limit is None or 1 <= commit_audit_limit <= 2000
        self.port, self.trace, self.process, self.lifetime = port, trace, None, lifetime
        self.audit_limit = audit_limit
        self.commit_audit_limit = commit_audit_limit

    @property
    def origin(self):
        return f'mqtt://127.0.0.1:{self.port}'

    def start(self):
        assert self.process is None or self.process.poll() is not None
        self.process = subprocess.Popen([
            str(ROOT / '.runtime/venvs/mqtt-broker/bin/python'),
            str(ROOT / 'tests/mqtt_supervisor_broker_fixture.py'), str(self.port), str(self.trace), str(self.lifetime), str(self.audit_limit),
            str(self.commit_audit_limit) if self.commit_audit_limit is not None else 'all',
        ], cwd=ROOT, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
            'GA_MQTT_PROTOCOL_AUDIT_DATABASE_URL': settings().database_url,
            'GA_MQTT_PROTOCOL_AUDIT_PYTHON': str(ROOT / '.venv/bin/python')},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready_line(self.process, 'READY')
        assert self.process.poll() is None
        process = self.process
        output_path = self.trace.with_suffix('.stdout.log')
        def drain_output():
            written = 0
            with output_path.open('a') as output:
                for line in process.stdout:
                    if written < 1024 * 1024:
                        part = line[:1024 * 1024 - written]
                        output.write(part); output.flush(); written += len(part)
                    # Continue draining after the bounded artifact is full.
        self.output_thread = threading.Thread(target=drain_output, daemon=True)
        self.output_thread.start()

    def stop(self, *, crash=True):
        if self.process:
            stop_child(self.process, crash=crash)
            assert self.process.poll() is not None
            if hasattr(self, 'output_thread'):
                self.output_thread.join(timeout=3)
                assert not self.output_thread.is_alive(), 'Broker diagnostic reader did not stop'
            self.process.stdout.close()

    def events(self, gateway_id):
        if not self.trace.exists():
            return []
        rows = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return [row for row in rows if row['client_id'].startswith('ga-read-v2-' + gateway_id + '-v')]


@pytest.fixture
def protocol_broker(tmp_path, monkeypatch):
    assert make_url(settings().database_url).database.startswith('ga_test_'), '禁止真实应用数据库'
    python = ROOT / '.runtime/venvs/mqtt-broker/bin/python'
    if not python.is_file():
        pytest.skip('AMQTT0.12.1隔离运行时未准备好；真实协议验证未执行')
    pytest.importorskip('paho.mqtt.client', reason='paho2.1.0未准备好；真实协议验证未执行')
    from importlib.metadata import version
    assert version('paho-mqtt') == '2.1.0'
    check = subprocess.run([str(python), '-c',
        'import amqtt; assert amqtt.__version__ == "0.12.1"'],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
    assert check.returncode == 0, '真实协议fixture需要AMQTT0.12.1'
    from generic_agents import models
    if not hasattr(models, 'MqttReceiverState') or not (ROOT / 'src/generic_agents/mqtt_supervisor.py').is_file():
        pytest.skip('长驻接收实现或状态迁移尚未准备好；真实协议验证未执行')
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
    broker = LoopbackBroker(port, tmp_path / 'real-packets.jsonl')
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', broker.origin)
    try:
        broker.start()
        yield broker
    finally:
        broker.stop(crash=False)


def worker_environment(origin):
    assert make_url(settings().database_url).database.startswith('ga_test_')
    return {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
            'GA_DATABASE_URL': settings().database_url, 'GA_MQTT_ALLOWED_BROKERS': origin,
            'GA_STORAGE_DIR': str(settings().storage_dir),
            'GA_MQTT_RECEIVER_POLL_SECONDS': '0.2', 'GA_MQTT_RECEIVER_LEASE_SECONDS': '5',
            'GA_MQTT_RECEIVER_CONNECT_TIMEOUT_SECONDS': '1',
            'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}


@pytest.fixture
def worker_processes(tmp_path):
    children = []

    def start(origin):
        log = tmp_path / f'mqtt-worker-{len(children)}.log'
        with log.open('w') as output:
            child = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'generic_agents.cli', 'mqtt-worker'],
                cwd=ROOT, env=worker_environment(origin), stdout=output, stderr=subprocess.STDOUT, text=True)
        child.protocol_log = log
        children.append(child)
        return child

    yield start
    for child in children:
        stop_child(child)


def alive(process):
    assert process.poll() is None, 'mqtt-worker提前退出: ' + process.protocol_log.read_text()[-1200:]


def wait_for(check, processes=(), *, timeout=25, description='真实协议条件'):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        for process in processes:
            alive(process)
        last = check()
        if last:
            return last
        time.sleep(.25)
    raise AssertionError(description + '未在限定时间内成立；最后结果=' + repr(last)[:500])


def observe_for(check, processes=(), *, seconds=1.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for process in processes:
            alive(process)
        assert check()
        time.sleep(.25)


def receiver_state(gateway_id):
    from generic_agents.models import MqttReceiverState
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceiverState).where(MqttReceiverState.gateway_id == gateway_id))
        if not row:
            return None
        return {'status': row.status, 'gateway_version': row.gateway_version, 'config_hash': row.config_hash,
                'lease_token': row.lease_token, 'lease_until': row.lease_until,
                'heartbeat_at': row.heartbeat_at, 'data': dict(row.data)}


def subscribed(gateway_id, process, *, different_token=None, gateway_version=None):
    def ready():
        state = receiver_state(gateway_id)
        if not state or state['status'] != 'receiving' or not state['data'].get('subscribed_at'):
            return None
        if different_token and state['lease_token'] == different_token:
            return None
        if gateway_version and state['gateway_version'] != gateway_version:
            return None
        assert state['heartbeat_at'] and state['lease_until'] > datetime.now(timezone.utc)
        return state
    return wait_for(ready, [process], description='live mqtt-worker、有效租约和真实SUBACK状态')


def configuration(origin, *, topic='fixture/one', topics=None):
    return {'broker': origin, 'topics': topics or {topic: 'sensor-1'},
            'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
            'fields': {}, 'username_ref': '', 'password_ref': ''}


def active_gateway(account, origin, *, topics=None):
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        principal = Principal(user.id, account[2], 'admin', 'mqtt-supervisor-protocol')
        gateway = create_gateway(db, principal, '本地模拟报文协议验证', configuration(origin, topics=topics))
        set_status(db, principal, gateway.id, gateway.version, 'active'); db.commit()
        return principal, gateway.id, gateway.version


def packet(record, *, device='sensor-1', value=39.5):
    return json.dumps({'record_id': record, 'device_id': device,
        'observed_at': (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
        'values': {'t': value}, 'units': {'t': 'C'}}, separators=(',', ':')).encode()


def publish(broker, messages):
    assert broker.process.poll() is None, '真实broker已退出'
    import paho.mqtt.client as mqtt
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='simulated-sensor-' + uuid4().hex[:10])
    publisher.connect('127.0.0.1', broker.port, keepalive=10); publisher.loop_start()
    try:
        for topic, raw in messages:
            result = publisher.publish(topic, raw, qos=1, retain=False)
            result.wait_for_publish(timeout=5)
            assert result.is_published(), '模拟源报文未被真实broker确认'
    finally:
        publisher.disconnect(); publisher.loop_stop()


def receipt(gateway_id, raw):
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway_id,
                                                   MqttReceipt.input_sha256 == sha256(raw).hexdigest()))
        return {'id': row.id, 'evidence_id': row.evidence_id, 'sha256': row.input_sha256,
                'deliveries': row.data['deliveries'], 'data': dict(row.data)} if row else None


def received(broker, gateway_id, raw, worker, *, deliveries=1):
    row = wait_for(lambda: (value if (value := receipt(gateway_id, raw)) and
                           value['deliveries'] >= deliveries else None), [worker],
                   description='已提交PG原件和去重收件记录')
    def acknowledged():
        events = broker.events(gateway_id)
        publications = [event for event in events if event.get('packet') == 'PublishPacket' and
                        event['event'] == 'mqtt_packet_sent' and event.get('sha256') == row['sha256']]
        return any(ack['event'] == 'mqtt_packet_received' and ack.get('packet') == 'PubackPacket' and
                   ack['broker_pid'] == item['broker_pid'] and ack['mid'] == item['mid'] and
                   ack['sequence'] > item['sequence'] and ack.get('committed_visible_at_puback') is True
                   for item in publications for ack in events)
    wait_for(acknowledged, [worker], description='真实broker收到PUBACK时独立PG会话可见已提交原件')
    return row


def protocol_read_only(broker, gateway_id, topics):
    events = broker.events(gateway_id)
    subscriptions = [row for row in events if row.get('packet') == 'SubscribePacket' and
                     row['event'] == 'mqtt_packet_received']
    assert subscriptions, '只有真实SUBSCRIBE审计可作为订阅证据'
    assert all(qos == 1 for row in subscriptions for _, qos in row['topics'])
    assert set(topics) <= {topic for row in subscriptions for topic, _ in row['topics']}
    assert not any(row.get('packet') == 'PublishPacket' and row['event'] == 'mqtt_packet_received'
                   for row in events), '接收worker不得向设备发送任何PUBLISH命令'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


def fresh_local_job(job_id, origin):
    code = ('import sys; from generic_agents.worker import process_once; '
            'assert process_once(sys.argv[1],deliver=False); print("PROCESSED")')
    result = subprocess.run([str(ROOT / '.venv/bin/python'), '-c', code, job_id],
        cwd=ROOT, env=worker_environment(origin), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == 'PROCESSED', result.stdout[-1200:]


def test_real_supervisor_continuous_dedupe_broker_and_process_recovery_to_local_data(
        client, prepared_mqtt, protocol_broker, worker_processes):
    """Real transport / durability evidence; numeric fixture is not field precision."""
    broker = protocol_broker
    gateway, obj, rule, principal = prepared_mqtt
    configured = client.put('/api/mqtt/gateways/' + gateway['id'], json={
        'name': gateway['name'], 'expected_version': gateway['version'],
        'configuration': {**gateway['configuration'], 'broker': broker.origin}})
    assert configured.status_code == 200, configured.text
    activated = client.post('/api/mqtt/gateways/' + gateway['id'] + '/status', json={
        'expected_version': configured.json()['version'], 'status': 'active'})
    assert activated.status_code == 200, activated.text
    gateway = activated.json(); identity = gateway['id']
    confirm_binding(client, (gateway, obj, rule, principal))
    worker = worker_processes(broker.origin)
    first_state = subscribed(identity, worker)
    raw = packet('continuous-round-1', device='device-one')
    second_raw = packet('continuous-round-2', device='device-one', value=37)
    publish(broker, [('sensor/one', raw)]); first = received(broker, identity, raw, worker)
    publish(broker, [('sensor/one', second_raw), ('sensor/one', raw)])
    received(broker, identity, second_raw, worker); received(broker, identity, raw, worker, deliveries=2)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 2
        evidence, saved = evidence_bytes(db, principal, first['evidence_id'])
        assert saved == raw and evidence.sha256 == first['sha256'] == sha256(raw).hexdigest()

    broker.stop(crash=True)
    def waiting():
        state = receiver_state(identity)
        return state if state and state['status'] == 'backoff' else None
    backoff = wait_for(waiting, [worker], description='broker退出后持久化backoff状态')
    assert backoff['data'].get('error_code') and backoff['data'].get('next_retry_at')
    assert any(item.get('state') == 'backoff' for item in backoff['data'].get('history', []))
    broker.start()
    reconnected = subscribed(identity, worker)
    assert reconnected['data']['subscribed_at'] != first_state['data']['subscribed_at']
    third_raw = packet('after-broker-restart', device='device-one', value=36)
    publish(broker, [('sensor/one', third_raw)]); received(broker, identity, third_raw, worker)

    old_token = reconnected['lease_token']
    stop_child(worker, crash=True)
    assert worker.poll() is not None
    restarted = worker_processes(broker.origin)
    subscribed(identity, restarted, different_token=old_token)
    fourth_raw = packet('after-worker-restart', device='device-one', value=35)
    publish(broker, [('sensor/one', raw), ('sensor/one', fourth_raw)])
    recovered = received(broker, identity, raw, restarted, deliveries=3)
    received(broker, identity, fourth_raw, restarted)
    assert recovered['id'] == first['id'] and recovered['evidence_id'] == first['evidence_id']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 4
        assert evidence_bytes(db, principal, recovered['evidence_id'])[1] == raw
        job = db.scalar(select(Job).where(Job.kind == 'submit_mqtt_receipt',
                        Job.data['receipt_id'].as_string() == first['id']))
        assert job; submit_id = job.id
    fresh_local_job(submit_id, broker.origin)
    with Session(engine()) as db:
        submitted = db.get(Job, submit_id)
        assert submitted.status == 'succeeded'
        observation_id = db.get(MqttReceipt, first['id']).data['observation_id']
        job = db.scalar(select(Job).where(Job.kind == 'process_mqtt_observation',
                        Job.data['observation_id'].as_string() == observation_id))
        assert job; extract_id = job.id
    fresh_local_job(extract_id, broker.origin)
    delivery = subprocess.run([str(ROOT / '.venv/bin/python'), '-c',
        'from generic_agents.mqtt_data import deliver_pending; deliver_pending(); print("DELIVERED")'],
        cwd=ROOT, env=worker_environment(broker.origin), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=30)
    assert delivery.returncode == 0 and delivery.stdout.strip() == 'DELIVERED', delivery.stdout[-1200:]
    with Session(engine()) as db:
        observation = db.get(Observation, observation_id)
        assert observation.status == 'processed'
        assert observation.data['extracted']['attributes'] == {'temperature': 39.5}
    records = client.get('/api/mqtt/gateways/' + identity + '/receipts')
    assert records.status_code == 200
    original = next(row for row in records.json() if row['id'] == first['id'])
    assert original['accepted_as_observation'] and original['data_received']
    assert client.get('/api/mqtt/receipts/' + first['id'] + '/original').content == raw
    alive(restarted); protocol_read_only(broker, identity, ['sensor/one'])
    stopping_at = time.time()
    stop_child(restarted)
    assert restarted.returncode == 0, 'CLI应响应SIGTERM并优雅关闭receiver'
    terminal = wait_for(lambda: (state if (state := receiver_state(identity)) and
                        state['status'] == 'stopped' and not state['lease_token'] else None),
                        timeout=8, description='优雅停机的持久stopped状态与释放租约')
    assert terminal['data'].get('error_code') == 'stopped'
    wait_for(lambda: any(event['event'] == 'broker_client_disconnected' and event['time'] >= stopping_at
                        for event in broker.events(identity)), timeout=8,
             description='SIGTERM之后真实broker确认断开')
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 4
        assert evidence_bytes(db, principal, first['evidence_id'])[1] == raw


@pytest.mark.parametrize('change', ['paused', 'owner_revoked'])
def test_real_supervisor_stops_after_pause_or_owner_revocation_without_ack_or_receipt(
        account, protocol_broker, worker_processes, change):
    broker = protocol_broker
    principal, identity, version = active_gateway(account, broker.origin)
    worker = worker_processes(broker.origin); subscribed(identity, worker)
    initial = packet('before-guard'); publish(broker, [('fixture/one', initial)])
    original = received(broker, identity, initial, worker)
    with Session(engine()) as db:
        if change == 'paused':
            set_status(db, principal, identity, version, 'paused')
        else:
            member = db.scalar(select(Membership).where(Membership.user_id == principal.user_id,
                                                         Membership.workspace_id == principal.workspace_id))
            member.role = 'viewer'
        db.commit()
    wait_for(lambda: (state if (state := receiver_state(identity)) and
                     state['status'] != 'receiving' and not state['lease_token'] else None),
             [worker], description='许可撤回后数据库停止态和释放独占租约')
    wait_for(lambda: any(row['event'] == 'broker_client_disconnected' for row in broker.events(identity)),
             [worker], description='真实broker确认接收者断开')
    before = len(broker.events(identity))
    blocked_raw = packet('while-' + change)
    publish(broker, [('fixture/one', blocked_raw)])
    def not_accepted():
        return receipt(identity, blocked_raw) is None and not any(
            row.get('packet') == 'PubackPacket' and row['event'] == 'mqtt_packet_received'
            for row in broker.events(identity)[before:])
    observe_for(not_accepted, [worker], seconds=2)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 1
        assert db.get(MqttReceipt, original['id']).input_sha256 == sha256(initial).hexdigest()
    protocol_read_only(broker, identity, ['fixture/one'])


def test_two_fresh_workers_have_one_real_subscription_and_crash_takeover(
        account, protocol_broker, worker_processes):
    broker = protocol_broker
    topics = {'fixture/one': 'sensor-1', 'fixture/two': 'sensor-2'}
    principal, identity, _ = active_gateway(account, broker.origin, topics=topics)
    leader = worker_processes(broker.origin); first = subscribed(identity, leader)
    follower = worker_processes(broker.origin)
    def exclusive():
        state = receiver_state(identity)
        events = broker.events(identity)
        return state and state['lease_token'] == first['lease_token'] and sum(
            row['event'] == 'broker_client_connected' for row in events) == 1
    observe_for(exclusive, [leader, follower], seconds=2)
    key = int.from_bytes(sha256(('mqtt-reader:' + identity).encode()).digest()[:8], 'big', signed=True)
    unsigned = key & ((1 << 64) - 1)
    with engine().connect() as connection:
        count = connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
            'AND classid=:high AND objid=:low AND objsubid=1'),
            {'high': unsigned >> 32, 'low': unsigned & ((1 << 32) - 1)})
        assert count == 1, '真实PG会话仅允许一个gateway leader'
    raw = packet('single-leader')
    publish(broker, [('fixture/one', raw)])
    received(broker, identity, raw, leader)
    assert receipt(identity, raw)['deliveries'] == 1
    stop_child(leader, crash=True)
    takeover = subscribed(identity, follower, different_token=first['lease_token'])
    assert takeover['lease_token']
    next_raw = packet('follower-takeover', device='sensor-2')
    publish(broker, [('fixture/two', next_raw)])
    received(broker, identity, next_raw, follower)
    protocol_read_only(broker, identity, topics)


def test_real_active_config_version_switch_disconnects_and_reads_only_new_topics(
        client, account, protocol_broker, worker_processes):
    broker = protocol_broker
    principal, identity, version = active_gateway(account, broker.origin)
    worker = worker_processes(broker.origin); before = subscribed(identity, worker)
    raw = packet('old-topic-original'); publish(broker, [('fixture/one', raw)])
    original = received(broker, identity, raw, worker)
    changed = client.put('/api/mqtt/gateways/' + identity, json={
        'name': '切换主题后的本地模拟来源', 'expected_version': version,
        'configuration': configuration(broker.origin, topic='fixture/new')})
    assert changed.status_code == 200, changed.text
    activated = client.post('/api/mqtt/gateways/' + identity + '/status', json={
        'expected_version': changed.json()['version'], 'status': 'active'})
    assert activated.status_code == 200, activated.text
    after = subscribed(identity, worker, different_token=before['lease_token'],
                       gateway_version=activated.json()['version'])
    assert after['config_hash'] != before['config_hash']
    assert any(row['event'] == 'broker_client_disconnected' for row in broker.events(identity))
    # A stale persistent broker subscription must not starve the new authorized topic.
    unauthorized = packet('removed-topic')
    current = packet('current-topic')
    publish(broker, [('fixture/one', unauthorized), ('fixture/new', current)])
    received(broker, identity, current, worker)
    assert receipt(identity, unauthorized) is None
    with Session(engine()) as db:
        assert db.get(MqttReceipt, original['id']).input_sha256 == sha256(raw).hexdigest()
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 2
    protocol_read_only(broker, identity, ['fixture/one', 'fixture/new'])
