import base64
import sys
import threading
import time

import pytest

from generic_agents.dji_cloud2_process import Bridge, BridgeError, BridgeRevoked, init_frame, parse_result


def _b(value):
    return base64.urlsafe_b64encode(value.encode()).rstrip(b'=').decode()


def _child(code):
    return [sys.executable, '-u', '-c', code]


def test_secrets_are_sent_over_stdin_and_real_result_parsed(monkeypatch):
    monkeypatch.setenv('GA_SYNTHETIC_INHERITED_SECRET', 'must-not-reach-child')
    source = """import sys,os
line = sys.stdin.readline()
assert 'sensitive-secret' not in ' '.join(sys.argv)
assert 'GA_SYNTHETIC_INHERITED_SECRET' not in os.environ
assert line.startswith('INIT\\t1\\t')
print('READY\\t1', flush=True)
cmd = sys.stdin.readline().strip().split('\\t')
print('RESULT\\t'+cmd[1]+'\\t1\\tU04tMQ\\t5py65Zy6\\tTTM\\t1', flush=True)
sys.stdin.read()
"""
    stop = threading.Event()
    bridge = Bridge(_child(source), lambda: True, stop, timeout=2)
    try:
        bridge.start(init_frame('127.0.0.1', 443, {'x-token': 'sensitive-secret'}, {'x-ws': 'secret-2'}))
        assert bridge.list_devices() == [{'serial': 'SN-1', 'name': '机场', 'model': 'M3', 'online': True}]
    finally:
        bridge.close()


def test_late_result_is_rejected_after_revocation():
    source = """import sys,time
sys.stdin.readline(); print('READY\\t1',flush=True)
cmd=sys.stdin.readline().strip().split('\\t')
time.sleep(.5); print('RESULT\\t'+cmd[1]+'\\t0',flush=True)
time.sleep(10)
"""
    stop = threading.Event()
    allowed = threading.Event(); allowed.set()
    bridge = Bridge(_child(source), allowed.is_set, stop, timeout=2, interval=.03)
    try:
        bridge.start(init_frame('127.0.0.1', 443, {'h':'v'}, {'h':'v'}))
        threading.Timer(.1, allowed.clear).start()
        started = time.monotonic()
        with pytest.raises(BridgeRevoked): bridge.list_devices()
        assert time.monotonic() - started < 1
    finally:
        bridge.close()


def test_oversized_or_malformed_protocol_is_rejected():
    with pytest.raises(BridgeError): parse_result(b'RESULT\tid\t1001', 'id')
    with pytest.raises(BridgeError): parse_result(b'RESULT\tid\t1\t@@\t\t\t1', 'id')
    with pytest.raises(BridgeError): init_frame('127.0.0.1', 443, {'x': 'a' * 20000}, {})
    with pytest.raises(BridgeError, match='authentication_failed'): parse_result(b'ERROR\tid\tAUTH', 'id')
    with pytest.raises(BridgeError, match='invalid_protocol'): parse_result(b'ERROR\tid\tSENSITIVE', 'id')


def test_auth_store_failure_closes_without_accepting_result():
    source = """import sys,time
sys.stdin.readline(); print('READY\\t1',flush=True)
sys.stdin.readline(); time.sleep(5)
"""
    stop = threading.Event()
    calls = [0]
    def authorize():
        calls[0] += 1
        if calls[0] > 5: raise RuntimeError('sensitive error must not be exposed')
        return True
    bridge = Bridge(_child(source), authorize, stop, timeout=2, interval=.03)
    try:
        bridge.start(init_frame('127.0.0.1', 443, {'h':'v'}, {'h':'v'}))
        with pytest.raises(BridgeRevoked, match='authorization_unavailable'): bridge.list_devices()
    finally:
        bridge.close()


def test_stalled_database_authorization_kills_sdk_child_without_waiting_for_database():
    source = """import sys,time
sys.stdin.readline(); print('READY\\t1',flush=True)
sys.stdin.readline(); time.sleep(10)
"""
    stop = threading.Event()
    stalled = threading.Event(); release = threading.Event()
    def authorize():
        if stalled.is_set(): release.wait(3)
        return True
    bridge = Bridge(_child(source), authorize, stop, timeout=3, interval=.03, authorization_ttl=.4)
    try:
        bridge.start(init_frame('127.0.0.1', 443, {'h':'v'}, {'h':'v'}))
        stalled.set()
        errors = []
        def request():
            try: bridge.list_devices()
            except BridgeError as error: errors.append(error)
        thread = threading.Thread(target=request); thread.start()
        deadline = time.monotonic() + 2
        while bridge.process.poll() is None and time.monotonic() < deadline:
            time.sleep(.03)
        assert bridge.process.poll() is not None
        release.set(); thread.join(2)
        assert errors and isinstance(errors[0], BridgeRevoked)
    finally:
        release.set(); bridge.close()
