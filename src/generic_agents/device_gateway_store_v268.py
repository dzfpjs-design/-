"""Workspace-scoped gateway admission and secret-free public projections.

This slice authorizes a simulated catalogue entry. It never approves physical
identity, binds a capability or dispatches a device operation.
"""
from __future__ import annotations
import copy
import hmac
import math
import re
import unicodedata
from datetime import datetime
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, text
from .contracts import digest
from .models import Audit, Job, Membership, SensorDevice, User, uid
from .security import Principal
from .services import audit, check_version, scoped
from .device_gateway_models_v268 import (DeviceGatewayConnectionV268 as Connection,
    DeviceGatewayCommandV268 as Command, DeviceGatewayDeviceGrantV268 as Grant)

KIND = 'discover_device_gateway_v268'
PROTOCOL = 'procurement-simulator-v1'
QUEUED = 'device.gateway_discovery_queued_v268'
COMPLETED = 'device.gateway_discovery_completed_v268'
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
ACTIONS = {'get_status', 'read_telemetry', 'set_ptz', 'start_mission', 'return_to_dock', 'cancel_mission'}
PROFILE_ACTIONS = {
    'drone_dock': {'get_status', 'read_telemetry', 'start_mission', 'return_to_dock', 'cancel_mission'},
    'ptz_camera': {'get_status', 'read_telemetry', 'set_ptz'},
    'weather_station': {'get_status', 'read_telemetry'},
}
MARK = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False,
        'physical_device_connected': False}
DEVICE_FIELDS = {'external_id', 'namespace', 'profile_id', 'kind', 'name', 'revision', 'actions',
    'sampled_at', 'time_semantics', 'procurement_requirements', 'simulation_assumptions', 'source', *MARK}


def contains_secret(value, secret):
    # Inspect decoded values and keys. Serializing first would miss tokens
    # containing JSON escapes such as a quote or a backslash.
    if type(value) is str:
        return secret in value
    if type(value) is dict:
        return any(contains_secret(k, secret) or contains_secret(v, secret) for k, v in value.items())
    if type(value) is list:
        return any(contains_secret(item, secret) for item in value)
    return False


def canonical_uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '标识必须为标准小写UUID') from None
    return value


def lock(db, workspace_id):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
               {'key': 'device-gateway-v268:' + workspace_id})


def member(db, p, *, admin=False, locked=False):
    query = select(Membership.role).join(User, User.id == Membership.user_id).where(
        Membership.workspace_id == p.workspace_id, Membership.user_id == p.user_id,
        User.active.is_(True))
    if locked:
        query = query.with_for_update()
    role = db.scalar(query)
    if role is None or admin and role != 'admin':
        raise HTTPException(403, '当前工作区权限已撤销或不允许此操作')
    return Principal(p.user_id, p.workspace_id, role, p.session_id)


def text_value(value, maximum=200):
    if (type(value) is not str or not value.strip() or len(value) > maximum
            or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('invalid catalogue text')
    return value


def safe_tree(value, depth=0, budget=None):
    """Bounded procurement evidence, with no arbitrary executable/UI structure."""
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 6 or budget[0] > 512:
        raise ValueError('catalogue evidence too large')
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float) and math.isfinite(value) and abs(value) <= 10**15:
        return value
    if type(value) is str:
        return text_value(value, 500)
    if type(value) is list and len(value) <= 40:
        return [safe_tree(v, depth + 1, budget) for v in value]
    if type(value) is dict and len(value) <= 40:
        return {text_value(k, 100): safe_tree(v, depth + 1, budget) for k, v in value.items()}
    raise ValueError('invalid catalogue evidence')


