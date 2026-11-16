"""Explicit instance consumer for existing local state-projection Jobs.

Selection is not execution authority. Original source/ACK and current target
are checked before the original exact worker acquires its lease and rechecks
the Job actor. No ACK, model, DataGap, plan, device, or paid Job is dispatched.
The bounded page is not a proof of complete operational inventory or absence.
"""
import re
import signal
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, func, select, literal
from sqlalchemy.orm import Session

from . import models as m
from . import state_refresh as refresh
from .contracts import digest
from .db import engine
from .security import Principal

KIND = refresh.KIND
REQUESTED = 'object_state.refresh_requested'
REQUEST_KEYS = {'schema', 'entity_id', 'entity_version', 'instance_id', 'release_id',
    'object_definition_hash', 'authorized_by', 'reason', 'trigger_observation_id',
    'trigger_observation_version', 'due_state_hash'}
MANUAL_KEYS = {'job_id', 'entity_id', 'instance_id', 'request_id',
    'idempotency_key', 'state_request_hash', 'actor_id'}
ERROR = '原对象投影请求、实例或独立依据无法核对；原记录保留，未领取其他任务'
SOURCE_ERROR = '原来源或独立收件依据受阻；原登记与队列保留，未重放收件或处理原件'
STALE = '原对象、实例版本或输入版本已变化；原请求保留，请由当前获授权人员明确重新核对'
AUTH_ERROR = '原投影发起人已停用或失去执行权限；机构原数据保留，请由当前获授权人员提交新核对请求'
WAIT_ACK = '原处理结果尚未完成独立收件；投影任务继续排队，不重复模型或代办接收'
WAIT_PARENT = '原状态核对仍在保存完成记录；到期投影继续排队，不重复来源处理'
MAX_CHAIN = 32


def need(value, message=ERROR):
    if not value: raise HTTPException(409, message)


def _uuid(value):
    try: return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


def _sha(value): return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _scope(workspace_id, instance_id, entity_id=None, source_id=None, observation_id=None, job_id=None, limit=20):
    need(_uuid(workspace_id) and _uuid(instance_id) and type(limit) is int and 1 <= limit <= 50
        and all(value is None or _uuid(value) for value in (entity_id, source_id, observation_id, job_id)))


def _stopped(event): return event is not None and event.is_set()


def _actor(db, workspace_id, user_id):
    from .worker import actor, ActorRevoked
    try: return actor(db, workspace_id, user_id)
    except ActorRevoked: need(False, AUTH_ERROR)


def _manual_key(actor_id, request_id):
    need(_uuid(actor_id) and type(request_id) is str and re.fullmatch('[a-zA-Z0-9_-]{12,100}', request_id))
    return 'state-refresh:manual:' + actor_id + ':' + request_id


def _manual_anchor(job, instance_id, entity_id, request_id):
    return {'job_id': job.id, 'entity_id': entity_id, 'instance_id': instance_id,
        'request_id': request_id, 'idempotency_key': job.idempotency_key,
        'state_request_hash': job.data['state_request_hash'], 'actor_id': job.data['actor_id']}


def _manual_rows(db, workspace_id, key, job_id=None):
    parts = key.split(':', 3)
    need(len(parts) == 4 and parts[:2] == ['state-refresh', 'manual'])
    actor_id, nonce = parts[2:]
    query = select(m.Audit).where(m.Audit.workspace_id == workspace_id, m.Audit.action == REQUESTED,
        or_(m.Audit.data['idempotency_key'].as_string() == key,
            m.Audit.object_id == job_id if job_id is not None else False,
            and_(m.Audit.data['request_id'].as_string() == nonce,
                or_(m.Audit.actor_id == actor_id, m.Audit.data['actor_id'].as_string() == actor_id))))
    return list(db.scalars(query.limit(3)))


def _manual_proof(db, job, request):
    prefix = 'state-refresh:manual:' + job.data['actor_id'] + ':'
    need(job.idempotency_key.startswith(prefix))
    nonce = job.idempotency_key[len(prefix):]
    need(_manual_key(job.data['actor_id'], nonce) == job.idempotency_key)
    rows = _manual_rows(db, job.workspace_id, job.idempotency_key, job.id)
    expected = _manual_anchor(job, request['instance_id'], request['entity_id'], nonce)
    need(len(rows) == 1 and rows[0].object_id == job.id and rows[0].actor_id == job.data['actor_id']
        and type(rows[0].data) is dict and set(rows[0].data) == MANUAL_KEYS and rows[0].data == expected)
    need(request['reason'] == '用户请求本地重新核对' and request['trigger_observation_id'] is None
        and request['trigger_observation_version'] is None and request['due_state_hash'] is None)


