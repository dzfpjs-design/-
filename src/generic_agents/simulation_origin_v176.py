"""Cold provenance of an isolated simulator sample; never field authority.

The evidence is the canonical *parsed Gateway output*, not native HTTP bytes.
Live admission, current IAM, policy and device permissions remain caller duties.
Execution additionally requires the original leased processing Job. This reader
does not create a source, repair records, invoke a device or emit an event.
"""
import copy
import hashlib
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import ObservationCreate, digest
from .evidence import evidence_bytes
from .leases import checkpoint, ownership
from .models import Audit, Capability, DomainInstance, Entity, InstanceRelease, Invocation, Job, Observation
from .simulation_models_v176 import (SimulationEnvironmentV176 as Environment,
    SimulationDefinitionV176 as Definition, SimulationSampleV176 as Sample)
from .typed_payload_codec_v131 import canonical_json

SCHEMA = 'simulation-origin-v176'
PROCESS = 'process_simulation_observation_v176'
WEATHER_UNITS = {'temperature_c': '°C', 'humidity_percent': '%',
    'wind_m_s': 'm/s', 'pressure_hpa': 'hPa'}
_RANGES = {'temperature_c': (-50, 100), 'humidity_percent': (0, 100),
    'wind_m_s': (0, 75), 'pressure_hpa': (500, 1100)}
_MARK = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False}
_ORIGIN = {'schema', 'environment_id', 'definition_id', 'sample_id', 'invocation_id', 'receipt_evidence_id'}
_ENVIRONMENT = {'schema', 'simulation', 'origin_kind', 'field_verified', 'instance_id',
    'release_id', 'namespace', 'package_id', 'configuration_hash', 'created_by', 'note'}
_DEFINITION = {'schema', 'environment_hash', 'instance_id', 'release_id', 'entity_id',
    'source_id', 'extractor_id', 'gateway_id', 'business_id', 'device_id', 'fields',
    'interval_seconds', 'valid_until', 'goal_spec', 'pins'}
_SAMPLE = {'schema', 'definition_hash', 'environment_hash', 'inputs', 'input_hash',
    'job_id', 'output_hash', 'receipt_evidence_id', 'receipt_sha256',
    'observation_request_hash', 'processing_job_id', 'received_at'}
_MUTABLE_SAMPLE = frozenset({'task_id', 'signal_id', 'handoff_issue'})
_OTHER_ORIGINS = {'reported_rule_contract', 'mqtt_receipt_id', 'mqtt_binding_id',
    'mqtt_binding_hash', 'local_instance', 'local_source_hash', 'local_capability_hash',
    'local_original_sha256', 'native_instance_id', 'native_packet'}
ERROR = '模拟观测的原调用、独立来源或范围无法核对；原记录保留，未授予新执行'
MAX_BYTES = 1024 * 1024


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def _bytes(value):
    raw = canonical_json(value).encode('utf-8')
    _need(len(raw) <= MAX_BYTES)
    return raw


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _instant(value):
    _need(type(value) is str and 1 <= len(value) <= 100 and '\x00' not in value)
    result = datetime.fromisoformat(value)
    _need(result.tzinfo is not None and result.utcoffset() is not None)
    return result


def _marks(value):
    _need(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v
                                    for k, v in _MARK.items()))


def _sample_values(output, device_id, fields):
    _need(device_id == 'sim-weather-01' and type(device_id) is str)
    _need(type(fields) is list and 1 <= len(fields) <= len(WEATHER_UNITS)
          and all(type(field) is str and field in WEATHER_UNITS for field in fields)
          and len(fields) == len(set(fields)))
    _bytes(output); _marks(output)
    _need(output.get('device_id') == device_id and output.get('action') == 'read_telemetry'
          and output.get('status') == 'succeeded'
          and type(output.get('request_id')) is str and bool(output['request_id']))
    result = output.get('result'); _marks(result)
    _need(result.get('id') == device_id and result.get('time_semantics') == 'simulator_sample_time'
          and result.get('physical_device_connected') is False)
    _instant(result.get('sampled_at'))
    state = result.get('state'); _need(type(state) is dict)
    attributes = {}
    for field in fields:
        value = state.get(field)
        _need(type(value) in {int, float} and math.isfinite(value)
              and _RANGES[field][0] <= value <= _RANGES[field][1])
        attributes[field] = value
    return {'attributes': attributes, 'observed_at': result['sampled_at'],
            'field_units': {field: WEATHER_UNITS[field] for field in fields}}


