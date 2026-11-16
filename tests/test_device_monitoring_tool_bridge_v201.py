"""Real isolated V200/V175 SQLite receipts behind the registered-tool bridge.

The transport-loss injections do not replace scheduling, device state changes,
inventory checks or frozen results. Root runs these tests; authors do not.
"""
import copy
import json
import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
import pytest

from generic_agents import device_monitoring_simulator_v200 as monitoring
from generic_agents import device_monitoring_tool_bridge_v201 as tool
from generic_agents.contracts import digest
from generic_agents.device_simulator_v175 import LabChange, _request_hash
from tests.test_device_monitoring_simulator_v200 import (
    CAMERA, TOKEN, Clock, actions, assert_sim, install_body, raw, rows, sha,
)


KEY = 'tool-install-original-v201'


def scene(tmp_path):
    path = tmp_path / 'tool-monitor.sqlite3'
    clock = Clock()
    ledger = monitoring.MonitoringLedger(path, clock)
    bridge = tool.ToolBridge(ledger)
    body = install_body(ledger, clock, camera=True)
    return path, clock, ledger, bridge, body


def envelope(value, body, status, *, key=KEY):
    Draft202012Validator(tool.RECEIPT_SCHEMA).validate(value)
    expected = {'schema', 'idempotency_key', 'input_hash', 'status', 'terminal'}
    if status == 'succeeded': expected.add('output')
    if status in {'unknown', 'failed'}: expected.add('error')
    assert set(value) == expected
    assert value['schema'] == 'registered-tool-receipt-v201'
    assert value['idempotency_key'] == key
    assert value['input_hash'] == _request_hash(body) == digest(body)
    assert value['status'] == status
    assert value['terminal'] is (status in {'succeeded', 'failed'})
    if 'error' in value:
        assert isinstance(value['error'], str) and value['error'].strip()
    return value


def forbid_writes(monkeypatch, ledger):
    def forbid(*_args, **_kwargs):
        pytest.fail('original GET must not install, consume or invoke a device')
    monkeypatch.setattr(ledger, 'install', forbid)
    monkeypatch.setattr(ledger, 'consume_due', forbid)
    monkeypatch.setattr(ledger.devices, 'invoke', forbid)


def complete_first(clock, ledger):
    first = ledger.consume_due()['plans'][0]
    selected = [tick for tick in first['ticks']
        if tick['configuration_version'] == first['configuration_version']]
    assert len(selected) == 1 and selected[0]['status'] == 'accepted'
    clock.advance(2)
    current = ledger.consume_due()['plans'][0]
    selected = [tick for tick in current['ticks']
        if tick['configuration_version'] == current['configuration_version']]
    assert current['first_action_verified'] is True
    assert len(selected) == 1 and selected[0]['status'] == 'succeeded'
    return current


