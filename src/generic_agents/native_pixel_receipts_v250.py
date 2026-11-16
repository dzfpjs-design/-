"""Emit one real pixel handoff and verify its independent existing receiver ACK."""
import copy

from fastapi import HTTPException
from sqlalchemy import select, text

from . import models as m
from .contracts import digest
from .services import audit, enqueue_event
from . import native_pixel_provenance_v250 as origin

need = origin.need
SCHEMA = origin.RECEPTION_SCHEMA


def handles(db, p, observation):
    return origin.has_issued(db, p, observation)


def _source(db, p, instance_id):
    value = origin.source_manifest(instance_id).model_dump(mode='json')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest(['native-pixel-source-v250', p.workspace_id, instance_id])[:15], 16)})
    source = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.namespace == value['namespace'], m.Capability.name == value['name'],
        m.Capability.release_version == value['version']).with_for_update(read=True)
        .execution_options(populate_existing=True))
    if source is None:
        previous = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == origin.SOURCE_ACTION, m.Audit.data['instance_id'].as_string() == instance_id).limit(1))
        need(previous is None)  # Missing original identity is not a new source.
        source = m.Capability(workspace_id=p.workspace_id, namespace=value['namespace'],
            name=value['name'], release_version=value['version'], kind='source', status='published',
            data=value, content_hash=digest(value), validated_hash=digest(value))
        db.add(source); db.flush()
        audit(db, p, origin.SOURCE_ACTION, source.id, origin.source_anchor(instance_id, source.content_hash))
        db.flush()
    need(source.status == 'published')
    return origin.checked_source(db, p, source, instance_id)


def _emit(db, p, context):
    run, task = context['run'], context['task']
    need(p.user_id == origin.issuer(context) and len(task.data['entity_ids']) == 1)
    origin.declaration(context)
    existing = origin.observations(db, p, run.id)
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == origin.ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run.id)))
    if existing or issued:
        need(len(existing) == len(issued) == 1)
        origin.checked_observation(db, p, existing[0], completed_context=context)
        return 0
    source = _source(db, p, run.data['instance_id'])
    entity_id = task.data['entity_ids'][0]
    observation = m.Observation(workspace_id=p.workspace_id, source_id=source.id,
        source_record_id='WorkflowPixelRun:' + run.id + ':' + entity_id,
        processing_version='pending-v250', entity_id=entity_id, observed_at=None,
        status='processed', data={})
    db.add(observation); db.flush()
    packet = origin.packet(context, source, observation)
    observation.business_day = packet['registration_day']
    observation.processing_version = digest(packet)[:40]
    observation.data = {'schema': 'native-pixel-observation-v250',
        origin.PACKET: packet, 'native_pixel_packet_hash_v250': digest(packet),
        'native_instance_id': run.data['instance_id'], 'submitted_by': p.user_id,
        'candidate_only': True, 'evidence_ids': packet['evidence_ids'],
        'invocation_id': run.invocation_id, 'extracted': {'attributes': {}, 'anomalies': []}}
    event = enqueue_event(db, p, 'observation.data', 'data:' + observation.id,
                          origin.event_base(observation, packet))
    db.flush()
    marker = origin.issued_anchor(observation, event, packet)
    observation.data = {**observation.data, origin.ISSUED: marker}
    audit(db, p, origin.ISSUED_ACTION, observation.id, marker)
    db.flush()
    origin.checked_observation(db, p, observation, event, completed_context=context)
    return 1


def emit_completed(db, p, job):
    """Only the real parent's finishing transaction owns this writer seam."""
    from .workflow_pixel_runs_v250 import checked_completed_run
    from .leases import ownership
    lease = ownership(db)
    need(job.workspace_id == p.workspace_id and job.status == 'running'
         and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
         and bool(job.lease_token))
    runs = list(db.scalars(select(m.WorkflowPixelRun).where(m.WorkflowPixelRun.workspace_id == p.workspace_id,
        m.WorkflowPixelRun.job_id == job.id, m.WorkflowPixelRun.status == 'succeeded')))
    return sum(_emit(db, p, checked_completed_run(db, p, run, job)) for run in runs)


def emit_settled(db, p, run, settlement):
    """Explicit original-result handoff; never changes the failed producer Job."""
    from .workflow_pixel_runs_v250 import checked_settled_run
    return _emit(db, p, checked_settled_run(db, p, run, settlement))


