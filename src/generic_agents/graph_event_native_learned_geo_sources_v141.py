"""Closed learned GeoPair messages, issued only after real data reception.

These are machine candidates, not entity facts. Historical projection checks use
the original V138 execution/ready/reception chain and the independently recorded
first-input eligibility. They never renew a model or a member's current rights.
"""
import copy
import math
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import Manifest, digest
from .services import require, validate
from . import native_learned_geo_pair_provenance_v138 as origin

ADAPTER = 'native-learned-binary-change-v1'
CONTRACT_SCHEMA = 'graph-event-native-learned-geo-source-contract-v1'
PROJECTION_SCHEMA = 'graph-event-native-learned-geo-projection-v1'
PROOF_SCHEMA = 'graph-event-native-learned-geo-proof-v1'
OUTPUT_SCHEMA = 'graph-event-native-learned-geo-output-v1'
_HASH = re.compile(r'^[a-f0-9]{64}$')
_CONTRACT = {'schema', 'adapter_id', 'event_kind', 'correlation_kind', 'instance_id',
    'source_id', 'source_hash', 'source_release_version', 'run_type', 'request_kind',
    'capability_id', 'capability_hash', 'capability_release_version', 'model_trace',
    'model_trace_hash', 'probability_threshold', 'alignment', 'runtime_hash',
    'result_schema', 'output_schema', 'output_schema_hash', 'contract_hash'}
_TRACE = {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'pipeline_hash', 'weights_sha256', 'backend'}
_ARTIFACTS = {'before', 'after', 'valid', 'probability', 'candidate',
    'probability_data', 'probability_raster', 'change_mask'}
_PROOF = {'schema', 'workspace_id', 'adapter_id', 'event_kind', 'event_key',
    'source_event_base_hash', 'observation_id', 'source_record_id', 'processing_version',
    'instance_id', 'source_id', 'source_hash', 'source_release_version', 'entity_id',
    'capability_id', 'capability_hash', 'capability_release_version',
    'run_id', 'run_generation', 'run_request_id', 'run_request_hash', 'run_machine_hash', 'run_result_hash',
    'model_trace_hash', 'runtime_hash', 'model_ready_audit_id', 'model_ready_audit_hash',
    'processing_job_id', 'processing_actor_id', 'processing_job_hash', 'receipt_actor_id',
    'receipt_audit_id', 'receipt_audit_hash', 'completion_audit_id', 'completion_audit_hash',
    'input_audit_id', 'input_anchor_hash', 'result_audit_id', 'result_anchor_hash',
    'issued_audit_id', 'issued_anchor_hash', 'eligibility_audit_id', 'eligibility_hash',
    'registration_audit_id', 'registration_hash', 'workflow_task_id', 'release_id',
    'configuration_hash', 'business_id', 'object_snapshot_hash', 'comparison_snapshot_hash',
    'sources_hash', 'workflow_receipt_hash', 'invocation_id', 'invocation_hash',
    'memory_id', 'memory_payload_hash', 'data_reception_hash', 'evidence', 'artifacts', 'native_packet_hash'}
RESULT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['attributes'],
    'properties': {'attributes': {'type': 'object', 'additionalProperties': False,
        'required': ['差异候选像素数', '未知像素数'], 'properties': {
            '差异候选像素数': {'type': 'integer', 'minimum': 0},
            '未知像素数': {'type': 'integer', 'minimum': 0}}}}}


def _need(value):
    if not value:
        raise ValueError('native_learned_geo_graph_source:invalid')


def _uuid(value):
    _need(type(value) is str and str(UUID(value)) == value)


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None)


def _safe(value):
    from .graph_event_observation_sources import _safe as safe_json
    safe_json(value)


def _trace(value):
    from .local_changer_install import PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE
    _need(type(value) is dict and set(value) == _TRACE and value['pack_id'] == PACK
        and type(value['generation']) is int and value['generation'] >= 1 and value['backend'] == 'cpu'
        and value['recipe_hash'] == RECIPE_HASH and value['pipeline_hash'] == PIPELINE_HASH
        and value['weights_sha256'] == RECIPE['files'][0]['sha256'])
    _uuid(value['installation_id'])
    for key in ('recipe_hash', 'pipeline_hash', 'weights_sha256'):
        _hash(value[key])


