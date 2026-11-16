"""Public synthetic plans through the actual existing reducer, not tool receipts.

No human qualification, source truth, member/capability authority, application
database, model or device is represented as verified by these kernel tests.
"""
import copy
import json

import pytest

from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import business_flow_nested_causal_v192 as causal
from generic_agents import business_flow_nested_execution_v192 as compiler
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from tests.test_report_business_flow_wire_v184 import scene as old_scene


def guards():
    return {key: None for key in ('start', 'precondition', 'stop', 'exception')}


def schema(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def scene(*, nullable=False, mode='core'):
    plan, source, old_action, _, attribution = old_scene(2)
    plan['schema'] = plans.PLAN

    def action(name, actor='human'):
        role = {'human': '资料核查人员', 'business_agent': '业务驱动智能体',
            'perception_agent': '多模感知智能体'}[actor]
        return old_action(name, actor=actor, role=role, inputs=[], outputs=[name + '记录'])

    def steps(*actions):
        return {'kind': 'steps', 'attribution': copy.deepcopy(attribution), 'steps': list(actions)}

    def decision(name, yes, no):
        return {'kind': 'decision', 'attribution': copy.deepcopy(attribution), 'decision': action(name),
            'branches': [{'condition': '结果为肯定', 'attribution': copy.deepcopy(attribution), 'phases': yes},
                {'condition': '结果为否定', 'attribution': copy.deepcopy(attribution), 'phases': no}]}

    observe = decision('核对飞行条件', [steps(action('开展补充观察', 'perception_agent'))], [])
    parallel = {'kind': 'parallel', 'attribution': copy.deepcopy(attribution),
        'dispatch': action('组织通知与补核', 'business_agent'),
        'branches': [{'phases': [steps(action('通知责任人员', 'business_agent'))]}, {'phases': [observe]}],
        'wait_mode': 'all', 'join_attribution': copy.deepcopy(attribution)}
    plan['business']['flows'][0]['phases'] = [decision('确认修复建议', [parallel], [steps(action('记录退回理由'))])]
    if mode == 'loop':
        plan['business']['flows'][0]['phases'] = [{'kind': 'repeat', 'attribution': copy.deepcopy(attribution),
            'steps': [action('修订核查材料')], 'check': action('核对飞行条件'),
            'repeat_if': '核查结果要求再次修订', 'finish_if': '核查结果不要求再次修订'}]
    elif mode == 'nested_forks':
        inner = copy.deepcopy(parallel)
        inner['dispatch'] = action('组织内部复核', 'business_agent')
        inner['branches'] = [{'phases': [steps(action('复核一', 'perception_agent'))]},
            {'phases': [steps(action('复核二', 'business_agent'))]}]
        parallel['branches'][1]['phases'] = [inner]
        plan['business']['flows'][0]['phases'] = [parallel]
    plan['business']['flows'][0]['completion'] = action('记录本次处理结果')
    walked = plans.walk_action_ports(plan, scope_binding=source['scope_binding'])
    actions = walked['actions']
    paths = {x['name']: x['path'] for x in actions}
    nodes, output_contracts = {}, []
    for item in actions:
        boolean = item['name'] in {'确认修复建议', '核对飞行条件'}
        field, kind = ('approved', 'boolean') if boolean else ('result', 'string')
        output = schema(**{field: {'type': ['boolean', 'null'] if boolean and nullable else kind}})
        row = {'kind': 'human' if item['actor'] == 'human' else 'ability',
            'actor_id': 'public_actor_' + str(len(nodes)), 'guards': guards(), 'input_ports': [],
            'output_ports': [{'ordinal': 0, 'field': field}]}
        if row['kind'] == 'human':
            row.update(role=item['role'], output_schema=output)
        else:
            row['execution'] = {'capability_id': 'public_synthetic_capability', 'capability_hash': 'a' * 64,
                'agent_role': compiler.ACTORS[item['actor']], 'input_bindings': {}, 'arguments': {},
                'input_schema': schema(), 'output_schema': output, 'adapter': 'builtin',
                'release_version': '1.0.0', 'max_attempts': 1}
        nodes[item['path']] = row
        output_contracts.extend({'path': port['path'], 'contract': {'data_type': kind, 'unit': None}}
            for port in item['output_ports'])
    references = {name: {'kind': 'boolean', 'nullable': nullable, 'source': 'output',
        'node_id': paths[label], 'field': 'approved'} for name, label in
        [('approval', '确认修复建议'), ('flight', '核对飞行条件')] if label in paths}
    predicates = {name: {'op': 'eq', 'left': {'op': 'ref', 'key': name},
        'right': {'op': 'value', 'kind': 'boolean', 'value': True}} for name in references}
    bindings = {'schema': compiler.BINDINGS, 'input_schema': schema(), 'references': references,
        'predicates': predicates, 'guards': guards(), 'nodes': nodes, 'entries': {},
        'contract_schemas': [{'contract': {'data_type': kind, 'unit': None},
            'kind': kind, 'nullable': nullable if kind == 'boolean' else False}
            for kind in sorted({x['contract']['data_type'] for x in output_contracts})],
        'conditions': [{'path': paths[label].rsplit('/decision', 1)[0], 'predicate_id': name,
            'true_branch_index': 0, 'false_branch_index': 1} for name, label in
            [('approval', '确认修复建议'), ('flight', '核对飞行条件')] if label in paths and paths[label].endswith('/decision')],
        'loops': [{'path': '/business/flows/0/phases/0', 'predicate_id': 'flight',
            'repeat_on': 'true', 'max_iterations': 1}] if mode == 'loop' else []}
    sidecar = {'schema': causal.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(source['scope_binding']), 'entry_ports': [],
        'output_contracts': output_contracts, 'input_bindings': []}
    return plan, source, bindings, sidecar, paths


def build(values):
    p, s, b, c, _ = values
    return compiler.build_execution_graph(p, 0, b, scope_binding=s['scope_binding'], causal_bindings=c)


def ids(result, paths):
    mapping = {x['ast_path']: x['graph_node_id'] for x in result['source_mapping']['actions']}
    return {name: mapping[path] for name, path in paths.items()}


def ready(state):
    return {state['activations'][key]['node_id'] for key in [*state['ready_human_ids'], *state['eligible_ability_ids']]}


def finish(graph, state, node_id, output=None, request_id=None):
    matches = [x for x in state['activations'].values() if x['node_id'] == node_id and x['status'] == 'ready']
    assert len(matches) == 1
    a = matches[0]
    node = next(x for x in graph['nodes'] if x['id'] == node_id)
    return kernel.complete(graph, state, a['id'], output or {'result': 'public synthetic result'},
        request_id or 'complete_' + a['id'], node['actor_id'], a['version'], a['epoch'], state['clock'])


def test_conditional_parallel_starts_notification_before_slow_flight_check_and_empty_branch_arrives_once():
    values = scene(); result = build(values); graph = result['graph']; names = ids(result, values[4])
    before = copy.deepcopy(values)
    state = kernel.new_run(graph, {}, 10)
    state = finish(graph, state, names['确认修复建议'], {'approved': True})
    state = finish(graph, state, names['组织通知与补核'])
    assert ready(state) == {names['通知责任人员'], names['核对飞行条件']}
    state = finish(graph, state, names['通知责任人员'])
    assert names['核对飞行条件'] in ready(state) and names['记录本次处理结果'] not in ready(state)
    state = finish(graph, state, names['核对飞行条件'], {'approved': False})
    assert ready(state) == {names['记录本次处理结果']}
    assert not any(x['node_id'] == names['开展补充观察'] for x in state['activations'].values())
    scopes = list(state['fork_scopes'].values()); assert len(scopes) == 1
    assert scopes[0]['closed'] is True and set(scopes[0]['arrived']) == {'b_0', 'b_1'}
    state = finish(graph, state, names['记录本次处理结果'])
    assert state['status'] == 'succeeded'
    assert kernel.check_run(graph, json.loads(json.dumps(state))) == state
    assert values == before and result['can_execute'] is False and result['entry_facts_verified'] is False


@pytest.mark.parametrize('notify_first', [True, False])
def test_both_real_kernel_branches_must_finish_regardless_of_completion_order(notify_first):
    values = scene(); result = build(values); graph = result['graph']; names = ids(result, values[4])
    state = finish(graph, kernel.new_run(graph, {}, 10), names['确认修复建议'], {'approved': True})
    state = finish(graph, state, names['组织通知与补核'])
    state = finish(graph, state, names['核对飞行条件'], {'approved': True})
    assert ready(state) == {names['通知责任人员'], names['开展补充观察']}
    first, second = ('通知责任人员', '开展补充观察') if notify_first else ('开展补充观察', '通知责任人员')
    state = finish(graph, state, names[first]); assert ready(state) == {names[second]}
    state = finish(graph, state, names[second]); assert ready(state) == {names['记录本次处理结果']}


def test_negative_confirmation_never_creates_dispatch_or_either_observation_branch():
    values = scene(); result = build(values); graph = result['graph']; names = ids(result, values[4])
    state = finish(graph, kernel.new_run(graph, {}, 10), names['确认修复建议'], {'approved': False})
    assert ready(state) == {names['记录退回理由']} and state['eligible_ability_ids'] == []
    assert {x['node_id'] for x in state['activations'].values()}.isdisjoint(
        {names['组织通知与补核'], names['通知责任人员'], names['核对飞行条件'], names['开展补充观察']})
    state = finish(graph, state, names['记录退回理由'])
    assert ready(state) == {names['记录本次处理结果']}


def test_unknown_confirmation_does_not_default_to_positive_and_can_be_cancelled():
    values = scene(nullable=True); result = build(values); graph = result['graph']; names = ids(result, values[4])
    state = finish(graph, kernel.new_run(graph, {}, 10), names['确认修复建议'], {'approved': None})
    assert ready(state) == set() and state['status'] == 'waiting'
    assert any(x['result']['truth'] == 'unknown' for x in state['predicate_receipts'])
    cancelled = kernel.cancel(graph, state, 'cancel_unknown', 'public_operator', state['version'], state['cancel_epoch'], 10)
    assert cancelled['status'] == 'cancelled' and ready(cancelled) == set()


def test_exact_kernel_command_recovery_does_not_duplicate_parallel_activations():
    values = scene(); result = build(values); graph = result['graph']; names = ids(result, values[4])
    initial = kernel.new_run(graph, {}, 10)
    a = next(x for x in initial['activations'].values() if x['node_id'] == names['确认修复建议'])
    actor = next(x['actor_id'] for x in graph['nodes'] if x['id'] == a['node_id'])
    first = kernel.complete(graph, initial, a['id'], {'approved': True}, 'approval_once', actor, 0, 0, 10)
    assert kernel.complete(graph, first, a['id'], {'approved': True}, 'approval_once', actor, 0, 0, 10) == first
    with pytest.raises(ValueError):
        kernel.complete(graph, first, a['id'], {'approved': False}, 'approval_once', actor, 0, 0, 10)


@pytest.mark.parametrize('change,code', [
    ('missing_action', 'action_coverage'), ('wrong_role', 'human_role'),
    ('constant_approval', 'decision_comparison_shape'), ('foreign_predicate', 'decision_comparison_shape'),
    ('same_outcome', 'binary_outcomes'), ('any', 'any_join_not_supported'),
    ('missing_output', 'port_count'), ('extra_entry', 'entry_coverage')])
def test_incomplete_or_unsupported_bindings_never_produce_partial_runtime_graph(change, code):
    values = scene(); p, s, b, c, paths = values
    if change == 'missing_action': del b['nodes'][paths['通知责任人员']]
    elif change == 'wrong_role': b['nodes'][paths['确认修复建议']]['role'] = '其它岗位'
    elif change == 'constant_approval': b['predicates']['approval'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    elif change == 'foreign_predicate':
        b['predicates']['approval'] = {'op': 'all', 'args': [b['predicates']['approval'], b['predicates']['flight']]}
    elif change == 'same_outcome': b['conditions'][0]['false_branch_index'] = 0
    elif change == 'any':
        p['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['wait_mode'] = 'any'
        c['source_plan_hash'] = digest(p)
    elif change == 'missing_output': b['nodes'][paths['确认修复建议']]['output_ports'] = []
    else: b['entries']['/entry_ports/99'] = 'not_actual'
    with pytest.raises(ValueError, match=code): build(values)


def test_original_service_actor_is_not_renamed_as_an_agent_to_fit_the_runtime():
    values = scene(); p, _, _, c, paths = values
    original = p['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    original['actor'] = 'task_center'; c['source_plan_hash'] = digest(p)
    with pytest.raises(ValueError, match='service_actor_not_registered'): build(values)


def test_predicate_cannot_read_a_different_boolean_field_than_the_actual_bound_confirmation_port():
    values = scene(); _, _, b, _, paths = values
    row = b['nodes'][paths['确认修复建议']]
    row['output_schema'] = schema(approved={'type': 'boolean'}, other={'type': 'boolean'})
    row['output_ports'][0]['field'] = 'other'
    # Both booleans exist and are required, but only `other` is the original
    # confirmation's declared port. Merely sharing a producer is insufficient.
    with pytest.raises(ValueError, match='predicate_dependency_not_consumed'): build(values)


@pytest.mark.parametrize('hidden_guard', [False, True])
def test_hidden_future_output_cannot_be_added_via_execution_input_or_node_guard(hidden_guard):
    values = scene(); _, _, b, _, paths = values
    row = b['nodes'][paths['通知责任人员']]
    if hidden_guard:
        row['guards']['precondition'] = 'flight'
    else:
        execution = row['execution']
        execution['input_schema'] = schema(future={'type': 'boolean'})
        execution['input_bindings'] = {'future': 'flight'}
    code = 'undeclared_guard_dependency' if hidden_guard else 'input_binding_coverage'
    with pytest.raises(ValueError, match=code): build(values)


def test_extra_live_input_must_be_declared_in_the_original_causal_entry_and_input_ports():
    values = scene(); _, _, b, _, paths = values
    b['input_schema'] = schema(extra={'type': 'string'})
    b['references']['extra'] = {'source': 'input', 'field': 'extra', 'node_id': None,
        'kind': 'string', 'nullable': False}
    row = b['nodes'][paths['通知责任人员']]
    row['execution']['input_schema'] = schema(extra={'type': 'string'})
    row['execution']['input_bindings'] = {'extra': 'extra'}
    with pytest.raises(ValueError, match='input_binding_coverage'): build(values)


@pytest.mark.parametrize('decorated', [False, True])
def test_result_reference_cannot_decorate_a_tautology_to_dispatch_a_rejected_confirmation(decorated):
    values = scene(); b = values[2]
    if decorated:
        b['predicates']['approval'] = {'op': 'any', 'args': [
            {'op': 'value', 'kind': 'boolean', 'value': True}, b['predicates']['approval']]}
    else:
        b['predicates']['approval']['right'] = {'op': 'ref', 'key': 'approval'}
    with pytest.raises(ValueError, match='decision_comparison_shape'): build(values)


def test_actual_output_schema_cannot_contradict_the_bound_causal_contract():
    values = scene(); b, paths = values[2], values[4]
    b['nodes'][paths['记录本次处理结果']]['output_schema'] = schema(result={'type': 'integer'})
    with pytest.raises(ValueError, match='contract_runtime_type'): build(values)


@pytest.mark.parametrize('actual_unit', ['m', 'cm', None])
def test_named_contract_has_explicit_codec_and_exact_unit_metadata_without_guessed_conversion(actual_unit):
    values = scene(); _, _, b, c, paths = values
    contract = {'data_type': '观测距离', 'unit': 'm'}
    for row in c['output_contracts']:
        if row['path'] == paths['记录本次处理结果'] + '/outputs/0': row['contract'] = contract
    b['contract_schemas'].append({'contract': contract, 'kind': 'number', 'nullable': False})
    field = {'type': 'number'}
    if actual_unit is not None: field['x-unit'] = actual_unit
    b['nodes'][paths['记录本次处理结果']]['output_schema'] = schema(result=field)
    if actual_unit == 'm':
        result = build(values)
        node_id = ids(result, paths)['记录本次处理结果']
        node = next(x for x in result['graph']['nodes'] if x['id'] == node_id)
        assert node['output_schema']['properties']['result']['x-unit'] == 'm'
        assert result['entry_facts_verified'] is False
    else:
        with pytest.raises(ValueError, match='contract_runtime_unit'): build(values)


@pytest.mark.parametrize('change', ['none', 'unit', 'type', 'nullable'])
def test_actual_ability_consumes_the_bound_entry_value_with_exact_target_type_and_unit(change):
    values = scene(); p, _, b, c, paths = values
    original = p['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    original['inputs'] = ['观测距离']
    contract = {'data_type': '观测距离', 'unit': 'm'}
    c['source_plan_hash'] = digest(p)
    c['entry_ports'] = [{'path': '/entry_ports/0', 'flow_path': '/business/flows/0', 'contract': contract}]
    c['input_bindings'] = [{'input_path': paths['通知责任人员'] + '/inputs/0', 'contract': contract, 'sources': ['/entry_ports/0']}]
    b['contract_schemas'].append({'contract': contract, 'kind': 'number', 'nullable': False})
    b['input_schema'] = schema(distance={'type': 'number', 'x-unit': 'm'})
    b['references']['distance'] = {'source': 'input', 'field': 'distance', 'node_id': None, 'kind': 'number', 'nullable': False}
    b['entries'] = {'/entry_ports/0': 'distance'}
    row = b['nodes'][paths['通知责任人员']]
    row['input_ports'] = [{'ordinal': 0, 'reference': 'distance'}]
    target = {'type': 'number', 'x-unit': 'm'}
    if change == 'unit': target['x-unit'] = 'cm'
    elif change == 'type': target['type'] = 'integer'
    elif change == 'nullable': target['type'] = ['number', 'null']
    row['execution']['input_schema'] = schema(data=target)
    row['execution']['input_bindings'] = {'data': 'distance'}
    if change != 'none':
        with pytest.raises(ValueError, match='contract_runtime_unit' if change == 'unit' else 'contract_runtime_type'): build(values)
    else:
        result = build(values); graph = result['graph']; names = ids(result, paths)
        state = finish(graph, kernel.new_run(graph, {'distance': 12.5}, 10), names['确认修复建议'], {'approved': True})
        state = finish(graph, state, names['组织通知与补核'])
        activation = next(x for x in state['activations'].values() if x['node_id'] == names['通知责任人员'])
        assert kernel.ability_inputs(graph, state, activation['id']) == {'data': 12.5}


@pytest.mark.parametrize('change', ['none', 'unit', 'sibling'])
def test_schema_ref_keeps_matching_unit_and_rejects_conflicting_or_ignored_sibling_constraints(change):
    values = scene(); _, _, b, c, paths = values
    contract = {'data_type': '观测距离', 'unit': 'm'}
    for row in c['output_contracts']:
        if row['path'] == paths['记录本次处理结果'] + '/outputs/0': row['contract'] = contract
    b['contract_schemas'].append({'contract': contract, 'kind': 'number', 'nullable': False})
    field = {'$ref': '#/$defs/distance', 'x-unit': 'cm' if change == 'unit' else 'm'}
    if change == 'sibling': field['type'] = 'string'
    output = schema(result=field); output['$defs'] = {'distance': {'type': 'number', 'x-unit': 'm'}}
    b['nodes'][paths['记录本次处理结果']]['output_schema'] = output
    if change == 'none': assert build(values)['graph'] is not None
    else:
        with pytest.raises(ValueError, match='field_schema_unit_conflict' if change == 'unit' else 'field_schema_ref_siblings'): build(values)


def test_nested_forks_preserve_parent_branch_identity_until_both_inner_branches_arrive():
    values = scene(mode='nested_forks'); result = build(values); graph = result['graph']; names = ids(result, values[4])
    state = finish(graph, kernel.new_run(graph, {}, 10), names['组织通知与补核'])
    assert ready(state) == {names['通知责任人员'], names['组织内部复核']}
    state = finish(graph, state, names['通知责任人员'])
    state = finish(graph, state, names['组织内部复核'])
    assert ready(state) == {names['复核一'], names['复核二']}
    state = finish(graph, state, names['复核二'])
    assert ready(state) == {names['复核一']}
    state = finish(graph, state, names['复核一'])
    assert ready(state) == {names['记录本次处理结果']}
    assert len(state['fork_scopes']) == 2 and all(x['closed'] for x in state['fork_scopes'].values())
    assert kernel.check_run(graph, json.loads(json.dumps(state))) == state


@pytest.mark.parametrize('continue_again', [False, True])
def test_repeat_uses_actual_check_output_and_enforces_the_native_bound(continue_again):
    values = scene(mode='loop'); result = build(values); graph = result['graph']; names = ids(result, values[4])
    state = kernel.new_run(graph, {}, 10)
    state = finish(graph, state, names['修订核查材料'])
    state = finish(graph, state, names['核对飞行条件'], {'approved': True})
    assert ready(state) == {names['修订核查材料']}
    state = finish(graph, state, names['修订核查材料'])
    state = finish(graph, state, names['核对飞行条件'], {'approved': continue_again})
    if continue_again:
        assert state['status'] == 'failed' and ready(state) == set()
    else:
        assert ready(state) == {names['记录本次处理结果']}
    assert len([x for x in state['activations'].values() if x['node_id'] == names['修订核查材料']]) == 2


def test_compilation_is_deterministic_and_full_rebuild_rejects_self_resealed_control_edits():
    values = scene(); result = build(values); p, s, b, c, _ = values
    assert compiler.check_execution_graph(result, p, 0, b, scope_binding=s['scope_binding'], causal_bindings=c) == result
    assert build(values) == result
    changed = copy.deepcopy(result); changed['graph']['edges'][0]['to'] = changed['graph']['entry']
    changed['graph_hash'] = digest(changed['graph'])
    changed['translation_hash'] = digest({k: v for k, v in changed.items() if k != 'translation_hash'})
    with pytest.raises(ValueError, match='projection_changed'):
        compiler.check_execution_graph(changed, p, 0, b, scope_binding=s['scope_binding'], causal_bindings=c)


def test_oversized_causal_payload_is_rejected_before_analysis(monkeypatch):
    values = scene(); values[3]['output_contracts'][0]['contract']['data_type'] = 'x' * (kernel.MAX_BYTES + 1)
    def forbidden(*args, **kwargs): raise AssertionError('oversized payload reached analysis')
    monkeypatch.setattr(causal, 'analyze_availability', forbidden)
    with pytest.raises(ValueError): build(values)