def receipt(db, p, event, observation):
    context = origin.checked_observation(db, p, observation, event)
    run, release, packet = context['run'], context['release'], context['packet']
    from .instance_data_receipts import verify_release
    need(verify_release(db, p, release) is not None)
    plan, declarations = origin.declaration(context)
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id,
        'source_id': context['source'].id, 'source_hash': context['source'].content_hash,
        'observation_id': observation.id, 'event_id': context['event'].id,
        'instance_id': run.data['instance_id'], 'release_id': release.id,
        'package_id': release.package_id, 'configuration_hash': release.configuration_hash,
        'data_plan_hash': plan['plan_hash'], 'business_id': run.data['business_id'],
        'task_id': run.task_id, 'run_id': run.id, 'entity_id': observation.entity_id,
        'job_id': run.job_id, 'invocation_id': run.invocation_id,
        'capability_id': run.capability_id, 'capability_hash': run.data['capability_hash'],
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'completion_audit_id': context['completion_audit'].id,
        'issued_audit_id': context['issued_audit'].id, 'packet_hash': digest(packet),
        'event_base_hash': digest(context['base']), 'request_hash': run.data['request_hash'],
        'result_hash': run.data['result_hash'], 'output_hash': digest(run.data['output']),
        'model_trace_hash': run.data['model_trace_hash'], 'sources_hash': run.data['sources_hash'],
        'artifacts': copy.deepcopy(packet['artifacts']), 'registered_at': packet['registered_at'],
        'registration_day': packet['registration_day'],
        'declaration_status': 'declared_native_pixel_result', 'declared_outputs': declarations,
        'producer_status': packet['producer_status'], 'settlement': copy.deepcopy(packet['settlement']),
        'candidate_only': True, 'not_authoritative': True, 'source_time_verified': False,
        'field_truth_verified': False, 'object_state_updated': False, 'can_execute': False, 'can_delete': False}
    return {**value, 'receipt_hash': digest(value)}


def verify_memory(observation, memory):
    need(memory.data.get(origin.PACKET) == observation.data[origin.PACKET]
         and memory.data.get('observed_at', 'absent') is None
         and memory.data.get('invocation_id') == observation.data['invocation_id'])


def _memories(db, p, event):
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id,
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))


def receipt_projection(db, p, run):
    value = {'schema': 'native-pixel-reception-status-v250', 'run_id': run.id,
        'observation_id': None, 'event_id': None, 'memory_id': None,
        'state': 'waiting_data', 'receipt': None, 'integrity_issue': None}
    try:
        from .workflow_pixel_runs_v250 import checked_saved_run, checked_run, SETTLED_ACTION
        saved = checked_saved_run(db, p, run)
        rows = origin.observations(db, p, run.id)
        settlements = origin.audits(db, p, SETTLED_ACTION, run.id)
        if saved['job'].status == 'failed' and not settlements:
            need(not rows)
            value.update(state='producer_failed',
                integrity_issue='原业务处理未成功终结；请从原任务核对并明确恢复，旧失败记录保留。')
            return value
        if saved['job'].status in {'queued', 'running'} and not rows and not settlements:
            return value
        checked_run(db, p, run)
        need(len(rows) == 1)
        observation = rows[0]; value['observation_id'] = observation.id
        context = origin.checked_observation(db, p, observation)
        event = context['event']; value['event_id'] = event.id
        if event.status == 'received':
            from .instance_data_receipts import observation_receipt
            actual = observation_receipt(db, p, observation)['data_reception']
            memories = _memories(db, p, event)
            need(len(memories) == 1 and event.received_at is not None)
            value.update(state='received', receipt=actual, memory_id=memories[0].id)
        else:
            need(event.status in {'pending_delivery', 'blocked_delivery'}
                 and event.received_at is None and not _memories(db, p, event)
                 and 'data_reception' not in observation.data and 'data_reception' not in event.data)
        return value
    except HTTPException as error:
        if error.status_code == 403:
            raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        pass
    value.update(state='blocked_integrity', receipt=None, memory_id=None, integrity_issue=origin.ERROR)
    return value


def require_task_received(db, p, task):
    from .workflow_pixel_runs_v250 import consumed_runs_for_task
    runs = consumed_runs_for_task(db, p, task)
    for run in runs:
        result = receipt_projection(db, p, run)
        need(result['state'] == 'received', result['integrity_issue'] or
             '本次像素成果仍待数据管理智能体独立接收；人工草稿可以先保存。')
    return None


def task_reception_gate(db, p, task):
    try:
        require_task_received(db, p, task)
        return {'allowed': True, 'reason': None}
    except HTTPException as error:
        if error.status_code == 403:
            raise
        return {'allowed': False, 'reason': str(error.detail)}
