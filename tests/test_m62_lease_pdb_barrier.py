"""Standard-pdb barrier checks with one isolated fake-only subprocess.

No database, socket, product source, sandbox profile, or real worker is used.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pdb
import socket
import stat
import subprocess
import sys
import sysconfig
import time

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / '.runtime/m62-lease-rehearsal-v3.py'
SPEC = importlib.util.spec_from_file_location('m62_offline_pdb_harness', SCRIPT)
h = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = h
SPEC.loader.exec_module(h)
PREFIX = 'M62_LEASE_PDB_BARRIER_' + 'a' * 32 + ':'


@pytest.fixture(autouse=True)
def forbid_database_and_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('offline pdb test attempted a database or socket call')
    for target, name in ((h.step, 'query'), (h.step, 'context'),
            (h.step, 'create_engine'), (h.m, 'create_engine'),
            (h.m, 'snapshot_owned'), (h.m.check, 'create_engine'),
            (socket, 'socket'), (socket, 'create_connection')):
        monkeypatch.setattr(target, name, denied)


def test_standard_pdb_drops_bang_but_keeps_supported_print_command():
    debugger = pdb.Pdb(stdin=io.StringIO(), stdout=io.StringIO(), readrc=False)
    debugger.commands_bnum = 1
    debugger.commands[1] = []
    debugger.commands_silent[1] = False
    debugger.commands_doprompt[1] = True
    debugger.handle_command_def("!print('dropped')")
    assert debugger.commands[1] == []
    debugger.handle_command_def("p print('retained', flush=True)")
    assert debugger.commands[1] == ["p print('retained', flush=True)"]
    assert debugger.commands_doprompt[1] is True


def test_controller_command_stream_uses_supported_p_and_keeps_prompt(tmp_path):
    stream = h.pdb_command_stream(tmp_path / 'fixture.py', 23, PREFIX)
    lines = stream.splitlines()
    assert lines[:3] == ['break ' + str(tmp_path / 'fixture.py') + ':23',
        'commands 1', 'silent']
    assert lines[3].startswith('p print(')
    assert lines[-2:] == ['end', 'continue']
    assert all(not line.startswith('!') for line in lines)
    assert 'continue' not in lines[3:-2]


@pytest.mark.parametrize('path,line,prefix', [
    (Path('relative.py'), 3, PREFIX), (Path('/tmp/a.py'), 0, PREFIX),
    (Path('/tmp/a.py'), True, PREFIX), (Path('/tmp/a\nb.py'), 3, PREFIX),
    (Path('/tmp/a.py'), 3, 'untrusted\ncontinue'),
])
def test_command_arguments_reject_injection_and_ambiguous_types(path, line, prefix):
    with pytest.raises(RuntimeError):
        h.pdb_command_stream(path, line, prefix)


def test_exact_stream_with_standard_python_pdb_waits_before_fake_boundary(tmp_path):
    """The sole permitted subprocess runs a tiny fake fixture, never GA code."""
    os.chmod(tmp_path, 0o700)
    fixture = tmp_path / 'm62_tiny_fixture.py'
    source = '''import json, os, pdb, sys
class FakeSession:
    info = {'worker_lease': {'job_id': 'fake-job', 'lease_token': 'fake-token'}}
    def in_transaction(self):
        return False
db = FakeSession()
adapter = 'builtin'
execution = {'handler': 'data_record_aggregate'}
dispatched = True
native_query = True
try:
    os.fstat(int(os.environ['M62_TEST_INHERITABLE_FD']))
except OSError:
    inherited_fd_closed = True
else:
    inherited_fd_closed = False
print('RUNTIME_DESCRIPTOR:' + json.dumps({'python': list(sys.version_info[:3]),
    'executable': sys.executable, 'pdb_file': pdb.__file__,
    'inherited_fd_closed': inherited_fd_closed}), flush=True)
passed_fake_boundary = False
passed_fake_boundary = True
print('PASSED_FAKE_BOUNDARY', flush=True)
raise SystemExit(1)
'''
    fixture.write_text(source)
    os.chmod(fixture, 0o600)
    break_line = source.splitlines().index('passed_fake_boundary = True') + 1
    python = ROOT / '.venv/bin/python'
    log_path = tmp_path / 'pdb.log'
    canary_fd = os.open(tmp_path / 'descriptor-canary',
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.set_inheritable(canary_fd, True)
    descriptor = os.open(log_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    child = None
    try:
        with os.fdopen(descriptor, 'w+b', buffering=0) as log:
            environment = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path),
                'TMPDIR': str(tmp_path), 'PYTHONPATH': str(tmp_path),
                'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1',
                'PYTHONDONTWRITEBYTECODE': '1',
                'M62_TEST_INHERITABLE_FD': str(canary_fd)}
            child = subprocess.Popen([str(python), '-B', '-m', 'pdb', '-m',
                'm62_tiny_fixture'], cwd=tmp_path, env=environment,
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                close_fds=True, start_new_session=True, text=True, encoding='utf-8')
            child.stdin.write(h.pdb_command_stream(fixture, break_line, PREFIX))
            child.stdin.flush()
            until = time.monotonic() + 5
            output = ''
            while PREFIX not in output:
                assert child.poll() is None, 'tiny debugger exited before barrier'
                assert time.monotonic() < until, 'tiny debugger marker timeout'
                output = h.output_so_far(log).decode()
                time.sleep(.01)
            marker_line = next(line for line in output.splitlines() if PREFIX in line)
            marker = json.loads(marker_line.split(PREFIX, 1)[1])
            assert marker == {'file': str(fixture), 'job_id': 'fake-job',
                'lease_token': 'fake-token', 'in_transaction': False,
                'adapter': 'builtin', 'handler': 'data_record_aggregate',
                'dispatched': True, 'native_query': True}
            descriptor_line = next(line for line in output.splitlines()
                if 'RUNTIME_DESCRIPTOR:' in line)
            runtime = json.loads(descriptor_line.split('RUNTIME_DESCRIPTOR:', 1)[1])
            assert tuple(runtime['python']) == sys.version_info[:3]
            assert Path(runtime['executable']) == python
            assert Path(runtime['pdb_file']).resolve() == Path(pdb.__file__).resolve()
            assert Path(pdb.__file__).resolve().parent == Path(sysconfig.get_path('stdlib')).resolve()
            assert runtime['inherited_fd_closed'] is True
            time.sleep(.15)
            output = h.output_so_far(log).decode()
            assert child.poll() is None
            assert output.rstrip().endswith('(Pdb)')
            assert 'PASSED_FAKE_BOUNDARY' not in output
            assert stat.S_IMODE(os.fstat(log.fileno()).st_mode) == 0o600
            child.stdin.write('continue\n')
            child.stdin.flush()
            child.stdin.close()
            child.wait(timeout=5)
            output = h.output_so_far(log).decode()
            assert 'PASSED_FAKE_BOUNDARY' in output
            assert 'The program exited via sys.exit(). Exit status: 1' in output
            assert child.returncode == 0  # pdb handled fixture SystemExit(1).
            assert hashlib.sha256(output.encode()).digest()
    finally:
        if child is not None:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        os.close(canary_fd)