def request_manual(db, p, instance_id, entity, request_id):
    """The normal POST's atomic future-only Job/Audit seam; never backfill.

    The caller retains its original active-instance/entity checks and commit.
    A surviving old Job without its independent request proof remains blocked.
    """
    key = _manual_key(p.user_id, request_id)
    actual = _actor(db, p.workspace_id, p.user_id)
    need(actual.role == p.role and _uuid(instance_id) and entity.workspace_id == p.workspace_id)
    refresh.lock(db, p.workspace_id, entity.id)
    prior = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    rows = _manual_rows(db, p.workspace_id, key, prior.id if prior is not None else None)
    if prior is None: need(not rows)
    else:
        request = refresh.checked(prior)
        need(prior.kind == KIND and request['instance_id'] == instance_id and request['entity_id'] == entity.id)
        _manual_proof(db, prior, request)
    job = refresh.enqueue(db, p, entity, '用户请求本地重新核对', 'manual:' + p.user_id + ':' + request_id)
    need(job is not None and job.kind == KIND and job.idempotency_key == key)
    request = refresh.checked(job)
    need(request['instance_id'] == instance_id and request['entity_id'] == entity.id)
    if prior is None:
        from .services import audit
        audit(db, p, REQUESTED, job.id, _manual_anchor(job, instance_id, entity.id, request_id))
        db.flush()
    _manual_proof(db, job, request)
    return job


@contextmanager
def _reader():
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            with Session(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode='rollback_only') as db:
                yield db
        finally:
            if transaction.is_active: transaction.rollback()


def _request(job):
    need(job is not None and job.kind == KIND and type(job.data) is dict)
    value = refresh.checked(job)
    need(set(value) == REQUEST_KEYS and value['schema'] == refresh.SCHEMA
        and all(_uuid(value[key]) for key in ('entity_id', 'instance_id', 'release_id', 'authorized_by'))
        and value['authorized_by'] == job.data.get('actor_id') and type(value['entity_version']) is int
        and value['entity_version'] >= 1 and _sha(value['object_definition_hash'])
        and type(value['reason']) is str and 1 <= len(value['reason']) <= 2000)
    return value


def _completed_proof(db, job):
    request = _request(job); result = job.data.get('state_result')
    need(type(result) is dict and result.get('schema') == refresh.SCHEMA
        and result.get('entity_id') == request['entity_id'] and result.get('instance_id') == request['instance_id']
        and _sha(result.get('state_hash')) and type(result.get('state_version')) is int
        and result['state_version'] >= 1 and result.get('cloud_calls') == 0 and result.get('device_actions') == 0
        and result.get('entity_updated') is False and result.get('physical_state_verified') is False)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == job.workspace_id,
        m.Audit.action == 'object_state.background_checked',
        m.Audit.data['job_id'].as_string() == job.id).limit(4)))
    # The unchanged handler can be recovered after its committed result but
    # before Job finalization. Each real attempt may retain its own Audit.
    need(1 <= len(proofs) <= min(3, job.attempts) and any(proof.data == {
        'job_id': job.id, 'state_version': result['state_version'], 'state_hash': result['state_hash']} for proof in proofs))
    need(all(proof.object_id == request['entity_id'] and proof.actor_id == job.data['actor_id']
        and set(proof.data) == {'job_id', 'state_version', 'state_hash'}
        and type(proof.data['state_version']) is int and proof.data['state_version'] >= 1
        and _sha(proof.data['state_hash']) for proof in proofs))
    return result


def _review_authority(db, job, observation):
    from .reported_form import verified_history
    history = verified_history(observation.data)
    review = observation.data.get('human_verification')
    need(type(review) is dict and history and len(history) <= 1000 and history[-1] == review
        and digest(history) == observation.data.get('verification_history_hash')
        and review.get('reviewed_by') == job.data['actor_id'], SOURCE_ERROR)
    fingerprint = digest({'extracted': observation.data['extracted'],
        'evidence_ids': observation.data.get('evidence_ids', []), 'invocation_id': observation.data['invocation_id']})
    need(review.get('input_hash') == fingerprint and review.get('decision') in {'confirmed', 'rejected'}, SOURCE_ERROR)
    expected = Counter()
    for item in history:
        need(_uuid(item.get('reviewed_by')) and item.get('decision') in {'confirmed', 'rejected'}
            and type(item.get('reason')) is str and type(item.get('reviewed_at')) is str, SOURCE_ERROR)
        stamp = datetime.fromisoformat(item['reviewed_at'])
        need(stamp.tzinfo is not None and stamp.utcoffset() is not None, SOURCE_ERROR)
        expected[(item['reviewed_by'], item['decision'], item['reason'])] += 1
    if review.get('request_id'):
        need(review.get('body_hash') == digest({'expected_version': observation.version - 1,
            'reason': review['reason'], 'decision': review['decision'], 'request_id': review['request_id'],
            'expected_input_hash': fingerprint}), SOURCE_ERROR)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == job.workspace_id,
        m.Audit.object_id == observation.id, m.Audit.action == 'observation.verified').limit(1001)))
    actual = Counter()
    for proof in proofs:
        need(type(proof.data) is dict and set(proof.data) == {'decision', 'reason'}
            and proof.created_at.tzinfo is not None and proof.created_at.utcoffset() is not None, SOURCE_ERROR)
        actual[(proof.actor_id, proof.data['decision'], proof.data['reason'])] += 1
    # The original API Audit has no version/hash field. Match its complete
    # multiplicity to the checked immutable review history, not just an older
    # same-reason row. Clocks are validated, never used to guess version order.
    need(actual == expected, SOURCE_ERROR)


