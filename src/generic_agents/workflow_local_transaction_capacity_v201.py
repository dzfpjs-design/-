"""CPU capacity only for ended, canonical graph transactions/orchestrators.

An unknown HTTP/device/model result still occupies capacity. This exception
does not settle an Invocation, renew a Job, replay a key or authorize dispatch.
Archive/packet handlers are local. The exact V201 orchestrator only submits
effects through separately counted, single-slot registered HTTP children.
"""
from fastapi import HTTPException
from jsonschema.exceptions import SchemaError
from sqlalchemy import func, select

from .contracts import Manifest, digest
from .models import (Acquisition, Audit, Capability, DataGap, DataRun, Event,
    Invocation, Job, Memory, MonitorPolicy, Observation, ObservationPlan, Task)


ACTIVE = ('running', 'unknown', 'waiting_receipt')
ENDED = frozenset({'failed', 'blocked', 'cancelled'})
AUTH_KEYS = {'epoch', 'job_id', 'lease_token', 'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
MAX_FOOTPRINT = 4096


def _handler(cap):
    adapter = cap.data.get('execution', {}).get('adapter')
    if adapter == 'task_archive':
        from . import workflow_task_archive_v198 as handler
    elif adapter == 'task_evidence_packet':
        from . import workflow_task_packet_v199 as handler
    elif adapter == 'runtime_document_packet':
        from . import workflow_document_packet_v207 as handler
    elif adapter == 'workflow_service' and cap.data.get('execution', {}).get('operation') == 'execute_registered_tool_plan':
        from . import workflow_registered_tool_plan_v201 as handler
    elif adapter == 'workflow_service' and cap.data.get('execution', {}).get('operation') == 'resense_from_context':
        from . import workflow_service_context_v196 as handler
    else:
        return None
    handler.validate_manifest(cap.data)
    return handler


def _need(value):
    if not value:
        raise ValueError('local_transaction_capacity_original_proof')


def _row(db, p, model, identity):
    value = db.scalar(select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
        .execution_options(populate_existing=True))
    _need(value is not None)
    return value


def _audit(db, p, action, identity, actor_id, expected):
    query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity)
    if action in {'workflow.graph.dispatch', 'workflow.graph.dispatch_authorized'}:
        query = query.where(Audit.data['job_id'].as_string() == expected['job_id'])
    rows = list(db.scalars(query.limit(2)))
    _need(len(rows) == 1 and rows[0].actor_id == actor_id and rows[0].data == expected)


