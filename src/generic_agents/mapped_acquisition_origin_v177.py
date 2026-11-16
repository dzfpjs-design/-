"""Cold origin for explicitly mapped, simulated HTTP Acquisition receipts.

An original parsed supplier receipt is not native HTTP bytes, a physical field
measurement or an authorization to read/control a device. The actual parent
live guards remain in its dispatcher. Historical readers need workspace scope,
not the author's current session; only the actual owned worker may execute.
"""
import copy
import math
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from . import http_observation_exchange_v177 as ledger
from . import http_observation_mapping_v177 as mapping
from . import http_observation_transport_v177 as transport
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .models import Acquisition, Audit, Capability, DomainInstance, Entity, InstanceRelease, Job, Observation, SensorAsset
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment
from .contracts import ObservationCreate
from .leases import ownership, checkpoint


SCHEMA = 'http-mapped-origin-v177'
CHECKED_SCHEMA = 'http-mapped-checked-origin-v177'
PROCESS = 'process_http_mapped_observation_v177'
PROCESSING_VERSION = 'http-mapped-v177'
PREFIX = 'http-acquisition:'
ERROR = 'HTTP映射观测的原交换、独立回执或处理血缘无法核对；原记录保留，未授予新执行'
_ORIGIN = {'schema', 'exchange_id'}
_OTHER_ORIGINS = frozenset({'simulation_origin', 'reported_rule_contract', 'reported_contract', 'mqtt_receipt_id',
    'mqtt_binding_id', 'mqtt_binding_hash', 'local_instance', 'local_source_hash',
    'local_capability_hash', 'local_original_sha256', 'native_instance_id', 'native_packet'})
_ENVIRONMENT = {'schema', 'simulation', 'origin_kind', 'field_verified', 'instance_id',
    'release_id', 'namespace', 'package_id', 'configuration_hash', 'created_by', 'note'}


def _need(condition):
    if not condition:
        raise HTTPException(409, ERROR)


def _same(left, right):
    return mapping.canonical(left) == mapping.canonical(right)


def _rows(db, statement):
    return list(db.scalars(statement.execution_options(populate_existing=True)))


def _get(db, p, model, identity):
    mapping._uuid(identity)
    rows = _rows(db, select(model).where(model.workspace_id == p.workspace_id, model.id == identity))
    _need(len(rows) == 1)
    return rows[0]


def _marks(value):
    _need(type(value) is dict and all(type(value.get(key)) is type(expected)
        and value[key] == expected for key, expected in ledger.MARK.items()))


def _origin(value):
    _need(type(value) is dict and set(value) == _ORIGIN and value['schema'] == SCHEMA)
    mapping._uuid(value['exchange_id'])
    return copy.deepcopy(value)


def _cap(db, p, identity, expected_hash, kind, *, execution):
    mapping._hash(expected_hash)
    cap = _get(db, p, Capability, identity)
    _need(cap.kind == kind and cap.content_hash == cap.validated_hash == expected_hash
        and mapping.digest(cap.data) == expected_hash)
    if execution:
        _need(cap.status == 'published')
    return cap


def _rule_shape(cap, fields, units, scope):
    execution, policy = cap.data['execution'], cap.data['data_policy']
    _need(type(execution) is dict
        and set(execution) in ({'adapter', 'handler'}, {'adapter', 'handler', 'side_effect'})
        and execution['adapter'] == 'builtin' and execution['handler'] == 'numeric_rules'
        and ('side_effect' not in execution or execution['side_effect'] is False)
        and type(cap.data.get('allowed_agent_roles')) is list and 'perception' in cap.data['allowed_agent_roles']
        and type(policy) is dict and policy.get('deployment') == 'local'
        and policy.get('cloud_allowed') is False and not policy.get('external_allowed'))
    specification = cap.data['specification']; rules = specification.get('rules', [])
    _need(type(rules) is list and len(rules) <= 64)
    managed = specification.get('managed_numeric_rule')
    if managed is not None or cap.namespace == 'platform_numeric_rules':
        _need(type(managed) is dict and managed.get('package_id') == scope['package_id']
            and managed.get('entity_type') == scope['entity_type'])
    for rule in rules:
        _need(type(rule) is dict and rule.get('field') in fields and rule.get('operator') in {'gt', 'lt'}
            and type(rule.get('threshold')) in {int, float} and abs(rule['threshold']) <= 1e15
            and math.isfinite(rule['threshold']) and type(rule.get('id')) is str and bool(rule['id'])
            and ('unit' not in rule or rule['unit'] == units[rule['field']]))
        if managed is not None:
            _need(rule.get('unit') == units[rule['field']])
    return rules


