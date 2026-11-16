"""Actual V195 compiler/causal checker/kernel with declared-port projections.

Synthetic source and capability pins establish no registration, human review,
entry-fact, execution or physical-data authority. Root runs these pure tests.
"""
import copy
import json

import pytest

from generic_agents import business_flow_nested_execution_v195 as compiler
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import business_flow_nested_causal_v192 as causal
from generic_agents import full_semantic_graph_v1 as native
from generic_agents import guarded_port_projection_v198 as projection
from generic_agents.contracts import digest
from tests.test_business_flow_nested_execution_v192 import scene as original_scene, guards, schema, finish
from tests.test_report_business_flow_wire_v184 import no_external


ROOT_FIELD = 'bundle/~'
ROOT_POINTER = '/bundle~1~0'
PRODUCER = '/business/flows/0/phases/0/steps/0'
CONSUMER = '/business/flows/0/phases/0/steps/1'
COMPLETION = '/business/flows/0/completion'
OBJECT = {'data_type': 'object', 'unit': None}
STRING = {'data_type': 'string', 'unit': None}


def scene(*, whole=False):
    plan, source, _, _, _ = original_scene()
    flow = plan['business']['flows'][0]
    template = copy.deepcopy(flow['completion'])
    def action(name, *, actor='human', inputs=None, outputs=None):
        value = copy.deepcopy(template)
        value.update(name=name, actor=actor, role='数据管理智能体' if actor == 'data_agent' else template['role'],
            inputs=[] if inputs is None else inputs, outputs=[name + '产出'] if outputs is None else outputs)
        return value
    flow['phases'] = [{'kind': 'steps', 'attribution': copy.deepcopy(flow['phases'][0]['attribution']),
        'steps': [action('准备完整资料包', outputs=['完整资料包']),
            action('使用已声明资料包', actor='data_agent', inputs=['完整资料包'], outputs=['办理结果'])]}]
    flow['completion'] = action('记录本次结束依据', outputs=['结束记录'])
    published = schema(**{ROOT_FIELD: schema(status={'type': 'string'}, detail={'type': 'string'}),
        'not_declared': {'type': 'string'}})
    origin = {'node_id': PRODUCER, 'port_ordinal': 0, 'output_pointer': '' if whole else ROOT_POINTER}
    def ref(field):
        return {'schema': projection.SCHEMA, 'source': 'port_projection', 'kind': 'string',
            'nullable': False, 'unit': None, 'consumer_node_id': CONSUMER, 'origin': copy.deepcopy(origin),
            'projection': {'op': 'select', 'pointer': (ROOT_POINTER if whole else '') + '/' + field,
                'require_non_null': True}}
    nodes = {
        PRODUCER: {'kind': 'human', 'actor_id': 'public_producer', 'role': flow['phases'][0]['steps'][0]['role'],
            'guards': guards(), 'input_ports': [], 'output_ports': [{'ordinal': 0, 'field': '$output' if whole else ROOT_FIELD}],
            'output_schema': published},
        CONSUMER: {'kind': 'ability', 'actor_id': 'public_consumer', 'guards': guards(),
            # One semantic object port is explicitly lowered to two physical
            # scalar arguments. Primary is one of those actual references.
            'input_ports': [{'ordinal': 0, 'reference': 'package_status'}],
            'output_ports': [{'ordinal': 0, 'field': 'result'}], 'execution': {
                'capability_id': 'public_registered_looking_tool', 'capability_hash': 'a' * 64,
                'agent_role': 'data', 'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1,
                'input_bindings': {'status': 'package_status', 'details': 'package_detail'}, 'arguments': {},
                'input_schema': schema(status={'type': 'string'}, details={'type': 'string'}),
                'output_schema': schema(result={'type': 'string'})}},
        COMPLETION: {'kind': 'human', 'actor_id': 'public_completer', 'role': flow['completion']['role'],
            'guards': guards(), 'input_ports': [], 'output_ports': [{'ordinal': 0, 'field': 'result'}],
            'output_schema': schema(result={'type': 'string'})},
    }
    bindings = {'schema': compiler.BINDINGS, 'input_schema': schema(),
        'references': {'package_status': ref('status'), 'package_detail': ref('detail')},
        'predicates': {}, 'guards': guards(), 'nodes': nodes, 'entries': {}, 'conditions': [], 'loops': [],
        'contract_schemas': [{'contract': copy.deepcopy(contract), 'kind': contract['data_type'], 'nullable': False}
            for contract in (OBJECT, STRING)]}
    sidecar = {'schema': causal.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(source['scope_binding']), 'entry_ports': [],
        'output_contracts': [{'path': PRODUCER + '/outputs/0', 'contract': copy.deepcopy(OBJECT)},
            {'path': CONSUMER + '/outputs/0', 'contract': copy.deepcopy(STRING)},
            {'path': COMPLETION + '/outputs/0', 'contract': copy.deepcopy(STRING)}],
        'input_bindings': [{'input_path': CONSUMER + '/inputs/0', 'contract': copy.deepcopy(OBJECT),
            'sources': [PRODUCER + '/outputs/0']}]}
    return plan, source, bindings, sidecar


