"""Root-run disposable-PG + real loopback simulator acceptance for V268.

These tests create actual saved requests, run the scoped consumer and inspect
both committed tables and public DTOs. They never assert physical connectivity.
"""
import copy
import json
import socket
import threading
import time
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents import device_gateway_consumer_v268 as consumer
from generic_agents import device_gateway_store_v268 as store
from generic_agents import gateway_credentials_v268 as crypto
from generic_agents.device_gateway_models_v268 import (DeviceGatewayConnectionV268 as Connection,
    DeviceGatewayCommandV268 as Command, DeviceGatewayDeviceGrantV268 as Grant)
from generic_agents.device_simulator_v175 import create_app
from generic_agents.security import new_session

BASE = '/api/workspace/device-gateways'
TOKEN = 'original-gateway-secret-' + 'a' * 32


def good(response):
    assert response.status_code < 300, response.text[:500]
    return response.json()


def nonce():
    return str(uuid4())


@pytest.fixture
def scene(client, tmp_path, monkeypatch, request):
    key_dir = tmp_path / 'gateway-keys'; key_dir.mkdir(mode=0o700)
    key = key_dir / 'master.key'; crypto.create_key(key)
    monkeypatch.setattr(settings(), 'device_gateway_key_file', key)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(20)
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', origin)
    token = getattr(request, 'param', TOKEN)
    simulator = create_app(tmp_path / 'supplier.sqlite', token)
    server = uvicorn.Server(uvicorn.Config(simulator, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    workspace_id = client.headers['X-Workspace-ID']
    with Session(engine()) as db:
        actor_id = db.scalar(select(m.Membership.user_id).where(m.Membership.workspace_id == workspace_id))
    s = NS(client=client, workspace_id=workspace_id, actor_id=actor_id, origin=origin,
           server=server, simulator=simulator, key=key, token=token)
    yield s
    server.should_exit = True; thread.join(timeout=5); sock.close()


def create(s, **changes):
    body = {'request_id': nonce(), 'name': '采购模拟设备目录', 'origin': s.origin, 'token': s.token}
    body.update(changes)
    result = good(s.client.post(BASE, json=body))
    s.connection_id = result['connection_id']
    return body, result


def discover(s, expected_version=1):
    result = good(s.client.post(BASE + '/' + s.connection_id + '/discover', json={
        'request_id': nonce(), 'expected_version': expected_version, 'reason': '明确测试采购模拟目录'}))
    return result['discovery']['id']


def read(s):
    return good(s.client.get(BASE + '/' + s.connection_id))


def completed(s):
    identifier = discover(s)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + identifier))
    assert result['status'] == 'succeeded' and result['effective']
    return result


def grant(s, discovery):
    device = next(d for d in discovery['devices'] if d['kind'] == 'weather_station')
    body = {'request_id': nonce(), 'expected_version': read(s)['version'],
        'discovery_job_id': discovery['id'], 'device_external_id': device['external_id'],
        'actions': ['get_status', 'read_telemetry'], 'reason': '确认该模拟监测站及逐项只读动作'}
    return body, good(s.client.post(BASE + '/' + s.connection_id + '/grant', json=body))


def change(s, operation, **fields):
    return good(s.client.post(BASE + '/' + s.connection_id + '/' + operation, json={
        'request_id': nonce(), 'expected_version': read(s)['version'], 'reason': '管理员明确修改连接', **fields}))


