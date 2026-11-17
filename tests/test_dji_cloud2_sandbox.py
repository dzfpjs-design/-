import dataclasses
import json
import os
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
        self.internal = True
        self.network_name = ''
        self.driver = 'bridge'
        self.members = {}

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
            self.network = True; self.network_name = args[-1]; self.internal = '--internal' in args
            return subprocess.CompletedProcess(args, 0, b'd'*64)
        if args[1:3] == ['network', 'inspect']:
            name = self.network_name
            obj = {'Id':'d'*64,'Name':name,'Driver':self.driver,'Scope':'local','Internal':self.internal,
                   'Containers':self.members,'Labels':{'org.generic-agents.component':'dji-cloud2-test-network',
                                              'org.generic-agents.lease':name.rsplit('-',1)[1]}}
            return subprocess.CompletedProcess(args,0,json.dumps(obj).encode())
        if args[1] == 'inspect':
            requested = args[-1]
            is_guard = requested == '1'*64 or 'guard' in requested
            matching = [name for name in self.processes if ('guard' in name) == is_guard]
            name = matching[0] if matching else requested
            component = 'dji-cloud2-egress' if is_guard else 'dji-cloud2-sdk'
            obj = {'State':{'Running':True,'Pid':123},
                   'Id': ('1' if is_guard else '2')*64, 'Name':'/'+name,
                   'Image':spec().guard_image if is_guard else spec().sdk_image,
                   'Config':{'Labels':{'org.generic-agents.component':component,
                                       'org.generic-agents.lease':name.rsplit('-',1)[1]}},
                   'HostConfig':{'NetworkMode':'d'*64,'Privileged':False,'CapAdd':['NET_ADMIN'],'CapDrop':['ALL']},
                   'NetworkSettings':{'Networks':{self.network_name:{'NetworkID':'d'*64}}}}
            return subprocess.CompletedProcess(args,0,json.dumps(obj).encode())
        if args[1:3] == ['network', 'rm']:
            self.network = False
        if args[1:3] == ['network', 'ls']:
            return subprocess.CompletedProcess(args,0,b'd'*64 if self.network else b'')
        if args[1] == 'kill':
            names = [name for name in self.processes if ('guard' in name) == (args[-1] == '1'*64)]
            proc = self.processes.get(names[0]) if names else None
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        out = b''
        if args[1] == 'ps':
            if '{{.ID}}' in args:
                out = ('\n'.join(('1' if 'guard' in name else '2')*64
                                 for name,p in self.processes.items() if p.poll() is None)).encode()
            else:
                out = ('\n'.join(name for name,p in self.processes.items() if p.poll() is None)).encode()
        return subprocess.CompletedProcess(args, 0, out)


def test_no_kill_for_unknown_or_replaced_container():
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', run=fake.run)
    sandbox._kill_owned('sdk')
    assert not any(cmd[1] == 'kill' for cmd in fake.commands)
    sandbox._preflight_sdk_identity = '2'*64
    fake.processes['replacement-other'] = SimpleNamespace(poll=lambda: None)
    sandbox._kill_owned('sdk')
    assert not any(cmd[1] == 'kill' for cmd in fake.commands)


def test_renamed_owned_container_never_confirms_stop():
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', run=fake.run)
    sandbox._sdk_create_attempted = True
    sandbox._preflight_sdk_identity = '2'*64
    fake.processes['renamed-other'] = SimpleNamespace(poll=lambda: None)
    result = sandbox.stop()
    assert not result.containers_stopped and not result.confirmed
    assert not any(cmd[1] == 'kill' for cmd in fake.commands)


def test_start_rejects_unexpected_network_attachment(monkeypatch):
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run)
    sandbox.prepare_network()
    fake.members = {'f'*64: {'Name':'stranger'}}
    with pytest.raises(SandboxError):
        sandbox.start()
    assert not any(cmd[1] == 'run' for cmd in fake.commands)


def test_guard_network_rejects_unexpected_attachment():
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', run=fake.run)
    sandbox.prepare_network()
    fake.members = {'1'*64: {}, 'f'*64: {}}
    with pytest.raises(ValueError):
        sandbox._verify_guard_network_only('1'*64)