def build(values):
    plan, source, bindings, sidecar = values
    return compiler.build_execution_graph(plan, 0, bindings, scope_binding=source['scope_binding'], causal_bindings=sidecar)


@pytest.mark.parametrize('whole', [False, True])
def test_declared_compound_port_splits_into_actual_scalar_inputs_without_replacing_its_contract(whole):
    values = scene(whole=whole); before = copy.deepcopy(values)
    result = build(values); graph = result['graph']
    mapping = {row['ast_path']: row['graph_node_id'] for row in result['source_mapping']['actions']}
    for key in ('package_status', 'package_detail'):
        compiled = graph['references'][key]
        assert compiled['consumer_node_id'] == mapping[CONSUMER]
        assert compiled['origin']['node_id'] == mapping[PRODUCER]
        assert compiled['origin']['port_ordinal'] == 0
        assert compiled['origin']['output_pointer'] == ('' if whole else ROOT_POINTER)
    assert result['causal_bindings']['input_bindings'][0]['contract'] == OBJECT
    assert result['causal_bindings']['output_contracts'][0]['contract'] == OBJECT
    output = {ROOT_FIELD: {'status': 'ready', 'detail': '完整保留原资料中的字段'},
        'not_declared': '未声明为另一个语义端口的产出'}
    state = native.new_run(graph, {}, 10)
    state = finish(graph, state, mapping[PRODUCER], output, request_id='original_full_package')
    current = next(value for value in state['activations'].values()
        if value['node_id'] == mapping[CONSUMER] and value['status'] == 'ready')
    assert native.ability_inputs(graph, state, current['id']) == {'status': 'ready', 'details': '完整保留原资料中的字段'}
    assert current['inputs_snapshot']['arguments'] == {'status': 'ready', 'details': '完整保留原资料中的字段'}
    state = finish(graph, state, mapping[CONSUMER], {'result': '合成工具结果，不声称真实调用'}, request_id='original_consumer')
    state = finish(graph, state, mapping[COMPLETION], {'result': '人工结束记录，非审批证明'}, request_id='original_completion')
    assert state['status'] == 'succeeded' and native.check_run(graph, json.loads(json.dumps(state))) == state
    assert values == before and all(result[key] is False for key in compiler.FLAGS)
    assert compiler.check_execution_graph(result, values[0], 0, values[2],
        scope_binding=values[1]['scope_binding'], causal_bindings=values[3]) == result


@pytest.mark.parametrize('fault', ['undeclared_top_output', 'wrong_ordinal', 'boolean_ordinal', 'float_ordinal',
    'extra_origin', 'extra_consumer', 'unused_projection', 'outside_published_schema', 'primary_not_physical',
    'derived_scalar_as_root_contract'])
def test_projection_must_stay_with_exact_declared_source_consumer_port_and_published_shape(fault):
    values = scene(); _, _, bindings, _ = values
    primary, extra = bindings['references']['package_status'], bindings['references']['package_detail']
    if fault == 'undeclared_top_output':
        # It exists in the real published schema, but is not the causal port.
        for ref in (primary, extra):
            ref['origin']['output_pointer'] = '/not_declared'
            ref['projection'] = {'op': 'copy', 'pointer': ''}
    elif fault in {'wrong_ordinal', 'boolean_ordinal', 'float_ordinal'}:
        ordinal = {'wrong_ordinal': 1, 'boolean_ordinal': True, 'float_ordinal': 0.0}[fault]
        for ref in (primary, extra): ref['origin']['port_ordinal'] = ordinal
    elif fault == 'extra_origin': extra['origin']['output_pointer'] = '/not_declared'
    elif fault == 'extra_consumer': extra['consumer_node_id'] = COMPLETION
    elif fault == 'unused_projection': bindings['references']['unused'] = copy.deepcopy(primary)
    elif fault == 'outside_published_schema': extra['projection']['pointer'] = '/unpublished_field'
    elif fault == 'primary_not_physical':
        bindings['nodes'][CONSUMER]['execution']['input_bindings']['status'] = 'package_detail'
    else:
        next(row for row in bindings['contract_schemas'] if row['contract'] == OBJECT)['kind'] = 'string'
    before = copy.deepcopy(values)
    with pytest.raises(ValueError): build(values)
    assert values == before