def normalize_catalogue(value):
    """Only the published procurement simulator protocol, never hardware discovery."""
    if (type(value) is not dict or set(value) != {'devices', 'simulation', 'origin_kind', 'field_verified'}
            or value.get('simulation') is not True or value.get('field_verified') is not False
            or value.get('origin_kind') != 'simulated_equipment'
            or type(value.get('devices')) is not list or len(value['devices']) > 100):
        raise ValueError('invalid simulated catalogue')
    result = []; seen = set()
    fields = {'id', 'namespace', 'profile', 'revision', 'sampled_at', 'state', 'time_semantics', *MARK}
    profile_fields = {'id', 'kind', 'name', 'actions', 'procurement_requirements', 'simulation_assumptions', 'source'}
    for raw in value['devices']:
        if type(raw) is not dict or set(raw) != fields or any(raw.get(k) is not v if type(v) is bool else raw.get(k) != v for k, v in MARK.items()):
            raise ValueError('invalid simulated device')
        external = text_value(raw['id'], 128)
        if not KEY.fullmatch(external) or external in seen:
            raise ValueError('duplicate or invalid device identity')
        seen.add(external)
        namespace = text_value(raw['namespace'], 100)
        if (type(raw['revision']) is not int or raw['revision'] < 1
                or raw['time_semantics'] != 'simulator_sample_time' or type(raw['state']) is not dict):
            raise ValueError('invalid simulator revision or clock')
        sampled_at = datetime.fromisoformat(text_value(raw['sampled_at'], 80).replace('Z', '+00:00'))
        if sampled_at.tzinfo is None:
            raise ValueError('ambiguous simulator clock')
        safe_tree(raw['state'])
        profile = raw['profile']
        if type(profile) is not dict or set(profile) != profile_fields or profile.get('kind') not in PROFILE_ACTIONS:
            raise ValueError('unknown procurement profile')
        if any(type(profile[key]) is not dict for key in ('procurement_requirements', 'simulation_assumptions', 'source')):
            raise ValueError('invalid procurement evidence')
        actions = profile.get('actions')
        if (type(actions) is not list or not 1 <= len(actions) <= 6
                or any(type(a) is not str for a in actions) or len(actions) != len(set(actions))
                or not set(actions) <= PROFILE_ACTIONS[profile['kind']]):
            raise ValueError('invalid profile actions')
        result.append({'external_id': external, 'namespace': namespace,
            'profile_id': text_value(profile['id'], 128), 'kind': profile['kind'],
            'name': text_value(profile['name']), 'revision': raw['revision'], 'actions': sorted(actions),
            'sampled_at': raw['sampled_at'], 'time_semantics': 'simulator_sample_time',
            'procurement_requirements': safe_tree(profile['procurement_requirements']),
            'simulation_assumptions': safe_tree(profile['simulation_assumptions']),
            'source': safe_tree(profile['source']), **MARK})
    return sorted(result, key=lambda d: d['external_id'])


def admission(job):
    d = job.data
    return {'schema': 'device-gateway-discovery-admission-v268', 'job_id': job.id,
        'connection_id': d.get('connection_id'), 'actor_id': d.get('actor_id'),
        'command_id': d.get('command_id'), 'request_id': d.get('request_id'),
        'connection_version': d.get('connection_version'), 'credential_revision': d.get('credential_revision'),
        'origin': d.get('origin'), 'protocol': d.get('protocol'), 'payload_hmac': d.get('payload_hmac')}


def admitted(db, job):
    if job.kind != KIND or type(job.data) is not dict or job.data.get('schema') != 'device-gateway-discovery-job-v268':
        return False
    d = job.data
    cmd = db.scalar(select(Command).where(Command.workspace_id == job.workspace_id,
        Command.id == d.get('command_id'), Command.actor_id == d.get('actor_id'),
        Command.request_id == d.get('request_id'), Command.connection_id == d.get('connection_id'),
        Command.operation == 'discover', Command.payload_hmac == d.get('payload_hmac')))
    if not cmd:
        return False
    safe = cmd.data.get('safe_payload', {})
    if (safe.get('workspace_id') != job.workspace_id or safe.get('actor_id') != d.get('actor_id')
            or safe.get('connection_id') != d.get('connection_id') or safe.get('operation') != 'discover'
            or safe.get('body', {}).get('expected_version') != d.get('connection_version')
            or safe.get('body', {}).get('request_id') != d.get('request_id')):
        return False
    rows = db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.object_id == job.id, Audit.actor_id == d.get('actor_id'), Audit.action == QUEUED)).all()
    return len(rows) == 1 and rows[0].data == admission(job)


def completion(job, *, status=None, catalogue_digest=None, error_code=None):
    return {**admission(job), 'schema': 'device-gateway-discovery-completion-v268',
        'status': status or job.status,
        'catalogue_digest': catalogue_digest if catalogue_digest is not None else job.data.get('catalogue_digest'),
        'error_code': error_code if error_code is not None else job.data.get('error_code')}