def test_cancel_during_authorization_cannot_become_allowed():
    entered, release, results = threading.Event(), threading.Event(), []
    def authorize(_):
        entered.set(); assert release.wait(5); return True
    sandbox = DockerSandbox(spec(), authorize, docker='/usr/bin/docker')
    thread = threading.Thread(target=lambda: results.append(sandbox._allowed()))
    thread.start()
    assert entered.wait(2)
    sandbox._stop.set()
    release.set(); thread.join(5)
    assert results == [False]


@pytest.mark.parametrize('bad_list', ['containers', 'networks'])
def test_malformed_docker_id_listing_is_unverified(bad_list):
    fake = FakeDocker()
    def run(args, **kwargs):
        if (bad_list == 'containers' and args[1:3] == ['ps', '-a'] and '{{.ID}}' in args
                or bad_list == 'networks' and args[1:3] == ['network', 'ls']):
            return subprocess.CompletedProcess(args, 0, b'truncated-or-corrupt\xff\n')
        return fake.run(args, **kwargs)
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', run=run)
    if bad_list == 'networks':
        sandbox._network_create_attempted = True
        sandbox._owned_network_id = 'd'*64
    outcome = sandbox.stop()
    if bad_list == 'containers': assert not outcome.containers_stopped
    else: assert not outcome.network_removed


@pytest.mark.parametrize('blocked_step', ['network', 'guard', 'sdk'])
def test_cancel_during_create_cannot_report_success_or_later_send_credentials(monkeypatch, blocked_step):
    fake = FakeDocker()
    entered, release = threading.Event(), threading.Event()
    def run(args, **kwargs):
        if blocked_step == 'network' and args[1:3] == ['network', 'create']:
            entered.set(); assert release.wait(8)
        return fake.run(args, **kwargs)
    def popen(args, **kwargs):
        if blocked_step in args[args.index('--name')+1]:
            entered.set(); assert release.wait(8)
        return fake.popen(args, **kwargs)
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=popen, run=run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    errors = []
    def launch():
        try: sandbox.start()
        except SandboxError as exc: errors.append(exc)
    thread = threading.Thread(target=launch)
    thread.start()
    try:
        assert entered.wait(5)
        outcome = sandbox.stop()
        assert not outcome.confirmed
        release.set(); thread.join(10)
        assert not thread.is_alive() and errors
        assert not sandbox._init_attempted
        if blocked_step in ('network', 'guard'):
            assert not any(cmd[1] == 'run' and sandbox.sdk_name in cmd for cmd in fake.commands)
    finally:
        release.set(); thread.join(10)
        for proc in fake.processes.values():
            if proc.poll() is None: proc.kill(); proc.wait(timeout=2)