def _observation(db, p, job, request):
    identity = request['trigger_observation_id']
    need(_uuid(identity) and type(request['trigger_observation_version']) is int
        and request['trigger_observation_version'] >= 1 and request['due_state_hash'] is None)
    observation = db.scalar(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        m.Observation.id == identity))
    need(observation is not None and observation.entity_id == request['entity_id'], SOURCE_ERROR)
    need(observation.version == request['trigger_observation_version'], STALE)
    expected = 'state-refresh:' + observation.id + ':' + str(request['trigger_observation_version']) + ':' + digest(request['reason'])[:12]
    need(job.idempotency_key == expected)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1 and events[0].kind == 'observation.data', SOURCE_ERROR)
    event = events[0]
    need(event.data.get('observation_id') == observation.id and event.data.get('entity_id') == observation.entity_id, SOURCE_ERROR)
    if request['reason'] == '人工核查结果已保存':
        _review_authority(db, job, observation)
    else:
        need(request['reason'] == '新登记处理结果已保存'
            and (event.data.get('actor_id') or observation.data.get('submitted_by')) == request['authorized_by'], SOURCE_ERROR)
    from . import instance_data_receipts as receipts
    from .state_receipt_guard_v245 import check as receipt_state
    if observation.status != 'processed': need(False, WAIT_ACK)
    admitted = receipt_state(db, p, observation)
    if admitted is not None and admitted['status'] == 'waiting_data':
        need(admitted.get('delivery_status') != 'blocked_delivery', SOURCE_ERROR)
        need(False, WAIT_ACK)
    need(event.status == 'received', SOURCE_ERROR)
    receipt = receipts.observation_receipt(db, p, observation)
    if not receipt.get('data_reception'):
        # Verified legacy releases retain their old contract, but this queue
        # still requires the actual independent Event and unique saved Memory.
        from .observation_integrity import checked_report
        checked_report(db, p, observation)
        from .simulation_observation_origin_v177 import guard
        simulation = guard(db, p, observation)
        receipts._event_matches(event, observation, simulation=simulation)
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)))
        need(len(memories) == 1 and memories[0].status == 'candidate'
            and memories[0].data.get('not_authoritative') is True, SOURCE_ERROR)
        saved_event = type('SavedEvent', (), {'workspace_id': memories[0].workspace_id, 'kind': event.kind,
            'idempotency_key': event.idempotency_key, 'data': memories[0].data})()
        receipts._event_matches(saved_event, observation, simulation=simulation)
    # Source is optional as a selector, never a fabricated grant. Native
    # receipt dispatch cold-checks the real packet/Task/SDK/release/ACK even
    # when no ordinary public Source can be used for selection.
    return observation


def _origin(db, p, job, request, seen=None):
    seen = set() if seen is None else seen
    need(job.id not in seen and len(seen) < MAX_CHAIN)
    seen.add(job.id)
    if request['trigger_observation_id'] is not None:
        return _observation(db, p, job, request)
    if job.idempotency_key.startswith('state-refresh:manual:'):
        _manual_proof(db, job, request); return None
    need(request['reason'] == '登记依据时效到期' and _sha(request['due_state_hash'])
        and request['trigger_observation_version'] is None)
    prefix = 'state-refresh:expiry:' + request['entity_id'] + ':' + request['due_state_hash'] + ':'
    need(job.idempotency_key.startswith(prefix))
    parent_id = job.idempotency_key[len(prefix):]
    need(_uuid(parent_id))
    parent = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == parent_id))
    parent_request = _request(parent); result = _completed_proof(db, parent)
    need(parent.data.get('next_expiry_job_id') == job.id
        and parent_request['entity_id'] == request['entity_id']
        and parent_request['instance_id'] == request['instance_id']
        and parent_request['authorized_by'] == request['authorized_by']
        and result['state_hash'] == request['due_state_hash'])
    stamp = datetime.fromisoformat(parent.data['next_expiry_at'])
    need(stamp.tzinfo is not None and stamp == job.available_at)
    if parent.status in {'queued', 'running'}: need(False, WAIT_PARENT)
    need(parent.status == 'succeeded')
    return _origin(db, p, parent, parent_request, seen)


