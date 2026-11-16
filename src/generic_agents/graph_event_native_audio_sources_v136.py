"""Explicit native AudioRun source family; never a processing Invocation adapter."""
import copy
import re
from uuid import UUID

from fastapi import HTTPException

from . import models as m
from .contracts import digest
from .services import require, validate
from . import native_audio_provenance_v136 as origin

ADAPTER = 'native-audio-transcription-v1'
CONTRACT_SCHEMA = 'graph-event-native-audio-source-contract-v1'
PROJECTION_SCHEMA = 'graph-event-native-audio-projection-v1'
PROOF_SCHEMA = 'graph-event-native-audio-proof-v1'
OUTPUT_SCHEMA = 'graph-event-native-audio-output-v1'
_HASH = re.compile(r'^[a-f0-9]{64}$')
_CONTRACT = {'schema', 'adapter_id', 'event_kind', 'correlation_kind', 'instance_id',
    'source_id', 'source_hash', 'source_release_version', 'run_type', 'installation',
    'installation_hash', 'result_schema', 'output_schema', 'output_schema_hash', 'contract_hash'}
_PROOF = {'schema', 'workspace_id', 'adapter_id', 'event_kind', 'event_key',
    'source_event_base_hash', 'observation_id', 'source_record_id', 'processing_version',
    'instance_id', 'source_id', 'source_hash', 'source_release_version', 'entity_id',
    'run_id', 'run_generation', 'run_request_id', 'run_machine_hash', 'run_result_hash',
    'run_request_hash', 'installation_hash', 'processing_job_id', 'processing_actor_id',
    'receipt_actor_id', 'receipt_audit_id', 'completion_audit_id', 'input_audit_id', 'result_audit_id', 'processing_job_hash',
    'workflow_task_id', 'release_id', 'configuration_hash', 'business_id',
    'object_snapshot_hash', 'audio_snapshot_hash', 'workflow_receipt_hash',
    'invocation_id', 'invocation_hash', 'evidence', 'native_packet_hash'}
RESULT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['attributes'],
    'properties': {'attributes': {'type': 'object', 'additionalProperties': False,
        'required': ['已执行整片转写', '机器转写文字'], 'properties': {
            '已执行整片转写': {'type': 'boolean'},
            '机器转写文字': {'type': 'string', 'maxLength': 12000}}}}}


def _need(ok):
    if not ok:
        raise ValueError('native_audio_graph_source:invalid')


def _uuid(value):
    _need(type(value) is str and str(UUID(value)) == value)


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None)


def _safe(value):
    from .graph_event_observation_sources import _safe as safe_json
    safe_json(value)


def output_schema():
    properties = {key: {'type': 'string'} for key in ('adapter_id', 'observation_id',
        'entity_id', 'source_id', 'run_id', 'task_id', 'business_day', 'date_basis')}
    properties.update(schema={'const': OUTPUT_SCHEMA}, observed_at={'type': 'null'},
        candidate_only={'const': True}, source_time_verified={'const': False},
        physical_state_verified={'const': False}, entity_state_updated={'const': False},
        result=copy.deepcopy(RESULT_SCHEMA))
    return {'type': 'object', 'additionalProperties': False,
        'required': list(properties), 'properties': properties}


def check_contract(value):
    _safe(value)
    _need(type(value) is dict and set(value) == _CONTRACT
        and value['schema'] == CONTRACT_SCHEMA and value['adapter_id'] == ADAPTER
        and value['event_kind'] == 'observation.data' and value['correlation_kind'] == 'entity_id'
        and value['run_type'] == 'AudioRun')
    for key in ('instance_id', 'source_id'):
        _uuid(value[key])
    for key in ('source_hash', 'installation_hash', 'output_schema_hash', 'contract_hash'):
        _hash(value[key])
    _need(type(value['source_release_version']) is str and bool(value['source_release_version']))
    pin = value['installation']
    _need(type(pin) is dict and set(pin) == {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'backend'}
        and type(pin['generation']) is int and pin['generation'] >= 1 and pin['backend'] == 'cpu'
        and pin['pack_id'] == 'sensevoice-small-v1')
    _uuid(pin['installation_id']); _hash(pin['recipe_hash'])
    _need(value['installation_hash'] == digest(pin)
        and digest(value['result_schema']) == digest(RESULT_SCHEMA)
        and digest(value['output_schema']) == digest(output_schema())
        and value['output_schema_hash'] == digest(value['output_schema'])
        and value['contract_hash'] == digest({k: v for k, v in value.items() if k != 'contract_hash'}))
    return copy.deepcopy(value)


def registration_fields(contract):
    value = check_contract(contract)
    return {'adapter_id': ADAPTER, 'instance_id': value['instance_id'],
        'source_id': value['source_id'], 'source_hash': value['source_hash'],
        'installation_id': value['installation']['installation_id'],
        'installation_hash': value['installation_hash']}


