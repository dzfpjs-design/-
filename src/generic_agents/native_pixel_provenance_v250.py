"""Independent pixel-result handoff identity; never a numeric sensor source."""
import copy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, or_

from . import models as m
from .contracts import Manifest, digest
from .services import audit
from . import native_pixel_data_plan_v250 as design

PACKET = 'native_pixel_packet_v250'
ISSUED = 'native_pixel_issued_v250'
ISSUED_ACTION = 'native_pixel.receipt_issued_v250'
SOURCE_ACTION = 'native_pixel.source_derived_v250'
RECEPTION_SCHEMA = 'instance-native-pixel-data-reception-v250'
ERROR = '原像素成果、任务范围、独立来源或数据接收依据无法核对；原件及人工记录保留'
TZ = ZoneInfo('Asia/Shanghai')


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def same(left, right):
    return digest(left) == digest(right)


def iso(value):
    need(isinstance(value, datetime) and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def row(db, p, cls, identity):
    value = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
                      .execution_options(populate_existing=True))
    need(value is not None)
    return value


def audits(db, p, action, identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))


def source_manifest(instance_id):
    return Manifest(kind='source', namespace='platform_native_pixel_results',
        name='instance_' + instance_id.replace('-', ''), version='1.0.0',
        owner='平台数据管理', description='业务任务本地像素变化候选成果（采集时刻未知）',
        allowed_agent_roles=['perception', 'data'], execution={'adapter': 'upload'},
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
        specification={'managed_instance': instance_id, 'source_family': design.FAMILY,
            'coordinate_space': 'pixel', 'date_basis': 'machine_result_registered',
            'timezone': 'Asia/Shanghai'},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})


def source_anchor(instance_id, source_hash):
    return {'schema': 'native-pixel-source-v250', 'instance_id': instance_id,
            'source_hash': source_hash}


def checked_source(db, p, source, instance_id):
    expected = source_manifest(instance_id).model_dump(mode='json')
    need(source.workspace_id == p.workspace_id and source.kind == 'source'
         and same(source.data, expected) and source.content_hash == source.validated_hash == digest(expected))
    issued = audits(db, p, SOURCE_ACTION, source.id)
    need(len(issued) == 1 and issued[0].data == source_anchor(instance_id, source.content_hash))
    inventory = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == SOURCE_ACTION, m.Audit.data['instance_id'].as_string() == instance_id)))
    need(len(inventory) == 1 and inventory[0].id == issued[0].id)
    return source


def has_issued(db, p, observation):
    """Independent source, Event and Audit traces prevent JSON-tag downgrade."""
    if any(key in observation.data for key in (PACKET, ISSUED)):
        return True
    receipt = observation.data.get('data_reception')
    if type(receipt) is dict and receipt.get('schema') == RECEPTION_SCHEMA:
        return True
    if audits(db, p, ISSUED_ACTION, observation.id):
        return True
    record_id = getattr(observation, 'source_record_id', '')
    if type(record_id) is str and record_id.startswith('WorkflowPixelRun:'):
        pieces = record_id.split(':')
        if len(pieces) == 3:
            run_id = pieces[1]
            run = db.scalar(select(m.WorkflowPixelRun.id).where(m.WorkflowPixelRun.workspace_id == p.workspace_id,
                m.WorkflowPixelRun.id == run_id))
            trace = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_(['workflow_pixel.input_frozen_v250', 'workflow_pixel.result_frozen_v250']),
                m.Audit.object_id == run_id).limit(1))
            if run is not None or trace is not None:
                return True
    source = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
        m.Capability.id == observation.source_id))
    if source is not None and (getattr(source, 'namespace', None) == 'platform_native_pixel_results'
            or source.data.get('specification', {}).get('source_family') == design.FAMILY):
        return True
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id))
    return bool(event is not None and PACKET in event.data)


def observations(db, p, run_id):
    issued_ids = select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run_id)
    return list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.data[PACKET]['run_id'].as_string() == run_id,
            m.Observation.data[ISSUED]['run_id'].as_string() == run_id,
            m.Observation.source_record_id.startswith('WorkflowPixelRun:' + run_id + ':'),
            m.Observation.id.in_(issued_ids))).execution_options(populate_existing=True)))


def declaration(context):
    release, run = context['release'], context['run']
    try:
        return design.declared(release.data['snapshot'], run.data['business_id'],
                               run.capability_id, run.data['capability_hash'])
    except (ValueError, KeyError, TypeError):
        need(False)


def issuer(context):
    settled = context.get('settlement_audit')
    return settled.actor_id if settled is not None else context['run'].owner_id


def settlement_proof(context):
    # The authoritative core verifies the independent command and all producer
    # anchors. Do not reinterpret an arbitrary Audit as a local recovery grant.
    value = context.get('settlement_audit')
    return copy.deepcopy(value.data) if value is not None else None


