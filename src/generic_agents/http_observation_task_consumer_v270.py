"""Task-scoped scheduling and an independent original V177 data receiver.

Run ``jobs`` and ``receipts`` as separate processes. Selection is not an
execution grant: only the original exact-ID worker claims/leases a Job. The
receipt process calls the original data receiver, then cold-reads its real
Event/Observation/Memory receipt. It never writes an ACK or accepts a Goal.
All V177 simulation/test-only markers remain mandatory and unchanged.

Inventories are bounded, fail closed on overflow, and are rebuilt from durable
original rows and independent audits on every tick, including after restart.
No local cursor or previously selected source is execution authority.
"""
import signal
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from . import models as m
from .contracts import digest
from .db import engine
from .security import Principal

FAMILY = 'http_mapped_v177'
KINDS = ('dispatch_acquisition', 'read_http_observation_receipt_v177',
    'process_http_mapped_observation_v177', 'settle_received_acquisition_v245',
    'check_native_business_goal')
OPERATIONS = {'resense_with_receipt', 'resense_from_context'}
MODES = {'jobs', 'receipts'}
MAX_RECORDS = 1000
MAX_READS = 8
RECEIVED = 'http.observation.task_receiver.received_v270'
ERROR = '原任务、原补采或原处理归属无法核对；记录保留，未领取其他任务'
CAPACITY = '原任务库存超过本处理器核对容量；没有截断为完整或空库存，请核查原记录'
ACK_ERROR = '原观测或独立数据接收依据受阻；处理成果保留，未代办接收或重新处理'
FAILURES = (HTTPException, DBAPIError, KeyError, ValueError, TypeError, AttributeError,
    OSError, OverflowError, StopIteration)


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _scope(workspace_id, instance_id, task_id, family, limit=20):
    need(all(_uuid(value) for value in (workspace_id, instance_id, task_id))
        and type(family) is str and family == FAMILY
        and type(limit) is int and 1 <= limit <= 50)


def _stopped(stop):
    return stop is not None and stop.is_set()


@contextmanager
def _reader():
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            with Session(bind=connection, autoflush=False, expire_on_commit=False,
                    join_transaction_mode='rollback_only') as db:
                yield db
        finally:
            if transaction.is_active:
                transaction.rollback()


def _rows(db, query, maximum=MAX_RECORDS):
    rows = list(db.scalars(query.limit(maximum + 1)))
    need(len(rows) <= maximum, CAPACITY)
    return rows


def _get(db, p, model, identity):
    need(_uuid(identity))
    row = db.scalar(select(model).where(model.workspace_id == p.workspace_id, model.id == identity))
    need(row is not None)
    return row


def _task(db, workspace_id, instance_id, task_id):
    from .workflow_task_consumer_v265 import _created
    from .native_goals import saved_context
    task = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace_id, m.Task.id == task_id))
    need(task is not None)
    # Inventory attribution only. This Principal is never a Job execution grant.
    p = Principal(task.owner_id, workspace_id, 'viewer', 'http-task-inventory-v270')
    release, business = _created(db, workspace_id, instance_id, task, None)
    need(business['execution_mode'] == 'confirmed_graph_workflow')
    context = saved_context(db, p, task)
    need(context['instance_id'] == instance_id and context['release_id'] == release.id)
    definitions = _rows(db, select(m.Audit).where(m.Audit.workspace_id == workspace_id,
        m.Audit.action == 'native_goal.defined', m.Audit.object_id == task_id), 2)
    need(len(definitions) == 1 and definitions[0].actor_id == context['actor_id']
        and definitions[0].data.get('goal_hash') == task.data['goal_hash'])
    return p, task


def _service_delegations(binding):
    """Dispatch only explicit original families to their own frozen verifier."""
    need(type(binding) is dict)
    if binding.get('schema') == 'authored-goal-data-workflow-binding-v288':
        from .authored_goal_data_workflow_store_v288 import service_delegations
        return service_delegations(binding)
    if binding.get('schema') == 'authored-decision-workflow-binding-v286':
        from .authored_decision_workflow_store_v286 import service_delegations
        return service_delegations(binding)
    if binding.get('schema') == 'authored-decision-workflow-binding-v278':
        from .authored_decision_workflow_store_v278 import service_delegations
        return service_delegations(binding)
    if binding.get('schema') == 'authored-service-workflow-binding-v270':
        from .authored_service_workflow_store_v270 import service_delegations
        return service_delegations(binding)
    from .business_flow_nested_adoption_v195 import BINDING, service_delegations
    need(binding.get('schema') == BINDING)
    return service_delegations(binding['translation'])


def _origin(db, p, task, call):
    """Read the actual frozen graph service call and its independent state audit."""
    from . import workflow_graph as graph, workflow_graph_scope as scope
    from . import workflow_service_v195 as service
    job = _get(db, p, m.Job, call.data.get('job_id'))
    actual, release, business, value, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    need(actual.id == task.id)
    transport = scope._job_matches(task, job, state, activation, node)
    capability = _get(db, p, m.Capability, call.capability_id)
    spec = node['execution']; execution = capability.data['execution']
    need(execution.get('adapter') == 'workflow_service' and execution.get('operation') in OPERATIONS
        and capability.id == spec['capability_id']
        and capability.content_hash == capability.validated_hash == spec['capability_hash'] == digest(capability.data))
    binding = graph.business_binding(release, business)
    delegation = _service_delegations(binding).get(node['id'])
    need(delegation is not None and delegation['operation'] == execution['operation']
        and delegation['service_actor'] == execution['service_actor'])
    inputs = job.data['inputs']; approval = service.make_approval(task, job, node, inputs)
    need(call.workspace_id == p.workspace_id and call.idempotency_key == job.data['invocation_key']
        and call.data.get('role') == delegation['agent_role']
        and call.data.get('capability_hash') == capability.content_hash
        and call.data.get('input_hash') == digest(inputs) and call.data.get('inputs') == inputs
        and call.data.get('approval') == approval and call.data.get('side_effect') is True)
    context = {'task': task, 'job': job, 'capability': capability, 'binding': binding,
        'delegation': delegation, 'transport': transport}
    issuer = Principal(job.data['actor_id'], p.workspace_id, 'viewer', 'http-service-history-v270')
    original = service._state(db, issuer, context, call, inputs, call.idempotency_key, approval)
    need(original is not None and original['simulation_only'] is True)
    data = original['operation_data']
    need(set(data) == {'observation_request', 'acquisition_pins', 'source_contracts'}
        and type(data['acquisition_pins']) is list and 0 < len(data['acquisition_pins']) <= service.MAX_ACQUISITIONS
        and len({pin['acquisition_id'] for pin in data['acquisition_pins']}) == len(data['acquisition_pins']))
    return original


