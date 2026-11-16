"""Server-issued, isolated weather Sources for the unchanged V177 acquisition.

Source issuance follows a real final release and a real local extractor. It
creates no asset, device approval, observation, acquisition or receipt. Secrets
remain in the shared immutable ciphertext table and are opened only by the
dedicated acquisition transport.
"""
import copy
from contextlib import contextmanager
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import Manifest, digest
from .device_acquisition_adapter_v176 import FIELDS
from .device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from .device_gateway_models_v268 import DeviceGatewayDeviceGrantV268 as Grant
from .device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot
from .device_gateway_credential_snapshots_v270 import cipher_hash, ensure_snapshot
from . import device_gateway_store_v268 as store
from . import device_gateway_tools_v269 as tools
from . import http_observation_mapping_v177 as mapping
from . import http_observation_transport_v177 as transport
from .models import Audit, Capability, DomainInstance, InstanceRelease
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment
from .services import audit, check_version, scoped

NAMESPACE = 'device_gateway_source_v270'
CONTRACT = 'device-gateway-source-contract-v270'
BINDING = 'device-gateway-source-binding-v270'
BINDING_KEY = 'device_gateway_source_binding'
SCOPE = 'device-gateway-source-v270'
EXECUTION_KEY = 'managed_source_scope'
ISSUED = 'device.gateway_source_issued_v270'
ISSUED_SCHEMA = 'device-gateway-source-issued-v270'
OPERATION = 'register_source_v270'
DEVICE = 'sim-weather-01'
ERROR = '天气来源、明确授权、实例版本或原凭据快照无法核对，未发送请求'
_PERMIT_KEY = 'device_gateway_source_registration_v270'
_PERMIT = object()
_EXTRA = {'mapping', 'extractor_id', 'extractor_hash'}
_BINDING_FIELDS = tools._BINDING_FIELDS | _EXTRA


def need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise HTTPException(409, ERROR) from None
    return checked


def applies(value):
    if type(value) is not dict:
        return False
    spec = value.get('specification'); execution = value.get('execution')
    spec = spec if type(spec) is dict else {}
    execution = execution if type(execution) is dict else {}
    return (value.get('namespace') == NAMESPACE or spec.get('schema') == CONTRACT
        or BINDING_KEY in spec or EXECUTION_KEY in execution)


def managed(db, p, cap):
    """An independent issuance survives removal of every manifest marker."""
    need(isinstance(cap, Capability))
    return (cap.namespace == NAMESPACE or applies(cap.data)
        or db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == cap.id, Audit.action == ISSUED).limit(1)) is not None)


def guard_registration(db, p, value):
    if applies(value) and db.info.get(_PERMIT_KEY) is not _PERMIT:
        raise HTTPException(422, '托管天气来源只能通过真实连接授权和实例版本登记，不能复制或手抄清单')


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


@safe
def checked_binding(value):
    need(type(value) is dict and set(value) == _BINDING_FIELDS and value['schema'] == BINDING)
    # Reuse only the independently checked gateway Grant contract, never Tool
    # issuance, Invocation authority or the V201 transport.
    gateway = {key: copy.deepcopy(value[key]) for key in tools._BINDING_FIELDS}
    gateway['schema'] = tools.BINDING
    gateway = tools._checked_binding(gateway)
    need(gateway['device_external_id'] == DEVICE and gateway['action'] == 'read_telemetry')
    mapping._config(value['mapping'])
    need(set(value['mapping']['links']) <= FIELDS[DEVICE])
    mapping._uuid(value['extractor_id']); mapping._hash(value['extractor_hash'])
    return copy.deepcopy(value)


def _gateway_binding(binding):
    return {**{key: copy.deepcopy(binding[key]) for key in tools._BINDING_FIELDS}, 'schema': tools.BINDING}


