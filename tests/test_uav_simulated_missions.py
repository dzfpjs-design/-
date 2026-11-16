from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException

from generic_agents.device_simulator_v175 import DeviceLedger, LabChange
from generic_agents.uav_simulated_missions import SimulatedMissionService


@pytest.fixture
def lab(tmp_path):
    now = [1000.0]
    clock = lambda: now[0]
    device = DeviceLedger(tmp_path / 'device.sqlite', clock=clock)
    missions = SimulatedMissionService(tmp_path / 'missions.sqlite', device, clock=clock)
    return missions, device, now


def plan(missions):
    return missions.prepare('forest', 'smoke-1', [117.6, 24.5], [117.601, 24.501])


def test_approval_persistence_and_simulated_completion(lab):
    service, device, now = lab
    draft = plan(service)
    assert draft['status'] == 'awaiting_approval'
    assert '不能用于实际飞行' in draft['plan']['limitations']
    assert device.device('sim-dock-01')['state']['phase'] == 'idle'
    with pytest.raises(HTTPException):
        service.approve_and_start('forest', draft['id'], manager='manager', plan_hash='wrong')
    launched = service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    assert launched['status'] == 'flying'
    recreated = SimulatedMissionService(service.path, device, clock=service.clock)
    assert recreated.get('forest', draft['id'])['approval']['manager'] == 'manager'
    with pytest.raises(HTTPException):
        recreated.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    with pytest.raises(HTTPException):
        recreated.get('other-workspace', draft['id'])
    now[0] += draft['plan']['duration_seconds'] + 1
    finished = recreated.refresh('forest', draft['id'])
    assert finished['status'] == 'completed'
    assert finished['receipt']['result']['artifacts'] == []
    assert finished['receipt']['result']['field_observation_created'] is False


def test_low_battery_and_confirmed_return(lab):
    service, device, now = lab
    draft = plan(service)
    service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    current = device.device('sim-dock-01')
    device.lab_change('battery-change', LabChange(device_id='sim-dock-01', expected_revision=current['revision'], telemetry={'battery_percent': 20}))
    warning = service.refresh('forest', draft['id'])
    assert warning['status'] == 'low_battery'
    assert warning['alerts']
    returning = service.confirm_return('forest', draft['id'], manager='manager')
    assert returning['status'] == 'returning'
    now[0] += 3
    assert service.refresh('forest', draft['id'])['status'] == 'returned'


def test_lost_link_stays_occupied_and_cannot_issue_return(lab):
    service, device, now = lab
    draft = plan(service)
    service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    current = device.device('sim-dock-01')
    device.lab_change('offline', LabChange(device_id='sim-dock-01', expected_revision=current['revision'], fault='offline'))
    warning = service.refresh('forest', draft['id'])
    assert warning['status'] == 'connection_lost'
    assert device.device('sim-dock-01')['state']['active_request_id']
    with pytest.raises(HTTPException):
        service.confirm_return('forest', draft['id'], manager='manager')


def test_lost_dispatch_reply_recovers_original_without_resend(lab, monkeypatch):
    service, device, now = lab
    draft = plan(service)
    invoke = device.invoke
    def delivered_then_timeout(*args, **kwargs):
        invoke(*args, **kwargs)
        raise TimeoutError('reply lost')
    monkeypatch.setattr(device, 'invoke', delivered_then_timeout)
    assert service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])['status'] == 'dispatch_unknown'
    def never_resend(*args, **kwargs):
        raise AssertionError('must not resend')
    monkeypatch.setattr(device, 'invoke', never_resend)
    assert service.refresh('forest', draft['id'])['status'] == 'flying'


def test_invalid_or_stale_plan_is_not_dispatched(lab):
    service, device, now = lab
    assert service.prepare('forest', 'far', [0, 0], [10, 10])['status'] == 'blocked'
    draft = plan(service)
    current = device.device('sim-dock-01')
    device.lab_change('weather', LabChange(device_id='sim-dock-01', expected_revision=current['revision'], telemetry={'wind_m_s': 11}))
    with pytest.raises(HTTPException):
        service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    assert device.device('sim-dock-01')['state']['phase'] == 'idle'


