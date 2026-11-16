"""Registration-visible-at-first-input eligibility; no clock or back-signing.

Only the real V138 adapter's newly created generation-one input can issue this
independent Audit. Old/retried Pairs and receipt replays never enter the writer.
"""
from sqlalchemy import select, or_, and_

from . import models as m
from .contracts import digest
from .graph_event_models import GraphEventProducer
from .graph_event_source_families_v136 import (
    NATIVE_LEARNED_GEO, check_native_producer, record_family,
    verify_native_registration, verify_contract_current,
)

ACTION = 'graph_event.native_learned_geo_eligible_v141'
REGISTRATION = 'graph_event.native_learned_geo_producer_registered_v141'
SCHEMA = 'graph-event-native-learned-geo-eligibility-v1'
KEYS = {'schema', 'workspace_id', 'instance_id', 'producer_id', 'processing_actor_id',
    'registration_audit_id', 'registration_hash', 'command_hash', 'contract_hash',
    'pair_id', 'task_id', 'job_id', 'capability_id', 'capability_hash', 'input_audit_id', 'input_hash'}


def need(value):
    if not value: raise ValueError('native_learned_geo_registration_eligibility')


def _registration(db, p, producer):
    verify_native_registration(db, p, producer)
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == producer.id, m.Audit.action == REGISTRATION).limit(2)))
    need(len(rows) == 1)
    return rows[0]


def _value(p, producer, registration, pair, task, first):
    return {'schema': SCHEMA, 'workspace_id': p.workspace_id,
        'instance_id': task.data['instance_id'], 'producer_id': producer.id,
        'processing_actor_id': first.data['actor_id'],
        'registration_audit_id': registration.id, 'registration_hash': digest(registration.data),
        'command_hash': producer.command_hash, 'contract_hash': producer.contract_hash,
        'pair_id': pair.id, 'task_id': task.id, 'job_id': first.data['job_id'],
        'capability_id': first.data['capability_id'], 'capability_hash': first.data['capability_hash'],
        'input_audit_id': first.id, 'input_hash': digest(first.data)}


def inventory(db, p, pair_id):
    from .graph_event_admission import _uuid, _hash
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.object_id == pair_id).limit(201)))
    need(len(rows) <= 200)
    producers = set()
    for row in rows:
        value = row.data
        need(type(value) is dict and set(value) == KEYS and value['schema'] == SCHEMA
            and value['workspace_id'] == row.workspace_id == p.workspace_id and value['pair_id'] == pair_id
            and row.actor_id == value['processing_actor_id'] and value['producer_id'] not in producers)
        need(all(_uuid(value[key]) for key in ('workspace_id', 'instance_id', 'producer_id',
            'processing_actor_id', 'registration_audit_id', 'pair_id', 'task_id', 'job_id', 'capability_id', 'input_audit_id'))
            and all(_hash(value[key]) for key in ('registration_hash', 'command_hash', 'contract_hash', 'capability_hash', 'input_hash')))
        producers.add(value['producer_id'])
    return rows


def has_eligibility(db, p, producer, pair_id):
    return any(row.data['producer_id'] == producer.id for row in inventory(db, p, pair_id))


def record_eligibility(db, p, pair, task, job, capability):
    """Private adapter hook, after the genuine first Input Audit was flushed."""
    from . import native_learned_geo_pair_provenance_v138 as origin
    from .leases import ownership
    from .security import require
    require(p, 'admin', 'operator')
    lease = ownership(db)
    first = origin.input_audits(db, p, pair.id)
    need(pair.data['generation'] == 1 and pair.status == 'queued' and len(first) == 1
        and first[0].data['generation'] == 1 and first[0].data['task_id'] == task.id
        and first[0].data['job_id'] == job.id and first[0].actor_id == p.user_id == job.data['actor_id']
        and first[0].data['capability_id'] == capability.id
        and job.status == 'running' and lease.get('job_id') == job.id
        and lease.get('lease_token') == job.lease_token and not inventory(db, p, pair.id))
    registered = select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == REGISTRATION, m.Audit.data['instance_id'].as_string() == task.data['instance_id'])
    rows = list(db.scalars(select(GraphEventProducer).where(
        GraphEventProducer.workspace_id == p.workspace_id, GraphEventProducer.status == 'active',
        or_(and_(GraphEventProducer.data['contract']['instance_id'].as_string() == task.data['instance_id'],
                GraphEventProducer.data['contract']['capability_id'].as_string() == capability.id),
            and_(GraphEventProducer.data['registration']['command']['instance_id'].as_string() == task.data['instance_id'],
                GraphEventProducer.data['registration']['command']['capability_id'].as_string() == capability.id),
            GraphEventProducer.id.in_(registered)))
        .order_by(GraphEventProducer.id).limit(201).with_for_update(read=True)
        .execution_options(populate_existing=True)))
    need(len(rows) <= 200)
    for producer in rows:
        from .graph_event_admission import check_producer
        check_producer(producer)
        if record_family(producer) != NATIVE_LEARNED_GEO:
            need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action == REGISTRATION, m.Audit.object_id == producer.id).limit(1)) is None)
            continue
        contract = check_native_producer(producer)
        registration = _registration(db, p, producer)
        if contract['instance_id'] != task.data['instance_id'] or contract['capability_id'] != capability.id:
            continue
        verify_contract_current(db, p, contract, producer=producer, lock=True)
        need(contract['capability_id'] == capability.id and contract['capability_hash'] == capability.content_hash)
        db.add(m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=ACTION, object_id=pair.id,
            data=_value(p, producer, registration, pair, task, first[0])))
    db.flush()


def eligibility_proof(db, p, producer, context):
    from . import native_learned_geo_pair_provenance_v138 as origin
    need(record_family(producer) == NATIVE_LEARNED_GEO)
    contract = check_native_producer(producer)
    pair, task = context['pair'], context['task']
    first = [row for row in origin.input_audits(db, p, pair.id) if row.data.get('generation') == 1]
    need(len(first) == 1 and first[0].data['task_id'] == task.id
        and first[0].data['instance_id'] == contract['instance_id'] == task.data['instance_id']
        and first[0].data['capability_id'] == contract['capability_id'] == context['capability'].id
        and first[0].data['capability_hash'] == contract['capability_hash'])
    actual = [row for row in inventory(db, p, pair.id) if row.data['producer_id'] == producer.id]
    need(len(actual) == 1)
    registration = _registration(db, p, producer)
    need(digest(actual[0].data) == digest(_value(p, producer, registration, pair, task, first[0])))
    return {'audit': actual[0], 'registration_audit': registration}