def sample_values(output, device_id, fields):
    """Extract only the four declared weather values, preserving typed numbers/time."""
    try:
        return _sample_values(output, device_id, fields)
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError):
        raise HTTPException(409, ERROR) from None


def receipt_basis(data):
    """Freeze every received-sample field except three explicit handoff projections.

    Unknown additional fields are included in the audit hash, never discarded.
    task_id/signal_id/handoff_issue are not sample provenance or execution grants.
    """
    try:
        _need(type(data) is dict and _SAMPLE <= set(data)
              and data.get('schema') == 'simulation-sample-v176' and 'issue' not in data)
        value = {key: copy.deepcopy(item) for key, item in data.items() if key not in _MUTABLE_SAMPLE}
        _bytes(value)
        return value
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError):
        raise HTTPException(409, ERROR) from None


def _rows(db, statement):
    return list(db.scalars(statement.execution_options(populate_existing=True)))


def _get(db, p, model, identity):
    _need(type(identity) is str and bool(identity))
    rows = _rows(db, select(model).where(model.workspace_id == p.workspace_id, model.id == identity))
    _need(len(rows) == 1)
    return rows[0]


def _linked_samples(db, p, observation_id):
    return _rows(db, select(Sample).where(Sample.workspace_id == p.workspace_id,
                                        Sample.observation_id == observation_id))


def _environments(db, p, namespace):
    return _rows(db, select(Environment).where(Environment.workspace_id == p.workspace_id,
                                             Environment.data['namespace'].as_string() == namespace))


def _audit(db, p, action, row, expected):
    rows = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == row.id))
    _need(len(rows) == 1 and rows[0].actor_id == row.actor_id and _same(rows[0].data, expected))
    return rows[0]


def _call_snapshot(call):
    return {key: copy.deepcopy(getattr(call, key)) for key in
            ('id', 'workspace_id', 'capability_id', 'idempotency_key', 'status', 'version', 'data')}