def test_real_catalogue_persists_and_grant_registers_review_identity_only(scene):
    s = scene; body, created = create(s)
    assert created['connection']['credential_configured'] and created['connection']['latest_discovery'] is None
    discovery = completed(s)
    assert len(discovery['devices']) == 3
    assert {d['kind'] for d in discovery['devices']} == {'drone_dock', 'ptz_camera', 'weather_station'}
    for d in discovery['devices']:
        assert d['simulation'] is True and d['origin_kind'] == 'simulated_equipment'
        assert d['field_verified'] is False and d['physical_device_connected'] is False
    grant_body, receipt = grant(s, discovery)
    saved = receipt['grant']; assert saved['effective']
    with Session(engine()) as db:
        connection = db.get(Connection, s.connection_id)
        assert TOKEN not in connection.token_cipher and TOKEN not in json.dumps(connection.data)
        assert crypto.open_secret(s.workspace_id, connection.id, 1, connection.token_cipher) == TOKEN
        identity = db.get(m.SensorDevice, saved['sensor_device_id'])
        assert identity.status == 'review' and identity.approved_by is None
        assert identity.identity_issuer == 'gateway:' + connection.id
        assert db.scalar(select(m.AssetDeviceBinding.id).where(m.AssetDeviceBinding.workspace_id == s.workspace_id)) is None
        for cls in (Command, Grant, m.Job, m.Audit):
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)):
                assert TOKEN not in json.dumps(row.data)
    assert read(s)['grants'][0]['id'] == saved['id']
    assert good(s.client.post(BASE + '/' + s.connection_id + '/grant', json=grant_body)) == receipt
    same = {**grant_body, 'request_id': nonce()}
    assert good(s.client.post(BASE + '/' + s.connection_id + '/grant', json=same))['grant']['id'] == saved['id']
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Grant).where(Grant.workspace_id == s.workspace_id)))) == 1


def test_actor_nonce_replays_exact_receipt_and_conflicting_payload_fails(scene):
    s = scene; body, receipt = create(s)
    assert good(s.client.post(BASE, json=body)) == receipt
    for changed in ({'token': 'different-secret-token-123456789'}, {'name': '另一个原申请'}):
        assert s.client.post(BASE, json={**body, **changed}).status_code == 409
    lookup = good(s.client.get(BASE + '/requests/' + body['request_id']))
    assert lookup == {'found': True, 'request_id': body['request_id'], 'result': receipt}
    missing = nonce()
    assert good(s.client.get(BASE + '/requests/' + missing)) == {'found': False, 'request_id': missing, 'result': None}
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Connection).where(Connection.workspace_id == s.workspace_id)))) == 1


def test_get_is_passive_without_key_decrypt_or_network(scene, monkeypatch):
    s = scene; body, _ = create(s); completed(s)
    def forbidden(*args, **kwargs):
        raise AssertionError('a GET must never access credentials or transport')
    for name in ('_master_key', 'seal', 'open_secret', 'request_fingerprint', 'validate_origin'):
        monkeypatch.setattr(crypto, name, forbidden)
    monkeypatch.setattr(consumer, 'fetch_catalogue', forbidden)
    assert len(good(s.client.get(BASE))['connections']) == 1
    result = read(s)
    assert good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + result['latest_discovery']['id']))['effective']
    assert good(s.client.get(BASE + '/requests/' + body['request_id']))['found']
    assert TOKEN not in json.dumps(result) and 'token_cipher' not in json.dumps(result)


def test_pause_resume_and_rotation_invalidate_previous_discovery_and_grant(scene):
    s = scene; create(s); discovery = completed(s); _, receipt = grant(s, discovery)
    change(s, 'status', status='paused')
    paused = read(s)
    assert not paused['latest_discovery']['effective'] and not paused['grants'][0]['effective']
    assert s.client.post(BASE + '/' + s.connection_id + '/discover', json={
        'request_id': nonce(), 'expected_version': paused['version'], 'reason': '不能暂停时请求'}).status_code == 409
    change(s, 'status', status='enabled')
    assert not read(s)['grants'][0]['effective']
    change(s, 'credential', token='new-explicit-credential-123456789')
    current = read(s)
    assert current['credential_revision'] == 2 and not current['latest_discovery']['effective']
    assert not current['grants'][0]['effective']
    with Session(engine()) as db:
        connection = db.get(Connection, s.connection_id)
        assert crypto.open_secret(s.workspace_id, connection.id, 2, connection.token_cipher) == 'new-explicit-credential-123456789'


