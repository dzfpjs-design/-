"""Explicit instance/family consumer for ordinary, genuinely created Tasks.

Selection is not dispatch authority. The unchanged exact-ID worker owns the
lease, fresh permissions, budgets, registered adapter and side-effect gates.
Receipt readiness delays only an unissued fixed status Job. Successful SDK
snapshots are immutable; empty registered scope is not expected-source truth.
Each page is bounded and is not a complete operational inventory/none proof.
"""
import signal
import threading
from contextlib import contextmanager
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.orm import Session

from . import models as m
from .contracts import digest
from .db import engine
from .security import Principal

NATIVE_GOAL_KIND = 'check_native_business_goal'
KINDS = {'compact': ('execute_workflow_step',), 'graph': (
    'execute_full_semantic_graph_node', 'advance_full_semantic_graph',
    'receive_full_semantic_graph_event', 'drain_full_semantic_graph_admissions', NATIVE_GOAL_KIND)}
ERROR = '原工作流任务、实例或后台执行归属无法核对；未领取或调用能力'
ACK_ERROR = '原成果或独立收件依据受阻，原队列与记录保留；请核对数据交付，未重新处理原成果'
ACK_WAIT = '原成果已保存，等待数据管理独立接收；后台任务继续排队，不重复运行模型'
PRODUCER_WAIT = '上一步仍在保存完成记录，后继任务继续排队；原成果保留'
MAX_RECORDS = 1000


def need(value, message=ERROR):
    if not value: raise HTTPException(409, message)


def _uuid(value):
    try: return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


def _scope(workspace_id, instance_id, family, task_id=None, limit=20):
    need(_uuid(workspace_id) and _uuid(instance_id) and type(family) is str and family in KINDS
        and (task_id is None or _uuid(task_id)) and type(limit) is int and 1 <= limit <= 50)


def _stopped(event): return event is not None and event.is_set()


def _principal(db, workspace_id, actor_id):
    row = db.execute(select(m.User, m.Membership).join(m.Membership,
        m.Membership.user_id == m.User.id).where(m.User.id == actor_id,
            m.Membership.workspace_id == workspace_id)).first()
    need(row is not None and row[0].active and row[1].role in {'admin', 'operator'},
        '原执行账号或权限已变化，未领取或调用能力；请核对授权')
    return Principal(actor_id, workspace_id, row[1].role, 'workflow-task-consumer-v265')


def _created(db, workspace_id, instance_id, task, family):
    need(task is not None and task.workspace_id == workspace_id
        and task.data.get('instance_id') == instance_id)
    release = db.scalar(select(m.InstanceRelease).where(m.InstanceRelease.workspace_id == workspace_id,
        m.InstanceRelease.id == task.data.get('instance_release_id')))
    need(release is not None and release.instance_id == instance_id and release.package_id == task.package_id
        and digest(release.data['snapshot']) == release.configuration_hash)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == workspace_id,
        m.Audit.action == 'workflow.created', m.Audit.object_id == task.id).limit(2)))
    need(len(proofs) == 1 and proofs[0].actor_id == task.owner_id
        and proofs[0].data == {'release': release.id, 'business': task.data['business_id']})
    business = next((b for b in release.data['snapshot']['businesses'] if b['id'] == task.data['business_id']), None)
    need(business is not None)
    actual = 'graph' if business['execution_mode'] == 'confirmed_graph_workflow' else 'compact'
    need(family is None or actual == family)
    return release, business


def _native_goal(db, p, task, release, business, job):
    """A real native Goal queue entry, not a graph activation or SDK retry.

    saved_context checks the immutable Goal/objects against the actual Task
    release. The existing queue/time-trigger checker applies to bootstrap and
    evaluation Jobs alike; an evaluation ref is not required for initial Q0.
    The owned worker still checks live permissions, its lease and producer
    barrier before running the unchanged native Goal handler.
    """
    from .native_goals import saved_context
    from .goal_evaluation_provider_v288 import _goal_job
    from .services import published
    need(job.kind == NATIVE_GOAL_KIND and job.workspace_id == p.workspace_id == task.workspace_id
        and job.status in {'queued', 'running'} and job.data['task_id'] == task.id
        and job.data['actor_id'] == p.user_id
        and business['execution_mode'] == 'confirmed_graph_workflow'
        and release.id == task.data['instance_release_id'] and release.instance_id == task.data['instance_id']
        and release.package_id == task.package_id)
    published(db, p, task.package_id, 'domain_package')
    context = saved_context(db, p, task)
    need(context == task.data['native_goal_context'] and digest(context) == task.data['native_goal_context_hash']
        and job.data['context_hash'] == digest(context))
    _goal_job(task, job)
    from .goal_evaluation_maturity_v299 import candidate_scope
    candidate_scope(db,p,task,job)


