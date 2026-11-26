"""Loopback-only synthetic evidence; counts sockets, not model requests."""
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from time import monotonic, sleep

import pytest
import httpx
from tests.synthetic_connection_receipt import ConnectionReceipt
from tests.synthetic_deepseek_loopback_v2 import provider


def port():
    for _ in range(30):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            value = sock.getsockname()[1]
        if value not in (8766, 55486):return value
    raise RuntimeError('no isolated port')


def snapshot(path):
    value=json.loads(path.read_text())
    assert set(value)=={'state','accepted'}
    assert value['state'] in ('starting','running','stopped')
    assert type(value['accepted']) is int
    return value


def wait_count(path, count):
    end=monotonic()+5
    while monotonic()<end:
        if snapshot(path)['accepted']>=count:return
        sleep(.01)
    raise AssertionError('connection count was not persisted')


def test_short_and_incomplete_connections_are_counted_without_contents(tmp_path):
    listen=port(); path=tmp_path/'connections.json'
    with provider(listen,connection_stats=path):
        assert snapshot(path)=={'state':'running','accepted':0}
        for payload in (b'',b'POST /chat/completions HTTP/1.1\r\nAuthorization: secret',
                        b'POST /chat/completions HTTP/1.1\r\nContent-Length: 80\r\n\r\nprivate'):
            with socket.create_connection(('127.0.0.1',listen),timeout=3) as sock:
                if payload:sock.sendall(payload)
        wait_count(path,3)
    assert snapshot(path)=={'state':'stopped','accepted':3}
    assert b'secret' not in path.read_bytes() and b'private' not in path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode)==0o600


def test_concurrent_connections_count_once_each(tmp_path):
    listen=port();path=tmp_path/'connections.json'
    def connect(_):
        with socket.create_connection(('127.0.0.1',listen),timeout=5):pass
    with provider(listen,connection_stats=path):
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(connect,range(24)))
        wait_count(path,24)
    assert snapshot(path)=={'state':'stopped','accepted':24}


def test_completed_post_keeps_socket_count_separate_from_http_receipt(tmp_path):
    listen=port();path=tmp_path/'connections.json';requests=tmp_path/'requests.jsonl'
    with provider(listen,connection_stats=path,request_log=requests) as server:
        with httpx.Client(trust_env=False,timeout=5) as client:
            assert client.post(f'http://127.0.0.1:{listen}/chat/completions',content=b'synthetic').status_code==200
        assert len(server.requests)==1
    assert snapshot(path)=={'state':'stopped','accepted':1}
    assert len(requests.read_text().splitlines())==1


def test_held_partial_request_cannot_claim_clean_shutdown(tmp_path):
    listen=port();path=tmp_path/'connections.json'
    with pytest.raises(RuntimeError,match='receipt incomplete'):
        with provider(listen,connection_stats=path):
            sock=socket.create_connection(('127.0.0.1',listen),timeout=3)
            sock.sendall(b'POST /chat/completions HTTP/1.1\r\n')
            wait_count(path,1)
    try:assert snapshot(path)=={'state':'running','accepted':1}
    finally:sock.close()


