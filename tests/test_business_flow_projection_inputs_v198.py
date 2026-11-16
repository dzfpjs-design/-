"""Real projected human inputs, not adoption, source truth or execution proof.

The main cases use the actual V195 compiler and native cold-state checker.
The native branch fixture supplies presentation metadata only; its duplicate
producer probe is explicitly corrupt and must never pass a cold read.
"""
import copy
import json

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_inputs as inputs
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import guarded_port_projection_v198 as projection
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_business_flow_nested_port_projection_v198 import (
    scene as port_scene, build, ROOT_FIELD, PRODUCER, CONSUMER, COMPLETION)
from tests.test_business_flow_nested_execution_v192 import finish
from tests.test_full_semantic_graph_v1 import human, finish as finish_native
from tests.test_full_semantic_port_projection_v198 import graph as native_projection_graph
from tests.test_report_business_flow_wire_v184 import no_external


BINDING = 'business-flow-nested-execution-binding-v195'


def human_scene(*, whole=False, selected_schema=None, kind='string', nullable=False,
                require_non_null=True):
    values = port_scene(whole=whole)
    plan, _, bindings, sidecar = values
    flow = plan['business']['flows'][0]
    action = flow['phases'][0]['steps'][1]
    action['actor'] = 'human'
    action['role'] = flow['completion']['role']
    previous = bindings['nodes'][CONSUMER]
    bindings['nodes'][CONSUMER] = {
        'kind': 'human', 'actor_id': previous['actor_id'], 'role': action['role'],
        'guards': copy.deepcopy(previous['guards']),
        'input_ports': copy.deepcopy(previous['input_ports']),
        'output_ports': copy.deepcopy(previous['output_ports']),
        'output_schema': copy.deepcopy(previous['execution']['output_schema'])}
    # The human has one original semantic input and consumes only its declared
    # projection; the old ability's second physical argument is no longer used.
    del bindings['references']['package_detail']
    ref = bindings['references']['package_status']
    ref.update(kind=kind, nullable=nullable)
    ref['projection']['require_non_null'] = require_non_null
    if selected_schema is not None:
        bindings['nodes'][PRODUCER]['output_schema']['properties'][ROOT_FIELD][
            'properties']['status'] = copy.deepcopy(selected_schema)
    sidecar['source_plan_hash'] = digest(plan)
    translated = build(values)
    binding = {'schema': BINDING, 'graph': translated['graph'], 'translation': translated}
    mapping = {row['ast_path']: row['graph_node_id']
        for row in translated['source_mapping']['actions']}
    return values, binding, mapping


def current(state, node_id, status):
    matches = [row for row in state['activations'].values()
        if row['node_id'] == node_id and row['status'] == status]
    assert len(matches) == 1
    return matches[0]


def native_presentation(*, choose=False):
    """Native reducer fixture plus labels, explicitly not V195 adoption proof."""
    graph = native_projection_graph(choose=choose)
    index = next(i for i, row in enumerate(graph['nodes']) if row['id'] == 'consume')
    old = graph['nodes'][index]
    node = human('consume')
    node['actor_id'] = old['actor_id']
    node['output_schema'] = copy.deepcopy(old['execution']['output_schema'])
    graph['nodes'][index] = node
    assert kernel.validate_graph(graph) == graph
    actions = [{'graph_node_id': row['id'],
        'source_action': {'name': '原发布产物' if row['id'] == 'produce' else row['id'],
            'inputs': ['原流程选定资料'] if row['id'] == 'consume' else []},
        'input_ports': [{'ordinal': 0, 'reference': 'context'}]
            if row['id'] == 'consume' else []} for row in graph['nodes']]
    return {'schema': BINDING, 'graph': graph,
        'translation': {'source_mapping': {'actions': actions}}}


@pytest.mark.parametrize('whole', [False, True])
def test_actual_v195_human_port_shows_derived_value_schema_and_exact_cold_producer(whole):
    values, binding, mapping = human_scene(whole=whole)
    before = copy.deepcopy(values)
    graph = binding['graph']
    output = {ROOT_FIELD: {'status': 'ready', 'detail': '完整原包仍由原生产者保留'},
        'not_declared': '不作为其他端口猜取'}
    state = finish(graph, kernel.new_run(graph, {}, 10), mapping[PRODUCER], output,
        request_id='saved_original_package')
    state = json.loads(json.dumps(state))
    assert kernel.check_run(graph, state) == state
    consumer = current(state, mapping[CONSUMER], 'ready')
    producer = current(state, mapping[PRODUCER], 'completed')
    rows = inputs.describe(binding, state, consumer['id'])
    ref = graph['references']['package_status']
    source = next(row for row in graph['nodes'] if row['id'] == mapping[PRODUCER])
    expected_schema = projection.derive_schema(source['output_schema'], ref)
    assert rows == [{'label': '完整资料包', 'ordinal': 0, 'available': True,
        'value': 'ready', 'source': 'port_projection',
        'source_name': '准备完整资料包中明确选定的资料', 'availability_code': 'ready',
        'producer_activation_id': producer['id'], 'schema': expected_schema}]
    assert expected_schema['type'] == 'string'
    assert binding['translation']['causal_bindings']['input_bindings'][0]['contract']['data_type'] == 'object'
    assert inputs.require_inputs(binding, state, consumer['id']) == rows
    state = finish(graph, state, mapping[CONSUMER], {'result': '已核对原输入'},
        request_id='saved_human_consumer')
    state = finish(graph, state, mapping[COMPLETION], {'result': '人工结束记录'},
        request_id='saved_human_completion')
    assert state['status'] == 'succeeded' and kernel.check_run(graph, state) == state
    assert values == before