def packet(context, source, observation):
    run, task = context['run'], context['task']
    need(len(task.data['entity_ids']) == 1 and observation.entity_id == task.data['entity_ids'][0])
    plan, _ = declaration(context)
    return {'schema': 'native-pixel-result-packet-v250', 'workspace_id': run.workspace_id,
        'instance_id': run.data['instance_id'], 'release_id': run.data['release_id'],
        'business_id': run.data['business_id'], 'task_id': task.id, 'run_id': run.id,
        'entity_id': observation.entity_id, 'source_id': source.id,
        'capability_id': run.capability_id, 'capability_hash': run.data['capability_hash'],
        'job_id': run.job_id, 'invocation_id': run.invocation_id,
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output']), 'sources_hash': run.data['sources_hash'],
        'model_trace_hash': run.data['model_trace_hash'], 'data_plan_hash': plan['plan_hash'],
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'completion_audit_id': context['completion_audit'].id,
        # Normal emission shares the producer's successful terminal commit.
        # Its uncommitted running value must not change the immutable packet.
        'producer_status': 'failed' if context.get('settlement_audit') is not None else 'succeeded',
        'settlement': settlement_proof(context),
        'artifacts': copy.deepcopy(run.data['result']['artifacts']),
        'evidence_ids': [run.data['sources'][side]['id'] for side in ('before', 'after')],
        'registered_at': iso(observation.created_at),
        'registration_day': str(observation.created_at.astimezone(TZ).date()),
        'coordinate_space': 'pixel', 'georeference_available': False,
        'candidate_only': True, 'not_authoritative': True, 'field_truth_verified': False,
        'object_state_updated': False, 'association_basis': 'task_entity_association_not_verified_coverage'}


def event_base(observation, value):
    return {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': observation.source_id, 'invocation_id': value['invocation_id'],
        'evidence_ids': value['evidence_ids'], 'extracted': {'attributes': {}, 'anomalies': []},
        PACKET: copy.deepcopy(value), 'observed_at': None, 'candidate_only': True}


def issued_anchor(observation, event, value):
    return {'schema': 'native-pixel-issued-v250', 'run_id': value['run_id'],
        'instance_id': value['instance_id'], 'task_id': value['task_id'],
        'observation_id': observation.id, 'event_id': event.id, 'source_id': observation.source_id,
        'entity_id': observation.entity_id, 'packet_hash': digest(value),
        'event_base_hash': digest(event_base(observation, value)),
        'registered_at': value['registered_at'], 'registration_day': value['registration_day']}


def checked_observation(db, p, observation, event=None, *, completed_context=None):
    """Cold, SELECT-only verification. completed_context is writer-internal only."""
    try:
        observation = row(db, p, m.Observation, observation.id)
        value = observation.data.get(PACKET)
        need(type(value) is dict and type(value.get('run_id')) is str)
        from .workflow_pixel_runs_v250 import checked_run
        run = row(db, p, m.WorkflowPixelRun, value['run_id'])
        context = completed_context if completed_context is not None else checked_run(db, p, run)
        need(context['run'].id == run.id and run.workspace_id == p.workspace_id)
        source = checked_source(db, p, row(db, p, m.Capability, observation.source_id), run.data['instance_id'])
        expected = packet(context, source, observation)
        need(same(value, expected) and observation.data.get('native_pixel_packet_hash_v250') == digest(expected)
             and observation.status == 'processed' and observation.observed_at is None
             and observation.business_day == expected['registration_day']
             and observation.source_record_id == 'WorkflowPixelRun:' + run.id + ':' + observation.entity_id
             and observation.processing_version == digest(expected)[:40]
             and observation.data.get('schema') == 'native-pixel-observation-v250'
             and observation.data.get('native_instance_id') == run.data['instance_id']
             and observation.data.get('submitted_by') == issuer(context)
             and observation.data.get('candidate_only') is True
             and observation.data.get('invocation_id') == run.invocation_id
             and observation.data.get('evidence_ids') == expected['evidence_ids']
             and observation.data.get('extracted') == {'attributes': {}, 'anomalies': []})
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            m.Event.idempotency_key == 'data:' + observation.id)))
        need(len(events) == 1)
        saved = events[0]
        need(event is None or event.id == saved.id)
        base = event_base(observation, expected)
        need(saved.kind == 'observation.data' and all(k in saved.data and same(saved.data[k], v) for k, v in base.items())
             and set(saved.data) <= set(base) | {'data_reception', 'delivery_error', 'previous_delivery_error', 'derivation_receipt'})
        issued = audits(db, p, ISSUED_ACTION, observation.id)
        marker = issued_anchor(observation, saved, expected)
        need(len(issued) == 1 and issued[0].actor_id == issuer(context) and issued[0].data == marker
             and observation.data.get(ISSUED) == marker)
        all_issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run.id)))
        need(len(all_issued) == 1 and all_issued[0].id == issued[0].id
             and len(observations(db, p, run.id)) == 1)
        return {**context, 'observation': observation, 'source': source, 'packet': expected,
                'event': saved, 'base': base, 'issued_audit': issued[0]}
    except HTTPException as error:
        if error.status_code == 403:
            raise
        need(False)
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        need(False)
