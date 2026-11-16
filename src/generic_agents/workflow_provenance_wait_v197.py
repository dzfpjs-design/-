"""Persist a same-Job wait before any graph reservation or Invocation.

Scheduling checks immutable wait evidence, never authorizes a capability. The
normal worker repeats current source/IAM/Goal checks after waking the same Job.
Caller commits preflight/schedule_once; poll_once owns its maintenance Session.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import func, select

from .contracts import digest
from .models import Audit, InstanceRelease, Invocation, Job, Task, now
from .services import audit, validate
from .leases import ownership
from . import workflow_graph_scope as scope
from . import workflow_task_context_v196 as reader
from . import business_flow_review_store as safe

KEY = 'workflow_provenance_wait_v197'
SCHEMA = 'workflow-provenance-wait-ledger-v197'
RECORD = 'workflow-provenance-wait-v197'
WAIT = 'workflow.graph.provenance_wait_v197'
WAKE = 'workflow.graph.provenance_wake_v197'
PHASE = 'waiting_provenance'
MAX_WAITS = 64
MAX_DEPENDENCIES = 16
MAX_BYTES = 2 * 1024 * 1024
ERROR = '原图任务的来源等待、实际依赖或独立审计无法核对；未预留额度或创建新调用'
IDENTITY_KEYS = ('task_id', 'actor_id', 'activation_id', 'graph_hash', 'cancel_epoch', 'input_hash', 'invocation_key')
RECORD_KEYS = {'schema', 'version', 'previous_hash', 'job_id', 'task_id', 'actor_id', 'activation_id',
    'graph_hash', 'cancel_epoch', 'input_hash', 'invocation_key', 'capability_id', 'capability_hash',
    'goal_context_hash', 'attempts_at_wait', 'dependencies', 'record_hash'}
DEPENDENCY_KEYS = {'kind', 'job_id', 'job_kind', 'idempotency_key', 'actor_id', 'task_id',
    'source_path', 'source_hash', 'invocation_id', 'invocation_output_hash', 'pin'}


def need(ok):
    if not ok: raise HTTPException(409, ERROR)


def _same(a, b): return safe._same(a, b)


def _family(cap):
    execution = cap.data.get('execution', {})
    if execution.get('adapter') == 'business_goal_ready_v288':
        from .business_goal_ready_runtime_v288 import _manifest_shape
        _manifest_shape(cap.data)
        return 'goal_ready_reasoning_v288'
    if execution.get('operation') == 'plan_approved_goal_data_v288':
        from .workflow_goal_data_route_runtime_v288 import validate_manifest
        validate_manifest(cap.data)
        return 'goal_data_prepare_v288'
    if execution.get('operation') == 'create_goal_evaluation_packet_v288':
        from .workflow_goal_evaluation_packet_v288 import validate_manifest
        validate_manifest(cap.data)
        return 'goal_evaluation_packet_v288'
    if execution.get('operation') == 'plan_business_decision_review_v285':
        from .workflow_decision_tool_planner_v285 import validate_manifest
        validate_manifest(cap.data)
        return 'decision_plan_v285'
    if execution.get('operation') == 'plan_business_decision_v277':
        from .workflow_decision_tool_planner_v277 import validate_manifest
        validate_manifest(cap.data)
        return 'decision_plan_v277'
    if execution.get('operation') == 'execute_registered_tool_plan':
        from .workflow_registered_tool_plan_v201 import validate_manifest
        validate_manifest(cap.data)
        return 'registered_tool_plan'
    if execution.get('operation') == 'adjust_monitoring_schedule':
        from .workflow_monitor_schedule_v200 import validate_manifest
        validate_manifest(cap.data)
        return 'monitor_schedule'
    if execution.get('adapter') == 'task_evidence_packet':
        from .workflow_task_packet_v199 import validate_manifest
        validate_manifest(cap.data)
        return 'reader'
    if execution.get('handler') == reader.HANDLER:
        need(_same(execution, {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False}))
        return 'reader'
    if execution.get('handler') == 'workflow_task_originals_dedup_v219':
        from .workflow_data_quality_v219 import validate_manifest
        validate_manifest(cap.data)
        return 'originals_dedup_v219'
    if execution.get('handler') == 'workflow_task_quality_archive_v220':
        from .workflow_quality_archive_v220 import validate_manifest
        validate_manifest(cap.data)
        return 'quality_archive_v220'
    if execution.get('handler') == 'require_observation_context_v197':
        need(_same(execution, {'adapter': 'builtin', 'handler': 'require_observation_context_v197', 'side_effect': False}))
        return 'require_context'
    if execution.get('operation') == 'resense_from_context':
        need(_same(execution, {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': 'resense_from_context', 'side_effect': True}))
        return 'context_service'
    if execution.get('adapter') == 'business_reasoning':
        need(execution.get('side_effect') is False)
        return 'reasoning'
    return None


def _original_call(db, job):
    return db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
        Invocation.idempotency_key == job.data['invocation_key']).execution_options(populate_existing=True))


def _at(value, pointer):
    if pointer == '/context': return value['context']
    parts = pointer.split('/')
    need(len(parts) == 4 and parts[:2] == ['', 'candidates'] and parts[2].isdigit()
        and str(int(parts[2])) == parts[2] and parts[3] == 'context')
    return value['candidates'][int(parts[2])]['context']


def _wait_audit(job, record):
    return {'schema': RECORD, 'job_id': job.id, 'task_id': record['task_id'],
        'record_hash': record['record_hash'], 'record': copy.deepcopy(record)}


def _wake_audit(job, record):
    return {'schema': RECORD, 'job_id': job.id, 'task_id': record['task_id'], 'record_hash': record['record_hash']}


def _inventory(db, task, job):
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.action.in_([WAIT, WAKE]), Audit.object_id == job.id)))
    ledger = job.data.get(KEY)
    if ledger is None:
        need(not events and 'provenance_wait_hash' not in task.data.get('graph_jobs', {}).get(job.data.get('activation_id'), {}))
        return {'schema': SCHEMA, 'history': [], 'active_hash': None, 'woken_hashes': []}
    safe._bytes(ledger, MAX_BYTES)
    need(type(ledger) is dict and set(ledger) == {'schema', 'history', 'active_hash', 'woken_hashes'}
        and ledger['schema'] == SCHEMA and type(ledger['history']) is list
        and 0 < len(ledger['history']) <= MAX_WAITS and type(ledger['woken_hashes']) is list
        and len(ledger['woken_hashes']) == len(set(ledger['woken_hashes'])))
    hashes = []; previous = None; used = set()
    for version, record in enumerate(ledger['history'], 1):
        need(type(record) is dict and set(record) == RECORD_KEYS and record['schema'] == RECORD
            and type(record['version']) is int and record['version'] == version
            and record['previous_hash'] == previous and record['job_id'] == job.id
            and all(_same(record[key], job.data[key]) for key in IDENTITY_KEYS)
            and record['task_id'] == task.id and type(record['attempts_at_wait']) is int
            and 0 <= record['attempts_at_wait'] <= job.attempts
            and record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'})
            and type(record['dependencies']) is list and 0 < len(record['dependencies']) <= MAX_DEPENDENCIES)
        for dependency in record['dependencies']:
            need(type(dependency) is dict and set(dependency) == DEPENDENCY_KEYS
                and dependency['kind'] in {'native_goal_check', 'context_producer', 'reasoning_producer',
                    'registered_tool_reasoning_producer', 'decision_reasoning_producer_v277', 'decision_reasoning_producer_v285', 'originals_quality_producer_v219',
                    'quality_archive_dedup_producer_v220', 'goal_data_reader_producer_v288',
                    'goal_data_barrier_producer_v288', 'goal_ready_goal_check_v288'}
                and dependency['job_id'] != job.id and dependency['task_id'] == task.id)
            safe.identity(dependency['job_id']); safe.identity(dependency['actor_id'])
            need(type(dependency['source_hash']) is str and safe.SHA.fullmatch(dependency['source_hash']) is not None)
        matches = [event for event in events if event.action == WAIT and event.data.get('record_hash') == record['record_hash']]
        need(len(matches) == 1 and matches[0].actor_id == record['actor_id']
            and _same(matches[0].data, _wait_audit(job, record)))
        used.add(matches[0].id)
        if record['record_hash'] in ledger['woken_hashes']:
            matches = [event for event in events if event.action == WAKE and event.data.get('record_hash') == record['record_hash']]
            need(len(matches) == 1 and matches[0].actor_id == record['actor_id']
                and _same(matches[0].data, _wake_audit(job, record)))
            used.add(matches[0].id)
        hashes.append(record['record_hash']); previous = record['record_hash']
    need(set(ledger['woken_hashes']).issubset(hashes) and len(used) == len(events)
        and used == {event.id for event in events}
        and ledger['active_hash'] in {None, hashes[-1]}
        and set(hashes) == set(ledger['woken_hashes']) | ({ledger['active_hash']} if ledger['active_hash'] else set())
        and ledger['active_hash'] not in ledger['woken_hashes'])
    transport = task.data['graph_jobs'][job.data['activation_id']]
    need(transport.get('provenance_wait_hash') == hashes[-1])
    return copy.deepcopy(ledger)


def _job(db, workspace_id, identifier):
    row = db.scalar(select(Job).where(Job.workspace_id == workspace_id, Job.id == identifier)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def _dependency(db, task, kind, pin, *, source_path=None, source_hash=None):
    job = _job(db, task.workspace_id, pin['job_id'])
    need(job.data.get('task_id') == task.id and job.data.get('actor_id') == pin['actor_id'])
    if kind == 'native_goal_check':
        need(job.kind == 'check_native_business_goal' and job.idempotency_key == pin['idempotency_key']
            and job.data.get('context_hash') == pin['context_hash'])
        call_id = None; output_hash = None
    elif kind == 'goal_ready_goal_check_v288':
        need(job.kind == 'check_native_business_goal' and job.idempotency_key == pin['idempotency_key']
            and job.data.get('context_hash') == pin['context_hash']
            and job.data.get('goal_evaluation_ref_v288') == pin['evaluation_ref']
            and job.data.get('goal_evaluation_producer_job_v288') == pin['evaluation_producer_job_id'])
        call_id = None; output_hash = None
    else:
        need(job.kind == scope.KIND)
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == task.workspace_id,
            Invocation.id == pin['invocation_id']).execution_options(populate_existing=True))
        need(call is not None and call.status == 'succeeded' and call.data.get('job_id') == job.id
            and call.idempotency_key == job.data.get('invocation_key')
            and digest(call.idempotency_key) == pin['request_key_hash'])
        call_id = call.id; output_hash = digest(call.data['output'])
    return {'kind': kind, 'job_id': job.id, 'job_kind': job.kind, 'idempotency_key': job.idempotency_key,
        'actor_id': job.data['actor_id'], 'task_id': task.id, 'source_path': source_path,
        'source_hash': digest(pin) if source_hash is None else source_hash,
        'invocation_id': call_id, 'invocation_output_hash': output_hash, 'pin': copy.deepcopy(pin)}


def _dependencies(db, p, task, family, inputs):
    dependencies = []
    if family == 'reader':
        if task.data.get('native_goal_query_id') is not None: return []
        pin = task.data.get('native_goal_check_job_pin')
        need(type(pin) is dict and set(pin) == {'job_id', 'idempotency_key', 'context_hash', 'actor_id'}
            and task.data.get('native_goal_check_job_pin_hash') == digest(pin)
            and pin['context_hash'] == digest(task.data.get('native_goal_context'))
            and pin['context_hash'] == task.data.get('native_goal_context_hash'))
        dependency = _dependency(db, task, 'native_goal_check', pin)
        need(_job(db, task.workspace_id, dependency['job_id']).status in {'queued', 'running'})
        return [dependency]
    if family in {'context_service', 'require_context'}:
        values = [('/context', inputs['context'])] if inputs['context'] is not None else []
    else:
        values = [('/candidates/' + str(index) + '/context', value['context'])
            for index, value in enumerate(inputs['candidates']) if value['context'] is not None]
    for path, value in values:
        try: reader.checked_context(db, p, value, task=task, dispatch=False)
        except reader.ProducerPending as pending:
            need(value['producer']['job_id'] == pending.job_id and value['producer']['invocation_id'] == pending.invocation_id)
            dependencies.append(_dependency(db, task, 'context_producer', value['producer'],
                source_path=path, source_hash=digest(value)))
    need(len(dependencies) <= MAX_DEPENDENCIES)
    return dependencies


def _monitor_dependencies(db, p, task, job, inputs, *, checker=None, kind='reasoning_producer', field='reasoning_result'):
    if checker is None:
        from .workflow_monitor_schedule_v200 import producer_wait_pin as checker
    try:
        checker(db, p, task, job, inputs)
    except reader.ProducerPending as pending:
        original = _job(db, task.workspace_id, pending.job_id)
        need(original.status == 'running' and original.data.get('task_id') == task.id)
        pin = {'job_id': original.id, 'invocation_id': pending.invocation_id,
            'actor_id': original.data['actor_id'],
            'request_key_hash': digest(original.data['invocation_key'])}
        return [_dependency(db, task, kind, pin,
            source_path='/' + field, source_hash=digest(inputs[field]))]
    return []


def _quality_dependencies(db, p, task, job, inputs):
    """Only a completely proved n3 output with its own still-running Job waits.

    The V219 checker validates the consumer's actual graph output reference and
    independent producer proofs before raising ProducerPending. Its exception
    alone is not a producer identity, a result or permission to dispatch.
    """
    from .workflow_data_quality_v219 import producer_wait_pin
    try:
        producer_wait_pin(db, p, task, job, inputs)
    except reader.ProducerPending as pending:
        report = inputs['quality_report']; pin = report['producer']
        need(pin['workspace_id'] == task.workspace_id == p.workspace_id and pin['task_id'] == task.id
            and pin['job_id'] == pending.job_id and pin['invocation_id'] == pending.invocation_id)
        original = _job(db, task.workspace_id, pending.job_id)
        need(original.status == 'running' and original.data.get('task_id') == task.id)
        return [_dependency(db, task, 'originals_quality_producer_v219', pin,
            source_path='/quality_report', source_hash=digest(report))]
    return []


def _archive_dependencies(db, p, task, job, inputs):
    """Only the completely proved dedup producer can delay this archive Job.

    The archive checker cold-verifies both reports, the original graph output
    bindings and the n4 input chain first. A nonterminal n3 is an invalid chain,
    never a reason to register an n4 dependency or to make another invocation.
    """
    from .workflow_quality_archive_v220 import producer_wait_pin
    try:
        producer_wait_pin(db, p, task, job, inputs)
    except reader.ProducerPending as pending:
        report = inputs['dedup_report']; pin = report['producer']
        need(pin['workspace_id'] == task.workspace_id == p.workspace_id and pin['task_id'] == task.id
            and pin['job_id'] == pending.job_id and pin['invocation_id'] == pending.invocation_id)
        original = _job(db, task.workspace_id, pending.job_id)
        need(original.status == 'running' and original.data.get('task_id') == task.id)
        return [_dependency(db, task, 'quality_archive_dedup_producer_v220', pin,
            source_path='/dedup_report', source_hash=digest(report))]
    return []


def _locked_preflight(db, p, task, job, cap, inputs):
    locked_task = db.scalar(select(Task).where(Task.workspace_id == p.workspace_id, Task.id == task.id)
        .with_for_update().execution_options(populate_existing=True))
    lease = ownership(db)
    current = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == lease.get('lease_token'),
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(locked_task is not None and current is not None and lease.get('job_id') == job.id and lease.get('lease_token'))
    actual_task, release, business, graph, state, activation, actual_node = scope._frozen(
        db, p, task.id, current.data.get('activation_id'))
    scope._job_matches(actual_task, current, state, activation, actual_node)
    need(actual_task.id == locked_task.id and actual_node['actor_id'] == p.user_id
        and activation['status'] == 'ready' and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] == current.data['cancel_epoch']
        and _same(current.data['inputs'], inputs) and digest(inputs) == current.data['input_hash']
        and actual_node['execution']['capability_id'] == cap.id
        and actual_node['execution']['capability_hash'] == cap.content_hash)
    return actual_task, current, actual_node


def _lock_wait_registration(db, p, task, job, node, inputs):
    """Lock the original Job only after all potentially long dependency reads.

    The Task cancel lock has been held since _locked_preflight. No source
    qualification, provider, continuation or lease renewal runs in this short
    write section. The ordinary COMMIT fence still owns the actual deadline.
    """
    expected_data = copy.deepcopy(job.data)
    expected_runtime = copy.deepcopy(task.data['graph_runtime'])
    lease = ownership(db)
    current_task = db.scalar(select(Task).where(Task.workspace_id == p.workspace_id, Task.id == task.id)
        .with_for_update().execution_options(populate_existing=True))
    current = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == lease.get('lease_token'),
        Job.lease_until > func.clock_timestamp()).with_for_update().execution_options(populate_existing=True))
    need(current_task is not None and current is not None and lease.get('job_id') == job.id
        and bool(lease.get('lease_token')) and _same(current.data, expected_data)
        and _same(current_task.data['graph_runtime'], expected_runtime))
    state = current_task.data['graph_runtime']; activation = state['activations'][current.data['activation_id']]
    scope._job_matches(current_task, current, state, activation, node)
    need(state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['node_id'] == node['id'] and node['actor_id'] == p.user_id
        and activation['epoch'] == state['cancel_epoch'] == current.data['cancel_epoch']
        and _same(current.data['inputs'], inputs) and current.data['input_hash'] == digest(inputs)
        and _original_call(db, current) is None)
    from .workflow_graph_goal_guard_v133 import reservations
    need(not any(item['invocation_key'] == current.data['invocation_key'] for item in reservations(current_task)))
    transport = current_task.data['graph_jobs'][current.data['activation_id']]
    need(not set(transport) & {'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'})
    return current_task, current


def preflight(db, p, task, job, node, cap, inputs):
    """True means stop immediately and let the caller commit this same Job."""
    family = _family(cap)
    if family is None: return False
    validate(cap.data['input_schema'], inputs)
    task, job, actual_node = _locked_preflight(db, p, task, job, cap, inputs)
    need(_same(actual_node, node) and _original_call(db, job) is None)
    ledger = _inventory(db, task, job); need(ledger['active_hash'] is None)
    from .workflow_resense_pre_dispatch_recovery_v290 import KEY as resense_recovery_key
    if family == 'context_service' and resense_recovery_key in job.data:
        # A retained graph intent cannot enter the ordinary first-dispatch
        # waiter. Only this exact V288 original Job certificate cold-checks
        # matured dependencies, source producer, current IAM/175/Goal and the
        # full no-SDK/no-authorization chain; it neither registers a new wait
        # nor starts an SDK, query or acquisition.
        from .workflow_resense_pre_dispatch_recovery_v290 import preflight as resense_preflight
        return resense_preflight(db,p,task,job,node,cap,inputs)
    from .workflow_graph_goal_guard_v133 import reservations
    reserved = reservations(task)
    need(not any(item['invocation_key'] == job.data['invocation_key'] for item in reserved))
    transport = task.data['graph_jobs'][job.data['activation_id']]
    need(not set(transport) & {'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'})
    if family == 'goal_ready_reasoning_v288':
        from .workflow_goal_ready_goal_barrier_v288 import waiting_pin, KIND, FIELD
        pin = waiting_pin(db, p, task, job, inputs)
        dependencies = [] if pin is None else [_dependency(db, task, KIND, pin,
            source_path='/' + FIELD, source_hash=digest(inputs[FIELD]))]
    elif family == 'goal_data_prepare_v288':
        from .workflow_goal_data_route_runtime_v288 import producer_wait_pin
        dependencies = _monitor_dependencies(db,p,task,job,inputs,checker=producer_wait_pin,
            kind='goal_data_reader_producer_v288',field='context_result')
    elif family == 'goal_evaluation_packet_v288':
        from .workflow_goal_evaluation_packet_v288 import producer_wait_pin
        dependencies = _monitor_dependencies(db,p,task,job,inputs,checker=producer_wait_pin,
            kind='goal_data_barrier_producer_v288',field='local_plan')
    elif family == 'decision_plan_v285':
        from .workflow_decision_tool_planner_v285 import producer_wait_pin
        dependencies = _monitor_dependencies(db,p,task,job,inputs,checker=producer_wait_pin,
            kind='decision_reasoning_producer_v285',field='decision_result')
    elif family == 'decision_plan_v277':
        from .workflow_decision_tool_planner_v277 import producer_wait_pin
        dependencies = _monitor_dependencies(db,p,task,job,inputs,checker=producer_wait_pin,
            kind='decision_reasoning_producer_v277',field='decision_result')
    elif family == 'registered_tool_plan':
        from .workflow_registered_tool_plan_v201 import producer_wait_pin
        dependencies = _monitor_dependencies(db,p,task,job,inputs,checker=producer_wait_pin,
            kind='registered_tool_reasoning_producer')
    elif family == 'monitor_schedule':
        dependencies = _monitor_dependencies(db,p,task,job,inputs)
    elif family == 'originals_dedup_v219':
        dependencies = _quality_dependencies(db,p,task,job,inputs)
    elif family == 'quality_archive_v220':
        dependencies = _archive_dependencies(db,p,task,job,inputs)
    else:
        dependencies = _dependencies(db,p,task,family,inputs)
    if not dependencies: return False
    task, job = _lock_wait_registration(db, p, task, job, actual_node, inputs)
    ledger = _inventory(db, task, job); need(ledger['active_hash'] is None)
    need(len(ledger['history']) < MAX_WAITS)
    record = {'schema': RECORD, 'version': len(ledger['history']) + 1,
        'previous_hash': ledger['history'][-1]['record_hash'] if ledger['history'] else None,
        'job_id': job.id, **{key: copy.deepcopy(job.data[key]) for key in IDENTITY_KEYS},
        'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'goal_context_hash': digest(task.data['native_goal_context']) if task.data.get('native_goal_context') is not None else None,
        'attempts_at_wait': job.attempts, 'dependencies': dependencies}
    record['record_hash'] = digest(record)
    ledger['history'].append(record); ledger['active_hash'] = record['record_hash']; safe._bytes(ledger, MAX_BYTES)
    job.data = {**job.data, KEY: ledger}
    transports = copy.deepcopy(task.data['graph_jobs'])
    transports[job.data['activation_id']] = {**transport, 'phase': PHASE,
        'provenance_wait_hash': record['record_hash'],
        'reason': '等待原资料检查或上一步结果保存完成；随后继续本次处理'}
    task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
    audit(db, p, WAIT, job.id, _wait_audit(job, record))
    # No ORM query after this assignment: preserve the worker's final lease fence.
    job.status = PHASE
    return True


def _scheduler_state(db, task, job):
    from . import workflow_graph
    from . import full_semantic_graph_v1 as pure
    release = db.scalar(select(InstanceRelease).where(InstanceRelease.workspace_id == task.workspace_id,
        InstanceRelease.id == task.data['instance_release_id']).execution_options(populate_existing=True))
    need(release is not None)
    businesses = [value for value in release.data['snapshot']['businesses'] if value['id'] == task.data['business_id']]
    need(len(businesses) == 1)
    graph = workflow_graph.graph_for(release, businesses[0]); state = pure.check_run(graph, task.data['graph_runtime'])
    activation = state['activations'][job.data['activation_id']]
    node = next(value for value in graph['nodes'] if value['id'] == activation['node_id'])
    scope._job_matches(task, job, state, activation, node)
    need(state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
        and activation['epoch'] == state['cancel_epoch'] == job.data['cancel_epoch'])
    return node


def _dependency_terminal(db, task, job, dependency):
    original = _job(db, job.workspace_id, dependency['job_id'])
    need(original.kind == dependency['job_kind'] and original.idempotency_key == dependency['idempotency_key']
        and original.data.get('task_id') == task.id and original.data.get('actor_id') == dependency['actor_id'])
    pin = dependency['pin']
    if dependency['kind'] == 'native_goal_check':
        need(_same(task.data.get('native_goal_check_job_pin'), pin)
            and task.data.get('native_goal_check_job_pin_hash') == digest(pin) == dependency['source_hash']
            and original.data.get('context_hash') == pin['context_hash'])
    elif dependency['kind'] == 'goal_ready_goal_check_v288':
        from .workflow_goal_ready_goal_barrier_v288 import ready, FIELD
        need(dependency['source_path'] == '/' + FIELD
            and digest(job.data['inputs'][FIELD]) == dependency['source_hash']
            and pin['job_id'] == original.id and dependency['invocation_id'] is None
            and dependency['invocation_output_hash'] is None)
        from .worker import actor
        principal = actor(db, task.workspace_id, job.data['actor_id'])
        if not ready(db, principal, task, job, job.data['inputs'], pin):
            return False
    elif dependency['kind'] == 'originals_quality_producer_v219':
        need(dependency['source_path'] == '/quality_report'
            and digest(job.data['inputs']['quality_report']) == dependency['source_hash'])
        from .workflow_data_quality_v219 import producer_wait_pin
        from .worker import actor
        principal = actor(db, task.workspace_id, job.data['actor_id'])
        try:
            actual_pin = producer_wait_pin(db, principal, task, job, job.data['inputs'])
        except reader.ProducerPending as pending:
            need(original.status == 'running' and pending.job_id == original.id
                and pending.invocation_id == dependency['invocation_id'])
            return False
        need(_same(actual_pin, pin))
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
            Invocation.id == dependency['invocation_id']).execution_options(populate_existing=True))
        need(call is not None and call.status == 'succeeded' and call.data.get('job_id') == original.id
            and call.idempotency_key == original.data.get('invocation_key')
            and digest(call.data.get('output')) == dependency['invocation_output_hash'])
    elif dependency['kind'] == 'quality_archive_dedup_producer_v220':
        need(dependency['source_path'] == '/dedup_report'
            and digest(job.data['inputs']['dedup_report']) == dependency['source_hash'])
        from .workflow_quality_archive_v220 import producer_wait_pin
        from .worker import actor
        principal = actor(db, task.workspace_id, job.data['actor_id'])
        try:
            actual_pin = producer_wait_pin(db, principal, task, job, job.data['inputs'])
        except reader.ProducerPending as pending:
            need(original.status == 'running' and pending.job_id == original.id
                and pending.invocation_id == dependency['invocation_id'])
            return False
        need(_same(actual_pin, pin))
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
            Invocation.id == dependency['invocation_id']).execution_options(populate_existing=True))
        need(call is not None and call.status == 'succeeded' and call.data.get('job_id') == original.id
            and call.idempotency_key == original.data.get('invocation_key')
            and digest(call.data.get('output')) == dependency['invocation_output_hash'])
    elif dependency['kind'] in {'reasoning_producer', 'registered_tool_reasoning_producer', 'decision_reasoning_producer_v277', 'decision_reasoning_producer_v285',
            'goal_data_reader_producer_v288', 'goal_data_barrier_producer_v288'}:
        if dependency['kind'] == 'goal_data_reader_producer_v288':
            field = 'context_result'
        elif dependency['kind'] == 'goal_data_barrier_producer_v288':
            field = 'local_plan'
        else:
            field = 'decision_result' if dependency['kind'] in {'decision_reasoning_producer_v277','decision_reasoning_producer_v285'} else 'reasoning_result'
        need(dependency['source_path'] == '/' + field
            and digest(job.data['inputs'][field]) == dependency['source_hash'])
        if dependency['kind'] == 'goal_data_reader_producer_v288':
            from .workflow_goal_data_route_runtime_v288 import producer_wait_pin
        elif dependency['kind'] == 'goal_data_barrier_producer_v288':
            from .workflow_goal_evaluation_packet_v288 import producer_wait_pin
        elif dependency['kind'] == 'decision_reasoning_producer_v285':
            from .workflow_decision_tool_planner_v285 import producer_wait_pin
        elif dependency['kind'] == 'decision_reasoning_producer_v277':
            from .workflow_decision_tool_planner_v277 import producer_wait_pin
        elif dependency['kind'] == 'registered_tool_reasoning_producer':
            from .workflow_registered_tool_plan_v201 import producer_wait_pin
        else:
            from .workflow_monitor_schedule_v200 import producer_wait_pin
        from .worker import actor
        principal = actor(db, task.workspace_id, job.data['actor_id'])
        try:
            actual_pin = producer_wait_pin(db, principal, task, job, job.data['inputs'])
        except reader.ProducerPending as pending:
            need(original.status == 'running' and pending.job_id == original.id
                and pending.invocation_id == dependency['invocation_id'])
            return False
        need(_same({key: actual_pin[key] for key in pin}, pin))
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
            Invocation.id == dependency['invocation_id']).execution_options(populate_existing=True))
        need(call is not None and call.status == 'succeeded' and call.data.get('job_id') == original.id
            and call.idempotency_key == original.data.get('invocation_key')
            and digest(call.data.get('output')) == dependency['invocation_output_hash'])
    else:
        value = _at(job.data['inputs'], dependency['source_path'])
        need(digest(value) == dependency['source_hash'] and _same(value['producer'], pin))
        call = db.scalar(select(Invocation).where(Invocation.workspace_id == job.workspace_id,
            Invocation.id == dependency['invocation_id']).execution_options(populate_existing=True))
        need(call is not None and call.status == 'succeeded' and call.data.get('job_id') == original.id
            and call.idempotency_key == original.data.get('invocation_key')
            and digest(call.data.get('output')) == dependency['invocation_output_hash'])
    if original.status in {'queued', 'running'}: return False
    need(original.status == 'succeeded')
    if dependency['kind'] == 'native_goal_check': need(task.data.get('native_goal_query_id') is not None)
    return True


def _reject(task, job, status, reason):
    job.status = status
    if type(job.data) is dict: job.data = {**job.data, 'error': reason}
    transports = task.data.get('graph_jobs') if task is not None and type(task.data) is dict else None
    if type(transports) is not dict: return
    matches = [key for key, value in transports.items() if type(value) is dict and value.get('job_id') == job.id]
    if len(matches) != 1: return
    changed = copy.deepcopy(transports); changed[matches[0]] = {**changed[matches[0]], 'phase': status, 'reason': reason}
    task.data = {**task.data, 'graph_jobs': changed}; task.version += 1


def can_start_after_wait(db, task, job):
    """A read-only exception for an original, never-dispatched waiting Job.

    This is not a lease/source/IAM or budget grant. The worker retains all its
    ordinary guards and reservations, including the final dispatch fence.
    """
    actual_task = db.scalar(select(Task).where(Task.workspace_id == task.workspace_id, Task.id == task.id)
        .execution_options(populate_existing=True))
    actual = db.scalar(select(Job).where(Job.workspace_id == task.workspace_id, Job.id == job.id,
        Job.kind == scope.KIND).execution_options(populate_existing=True))
    try:
        need(actual_task is not None and actual is not None and actual.status in {'queued', 'running'}
            and actual_task.status not in {'workflow_cancelled', 'workflow_stopped'}
            and not actual_task.data.get('native_goal_cancelled') and not actual_task.data.get('native_goal_timed_out'))
        ledger = _inventory(db, actual_task, actual)
        need(ledger['history'] and ledger['active_hash'] is None
            and ledger['history'][-1]['record_hash'] in ledger['woken_hashes'])
        record = ledger['history'][-1]; transport = actual_task.data['graph_jobs'][actual.data['activation_id']]
        need(transport.get('phase') in {'queued', 'running'} and transport.get('job_id') == actual.id
            and not set(transport) & {'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'}
            and _original_call(db, actual) is None
            and record['goal_context_hash'] == (digest(actual_task.data['native_goal_context'])
                if actual_task.data.get('native_goal_context') is not None else None))
        node = _scheduler_state(db, actual_task, actual)
        need(node['execution']['capability_id'] == record['capability_id']
            and node['execution']['capability_hash'] == record['capability_hash'])
        return True
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False


def _poll_scope(workspace_id, job_id):
    if workspace_id is not None: safe.identity(workspace_id)
    if job_id is not None:
        need(workspace_id is not None)
        safe.identity(job_id)


def schedule_once(db, *, workspace_id=None, job_id=None, limit=64):
    """Requeue only the existing Job. Caller owns commit; no current grant here."""
    need(type(limit) is int and 1 <= limit <= 256)
    _poll_scope(workspace_id, job_id)
    query = select(Job).where(Job.kind == scope.KIND, Job.status == PHASE,
        Job.available_at <= func.clock_timestamp()).order_by(Job.available_at, Job.id).limit(limit)
    if workspace_id is not None: query = query.where(Job.workspace_id == workspace_id)
    if job_id is not None: query = query.where(Job.id == job_id)
    count = 0
    for candidate in list(db.scalars(query)):
        task = db.scalar(select(Task).where(Task.workspace_id == candidate.workspace_id, Task.id == candidate.data.get('task_id'))
            .with_for_update().execution_options(populate_existing=True))
        job = db.scalar(select(Job).where(Job.workspace_id == candidate.workspace_id, Job.id == candidate.id,
            Job.kind == scope.KIND, Job.status == PHASE).with_for_update().execution_options(populate_existing=True))
        if job is None: continue
        try:
            need(task is not None)
            current_state = task.data.get('graph_runtime', {})
            epoch = current_state.get('cancel_epoch')
            if (task.status in {'workflow_cancelled', 'workflow_stopped'}
                    or current_state.get('status') in {'cancelled', 'stopped'}
                    or task.data.get('native_goal_cancelled') or task.data.get('native_goal_timed_out')
                    or type(epoch) is int and epoch != job.data.get('cancel_epoch')):
                _reject(task, job, 'cancelled', '原流程、目标或节点代次已停止；保留原等待证据，未创建新调用')
                continue
            ledger = _inventory(db, task, job); need(ledger['active_hash'] is not None)
            record = ledger['history'][-1]; transport = task.data['graph_jobs'][job.data['activation_id']]
            need(transport.get('phase') == PHASE and record['attempts_at_wait'] == job.attempts
                and _original_call(db, job) is None
                and record['goal_context_hash'] == (digest(task.data['native_goal_context']) if task.data.get('native_goal_context') is not None else None))
            node = _scheduler_state(db, task, job)
            need(node['execution']['capability_id'] == record['capability_id']
                and node['execution']['capability_hash'] == record['capability_hash'])
            outcomes = [_dependency_terminal(db, task, job, dependency) for dependency in record['dependencies']]
            if not all(outcomes): continue
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
            _reject(task, job, 'blocked', ERROR); continue
        ledger['active_hash'] = None; ledger['woken_hashes'].append(record['record_hash'])
        job.data = {**job.data, KEY: ledger}; job.status = 'queued'
        transports = copy.deepcopy(task.data['graph_jobs']); transports[job.data['activation_id']] = {**transport, 'phase': 'queued'}
        task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
        from .security import Principal
        # Audit attribution only; this Principal is never passed to an authority guard.
        actor = Principal(record['actor_id'], job.workspace_id, 'viewer', 'provenance-wakeup')
        audit(db, actor, WAKE, job.id, _wake_audit(job, record)); count += 1
    return count


def poll_once(workspace_id=None, *, job_id=None):
    _poll_scope(workspace_id, job_id)
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        count = schedule_once(db, workspace_id=workspace_id, job_id=job_id)
        db.commit()
        return count
