"""Security properties of the server-owned credential envelope, not UI mocks."""
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import gateway_credentials_v268 as vault
from generic_agents.config import settings


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    key = tmp_path / 'gateway.key'
    vault.create_key(key)
    monkeypatch.setattr(settings(), 'device_gateway_key_file', key)
    return key


def test_same_secret_has_different_envelopes_and_bound_scope(key_file):
    workspace, connection = str(uuid4()), str(uuid4())
    secret = 'server-bearer-secret-test-v268'
    first = vault.seal(workspace, connection, 1, secret)
    second = vault.seal(workspace, connection, 1, secret)
    assert first != second and secret not in first
    assert vault.open_secret(workspace, connection, 1, first) == secret
    for scope in [(str(uuid4()), connection, 1), (workspace, str(uuid4()), 1),
                  (workspace, connection, 2)]:
        with pytest.raises(HTTPException) as error:
            vault.open_secret(*scope, first)
        assert secret not in str(error.value.detail)


def test_tampered_envelope_cannot_decrypt_or_leak(key_file):
    import base64
    ws, conn = str(uuid4()), str(uuid4())
    encrypted = vault.seal(ws, conn, 1, 'another-private-bearer')
    raw = bytearray(base64.urlsafe_b64decode(encrypted[7:]))
    for index in (0, 12, len(raw) - 1):
        changed = raw.copy()
        changed[index] ^= 1
        damaged = 'gac268.' + base64.urlsafe_b64encode(changed).decode()
        with pytest.raises(HTTPException):
            vault.open_secret(ws, conn, 1, damaged)


def test_missing_or_wrong_key_never_reinitializes(key_file):
    ws, conn = str(uuid4()), str(uuid4())
    encrypted = vault.seal(ws, conn, 1, 'preserved-original-credential')
    original = key_file.read_bytes()
    key_file.unlink()
    with pytest.raises(HTTPException):
        vault.open_secret(ws, conn, 1, encrypted)
    assert not key_file.exists()
    key_file.write_bytes(os.urandom(32))
    key_file.chmod(0o600)
    with pytest.raises(HTTPException):
        vault.open_secret(ws, conn, 1, encrypted)
    key_file.write_bytes(original)
    assert vault.open_secret(ws, conn, 1, encrypted) == 'preserved-original-credential'


def test_key_permissions_links_and_length_fail_closed(key_file, monkeypatch):
    ws, conn = str(uuid4()), str(uuid4())
    original = key_file.read_bytes()
    key_file.chmod(0o644)
    with pytest.raises(HTTPException):
        vault.seal(ws, conn, 1, 'sensitive-test-credential')
    key_file.chmod(0o600)
    link = key_file.parent / 'link.key'
    link.symlink_to(key_file)
    monkeypatch.setattr(settings(), 'device_gateway_key_file', link)
    with pytest.raises(HTTPException):
        vault.seal(ws, conn, 1, 'sensitive-test-credential')
    monkeypatch.setattr(settings(), 'device_gateway_key_file', key_file)
    key_file.write_bytes(original[:-1])
    with pytest.raises(HTTPException):
        vault.seal(ws, conn, 1, 'sensitive-test-credential')


def test_request_fingerprint_binds_scope_secret_and_operation(key_file):
    payload = {'workspace_id': str(uuid4()), 'actor_id': str(uuid4()),
        'connection_id': str(uuid4()), 'operation': 'rotate', 'expected_version': 1}
    first = vault.request_fingerprint(payload, 'private-credential-one')
    assert first == vault.request_fingerprint(payload, 'private-credential-one')
    assert first != vault.request_fingerprint(payload, 'private-credential-two')
    assert first != vault.request_fingerprint({**payload, 'operation': 'create'}, 'private-credential-one')
    assert first != vault.request_fingerprint({**payload, 'workspace_id': str(uuid4())}, 'private-credential-one')


def test_nonregular_key_does_not_wait_for_a_fifo_writer(key_file, monkeypatch):
    fifo = key_file.parent / 'not-a-key.fifo'
    os.mkfifo(fifo, 0o600)
    monkeypatch.setattr(settings(), 'device_gateway_key_file', fifo)
    with pytest.raises(HTTPException):
        vault.seal(str(uuid4()), str(uuid4()), 1, 'never-a-real-key')


def test_numeric_exact_origin_only_no_dns_or_development_bypass(monkeypatch):
    monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', 'http://127.0.0.1:54321')
    assert vault.validate_origin('http://127.0.0.1:54321') == 'http://127.0.0.1:54321'
    for origin in ('http://127.0.0.1:54322', 'http://localhost:54321', 'http://127.0.0.1:54321/',
        'http://127.0.0.1:54321?token=x', 'http://127.0.0.1:54321#x',
        'http://user:pass@127.0.0.1:54321', 'http://127.0.0.1:54321/devices',
        'http://127.0.0.2:54321', 'http://[::1]:54321', 'http://169.254.169.254:54321',
        'http://127.0.0.1:65536', 'http://127.0.0.1:054321', 'https://example.com'):
        with pytest.raises(HTTPException):
            vault.validate_origin(origin)
