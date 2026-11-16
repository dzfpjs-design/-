import dataclasses
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from generic_agents.dji_cloud2_sandbox import DockerSandbox, SandboxError, SandboxSpec, _GUARD


@pytest.fixture(autouse=True)
def fake_daemon_for_unit_tests(monkeypatch):
    # This file tests orchestration with fake Docker. Target-runtime identity
    # validation is a separate, fail-closed Linux integration requirement.
    monkeypatch.setattr(DockerSandbox, '_verify_daemon_identity', lambda _: None)


def spec(**changes):
    value = SandboxSpec(workspace_id='0f1d5e12-46c8-4a15-9204-22a0db00e055',
                        connection_id='f914bc2f-54a6-4dc6-9d73-a331a982ac54', generation=1,
                        host='10.1.2.3', port=443, policy_digest='a'*64,
                        guard_image='sha256:'+'b'*64, sdk_image='sha256:'+'c'*64,
                        allowed_networks=('10.1.2.3/32',))
    return dataclasses.replace(value, **changes)


class FakeDocker:
    def __init__(self, respond=True):
        self.processes = {}
        self.commands = []
        self.respond = respond
        self.daemon_up = True
        self.network = False
        self.network_name = ''
        self.driver = 'bridge'

    def popen(self, args, **kwargs):
        self.commands.append(args)
        name = args[args.index('--name')+1]
        if 'guard' in name:
            reply = "print('FENCED',flush=True)" if self.respond else 'pass'
            source = "import sys\nprint('BOOT',flush=True)\nfor x in sys.stdin:\n if x.strip()=='ARM': print('READY',flush=True)\n if x.strip()=='STOP':\n  " + reply + "\n  break\n"
        else:
            source = "import sys\nsys.stdin.buffer.read()\n"
        proc = subprocess.Popen([sys.executable, '-u', '-c', source], **kwargs)
        self.processes[name] = proc
        return proc

    def run(self, args, **kwargs):
        self.commands.append(args)
        if not self.daemon_up:
            raise subprocess.TimeoutExpired(args, 3)
        if args[1:3] == ['network', 'create']:
            self.network = True; self.network_name = args[-1]
            return subprocess.CompletedProcess(args, 0, b'd'*64)
        if args[1:3] == ['network', 'inspect']:
            name = self.network_name
            obj = {'Id':'d'*64,'Name':name,'Driver':self.driver,'Scope':'local','Internal':True,
                   'Containers':{},'Labels':{'org.generic-agents.component':'dji-cloud2-test-network',
                                              'org.generic-agents.lease':name.rsplit('-',1)[1]}}
            return subprocess.CompletedProcess(args,0,json.dumps(obj).encode())
        if args[1] == 'inspect':
            obj = {'State':{'Running':True,'Pid':123},
                   'HostConfig':{'NetworkMode':'d'*64,'Privileged':False,'CapAdd':['NET_ADMIN'],'CapDrop':['ALL']},
                   'NetworkSettings':{'Networks':{self.network_name:{'NetworkID':'d'*64}}}}
            return subprocess.CompletedProcess(args,0,json.dumps(obj).encode())
        if args[1:3] == ['network', 'rm']:
            self.network = False
        if args[1:3] == ['network', 'ls']:
            return subprocess.CompletedProcess(args,0,b'd'*64 if self.network else b'')
        if args[1] == 'kill':
            proc = self.processes.get(args[-1])
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        out = b''
        if args[1] == 'ps':
            out = ('\n'.join(name for name,p in self.processes.items() if p.poll() is None)).encode()
        return subprocess.CompletedProcess(args, 0, out)


def test_spec_fails_closed_and_accepts_an_explicit_32_host_ceiling():
    spec().validate()
    for overrides in ({'host':'127.0.0.1'}, {'host':'10.1.2.4'}, {'host':'example.test'},
                      {'port':True}, {'generation':0}, {'guard_image':'untrusted:latest'},
                      {'docker_network_id':'d'*64}, {'allowed_networks':()}):
        with pytest.raises(SandboxError): spec(**overrides).validate()