def _origins(db, p, task):
    from . import workflow_service_v195 as service
    anchors = _rows(db, select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == service.STARTED, m.Audit.data['task_id'].as_string() == task.id,
        m.Audit.data['operation'].as_string().in_(OPERATIONS)))
    hints = {row.object_id for row in anchors}
    producers = _rows(db, select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.kind == 'execute_full_semantic_graph_node', m.Job.data['task_id'].as_string() == task.id))
    original_keys = [job.data['invocation_key'] for job in producers if type(job.data.get('invocation_key')) is str]
    calls = _rows(db, select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
        or_(m.Invocation.id.in_(hints), m.Invocation.idempotency_key.in_(original_keys),
            m.Invocation.data['service_state']['task_id'].as_string() == task.id,
            m.Invocation.data['approval']['task_id'].as_string() == task.id)))
    need(hints.issubset({call.id for call in calls}))
    result = {}
    for call in calls:
        cap = _get(db, p, m.Capability, call.capability_id)
        saved_state = call.data.get('service_state')
        need(saved_state is None or type(saved_state) is dict)
        marked = (call.id in hints or (saved_state or {}).get('operation') in OPERATIONS
            or cap.data.get('execution', {}).get('operation') in OPERATIONS)
        if not marked:
            continue
        if call.data.get('service_state') is None:
            # A genuinely unissued service has not created acquisition authority.
            need(call.id not in hints and call.status in {'created', 'running'})
            continue
        original = _origin(db, p, task, call)
        for pin in original['operation_data']['acquisition_pins']:
            saved = result.get(pin['acquisition_id'])
            need(saved is None or saved['pin'] == pin)
            result[pin['acquisition_id']] = {'pin': pin, 'request': original['operation_data']['observation_request'],
                'invocation_id': call.id, 'service_state_hash': original['state_hash']}
    need(len(result) <= MAX_RECORDS, CAPACITY)
    return result


def _first_dispatch_pending(db, p, task, acquisition, plan, source, origin):
    """Classify one never-claimed original mapped dispatch, never grant IO.

    Selection commits authorized Acq + awaiting_dispatch Plan + queued Job
    before the actual leased dispatcher creates a V177 Exchange. Missing
    Exchange is allowed only at that precise original boundary. Any attempt,
    edited Job, retained wire/receipt or reverse witness keeps find_exchange's
    strict missing/orphan behavior. This function creates or changes nothing.
    """
    from . import http_observation_transport_v177 as transport
    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
    from .monitor_scheduling_service_v253 import KEY as schedule_key, ACTION_PIN
    if (acquisition.status != 'authorized' or acquisition.version != 1
            or plan.status != 'awaiting_dispatch'
            or plan.data.get('current_acquisition_id') != acquisition.id
            or transport.MAPPING_KEY not in source.data.get('specification', {})):
        return False
    key = 'dispatch:' + acquisition.id
    jobs = _rows(db, select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.kind == 'dispatch_acquisition', or_(m.Job.data['acquisition_id'].as_string() == acquisition.id,
            m.Job.idempotency_key == key)), 2)
    if len(jobs) != 1:
        return False
    job = jobs[0]
    if not (job.status == 'queued' and type(job.attempts) is int and job.attempts == 0
            and job.lease_token is None and job.lease_until is None and job.idempotency_key == key
            and job.data == {'acquisition_id': acquisition.id, 'actor_id': acquisition.data.get('actor_id')}):
        return False
    if _rows(db, select(Exchange).where(Exchange.workspace_id == p.workspace_id,
            or_(Exchange.acquisition_id == acquisition.id,
                Exchange.data['freeze']['original_request_key'].as_string() == acquisition.idempotency_key)), 1):
        return False  # Every existing Exchange still uses its original verifier.
    need(acquisition.workspace_id == plan.workspace_id == source.workspace_id == task.workspace_id == p.workspace_id
        and source.kind == 'source' and source.status == 'published'
        and source.content_hash == source.validated_hash == digest(source.data) == origin['pin']['source_hash']
        and source.data['data_policy'].get('simulation') is True and source.data['data_policy'].get('test_only') is True
        and type(source.data['specification'][transport.MAPPING_KEY]) is dict
        and acquisition.capability_id == source.id and plan.id == origin['pin']['observation_plan_id']
        and plan.policy_id == origin['pin']['policy_id'] and plan.entity_id == acquisition.entity_id
        and plan.data.get('observation_request') == origin['request'])
    forbidden = ('dispatch', 'exchange', 'receipt', 'closing_observation', 'wire', 'provider_response')
    need(not any(any(marker in field.lower() for marker in forbidden) for field in acquisition.data))
    call = _get(db, p, m.Invocation, origin['invocation_id'])
    state = _origin(db, p, task, call)  # Actual SDK/state and independent STARTED Audit, never a supplied hash alone.
    need(state['state_hash'] == origin['service_state_hash']
        and state['operation_data']['observation_request'] == origin['request']
        and state['operation_data']['acquisition_pins'].count(origin['pin']) == 1)
    audits = _rows(db, select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == acquisition.id))
    schedule = acquisition.data.get(schedule_key)
    if schedule is None:
        need(not audits)
    else:
        need(len(audits) == 1 and audits[0].action == ACTION_PIN and audits[0].actor_id == state['actor_id']
            and audits[0].data == {'schema': 'monitor-scheduling-acquisition-audit-v253',
                'acquisition_id': acquisition.id, 'policy_id': plan.policy_id, 'actor_id': state['actor_id'],
                'pin_hash': digest(schedule), 'pin': schedule})
    seeds = {acquisition.id, acquisition.idempotency_key, job.id}
    def references(value, depth=0):
        need(depth <= 32)
        if type(value) is str: return {value}
        if type(value) is dict:
            return set().union(*(references(item, depth + 1) for item in value.values()))
        if type(value) is list:
            return set().union(*(references(item, depth + 1) for item in value))
        return set()
    # Stream the complete workspace inventory to detect surviving evidence
    # even when the forward references have disappeared.
    for model in (Exchange, m.Event, m.Memory, m.Audit, m.Invocation, m.Job):
        query = (select(model).where(model.workspace_id == p.workspace_id)
                 .order_by(model.id).execution_options(yield_per=200, populate_existing=True))
        rows = db.scalars(query)
        try:
            for row in rows:
                if model is m.Job and row.id == job.id: continue
                if model is m.Invocation and row.id == call.id: continue
                if model is m.Audit and row in audits: continue
                need(type(row.data) is dict and not (references(row.data) & seeds))
        finally:
            rows.close()
    return True


