"""Source-family unit contracts; not runtime or product acceptance evidence.

HTTP is replaced at the client boundary. The authorization-less transport
orchestration tests deliberately make no claim about real Acq/Goal/lease or
DataReception authority. No test in this file starts a device or sends HTTP.
"""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import device_gateway_sources_v270 as sources
from generic_agents import device_gateway_source_http_v270 as transport
from generic_agents import device_gateway_credential_snapshots_v270 as snapshots
from generic_agents import device_gateway_tools_v269 as tools
from generic_agents import gateway_credentials_v268 as credentials
from generic_agents.contracts import digest
from generic_agents.device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from generic_agents.device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot
from generic_agents.models import Acquisition, Audit, Capability
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange


def identity(number):
    return f'00000000-0000-0000-0000-{number:012d}'


@pytest.fixture
def recipe(monkeypatch):
    origin = 'http://127.0.0.1:18770'
    monkeypatch.setattr(credentials, 'settings', lambda: SimpleNamespace(device_gateway_allowed_origins=origin))
    return {'schema': sources.BINDING, 'connection_id': identity(1), 'connection_version': 3,
        'credential_revision': 2, 'origin': origin, 'protocol': sources.store.PROTOCOL,
        'grant_id': identity(2), 'grant_version': 2, 'device_external_id': sources.DEVICE,
        'sensor_device_id': identity(3), 'discovery_job_id': identity(4),
        'catalogue_digest': 'a' * 64, 'grant_actor_id': identity(5),
        'grant_actions': ['read_telemetry'], 'action': 'read_telemetry',
        'extractor_id': identity(6), 'extractor_hash': 'b' * 64,
        'mapping': {'schema': sources.mapping.CONFIG_SCHEMA,
            'scope': {'workspace_id': identity(7), 'environment_id': identity(8),
                'instance_id': identity(9), 'release_id': identity(10),
                'package_id': identity(11), 'package_hash': 'c' * 64, 'entity_type': 'weather_site'},
            'links': {'temperature_c': {'field_key': 'air_temperature', 'unit': '°C'}},
            'supplier_contract': sources.mapping.SUPPLIER, 'time_semantics': sources.mapping.TIME}}


def capability(value, number):
    return Capability(id=identity(number), workspace_id=identity(7), kind=value['kind'],
        namespace=value['namespace'], name=value['name'], release_version=value['version'],
        status='published', data=copy.deepcopy(value), content_hash=digest(value),
        validated_hash=digest(value), version=3)


def issuance(cap, binding, snapshot, family):
    action = sources.ISSUED if family == 'source' else tools.ISSUED
    schema = sources.ISSUED_SCHEMA if family == 'source' else tools.ISSUED_SCHEMA
    return Audit(id=identity(30 if family == 'source' else 31), workspace_id=identity(7),
        actor_id=identity(5), object_id=cap.id, action=action,
        data={'schema': schema, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'actor_id': identity(5), 'binding': copy.deepcopy(binding), 'snapshot_id': snapshot.id,
            'snapshot_cipher_sha256': snapshots.cipher_hash(snapshot)})


class Rows:
    """Small fixed-identity SQL result fixture, without a DB or an IO engine."""
    def __init__(self, rows):
        self.rows = list(rows)

    def _matching(self, statement):
        model = statement.column_descriptions[0]['entity']
        rows = [row for row in self.rows if isinstance(row, model)]
        for name, value in statement.compile().params.items():
            for field in ('workspace_id', 'object_id', 'connection_id', 'credential_revision', 'action', 'id'):
                if name.startswith(field + '_'):
                    if isinstance(value, (list, tuple)):
                        rows = [row for row in rows if getattr(row, field, None) in value]
                    else:
                        rows = [row for row in rows if getattr(row, field, None) == value]
                    break
        return rows

    def scalar(self, statement):
        rows = self._matching(statement)
        return rows[0] if rows else None

    def scalars(self, statement):
        return self._matching(statement)

    def add(self, value):
        self.rows.append(value)

    def flush(self):
        pass


def test_source_recipe_is_separate_closed_weather_family(recipe):
    value = sources.manifest(recipe)
    assert value['kind'] == 'source' and sources.applies(value)
    assert not tools.applies(value)
    assert value['execution'] == {'adapter': 'http', 'managed_source_scope': sources.SCOPE,
        'side_effect': True, 'endpoint': recipe['origin'] + '/acquisitions',
        'receipt_endpoint': recipe['origin'] + '/acquisitions/receipts'}
    assert value['specification']['observation_capability']['actions'] == ['observe']
    assert value['data_policy']['simulation'] is True
    assert value['data_policy']['field_verified'] is False
    assert value['data_policy']['physical_device_connected'] is False
    assert 'secret_ref' not in value['execution'] and 'credential_scope' not in value['execution']
    for changed in ('device', 'action', 'field', 'unit', 'extra'):
        altered = copy.deepcopy(recipe)
        if changed == 'device':
            altered['device_external_id'] = 'sim-dock-01'
        elif changed == 'action':
            altered['action'] = 'get_status'
        elif changed == 'field':
            altered['mapping']['links'] = {'battery_percent': {'field_key': 'battery', 'unit': '%'}}
        elif changed == 'unit':
            altered['mapping']['links']['temperature_c']['unit'] = 'K'
        else:
            altered['secret_ref'] = 'GA_CONNECTOR_SECRET_FORBIDDEN'
        with pytest.raises(HTTPException):
            sources.manifest(altered)