@pytest.mark.parametrize('change_kind', ['pause', 'credential', 'revoke'])
def test_changes_during_http_fail_same_job_without_success_or_new_job(scene, monkeypatch, change_kind):
    s = scene; create(s); job_id = discover(s); real = consumer.fetch_catalogue
    def changed(origin, token):
        devices = real(origin, token)
        if change_kind == 'pause':
            change(s, 'status', status='paused')
        elif change_kind == 'credential':
            change(s, 'credential', token='new-explicit-credential-123456789')
        else:
            with Session(engine()) as db:
                member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
                    m.Membership.user_id == s.actor_id))
                member.role = 'operator'; db.commit()
        return devices
    monkeypatch.setattr(consumer, 'fetch_catalogue', changed)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))
    assert result['status'] == 'failed' and result['devices'] == [] and not result['effective']
    assert result['error_code'] in {'discovery_connection_changed', 'discovery_actor_revoked'}
    assert not consumer.consume_once(s.workspace_id, s.connection_id)
    with Session(engine()) as db:
        rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id, m.Job.kind == store.KIND)))
        assert len(rows) == 1 and rows[0].id == job_id and rows[0].lease_token is None
        audits = list(db.scalars(select(m.Audit).where(m.Audit.object_id == job_id, m.Audit.action == store.COMPLETED)))
        assert len(audits) == 1


def test_worker_rechecks_admin_before_http_and_global_claim_excludes_gateway(scene, monkeypatch):
    s = scene; create(s); job_id = discover(s)
    from generic_agents.worker import claim
    assert claim() is None
    with Session(engine()) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
            m.Membership.user_id == s.actor_id))
        member.role = 'operator'; db.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError('revoked actor must not make an HTTP request')
    monkeypatch.setattr(consumer, 'fetch_catalogue', forbidden)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))
    assert result['status'] == 'failed' and result['error_code'] == 'discovery_actor_revoked'
    assert s.client.post(BASE, json={'request_id': nonce(), 'name': '越权', 'origin': s.origin, 'token': TOKEN}).status_code == 403


def test_cross_workspace_and_actor_receipts_are_scoped(scene):
    s = scene; body, _ = create(s)
    with Session(engine()) as db:
        another_ws = m.Workspace(name='另一个工作区'); db.add(another_ws); db.flush()
        db.add(m.Membership(workspace_id=another_ws.id, user_id=s.actor_id, role='admin'))
        other_id = another_ws.id
        user = m.User(username='gateway-' + uuid4().hex, password_hash='unusable-test-account'); db.add(user); db.flush()
        db.add(m.Membership(workspace_id=s.workspace_id, user_id=user.id, role='viewer'))
        _, bearer, _ = new_session(db, user, kind='api'); db.commit()
    s.client.headers['X-Workspace-ID'] = other_id
    assert s.client.get(BASE + '/' + s.connection_id).status_code == 404
    assert good(s.client.get(BASE + '/requests/' + body['request_id']))['found'] is False
    assert good(s.client.get(BASE))['connections'] == []
    s.client.headers['X-Workspace-ID'] = s.workspace_id
    with TestClient(s.client.app) as viewer:
        viewer.headers.update({'Authorization': 'Bearer ' + bearer, 'X-Workspace-ID': s.workspace_id})
        assert good(viewer.get(BASE + '/' + s.connection_id))['id'] == s.connection_id
        assert good(viewer.get(BASE + '/requests/' + body['request_id']))['found'] is False
        assert viewer.post(BASE, json={'request_id': nonce(), 'name': '不能写', 'origin': s.origin, 'token': TOKEN}).status_code == 403