def _bindings(db, p, task, origins):
    from . import native_goal_acquisitions as native, workflow_service_v195 as service
    from . import http_observation_worker_v177 as http, http_observation_transport_v177 as transport
    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
    from .http_observation_exchange_v177 import ACTION
    result = {}
    for identity, origin in origins.items():
        acquisition, plan = native.acquisition(db, p, task, identity)
        policy = _get(db, p, m.MonitorPolicy, plan.policy_id)
        need(service._acquisition_pin(db, p, acquisition, policy, origin['request']) == origin['pin'])
        source = _get(db, p, m.Capability, acquisition.capability_id)
        exchange = (None if _first_dispatch_pending(db, p, task, acquisition, plan, source, origin)
            else http.find_exchange(db, p, acquisition))
        if exchange is None and transport.MAPPING_KEY not in source.data['specification']:
            continue
        need(source.content_hash == source.validated_hash == digest(source.data) == origin['pin']['source_hash']
            and source.data['data_policy'].get('simulation') is True
            and source.data['data_policy'].get('test_only') is True)
        if exchange is not None:
            from .http_observation_exchange_v177 import _parent_pins
            need(exchange.instance_id == task.data['instance_id'] and exchange.release_id == task.data['instance_release_id']
                and exchange.data['freeze']['parent_pins'] == _parent_pins(db, p, acquisition))
        result[identity] = {**origin, 'acquisition': acquisition, 'plan': plan, 'exchange': exchange}
    # Reverse independently stored exchanges cannot disappear into an empty or
    # incomplete forward inventory after damage to a service/acquisition hint.
    reverse = _rows(db, select(Exchange).where(Exchange.workspace_id == p.workspace_id,
        Exchange.data['freeze']['parent_pins']['task_id'].as_string() == task.id))
    for exchange in reverse:
        need(exchange.acquisition_id in result and result[exchange.acquisition_id]['exchange'] is not None
            and result[exchange.acquisition_id]['exchange'].id == exchange.id)
    audits = _rows(db, select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION,
        m.Audit.data['state']['data']['freeze']['parent_pins']['task_id'].as_string() == task.id))
    known = {row['exchange'].id for row in result.values() if row['exchange'] is not None}
    need(all(anchor.object_id in known for anchor in audits))
    return result


def _observation(db, p, task, binding):
    from .mapped_acquisition_origin_v177 import guard
    exchange = binding['exchange']
    if exchange is None or exchange.observation_id is None:
        return None
    original = _get(db, p, m.Observation, exchange.observation_id)
    proof = guard(db, p, original)
    need(proof is not None and proof['acquisition_id'] == binding['acquisition'].id)
    process = _get(db, p, m.Job, proof['processing_job_id'])
    need(process.data['observation_id'] == original.id)
    return {'observation': original, 'proof': proof, 'process': process, 'exchange': exchange}


def _goal_job(task, job):
    context = task.data['native_goal_context']
    need(job.data.get('task_id') == task.id and job.data.get('context_hash') == digest(context)
        and job.data.get('actor_id') == context['actor_id'])
    trigger = job.data.get('time_trigger')
    if trigger is not None:
        need(job.data.get('time_trigger_hash') == digest(trigger)
            and job.idempotency_key == 'native-goal-time:' + task.id + ':' + digest(trigger))
    else:
        prefix = 'native-goal:' + task.id + ':' + digest(context) + ':'
        need(job.idempotency_key.startswith(prefix) and len(job.idempotency_key) > len(prefix))