@pytest.mark.parametrize('first_family', ['source', 'tool'])
def test_snapshot_can_be_shared_in_either_issuance_order_without_fake_tool(recipe, first_family):
    p = SimpleNamespace(workspace_id=identity(7), user_id=identity(5))
    connection = Connection(id=identity(1), workspace_id=p.workspace_id, version=3,
        credential_revision=2, token_cipher='ciphertext-old')
    snapshot = Snapshot(id=identity(20), workspace_id=p.workspace_id,
        connection_id=connection.id, credential_revision=2, token_cipher=connection.token_cipher)
    source = capability(sources.manifest(recipe), 21)
    tool_binding = sources._gateway_binding(recipe)
    tool = capability(tools._manifest(tool_binding), 22)
    source_proof = issuance(source, recipe, snapshot, 'source')
    tool_proof = issuance(tool, tool_binding, snapshot, 'tool')
    first_cap, first_proof = (source, source_proof) if first_family == 'source' else (tool, tool_proof)
    db = Rows([connection, snapshot, first_cap, first_proof])
    assert snapshots.ensure_snapshot(db, p, connection) is snapshot
    # A second family uses its own independent issuance, not a forged first-family audit.
    db.rows.extend([tool, tool_proof] if first_family == 'source' else [source, source_proof])
    assert snapshots.ensure_snapshot(db, p, connection) is snapshot
    assert source_proof.action != tool_proof.action
    db.rows.remove(snapshot)
    with pytest.raises(HTTPException):
        snapshots.ensure_snapshot(db, p, connection)
    assert not any(isinstance(row, Snapshot) for row in db.rows)


@pytest.mark.parametrize('damage', ['ciphertext', 'capability_hash', 'snapshot_id', 'duplicate_audit'])
def test_source_issued_snapshot_requires_the_original_independent_proof(recipe, damage):
    p = SimpleNamespace(workspace_id=identity(7), user_id=identity(5))
    snapshot = Snapshot(id=identity(20), workspace_id=p.workspace_id,
        connection_id=identity(1), credential_revision=2, token_cipher='ciphertext-old')
    cap = capability(sources.manifest(recipe), 21)
    proof = issuance(cap, recipe, snapshot, 'source')
    db = Rows([snapshot, cap, proof])
    if damage == 'ciphertext':
        snapshot.token_cipher = 'changed-ciphertext'
    elif damage == 'capability_hash':
        proof.data = {**proof.data, 'capability_hash': 'd' * 64}
    elif damage == 'snapshot_id':
        proof.data = {**proof.data, 'snapshot_id': identity(99)}
    else:
        db.rows.append(issuance(cap, recipe, snapshot, 'source'))
    with pytest.raises(HTTPException):
        sources.issued_source(db, p, cap, recipe)


@pytest.mark.parametrize('failure', [None, 'preflight', 'decrypt', 'callback', 'send'])
def test_post_preserves_exact_wire_and_never_marks_sent_before_preflight(recipe, monkeypatch, failure):
    cap = capability(sources.manifest(recipe), 21)
    body = '{ "request_key": "original-001", "note": "原文" }'
    import hashlib
    wire = {'body': body, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
        'request': {'request_key': 'original-001'}}
    context = {'capability': cap, 'snapshot': SimpleNamespace(id=identity(20)),
        'exchange': SimpleNamespace(data={'freeze': {'wire_frozen': wire}}),
        'endpoint': recipe['origin'] + '/acquisitions', 'key': 'original-001'}
    events = []
    def authorize(*args, operation, stage='preflight'):
        events.append(stage)
        if failure == stage:
            raise HTTPException(409, 'blocked')
        return context
    def secret(_):
        if failure == 'decrypt':
            raise HTTPException(409, 'blocked')
        return 'opaque-private-bearer'
    def before():
        events.append('callback')
        if failure == 'callback':
            raise HTTPException(409, 'blocked')
    def send(method, endpoint, token, key, *, wire, before_capture):
        events.append('http')
        assert method == 'POST' and endpoint == context['endpoint'] and key == 'original-001'
        assert wire == body.encode('utf-8')
        before_capture()
        return {'accepted': True}
    monkeypatch.setattr(transport, 'authorize_acquisition', authorize)
    monkeypatch.setattr(transport, '_secret', secret)
    monkeypatch.setattr(transport.leases, 'checkpoint', lambda db: events.append('checkpoint'))
    monkeypatch.setattr(transport, '_exchange', send)
    monkeypatch.setattr(transport, '_accepted', lambda value, _: value)
    call = lambda: transport.post_acquisition(None, None, cap, None, context['exchange'],
        before_authorize=before, on_send=lambda: events.append('on_send'))
    if failure:
        with pytest.raises(HTTPException):
            call()
        assert 'on_send' not in events and 'http' not in events
    else:
        assert call() == {'accepted': True}
        assert events == ['preflight', 'callback', 'send', 'checkpoint', 'on_send', 'http', 'receive']