def completed(db, job):
    rows = db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.object_id == job.id, Audit.actor_id == job.data.get('actor_id'), Audit.action == COMPLETED)).all()
    if len(rows) != 1 or rows[0].data != completion(job):
        return False
    if job.status != 'succeeded':
        return True
    devices = job.data.get('devices')
    if type(devices) is not list or len(devices) > 100:
        return False
    # Reconstruct just the declared protocol fields and normalize again.
    # Extra/raw remote fields cannot appear in a successful GET projection.
    try:
        if any(type(d) is not dict or set(d) != DEVICE_FIELDS for d in devices):
            return False
        raw = [{**MARK, 'id': d['external_id'], 'namespace': d['namespace'],
            'revision': d['revision'], 'sampled_at': d['sampled_at'], 'state': {},
            'time_semantics': d['time_semantics'], 'profile': {'id': d['profile_id'],
                **{k: d[k] for k in ('kind', 'name', 'actions', 'procurement_requirements',
                                     'simulation_assumptions', 'source')}}} for d in devices]
        normalized = normalize_catalogue({'devices': raw, 'simulation': True,
            'origin_kind': 'simulated_equipment', 'field_verified': False})
        return normalized == devices and digest(normalized) == job.data.get('catalogue_digest')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return False


def discovery_view(db, connection, job):
    d = job.data
    valid = admitted(db, job) and (job.status not in {'succeeded', 'failed'} or completed(db, job))
    effective = (valid and job.status == 'succeeded' and connection.status == 'enabled'
        and d.get('connection_version') == connection.version
        and d.get('credential_revision') == connection.credential_revision
        and d.get('origin') == connection.origin and d.get('protocol') == connection.protocol)
    return {'id': job.id, 'connection_id': connection.id, 'status': job.status if valid else 'invalid',
        'connection_version': d.get('connection_version'), 'credential_revision': d.get('credential_revision'),
        'created_at': job.created_at.isoformat(), 'completed_at': d.get('completed_at') if valid else None,
        'error_code': d.get('error_code') if valid else None,
        'catalogue_digest': d.get('catalogue_digest') if valid else None,
        'devices': copy.deepcopy(d.get('devices', [])) if valid and job.status == 'succeeded' else [],
        'effective': effective, 'integrity_error': None if valid else 'discovery_integrity_invalid'}


def grant_view(db, connection, row):
    active_actor = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == row.actor_id, User.active.is_(True), Membership.workspace_id == row.workspace_id,
        Membership.role == 'admin')) is not None
    proof = row.data.get('proof', {})
    audits = db.scalars(select(Audit).where(Audit.workspace_id == row.workspace_id,
        Audit.object_id == row.id, Audit.action == 'device.gateway_granted_v268',
        Audit.data['grant_version'].as_integer() == row.version)).all()
    integrity = len(audits) == 1 and audits[0].data == proof and proof == grant_proof(row)
    discovery = db.scalar(select(Job).where(Job.id == row.discovery_job_id,
        Job.workspace_id == row.workspace_id, Job.kind == KIND,
        Job.data['connection_id'].as_string() == row.connection_id))
    discovery_valid = (discovery is not None and admitted(db, discovery) and completed(db, discovery)
        and discovery.status == 'succeeded' and discovery.data.get('catalogue_digest') == row.catalogue_digest
        and discovery.data.get('connection_version') == row.connection_version
        and discovery.data.get('credential_revision') == row.credential_revision)
    return {'id': row.id, 'connection_id': row.connection_id, 'device_external_id': row.device_external_id,
        'sensor_device_id': row.sensor_device_id, 'actions': list(row.data.get('actions', [])),
        'discovery_job_id': row.discovery_job_id, 'catalogue_digest': row.catalogue_digest,
        'connection_version': row.connection_version, 'credential_revision': row.credential_revision,
        'version': row.version, 'created_at': row.created_at.isoformat(),
        'effective': bool(integrity and discovery_valid and active_actor and connection.status == 'enabled'
            and row.connection_version == connection.version and row.credential_revision == connection.credential_revision),
        'integrity_error': None if integrity and discovery_valid else 'grant_integrity_invalid'}


def grant_proof(row):
    return {'connection_id': row.connection_id, 'device_external_id': row.device_external_id,
        'sensor_device_id': row.sensor_device_id, 'actions': row.data.get('actions'),
        'discovery_job_id': row.discovery_job_id, 'catalogue_digest': row.catalogue_digest,
        'connection_version': row.connection_version, 'credential_revision': row.credential_revision,
        'grant_version': row.version, 'actor_id': row.actor_id}