def _cold(db, p, model, identity):
    row = db.scalar(select(model).where(model.id == identity,
        model.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(row is not None)
    return row


@safe
def manifest(binding):
    b = checked_binding(binding); config = b['mapping']; origin = b['origin']
    fields = sorted(link['field_key'] for link in config['links'].values())
    properties = {'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 128},
        'target_external_id': {'type': 'string'}, 'fields': {'type': 'array', 'minItems': 1,
            'uniqueItems': True, 'items': {'type': 'string', 'enum': fields}},
        'max_duration_seconds': {'type': 'integer', 'minimum': 1},
        'asset_external_id': {'const': DEVICE}, 'action': {'const': 'observe'},
        'max_cost_minor': {'type': 'integer', 'minimum': 0}, 'currency': {'const': 'CNY'}}
    receipt = {'schema': {'const': mapping.SUPPLIER},
        **{key: {'const': value} for key, value in mapping.MARK.items()},
        'request_key': {'type': 'string'}, 'asset_external_id': {'const': DEVICE},
        'target_external_id': {'type': 'string'}, 'receipt_id': {'type': 'string'},
        'status': {'enum': ['accepted', 'completed', 'completed_no_data']},
        'request_body_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'request_hash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'observation': {'type': ['object', 'null']}, 'supplier_receipt': {'type': 'object'}}
    return Manifest(kind='source', namespace=NAMESPACE, name='weather_' + digest(b)[:48],
        version=f"{b['connection_version']}.{b['grant_version']}.{b['credential_revision']}",
        owner='设备网关服务器', description='独立模拟环境中已授权天气设备的原号补采与回执核对；不代表现场气象。',
        allowed_agent_roles=['perception'],
        input_schema={'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties},
        output_schema={'type': 'object', 'required': list(receipt), 'properties': receipt},
        execution={'adapter': 'http', EXECUTION_KEY: SCOPE, 'side_effect': True,
            'endpoint': origin + '/acquisitions', 'receipt_endpoint': origin + '/acquisitions/receipts'},
        data_policy={'cloud_allowed': False, 'external_allowed': True, 'deployment': 'local',
            **mapping.MARK, 'test_only': True, 'physical_device_connected': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}, dependencies=[b['extractor_id']],
        specification={'schema': CONTRACT, BINDING_KEY: b, 'timezone': 'Asia/Shanghai',
            'simulation_environment_id': config['scope']['environment_id'],
            transport.MAPPING_KEY: copy.deepcopy(config),
            transport.EXTRACTOR_ID: b['extractor_id'], transport.EXTRACTOR_HASH: b['extractor_hash'],
            'observation_capability': {'fields': fields, 'modalities': ['numeric'],
                'actions': ['observe'], 'receipt_contract': 'request_asset_terminal_v1'}}
    ).model_dump(mode='json')


@safe
def issued_source(db, p, cap, binding):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == cap.id, Audit.action == ISSUED).execution_options(populate_existing=True)))
    need(len(rows) == 1 and type(rows[0].data) is dict)
    proof = rows[0].data; identity = proof.get('snapshot_id')
    store.canonical_uuid(identity)
    snapshot = db.scalar(select(Snapshot).where(Snapshot.id == identity,
        Snapshot.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(snapshot is not None and snapshot.connection_id == binding['connection_id']
        and snapshot.credential_revision == binding['credential_revision'])
    need(proof == {'schema': ISSUED_SCHEMA, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'actor_id': rows[0].actor_id, 'binding': binding, 'snapshot_id': snapshot.id,
        'snapshot_cipher_sha256': cipher_hash(snapshot)})
    return snapshot


@safe
def _recipe_scope(db, p, binding, *, live):
    from .simulation_loop_v176 import environment
    from .instance_data_receipts import verify_release
    from .services import published
    config = binding['mapping']; scope = config['scope']
    # Populate the real rows before legacy scoped helpers can reuse identities
    # already present in this worker's ORM identity map.
    for model, key in ((Environment, 'environment_id'), (DomainInstance, 'instance_id'),
                       (InstanceRelease, 'release_id'), (Capability, 'package_id')):
        _cold(db, p, model, scope[key])
    env, instance, release = environment(db, p, scope['environment_id'], live=live)
    if live:
        package = published(db, p, release.package_id, 'domain_package')
    else:
        package = _cold(db, p, Capability, release.package_id)
        need(package.kind == 'domain_package' and package.content_hash == digest(package.data)
            and package.validated_hash == package.content_hash)
        anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == package.id, Audit.action == 'capability.publish')))
        need(len(anchors) == 1 and anchors[0].data.get('hash') == package.content_hash)
    need(scope == {'workspace_id': p.workspace_id, 'environment_id': env.id,
        'instance_id': instance.id, 'release_id': release.id, 'package_id': package.id,
        'package_hash': package.content_hash, 'entity_type': scope['entity_type']}
        and release.status == 'prepared' and package.namespace == instance.namespace)
    plan = verify_release(db, p, release)
    schemas = package.data['specification']['entity_schemas']
    mapping._no_refs(schemas)
    selected = schemas[scope['entity_type']]
    need(type(selected) is dict and selected.get('type') == 'object'
        and type(selected.get('properties')) is dict)
    for link in config['links'].values():
        mapping._field_schema(selected['properties'].get(link['field_key']), link['unit'])
    units = {link['field_key']: link['unit'] for link in config['links'].values()}
    extractor = _cold(db, p, Capability, binding['extractor_id'])
    if live:
        extractor = transport._registered_rules(db, p, binding['extractor_id'], set(units),
            package_id=package.id, entity_type=scope['entity_type'], field_units=units)
    else:
        # An ended/paused business or a retired extractor does not grant a new
        # action, but must not force an already-sent receipt to use a new key.
        need(extractor.kind == 'tool'
            and extractor.content_hash == digest(extractor.data))
        published_anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == extractor.id, Audit.action == 'capability.publish')))
        need(len(published_anchors) == 1 and published_anchors[0].data.get('hash') == binding['extractor_hash'])
    need(extractor.content_hash == binding['extractor_hash']
        and extractor.data['data_policy'].get('deployment') == 'local')
    # The final real release must have an independently verified asynchronous
    # output declaration. A manual-only release is not a data-reception plan.
    need(type(plan) is dict and type(plan.get('businesses')) is list)
    declarations = [item for business in plan['businesses'] for item in business.get('outputs', [])
        if item.get('kind') == 'asynchronous_perception'
        and item.get('capability_id') == extractor.id
        and item.get('capability_hash') == extractor.content_hash
        and item.get('release_version') == extractor.release_version
        and item.get('output_schema') == extractor.data['output_schema']]
    need(bool(declarations))
    for declaration in declarations:
        need(type(declaration.get('declaration_kind')) is str and bool(declaration['declaration_kind']))
        mapping._hash(declaration.get('source_binding_hash'))
    return env, instance, release, package