def _prove(db, workspace_id, instance_id, family, job_id, task_id=None):
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.id == job_id))
    need(job is not None and job.kind in KINDS[family] and type(job.data) is dict)
    task = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id,
        m.Task.id == job.data.get('task_id')))
    need(task is not None and (task_id is None or task.id == task_id))
    release, business = _created(db, workspace_id, instance_id, task, family)
    p = _principal(db, workspace_id, job.data['actor_id'])
    from .workspace_api import saved_execution
    actual, checked_release, checked_business = saved_execution(db, p, task.id)
    need(actual.id == task.id and checked_release.id == release.id and checked_business['id'] == business['id'])
    if family == 'compact':
        scope = job.data['scope']; runtime = task.data['workflow_runtime']; auto = runtime.get('automatic', {})
        need(type(scope) is dict and type(scope.get('visit')) is int and scope['visit'] >= 0
            and type(scope.get('attempt')) is int and scope['attempt'] >= 1
            and digest(scope['inputs']) == scope['input_hash']
            and job.idempotency_key == job.data['invocation_key'] == 'workflow:' + task.id + ':'
                + str(scope['visit']) + ':' + str(scope['attempt']))
        queued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == workspace_id,
            m.Audit.action == 'workflow.ability_queued', m.Audit.object_id == task.id,
            m.Audit.data['job'].as_string() == job.id).limit(2)))
        need(len(queued) == 1 and queued[0].actor_id == p.user_id
            and queued[0].data == {'step': scope['step_id'], 'job': job.id, 'attempt': scope['attempt']})
        if scope['visit'] < len(runtime['receipts']):
            from .workflow_job_dependencies_v264 import parent_pin
            parent_pin(db, p, task, job.id)  # A handler-complete producer still needs its real final commit.
        else:
            need(scope['visit'] == len(runtime['receipts']) and auto.get('job_id') == job.id
                and all(auto.get(k) == v for k, v in scope.items()) and auto.get('actor_id') == p.user_id
                and runtime['current_step_id'] == scope['step_id'])
    else:
        from . import workflow_graph as graph
        state = task.data['graph_runtime']; value = graph.graph_for(release, business)
        graph.invoke_pure(graph.graph_api.check_run, value, state)
        if job.kind == NATIVE_GOAL_KIND:
            _native_goal(db, p, task, release, business, job)
            return p, task, release, business, job
        need(job.data['graph_hash'] == state['graph_hash'])
        if job.kind == 'execute_full_semantic_graph_node':
            from .workflow_graph_scope import _job_matches
            activation = state['activations'][job.data['activation_id']]; node = graph.node_for(value, activation)
            _job_matches(task, job, state, activation, node)
            need(node['actor_id'] == p.user_id)
        elif job.kind == 'advance_full_semantic_graph':
            activation = state['activations'][job.data['activation_id']]
            key = job.data['activation_id'] + ':' + str(job.data['cancel_epoch'])
            need(task.data['graph_timers'].get(key) == job.id and job.data['deadline'] == activation['deadline']
                and job.idempotency_key == 'graph-timer:' + task.id + ':' + key)
        elif job.kind == 'receive_full_semantic_graph_event':
            event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id, m.Event.id == job.data['event_id']))
            need(event is not None and digest(event.data) == job.data['event_hash']
                and job.idempotency_key == 'graph-event:' + task.id + ':' + event.id
                and event.kind in {s['event_type'] for s in value['subscriptions']})
        else:
            from . import workflow_graph_waits as waits
            binding = graph.business_binding(release, business); authority = waits.checked(task, binding)
            need(authority is not None and task.data['graph_wait_poll']['job_id'] == job.id
                and job.data['authority_hash'] == authority['authority_hash']
                and job.data['binding_hash'] == binding['binding_hash'])
    return p, task, release, business, job


def _task_query(workspace_id, instance_id, task_id=None):
    query = select(m.Task.id).where(m.Task.workspace_id == workspace_id,
        m.Task.data['instance_id'].as_string() == instance_id)
    if task_id is not None: query = query.where(m.Task.id == task_id)
    return query


