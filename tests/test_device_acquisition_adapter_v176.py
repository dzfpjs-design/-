"""Actual standalone SQLite/ASGI adapter tests; no platform DB or equipment.

Root runs these in its isolated harness without repository conftest. The tests
use real simulator persistence and state transitions, not fabricated success.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from generic_agents.device_simulator_v175 import DeviceLedger, LabChange, create_app
from generic_agents.device_acquisition_adapter_v176 import (
    AcquisitionAdapterV176, canonical, normalize_request)

TOKEN = 'synthetic-acquisition-adapter-contract-token-v176'


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


def request(key='original-acq-1', **changes):
    return {'request_key': key, 'target_external_id': 'simulated-target-1',
        'fields': ['temperature_c', 'humidity_percent'], 'max_duration_seconds': 10,
        'asset_external_id': 'sim-weather-01', 'action': 'read_existing',
        'max_cost_minor': 0, 'currency': 'CNY', **changes}


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def headers(key=None):
    value = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
    if key is not None:
        value['Idempotency-Key'] = key
    return value


def counts(path):
    with sqlite3.connect(path) as db:
        return (db.execute('SELECT count(*) FROM acquisition_adapter_v176').fetchone()[0],
            db.execute('SELECT count(*) FROM receipts').fetchone()[0])


@pytest.mark.parametrize('change', [
    {'max_cost_minor': True}, {'max_duration_seconds': 1.0},
    {'fields': ['temperature_c', 'temperature_c']}, {'fields': ['new_image']},
    {'asset_external_id': 'unknown-real-device'}, {'currency': 'USD'},
    {'fields': ['battery_percent']}, {'extra_authorization': True},
    {'asset_external_id': 'sim-camera-01', 'fields': ['pan_degrees'], 'action': 'observe'}])
def test_closed_request_rejects_ambiguous_or_unimplemented_capabilities(change):
    value = request(**change)
    with pytest.raises(HTTPException) as error:
        normalize_request(wire(value), value['request_key'])
    assert error.value.status_code == 422


def test_header_identity_duplicate_keys_and_exact_wire_are_preserved():
    body = request()
    raw = b' ' + wire(body) + b'\n'
    typed, exact, sha = normalize_request(raw, body['request_key'])
    assert exact.encode() == raw and typed.fields == body['fields'] and len(sha) == 64
    with pytest.raises(HTTPException) as mismatch:
        normalize_request(raw, 'another-original')
    assert mismatch.value.status_code == 409
    duplicate = wire(body)[:-1] + b',"max_cost_minor":1}'
    with pytest.raises(HTTPException) as invalid:
        normalize_request(duplicate, body['request_key'])
    assert invalid.value.status_code == 422
    no_asset = request(); no_asset.pop('asset_external_id')
    with pytest.raises(HTTPException):
        normalize_request(wire(no_asset), body['request_key'])


def test_authenticated_routes_save_actual_weather_telemetry_and_frozen_original(tmp_path):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    body = request(); raw = wire(body)
    with TestClient(create_app(path, TOKEN, clock=clock)) as client:
        assert client.post('/acquisitions', content=raw,
            headers={'Idempotency-Key': body['request_key']}).status_code == 401
        response = client.post('/acquisitions', content=raw, headers=headers(body['request_key']))
        assert response.status_code == 200, response.text
        first = response.json()
        assert first['status'] == 'completed' and first['terminal'] is True
        assert first['no_future_device_activity'] is True and first['simulation'] is True
        assert first['field_verified'] is False and first['source_time_verified'] is False
        sample = first['observation']
        assert sample['attributes'] == {'temperature_c': 25, 'humidity_percent': 70}
        assert sample['field_units'] == {'temperature_c': '°C', 'humidity_percent': '%'}
        assert sample['time_semantics'] == 'simulator_sample_time'
        assert sample['observed_at'] == first['supplier_receipt']['result']['sampled_at']
        assert sample['physical_device_connected'] is False
        assert first['artifacts'] == [] and sample['image_evidence_ids'] == []
        assert client.get('/acquisitions/receipts', params={'request_key': body['request_key']}).status_code == 401
        clock.advance(100)
        recovered = client.get('/acquisitions/receipts', params={'request_key': body['request_key']}, headers=headers())
        assert recovered.status_code == 200 and recovered.json() == first
        repeated = client.post('/acquisitions', content=raw, headers=headers(body['request_key']))
        assert repeated.status_code == 200 and repeated.json() == first
        for replacement in [raw + b' ', wire(request(target_external_id='different-target')),
                wire({**body, 'unrecognized': True})]:
            assert client.post('/acquisitions', content=replacement,
                headers=headers(body['request_key'])).status_code == 409
    assert counts(path) == (1, 1)
    with TestClient(create_app(path, TOKEN, clock=clock)) as reopened:
        recovered = reopened.get('/acquisitions/receipts', params={'request_key': body['request_key']}, headers=headers())
        assert recovered.status_code == 200 and recovered.json() == first
    assert counts(path) == (1, 1)


def test_caller_mutation_and_restart_never_mutate_or_reinvoke_original(tmp_path, monkeypatch):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request(); first = adapter.submit(wire(body), body['request_key'])
    expected = json.loads(canonical(first))
    first['observation']['attributes']['temperature_c'] = 99
    first['supplier_receipt']['result']['state']['temperature_c'] = 99
    clock.advance(500)
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    def no_dispatch(*args, **kwargs):
        pytest.fail('Original request was dispatched again')
    monkeypatch.setattr(ledger, 'invoke', no_dispatch)
    assert adapter.receipt(body['request_key']) == expected
    assert adapter.submit(wire(body), body['request_key']) == expected
    assert counts(path) == (1, 1)


def test_running_flight_does_not_complete_or_produce_imagery(tmp_path):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request(asset_external_id='sim-dock-01', action='observe', fields=['battery_percent'])
    first = adapter.submit(wire(body), body['request_key'])
    assert first['status'] == 'accepted' and first['raw_device_status'] in {'accepted', 'running'}
    assert first['terminal'] is False and first['no_future_device_activity'] is False
    assert first['observation'] is None and first['observation_available'] is False
    clock.advance(1)
    assert adapter.receipt(body['request_key'])['status'] == 'accepted'
    clock.advance(2)
    last = adapter.receipt(body['request_key'])
    assert last['status'] == 'completed_no_data' and last['terminal'] is True
    assert last['no_future_observation'] is True and last['no_future_device_activity'] is True
    assert last['observation'] is None and last['artifacts'] == []
    assert last['field_observation_created'] is False
    assert ledger.device('sim-dock-01')['state']['active_request_id'] is None
    assert adapter.submit(wire(body), body['request_key']) == last
    assert counts(path) == (1, 1)


@pytest.mark.parametrize('supplier_status', ['accepted', 'running'])
def test_both_in_progress_supplier_states_project_to_pending_acquisition(tmp_path, supplier_status):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request(asset_external_id='sim-dock-01', action='observe', fields=['battery_percent'])
    first = adapter.submit(wire(body), body['request_key'])
    with ledger.transaction() as db:
        mapping, typed, _ = adapter._checked(db.execute(
            'SELECT * FROM acquisition_adapter_v176 WHERE request_key=?', (body['request_key'],)).fetchone())
    supplier = json.loads(canonical(first['supplier_receipt']))
    supplier['status'] = supplier_status
    # Pure projection of the two explicitly allowed transport states; the
    # production receipt path separately verifies actual supplier persistence.
    value = adapter._project(mapping, typed, supplier)
    assert value['status'] == 'accepted' and value['terminal'] is False
    assert value['observation_available'] is False and value['observation'] is None
    assert value['no_future_device_activity'] is False


def test_fault_keeps_unknown_platform_result_and_never_releases_or_retries(tmp_path, monkeypatch):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request(asset_external_id='sim-dock-01', action='observe', fields=['battery_percent'])
    first = adapter.submit(wire(body), body['request_key'])
    state = ledger.device('sim-dock-01')
    ledger.lab_change('fault-for-original', LabChange(device_id='sim-dock-01',
        expected_revision=state['revision'], fault='motor_fault'))
    monkeypatch.setattr(ledger, 'invoke', lambda *args, **kwargs: pytest.fail('Faulted action was retried'))
    for action in [lambda: adapter.receipt(body['request_key']),
            lambda: adapter.submit(wire(body), body['request_key'])]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    state = ledger.device('sim-dock-01')['state']
    assert state['active_request_id'] == first['device_command_key']
    assert state['phase'] == 'recovery_required'


def test_intent_without_supplier_receipt_cannot_dispatch_after_restart(tmp_path, monkeypatch):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request()
    def interrupted(*args, **kwargs):
        raise RuntimeError('simulated interruption before supplier receipt')
    monkeypatch.setattr(ledger, 'invoke', interrupted)
    with pytest.raises(HTTPException):
        adapter.submit(wire(body), body['request_key'])
    assert counts(path) == (1, 0)
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    monkeypatch.setattr(ledger, 'invoke', lambda *args, **kwargs: pytest.fail('Uncertain original was retried'))
    for action in [lambda: adapter.receipt(body['request_key']),
            lambda: adapter.submit(wire(body), body['request_key'])]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    assert counts(path) == (1, 0)


def test_committed_supplier_result_survives_interruption_before_adapter_cache(tmp_path, monkeypatch):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request()
    class Interrupted(BaseException):
        pass
    def interrupted(*args, **kwargs):
        raise Interrupted()
    monkeypatch.setattr(adapter, 'receipt', interrupted)
    with pytest.raises(Interrupted):
        adapter.submit(wire(body), body['request_key'])
    assert counts(path) == (1, 1)
    clock.advance(100)
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    monkeypatch.setattr(ledger, 'invoke', lambda *args, **kwargs: pytest.fail('Committed supplier call was retried'))
    result = adapter.submit(wire(body), body['request_key'])
    assert result['status'] == 'completed'
    assert result['observation']['attributes']['temperature_c'] == 25
    assert result['observation']['observed_at'] == result['supplier_receipt']['result']['sampled_at']
    assert counts(path) == (1, 1)


def test_concurrent_original_submission_maps_to_only_one_supplier_command(tmp_path):
    path, clock = tmp_path / 'sim.sqlite3', Clock()
    ledger = DeviceLedger(path, clock); adapter = AcquisitionAdapterV176(ledger)
    body = request()
    def submit():
        try:
            return adapter.submit(wire(body), body['request_key'])
        except HTTPException as error:
            assert error.status_code == 409
            return None  # Original dispatch can still be between intent/receipt commits.
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: submit(), range(2)))
    assert any(value is not None for value in values)
    final = adapter.receipt(body['request_key'])
    assert final['status'] == 'completed'
    assert all(value is None or value == final for value in values)
    assert counts(path) == (1, 1)