@pytest.mark.parametrize('fault', ['physical', 'unknown_action', 'duplicate', 'bad_profile', 'bad_time', 'nonfinite'])
def test_malformed_catalogue_is_rejected_with_no_saved_devices(scene, monkeypatch, fault):
    s = scene; create(s); job_id = discover(s)
    real_normalize = store.normalize_catalogue
    def malformed(value):
        value = copy.deepcopy(value)
        if fault == 'physical': value['devices'][0]['physical_device_connected'] = True
        elif fault == 'unknown_action': value['devices'][0]['profile']['actions'].append('fly_anywhere')
        elif fault == 'duplicate': value['devices'].append(copy.deepcopy(value['devices'][0]))
        elif fault == 'bad_profile': value['devices'][0]['profile']['source'] = 'arbitrary-string'
        elif fault == 'bad_time': value['devices'][0]['sampled_at'] = '2026-01-01T00:00:00'
        elif fault == 'nonfinite': value['devices'][0]['profile']['simulation_assumptions']['bad'] = float('nan')
        return real_normalize(value)
    monkeypatch.setattr(store, 'normalize_catalogue', malformed)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))
    assert result['status'] == 'failed' and result['devices'] == []
    assert result['error_code'] == 'discovery_catalogue_invalid' and result['integrity_error'] is None


def test_missing_independent_completion_audit_invalidates_discovery_and_grant(scene):
    s = scene; create(s); discovery = completed(s); grant(s, discovery)
    with Session(engine()) as db:
        proof = db.scalar(select(m.Audit).where(m.Audit.object_id == discovery['id'], m.Audit.action == store.COMPLETED))
        db.delete(proof); db.commit()
    result = read(s)
    assert result['latest_discovery']['status'] == 'invalid'
    assert result['latest_discovery']['integrity_error'] == 'discovery_integrity_invalid'
    assert not result['grants'][0]['effective'] and result['grants'][0]['integrity_error']


@pytest.mark.parametrize('fault', ['short', 'crlf', 'extra', 'mismatch', 'bad_nonce', 'invalid_json'])
def test_validation_never_echoes_secret_input(scene, fault):
    s = scene; secret = 'private-secret-for-redaction-123456789'
    body = {'request_id': nonce(), 'name': '敏感输入校验', 'origin': s.origin, 'token': secret}
    if fault == 'short': body['token'] = 'pvt'
    elif fault == 'crlf': body['token'] = secret + '\r\nX-Secret: secret'
    elif fault == 'extra': body['secret_ref'] = secret
    elif fault == 'mismatch': body['token'] = {'secret': secret}
    elif fault == 'bad_nonce': body['request_id'] = 'bad-nonce-' + secret
    if fault == 'invalid_json':
        response = s.client.post(BASE, content='{"token":"' + secret + '"', headers={'Content-Type': 'application/json'})
    else:
        response = s.client.post(BASE, json=body)
    assert response.status_code == 422
    assert secret not in response.text and 'pvt' not in response.text and 'input' not in response.text


@pytest.mark.parametrize('scene', ['quote-"-gateway-token-123456789', 'slash-\\-gateway-token-123456789'], indirect=True)
@pytest.mark.parametrize('location', ['value', 'key'])
def test_remote_escaped_token_echo_never_becomes_saved_metadata(scene, monkeypatch, location):
    s = scene; create(s); job_id = discover(s); real = store.normalize_catalogue
    def echoed(value):
        value = copy.deepcopy(value)
        if location == 'value':
            value['devices'][0]['profile']['name'] = s.token
        else:
            value['devices'][0]['profile']['source'][s.token] = 'remote echo'
        return real(value)
    monkeypatch.setattr(store, 'normalize_catalogue', echoed)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))
    assert result['status'] == 'failed' and result['devices'] == []
    assert result['error_code'] == 'discovery_catalogue_invalid'
    with Session(engine()) as db:
        for cls in (Command, m.Job, m.Audit):
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)):
                assert not store.contains_secret(row.data, s.token)