def output_schema():
    properties = {key: {'type': 'string'} for key in ('adapter_id', 'observation_id',
        'entity_id', 'source_id', 'run_id', 'task_id', 'business_day')}
    properties.update(schema={'const': OUTPUT_SCHEMA}, date_basis={'const': 'A_original_source_request'},
        observed_at={'type': 'null'}, candidate_only={'const': True}, data_received={'const': True},
        source_time_verified={'const': False}, physical_state_verified={'const': False},
        entity_state_updated={'const': False}, result=copy.deepcopy(RESULT_SCHEMA),
        candidate_pixels={'type': 'integer', 'minimum': 0, 'title': '差异候选像素数'},
        unknown_pixels={'type': 'integer', 'minimum': 0, 'title': '未知像素数'})
    return {'type': 'object', 'additionalProperties': False,
        'required': list(properties), 'properties': properties}


def check_contract(value):
    _safe(value)
    _need(type(value) is dict and set(value) == _CONTRACT and value['schema'] == CONTRACT_SCHEMA
        and value['adapter_id'] == ADAPTER and value['event_kind'] == 'observation.data'
        and value['correlation_kind'] == 'entity_id' and value['run_type'] == 'GeoPair'
        and value['request_kind'] == 'learned_binary_change')
    for key in ('instance_id', 'source_id', 'capability_id'):
        _uuid(value[key])
    for key in ('source_hash', 'capability_hash', 'model_trace_hash', 'runtime_hash', 'output_schema_hash', 'contract_hash'):
        _hash(value[key])
    for key in ('source_release_version', 'capability_release_version'):
        _need(type(value[key]) is str and bool(value[key]))
    _trace(value['model_trace'])
    threshold = value['probability_threshold']
    _need(type(threshold) in {int, float} and 0 < threshold < 1 and math.isfinite(threshold)
        and value['alignment'] in {'same_grid', 'reproject_after_to_before'}
        and value['runtime_hash'] == value['model_trace']['pipeline_hash']
        and value['model_trace_hash'] == digest(value['model_trace'])
        and digest(value['result_schema']) == digest(RESULT_SCHEMA)
        and digest(value['output_schema']) == digest(output_schema())
        and value['output_schema_hash'] == digest(value['output_schema'])
        and value['contract_hash'] == digest({key: item for key, item in value.items() if key != 'contract_hash'}))
    return copy.deepcopy(value)


def registration_fields(contract):
    value = check_contract(contract)
    return {key: value[key] for key in ('adapter_id', 'instance_id', 'source_id', 'source_hash',
        'capability_id', 'capability_hash')}


def build_contract(db, p, instance_id, source_id, capability_id, *, lock=True):
    from .native_audio_provenance_v136 import actor, row
    from .workflow_learned_geo_pairs import validate_manifest
    _need(type(lock) is bool)
    require(p, 'admin', 'operator')
    if lock:
        from .relations import lock_current_actor
        lock_current_actor(db, p)
    actor(db, p, p.user_id, lock=lock)
    instance = row(db, m.DomainInstance, instance_id, p, lock=lock)
    source, cap = [row(db, m.Capability, identity, p, lock=lock) for identity in (source_id, capability_id)]
    _need(instance.status == 'partially_active' and instance.active_release_id is not None
        and source.kind == 'source' and source.status == 'published' and source.namespace == 'platform_native_receipts'
        and source.content_hash == source.validated_hash == digest(source.data)
        and source.data['specification']['managed_instance'] == instance.id
        and source.data['execution']['adapter'] == 'upload'
        and source.data['data_policy']['cloud_allowed'] is False
        and source.data['data_policy']['external_allowed'] is False
        and cap.kind == 'specialist_model' and cap.status == 'published'
        and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.data['execution']['adapter'] == 'local_learned_geo_pair')
    execution = cap.data['execution']
    _trace(execution['model_trace'])
    # Hold the actual installation row through live validation and admission's
    # pre-cursor work. Historical checks below never take this current gate.
    row(db, m.SpecialistInstallation, execution['model_trace']['installation_id'], p, lock=lock)
    validate_manifest(db, p, Manifest.model_validate(cap.data), live=True)
    value = {'schema': CONTRACT_SCHEMA, 'adapter_id': ADAPTER, 'event_kind': 'observation.data',
        'correlation_kind': 'entity_id', 'instance_id': instance.id, 'source_id': source.id,
        'source_hash': source.content_hash, 'source_release_version': source.release_version,
        'run_type': 'GeoPair', 'request_kind': 'learned_binary_change', 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'capability_release_version': cap.release_version,
        'model_trace': copy.deepcopy(execution['model_trace']), 'model_trace_hash': digest(execution['model_trace']),
        'probability_threshold': execution['probability_threshold'], 'alignment': execution['alignment'],
        'runtime_hash': execution['runtime_hash'], 'result_schema': copy.deepcopy(RESULT_SCHEMA),
        'output_schema': output_schema()}
    value['output_schema_hash'] = digest(value['output_schema'])
    value['contract_hash'] = digest(value)
    return check_contract(value)


