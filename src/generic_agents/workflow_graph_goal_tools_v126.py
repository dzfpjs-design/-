"""Goal tools backed by frozen graph nodes and actual completed invocations.

These SELECT-only checks grant no execution permission and never rebind a legacy
flow. A committed graph result whose original Job has not yet been successfully
reaped is pending as a whole, including every result of a repeated capability.
"""
import copy
from datetime import datetime

from fastapi import HTTPException

from .contracts import digest
from .models import Entity, Invocation, Job
from .services import published, scoped, validate
from . import full_semantic_graph_v1 as pure
from . import workflow_graph as graph_runtime
from . import workflow_graph_scope as graph_scope


_ERROR = '完整图能力的原节点、输入、实际调用或派发回执无法核对，不能用于目标验收'
_RESULT_KEYS = {'request_id', 'step_id', 'activation_id', 'actor', 'at', 'execution_mode',
    'invocation_id', 'capability_id', 'capability_hash', 'release_version', 'input_snapshot',
    'input_hash', 'output', 'entity_state_updated', 'candidate_only'}
_AUTH_KEYS = {'epoch', 'job_id', 'lease_token', 'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
_BUSINESS_FLOW_BINDING = 'business-flow-execution-binding-v1'
_NESTED_BINDING = 'business-flow-nested-execution-binding-v195'
_AUTHORED_SERVICE_BINDING = 'authored-service-workflow-binding-v270'
_AUTHORED_DECISION_BINDING = 'authored-decision-workflow-binding-v278'
_AUTHORED_DECISION_BINDINGS = frozenset({_AUTHORED_DECISION_BINDING, 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'})
_AUTHORED_SERVICE_BINDINGS = frozenset({_AUTHORED_SERVICE_BINDING, *_AUTHORED_DECISION_BINDINGS})
_LEGACY_BINDINGS = frozenset({'report-full-semantic-graph-binding-v3',
    'report-full-semantic-graph-binding-v4', 'report-object-graph-binding-v171'})


def _need(value):
    if not value:
        raise HTTPException(409, _ERROR)


def _same(left, right):
    return digest(left) == digest(right)


def _context(db, p, release, business):
    from .instance_semantic_runtime_dispatch import modern
    _need(release.workspace_id == p.workspace_id)
    snapshot = release.data['snapshot']
    runtime = modern(snapshot)
    businesses = snapshot['businesses']
    frozen = [item for item in businesses if item['id'] == business['id']]
    _need(len(frozen) == 1 and _same(frozen[0], business)
        and business['execution_mode'] == 'confirmed_graph_workflow'
        and type(business.get(runtime.BUSINESS_MARKER)) is dict)
    _need(runtime.verify_release(db, p, release, live=False) is not None)
    binding = graph_runtime.business_binding(release, business)
    graph = binding['graph']
    package = published(db, p, release.package_id, 'domain_package')
    dependencies = package.data['dependencies']
    _need(type(dependencies) is list and len(dependencies) == len(set(dependencies)))
    return binding, graph, set(dependencies)


def _capability(db, p, node, dependencies, *, nested=False, delegation=None):
    from .workflow_abilities import eligible, eligible_service
    execution = node['execution']; identity = execution['capability_id']
    _need(identity in dependencies)
    cap = published(db, p, identity)
    if delegation is not None:
        _need(nested and execution['adapter'] == 'workflow_service')
        eligible_service(cap, execution['agent_role'], service_actor=delegation['service_actor'],
            operation=delegation['operation'])
    elif nested:
        _need(execution['adapter'] != 'workflow_service')
        eligible(cap, execution['agent_role'], allow_task_archive=True, allow_task_packet=True,
            allow_document_packet=True)
    else:
        eligible(cap, execution['agent_role'])
    _need(cap.kind in {'tool', 'specialist_model'} and cap.content_hash == digest(cap.data)
        and cap.validated_hash == cap.content_hash
        and execution['capability_hash'] == cap.content_hash
        and execution['release_version'] == cap.release_version
        and execution['adapter'] == cap.data['execution']['adapter']
        and _same(execution['input_schema'], cap.data['input_schema'])
        and _same(execution['output_schema'], cap.data['output_schema'])
        and execution['max_attempts'] == cap.data['budget'].get('max_attempts', 2))
    return cap


def _catalog(db, p, release, business, binding, graph, dependencies):
    _need(type(binding) is dict and type(binding.get('schema')) is str)
    nested = binding['schema'] == _NESTED_BINDING
    authored = binding['schema'] in _AUTHORED_SERVICE_BINDINGS
    delegations = {}
    if nested:
        from .business_flow_nested_adoption_v195 import check_binding, service_delegations
        checked = check_binding(binding)
        _need(_same(checked, binding) and _same(checked['graph'], graph)
            and checked['business_name'] == business['name']
            and _same(checked['flow'], business['flow']))
        actions = checked['translation']['source_mapping']['actions']
        _need(type(actions) is list and bool(actions)
            and len({row['ast_path'] for row in actions}) == len(actions)
            and len({row['graph_node_id'] for row in actions}) == len(actions))
        names = {row['graph_node_id']: row['source_action']['name'] for row in actions}
        _need(all(type(name) is str and bool(name) for name in names.values())
            and set(names) == {node['id'] for node in graph['nodes'] if node['kind'] in {'human', 'ability'}}
            and all(node['source_step_id'] == node['id'] for node in graph['nodes']
                if node['kind'] in {'human', 'ability'}))
        delegations = service_delegations(checked['translation'])
        _need(type(checked['capability_pins']) is dict
            and set(checked['capability_pins']) == {node['id'] for node in graph['nodes'] if node['kind'] == 'ability'})
    elif authored:
        if binding['schema'] in _AUTHORED_DECISION_BINDINGS:
            from .authored_workflow_store_v208 import _binding_family
            family = _binding_family(binding)
            check_binding, service_delegations = family.check_binding, family.service_delegations
        else:
            from .authored_service_workflow_store_v270 import check_binding, service_delegations
        checked = check_binding(binding)
        _need(_same(checked, binding) and _same(checked['graph'], graph)
            and checked['business_name'] == business['name']
            and _same(checked['flow'], business['flow']))
        # The independently saved human flow and deterministic recipe own these
        # names. No report source, nested translation or invented AST is used.
        original = checked['flow']['steps']
        _need(type(original) is list and bool(original)
            and all(type(step) is dict and type(step.get('id')) is str and bool(step['id'])
                and type(step.get('name')) is str and bool(step['name']) for step in original)
            and len({step['id'] for step in original}) == len(original))
        names = {step['id']: step['name'] for step in original}
        _need(all(node['source_step_id'] is None or node['source_step_id'] in names
            for node in graph['nodes'] if node['kind'] in {'human', 'ability'})
            and type(checked['capability_pins']) is dict
            and set(checked['capability_pins']) == {node['id'] for node in graph['nodes'] if node['kind'] == 'ability'})
        delegations = service_delegations(checked)
    elif binding['schema'] == _BUSINESS_FLOW_BINDING:
        from .business_flow_execution_adoption import check_binding
        checked = check_binding(binding)
        _need(_same(checked, binding) and _same(checked['graph'], graph)
            and checked['business_name'] == business['name']
            and _same(checked['flow'], business['flow']))
        original = checked['flow']['source_flow']['nodes']
        _need(type(original) is list and bool(original)
            and all(type(row) is dict and type(row.get('node_id')) is str
                and bool(row['node_id']) and type(row.get('name')) is str
                and bool(row['name']) for row in original)
            and len({row['node_id'] for row in original}) == len(original))
        names = {row['node_id']: row['name'] for row in original}
        _need(all(type(node.get('source_step_id')) is str and node['source_step_id'] in names
            for node in graph['nodes'] if node['kind'] in {'human', 'ability'})
            and type(checked['capability_pins']) is dict
            and set(checked['capability_pins']) == {node['id'] for node in graph['nodes']
                if node['kind'] == 'ability'})
    else:
        _need(binding['schema'] in _LEGACY_BINDINGS)
        original = binding['source_business']['steps']
        source = binding['source_design']['business_bindings'][binding['candidate_index']]['step_ids']
        _need(len(original) == len(source) and len(set(source)) == len(source))
        names = {identity: step['name'] for identity, step in zip(source, original)}
    entries = {}
    for node in graph['nodes']:
        if node['kind'] != 'ability':
            continue
        _need(_same(binding['capability_pins'][node['id']], node['execution']))
        cap = _capability(db, p, node, dependencies, nested=nested or authored, delegation=delegations.get(node['id']))
        label = names[node['source_step_id']] if node['source_step_id'] is not None else cap.data['description']
        entry = entries.setdefault(cap.id, {'capability': cap, 'step_names': [], 'sources': ['workflow_step']})
        if label not in entry['step_names']:
            entry['step_names'].append(label)
    return entries


def catalog(db, p, release, business):
    """The old metadata shape, with inventory taken only from the adopted graph."""
    try:
        binding, graph, dependencies = _context(db, p, release, business)
        return _catalog(db, p, release, business, binding, graph, dependencies)
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, RecursionError):
        _need(False)


def _time(value):
    _need(type(value) is str)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed


def _actual_result(db, p, task, graph, state, nodes, receipt, entry, *, nested=False, authored=False, binding=None):
    _need(type(receipt) is dict and set(receipt) == _RESULT_KEYS
        and receipt['execution_mode'] == 'registered_graph_ability'
        and receipt['candidate_only'] is True and receipt['entity_state_updated'] is False)
    activation = state['activations'][receipt['activation_id']]
    node = nodes[activation['node_id']]; cap = entry['capability']
    _need(node['kind'] == 'ability' and activation['status'] == 'completed'
        and receipt['step_id'] == (node['source_step_id'] or node['id'])
        and receipt['actor'] == node['actor_id']
        and receipt['capability_id'] == cap.id == node['execution']['capability_id']
        and receipt['capability_hash'] == cap.content_hash
        and receipt['release_version'] == cap.release_version
        and _same(receipt['output'], activation['output'])
        and _same(receipt['input_snapshot'], activation['inputs_snapshot']['arguments'])
        and receipt['input_hash'] == digest(receipt['input_snapshot']))
    job = scoped(db, Job, receipt['request_id'], p)
    transport = graph_scope._job_matches(task, job, state, activation, node)
    attempt = transport.get('attempt')
    _need(type(attempt) is int and 1 <= attempt <= node['execution']['max_attempts'])
    key = 'graph:' + task.id + ':' + activation['id'] + ':' + str(attempt)
    _need(job.id == receipt['request_id'] and job.idempotency_key == key
        and job.data.get('invocation_key') == key and job.data.get('attempt') == attempt
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('invocation_id') == receipt['invocation_id']
        and transport.get('output_hash') == digest(receipt['output'])
        and transport.get('dispatch_input_hash') == receipt['input_hash']
        and _same(job.data['inputs'], receipt['input_snapshot']))
    owner, authorization = transport.get('dispatch_ownership'), transport.get('dispatch_authorization')
    _need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'} and owner['job_id'] == job.id
        and type(owner['lease_token']) is str and bool(owner['lease_token'])
        and type(authorization) is dict and set(authorization) == _AUTH_KEYS
        and type(authorization['epoch']) is int and authorization['epoch'] == activation['epoch']
        and authorization['job_id'] == job.id and authorization['lease_token'] == owner['lease_token']
        and authorization['input_hash'] == receipt['input_hash']
        and authorization['capability_hash'] == cap.content_hash
        and authorization['actor_id'] == node['actor_id'])
    call = scoped(db, Invocation, receipt['invocation_id'], p)
    approval = None
    adapter = node['execution']['adapter']
    strict = nested or authored
    if authored:
        _need(type(binding) is dict and binding.get('schema') in _AUTHORED_SERVICE_BINDINGS
            and _same(binding['graph'], graph))
    if strict and adapter in {'workflow_service', 'task_evidence_packet', 'task_archive', 'runtime_document_packet'}:
        if adapter == 'workflow_service':
            from . import workflow_service_v195 as handler
        elif adapter == 'task_evidence_packet':
            from . import workflow_task_packet_v199 as handler
        elif adapter == 'runtime_document_packet':
            from . import workflow_document_packet_v207 as handler
        else:
            from . import workflow_task_archive_v198 as handler
        approval = handler.make_approval(task, job, node, receipt['input_snapshot'])
    graph_runtime.check_invocation(call, p, cap, node, receipt['input_snapshot'], job, owner, approval=approval)
    _need(call.status == 'succeeded' and _same(call.data['output'], receipt['output']))
    if strict and adapter == 'workflow_service':
        from . import workflow_registered_tool_plan_v201 as plans
        # Reconcile can dispatch a prepared next item. Acceptance only reads a
        # complete original inventory, never a live continuation or fake actor.
        operation = cap.data['execution'].get('operation')
        if operation == plans.OP:
            _need(_same(plans.saved_receipt(db, p, cap, receipt['input_snapshot'], key, approval, call), receipt['output']))
        elif authored and operation == 'plan_approved_goal_data_v288':
            from .workflow_goal_data_route_runtime_v288 import saved_result
            _need(_same(saved_result(db, p, task, job, call, cap, receipt['input_snapshot']), receipt['output']))
        elif authored and operation == 'create_goal_evaluation_packet_v288':
            from .workflow_goal_evaluation_packet_v288 import saved_result
            _need(_same(saved_result(db, p, task, job, call, cap, receipt['input_snapshot']), receipt['output']))
        elif authored and operation == 'plan_business_decision_review_v285':
            from .workflow_decision_tool_planner_v285 import saved_result
            _need(_same(saved_result(db, p, task, job, call, cap, receipt['input_snapshot']), receipt['output']))
        elif authored and operation == 'plan_business_decision_v277':
            from .workflow_decision_tool_planner_v277 import saved_result
            _need(_same(saved_result(db, p, task, job, call, cap, receipt['input_snapshot']), receipt['output']))
        elif authored and operation == 'resense_from_context':
            from .workflow_resense_goal_receipts_v271 import saved_receipt
            _need(_same(saved_receipt(db, p, task, job, call, cap, receipt['input_snapshot'],
                key, approval, binding), receipt['output']))
        else:
            _need(False)
    elif strict and adapter == 'task_evidence_packet':
        from .workflow_task_packet_v199 import read_original_context
        _need(_same(read_original_context(db, p, call), receipt['output']['context_result']))
    elif strict and adapter == 'runtime_document_packet':
        from .workflow_document_packet_v207 import read_original_context
        _need(_same(read_original_context(db, p, call), receipt['output']['context_result']))
    elif strict and adapter == 'task_archive':
        from .workflow_task_archive_v198 import read_original_receipt
        _need(_same(read_original_receipt(db, p, call), receipt['output']))
    elif strict and adapter in {'business_reasoning', 'business_decision_v276','business_decision_v284','business_goal_ready_v288'}:
        from .business_reasoning_protocols_v276 import family
        _need(_same(family(adapter).saved_result(db, p, task, job, call, cap, receipt['input_snapshot']), receipt['output']))
    # Authorization uses the database clock; the gateway/receipt use the
    # application clock. Their cross-clock order is not execution authority.
    for stamp in (call.data['started_at'], authorization['authorized_at'], receipt['at']):
        _time(stamp)
    command = {'request_id': job.id, 'op': 'complete', 'actor_id': node['actor_id'],
        'body_hash': digest({'activation_id': activation['id'], 'output': receipt['output'],
            'actor_id': node['actor_id'], 'expected_version': activation['version'] - 1,
            'cancel_epoch': activation['epoch']})}
    _need(sum(item == command for item in state['commands']) == 1)
    validate(cap.data['output_schema'], receipt['output'])
    _need(job.status in {'queued', 'running', 'failed', 'blocked', 'cancelled', 'succeeded'})
    value = {'status': 'succeeded', 'method': 'registered_capability', 'capability_id': cap.id,
        'capability_name': cap.data['description'], 'capability_hash': cap.content_hash,
        'invocation_id': call.id, 'input_hash': receipt['input_hash'], 'output': copy.deepcopy(receipt['output']),
        'workflow_step_id': receipt['step_id'], 'workflow_receipt_hash': digest(receipt),
        'candidate_only': True, 'physical_state_verified': False}
    return value, job.status == 'succeeded'


def receipts(db, p, task, *, lock_sources=True):
    """Normalize real graph results; never infer success from graph completion."""
    try:
        from .workspace_api import saved_execution
        actual, release, business = saved_execution(db, p, task.id)
        _need(actual.workspace_id == p.workspace_id and actual.id == task.id
            and _same(actual.data, task.data) and 'graph_runtime' in actual.data)
        binding, graph, dependencies = _context(db, p, release, business)
        state = pure.check_run(graph, actual.data['graph_runtime'])
        _need(actual.status == {'succeeded': 'workflow_completed', 'cancelled': 'workflow_cancelled',
            'failed': 'workflow_failed', 'stopped': 'workflow_stopped'}.get(state['status'], 'workflow_waiting'))
        allowed = _catalog(db, p, release, business, binding, graph, dependencies)
        projection = actual.data['workflow_runtime']
        _need(projection['schema'] == 'workflow-graph-projection-v1'
            and projection['current_step_id'] is None)
        candidates = projection['receipts']
        _need(type(candidates) is list and all(type(item) is dict and item.get('execution_mode')
            in {'registered_graph_ability', 'human_graph_result'} for item in candidates))
        nodes = {node['id']: node for node in graph['nodes']}
        ability = [item for item in candidates if item['execution_mode'] == 'registered_graph_ability']
        expected = {identity for identity, item in state['activations'].items()
            if item['status'] == 'completed' and nodes[item['node_id']]['kind'] == 'ability'}
        _need(len(ability) == len(expected)
            and {item['activation_id'] for item in ability} == expected)
        from .native_goals import verify_workflow_inputs
        verify_workflow_inputs(db, p, actual, lock_sources=lock_sources)
        from .entity_release_compat import verify_task_objects
        verify_task_objects(db, p, actual, release, lock_sources=lock_sources)
        for original in actual.data.get('object_snapshots', []):
            obj = scoped(db, Entity, original['id'], p)
            _need(obj.version == original['version'] and _same(obj.data, original['attributes']))
        required = {item['capability_id'] for item in actual.data.get('goal_spec', {}).get('criteria', [])
            if item['kind'] in {'capability_success', 'tool_value'}}
        result, pending = [], False
        for receipt in ability:
            _need(receipt['capability_id'] in allowed)
            value, reaped = _actual_result(db, p, actual, graph, state, nodes, receipt,
                allowed[receipt['capability_id']], nested=binding['schema'] == _NESTED_BINDING,
                authored=binding['schema'] in _AUTHORED_SERVICE_BINDINGS, binding=binding)
            if binding['schema'] in {_NESTED_BINDING, *_AUTHORED_SERVICE_BINDINGS} and not reaped:
                # This is an acceptance/read gate, not graph node settlement.
                # An empty tool list cannot represent a still-unreaped result:
                # even an unlisted capability must finish its original Job.
                raise HTTPException(409, '原流程节点的处理Job尚未成功收尾，暂不能作为整图验收依据；请核对原任务进度')
            if receipt['capability_id'] in required:
                pending = pending or not reaped
                result.append(value)
        # A delayed/unknown/late result cannot be omitted to leave only a good
        # result for the same capability's all-matching tool_value criterion.
        if pending or state['status'] in {'cancelled', 'stopped', 'failed'}:
            return []
        return result
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, RecursionError):
        _need(False)