def _resense_source_proof(db, p, cap, call, job, task, transport, handler, inputs):
    """Cold originals only; never acquire the live Gap/Task dispatch locks.

    reader.checked_context, even with dispatch=False, takes live request locks.
    A capacity read instead checks its actual frozen producer and source rows;
    no historical principal, monitor grant or observation request is installed.
    """
    from . import workflow_graph_scope as scope, workflow_task_context_v196 as reader
    from .workflow_graph import business_binding
    from .workflow_job_dependencies_v264 import parent_pin
    from .authored_goal_data_workflow_store_v288 import check_binding, service_delegations
    from .goals import snapshot
    _need(job.data['actor_id'] == p.user_id and call.data.get('service_state') is None
        and 'service_state' not in call.data and 'output' not in call.data)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    _need(actual.id == task.id and actual.data == task.data and node['actor_id'] == p.user_id)
    scope._job_matches(actual, job, state, activation, node)
    binding = business_binding(release, business)
    _need(binding.get('schema') == 'authored-goal-data-workflow-binding-v288'
        and check_binding(binding) == binding
        and binding['graph'] == graph and binding['workspace_id'] == p.workspace_id
        and service_delegations(binding).get(node['id']) == {
            'service_actor': 'execution_service', 'operation': handler.OP, 'agent_role': 'business'})
    execution = node['execution']
    _need(execution['adapter'] == 'workflow_service' and execution['agent_role'] == 'business'
        and execution['capability_id'] == cap.id and execution['capability_hash'] == cap.content_hash
        and execution['release_version'] == cap.release_version == cap.data['version']
        and execution['input_schema'] == cap.data['input_schema']
        and execution['output_schema'] == cap.data['output_schema']
        and activation['inputs_snapshot']['arguments'] == inputs)
    _audit(db, p, 'workflow.graph.dispatch', task.id, p.user_id,
        {'activation_id': activation['id'], 'job_id': job.id})
    _audit(db, p, 'workflow.graph.dispatch_authorized', task.id, p.user_id,
        {'activation_id': activation['id'], 'job_id': job.id,
            'epoch': job.data['cancel_epoch'], 'input_hash': digest(inputs)})
    current = {'task': task, 'job': job, 'node': node, 'release': release,
        'binding': binding, 'transport': transport, 'capability': cap,
        'delegation': {'operation': handler.OP, 'agent_role': 'business'}}
    _need(handler._checked_proof(db, p, current, call, inputs, call.idempotency_key,
        call.data['approval']) is not None)
    # This also rejects a STARTED audit whose mutable service_state disappeared.
    _need(handler.shared._state(db, p, current, call, inputs, call.idempotency_key,
        call.data['approval']) is None)
    context = inputs['context']; reader._check_context(context)
    producer = context['producer']; original_job = _row(db, p, Job, producer['job_id'])
    original_call = _row(db, p, Invocation, producer['invocation_id'])
    original_cap = _row(db, p, Capability, producer['capability_id'])
    reader.validate_manifest(original_cap.data)
    _need(original_cap.status == 'published' and original_cap.kind == 'tool'
        and original_cap.content_hash == original_cap.validated_hash == digest(original_cap.data)
        == producer['capability_hash'] and original_job.status == 'succeeded'
        and original_call.status == 'succeeded' and original_call.data.get('role') == 'data'
        and original_call.data.get('side_effect') is False and original_call.data.get('approval') is None)
    pin = parent_pin(db, p, task, original_job.id)
    _need(all(pin[key] == producer[key] for key in (
        'job_id', 'actor_id', 'activation_id', 'invocation_id', 'capability_id', 'capability_hash', 'input_hash'))
        and pin['output_hash'] == digest(original_call.data['output'])
        and original_call.capability_id == original_cap.id
        and original_call.idempotency_key == original_job.idempotency_key
        and digest(original_call.idempotency_key) == producer['request_key_hash'])
    result = reader.checked_result(original_call.data['output'])
    _need(any(context == item for item in [result['resense_context'], *result['candidates']] if item is not None))
    original_activation = state['activations'][producer['activation_id']]
    _need(original_activation['node_id'] == producer['node_id']
        and original_activation['status'] == 'completed'
        and original_activation['epoch'] == producer['cancel_epoch'] == activation['epoch']
        and original_activation['output'] == original_call.data['output'])
    _audit(db, p, reader.PRODUCED, original_call.id, producer['actor_id'],
        reader.receipt_audit_data(original_call, original_job, result))
    _need(context['scope'] == {'workspace_id': p.workspace_id, 'task_id': task.id,
        'instance_id': release.instance_id, 'release_id': release.id, 'package_id': release.package_id,
        'business_id': business['id'], 'graph_hash': state['graph_hash'],
        'source_binding_hash': binding['binding_hash'], 'goal_snapshot_hash': digest(snapshot(task)),
        'native_goal_context_hash': digest(task.data.get('native_goal_context'))})
    run = _row(db, p, DataRun, context['query']['query_id'])
    gap = _row(db, p, DataGap, context['gap']['gap_id'])
    request = context['gap']['observation_request']
    _need(task.data.get('native_goal_query_id') == run.id == gap.query_id
        and run.data.get('native_goal_task_id') == task.id
        and digest(run.data['request']) == context['query']['request_hash'] == request['query_request_hash']
        and digest(run.data.get('goal_delegation')) == context['query']['goal_delegation_hash']
        and run.data.get('goal_delegation') == request['goal_delegation']
        == {'task_id': task.id, 'goal_snapshot': snapshot(task)}
        and run.data.get('native_goal_context_hash') == task.data.get('native_goal_context_hash')
        == digest(task.data.get('native_goal_context'))
        and request['query_id'] == run.id and request['gap_id'] == gap.id
        and gap.data.get('business_task_id') == task.id and gap.entity_id == context['gap']['entity_id']
        and gap.data.get('request') == request['request']
        and digest(request) == context['gap']['observation_request_hash']
        and sorted(gap.data['missing_fields']) == sorted(context['gap']['fields']) == sorted(request['fields']))
    policy = _row(db, p, MonitorPolicy, context['policy']['policy_id'])
    _need(policy.data.get('approved_hash') == context['policy']['approved_hash']
        and policy.approved_by == context['policy']['approved_by'])
    source_ids = []
    for source_pin in context['policy']['source_pins']:
        source = _row(db, p, Capability, source_pin['source_id']); source_ids.append(source.id)
        _need(source.kind == 'source' and source.status == 'published'
            and source.content_hash == source.validated_hash == digest(source.data) == source_pin['source_hash'])
    _need(source_ids and len(source_ids) == len(set(source_ids))
        and set(source_ids) == set(policy.data['source_ids']))
    return context


