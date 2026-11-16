"""Root-run PostgreSQL/API tests for issued device capability bindings.

The V268 fixture uses the actual loopback simulator and durable discovery.
Invocation fixtures below isolate the credential/binding gate; they do not
claim to prove an adopted Brain plan, parent authority, lease or target flow.
"""
import copy
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents import gateway_credentials_v268 as crypto
from generic_agents import device_gateway_tools_v269 as subject
from generic_agents.contracts import digest
from generic_agents.security import Principal
from generic_agents.device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot
from generic_agents.device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from tests.test_device_gateway_v268 import (
    scene, create, completed, grant, read, change, good, nonce, BASE, TOKEN)


@pytest.fixture
def managed(scene):
    s = scene
    create(s)
    discovery = completed(s)
    _, granted = grant(s, discovery)
    s.grant = granted['grant']
    s.tool_body = {'request_id': nonce(), 'expected_version': read(s)['version'],
        'grant_id': s.grant['id'], 'grant_expected_version': s.grant['version'],
        'action': 'read_telemetry', 'reason': '明确登记此模拟设备的单项原号采样工具'}
    s.tool_receipt = good(s.client.post(BASE + '/' + s.connection_id + '/tools', json=s.tool_body))
    s.capability_id = s.tool_receipt['capability']['id']
    s.p = Principal(s.actor_id, s.workspace_id, 'admin', 'explicit-v269-binding-test')
    return s


def load(db, s):
    return db.get(m.Capability, s.capability_id)


def invocation(db, s):
    """Actual saved child row for the isolated historical credential gate."""
    cap = load(db, s)
    b = cap.data['specification']['device_gateway_binding']
    key = 'v269-original:' + uuid4().hex
    inputs = {'device_id': b['device_external_id'], 'action': b['action'],
        'expected_revision': None, 'arguments': {}, 'task_id': str(uuid4()), 'request_key': key}
    approval = {'schema': 'workflow-registered-tool-child-approval-v201',
        'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'input_hash': digest(inputs), 'request_key_hash': digest(key), 'task_id': inputs['task_id']}
    row = m.Invocation(workspace_id=s.workspace_id, capability_id=cap.id,
        idempotency_key=key, status='unknown', data={'capability_hash': cap.content_hash,
            'capability_snapshot': copy.deepcopy(cap.data), 'inputs': inputs,
            'input_hash': digest(inputs), 'role': 'business', 'approval': approval})
    db.add(row); db.flush()
    return row, inputs, key


def test_normal_registration_validates_publishes_and_pins_private_snapshot(managed):
    s = managed
    assert s.tool_receipt['capability']['status'] == 'published'
    with Session(engine()) as db:
        cap = load(db, s)
        assert cap.content_hash == cap.validated_hash == digest(cap.data)
        assert cap.data['execution']['credential_scope'] == subject.SCOPE
        assert 'secret_ref' not in cap.data['execution'] and 'headers' not in cap.data['execution']
        assert cap.data['execution']['endpoint'] == s.origin + '/registered-tools/invoke'
        assert cap.data['execution']['receipt_endpoint'] == s.origin + '/registered-tools/receipts'
        assert cap.data['execution']['side_effect'] is True
        assert cap.data['budget'] == {'max_attempts': 1, 'max_concurrency': 1}
        context = subject.validate_tool(db, s.p, cap)
        snapshot = context['snapshot']
        assert snapshot.credential_revision == 1 and snapshot.connection_id == s.connection_id
        assert snapshot.token_cipher == db.get(Connection, s.connection_id).token_cipher
        assert TOKEN not in snapshot.token_cipher
        proof = db.scalar(select(m.Audit).where(m.Audit.object_id == cap.id,
            m.Audit.workspace_id == s.workspace_id, m.Audit.action == subject.ISSUED))
        assert proof.data['capability_id'] == cap.id and proof.data['capability_hash'] == cap.content_hash
        assert proof.data['snapshot_id'] == snapshot.id
        assert proof.data['binding'] == context['binding']
        assert db.scalar(select(func.count()).select_from(m.Audit).where(
            m.Audit.object_id == cap.id, m.Audit.action == 'capability.publish')) == 1
        for cls in (m.Capability, m.Audit):
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)):
                assert TOKEN not in json.dumps(row.data)


