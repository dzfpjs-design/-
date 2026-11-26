"""Synthetic orchestration tests; never connect to a database or start a worker."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    path=Path(__file__).resolve().parents[1]/'scripts'/'create_fresh_advice_checkpoint.py'
    spec=importlib.util.spec_from_file_location('fresh_checkpoint_probe',path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize('result,stopped,expected_state,expected_exit',[
    (0,True,'passed',0),(1,True,'failed',1),(None,True,'timeout',1),
    (0,False,'process_cleanup_unconfirmed',1),
])
def test_synthetic_runner_uses_control_pipe_and_persists_status(tmp_path,monkeypatch,result,stopped,expected_state,expected_exit):
    m=module();pg=tmp_path/'pg';pg.mkdir()
    for name in ('initdb','pg_ctl','psql'):(pg/name).touch()
    root=tmp_path/'retained';checkpoint=tmp_path/'checkpoint';root.mkdir();checkpoint.mkdir()
    dirs=iter((root,checkpoint));monkeypatch.setattr(m.tempfile,'mkdtemp',lambda **kwargs:next(dirs))
    ports=iter((32001,32002));monkeypatch.setattr(m,'free_port',lambda excluded:next(ports))
    monkeypatch.setattr(m.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0))
    monkeypatch.setattr(m.subprocess,'check_output',lambda *args,**kwargs:'123')
    calls=[]
    def start(args,**kwargs):
        calls.append((args,kwargs));return SimpleNamespace(pid=123,returncode=None)
    monkeypatch.setattr(m.subprocess,'Popen',start)
    monkeypatch.setattr(m,'_await_test_result',lambda fd,timeout:result)
    monkeypatch.setattr(m,'_stop_test_process_group',lambda proc:stopped)
    assert m.run(pg,Path(sys.executable),10,preflight=True)==expected_exit
    status=json.loads((root/'status.json').read_text())
    assert status['state']==expected_state
    assert status['test_processes_stopped'] is stopped and status['cluster_stopped'] is True
    args,kwargs=calls[0]
    assert 'synthetic_process_anchor.py' in ' '.join(args)
    assert kwargs['stdin'] is subprocess.PIPE and kwargs['start_new_session'] is True
    assert len(kwargs['pass_fds']) == 1


def test_postgres_start_failure_does_not_claim_cluster_stopped(tmp_path,monkeypatch):
    m=module();pg=tmp_path/'pg';pg.mkdir()
    for name in ('initdb','pg_ctl','psql'):(pg/name).touch()
    root=tmp_path/'retained';checkpoint=tmp_path/'checkpoint';root.mkdir();checkpoint.mkdir()
    dirs=iter((root,checkpoint));monkeypatch.setattr(m.tempfile,'mkdtemp',lambda **kwargs:next(dirs))
    ports=iter((32001,32002));monkeypatch.setattr(m,'free_port',lambda excluded:next(ports))
    def run(args,**kwargs):
        if args[-1]=='start':raise subprocess.TimeoutExpired(args,60)
        return SimpleNamespace(returncode=1 if args[-1]=='stop' else 0)
    monkeypatch.setattr(m.subprocess,'run',run)
    with pytest.raises(subprocess.TimeoutExpired):m.run(pg,Path(sys.executable),10,preflight=True)
    assert json.loads((root/'status.json').read_text())['cluster_stopped'] is False
