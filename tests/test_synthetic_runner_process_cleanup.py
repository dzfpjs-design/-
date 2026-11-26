"""The synthetic runner must not leave its own test children running."""
import importlib.util
import json
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
        runner_module()._stop_test_process_group(process)
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