def test_install_and_read_are_pending_until_actual_tick_success(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    schema = tool.input_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(body)
    assert set(schema['required']) == set(body) and schema['additionalProperties'] is False
    pending = envelope(bridge.install(KEY, raw(body)), body, 'pending')
    with sqlite3.connect(path) as db:
        saved_raw, saved_record, saved_hash, result_hash = db.execute(
            'SELECT raw,record,record_hash,result_hash FROM monitoring_tool_commands_v201 WHERE request_key=?',
            (KEY,)).fetchone()
    record = json.loads(saved_record)
    assert saved_raw.encode('utf-8') == raw(body)
    assert record['raw_sha256'] == sha(raw(body)) and record['input_hash'] == digest(body)
    assert saved_hash == digest(record) and result_hash is None
    assert rows(path, 'receipts') == rows(path, 'monitoring_ticks_v200') == []
    with monkeypatch.context() as guard:
        forbid_writes(guard, ledger)
        assert bridge.receipt(KEY) == pending
    current = complete_first(clock, ledger)
    with monkeypatch.context() as guard:
        forbid_writes(guard, ledger)
        result = envelope(bridge.receipt(KEY), body, 'succeeded')
    output = result['output']
    assert set(output) == {'schema', 'simulation', 'origin_kind', 'field_verified',
        'physical_device_connected', 'flight_path_verified', 'image_generation',
        'hardware_authorized', 'install_receipt', 'configuration',
        'first_successful_tick', 'device_revision', 'first_effect_completed'}
    assert output['schema'] == 'device-monitoring-tool-output-v201'
    assert_sim(output)
    assert output['configuration'] == body['configuration']
    assert output['install_receipt'] == ledger.original(KEY, sha(raw(body)))
    assert output['install_receipt']['status'] == 'installed'
    assert output['install_receipt']['first_action_verified'] is False
    assert output['first_successful_tick'] == current['ticks'][0]
    assert output['device_revision'] == current['ticks'][0]['receipt']['result']['device_revision']
    assert output['first_effect_completed'] is True
    assert len(actions(path, 'set_ptz')) == 1


def actual_successful_output(tmp_path):
    _, clock, ledger, bridge, body = scene(tmp_path)
    bridge.install(KEY, raw(body))
    complete_first(clock, ledger)
    return envelope(bridge.receipt(KEY), body, 'succeeded')['output']


def test_public_output_schema_validates_real_successful_device_effect(tmp_path):
    output = actual_successful_output(tmp_path)
    schema = tool.output_schema()
    Draft202012Validator.check_schema(schema)
    assert set(schema['required']) == set(output) and schema['additionalProperties'] is False
    Draft202012Validator(schema).validate(output)


@pytest.mark.parametrize('mutation', ['typedfalseflag', 'missingreceipt', 'arbitraryextra', 'unverifiedfirsteffect'])
def test_public_output_schema_rejects_mutated_actual_success(tmp_path, mutation):
    output = copy.deepcopy(actual_successful_output(tmp_path))
    if mutation == 'typedfalseflag': output['field_verified'] = 0
    if mutation == 'missingreceipt': del output['first_successful_tick']['receipt']
    if mutation == 'arbitraryextra': output['first_successful_tick']['receipt']['result']['arbitrary_extra'] = True
    if mutation == 'unverifiedfirsteffect': output['first_effect_completed'] = False
    with pytest.raises(ValidationError): Draft202012Validator(tool.output_schema()).validate(output)


def test_first_success_is_frozen_across_restart_and_later_device_change(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    bridge.install(KEY, raw(body))
    complete_first(clock, ledger)
    original = envelope(bridge.receipt(KEY), body, 'succeeded')
    # A separately approved V200 configuration can move the same SIM later.
    other = install_body(ledger, clock, camera=True, version=1)
    other['configuration']['pan_degrees'] = -20
    ledger.install('later-v200-install', raw(other))
    complete_first(clock, ledger)
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    forbid_writes(monkeypatch, cold)
    assert recovered.receipt(KEY) == original
    assert len(actions(path, 'set_ptz')) == 2


@pytest.mark.parametrize('change', ['float_alias', 'zero_sign', 'whitespace'])
def test_same_key_cannot_replace_original_typed_utf8_body(tmp_path, change):
    path, _, _, bridge, body = scene(tmp_path)
    body['configuration'].update(pan_degrees=-0.0, tilt_degrees=1)
    original_raw = raw(body)
    first = bridge.install(KEY, original_raw)
    changed = copy.deepcopy(body)
    if change == 'float_alias': changed['configuration']['tilt_degrees'] = 1.0
    if change == 'zero_sign': changed['configuration']['pan_degrees'] = 0.0
    replacement = b' ' + original_raw if change == 'whitespace' else raw(changed)
    with pytest.raises(HTTPException) as error: bridge.install(KEY, replacement)
    assert error.value.status_code == 409
    assert bridge.receipt(KEY) == first
    assert rows(path, 'receipts') == []
    assert len(rows(path, 'monitoring_commands_v200')) == 1


def test_prepared_process_loss_with_missing_original_never_reinstalls(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    def crash(*_args, **_kwargs): raise SystemExit('loss after bridge prepare, before V200 install')
    with monkeypatch.context() as guard:
        guard.setattr(ledger, 'install', crash)
        with pytest.raises(SystemExit): bridge.install(KEY, raw(body))
    assert rows(path, 'monitoring_commands_v200') == []
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    forbid_writes(monkeypatch, cold)
    unknown = envelope(recovered.receipt(KEY), body, 'unknown')
    assert recovered.receipt(KEY) == unknown
    assert recovered.install(KEY, raw(body)) == unknown
    assert rows(path, 'receipts') == rows(path, 'monitoring_commands_v200') == []


def test_saved_v200_install_with_lost_bridge_reply_recovers_read_only(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    original_install = ledger.install
    def lose_reply(*args, **kwargs):
        original_install(*args, **kwargs)
        raise SystemExit('loss after actual V200 install commit')
    with monkeypatch.context() as guard:
        guard.setattr(ledger, 'install', lose_reply)
        with pytest.raises(SystemExit): bridge.install(KEY, raw(body))
    assert len(rows(path, 'monitoring_commands_v200')) == 1
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    with monkeypatch.context() as guard:
        forbid_writes(guard, cold)
        envelope(recovered.receipt(KEY), body, 'pending')
    complete_first(clock, cold)
    forbid_writes(monkeypatch, cold)
    envelope(recovered.receipt(KEY), body, 'succeeded')
    assert len(actions(path, 'set_ptz')) == 1


def test_bad_raw_is_rejected_without_original_monitor_install(tmp_path):
    path, _, _, bridge, _ = scene(tmp_path)
    for bad in [b'[]', b'{"x":NaN}', b'{"schema":"x","schema":"y"}']:
        with pytest.raises(HTTPException) as error: bridge.install(KEY, bad)
        assert error.value.status_code == 422
    assert rows(path, 'monitoring_commands_v200') == rows(path, 'receipts') == []


def test_wrong_family_cannot_use_registered_install_tool(tmp_path):
    path, _, _, bridge, _ = scene(tmp_path)
    body = {'schema': monitoring.STOP, 'device_id': CAMERA, 'expected_version': 0, 'reason': 'wrong tool family'}
    with pytest.raises(HTTPException) as error: bridge.install(KEY, raw(body))
    assert error.value.status_code == 422
    assert rows(path, 'monitoring_commands_v200') == rows(path, 'receipts') == []


def test_actual_device_rejection_is_failed_terminal_without_success(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    bridge.install(KEY, raw(body))
    ledger.devices.lab_change('actual-camera-fault', LabChange(device_id=CAMERA,
        expected_revision=ledger.devices.device(CAMERA)['revision'], fault='motor_fault'))
    current = ledger.consume_due()['plans'][0]
    assert current['ticks'][0]['status'] == 'blocked'
    assert current['first_action_verified'] is False
    failed = envelope(bridge.receipt(KEY), body, 'failed')
    assert failed['error'] == 'monitoring_first_effect_failed'
    assert not actions(path, 'set_ptz')
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    forbid_writes(monkeypatch, cold)
    assert recovered.receipt(KEY) == failed
    assert recovered.install(KEY, raw(body)) == failed


@pytest.mark.parametrize('corruption', ['missing_result', 'missing_marker', 'resigned_result'])
def test_retained_result_inventory_cannot_be_deleted_or_resigned(tmp_path, monkeypatch, corruption):
    path, clock, ledger, bridge, body = scene(tmp_path)
    bridge.install(KEY, raw(body)); complete_first(clock, ledger)
    value = envelope(bridge.receipt(KEY), body, 'succeeded')
    with sqlite3.connect(path) as db:
        if corruption == 'missing_result':
            db.execute('DELETE FROM monitoring_tool_results_v201 WHERE request_key=?', (KEY,))
        elif corruption == 'missing_marker':
            db.execute('UPDATE monitoring_tool_commands_v201 SET result_hash=NULL WHERE request_key=?', (KEY,))
        else:
            altered = copy.deepcopy(value)
            altered['output']['device_revision'] += 1
            hashed = digest(altered)
            db.execute('UPDATE monitoring_tool_results_v201 SET result=?,result_hash=? WHERE request_key=?',
                (raw(altered).decode('utf-8'), hashed, KEY))
            db.execute('UPDATE monitoring_tool_commands_v201 SET result_hash=? WHERE request_key=?', (hashed, KEY))
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    forbid_writes(monkeypatch, cold)
    with pytest.raises(HTTPException) as error: recovered.receipt(KEY)
    assert error.value.status_code == 409
    assert len(actions(path, 'set_ptz')) == 1


def test_original_v200_receipt_tampering_blocks_frozen_bridge_result(tmp_path, monkeypatch):
    path, clock, ledger, bridge, body = scene(tmp_path)
    bridge.install(KEY, raw(body)); complete_first(clock, ledger)
    envelope(bridge.receipt(KEY), body, 'succeeded')
    with sqlite3.connect(path) as db:
        encoded = db.execute('SELECT result FROM monitoring_commands_v200 WHERE request_key=?', (KEY,)).fetchone()[0]
        altered = json.loads(encoded)
        altered['first_action_verified'] = True
        db.execute('UPDATE monitoring_commands_v200 SET result=?,result_hash=? WHERE request_key=?',
            (raw(altered).decode('utf-8'), digest(altered), KEY))
    cold = monitoring.MonitoringLedger(path, clock)
    recovered = tool.ToolBridge(cold)
    forbid_writes(monkeypatch, cold)
    with pytest.raises(HTTPException) as error: recovered.receipt(KEY)
    assert error.value.status_code == 409
    assert len(actions(path, 'set_ptz')) == 1


def test_actual_asgi_fixed_receipt_url_header_hash_and_old_routes(tmp_path, monkeypatch):
    path = tmp_path / 'http-tool.sqlite3'
    clock = Clock()
    app = tool.create_app(path, TOKEN, clock=clock, auto_consume=False)
    authorization = {'Authorization': 'Bearer ' + TOKEN}
    headers = {**authorization, 'Idempotency-Key': KEY}
    body = install_body(app.state.monitoring_ledger, clock, camera=True)
    assert isinstance(app.state.monitoring_tool_bridge, tool.ToolBridge)
    with TestClient(app) as client:
        assert client.post('/tools/monitoring/install', content=raw(body)).status_code == 401
        assert client.get('/health').status_code == 200
        assert client.get('/devices/' + CAMERA, headers=authorization).status_code == 200
        assert client.get('/tools/monitoring/receipt', headers=authorization).status_code == 422
        assert client.get('/tools/monitoring/receipt', headers=authorization,
            params={'idempotency_key': KEY}).status_code == 422
        assert client.get('/tools/monitoring/receipt/' + KEY, headers=headers).status_code == 404
        absent = client.get('/tools/monitoring/receipt', headers=headers)
        assert absent.status_code == 404
        pending = client.post('/tools/monitoring/install', content=raw(body), headers=headers)
        assert pending.status_code == 202
        envelope(pending.json(), body, 'pending')
        pending_get = client.get('/tools/monitoring/receipt', headers=headers)
        assert pending_get.status_code == 200 and pending_get.json() == pending.json()
        assert client.get('/monitoring/requests/' + KEY,
            params={'expected_sha256': sha(raw(body))}, headers=authorization).status_code == 200
        assert client.post('/monitoring/consume', headers=authorization).status_code == 200
        clock.advance(2)
        assert client.post('/monitoring/consume', headers=authorization).status_code == 200
        response = client.get('/tools/monitoring/receipt', headers=headers)
        assert response.status_code == 200
        envelope(response.json(), body, 'succeeded')
        unknown_key = 'http-original-install-unknown'
        unknown_headers = {**authorization, 'Idempotency-Key': unknown_key}
        def interrupted_install(*_args, **_kwargs): raise OSError('loss before actual install')
        with monkeypatch.context() as guard:
            guard.setattr(app.state.monitoring_ledger, 'install', interrupted_install)
            unknown_post = client.post('/tools/monitoring/install', content=raw(body), headers=unknown_headers)
        assert unknown_post.status_code == 202
        unknown = envelope(unknown_post.json(), body, 'unknown', key=unknown_key)
        assert unknown['error'] == 'original_monitoring_install_unknown'
        with monkeypatch.context() as guard:
            forbid_writes(guard, app.state.monitoring_ledger)
            unknown_get = client.get('/tools/monitoring/receipt', headers=unknown_headers)
        assert unknown_get.status_code == 200 and unknown_get.json() == unknown
    assert len(actions(path, 'set_ptz')) == 1