def _prove(db, workspace_id, instance_id, identity, *, entity_id=None, source_id=None, observation_id=None):
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.id == identity))
    request = _request(job)
    need(request['instance_id'] == instance_id and (entity_id is None or request['entity_id'] == entity_id))
    p = _actor(db, workspace_id, request['authorized_by'])
    entity = db.scalar(select(m.Entity).where(m.Entity.workspace_id == workspace_id, m.Entity.id == request['entity_id']))
    instance = db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id == workspace_id, m.DomainInstance.id == instance_id))
    need(entity is not None and instance is not None and entity.namespace == instance.namespace)
    from .instance_api import active
    instance, release = active(db, p, instance_id)
    from .entity_release_compat import require_entity
    definition = require_entity(db, p, instance, release, entity, lock_sources=False)
    current = {'entity_id': entity.id, 'entity_version': entity.version, 'instance_id': instance.id,
        'release_id': release.id, 'object_definition_hash': digest(definition)}
    need(all(current[key] == request[key] for key in current), STALE)
    original_release = db.scalar(select(m.InstanceRelease).where(m.InstanceRelease.workspace_id == workspace_id,
        m.InstanceRelease.id == request['release_id']))
    need(original_release is not None and original_release.instance_id == instance_id
        and digest(original_release.data['snapshot']) == original_release.configuration_hash)
    origin = _origin(db, p, job, request)
    if observation_id is not None and (origin is None or origin.id != observation_id): return None
    if source_id is not None and (origin is None or origin.source_id != source_id): return None
    if job.data.get('state_result') is not None: _completed_proof(db, job)
    return job, origin


def _page(db, workspace_id, instance_id, entity_id, source_id, observation_id, job_id, after, limit):
    instance = db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id == workspace_id, m.DomainInstance.id == instance_id))
    need(instance is not None)
    entities = select(m.Entity.id).where(m.Entity.workspace_id == workspace_id, m.Entity.namespace == instance.namespace).correlate(None)
    if entity_id is not None: entities = entities.where(m.Entity.id == entity_id)
    observations = select(m.Observation.id).where(m.Observation.workspace_id == workspace_id,
        m.Observation.entity_id.in_(entities)).correlate(None)
    if source_id is not None: observations = observations.where(m.Observation.source_id == source_id)
    if observation_id is not None: observations = observations.where(m.Observation.id == observation_id)
    manual_query = select(m.Audit.id).where(m.Audit.workspace_id == workspace_id, m.Audit.action == REQUESTED,
        m.Audit.object_id == m.Job.id, m.Audit.data['instance_id'].as_string() == instance_id)
    if entity_id is not None: manual_query = manual_query.where(m.Audit.data['entity_id'].as_string() == entity_id)
    manual = manual_query.exists()
    original_source = select(m.Observation.id).where(m.Observation.id.in_(observations),
        m.Job.idempotency_key.like(literal('state-refresh:') + m.Observation.id + literal(':%'))).exists()
    expiry = select(m.Entity.id).where(m.Entity.id.in_(entities),
        m.Job.idempotency_key.like(literal('state-refresh:expiry:') + m.Entity.id + literal(':%'))).exists()
    scoped = or_(m.Job.data['state_request']['instance_id'].as_string() == instance_id,
        m.Job.data['state_request']['entity_id'].as_string().in_(entities), manual, original_source, expiry,
        m.Job.data['state_request']['trigger_observation_id'].as_string().in_(observations))
    query = select(m.Job.id, m.Job.available_at).where(m.Job.workspace_id == workspace_id,
        or_(m.Job.kind == KIND, manual, original_source, expiry), scoped,
        or_(m.Job.status == 'queued', and_(m.Job.status == 'running',
            or_(m.Job.lease_until.is_(None), m.Job.lease_until < func.clock_timestamp()))),
        m.Job.available_at <= func.clock_timestamp())
    if entity_id is not None: query = query.where(or_(
        m.Job.data['state_request']['entity_id'].as_string() == entity_id, manual, original_source, expiry))
    if source_id is not None or observation_id is not None:
        query = query.where(or_(m.Job.data['state_request']['trigger_observation_id'].as_string().in_(observations),
            original_source, expiry))
    # Expiry roots are followed by _origin. An explicit source/Observation
    # filter does not turn a manual no-source command into a source command.
    if job_id is not None: query = query.where(m.Job.id == job_id)
    narrowed = query.where(or_(m.Job.available_at > after[0],
        and_(m.Job.available_at == after[0], m.Job.id > after[1]))) if after else query
    rows = list(db.execute(narrowed.order_by(m.Job.available_at, m.Job.id).limit(limit)))
    if not rows and after: rows = list(db.execute(query.order_by(m.Job.available_at, m.Job.id).limit(limit)))
    return rows