def _recovery_source(db, p, sample, definition, environment, instance, release, gateway, sampler, call):
    """Check saved GET authority as the source actor, never the current reader.

    This deliberately does not call the recovery command's owner-only helper.
    A surviving independent READ anchor cannot downgrade to an ordinary sample
    when a mutable-looking marker or embedded proof has disappeared.
    """
    reads = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'simulation.sample.receipt_read_v176', Audit.object_id == call.id))
    if 'recovery' not in sample.data and 'simulation_receipt_recovery_v176' not in call.data and not reads:
        return
    recovery = sample.data.get('recovery'); proof = call.data.get('simulation_receipt_recovery_v176')
    _need(type(recovery) is dict and type(proof) is dict and len(reads) == 1)
    _need(recovery.get('schema') == 'simulation-sample-recovery-v176'
          and recovery.get('status') == 'verified_succeeded'
          and recovery.get('invocation_id') == call.id
          and recovery.get('original_sampler_job_id') == sampler.id
          and recovery.get('output_hash') == _hash(call.data['output'])
          and recovery.get('query_only') is True and recovery.get('post_replayed') is False
          and type(recovery.get('receipt_query_performed')) is bool)
    _instant(recovery['checked_at'])
    job = _get(db, p, Job, recovery['job_id'])
    instruction = job.data.get('instruction')
    _need(job.kind == 'recover_simulation_sample_v176' and job.status == 'succeeded'
          and job.data.get('sample_id') == sample.id and job.data.get('actor_id') == sample.actor_id
          and job.data.get('original_sampler_job_id') == sampler.id
          and job.idempotency_key == 'simulation-sample-recovery:' + sample.id + ':' + recovery['request_id']
          and type(instruction) is dict and set(instruction) == {'schema', 'sample_id', 'actor_id',
              'original_sampler_job_id', 'sample_version', 'source_basis_hash', 'request_id'}
          and instruction['schema'] == 'simulation-sample-recovery-instruction-v176'
          and instruction['sample_id'] == sample.id and instruction['actor_id'] == sample.actor_id
          and instruction['original_sampler_job_id'] == sampler.id
          and instruction['request_id'] == recovery['request_id']
          and type(instruction['sample_version']) is int and instruction['sample_version'] >= 1)
    enqueue = _audit(db, p, 'simulation.sample.enqueued_v176', sample,
                     {'definition_id': definition.id, 'request_id': sample.request_id})
    basis = {'sample_id': sample.id, 'actor_id': sample.actor_id,
        'definition_id': definition.id, 'definition_hash': _hash(definition.data),
        'environment_id': environment.id, 'environment_hash': _hash(environment.data),
        'instance_id': instance.id, 'release_id': release.id, 'original_sampler_job_id': sampler.id,
        'original_key': sampler.idempotency_key, 'input_hash': sample.data['input_hash'],
        'gateway_id': gateway.id, 'gateway_hash': gateway.content_hash, 'enqueue_audit_id': enqueue.id}
    _need(instruction['source_basis_hash'] == recovery['source_basis_hash'] == _hash(basis))
    for action, expected in (
        ('simulation.sample.recovery.enqueued_v176', {'sample_id': sample.id,
            'instruction_hash': _hash(instruction), 'job_key': job.idempotency_key}),
        ('simulation.sample.recovery.checked_v176', {'sample_id': sample.id,
            'status': 'verified_succeeded', 'recovery_hash': _hash(recovery),
            'recovery_snapshot': recovery, 'post_replayed': False})):
        anchors = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == action, Audit.object_id == job.id))
        _need(len(anchors) == 1 and anchors[0].actor_id == sample.actor_id and _same(anchors[0].data, expected))
    _need(set(proof) == {'schema', 'sample_id', 'recovery_job_id', 'original_state', 'original_state_hash',
        'receipt', 'receipt_hash', 'receipt_evidence_id', 'receipt_sha256', 'checked_at', 'query_only', 'post_replayed'}
        and proof['schema'] == 'simulation-read-receipt-recovery-v176'
        and proof['sample_id'] == sample.id and proof['query_only'] is True and proof['post_replayed'] is False)
    _instant(proof['checked_at'])
    # A later recovery may reuse the same already-restored call; the original
    # GET job remains its authority and need not equal the latest recovery job.
    read_job = _get(db, p, Job, proof['recovery_job_id'])
    read_terminal = (read_job.status == 'succeeded' or
        (read_job.status == 'failed' and read_job.id != job.id
         and recovery.get('previous_recovery_job_id') == read_job.id))
    _need(read_job.kind == 'recover_simulation_sample_v176' and read_terminal
          and read_job.data.get('sample_id') == sample.id
          and read_job.data.get('actor_id') == sample.actor_id
          and read_job.data.get('original_sampler_job_id') == sampler.id)
    read_instruction = read_job.data.get('instruction')
    _need(type(read_instruction) is dict and set(read_instruction) == set(instruction)
          and read_instruction['schema'] == instruction['schema']
          and read_instruction['sample_id'] == sample.id and read_instruction['actor_id'] == sample.actor_id
          and read_instruction['original_sampler_job_id'] == sampler.id
          and read_instruction['source_basis_hash'] == _hash(basis)
          and type(read_instruction['sample_version']) is int and read_instruction['sample_version'] >= 1
          and type(read_instruction['request_id']) is str
          and read_job.idempotency_key == 'simulation-sample-recovery:' + sample.id + ':' + read_instruction['request_id'])
    read_enqueues = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'simulation.sample.recovery.enqueued_v176', Audit.object_id == read_job.id))
    _need(len(read_enqueues) == 1 and read_enqueues[0].actor_id == sample.actor_id
          and _same(read_enqueues[0].data, {'sample_id': sample.id,
              'instruction_hash': _hash(read_instruction), 'job_key': read_job.idempotency_key}))
    original, receipt = proof['original_state'], proof['receipt']
    _need(type(original) is dict and set(original) == set(_call_snapshot(call))
          and original['id'] == call.id and original['workspace_id'] == p.workspace_id
          and original['capability_id'] == call.capability_id and original['idempotency_key'] == call.idempotency_key
          and original['status'] in {'running', 'unknown', 'failed', 'succeeded'}
          and type(original['version']) is int and type(call.version) is int
          and call.version == original['version'] + 1 and original['version'] >= 1
          and proof['original_state_hash'] == _hash(original) and type(original['data']) is dict
          and 'simulation_receipt_recovery_v176' not in original['data']
          and _same({k: v for k, v in original['data'].items() if k != 'output'},
                    {k: v for k, v in call.data.items() if k not in {'output', 'simulation_receipt_recovery_v176'}})
          and type(receipt) is dict and {'idempotency_key', 'input_hash', 'status', 'output'} <= set(receipt)
          and set(receipt) <= {'idempotency_key', 'input_hash', 'status', 'output', 'terminal'}
          and ('terminal' not in receipt or type(receipt['terminal']) is bool)
          and receipt['status'] == 'succeeded' and receipt['idempotency_key'] == call.idempotency_key
          and receipt['input_hash'] == sample.data['input_hash'] and _same(receipt['output'], call.data['output'])
          and proof['receipt_hash'] == _hash(receipt))
    if original['status'] == 'succeeded':
        _need(_same(original['data'].get('output'), call.data['output']))
    evidence, raw = evidence_bytes(db, p, proof['receipt_evidence_id'])
    _need(evidence.workspace_id == p.workspace_id and evidence.media_type == 'application/json'
          and type(evidence.size_bytes) is int and evidence.size_bytes == len(raw) <= MAX_BYTES
          and evidence.sha256 == proof['receipt_sha256'] == hashlib.sha256(raw).hexdigest()
          and raw == _bytes(receipt) and type(evidence.data) is dict)
    _marks(evidence.data)
    _need(evidence.data.get('purpose') == 'parsed_simulation_get_receipt_v176'
          and evidence.data.get('native_http_bytes') is False
          and evidence.data.get('sample_id') == sample.id and evidence.data.get('invocation_id') == call.id
          and evidence.data.get('recovery_job_id') == read_job.id)
    expected = {'sample_id': sample.id, 'recovery_job_id': read_job.id,
        'receipt_evidence_id': evidence.id, 'receipt_sha256': evidence.sha256,
        'receipt_hash': proof['receipt_hash'], 'original_state_hash': proof['original_state_hash'],
        'resulting_call_hash': _hash(_call_snapshot(call)), 'query_only': True, 'post_replayed': False}
    _need(reads[0].actor_id == sample.actor_id and _same(reads[0].data, expected))


