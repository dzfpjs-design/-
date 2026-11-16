"""Sound handoff outbox and independent candidate data-reception contract."""
import copy
from fastapi import HTTPException
from sqlalchemy import select, text
from . import models as m
from .contracts import digest
from .services import audit, enqueue_event
from .sound_models import EnvironmentSoundRun
from . import native_sound_provenance as origin

need = origin.need
SCHEMA = origin.RECEPTION_SCHEMA


def handles(db, p, observation):
    return origin.has_issued(db, p, observation)


def _source(db, p, instance_id):
    expected = origin.source_manifest(instance_id).model_dump(mode='json')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest(['native-sound-source-v1', p.workspace_id, instance_id])[:15], 16)})
    source = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.namespace == expected['namespace'], m.Capability.name == expected['name'],
        m.Capability.release_version == expected['version']).execution_options(populate_existing=True))
    if source is None:
        old = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == origin.SOURCE_ACTION, m.Audit.data['instance_id'].as_string() == instance_id).limit(1))
        need(old is None)
        source = m.Capability(workspace_id=p.workspace_id, namespace=expected['namespace'], name=expected['name'],
            release_version=expected['version'], kind='source', status='published', data=expected,
            content_hash=digest(expected), validated_hash=digest(expected))
        db.add(source); db.flush()
        audit(db, p, origin.SOURCE_ACTION, source.id, origin.source_anchor(instance_id, source.content_hash))
        db.flush()
    need(source.status == 'published')
    return origin.checked_source(db, p, source, instance_id)


def _emit(db, p, context):
    run, task = context['run'], context['task']
    need(run.owner_id == p.user_id and len(task.data['entity_ids']) == 1)
    origin.declaration(context)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest(['native-sound-handoff-v1', p.workspace_id, run.id])[:15], 16)})
    rows = origin.observations(db, p, run.id)
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == origin.ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run.id)))
    if rows or issued:
        need(len(rows) == len(issued) == 1)
        origin.checked_observation(db, p, rows[0], completed_context=context)
        return 0
    source = _source(db, p, run.data['instance_id']); entity_id = task.data['entity_ids'][0]
    observation = m.Observation(workspace_id=p.workspace_id, source_id=source.id,
        source_record_id='EnvironmentSoundRun:' + run.id + ':' + entity_id,
        processing_version='pending-sound-v1', entity_id=entity_id, observed_at=None, status='processed', data={})
    db.add(observation); db.flush()
    value = origin.packet(context, source, observation)
    observation.business_day = value['registration_day']; observation.processing_version = digest(value)[:40]
    observation.data = {'schema': 'native-sound-observation-v1', origin.PACKET: value,
        'native_sound_packet_hash_v1': digest(value), 'native_instance_id': run.data['instance_id'],
        'submitted_by': p.user_id, 'candidate_only': True, 'evidence_ids': value['evidence_ids'],
        'invocation_id': run.invocation_id, 'extracted': {'attributes': {}, 'anomalies': []}}
    event = enqueue_event(db, p, 'observation.data', 'data:' + observation.id, origin.event_base(observation, value))
    db.flush(); marker = origin.issued_anchor(observation, event, value)
    observation.data = {**observation.data, origin.ISSUED: marker}
    audit(db, p, origin.ISSUED_ACTION, observation.id, marker); db.flush()
    origin.checked_observation(db, p, observation, event, completed_context=context)
    return 1


def emit_completed(db, p, job):
    from .leases import ownership
    lease = ownership(db)
    need(job.workspace_id == p.workspace_id and job.status == 'running'
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token and bool(job.lease_token))
    runs = list(db.scalars(select(EnvironmentSoundRun).where(EnvironmentSoundRun.workspace_id == p.workspace_id,
        EnvironmentSoundRun.job_id == job.id, EnvironmentSoundRun.status == 'succeeded')))
    return sum(_emit(db, p, origin.completed(db, p, run, producer_job=job)) for run in runs)


def emit_settled(db, p, run, job, proof):
    from .sound_result_settlement import checked_settled
    context = checked_settled(db, p, run, finishing_job=job)
    need(context['settlement_audit'].id == proof.id)
    return _emit(db, p, context)