def _job(db, p, task, bindings, observations, job):
    from . import http_observation_worker_v177 as http, acquisition_ack_settlement_v245 as settlement
    need(job.workspace_id == p.workspace_id and job.kind in KINDS and type(job.data) is dict)
    if job.kind == 'check_native_business_goal':
        _goal_job(task, job)
        return
    if job.kind == 'dispatch_acquisition':
        binding = bindings.get(job.data.get('acquisition_id')); need(binding is not None)
        acquisition = binding['acquisition']
        prefix = 'dispatch:' + acquisition.id
        suffix = job.idempotency_key.removeprefix(prefix + ':resume:')
        need(job.data.get('actor_id') == acquisition.data['actor_id']
            and (job.idempotency_key == prefix or
                job.idempotency_key.startswith(prefix + ':resume:') and suffix.isdecimal()))
    elif job.kind == 'read_http_observation_receipt_v177':
        matches = [row['exchange'] for row in bindings.values() if row['exchange'] is not None
            and row['exchange'].id == job.data.get('exchange_id')]
        need(len(matches) == 1)
        issuer = Principal(job.data['actor_id'], p.workspace_id, 'viewer', 'http-receipt-history-v270')
        http._read_job(db, issuer, matches[0], job)
    elif job.kind == 'process_http_mapped_observation_v177':
        observation = observations.get(job.data.get('observation_id')); need(observation is not None)
        need(job.id == observation['process'].id and job.data['actor_id'] == observation['exchange'].actor_id)
    else:
        pin = job.data.get('settlement'); need(type(pin) is dict)
        need(pin.get('acquisition_id') in bindings and pin.get('observation_id') in observations
            and pin.get('instance_id') == task.data['instance_id'] and pin.get('family') == FAMILY
            and job.data.get('settlement_hash') == digest(pin)
            and job.idempotency_key == 'acquisition-data-ack:' + pin['acquisition_id'] + ':' + pin['observation_id'])
        observation = observations[pin['observation_id']]['observation']
        event = _get(db, p, m.Event, pin['event_id'])
        need(settlement.checked_input(db, p, event, observation) == pin
            and job.data.get('actor_id') == pin['actor_id'])
        anchors = _rows(db, select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == settlement.ACTION, m.Audit.object_id == job.id), 2)
        need(len(anchors) == 1 and anchors[0].actor_id == pin['actor_id'] and anchors[0].data == {
            'job_id': job.id, 'settlement': pin, 'settlement_hash': digest(pin)})


def _inventory(db, workspace_id, instance_id, task_id):
    from . import acquisition_ack_settlement_v245 as settlement
    p, task = _task(db, workspace_id, instance_id, task_id)
    origins = _origins(db, p, task)
    bindings = _bindings(db, p, task, origins)
    observations = {}
    for binding in bindings.values():
        observation = _observation(db, p, task, binding)
        if observation is not None:
            observations[observation['observation'].id] = observation
    acquisitions = list(bindings)
    exchanges = [value['exchange'].id for value in bindings.values() if value['exchange'] is not None]
    anchors = _rows(db, select(m.Audit).where(m.Audit.workspace_id == workspace_id,
        m.Audit.action == settlement.ACTION, m.Audit.data['settlement']['acquisition_id'].as_string().in_(acquisitions)))
    settlement_ids = {row.object_id for row in anchors}
    goal_ids = set()
    latest = task.data.get('native_goal_check_job_pin')
    if latest is not None:
        need(type(latest) is dict and task.data.get('native_goal_check_job_pin_hash') == digest(latest))
        goal_ids.add(latest['job_id'])
    timer = task.data.get('native_goal_timer')
    if timer is not None:
        need(type(timer) is dict and task.data.get('native_goal_timer_hash') == digest(timer))
        goal_ids.add(timer['job_id'])
    jobs = _rows(db, select(m.Job).where(m.Job.workspace_id == workspace_id, or_(
        and_(m.Job.kind == 'check_native_business_goal', m.Job.data['task_id'].as_string() == task.id),
        m.Job.data['acquisition_id'].as_string().in_(acquisitions),
        m.Job.data['exchange_id'].as_string().in_(exchanges),
        m.Job.data['http_mapped_exchange_id'].as_string().in_(exchanges),
        m.Job.data['observation_id'].as_string().in_(list(observations)),
        m.Job.data['settlement']['acquisition_id'].as_string().in_(acquisitions), m.Job.id.in_(settlement_ids | goal_ids))))
    need((settlement_ids | goal_ids).issubset({job.id for job in jobs}))
    selected = []
    for job in jobs:
        # Other established families sharing an Observation are never claimed.
        if job.kind not in KINDS:
            need(job.id not in settlement_ids | goal_ids and not job.idempotency_key.startswith('extract:')
                and not job.idempotency_key.startswith('dispatch:'))
            continue
        _job(db, p, task, bindings, observations, job)
        selected.append(job)
    for identity, original in observations.items():
        acquisition = bindings[original['proof']['acquisition_id']]['acquisition']
        if acquisition.status != 'awaiting_observation':
            continue
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + identity))
        if event is not None and event.status == 'received':
            # ACK and settlement enqueue are one original receiver transaction.
            # A missing queue row must not become idle/success or be recreated
            # by the consumer after an explicit original settlement block.
            key = 'acquisition-data-ack:' + acquisition.id + ':' + identity
            need(any(job.kind == settlement.KIND and job.idempotency_key == key for job in selected), ACK_ERROR)
    return p, task, bindings, observations, selected