def test_nonce_replay_conflict_and_new_nonce_reuses_same_actual_capability(managed):
    s = managed; url = BASE + '/' + s.connection_id + '/tools'
    assert good(s.client.post(url, json=s.tool_body)) == s.tool_receipt
    assert s.client.post(url, json={**s.tool_body, 'action': 'get_status'}).status_code == 409
    another = good(s.client.post(url, json={**s.tool_body, 'request_id': nonce()}))
    assert another['capability']['id'] == s.capability_id
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Snapshot).where(
            Snapshot.workspace_id == s.workspace_id)) == 1


def test_user_cannot_submit_managed_manifest_or_override_template(managed):
    s = managed
    with Session(engine()) as db:
        manifest = copy.deepcopy(load(db, s).data)
    for fragment in ('none', 'specification', 'credential_scope', 'device_gateway_connection_id'):
        copied = copy.deepcopy(manifest)
        if fragment == 'specification':
            copied['specification'] = {}
        elif fragment != 'none':
            copied['execution'].pop(fragment)
        assert s.client.post('/api/capabilities', json=copied).status_code == 422
    for field, value in (('endpoint', 'http://127.0.0.1:9/lab/state'),
                         ('input_schema', {'type': 'object'}), ('allowed_agent_roles', ['data']),
                         ('action', 'set_ptz')):
        assert s.client.post(BASE + '/' + s.connection_id + '/tools',
            json={**s.tool_body, 'request_id': nonce(), field: value}).status_code == 422


def test_metadata_reads_do_not_read_keys_decrypt_or_expose_cipher(managed, monkeypatch):
    s = managed
    def forbidden(*args, **kwargs):
        raise AssertionError('metadata must not read keys or decrypt')
    monkeypatch.setattr(crypto, '_keys', forbidden)
    monkeypatch.setattr(crypto, 'open_secret', forbidden)
    response = good(s.client.get(BASE + '/' + s.connection_id + '/tools'))
    assert len(response['tools']) == 1 and response['tools'][0]['integrity_valid']
    assert response['tools'][0]['configuration_current']
    assert response['tools'][0]['availability_reason'] == 'current'
    encoded = json.dumps(response)
    assert TOKEN not in encoded and 'token_cipher' not in encoded and 'snapshot' not in encoded
    with Session(engine()) as db:
        cap = load(db, s)
        subject.validate_tool(db, s.p, cap)


@pytest.mark.parametrize('mutation', ['action', 'endpoint', 'capability_hash', 'issued_audit', 'snapshot'])
def test_binding_tampering_fails_before_authorization(managed, mutation):
    s = managed
    with Session(engine()) as db:
        cap = load(db, s)
        if mutation in {'action', 'endpoint'}:
            value = copy.deepcopy(cap.data)
            if mutation == 'action':
                value['specification']['device_gateway_binding']['action'] = 'get_status'
            else:
                value['execution']['receipt_endpoint'] = s.origin + '/devices'
            cap.data = value; cap.content_hash = cap.validated_hash = digest(value)
        elif mutation == 'capability_hash':
            cap.content_hash = '0' * 64
        elif mutation == 'issued_audit':
            proof = db.scalar(select(m.Audit).where(m.Audit.object_id == cap.id,
                m.Audit.action == subject.ISSUED))
            db.delete(proof)
        else:
            snapshot = db.scalar(select(Snapshot).where(Snapshot.workspace_id == s.workspace_id))
            db.execute(update(Snapshot).where(Snapshot.id == snapshot.id)
                .values(token_cipher='corrupt-ciphertext').execution_options(synchronize_session=False))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as failed:
            subject.validate_tool(db, s.p, load(db, s))
        assert failed.value.status_code == 409


def test_copy_to_another_actual_capability_does_not_inherit_issued_authority(managed):
    s = managed
    with Session(engine()) as db:
        original = load(db, s)
        copied = m.Capability(workspace_id=s.workspace_id, namespace='copy_v269', name='copy',
            release_version='1.0.0', kind='tool', status='published',
            data=copy.deepcopy(original.data), content_hash=original.content_hash,
            validated_hash=original.content_hash)
        db.add(copied); db.flush()
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, copied)