def _page(db, workspace_id, instance_id, family, task_id, after, limit):
    tasks = _task_query(workspace_id, instance_id, task_id).correlate(None)
    # Reverse hints discover damaged forward pointers. They are restrictions,
    # never proof: every selected row must still pass _prove before claim.
    from .workflow_job_dependencies_v264 import BOUND
    from .workflow_provenance_wait_v197 import PHASE
    reverse = select(m.Audit.id).where(m.Audit.workspace_id == workspace_id,
        or_(and_(m.Audit.action == 'workflow.ability_queued', m.Audit.object_id.in_(tasks),
                m.Audit.data['job'].as_string() == m.Job.id),
            and_(m.Audit.action == BOUND, m.Audit.object_id == m.Job.id,
                m.Audit.data['task_id'].as_string().in_(tasks)))).exists()
    base = select(m.Job.id, m.Job.available_at).where(m.Job.workspace_id == workspace_id,
        m.Job.kind.in_(KINDS[family]), m.Job.available_at <= func.clock_timestamp(),
        or_(m.Job.data['task_id'].as_string().in_(tasks), reverse),
        or_(m.Job.status.in_(['queued', 'waiting_service']),
            and_(m.Job.kind == 'execute_full_semantic_graph_node', m.Job.status == PHASE),
            and_(m.Job.status == 'running',
            or_(m.Job.lease_until.is_(None), m.Job.lease_until < func.clock_timestamp()))))
    query = base.where(or_(m.Job.available_at > after[0], and_(m.Job.available_at == after[0], m.Job.id > after[1]))) if after else base
    rows = list(db.execute(query.order_by(m.Job.available_at, m.Job.id).limit(limit)))
    if not rows and after: rows = list(db.execute(base.order_by(m.Job.available_at, m.Job.id).limit(limit)))
    return rows


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


def _receipt_cap(db, task, job, business):
    if job.kind == 'execute_workflow_step':
        step = next(v for v in business['flow']['steps'] if v['id'] == job.data['scope']['step_id'])
        execution = step['execution']; inputs = job.data['scope']['inputs']
    elif job.kind == 'execute_full_semantic_graph_node':
        from . import workflow_graph as graph
        activation = task.data['graph_runtime']['activations'][job.data['activation_id']]
        step = graph.node_for(graph.graph_for(db.get(m.InstanceRelease, task.data['instance_release_id']), business), activation)
        execution = step['execution']; inputs = job.data['inputs']
    else: return None
    cap = db.scalar(select(m.Capability).where(m.Capability.workspace_id == task.workspace_id,
        m.Capability.id == execution['capability_id']))
    need(cap is not None and cap.content_hash == execution['capability_hash'] == digest(cap.data))
    handler = cap.data['execution'].get('handler')
    from . import workflow_sound_receipt_status_v261 as sound
    from . import workflow_pixel_receipt_status_v263 as pixel
    reader = sound if handler == sound.HANDLER else pixel if handler == pixel.HANDLER else None
    if reader is not None:
        need(digest(cap.data) == digest(reader.manifest().model_dump(mode='json')) and inputs == {'task_id': task.id}, ACK_ERROR)
    return reader


def readiness(db, p, task, job, business=None):
    """No mutation; only scheduling readiness, never a saved SDK response."""
    ready = {'state': 'ready', 'reason': None}
    from .workflow_provenance_wait_v197 import PHASE
    if job.status == PHASE: return {'state': 'waiting_producer', 'reason': PRODUCER_WAIT}
    if job.status in {'blocked', 'failed', 'cancelled'}:
        return {'state': 'blocked_integrity', 'reason': ERROR}
    if job.kind == NATIVE_GOAL_KIND:
        from .goal_evaluation_provider_v288 import job_ready
        return ready if job_ready(db, p, job) else {'state': 'waiting_producer', 'reason': PRODUCER_WAIT}
    if job.status != 'queued': return ready
    calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
        m.Invocation.idempotency_key == job.data.get('invocation_key')).limit(2)))
    need(len(calls) <= 1)
    if calls: return ready  # Original SDK/recovery only; never recompute a saved waiting snapshot.
    from .workflow_job_dependencies_v264 import pending_predicate
    if db.scalar(select(m.Job.id).where(m.Job.id == job.id, m.Job.workspace_id == p.workspace_id, pending_predicate())):
        return {'state': 'waiting_producer', 'reason': PRODUCER_WAIT}
    if business is None:
        _, business = _created(db, p.workspace_id, task.data['instance_id'], task,
            'graph' if 'graph_runtime' in task.data else 'compact')
    reader = _receipt_cap(db, task, job, business)
    if reader is None: return ready
    value = reader.execute(db, p, task.id)
    blocked_event = any(ref['event_id'] is not None and db.scalar(select(m.Event.status).where(
        m.Event.workspace_id == p.workspace_id, m.Event.id == ref['event_id'])) == 'blocked_delivery' for ref in value['runs'])
    if value['blocked_count'] or blocked_event: return {'state': 'blocked_integrity', 'reason': ACK_ERROR}
    if value['registered_count'] > value['received_count']:
        return {'state': 'waiting_data', 'reason': ACK_WAIT}
    return ready  # Includes empty registered scope; it still reports all_registered_received=False.


