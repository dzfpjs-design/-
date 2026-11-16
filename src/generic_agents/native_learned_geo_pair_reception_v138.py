"""Modern reception of actual learned candidates, not a generic native bypass."""
from .contracts import digest
from . import native_learned_geo_pair_provenance_v138 as origin

SCHEMA = origin.RECEPTION_SCHEMA


def handles(db, p, observation):
    # Independent new-family anchors select this path even if JSON tags vanish.
    return bool(origin.has_issued(db, p, observation))


def receipt(db, p, event, observation):
    from .instance_data_receipts import verify_release, _declarations
    context = origin.checked_observation(db, p, observation, event)
    release = context['release']; plan = verify_release(db, p, release)
    if plan is None:
        origin.need('data_reception' not in observation.data and 'data_reception' not in event.data)
        return None
    pair, task = context['pair'], context['task']
    declared = _declarations(plan, context['capability'], context['receipt']['output'])
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'event_id': event.id,
        'observation_id': observation.id, 'entity_id': observation.entity_id, 'instance_id': release.instance_id,
        'release_id': release.id, 'release_number': release.release_number, 'package_id': release.package_id,
        'configuration_hash': release.configuration_hash, 'requirements_schema': plan['schema'], 'requirements_hash': plan['requirements_hash'],
        'source_id': context['source'].id, 'source_hash': context['source'].content_hash,
        'source_record_id': observation.source_record_id, 'processing_version': observation.processing_version,
        'native_packet_hash': digest(context['packet']), 'source_event_base_hash': digest(context['base']),
        'run_type': 'GeoPair', 'request_kind': 'learned_binary_change', 'run_id': pair.id, 'run_generation': pair.data['generation'],
        'run_request_hash': pair.data['request_hash'], 'run_result_hash': pair.data['result_hash'],
        'run_machine_hash': context['packet']['source_machine_hash'], 'runtime_hash': pair.data['runtime_hash'],
        'model_trace': pair.data['model_trace'], 'model_trace_hash': pair.data['model_trace_hash'],
        'model_ready_audit_id': context['model_ready_audit'].id, 'workflow_task_id': task.id, 'business_id': task.data['business_id'],
        'processing_job_id': context['job'].id, 'processing_actor_id': context['job'].data['actor_id'],
        'receipt_actor_id': observation.data['submitted_by'], 'completion_audit_id': context['completion_audit'].id,
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'receipt_audit_id': context['reception_audit'].id, 'issued_audit_id': context['issued_audit'].id,
        'comparison_snapshot_hash': digest(task.data['comparison_scene_snapshots']), 'sources_hash': pair.data['sources_hash'],
        'object_snapshot_hash': digest(next(item for item in task.data['object_snapshots'] if item['id'] == observation.entity_id)),
        'invocation_id': context['call'].id, 'workflow_receipt_hash': digest(context['receipt']),
        'evidence': [{'id': item['evidence_id'], 'sha256': item['sha256']} for item in pair.data['sources']],
        'artifacts': {kind: {'id': item.id, 'sha256': item.sha256} for kind, item in context['artifacts'].items()},
        'declaration_status': 'not_declared', 'declared_outputs': [], 'declaration_reason': 'native_packet_not_in_original_data_plan',
        'original_declared_outputs': declared, 'candidate_only': True, 'learned_model': True, 'not_authoritative': True,
        'source_time_verified': False, 'physical_state_verified': False, 'entity_state_updated': False, 'can_execute': False, 'can_delete': False}
    return {**value, 'receipt_hash': digest(value)}


def verify_memory(observation, memory):
    from .native_geo_pair_reception_v137 import verify_memory as original_native_packet
    original_native_packet(observation, memory)