def _poll_state(exchange, jobs, current):
    """Only durable original GET history controls retry pacing and its bound."""
    from .http_observation_exchange_v177 import can_resume_observation
    if exchange.status != 'awaiting_receipt' and not can_resume_observation(exchange):
        return {'state': 'not_needed'}
    reads = exchange.data['read_jobs']
    by_id = {job.id: job for job in jobs}
    need(all(row['job_id'] in by_id for row in reads))
    need(exchange.data['active_read_job_id'] is None or exchange.data['active_read_job_id'] in by_id)
    active = by_id.get(exchange.data['active_read_job_id'])
    if active is not None and active.status in {'queued', 'running'}:
        return {'state': 'waiting_read', 'job_id': active.id}
    if len(reads) >= MAX_READS:
        return {'state': 'blocked_read_limit', 'reason': '原号回执核对已达自动查询上限；原号保留，请核查供应服务'}
    if reads:
        last = by_id[reads[-1]['job_id']]
        delay = min(300, 5 * 2 ** min(len(reads) - 1, 6))
        completed = (getattr(last, 'data', None) or {}).get('finished_at')
        base = last.created_at
        if completed is not None:
            need(type(completed) is str)
            completed = datetime.fromisoformat(completed)
            need(completed.tzinfo is not None)
            base = max(base, completed)
        due = base + timedelta(seconds=delay)
        if current < due:
            return {'state': 'waiting_backoff', 'retry_at': due.isoformat()}
    return {'state': 'ready'}


def _result(workspace_id, instance_id, task_id, mode):
    return {'schema': 'http-observation-task-consumer-v270', 'mode': mode, 'family': FAMILY,
        'workspace_id': workspace_id, 'instance_id': instance_id, 'task_id': task_id,
        'state': 'idle', 'processed': False, 'job_id': None, 'read_queued': None,
        'jobs': [], 'polls': [], 'acquisitions': [], 'observations': [], 'received': [], 'waiting': [], 'blocked': [],
        'inventory': 'not_read', 'received_count': 0, 'simulation': True, 'test_only': True,
        'field_verified': False, 'production_activation': False}


def _pending_unsent_resume(db, p, task, binding, failed):
    """Cold historical classification only; the queued worker still grants IO.

    A normal Plan retry may queue the FIRST send of the same frozen Exchange.
    Its original failed dispatch remains history only if all old terminal Jobs
    have exact independent no-send proofs and the current resume is untouched.
    No synthetic worker lease, persisted skip marker or status-only exemption
    participates in this read-only predicate.
    """
    from . import managed_source_unsent_v270 as unsent
    try:
        acq, plan, exchange = binding['acquisition'], binding['plan'], binding['exchange']
        if (exchange is None or acq.status != 'authorized' or plan.status != 'awaiting_dispatch'
                or plan.data.get('current_acquisition_id') != acq.id
                or failed.kind != 'dispatch_acquisition' or failed.data.get('acquisition_id') != acq.id):
            return False
        issuer = Principal(acq.data['actor_id'], p.workspace_id, 'viewer', 'http-unsent-resume-history-v291')
        actual, row, pin = unsent._cold_scope(db, issuer, exchange)
        need(actual.id == acq.id and row.id == exchange.id
            and row.instance_id == task.data['instance_id'] and row.release_id == task.data['instance_release_id'])
        jobs, audits = unsent._rows(db, issuer, actual)
        queued = [candidate for candidate in jobs if candidate.status == 'queued']
        need(len(queued) == 1)
        resume = queued[0]
        need(resume.id != failed.id and resume.id != row.dispatch_job_id
            and resume.attempts == 0 and type(resume.attempts) is int
            and resume.lease_token is None and resume.lease_until is None
            and resume.idempotency_key == 'dispatch:' + actual.id + ':resume:' + str(actual.version)
            and resume.data == {'acquisition_id': actual.id, 'actor_id': actual.data['actor_id']})
        unsent._normal_retry(resume, actual)
        facts = unsent._facts(jobs)
        need(sum(candidate.id == failed.id for candidate in jobs) == 1)
        proofs = unsent._prior_proofs(pin, resume.id, facts, unsent._audit_facts(audits),
            exchange_created_at=row.created_at)
        need(bool(proofs))
        unsent._unsent(actual, row, audits)
        need(any(item.action == unsent.UNSENT and item.data.get('job_id') == failed.id for item in audits))
        recovered = [item for item in plan.data.get('history', []) if item.get('kind') == 'resource_recovered'
            and item.get('acquisition_id') == actual.id]
        need(bool(recovered))
        at = max(unsent._time(item['at']) for item in recovered)
        previous = [fact for fact in facts if fact['id'] != resume.id]
        need(all(at > unsent._time(fact['finished_at']) for fact in previous)
            and unsent._time(resume.created_at) >= at)
        retries = _rows(db, select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == plan.id, m.Audit.action == 'sensor.plan_retried'))
        matches = [anchor for anchor in retries if unsent._time(anchor.created_at) >= at
            and type(anchor.data) is dict and set(anchor.data) == {'reason'}
            and type(anchor.data['reason']) is str and bool(anchor.data['reason'])]
        need(len(matches) == 1)
        actor = db.scalar(select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
            m.User.id == matches[0].actor_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
            m.Membership.role.in_(['admin', 'operator'])))
        need(actor == matches[0].actor_id)
        return True
    except FAILURES:
        return False