def _readiness(workspace_id, instance_id, family, job_id, task_id=None):
    try:
        with _reader() as db:
            p, task, _release, business, job = _prove(db, workspace_id, instance_id, family, job_id, task_id)
            return readiness(db, p, task, job, business)
    except HTTPException as error:
        from .workflow_pixel_receipt_status_v263 import PRODUCER_PENDING
        return {'state': 'waiting_producer' if error.detail == PRODUCER_PENDING else 'blocked_integrity',
            'reason': PRODUCER_WAIT if error.detail == PRODUCER_PENDING else ACK_ERROR if error.status_code == 409 else ERROR}


def _native_observations(db, p, task):
    from . import pixel_question_sources_v252 as pixels, task_sound_query_evidence as sounds
    from . import native_pixel_provenance_v250 as pixel_origin, native_sound_provenance as sound_origin
    pairs = [(run, pixel_origin) for run in pixels._inventory(db, p, task.data['instance_id'], task.id, None, MAX_RECORDS)]
    pairs += [(run, sound_origin) for run in sounds._inventory(db, p, task.data['instance_id'], task.id, MAX_RECORDS)]
    need(len(pairs) <= MAX_RECORDS, ACK_ERROR)
    # A saved real native SDK is another reverse witness if its Run and all
    # mutable source hints were removed. Never turn that damage into zero.
    from .workflow_job_dependencies_v264 import parent_pin
    registered = {origin.PACKET: {run.id for run, selected in pairs if selected is origin}
        for origin in (pixel_origin, sound_origin)}
    adapters = {'local_pixel_change': ('pixel_run_id', pixel_origin.PACKET),
        'local_environment_sound': ('sound_run_id', sound_origin.PACKET)}
    for receipt in task.data['workflow_runtime']['receipts']:
        if receipt.get('execution_mode') not in {'registered_ability', 'registered_graph_ability'}: continue
        cap = db.scalar(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
            m.Capability.id == receipt.get('capability_id')))
        need(cap is not None and cap.content_hash == cap.validated_hash == digest(cap.data)
            == receipt.get('capability_hash'), ACK_ERROR)
        selected = adapters.get(cap.data['execution'].get('adapter'))
        if selected is not None:
            parent_pin(db, p, task, receipt['request_id'])
            key, packet = selected
            need(type(receipt['output']) is dict and receipt['output'].get(key) in registered[packet], ACK_ERROR)
    ids = []
    for run, origin in pairs:
        need(run.task_id == task.id and run.workspace_id == p.workspace_id, ACK_ERROR)
        for observation in origin.observations(db, p, run.id):
            packet = observation.data.get(origin.PACKET)
            issuer = observation.data.get('submitted_by')
            need(observation.workspace_id == p.workspace_id and _uuid(issuer)
                and type(packet) is dict and packet.get('task_id') == task.id
                and packet.get('run_id') == run.id and packet.get('instance_id') == task.data['instance_id'], ACK_ERROR)
            # This is the ordinary independent receiver's historical identity,
            # never a replacement execution principal. The original verifier
            # must prove this issuer against the Run/SDK/receipt/Audits before
            # the separate receiver is invoked. Personnel changes do not
            # destroy a legitimately produced source's right to be archived.
            receiver = Principal(issuer, p.workspace_id, 'viewer', 'workflow-task-data-receiver-v265')
            context = origin.checked_observation(db, receiver, observation)
            expected_issuer = origin.issuer(context) if origin is pixel_origin else context['run'].owner_id
            need(context['task'].id == task.id and context['run'].id == run.id
                and context['event'].kind == 'observation.data' and observation.workspace_id == p.workspace_id
                and context['release'].instance_id == task.data['instance_id'] and issuer == expected_issuer, ACK_ERROR)
            if context['event'].status == 'pending_delivery': ids.append(observation.id)
    need(len(ids) == len(set(ids)), ACK_ERROR)
    return ids


