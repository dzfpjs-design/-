"""Workspace-scoped observation execution, daily inventory and saved-fact ACK.

The roots perform database work only. They never extract again, reset blocked
delivery or authorize a device action. A daily inventory pins saved business
days; it is neither sensor-clock verification nor a physical-state assertion.
"""
import copy
from datetime import date, datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .contracts import digest
from .db import engine
from .models import Audit, Capability, Entity, Event, Membership, Observation, User, uid
from .security import Principal
from .services import audit, published, scoped

SCHEMA = 'workspace-observation-daily-v204'
DAILY_AUDIT = 'observation.daily_frozen_v204'
EXECUTION = 'workspace-observation-execution-v204'
EXECUTION_KEY = 'workspace_observation_execution_v204'
EXECUTION_AUDIT = 'observation.execution_pinned_v204'
ATTEMPTS_KEY = 'workspace_observation_attempts_v204'
ATTEMPT_AUDIT = 'observation.execution_attempt_v204'
PAGE = 64
# Each receiver selects at most 100 original events. Unknown graph-only kinds
# cannot permanently occupy the first page ahead of ordinary observation ACKs.
ACK_KINDS = ('observation.data', 'observation.failed', 'perception.anomaly',
    'perception.candidate', 'perception.daily', 'data.gap',
    'data.gap_coordination', 'perception.acquisition_escalation')
ERROR = '原普通观测、工作区、来源或提取批准无法核对；未开始新的提取'


def need(ok, message=ERROR):
    if not ok:
        raise HTTPException(409, message)


def workspace(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('观测归集必须提供标准小写工作区 UUID') from None
    return value


def stopped(event):
    return event is not None and event.is_set()


def _hash(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def guard_execution(db, p, job, observation, *, first=False):
    """Opt-in for the new scoped ordinary Job, never an historical ACK gate.

    Old queued requests have no ingestion-time Source hash. Their first actual
    execution freezes the currently authorized Source; later attempts cannot
    replace that pin. Capability locks are released by the existing gateway's
    reservation commit, and the entire pin is checked again before publication.
    """
    need(job.kind == 'process_observation' and job.workspace_id == observation.workspace_id == p.workspace_id
        and job.idempotency_key == 'extract:' + observation.id
        and job.data.get('observation_id') == observation.id
        and job.data.get('actor_id') == p.user_id
        and type(observation.data.get('submitted_by')) is str
        and type(job.attempts) is int and 1 <= job.attempts <= 3
        and job.data.get('extractor_id') == observation.data.get('extractor_id'))
    allowed = db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin', 'operator'])))
    need(allowed is not None)
    # No Task/Policy/action locks are acquired by this ordinary receipt guard.
    source = scoped(db, Capability, observation.source_id, p, True)
    source = published(db, p, source.id, 'source')
    from .observation_inputs import original
    original(db, p, observation)
    scoped(db, Entity, observation.entity_id, p)
    extractor = scoped(db, Capability, job.data['extractor_id'], p, True)
    extractor = published(db, p, extractor.id)
    need(extractor.kind in {'tool', 'specialist_model'}
        and 'perception' in extractor.data.get('allowed_agent_roles', []))
    saved = job.data.get(EXECUTION_KEY)
    need(saved is None or type(saved) is dict)
    pin = {'schema': EXECUTION, 'workspace_id': p.workspace_id,
        'source_pin_basis': 'first_scoped_execution_current_definition',
        'job_id': job.id, 'observation_id': observation.id,
        'original_submitter_id': observation.data['submitted_by'],
        'initial_actor_id': saved.get('initial_actor_id') if saved is not None else p.user_id,
        'initial_attempt': saved.get('initial_attempt') if saved is not None else job.attempts,
        'source_id': source.id, 'source_hash': source.content_hash,
        'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
        'input_hash': observation.data['input_hash']}
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == EXECUTION_AUDIT)))
    attempts = job.data.get(ATTEMPTS_KEY, [])
    need(type(attempts) is list and len(attempts) <= 3)
    attempt_events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == job.id, Audit.action == ATTEMPT_AUDIT)))
    need(len(attempt_events) == len(attempts))
    previous_attempt = 0
    for recorded in attempts:
        need(type(recorded) is dict and set(recorded) == {'attempt', 'actor_id', 'source_pin_hash', 'retry_audit_id'}
            and type(recorded['attempt']) is int and previous_attempt < recorded['attempt'] <= job.attempts
            and recorded['source_pin_hash'] == digest(pin))
        matched = [event for event in attempt_events if event.data == recorded and event.actor_id == recorded['actor_id']]
        need(len(matched) == 1)
        previous_attempt = recorded['attempt']
    retry_audit_id = None
    existing_attempt = next((record for record in attempts if record['attempt'] == job.attempts), None)
    if existing_attempt is not None:
        need(existing_attempt['actor_id'] == p.user_id)
    else:
        need(first)
        previous_actor = attempts[-1]['actor_id'] if attempts else observation.data['submitted_by']
        if previous_actor != p.user_id:
            retries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                Audit.object_id == observation.id, Audit.action == 'observation.retry',
                Audit.actor_id == p.user_id, Audit.data['next_attempt'].as_integer() == job.attempts)))
            need(len(retries) == 1, '原观测执行人已变化但没有本次明确重试依据；未消耗新的提取调用')
            retry_audit_id = retries[0].id
    if saved is None:
        need(first and not events and not attempts)
        job.data = {**job.data, EXECUTION_KEY: copy.deepcopy(pin)}
        audit(db, p, EXECUTION_AUDIT, job.id, {'pin': pin, 'pin_hash': digest(pin)})
        db.flush()
    else:
        need(saved == pin and len(events) == 1 and events[0].actor_id == pin['initial_actor_id']
            and events[0].data == {'pin': pin, 'pin_hash': digest(pin)})
    if existing_attempt is None:
        record = {'attempt': job.attempts, 'actor_id': p.user_id,
            'source_pin_hash': digest(pin), 'retry_audit_id': retry_audit_id}
        job.data = {**job.data, ATTEMPTS_KEY: [*attempts, record]}
        audit(db, p, ATTEMPT_AUDIT, job.id, record)
        db.flush()
    return pin


