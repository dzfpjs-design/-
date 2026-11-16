"""EXPERIMENT ONLY: NOT a production isolation or DJI connection boundary.

This module is deliberately not connected to the product's real-SDK gate. It
creates only a dedicated *internal* Docker bridge, which cannot reach a real
external LAN device. Failure to identify a local Linux dockerd and demonstrate
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
            if (not _HEX.fullmatch(self.policy_digest) or not _IMAGE.fullmatch(self.guard_image)
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
                 popen=subprocess.Popen, run=subprocess.run, lease_seconds=2.0):
        spec.validate()
        if not callable(authorize) or not os.path.isabs(docker) or not 1.0 <= lease_seconds <= 5.0:
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
        self._io_lock = threading.Lock()
        self._failed = threading.Event()
        self._started = False

    def _env(self):
        # Never inherit proxy settings, Docker remote contexts, or application secrets.
        return {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'DOCKER_HOST': 'unix:///var/run/docker.sock'}

    def _allowed(self):
        try:
            return self.authorize(self.spec) is True
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
        """Create our own internal bridge; cannot reach an external LAN device."""
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
        created = self._docker(['network', 'create', '--driver', 'bridge', '--internal',
                                '--label', 'org.generic-agents.component=dji-cloud2-test-network',
                                '--label', 'org.generic-agents.lease='+lease, self.network_name])
        if created is None or created.returncode != 0:
            raise SandboxError('network_create_unconfirmed')
        try:
            network_id = created.stdout.decode('ascii').strip()
            if not _NETWORK.fullmatch(network_id):
                raise ValueError()
            self.network_id = network_id
            self._verify_network(require_empty=True)
            self._owned_network_id = network_id
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
                or network.get('Internal') is not True or require_empty and network.get('Containers')
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
                or set(host_config.get('CapAdd') or []) != {'NET_ADMIN'}
                or set(host_config.get('CapDrop') or []) != {'ALL'}
                or len(links) != 1 or next(iter(links.values())).get('NetworkID') != self.network_id):
            raise ValueError()
        host = os.stat('/proc/1/ns/net')
        guard = os.stat('/proc/%d/ns/net' % state['Pid'])
        if (host.st_dev, host.st_ino) == (guard.st_dev, guard.st_ino):
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

    def start(self):
        if self._started or not self._allowed():
            raise SandboxError('authorization_unavailable')
        if self.network_id is None:
            self.prepare_network()
        try:
            self._verify_network()
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            # Only our already verified internally-created network can be
            # considered for cleanup; inability to verify remains unknown.
            self.stop()
            raise SandboxError('network_verification_failed') from None
        self._started = True
        try:
            self.guard = self._popen(self._guard_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=self._env(), close_fds=True, bufsize=0)
            if self._line(5) != b'BOOT' or not self._allowed():
                raise SandboxError('guard_unavailable')
            self._verify_guard_namespace()
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
        self._docker(['kill', self.sdk_name])

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
            self._docker(['kill', self.sdk_name])

    def _docker(self, args, timeout=3):
        try:
            return self._run([self.docker, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self._env(), timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None

    def stop(self):
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
        # Each name is generated internally and bound to this unique instance.
        # Guard can already be gone due to --rm, so only `ps` provides proof.
        for name in (self.sdk_name, self.guard_name):
            if not _NAME.fullmatch(name):
                return StopResult(False, False, False)
            self._docker(['kill', name])
        result = self._docker(['ps', '-a', '--format', '{{.Names}}'])
        stopped = result is not None and result.returncode == 0 and not ({self.sdk_name, self.guard_name} & set(result.stdout.decode('utf-8','replace').splitlines()))
        if self._owned_network_id:
            self._docker(['network', 'rm', self._owned_network_id])
        networks = self._docker(['network', 'ls', '--no-trunc', '--quiet'])
        removed = (not self._network_create_attempted or self._owned_network_id is not None and networks is not None
                   and networks.returncode == 0 and self._owned_network_id not in networks.stdout.decode('ascii','replace').splitlines())
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
                        proc.terminate(); proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=0.5)
                    except (OSError, subprocess.TimeoutExpired):
                        reaped = False
                except OSError:
                    reaped = False
        return StopResult(fenced, stopped and reaped, removed)