@pytest.mark.parametrize('mutation', ['pause', 'credential'])
def test_queued_job_changed_before_claim_never_sends_http(scene, monkeypatch, mutation):
    s = scene; create(s); job_id = discover(s)
    if mutation == 'pause': change(s, 'status', status='paused')
    else: change(s, 'credential', token='new-credential-before-claim-12345')
    def forbidden(*args, **kwargs): raise AssertionError('stale original must not send')
    monkeypatch.setattr(consumer, 'fetch_catalogue', forbidden)
    assert consumer.consume_once(s.workspace_id, s.connection_id)
    result = good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))
    assert result['status'] == 'failed' and result['error_code'] == 'discovery_connection_changed'


def test_stale_terminal_lease_cannot_write_result_or_audit(scene):
    s = scene; create(s); job_id = discover(s)
    from generic_agents.worker import claim
    claimed = claim(job_id, kinds=(store.KIND,), workspace_id=s.workspace_id)
    assert claimed is not None
    assert not consumer.terminal(s.workspace_id, s.connection_id, job_id, nonce(), error_code='discovery_failed')
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.status == 'running' and job.lease_token == claimed[1]
        assert db.scalar(select(m.Audit.id).where(m.Audit.object_id == job_id, m.Audit.action == store.COMPLETED)) is None
    assert consumer.execute(s.workspace_id, s.connection_id, *claimed)
    assert good(s.client.get(BASE + '/' + s.connection_id + '/discoveries/' + job_id))['status'] == 'succeeded'


def test_unsupported_action_and_foreign_discovery_do_not_create_identity_or_grant(scene):
    s = scene; create(s); discovery = completed(s)
    weather = next(d for d in discovery['devices'] if d['kind'] == 'weather_station')
    body = {'request_id': nonce(), 'expected_version': 1, 'discovery_job_id': discovery['id'],
        'device_external_id': weather['external_id'], 'actions': ['start_mission'], 'reason': '不能越过声明动作'}
    assert s.client.post(BASE + '/' + s.connection_id + '/grant', json=body).status_code == 422
    original_connection = s.connection_id; create(s)
    assert s.client.post(BASE + '/' + s.connection_id + '/grant', json={**body, 'request_id': nonce()}).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(Grant.id).where(Grant.workspace_id == s.workspace_id)) is None
        assert db.scalar(select(m.SensorDevice.id).where(m.SensorDevice.workspace_id == s.workspace_id)) is None
    assert original_connection != s.connection_id


def test_request_nonce_is_global_per_actor_across_operations_and_connections(scene):
    s = scene; original, _ = create(s)
    create(s)
    body = {'request_id': original['request_id'], 'expected_version': 1, 'reason': '不能重用其他动作nonce'}
    assert s.client.post(BASE + '/' + s.connection_id + '/discover', json=body).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(m.Job.id).where(m.Job.workspace_id == s.workspace_id, m.Job.kind == store.KIND)) is None


@pytest.mark.parametrize('operation', ['create', 'credential'])
def test_current_token_misplaced_into_metadata_is_rejected_without_persistence(scene, operation):
    s = scene
    if operation == 'credential':
        create(s)
    with Session(engine()) as db:
        before = {cls.__name__: len(list(db.scalars(select(cls.id).where(cls.workspace_id == s.workspace_id))))
                  for cls in (Connection, Command, m.Audit)}
    if operation == 'create':
        response = s.client.post(BASE, json={'request_id': nonce(), 'name': '误贴' + TOKEN,
            'origin': s.origin, 'token': TOKEN})
    else:
        response = s.client.post(BASE + '/' + s.connection_id + '/credential', json={
            'request_id': nonce(), 'expected_version': 1, 'reason': '误贴' + TOKEN, 'token': TOKEN})
    assert response.status_code == 422 and TOKEN not in response.text
    with Session(engine()) as db:
        after = {cls.__name__: len(list(db.scalars(select(cls.id).where(cls.workspace_id == s.workspace_id))))
                 for cls in (Connection, Command, m.Audit)}
        assert before == after