def _result(result, attributes, rules):
    """Match the existing builtin numeric_rules result, not a physical finding."""
    matches = []
    for rule in rules:
        if rule['field'] not in attributes:
            continue
        value = attributes[rule['field']]
        _need(type(value) in {int, float} and abs(value) <= 1e15 and math.isfinite(value))
        if ((rule['operator'] == 'gt' and value > rule['threshold'])
                or (rule['operator'] == 'lt' and value < rule['threshold'])):
            matches.append({'field': rule['field'], 'value': value, 'rule_id': rule['id'],
                'severity': rule.get('severity', 'warning')})
    expected = {'attributes': attributes, 'anomalies': matches, 'method': 'registered_numeric_rules'}
    _need(_same(result, expected))


def _body(exchange, extractor_id, observation):
    expected = transport._body(exchange, extractor_id).model_dump(mode='json')
    _need(expected['evidence_ids'] == [])  # A parsed receipt is not a file-model input.
    original = {key: value for key, value in expected.items() if key != 'evidence_ids'}
    data = observation.data; saved = exchange.data['observation_pin']
    _need(type(data) is dict and _same(data['original_request'], original)
        and _same(ObservationCreate.model_validate(original).model_dump(mode='json'), expected)
        and data['input_hash'] == mapping.digest(original) == saved['input_hash']
        and _same(saved['original_request'], original) and saved['observation_id'] == observation.id
        and saved['projection_hash'] == mapping.digest(exchange.data['projection']))
    for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day'):
        _need(getattr(observation, key) == expected[key])
    for key in ('attributes', 'evidence_ids', 'extractor_id', 'startup_mode', 'acquisition_id', 'sensor_asset_id', 'sensor_modality'):
        _need(_same(data.get(key), expected[key]))
    _need(data.get('submitted_by') == exchange.actor_id
        and not (_OTHER_ORIGINS & set(data)) and data.get('candidate_only', False) is False)
    labels = transport._labels(exchange)
    _need(all(_same(data.get(key), value) for key, value in labels.items())
        and data.get('http_mapped_receipt_evidence_id_v177') == exchange.data['receipt_history'][-1]['evidence_id'])
    clock = datetime.fromisoformat(expected['observed_at'])
    _need(clock.tzinfo is not None and clock.utcoffset() is not None
        and type(observation.observed_at) is datetime and observation.observed_at.tzinfo is not None
        and observation.observed_at.utcoffset() is not None
        and clock.astimezone(timezone.utc) == observation.observed_at.astimezone(timezone.utc))
    return expected


def _scope(db, p, exchange, original_source, *, execution):
    frozen = exchange.data['freeze']; frozen_mapping = frozen['mapping']
    scope = copy.deepcopy(frozen_mapping['mapping']['scope']); pins = frozen['parent_pins']
    _need(all(getattr(exchange, key) == scope[key] for key in ('workspace_id', 'environment_id', 'instance_id', 'release_id')))
    instance = _get(db, p, DomainInstance, scope['instance_id'])
    release = _get(db, p, InstanceRelease, scope['release_id'])
    environment = _get(db, p, Environment, scope['environment_id'])
    entity = _get(db, p, Entity, pins['entity_id'])
    package = _cap(db, p, scope['package_id'], scope['package_hash'], 'domain_package', execution=execution)
    _need(release.instance_id == instance.id and release.package_id == package.id
        and entity.schema_capability_id == package.id and entity.entity_type == scope['entity_type']
        and entity.external_id == frozen['domain_frozen']['request']['target_external_id']
        and entity.namespace == instance.namespace == package.namespace
        and environment.instance_id == instance.id and environment.release_id == release.id)
    e = environment.data
    _need(type(e) is dict and set(e) == _ENVIRONMENT and e['schema'] == 'simulation-environment-v176'
        and e['created_by'] == environment.actor_id and type(e['note']) is str
        and e['instance_id'] == instance.id and e['release_id'] == release.id
        and e['namespace'] == instance.namespace and e['package_id'] == package.id
        and e['configuration_hash'] == release.configuration_hash)
    mapping._marks(e)
    creation = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'simulation.environment.created_v176', Audit.object_id == environment.id))
    _need(len(creation) == 1 and creation[0].actor_id == environment.actor_id
        and _same(creation[0].data, {'record_hash': mapping.digest(e)}))
    snapshot = release.data['snapshot']
    _need(type(snapshot) is dict and mapping.digest(snapshot) == release.configuration_hash
        and snapshot['profile'].get('simulation_v176') is True)
    rebuilt = mapping.freeze_mapping(original_source.data, {**scope, 'source_id': original_source.id},
        package.data['specification']['entity_schemas'], frozen_mapping['mapping']['links'])
    _need(_same(rebuilt, frozen_mapping))
    return scope, pins, entity


