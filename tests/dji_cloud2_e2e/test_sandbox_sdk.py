"""Opt-in Linux integration test: patched JNI + TLS + PostgreSQL + sandbox.

The SDK endpoint is a synthetic service in a fresh Docker *internal* network;
this is not a DJI simulator, a production deployment or an external-LAN test.
"""
import base64
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import select as io_select
import subprocess
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.dji_cloud2_connections import acquire_snapshot, is_current, publish_state
from generic_agents.dji_cloud2_process import parse_result
from generic_agents.dji_cloud2_sandbox import DockerSandbox, SandboxSpec
from generic_agents.models import Job, Membership, Workspace
from tests.test_managed_mqtt_grants import vault

pytestmark = pytest.mark.skipif(os.getenv('GA_DJI2_E2E_ENABLE') != '1', reason='explicit isolated Linux Docker E2E opt-in required')
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOCKER = '/usr/bin/docker'
SYNTHETIC_SECRET = 'synthetic-e2e-token'


def docker(args, *, data=None, timeout=90, check=True):
    result = subprocess.run([DOCKER, *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env={'PATH': '/usr/bin:/bin', 'LANG': 'C',
                                                  'DOCKER_HOST': 'unix:///var/run/docker.sock'})
    if check and result.returncode:
        # Docker output can include arbitrary container logs or environment.
        raise AssertionError('isolated Docker operation failed: ' + args[0])
    return result


def tar_context(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content); info.mode = 0o755 if name == 'bridge-launch' else 0o644
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def build_image(tag, dockerfile, files):
    files = {'Dockerfile': dockerfile.encode(), **files}
    result = docker(['build', '--quiet', '--tag', tag, '-'], data=tar_context(files), timeout=300)
    ident = result.stdout.decode().strip().splitlines()[-1]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', ident): raise AssertionError('invalid test image id')
    cfg = json.loads(docker(['image', 'inspect', '--format', '{{json .Config}}', ident]).stdout)
    if cfg.get('Volumes'): raise AssertionError('test image must not declare writable volumes')
    return ident


def choose_subnet():
    listed = docker(['network', 'ls', '--quiet', '--no-trunc']).stdout.decode().splitlines()
    occupied = []
    if listed:
        objects = json.loads(docker(['network', 'inspect', *listed]).stdout)
        for obj in objects:
            for item in (obj.get('IPAM') or {}).get('Config') or []:
                try: occupied.append(ipaddress.ip_network(item.get('Subnet'), strict=False))
                except (TypeError, ValueError): pass
    # An overlapping host route could disrupt unrelated LAN access even when
    # Docker itself has no overlapping network. Fail closed if routes cannot
    # be inspected, and ignore only the unavoidable default route.
    route_result = subprocess.run(['/usr/sbin/ip', '-j', '-4', 'route', 'show', 'table', 'all'],
                                  capture_output=True, timeout=5)
    if route_result.returncode: raise AssertionError('cannot verify host routes')
    for route in json.loads(route_result.stdout):
        dst = route.get('dst', 'default')
        if dst in ('default', '0.0.0.0/0'): continue
        try: occupied.append(ipaddress.ip_network(dst, strict=False))
        except ValueError: raise AssertionError('cannot parse host route') from None
    for second in range(16, 32):
        for third in range(240, 255):
            candidate = ipaddress.ip_network(f'172.{second}.{third}.0/24')
            if not any(n.version == 4 and candidate.overlaps(n) for n in occupied): return candidate
    raise AssertionError('no isolated test subnet without host-route overlap')


class TestOnlySubnetSandbox(DockerSandbox):
    """Only test-network allocation differs; all production checks stay intact."""
    __test__ = False
    def __init__(self, *args, subnet, allow_test_fixtures=False, **kwargs):
        self.test_subnet = subnet
        self.allow_test_fixtures = allow_test_fixtures
        super().__init__(*args, **kwargs)

    def _docker(self, args, timeout=3):
        if args[:2] == ['network', 'create']:
            args = [*args[:-1], '--subnet', str(self.test_subnet), args[-1]]
        return super()._docker(args, timeout=timeout)

    def _test_fixture_members(self, guard_id=None):
        inspected = self._docker(['network', 'inspect', '--format', '{{json .}}', self.network_id])
        if inspected is None or inspected.returncode:
            raise ValueError('network inspection failed')
        members = json.loads(inspected.stdout).get('Containers')
        if not isinstance(members, dict) or guard_id is not None and guard_id not in members:
            raise ValueError('guard missing')
        for identity in set(members) - ({guard_id} if guard_id else set()):
            result = self._docker(['inspect', '--format', '{{json .}}', identity])
            if result is None or result.returncode:
                raise ValueError('fixture inspection failed')
            item = json.loads(result.stdout)
            labels = item['Config'].get('Labels') or {}
            if (item.get('Id') != identity or not item.get('Name', '').startswith('/ga-dji2-e2e-')
                    or labels.get('org.generic-agents.component') != 'dji-cloud2-e2e-fixture'
                    or labels.get('org.generic-agents.lease') != self.guard_name.rsplit('-', 1)[1]):
                raise ValueError('unknown attachment')

    def _verify_network(self, *, require_empty=False):
        if not self.allow_test_fixtures or not require_empty:
            return super()._verify_network(require_empty=require_empty)
        super()._verify_network(require_empty=False)
        self._test_fixture_members()

    def _verify_guard_network_only(self, guard_id):
        if not self.allow_test_fixtures:
            return super()._verify_guard_network_only(guard_id)
        super()._verify_network()
        self._test_fixture_members(guard_id)


def read_line(proc, timeout=20):
    deadline = time.monotonic() + timeout; result = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = io_select.select([proc.stdout], [], [], max(0, deadline - time.monotonic()))
        if not ready: break
        value = os.read(proc.stdout.fileno(), 1)
        if value == b'\n': return bytes(result)
        if not value or len(result) >= 262144: break
        result += value
    raise AssertionError('SDK response timed out or malformed')


def events(name):
    result = docker(['logs', name], check=False)
    encoded = base64.urlsafe_b64encode(('X-Workspace-Token='+SYNTHETIC_SECRET).encode()).rstrip(b'=')
    if SYNTHETIC_SECRET.encode() in result.stdout + result.stderr or encoded in result.stdout + result.stderr:
        raise AssertionError('synthetic credential leaked to fixture logs')
    rows = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: pass
    return rows


def remove_owned_container(name, component, lease):
    result = docker(['inspect', '--format', '{{json .Config.Labels}}', name], check=False)
    if result.returncode:
        names = docker(['ps', '-a', '--format', '{{.Names}}']).stdout.decode().splitlines()
        if name in names: raise AssertionError('cannot inspect owned test container')
        return
    labels = json.loads(result.stdout)
    if labels.get('org.generic-agents.component') != component or labels.get('org.generic-agents.lease') != lease:
        raise AssertionError('refusing to remove container with unexpected ownership')
    docker(['rm', '--force', name])


def remove_owned_network(network_id, name, lease):
    result = docker(['network', 'inspect', '--format', '{{json .}}', network_id], check=False)
    if result.returncode:
        ids = docker(['network', 'ls', '--no-trunc', '--quiet']).stdout.decode().splitlines()
        if network_id in ids: raise AssertionError('cannot inspect owned test network')
        return
    obj = json.loads(result.stdout); labels = obj.get('Labels') or {}
    if (obj.get('Id') != network_id or obj.get('Name') != name or obj.get('Driver') != 'bridge'
            or obj.get('Internal') is not True or labels.get('org.generic-agents.lease') != lease
            or labels.get('org.generic-agents.component') != 'dji-cloud2-test-network'):
        raise AssertionError('refusing to remove network with unexpected ownership')
    docker(['network', 'rm', network_id])


def wait_for(fn, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result: return result
        time.sleep(.1)
    raise AssertionError('isolated scenario did not reach expected state')


@pytest.fixture(scope='module')
def assets(tmp_path_factory):
    if not Path('/proc/1/ns/pid').exists() or not Path(DOCKER).is_file():
        pytest.fail('requires a native Linux host with a local Docker daemon')
    base = os.getenv('GA_DJI2_E2E_BASE_IMAGE', '')
    python_base = os.getenv('GA_DJI2_E2E_PYTHON_IMAGE', '')
    for value in (base, python_base):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
            pytest.fail('test base images must be local immutable sha256 image IDs')
    jar_values = []
    for label in ('SDK', 'CONTROL'):
        jar = Path(os.getenv('GA_DJI2_E2E_'+label+'_JAR', ''))
        expected = os.getenv('GA_DJI2_E2E_'+label+'_SHA256', '')
        if not jar.is_absolute() or not jar.is_file() or not re.fullmatch(r'[0-9a-f]{64}', expected):
            pytest.fail('patched and vulnerable-control test JARs with explicit SHA-256 are required')
        contents = jar.read_bytes()
        if hashlib.sha256(contents).hexdigest() != expected: pytest.fail('test JAR hash mismatch')
        jar_values.append(contents)
    if jar_values[0] == jar_values[1]:
        pytest.fail('patched SDK and intentionally vulnerable control must be distinct files')
    subnet = choose_subnet()
    host, target = str(subnet.network_address + 10), str(subnet.network_address + 11)
    temp = tmp_path_factory.mktemp('dji2-certificate'); key = temp / 'key.pem'; cert = temp / 'cert.pem'
    # The test certificate and private key are ephemeral and never used by a real service.
    openssl = subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-keyout', str(key), '-out', str(cert), '-subj', '/CN='+host,
        '-addext', 'subjectAltName=IP:'+host+',IP:'+target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if openssl.returncode: pytest.fail('could not create ephemeral synthetic TLS certificate')
    tags = ['ga-dji2-e2e-'+uuid4().hex+'-'+kind+':temp' for kind in ('guard', 'sdk', 'control', 'fixture')]
    try:
        guard_file = f'''FROM {python_base}\nRUN apt-get update && apt-get install -y --no-install-recommends iptables && ln -sf /usr/local/bin/python3 /usr/bin/python3 && rm -rf /var/lib/apt/lists/*\n'''
        guard = build_image(tags[0], guard_file, {})
        bridge = (ROOT / 'src/dji_cloud2_bridge/DjiCloud2Bridge.java').read_text()
        original = 'cfg.setVerifySsl(true);'
        if bridge.count(original) != 1: pytest.fail('bridge TLS configuration changed')
        # Only the isolated test image trusts this ephemeral test CA.
        bridge = bridge.replace(original, 'cfg.setSslCaPath("/opt/dji-cloud2/test-ca.pem"); ' + original)
        launch = b'#!/bin/sh\nsh -c \'env -i PATH=/opt/java/openjdk/bin:/usr/bin:/bin LANG=C.UTF-8 java -cp /opt/dji-cloud2/out:/opt/dji-cloud2/sdk.jar DjiCloud2Bridge; code=$?; exit "$code"\'\n'
        sdk_file = f'''FROM {base}\nCOPY DjiCloud2Bridge.java sdk.jar test-ca.pem bridge-launch /opt/dji-cloud2/\nRUN mkdir -p /opt/dji-cloud2/out && javac --release 17 -cp /opt/dji-cloud2/sdk.jar -d /opt/dji-cloud2/out /opt/dji-cloud2/DjiCloud2Bridge.java\n'''
        sdk = build_image(tags[1], sdk_file, {'DjiCloud2Bridge.java': bridge.encode(), 'sdk.jar': jar_values[0],
                                               'test-ca.pem': cert.read_bytes(), 'bridge-launch': launch})
        # Vulnerable control is only started inside the same isolated test
        # sandbox against synthetic endpoints and must never be deployed.
        control = build_image(tags[2], sdk_file, {'DjiCloud2Bridge.java': bridge.encode(), 'sdk.jar': jar_values[1],
                                                   'test-ca.pem': cert.read_bytes(), 'bridge-launch': launch})
        fixture_file = f'''FROM {python_base}\nCOPY fixture_server.py cert.pem key.pem /opt/fixture/\nENTRYPOINT ["python3", "-u", "/opt/fixture/fixture_server.py"]\n'''
        fixture = build_image(tags[3], fixture_file, {'fixture_server.py': (HERE / 'fixture_server.py').read_bytes(),
                                                       'cert.pem': cert.read_bytes(), 'key.pem': key.read_bytes()})
        yield guard, sdk, control, fixture, subnet
    finally:
        for tag in tags:
            docker(['image', 'rm', tag], check=False)


def make_connection(client, host, subnet, monkeypatch, *, http_secret=SYNTHETIC_SECRET, ws_secret=SYNTHETIC_SECRET):
    monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS', str(subnet))
    monkeypatch.setenv('GA_DJI_CLOUD2_ENDPOINTS', host + ':18443')
    response = client.post('/api/dji/cloud2/connections', json={'request_id': str(uuid4()),
        'name': '隔离测试连接', 'host': host, 'port': 18443, 'confirm': True,
        'http_headers': [{'name': 'X-Workspace-Token', 'value': http_secret}],
        'ws_headers': [{'name': 'X-Workspace-Token', 'value': ws_secret}]})
    assert response.status_code == 200
    return response.json()['connection']


def one_scenario(client, account, monkeypatch, assets, *, mode, fault=None, vulnerable=False,
                 http_secret=SYNTHETIC_SECRET, ws_secret=SYNTHETIC_SECRET):
    guard, patched, control, fixture, subnet = assets
    sdk = control if vulnerable else patched
    host, target = str(subnet.network_address + 10), str(subnet.network_address + 11)
    record = make_connection(client, host, subnet, monkeypatch, http_secret=http_secret, ws_secret=ws_secret); workspace = account[2]
    spec = SandboxSpec(workspace_id=workspace, connection_id=record['id'], generation=record['version'],
        host=host, port=18443, policy_digest='a'*64, guard_image=guard, sdk_image=sdk, allowed_networks=(str(subnet),))
    def authorize(_spec):
        with Session(engine()) as db:
            # A broken implementation must time out rather than hang the VM.
            db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            return is_current(db, workspace, record['id'], record['version'])
    sandbox = TestOnlySubnetSandbox(spec, authorize, docker=DOCKER, subnet=subnet, allow_test_fixtures=True)
    names = ['ga-dji2-e2e-source-'+uuid4().hex, 'ga-dji2-e2e-target-'+uuid4().hex]
    lease = sandbox.guard_name.rsplit('-', 1)[1]
    proc = None; outcome = None; journal_id = None
    try:
        # Record identities durably before any Docker create request.
        with Session(engine()) as db:
            journal_id = journal.begin(db, sandbox); db.commit()
        with Session(engine()) as db:
            assert any(row['id'] == journal_id and row['state'] == 'starting' for row in journal.pending(db, workspace))
        sandbox.prepare_network()
        with Session(engine()) as db:
            journal.record_network(db, workspace, journal_id, sandbox); db.commit()
        for name, ip, role in ((names[0], host, 'source'), (names[1], target, 'target')):
            docker(['run', '--detach', '--rm', '--name', name, '--network', sandbox.network_id, '--ip', ip,
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--no-healthcheck', '--read-only',
                '--label', 'org.generic-agents.component=dji-cloud2-e2e-fixture',
                '--label', 'org.generic-agents.lease='+lease,
                '--env', 'FIXTURE_ROLE='+role, '--env', 'FIXTURE_MODE='+mode,
                '--env', 'FIXTURE_TARGET=https://'+target+':18443/capture', fixture])
        for name in names:
            wait_for(lambda: any(item.get('kind') == 'ready' for item in events(name)))
        if vulnerable:
            # Explicit positive control: the exact same vulnerable JAR must
            # leak the synthetic token on this *internal* network without the
            # guard. Never run this container with real credentials or routes.
            control_name = 'ga-dji2-e2e-control-'+uuid4().hex
            command = [DOCKER, 'run', '--rm', '--interactive', '--name', control_name,
                       '--network', sandbox.network_id, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--no-healthcheck', '--log-driver', 'none', '--user', '65534:65534', '--read-only',
                       '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=128m,mode=1777', '--memory', '512m', '--pids-limit', '64',
                       '--label', 'org.generic-agents.component=dji-cloud2-e2e-control',
                       '--label', 'org.generic-agents.lease='+lease,
                       '--entrypoint', '/opt/dji-cloud2/bridge-launch', control]
            control_proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={'PATH':'/usr/bin:/bin', 'LANG':'C', 'DOCKER_HOST':'unix:///var/run/docker.sock'}, bufsize=0)
            try:
                from generic_agents.dji_cloud2_process import init_frame
                control_proc.stdin.write(init_frame(host, 18443, {'X-Workspace-Token': SYNTHETIC_SECRET},
                                                     {'X-Workspace-Token': SYNTHETIC_SECRET})); control_proc.stdin.flush()
                assert read_line(control_proc) == b'READY\t1'
                control_proc.stdin.write(('LIST\t'+str(uuid4())+'\n').encode()); control_proc.stdin.flush()
                wait_for(lambda: any(item.get('synthetic_auth_seen') for item in events(names[1])), timeout=25)
            finally:
                remove_owned_container(control_name, 'dji-cloud2-e2e-control', lease)
                try: control_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    control_proc.kill(); control_proc.wait(timeout=5)
                for stream in (control_proc.stdin, control_proc.stdout):
                    if stream: stream.close()
            # Drain any request already accepted before removing the control.
            time.sleep(.5)
            before = len(events(names[1]))
        else:
            before = len(events(names[1]))
        with Session(engine()) as db:
            snapshot = acquire_snapshot(db, workspace, record['id']); db.commit()
            saved = db.get(Job, record['id'])
            assert SYNTHETIC_SECRET not in json.dumps(saved.data)
            other = Workspace(name='独立工作区'); db.add(other); db.commit(); other_id = other.id
        with Session(engine()) as db:
            with pytest.raises(HTTPException) as denied: acquire_snapshot(db, other_id, record['id'])
            assert denied.value.status_code == 404
        proc = sandbox.start()
        sandbox.preflight_sdk()
        if fault == 'revoke_before_init':
            response = client.post('/api/dji/cloud2/connections/'+record['id']+'/revoke', json={
                'request_id': str(uuid4()), 'version': record['version'], 'confirm': True})
            assert response.status_code == 200
            with pytest.raises(HTTPException): journal.send_init(workspace, journal_id, sandbox)
            assert not [item for item in events(names[0]) if item.get('kind') != 'ready']
            return
        if fault == 'revoke_during_init':
            lock = int.from_bytes(hashlib.sha256(('dji-cloud2\0'+workspace).encode()).digest()[:8], 'big', signed=False)
            pending = None
            with ThreadPoolExecutor(max_workers=1) as pool:
                original_send = sandbox.send_init
                def competing_send(frame, timeout=3):
                    nonlocal pending
                    pending = pool.submit(client.post, '/api/dji/cloud2/connections/'+record['id']+'/revoke',
                        json={'request_id': str(uuid4()), 'version': record['version'], 'confirm': True})
                    def writer_waiting():
                        with Session(engine()) as check:
                            return check.scalar(text('''SELECT EXISTS (SELECT 1 FROM pg_locks
                                WHERE locktype='advisory' AND NOT granted AND objsubid=1
                                AND classid=:high AND objid=:low)'''),
                                {'high': lock >> 32, 'low': lock & 0xffffffff})
                    wait_for(writer_waiting, timeout=3)
                    return original_send(frame, timeout)
                sandbox.send_init = competing_send
                journal.send_init(workspace, journal_id, sandbox)
                assert pending is not None
                assert pending.result(timeout=8).status_code == 200
            with Session(engine()) as db:
                assert not publish_state(db, workspace, record['id'], record['version'], 'connected', 0)
            return
        journal.send_init(workspace, journal_id, sandbox)
        sandbox.read_sdk_ready()
        with Session(engine()) as db:
            journal.mark_active(db, workspace, journal_id, sandbox); db.commit()
        rid = str(uuid4()); proc.stdin.write(('LIST\t'+rid+'\n').encode()); proc.stdin.flush()
        wait_for(lambda: any(x.get('kind') == 'http' and x.get('synthetic_auth_seen') == (http_secret == SYNTHETIC_SECRET)
                             for x in events(names[0])))
        if fault == 'revoke':
            response = client.post('/api/dji/cloud2/connections/'+record['id']+'/revoke', json={
                'request_id': str(uuid4()), 'version': record['version'], 'confirm': True})
            assert response.status_code == 200
            wait_for(lambda: sandbox._failed.is_set())
            with Session(engine()) as db:
                assert not publish_state(db, workspace, record['id'], record['version'], 'connected', 0)
            # Local revocation and eventual process termination do not imply a
            # positive guard fence receipt; that outcome must remain unknown.
        elif fault == 'guard_death':
            docker(['kill', sandbox.guard_name])
            wait_for(lambda: sandbox._failed.is_set())
            wait_for(lambda: docker(['inspect', sandbox.sdk_name], check=False).returncode != 0)
        elif fault == 'role_restore':
            with Session(engine()) as db:
                member = db.scalar(select(Membership).where(Membership.workspace_id == workspace))
                member.role = 'viewer'; db.flush(); member.role = 'admin'; db.commit()
            with Session(engine()) as db:
                assert not is_current(db, workspace, record['id'], record['version'])
                db.rollback()
                assert not publish_state(db, workspace, record['id'], record['version'], 'connected', 0)
            wait_for(lambda: sandbox._failed.is_set())
        else:
            line = read_line(proc)
            if mode == 'normal' and http_secret == SYNTHETIC_SECRET:
                assert parse_result(line, rid) == []
                with Session(engine()) as db:
                    assert publish_state(db, workspace, record['id'], record['version'], 'connected', 0); db.commit()
                if ws_secret != SYNTHETIC_SECRET:
                    wait_for(lambda: any(item.get('kind') == 'unauthorized' for item in events(names[0])))
            else:
                assert line.startswith(('ERROR\t'+rid+'\t').encode())
                if http_secret != SYNTHETIC_SECRET:
                    assert line == ('ERROR\t'+rid+'\tAUTH').encode()
                assert len(events(names[1])) == before, 'redirect reached the unapproved endpoint'
    finally:
        if sandbox.network_id:
            for name in names:
                docker(['network', 'disconnect', '--force', sandbox.network_id, name], check=False)
        outcome = sandbox.stop()
        for name in names: remove_owned_container(name, 'dji-cloud2-e2e-fixture', lease)
        if journal_id:
            with Session(engine()) as db:
                journal_state = journal.mark_stop(db, workspace, journal_id, sandbox, outcome); db.commit()
            with Session(engine()) as db:
                outstanding = [row for row in journal.pending(db, workspace) if row['id'] == journal_id]
                assert (not outstanding) if journal_state == 'closed' else (outstanding[0]['state'] == 'cleanup_unverified')
        if not outcome.network_removed or not outcome.containers_stopped:
            raise AssertionError('test cleanup unverified; inspect only resources '+sandbox.network_name+' '+sandbox.guard_name+' '+sandbox.sdk_name)
    if not fault:
        assert outcome.confirmed, 'sandbox termination or network fence could not be confirmed'
    else:
        assert not outcome.confirmed, 'automatic failure must not be mistaken for an acknowledged fence'


@pytest.mark.parametrize('options', [
    pytest.param({'mode':'normal'}, id='patched-sdk-read'),
    pytest.param({'mode':'redirect'}, id='patched-redirect-blocked'),
    pytest.param({'mode':'redirect','vulnerable':True}, id='guard-blocks-proven-vulnerable-control'),
    pytest.param({'mode':'delayed','fault':'revoke'}, id='revoke-during-request'),
    pytest.param({'mode':'delayed','fault':'guard_death'}, id='guard-killed'),
    pytest.param({'mode':'delayed','fault':'role_restore'}, id='role-revocation-cannot-revive'),
    pytest.param({'mode':'normal','fault':'revoke_before_init'}, id='revoke-before-init'),
    pytest.param({'mode':'normal','fault':'revoke_during_init'}, id='revoke-queues-behind-init'),
    pytest.param({'mode':'normal','http_secret':'synthetic-wrong-http'}, id='wrong-http-auth'),
    pytest.param({'mode':'normal','ws_secret':'synthetic-wrong-ws'}, id='wrong-websocket-auth'),
])
def test_sdk_sandbox_scenario(client, account, vault, monkeypatch, assets, options):
    one_scenario(client, account, monkeypatch, assets, **options)


def test_unknown_network_create_outcome_stays_blocked(client, account, vault, monkeypatch, assets):
    guard, sdk, _control, _fixture, subnet = assets
    host = str(subnet.network_address + 10)
    record = make_connection(client, host, subnet, monkeypatch); workspace = account[2]
    spec = SandboxSpec(workspace_id=workspace, connection_id=record['id'], generation=record['version'],
        host=host, port=18443, policy_digest='a'*64, guard_image=guard, sdk_image=sdk, allowed_networks=(str(subnet),))
    sandbox = TestOnlySubnetSandbox(spec, lambda _: True, docker=DOCKER, subnet=subnet)
    with Session(engine()) as db:
        journal_id = journal.begin(db, sandbox); db.commit()
    # Model a create request with no trustworthy response. Even a later
    # negative listing cannot close an unknown result.
    with Session(engine()) as db:
        assert journal.mark_unverified(db, workspace, journal_id) == 'cleanup_unverified'
        db.commit()
    with Session(engine()) as db:
        other = TestOnlySubnetSandbox(spec, lambda _: True, docker=DOCKER, subnet=subnet)
        with pytest.raises(HTTPException) as blocked: journal.begin(db, other)
        assert blocked.value.status_code == 409


def test_supervisor_sigkill_preserves_journal_and_blocks_automatic_restart(client, account, vault, monkeypatch, assets):
    guard, sdk, _control, fixture, subnet = assets
    host = str(subnet.network_address + 10)
    record = make_connection(client, host, subnet, monkeypatch); workspace = account[2]
    payload = {'workspace_id': workspace, 'connection_id': record['id'], 'generation': record['version'],
               'host': host, 'guard_image': guard, 'sdk_image': sdk, 'subnet': str(subnet)}
    safe_env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT),
                'GA_DATABASE_URL':os.environ['GA_DATABASE_URL'],
                'GA_REDIS_URL':os.environ['GA_REDIS_URL'],
                'GA_FILE_GATEWAY_KEY_FILE':os.environ['GA_FILE_GATEWAY_KEY_FILE'],
                'GA_MANAGED_GATEWAY_NETWORKS':str(subnet), 'GA_DJI_CLOUD2_ENDPOINTS':host+':18443'}
    worker = subprocess.Popen([sys.executable, str(HERE/'worker.py'), json.dumps(payload)], cwd=ROOT, env=safe_env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    intent = None; network_id = None; fixture_name = 'ga-dji2-e2e-source-'+uuid4().hex
    try:
        intent = json.loads(read_line(worker, timeout=30)); assert intent['event'] == 'intent'
        net = json.loads(read_line(worker, timeout=30)); assert net['event'] == 'network'
        network_id = net['network_id']; lease = intent['guard'].rsplit('-', 1)[1]
        docker(['run', '--detach', '--rm', '--name', fixture_name, '--network', network_id, '--ip', host,
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--no-healthcheck', '--read-only',
                '--label', 'org.generic-agents.component=dji-cloud2-e2e-fixture',
                '--label', 'org.generic-agents.lease='+lease,
                '--env', 'FIXTURE_ROLE=source', '--env', 'FIXTURE_MODE=normal', fixture])
        wait_for(lambda: any(item.get('kind') == 'ready' for item in events(fixture_name)))
        worker.stdin.write(b'GO\n'); worker.stdin.flush()
        assert json.loads(read_line(worker, timeout=40))['event'] == 'active'
        assert any(item.get('kind') == 'http' and item.get('synthetic_auth_seen') for item in events(fixture_name))
        worker.kill(); worker.wait(timeout=5)
        # A newly opened DB connection represents a restarted supervisor; it
        # must see the prior resource intent and refuse another connection.
        with Session(engine()) as db:
            pending = [row for row in journal.pending(db, workspace) if row['id'] == intent['journal_id']]
            assert pending and pending[0]['state'] == 'active'
            spec = SandboxSpec(workspace_id=workspace, connection_id=record['id'], generation=record['version'],
                host=host, port=18443, policy_digest='a'*64, guard_image=guard, sdk_image=sdk, allowed_networks=(str(subnet),))
            other = TestOnlySubnetSandbox(spec, lambda _: True, docker=DOCKER, subnet=subnet)
            with pytest.raises(HTTPException) as blocked: journal.begin(db, other)
            assert blocked.value.status_code == 409
            db.rollback()
        revoked = client.post('/api/dji/cloud2/connections/'+record['id']+'/revoke', json={
            'request_id': str(uuid4()), 'version': record['version'], 'confirm': True})
        assert revoked.status_code == 200
    finally:
        if worker.poll() is None:
            worker.kill(); worker.wait(timeout=5)
        if intent and network_id:
            lease = intent['guard'].rsplit('-', 1)[1]
            remove_owned_container(fixture_name, 'dji-cloud2-e2e-fixture', lease)
            # Recovery runs in a fresh process, which discovers resources from
            # PostgreSQL and independently verifies daemon and ownership.
            recovery_data = {**payload, 'journal_id': intent['journal_id']}
            recovery = subprocess.run([sys.executable, str(HERE/'recover.py'), json.dumps(recovery_data)],
                                      cwd=ROOT, env=safe_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      timeout=35)
            if recovery.returncode:
                # Test-only cleanup prevents leaked fixtures on a failing run,
                # still verifies identity, and must never hide the failure.
                remove_owned_container(intent['sdk'], 'dji-cloud2-sdk', lease)
                remove_owned_container(intent['guard'], 'dji-cloud2-egress', lease)
                remove_owned_network(network_id, intent['network'], lease)
                raise AssertionError('fresh supervisor recovery failed; journal requires investigation')
            assert json.loads(recovery.stdout)['state'] == 'cleanup_unverified'
            assert json.loads(recovery.stdout)['outcome'] == 'resources_absent_unverified'
            with Session(engine()) as db:
                assert any(row['id'] == intent['journal_id'] and row['state'] == 'cleanup_unverified'
                           for row in journal.pending(db, workspace))
                assert not is_current(db, workspace, record['id'], record['version'])
                with pytest.raises(HTTPException) as blocked:
                    journal.begin(db, other)
                assert blocked.value.status_code == 403
