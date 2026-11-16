"""Server-owned, capability-specific simulated device tool bindings.

Registration never decrypts credentials or makes network requests. Dispatch
uses current authority; an original invocation's receipt may use only its
independently pinned historical ciphertext, never the connection's new token.
"""
from __future__ import annotations
import copy
import hashlib
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Manifest, digest
from .models import Audit, Capability, Invocation, Job, SensorDevice, uid
from .services import audit, check_version, scoped, validate
from . import device_gateway_store_v268 as store
from .device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from .device_gateway_models_v268 import DeviceGatewayDeviceGrantV268 as Grant
from .device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot

NAMESPACE = 'device_gateway_v269'
CONTRACT = 'device-gateway-tool-contract-v269'
BINDING = 'device-gateway-tool-binding-v269'
SCOPE = 'device-gateway-tool-v269'
ISSUED = 'device.gateway_tool_issued_v269'
ISSUED_SCHEMA = 'device-gateway-tool-issued-v269'
OPERATION = 'register_tool_v269'
ACTIONS = ('get_status', 'read_telemetry')
ERROR = '设备工具绑定、权限或原凭据快照无法核对，未发送请求'
_PERMIT_KEY = 'device_gateway_tool_registration_v269'
_PERMIT = object()
_BINDING_FIELDS = {'schema', 'connection_id', 'connection_version', 'credential_revision',
    'origin', 'protocol', 'grant_id', 'grant_version', 'device_external_id', 'sensor_device_id',
    'discovery_job_id', 'catalogue_digest', 'grant_actor_id', 'grant_actions', 'action'}


def _need(ok, message=ERROR, status=409):
    if not ok:
        raise HTTPException(status, message)


def applies(manifest):
    """Reserved fragments cannot be removed one at a time to enter legacy IO."""
    if type(manifest) is not dict:
        return False
    specification = manifest.get('specification')
    execution = manifest.get('execution')
    specification = specification if type(specification) is dict else {}
    execution = execution if type(execution) is dict else {}
    return (manifest.get('namespace') == NAMESPACE
        or specification.get('schema') == CONTRACT
        or 'device_gateway_binding' in specification
        or 'credential_scope' in execution
        or 'device_gateway_connection_id' in execution
        or any(type(execution.get(key)) is str and
               execution[key].split('?', 1)[0].split('#', 1)[0].rstrip('/').endswith(
                   ('/registered-tools/invoke', '/registered-tools/receipts'))
               for key in ('endpoint', 'receipt_endpoint')))


def guard_registration(db, p, value):
    if applies(value) and db.info.get(_PERMIT_KEY) is not _PERMIT:
        raise HTTPException(422, '设备网关工具只能通过连接的明确设备授权登记，不能手抄或复制清单')


@contextmanager
def _registration_permit(db):
    previous = db.info.get(_PERMIT_KEY)
    db.info[_PERMIT_KEY] = _PERMIT
    try:
        yield
    finally:
        if previous is None:
            db.info.pop(_PERMIT_KEY, None)
        else:
            db.info[_PERMIT_KEY] = previous


def _cipher_hash(snapshot):
    _need(type(snapshot.token_cipher) is str and bool(snapshot.token_cipher))
    return hashlib.sha256(snapshot.token_cipher.encode('utf-8')).hexdigest()


