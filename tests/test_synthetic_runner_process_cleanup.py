"""The synthetic runner must not leave its own test children running."""
import importlib.util
import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def runner_module():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'run_retained_user_advice_checkpoint.py'
    spec = importlib.util.spec_from_file_location('retained_advice_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_ports_exclude_formal_services_and_each_other(monkeypatch):
    module = runner_module()
    candidates = iter((55486, 8766, 32001, 32001, 32002, 55486, 32003))
    monkeypatch.setattr(module, '_port', lambda: next(candidates))
    assert module._isolated_ports() == (32001, 32002, 32003)


def test_synthetic_ports_fail_closed_when_only_conflicts_are_available(monkeypatch):
    module = runner_module()
    attempts = []
    monkeypatch.setattr(module, '_port', lambda: attempts.append(55486) or 55486)
    with pytest.raises(RuntimeError, match='isolated ports'):
        module._isolated_ports()
    assert 0 < len(attempts) <= 100


def test_no_retention_directory_when_port_selection_exhausted(tmp_path, monkeypatch):
    module = runner_module()
    pg = tmp_path / 'pg'
    pg.mkdir()
    for name in ('initdb', 'pg_ctl', 'psql'):
        (pg / name).touch()
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    monkeypatch.setattr(module, '_port', lambda: 8766)
    roots = []
    monkeypatch.setattr(module, 'private_root', lambda: roots.append('created'))
    with pytest.raises(RuntimeError, match='isolated ports'):
        module.run(pg, Path(sys.executable), checkpoint, 10, retention_probe=True)
    assert not roots


def test_failed_parent_does_not_leave_a_running_child(tmp_path):
    marker = tmp_path / 'child-continued'
    ready = tmp_path / 'child-ready'
    child = "import pathlib,sys,time;pathlib.Path(sys.argv[1]).touch();time.sleep(1);pathlib.Path(sys.argv[2]).touch()"
    parent = ("import pathlib,subprocess,sys,time;"
              "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]);"
              "p=pathlib.Path(sys.argv[2]);"
              "exec('while not p.exists(): time.sleep(0.01)');sys.exit(1)")
    process = subprocess.Popen([sys.executable, '-c', parent, child, str(ready), str(marker)],
                               start_new_session=True)
    try:
        assert process.wait(timeout=5) == 1
        assert runner_module()._stop_test_process_group(process) is True
        time.sleep(1.2)
        assert not marker.exists()
    finally:
        # Only the process group created by this test is targeted.
        import os, signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_uncertain_database_start_is_not_reported_as_stopped(tmp_path, monkeypatch):
    module = runner_module()
    pg = tmp_path / 'pg'
    pg.mkdir()
    for name in ('initdb', 'pg_ctl', 'psql'):
        (pg / name).touch()
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    root = tmp_path / 'retained'
    root.mkdir()
    monkeypatch.setattr(module, 'private_root', lambda: root)
    ports = iter((31001, 31002, 31003))
    monkeypatch.setattr(module, '_port', lambda: next(ports))
    calls = []

    def run_fake(command, **kwargs):
        calls.append(command[-1])
        if command[-1] == 'start':
            raise subprocess.TimeoutExpired(command, 60)
        return SimpleNamespace(returncode=1 if command[-1] == 'stop' else 0)

    monkeypatch.setattr(module.subprocess, 'run', run_fake)
    with pytest.raises(subprocess.TimeoutExpired):
        module.run(pg, Path(sys.executable), checkpoint, 10, retention_probe=True)
    assert calls == ['UTF8', 'start', 'stop']
    status = json.loads((root / 'status.json').read_text())
    assert status['state'] == 'error'
    assert status['cluster_stopped'] is False


def test_cleanup_refuses_when_test_children_may_still_run():
    module = runner_module()
    with tempfile.TemporaryDirectory(prefix='advice-race-pg-') as name:
        root = Path(name)
        (root / 'retain-synthetic-run.json').write_text(json.dumps({'schema': module.SCHEMA}))
        (root / 'status.json').write_text(json.dumps({'cluster_stopped': True,
                                                     'test_processes_stopped': False}))
        with pytest.raises(ValueError):
            module.cleanup(root)
        assert root.exists()


def test_group_still_present_after_kill_is_not_confirmed(monkeypatch):
    module = runner_module()
    sent = []
    monkeypatch.setattr(module.os, 'killpg', lambda pid, sig: sent.append(sig))
    times = iter((0, 0, 10))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0, poll=lambda: 0)
    assert module._stop_test_process_group(process) is False
    assert signal.SIGTERM in sent and signal.SIGKILL in sent and 0 in sent


def test_group_cannot_be_inspected_is_not_confirmed(monkeypatch):
    module = runner_module()
    def reject(pid, sig):
        raise PermissionError('not permitted')
    monkeypatch.setattr(module.os, 'killpg', reject)
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0, poll=lambda: 0)
    assert module._stop_test_process_group(process) is False


def test_successful_test_with_unconfirmed_process_group_reports_failure(tmp_path, monkeypatch):
    module = runner_module()
    pg = tmp_path / 'pg'
    pg.mkdir()
    for name in ('initdb', 'pg_ctl', 'psql'):
        (pg / name).touch()
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    root = tmp_path / 'retained'
    root.mkdir()
    monkeypatch.setattr(module, 'private_root', lambda: root)
    ports = iter((31001, 31002, 31003))
    monkeypatch.setattr(module, '_port', lambda: next(ports))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: '123')
    process = SimpleNamespace(pid=123, wait=lambda timeout: 0, poll=lambda: 0)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(module, '_stop_test_process_group', lambda _: False)
    assert module.run(pg, Path(sys.executable), checkpoint, 10, retention_probe=True) == 1
    status = json.loads((root / 'status.json').read_text())
    assert status['state'] == 'process_cleanup_unconfirmed'
    assert status['cluster_stopped'] is True
    assert status['test_processes_stopped'] is False