def drain_task(workspace_id, instance_id, family, task_id, *, stop_event=None):
    if _stopped(stop_event): return 0
    with _reader() as db:
        task = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id, m.Task.id == task_id))
        _created(db, workspace_id, instance_id, task, family)
        if family == 'compact':
            pointer = task.data['workflow_runtime'].get('automatic', {}).get('job_id')
            pointers = [pointer] if pointer is not None else []
        else:
            pointers = [value['job_id'] for value in task.data.get('graph_jobs', {}).values()]
            pointers += list(task.data.get('graph_timers', {}).values())
            poll = task.data.get('graph_wait_poll', {}).get('job_id')
            if poll is not None: pointers.append(poll)
        need(all(_uuid(value) for value in pointers) and len(pointers) == len(set(pointers)))
        for pointer in pointers:
            linked = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.id == pointer))
            need(linked is not None and linked.data.get('task_id') == task.id)
        # Only the workspace is used by the initial independent inventories.
        # Each actual source below must establish its own historical issuer;
        # the Task creator is neither a fallback receiver nor a Job executor.
        p = Principal(task.owner_id, workspace_id, 'viewer', 'workflow-task-source-inventory-v265')
        ids = _native_observations(db, p, task)
    from .worker import deliver_events
    seen = 0
    for identity in ids:
        if _stopped(stop_event): break
        deliver_events(workspace_id=workspace_id, observation_id=identity, event_kind='observation.data')
        with _reader() as db:
            event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
                m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + identity))
            seen += int(event is not None and event.status == 'received')
    return seen  # Newly observed reception, not attribution to one of concurrent receivers.


def _delivery_page(db, workspace_id, instance_id, task_id, after, limit):
    base = select(m.Task.id, m.Task.created_at).where(m.Task.id.in_(_task_query(workspace_id, instance_id, task_id)))
    query = base.where(or_(m.Task.created_at > after[0], and_(m.Task.created_at == after[0], m.Task.id > after[1]))) if after else base
    rows = list(db.execute(query.order_by(m.Task.created_at, m.Task.id).limit(limit)))
    if not rows and after: rows = list(db.execute(base.order_by(m.Task.created_at, m.Task.id).limit(limit)))
    return rows