def _parent(db, p, exchange, pins):
    acq = _get(db, p, Acquisition, exchange.acquisition_id)
    frozen = exchange.data['freeze']; request = frozen['domain_frozen']['request']
    _need(acq.entity_id == pins['entity_id'] and acq.capability_id == exchange.source_id
        and acq.idempotency_key == frozen['original_request_key'] == request['request_key']
        and acq.data.get('actor_id') == exchange.actor_id
        and acq.data.get('source_hash') == frozen['mapping']['source_manifest_hash']
        and _same(ledger._parent_pins(db, p, acq), pins))
    asset = _get(db, p, SensorAsset, pins['sensor_asset_id'])
    _need(asset.source_id == exchange.source_id and asset.external_id == request['asset_external_id'])
    dispatch = _get(db, p, Job, exchange.dispatch_job_id)
    identity = frozen['dispatch_identity']
    _need(dispatch.id == identity['job_id'] and dispatch.kind == identity['kind'] == 'dispatch_acquisition'
        and dispatch.idempotency_key == identity['idempotency_key']
        and dispatch.data.get('actor_id') == identity['actor_id'] == exchange.actor_id
        and dispatch.data.get('acquisition_id') == acq.id)
    receipt_origin = exchange.data['receipt_history'][-1]['receipt_origin']
    parent = _get(db, p, Job, receipt_origin['job_id'])
    _need(parent.status == 'succeeded' and parent.data.get('actor_id') == exchange.actor_id
        and receipt_origin['actor_id'] == exchange.actor_id
        and receipt_origin['operation'] in {'dispatch_post', 'original_receipt_get'})
    if receipt_origin['operation'] == 'dispatch_post':
        if parent.id != dispatch.id:
            from .managed_source_unsent_v270 import verify_retry_receipt_origin
            _need(verify_retry_receipt_origin(db,p,exchange,receipt_origin))
    else:
        _need(parent.kind == ledger.READ_KIND and parent.data.get('exchange_id') == exchange.id)
    return parent


def _execution(db, p, process, actor_id):
    lease = ownership(db)
    _need(p.user_id == actor_id and process.data.get('actor_id') == actor_id
        and process.status == 'running' and type(lease) is dict
        and lease.get('job_id') == process.id and type(process.lease_token) is str and bool(process.lease_token)
        and lease.get('lease_token') == process.lease_token)
    checkpoint(db)  # Original lease/IAM/database failures must propagate.


