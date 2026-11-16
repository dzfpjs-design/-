"""DJI status parsing and an isolated real MQTT -> PostgreSQL journey."""
import json
from hashlib import sha256
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.dji_dock_status_api import router, parse_osd
from generic_agents.models import MqttReceipt, User, Workspace, Membership
from generic_agents.mqtt_inbox import create_gateway, set_status
from generic_agents.security import Principal
from tests.test_mqtt_protocol import broker, reader, publish, ready_line, finish

if not any(getattr(route, 'path', None) == '/api/dji/dock-status/sources' for route in app.routes):
    app.include_router(router)


def test_osd_does_not_invent_valid_location_or_temperature():
    result = parse_osd(json.dumps({'timestamp': 1700000000123, 'data': {
        'home_position_is_valid': 0, 'latitude': 24.5, 'longitude': 117.6,
        'wind_speed': 3.5, 'environment_temperature': 29.5,
        'drone_charge_state': {'capacity_percent': 32767}, 'mode_code': 4,
        'drone_in_dock': 1, 'rainfall': 0}}).encode())
    assert result['position'] is None and result['battery_percent'] is None
    assert result['wind_m_s'] == 3.5 and result['reported_at'].startswith('2023-11-14')
    valid = parse_osd(b'{"data":{"home_position_is_valid":3,"latitude":24.5,"longitude":117.6}}')
    assert valid['position']['calibration'] == 'device_reported_valid' and valid['reported_at'] is None
    with pytest.raises(ValueError):
        parse_osd(b'{"data":{},"data":{}}')
    with pytest.raises(ValueError):
        parse_osd(b'{"data":{"wind_speed":NaN}}')


def test_real_mqtt_packet_is_scoped_and_source_time_preserved(account, broker, monkeypatch, client):
    origin = f'mqtt://127.0.0.1:{broker}'
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', origin)
    topic = 'thing/product/SIMDOCK123/osd'
    config = {'broker': origin, 'topics': {topic: '人工确认的模拟机场'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
        'fields': {}, 'username_ref': '', 'password_ref': ''}
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'fixture')
        gateway = create_gateway(db, p, '模拟 MQTT 接入', config)
        set_status(db, p, gateway.id, gateway.version, 'active'); db.commit()
        gateway_id = gateway.id
    raw = json.dumps({'timestamp': 1760000000123, 'data': {'mode_code': 4,
        'home_position_is_valid': 1, 'latitude': 24.5, 'longitude': 117.6,
        'wind_speed': 3.0, 'environment_temperature': 25, 'drone_in_dock': 1,
        'drone_charge_state': {'capacity_percent': 75}}}).encode()
    process = reader(gateway_id, origin, 1)
    try:
        ready_line(process, 'SUBSCRIBED'); publish(broker, [(topic, raw)])
        outcome = finish(process); assert outcome['received'] == 1 and outcome['failure'] is None
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)
    sources = client.get('/api/dji/dock-status/sources'); assert sources.status_code == 200
    assert len(sources.json()['sources']) == 1
    route = f'/api/dji/dock-status/sources/{gateway_id}/SIMDOCK123'
    response = client.get(route); assert response.status_code == 200
    view = response.json(); latest = view['latest']
    assert latest['parsed'] and latest['reported']['battery_percent'] == 75
    assert latest['reported']['position']['latitude'] == 24.5
    assert latest['reported']['reported_at'].startswith('2025-10-09')
    assert latest['received_at'] != latest['reported']['reported_at']
    assert 'device_online' not in json.dumps(view) and 'no-store' in response.headers['cache-control']
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway_id))
        assert row.input_sha256 == sha256(raw).hexdigest() and row.status == 'quarantined'
        # An authorised topic and exact workspace are required, never arbitrary broker probing.
        other = Workspace(name='其他工作区'); db.add(other); db.commit(); other_id = other.id
    assert client.get(f'/api/dji/dock-status/sources/{gateway_id}/OTHER').status_code == 404
    assert client.get(route,headers={'X-Workspace-ID':other_id}).status_code == 403
    # Reload/lookup is read-only and returns the same original receipt, even though
    # generic JSON sensor parsing quarantines this DJI-specific format.
    assert client.get(route).json()['latest']['receipt_id'] == latest['receipt_id']

    # A retained MQTT packet is not a live-presence signal. Deliver a different
    # packet before subscribing, then verify the broker sets the retain flag.
    import paho.mqtt.client as mqtt
    transmitter = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='fixture-retained-dock')
    transmitter.connect('127.0.0.1', broker, keepalive=30); transmitter.loop_start()
    retained = json.dumps({'timestamp': 1760000000124, 'data': {'mode_code': 0}}).encode()
    try:
        sent = transmitter.publish(topic, retained, qos=1, retain=True)
        sent.wait_for_publish(timeout=5); assert sent.is_published()
    finally:
        transmitter.disconnect(); transmitter.loop_stop()
    second = reader(gateway_id, origin, 1)
    try:
        assert finish(second)['received'] == 1
    finally:
        if second.poll() is None: second.terminate(); second.wait(timeout=5)
    retained_view = client.get(route).json()['latest']
    assert retained_view['retained'] is True and '不代表设备当前在线' in retained_view['state_note']
