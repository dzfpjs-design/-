"""Experimental durable intent journal for isolated Cloud API 2 sandboxes.

Not wired to the production worker. A start with an unknown Docker result must
remain unresolved; absence from one Docker listing cannot prove that a timed
out create will not complete later. The caller owns the per-connection lease.
"""
from __future__ import annotations

import re
import json
import os
import socket
import struct
import subprocess
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException

from .db import engine
from .dji_cloud2_connections import _lock, acquire_snapshot, is_current
from .dji_cloud2_process import init_frame
from .dji_cloud2_sandbox import DockerSandbox, SandboxError, StopResult
from .models import Job, now

KIND = 'dji_cloud2_sandbox_intent_v1'
_SUFFIX = re.compile(r'[0-9a-f]{32}\Z')
_NETWORK_ID = re.compile(r'[0-9a-f]{64}\Z')


def _checked_names(sandbox):
    if not isinstance(sandbox, DockerSandbox):
        raise ValueError('invalid sandbox')
    names = (sandbox.guard_name, sandbox.sdk_name, sandbox.network_name)
    prefixes = ('ga-dji2-guard-', 'ga-dji2-sdk-', 'ga-dji2-net-')
    suffixes = [name[len(prefix):] if name.startswith(prefix) else ''
                for name, prefix in zip(names, prefixes)]
    if len(set(suffixes)) != 1 or not _SUFFIX.fullmatch(suffixes[0]):
        raise ValueError('invalid sandbox identity')
    return names, suffixes[0]


def _get(db, workspace, journal_id, *, lock=False):
    query = select(Job).where(Job.id == journal_id, Job.workspace_id == workspace,
                              Job.kind == KIND, Job.status == 'internal')
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '未找到隔离环境记录')
    return row


def _matches(row, sandbox, workspace):
    names, _ = _checked_names(sandbox)
    spec = sandbox.spec
    return (spec.workspace_id == workspace and spec.connection_id == row.data.get('connection_id')
            and spec.generation == row.data.get('generation')
            and getattr(spec, 'network_mode', 'internal') == row.data.get('network_mode', 'internal')
            and spec.host == row.data.get('host') and spec.port == row.data.get('port')
            and spec.policy_digest == row.data.get('policy_digest')
            and spec.guard_image == row.data.get('guard_image') and spec.sdk_image == row.data.get('sdk_image')
            and [row.data.get(k) for k in ('guard_name', 'sdk_name', 'network_name')] == list(names))


def begin(db, sandbox):
    """Persist BEFORE any Docker create; caller must commit before proceeding."""
    names, suffix = _checked_names(sandbox)
    spec = sandbox.spec
    _lock(db, spec.workspace_id)
    if sandbox._network_create_attempted or sandbox._started:
        raise HTTPException(409, '隔离环境已开始创建，不能补记原始意图')
    if not is_current(db, spec.workspace_id, spec.connection_id, spec.generation):
        raise HTTPException(403, '连接授权已失效')
    snapshot = acquire_snapshot(db, spec.workspace_id, spec.connection_id)
    if snapshot['host'] != spec.host or snapshot['port'] != spec.port:
        raise HTTPException(403, '隔离目标与连接授权不一致')
    rows = db.scalars(select(Job).where(Job.workspace_id == spec.workspace_id,
                      Job.kind == KIND, Job.status == 'internal').with_for_update()).all()
    if any(row.data.get('connection_id') == spec.connection_id and row.data.get('state') != 'closed'
           for row in rows):
        raise HTTPException(409, '原隔离环境尚未确认清理，不能启动新环境')
    row = Job(workspace_id=spec.workspace_id, kind=KIND, status='internal',
              idempotency_key='dji-cloud2-sandbox-intent:' + suffix,
              data={'connection_id': spec.connection_id, 'generation': spec.generation,
                    'host': spec.host, 'port': spec.port, 'policy_digest': spec.policy_digest,
                    'network_mode': getattr(spec, 'network_mode', 'internal'),
                    'guard_image': spec.guard_image, 'sdk_image': spec.sdk_image,
                    'guard_name': names[0], 'sdk_name': names[1], 'network_name': names[2],
                    'network_id': None, 'state': 'starting', 'started_confirmed': False})
    db.add(row); db.flush()
    return row.id


