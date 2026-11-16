"""Workspace-loop wiring without a production worker or physical equipment."""
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from generic_agents import worker
from generic_agents import uav_simulated_setup_monitor as wiring
from generic_agents.device_simulator_v175 import LabChange
from generic_agents.uav_simulated_monitor import SimulatedWorkspaceMonitor


def start_flight(root, workspace):
    monitor=SimulatedWorkspaceMonitor(root,workspace)
    draft=monitor.service.prepare(workspace,'模拟事件',[117.6,24.5],[117.601,24.501])
    monitor.service.approve_and_start(workspace,draft['id'],manager='manager',plan_hash=draft['plan_hash'])
    device=monitor.service.devices;view=device.device('sim-dock-01')
    device.lab_change('offline-test',LabChange(device_id='sim-dock-01',expected_revision=view['revision'],fault='offline'))
    return monitor,draft['id']


def patch_worker(monkeypatch, stop, workspace):
    class Session:
        def __init__(self,*args): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,model,identity): return object() if identity==workspace else None
    monkeypatch.setattr(worker,'Session',Session)
    monkeypatch.setattr(worker,'engine',lambda:None)
    monkeypatch.setattr(worker,'_StopEvent',lambda:stop)
    monkeypatch.setattr(worker,'SETUP_POLL_ROOTS',())
    monkeypatch.setattr(worker,'SETUP_DAILY_ROOTS',())
    monkeypatch.setattr(worker.signal,'getsignal',lambda signum:None)
    monkeypatch.setattr(worker.signal,'signal',lambda *args:None)


def test_setup_worker_updates_only_its_workspace_and_stops_monitor(tmp_path,monkeypatch):
    a,b=str(uuid4()),str(uuid4())
    ma,ida=start_flight(tmp_path,a);mb,idb=start_flight(tmp_path,b)
    stop=Event();patch_worker(monkeypatch,stop,a)
    monkeypatch.setattr(wiring,'settings',lambda:SimpleNamespace(storage_dir=tmp_path))
    def process(**kwargs):
        assert kwargs['workspace_id']==a
        # Wait for the actual background monitor, without a production service.
        for _ in range(200):
            if ma.service.get(a,ida)['status']=='connection_lost': break
            Event().wait(.01)
        stop.set();return False
    monkeypatch.setattr(worker,'process_once',process)
    assert worker.run_setup(a)==0
    assert ma.service.get(a,ida)['status']=='connection_lost'
    assert mb.service.get(b,idb)['status']=='flying'


def test_no_existing_ledger_does_not_create_simulator_files(tmp_path,monkeypatch):
    wid=str(uuid4());stop=Event()
    monkeypatch.setattr(wiring,'settings',lambda:SimpleNamespace(storage_dir=tmp_path))
    monkeypatch.setattr(wiring,'INTERVAL_SECONDS',.01)
    def forbidden(*args,**kwargs): raise AssertionError('不得建立模拟器')
    monkeypatch.setattr(wiring,'SimulatedWorkspaceMonitor',forbidden)
    thread=wiring.start_workspace_monitor(wid,stop)
    Event().wait(.05);stop.set();thread.join(timeout=2)
    assert not thread.is_alive()
    assert not (tmp_path/'uav-simulation').exists()


def test_monitor_exception_is_logged_and_does_not_stop_setup_worker(tmp_path,monkeypatch,capsys):
    wid=str(uuid4());start_flight(tmp_path,wid)
    stop=Event();attempt=Event();patch_worker(monkeypatch,stop,wid)
    monkeypatch.setattr(wiring,'settings',lambda:SimpleNamespace(storage_dir=tmp_path))
    class BrokenMonitor:
        def __init__(self,*args): pass
        def tick(self,**kwargs): attempt.set();raise RuntimeError('do not expose details')
    monkeypatch.setattr(wiring,'SimulatedWorkspaceMonitor',BrokenMonitor)
    def process(**kwargs):
        assert attempt.wait(3)
        stop.set();return False
    monkeypatch.setattr(worker,'process_once',process)
    assert worker.run_setup(wid)==0
    output=capsys.readouterr().out
    assert 'RuntimeError' in output and '主处理器继续运行' in output
    assert 'do not expose details' not in output
