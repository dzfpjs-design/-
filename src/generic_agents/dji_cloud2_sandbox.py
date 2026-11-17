"""EXPERIMENT ONLY: NOT a production isolation or DJI connection boundary.

This module is deliberately not connected to the product's real-SDK gate. It
creates dedicated Docker bridges. The default is *internal*; `lan_test` is an
explicit research-only mode for synthetic endpoints, never a production gate.
Failure to identify a local Linux dockerd and demonstrate
distinct guard and host network namespaces stops BEFORE arming the guard; the
unarmed guard must never run any firewall command. Do not weaken those checks
for Docker Desktop, remote daemons or containerized managers.

The caller MUST exclusively own the connection, durably record resource intent
before starting, and stop renewing immediately upon authorization/endpoint or
version change. That durable journal, exclusive ownership, restart recovery,
SDK-image signature/content and actual mount/environment verification are not
implemented here. Image IDs having digest syntax do not establish trust; images
may declare VOLUME or unsafe loader environment variables. Nor does an IP/port
filter enforce read-only access: production needs verified SDK redirect fixes,
server-enforced read-only identity and/or a trusted request-filtering proxy.

Guard or daemon failure can leave stop status unverified; never translate that
into confirmed disconnection or enable an automatic restart. The caller must
surface an alert and block further actions. Timeout cannot retract already sent
or buffered data. Neither a host nor user-provided network may be reused. Linux
runtime verification, management-failure recovery and operator UX remain open
release blockers. No real device, credential, or official simulator is tested.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import select
import socket
import struct
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass

_IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
_NETWORK = re.compile(r'[0-9a-f]{64}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'ga-dji2-(?:guard|sdk|net)-[0-9a-f]{32}\Z')

# Runs inside a trusted guard container, never inside the SDK PID namespace.
# Only its anonymous stdin controls the lease; no socket or shared volume exists.
_GUARD = r'''
import ipaddress, os, select, subprocess, sys, time
ip=str(ipaddress.IPv4Address(sys.argv[1])); port=int(sys.argv[2]); ttl=float(sys.argv[3])
if not (1 <= port <= 65535 and 1.0 <= ttl <= 5.0): sys.exit(90)
v4_deny=b'*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\nCOMMIT\n'
v6_deny=v4_deny
v4_allow=('*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\n'
 '-A INPUT -p tcp -s '+ip+'/32 --sport '+str(port)+' -m conntrack --ctstate ESTABLISHED -j ACCEPT\n'
 '-A OUTPUT -p tcp -d '+ip+'/32 --dport '+str(port)+' -j ACCEPT\nCOMMIT\n').encode('ascii')
def restore(tool, rules):
    subprocess.run([tool],input=rules,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,check=True)
def fence():
    # A failed rule install is never acknowledged as a fence.
    restore('/usr/sbin/iptables-nft-restore',v4_deny)
    restore('/usr/sbin/ip6tables-nft-restore',v6_deny)
def verify(tool, chain, expected):
    result=subprocess.run([tool,'-S',chain],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=1,check=True)
    lines=result.stdout.decode('ascii').strip().splitlines()
    if len(lines) != expected+1 or lines[0] != '-P '+chain+' DROP': raise ValueError('bad firewall')
    return lines
def write(s):
    try: os.write(1,s.encode('ascii'))
    except OSError: pass
write('BOOT\n')
try:
    # Failure or EOF BEFORE ARM must not execute even the deny-rule cleanup.
    if not select.select([0],[],[],8)[0] or os.read(0,64) != b'ARM\n': sys.exit(89)
except (OSError, ValueError):
    sys.exit(89)
try:
    fence()                              # never allow while constructing rules
    restore('/usr/sbin/iptables-nft-restore',v4_allow)
    out=verify('/usr/sbin/iptables-nft','OUTPUT',1)
    incoming=verify('/usr/sbin/iptables-nft','INPUT',1)
    if ('-d '+ip+'/32' not in out[1] or '--dport '+str(port) not in out[1]
        or '-s '+ip+'/32' not in incoming[1] or '--sport '+str(port) not in incoming[1]): raise ValueError('bad firewall')
    verify('/usr/sbin/ip6tables-nft','OUTPUT',0)
    verify('/usr/sbin/ip6tables-nft','INPUT',0)
    write('READY\n')
    expiry=time.monotonic()+ttl
    pending=b''
    while True:
        remain=expiry-time.monotonic()
        if remain <= 0: break
        if not select.select([0],[],[],remain)[0]: break
        data=os.read(0,64)
        if not data: break
        pending += data
        if len(pending)>64: break
        while b'\n' in pending:
            line,pending=pending.split(b'\n',1)
            if line == b'STOP':
                fence(); write('FENCED\n'); sys.exit(0)
            if line != b'PING': raise ValueError('invalid command')
            expiry=time.monotonic()+ttl
except SystemExit:
    raise
except BaseException:
    # The parent must treat loss of this process or its Docker daemon as unknown.
    pass
try: fence()
except BaseException: sys.exit(91)
sys.exit(92)
'''


class SandboxError(Exception):
    """Safe error code, never contains credentials or raw Docker output."""


@dataclass(frozen=True)
class SandboxSpec:
    workspace_id: str
    connection_id: str
    generation: int
    host: str
    port: int
    policy_digest: str
    guard_image: str
    sdk_image: str
    allowed_networks: tuple[str, ...]
    network_mode: str = 'internal'
    # External IDs, especially a host-network ID, MUST NEVER be accepted.
    docker_network_id: str = ''

    def validate(self):
        try:
            for value in (self.workspace_id, self.connection_id):
                if str(uuid.UUID(value)) != value:
                    raise ValueError()
            if type(self.generation) is not int or self.generation < 1:
                raise ValueError()
            ip = ipaddress.IPv4Address(self.host)
            if str(ip) != self.host or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                raise ValueError()
            if type(self.port) is not int or not 1 <= self.port <= 65535:
                raise ValueError()
            if not isinstance(self.allowed_networks, tuple) or not self.allowed_networks:
                raise ValueError()
            nets = [ipaddress.IPv4Network(n, strict=True) for n in self.allowed_networks]
            if not any(ip in n and (n.prefixlen >= 31 or ip not in (n.network_address, n.broadcast_address)) for n in nets):
                raise ValueError()
            if (self.network_mode not in ('internal', 'lan_test')
                    or not _HEX.fullmatch(self.policy_digest) or not _IMAGE.fullmatch(self.guard_image)
                    or not _IMAGE.fullmatch(self.sdk_image) or self.docker_network_id != ''):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SandboxError('invalid_sandbox_spec') from None


@dataclass(frozen=True)
class StopResult:
    network_fenced: bool
    containers_stopped: bool
    network_removed: bool

    @property
    def confirmed(self):
        return self.network_fenced and self.containers_stopped and self.network_removed


class DockerSandbox:
    """Own exactly one guard and one SDK container, with a non-reusable lease.

    Call `stop` on revocation before displaying a disconnected confirmation.
    Inability to verify fence and removal means unknown, not successful stop.
    """
    def __init__(self, spec: SandboxSpec, authorize, *, docker='/usr/bin/docker',
                 popen=subprocess.Popen, run=subprocess.run, lease_seconds=2.0,
                 experimental_lan=False):
        spec.validate()
        if (not callable(authorize) or not os.path.isabs(docker) or not 1.0 <= lease_seconds <= 5.0
                or type(experimental_lan) is not bool
                or spec.network_mode == 'lan_test' and
                   (not experimental_lan or os.environ.get('GA_DJI2_LAN_E2E_ENABLE') != '1')):
            raise SandboxError('invalid_sandbox_spec')
        self.spec, self.authorize, self.docker = spec, authorize, docker
        self._popen, self._run, self.lease_seconds = popen, run, lease_seconds
        identity = f'{spec.workspace_id}\0{spec.connection_id}\0{spec.generation}\0{spec.host}\0{spec.port}\0{spec.policy_digest}\0{spec.sdk_image}\0{uuid.uuid4()}'
        suffix = hashlib.sha256(identity.encode()).hexdigest()[:32]
        self.guard_name, self.sdk_name, self.network_name = 'ga-dji2-guard-' + suffix, 'ga-dji2-sdk-' + suffix, 'ga-dji2-net-' + suffix
        self.network_id = None
        self._owned_network_id = None
        self._network_create_attempted = False
        self.guard = self.sdk = self.thread = self.monitor = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._timeout_stop_result = StopResult(False, False, False)
        self._stop_timeout_observed = threading.Event()
        self._io_lock = threading.Lock()
        self._sdk_send_lock = threading.Lock()
        self._failed = threading.Event()
        self._started = False
        self._guard_create_attempted = False
        self._sdk_create_attempted = False
        self._preflight_sdk_identity = None
        self._preflight_guard_identity = None
        self._last_guard_identity = None
        self._owned_guard_id = None
        self._sdk_ready_identity = None
        self._init_attempted = False
        self._last_stop_result = None

    def _env(self):
        # Never inherit proxy settings, Docker remote contexts, or application secrets.
        return {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'DOCKER_HOST': 'unix:///var/run/docker.sock'}

    def _allowed(self):
        try:
            return (not self._stop.is_set() and self.authorize(self.spec) is True
                    and not self._stop.is_set())
        except Exception:
            return False

    def _line(self, timeout):
        end = time.monotonic() + timeout
        data = bytearray()
        fd = self.guard.stdout.fileno()
        while time.monotonic() < end:
            readable, _, _ = select.select([fd], [], [], max(0, end-time.monotonic()))
            if not readable:
                break
            b = os.read(fd, 1)
            if not b or len(data) >= 32:
                break
            if b == b'\n':
                return bytes(data)
            data += b
        raise SandboxError('guard_unavailable')

    def _guard_cmd(self):
        s = self.spec
        return [self.docker, 'run', '--rm', '--interactive', '--log-driver', 'none', '--name', self.guard_name,
                '--label', 'org.generic-agents.component=dji-cloud2-egress',
                '--label', 'org.generic-agents.lease='+self.guard_name.rsplit('-',1)[1],
                '--network', self.network_id, '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN',
                '--security-opt', 'no-new-privileges', '--no-healthcheck', '--read-only', '--memory', '128m', '--memory-swap', '128m',
                '--cpus', '0.25', '--pids-limit', '32', '--ulimit', 'core=0:0',
                '--entrypoint', '/usr/bin/python3', s.guard_image, '-u', '-c', _GUARD,
                s.host, str(s.port), str(self.lease_seconds)]

    def _sdk_cmd(self):
        s = self.spec
        # The fixed operator-built image embeds the reviewed bridge/JAR and entrypoint.
        # No host path, Docker socket, secret, or mutable command is injected.
        return [self.docker, 'run', '--rm', '--interactive', '--log-driver', 'none', '--name', self.sdk_name,
                '--label', 'org.generic-agents.component=dji-cloud2-sdk',
                '--label', 'org.generic-agents.lease='+self.guard_name.rsplit('-',1)[1],
                '--network', 'container:'+self.guard_name, '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--no-healthcheck', '--user', '65534:65534', '--read-only',
                '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=128m,mode=1777', '--memory', '512m', '--memory-swap', '512m',
                '--cpus', '1', '--pids-limit', '64', '--ulimit', 'core=0:0',
                '--env', 'HTTP_PROXY=', '--env', 'HTTPS_PROXY=', '--env', 'ALL_PROXY=',
                '--env', 'http_proxy=', '--env', 'https_proxy=', '--env', 'all_proxy=',
                '--env', 'NO_PROXY=*', '--env', 'no_proxy=*',
                '--entrypoint', '/opt/dji-cloud2/bridge-launch', s.sdk_image]

    def prepare_network(self):
        with self._lifecycle_lock:
            return self._prepare_network_locked()

    def _prepare_network_locked(self):
        """Create our own bridge; LAN mode is explicit and research-only."""
        if self.network_id is not None or self._started or not self._allowed():
            raise SandboxError('authorization_unavailable')
        try:
            self._verify_daemon_identity()
        except (OSError, ValueError, AttributeError, struct.error):
            raise SandboxError('daemon_identity_unverified') from None
        lease = self.network_name.rsplit('-', 1)[1]
        # Caller must durably journal the generated names before this call in
        # a future integration. A timed-out Docker create is not proof of absence.
        self._network_create_attempted = True
        args = ['network', 'create', '--driver', 'bridge']
        if self.spec.network_mode == 'internal': args.append('--internal')
        args += ['--label', 'org.generic-agents.component=dji-cloud2-test-network',
                 '--label', 'org.generic-agents.lease='+lease, self.network_name]
        created = self._docker(args)
        if created is None or created.returncode != 0:
            raise SandboxError('network_create_unconfirmed')
        try:
            network_id = created.stdout.decode('ascii').strip()
            if not _NETWORK.fullmatch(network_id):
                raise ValueError()
            self.network_id = network_id
            self._verify_network(require_empty=True)
            self._owned_network_id = network_id
            if not self._allowed():
                self._stop_locked()
                raise SandboxError('authorization_unavailable')
            return network_id
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            # Ownership could not be proven: do not remove an unknown network.
            raise SandboxError('network_verification_failed') from None

    def _verify_network(self, *, require_empty=False):
        if not isinstance(self.network_id, str) or not _NETWORK.fullmatch(self.network_id):
            raise ValueError()
        inspect = self._docker(['network', 'inspect', '--format', '{{json .}}', self.network_id])
        if inspect is None or inspect.returncode != 0:
            raise ValueError()
        network = json.loads(inspect.stdout)
        if (network.get('Id') != self.network_id or network.get('Name') != self.network_name
                or network.get('Driver') != 'bridge' or network.get('Scope') != 'local'
                or network.get('Internal') is not (self.spec.network_mode == 'internal')
                or not isinstance(network.get('Containers'), dict)
                or require_empty and network.get('Containers')
                or network.get('Labels', {}).get('org.generic-agents.component') != 'dji-cloud2-test-network'
                or network.get('Labels', {}).get('org.generic-agents.lease') != self.network_name.rsplit('-',1)[1]):
            raise ValueError()

    def _verify_guard_namespace(self):
        """Host Linux only. No firewall write occurs if /proc cannot prove it."""
        self._verify_daemon_identity()
        result = self._docker(['inspect', '--format', '{{json .}}', self.guard_name])
        if result is None or result.returncode != 0:
            raise ValueError()
        info = json.loads(result.stdout)
        host_config, state = info['HostConfig'], info['State']
        links = info['NetworkSettings']['Networks']
        if (not state.get('Running') or type(state.get('Pid')) is not int or state['Pid'] <= 1
                or host_config.get('NetworkMode') not in (self.network_id, self.network_name)
                or host_config.get('Privileged') is not False
                or {str(value).removeprefix('CAP_') for value in (host_config.get('CapAdd') or [])} != {'NET_ADMIN'}
                or set(host_config.get('CapDrop') or []) != {'ALL'}
                or len(links) != 1 or next(iter(links.values())).get('NetworkID') != self.network_id):
            raise ValueError()
        host = os.stat('/proc/1/ns/net')
        guard = os.stat('/proc/%d/ns/net' % state['Pid'])
        if (host.st_dev, host.st_ino) == (guard.st_dev, guard.st_ino):
            raise ValueError()

    def _verify_guard_network_only(self, guard_id):
        self._verify_network()
        inspected = self._docker(['network', 'inspect', '--format', '{{json .}}', self.network_id])
        if inspected is None or inspected.returncode:
            raise ValueError()
        members = json.loads(inspected.stdout).get('Containers')
        if not isinstance(members, dict) or set(members) != {guard_id}:
            raise ValueError()

    def _verify_daemon_identity(self):
        """Reject remote/proxy daemons or ambiguous PID namespaces."""
        if not hasattr(socket, 'SO_PEERCRED'):
            raise ValueError()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect('/var/run/docker.sock')
            pid, _, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        if pid <= 1 or os.path.basename(os.readlink('/proc/%d/exe' % pid)) != 'dockerd':
            raise ValueError()
        namespaces = [os.stat(path) for path in ('/proc/1/ns/pid', '/proc/self/ns/pid', '/proc/%d/ns/pid' % pid)]
        if len({(item.st_dev, item.st_ino) for item in namespaces}) != 1:
            raise ValueError()

    def verified_guard_id(self):
        """Recheck guard namespace, immutable image and generated lease label."""
        self._verify_guard_namespace()
        result = self._docker(['inspect', '--format', '{{json .}}', self.guard_name])
        if result is None or result.returncode:
            raise SandboxError('guard_identity_unverified')
        try:
            info = json.loads(result.stdout)
            identity = info['Id']
            labels = info['Config'].get('Labels') or {}
            if (not _NETWORK.fullmatch(identity) or info.get('Name') != '/'+self.guard_name
                    or info.get('Image') != self.spec.guard_image or not info['State'].get('Running')
                    or self._preflight_guard_identity is not None and identity != self._preflight_guard_identity
                    or labels.get('org.generic-agents.component') != 'dji-cloud2-egress'
                    or labels.get('org.generic-agents.lease') != self.guard_name.rsplit('-',1)[1]):
                raise ValueError()
            return identity
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise SandboxError('guard_identity_unverified') from None

    def _inspect_sdk_identity(self):
        """Check the daemon's actual SDK container, not the docker CLI process."""
        verified_guard = self.verified_guard_id()
        try:
            self._verify_guard_network_only(verified_guard)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise SandboxError('sdk_identity_unverified') from None
        guard = self._docker(['inspect', '--format', '{{json .}}', self.guard_name])
        sdk = self._docker(['inspect', '--format', '{{json .}}', self.sdk_name])
        if guard is None or sdk is None or guard.returncode or sdk.returncode:
            raise SandboxError('sdk_identity_unverified')
        try:
            guard_info, info = json.loads(guard.stdout), json.loads(sdk.stdout)
            guard_id, sdk_id = guard_info['Id'], info['Id']
            state, host, config = info['State'], info['HostConfig'], info['Config']
            labels = config.get('Labels') or {}
            lease = self.sdk_name.rsplit('-', 1)[1]
            env = config.get('Env') or []
            if not isinstance(env, list):
                raise ValueError()
            allowed_names = {'PATH', 'JAVA_HOME', 'LANG', 'LANGUAGE', 'LC_ALL', 'JAVA_VERSION', 'TZ',
                             'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
                             'all_proxy', 'NO_PROXY', 'no_proxy'}
            seen = set()
            for item in env:
                if not isinstance(item, str) or '=' not in item:
                    raise ValueError()
                name, value = item.split('=', 1)
                if name not in allowed_names or name in seen:
                    raise ValueError()
                seen.add(name)
                if name.lower() in ('http_proxy', 'https_proxy', 'all_proxy') and value:
                    raise ValueError()
                if name.lower() == 'no_proxy' and value != '*':
                    raise ValueError()
                if name == 'PATH' and value != '/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin':
                    raise ValueError()
                if name == 'JAVA_HOME' and value != '/opt/java/openjdk':
                    raise ValueError()
                if name in ('LANG', 'LANGUAGE', 'LC_ALL', 'JAVA_VERSION', 'TZ') and not re.fullmatch(r'[A-Za-z0-9._@:+/\-]{1,128}', value):
                    raise ValueError()
            if not {'PATH', 'JAVA_HOME'}.issubset(seen):
                raise ValueError()
            if (guard_id != verified_guard or not _NETWORK.fullmatch(guard_id) or not _NETWORK.fullmatch(sdk_id)
                    or info.get('Name') != '/'+self.sdk_name or info.get('Image') != self.spec.sdk_image
                    or not state.get('Running') or type(state.get('Pid')) is not int or state['Pid'] <= 1
                    or host.get('NetworkMode') not in ('container:'+guard_id, 'container:'+self.guard_name)
                    or host.get('Privileged') is not False or host.get('ReadonlyRootfs') is not True
                    or set(host.get('CapDrop') or []) != {'ALL'} or host.get('CapAdd')
                    or host.get('Binds') or config.get('Volumes')
                    or ((config.get('Healthcheck') or {}).get('Test') not in (None, ['NONE']))
                    or any(mount.get('Type') != 'tmpfs' or mount.get('Destination') != '/tmp'
                           for mount in (info.get('Mounts') or []))
                    or (host.get('LogConfig') or {}).get('Type') != 'none'
                    or config.get('User') != '65534:65534'
                    or labels.get('org.generic-agents.component') != 'dji-cloud2-sdk'
                    or labels.get('org.generic-agents.lease') != lease):
                raise ValueError()
            if not self._same_network_namespace(guard_info['State']['Pid'], state['Pid']):
                raise ValueError()
            if self.verified_guard_id() != guard_id:
                raise ValueError()
            self._last_guard_identity = guard_id
            return sdk_id
        except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError):
            raise SandboxError('sdk_identity_unverified') from None

    def _same_network_namespace(self, first_pid, second_pid):
        first = os.stat('/proc/%d/ns/net' % first_pid)
        second = os.stat('/proc/%d/ns/net' % second_pid)
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)

    def preflight_sdk(self, timeout=20):
        """Verify isolation and container identity BEFORE sending credentials."""
        if self.sdk is None or self._preflight_sdk_identity is not None:
            raise SandboxError('sdk_identity_unverified')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._failed.is_set() or self._stop.is_set() or self.sdk.poll() is not None or not self._allowed():
                raise SandboxError('sdk_identity_unverified')
            try:
                self._preflight_sdk_identity = self._inspect_sdk_identity()
                if not isinstance(self._last_guard_identity, str) or not _NETWORK.fullmatch(self._last_guard_identity):
                    self._preflight_sdk_identity = None
                    raise SandboxError('guard_identity_unverified')
                self._preflight_guard_identity = self._last_guard_identity
                return
            except (SandboxError, ValueError, OSError):
                self._stop.wait(.25)
        raise SandboxError('sdk_identity_unverified')

    def read_sdk_ready(self, timeout=20):
        """Read READY from this sandbox's SDK pipe, then bind its actual ID."""
        if self.sdk is None or not self._preflight_sdk_identity or not self._init_attempted or self._sdk_ready_identity is not None:
            raise SandboxError('sdk_identity_unverified')
        deadline = time.monotonic() + timeout
        output = bytearray()
        fd = self.sdk.stdout.fileno()
        while time.monotonic() < deadline:
            if self._failed.is_set() or self._stop.is_set() or self.sdk.poll() is not None:
                raise SandboxError('sdk_identity_unverified')
            readable, _, _ = select.select([fd], [], [], min(.25, max(0, deadline-time.monotonic())))
            if not readable:
                continue
            chunk = os.read(fd, 1)
            if not chunk or len(output) >= 32:
                raise SandboxError('sdk_identity_unverified')
            if chunk == b'\n':
                if bytes(output) != b'READY\t1':
                    raise SandboxError('sdk_identity_unverified')
                sdk_id = self._inspect_sdk_identity()
                if sdk_id != self._preflight_sdk_identity or self._last_guard_identity != self._preflight_guard_identity:
                    raise SandboxError('sdk_identity_unverified')
                self._sdk_ready_identity = sdk_id
                return
            output.extend(chunk)
        raise SandboxError('sdk_identity_unverified')

    def send_init(self, frame, timeout=3):
        """Bounded one-shot write; caller holds the DB authorization fence."""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 3:
            raise SandboxError('sdk_init_unconfirmed')
        if not self._sdk_send_lock.acquire(blocking=False):
            raise SandboxError('sdk_init_unconfirmed')
        try:
            if (self.sdk is None or not self._preflight_sdk_identity or self._init_attempted
                    or self.guard is None or self.guard.poll() is not None
                    or self._failed.is_set() or self._stop.is_set() or self.sdk.poll() is not None
                    or not isinstance(frame, bytes) or len(frame) > 16384
                    or not frame.startswith(b'INIT\t1\t') or not frame.endswith(b'\n')):
                raise SandboxError('sdk_init_unconfirmed')
            # A partial write has unknown outcome; never send a second INIT on
            # this process. No database or Docker RPC occurs while the caller
            # holds the database authorization fence.
            self._init_attempted = True
            deadline, offset = time.monotonic()+timeout, 0
            try:
                fd = self.sdk.stdin.fileno()
                os.set_blocking(fd, False)
                while offset < len(frame):
                    if (self._failed.is_set() or self._stop.is_set() or self.guard.poll() is not None
                            or self.sdk.poll() is not None):
                        raise SandboxError('sdk_init_unconfirmed')
                    remain = deadline-time.monotonic()
                    if remain <= 0:
                        raise SandboxError('sdk_init_unconfirmed')
                    _, writable, _ = select.select([], [fd], [], min(.25, remain))
                    if writable:
                        try: offset += os.write(fd, frame[offset:])
                        except BlockingIOError: continue
            except (OSError, ValueError):
                raise SandboxError('sdk_init_unconfirmed') from None
        finally:
            self._sdk_send_lock.release()

    def verify_preflight(self):
        """Bounded Docker checks must occur before acquiring DB row locks."""
        if (self.sdk is None or not self._preflight_sdk_identity or self._init_attempted
                or self._failed.is_set() or self._stop.is_set() or self.sdk.poll() is not None):
            raise SandboxError('sdk_init_unconfirmed')
        if (self._inspect_sdk_identity() != self._preflight_sdk_identity
                or self._last_guard_identity != self._preflight_guard_identity):
            raise SandboxError('sdk_init_unconfirmed')

    def verify_sdk_ready(self):
        """Re-check the same container before marking its durable intent active."""
        if not self._sdk_ready_identity or self._sdk_ready_identity != self._preflight_sdk_identity or self._failed.is_set() or self._stop.is_set():
            raise SandboxError('sdk_identity_unverified')
        if (self._inspect_sdk_identity() != self._sdk_ready_identity
                or self._last_guard_identity != self._preflight_guard_identity):
            raise SandboxError('sdk_identity_unverified')
        return self._sdk_ready_identity

    def start(self):
        with self._lifecycle_lock:
            return self._start_locked()

    def _start_locked(self):
        if self._started or not self._allowed():
            raise SandboxError('authorization_unavailable')
        if self.network_id is None:
            self.prepare_network()
        try:
            self._verify_network(require_empty=True)
            if not self._allowed():
                raise SandboxError('authorization_unavailable')
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            # Only our already verified internally-created network can be
            # considered for cleanup; inability to verify remains unknown.
            self.stop()
            raise SandboxError('network_verification_failed') from None
        except SandboxError:
            self.stop()
            raise
        self._started = True
        try:
            self._guard_create_attempted = True
            self.guard = self._popen(self._guard_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=self._env(), close_fds=True, bufsize=0)
            if self._line(5) != b'BOOT' or not self._allowed():
                raise SandboxError('guard_unavailable')
            self._verify_guard_namespace()
            self._owned_guard_id = self.verified_guard_id()
            if not self._allowed():
                raise SandboxError('authorization_unavailable')
            self.guard.stdin.write(b'ARM\n'); self.guard.stdin.flush()
            if self._line(5) != b'READY' or not self._allowed():
                raise SandboxError('guard_unavailable')
            os.set_blocking(self.guard.stdin.fileno(), False)
            self.monitor = threading.Thread(target=self._monitor_guard, daemon=True, name='dji2-egress-monitor')
            self.monitor.start()
            self.thread = threading.Thread(target=self._renew, daemon=True, name='dji2-egress-lease')
            self.thread.start()
            if self._failed.is_set() or not self._allowed():
                raise SandboxError('authorization_unavailable')
            self._sdk_create_attempted = True
            self.sdk = self._popen(self._sdk_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=self._env(), close_fds=True, bufsize=0)
            if self._failed.is_set() or not self._allowed():
                raise SandboxError('authorization_unavailable')
            return self.sdk
        except Exception:
            self.stop()
            raise SandboxError('sandbox_start_failed') from None

    def _renew(self):
        while not self._stop.wait(self.lease_seconds / 4):
            if not self._allowed() or self.guard.poll() is not None:
                self._fail_lease()
                return
            try:
                with self._io_lock:
                    self.guard.stdin.write(b'PING\n')
                    self.guard.stdin.flush()
            except (OSError, ValueError):
                self._fail_lease()
                return

    def _fail_lease(self):
        self._failed.set()
        self._stop.set()
        # The guard receives EOF even if Docker's management API is unavailable.
        try: self.guard.stdin.close()
        except (OSError, ValueError): pass
        # Best effort only; `stop()` must report unverified if fence was not
        # explicitly acknowledged, even when this succeeds.
        self._kill_owned('sdk')

    def _monitor_guard(self):
        # This thread never calls the authorization store: a blocked database
        # cannot stop detection of guard death. Failure to contact Docker still
        # leaves stop unverified; there is no unsafe fallback to host networking.
        try:
            self.guard.wait()
        except Exception:
            pass
        if not self._stop.is_set():
            self._failed.set()
            self._stop.set()
            self._kill_owned('sdk')

    def _kill_owned(self, kind):
        """Never kill a replacement by name, or an unverified create outcome."""
        identity = self._preflight_sdk_identity if kind == 'sdk' else self._owned_guard_id
        name = self.sdk_name if kind == 'sdk' else self.guard_name
        image = self.spec.sdk_image if kind == 'sdk' else self.spec.guard_image
        component = 'dji-cloud2-sdk' if kind == 'sdk' else 'dji-cloud2-egress'
        if not isinstance(identity, str) or not _NETWORK.fullmatch(identity):
            return
        result = self._docker(['inspect', '--format', '{{json .}}', identity])
        if result is None or result.returncode:
            return
        try:
            obj = json.loads(result.stdout)
            labels = obj['Config'].get('Labels') or {}
            if (obj.get('Id') != identity or obj.get('Name') != '/' + name or obj.get('Image') != image
                    or labels.get('org.generic-agents.component') != component
                    or labels.get('org.generic-agents.lease') != name.rsplit('-', 1)[1]):
                return
            self._docker(['kill', identity])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return

    def _docker(self, args, timeout=3):
        try:
            return self._run([self.docker, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self._env(), timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None

    def stop(self):
        self._stop.set()
        if not self._lifecycle_lock.acquire(timeout=3):
            self._stop_timeout_observed.set()
            return self._timeout_stop_result
        try:
            return self._stop_locked()
        finally:
            self._lifecycle_lock.release()

    def _stop_locked(self):
        with self._sdk_send_lock:
            self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        fenced = False
        if self.guard and self.guard.poll() is None:
            try:
                with self._io_lock:
                    self.guard.stdin.write(b'STOP\n'); self.guard.stdin.flush()
                fenced = self._line(3) == b'FENCED'
            except (OSError, ValueError, SandboxError):
                pass
        # Guard can already be gone due to --rm, so only `ps` provides proof.
        for kind, name in (('sdk', self.sdk_name), ('guard', self.guard_name)):
            if not _NAME.fullmatch(name):
                self._last_stop_result = StopResult(False, False, False)
                return self._last_stop_result
            self._kill_owned(kind)
        result = self._docker(['ps', '-a', '--format', '{{.Names}}'])
        ids_result = self._docker(['ps', '-a', '--no-trunc', '--format', '{{.ID}}'])
        def verified_ids(response):
            if response is None or response.returncode:
                return None
            try:
                values = response.stdout.decode('ascii', 'strict').splitlines()
                if any(not _NETWORK.fullmatch(value) for value in values):
                    return None
                return set(values)
            except (UnicodeError, AttributeError):
                return None
        listed_ids = verified_ids(ids_result)
        try:
            names = result.stdout.decode('ascii', 'strict').splitlines() if result is not None and not result.returncode else None
            if names is not None and any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value) for value in names):
                names = None
        except (UnicodeError, AttributeError):
            names = None
        owned_ids = (self._preflight_sdk_identity, self._owned_guard_id)
        identities_known = all(not attempted or isinstance(identity, str) and _NETWORK.fullmatch(identity)
            for attempted, identity in zip((self._sdk_create_attempted, self._guard_create_attempted), owned_ids))
        stopped = (identities_known and names is not None
            and listed_ids is not None
            and not ({self.sdk_name, self.guard_name} & set(names))
            and not (set(filter(None, owned_ids)) & listed_ids))
        if self._owned_network_id:
            self._docker(['network', 'rm', self._owned_network_id])
        networks = self._docker(['network', 'ls', '--no-trunc', '--quiet'])
        network_ids = verified_ids(networks)
        removed = (not self._network_create_attempted or self._owned_network_id is not None
                   and network_ids is not None and self._owned_network_id not in network_ids)
        reaped = True
        for proc in (self.sdk, self.guard):
            if proc is not None:
                for stream in (proc.stdin, proc.stdout):
                    try:
                        if stream: stream.close()
                    except (OSError, ValueError): pass
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=0.5)
                    except (OSError, subprocess.TimeoutExpired):
                        reaped = False
                except OSError:
                    reaped = False
        self._last_stop_result = StopResult(fenced, stopped and reaped, removed)
        return self._last_stop_result
