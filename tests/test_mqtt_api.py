from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import User, Membership, Workspace, MqttGateway
from generic_agents.security import Principal
from generic_agents.mqtt_inbox import capture


def configuration():
    return {'broker': 'mqtt://127.0.0.1:1883', 'topics': {'sensor/one': 'one'},
            'fields': {}, 'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
            'username_ref': '', 'password_ref': ''}


def test_saved_gateway_packet_review_pause_and_config_revision(client, account, monkeypatch):
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    response = client.post('/api/mqtt/gateways', json={'name': '传感器接入', 'configuration': configuration()})
    assert response.status_code == 201, response.text
    row = response.json(); assert row['status'] == 'paused' and not row['device_commands_supported']
    assert client.get('/api/mqtt/gateways').json() == [row]
    active = client.post('/api/mqtt/gateways/' + row['id'] + '/status',
        json={'expected_version': row['version'], 'status': 'active'}).json()
    assert '实际连接由读取进程确认' in active['status_meaning']
    raw = b'{"device_id":"one","record_id":"r1","observed_at":"2026-10-10T08:00:00+08:00","values":{"t":12}}'
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'api-protocol-review')
        receipt = capture(db, p, row['id'], active['version'], 'sensor/one', raw, 1)
        receipt_id = receipt.id; db.commit()
    receipt_view = client.get('/api/mqtt/gateways/' + row['id'] + '/receipts').json()[0]
    assert receipt_view['measurement_units']=={'temperature':'C'}
    assert receipt_view['status'] == 'awaiting_object_binding' and not receipt_view['accepted_as_observation']
    assert 'config_snapshot' not in receipt_view and 'received_by' not in receipt_view
    download = client.get('/api/mqtt/receipts/' + receipt_id + '/original')
    assert download.content == raw and download.headers['x-content-sha256'] == receipt_view['input_sha256']
    assert client.get('/api/mqtt/gateways/' + row['id'] + '/receipts?limit=101').status_code == 422
    changed=configuration();changed['mapping']['t']['unit']='F'
    updated = client.put('/api/mqtt/gateways/' + row['id'], json={
        'name': '已修改的传感器', 'configuration': changed, 'expected_version': active['version']})
    assert updated.status_code == 200 and updated.json()['status'] == 'paused'
    assert client.put('/api/mqtt/gateways/' + row['id'], json={
        'name': 'stale', 'configuration': configuration(), 'expected_version': active['version']}).status_code == 409
    assert client.get('/api/mqtt/gateways/' + row['id'] + '/receipts').json()[0] == receipt_view
    assert client.get('/api/mqtt/receipts/' + receipt_id + '/original').content == raw
    # Removing server network permission must never prevent the user from pausing.
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', '')
    paused = client.post('/api/mqtt/gateways/' + row['id'] + '/status', json={
        'expected_version': updated.json()['version'], 'status': 'paused'})
    assert paused.status_code == 200
    assert client.get('/api/mqtt/receipts/' + receipt_id + '/original').content == raw
    assert client.post('/api/mqtt/gateways/' + row['id'] + '/status', json={
        'expected_version': paused.json()['version'], 'status': 'active'}).status_code == 403


def test_unauthorized_non_admin_and_foreign_workspace_cannot_configure(client, account, monkeypatch):
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    row = client.post('/api/mqtt/gateways', json={'name': 'own', 'configuration': configuration()}).json()
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        foreign = Workspace(name='无权访问的工作区'); db.add(foreign); db.flush()
        gateway = MqttGateway(workspace_id=foreign.id, owner_id=user.id, name='other', status='paused', data=configuration())
        db.add(gateway); db.flush(); foreign_id = gateway.id
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        membership.role = 'viewer'; db.commit()
    assert client.get('/api/mqtt/gateways/' + foreign_id + '/receipts').status_code == 404
    assert client.post('/api/mqtt/gateways', json={'name': 'forbidden', 'configuration': configuration()}).status_code == 403
    assert client.post('/api/mqtt/gateways/' + row['id'] + '/status',
        json={'status': 'active', 'expected_version': row['version']}).status_code == 403
    assert client.get('/api/mqtt/gateways/missing/receipts').status_code == 404
    assert client.get('/api/mqtt/gateways/'+row['id']+'/receipts?after=missing').status_code==404
    assert client.get('/api/mqtt/receipts/missing/original').status_code == 404
    client.cookies.clear()
    assert client.get('/api/mqtt/gateways').status_code == 401


def test_broker_permissions_and_unknown_secret_or_payload_ingest_endpoint_are_explicit(client):
    assert client.post('/api/mqtt/gateways', json={'name': 'bad', 'configuration': configuration()}).status_code == 403
    assert client.post('/api/mqtt/receipts', json={'payload': 'not real MQTT'}).status_code == 404