def build_contract(db, p, instance_id, source_id, installation_id, *, lock=True):
    origin.need(type(lock) is bool)
    require(p, 'admin', 'operator')
    if lock:
        from .relations import lock_current_actor
        lock_current_actor(db, p)
    origin.actor(db, p, p.user_id, lock=lock)
    instance = origin.row(db, m.DomainInstance, instance_id, p, lock=lock)
    source = origin.row(db, m.Capability, source_id, p, lock=lock)
    origin.need(instance.active_release_id is not None and instance.status == 'partially_active'
        and source.kind == 'source' and source.status == 'published'
        and source.namespace == 'platform_native_receipts'
        and source.content_hash == source.validated_hash == digest(source.data)
        and source.data['specification']['managed_instance'] == instance.id
        and source.data['execution']['adapter'] == 'upload'
        and source.data['data_policy']['cloud_allowed'] is False
        and source.data['data_policy']['external_allowed'] is False)
    installation = origin.installation_pin(db, p, installation_id, live=True, lock=lock)
    value = {'schema': CONTRACT_SCHEMA, 'adapter_id': ADAPTER, 'event_kind': 'observation.data',
        'correlation_kind': 'entity_id', 'instance_id': instance.id, 'source_id': source.id,
        'source_hash': source.content_hash, 'source_release_version': source.release_version,
        'run_type': 'AudioRun', 'installation': installation, 'installation_hash': digest(installation),
        'result_schema': copy.deepcopy(RESULT_SCHEMA), 'output_schema': output_schema()}
    value['output_schema_hash'] = digest(value['output_schema'])
    value['contract_hash'] = digest(value)
    return check_contract(value)


def verify_contract_current(db, p, contract, *, lock=False):
    value = check_contract(contract)
    actual = build_contract(db, p, value['instance_id'], value['source_id'],
        value['installation']['installation_id'], lock=lock)
    origin.need(origin.same(actual, value))
    return actual


def check_projection(value, contract=None):
    _safe(value)
    _need(type(value) is dict and set(value) == {'schema', 'source_event_id', 'observation_id',
        'source_key', 'correlation', 'output', 'proof', 'projection_hash'}
        and value['schema'] == PROJECTION_SCHEMA)
    for key in ('source_event_id', 'observation_id', 'correlation'):
        _uuid(value[key])
    _hash(value['projection_hash'])
    _need(value['projection_hash'] == digest({k: v for k, v in value.items() if k != 'projection_hash'}))
    proof = value['proof']; output = value['output']
    _need(type(proof) is dict and set(proof) == _PROOF and proof['schema'] == PROOF_SCHEMA
        and proof['adapter_id'] == ADAPTER and proof['event_kind'] == 'observation.data')
    for key in ('workspace_id', 'observation_id', 'instance_id', 'source_id', 'entity_id',
            'run_id', 'processing_job_id', 'processing_actor_id', 'receipt_actor_id',
            'receipt_audit_id', 'completion_audit_id', 'input_audit_id', 'result_audit_id', 'workflow_task_id', 'release_id', 'invocation_id'):
        _uuid(proof[key])
    for key in ('source_event_base_hash', 'source_hash', 'run_machine_hash', 'run_result_hash',
            'run_request_hash', 'installation_hash', 'processing_job_hash', 'configuration_hash',
            'object_snapshot_hash', 'audio_snapshot_hash', 'workflow_receipt_hash',
            'invocation_hash', 'native_packet_hash'):
        _hash(proof[key])
    _need(type(proof['run_generation']) is int and proof['run_generation'] >= 1
        and type(proof['run_request_id']) is str and bool(proof['run_request_id'])
        and type(proof['processing_version']) is str and bool(proof['processing_version'])
        and type(proof['source_release_version']) is str and bool(proof['source_release_version'])
        and type(proof['business_id']) is str and bool(proof['business_id'])
        and proof['observation_id'] == value['observation_id']
        and proof['entity_id'] == value['correlation']
        and proof['event_key'] == 'data:' + value['observation_id']
        and proof['source_record_id'] == 'AudioRun:' + proof['run_id'] + ':' + value['correlation'])
    _need(type(proof['evidence']) is list and len(proof['evidence']) == 1)
    item = proof['evidence'][0]
    _need(type(item) is dict and set(item) == {'id', 'sha256'})
    _uuid(item['id']); _hash(item['sha256'])
    try:
        validate(output_schema(), output)
    except HTTPException as error:
        raise ValueError('native_audio_graph_source:invalid_output') from error
    _need(output['adapter_id'] == ADAPTER and output['observation_id'] == value['observation_id']
        and output['entity_id'] == value['correlation'] and output['source_id'] == proof['source_id']
        and output['run_id'] == proof['run_id'] and output['task_id'] == proof['workflow_task_id']
        and type(output['candidate_only']) is bool and output['candidate_only'] is True
        and all(type(output[key]) is bool and output[key] is False for key in
            ('source_time_verified', 'physical_state_verified', 'entity_state_updated'))
        and value['source_key'] == 'native-audio:' + digest({'adapter_id': ADAPTER,
            'run_id': proof['run_id'], 'entity_id': proof['entity_id'], 'machine_hash': proof['run_machine_hash']}))
    if contract is not None:
        frozen = check_contract(contract)
        _need(all(proof[key] == frozen[key] for key in
            ('instance_id', 'source_id', 'source_hash', 'source_release_version', 'installation_hash')))
    return copy.deepcopy(value)


