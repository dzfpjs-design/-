"""Candidate data reception for actual native audio, not an extractor fallback."""
import copy

from sqlalchemy import select

from . import models as m
from .contracts import digest
from . import native_audio_provenance_v136 as origin

SCHEMA = 'instance-native-audio-data-reception-v1'


def handles(db, p, observation):
    if origin.has_issued(db, p, observation):
        return True
    value = observation.data.get('native_packet')
    return (type(value) is dict and type(value.get('source_key')) is str
        and value['source_key'].startswith('AudioRun:'))


def receipt(db, p, event, observation):
    if not origin.has_issued(db,p,observation):
        # Real pre-V136 runs cannot acquire a newly signed execution/input
        # anchor. Preserve their existing legacy no-data-plan reception only.
        from .native_receipts import checked
        from .instance_data_receipts import verify_release
        checked(db,p,observation)
        task=origin.row(db,m.Task,observation.data['native_packet']['task_id'],p)
        release=origin.row(db,m.InstanceRelease,task.data['instance_release_id'],p)
        origin.need(verify_release(db,p,release) is None
            and 'data_reception' not in observation.data and 'data_reception' not in event.data)
        return None
    context = origin.checked_observation(db, p, observation, event)
    from .instance_data_receipts import verify_release, _declarations
    release = context['release']; plan = verify_release(db, p, release)
    if plan is None:
        # A genuine legacy release retains its old no-plan receiver. New native
        # metadata does not retrofit or sign a declaration onto that release.
        origin.need('data_reception' not in observation.data and 'data_reception' not in event.data)
        return None
    task, run, source = context['task'], context['run'], context['source']
    declared = _declarations(plan, context['capability'], context['receipt']['output'])
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'event_id': event.id,
        'observation_id': observation.id, 'entity_id': observation.entity_id,
        'instance_id': release.instance_id, 'release_id': release.id,
        'release_number': release.release_number, 'package_id': release.package_id,
        'configuration_hash': release.configuration_hash,
        'requirements_schema': plan['schema'], 'requirements_hash': plan['requirements_hash'],
        'source_id': source.id, 'source_hash': source.content_hash,
        'source_record_id': observation.source_record_id, 'processing_version': observation.processing_version,
        'native_packet_hash': digest(context['packet']), 'source_event_base_hash': digest(context['base']),
        'run_type': 'AudioRun', 'run_id': run.id, 'run_generation': run.data['generation'],
        'run_request_hash': run.data['request_hash'], 'run_result_hash': run.data['result_hash'],
        'run_machine_hash': context['packet']['source_machine_hash'],
        'workflow_task_id': task.id, 'business_id': task.data['business_id'],
        'processing_job_id': context['job'].id, 'processing_actor_id': context['job'].data['actor_id'],
        'receipt_actor_id': observation.data['submitted_by'],
        'completion_audit_id': context['completion_audit'].id,
        'input_audit_id': context['input_audit'].id,
        'result_audit_id': context['result_audit'].id,
        'receipt_audit_id': context['reception_audit'].id,
        'input_audio_id': context['attached'].id, 'audio_snapshot_hash': context['attached'].data['snapshot_hash'],
        'object_snapshot_hash': digest(next(item for item in task.data['object_snapshots']
            if item['id'] == observation.entity_id)),
        'installation_hash': digest(context['installation']), 'invocation_id': context['call'].id,
        'workflow_receipt_hash': digest(context['receipt']),
        'evidence': [{'id': context['evidence'].id, 'sha256': context['evidence'].sha256}],
        # The frozen data plan describes the original capability output. It did
        # not declare this later native packet normalizer. Keep both explicit.
        'declaration_status': 'not_declared', 'declared_outputs': [],
        'declaration_reason': 'native_packet_not_in_original_data_plan',
        'original_declared_outputs': declared,
        'candidate_only': True, 'not_authoritative': True, 'source_time_verified': False,
        'physical_state_verified': False, 'entity_state_updated': False,
        'can_execute': False, 'can_delete': False}
    return {**value, 'receipt_hash': digest(value)}


def verify_memory(observation, memory):
    origin.need('native_receipt' in memory.data and origin.same(memory.data['native_receipt'],
        observation.data['native_packet']) and 'observed_at' in memory.data
        and memory.data['observed_at'] is None and memory.data.get('invocation_id') is None)