@safe
def _scope(db, p, binding, source, *, live):
    need(isinstance(source, Capability) and source.workspace_id == p.workspace_id)
    scope = _recipe_scope(db, p, binding, live=live)
    config = binding['mapping']
    frozen = mapping.freeze_mapping(source.data, {**config['scope'], 'source_id': source.id},
        scope[3].data['specification']['entity_schemas'], config['links'])
    if live:
        transport._extractor(db, p, source, frozen)
    return scope


def _current_gateway(db, p, binding, connection, snapshot):
    grant = db.scalar(select(Grant).where(Grant.id == binding['grant_id'],
        Grant.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(connection.status == 'enabled' and connection.version == binding['connection_version']
        and connection.credential_revision == binding['credential_revision']
        and connection.token_cipher == snapshot.token_cipher and grant is not None
        and grant.connection_id == connection.id and grant.version == binding['grant_version']
        and tools._binding(connection, grant, 'read_telemetry') == _gateway_binding(binding)
        and store.grant_view(db, connection, grant)['effective'])
    device = tools._identity(db, p, binding)
    from .device_identity import active_admin
    need(device.status == 'enabled' and active_admin(db, p, device.approved_by),
        '请先通过统一设备核查确认天气设备，再登记或派发本来源')
    return grant, device


@safe
def validate_source(db, p, cap, purpose='prepare'):
    """Return server-only ORM context; not an acquisition or dispatch grant."""
    need(purpose in {'prepare', 'dispatch', 'receipt'} and isinstance(cap, Capability))
    store.member(db, p)
    actual = db.scalar(select(Capability).where(Capability.id == cap.id,
        Capability.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(actual is not None and actual.kind == 'source' and managed(db, p, actual)
        and actual.content_hash == digest(actual.data))
    binding = checked_binding(actual.data['specification'].get(BINDING_KEY))
    need(actual.data == manifest(binding) and actual.namespace == NAMESPACE
        and actual.name == actual.data['name'] and actual.release_version == actual.data['version'])
    need(purpose == 'receipt' or (actual.status in {'draft', 'validated', 'published'}
        if purpose == 'prepare' else actual.status == 'published'))
    if actual.status in {'validated', 'published'} or purpose == 'dispatch':
        need(actual.validated_hash == actual.content_hash)
    if purpose == 'receipt':
        anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == actual.id, Audit.action == 'capability.publish')))
        need(len(anchors) == 1 and anchors[0].data.get('hash') == actual.content_hash)
    snapshot = issued_source(db, p, actual, binding)
    connection = db.scalar(select(Connection).where(Connection.id == binding['connection_id'],
        Connection.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(connection is not None and connection.origin == binding['origin']
        and connection.protocol == binding['protocol'])
    tools._historical_grant(db, p, binding)
    scope = _scope(db, p, binding, actual, live=purpose != 'receipt')
    grant = device = None
    if purpose != 'receipt':
        grant, device = _current_gateway(db, p, binding, connection, snapshot)
    return {'capability': actual, 'binding': binding, 'snapshot': snapshot,
        'connection': connection, 'grant': grant, 'device': device,
        'environment': scope[0], 'instance': scope[1], 'release': scope[2], 'package': scope[3],
        'endpoint': binding['origin'] + '/acquisitions',
        'receipt_endpoint': binding['origin'] + '/acquisitions/receipts'}


def _view(cap):
    return {'id': cap.id, 'hash': cap.content_hash, 'version': cap.version,
        'release_version': cap.release_version, 'status': cap.status}


@safe
def create_source(db, p, body, *, connection_id=None):
    need(body.confirm_simulation is True)
    identifier = store.canonical_uuid(connection_id or getattr(body, 'connection_id', None))
    cmd, prior = store.command(db, p, OPERATION, body, identifier)
    if prior is not None:
        return prior
    connection = store.checked_connection(db, p, identifier, body.expected_version)
    grant = scoped(db, Grant, str(body.grant_id), p, True)
    check_version(grant, body.grant_expected_version)
    need(grant.connection_id == connection.id and grant.device_external_id == DEVICE
        and 'read_telemetry' in grant.data.get('actions', [])
        and store.grant_view(db, connection, grant)['effective'])
    from .simulation_loop_v176 import environment
    from .services import published
    env, instance, release = environment(db, p, str(body.environment_id), live=True)
    package = published(db, p, release.package_id, 'domain_package')
    config = {'schema': mapping.CONFIG_SCHEMA, 'scope': {'workspace_id': p.workspace_id,
        'environment_id': env.id, 'instance_id': instance.id, 'release_id': release.id,
        'package_id': package.id, 'package_hash': package.content_hash, 'entity_type': body.entity_type},
        'links': copy.deepcopy(body.links), 'supplier_contract': mapping.SUPPLIER, 'time_semantics': mapping.TIME}
    binding = checked_binding({**tools._binding(connection, grant, 'read_telemetry'),
        'schema': BINDING, 'mapping': config, 'extractor_id': str(body.extractor_id),
        'extractor_hash': body.extractor_hash})
    value = manifest(binding)
    _recipe_scope(db, p, binding, live=True)
    tools._historical_grant(db, p, binding)
    # Verify a real approved device before creating any credential snapshot.
    device = tools._identity(db, p, binding)
    from .device_identity import active_admin
    need(device.status == 'enabled' and active_admin(db, p, device.approved_by))
    snapshot = ensure_snapshot(db, p, connection)
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == NAMESPACE, Capability.name == value['name'],
        Capability.release_version == value['version']).with_for_update())
    from . import services
    if cap is None:
        with _registration_permit(db):
            cap = services.register(db, p, Manifest.model_validate(value))
        audit(db, p, ISSUED, cap.id, {'schema': ISSUED_SCHEMA, 'capability_id': cap.id,
            'capability_hash': cap.content_hash, 'actor_id': p.user_id, 'binding': binding,
            'snapshot_id': snapshot.id, 'snapshot_cipher_sha256': cipher_hash(snapshot)})
        db.flush()
        validate_source(db, p, cap)
        services.validate_capability(db, p, cap.id, cap.version)
        services.publish(db, p, cap.id, cap.version, body.reason)
    else:
        need(cap.data == value and cap.status == 'published')
        validate_source(db, p, cap)
    store.accept_command(db, p, cmd, connection)
    result = {'request_id': cmd.request_id, 'operation': OPERATION, 'connection_id': connection.id,
        'connection': store.connection_view(db, connection), 'discovery': None,
        'grant': store.grant_view(db, connection, grant), 'capability': _view(cap),
        'simulation': True, 'field_verified': False, 'observation_created': False}
    cmd.data = {**cmd.data, 'result': result}; db.add(cmd)
    audit(db, p, 'device.gateway_command_completed_v268', cmd.id,
        {'operation': cmd.operation, 'request_id': cmd.request_id, 'connection_id': connection.id,
         'payload_hmac': cmd.payload_hmac, 'result_hash': digest(result)})
    db.commit()
    return result


def list_sources(db, p, connection_id):
    store.member(db, p); identifier = store.canonical_uuid(connection_id)
    connection = scoped(db, Connection, identifier, p)
    rows = list(db.scalars(select(Capability).join(Audit,
        (Audit.object_id == Capability.id) & (Audit.workspace_id == Capability.workspace_id))
        .where(Capability.workspace_id == p.workspace_id, Audit.action == ISSUED,
            Audit.data['binding']['connection_id'].as_string() == connection.id)
        .order_by(Capability.created_at.desc(), Capability.id.desc()).limit(51)))
    result = []
    for cap in rows[:50]:
        integrity = current = False; device_id = environment_id = None
        try:
            context = validate_source(db, p, cap, 'receipt')
            binding = context['binding']; integrity = True
            device_id = binding['device_external_id']; environment_id = binding['mapping']['scope']['environment_id']
            validate_source(db, p, cap, 'prepare')
            current = cap.status == 'published' and cap.validated_hash == cap.content_hash
        except HTTPException:
            pass
        reason = ('current' if current else 'integrity_invalid' if not integrity
            else 'capability_not_published' if cap.status != 'published'
            else 'connection_paused' if connection.status != 'enabled' else 'authorization_changed')
        result.append({**_view(cap), 'connection_id': connection.id, 'device_external_id': device_id,
            'environment_id': environment_id, 'integrity_valid': integrity,
            'configuration_current': current, 'availability_reason': reason,
            'simulation': True, 'field_verified': False,
            'observation_created': False})
    return {'sources': result, 'has_more': len(rows) > 50}