def step(workspace_id, instance_id, *, family, task_id=None, after=None, delivery_after=None, limit=20, stop_event=None):
    _scope(workspace_id, instance_id, family, task_id, limit)
    result = {'processed': False, 'after': after, 'delivery_after': delivery_after, 'waiting': [], 'errors': [], 'delivered': 0}
    if _stopped(stop_event): return result
    with Session(engine()) as db:
        instance = db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id == workspace_id, m.DomainInstance.id == instance_id))
        need(instance is not None)
        if task_id is not None:
            target = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id, m.Task.id == task_id))
            _release, business = _created(db, workspace_id, instance_id, target, None)
            actual = 'graph' if business['execution_mode'] == 'confirmed_graph_workflow' else 'compact'
            if actual != family:
                result['errors'].append({'job_id': None, 'reason': ERROR})
                return result
        deliveries = _delivery_page(db, workspace_id, instance_id, task_id, delivery_after, limit)
    for identity, created_at in deliveries:
        if _stopped(stop_event): return result
        result['delivery_after'] = (created_at, identity)
        try:
            with Session(engine()) as db:
                target = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id, m.Task.id == identity))
                _release, business = _created(db, workspace_id, instance_id, target, None)
                actual = 'graph' if business['execution_mode'] == 'confirmed_graph_workflow' else 'compact'
            if actual != family: continue
            result['delivered'] += drain_task(workspace_id, instance_id, family, identity, stop_event=stop_event)
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
            result['errors'].append({'job_id': None, 'reason': ACK_ERROR})
    with Session(engine()) as db: rows = _page(db, workspace_id, instance_id, family, task_id, after, limit)
    for identity, available_at in rows:
        if _stopped(stop_event): break
        result['after'] = (available_at, identity)
        try:
            with Session(engine()) as db:
                _p, task, _release, _business, job = _prove(db, workspace_id, instance_id, family, identity, task_id)
                from .workflow_provenance_wait_v197 import PHASE
                selected_task_id, service_wait = task.id, job.status == 'waiting_service'
                provenance_wait = job.status == PHASE
            if _stopped(stop_event): break
            if service_wait:
                from .workflow_service_poll_v195 import schedule_once
                schedule_once(workspace_id, job_id=identity)
            if provenance_wait:
                from .workflow_provenance_wait_v197 import poll_once
                poll_once(workspace_id, job_id=identity)
            gate = _readiness(workspace_id, instance_id, family, identity, task_id)
            if gate['state'] != 'ready':
                result['waiting'].append({'job_id': identity, **gate}); continue
            if _stopped(stop_event): break
            from .worker import process_once
            if process_once(job_id=identity, kinds=list(KINDS[family]), deliver=False,
                    workspace_id=workspace_id, _stop_event=stop_event):
                result['processed'] = True
                if not _stopped(stop_event):
                    try: result['delivered'] += drain_task(workspace_id, instance_id, family, selected_task_id, stop_event=stop_event)
                    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
                        result['errors'].append({'job_id': identity, 'reason': ACK_ERROR})
                break
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
            result['errors'].append({'job_id': identity, 'reason': ERROR})
    if not rows: result['after'] = None
    if not deliveries: result['delivery_after'] = None
    return result


def admission_reason(db, p, task, job):
    """Use only an existing clean RR/RO caller; never rent another connection.

    Writer/POST projections retain their original generic reason. This helper
    does not switch, close or roll back the caller's transaction, and it grants
    no readiness/claim authority. The scheduler keeps its independent reader.
    """
    if job.status != 'queued' or not any(job.kind in kinds for kinds in KINDS.values()): return None
    # Optional display-only contexts outside a real Task keep their original
    # status reason. This never authorizes a worker claim or bypasses _prove.
    if db is None or p is None or task is None: return None
    if db.new or db.dirty or db.deleted or not db.in_transaction(): return None
    if (db.scalar(text('SHOW transaction_isolation')) != 'repeatable read'
            or db.scalar(text('SHOW transaction_read_only')) != 'on'): return None
    try:
        from .media_daily_task_adapter import current_actor
        current_actor(db, p)
        need(task.workspace_id == p.workspace_id)
        family = 'graph' if job.kind in KINDS['graph'] else 'compact'
        actor, saved_task, _release, business, saved_job = _prove(db, p.workspace_id,
            task.data['instance_id'], family, job.id, task.id)
        result = readiness(db, actor, saved_task, saved_job, business)
        return result['reason'] if result['state'] != 'ready' else None
    except HTTPException as error:
        from .workflow_pixel_receipt_status_v263 import PRODUCER_PENDING
        return (PRODUCER_WAIT if error.detail == PRODUCER_PENDING
            else ACK_ERROR if error.status_code == 409 else ERROR)
    except (KeyError, ValueError, TypeError, AttributeError, OSError): return ERROR


def run(workspace_id, instance_id, *, family, task_id=None, stop_event=None):
    _scope(workspace_id, instance_id, family, task_id)
    stop = stop_event if stop_event is not None else threading.Event()
    need(threading.current_thread() is threading.main_thread() or stop_event is not None)
    previous = {}; after = delivery_after = None
    def request_stop(_signal, _frame): stop.set()
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM): previous[signum] = signal.signal(signum, request_stop)
    print('指定工作区、实例和类型的工作流处理器已启动；人工确认、原审批与能力权限保持。', flush=True)
    try:
        while not stop.is_set():
            try:
                value = step(workspace_id, instance_id, family=family, task_id=task_id,
                    after=after, delivery_after=delivery_after, stop_event=stop)
                after, delivery_after = value['after'], value['delivery_after']
                for error in value['errors']: print(error['reason'], flush=True)
                if not value['processed']: stop.wait(1 if not value['errors'] else 2)
            except Exception:
                print(ERROR, flush=True); stop.wait(2)
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)
        print('工作流处理器已停止；已保存的执行、人工工作和收件记录保留。', flush=True)
    return 0