@pytest.mark.parametrize('kind,value,require_non_null', [
    ('string', '', True), ('object', {}, True), ('array', [], True),
    ('string', None, False), ('string', None, True)])
def test_empty_and_nullable_projected_values_are_not_replaced_by_defaults(kind, value, require_non_null):
    selected = {'type': [kind, 'null']}
    if kind == 'object': selected.update(properties={}, required=[], additionalProperties=False)
    if kind == 'array': selected.update(items={'type': 'string'}, maxItems=8)
    _, binding, mapping = human_scene(selected_schema=selected, kind=kind,
        nullable=not require_non_null, require_non_null=require_non_null)
    graph = binding['graph']
    output = {ROOT_FIELD: {'status': copy.deepcopy(value), 'detail': '保留完整原产物'},
        'not_declared': '不补成输入'}
    state = finish(graph, kernel.new_run(graph, {}, 10), mapping[PRODUCER], output,
        request_id='saved_empty_or_null')
    state = json.loads(json.dumps(state))
    assert kernel.check_run(graph, state) == state
    available = value is not None or not require_non_null
    consumer = current(state, mapping[CONSUMER], 'ready' if available else 'waiting_guard')
    before = canonical_json(state)
    row, = inputs.describe(binding, state, consumer['id'])
    source = next(item for item in graph['nodes'] if item['id'] == mapping[PRODUCER])
    assert row['schema'] == projection.derive_schema(source['output_schema'], graph['references']['package_status'])
    assert row['available'] is available and row['value'] == value
    assert type(row['value']) is type(value)
    assert row['availability_code'] == ('ready' if available else 'unavailable')
    if available:
        assert row['producer_activation_id'] == current(state, mapping[PRODUCER], 'completed')['id']
        assert inputs.require_inputs(binding, state, consumer['id']) == [row]
    else:
        assert state['status'] == 'blocked' and row['producer_activation_id'] is None
        with pytest.raises(HTTPException) as error:
            inputs.require_inputs(binding, state, consumer['id'])
        assert error.value.status_code == 409
    assert canonical_json(state) == before


def test_real_unselected_producer_has_missing_input_without_a_guessed_activation():
    binding = native_presentation(choose=True)
    graph = binding['graph']
    state = finish_native(graph, kernel.new_run(graph, {}, 10), 'choose', {'ok': False})
    state = json.loads(json.dumps(state))
    assert kernel.check_run(graph, state) == state
    consumer = current(state, 'consume', 'waiting_guard')
    assert not any(row['node_id'] == 'produce' for row in state['activations'].values())
    before = canonical_json(state)
    row, = inputs.describe(binding, state, consumer['id'])
    source = next(item for item in graph['nodes'] if item['id'] == 'produce')
    assert row['schema'] == projection.derive_schema(source['output_schema'], graph['references']['context'])
    assert row['availability_code'] == 'missing' and row['available'] is False
    assert row['value'] is None and row['producer_activation_id'] is None
    with pytest.raises(HTTPException) as error:
        inputs.require_inputs(binding, state, consumer['id'])
    assert error.value.status_code == 409 and canonical_json(state) == before


def test_ambiguous_duplicate_products_are_rejected_by_the_real_cold_state_check():
    binding = native_presentation()
    graph = binding['graph']
    state = finish_native(graph, kernel.new_run(graph, {}, 10), 'produce',
        {'packet': {'context': {'value': 1.0}}})
    assert kernel.check_run(graph, state) == state
    consumer = current(state, 'consume', 'ready')
    producer = current(state, 'produce', 'completed')
    row, = inputs.describe(binding, state, consumer['id'])
    assert row['available'] is True and row['producer_activation_id'] == producer['id']
    # This is a corruption probe, not a legitimate second saved activation.
    damaged = copy.deepcopy(state)
    duplicate = copy.deepcopy(producer); duplicate['id'] = 'a999'
    damaged['activations'][duplicate['id']] = duplicate
    ref = graph['references']['context']
    assert kernel._projection_resolution(ref, damaged, kernel._metadata(graph),
        consumer['context'], 'consume') == {'status': 'ambiguous', 'count': 2}
    before = canonical_json(damaged)
    with pytest.raises(ValueError): kernel.check_run(graph, damaged)
    with pytest.raises(HTTPException) as error:
        inputs.describe(binding, damaged, consumer['id'])
    assert error.value.status_code == 409 and canonical_json(damaged) == before
