"""Historical authority for one managed, reported numeric-rule observation.

This reader neither publishes a rule nor labels a record. It requires the real
processed Job/Invocation and the original Event/modern Memory receipt. Current
publication, active-release and actor permissions belong to the calling action;
they must not be used to reconstruct or re-sign historical machine evidence.
"""
import copy
import hashlib
import math
from uuid import UUID

from fastapi import HTTPException
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import select

from .contracts import digest
from .instance_data_receipts import observation_receipt
from .local_rules_api import CREATE_ACTION, NAMESPACE, _saved_manifest
from .models import Audit, Capability, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Memory, Observation
from .observation_integrity import checked_report
from .observation_time_anchor_v135 import processing_anchor, same_observation_instant
from .perception_signals import machine_basis
from .services import validate
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'threshold-evolution-feedback-basis-v147'
ERROR = '原数值登记、规则或接收依据无法核对；原记录保留，不能作为阈值反馈依据'
_REPORTED = {'schema', 'instance_id', 'release_id', 'entity_id', 'entity_version', 'source_hash', 'extractor_hash'}
_LINEAGE = {'entity_source_package_id', 'entity_definition_hash'}


def _need(condition, status=409):
    if not condition:
        raise HTTPException(status, ERROR)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _row(db, p, model, identity, *, status=409):
    _need(type(identity) is str and bool(identity), status)
    value = db.scalar(select(model).where(model.workspace_id == p.workspace_id,
        model.id == identity).execution_options(populate_existing=True))
    _need(value is not None, status)
    return value


def _rows(db, statement):
    return list(db.scalars(statement.execution_options(populate_existing=True)))