def test_concurrent_approvals_send_one_command(lab):
    service, device, now = lab
    draft = plan(service)
    def approve():
        try:
            return service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])['status']
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(), range(2)))
    assert sorted(map(str, results)) == ['409', 'flying']
    with device.transaction() as db:
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0] == 1


def test_missing_receipt_stays_unknown_without_second_dispatch(lab, monkeypatch):
    service, device, now = lab
    draft = plan(service)
    def timeout(*args, **kwargs):
        raise TimeoutError('unknown whether delivered')
    monkeypatch.setattr(device, 'invoke', timeout)
    assert service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])['status'] == 'dispatch_unknown'
    assert service.refresh('forest', draft['id'])['status'] == 'dispatch_unknown'
    with pytest.raises(HTTPException):
        service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])


def test_late_start_reply_does_not_overwrite_later_return(lab, monkeypatch):
    service, device, now = lab
    draft = plan(service)
    sent, resume = Event(), Event()
    invoke = device.invoke
    def delayed(key, command):
        receipt = invoke(key, command)
        if command.action == 'start_mission':
            sent.set()
            assert resume.wait(5)
        return receipt
    monkeypatch.setattr(device, 'invoke', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.approve_and_start, 'forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
        try:
            assert sent.wait(5)
            assert service.refresh('forest', draft['id'])['status'] == 'flying'
            returning = service.confirm_return('forest', draft['id'], manager='manager')
            assert returning['status'] == 'returning'
            now[0] += 3
            settled = service.refresh('forest', draft['id'])
            assert settled['status'] == 'returned'
        finally:
            resume.set()
        late = future.result(timeout=5)
    assert late['status'] == 'returned'
    assert late['receipt']['action'] == 'return_to_dock'
    assert service.get('forest', draft['id'])['status'] == 'returned'


def test_late_start_reply_does_not_overwrite_intervening_unknown_refresh(lab, monkeypatch):
    service, device, now = lab
    draft = plan(service)
    sent, resume = Event(), Event()
    invoke = device.invoke
    def delayed(key, command):
        receipt = invoke(key, command)
        sent.set()
        assert resume.wait(5)
        return receipt
    monkeypatch.setattr(device, 'invoke', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.approve_and_start, 'forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
        try:
            assert sent.wait(5)
            monkeypatch.setattr(device, 'receipt', lambda key: (_ for _ in ()).throw(TimeoutError()))
            assert service.refresh('forest', draft['id'])['status'] == 'dispatch_unknown'
        finally:
            resume.set()
        assert future.result(timeout=5)['status'] == 'dispatch_unknown'
    assert service.get('forest', draft['id'])['receipt'] is None


def test_late_return_reply_reloads_completed_status(lab, monkeypatch):
    service, device, now = lab
    draft = plan(service)
    service.approve_and_start('forest', draft['id'], manager='manager', plan_hash=draft['plan_hash'])
    sent, resume = Event(), Event()
    invoke = device.invoke
    def delayed(key, command):
        receipt = invoke(key, command)
        if command.action == 'return_to_dock':
            sent.set()
            assert resume.wait(5)
        return receipt
    monkeypatch.setattr(device, 'invoke', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.confirm_return, 'forest', draft['id'], manager='manager')
        try:
            assert sent.wait(5)
            now[0] += 3
            assert service.refresh('forest', draft['id'])['status'] == 'returned'
        finally:
            resume.set()
        assert future.result(timeout=5)['status'] == 'returned'
    monkeypatch.setattr(device, 'receipt', lambda key: (_ for _ in ()).throw(TimeoutError()))
    assert service.refresh('forest', draft['id'])['status'] == 'returned'
