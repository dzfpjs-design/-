"""Modern reception of new, scoped native video results; never a workflow step.

The capture and video workers have their own real Jobs and independent frozen
provenance. They did not execute a Task node or a Gateway Invocation. Receiving
their saved-frame summary therefore grants no declared workflow completion.
"""
from fastapi import HTTPException

from .contracts import digest
from . import native_video_provenance_v164 as origin


SCHEMA = 'instance-native-video-data-reception-v1'


def _need(value):
    if not value:
        raise HTTPException(409, '原视频读取、保存画面或数据接收依据无法核对；原件与处理记录保留')


def _same(left, right):
    return digest(left) == digest(right)


def handles(db, p, observation):
    # Independent issuance inventory prevents deleting the JSON tag from
    # selecting another receiver. Known old VideoRun packets also stay here;
    # they cannot acquire a new modern receipt by falling back to an extractor.
    if origin.has_issued(db, p, observation):
        return True
    value = observation.data.get('native_packet')
    return (type(value) is dict and type(value.get('source_key')) is str
        and value['source_key'].startswith('VideoRun:'))


def receipt(db, p, event, observation):
    from .instance_data_receipts import verify_release

    if not origin.has_issued(db, p, observation):
        # Preserve only the actual pre-plan receiver. A modern release with
        # old/no-input video must fail closed, never acquire this new receipt.
        from .native_receipts import checked
        from .instance_data_receipts import _destination, _event_matches
        from .models import InstanceRelease, VideoRun
        from .services import scoped
        checked(db, p, observation)
        packet = observation.data['native_packet']
        _need(type(packet) is dict and type(packet.get('source_key')) is str
            and packet['source_key'].startswith('VideoRun:'))
        run = scoped(db, VideoRun, packet['source_key'].split(':', 1)[1], p)
        source_context = run.data.get('source_context')
        _need(source_context is None or type(source_context) is dict)
        association = source_context.get('domain_binding') if source_context is not None else None
        _need(association is None or type(association) is dict)
        identity = observation.data.get('native_instance_id')
        _need(type(identity) is str and bool(identity))
        if association is not None:
            # An Entity can legitimately originate in an older compatible
            # release. That origin cannot turn this Run's modern fixed
            # destination into a pre-plan video receiver.
            _need(type(association.get('instance_release_id')) is str
                and bool(association['instance_release_id'])
                and association.get('instance_id') == identity)
            release = scoped(db, InstanceRelease, association['instance_release_id'], p)
            _need(release.instance_id == identity)
        else:
            release, _, _ = _destination(db, p, observation)
            _need(release is None or release.instance_id == identity)
        _need((release is None or verify_release(db, p, release) is None)
            and 'data_reception' not in observation.data and 'data_reception' not in event.data)
        _event_matches(event, observation)
        _need('native_receipt' in event.data and _same(event.data['native_receipt'], packet))
        return None
    context = origin.checked_observation(db, p, observation, event, live=False)
    release = context['release']
    plan = verify_release(db, p, release)
    # New V164 scope is a modern data plan, not an invitation to retrofit a
    # receipt onto an old source or to invent a plan for a legacy release.
    _need(type(plan) is dict and _same(plan, context['plan'])
        and _same(plan, context['requirements']))
    run, session = context['run'], context['session']
    source, native_source = context['source'], context['native_source']
    scope = context['frozen_scope']
    _need(native_source.id == observation.source_id
        and scope['source_id'] == source.id and scope['source_hash'] == source.content_hash
        and scope['instance_id'] == release.instance_id and scope['release_id'] == release.id
        and scope['entity_id'] == observation.entity_id
        and scope['requirements_schema'] == plan['schema']
        and scope['requirements_hash'] == plan['requirements_hash'])
    frames = context['frames']
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'event_id': event.id,
        'observation_id': observation.id, 'entity_id': observation.entity_id,
        'instance_id': release.instance_id, 'release_id': release.id,
        'release_number': release.release_number, 'package_id': release.package_id,
        'configuration_hash': release.configuration_hash,
        'requirements_schema': plan['schema'], 'requirements_hash': plan['requirements_hash'],
        'source_id': native_source.id, 'source_hash': native_source.content_hash,
        'origin_source_id': source.id, 'origin_source_hash': source.content_hash,
        'source_record_id': observation.source_record_id, 'processing_version': observation.processing_version,
        'native_packet_hash': digest(context['packet']), 'source_event_base_hash': digest(context['base']),
        'run_type': 'VideoRun', 'run_id': run.id, 'run_generation': run.data['generation'],
        'run_request_key': run.request_key, 'run_result_hash': run.data['result_hash'],
        'run_machine_hash': context['packet']['source_machine_hash'],
        'session_id': session.id, 'capture_hash': session.data['capture_hash'],
        'pipeline_hash': run.data['pipeline_hash'], 'capture_receipt_hash': digest(session.data['receipt']),
        'read_reservation_id': scope['reservation_id'], 'supplement_plan_id': scope['plan_id'],
        'automatic_watch_id': scope['watch_id'], 'business_id': scope['business_id'],
        'workflow_task_id': None, 'invocation_id': None,
        'capture_job_id': context['capture_job'].id, 'capture_actor_id': context['input_audit'].actor_id,
        'processing_job_id': context['job'].id, 'processing_actor_id': context['result_audit'].actor_id,
        'receipt_actor_id': observation.data['submitted_by'],
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'completion_audit_id': context['completion_audit'].id, 'issued_audit_id': context['issued_audit'].id,
        'receipt_audit_id': context['reception_audit'].id,
        'installation_hash': digest(context['installation']), 'frozen_scope_hash': digest(scope),
        'evidence': [{'id': run.evidence_id, 'sha256': run.data['input_sha256']}],
        'frames': [{'id': frame.id, 'evidence_id': frame.evidence_id,
            'sample_index': frame.sample_index, 'sha256': frame.data['frame_sha256'],
            'receipt_hash': frame.data['receipt_hash']} for frame in frames],
        'saved_frames': len(frames),
        'declaration_status': 'not_declared', 'declared_outputs': [],
        'declaration_reason': 'native_saved_video_frames_not_single_image_step_output',
        'original_declared_outputs': [],
        'candidate_only': True, 'not_authoritative': True, 'source_time_verified': False,
        'physical_state_verified': False, 'entity_state_updated': False,
        'can_execute': False, 'can_delete': False}
    if scope.get('capture_mode') == 'manual':
        value['capture_mode'] = 'manual'
    return {**value, 'receipt_hash': digest(value)}


def verify_memory(observation, memory):
    # The common receiver separately cold-checks the unique scoped Event,
    # candidate Memory and ACK. Preserve the native packet's actual presence,
    # types and unknown clock rather than accepting missing/None aliases.
    _need(type(memory.data) is dict and 'native_receipt' in memory.data
        and _same(memory.data['native_receipt'], observation.data['native_packet'])
        and 'observed_at' in memory.data and memory.data['observed_at'] is None
        and 'invocation_id' in memory.data and memory.data['invocation_id'] is None)