def daily_value(source, day, rows, at, receipts=None):
    """Freeze this saved day, including every non-success stage and late row."""
    need(type(day) is str and date.fromisoformat(day).isoformat() == day,
        '原观测业务日期无法核对；未改用今天替代')
    need(isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None)
    specification = source.data.get('specification', {})
    zone = specification.get('timezone', 'UTC')
    limits = []
    coherent = source.kind == 'source' and source.content_hash == source.validated_hash == digest(source.data)
    if not coherent:
        limits.append('source_definition_requires_review')
    try:
        today = at.astimezone(ZoneInfo(zone)).date()
        provisional = date.fromisoformat(day) >= today
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        provisional = True
        limits.append('source_timezone_requires_review')
    records = []
    producer_actor_id = None
    receipts = receipts or {}
    for row in sorted(rows, key=lambda value: value.id):
        need(row.workspace_id == source.workspace_id and row.source_id == source.id
            and row.business_day == day and not row.data.get('candidate_only'))
        if producer_actor_id is None:
            producer_actor_id = row.data.get('submitted_by')
        receipt = receipts.get((row.id, 'observation.data' if row.status == 'processed' else 'observation.failed'))
        records.append({'observation_id': row.id, 'version': row.version,
            'submitted_by': row.data.get('submitted_by'),
            'status': row.status, 'input_hash': row.data.get('input_hash'),
            'ack_event_id': receipt.id if receipt else None,
            'ack_status': receipt.status if receipt else None,
            'data_received': bool(row.status == 'processed' and receipt and receipt.status == 'received')})
    records.sort(key=lambda row: row['observation_id'])
    processed = [row['observation_id'] for row in records if row['status'] == 'processed']
    failed = [row['observation_id'] for row in records if row['status'] == 'failed']
    pending = [row['observation_id'] for row in records if row['status'] not in {'processed', 'failed'}]
    unreceived = [row['observation_id'] for row in records if row['status'] == 'processed' and not row['data_received']]
    value = {'schema': SCHEMA, 'source_id': source.id, 'business_day': day,
        'producer_actor_id': producer_actor_id, 'producer_actor_basis': 'first_saved_submitter_internal_inventory_only',
        'timezone': zone, 'date_basis': 'saved_observation_business_day',
        'source_hash': source.content_hash, 'source_integrity_verified': coherent,
        'source_definition_basis': 'current_inventory_metadata_only',
        'source_clock_verified': False, 'physical_state_verified': False,
        'simulation': bool(specification.get('simulation_environment_id')
            or source.data.get('data_policy', {}).get('simulation')),
        'not_authoritative': True, 'provisional': provisional, 'limits': limits,
        'records': records, 'processed_ids': processed, 'failed_ids': failed, 'pending_ids': pending,
        'unreceived_ids': unreceived,
        'status': 'incomplete' if provisional or limits or failed or pending or unreceived else 'complete'}
    value['revision_hash'] = digest(value)
    return value


def _daily_key(value):
    return 'workspace-observation-daily:' + value['source_id'] + ':' + value['business_day'] + ':' + value['revision_hash']


def _anchor(event):
    return {'schema': SCHEMA, 'event_id': event.id, 'event_hash': digest(event.data)}


