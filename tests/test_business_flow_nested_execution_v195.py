"""Independent V195 compiler through the actual existing graph reducer.

Synthetic registered-looking pins are not registry/IAM/adoption proof. Root
executes these tests; real human input presentation and Task gates are separate.
"""
import copy
import json

import pytest

from generic_agents import business_flow_nested_execution_v195 as compiler
from generic_agents import business_flow_nested_execution_v192 as previous
from generic_agents import business_flow_nested_causal_v192 as causal
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from tests.test_business_flow_nested_execution_v192 import scene as original_scene, ids, finish, ready
from tests.test_report_business_flow_wire_v184 import no_external


def scene():
    plan, source, bindings, sidecar, paths = original_scene()
    completion = '/business/flows/0/completion'
    plan['business']['flows'][0]['completion']['inputs'] = ['当前实际分支的处置结果']
    bindings['schema'] = compiler.BINDINGS
    bindings['nodes'][completion]['input_ports'] = [{'ordinal': 0, 'reference': 'branch_result'}]
    candidates = [{'node_id': paths['通知责任人员'], 'field': 'result'},
        {'node_id': paths['记录退回理由'], 'field': 'result'}]
    bindings['references']['branch_result'] = {'schema': kernel.EXCLUSIVE_REFERENCE,
        'source': 'exclusive_output', 'kind': 'string', 'nullable': False, 'unit': None,
        'consumer_node_id': completion, 'candidates': candidates}
    sidecar['source_plan_hash'] = digest(plan)
    sidecar['input_bindings'] = [{'input_path': completion + '/inputs/0',
        'contract': {'data_type': 'string', 'unit': None},
        'sources': [row['node_id'] + '/outputs/0' for row in candidates]}]
    return plan, source, bindings, sidecar, paths


def build(values):
    plan, source, bindings, sidecar, _ = values
    return compiler.build_execution_graph(plan, 0, bindings,
        scope_binding=source['scope_binding'], causal_bindings=sidecar)


@pytest.mark.parametrize('approved', [True, False])
def test_actual_conditional_candidate_can_finish_without_a_fake_human_join_or_first_value(approved):
    values = scene(); before = copy.deepcopy(values)
    result = build(values); graph = result['graph']; names = ids(result, values[4])
    completion = next(row['graph_node_id'] for row in result['source_mapping']['actions']
        if row['ast_path'] == '/business/flows/0/completion')
    assert result['schema'] == compiler.SCHEMA and result['execution_bindings']['schema'] == compiler.BINDINGS
    ref = graph['references']['branch_result']
    assert ref['schema'] == kernel.EXCLUSIVE_REFERENCE and ref['consumer_node_id'] == completion
    assert [row['node_id'] for row in ref['candidates']] == [names['通知责任人员'], names['记录退回理由']]
    state = finish(graph, kernel.new_run(graph, {}, 10), names['确认修复建议'], {'approved': approved})
    expected = '独立通知的实际回执' if approved else '退回分支的实际记录'
    producer_name = '通知责任人员' if approved else '记录退回理由'
    if approved:
        state = finish(graph, state, names['组织通知与补核'])
        state = finish(graph, state, names['通知责任人员'], {'result': expected})
        assert completion not in ready(state)
        state = finish(graph, state, names['核对飞行条件'], {'approved': False})
    else:
        state = finish(graph, state, names['记录退回理由'], {'result': expected})
    assert completion in ready(state)
    activation = next(a for a in state['activations'].values() if a['node_id'] == completion)
    values_at_node, producers = kernel._resolution(graph, state, kernel._metadata(graph),
        activation['context'], consumer_node_id=completion)
    assert values_at_node['branch_result'] == expected
    assert state['activations'][producers['branch_result']]['node_id'] == names[producer_name]
    assert activation['inputs_snapshot'] is None  # Human input UI belongs to Root's formal adapter.
    state = finish(graph, state, completion)
    assert state['status'] == 'succeeded'
    assert kernel.check_run(graph, json.loads(json.dumps(state))) == state
    assert values == before and all(result[key] is False for key in compiler.FLAGS)
    assert compiler.check_execution_graph(result, values[0], 0, values[2],
        scope_binding=values[1]['scope_binding'], causal_bindings=values[3]) == result