def _manifest(cap, kind):
    _need(cap.kind == kind and type(cap.data) is dict
        and cap.content_hash == digest(cap.data) and cap.validated_hash == cap.content_hash)
    canonical_json(cap.data)
    execution, policy = cap.data['execution'], cap.data['data_policy']
    _need(type(execution) is dict and type(policy) is dict
        and (execution.get('side_effect') is None or execution.get('side_effect') is False)
        and policy.get('cloud_allowed') is False and policy.get('deployment') == 'local')
    if kind == 'source':
        _need(execution.get('adapter') == 'upload' or
            execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules')
    else:
        _need(execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules')


def _audit_value(row):
    # SQL display clocks are not content or provenance authority.
    return {'id': row.id, 'actor_id': row.actor_id, 'action': row.action,
        'object_id': row.object_id, 'data': copy.deepcopy(row.data)}


def _label(release, metadata, field, field_schema):
    title = field_schema.get('title')
    if type(title) is str and title.strip():
        return title
    types = release.data.get('snapshot', {}).get('object_types', [])
    labels = [item.get('label') for kind in types if type(kind) is dict
        and kind.get('key') == metadata['entity_type'] for item in kind.get('fields', [])
        if type(item) is dict and item.get('key') == field]
    if len(labels) == 1 and type(labels[0]) is str and labels[0].strip():
        return labels[0]
    return field


def _basis(db, p, observation_id, rule_id, expected_observation_version):
    _need(type(observation_id) is str and str(UUID(observation_id)) == observation_id, 422)
    _need(type(rule_id) is str and 1 <= len(rule_id) <= 80 and rule_id == rule_id.strip(), 422)
    observation = _row(db, p, Observation, observation_id, status=404)
    if expected_observation_version is not None:
        _need(type(expected_observation_version) is int and expected_observation_version >= 1, 422)
        _need(observation.version == expected_observation_version)
    data = observation.data
    _need(observation.status == 'processed' and type(data) is dict
        and data.get('candidate_only', False) is False
        and type(data.get('reported_rule_contract')) is dict)
    _need(not any(key in data for key in ('native_instance_id', 'native_packet', 'local_instance',
        'local_capability_hash', 'local_source_hash', 'mqtt_receipt_id', 'mqtt_binding_id', 'mqtt_binding_hash')))
    _need(_same(data.get('evidence_ids', []), []) and not data.get('model_receipt')
        and data.get('acquisition_id') is None and data.get('sensor_asset_id') is None
        and data.get('sensor_modality') is None)
    original, contract = data.get('original_request'), data['reported_rule_contract']
    _need(type(original) is dict and data.get('input_hash') == digest(original)
        and set(contract) in (_REPORTED, _REPORTED | _LINEAGE)
        and contract['schema'] == 'local-reported-rule-v1'
        and type(contract['entity_version']) is int and contract['entity_version'] >= 1)
    _need(_same(original.get('attributes'), data.get('attributes'))
        and _same(original.get('evidence_ids', []), [])
        and original.get('acquisition_id') is None and original.get('sensor_asset_id') is None
        and original.get('sensor_modality') is None
        and original.get('startup_mode') in {'manual', 'automatic', 'historical'})
    for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day'):
        _need(key in original and _same(original[key], getattr(observation, key)))
    _need(original.get('extractor_id') == data['extractor_id']
        and contract['entity_id'] == observation.entity_id)
    same_observation_instant(original['observed_at'], observation.observed_at)

    source = _row(db, p, Capability, observation.source_id)
    extractor = _row(db, p, Capability, data['extractor_id'])
    _manifest(source, 'source'); _manifest(extractor, 'tool')
    _need(extractor.namespace == NAMESPACE
        and contract['source_hash'] == source.content_hash
        and contract['extractor_hash'] == extractor.content_hash)
    definition = _saved_manifest(db, p, extractor)
    rules = extractor.data['specification']['rules']
    _need(len(definition.rules) == len(rules) == 1 and rules[0]['id'] == rule_id)
    rule = rules[0]
    # The saved authoring contract is the authority, never today's Entity value.
    _need(_same(rule, definition.rules[0].model_dump()))
    metadata = extractor.data['specification']['managed_numeric_rule']
    field_schema = extractor.data['input_schema']['properties']['attributes']['properties'][rule['field']]
    values = data['attributes']
    _need(type(values) is dict and rule['field'] in values)
    actual_value = values[rule['field']]
    _need(type(actual_value) in {int, float} and math.isfinite(actual_value))
    inputs = {'attributes': values}
    validate(extractor.data['input_schema'], inputs)
    actual_hit = (actual_value > rule['threshold'] if rule['operator'] == 'gt' else actual_value < rule['threshold'])
    expected_output = {'attributes': copy.deepcopy(values),
        'anomalies': ([{'field': rule['field'], 'value': actual_value, 'rule_id': rule_id,
            'severity': rule.get('severity', 'warning')}] if actual_hit else []),
        'method': 'registered_numeric_rules'}
    _need(_same(data['extracted'], expected_output))
    report = checked_report(db, p, observation)
    _need(report.get('review') != 'rejected' and _same(report['extracted'], expected_output))
    call = _row(db, p, Invocation, data['invocation_id'])
    jobs = _rows(db, select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'extract:' + observation.id))
    _need(len(jobs) == 1)
    job = jobs[0]
    _need(job.kind == 'process_reported_observation' and job.status == 'succeeded'
        and job.data.get('observation_id') == observation.id
        and job.data.get('extractor_id') == extractor.id
        and type(job.attempts) is int and job.attempts >= 1)
    from .threshold_policy_execution_v153 import historical_call_key
    policy_key = historical_call_key(db, p, observation, job)
    expected_key = policy_key or job.data.get('resume_invocation_key') or 'observation:' + observation.id + ':attempt:' + str(job.attempts)
    _need(call.status == 'succeeded' and call.capability_id == extractor.id
        and call.idempotency_key == expected_key and call.data.get('job_id') == job.id
        and type(call.data.get('lease_token')) is str and bool(call.data['lease_token'])
        and call.data.get('role') == 'perception' and call.data.get('side_effect') is False
        and call.data.get('approval') is None and call.data.get('capability_hash') == extractor.content_hash
        and call.data.get('input_hash') == digest(inputs) and _same(call.data.get('output'), expected_output))
    ingests = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == observation.id, Audit.action == 'observation.ingest'))
    _need(len(ingests) == 1 and ingests[0].actor_id == data['submitted_by'])
    creates = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == extractor.id, Audit.action == CREATE_ACTION))
    _need(len(creates) == 1)

    instance = _row(db, p, DomainInstance, contract['instance_id'])
    release = _row(db, p, InstanceRelease, contract['release_id'])
    entity = _row(db, p, Entity, observation.entity_id)
    _row(db, p, Capability, release.package_id)
    _need(release.instance_id == instance.id and entity.namespace == instance.namespace
        and metadata['entity_type'] == entity.entity_type and metadata['package_id'] == release.package_id)
    received = observation_receipt(db, p, observation)
    _need(type(received) is dict and set(received) == {'data_reception'})
    receipt = received['data_reception']
    _need(type(receipt) is dict and receipt.get('schema') == 'instance-data-reception-receipt-v1'
        and receipt.get('instance_id') == instance.id and receipt.get('release_id') == release.id
        and receipt.get('observation_id') == observation.id and receipt.get('entity_id') == entity.id
        and receipt.get('source_id') == source.id and receipt.get('extractor_id') == extractor.id)
    event = _row(db, p, Event, receipt['event_id'])
    _need(event.kind == 'observation.data' and event.idempotency_key == 'data:' + observation.id
        and event.status == 'received')
    data_anchor = processing_anchor(db, p, observation, event_id=event.id)
    anomaly = _rows(db, select(Event).where(Event.workspace_id == p.workspace_id,
        Event.idempotency_key == 'anomaly:' + observation.id))
    _need(len(anomaly) == (1 if actual_hit else 0))
    machine = machine_basis(db, p, observation)
    memories = _rows(db, select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.data['event_id'].as_string() == event.id,
        Memory.data['type'].as_string() == 'observation_evidence').limit(2))
    _need(len(memories) == 1 and memories[0].status == 'candidate'
        and _same(memories[0].data.get('data_reception'), receipt))
    memory = memories[0]
    proof = {'schema': 'threshold-evolution-machine-proof-v147', 'workspace_id': p.workspace_id,
        'original_request': copy.deepcopy(original), 'original_input_hash': data['input_hash'],
        'reported_rule_contract': copy.deepcopy(contract), 'machine': copy.deepcopy(machine),
        'source': {'id': source.id, 'hash': source.content_hash, 'manifest': copy.deepcopy(source.data)},
        'extractor': {'id': extractor.id, 'hash': extractor.content_hash, 'manifest': copy.deepcopy(extractor.data)},
        'authoring_audit': _audit_value(creates[0]), 'ingest_audit': _audit_value(ingests[0]),
        'job': {'id': job.id, 'kind': job.kind, 'key': job.idempotency_key,
            'observation_id': observation.id, 'extractor_id': extractor.id},
        'invocation': {'id': call.id, 'key': call.idempotency_key, 'capability_id': call.capability_id,
            'job_id': job.id, 'role': call.data['role'], 'capability_hash': extractor.content_hash,
            'input_hash': call.data['input_hash'], 'output': copy.deepcopy(call.data['output']),
            # Pin the saved call value; this is not a retroactive independent
            # dispatch attestation and never compares the later Job lease.
            'lease_token_hash': _sha(call.data['lease_token'])},
        'data_event': copy.deepcopy(data_anchor), 'receipt': copy.deepcopy(receipt),
        'memory': {'id': memory.id, 'data_hash': _sha(memory.data)},
        'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False}
    value = {'schema': SCHEMA, 'observation_id': observation.id,
        'baseline_capability_id': extractor.id, 'baseline_capability_hash': extractor.content_hash,
        'rule_id': rule_id, 'field': rule['field'],
        'field_label': _label(release, metadata, rule['field'], field_schema),
        'unit': rule['unit'], 'operator': rule['operator'], 'threshold': rule['threshold'],
        'actual_value': actual_value, 'actual_hit': actual_hit,
        'instance_id': instance.id, 'release_id': release.id, 'entity_id': entity.id,
        'source_id': source.id, 'proof': proof}
    return {**copy.deepcopy(value), 'basis_hash': _sha(value), 'observation_version': observation.version}


def feedback_basis(db, p, observation_id, rule_id, expected_observation_version=None):
    """Cold-read only; expose proof solely to the private feedback authority."""
    try:
        return _basis(db, p, observation_id, rule_id, expected_observation_version)
    except HTTPException as error:
        raise HTTPException(error.status_code if error.status_code in {403, 404, 422} else 409, ERROR) from None
    except (ValueError, KeyError, TypeError, AttributeError, OSError, OverflowError, RecursionError,
            SchemaError, ValidationError):
        raise HTTPException(409, ERROR) from None
