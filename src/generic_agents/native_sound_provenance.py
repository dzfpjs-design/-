"""Independent Task sound-result provenance; never an ASR or numeric source."""
import copy
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, or_
from . import models as m
from .contracts import Manifest, digest
from .sound_models import EnvironmentSoundRun
from . import native_sound_data_plan as design

PACKET = 'native_sound_packet_v1'
ISSUED = 'native_sound_issued_v1'
ISSUED_ACTION = 'sound.native_result_issued_v1'
SOURCE_ACTION = 'sound.native_source_derived_v1'
RECEPTION_SCHEMA = 'instance-native-sound-data-reception-v1'
ERROR = '原环境声音成果、任务、原件或独立数据交接凭据无法核对；原分数与人工记录保留'
TZ = ZoneInfo('Asia/Shanghai')


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def iso(value):
    need(isinstance(value, datetime) and value.utcoffset() is not None)
    return value.astimezone(UTC).isoformat()


def row(db, p, cls, identity):
    value = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(value is not None)
    return value


def audits(db, p, action, identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))


def source_manifest(instance_id):
    return Manifest(kind='source', namespace='platform_native_sound_results',
        name='instance_' + instance_id.replace('-', ''), version='1.0.0', owner='平台数据管理',
        description='业务任务完整原音的环境声音候选；未知录制时刻保留未知。',
        allowed_agent_roles=['perception', 'data'], execution={'adapter': 'upload'},
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
        specification={'managed_instance': instance_id, 'source_family': design.FAMILY,
            'date_basis': 'machine_result_registered', 'timezone': 'Asia/Shanghai',
            'score_semantics': 'uncalibrated_multilabel', 'candidate_only': True},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})


def source_anchor(instance_id, source_hash):
    return {'schema': 'native-sound-source-v1', 'instance_id': instance_id, 'source_hash': source_hash}


def checked_source(db, p, source, instance_id):
    expected = source_manifest(instance_id).model_dump(mode='json')
    need(source.workspace_id == p.workspace_id and source.kind == 'source'
        and digest(source.data) == digest(expected) == source.content_hash == source.validated_hash)
    own = audits(db, p, SOURCE_ACTION, source.id)
    inventory = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == SOURCE_ACTION, m.Audit.data['instance_id'].as_string() == instance_id)))
    need(len(own) == len(inventory) == 1 and own[0].id == inventory[0].id
        and digest(own[0].data) == digest(source_anchor(instance_id, source.content_hash)))
    return source


def has_issued(db, p, observation):
    if any(k in observation.data for k in (PACKET, ISSUED)):
        return True
    receipt = observation.data.get('data_reception')
    if type(receipt) is dict and receipt.get('schema') == RECEPTION_SCHEMA:
        return True
    if audits(db, p, ISSUED_ACTION, observation.id):
        return True
    if str(observation.source_record_id).startswith('EnvironmentSoundRun:'):
        return True
    source = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.id == observation.source_id))
    if source is not None and (source.namespace == 'platform_native_sound_results'
            or source.data.get('specification', {}).get('source_family') == design.FAMILY):
        return True
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id))
    return event is not None and PACKET in event.data


def observations(db, p, run_id):
    anchors = select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run_id)
    return list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.data[PACKET]['run_id'].as_string() == run_id,
            m.Observation.data[ISSUED]['run_id'].as_string() == run_id,
            m.Observation.source_record_id.startswith('EnvironmentSoundRun:' + run_id + ':'),
            m.Observation.id.in_(anchors))).execution_options(populate_existing=True)))


def declaration(context):
    run, release = context['run'], context['release']
    try:
        return design.declared(release.data['snapshot'], run.data['business_id'], run.capability_id, run.data['capability_hash'])
    except (KeyError, ValueError, TypeError):
        need(False, '原业务版本未声明声音成果与独立接收；请正常发布含声音数据方案的新版本')


def completed(db, p, run, *, producer_job=None):
    from .workflow_sound import completed_context
    context = completed_context(db, p, run)
    need(context['run'].id == run.id)
    job = context['job']
    if producer_job is None:
        if job.status == 'failed':
            from .sound_result_settlement import checked_settled
            return checked_settled(db, p, run)
        need(job.status == 'succeeded')
    else:
        # This is only the finishing worker's same transaction. It does not
        # turn the uncommitted running Job into a historical success credential.
        from .leases import ownership
        lease = ownership(db)
        need(job.id == producer_job.id and job.status == 'running' and job.workspace_id == p.workspace_id
            and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
            and bool(job.lease_token) and job.data.get('actor_id') == p.user_id == run.owner_id)
    context['input_audit'] = row(db, p, m.Audit, run.data['input_audit_id'])
    context['result_audit'] = row(db, p, m.Audit, run.data['result_audit_id'])
    declaration(context)
    return context


