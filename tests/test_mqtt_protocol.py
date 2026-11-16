"""A real loopback protocol and separate reader process, using simulated sensor input."""
from pathlib import Path
import json
import os
import select as io_select
import socket
import subprocess
import time
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.evidence import evidence_bytes
from generic_agents.models import User, MqttReceipt, CloudCall, Observation
from tests.test_mqtt_observations import prepared_mqtt, bind as confirm_binding, process_receipt
from generic_agents.security import Principal
from generic_agents.mqtt_inbox import create_gateway, set_status

ROOT = Path(__file__).resolve().parents[1]


def ready_line(process, expected, timeout=8):
    deadline = time.monotonic() + timeout
    lines = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('验证进程提前退出: ' + ''.join(lines + [process.stdout.read()])[:1200])
        readable, _, _ = io_select.select([process.stdout], [], [], .2)
        if readable:
            line = process.stdout.readline(); lines.append(line)
            if line.strip() == expected: return
    raise AssertionError('已启动的验证进程未在限定时间内提供准备状态')


@pytest.fixture
def broker():
    if os.getenv('GA_M39_REAL_MQTT') != '1': pytest.skip('真实协议验证需要显式开启隔离broker')
    python = ROOT / '.runtime/venvs/mqtt-broker/bin/python'
    assert python.is_file(), '隔离AMQTT解释器未准备好'
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
    child = subprocess.Popen([str(python), str(ROOT / 'tests/mqtt_broker_fixture.py'), str(port)],
        cwd=ROOT, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        ready_line(child, 'READY')
        yield port
    finally:
        if child.poll() is None: child.terminate()
        try: child.wait(timeout=8)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3)
        child.stdout.close()


def reader(gateway, origin, messages):
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                   'GA_DATABASE_URL': settings().database_url,
                   'GA_MQTT_ALLOWED_BROKERS': origin, 'GA_STORAGE_DIR': str(settings().storage_dir)}
    process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'generic_agents.mqtt_subscriber',
        '--gateway', gateway, '--seconds', '15', '--messages', str(messages)], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return process


def publish(port, messages):
    import paho.mqtt.client as mqtt
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='fixture-simulated-sensor')
    publisher.connect('127.0.0.1', port, keepalive=30); publisher.loop_start()
    try:
        for topic, raw in messages:
            result = publisher.publish(topic, raw, qos=1, retain=False)
            result.wait_for_publish(timeout=5)
            assert result.is_published()
    finally:
        publisher.disconnect(); publisher.loop_stop()


def finish(process):
    try:
        output, _ = process.communicate(timeout=20)
        assert process.returncode == 0, output[:1200]
        return json.loads(output.strip().splitlines()[-1])
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=5)