def _accepted_unsent_resume(db, p, task, binding, failed):
    """A real accepted first POST makes its proven pre-send failures history.

    Read the immutable receipt/authority chain and historical sender lease;
    never require or fabricate a live lease and never call a sender. Unknown
    current outcomes, failed senders, incomplete old no-send proofs or a later
    contradictory receipt remain blocked rather than being hidden as history.
    """
    from . import http_observation_exchange_v177 as ledger, managed_source_unsent_v270 as unsent
    try:
        acq, exchange = binding['acquisition'], binding['exchange']
        if (exchange is None or acq.status not in {'awaiting_observation', 'succeeded'}
                or failed.kind != 'dispatch_acquisition' or failed.data.get('acquisition_id') != acq.id):
            return False
        issuer = Principal(acq.data['actor_id'], p.workspace_id, 'viewer', 'http-accepted-resume-history-v291')
        checked = ledger.checked_store(db, issuer, exchange.id)
        actual, row, pin = unsent._cold_scope(db, issuer, checked)
        need(actual.id == acq.id and row.id == exchange.id
            and row.instance_id == task.data['instance_id'] and row.release_id == task.data['instance_release_id']
            and type(actual.data.get('dispatch_started_at')) is str and bool(actual.data['dispatch_started_at'])
            and actual.data.get('request_hash') == digest(row.data['freeze']['wire_frozen']['request']))
        history = row.data['receipt_history']
        need(type(history) is list and bool(history))
        posts = [entry for entry in history if entry['receipt_origin']['operation'] == 'dispatch_post']
        need(len(posts) == 1)
        entry = posts[0]; origin = entry['receipt_origin']
        need(origin['job_id'] != row.dispatch_job_id
            and entry['raw'].get('status') in {'accepted', 'completed'}
            and entry['projection'].get('status') in {'waiting', 'mapped', 'unclocked'}
            and history[-1]['raw'].get('status') in {'accepted', 'completed'}
            and history[-1]['projection'].get('status') in {'waiting', 'mapped', 'unclocked'}
            and entry['raw_hash'] == digest(entry['raw']) and entry['projection_hash'] == digest(entry['projection']))
        need(unsent.verify_retry_receipt_origin(db, issuer, row, origin))
        jobs, audits = unsent._rows(db, issuer, actual)
        sender = [candidate for candidate in jobs if candidate.id == origin['job_id']]
        need(len(sender) == 1 and sender[0].status == 'succeeded' and sender[0].lease_until is None
            and sender[0].data.get('actor_id') == origin['actor_id'] == row.actor_id
            and sender[0].data.get('acquisition_id') == actual.id
            and sender[0].lease_token == origin['lease_token']
            and type(sender[0].data.get('finished_at')) is str and bool(sender[0].data['finished_at'])
            and sum(candidate.id == failed.id for candidate in jobs) == 1 and failed.id != sender[0].id)
        unsent._normal_retry(sender[0], actual)
        proofs = unsent._prior_proofs(pin, sender[0].id, unsent._facts(jobs), unsent._audit_facts(audits),
            exchange_created_at=row.created_at)
        need(bool(proofs) and any(anchor.id in proofs and anchor.action in {unsent.UNSENT, unsent.PRE_UNSENT}
            and anchor.data.get('job_id') == failed.id for anchor in audits))
        return True
    except FAILURES:
        return False


def _unresolved_failure(task, bindings, observations, job, *, db=None, p=None):
    if job.status not in {'failed', 'blocked'}:
        return False
    if job.kind == 'check_native_business_goal':
        latest = task.data.get('native_goal_check_job_pin') or {}
        timer = task.data.get('native_goal_timer') or {}
        return job.id in {latest.get('job_id'), timer.get('job_id')}
    if job.kind == 'process_http_mapped_observation_v177':
        return True  # This original processing Job must actually commit success.
    if job.kind == 'read_http_observation_receipt_v177':
        exchange = next(row['exchange'] for row in bindings.values() if row['exchange'] is not None
            and row['exchange'].id == job.data['exchange_id'])
        return exchange.status == 'awaiting_receipt' and exchange.data['active_read_job_id'] == job.id
    identity = job.data['acquisition_id'] if job.kind == 'dispatch_acquisition' else job.data['settlement']['acquisition_id']
    if job.kind == 'dispatch_acquisition' and db is not None and p is not None:
        if (_pending_unsent_resume(db, p, task, bindings[identity], job)
                or _accepted_unsent_resume(db, p, task, bindings[identity], job)):
            return False
    return bindings[identity]['acquisition'].status not in {'succeeded', 'cancelled', 'rejected'}


def discover(workspace_id, instance_id, task_id, *, family, limit=20, stop_event=None):
    _scope(workspace_id, instance_id, task_id, family, limit)
    result = _result(workspace_id, instance_id, task_id, 'discovery')
    if _stopped(stop_event):
        result['state'] = 'stopped'; return result
    try:
        with _reader() as db:
            p, task, bindings, observations, jobs = _inventory(db, workspace_id, instance_id, task_id)
            current = db.scalar(select(func.clock_timestamp()))
            for job in sorted(jobs, key=lambda row: (row.available_at, row.id)):
                ready = job.available_at <= current and (job.status == 'queued' or job.status == 'running'
                    and (job.lease_until is None or job.lease_until < current))
                result['jobs'].append({'job_id': job.id, 'kind': job.kind, 'status': job.status, 'ready': ready})
                if _unresolved_failure(task, bindings, observations, job, db=db, p=p):
                    result['blocked'].append({'job_id': job.id, 'state': job.status, 'reason': ERROR})
            for binding in bindings.values():
                exchange = binding['exchange']
                acquisition = binding['acquisition']
                result['acquisitions'].append({'acquisition_id': acquisition.id, 'status': acquisition.status,
                    'plan_id': binding['plan'].id, 'plan_status': binding['plan'].status})
                if acquisition.status in {'blocked_connector', 'blocked_resource'} or acquisition.status == 'unknown' and exchange is None:
                    result['blocked'].append({'acquisition_id': acquisition.id,
                        'state': acquisition.status, 'reason': '原补采受阻或原派发结果未知；原号保留，未重发设备请求'})
                elif acquisition.status not in {'succeeded', 'cancelled', 'rejected'}:
                    result['waiting'].append({'acquisition_id': acquisition.id, 'state': acquisition.status})
                if exchange is not None:
                    result['polls'].append({'acquisition_id': binding['acquisition'].id, 'exchange_id': exchange.id,
                        **_poll_state(exchange, jobs, current)})
            for identity, row in observations.items():
                event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
                    m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + identity))
                recorded = False
                if event is not None and event.status == 'received' and row['process'].status == 'succeeded':
                    from .instance_data_receipts import observation_receipt
                    receipt = observation_receipt(db, p, row['observation']).get('data_reception')
                    expected = _receipt_record(task, row, event, receipt)
                    traces = _rows(db, select(m.Audit).where(m.Audit.workspace_id == workspace_id,
                        m.Audit.action == RECEIVED, m.Audit.object_id == event.id), 2)
                    if traces:
                        need(len(traces) == 1 and traces[0].actor_id == expected['origin_actor_id']
                            and traces[0].data == expected, ACK_ERROR)
                        recorded = True
                    result['received_count'] += 1
                result['observations'].append({'observation_id': identity, 'processing_job_id': row['process'].id,
                    'processing_status': row['process'].status, 'status': row['observation'].status,
                    'event_id': event.id if event is not None else None, 'event_status': event.status if event is not None else None,
                    'receiver_recorded': recorded})
            result['inventory'] = 'bounded_durable_snapshot'
            if not bindings:
                result['waiting'].append({'state': 'waiting_resense_origin',
                    'reason': '尚无可核对的原补采服务登记；等待原图流程，不借对象范围领取其他补采'})
            if any(row['state'].startswith('blocked') for row in result['polls']):
                result['blocked'].extend(row for row in result['polls'] if row['state'].startswith('blocked'))
            result['waiting'].extend(row for row in result['polls'] if row['state'].startswith('waiting'))
            result['state'] = 'blocked' if result['blocked'] else 'waiting' if result['waiting'] else 'idle'
    except FAILURES as error:
        result['state'] = 'blocked'
        result['blocked'].append({'state': 'blocked_integrity',
            'reason': CAPACITY if isinstance(error, HTTPException) and error.detail == CAPACITY else ERROR})
    return result