def record_network(db, workspace, journal_id, sandbox):
    _lock(db, workspace)
    row = _get(db, workspace, journal_id, lock=True)
    if (not _matches(row, sandbox, workspace) or row.data.get('state') != 'starting'
            or not isinstance(sandbox._owned_network_id, str)
            or not _NETWORK_ID.fullmatch(sandbox._owned_network_id)):
        raise HTTPException(409, '隔离网络状态无法核对')
    row.data = {**row.data, 'network_id': sandbox._owned_network_id}; row.version += 1
    db.flush()


def mark_active(db, workspace, journal_id, sandbox):
    try:
        sdk_id = sandbox.verify_sdk_ready()
        guard_id = sandbox.verified_guard_id()
    except (SandboxError, ValueError, OSError):
        raise HTTPException(409, 'SDK 启动或容器身份尚未确认') from None
    _lock(db, workspace)
    row = _get(db, workspace, journal_id, lock=True)
    if (not _matches(row, sandbox, workspace) or row.data.get('state') != 'starting'
            or not sandbox._started or sandbox.guard is None or sandbox.sdk is None
            or sandbox._stop.is_set() or sandbox._failed.is_set()
            or sandbox.guard.poll() is not None or sandbox.sdk.poll() is not None
            or sandbox.network_id != sandbox._owned_network_id
            or sandbox._owned_network_id != row.data.get('network_id')
            or not is_current(db, workspace, row.data['connection_id'], row.data['generation'])):
        raise HTTPException(409, '隔离环境或授权状态无法确认')
    if not _NETWORK_ID.fullmatch(sdk_id) or not _NETWORK_ID.fullmatch(guard_id):
        raise HTTPException(409, 'SDK 或守护容器身份无法核对')
    row.data = {**row.data, 'state': 'active', 'started_confirmed': True,
                'sdk_container_id': sdk_id, 'guard_container_id': guard_id}
    row.version += 1; db.flush()


def mark_stop(db, workspace, journal_id, sandbox, result):
    """Do not close an uncertain create or an unverified network fence."""
    if not isinstance(sandbox, DockerSandbox) or not isinstance(result, StopResult):
        raise ValueError('invalid stop result')
    # A lock-timeout receipt is a separate immutable, negative-only attestation.
    # It remains valid if the in-flight creator later updates its owned IDs or
    # the normal stop receipt, but it can never close a journal.
    timeout = (result is getattr(sandbox, '_timeout_stop_result', None)
               and sandbox._stop_timeout_observed.is_set()
               and result == StopResult(False, False, False))
    if not timeout and result is not sandbox._last_stop_result:
        raise ValueError('invalid stop result')
    _lock(db, workspace)
    row = _get(db, workspace, journal_id, lock=True)
    if (not _matches(row, sandbox, workspace)
            or not timeout and sandbox._owned_network_id != row.data.get('network_id')):
        raise HTTPException(409, '停止回执与隔离环境不匹配')
    state = row.data.get('state')
    if state == 'closed':
        return 'closed'
    if state not in ('starting', 'active', 'cleanup_unverified'):
        raise HTTPException(409, '隔离环境记录无法核对')
    # A crash or timeout before mark_active can leave a late daemon operation.
    # No single negative listing or caller-provided cleanup result proves it
    # cannot appear later; keep the record as a manual recovery blocker.
    confirmed = not timeout and row.data.get('started_confirmed') is True and result.confirmed
    new_state = 'closed' if confirmed else 'cleanup_unverified'
    row.data = {**row.data, 'state': new_state}; row.version += 1; db.flush()
    return new_state


