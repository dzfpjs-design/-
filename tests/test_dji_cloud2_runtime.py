"""Real PostgreSQL and child-process lifecycle; child is not a DJI simulator."""
import sys
import threading
import time
from uuid import uuid4

from generic_agents import dji_cloud2_runtime as runtime
from tests.test_dji_cloud2_connections import allowed, payload
from tests.test_managed_mqtt_grants import vault


def test_real_sdk_is_hard_disabled_until_redirect_egress_is_confined(monkeypatch):
    monkeypatch.setenv('GA_DJI_CLOUD2_JAVA', '/bin/true')
    monkeypatch.setenv('GA_DJI_CLOUD2_BRIDGE_DIR', '/tmp')
    monkeypatch.setenv('GA_DJI_CLOUD2_SDK_JAR', '/tmp/fake.jar')
    assert runtime._command() is None


def _wait(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value: return value
        time.sleep(.05)
    raise AssertionError('timed out waiting for expected state')


def test_child_restart_and_connection_revocation(client, account, allowed, tmp_path):
    created = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    counter = tmp_path / 'starts'
    source = r'''import pathlib,sys,time
p=pathlib.Path(sys.argv[1]); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))
sys.stdin.readline()
if n == 1: sys.exit(7)
print('READY\t1',flush=True)
while True:
 line=sys.stdin.readline()
 if not line: break
 parts=line.strip().split('\t')
 if parts[0]=='LIST': print('RESULT\t'+parts[1]+'\t1\tU04\t\tTTM\t1',flush=True)
'''
    command = [sys.executable, '-u', '-c', source, str(counter)]
    stop = threading.Event()
    thread = threading.Thread(target=runtime._run_connection, args=(account[2], created['id'], stop),
                              kwargs={'command_factory': lambda: command}, daemon=True)
    thread.start()
    try:
        def connected():
            response = client.get('/api/dji/cloud2/connections').json()['connections'][0]
            return response if response['status'] == 'connected' else None
        live = _wait(connected)
        assert int(counter.read_text()) >= 2
        assert live['device_count'] == 1
        revoked = client.post('/api/dji/cloud2/connections/'+created['id']+'/revoke', json={
            'request_id':str(uuid4()), 'version':created['version'], 'confirm':True})
        assert revoked.status_code == 200
        thread.join(timeout=4)
        assert not thread.is_alive()
        current = client.get('/api/dji/cloud2/connections').json()['connections'][0]
        assert current['status'] == 'revoked' and current['device_count'] is None
    finally:
        stop.set(); thread.join(timeout=4)


def test_revocation_while_sdk_response_is_in_flight(client, account, allowed, tmp_path):
    created = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    in_flight = tmp_path / 'in_flight'
    source = r'''import pathlib,sys,time
sys.stdin.readline(); print('READY\t1',flush=True)
parts=sys.stdin.readline().strip().split('\t'); pathlib.Path(sys.argv[1]).touch()
time.sleep(2); print('RESULT\t'+parts[1]+'\t0',flush=True); time.sleep(5)
'''
    command = [sys.executable, '-u', '-c', source, str(in_flight)]
    stop = threading.Event()
    thread = threading.Thread(target=runtime._run_connection, args=(account[2], created['id'], stop),
                              kwargs={'command_factory': lambda: command}, daemon=True)
    thread.start()
    try:
        _wait(in_flight.exists)
        revoked = client.post('/api/dji/cloud2/connections/'+created['id']+'/revoke', json={
            'request_id':str(uuid4()), 'version':created['version'], 'confirm':True})
        assert revoked.status_code == 200
        thread.join(timeout=4)
        assert not thread.is_alive()
        current = client.get('/api/dji/cloud2/connections').json()['connections'][0]
        assert current['status'] == 'revoked' and current['device_count'] is None
    finally:
        stop.set(); thread.join(timeout=4)