def _resense_footprint_empty(db, p, task, context):
    """Fail closed on any unaccounted effect, ambiguous orphan or scan overflow.

    No service_state means no child inventory is accounted for. Read complete
    bounded workspace inventories, including reverse receipt/dispatch witnesses;
    an overflowing inventory holds capacity rather than being treated as empty.
    """
    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177
    from .http_observation_exchange_v177 import ACTION as HTTP_EXCHANGE_CHANGED
    seeds = {task.id, context['gap']['gap_id'], context['query']['query_id']}
    policy_id, entity_id = context['policy']['policy_id'], context['gap']['entity_id']
    models = (ObservationPlan, Acquisition, Job, Observation, HTTPObservationExchangeV177, Event, Memory, Audit)
    inventories = {}
    for model in models:
        rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id)
            .limit(MAX_FOOTPRINT + 1)))
        _need(len(rows) <= MAX_FOOTPRINT)
        inventories[model] = rows

    def references(value, depth=0):
        _need(depth <= 32)
        if type(value) is str: return {value}
        if type(value) is dict:
            return set().union(*(references(item, depth + 1) for item in value.values()))
        if type(value) is list:
            return set().union(*(references(item, depth + 1) for item in value))
        return set()

    plans = {row.id: row for row in inventories[ObservationPlan]}
    for model, rows in inventories.items():
        for row in rows:
            value = row.data
            _need(type(value) is dict)
            if model is Job and row.kind != 'dispatch_acquisition': continue
            if model is Event and row.kind in {'data.gap', 'data.gap_coordination'}: continue
            if model is Memory and not (value.get('type') == 'observation_evidence'
                    or 'data_reception' in value or 'observation_id' in value): continue
            if model is Audit and not (row.action == HTTP_EXCHANGE_CHANGED
                    or row.action.startswith(('acquisition.', 'perception.acquire',
                        'perception.plan', 'observation.', 'data.observation', 'sensing.'))): continue
            if references(value) & seeds: return False
            if model is ObservationPlan and row.policy_id == policy_id and row.entity_id == entity_id:
                request = value.get('observation_request')
                if type(request) is not dict or not {'gap_id', 'query_id', 'goal_delegation'} <= set(request):
                    return False
            if model is Acquisition and row.entity_id == entity_id and (
                    value.get('policy_id') == policy_id
                    or row.capability_id in {pin['source_id'] for pin in context['policy']['source_pins']}):
                plan = plans.get(value.get('observation_plan_id'))
                if (not value.get('policy_id') or plan is None
                        or type(plan.data.get('observation_request')) is not dict): return False
    return True