def _capability(db, p, identity, pin, kind):
    cap = _get(db, p, Capability, identity)
    _need(cap.kind == kind and type(cap.data) is dict and cap.content_hash == pin == _hash(cap.data)
          and cap.validated_hash == pin)
    return cap


def _guard(db, p, observation, *, execution):
    _need(type(execution) is bool and observation.workspace_id == p.workspace_id
          and type(observation.data) is dict)
    observation = _get(db, p, Observation, observation.id)
    data = observation.data
    source = _get(db, p, Capability, observation.source_id)
    _need(type(source.data) is dict)
    specification, policy = source.data.get('specification', {}), source.data.get('data_policy', {})
    _need(type(specification) is dict and type(policy) is dict)
    samples = _linked_samples(db, p, observation.id)
    entity = _get(db, p, Entity, observation.entity_id) if observation.entity_id is not None else None
    environments = _environments(db, p, entity.namespace) if entity is not None else []
    explicit = ('simulation_origin' in data or 'simulation_environment_id' in specification
              or ('simulation' in policy and policy['simulation'] is not False)
              or policy.get('origin_kind') == 'simulated_equipment')
    marked = explicit or bool(samples) or bool(environments)
    if not marked:
        return None
    # An environment owns its V176 Samples, not every independently sourced
    # packet in the namespace. Explicit/linked V176 evidence still takes the
    # unchanged path below, including its marker-deletion protection.
    if not explicit and not samples and len(environments) == 1 and (
            source.data.get('execution', {}).get('adapter') == 'mqtt_source'
            or any(key in data for key in ('mqtt_receipt_id', 'mqtt_binding_id', 'mqtt_delivery_id'))):
        from .mqtt_namespace_origin_v298 import is_independent_mqtt
        if is_independent_mqtt(db, p, observation, source=source, entity=entity,
                environments=environments, execution=execution):
            return None
    origin = data.get('simulation_origin')
    _need(type(origin) is dict and set(origin) == _ORIGIN and origin['schema'] == SCHEMA
          and entity is not None and len(samples) == len(environments) == 1
          and samples[0].id == origin['sample_id'] and environments[0].id == origin['environment_id'])
    _need(not (_OTHER_ORIGINS & set(data)) and data.get('candidate_only', False) is False)
    sample = _get(db, p, Sample, origin['sample_id'])
    definition = _get(db, p, Definition, origin['definition_id'])
    environment = _get(db, p, Environment, origin['environment_id'])
    e, d, s = environment.data, definition.data, sample.data
    _need(type(e) is dict and set(e) == _ENVIRONMENT and e['schema'] == 'simulation-environment-v176')
    _marks(e)
    _need(e['created_by'] == environment.actor_id and type(e['note']) is str)
    _audit(db, p, 'simulation.environment.created_v176', environment, {'record_hash': _hash(e)})
    _need(type(d) is dict and set(d) == _DEFINITION and d['schema'] == 'simulation-definition-v176'
          and definition.environment_id == environment.id and d['environment_hash'] == _hash(e))
    _audit(db, p, 'simulation.definition.created_v176', definition, {'record_hash': _hash(d)})
    _need(type(d['interval_seconds']) is int and d['interval_seconds'] > 0
          and (d['goal_spec'] is None or type(d['goal_spec']) is dict))
    _instant(d['valid_until'])  # Historical expiry does not revoke read-only evidence.
    _need(sample.definition_id == definition.id and sample.status == 'received'
          and sample.observation_id == observation.id and sample.invocation_id == origin['invocation_id'])
    _audit(db, p, 'simulation.sample.enqueued_v176', sample,
           {'definition_id': definition.id, 'request_id': sample.request_id})
    basis = receipt_basis(s)
    _need(s['definition_hash'] == _hash(d) and s['environment_hash'] == _hash(e)
          and s['receipt_evidence_id'] == origin['receipt_evidence_id'])
    _audit(db, p, 'simulation.sample.received_v176', sample, {'observation_id': observation.id,
        'invocation_id': sample.invocation_id, 'origin_hash': _hash(origin),
        'output_hash': s['output_hash'], 'record_hash': _hash(basis)})

    instance = _get(db, p, DomainInstance, environment.instance_id)
    release = _get(db, p, InstanceRelease, environment.release_id)
    _need(e['instance_id'] == d['instance_id'] == instance.id == release.instance_id
          and e['release_id'] == d['release_id'] == release.id
          and e['namespace'] == instance.namespace == entity.namespace
          and d['entity_id'] == entity.id == observation.entity_id
          and e['package_id'] == release.package_id == entity.schema_capability_id
          and e['configuration_hash'] == release.configuration_hash)
    snapshot = release.data['snapshot']
    _need(type(snapshot) is dict and _hash(snapshot) == release.configuration_hash
          and snapshot['profile'].get('simulation_v176') is True
          and any(item.get('id') == d['business_id'] for item in snapshot['businesses']))
    package = _get(db, p, Capability, release.package_id)
    _need(package.kind == 'domain_package' and package.namespace == instance.namespace
          and package.content_hash == package.validated_hash == _hash(package.data))
    _need(type(d['pins']) is dict and set(d['pins']) == {d['source_id'], d['extractor_id'], d['gateway_id']})
    source = _capability(db, p, d['source_id'], d['pins'][d['source_id']], 'source')
    extractor = _capability(db, p, d['extractor_id'], d['pins'][d['extractor_id']], 'tool')
    gateway = _capability(db, p, d['gateway_id'], d['pins'][d['gateway_id']], 'tool')
    _need(source.id == observation.source_id and data['extractor_id'] == extractor.id
          and source.data['specification'].get('simulation_environment_id') == environment.id
          and source.data['data_policy'].get('test_only') is True
          and source.data['data_policy'].get('cloud_allowed') is False
          and source.data['data_policy'].get('deployment') == 'local')
    _need(extractor.data['execution'].get('adapter') == 'builtin'
          and extractor.data['execution'].get('handler') == 'numeric_rules'
          and extractor.data['execution'].get('side_effect', False) is False
          and extractor.data['data_policy'].get('cloud_allowed') is False
          and gateway.namespace == 'equipment_simulation' and gateway.name == 'read_equipment'
          and gateway.data['execution'].get('adapter') == 'http'
          and gateway.data['execution'].get('side_effect', False) is False
          and gateway.data['data_policy'].get('simulation') is True
          and gateway.data['data_policy'].get('test_only') is True)

    inputs = {'device_id': d['device_id'], 'action': 'read_telemetry', 'expected_revision': None, 'arguments': {}}
    _need(_same(s['inputs'], inputs) and s['input_hash'] == _hash(inputs))
    call = _get(db, p, Invocation, sample.invocation_id)
    sampler = _get(db, p, Job, s['job_id'])
    _need(sampler.kind == 'sample_simulation_v176' and sampler.status == 'succeeded'
          and sampler.idempotency_key == 'simulation-sample:' + sample.id
          and sampler.data.get('sample_id') == sample.id and sampler.data.get('actor_id') == sample.actor_id)
    _need(call.status == 'succeeded' and call.capability_id == gateway.id
          and call.idempotency_key == 'simulation-sample:' + sample.id
          and call.data.get('capability_hash') == gateway.content_hash
          and call.data.get('input_hash') == s['input_hash'] and call.data.get('role') == 'perception'
          and call.data.get('side_effect') is False and call.data.get('job_id') == sampler.id
          and type(call.data.get('lease_token')) is str and bool(call.data['lease_token'])
          and s['output_hash'] == _hash(call.data['output'])
          and call.data['output'].get('request_id') == call.idempotency_key)
    _recovery_source(db, p, sample, definition, environment, instance, release, gateway, sampler, call)
    values = sample_values(call.data['output'], d['device_id'], d['fields'])
    receipt, content = evidence_bytes(db, p, origin['receipt_evidence_id'])
    _need(receipt.workspace_id == p.workspace_id and receipt.media_type == 'application/json'
          and type(receipt.size_bytes) is int and receipt.size_bytes == len(content) <= MAX_BYTES
          and receipt.sha256 == s['receipt_sha256'] == _hash(call.data['output'])
          and hashlib.sha256(content).hexdigest() == receipt.sha256
          and content == _bytes(call.data['output']))
    _marks(receipt.data)
    _need(receipt.data.get('purpose') == 'parsed_device_receipt_copy_v176'
          and receipt.data.get('native_http_bytes') is False
          and receipt.data.get('invocation_id') == call.id and not receipt.data.get('cloud_model_ids'))
    _instant(s['received_at'])

    original = data['original_request']
    _need(type(original) is dict and s['observation_request_hash'] == data['input_hash'] == _hash(original))
    body = ObservationCreate.model_validate(original)
    _need(_same(original, body.model_dump()) or _same(original, body.model_dump(exclude={'evidence_ids'})))
    _need(body.source_id == source.id and body.source_record_id == 'simulation-sample:' + sample.id
          and body.processing_version == 'simulation-v176' and body.entity_id == entity.id
          and body.extractor_id == extractor.id and body.startup_mode == 'automatic'
          and body.evidence_ids == [] and body.acquisition_id is None
          and body.sensor_asset_id is None and body.sensor_modality is None
          and body.observed_at == values['observed_at'] and _same(body.attributes, values['attributes'])
          and _same(data['attributes'], values['attributes']) and data.get('evidence_ids', []) == []
          and all(data.get(key) is None for key in ('acquisition_id', 'sensor_asset_id', 'sensor_modality'))
          and data.get('source_time_verified') is False and data.get('date_basis') == 'simulator_sample_time'
          and _same(data.get('field_units'), values['field_units']))
    for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day'):
        _need(getattr(body, key) == getattr(observation, key))
    clock = _instant(values['observed_at'])
    _need(observation.observed_at is not None and observation.observed_at.tzinfo is not None
          and clock.astimezone(timezone.utc) == observation.observed_at.astimezone(timezone.utc)
          and body.business_day == str(clock.astimezone(ZoneInfo('Asia/Shanghai')).date()))
    jobs = _rows(db, select(Job).where(Job.workspace_id == p.workspace_id,
                                     Job.idempotency_key == 'extract:' + observation.id))
    _need(len(jobs) == 1)
    process = jobs[0]
    _need(process.id == s['processing_job_id'] and process.kind == PROCESS
          and process.data.get('observation_id') == observation.id
          and process.data.get('extractor_id') == extractor.id
          and process.data.get('actor_id') == data.get('submitted_by') == sample.actor_id)
    ingests = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'observation.ingest', Audit.object_id == observation.id))
    _need(len(ingests) == 1 and ingests[0].actor_id == sample.actor_id)
    if execution:
        lease = ownership(db)
        _need(process.status == 'running' and type(lease) is dict
              and lease.get('job_id') == process.id and lease.get('lease_token') == process.lease_token
              and type(process.lease_token) is str and bool(process.lease_token)
              and process.data.get('actor_id') == p.user_id)
        checkpoint(db)  # Propagate lease/IAM loss; never turn it into a parse error.
    return {'schema': SCHEMA, **_MARK, **{key: origin[key] for key in _ORIGIN - {'schema'}},
        'instance_id': instance.id, 'release_id': release.id, 'entity_id': entity.id,
        'source_id': source.id, 'extractor_id': extractor.id, 'environment_hash': _hash(e),
        'definition_hash': _hash(d), 'origin_hash': _hash(origin),
        'receipt_sha256': receipt.sha256, 'time_semantics': 'simulator_sample_time',
        'receipt_representation': 'canonical_gateway_output_snapshot',
        'native_http_bytes': False, 'physical_device_connected': False}


def guard(db, p, observation, *, execution=False):
    """None only for unmarked subjects with no independent simulation ownership.

    This establishes saved-source integrity. It grants no permission to dispatch,
    approve, publish a rule or treat synthetic values as physical observations.
    """
    try:
        return _guard(db, p, observation, execution=execution)
    except HTTPException as error:
        raise HTTPException(409, ERROR) from None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError,
            UnicodeError, OSError):
        raise HTTPException(409, ERROR) from None
