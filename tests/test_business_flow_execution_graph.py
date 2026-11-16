"""Synthetic public pure translations and REAL native graph reductions only.

No DB/member/capability/source authorization, provider or device is simulated as
verified.  The small source fixture uses the actual wire182 shape.  These tests
exercise the existing graph reducer, never replace its validators or receipts.
"""
import copy
import socket
import subprocess

import pytest

from generic_agents import business_flow_execution_graph as subject
from generic_agents import full_semantic_graph_v1 as kernel
from tests.test_report_business_flow_wire_v182 import scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure converter cannot perform IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def guards(): return {'start': None, 'precondition': None, 'stop': None, 'exception': None}


def schema(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def bindings(value):
    flow = value['business']['flows'][0]
    nodes = {}
    for node in flow['nodes']:
        ports = {label: 'output_' + str(i) for i, label in enumerate(node['outputs'])}
        output = schema(**{field: {'type': 'string'} for field in ports.values()})
        common = {'actor_id': 'public_actor_' + node['node_id'],
            'input_ports': {name: 'source_input' for name in node['inputs']},
            'output_ports': ports, 'guards': guards()}
        if node['actor'] == 'human':
            nodes[node['node_id']] = {**common, 'kind': 'human', 'role': node['role'], 'output_schema': output}
        else:
            execution = {'capability_id': 'public_capability', 'capability_hash': 'a' * 64,
                'agent_role': {'business_agent': 'business', 'perception_agent': 'perception',
                    'data_agent': 'data'}.get(node['actor'], 'business'),
                'input_bindings': {'data': 'source_input'}, 'arguments': {},
                'input_schema': schema(data={'type': 'string'}), 'output_schema': output,
                'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}
            nodes[node['node_id']] = {**common, 'kind': 'ability', 'execution': execution}
    required = list(dict.fromkeys(key for item in flow['deliverables'] for key in item['produced_by']))
    return {'schema': subject.BINDINGS, 'input_schema': schema(source_input={'type': 'string'}),
        'references': {'source_input': {'kind': 'string', 'nullable': False, 'source': 'input',
            'field': 'source_input', 'node_id': None}}, 'predicates': {}, 'guards': guards(),
        'nodes': nodes, 'conditions': [], 'parallel_groups': [], 'loops': [],
        'goals': [{'id': 'completed_output', 'required_nodes': required,
            'deliverable_indices': list(range(len(flow['deliverables'])))}]}


def source_shape(names, edges, *, start=None, ends=None):
    value, context = scene(); flow = value['business']['flows'][0]
    template = copy.deepcopy(flow['nodes'][0]); attr = copy.deepcopy(template['attribution'])
    flow['nodes'] = [{**copy.deepcopy(template), 'node_id': name, 'name': '公开步骤 ' + name} for name in names]
    flow['start_node'] = start or names[0]; flow['end_nodes'] = ends or [names[-1]]
    flow['edges'] = [{**copy.deepcopy(edge), 'attribution': copy.deepcopy(attr)} for edge in edges]
    flow['deliverables'][0]['produced_by'] = [names[-1]]
    return value, context


def chain(count=3):
    names = ['step_' + str(i) for i in range(count)]
    return source_shape(names, [{'kind': 'sequence', 'from_node': a, 'to_node': b}
        for a, b in zip(names, names[1:])])


def convert(value, context, supplied=None):
    return subject.build_execution_graph(value, value['business']['flows'][0]['flow_id'],
        supplied if supplied is not None else bindings(value), scope_binding=context['scope_binding'])


def codes(result): return {row['code'] for row in result['issues']}


def native_id(result, source_id):
    return next(row['graph_node_id'] for row in result['source_mapping']['nodes'] if row['source_node_id'] == source_id)


def pending_sources(result, state):
    reverse = {row['graph_node_id']: row['source_node_id'] for row in result['source_mapping']['nodes']}
    return {reverse[state['activations'][key]['node_id']] for key in state['ready_human_ids']}


def finish(result, state, source_id, output=None):
    graph = result['graph']; node_id = native_id(result, source_id)
    activation = next(row for row in state['activations'].values() if row['node_id'] == node_id and row['status'] == 'ready')
    node = next(row for row in graph['nodes'] if row['id'] == node_id)
    if output is None: output = {key: '公开核对记录' for key in node['output_schema']['required']}
    return kernel.complete(graph, state, activation['id'], output, 'public_' + activation['id'],
        node['actor_id'], activation['version'], activation['epoch'], state['clock'] + 1)


def test_single_flow_runs_real_human_receipt_and_preserves_full_source_without_authority():
    value, context = scene(); supplied = bindings(value); before = copy.deepcopy((value, supplied, context))
    result = convert(value, context, supplied)
    assert not result['issues'] and result['graph'] is not None
    assert set(result) == {'schema', 'graph', 'source_value', 'flow_id', 'unselected_flow_ids',
        'execution_bindings', 'source_mapping', 'issues', 'translation_hash', *subject.FLAGS}
    assert all(result[key] is False for key in subject.FLAGS)
    state = kernel.new_run(result['graph'], {'source_input': '人工提供的公开输入'}, 1)
    assert state['status'] == 'running' and pending_sources(result, state) == {'review'}
    state = finish(result, state, 'review')
    assert state['status'] == 'succeeded' and len(state['goal_receipts']) == 1
    assert result['source_value'] == value and (value, supplied, context) == before
    assert subject.check_execution_graph(result, value, result['flow_id'], supplied,
        scope_binding=context['scope_binding']) == result
    result['source_value']['evidence'][0]['quote'] += '本地改动'
    assert value == before[0]


def test_seventeen_node_chain_is_complete_and_never_reduced_to_legacy_sixteen_steps():
    value, context = chain(17); result = convert(value, context)
    assert result['issues'] == [] and len(result['source_mapping']['nodes']) == 17
    assert len(result['graph']['nodes']) == 18 and len(result['source_mapping']['edges']) == 16
    assert all(row['graph_edge_ids'] for row in result['source_mapping']['edges'])
    assert len(result['source_mapping']['deliverables']) == 1
    state = kernel.new_run(result['graph'], {'source_input': '公开输入'}, 1)
    for n in range(17):
        assert pending_sources(result, state) == {'step_' + str(n)}
        state = finish(result, state, 'step_' + str(n))
    assert state['status'] == 'succeeded'


def condition_scene():
    value, context = source_shape(['check', 'yes', 'no', 'archive'], [
        {'kind': 'condition', 'from_node': 'check', 'to_node': 'yes', 'condition': '明确同意时进入这一分支。'},
        {'kind': 'condition', 'from_node': 'check', 'to_node': 'no', 'condition': '明确不同意时进入另一分支。'},
        {'kind': 'sequence', 'from_node': 'yes', 'to_node': 'archive'},
        {'kind': 'sequence', 'from_node': 'no', 'to_node': 'archive'}])
    supplied = bindings(value)
    supplied['input_schema']['properties']['approved'] = {'type': 'boolean'}
    supplied['references']['approved'] = {'kind': 'boolean', 'nullable': False, 'source': 'input',
        'field': 'approved', 'node_id': None}
    supplied['predicates']['approved'] = {'op': 'ref', 'key': 'approved'}
    supplied['conditions'] = [{'true_edge_index': 0, 'false_edge_index': 1, 'predicate_id': 'approved'}]
    return value, context, supplied


@pytest.mark.parametrize('approved,expected', [(True, 'yes'), (False, 'no')])
def test_explicit_two_condition_edges_take_exact_typed_branch_and_shared_archive(approved, expected):
    value, context, supplied = condition_scene(); result = convert(value, context, supplied)
    assert result['issues'] == []
    state = kernel.new_run(result['graph'], {'source_input': '公开输入', 'approved': approved}, 1)
    state = finish(result, state, 'check')
    assert pending_sources(result, state) == {expected}
    state = finish(result, state, expected); assert pending_sources(result, state) == {'archive'}
    assert finish(result, state, 'archive')['status'] == 'succeeded'
    assert set(result['source_mapping']['edges'][0]['graph_edge_ids']).intersection(
        result['source_mapping']['edges'][1]['graph_edge_ids'])  # Explicit shared connector, not lost edges.


def test_unknown_condition_waits_and_does_not_guess_false_or_finish():
    value, context, supplied = condition_scene(); result = convert(value, context, supplied)
    state = finish(result, kernel.new_run(result['graph'], {'source_input': '公开输入'}, 1), 'check')
    assert pending_sources(result, state) == set()
    assert state['status'] not in {'succeeded', 'failed'} and state['goal_receipts'] == []


def test_goal_cannot_require_both_exclusive_branch_receipts_and_remain_known_unfinishable():
    value, context, supplied = condition_scene()
    value['business']['flows'][0]['deliverables'][0]['produced_by'] = ['yes', 'no']
    supplied['goals'][0]['required_nodes'] = ['yes', 'no']
    result = convert(value, context, supplied)
    assert result['graph'] is None and 'goal_mutually_exclusive' in codes(result)
    assert result['source_mapping']['deliverables'][0]['producers'][0]['source_node_id'] == 'yes'
    assert result['source_mapping']['deliverables'][0]['producers'][1]['source_node_id'] == 'no'


@pytest.mark.parametrize('mode', ['unbound', 'constant', 'boolean_position', 'mixed_sequence', 'extra_exit'])
def test_condition_cannot_be_guessed_completed_with_constant_or_reordered_as_else(mode):
    value, context, supplied = condition_scene()
    if mode == 'unbound': supplied['conditions'] = []
    elif mode == 'constant': supplied['predicates']['approved'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    elif mode == 'boolean_position': supplied['conditions'][0]['true_edge_index'] = False
    elif mode == 'mixed_sequence':
        edge = value['business']['flows'][0]['edges'][1]; edge['kind'] = 'sequence'; del edge['condition']
    else:
        edge = copy.deepcopy(value['business']['flows'][0]['edges'][0]); edge['to_node'] = 'archive'
        value['business']['flows'][0]['edges'].append(edge)
    result = convert(value, context, supplied)
    assert result['graph'] is None and result['issues']
    assert len(result['source_mapping']['edges']) == len(value['business']['flows'][0]['edges'])


def parallel_scene(nested=False):
    if not nested:
        value, context = source_shape(['start', 'left', 'right', 'archive'], [
            {'kind': 'parallel', 'from_node': 'start', 'to_node': 'left', 'group': 'both'},
            {'kind': 'parallel', 'from_node': 'start', 'to_node': 'right', 'group': 'both'},
            {'kind': 'wait', 'from_nodes': ['left', 'right'], 'to_node': 'archive', 'mode': 'all'}])
        supplied = bindings(value); supplied['parallel_groups'] = [{'group': 'both', 'wait_edge_index': 2}]
    else:
        value, context = source_shape(['start', 'left', 'x', 'y', 'left_tail', 'right', 'archive'], [
            {'kind': 'parallel', 'from_node': 'start', 'to_node': 'left', 'group': 'outer'},
            {'kind': 'parallel', 'from_node': 'start', 'to_node': 'right', 'group': 'outer'},
            {'kind': 'parallel', 'from_node': 'left', 'to_node': 'x', 'group': 'inner'},
            {'kind': 'parallel', 'from_node': 'left', 'to_node': 'y', 'group': 'inner'},
            {'kind': 'wait', 'from_nodes': ['x', 'y'], 'to_node': 'left_tail', 'mode': 'all'},
            {'kind': 'wait', 'from_nodes': ['left_tail', 'right'], 'to_node': 'archive', 'mode': 'all'}])
        supplied = bindings(value); supplied['parallel_groups'] = [
            {'group': 'outer', 'wait_edge_index': 5}, {'group': 'inner', 'wait_edge_index': 4}]
    return value, context, supplied


def test_explicit_all_join_waits_for_both_real_receipts_without_double_target_activation():
    value, context, supplied = parallel_scene(); result = convert(value, context, supplied)
    assert result['issues'] == []
    state = finish(result, kernel.new_run(result['graph'], {'source_input': '公开输入'}, 1), 'start')
    assert pending_sources(result, state) == {'left', 'right'}
    state = finish(result, state, 'left'); assert pending_sources(result, state) == {'right'}
    assert not any(row['node_id'] == native_id(result, 'archive') for row in state['activations'].values())
    state = finish(result, state, 'right'); assert pending_sources(result, state) == {'archive'}
    assert len([row for row in state['activations'].values() if row['node_id'] == native_id(result, 'archive')]) == 1
    assert finish(result, state, 'archive')['status'] == 'succeeded'
    assert len(result['source_mapping']['edges'][2]['origin_arrivals']) == 2


def test_nested_all_regions_keep_distinct_scopes_and_do_not_wait_for_the_wrong_branch():
    value, context, supplied = parallel_scene(nested=True); result = convert(value, context, supplied)
    assert result['issues'] == [] and len(result['source_mapping']['parallel_groups']) == 2
    state = finish(result, kernel.new_run(result['graph'], {'source_input': '公开输入'}, 1), 'start')
    assert pending_sources(result, state) == {'left', 'right'}
    state = finish(result, state, 'left'); assert pending_sources(result, state) == {'x', 'y', 'right'}
    state = finish(result, state, 'right'); state = finish(result, state, 'x')
    assert pending_sources(result, state) == {'y'}
    state = finish(result, state, 'y'); assert pending_sources(result, state) == {'left_tail'}
    state = finish(result, state, 'left_tail'); assert pending_sources(result, state) == {'archive'}
    assert finish(result, state, 'archive')['status'] == 'succeeded'


@pytest.mark.parametrize('fault', ['any', 'missing_pair', 'duplicate_pair', 'cross_branch', 'extra_exit', 'wrong_tail_count'])
def test_any_ambiguous_or_cross_branch_parallel_is_blocked_without_dropping_any_original(fault):
    value, context, supplied = parallel_scene(); flow = value['business']['flows'][0]
    if fault == 'any': flow['edges'][2]['mode'] = 'any'
    elif fault == 'missing_pair': supplied['parallel_groups'] = []
    elif fault == 'duplicate_pair': supplied['parallel_groups'].append(copy.deepcopy(supplied['parallel_groups'][0]))
    elif fault in {'cross_branch', 'extra_exit'}:
        edge = {'kind': 'sequence', 'from_node': 'left', 'to_node': 'right' if fault == 'cross_branch' else 'archive',
            'attribution': copy.deepcopy(flow['edges'][0]['attribution'])}; flow['edges'].append(edge)
    else:
        # Keep the source wire structurally complete: right can still reach
        # archive via left.  It is the explicit fork/all pairing that is wrong,
        # not a disconnected synthetic source rejected before the converter.
        flow['edges'][2]['from_nodes'] = ['left']
        flow['edges'].append({'kind': 'sequence', 'from_node': 'right', 'to_node': 'left',
            'attribution': copy.deepcopy(flow['edges'][0]['attribution'])})
    result = convert(value, context, supplied)
    assert result['graph'] is None and result['issues'] and result['source_value'] == value
    assert len(result['source_mapping']['edges']) == len(flow['edges'])


def test_single_predecessor_all_keeps_its_wait_origin_and_needs_no_fake_single_branch_fork():
    value, context = source_shape(['start', 'archive'], [
        {'kind': 'wait', 'from_nodes': ['start'], 'to_node': 'archive', 'mode': 'all'}])
    result = convert(value, context)
    assert result['issues'] == [] and not any(row['kind'] == 'fork' for row in result['graph']['nodes'])
    assert result['source_mapping']['edges'][0]['kind'] == 'wait'
    assert result['source_mapping']['edges'][0]['origin_arrivals'][0]['source_node_id'] == 'start'


def test_explicit_loop_is_finite_and_keeps_native_back_edge_contract():
    value, context = source_shape(['check', 'archive'], [
        {'kind': 'condition', 'from_node': 'check', 'to_node': 'check', 'condition': '尚需再次核对时重复当前步骤。'},
        {'kind': 'condition', 'from_node': 'check', 'to_node': 'archive', 'condition': '核对完成后保留归档记录。'}])
    supplied = bindings(value); supplied['conditions'] = [{'true_edge_index': 0, 'false_edge_index': 1, 'predicate_id': 'redo'}]
    supplied['nodes']['check']['output_schema']['properties']['redo'] = {'type': 'boolean'}
    supplied['nodes']['check']['output_schema']['required'].append('redo')
    supplied['references']['redo'] = {'kind': 'boolean', 'nullable': False, 'source': 'output', 'field': 'redo', 'node_id': 'check'}
    supplied['predicates']['redo'] = {'op': 'ref', 'key': 'redo'}
    supplied['loops'] = [{'edge_index': 0, 'max_iterations': 2}]
    result = convert(value, context, supplied); assert not result['issues']
    state = kernel.new_run(result['graph'], {'source_input': '公开输入'}, 1)
    state = finish(result, state, 'check', {'output_0': '公开复核', 'redo': True})
    assert pending_sources(result, state) == {'check'}
    state = finish(result, state, 'check', {'output_0': '公开复核', 'redo': False})
    assert pending_sources(result, state) == {'archive'}
    assert finish(result, state, 'archive')['status'] == 'succeeded'
    supplied['loops'] = []
    assert convert(value, context, supplied)['graph'] is None  # No default loop bound.


@pytest.mark.parametrize('limit', [True, 2.0, 0, 101])
def test_loop_limits_are_strict_and_not_inferred_on_a_forward_edge(limit):
    value, context = chain(2); supplied = bindings(value)
    supplied['loops'] = [{'edge_index': 0, 'max_iterations': limit}]
    result = convert(value, context, supplied)
    assert result['graph'] is None and 'loop_binding_invalid' in codes(result)


@pytest.mark.parametrize('actor', ['business_agent', 'perception_agent', 'data_agent'])
def test_three_ability_roles_use_typed_pins_but_pure_module_never_grants_real_capability_authority(actor):
    value, context = scene(); value['business']['flows'][0]['nodes'][0]['actor'] = actor
    supplied = bindings(value); result = convert(value, context, supplied)
    assert result['issues'] == []
    graph_node = next(row for row in result['graph']['nodes'] if row['kind'] == 'ability')
    assert graph_node['execution'] == supplied['nodes']['review']['execution']
    assert all(result[key] is False for key in subject.FLAGS)
    supplied['nodes']['review']['execution']['agent_role'] = 'data' if actor != 'data_agent' else 'business'
    assert 'agent_role_mismatch' in codes(convert(value, context, supplied))


def test_external_schema_reference_is_rejected_before_constant_validation_without_any_network(monkeypatch):
    value, context = scene(); value['business']['flows'][0]['nodes'][0]['actor'] = 'business_agent'
    supplied = bindings(value); execution = supplied['nodes']['review']['execution']
    execution['input_schema']['properties']['data'] = {'$ref': 'https://example.invalid/not-a-local-schema'}
    execution['arguments'] = {'data': '公开常量'}; execution['input_bindings'] = {}
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('network'); raise AssertionError('No network retrieval, including blocked inputs')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    result = convert(value, context, supplied)
    assert result['graph'] is None and 'ability_argument_invalid' in codes(result)
    assert attempted == []


@pytest.mark.parametrize('consumed', [True, False])
def test_ability_original_input_is_actually_consumed_not_only_a_decorative_reference(consumed):
    value, context = scene(); node = value['business']['flows'][0]['nodes'][0]
    node['actor'] = 'business_agent'; supplied = bindings(value)
    execution = supplied['nodes']['review']['execution']
    if not consumed:
        # The original ref still exists, so an existence-only check would pass.
        execution['input_bindings'] = {}; execution['arguments'] = {'data': '不同的固定常量'}
    result = convert(value, context, supplied)
    if consumed:
        assert result['issues'] == [] and result['graph'] is not None
        runtime = next(row for row in result['graph']['nodes'] if row['kind'] == 'ability')
        assert runtime['execution']['input_bindings']['data'] == 'source_input'
    else:
        assert result['graph'] is None and 'original_input_not_consumed' in codes(result)
        assert result['source_mapping']['nodes'][0]['input_ports'][0]['reference_key'] == 'source_input'


def test_explicit_two_original_input_labels_may_share_one_actually_consumed_reference():
    value, context = scene(); node = value['business']['flows'][0]['nodes'][0]
    node['actor'] = 'business_agent'; node['inputs'].append('人工明确关联的另一输入名称')
    supplied = bindings(value); result = convert(value, context, supplied)
    assert result['issues'] == [] and result['graph'] is not None
    assert len(result['source_mapping']['nodes'][0]['input_ports']) == 2
    assert {row['reference_key'] for row in result['source_mapping']['nodes'][0]['input_ports']} == {'source_input'}


def test_actual_additional_server_input_is_not_removed_by_original_port_consumption_check():
    value, context = scene(); value['business']['flows'][0]['nodes'][0]['actor'] = 'business_agent'
    supplied = bindings(value); execution = supplied['nodes']['review']['execution']
    supplied['input_schema']['properties']['task_identifier'] = {'type': 'string'}
    supplied['input_schema']['required'].append('task_identifier')
    supplied['references']['task_identifier'] = {'kind': 'string', 'nullable': False,
        'source': 'input', 'field': 'task_identifier', 'node_id': None}
    execution['input_schema']['properties']['task_id'] = {'type': 'string'}
    execution['input_schema']['required'].append('task_id')
    execution['input_bindings']['task_id'] = 'task_identifier'
    result = convert(value, context, supplied)
    assert result['issues'] == [] and result['graph'] is not None
    runtime = next(row for row in result['graph']['nodes'] if row['kind'] == 'ability')
    assert runtime['execution']['input_bindings'] == {'data': 'source_input', 'task_id': 'task_identifier'}


@pytest.mark.parametrize('actor', ['task_center', 'execution_service'])
def test_orchestrator_and_execution_service_are_never_relabelled_as_an_agent_or_deleted(actor):
    value, context = scene(); value['business']['flows'][0]['nodes'][0]['actor'] = actor
    result = convert(value, context)
    assert result['graph'] is None and 'unsupported_actor' in codes(result)
    assert result['source_value']['business']['flows'][0]['nodes'][0]['actor'] == actor


@pytest.mark.parametrize('fault', ['missing_input', 'missing_output', 'optional_output', 'wrong_ref', 'extra_node',
    'renamed_role', 'long_role', 'guard_unknown', 'reference_nullable', 'legacy_execution', 'collapsed_outputs'])
def test_missing_or_mutated_ports_roles_schemas_and_legacy_execution_cannot_pass(fault):
    value, context = scene(); supplied = bindings(value); row = supplied['nodes']['review']
    if fault == 'missing_input': row['input_ports'] = {}
    elif fault == 'missing_output': row['output_ports'] = {}
    elif fault == 'optional_output': row['output_schema']['required'] = []
    elif fault == 'wrong_ref': row['input_ports'][next(iter(row['input_ports']))] = 'not_declared'
    elif fault == 'extra_node': supplied['nodes']['extra'] = copy.deepcopy(row)
    elif fault == 'renamed_role': row['role'] = '另外的岗位'
    elif fault == 'long_role': value['business']['flows'][0]['nodes'][0]['role'] = '岗位' * 60; row['role'] = '岗位' * 60
    elif fault == 'guard_unknown': row['guards']['precondition'] = 'not_declared'
    elif fault == 'reference_nullable': supplied['references']['source_input']['nullable'] = True
    elif fault == 'legacy_execution': row['execution'] = {'capability_id': 'legacy', 'bindings': {}, 'mode': '自动运行'}
    else:
        node = value['business']['flows'][0]['nodes'][0]; node['outputs'].append('另一个原产出')
        row['output_ports']['另一个原产出'] = 'output_0'
    result = convert(value, context, supplied)
    assert result['graph'] is None and result['issues']


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'producer', 'boolean_index', 'wrong_output_field'])
def test_every_deliverable_has_its_original_producer_output_and_exact_goal_mapping(fault):
    value, context = chain(2); supplied = bindings(value)
    if fault == 'missing': supplied['goals'] = []
    elif fault == 'duplicate': supplied['goals'].append({**copy.deepcopy(supplied['goals'][0]), 'id': 'another'})
    elif fault == 'producer': supplied['goals'][0]['required_nodes'] = ['step_0']
    elif fault == 'boolean_index': supplied['goals'][0]['deliverable_indices'] = [False]
    else: supplied['nodes']['step_1']['output_ports']['核查记录'] = 'not_actual'
    result = convert(value, context, supplied)
    assert result['graph'] is None and result['issues']


@pytest.mark.parametrize('trigger', ['schedule', 'event', 'data_arrival'])
def test_trigger_text_does_not_create_a_scheduler_or_event_authority(trigger):
    value, context = scene(); value['business']['flows'][0]['trigger']['kind'] = trigger
    result = convert(value, context)
    assert result['graph'] is None and 'trigger_not_connected' in codes(result)


def test_all_flows_quotes_and_duplicate_endpoint_edges_are_not_silently_collapsed():
    value, context = chain(2); selected = value['business']['flows'][0]
    another = copy.deepcopy(selected); another['flow_id'] = 'another_flow'; another['name'] = '另一个独立流程'
    value['business']['flows'].append(another); supplied = bindings(value)
    result = convert(value, context, supplied)
    assert result['issues'] == [] and result['unselected_flow_ids'] == ['another_flow']
    assert result['source_value'] == value and len(result['source_mapping']['flows']) == 2
    assert result['source_mapping']['evidence'][0]['quote_hash']
    duplicate = copy.deepcopy(selected['edges'][0]); duplicate['attribution']['reason'] = '另一条独立保存的依据，不能按端点静默去重。'
    selected['edges'].append(duplicate)
    blocked = convert(value, context, supplied)
    assert blocked['graph'] is None and len(blocked['source_mapping']['edges']) == 2


@pytest.mark.parametrize('tamper', ['graph', 'source', 'mapping', 'bindings', 'flags'])
def test_resealing_a_changed_translation_never_substitutes_for_full_rebuild(tamper):
    value, context = scene(); supplied = bindings(value); result = convert(value, context, supplied)
    if tamper == 'graph': result['graph']['nodes'][0]['actor_id'] = 'different_actor'
    elif tamper == 'source': result['source_value']['evidence'][0]['quote'] = '不是原引用'
    elif tamper == 'mapping': result['source_mapping']['nodes'] = []
    elif tamper == 'bindings': result['execution_bindings']['nodes']['review']['actor_id'] = 'different_actor'
    else: result['can_execute'] = True
    result['translation_hash'] = subject._hash({k: v for k, v in result.items() if k != 'translation_hash'})
    with pytest.raises(ValueError):
        subject.check_execution_graph(result, value, result['flow_id'], supplied, scope_binding=context['scope_binding'])


def test_wrong_scope_binding_and_invalid_source_wire_are_not_saved_as_a_successful_translation():
    value, context = scene(); wrong = copy.deepcopy(context['scope_binding']); wrong['plan_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        subject.build_execution_graph(value, value['business']['flows'][0]['flow_id'], bindings(value), scope_binding=wrong)
    value['business']['flows'][0]['nodes'][0]['private_note'] = 'extra source field'
    with pytest.raises(ValueError): convert(value, context)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), object(), (1, 2)])
def test_non_json_or_nonfinite_bindings_are_rejected_without_repair(bad):
    value, context = scene(); supplied = bindings(value); supplied['extra'] = bad
    with pytest.raises(ValueError): convert(value, context, supplied)