def send_init(workspace, journal_id, sandbox):
    """Own and always close the DB fence around the bounded credential write."""
    try:
        sandbox.verify_preflight()
    except (SandboxError, ValueError, OSError):
        raise HTTPException(409, 'SDK 容器身份尚未确认') from None
    with Session(engine()) as db:
        try:
            # Limits apply only to this transaction; network/pool failures are
            # still distinct failures and must not be reported as a fence.
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            _lock(db, workspace, shared=True)
            row = _get(db, workspace, journal_id)
            if not _matches(row, sandbox, workspace) or row.data.get('state') != 'starting':
                raise HTTPException(409, '隔离环境或原请求不匹配')
            snapshot = acquire_snapshot(db, workspace, row.data['connection_id'])
            if (snapshot['version'] != row.data['generation'] or snapshot['host'] != sandbox.spec.host
                    or snapshot['port'] != sandbox.spec.port):
                raise HTTPException(403, '连接授权或隔离目标已变化')
            frame = init_frame(snapshot['host'], snapshot['port'],
                {item['name']:item['value'] for item in snapshot['http_headers']},
                {item['name']:item['value'] for item in snapshot['ws_headers']})
            sandbox.send_init(frame)
            db.commit()
        except BaseException:
            db.rollback()
            raise


def mark_unverified(db, workspace, journal_id):
    """After coordinator loss, block restarts without claiming a fence."""
    _lock(db, workspace)
    row = _get(db, workspace, journal_id, lock=True)
    if row.data.get('state') == 'closed':
        return 'closed'
    if row.data.get('state') not in ('starting', 'active', 'cleanup_unverified'):
        raise HTTPException(409, '隔离环境记录无法核对')
    row.data = {**row.data, 'state': 'cleanup_unverified'}; row.version += 1; db.flush()
    return 'cleanup_unverified'


def pending(db, workspace):
    _lock(db, workspace, shared=True)
    rows = db.scalars(select(Job).where(Job.workspace_id == workspace, Job.kind == KIND,
                      Job.status == 'internal').order_by(Job.created_at)).all()
    return [{'id': row.id, **row.data} for row in rows if row.data.get('state') != 'closed']


class _RecoveryFailure(Exception):
    """Only fixed categories may be returned; no raw daemon output is stored."""


class _RecoveryDocker:
    """Narrow local Docker access for explicitly recorded experimental assets."""
    def verify_daemon(self):
        if not hasattr(socket, 'SO_PEERCRED'):
            raise _RecoveryFailure('daemon_unavailable')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect('/var/run/docker.sock')
                pid, _, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET,
                    socket.SO_PEERCRED, struct.calcsize('3i')))
            if pid <= 1 or os.path.basename(os.readlink('/proc/%d/exe' % pid)) != 'dockerd':
                raise ValueError()
            namespaces = [os.stat(path) for path in
                ('/proc/1/ns/pid', '/proc/self/ns/pid', '/proc/%d/ns/pid' % pid)]
            if len({(item.st_dev, item.st_ino) for item in namespaces}) != 1:
                raise ValueError()
        except (OSError, ValueError, AttributeError, struct.error):
            raise _RecoveryFailure('daemon_unavailable') from None

    def _run(self, args):
        try:
            result = subprocess.run(['/usr/bin/docker', *args], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False,
                env={'PATH':'/usr/bin:/bin','LANG':'C','DOCKER_HOST':'unix:///var/run/docker.sock'})
            if len(result.stdout) > 2*1024*1024:
                raise _RecoveryFailure('inspection_unavailable')
            return result
        except (OSError, subprocess.SubprocessError):
            raise _RecoveryFailure('inspection_unavailable') from None

    def containers(self):
        result = self._run(['ps', '-a', '--no-trunc', '--format', '{{.Names}}'])
        if result.returncode:
            raise _RecoveryFailure('inspection_unavailable')
        return set(result.stdout.decode('utf-8', 'strict').splitlines())

    def networks(self):
        result = self._run(['network', 'ls', '--no-trunc', '--quiet'])
        if result.returncode:
            raise _RecoveryFailure('inspection_unavailable')
        result_ids = result.stdout.decode('ascii', 'strict').splitlines()
        if any(not _NETWORK_ID.fullmatch(value) for value in result_ids):
            raise _RecoveryFailure('inspection_unavailable')
        return set(result_ids)

    def inspect_container(self, name, identity=None):
        names = self.containers()
        result = self._run(['ps', '-a', '--no-trunc', '--format', '{{.ID}}'])
        if result.returncode:
            raise _RecoveryFailure('inspection_unavailable')
        ids = result.stdout.decode('ascii', 'strict').splitlines()
        if any(not _NETWORK_ID.fullmatch(value) for value in ids):
            raise _RecoveryFailure('inspection_unavailable')
        if identity and identity in ids:
            selector = identity
        elif name in names:
            selector = name
        else:
            return None
        result = self._run(['inspect', '--format', '{{json .}}', selector])
        if result.returncode:
            raise _RecoveryFailure('inspection_unavailable')
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict): raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise _RecoveryFailure('inspection_unavailable') from None

    def inspect_network(self, identity):
        if identity not in self.networks():
            return None
        result = self._run(['network', 'inspect', '--format', '{{json .}}', identity])
        if result.returncode:
            raise _RecoveryFailure('inspection_unavailable')
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict): raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise _RecoveryFailure('inspection_unavailable') from None

    def remove_container(self, identity):
        if not _NETWORK_ID.fullmatch(identity): raise _RecoveryFailure('identity_unverified')
        result = self._run(['rm', '--force', identity])
        if result.returncode: raise _RecoveryFailure('cleanup_unverified')

    def remove_network(self, identity):
        if not _NETWORK_ID.fullmatch(identity): raise _RecoveryFailure('identity_unverified')
        result = self._run(['network', 'rm', identity])
        if result.returncode: raise _RecoveryFailure('cleanup_unverified')