def test_launch_order_is_guard_then_sdk_and_fenced_stop(monkeypatch):
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    guard, sdk = [cmd for cmd in fake.commands if len(cmd)>1 and cmd[1]=='run']
    assert guard[guard.index('--name')+1] == sandbox.guard_name
    assert sdk[sdk.index('--network')+1] == 'container:'+sandbox.guard_name
    assert '--cap-add' not in sdk and sdk[sdk.index('--cap-drop')+1] == 'ALL'
    assert '--read-only' in sdk and '65534:65534' in sdk
    assert '/var/run/docker.sock' not in ' '.join(sdk)
    assert all(x not in sandbox._env() for x in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'))
    result = sandbox.stop()
    assert result.confirmed and result.network_fenced and result.containers_stopped


def test_missing_fence_ack_and_docker_failure_are_not_success(monkeypatch):
    fake = FakeDocker(respond=False)
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    result = sandbox.stop()
    assert not result.confirmed and not result.network_fenced
    assert result.containers_stopped

    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    fake.daemon_up = False
    result = sandbox.stop()
    assert result.network_fenced and not result.containers_stopped and not result.confirmed
    for p in fake.processes.values():
        if p.poll() is None:
            p.kill(); p.wait(timeout=2)


def test_authorization_revocation_stops_lease_and_kills_sdk(monkeypatch):
    fake = FakeDocker()
    authorized = threading.Event(); authorized.set()
    sandbox = DockerSandbox(spec(), lambda _: authorized.is_set(), docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    authorized.clear()
    assert sandbox._failed.wait(3)
    end = time.monotonic()+3
    while fake.processes[sandbox.sdk_name].poll() is None and time.monotonic()<end:
        time.sleep(.02)
    assert fake.processes[sandbox.sdk_name].poll() is not None
    # EOF-triggered fence has no management acknowledgement: report unknown.
    assert not sandbox.stop().confirmed


def test_guard_death_is_monitored_even_if_authorization_blocks(monkeypatch):
    fake = FakeDocker()
    block = threading.Event(); entered = threading.Event(); release = threading.Event()
    def authorize(_):
        if block.is_set():
            entered.set(); release.wait(5)
        return True
    sandbox = DockerSandbox(spec(), authorize, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    try:
        block.set()
        assert entered.wait(2)
        fake.processes[sandbox.guard_name].kill()
        assert sandbox._failed.wait(2)
        deadline = time.monotonic()+2
        while fake.processes[sandbox.sdk_name].poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert fake.processes[sandbox.sdk_name].poll() is not None
    finally:
        release.set()
        assert not sandbox.stop().confirmed


def test_namespace_verification_failure_never_arms_or_starts_sdk(monkeypatch):
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    def refuse(): raise ValueError('not proven')
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', refuse)
    with pytest.raises(SandboxError): sandbox.start()
    launches = [cmd for cmd in fake.commands if len(cmd)>1 and cmd[1]=='run']
    assert len(launches)==1 and sandbox.guard_name in launches[0]


def test_uncertain_network_creation_is_not_reported_removed():
    fake = FakeDocker(); fake.daemon_up = False
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run)
    with pytest.raises(SandboxError, match='network_create_unconfirmed'): sandbox.prepare_network()
    assert not sandbox.stop().network_removed


def test_host_network_or_shared_namespace_never_arms_guard(monkeypatch):
    fake = FakeDocker(); fake.driver = 'host'
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run)
    with pytest.raises(SandboxError, match='network_verification_failed'): sandbox.start()
    assert not [cmd for cmd in fake.commands if len(cmd)>1 and cmd[1]=='run']

    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run)
    real_stat = __import__('os').stat
    def stat(path, *args, **kwargs):
        if str(path).startswith('/proc/'):
            return SimpleNamespace(st_dev=1, st_ino=2)
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr('generic_agents.dji_cloud2_sandbox.os.stat', stat)
    with pytest.raises(SandboxError): sandbox.start()
    launches = [cmd for cmd in fake.commands if len(cmd)>1 and cmd[1]=='run']
    assert len(launches)==1 and sandbox.guard_name in launches[0]


def test_guard_eof_or_stop_before_arm_never_calls_firewall():
    # Substitute nonexistent executables: even a regression cannot touch the
    # host firewall, and an attempted pre-ARM call returns 91 rather than 89.
    source = _GUARD.replace('/usr/sbin/iptables-nft-restore','/nonexistent/iptables').replace('/usr/sbin/ip6tables-nft-restore','/nonexistent/ip6tables')
    for value in (b'', b'STOP\n'):
        result = subprocess.run([sys.executable, '-u', '-c', source, '10.1.2.3', '443', '1'],
                                input=value, capture_output=True, timeout=3)
        assert result.returncode == 89 and result.stdout == b'BOOT\n'