def _guard(db, p, observation, *, execution, result):
    _need(type(execution) is bool and observation.workspace_id == p.workspace_id)
    observation = _get(db, p, Observation, observation.id)
    _need(type(observation.data) is dict)
    source = _get(db, p, Capability, observation.source_id)
    _need(type(source.data) is dict and type(source.data.get('specification')) is dict)
    processes = _rows(db, select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'extract:' + observation.id))
    reverse = _rows(db, select(Exchange).where(Exchange.workspace_id == p.workspace_id,
        Exchange.observation_id == observation.id))
    anchors = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.data['state']['observation_id'].as_string() == observation.id))
    spec = source.data['specification']; data = observation.data
    traced = ('http_mapped_origin_v177' in data or 'http_mapped_receipt_evidence_id_v177' in data
        or observation.processing_version == PROCESSING_VERSION
        or type(observation.source_record_id) is str and observation.source_record_id.startswith(PREFIX)
        or any(key in spec for key in (transport.MAPPING_KEY, transport.EXTRACTOR_ID, transport.EXTRACTOR_HASH))
        or bool(reverse) or bool(anchors) or any(job.kind == PROCESS or
            any(key in job.data for key in ('http_mapped_exchange_id', 'http_mapped_parent_job_id')) for job in processes))
    if not traced:
        return None
    origin = _origin(data.get('http_mapped_origin_v177'))
    _need(len(reverse) == 1 and reverse[0].id == origin['exchange_id'] and bool(anchors)
        and all(anchor.object_id == origin['exchange_id'] and anchor.actor_id == reverse[0].actor_id for anchor in anchors))
    exchange = ledger.checked_store(db, p, origin['exchange_id'], lock=False)
    _need(exchange.workspace_id == p.workspace_id and exchange.observation_id == observation.id
        and exchange.source_id == observation.source_id and exchange.status == 'received'
        and exchange.data['projection']['status'] == 'mapped' and exchange.data['projection']['temporal_usable'] is True)
    stable = copy.deepcopy(ledger.snapshot(exchange))
    source = _cap(db, p, exchange.source_id, stable['data']['freeze']['mapping']['source_manifest_hash'], 'source', execution=execution)
    scope, pins, entity = _scope(db, p, exchange, source, execution=execution)
    expected_id, expected_hash = copy.deepcopy((source.data['specification'][transport.EXTRACTOR_ID],
        source.data['specification'][transport.EXTRACTOR_HASH]))
    extractor = _cap(db, p, expected_id, expected_hash, 'tool', execution=execution)
    frozen_mapping = exchange.data['freeze']['mapping']
    units = {link['field_key']: link['unit'] for link in frozen_mapping['mapping']['links'].values()}
    fields = set(frozen_mapping['field_schemas'])
    rules = _rule_shape(extractor, fields, units, scope)
    if execution and (extractor.data['specification'].get('managed_numeric_rule') is not None
            or extractor.namespace == 'platform_numeric_rules'):
        from .local_rules_api import require_current_check
        require_current_check(db, p, extractor)
        _need(extractor.content_hash == extractor.validated_hash == expected_hash == mapping.digest(extractor.data))
        rules = _rule_shape(extractor, fields, units, scope)
    body = _body(exchange, extractor.id, observation)
    _need(observation.entity_id == entity.id and body['extractor_id'] == extractor.id)
    parent = _parent(db, p, exchange, pins)
    _need(len(processes) == 1)
    process = processes[0]
    _need(process.kind == PROCESS and process.status in {'queued', 'running', 'succeeded', 'failed', 'cancelled'}
        and process.data.get('observation_id') == observation.id and process.data.get('extractor_id') == extractor.id
        and process.data.get('actor_id') == exchange.actor_id == data.get('submitted_by')
        and process.data.get('http_mapped_exchange_id') == exchange.id
        and process.data.get('http_mapped_parent_job_id') == parent.id)
    ingests = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'observation.ingest', Audit.object_id == observation.id))
    _need(len(ingests) == 1 and ingests[0].actor_id == exchange.actor_id)
    _marks(data)
    if result is not None:
        _result(result, body['attributes'], rules)
    _need(_same(ledger.snapshot(exchange), stable))
    if execution:
        _execution(db, p, process, exchange.actor_id)
    receipt = stable['data']['receipt_history'][-1]
    return {'schema': CHECKED_SCHEMA, **ledger.MARK, **{
        'exchange_id': exchange.id, 'acquisition_id': exchange.acquisition_id,
        'environment_id': exchange.environment_id, 'instance_id': exchange.instance_id,
        'release_id': exchange.release_id, 'entity_id': observation.entity_id, 'source_id': source.id,
        'extractor_id': extractor.id, 'processing_job_id': process.id, 'parent_job_id': parent.id,
        'receipt_evidence_id': receipt['evidence_id'], 'receipt_sha256': receipt['sha256'],
        'exchange_state_hash': mapping.digest(stable), 'mapping_hash': stable['data']['freeze']['mapping']['mapping_hash'],
        'domain_request_hash': stable['data']['freeze']['domain_frozen']['request_hash'],
        'wire_request_hash': stable['data']['freeze']['wire_frozen']['request_hash'],
        'input_hash': data['input_hash'], 'origin_hash': mapping.digest(origin),
        'time_semantics': mapping.TIME, 'receipt_representation': 'canonical_supplier_receipt_copy',
        'native_http_bytes': False, 'physical_device_connected': False, 'source_time_verified': False}}


def guard(db, p, observation, *, execution=False, result=None):
    """Check saved lineage only. None never means a damaged new origin is ordinary."""
    try:
        with db.no_autoflush:
            return _guard(db, p, observation, execution=execution, result=result)
    except HTTPException:
        raise HTTPException(409, ERROR) from None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError, OSError):
        raise HTTPException(409, ERROR) from None