def _resense_context_cpu(db, p, cap, call, job, task, transport, handler, inputs):
    # A retained state needs original-key reconciliation and full child resource
    # proof. It is deliberately outside this narrow no-state CPU exception.
    _need('service_state' not in call.data and 'output' not in call.data)
    context = _resense_source_proof(db, p, cap, call, job, task, transport, handler, inputs)
    _need(_resense_footprint_empty(db, p, task, context))


def _registered_targets(db, p, cap, call, inputs, handler):
    """Read original outbox and unchanged target slots, never resolve unknown.

    A missing target or incomplete retained outbox conservatively holds the
    parent slot. Child HTTP Invocations retain their ordinary aggregate count;
    no effect is sent without one of those durable child Invocations.
    """
    handler._inventory(db, p, call.id)
    pins = cap.data['specification']['tool_pins']
    steps = [step for candidate in inputs['candidate_plans'] for step in candidate['steps']]
    _need({step['capability_id'] for step in steps} == set(cap.data['dependencies']))
    for pin in pins:
        target = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
            Capability.id == pin['capability_id']).execution_options(populate_existing=True))
        _need(target is not None and target.workspace_id == p.workspace_id and target.kind == 'tool'
            and target.status == 'published' and target.release_version == pin['release_version']
            and target.content_hash == target.validated_hash == pin['capability_hash'] == digest(target.data))
        Manifest.model_validate(target.data)
        execution = target.data['execution']; budget = target.data['budget']
        _need(execution.get('adapter') == 'http' and execution.get('side_effect') is True
            and execution.get('dispatch_scope') == 'registered_tool_plan_v201'
            and execution.get('approval_policy') == 'admin'
            and execution.get('receipt_protocol') == handler.RECEIPT
            and type(execution.get('endpoint')) is str and bool(execution['endpoint'])
            and type(execution.get('receipt_endpoint')) is str and bool(execution['receipt_endpoint'])
            and target.data['data_policy'].get('external_allowed') is True
            and type(budget.get('max_attempts')) is int and budget['max_attempts'] == 1
            and type(budget.get('max_concurrency')) is int and budget['max_concurrency'] == 1)
        handler.business_tools.validate_bindings(target.data)
        _need(set(execution.get('input_bindings', {}).values()) <= {'entity_ids', 'task_id', 'request_key'})
        _need(all(step['capability_hash'] == target.content_hash
            and step['agent_role'] in target.data['allowed_agent_roles'] and step['allow_external_inputs'] is True
            for step in steps if step['capability_id'] == target.id))