def test_old_v192_still_rejects_the_same_alternative_instead_of_being_relaxed():
    plan, source, bindings, sidecar, _ = scene()
    bindings['schema'] = previous.BINDINGS
    with pytest.raises(ValueError, match='alternative_value_binding_not_supported'):
        previous.build_execution_graph(plan, 0, bindings,
            scope_binding=source['scope_binding'], causal_bindings=sidecar)
    assert previous.BINDINGS == 'business-flow-nested-execution-bindings-v192'
    assert previous.SCHEMA == 'business-flow-nested-execution-v192'


@pytest.mark.parametrize('fault', ['candidate_order', 'missing_candidate', 'extra_candidate', 'consumer',
    'kind', 'nullable', 'unit', 'extra_ref', 'hidden_dependency', 'old_marker', 'foreign_source'])
def test_exact_candidate_mapping_and_contract_are_required_for_compilation(fault):
    values = scene(); plan, _, bindings, sidecar, paths = values
    ref = bindings['references']['branch_result']
    if fault == 'candidate_order': ref['candidates'].reverse()
    elif fault == 'missing_candidate': ref['candidates'].pop()
    elif fault == 'extra_candidate': ref['candidates'].append(copy.deepcopy(ref['candidates'][0]))
    elif fault == 'consumer': ref['consumer_node_id'] = paths['通知责任人员']
    elif fault == 'kind': ref['kind'] = 'number'
    elif fault == 'nullable': ref['nullable'] = 0
    elif fault == 'unit': ref['unit'] = '℃'
    elif fault == 'extra_ref': ref['fallback'] = 'first'
    elif fault == 'hidden_dependency': bindings['references']['hidden'] = copy.deepcopy(ref)
    elif fault == 'old_marker': bindings['schema'] = previous.BINDINGS
    else: ref['candidates'][0]['node_id'] = '/business/flows/1/completion'
    with pytest.raises(ValueError): build(values)


def test_mixed_entry_and_output_is_explicitly_rejected_even_if_declared_availability_passes():
    values = scene(); plan, source, bindings, sidecar, _ = values
    contract = {'data_type': 'string', 'unit': None}
    sidecar['entry_ports'] = [{'path': '/entry_ports/0', 'flow_path': '/business/flows/0', 'contract': contract}]
    sidecar['input_bindings'][0]['sources'][1] = '/entry_ports/0'
    bindings['input_schema'] = {'type': 'object', 'properties': {'entry': {'type': 'string'}},
        'required': ['entry'], 'additionalProperties': False}
    bindings['references']['entry'] = {'kind': 'string', 'nullable': False,
        'source': 'input', 'field': 'entry', 'node_id': None}
    bindings['entries'] = {'/entry_ports/0': 'entry'}
    assert causal.analyze_availability(plan, sidecar, scope_binding=source['scope_binding'])['consistent'] is True
    with pytest.raises(ValueError, match='exclusive_output_only'): build(values)


def test_distinct_original_output_ordinals_cannot_be_collapsed_into_one_runtime_field():
    values = scene(); plan, _, bindings, sidecar, paths = values
    path = paths['通知责任人员']
    action = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    action['outputs'].append('另一份独立原始产出')
    bindings['nodes'][path]['output_ports'].append({'ordinal': 1, 'field': 'result'})
    sidecar['source_plan_hash'] = digest(plan)
    sidecar['output_contracts'].append({'path': path + '/outputs/1', 'contract': {'data_type': 'string', 'unit': None}})
    with pytest.raises(ValueError, match='output_ordinals_collapsed'): build(values)


def test_service_actor_is_preserved_and_explicitly_blocked_until_real_delegation_is_registered():
    values = scene(); plan, _, _, sidecar, _ = values
    action = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    action['actor'] = 'task_center'; sidecar['source_plan_hash'] = digest(plan)
    before = copy.deepcopy(plan)
    with pytest.raises(ValueError, match='service_actor_not_registered'): build(values)
    assert plan == before and action['actor'] == 'task_center'
