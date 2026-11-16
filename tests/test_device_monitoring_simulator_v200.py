"""Actual isolated SQLite/ASGI SIM contracts; no platform DB or hardware.

The controlled clock advances the unchanged V175 device state machine. Only
transport interruption is injected; scheduling, original receipts and inventory
are the real implementations. Root executes these tests separately.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import sqlite3
from threading import Barrier

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from generic_agents import device_monitoring_simulator_v200 as monitoring
from generic_agents.device_simulator_v175 import Command, DeviceLedger, LabChange, _json, _utc


DOCK = 'sim-dock-01'
CAMERA = 'sim-camera-01'
TOKEN = 'synthetic-monitoring-simulator-secret-v200'


class Clock:
    def __init__(self): self.now = 1_700_000_000.1234567
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


def raw(value): return _json(value).encode('utf-8')
def sha(value): return hashlib.sha256(value).hexdigest()


def configuration(clock, *, camera=False, cycles=2, duration=2, window=60):
    common = {'kind': 'camera' if camera else 'drone',
        'valid_from': _utc(clock()), 'valid_until': _utc(clock() + window)}
    if camera:
        return {**common, 'preset_ref': '选定预置位一', 'preset_hash': 'b' * 64,
            'pan_degrees': 12, 'tilt_degrees': -7, 'zoom': 33}
    return {**common, 'route_ref': '选定航线一', 'route_hash': 'a' * 64,
        'duration_seconds': duration, 'speed_m_s': 5, 'mission_name': '实验室周期模拟',
        'interval_seconds': max(5, duration), 'max_cycles': cycles}


def install_body(ledger, clock, *, camera=False, version=0, config=None):
    device_id = CAMERA if camera else DOCK
    return {'schema': monitoring.INSTALL, 'device_id': device_id,
        'expected_version': version,
        'expected_device_revision': ledger.devices.device(device_id)['revision'],
        'configuration': config or configuration(clock, camera=camera)}


def installed(tmp_path, *, camera=False, cycles=2, duration=2, window=60):
    clock = Clock(); path = tmp_path / 'monitor.sqlite3'
    ledger = monitoring.MonitoringLedger(path, clock)
    body = install_body(ledger, clock, camera=camera,
        config=configuration(clock, camera=camera, cycles=cycles, duration=duration, window=window))
    original = ledger.install('install-one', raw(body))
    return path, clock, ledger, body, original


def rows(path, table):
    # Table names are fixed test literals, never client input.
    assert table in {'receipts', 'monitoring_ticks_v200', 'monitoring_commands_v200',
        'monitoring_versions_v200', 'monitoring_slots_v200'}
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM ' + table)]


def actions(path, action):
    return [row for row in rows(path, 'receipts') if json.loads(row['body'])['action'] == action]


def assert_sim(value):
    assert value['simulation'] is True
    assert value['origin_kind'] == 'simulated_equipment'
    for key in ('field_verified', 'physical_device_connected', 'flight_path_verified',
            'image_generation', 'hardware_authorized'):
        assert value[key] is False


def test_install_is_not_device_success_and_original_snapshot_survives_cold_completion(tmp_path):
    path, clock, ledger, body, original = installed(tmp_path, camera=True)
    assert_sim(original)
    assert original['status'] == 'installed' and original['first_action_verified'] is False
    assert rows(path, 'receipts') == []
    assert ledger.install('install-one', raw(body)) == original
    accepted = ledger.consume_due()['plans'][0]
    assert accepted['status'] == 'waiting_receipt'
    assert accepted['first_action_verified'] is False
    assert accepted['ticks'][0]['receipt']['status'] == 'accepted'
    clock.advance(2)
    ledger = monitoring.MonitoringLedger(path, clock)
    completed = ledger.consume_due()['plans'][0]
    assert completed['status'] == 'completed' and completed['first_action_verified'] is True
    assert ledger.original('install-one', sha(raw(body))) == original
    assert ledger.install('install-one', raw(body)) == original
    assert len(actions(path, 'set_ptz')) == 1
    current = ledger.devices.device(CAMERA)['state']
    assert (current['pan_degrees'], current['tilt_degrees'], current['zoom']) == (12, -7, 33)
    assert_sim(completed)


def test_two_actual_drone_periods_and_maximum_survive_restart(tmp_path):
    path, clock, ledger, body, original = installed(tmp_path)
    first = ledger.consume_due()['plans'][0]
    first_key = first['ticks'][0]['request_key']
    assert first['next_due'] == monitoring._instant(first['ticks'][0]['prepared_at']) + 5
    # Merely polling the same instant cannot produce a second cycle.
    assert len(ledger.consume_due()['plans'][0]['ticks']) == 1
    clock.advance(2)
    ledger = monitoring.MonitoringLedger(path, clock)
    first_complete = ledger.consume_due()['plans'][0]
    assert first_complete['ticks'][0]['status'] == 'succeeded'
    assert first_complete['status'] == 'active' and first_complete['cycles_started'] == 1
    clock.advance(3)
    second = ledger.consume_due()['plans'][0]
    assert len(second['ticks']) == 2 and second['cycles_started'] == 2
    assert second['ticks'][1]['request_key'] != first_key
    assert second['next_due'] is None
    assert [row['action'] for row in [json.loads(r['body']) for r in actions(path, 'start_mission')]] == ['start_mission'] * 2
    clock.advance(2)
    done = ledger.consume_due()['plans'][0]
    assert done['status'] == 'completed' and all(t['status'] == 'succeeded' for t in done['ticks'])
    assert ledger.devices.device(DOCK)['state']['battery_percent'] < 100
    clock.advance(100)
    cold = monitoring.MonitoringLedger(path, clock)
    assert cold.consume_due()['plans'][0]['cycles_started'] == 2
    assert len(actions(path, 'start_mission')) == 2
    assert cold.original('install-one', sha(raw(body))) == original


def test_delayed_timer_consumes_one_cycle_without_catchup_burst(tmp_path):
    path, clock, ledger, _, _ = installed(tmp_path, cycles=3, window=600)
    ledger.consume_due(); clock.advance(100)
    second = ledger.consume_due()['plans'][0]
    assert len(second['ticks']) == 2 and second['ticks'][1]['ordinal'] == 2
    assert second['next_due'] == monitoring._instant(second['ticks'][1]['prepared_at']) + 5
    assert len(ledger.consume_due()['plans'][0]['ticks']) == 2
    assert len(actions(path, 'start_mission')) == 2


def test_stop_only_future_ticks_preserves_inflight_and_original_stop_receipt(tmp_path):
    path, clock, ledger, _, _ = installed(tmp_path, duration=10)
    before = ledger.consume_due()['plans'][0]; key = before['ticks'][0]['request_key']
    body = {'schema': monitoring.STOP, 'device_id': DOCK, 'expected_version': 1,
        'reason': '停止后续实验室计划，原飞行继续查原回执'}
    stopped = ledger.stop('stop-one', raw(body))
    assert stopped['in_flight_tick_keys'] == [key]
    assert stopped['stop_cancels_in_flight_device_action'] is False
    assert ledger.devices.device(DOCK)['state']['active_request_id'] == key
    assert not actions(path, 'return_to_dock') and not actions(path, 'cancel_mission')
    clock.advance(11)
    ledger = monitoring.MonitoringLedger(path, clock)
    current = ledger.consume_due()['plans'][0]
    assert current['status'] == 'stopped' and current['next_due'] is None
    assert current['ticks'][0]['status'] == 'succeeded' and current['first_action_verified'] is True
    assert ledger.original('stop-one', sha(raw(body))) == stopped
    assert ledger.stop('stop-one', raw(body)) == stopped
    clock.advance(100)
    assert ledger.consume_due()['plans'][0]['cycles_started'] == 1
    assert len(actions(path, 'start_mission')) == 1


def test_commit_then_lost_device_reply_is_recovered_by_original_receipt_only(tmp_path, monkeypatch):
    path, clock, ledger, _, _ = installed(tmp_path, cycles=1)
    real = ledger.devices.invoke
    def lose_reply(key, command):
        real(key, command)
        raise OSError('synthetic lost reply after actual SQLite commit')
    monkeypatch.setattr(ledger.devices, 'invoke', lose_reply)
    unknown = ledger.consume_due()['plans'][0]
    key = unknown['ticks'][0]['request_key']
    assert unknown['status'] == 'unknown' and len(actions(path, 'start_mission')) == 1
    clock.advance(3)
    cold = monitoring.MonitoringLedger(path, clock)
    def forbid(*_args, **_kwargs): pytest.fail('retained unknown tick must not invoke again')
    monkeypatch.setattr(cold.devices, 'invoke', forbid)
    recovered = cold.consume_due()['plans'][0]
    assert recovered['status'] == 'completed'
    assert recovered['ticks'][0]['request_key'] == key
    assert recovered['ticks'][0]['receipt']['status'] == 'succeeded'
    assert len(actions(path, 'start_mission')) == 1


def test_prepared_crash_with_no_device_receipt_remains_unknown_never_reposts(tmp_path, monkeypatch):
    path, clock, ledger, _, _ = installed(tmp_path, cycles=1)
    def crash_before_call(*_args): raise SystemExit('synthetic process loss')
    monkeypatch.setattr(ledger.devices, 'invoke', crash_before_call)
    with pytest.raises(SystemExit): ledger.consume_due()
    assert len(rows(path, 'monitoring_ticks_v200')) == 1 and rows(path, 'receipts') == []
    cold = monitoring.MonitoringLedger(path, clock)
    def forbid(*_args): pytest.fail('prepared original key must remain receipt-only')
    monkeypatch.setattr(cold.devices, 'invoke', forbid)
    value = cold.consume_due()['plans'][0]
    assert value['status'] == 'unknown' and value['first_action_verified'] is False
    assert value['ticks'][0]['receipt'] is None
    assert cold.consume_due()['plans'][0] == value
    assert rows(path, 'receipts') == []


@pytest.mark.parametrize('condition', ['offline', 'motor_fault', 'battery', 'wind', 'occupied'])
def test_actual_old_device_safety_rejects_tick_without_releasing_occupancy(tmp_path, condition):
    path, clock, ledger, _, _ = installed(tmp_path)
    if condition == 'occupied':
        ledger.devices.invoke('other-flight', Command(device_id=DOCK, action='start_mission',
            expected_revision=ledger.devices.device(DOCK)['revision'],
            arguments={'duration_seconds': 20, 'speed_m_s': 5, 'mission_name': 'other original flight'}))
    else:
        telemetry = {'battery_percent': 10} if condition == 'battery' else {'wind_m_s': 11} if condition == 'wind' else {}
        ledger.devices.lab_change('lab-safety', LabChange(device_id=DOCK,
            expected_revision=ledger.devices.device(DOCK)['revision'],
            fault=condition if condition in {'offline', 'motor_fault'} else None, telemetry=telemetry))
    value = ledger.consume_due()['plans'][0]
    assert value['status'] == 'blocked' and value['first_action_verified'] is False
    assert value['ticks'][0]['status'] == 'blocked' and value['ticks'][0]['receipt'] is None
    assert len(actions(path, 'start_mission')) == (1 if condition == 'occupied' else 0)
    if condition == 'occupied':
        assert ledger.devices.device(DOCK)['state']['active_request_id'] == 'other-flight'
    assert not actions(path, 'cancel_mission') and not actions(path, 'return_to_dock')


def test_future_window_and_insufficient_remaining_window_do_not_dispatch(tmp_path):
    clock = Clock(); path = tmp_path / 'future.sqlite3'; ledger = monitoring.MonitoringLedger(path, clock)
    config = configuration(clock); config['valid_from'] = _utc(clock() + 10)
    ledger.install('future', raw(install_body(ledger, clock, config=config)))
    assert ledger.consume_due()['plans'][0]['ticks'] == []
    clock.advance(61)
    assert ledger.consume_due()['plans'][0]['status'] == 'expired'
    assert rows(path, 'receipts') == []
    other = tmp_path / 'short.sqlite3'; ledger = monitoring.MonitoringLedger(other, clock)
    ledger.install('short', raw(install_body(ledger, clock,
        config=configuration(clock, duration=10, window=5))))
    assert ledger.consume_due()['plans'][0]['status'] == 'expired'
    assert rows(other, 'receipts') == []


def test_two_independent_ledger_instances_have_one_original_tick(tmp_path):
    path, clock, ledger, _, _ = installed(tmp_path)
    other = monitoring.MonitoringLedger(path, clock); barrier = Barrier(2)
    def consume(value):
        barrier.wait(timeout=5)
        return value.consume_due()
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(consume, [ledger, other]))
    assert all(len(value['plans'][0]['ticks']) == 1 for value in values)
    assert len(rows(path, 'monitoring_ticks_v200')) == len(actions(path, 'start_mission')) == 1


@pytest.mark.parametrize('change', ['float_alias', 'zero_sign', 'whitespace'])
def test_original_key_binds_exact_utf8_body_not_equivalent_json(tmp_path, change):
    clock = Clock(); path = tmp_path / 'typed.sqlite3'; ledger = monitoring.MonitoringLedger(path, clock)
    body = install_body(ledger, clock, camera=True)
    body['configuration']['pan_degrees'] = -0.0; body['configuration']['tilt_degrees'] = 1
    first = raw(body); ledger.install('typed-original', first)
    replacement = copy.deepcopy(body)
    if change == 'float_alias': replacement['configuration']['tilt_degrees'] = 1.0
    if change == 'zero_sign': replacement['configuration']['pan_degrees'] = 0.0
    changed = b' ' + first if change == 'whitespace' else raw(replacement)
    with pytest.raises(HTTPException) as error: ledger.install('typed-original', changed)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error: ledger.original('typed-original', sha(changed))
    assert error.value.status_code == 409
    assert rows(path, 'receipts') == [] and len(rows(path, 'monitoring_commands_v200')) == 1


@pytest.mark.parametrize('mutation', ['bool_version', 'bool_cycles', 'zoom34', 'missing_route_hash',
    'wrong_device_kind', 'extra_config', 'offset_missing', 'interval4', 'interval_less_duration'])
def test_closed_typed_configuration_rejects_before_install(tmp_path, mutation):
    clock = Clock(); path = tmp_path / 'invalid.sqlite3'; ledger = monitoring.MonitoringLedger(path, clock)
    body = install_body(ledger, clock, camera=mutation == 'zoom34')
    config = body['configuration']
    if mutation == 'bool_version': body['expected_version'] = False
    if mutation == 'bool_cycles': config['max_cycles'] = True
    if mutation == 'zoom34': config['zoom'] = 34
    if mutation == 'missing_route_hash': del config['route_hash']
    if mutation == 'wrong_device_kind': body['device_id'] = CAMERA
    if mutation == 'extra_config': config['waypoints'] = []
    if mutation == 'offset_missing': config['valid_from'] = '2026-10-24T10:00:00'
    if mutation == 'interval4': config['interval_seconds'] = 4
    if mutation == 'interval_less_duration': config.update(duration_seconds=10, interval_seconds=5)
    with pytest.raises(HTTPException) as error: ledger.install('invalid', raw(body))
    assert error.value.status_code == 422
    assert rows(path, 'monitoring_commands_v200') == rows(path, 'receipts') == []


@pytest.mark.parametrize('bad', [b'{"schema":"x","schema":"y"}', b'{"x":NaN}',
    b'{"x":Infinity}', b'\xff', b'[]', b'{}', b'{}' + b' ' * monitoring.MAX_RAW_BYTES])
def test_invalid_raw_never_reaches_a_device(bad):
    with pytest.raises(HTTPException) as error: monitoring.parse_wire(bad)
    assert error.value.status_code == 422


def test_configuration_and_device_cas_and_pending_plan_replacement_are_strict(tmp_path):
    path, clock, ledger, body, _ = installed(tmp_path)
    with pytest.raises(HTTPException) as error: ledger.install('stale-config', raw(body))
    assert error.value.status_code == 409
    revised = install_body(ledger, clock, version=1)
    revised['expected_device_revision'] += 1
    with pytest.raises(HTTPException) as error: ledger.install('stale-device', raw(revised))
    assert error.value.status_code == 409
    ledger.consume_due()
    revised = install_body(ledger, clock, version=1)
    with pytest.raises(HTTPException) as error: ledger.install('pending-new-plan', raw(revised))
    assert error.value.status_code == 409
    assert len(rows(path, 'monitoring_commands_v200')) == 1


@pytest.mark.parametrize('loss', ['command', 'version', 'tick', 'terminal_receipt'])
def test_inventory_loss_cannot_create_replacement_action(tmp_path, loss):
    path, clock, ledger, _, _ = installed(tmp_path, camera=True)
    ledger.consume_due(); clock.advance(2); ledger.consume_due()
    with sqlite3.connect(path) as db:
        if loss == 'command': db.execute('DELETE FROM monitoring_commands_v200')
        elif loss == 'version': db.execute('DELETE FROM monitoring_versions_v200')
        elif loss == 'tick': db.execute('DELETE FROM monitoring_ticks_v200')
        else: db.execute('DELETE FROM receipts')
    before = rows(path, 'receipts')
    cold = monitoring.MonitoringLedger(path, clock)
    with pytest.raises(HTTPException) as error: cold.consume_due()
    assert error.value.status_code == 409
    assert rows(path, 'receipts') == before


def test_legacy_state_is_never_taken_over_or_modified(tmp_path):
    path = tmp_path / 'legacy.sqlite3'; clock = Clock(); old = DeviceLedger(path, clock)
    old.invoke('old-read', Command(device_id=CAMERA, action='get_status'))
    before = rows(path, 'receipts')
    with pytest.raises(HTTPException) as error: monitoring.MonitoringLedger(path, clock)
    assert error.value.status_code == 422 and rows(path, 'receipts') == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='monitoring_meta_v200'").fetchone() is None


def test_real_asgi_fresh_boot_auth_install_original_get_and_cold_ptz(tmp_path):
    path = tmp_path / 'http.sqlite3'; clock = Clock()
    authorization = {'Authorization': 'Bearer ' + TOKEN}
    app = monitoring.create_app(path, TOKEN, clock=clock, auto_consume=False)
    body = install_body(app.state.monitoring_ledger, clock, camera=True); original_raw = raw(body)
    with TestClient(app) as client:
        assert client.post('/monitoring/install', content=original_raw).status_code == 401
        assert client.get('/devices/' + CAMERA, headers=authorization).status_code == 200
        response = client.post('/monitoring/install', content=original_raw,
            headers={**authorization, 'Idempotency-Key': 'http-install'})
        assert response.status_code == 200; original = response.json()
        assert client.get('/monitoring/requests/http-install', params={'expected_sha256': sha(original_raw)},
            headers=authorization).json() == original
        assert client.get('/monitoring/requests/http-install', params={'expected_sha256': '0' * 64},
            headers=authorization).status_code == 409
        assert client.post('/monitoring/consume', content=b'{"unexpected":1}', headers=authorization).status_code == 422
        value = client.post('/monitoring/consume', content=b'{}', headers=authorization)
        assert value.status_code == 200 and value.json()['plans'][0]['first_action_verified'] is False
    clock.advance(2)
    with TestClient(monitoring.create_app(path, TOKEN, clock=clock, auto_consume=False)) as client:
        result = client.post('/monitoring/consume', headers=authorization)
        assert result.status_code == 200 and result.json()['plans'][0]['first_action_verified'] is True
        assert client.get('/monitoring/devices/' + CAMERA, headers=authorization).json()['status'] == 'completed'
        assert client.get('/monitoring/requests/http-install', params={'expected_sha256': sha(original_raw)},
            headers=authorization).json() == original
        assert client.get('/monitoring/health', headers=authorization).json()['lab_interval_min_seconds'] == 5
    assert len(actions(path, 'set_ptz')) == 1
