"""Actual native receipt resolution behind human input labels."""
import copy
import pytest
from fastapi import HTTPException
from generic_agents import business_flow_execution_inputs as subject
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import business_flow_execution_graph as converter
from tests.test_business_flow_execution_graph import chain, bindings


def scene(missing=False):
    value, context = chain(2); supplied = bindings(value)
    first, second = value['business']['flows'][0]['nodes']
    second['inputs'] = ['前一步实际核查记录']
    supplied['nodes'][second['node_id']]['input_ports'] = {'前一步实际核查记录': 'prior_record'}
    supplied['references']['prior_record'] = {'kind': 'string', 'nullable': False,
        'source': 'output', 'field': 'output_0', 'node_id': first['node_id']}
    if missing: supplied['input_schema']['required'] = []
    translation = converter.build_execution_graph(value, value['business']['flows'][0]['flow_id'], supplied,
        scope_binding=context['scope_binding'])
    assert translation['issues'] == []
    return {'schema': 'business-flow-execution-binding-v1', 'graph': translation['graph'],
        'execution_bindings': supplied}


def test_bound_original_input_is_actual_context_scoped_prior_receipt_not_latest_arbitrary_output():
    binding = scene(); graph = binding['graph']; state = kernel.new_run(graph, {'source_input': '真实任务输入'}, 1)
    first = state['ready_human_ids'][0]
    actor = next(n['actor_id'] for n in graph['nodes'] if n['id'] == state['activations'][first]['node_id'])
    state = kernel.complete(graph, state, first, {'output_0': '实际已经保存的核查内容'}, 'request_one',
        actor, state['activations'][first]['version'],
        state['activations'][first]['epoch'], 2)
    second = state['ready_human_ids'][0]
    result = subject.describe(binding, state, second)
    assert result == [{'label': '前一步实际核查记录', 'available': True, 'value': '实际已经保存的核查内容',
        'source': 'output', 'producer_activation_id': first, 'schema': {'type': 'string'}}]
    assert subject.require_inputs(binding, state, second) == result


def test_missing_original_human_input_blocks_complete_and_preserves_unchanged_state():
    binding = scene(missing=True); graph = binding['graph']; state = kernel.new_run(graph, {}, 1)
    first = state['ready_human_ids'][0]; before = copy.deepcopy(state)
    values = subject.describe(binding, state, first)
    assert values and all(not item['available'] for item in values)
    with pytest.raises(HTTPException) as error: subject.require_inputs(binding, state, first)
    assert error.value.status_code == 409 and state == before


def test_legacy_binding_is_not_given_new_human_input_authority_or_schema():
    assert subject.describe({'schema': 'report-full-semantic-graph-binding-v4'}, {}, 'irrelevant') == []


def test_local_reference_input_schema_preserves_native_root_context():
    from jsonschema import Draft202012Validator
    value, context = chain(2); supplied = bindings(value)
    supplied['input_schema'] = {'type': 'object', '$ref': '#/$defs/data', '$defs': {
        'data': {'type': 'object', 'properties': {'source_input': {'$ref': '#/$defs/text'}},
            'required': ['source_input']}, 'text': {'type': 'string', 'minLength': 3}}}
    translated = converter.build_execution_graph(value, value['business']['flows'][0]['flow_id'],
        supplied, scope_binding=context['scope_binding'])
    assert translated['issues'] == []
    binding = {'schema': 'business-flow-execution-binding-v1', 'graph': translated['graph'],
        'execution_bindings': supplied}
    state = kernel.new_run(binding['graph'], {'source_input': '已保存的实际输入'}, 1)
    result = subject.require_inputs(binding, state, state['ready_human_ids'][0])
    assert result[0]['value'] == '已保存的实际输入'
    validator = Draft202012Validator(result[0]['schema'])
    assert validator.is_valid(result[0]['value']) and not validator.is_valid('短')