def verify_contract_current(db, p, contract, *, lock=False):
    value = check_contract(contract)
    rebuilt = build_contract(db, p, value['instance_id'], value['source_id'], value['capability_id'], lock=lock)
    _need(digest(value) == digest(rebuilt))
    return rebuilt


def check_projection(value, contract=None):
    _safe(value)
    _need(type(value) is dict and set(value) == {'schema', 'source_event_id', 'observation_id',
        'source_key', 'correlation', 'output', 'proof', 'projection_hash'} and value['schema'] == PROJECTION_SCHEMA)
    for key in ('source_event_id', 'observation_id', 'correlation'):
        _uuid(value[key])
    _hash(value['projection_hash'])
    _need(value['projection_hash'] == digest({key: item for key, item in value.items() if key != 'projection_hash'}))
    proof, output = value['proof'], value['output']
    _need(type(proof) is dict and set(proof) == _PROOF and proof['schema'] == PROOF_SCHEMA
        and proof['adapter_id'] == ADAPTER and proof['event_kind'] == 'observation.data')
    identifiers = {'workspace_id', 'observation_id', 'instance_id', 'source_id', 'entity_id', 'capability_id',
        'run_id', 'model_ready_audit_id', 'processing_job_id', 'processing_actor_id', 'receipt_actor_id',
        'receipt_audit_id', 'completion_audit_id', 'input_audit_id', 'result_audit_id', 'issued_audit_id',
        'eligibility_audit_id', 'registration_audit_id', 'workflow_task_id', 'release_id', 'invocation_id', 'memory_id'}
    hashes = {'source_event_base_hash', 'source_hash', 'capability_hash', 'run_request_id', 'run_request_hash',
        'run_machine_hash', 'run_result_hash', 'model_trace_hash', 'runtime_hash', 'model_ready_audit_hash',
        'processing_job_hash', 'receipt_audit_hash', 'completion_audit_hash', 'input_anchor_hash', 'result_anchor_hash',
        'issued_anchor_hash', 'eligibility_hash', 'registration_hash', 'configuration_hash', 'object_snapshot_hash',
        'comparison_snapshot_hash', 'sources_hash', 'workflow_receipt_hash', 'invocation_hash',
        'memory_payload_hash', 'data_reception_hash', 'native_packet_hash'}
    for key in identifiers:
        _uuid(proof[key])
    for key in hashes:
        _hash(proof[key])
    for key in ('source_release_version', 'capability_release_version', 'processing_version', 'business_id'):
        _need(type(proof[key]) is str and bool(proof[key]))
    _need(type(proof['run_generation']) is int and proof['run_generation'] >= 1
        and proof['observation_id'] == value['observation_id'] and proof['entity_id'] == value['correlation']
        and proof['event_key'] == 'data:' + value['observation_id']
        and proof['source_record_id'] == 'GeoPair:' + proof['run_id'] + ':' + value['correlation'])
    _need(type(proof['evidence']) is list and len(proof['evidence']) == 2
        and type(proof['artifacts']) is dict and set(proof['artifacts']) == _ARTIFACTS)
    for item in proof['evidence'] + list(proof['artifacts'].values()):
        _need(type(item) is dict and set(item) == {'id', 'sha256'})
        _uuid(item['id']); _hash(item['sha256'])
    try:
        validate(output_schema(), output)
    except HTTPException as error:
        raise ValueError('native_learned_geo_graph_source:invalid_output') from error
    _need(output['adapter_id'] == ADAPTER and output['observation_id'] == value['observation_id']
        and output['entity_id'] == value['correlation'] and output['source_id'] == proof['source_id']
        and output['run_id'] == proof['run_id'] and output['task_id'] == proof['workflow_task_id']
        and all(type(output[key]) is bool and output[key] is True for key in ('candidate_only', 'data_received'))
        and all(type(output[key]) is bool and output[key] is False for key in
            ('source_time_verified', 'physical_state_verified', 'entity_state_updated'))
        and all(type(number) is int and number >= 0 for number in output['result']['attributes'].values())
        and all(type(output[key]) is int and output[key] >= 0
            and output[key] == output['result']['attributes'][label] for key, label in
            (('candidate_pixels', '差异候选像素数'), ('unknown_pixels', '未知像素数')))
        and value['source_key'] == 'native-learned-geo:' + digest({'adapter_id': ADAPTER,
            'run_id': proof['run_id'], 'entity_id': proof['entity_id'], 'machine_hash': proof['run_machine_hash']}))
    if contract is not None:
        frozen = check_contract(contract)
        _need(all(proof[key] == frozen[key] for key in ('instance_id', 'source_id', 'source_hash',
            'source_release_version', 'capability_id', 'capability_hash', 'capability_release_version',
            'model_trace_hash', 'runtime_hash')))
    return copy.deepcopy(value)