def _queue_poll(workspace_id, instance_id, task_id, acquisition_id):
    from .worker import actor
    from .relations import lock_current_actor
    from .http_observation_worker_v177 import queue_read, _lock_acquisition
    from .http_observation_exchange_v177 import checked_store
    with Session(engine()) as db:
        _p, _task_row, bindings, _observations, jobs = _inventory(db, workspace_id, instance_id, task_id)
        need(acquisition_id in bindings)
        binding = bindings[acquisition_id]; exchange = binding['exchange']; need(exchange is not None)
        p = actor(db, workspace_id, exchange.actor_id)
        # Match queue_read's Actor -> Gap/Task/Acquisition -> Exchange order.
        # Re-read the durable budget under that same lock, including concurrent
        # independent or manual GET scheduling, before reserving another read.
        lock_current_actor(db, p)
        acquisition = _lock_acquisition(db, p, acquisition_id)
        exchange = checked_store(db, p, exchange.id, lock=True)
        reads = _rows(db, select(m.Job).where(m.Job.workspace_id == workspace_id,
            m.Job.id.in_([row['job_id'] for row in exchange.data['read_jobs']])))
        gate = _poll_state(exchange, reads, db.scalar(select(func.clock_timestamp())))
        if gate['state'] != 'ready':
            return None
        _exchange, job = queue_read(db, p, acquisition)
        db.commit()
        return job.id if job is not None else None


def tick(workspace_id, instance_id, task_id, *, family, limit=20, stop_event=None):
    """One bounded scheduler tick; never performs a data receipt or acceptance."""
    value = discover(workspace_id, instance_id, task_id, family=family, limit=limit, stop_event=stop_event)
    value['mode'] = 'jobs'
    if value['inventory'] == 'not_read' or _stopped(stop_event):
        return value
    from .worker import actor, process_once
    for candidate in [row for row in value['jobs'] if row['ready']][:limit]:
        if _stopped(stop_event):
            value['state'] = 'stopped'; return value
        try:
            # Fresh originals before the exact worker obtains its own lease.
            with _reader() as db:
                _p, _task_row, _bindings_rows, _observations, jobs = _inventory(db, workspace_id, instance_id, task_id)
                matching = [job for job in jobs if job.id == candidate['job_id'] and job.kind == candidate['kind']]
                need(len(matching) == 1)
                actor(db, workspace_id, matching[0].data['actor_id'])
            if process_once(job_id=candidate['job_id'], kinds=[candidate['kind']], deliver=False,
                    workspace_id=workspace_id, _stop_event=stop_event):
                value.update(state='processed', processed=True, job_id=candidate['job_id'])
                return value
        except FAILURES:
            value['blocked'].append({'job_id': candidate['job_id'], 'state': 'blocked_execution', 'reason': ERROR})
    for poll in [row for row in value['polls'] if row['state'] == 'ready'][:limit]:
        if _stopped(stop_event):
            value['state'] = 'stopped'; return value
        try:
            queued = _queue_poll(workspace_id, instance_id, task_id, poll['acquisition_id'])
            if queued is not None:
                value.update(state='read_queued', read_queued=queued); return value
        except FAILURES:
            value['blocked'].append({'acquisition_id': poll['acquisition_id'], 'state': 'blocked_read', 'reason': ERROR})
    if value['blocked']:
        value['state'] = 'blocked'
    return value


def _receipt_record(task, original, event, receipt):
    need(type(receipt) is dict and receipt.get('instance_id') == task.data['instance_id']
        and receipt.get('release_id') == task.data['instance_release_id']
        and receipt.get('observation_id') == original['observation'].id, ACK_ERROR)
    simulation = receipt.get('simulation_origin')
    need(type(simulation) is dict and simulation.get('simulation') is True
        and simulation.get('test_only') is True and simulation.get('field_verified') is False
        and simulation.get('production_activation') is False, ACK_ERROR)
    proof = original['proof']; exchange = original['exchange']
    return {'schema': 'http-observation-task-receiver-v270', 'family': FAMILY,
        'task_id': task.id, 'instance_id': task.data['instance_id'],
        'observation_id': original['observation'].id, 'event_id': event.id,
        'acquisition_id': proof['acquisition_id'], 'exchange_id': exchange.id,
        'dispatch_job_id': exchange.dispatch_job_id, 'processing_job_id': original['process'].id,
        'receipt_hash': digest(receipt), 'origin_actor_id': exchange.actor_id,
        'simulation': True, 'test_only': True, 'field_verified': False, 'production_activation': False}