def test_sdk_preflight_required_and_identity_must_remain_the_same(monkeypatch):
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    sandbox.guard = SimpleNamespace(poll=lambda: None)
    sandbox.sdk = subprocess.Popen([sys.executable, '-u', '-c',
        "import time; print('READY\\t1',flush=True); time.sleep(10)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        with pytest.raises(SandboxError): sandbox.read_sdk_ready(timeout=.2)
        sandbox._last_guard_identity = 'f'*64
        monkeypatch.setattr(sandbox, '_inspect_sdk_identity', lambda: 'a'*64)
        sandbox.preflight_sdk(timeout=1)
        sandbox.send_init(b'INIT\t1\tdGVzdA\n')
        monkeypatch.setattr(sandbox, '_inspect_sdk_identity', lambda: 'b'*64)
        with pytest.raises(SandboxError): sandbox.read_sdk_ready(timeout=1)
        with pytest.raises(SandboxError): sandbox.verify_sdk_ready()
    finally:
        sandbox.sdk.terminate(); sandbox.sdk.wait(timeout=2)
        sandbox.sdk.stdin.close(); sandbox.sdk.stdout.close()


def test_preflight_cannot_be_satisfied_by_running_cli_alone(monkeypatch):
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    sandbox.sdk = SimpleNamespace(poll=lambda: None)
    def rejected(): raise SandboxError('sdk_identity_unverified')
    monkeypatch.setattr(sandbox, '_inspect_sdk_identity', rejected)
    with pytest.raises(SandboxError): sandbox.preflight_sdk(timeout=.05)
    assert sandbox._preflight_sdk_identity is None


def test_concurrent_init_is_one_shot_and_bounded():
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    sandbox.guard = SimpleNamespace(poll=lambda: None)
    sandbox.sdk = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(10)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    sandbox._preflight_sdk_identity = 'a'*64
    errors = []
    try:
        fd = sandbox.sdk.stdin.fileno()
        os.set_blocking(fd, False)
        while True:
            try: os.write(fd, b'x'*4096)
            except BlockingIOError: break
        def first():
            try: sandbox.send_init(b'INIT\t1\tdGVzdA\n', timeout=.4)
            except SandboxError as error: errors.append(error)
        thread = threading.Thread(target=first); thread.start()
        deadline=time.monotonic()+1
        while not sandbox._init_attempted and time.monotonic()<deadline: time.sleep(.01)
        assert sandbox._init_attempted
        started=time.monotonic()
        with pytest.raises(SandboxError): sandbox.send_init(b'INIT\t1\tdGVzdA\n')
        assert time.monotonic()-started < .2
        thread.join(timeout=1)
        assert not thread.is_alive() and errors
    finally:
        sandbox.sdk.terminate(); sandbox.sdk.wait(timeout=2)
        sandbox.sdk.stdin.close(); sandbox.sdk.stdout.close()


def test_init_refuses_when_guard_already_died():
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    sandbox.guard = SimpleNamespace(poll=lambda: -9)
    sandbox.sdk = SimpleNamespace(poll=lambda: None)
    sandbox._preflight_sdk_identity = 'a'*64
    with pytest.raises(SandboxError): sandbox.send_init(b'INIT\t1\tdGVzdA\n')
    assert not sandbox._init_attempted


def test_stop_reports_unverified_when_process_survives_terminate_and_kill(monkeypatch):
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    class Stuck:
        stdin = stdout = None
        def poll(self): return None
        def wait(self, timeout): raise subprocess.TimeoutExpired('synthetic', timeout)
        def terminate(self): pass
        def kill(self): pass
    sandbox.sdk = Stuck()
    monkeypatch.setattr(sandbox, '_docker',
        lambda args, timeout=3: subprocess.CompletedProcess(args,0,b''))
    result = sandbox.stop()
    assert not result.confirmed and not result.containers_stopped
    assert result is sandbox._last_stop_result


def test_sdk_inspection_rejects_untrusted_environment_volume_and_image(monkeypatch):
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    monkeypatch.setattr(sandbox, '_verify_guard_network_only', lambda _id: None)
    monkeypatch.setattr(sandbox, '_same_network_namespace', lambda *_: True)
    guard_id, sdk_id = 'd'*64, 'e'*64
    guard = {'Id':guard_id, 'Name':'/'+sandbox.guard_name, 'Image':sandbox.spec.guard_image,
             'State':{'Pid':100,'Running':True},
             'Config':{'Labels':{'org.generic-agents.component':'dji-cloud2-egress',
                                 'org.generic-agents.lease':sandbox.guard_name.rsplit('-',1)[1]}}}
    info = {'Id':sdk_id, 'Name':'/'+sandbox.sdk_name, 'Image':sandbox.spec.sdk_image,
        'State':{'Running':True,'Pid':101}, 'Mounts':[],
        'HostConfig':{'NetworkMode':'container:'+guard_id,'Privileged':False,'ReadonlyRootfs':True,
                      'CapDrop':['ALL'],'CapAdd':None,'Binds':None,'LogConfig':{'Type':'none'}},
        'Config':{'User':'65534:65534','Volumes':None,'Healthcheck':{'Test':['NONE']},
                  'Labels':{'org.generic-agents.component':'dji-cloud2-sdk',
                            'org.generic-agents.lease':sandbox.sdk_name.rsplit('-',1)[1]},
                  'Env':['PATH=/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                         'JAVA_HOME=/opt/java/openjdk','HTTPS_PROXY=','NO_PROXY=*']}}
    def inspect(args, timeout=3):
        value = guard if args[-1] == sandbox.guard_name else info
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode())
    monkeypatch.setattr(sandbox, '_docker', inspect)
    assert sandbox._inspect_sdk_identity() == sdk_id
    info['Config']['Env'].append('OPENSSL_CONF=/attacker/config')
    with pytest.raises(SandboxError): sandbox._inspect_sdk_identity()
    info['Config']['Env'].pop()
    info['Config']['Volumes'] = {'/secrets':{}}
    with pytest.raises(SandboxError): sandbox._inspect_sdk_identity()
    info['Config']['Volumes'] = None
    info['Image'] = 'sha256:'+'f'*64
    with pytest.raises(SandboxError): sandbox._inspect_sdk_identity()


def test_guard_accepts_docker_capability_normalization_but_no_extra_capabilities(monkeypatch):
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker')
    sandbox.network_id = 'd'*64
    monkeypatch.setattr(sandbox, '_verify_daemon_identity', lambda: None)
    info = {'State':{'Running':True,'Pid':999},
        'HostConfig':{'NetworkMode':sandbox.network_id,'Privileged':False,'CapAdd':['CAP_NET_ADMIN'],'CapDrop':['ALL']},
        'NetworkSettings':{'Networks':{'test':{'NetworkID':sandbox.network_id}}}}
    monkeypatch.setattr(sandbox, '_docker', lambda args: subprocess.CompletedProcess(args,0,json.dumps(info).encode()))
    actual_stat = os.stat
    def safe_stat(path, *args, **kwargs):
        if path == '/proc/1/ns/net': return SimpleNamespace(st_dev=1,st_ino=1)
        if path == '/proc/999/ns/net': return SimpleNamespace(st_dev=1,st_ino=2)
        return actual_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', safe_stat)
    sandbox._verify_guard_namespace()
    info['HostConfig']['CapAdd'] = ['NET_ADMIN']
    sandbox._verify_guard_namespace()
    info['HostConfig']['CapAdd'] = ['CAP_NET_ADMIN','CAP_SYS_ADMIN']
    with pytest.raises(ValueError): sandbox._verify_guard_namespace()


def test_spec_fails_closed_and_accepts_an_explicit_32_host_ceiling():
    spec().validate()
    for overrides in ({'host':'127.0.0.1'}, {'host':'10.1.2.4'}, {'host':'example.test'},
                      {'port':True}, {'generation':0}, {'guard_image':'untrusted:latest'},
                      {'docker_network_id':'d'*64}, {'allowed_networks':()}, {'network_mode':'unexpected'}):
        with pytest.raises(SandboxError): spec(**overrides).validate()


def test_lan_network_requires_explicit_experimental_opt_in_and_own_bridge(monkeypatch):
    monkeypatch.delenv('GA_DJI2_LAN_E2E_ENABLE', raising=False)
    lan = spec(network_mode='lan_test')
    with pytest.raises(SandboxError): DockerSandbox(lan, lambda _: True)
    with pytest.raises(SandboxError): DockerSandbox(lan, lambda _: True, experimental_lan=True)
    monkeypatch.setenv('GA_DJI2_LAN_E2E_ENABLE', '1')
    fake = FakeDocker()
    sandbox = DockerSandbox(lan, lambda _: True, docker='/usr/bin/docker',
                            run=fake.run, popen=fake.popen, experimental_lan=True)
    assert sandbox.prepare_network() == 'd'*64
    creation = next(args for args in fake.commands if args[1:3] == ['network', 'create'])
    assert '--internal' not in creation
    assert creation[-1] == sandbox.network_name
    assert sandbox.stop().network_removed


def test_launch_order_is_guard_then_sdk_and_fenced_stop(monkeypatch):
    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    sandbox._preflight_sdk_identity = '2'*64
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
    sandbox._preflight_sdk_identity = '2'*64
    result = sandbox.stop()
    assert not result.confirmed and not result.network_fenced
    assert result.containers_stopped

    fake = FakeDocker()
    sandbox = DockerSandbox(spec(), lambda _: True, docker='/usr/bin/docker', popen=fake.popen, run=fake.run, lease_seconds=1)
    monkeypatch.setattr(sandbox, '_verify_guard_namespace', lambda: None)
    sandbox.start()
    sandbox._preflight_sdk_identity = '2'*64
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
    sandbox._preflight_sdk_identity = '2'*64
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
    sandbox._preflight_sdk_identity = '2'*64
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