def packet(context, source, observation):
    run, task = context['run'], context['task']; plan, _ = declaration(context)
    need(len(task.data['entity_ids']) == 1 and observation.entity_id == task.data['entity_ids'][0])
    original = run.data['sound_snapshot']['snapshot']; classification = run.data['result']['classification']
    value = {'schema': 'native-sound-result-packet-v1', 'workspace_id': run.workspace_id,
        'instance_id': run.data['instance_id'], 'release_id': run.data['release_id'],
        'business_id': run.data['business_id'], 'task_id': run.task_id, 'run_id': run.id,
        'entity_id': observation.entity_id, 'source_id': source.id,
        'sound_input_id': run.sound_input_id, 'sound_snapshot_hash': digest(run.data['sound_snapshot']),
        'capability_id': run.capability_id, 'capability_hash': run.data['capability_hash'],
        'job_id': run.job_id, 'invocation_id': run.invocation_id,
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output']), 'installation_proof_hash': digest(run.data['installation_proof']),
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'completion_audit_id': context['completion_audit'].id, 'data_plan_hash': plan['plan_hash'],
        'evidence_ids': [original['evidence_id']], 'input_sha256': original['input_sha256'],
        'configuration': copy.deepcopy(original['configuration']),
        'window_count': len(classification['windows']), 'candidate_label_count': len(classification['candidates']),
        'registered_at': iso(observation.created_at), 'registration_day': str(observation.created_at.astimezone(TZ).date()),
        'producer_status': 'succeeded', 'recorded_at': None, 'recording_time_verified': False,
        'candidate_only': True, 'not_authoritative': True, 'physical_state_verified': False,
        'entity_state_updated': False, 'association_basis': 'task_entity_association_not_verified_sound_attribution'}
    if context.get('settlement_audit') is not None:
        value.update(producer_status='failed', settlement=copy.deepcopy(context['settlement_audit'].data))
    return value


def event_base(observation, value):
    return {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': observation.source_id, 'invocation_id': value['invocation_id'],
        'evidence_ids': value['evidence_ids'], 'extracted': {'attributes': {}, 'anomalies': []},
        PACKET: copy.deepcopy(value), 'observed_at': None, 'candidate_only': True}


def issued_anchor(observation, event, value):
    return {'schema': 'native-sound-issued-v1', 'run_id': value['run_id'],
        'instance_id': value['instance_id'], 'task_id': value['task_id'],
        'observation_id': observation.id, 'event_id': event.id, 'source_id': observation.source_id,
        'entity_id': observation.entity_id, 'packet_hash': digest(value),
        'event_base_hash': digest(event_base(observation, value)),
        'registered_at': value['registered_at'], 'registration_day': value['registration_day']}


def checked_observation(db, p, observation, event=None, *, completed_context=None):
    try:
        observation = row(db, p, m.Observation, observation.id)
        value = observation.data[PACKET]; run = row(db, p, EnvironmentSoundRun, value['run_id'])
        context = completed_context if completed_context is not None else completed(db, p, run)
        need(context['run'].id == run.id and context['task'].id == run.task_id)
        source = checked_source(db, p, row(db, p, m.Capability, observation.source_id), run.data['instance_id'])
        expected = packet(context, source, observation)
        need(digest(value) == digest(expected) and observation.data.get('native_sound_packet_hash_v1') == digest(expected)
            and observation.status == 'processed' and observation.observed_at is None
            and observation.business_day == expected['registration_day']
            and observation.source_record_id == 'EnvironmentSoundRun:' + run.id + ':' + observation.entity_id
            and observation.processing_version == digest(expected)[:40]
            and observation.data.get('schema') == 'native-sound-observation-v1'
            and observation.data.get('native_instance_id') == run.data['instance_id']
            and observation.data.get('submitted_by') == run.owner_id and observation.data.get('candidate_only') is True
            and observation.data.get('invocation_id') == run.invocation_id
            and digest(observation.data.get('evidence_ids')) == digest(expected['evidence_ids'])
            and digest(observation.data.get('extracted')) == digest({'attributes': {}, 'anomalies': []}))
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            m.Event.idempotency_key == 'data:' + observation.id)))
        need(len(events) == 1 and (event is None or event.id == events[0].id)); saved = events[0]
        base = event_base(observation, expected)
        need(saved.kind == 'observation.data' and all(k in saved.data and digest(saved.data[k]) == digest(v) for k, v in base.items())
            and set(saved.data) <= set(base) | {'data_reception', 'delivery_error', 'previous_delivery_error', 'derivation_receipt'})
        own = audits(db, p, ISSUED_ACTION, observation.id); marker = issued_anchor(observation, saved, expected)
        inventory = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run.id)))
        need(len(own) == len(inventory) == 1 and own[0].id == inventory[0].id
            and own[0].actor_id == run.owner_id and digest(own[0].data) == digest(marker)
            and digest(observation.data.get(ISSUED)) == digest(marker) and len(observations(db, p, run.id)) == 1)
        return {**context, 'source': source, 'observation': observation, 'event': saved,
            'packet': expected, 'base': base, 'issued_audit': own[0]}
    except HTTPException as error:
        if error.status_code == 403:
            raise
        need(False)
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        need(False)