def _receipt(db, workspace_id, instance_id, task_id, observation_id):
    from .instance_data_receipts import observation_receipt
    p, task, _bindings_rows, observations, _jobs = _inventory(db, workspace_id, instance_id, task_id)
    need(observation_id in observations, ACK_ERROR)
    original = observations[observation_id]
    need(original['process'].status == 'succeeded' and original['observation'].status == 'processed', ACK_ERROR)
    event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
        m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation_id))
    need(event is not None and event.status == 'received', ACK_ERROR)
    receipt = observation_receipt(db, p, original['observation']).get('data_reception')
    return original, event, _receipt_record(task, original, event, receipt)


def _record_receipt(workspace_id, instance_id, task_id, observation_id):
    from .services import audit
    with Session(engine()) as db:
        # Serialize only the provenance audit with the same original Event.
        # The real receiver has already independently committed its actual ACK.
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation_id).with_for_update())
        need(event is not None, ACK_ERROR)
        _original, actual, record = _receipt(db, workspace_id, instance_id, task_id, observation_id)
        need(actual.id == event.id, ACK_ERROR)
        anchors = _rows(db, select(m.Audit).where(m.Audit.workspace_id == workspace_id,
            m.Audit.action == RECEIVED, m.Audit.object_id == event.id), 2)
        if anchors:
            need(len(anchors) == 1 and anchors[0].actor_id == record['origin_actor_id']
                and anchors[0].data == record, ACK_ERROR)
        else:
            # Attribution only, never passed to the independent receiver or an
            # execution gate. No synthetic data_service account is introduced.
            p = Principal(record['origin_actor_id'], workspace_id, 'viewer', 'http-task-receiver-audit-v270')
            audit(db, p, RECEIVED, event.id, record)
        db.commit()
        return record


def receive(workspace_id, instance_id, task_id, *, family, limit=20, stop_event=None):
    """Independent receiver tick: original receiver, then cold real ACK proof."""
    value = discover(workspace_id, instance_id, task_id, family=family, limit=limit, stop_event=stop_event)
    value['mode'] = 'receipts'
    if value['inventory'] == 'not_read' or _stopped(stop_event):
        return value
    from .worker import deliver_events
    # Cold verification of existing recorded receipts occurred in discover.
    # Prioritize remaining originals so an acknowledged first page cannot
    # starve a later page across successive independent receiver ticks.
    for row in [item for item in value['observations'] if not item.get('receiver_recorded', False)][:limit]:
        if _stopped(stop_event):
            value['state'] = 'stopped'; return value
        identity = row['observation_id']
        if row['processing_status'] != 'succeeded' or row['status'] != 'processed' or row['event_id'] is None:
            value['waiting'].append({'observation_id': identity, 'state': 'waiting_original_process'})
            continue
        if row['event_status'] == 'blocked_delivery':
            value['blocked'].append({'observation_id': identity, 'state': 'blocked_delivery', 'reason': ACK_ERROR})
            continue
        try:
            if row['event_status'] == 'pending_delivery':
                with _reader() as db:
                    _p, _task_row, _bindings_rows, originals, _jobs = _inventory(
                        db, workspace_id, instance_id, task_id)
                    need(identity in originals and originals[identity]['process'].status == 'succeeded'
                        and originals[identity]['observation'].status == 'processed', ACK_ERROR)
                # This is the actual independently transactional receiver. It
                # chooses its original data-plan-receiver attribution itself.
                deliver_events(workspace_id=workspace_id, observation_id=identity, event_kind='observation.data')
            if _stopped(stop_event):
                value['state'] = 'stopped'; return value
            with _reader() as db:
                _original, _event, _record = _receipt(db, workspace_id, instance_id, task_id, identity)
            value['received'].append(_record_receipt(workspace_id, instance_id, task_id, identity))
        except FAILURES:
            value['blocked'].append({'observation_id': identity, 'state': 'blocked_receipt', 'reason': ACK_ERROR})
    value['state'] = 'blocked' if value['blocked'] else 'received' if value['received'] or value['received_count'] else 'waiting'
    return value


def run(workspace_id, instance_id, task_id, *, family, mode, stop_event=None, on_result=None):
    """Use one process for jobs and another for receipts; Root owns CLI wiring."""
    _scope(workspace_id, instance_id, task_id, family)
    need(type(mode) is str and mode in MODES)
    need(on_result is None or callable(on_result))
    stop = stop_event if stop_event is not None else threading.Event()
    need(threading.current_thread() is threading.main_thread() or stop_event is not None)
    previous = {}
    if not stop.is_set() and threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda _signal, _frame: stop.set())
    action = tick if mode == 'jobs' else receive
    last = None
    try:
        while not stop.is_set():
            try:
                value = action(workspace_id, instance_id, task_id, family=family, stop_event=stop)
            except Exception:
                value = _result(workspace_id, instance_id, task_id, mode)
                value.update(state='blocked', blocked=[{'state': 'blocked_runtime', 'reason': ERROR}])
            fingerprint = digest(value)
            if fingerprint != last:
                if on_result is not None:
                    on_result(value)
                else:
                    print('原HTTP任务后台状态：' + value['state'], flush=True)
                last = fingerprint
            stop.wait(2 if value['blocked'] else 1)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0