def _orphan_manual(db, workspace_id, instance_id, entity_id, job_id, limit):
    surviving = select(m.Job.id).where(m.Job.workspace_id == workspace_id, m.Job.id == m.Audit.object_id).exists()
    query = select(m.Audit).where(m.Audit.workspace_id == workspace_id, m.Audit.action == REQUESTED,
        m.Audit.data['instance_id'].as_string() == instance_id, ~surviving)
    if entity_id is not None: query = query.where(m.Audit.data['entity_id'].as_string() == entity_id)
    if job_id is not None: query = query.where(m.Audit.object_id == job_id)
    return list(db.scalars(query.order_by(m.Audit.id).limit(limit)))


def step(workspace_id, instance_id, *, entity_id=None, source_id=None, observation_id=None,
        job_id=None, after=None, limit=20, stop_event=None):
    _scope(workspace_id, instance_id, entity_id, source_id, observation_id, job_id, limit)
    value = {'processed': False, 'after': after, 'waiting': [], 'errors': [], 'job_id': None}
    if _stopped(stop_event): return value
    with Session(engine()) as db:
        rows = _page(db, workspace_id, instance_id, entity_id, source_id, observation_id, job_id, after, limit)
        if source_id is None and observation_id is None:
            value['errors'] += [{'job_id': proof.object_id, 'reason': ERROR} for proof in
                _orphan_manual(db, workspace_id, instance_id, entity_id, job_id, limit)]
    for identity, available in rows:
        if _stopped(stop_event): break
        value['after'] = (available, identity)
        try:
            with _reader() as db:
                selected = _prove(db, workspace_id, instance_id, identity, entity_id=entity_id,
                    source_id=source_id, observation_id=observation_id)
                if selected is None: continue
            if _stopped(stop_event): break
            from .worker import process_once
            if process_once(job_id=identity, kinds=[KIND], deliver=False, workspace_id=workspace_id, _stop_event=stop_event):
                value['processed'] = True; value['job_id'] = identity; break
        except HTTPException as error:
            reason = error.detail if error.detail in {ERROR, SOURCE_ERROR, STALE, AUTH_ERROR, WAIT_ACK, WAIT_PARENT} else SOURCE_ERROR
            state = ('waiting_data' if reason == WAIT_ACK else 'waiting_parent' if reason == WAIT_PARENT
                else 'blocked_authorization' if reason == AUTH_ERROR else 'blocked_integrity')
            value['waiting'].append({'job_id': identity, 'state': state, 'reason': reason})
        except (KeyError, ValueError, TypeError, AttributeError, OSError, RecursionError):
            value['errors'].append({'job_id': identity, 'reason': ERROR})
    if not rows: value['after'] = None
    return value


def run(workspace_id, instance_id, *, entity_id=None, source_id=None, observation_id=None, job_id=None, stop_event=None):
    _scope(workspace_id, instance_id, entity_id, source_id, observation_id, job_id)
    stop = stop_event if stop_event is not None else threading.Event()
    need(threading.current_thread() is threading.main_thread() or stop_event is not None)
    previous = {}; after = None
    def request_stop(_signal, _frame): stop.set()
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM): previous[signum] = signal.signal(signum, request_stop)
    print('指定实例的本地对象登记状态核对已启动；不处理模型、设备、付费或业务计划任务。', flush=True)
    try:
        while not stop.is_set():
            try:
                result = step(workspace_id, instance_id, entity_id=entity_id, source_id=source_id,
                    observation_id=observation_id, job_id=job_id, after=after, stop_event=stop)
                after = result['after']
                for item in result['waiting'] + result['errors']: print(item['reason'], flush=True)
                if not result['processed']: stop.wait(2)
            except Exception:
                print(ERROR, flush=True); stop.wait(2)
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)
        print('对象登记状态核对已停止；原请求、来源和已保存结果保留。', flush=True)
    return 0
