"""Experimental durable intent journal for isolated Cloud API 2 sandboxes.

Not wired to the production worker. A start with an unknown Docker result must
remain unresolved; absence from one Docker listing cannot prove that a timed
out create will not complete later. The caller owns the per-connection lease.
"""
from __future__ import annotations

import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException

from .db import engine
from .dji_cloud2_connections import _lock, acquire_snapshot, is_current
from .dji_cloud2_process import init_frame
from .dji_cloud2_sandbox import DockerSandbox, SandboxError, StopResult
from .models import Job

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
    row.data = {**row.data, 'network_id': sandbox._owned_network_id}
    db.flush()


def mark_active(db, workspace, journal_id, sandbox):
    try:
        sdk_id = sandbox.verify_sdk_ready()
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
    row.data = {**row.data, 'state': 'active', 'started_confirmed': True, 'sdk_container_id': sdk_id}; db.flush()


def mark_stop(db, workspace, journal_id, sandbox, result):
    """Do not close an uncertain create or an unverified network fence."""
    if not isinstance(sandbox, DockerSandbox) or not isinstance(result, StopResult) or result is not sandbox._last_stop_result:
        raise ValueError('invalid stop result')
    _lock(db, workspace)
    row = _get(db, workspace, journal_id, lock=True)
    if (not _matches(row, sandbox, workspace)
            or sandbox._owned_network_id != row.data.get('network_id')):
        raise HTTPException(409, '停止回执与隔离环境不匹配')
    state = row.data.get('state')
    if state == 'closed':
        return 'closed'
    if state not in ('starting', 'active', 'cleanup_unverified'):
        raise HTTPException(409, '隔离环境记录无法核对')
    # A crash or timeout before mark_active can leave a late daemon operation.
    # No single negative listing or caller-provided cleanup result proves it
    # cannot appear later; keep the record as a manual recovery blocker.
    confirmed = row.data.get('started_confirmed') is True and result.confirmed
    new_state = 'closed' if confirmed else 'cleanup_unverified'
    row.data = {**row.data, 'state': new_state}; db.flush()
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
    row.data = {**row.data, 'state': 'cleanup_unverified'}; db.flush()
    return 'cleanup_unverified'


def pending(db, workspace):
    _lock(db, workspace, shared=True)
    rows = db.scalars(select(Job).where(Job.workspace_id == workspace, Job.kind == KIND,
                      Job.status == 'internal').order_by(Job.created_at)).all()
    return [{'id': row.id, **row.data} for row in rows if row.data.get('state') != 'closed']