def _ended_unknown(db, p, cap, call, handler):
    """Actual same-workspace original rows; malformed/missing proof holds slot."""
    try:
        data = call.data
        orchestrator = cap.data['execution']['adapter'] == 'workflow_service'
        resense = orchestrator and cap.data['execution'].get('operation') == 'resense_from_context'
        approval_handler = handler.shared if resense else handler.service if orchestrator else handler
        _need(call.status == 'unknown' and call.workspace_id == p.workspace_id and call.capability_id == cap.id
            and type(data) is dict and data.get('role') == ('business' if orchestrator else 'data')
            and data.get('side_effect') is True
            and data.get('capability_hash') == cap.content_hash == digest(cap.data)
            and cap.validated_hash == cap.content_hash and cap.status == 'published'
            and digest(data.get('capability_snapshot')) == cap.content_hash)
        # The frozen manifest must be the exact validated handler contract,
        # not a local-looking adapter/operation label on an external tool.
        handler.validate_manifest(data['capability_snapshot'])
        inputs = data['inputs']
        if cap.data['execution']['adapter'] == 'task_evidence_packet':
            handler.reader._inputs(inputs)
        else:
            handler._inputs(inputs)
        _need(data.get('input_hash') == digest(inputs))
        job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.id == data.get('job_id')).execution_options(populate_existing=True))
        _need(job is not None and job.workspace_id == p.workspace_id and job.status in ENDED
            and job.kind == 'execute_full_semantic_graph_node' and type(job.data) is dict
            and job.idempotency_key == job.data.get('invocation_key') == call.idempotency_key
            and job.data.get('input_hash') == data['input_hash'] and digest(job.data.get('inputs')) == data['input_hash'])
        original = job.data
        _need(type(original.get('attempt')) is int and original['attempt'] >= 1
            and type(original.get('cancel_epoch')) is int and original['cancel_epoch'] >= 0
            and type(original.get('actor_id')) is str and bool(original['actor_id'])
            and type(data.get('lease_token')) is str and bool(data['lease_token'])
            and call.idempotency_key == 'graph:' + original['task_id'] + ':'
                + original['activation_id'] + ':' + str(original['attempt']))
        task = db.scalar(select(Task).where(Task.workspace_id == p.workspace_id,
            Task.id == original['task_id']).execution_options(populate_existing=True))
        _need(task is not None and task.workspace_id == p.workspace_id)
        graph = task.data['graph_runtime']; activation = graph['activations'][original['activation_id']]
        _need(original['graph_hash'] == graph['graph_hash'] and activation['epoch'] == original['cancel_epoch'])
        current = task.data['graph_jobs'][original['activation_id']]
        _need(type(current) is dict and type(current.get('previous', [])) is list)
        transports = [row for row in [*current.get('previous', []), current]
            if type(row) is dict and row.get('job_id') == job.id]
        _need(len(transports) == 1)
        transport = transports[0]; owner = transport.get('dispatch_ownership')
        _need(transport.get('dispatch_committed') is True and transport.get('invocation_key') == call.idempotency_key
            and transport.get('input_hash') == transport.get('dispatch_input_hash') == data['input_hash']
            and transport.get('cancel_epoch') == original['cancel_epoch']
            and transport.get('attempt') == original['attempt']
            and type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
            and owner == {'job_id': job.id, 'lease_token': data['lease_token']})
        if 'invocation_id' in transport:
            _need(transport['invocation_id'] == call.id)
        if orchestrator:
            _need('dispatch_authorization' in transport)
        approval = data.get('approval')
        _need(type(approval) is dict and set(approval) == approval_handler.APPROVAL_KEYS
            and digest(approval) == digest({'schema': approval_handler.APPROVAL, 'task_id': task.id,
                'job_id': job.id, 'activation_id': original['activation_id'], 'graph_hash': original['graph_hash'],
                'input_hash': data['input_hash'], 'capability_id': cap.id,
                'capability_hash': cap.content_hash, 'cancel_epoch': original['cancel_epoch']}))
        if 'dispatch_authorization' in transport:
            authorization = transport['dispatch_authorization']
            _need(type(authorization) is dict and set(authorization) == AUTH_KEYS
                and all(authorization.get(key) == value for key, value in {
                    'epoch': original['cancel_epoch'], 'job_id': job.id, 'lease_token': data['lease_token'],
                    'input_hash': data['input_hash'], 'capability_hash': cap.content_hash,
                    'actor_id': original['actor_id']}.items())
                and type(authorization['authorized_at']) is str and bool(authorization['authorized_at']))
        if resense:
            _resense_context_cpu(db, p, cap, call, job, task, transport, handler, inputs)
        elif orchestrator:
            _registered_targets(db, p, cap, call, inputs, handler)
        return True
    except (HTTPException, SchemaError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, OverflowError):
        return False


def count_active(db, p, cap):
    """Keep effect slots; exclude only proven ended local/orchestrator CPU work."""
    try:
        handler = _handler(cap)
    except (HTTPException, SchemaError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        handler = None
    if handler is None:
        return db.scalar(select(func.count()).select_from(Invocation).where(
            Invocation.workspace_id == p.workspace_id, Invocation.capability_id == cap.id,
            Invocation.status.in_(ACTIVE)))
    calls = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.capability_id == cap.id, Invocation.status.in_(ACTIVE))))
    return sum(not _ended_unknown(db, p, cap, call, handler) for call in calls)