def project_source(db, p, producer, event, observation, *, live=True, lock=True):
    origin.need(type(live) is bool and type(lock) is bool and (not live or lock))
    from .graph_event_admission import check_producer
    from .graph_event_models import GraphEventProducer
    producer = origin.row(db, GraphEventProducer, producer.id, p, lock=lock)
    contract = check_contract(check_producer(producer))
    if live:
        origin.need(producer.status == 'active')
        verify_contract_current(db, p, contract, lock=lock)
        origin.actor(db, p, producer.registered_by, lock=lock)
    context = origin.checked_observation(db, p, observation, event, live=live, lock=lock)
    obs, run, task = context['observation'], context['run'], context['task']
    job, call, receipt, source = context['job'], context['call'], context['receipt'], context['source']
    origin.need(obs.data['submitted_by'] == p.user_id if live else True)
    origin.need(context['release'].instance_id == contract['instance_id']
        and source.id == contract['source_id'] and source.content_hash == contract['source_hash']
        and source.release_version == contract['source_release_version']
        and origin.same(context['installation'], contract['installation']))
    packet = context['packet']
    result = {'attributes': {key: packet['attributes'][key]
        for key in ('已执行整片转写', '机器转写文字')}}
    output = {'schema': OUTPUT_SCHEMA, 'adapter_id': ADAPTER, 'observation_id': obs.id,
        'entity_id': obs.entity_id, 'source_id': source.id, 'run_id': run.id, 'task_id': task.id,
        'business_day': packet['business_day'], 'date_basis': packet['date_basis'], 'observed_at': None,
        'candidate_only': True, 'source_time_verified': False, 'physical_state_verified': False,
        'entity_state_updated': False, 'result': result}
    job_identity = {'id': job.id, 'kind': job.kind, 'key': job.idempotency_key,
        'actor_id': job.data['actor_id'], 'task_id': task.id, 'invocation_key': job.data['invocation_key'],
        'run_id': run.id, 'generation': run.data['generation'],
        'input_hash': receipt['input_hash'], 'scope': job.data.get('scope', run.data.get('workflow_graph_scope'))}
    invocation = {'id': call.id, 'capability_id': call.capability_id, 'key': call.idempotency_key,
        **{key: call.data.get(key) for key in ('input_hash', 'output', 'capability_hash', 'role', 'job_id', 'lease_token')}}
    proof = {'schema': PROOF_SCHEMA, 'workspace_id': p.workspace_id, 'adapter_id': ADAPTER,
        'event_kind': 'observation.data', 'event_key': context['event'].idempotency_key,
        'source_event_base_hash': digest(context['base']), 'observation_id': obs.id,
        'source_record_id': obs.source_record_id, 'processing_version': obs.processing_version,
        'instance_id': context['release'].instance_id, 'source_id': source.id, 'source_hash': source.content_hash,
        'source_release_version': source.release_version, 'entity_id': obs.entity_id,
        'run_id': run.id, 'run_generation': run.data['generation'], 'run_request_id': run.request_id,
        'run_machine_hash': packet['source_machine_hash'], 'run_result_hash': run.data['result_hash'],
        'run_request_hash': run.data['request_hash'], 'installation_hash': digest(context['installation']),
        'processing_job_id': job.id, 'processing_actor_id': job.data['actor_id'],
        'receipt_actor_id': obs.data['submitted_by'], 'receipt_audit_id': context['reception_audit'].id,
        'completion_audit_id': context['completion_audit'].id, 'input_audit_id': context['input_audit'].id,
        'result_audit_id': context['result_audit'].id,
        'processing_job_hash': digest(job_identity),
        'workflow_task_id': task.id, 'release_id': context['release'].id,
        'configuration_hash': context['release'].configuration_hash, 'business_id': task.data['business_id'],
        'object_snapshot_hash': digest(next(item for item in task.data['object_snapshots'] if item['id'] == obs.entity_id)),
        'audio_snapshot_hash': context['attached'].data['snapshot_hash'], 'workflow_receipt_hash': digest(receipt),
        'invocation_id': call.id, 'invocation_hash': digest(invocation),
        'evidence': [{'id': context['evidence'].id, 'sha256': context['evidence'].sha256}],
        'native_packet_hash': obs.data['native_packet_hash']}
    value = {'schema': PROJECTION_SCHEMA, 'source_event_id': context['event'].id,
        'observation_id': obs.id, 'source_key': 'native-audio:' + digest({'adapter_id': ADAPTER,
            'run_id': run.id, 'entity_id': obs.entity_id, 'machine_hash': packet['source_machine_hash']}),
        'correlation': obs.entity_id, 'output': output, 'proof': proof}
    value['projection_hash'] = digest(value)
    return check_projection(value, contract)
