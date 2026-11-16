"""Real ASGI HTTP and SQLite protocol checks; no platform authority substitutes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from generic_agents.device_registered_tool_adapter_v269 import (
    MAX_RAW_BYTES, OUTPUT_SCHEMA, PROVIDER_SCOPE, RECEIPT_SCHEMA, TABLE,
    canonical, digest, output_schema, supplier_key)
from generic_agents.device_simulator_v175 import Command
from generic_agents.device_tool_simulator_v269 import create_app


TOKEN = 'v269-test-only-provider-secret-0123456789'


class Clock:
    def __init__(self):
        self.value = 1800000000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def node(tmp_path):
    path, clock = tmp_path / 'node.sqlite', Clock()
    app = create_app(path, TOKEN, clock=clock, auto_consume=False)
    with TestClient(app) as client:
        yield path, clock, app, client


def body(key='registered-tool-plan-v201:sample:0', **changes):
    value = {'device_id': 'sim-weather-01', 'action': 'read_telemetry',
             'expected_revision': None, 'arguments': {},
             'task_id': str(uuid4()), 'request_key': key}
    value.update(changes)
    return value


def headers(key):
    return {'Authorization': 'Bearer ' + TOKEN, 'Idempotency-Key': key,
            'Content-Type': 'application/json'}


def post(client, value, raw=None):
    return client.post('/registered-tools/invoke',
                       content=raw if raw is not None else canonical(value).encode(),
                       headers=headers(value['request_key']))


def get(client, key):
    return client.get('/registered-tools/receipts', headers=headers(key))


def rows(path, table):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM ' + table)]


def test_success_is_original_http_receipt_and_full_bound_hash(node):
    path, _, _, client = node
    value = body()
    response = post(client, value)
    assert response.status_code == 200
    output = response.json()
    Draft202012Validator(output_schema()).validate(output)
    receipt = get(client, value['request_key']).json()
    assert receipt == {'schema': RECEIPT_SCHEMA, 'idempotency_key': value['request_key'],
                       'input_hash': digest(value), 'status': 'succeeded',
                       'terminal': True, 'output': output}
    assert output['schema'] == OUTPUT_SCHEMA
    assert output['request_id'] == value['request_key']
    assert output['supplier_request_id'] == supplier_key(value['request_key'])
    assert output['supplier_receipt']['request_id'] == output['supplier_request_id']
    assert output['supplier_receipt_hash'] == digest(output['supplier_receipt'])
    assert output['supplier_receipt']['result']['time_semantics'] == 'simulator_sample_time'
    assert output['supplier_receipt']['result']['physical_device_connected'] is False
    assert output['simulation'] is True and output['observation_created'] is False
    assert output['field_verified'] is False and output['physical_device_connected'] is False
    saved = rows(path, TABLE)[0]
    assert bytes(saved['exact_input']) == canonical(value).encode()
    command = {name: value[name] for name in ('device_id', 'action', 'expected_revision', 'arguments')}
    assert saved['command_hash'] == digest(command) != receipt['input_hash']
    assert len(rows(path, 'receipts')) == 1
    assert TOKEN not in json.dumps(output) and TOKEN not in json.dumps(
        {name: item for name, item in saved.items() if name != 'exact_input'})


def test_duplicate_concurrent_post_invokes_supplier_once(node, monkeypatch):
    path, _, app, client = node
    adapter = app.state.registered_tool_adapter_v269
    invoke, calls, lock = adapter.ledger.invoke, [], threading.Lock()

    def counted(key, command):
        with lock:
            calls.append(key)
        return invoke(key, command)

    monkeypatch.setattr(adapter.ledger, 'invoke', counted)
    value = body()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: post(client, value), range(4)))
    assert all(response.status_code in {200, 409} for response in responses)
    assert sum(response.status_code == 200 for response in responses) >= 1
    final = get(client, value['request_key']).json()
    assert final['status'] == 'succeeded'
    assert len(calls) == len(rows(path, TABLE)) == len(rows(path, 'receipts')) == 1
    assert post(client, value).json() == final['output']
    assert len(calls) == 1


def test_same_key_changed_body_or_encoding_never_creates_another_command(node):
    path, _, _, client = node
    value = body()
    assert post(client, value).status_code == 200
    for changed in ({**value, 'task_id': str(uuid4())},
                    {**value, 'device_id': 'sim-camera-01'},
                    {**value, 'arguments': {'secret': 'must-not-echo'}}):
        response = post(client, changed)
        assert response.status_code == 409
        assert 'must-not-echo' not in response.text
    assert post(client, value, json.dumps(value).encode()).status_code == 409
    assert len(rows(path, TABLE)) == len(rows(path, 'receipts')) == 1


def test_cold_reopen_retains_original_sample_and_frozen_terminal(node):
    path, clock, _, client = node
    value = body()
    output = post(client, value).json()
    first = get(client, value['request_key'])
    clock.advance(3600)
    restarted = create_app(path, TOKEN, clock=clock, auto_consume=False)
    with TestClient(restarted) as cold:
        assert get(cold, value['request_key']).content == first.content
        assert post(cold, value).json() == output
        assert get(cold, value['request_key']).json()['output'] == output
    assert len(rows(path, TABLE)) == len(rows(path, 'receipts')) == 1


def test_committed_intent_crash_never_reinvokes_even_after_cold_reopen(node, monkeypatch):
    path, clock, app, _ = node
    value = body()
    adapter = app.state.registered_tool_adapter_v269

    class SimulatedProcessExit(BaseException):
        pass

    def crash_before_supplier(_key, _command):
        raise SimulatedProcessExit()

    monkeypatch.setattr(adapter.ledger, 'invoke', crash_before_supplier)
    with pytest.raises(SimulatedProcessExit):
        adapter.submit(canonical(value).encode(), value['request_key'])
    assert len(rows(path, TABLE)) == 1 and rows(path, 'receipts') == []
    restarted = create_app(path, TOKEN, clock=clock, auto_consume=False)
    cold_adapter = restarted.state.registered_tool_adapter_v269

    def forbidden(_key, _command):
        pytest.fail('a retained intent must never invoke again')

    monkeypatch.setattr(cold_adapter.ledger, 'invoke', forbidden)
    with TestClient(restarted) as cold:
        receipt = get(cold, value['request_key']).json()
        assert receipt['status'] == 'unknown' and receipt['terminal'] is False
        assert receipt['input_hash'] == digest(value) and 'output' not in receipt
        assert post(cold, value).status_code == 409
    assert rows(path, 'receipts') == []


def test_lost_supplier_reply_recovers_committed_original_without_new_invoke(node, monkeypatch):
    path, _, app, client = node
    adapter = app.state.registered_tool_adapter_v269
    invoke, calls = adapter.ledger.invoke, []

    def lost_reply(key, command):
        calls.append(key)
        invoke(key, command)
        raise ConnectionError('private transport secret must not escape')

    monkeypatch.setattr(adapter.ledger, 'invoke', lost_reply)
    value = body()
    # Deliberately discard the initial client's reply and recover by original GET.
    post(client, value)
    receipt = get(client, value['request_key'])
    assert receipt.json()['status'] == 'succeeded'
    assert 'private transport secret' not in receipt.text
    assert post(client, value).json() == receipt.json()['output']
    assert len(calls) == len(rows(path, 'receipts')) == 1


def test_pending_is_empty_202_and_full_drone_duration_is_preserved(node):
    path, clock, _, client = node
    value = body(device_id='sim-dock-01', action='start_mission', expected_revision=1,
                 arguments={'duration_seconds': 10, 'speed_m_s': 1, 'mission_name': '明确十秒SIM动作'})
    response = post(client, value)
    assert response.status_code == 202 and response.content == b''
    receipt = get(client, value['request_key']).json()
    assert receipt['status'] == 'pending' and receipt['terminal'] is False and 'output' not in receipt
    clock.advance(3)
    device = client.get('/devices/sim-dock-01', headers=headers('read')).json()
    assert device['state']['phase'] == 'flying'
    saved = rows(path, 'receipts')[0]
    assert json.loads(saved['body'])['arguments']['duration_seconds'] == 10
    clock.advance(7)
    receipt = get(client, value['request_key']).json()
    assert receipt['status'] == 'succeeded' and receipt['terminal'] is True
    assert receipt['output']['supplier_receipt']['result']['artifacts'] == []
    assert receipt['output']['observation_created'] is False


def test_failed_supplier_is_frozen_failure_not_success_or_not_executed(node):
    path, clock, _, client = node
    value = body(device_id='sim-camera-01', action='set_ptz', expected_revision=1,
                 arguments={'pan_degrees': 20, 'tilt_degrees': 10, 'zoom': 2})
    assert post(client, value).status_code == 202
    device = client.get('/devices/sim-camera-01', headers=headers('read')).json()
    fault = {'device_id': 'sim-camera-01', 'expected_revision': device['revision'],
             'fault': 'motor_fault', 'telemetry': {}, 'confirm_safe_idle': False}
    assert client.post('/lab/state', json=fault, headers=headers('lab:fault')).status_code == 200
    response = get(client, value['request_key'])
    receipt = response.json()
    assert receipt['status'] == 'failed' and receipt['terminal'] is True
    assert receipt['output']['supplier_receipt']['status'] == 'failed'
    assert post(client, value).status_code == 409
    clock.advance(100)
    assert get(client, value['request_key']).content == response.content
    with TestClient(create_app(path, TOKEN, clock=clock, auto_consume=False)) as cold:
        assert get(cold, value['request_key']).content == response.content


def test_cancelled_supplier_keeps_raw_cancellation_as_terminal_failure(node):
    path, clock, _, client = node
    value = body(device_id='sim-dock-01', action='start_mission', expected_revision=1,
                 arguments={'duration_seconds': 10, 'speed_m_s': 1, 'mission_name': '明确取消测试'})
    assert post(client, value).status_code == 202
    device = client.get('/devices/sim-dock-01', headers=headers('read')).json()
    command = {'device_id': 'sim-dock-01', 'action': 'cancel_mission',
               'expected_revision': device['revision'], 'arguments': {}}
    assert client.post('/invoke', json=command, headers=headers('recovery:cancel')).status_code == 200
    response = get(client, value['request_key'])
    receipt = response.json()
    assert receipt['status'] == 'failed' and receipt['terminal'] is True
    raw = receipt['output']['supplier_receipt']
    assert raw['status'] == 'cancelled' and raw['result']['followup_request_id'] == 'recovery:cancel'
    assert post(client, value).status_code == 409
    clock.advance(100)
    assert get(client, value['request_key']).content == response.content
    with TestClient(create_app(path, TOKEN, clock=clock, auto_consume=False)) as cold:
        assert get(cold, value['request_key']).content == response.content


def test_missing_journal_is_generic_404_without_fabricated_input_hash(node):
    path, _, _, client = node
    response = get(client, 'not-seen-before')
    assert response.status_code == 404
    assert response.json() == {'detail': '原请求尚无可核对记录，不能证明未执行'}
    assert rows(path, TABLE) == rows(path, 'receipts') == []


def test_unowned_supplier_key_cannot_be_adopted_without_original_journal(node):
    path, _, app, client = node
    value = body()
    adapter = app.state.registered_tool_adapter_v269
    command = Command.model_validate({name: value[name] for name in
                                     ('device_id', 'action', 'expected_revision', 'arguments')})
    adapter.ledger.invoke(supplier_key(value['request_key']), command)
    assert post(client, value).status_code == 409
    assert rows(path, TABLE) == [] and len(rows(path, 'receipts')) == 1


@pytest.mark.parametrize('change', [
    {'task_id': 'not-a-uuid'}, {'task_id': 'A' * 36}, {'extra': 'private-input-value'},
    {'expected_revision': True}, {'arguments': []}, {'arguments': {'extra': 1}},
    {'device_id': 1}, {'action': 'pretend_image_capture'},
])
def test_bad_types_or_unknown_fields_are_generic_and_do_not_persist(node, change):
    path, _, _, client = node
    response = post(client, body(**change))
    assert response.status_code == 422
    assert response.json() == {'detail': '原请求字段、类型或JSON不符合要求'}
    assert 'private-input-value' not in response.text
    assert rows(path, TABLE) == rows(path, 'receipts') == []


@pytest.mark.parametrize('raw', [
    b'{"device_id":"sim-weather-01","device_id":"sim-weather-01"}',
    b'{"arguments":{"nested":{"same":1,"same":2}}}',
    b'{"arguments":{"temperature":NaN}}', b'{"arguments":{"temperature":Infinity}}',
    b'[]', b'{', b'\xff', b'', b' ' * (MAX_RAW_BYTES + 1),
], ids=['duplicate-root', 'duplicate-nested', 'nan', 'infinity',
        'array', 'malformed', 'nonutf8', 'empty', 'oversize'])
def test_malformed_duplicate_nonfinite_and_large_bodies_are_bounded(node, raw):
    path, _, _, client = node
    response = client.post('/registered-tools/invoke', content=raw, headers=headers('invalid:raw'))
    assert response.status_code == 422
    assert rows(path, TABLE) == rows(path, 'receipts') == []


@pytest.mark.parametrize('nested', [False, True], ids=['root', 'nested-arguments'])
def test_duplicate_key_in_otherwise_valid_body_cannot_silently_dispatch(node, nested):
    path, _, _, client = node
    value = body()
    if nested:
        value.update(device_id='sim-camera-01', action='set_ptz', expected_revision=1,
                     arguments={'pan_degrees': 20, 'tilt_degrees': 10, 'zoom': 2})
        original, duplicate = '"pan_degrees":20', '"pan_degrees":20,"pan_degrees":20'
    else:
        original = '"device_id":"sim-weather-01"'
        duplicate = original + ',' + original
    raw = canonical(value).replace(original, duplicate, 1).encode()
    assert post(client, value, raw).status_code == 422
    assert rows(path, TABLE) == rows(path, 'receipts') == []


def test_journal_corruption_does_not_resample_or_expose_saved_request(node):
    path, _, _, client = node
    value = body()
    assert post(client, value).status_code == 200
    with sqlite3.connect(path) as db:
        db.execute('UPDATE ' + TABLE + ' SET canonical_input=?', ('private-corrupt-input',))
    response = get(client, value['request_key'])
    assert response.status_code == 409
    assert 'private-corrupt-input' not in response.text
    assert len(rows(path, 'receipts')) == 1


def test_stream_limit_auth_and_header_binding_precede_dispatch(node):
    path, _, _, client = node
    value = body()
    assert client.post('/registered-tools/invoke', content=canonical(value).encode()).status_code == 401
    assert client.get('/registered-tools/receipts', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    response = client.post('/registered-tools/invoke', content=canonical(value).encode(),
                           headers=headers('different:header'))
    assert response.status_code == 409
    response = client.post('/registered-tools/invoke', headers=headers(value['request_key']),
                           content=iter([b' ' * 32768, b' ' * 32768, b'x']))
    assert response.status_code == 422
    assert rows(path, TABLE) == rows(path, 'receipts') == []


def test_supplier_key_depends_only_on_fixed_provider_scope_and_original_key():
    key = 'registered-tool-plan-v201:known:0'
    expected = 'reg269:' + hashlib.sha256((PROVIDER_SCOPE + '\0' + key).encode()).hexdigest()
    assert supplier_key(key) == expected and len(expected) <= 128