@pytest.mark.parametrize('payload', [b'{"ok":true}', b'{"leak":"opaque-private-bearer"}',
    b'{"\\u006fpaque-private-bearer":1}', b'{"x":1,"x":2}', b'{"x":NaN}'])
def test_original_get_uses_only_exact_endpoint_key_and_strict_private_response(payload, monkeypatch):
    calls = []; captured = []
    class Response:
        status_code = 200
        headers = {'content-type': 'application/json', 'content-encoding': 'identity'}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def iter_raw(self):
            yield payload
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def stream(self, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return Response()
    monkeypatch.setattr(transport.httpx, 'Client', Client)
    endpoint = 'http://127.0.0.1:18770/acquisitions/receipts'
    call = lambda: transport._exchange('GET', endpoint, 'opaque-private-bearer', 'original:001',
        before_capture=lambda: captured.append(True))
    if payload == b'{"ok":true}':
        assert call() == {'ok': True} and captured == [True]
    else:
        with pytest.raises(HTTPException):
            call()
        assert captured == []
    method, actual_endpoint, request = calls[0]
    assert method == 'GET' and actual_endpoint == endpoint
    assert request['params'] == {'request_key': 'original:001'} and request['content'] is None
    assert request['headers']['Idempotency-Key'] == 'original:001'


def test_claimed_lease_dictionary_is_not_a_real_transaction_fence(recipe):
    source = capability(sources.manifest(recipe), 21)
    acq = Acquisition(id=identity(40), workspace_id=identity(7))
    exchange = Exchange(id=identity(41), workspace_id=identity(7))
    db = SimpleNamespace(info={'worker_lease': {'job_id': identity(42), 'lease_token': 'claimed'}})
    with pytest.raises(HTTPException):
        transport.authorize_acquisition(db, SimpleNamespace(workspace_id=identity(7)),
            source, acq, exchange, operation='post')


def test_manual_registration_is_rejected_and_issuance_cannot_be_erased_by_markers(recipe):
    p = SimpleNamespace(workspace_id=identity(7), user_id=identity(5))
    cap = capability(sources.manifest(recipe), 21)
    with pytest.raises(HTTPException):
        sources.guard_registration(SimpleNamespace(info={}), p, cap.data)
    proof = Audit(id=identity(30), workspace_id=p.workspace_id, actor_id=p.user_id,
        object_id=cap.id, action=sources.ISSUED, data={})
    cap.namespace = 'ordinary'
    cap.data = {'kind': 'source', 'namespace': 'ordinary', 'specification': {}, 'execution': {}}
    assert not sources.applies(cap.data)
    assert sources.managed(Rows([cap, proof]), p, cap)


def test_invalid_remote_schema_details_do_not_escape(monkeypatch):
    rejected = 'untrusted remote body text'
    monkeypatch.setattr(transport, 'validate', lambda *args: (_ for _ in ()).throw(
        HTTPException(422, [{'message': rejected}])))
    context = {'capability': SimpleNamespace(data={'output_schema': {}})}
    with pytest.raises(HTTPException) as caught:
        transport._accepted({'untrusted': rejected}, context)
    assert caught.value.status_code == 409 and rejected not in str(caught.value.detail)


@pytest.mark.parametrize('confirmation', [1, 'true', False, None])
def test_source_simulation_consent_requires_actual_json_true(confirmation):
    from pydantic import ValidationError
    from generic_agents.device_gateway_sources_api_v270 import RegisterSource
    body = {'request_id': identity(1), 'expected_version': 1, 'reason': '明确模拟范围',
        'grant_id': identity(2), 'grant_expected_version': 1, 'environment_id': identity(3),
        'entity_type': 'weather_station', 'links': {'temperature_c': {'field_key': 'temperature', 'unit': '°C'}},
        'extractor_id': identity(4), 'extractor_hash': 'a' * 64, 'confirm_simulation': confirmation}
    with pytest.raises(ValidationError):
        RegisterSource.model_validate(body)
    assert RegisterSource.model_validate({**body, 'confirm_simulation': True}).confirm_simulation is True