def _durable_identity(row):
    """Validate every persisted selector before looking at Docker resources."""
    try:
        d = row.data
        if not isinstance(d, dict) or str(uuid.UUID(row.workspace_id)) != row.workspace_id:
            raise ValueError()
        if str(uuid.UUID(d['connection_id'])) != d['connection_id'] or type(d['generation']) is not int or d['generation'] < 1:
            raise ValueError()
        suffix = d['guard_name'].removeprefix('ga-dji2-guard-')
        if (not _SUFFIX.fullmatch(suffix) or d['guard_name'] != 'ga-dji2-guard-'+suffix
                or d['sdk_name'] != 'ga-dji2-sdk-'+suffix or d['network_name'] != 'ga-dji2-net-'+suffix
                or row.idempotency_key != 'dji-cloud2-sandbox-intent:'+suffix
                or d.get('network_mode', 'internal') not in ('internal', 'lan_test')):
            raise ValueError()
        for field in ('network_id', 'guard_container_id', 'sdk_container_id'):
            value = d.get(field)
            if value is not None and (not isinstance(value, str) or not _NETWORK_ID.fullmatch(value)):
                raise ValueError()
        for field in ('sdk_image', 'guard_image'):
            if not isinstance(d.get(field), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', d[field]):
                raise ValueError()
        return dict(d), suffix
    except (KeyError, AttributeError, TypeError, ValueError):
        raise HTTPException(409, '隔离环境身份无法核对') from None


def _validate_owned_container(info, expected_id, name, component, suffix, image):
    if info is None:
        return
    labels = (info.get('Config') or {}).get('Labels') or {}
    if (not expected_id or info.get('Id') != expected_id or info.get('Name') != '/'+name
            or info.get('Image') != image or labels.get('org.generic-agents.component') != component
            or labels.get('org.generic-agents.lease') != suffix):
        raise _RecoveryFailure('identity_unverified')


def _validate_owned_network(info, data, suffix):
    if info is None:
        return
    labels = info.get('Labels') or {}
    allowed_internal = data.get('network_mode', 'internal') == 'internal'
    containers = info.get('Containers') or {}
    if (not isinstance(containers, dict) or info.get('Id') != data['network_id']
            or info.get('Name') != data['network_name'] or info.get('Driver') != 'bridge'
            or info.get('Scope') != 'local' or info.get('Internal') is not allowed_internal
            or labels.get('org.generic-agents.component') != 'dji-cloud2-test-network'
            or labels.get('org.generic-agents.lease') != suffix):
        raise _RecoveryFailure('identity_unverified')
    owned = {value for value in (data.get('guard_container_id'), data.get('sdk_container_id')) if value}
    if any(identity not in owned for identity in containers):
        raise _RecoveryFailure('network_in_use')


def _clear_owned(data, suffix, docker, lease):
    """Validate all resources before mutating any; operate only by immutable ID."""
    docker.verify_daemon()
    lease.check(); lease.connection.commit()
    sdk = docker.inspect_container(data['sdk_name'], data.get('sdk_container_id'))
    guard = docker.inspect_container(data['guard_name'], data.get('guard_container_id'))
    network = docker.inspect_network(data['network_id']) if data.get('network_id') else None
    _validate_owned_container(sdk, data.get('sdk_container_id'), data['sdk_name'],
        'dji-cloud2-sdk', suffix, data['sdk_image'])
    _validate_owned_container(guard, data.get('guard_container_id'), data['guard_name'],
        'dji-cloud2-egress', suffix, data['guard_image'])
    _validate_owned_network(network, data, suffix)
    # Unknown network IDs or pending creation can appear later. Never claim
    # the intent closed, even if all current objects are absent.
    for obj in (sdk, guard):
        if obj is not None:
            lease.check(); lease.connection.commit()
            docker.remove_container(obj['Id'])
    if network is not None:
        lease.check(); lease.connection.commit()
        refreshed = docker.inspect_network(data['network_id'])
        _validate_owned_network(refreshed, data, suffix)
        if refreshed is not None:
            if refreshed.get('Containers'): raise _RecoveryFailure('network_in_use')
            docker.remove_network(data['network_id'])
    lease.check(); lease.connection.commit()
    if (docker.inspect_container(data['sdk_name'], data.get('sdk_container_id')) is not None
            or docker.inspect_container(data['guard_name'], data.get('guard_container_id')) is not None):
        raise _RecoveryFailure('cleanup_unverified')
    if data.get('network_id') and data['network_id'] in docker.networks():
        raise _RecoveryFailure('cleanup_unverified')
    return 'resources_absent_unverified' if data.get('network_id') else 'creation_unresolved'


def recover_owned(workspace, journal_id, *, _docker_factory=_RecoveryDocker):
    """Reconcile one journal without inventing a FENCED receipt or reopening it.

    This is an experimental internal operation, not an HTTP endpoint or an
    authorization grant. Every mutation is limited to immutable, recorded IDs.
    A user-facing caller must authenticate workspace membership separately.
    """
    from .mqtt_subscriber import subscription_lease, LeaseBusy
    with Session(engine()) as db:
        row = _get(db, workspace, journal_id)
        initial, _ = _durable_identity(row)
    try:
        with subscription_lease('dji-cloud2:'+workspace+':'+initial['connection_id']) as lease:
            lease.check(); lease.connection.commit()
            with Session(engine()) as db:
                _lock(db, workspace)
                row = _get(db, workspace, journal_id, lock=True)
                data, suffix = _durable_identity(row)
                if data['connection_id'] != initial['connection_id']:
                    raise HTTPException(409, '隔离环境已变化')
                if data.get('state') == 'closed':
                    return {'state':'closed', 'outcome':'already_closed'}
                if data.get('state') not in ('starting', 'active', 'cleanup_unverified'):
                    raise HTTPException(409, '隔离环境状态无法核对')
                row.data = {**data, 'state':'cleanup_unverified', 'recovery_outcome':'checking'}
                row.version += 1; expected_version = row.version; db.commit()
            try:
                outcome = _clear_owned(data, suffix, _docker_factory(), lease)
            except _RecoveryFailure as error:
                outcome = str(error) if str(error) in {'daemon_unavailable','inspection_unavailable',
                    'identity_unverified','network_in_use','cleanup_unverified'} else 'inspection_unavailable'
            except (KeyError, ValueError, TypeError, UnicodeError, OSError):
                outcome = 'inspection_unavailable'
            lease.check(); lease.connection.commit()
            with Session(engine()) as db:
                _lock(db, workspace)
                row = _get(db, workspace, journal_id, lock=True)
                current, _ = _durable_identity(row)
                if row.version != expected_version or current.get('state') != 'cleanup_unverified':
                    raise HTTPException(409, '隔离环境状态已变化，请重新核对')
                row.data = {**current, 'recovery_outcome':outcome, 'recovery_at':now().isoformat()}
                row.version += 1; db.commit()
            return {'state':'cleanup_unverified', 'outcome':outcome}
    except LeaseBusy:
        raise HTTPException(409, '该连接仍有运行中的处理器，请稍后重试') from None