def connection_view(db, row):
    job = db.scalar(select(Job).where(Job.workspace_id == row.workspace_id, Job.kind == KIND,
        Job.data['connection_id'].as_string() == row.id).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    grants = list(db.scalars(select(Grant).where(Grant.workspace_id == row.workspace_id,
        Grant.connection_id == row.id).order_by(Grant.created_at.desc(), Grant.id.desc()).limit(51)))
    return {'id': row.id, 'name': row.name, 'origin': row.origin, 'protocol': row.protocol,
        'status': row.status, 'version': row.version, 'credential_revision': row.credential_revision,
        'credential_configured': bool(row.token_cipher), 'created_at': row.created_at.isoformat(),
        'latest_discovery': discovery_view(db, row, job) if job else None,
        'grants': [grant_view(db, row, grant) for grant in grants[:50]], 'grants_has_more': len(grants) > 50}


def command(db, p, operation, body, connection_id=None):
    """One actor nonce identifies the exact safe payload plus secret HMAC."""
    from .gateway_credentials_v268 import request_fingerprint
    lock(db, p.workspace_id); member(db, p, admin=True, locked=True)
    safe_body = body.model_dump(mode='json', exclude={'token'})
    safe = {'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'operation': operation,
            'connection_id': connection_id, 'body': safe_body}
    secret = body.token.get_secret_value() if hasattr(body, 'token') else None
    if secret is not None and contains_secret(safe_body, secret):
        raise HTTPException(422, '请将凭据仅填写在令牌字段，名称和理由不可包含凭据')
    fingerprint = request_fingerprint(safe, secret)
    prior = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == str(body.request_id)))
    if prior:
        if prior.operation != operation or not hmac.compare_digest(prior.payload_hmac, fingerprint):
            raise HTTPException(409, '此原请求标识已经用于不同的内容或条件')
        return prior, command_result(db, p, prior)
    row = Command(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=str(body.request_id), connection_id=connection_id, operation=operation,
        payload_hmac=fingerprint, data={'safe_payload': safe}, version=1)
    return row, None


def command_result(db, p, cmd):
    accepted = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.actor_id == p.user_id, Audit.object_id == cmd.id,
        Audit.action == 'device.gateway_command_accepted_v268')))
    receipts = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.actor_id == p.user_id, Audit.object_id == cmd.id,
        Audit.action == 'device.gateway_command_completed_v268')))
    expected = {'operation': cmd.operation, 'request_id': cmd.request_id,
        'connection_id': cmd.connection_id, 'payload_hmac': cmd.payload_hmac}
    expected_completion = {**expected, 'result_hash': digest(cmd.data.get('result'))}
    if (len(accepted) != 1 or accepted[0].data != expected
            or len(receipts) != 1 or receipts[0].data != expected_completion):
        raise HTTPException(409, '原请求回执完整性核对失败，请保留原请求标识')
    return copy.deepcopy(cmd.data['result'])


def finish_command(db, p, cmd, connection, *, discovery=None, grant=None):
    db.flush()
    result = {'request_id': cmd.request_id, 'operation': cmd.operation,
        'connection_id': connection.id, 'connection': connection_view(db, connection),
        'discovery': discovery_view(db, connection, discovery) if discovery else None,
        'grant': grant_view(db, connection, grant) if grant else None}
    cmd.data = {**cmd.data, 'result': result}; db.add(cmd)
    audit(db, p, 'device.gateway_command_completed_v268', cmd.id,
          {'operation': cmd.operation, 'request_id': cmd.request_id,
           'connection_id': connection.id, 'payload_hmac': cmd.payload_hmac, 'result_hash': digest(result)})
    db.commit()
    return result


def accept_command(db, p, cmd, connection):
    cmd.connection_id = connection.id; db.add(cmd); db.flush()
    audit(db, p, 'device.gateway_command_accepted_v268', cmd.id,
          {'operation': cmd.operation, 'request_id': cmd.request_id,
           'connection_id': connection.id, 'payload_hmac': cmd.payload_hmac})


def checked_connection(db, p, identifier, expected):
    row = scoped(db, Connection, canonical_uuid(identifier), p, True)
    check_version(row, expected)
    return row