def project_source(db, p, producer, event, observation, *, live=True, lock=True):
    from .native_audio_provenance_v136 import row, actor
    from .graph_event_admission import check_producer
    from .graph_event_models import GraphEventProducer
    from .graph_event_native_learned_geo_eligibility_v141 import eligibility_proof
    from .instance_data_receipts import observation_receipt
    _need(type(live) is bool and type(lock) is bool and (not live or lock))
    producer = row(db, GraphEventProducer, producer.id, p, lock=lock)
    contract = check_contract(check_producer(producer))
    if live:
        _need(producer.status == 'active')
        verify_contract_current(db, p, contract, lock=lock)
    context = origin.checked_observation(db, p, observation, event)
    obs, pair, task, source = context['observation'], context['pair'], context['task'], context['source']
    job, call, receipt, cap = context['job'], context['call'], context['receipt'], context['capability']
    _need(context['release'].instance_id == contract['instance_id'] and source.id == contract['source_id']
        and source.content_hash == contract['source_hash'] and source.release_version == contract['source_release_version']
        and cap.id == contract['capability_id'] and cap.content_hash == contract['capability_hash']
        and cap.release_version == contract['capability_release_version']
        and origin.same(pair.data['model_trace'], contract['model_trace'])
        and pair.data['runtime_hash'] == contract['runtime_hash']
        and origin.same(cap.data['execution']['probability_threshold'], contract['probability_threshold'])
        and cap.data['execution']['alignment'] == contract['alignment'])
    eligibility = eligibility_proof(db, p, producer, context)
    _need(type(eligibility) is dict and set(eligibility) == {'audit', 'registration_audit'})
    received = observation_receipt(db, p, obs)['data_reception']
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == context['event'].id,
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))
    _need(context['event'].status == 'received' and len(memories) == 1
        and memories[0].status == 'candidate' and received['schema'] == origin.RECEPTION_SCHEMA
        and received['run_id'] == pair.id and received['observation_id'] == obs.id
        and received['event_id'] == context['event'].id and received['entity_id'] == obs.entity_id)
    if live:
        require(p, 'admin', 'operator')
        from .relations import lock_current_actor
        lock_current_actor(db, p)
        _need(p.user_id == obs.data['submitted_by'])
        for identity in sorted({producer.registered_by, obs.data['submitted_by'], job.data['actor_id']}):
            actor(db, p, identity, lock=lock)
    packet = context['packet']
    output = {'schema': OUTPUT_SCHEMA, 'adapter_id': ADAPTER, 'observation_id': obs.id,
        'entity_id': obs.entity_id, 'source_id': source.id, 'run_id': pair.id, 'task_id': task.id,
        'business_day': packet['business_day'], 'date_basis': packet['date_basis'], 'observed_at': None,
        'candidate_only': True, 'data_received': True, 'source_time_verified': False,
        'physical_state_verified': False, 'entity_state_updated': False,
        'candidate_pixels': packet['attributes']['差异候选像素数'], 'unknown_pixels': packet['attributes']['未知像素数'],
        'result': {'attributes': {key: packet['attributes'][key] for key in ('差异候选像素数', '未知像素数')}}}
    job_identity = {'id': job.id, 'kind': job.kind, 'key': job.idempotency_key,
        'actor_id': job.data['actor_id'], 'task_id': task.id, 'invocation_key': job.data['invocation_key'],
        'run_id': pair.id, 'generation': pair.data['generation'], 'input_hash': receipt['input_hash'],
        'scope': job.data.get('scope', pair.data.get('workflow_graph_scope'))}
    invocation = {'id': call.id, 'capability_id': call.capability_id, 'key': call.idempotency_key,
        **{key: call.data.get(key) for key in ('input_hash', 'output', 'capability_hash', 'role', 'job_id', 'lease_token')}}
    proof = {'schema': PROOF_SCHEMA, 'workspace_id': p.workspace_id, 'adapter_id': ADAPTER,
        'event_kind': 'observation.data', 'event_key': context['event'].idempotency_key,
        'source_event_base_hash': digest(context['base']), 'observation_id': obs.id,
        'source_record_id': obs.source_record_id, 'processing_version': obs.processing_version,
        'instance_id': context['release'].instance_id, 'source_id': source.id, 'source_hash': source.content_hash,
        'source_release_version': source.release_version, 'entity_id': obs.entity_id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'capability_release_version': cap.release_version,
        'run_id': pair.id, 'run_generation': pair.data['generation'], 'run_request_id': pair.data['request_id'],
        'run_machine_hash': packet['source_machine_hash'], 'run_result_hash': pair.data['result_hash'],
        'run_request_hash': pair.data['request_hash'], 'model_trace_hash': pair.data['model_trace_hash'],
        'runtime_hash': pair.data['runtime_hash'], 'model_ready_audit_id': context['model_ready_audit'].id,
        'model_ready_audit_hash': digest(context['model_ready_audit'].data),
        'processing_job_id': job.id, 'processing_actor_id': job.data['actor_id'], 'processing_job_hash': digest(job_identity),
        'receipt_actor_id': obs.data['submitted_by'], 'receipt_audit_id': context['reception_audit'].id,
        'receipt_audit_hash': digest(context['reception_audit'].data),
        'completion_audit_id': context['completion_audit'].id, 'completion_audit_hash': digest(context['completion_audit'].data),
        'input_audit_id': context['input_audit'].id, 'input_anchor_hash': digest(context['input_audit'].data),
        'result_audit_id': context['result_audit'].id, 'result_anchor_hash': digest(context['result_audit'].data),
        'issued_audit_id': context['issued_audit'].id, 'issued_anchor_hash': digest(context['issued_audit'].data),
        'eligibility_audit_id': eligibility['audit'].id, 'eligibility_hash': digest(eligibility['audit'].data),
        'registration_audit_id': eligibility['registration_audit'].id, 'registration_hash': digest(eligibility['registration_audit'].data),
        'workflow_task_id': task.id, 'release_id': context['release'].id,
        'configuration_hash': context['release'].configuration_hash, 'business_id': task.data['business_id'],
        'object_snapshot_hash': digest(next(item for item in task.data['object_snapshots'] if item['id'] == obs.entity_id)),
        'comparison_snapshot_hash': digest(task.data['comparison_scene_snapshots']), 'sources_hash': pair.data['sources_hash'],
        'workflow_receipt_hash': digest(receipt), 'invocation_id': call.id, 'invocation_hash': digest(invocation),
        'memory_id': memories[0].id, 'memory_payload_hash': digest(memories[0].data), 'data_reception_hash': digest(received),
        'evidence': [{'id': item['evidence_id'], 'sha256': item['sha256']} for item in pair.data['sources']],
        'artifacts': {kind: {'id': item.id, 'sha256': item.sha256} for kind, item in context['artifacts'].items()},
        'native_packet_hash': obs.data['native_packet_hash']}
    value = {'schema': PROJECTION_SCHEMA, 'source_event_id': context['event'].id,
        'observation_id': obs.id, 'source_key': 'native-learned-geo:' + digest({'adapter_id': ADAPTER,
            'run_id': pair.id, 'entity_id': obs.entity_id, 'machine_hash': packet['source_machine_hash']}),
        'correlation': obs.entity_id, 'output': output, 'proof': proof}
    value['projection_hash'] = digest(value)
    return check_projection(value, contract)