def _checked_binding(value):
    _need(type(value) is dict and set(value) == _BINDING_FIELDS)
    for field in ('connection_id', 'grant_id', 'sensor_device_id', 'discovery_job_id', 'grant_actor_id'):
        try:
            store.canonical_uuid(value[field])
        except HTTPException:
            raise HTTPException(409, ERROR) from None
    _need(value['schema'] == BINDING and value['protocol'] == store.PROTOCOL
        and value['action'] in ACTIONS and type(value['device_external_id']) is str
        and 1 <= len(value['device_external_id']) <= 64
        and store.KEY.fullmatch(value['device_external_id']) is not None
        and type(value['catalogue_digest']) is str and len(value['catalogue_digest']) == 64
        and all(c in '0123456789abcdef' for c in value['catalogue_digest']))
    _need(all(type(value[field]) is int and value[field] >= 1
              for field in ('connection_version', 'credential_revision', 'grant_version')))
    actions = value['grant_actions']
    _need(type(actions) is list and bool(actions) and all(type(a) is str for a in actions)
        and actions == sorted(set(actions)) and set(actions) <= store.ACTIONS
        and value['action'] in actions)
    from .gateway_credentials_v268 import validate_origin
    validate_origin(value['origin'])  # Public exact-origin policy only; no key read.
    return copy.deepcopy(value)


def _manifest(binding):
    from .device_registered_tool_adapter_v269 import output_schema, RECEIPT_SCHEMA
    b = _checked_binding(binding)
    properties = {
        'device_id': {'type': 'string', 'const': b['device_external_id']},
        'action': {'const': b['action']},
        'expected_revision': {'type': 'null', 'const': None},
        'arguments': {'type': 'object', 'const': {}},
        'task_id': {'type': 'string', 'pattern':
                    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},
        'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                        'pattern': r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'},
    }
    output = output_schema()
    output['properties']['device_id'] = {'type': 'string', 'const': b['device_external_id']}
    output['properties']['action'] = {'const': b['action']}
    return Manifest(contract_version='1.0', kind='tool', namespace=NAMESPACE,
        name='device_' + b['grant_id'].replace('-', '') + '_' + b['action'],
        version=f"{b['connection_version']}.{b['grant_version']}.{b['credential_revision']}",
        owner='设备网关服务器', description='提交已授权模拟设备的原号采样请求并核对供应方持久回执；不创建现场观测。',
        input_schema={'type': 'object', 'additionalProperties': False,
                      'required': list(properties), 'properties': properties},
        output_schema=output, allowed_agent_roles=['business', 'perception'],
        execution={'adapter': 'http', 'credential_scope': SCOPE,
            'device_gateway_connection_id': b['connection_id'],
            'endpoint': b['origin'] + '/registered-tools/invoke',
            'receipt_endpoint': b['origin'] + '/registered-tools/receipts',
            'receipt_protocol': RECEIPT_SCHEMA, 'dispatch_scope': 'registered_tool_plan_v201',
            'approval_policy': 'admin', 'side_effect': True,
            'input_bindings': {'task_id': 'task_id', 'request_key': 'request_key'}},
        data_policy={'cloud_allowed': False, 'external_allowed': True,
            'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False,
            'physical_device_connected': False, 'test_only': True},
        budget={'max_attempts': 1, 'max_concurrency': 1}, dependencies=[],
        specification={'schema': CONTRACT, 'simulation_only': True, 'device_gateway_binding': b}
    ).model_dump(mode='json')


def _binding(connection, grant, action):
    return _checked_binding({'schema': BINDING, 'connection_id': connection.id,
        'connection_version': connection.version, 'credential_revision': connection.credential_revision,
        'origin': connection.origin, 'protocol': connection.protocol,
        'grant_id': grant.id, 'grant_version': grant.version,
        'device_external_id': grant.device_external_id, 'sensor_device_id': grant.sensor_device_id,
        'discovery_job_id': grant.discovery_job_id, 'catalogue_digest': grant.catalogue_digest,
        'grant_actor_id': grant.actor_id, 'grant_actions': list(grant.data.get('actions', [])), 'action': action})