def test_cross_workspace_and_changed_input_cannot_borrow_binding(managed):
    s = managed
    with Session(engine()) as db:
        cap = load(db, s)
        other = m.Workspace(name='其他隔离工作区'); db.add(other); db.flush()
        db.add(m.Membership(user_id=s.actor_id, workspace_id=other.id, role='admin')); db.flush()
        with pytest.raises(HTTPException):
            subject.validate_tool(db, Principal(s.actor_id, other.id, 'admin', 'explicit-test'), cap)
        row, inputs, key = invocation(db, s)
        for change_set in ({'action': 'get_status'}, {'device_id': 'another-device'},
                           {'request_key': 'different-key'}, {'task_id': str(uuid4())}):
            with pytest.raises(HTTPException):
                subject.validate_tool(db, s.p, cap, 'receipt', {**inputs, **change_set}, key, row)


def test_pause_and_rotation_block_dispatch_but_keep_only_original_receipt_snapshot(managed):
    s = managed
    with Session(engine()) as db:
        row, inputs, key = invocation(db, s); invocation_id = row.id
        before = subject.validate_tool(db, s.p, load(db, s), 'dispatch', inputs, key, row)
        snapshot_id = before['snapshot'].id; old_cipher = before['snapshot'].token_cipher
        db.commit()
    change(s, 'status', status='paused')
    inventory = good(s.client.get(BASE + '/' + s.connection_id + '/tools'))['tools'][0]
    assert inventory['integrity_valid'] and not inventory['configuration_current']
    assert inventory['availability_reason'] == 'connection_paused'
    change(s, 'credential', token='rotated-credential-' + 'b' * 32)
    with Session(engine()) as db:
        row = db.get(m.Invocation, invocation_id); cap = load(db, s)
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, cap, 'dispatch', inputs, key, row)
        history = subject.validate_tool(db, s.p, cap, 'receipt', inputs, key, row)
        assert history['snapshot'].id == snapshot_id
        assert history['snapshot'].token_cipher == old_cipher
        assert history['snapshot'].credential_revision == 1
        assert history['connection'].credential_revision == 2
        assert history['snapshot'].token_cipher != history['connection'].token_cipher
        assert subject.validate_tool(db, s.p, cap, 'receipt', key=key, invocation=row)['snapshot'].id == snapshot_id
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, cap, 'receipt', inputs, key, None)
    change(s, 'status', status='enabled')
    inventory = good(s.client.get(BASE + '/' + s.connection_id + '/tools'))['tools'][0]
    assert inventory['integrity_valid'] and not inventory['configuration_current']
    assert inventory['availability_reason'] == 'authorization_changed'


def test_replaced_grant_blocks_new_dispatch_but_original_receipt_keeps_historical_grant(managed):
    s = managed
    with Session(engine()) as db:
        row, inputs, key = invocation(db, s); invocation_id = row.id
        db.commit()
    good(s.client.post(BASE + '/' + s.connection_id + '/grant', json={
        'request_id': nonce(), 'expected_version': read(s)['version'],
        'discovery_job_id': s.grant['discovery_job_id'],
        'device_external_id': s.grant['device_external_id'], 'actions': ['get_status'],
        'reason': '明确替换设备授权，停止新遥测请求'}))
    with Session(engine()) as db:
        row = db.get(m.Invocation, invocation_id); cap = load(db, s)
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, cap, 'dispatch', inputs, key, row)
        assert subject.validate_tool(db, s.p, cap, 'receipt', key=key, invocation=row)['binding']['grant_version'] == 1


def test_revoked_capability_retains_only_historical_published_receipt_authority(managed):
    s = managed
    with Session(engine()) as db:
        row, inputs, key = invocation(db, s)
        cap = load(db, s); cap.status = 'revoked'; cap.validated_hash = None
        db.flush()
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, cap, 'dispatch', inputs, key, row)
        assert subject.validate_tool(db, s.p, cap, 'receipt', key=key, invocation=row)['capability'].id == cap.id


def test_missing_snapshot_never_uses_current_cipher_or_creates_replacement(managed):
    s = managed
    with Session(engine()) as db:
        row, inputs, key = invocation(db, s)
        snapshot = db.scalar(select(Snapshot).where(Snapshot.workspace_id == s.workspace_id))
        db.delete(snapshot); db.flush()
        with pytest.raises(HTTPException):
            subject.validate_tool(db, s.p, load(db, s), 'receipt', inputs, key, row)
        assert db.scalar(select(func.count()).select_from(Snapshot).where(
            Snapshot.workspace_id == s.workspace_id)) == 0