def fresh_worker(job_id,origin):
    environment={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8',
        'GA_DATABASE_URL':settings().database_url,'GA_MQTT_ALLOWED_BROKERS':origin,
        'GA_STORAGE_DIR':str(settings().storage_dir)}
    code='import sys; from generic_agents.worker import process_once; assert process_once(sys.argv[1],deliver=False); print("PROCESSED")'
    result=subprocess.run([str(ROOT/'.venv/bin/python'),'-c',code,job_id],cwd=ROOT,env=environment,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
    assert result.returncode==0 and result.stdout.strip()=='PROCESSED',result.stdout[:1200]


def fresh_data_receiver(origin):
    environment={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8',
        'GA_DATABASE_URL':settings().database_url,'GA_MQTT_ALLOWED_BROKERS':origin,
        'GA_STORAGE_DIR':str(settings().storage_dir)}
    code='from generic_agents.mqtt_data import deliver_pending; deliver_pending(); print("DELIVERED")'
    result=subprocess.run([str(ROOT/'.venv/bin/python'),'-c',code],cwd=ROOT,env=environment,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
    assert result.returncode==0 and result.stdout.strip()=='DELIVERED',result.stdout[:1200]


def test_real_qos1_originals_and_reader_process_restart_are_durable(account, broker, monkeypatch, client):
    origin = f'mqtt://127.0.0.1:{broker}'
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', origin)
    configuration = {'broker': origin, 'topics': {'fixture/sensor-1': 'sensor-1'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
        'fields': {}, 'username_ref': '', 'password_ref': ''}
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'mqtt-protocol-test')
        gateway = create_gateway(db, p, '真实协议验证的模拟传感器', configuration)
        set_status(db, p, gateway.id, gateway.version, 'active'); db.commit()
        identity = gateway.id
    raw = json.dumps({'record_id': 'protocol-1', 'device_id': 'sensor-1',
        'observed_at': '2026-10-10T23:00:00Z', 'values': {'t': 39.5}, 'units': {'t': 'C'}}).encode()
    bad = b'{"device_id":"sensor-1","record_id":"protocol-2","values":{"t":41}}'
    first = reader(identity, origin, 3)
    try:
        ready_line(first, 'SUBSCRIBED')
        publish(broker, [('fixture/sensor-1', raw), ('fixture/sensor-1', raw), ('fixture/sensor-1', bad)])
        first_result = finish(first)
    finally:
        if first.poll() is None: first.terminate(); first.wait(timeout=5)
    assert first_result['received'] == 3 and first_result['failure'] is None
    with Session(engine()) as db:
        rows = db.scalars(select(MqttReceipt).where(MqttReceipt.gateway_id == identity)).all()
        assert len(rows) == 2
        original_id = next(row.id for row in rows if row.data['decoded']['reported_record_id'] == 'protocol-1')
        bad_row = next(row for row in rows if row.id != original_id)
        assert bad_row.status == 'quarantined' and 'missing_capture_time' in bad_row.data['issues']
        assert evidence_bytes(db, p, bad_row.evidence_id)[1] == bad
        before = evidence_bytes(db, p, db.get(MqttReceipt, original_id).evidence_id)[1]
    response = client.get('/api/mqtt/gateways/' + identity + '/receipts')
    assert response.status_code == 200 and len(response.json()) == 2
    assert all(not row['accepted_as_observation'] for row in response.json())
    download = client.get('/api/mqtt/receipts/' + original_id + '/original')
    assert download.status_code == 200 and download.content == raw
    # A new OS process reconnects the same platform client and reuses the original receipt.
    second = reader(identity, origin, 1)
    try:
        ready_line(second, 'SUBSCRIBED')
        publish(broker, [('fixture/sensor-1', raw)])
        second_result = finish(second)
    finally:
        if second.poll() is None: second.terminate(); second.wait(timeout=5)
    assert second_result['receipt_ids'] == [original_id]
    with Session(engine()) as db:
        original = db.get(MqttReceipt, original_id)
        assert original.data['deliveries'] == 3 and evidence_bytes(db, p, original.evidence_id)[1] == before == raw
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 2
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Observation)) == 0
    assert client.get('/api/mqtt/receipts/' + original_id + '/original').content == raw


def test_running_reader_stops_when_gateway_paused_without_ack_or_new_receipt(account, broker, monkeypatch):
    origin = f'mqtt://127.0.0.1:{broker}'
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', origin)
    configuration = {'broker': origin, 'topics': {'fixture/pause': 'sensor-1'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
        'fields': {}, 'username_ref': '', 'password_ref': ''}
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'mqtt-pause-test')
        gateway = create_gateway(db, p, '暂停读取验证', configuration)
        set_status(db, p, gateway.id, gateway.version, 'active'); db.commit()
        identity, version = gateway.id, gateway.version
    process = reader(identity, origin, 1)
    try:
        ready_line(process, 'SUBSCRIBED')
        with Session(engine()) as db:
            set_status(db, p, identity, version, 'paused'); db.commit()
        raw = b'{"record_id":"r1","device_id":"sensor-1","observed_at":"2026-10-10T08:00:00Z","values":{"t":10}}'
        publish(broker, [('fixture/pause', raw)])
        output, _ = process.communicate(timeout=8)
        assert process.returncode == 1, output[:800]
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 0
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)