def receipt(db, p, event, observation):
    context = origin.checked_observation(db, p, observation, event)
    run, release, packet = context['run'], context['release'], context['packet']
    from .instance_data_receipts import verify_release
    need(verify_release(db, p, release) is not None)
    plan, declared = origin.declaration(context)
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'source_id': context['source'].id,
        'source_hash': context['source'].content_hash, 'observation_id': observation.id, 'event_id': event.id,
        'instance_id': run.data['instance_id'], 'release_id': release.id, 'package_id': release.package_id,
        'configuration_hash': release.configuration_hash, 'data_plan_hash': plan['plan_hash'],
        'business_id': run.data['business_id'], 'task_id': run.task_id, 'run_id': run.id, 'entity_id': observation.entity_id,
        'sound_input_id': run.sound_input_id, 'sound_snapshot_hash': digest(run.data['sound_snapshot']),
        'job_id': run.job_id, 'invocation_id': run.invocation_id, 'capability_id': run.capability_id,
        'capability_hash': run.data['capability_hash'], 'input_audit_id': context['input_audit'].id,
        'result_audit_id': context['result_audit'].id, 'completion_audit_id': context['completion_audit'].id,
        'issued_audit_id': context['issued_audit'].id, 'packet_hash': digest(packet),
        'event_base_hash': digest(context['base']), 'request_hash': run.data['request_hash'],
        'result_hash': run.data['result_hash'], 'output_hash': digest(run.data['output']),
        'installation_proof_hash': digest(run.data['installation_proof']),
        'evidence_ids': copy.deepcopy(packet['evidence_ids']), 'registered_at': packet['registered_at'],
        'registration_day': packet['registration_day'], 'declaration_status': 'declared_native_sound_result',
        'declared_outputs': declared, 'producer_status': packet['producer_status'],
        'candidate_only': True, 'not_authoritative': True, 'source_time_verified': False,
        'physical_state_verified': False, 'entity_state_updated': False, 'can_execute': False, 'can_delete': False}
    if 'settlement' in packet:
        value['settlement'] = copy.deepcopy(packet['settlement'])
    return {**value, 'receipt_hash': digest(value)}


def verify_memory(observation, memory):
    need(digest(memory.data.get(origin.PACKET)) == digest(observation.data[origin.PACKET])
        and memory.data.get('observed_at', 'absent') is None
        and memory.data.get('invocation_id') == observation.data['invocation_id'])


def _memories(db, p, event):
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id,
        m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))


def receipt_projection(db, p, run):
    value = {'schema': 'native-sound-reception-status-v1', 'run_id': run.id, 'observation_id': None,
        'event_id': None, 'memory_id': None, 'state': 'waiting_data', 'receipt': None, 'integrity_issue': None}
    try:
        from .workflow_sound import checked_saved_run
        context = checked_saved_run(db, p, run.id); rows = origin.observations(db, p, run.id)
        if context['job'].status in {'failed', 'cancelled'} and not rows:
            from .sound_result_settlement import _command_rows, _checked_failure
            jobs = _command_rows(db, p, run_id=run.id)
            for failed in jobs:
                if failed.status in {'failed', 'cancelled'}:
                    _checked_failure(db, p, failed)
                else:
                    need(failed.status in {'queued', 'running'})
            if any(j.status in {'queued', 'running'} for j in jobs):
                value.update(state='local_handoff_pending', integrity_issue='原处理失败记录保留，正在继续交接已保存成果；尚未完成独立收件。')
            else:
                value.update(state='producer_failed', integrity_issue='原任务交接未成功；原模型成果保留，请由有权的原执行人核对后明确继续交接。')
            return value
        if context['job'].status in {'queued', 'running'}:
            need(not rows)
            return value
        origin.completed(db, p, run); need(len(rows) == 1)
        observation = rows[0]; value['observation_id'] = observation.id
        context = origin.checked_observation(db, p, observation); event = context['event']; value['event_id'] = event.id
        if event.status == 'received':
            from .instance_data_receipts import observation_receipt
            actual = observation_receipt(db, p, observation)['data_reception']; memories = _memories(db, p, event)
            need(len(memories) == 1 and event.received_at is not None)
            value.update(state='received', receipt=actual, memory_id=memories[0].id)
        else:
            need(event.status in {'pending_delivery', 'blocked_delivery'} and event.received_at is None
                and not _memories(db, p, event) and 'data_reception' not in observation.data and 'data_reception' not in event.data)
        return value
    except HTTPException as error:
        if error.status_code == 403:
            raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        pass
    return {**value, 'state': 'blocked_integrity', 'receipt': None, 'memory_id': None, 'integrity_issue': origin.ERROR}


def require_task_received(db, p, task):
    from .workflow_sound import consumed_runs
    runs = consumed_runs(db, p, task); identities = {r.id for r in runs}
    # Completion/forward JSON deletion cannot hide an independently issued run.
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == origin.ISSUED_ACTION, m.Audit.data['task_id'].as_string() == task.id)))
    need(all(a.data.get('run_id') in identities for a in issued))
    for run in runs:
        result = receipt_projection(db, p, run)
        need(result['state'] == 'received', result['integrity_issue'] or
            '本次声音成果仍待数据管理智能体独立接收；人工草稿和候选核查可以先保存。')


def task_reception_gate(db, p, task):
    try:
        require_task_received(db, p, task)
        return {'allowed': True, 'reason': None}
    except HTTPException as error:
        if error.status_code == 403:
            raise
        return {'allowed': False, 'reason': str(error.detail)}