def test_whole_output_requires_the_single_actual_source_output_port():
    values = scene(whole=True); plan, source, bindings, sidecar = values
    plan['business']['flows'][0]['phases'][0]['steps'][0]['outputs'].append('第二个真实语义端口')
    bindings['nodes'][PRODUCER]['output_ports'].append({'ordinal': 1, 'field': 'not_declared'})
    sidecar['output_contracts'].append({'path': PRODUCER + '/outputs/1', 'contract': copy.deepcopy(STRING)})
    sidecar['source_plan_hash'] = digest(plan)
    # Actual AST/causal coverage is legal; the whole-output escape itself must
    # fail rather than hiding two original semantic output ordinals.
    assert plans.check_plan(plan, scope_binding=source['scope_binding']) == plan
    assert causal.analyze_availability(plan, sidecar, scope_binding=source['scope_binding'])['consistent'] is True
    with pytest.raises(ValueError): build(values)


def test_actual_required_dollar_output_property_keeps_legacy_scalar_field_semantics():
    values = scene(); _, _, bindings, sidecar = values
    bindings['nodes'][PRODUCER]['output_schema'] = schema(**{'$output': {'type': 'string'}})
    bindings['nodes'][PRODUCER]['output_ports'] = [{'ordinal': 0, 'field': '$output'}]
    bindings['references'] = {'legacy_text': {'kind': 'string', 'nullable': False,
        'source': 'output', 'node_id': PRODUCER, 'field': '$output'}}
    consumer = bindings['nodes'][CONSUMER]
    consumer['input_ports'] = [{'ordinal': 0, 'reference': 'legacy_text'}]
    consumer['execution']['input_bindings'] = {'text': 'legacy_text'}
    consumer['execution']['input_schema'] = schema(text={'type': 'string'})
    bindings['contract_schemas'] = [{'contract': copy.deepcopy(STRING), 'kind': 'string', 'nullable': False}]
    sidecar['output_contracts'][0]['contract'] = copy.deepcopy(STRING)
    sidecar['input_bindings'][0]['contract'] = copy.deepcopy(STRING)
    before = copy.deepcopy(values)
    translated = build(values); graph = translated['graph']
    mapping = {row['ast_path']: row['graph_node_id'] for row in translated['source_mapping']['actions']}
    ref = graph['references']['legacy_text']
    assert ref == {'kind': 'string', 'nullable': False, 'source': 'output',
        'node_id': mapping[PRODUCER], 'field': '$output'}
    assert not any(native._projection(value) for value in graph['references'].values())
    output = {'$output': '真实 required 属性的字符串产物，不能解释成整个 object'}
    state = finish(graph, native.new_run(graph, {}, 10), mapping[PRODUCER], output,
        request_id='legacy_required_dollar_output')
    activation = next(row for row in state['activations'].values()
        if row['node_id'] == mapping[CONSUMER] and row['status'] == 'ready')
    assert native.ability_inputs(graph, state, activation['id']) == {'text': output['$output']}
    state = finish(graph, state, mapping[CONSUMER], {'result': '保留旧标量语义'},
        request_id='legacy_dollar_output_consumer')
    state = finish(graph, state, mapping[COMPLETION], {'result': '合成流程结束'},
        request_id='legacy_dollar_output_completion')
    assert state['status'] == 'succeeded'
    assert native.check_run(graph, json.loads(json.dumps(state))) == state
    assert compiler.check_execution_graph(translated, values[0], 0, values[2],
        scope_binding=values[1]['scope_binding'], causal_bindings=values[3]) == translated
    assert values == before