def test_crash_leaves_running_and_accepted_count(tmp_path):
    listen=port();path=tmp_path/'connections.json'
    code='''import os,sys,time\nfrom tests.synthetic_deepseek_loopback_v2 import provider\nwith provider(int(sys.argv[1]),connection_stats=sys.argv[2]):\n print('ready',flush=True)\n time.sleep(30)\n'''
    proc=subprocess.Popen([sys.executable,'-u','-c',code,str(listen),str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        assert proc.stdout.readline().strip()=='ready'
        with socket.create_connection(('127.0.0.1',listen),timeout=3):pass
        wait_count(path,1)
        proc.kill();proc.wait(timeout=5)
        assert snapshot(path)=={'state':'running','accepted':1}
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=5)
        proc.stdout.close()


def test_crash_between_accept_and_receipt_leaves_unknown_not_clean(tmp_path):
    listen=port();path=tmp_path/'connections.json'
    code='''import sys,time\nfrom tests.synthetic_deepseek_loopback_v2 import provider\nwith provider(int(sys.argv[1]),connection_stats=sys.argv[2]) as server:\n original=server.connection_receipt._save\n def stall(state,count):\n  if count: print('accepted',flush=True);time.sleep(30)\n  return original(state,count)\n server.connection_receipt._save=stall\n print('ready',flush=True)\n time.sleep(30)\n'''
    proc=subprocess.Popen([sys.executable,'-u','-c',code,str(listen),str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        assert proc.stdout.readline().strip()=='ready'
        with socket.create_connection(('127.0.0.1',listen),timeout=3):
            assert proc.stdout.readline().strip()=='accepted'
            proc.kill();proc.wait(timeout=5)
        assert snapshot(path)=={'state':'running','accepted':0}
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=5)
        proc.stdout.close()


def test_write_failure_never_admits_socket_or_marks_clean(tmp_path,monkeypatch):
    listen=port();path=tmp_path/'connections.json'
    with pytest.raises(RuntimeError,match='receipt incomplete'):
        with provider(listen,connection_stats=path) as server:
            def fail(*_):raise OSError('synthetic disk failure')
            monkeypatch.setattr(server.connection_receipt,'_save',fail)
            with socket.create_connection(('127.0.0.1',listen),timeout=3) as sock:
                sock.settimeout(3)
                try:assert sock.recv(1)==b''
                except ConnectionResetError:pass
    assert snapshot(path)=={'state':'running','accepted':0}


def test_existing_receipt_is_never_overwritten(tmp_path):
    path=tmp_path/'connections.json';path.write_text('retain')
    with pytest.raises(FileExistsError):
        with provider(port(),connection_stats=path):pass
    assert path.read_text()=='retain'


@pytest.mark.parametrize('failure_point',['tempfile','short_write','file_fsync','replace','directory_fsync'])
def test_accept_persistence_failure_is_never_treated_as_clean(tmp_path,monkeypatch,failure_point):
    path=tmp_path/'connections.json';receipt=ConnectionReceipt(path);receipt.ready()
    real_fsync=os.fsync
    if failure_point=='tempfile':
        monkeypatch.setattr('tempfile.mkstemp',lambda *a,**k: (_ for _ in ()).throw(OSError('write')))
    elif failure_point=='short_write':
        real_fdopen=os.fdopen
        @contextmanager
        def short_fdopen(*args,**kwargs):
            with real_fdopen(*args,**kwargs) as stream:
                class ShortWriter:
                    def write(self,value):return stream.write(value[:1])
                    def __getattr__(self,key):return getattr(stream,key)
                yield ShortWriter()
        monkeypatch.setattr(os,'fdopen',short_fdopen)
    elif failure_point=='replace':
        monkeypatch.setattr(os,'replace',lambda *a,**k: (_ for _ in ()).throw(OSError('replace')))
    else:
        def fsync(fd):
            directory=stat.S_ISDIR(os.fstat(fd).st_mode)
            if directory == (failure_point=='directory_fsync'):raise OSError('fsync')
            return real_fsync(fd)
        monkeypatch.setattr(os,'fsync',fsync)
    with pytest.raises(OSError):receipt.accepted()
    assert receipt.failed is True and receipt.stopped() is False
    # A replaced but not directory-synced running snapshot is still unknown.
    assert snapshot(path)['state']=='running'


def test_final_directory_sync_failure_propagates_as_test_failure(tmp_path,monkeypatch):
    listen=port();path=tmp_path/'connections.json';real_fsync=os.fsync
    with pytest.raises(RuntimeError,match='receipt incomplete'):
        with provider(listen,connection_stats=path):
            def fsync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('directory fsync')
                return real_fsync(fd)
            monkeypatch.setattr(os,'fsync',fsync)
    # A visible stopped snapshot alone cannot establish that fsync succeeded.
    assert snapshot(path)['state']=='stopped'


def test_existing_symlink_not_followed(tmp_path):
    target=tmp_path/'target';target.write_text('retain')
    path=tmp_path/'connections.json';path.symlink_to(target)
    with pytest.raises(FileExistsError):
        with provider(port(),connection_stats=path):pass
    assert target.read_text()=='retain'