def _historical_grant(db, p, binding):
    b = binding
    proof = {'connection_id': b['connection_id'], 'device_external_id': b['device_external_id'],
        'sensor_device_id': b['sensor_device_id'], 'actions': b['grant_actions'],
        'discovery_job_id': b['discovery_job_id'], 'catalogue_digest': b['catalogue_digest'],
        'connection_version': b['connection_version'], 'credential_revision': b['credential_revision'],
        'grant_version': b['grant_version'], 'actor_id': b['grant_actor_id']}
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == b['grant_id'], Audit.action == 'device.gateway_granted_v268',
        Audit.data['grant_version'].as_integer() == b['grant_version'])))
    _need(len(rows) == 1 and rows[0].actor_id == b['grant_actor_id'] and rows[0].data == proof)
    job = db.scalar(select(Job).where(Job.id == b['discovery_job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == store.KIND).execution_options(populate_existing=True))
    _need(job is not None and job.status == 'succeeded' and store.admitted(db, job)
        and store.completed(db, job) and job.data.get('connection_id') == b['connection_id']
        and job.data.get('connection_version') == b['connection_version']
        and job.data.get('credential_revision') == b['credential_revision']
        and job.data.get('origin') == b['origin'] and job.data.get('protocol') == b['protocol']
        and job.data.get('catalogue_digest') == b['catalogue_digest'])
    devices = [d for d in job.data['devices'] if d['external_id'] == b['device_external_id']]
    _need(len(devices) == 1 and set(b['grant_actions']) <= set(devices[0]['actions']))


def _identity(db, p, binding):
    row = db.scalar(select(SensorDevice).where(SensorDevice.id == binding['sensor_device_id'],
        SensorDevice.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(row is not None and row.identity_scheme == 'supplier_device_id'
        and row.identity_issuer == 'gateway:' + binding['connection_id']
        and row.identity_reference == binding['device_external_id'])
    return row  # A review identity is permitted for explicit simulated reads only.


def _issued(db, p, cap, binding):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == cap.id, Audit.action == ISSUED)))
    _need(len(rows) == 1)
    proof = rows[0].data
    _need(type(proof) is dict and type(proof.get('snapshot_id')) is str)
    snapshot = db.scalar(select(Snapshot).where(Snapshot.id == proof['snapshot_id'],
        Snapshot.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(snapshot is not None and snapshot.connection_id == binding['connection_id']
        and snapshot.credential_revision == binding['credential_revision'])
    expected = {'schema': ISSUED_SCHEMA, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'actor_id': rows[0].actor_id, 'binding': binding, 'snapshot_id': snapshot.id,
        'snapshot_cipher_sha256': _cipher_hash(snapshot)}
    _need(proof == expected)
    return snapshot


def _actual_invocation(db, p, cap, inputs, key, invocation):
    _need(isinstance(invocation, Invocation) and type(invocation.id) is str)
    row = db.scalar(select(Invocation).where(Invocation.id == invocation.id,
        Invocation.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(row is not None and row.capability_id == cap.id and row.idempotency_key == key
        and row.data.get('capability_hash') == cap.content_hash)
    actual_inputs = row.data.get('inputs')
    approval = row.data.get('approval')
    _need(type(actual_inputs) is dict and row.data.get('input_hash') == digest(actual_inputs)
        and (inputs is None or actual_inputs == inputs))
    validate(cap.data['input_schema'], actual_inputs)
    store.canonical_uuid(actual_inputs['task_id'])
    _need(actual_inputs['request_key'] == key and row.data.get('role') in cap.data['allowed_agent_roles']
        and type(approval) is dict and approval.get('schema') == 'workflow-registered-tool-child-approval-v201'
        and approval.get('capability_id') == cap.id and approval.get('capability_hash') == cap.content_hash
        and approval.get('input_hash') == digest(actual_inputs)
        and approval.get('request_key_hash') == digest(key)
        and approval.get('task_id') == actual_inputs['task_id'])
    return row


def validate_tool(db, p, cap, purpose='prepare', inputs=None, key=None, invocation=None):
    """Return server-only ORM context. Never decrypt or serialize this result."""
    _need(purpose in {'prepare', 'dispatch', 'receipt'} and isinstance(cap, Capability))
    store.member(db, p, admin=True)
    actual = db.scalar(select(Capability).where(Capability.id == cap.id,
        Capability.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(actual is not None and actual.kind == 'tool' and applies(actual.data)
        and actual.content_hash == digest(actual.data))
    specification = actual.data.get('specification', {})
    _need(type(specification) is dict)
    b = _checked_binding(specification.get('device_gateway_binding'))
    _need(actual.data == _manifest(b) and actual.namespace == actual.data['namespace']
        and actual.name == actual.data['name'] and actual.release_version == actual.data['version'])
    _need((purpose == 'prepare' and actual.status in {'draft', 'validated', 'published'})
        or purpose == 'dispatch' and actual.status == 'published' or purpose == 'receipt')
    if purpose != 'receipt' and (actual.status in {'validated', 'published'} or purpose == 'dispatch'):
        _need(actual.validated_hash == actual.content_hash)
    if purpose == 'receipt':
        published = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == actual.id, Audit.action == 'capability.publish')))
        _need(len(published) == 1 and published[0].data.get('hash') == actual.content_hash)
    snapshot = _issued(db, p, actual, b)
    connection = db.scalar(select(Connection).where(Connection.id == b['connection_id'],
        Connection.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(connection is not None and connection.origin == b['origin'] and connection.protocol == b['protocol'])
    _historical_grant(db, p, b)
    grant = db.scalar(select(Grant).where(Grant.id == b['grant_id'],
        Grant.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    if purpose != 'receipt':
        _need(connection.status == 'enabled' and connection.version == b['connection_version']
            and connection.credential_revision == b['credential_revision']
            and connection.token_cipher == snapshot.token_cipher
            and grant is not None and grant.connection_id == connection.id
            and grant.version == b['grant_version'] and _binding(connection, grant, b['action']) == b
            and store.grant_view(db, connection, grant)['effective'])
        _identity(db, p, b)
    if purpose in {'dispatch', 'receipt'}:
        if purpose == 'receipt' and inputs is None:
            invocation = _actual_invocation(db, p, actual, None, key, invocation)
            inputs = copy.deepcopy(invocation.data['inputs'])
        _need(type(inputs) is dict and type(key) is str and store.KEY.fullmatch(key) is not None)
        validate(actual.data['input_schema'], inputs)
        store.canonical_uuid(inputs['task_id'])
        _need(inputs['request_key'] == key)
        if purpose == 'receipt' or invocation is not None:
            invocation = _actual_invocation(db, p, actual, inputs, key, invocation)
    return {'purpose': purpose, 'capability': actual, 'binding': b, 'connection': connection,
        'grant': grant, 'snapshot': snapshot, 'invocation': invocation,
        'endpoint': b['origin'] + '/registered-tools/invoke',
        'receipt_endpoint': b['origin'] + '/registered-tools/receipts'}


def _snapshot(db, p, connection):
    # Both server-issued families may own the same immutable revision. Missing
    # ciphertext with either issuance proof is damage, never a new credential.
    from .device_gateway_credential_snapshots_v270 import ensure_snapshot
    return ensure_snapshot(db,p,connection)


def _capability_view(cap):
    return {'id': cap.id, 'hash': cap.content_hash, 'version': cap.version,
        'release_version': cap.release_version, 'status': cap.status}


def create_tool(db, p, body, *, connection_id=None):
    """One explicit actor nonce registers, validates and publishes one exact tool."""
    identifier = store.canonical_uuid(connection_id or getattr(body, 'connection_id', None))
    cmd, prior = store.command(db, p, OPERATION, body, identifier)
    if prior is not None:
        return prior
    connection = store.checked_connection(db, p, identifier, body.expected_version)
    grant = scoped(db, Grant, str(body.grant_id), p, True)
    check_version(grant, body.grant_expected_version)
    _need(grant.connection_id == connection.id and body.action in ACTIONS
        and store.grant_view(db, connection, grant)['effective'])
    b = _binding(connection, grant, body.action)
    _identity(db, p, b); _historical_grant(db, p, b)
    value = _manifest(b)
    snapshot = _snapshot(db, p, connection)
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == value['namespace'], Capability.name == value['name'],
        Capability.release_version == value['version']).with_for_update())
    from . import services
    if cap is None:
        with _registration_permit(db):
            cap = services.register(db, p, Manifest.model_validate(value))
        proof = {'schema': ISSUED_SCHEMA, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'actor_id': p.user_id, 'binding': b, 'snapshot_id': snapshot.id,
            'snapshot_cipher_sha256': _cipher_hash(snapshot)}
        audit(db, p, ISSUED, cap.id, proof); db.flush()
        validate_tool(db, p, cap)
        services.validate_capability(db, p, cap.id, cap.version)
        validate_tool(db, p, cap)
        services.publish(db, p, cap.id, cap.version, body.reason)
    else:
        _need(cap.data == value and cap.status == 'published')
        validate_tool(db, p, cap)
    store.accept_command(db, p, cmd, connection)
    result = {'request_id': cmd.request_id, 'operation': OPERATION,
        'connection_id': connection.id, 'connection': store.connection_view(db, connection),
        'discovery': None, 'grant': store.grant_view(db, connection, grant),
        'capability': _capability_view(cap)}
    cmd.data = {**cmd.data, 'result': result}; db.add(cmd)
    audit(db, p, 'device.gateway_command_completed_v268', cmd.id,
        {'operation': cmd.operation, 'request_id': cmd.request_id, 'connection_id': connection.id,
         'payload_hmac': cmd.payload_hmac, 'result_hash': digest(result)})
    db.commit()
    return result


def list_tools(db, p, connection_id):
    store.member(db, p)
    identifier = store.canonical_uuid(connection_id)
    connection = db.scalar(select(Connection).where(Connection.id == identifier,
        Connection.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    _need(connection is not None, '记录不存在或不在当前工作区', 404)
    rows = list(db.scalars(select(Capability).join(Audit,
        (Audit.object_id == Capability.id) & (Audit.workspace_id == Capability.workspace_id))
        .where(Capability.workspace_id == p.workspace_id, Audit.action == ISSUED,
               Audit.data['binding']['connection_id'].as_string() == connection.id)
        .order_by(Capability.created_at.desc(), Capability.id.desc()).limit(51)))
    result = []
    for cap in rows[:50]:
        specification = cap.data.get('specification') if type(cap.data) is dict else None
        b = specification.get('device_gateway_binding') if type(specification) is dict else None
        valid = False
        try:
            checked = _checked_binding(b)
            _need(cap.data == _manifest(checked) and cap.content_hash == digest(cap.data)
                and cap.kind == 'tool' and cap.namespace == cap.data['namespace']
                and cap.name == cap.data['name'] and cap.release_version == cap.data['version'])
            _issued(db, p, cap, checked)
            valid = True
        except HTTPException:
            pass
        reason = 'integrity_invalid'
        if valid:
            if cap.status != 'published' or cap.validated_hash != cap.content_hash:
                reason = 'capability_not_published'
            elif connection.status != 'enabled':
                reason = 'connection_paused'
            else:
                reason = 'authorization_changed'
                current_grant = db.scalar(select(Grant).where(Grant.id == b['grant_id'],
                    Grant.workspace_id == p.workspace_id).execution_options(populate_existing=True))
                try:
                    _need(current_grant is not None and current_grant.connection_id == connection.id
                        and _binding(connection, current_grant, b['action']) == b
                        and store.grant_view(db, connection, current_grant)['effective'])
                    _identity(db, p, b)
                    reason = 'current'
                except HTTPException:
                    pass
        result.append({**_capability_view(cap), 'connection_id': connection.id,
            'device_external_id': b['device_external_id'] if valid else None,
            'action': b['action'] if valid else None, 'integrity_valid': valid,
            'configuration_current': reason == 'current', 'availability_reason': reason,
            'simulation': True, 'field_verified': False, 'observation_created': False})
    return {'tools': result, 'has_more': len(rows) > 50}