def test_actual_protocol_to_local_rule_business_anomaly_and_daily(client, prepared_mqtt, broker, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from generic_agents.models import Event, Task, Memory, Job
    from generic_agents.perception import object_state
    origin=f'mqtt://127.0.0.1:{broker}'
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers',origin)
    gateway,obj,rule,p=prepared_mqtt
    configured=client.put('/api/mqtt/gateways/'+gateway['id'],json={
        'name':gateway['name'],'expected_version':gateway['version'],
        'configuration':{**gateway['configuration'],'broker':origin}})
    assert configured.status_code==200,configured.text
    gateway=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={
        'expected_version':configured.json()['version'],'status':'active'}).json()
    fixture=(gateway,obj,rule,p);confirm_binding(client,fixture)
    clock=(datetime.now(timezone.utc)-timedelta(seconds=3)).isoformat()
    raw=json.dumps({'record_id':'through-real-mqtt','device_id':'device-one',
        'observed_at':clock,'values':{'t':39.5},'units':{'t':'C'}}).encode()
    process=reader(gateway['id'],origin,1)
    try:
        ready_line(process,'SUBSCRIBED');publish(broker,[('sensor/one',raw)])
        saved=finish(process)
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
    with Session(engine(),expire_on_commit=False) as db:
        receipt=db.get(MqttReceipt,saved['receipt_ids'][0])
        job=db.scalar(select(Job).where(Job.kind=='submit_mqtt_receipt',Job.data['receipt_id'].as_string()==receipt.id))
        assert job;submit_job_id=job.id
    # Reader has exited; a fresh OS worker consumes the durable submission.
    fresh_worker(submit_job_id,origin)
    with Session(engine()) as db:
        submitted=db.get(Job,submit_job_id);assert submitted.status=='succeeded'
        identity=db.get(MqttReceipt,receipt.id).data['observation_id']
        job=db.scalar(select(Job).where(Job.kind=='process_mqtt_observation',Job.data['observation_id'].as_string()==identity))
        assert job;extract_job_id=job.id
    # A second fresh OS worker consumes the saved observation/model choice.
    fresh_worker(extract_job_id,origin)
    fresh_data_receiver(origin)
    with Session(engine()) as db:
        row=db.get(Observation,identity)
        assert row.status=='processed' and row.data['extracted']['attributes']=={'temperature':39.5}
        assert object_state(db,p,obj['id'],['temperature'],3600)['fields']['temperature']['value']==39.5
        anomaly=db.scalar(select(Event).where(Event.kind=='perception.anomaly',Event.data['observation_id'].as_string()==identity))
        assert anomaly.status=='received'
        assert db.scalar(select(Task).where(Task.data['event_id'].as_string()==anomaly.id))
        from generic_agents.mqtt_daily import summary,records,validate_v2
        daily=db.scalar(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2')
            .order_by(Memory.created_at.desc()))
        assert daily and daily.status=='candidate'
        event=db.get(Event,daily.data['event_id']);assert event.status=='received'
        validate_v2(db,event)
        frozen=summary(db,p,daily.data['ledger_id'],revision=daily.data['revision'])
        assert frozen['revision_hash']==daily.data['revision_hash'] and frozen['counts']==daily.data['counts']
        assert frozen['counts']=={'raw_total':1,'data_received':1,'data_pending':0,'data_blocked':0,
            'raw_needs_review':0,'processing_total':1,'processing_succeeded':1,'processing_pending':0,
            'processing_failed':0,'processing_blocked':0}
        originals=records(db,p,frozen['id'],frozen['revision'],kind='original')
        processing=records(db,p,frozen['id'],frozen['revision'],kind='processing')
        assert originals['total']==1 and not originals['next_cursor']
        assert [(item['receipt_id'],item['status'],item['input_sha256']) for item in originals['records']]==[(receipt.id,'received',receipt.input_sha256)]
        assert processing['total']==1 and not processing['next_cursor']
        assert [(item['receipt_id'],item['observation_id'],item['status']) for item in processing['records']]==[(receipt.id,identity,'succeeded')]
        assert processing['records'][0]['deployment']=='local' and not processing['records'][0]['cloud_allowed']
        assert frozen['not_authoritative'] and not frozen['physical_state_verified']
        original_memory=db.scalar(select(Memory).where(Memory.data['type'].as_string()=='sensor_original'))
        assert original_memory.data['receipt_id']==receipt.id and original_memory.data['input_sha256']==receipt.input_sha256
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw
    assert client.get('/api/mqtt/gateways/'+gateway['id']+'/receipts').json()[0]['data_received']