def validate_daily(db, event):
    """An immutable producer audit authenticates old revisions without rebasing."""
    value = event.data
    need(type(value) is dict and value.get('schema') == SCHEMA and event.kind == 'perception.daily'
        and _hash(value.get('revision_hash'))
        and value['revision_hash'] == digest({k: v for k, v in value.items() if k != 'revision_hash'})
        and event.idempotency_key == _daily_key(value), '每日登记整理的原冻结修订无法核对')
    records = list(db.scalars(select(Audit).where(Audit.workspace_id == event.workspace_id,
        Audit.object_id == event.id, Audit.action == DAILY_AUDIT)))
    need(len(records) == 1 and records[0].actor_id == value.get('producer_actor_id')
        and records[0].data == _anchor(event), '每日登记整理的独立冻结依据无法核对')
    return copy.deepcopy(value)


def _groups(workspace_id, after):
    query = select(Observation.source_id, Observation.business_day).join(Capability,
        (Capability.id == Observation.source_id) & (Capability.workspace_id == Observation.workspace_id)).where(
        Observation.workspace_id == workspace_id, Capability.kind == 'source',
        Capability.namespace != 'platform_mqtt',
        Capability.data['specification']['mqtt_binding_id'].as_string().is_(None),
        Observation.business_day.is_not(None),
        Observation.data['candidate_only'].as_boolean().is_distinct_from(True))
    if after is not None:
        query = query.where(tuple_(Observation.source_id, Observation.business_day) > after)
    with Session(engine()) as db:
        return list(db.execute(query.distinct().order_by(Observation.source_id, Observation.business_day).limit(PAGE)))


def reconcile_daily(workspace_id, *, _stop_event=None):
    """Keyset batches cover actual saved days, including arrivals beyond 7 days.

    The short advisory lock belongs only to one workspace/source/day inventory;
    no model, receiver Session or action runs while it is held.
    """
    workspace_id = workspace(workspace_id)
    after = None
    created = 0
    while not stopped(_stop_event):
        groups = _groups(workspace_id, after)
        if not groups:
            break
        for source_id, day in groups:
            if stopped(_stop_event):
                return created
            after = (source_id, day)
            with Session(engine()) as db:
                key = int(digest({'workspace_observation_daily': workspace_id,
                    'source_id': source_id, 'business_day': day})[:15], 16)
                locked = db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key})
                if not locked:
                    continue  # Next root resumes this group; never wait across stop.
                if stopped(_stop_event):
                    db.rollback()
                    return created
                # This viewer only reads the scoped inventory. The frozen
                # audit below uses an actual saved submitter (Audit has a User
                # FK), never an invented system account or renewed authority.
                p = Principal('workspace-observation-intake', workspace_id, 'viewer', 'observation-daily')
                source = scoped(db, Capability, source_id, p)
                rows = list(db.scalars(select(Observation).where(Observation.workspace_id == workspace_id,
                    Observation.source_id == source_id, Observation.business_day == day,
                    Observation.data['candidate_only'].as_boolean().is_distinct_from(True)).order_by(Observation.id)))
                receipts = {}
                identities = [row.id for row in rows]
                for offset in range(0, len(identities), PAGE):
                    events = db.scalars(select(Event).where(Event.workspace_id == workspace_id,
                        Event.kind.in_(['observation.data', 'observation.failed']),
                        Event.data['observation_id'].as_string().in_(identities[offset:offset + PAGE]))
                        .order_by(Event.created_at, Event.id))
                    for event in events:
                        receipts[(event.data['observation_id'], event.kind)] = event
                value = daily_value(source, day, rows, db.scalar(select(func.clock_timestamp())), receipts)
                need(db.scalar(select(User.id).where(User.id == value['producer_actor_id'])) is not None,
                    '原观测提交人无法核对；未冒用其他账户冻结每日整理')
                identity = db.scalar(insert(Event).values(id=uid(), workspace_id=workspace_id,
                    kind='perception.daily', idempotency_key=_daily_key(value), data=value)
                    .on_conflict_do_nothing(index_elements=['workspace_id', 'idempotency_key']).returning(Event.id))
                if identity is not None:
                    event = db.get(Event, identity)
                    producer = Principal(value['producer_actor_id'], workspace_id, 'viewer', 'observation-daily-inventory')
                    audit(db, producer, DAILY_AUDIT, event.id, _anchor(event))
                if stopped(_stop_event):
                    db.rollback()
                    return created
                db.commit()
                created += int(identity is not None)
        if len(groups) < PAGE:
            break
    return created


def deliver_pending(workspace_id, *, _stop_event=None):
    """Receive saved facts even with no live local/native/MQTT group present."""
    workspace_id = workspace(workspace_id)
    from .worker import deliver_events
    for kind in ACK_KINDS:
        if stopped(_stop_event):
            break
        # The receiver owns all source/provenance/paused-instance derivation
        # gates. A blocked original stays blocked; no status reset occurs here.
        deliver_events(workspace_id=workspace_id, event_kind=kind)
