from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from generic_agents.device_simulator_v175 import LabChange
from generic_agents.uav_simulated_monitor import SimulatedWorkspaceMonitor


@pytest.fixture
def laboratory(tmp_path):
    now = [1000.0]
    workspace = str(uuid4())
    monitor = SimulatedWorkspaceMonitor(tmp_path, workspace, clock=lambda:now[0])
    return tmp_path, workspace, monitor, now


def start(monitor, workspace):
    service=monitor.service
    draft=service.prepare(workspace, '模拟烟雾', [117.6,24.5], [117.601,24.501])
    service.approve_and_start(workspace,draft['id'],manager='manager',plan_hash=draft['plan_hash'])
    return draft['id']


def test_monitor_persists_lost_link_and_does_not_invoke_commands(laboratory,monkeypatch):
    root,wid,monitor,now=laboratory
    mid=start(monitor,wid)
    device=monitor.service.devices
    state=device.device('sim-dock-01')
    device.lab_change('offline-test',LabChange(device_id='sim-dock-01',expected_revision=state['revision'],fault='offline'))
    monkeypatch.setattr(device,'invoke',lambda *args,**kwargs: pytest.fail('监测不得发送控制命令'))
    result=monitor.tick(batch_size=1)
    assert result == {'status':'ok','scanned':1,'refreshed':1,'errors':0}
    saved=monitor.service.get(wid,mid)
    assert saved['status']=='connection_lost' and saved['alerts']
    recreated=SimulatedWorkspaceMonitor(root,wid,clock=lambda:now[0])
    assert recreated.service.get(wid,mid)['status']=='connection_lost'


def test_monitor_detects_low_battery_without_auto_return(laboratory,monkeypatch):
    root,wid,monitor,now=laboratory
    mid=start(monitor,wid);device=monitor.service.devices
    state=device.device('sim-dock-01')
    device.lab_change('battery-test',LabChange(device_id='sim-dock-01',expected_revision=state['revision'],telemetry={'battery_percent':20}))
    monkeypatch.setattr(device,'invoke',lambda *args,**kwargs: pytest.fail('监测不得自动返航'))
    assert monitor.tick()['refreshed']==1
    saved=monitor.service.get(wid,mid)
    assert saved['status']=='low_battery' and not saved.get('return_request_id')


def test_batch_cursor_is_bounded_persistent_and_does_not_touch_other_workspace(laboratory,monkeypatch):
    root,wid,monitor,now=laboratory
    mid=start(monitor,wid)
    for i in range(4):
        monitor.service.prepare(wid,'草案'+str(i),[117.6,24.5],[117.601,24.501])
    # A wrong-workspace row, even if present in the same file, is never read.
    foreign=str(uuid4())
    foreign_mission=monitor.service.prepare(foreign,'其他工作区',[117.6,24.5],[117.601,24.501])
    observed=[];original=monitor.service.refresh
    def capture(workspace,mission_id):
        observed.append((workspace,mission_id));return original(workspace,mission_id)
    monkeypatch.setattr(monitor.service,'refresh',capture)
    first=monitor.tick(batch_size=2)
    assert first['scanned']==2
    restarted=SimulatedWorkspaceMonitor(root,wid,clock=lambda:now[0])
    observed2=[];original2=restarted.service.refresh
    def capture2(workspace,mission_id):
        observed2.append((workspace,mission_id));return original2(workspace,mission_id)
    monkeypatch.setattr(restarted.service,'refresh',capture2)
    assert restarted.tick(batch_size=2)['scanned']==2
    assert restarted.tick(batch_size=2)['scanned']==1
    assert observed+observed2==[(wid,mid)]
    assert all(row[1]!=foreign_mission['id'] for row in observed+observed2)
    assert restarted.tick(batch_size=2)['scanned']==2  # next pass wraps
    with pytest.raises(ValueError): restarted.tick(batch_size=101)


def test_cross_process_lease_prevents_overlapping_scans(laboratory,monkeypatch):
    root,wid,monitor,now=laboratory
    start(monitor,wid)
    entered,resume=Event(),Event();original=monitor.service.refresh
    def slow(workspace,mission_id):
        entered.set();assert resume.wait(5);return original(workspace,mission_id)
    monkeypatch.setattr(monitor.service,'refresh',slow)
    other=SimulatedWorkspaceMonitor(root,wid,clock=lambda:now[0])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(monitor.tick)
        try:
            assert entered.wait(5)
            assert other.tick()['status']=='busy'
        finally:
            resume.set()
        assert future.result(timeout=5)['refreshed']==1
    assert other.tick()['status']=='ok'


def test_periodic_processor_runs_and_stops(laboratory):
    root,wid,monitor,now=laboratory
    start(monitor,wid);stop=Event();results=[]
    def capture(result):
        results.append(result);stop.set()
    monitor.run_periodic(stop,interval_seconds=1,on_result=capture)
    assert len(results)==1 and results[0]['refreshed']==1
    with pytest.raises(ValueError): monitor.run_periodic(stop,interval_seconds=0)
