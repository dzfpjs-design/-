"""Closed JSON Schema fields through the real pure graph kernel.

Reader outputs below are synthetic contract-valid values, not executed source,
authorization, provider or PostgreSQL evidence. Published schemas stay intact.
"""
import copy

import pytest

from generic_agents import full_semantic_graph_v1 as native
from generic_agents import workflow_task_context_v196 as reader
from tests.test_full_semantic_graph_v1 import (
    ability, activation, base, control, edge, finish, human, object_schema,
    output_reference,
)


def field(value):
    return object_schema(value=value)


def test_actual_reader_enum_status_and_false_constants_resolve_without_rewriting_contract():
    schema = reader.output_schema(); before = copy.deepcopy(schema)
    assert 'type' not in schema['properties']['status']
    assert schema['properties']['status']['enum']
    assert native._field(schema, 'status') == ('string', False)
    for name in reader.FLAGS:
        assert schema['properties'][name] == {'const': False}
        assert native._field(schema, name) == ('boolean', False)
    for name in ('evidence_package', 'resense_context'):
        assert native._field(schema, name) == ('object', True)
    assert schema == before and reader.output_schema() == before


@pytest.mark.parametrize('spec,expected', [
    ({'const': False}, ('boolean', False)),
    ({'const': 0}, ('integer', False)),
    ({'const': 0.0}, ('number', False)),
    ({'const': ''}, ('string', False)),
    ({'const': {}}, ('object', False)),
    ({'const': []}, ('array', False)),
    ({'enum': ['ready', 'blocked']}, ('string', False)),
    ({'enum': [False, True]}, ('boolean', False)),
    ({'enum': [0, 1]}, ('integer', False)),
    ({'enum': [0, 1.5]}, ('number', False)),
    ({'enum': [None, 0, 1.5]}, ('number', True)),
    ({'enum': [None, False]}, ('boolean', True)),
    ({'enum': ['ready', None]}, ('string', True)),
    ({'enum': [{}, {'key': 'value'}]}, ('object', False)),
    ({'enum': [[], ['value'], None]}, ('array', True)),
    ({'const': '2026-01-01', 'format': 'date'}, ('date', False)),
])
def test_closed_const_enum_kinds_keep_exact_bool_numeric_and_nullable_contracts(spec, expected):
    schema = field(spec); before = copy.deepcopy(schema)
    assert native._field(schema, 'value') == expected
    assert schema == before


@pytest.mark.parametrize('spec', [
    {}, {'description': 'Unconstrained fields are not assignable'},
    {'enum': []}, {'enum': None}, {'enum': ('ready',)},
    {'const': None}, {'enum': [None]},
    {'enum': ['ready', False]}, {'enum': [False, 0]},
    {'enum': [{}, 'ready']}, {'enum': [[], {}]},
    {'enum': [None, 1, True]}, {'enum': [b'not_json']},
    {'type': 'unknown', 'enum': ['ready']},
    {'type': ['string', 'boolean'], 'enum': ['ready']},
])
def test_empty_unknown_or_mixed_finite_kinds_cannot_be_guessed(spec):
    with pytest.raises(ValueError):
        native._field(field(spec), 'value')


@pytest.mark.parametrize('spec,expected', [
    ({'type': 'boolean'}, ('boolean', False)),
    ({'type': 'integer'}, ('integer', False)),
    ({'type': 'number'}, ('number', False)),
    ({'type': ['number', 'null']}, ('number', True)),
    ({'type': 'object', 'properties': {}}, ('object', False)),
    ({'type': 'array', 'items': {'type': 'string'}}, ('array', False)),
    ({'type': 'string', 'format': 'date'}, ('date', False)),
    # Explicit nullable contracts remain conservative even with a smaller enum.
    ({'type': ['string', 'null'], 'enum': ['ready']}, ('string', True)),
    ({'type': 'string', 'enum': ['ready', None]}, ('string', False)),
])
def test_explicit_type_paths_preserve_the_existing_declared_contract(spec, expected):
    schema = field(spec); before = copy.deepcopy(schema)
    assert native._field(schema, 'value') == expected
    assert schema == before


def test_local_ref_resolves_a_closed_enum_without_mutating_defs():
    schema = field({'$ref': '#/$defs/Status'})
    schema['$defs'] = {'Status': {'enum': ['ready', None]}}
    before = copy.deepcopy(schema)
    assert native._field(schema, 'value') == ('string', True)
    assert schema == before


@pytest.mark.parametrize('wrong_kind', ['integer', 'number'])
def test_false_const_is_not_accepted_as_a_numeric_graph_reference(wrong_kind):
    producer = human('producer'); producer['output_schema'] = field({'const': False})
    graph = base([producer, control('end', 'end')], [edge('producer', 'end')], 'producer', ['producer'])
    graph['references']['flag'] = output_reference('producer', 'value', 'boolean')
    assert native.validate_graph(graph) == graph
    invalid = copy.deepcopy(graph); invalid['references']['flag']['kind'] = wrong_kind
    with pytest.raises(ValueError, match='reference_type'):
        native.validate_graph(invalid)


def test_nullable_enum_cannot_feed_a_nonnullable_ability_input_and_null_is_preserved():
    producer = human('producer'); producer['output_schema'] = field({'enum': ['ready', None]})
    consumer = ability('consumer', {'approved': 'nullable_status'})
    consumer['execution']['input_schema'] = object_schema(approved={'type': 'string'})
    graph = base([producer, consumer, control('end', 'end')],
        [edge('producer', 'consumer'), edge('consumer', 'end')], 'producer', ['producer', 'consumer'])
    graph['references']['nullable_status'] = output_reference('producer', 'value', 'string', True)
    with pytest.raises(ValueError, match='input_reference_nullable'):
        native.validate_graph(graph)
    consumer['execution']['input_schema']['properties']['approved']['type'] = ['string', 'null']
    before = copy.deepcopy(graph)
    state = native.new_run(graph, {}, 10)
    state = finish(graph, state, 'producer', {'value': None}, request='original_null', now=11)
    current = activation(state, 'consumer')
    assert native.ability_inputs(graph, state, current['id']) == {'approved': None}
    assert current['inputs_snapshot']['arguments']['approved'] is None
    state = finish(graph, state, 'consumer', {'ok': True}, request='accept_nullable', now=12)
    assert state['status'] == 'succeeded' and native.check_run(graph, state) == state
    assert graph == before


def reader_branch_graph():
    producer = ability('read_context', {})
    producer['execution'].update(input_schema=reader.input_schema(),
        output_schema=reader.output_schema(), arguments={'policy_pin': None})
    yes, no = human('has_no_gap'), human('review_blocked')
    graph = base([producer, control('choose', 'decision', predicate_id='no_gap'), yes, no,
            control('end', 'end')],
        [edge('read_context', 'choose'), edge('choose', yes['id'], 'true'),
            edge('choose', no['id'], 'false'), edge(yes['id'], 'end'), edge(no['id'], 'end')],
        'read_context', ['read_context'])
    graph['references']['reader_status'] = output_reference('read_context', 'status', 'string')
    graph['predicates']['no_gap'] = {'op': 'eq', 'left': {'op': 'ref', 'key': 'reader_status'},
        'right': {'op': 'value', 'kind': 'string', 'value': 'no_gap'}}
    return graph


@pytest.mark.parametrize('status,selected,unselected,truth', [
    ('no_gap', 'has_no_gap', 'review_blocked', 'true'),
    ('blocked', 'review_blocked', 'has_no_gap', 'false'),
])
def test_actual_reader_output_status_drives_both_real_kernel_branches_to_completion(status, selected, unselected, truth):
    graph = reader_branch_graph(); before = copy.deepcopy(graph)
    published = reader.output_schema()
    state = native.new_run(graph, {}, 10)
    current = activation(state, 'read_context')
    assert native.ability_inputs(graph, state, current['id']) == {'policy_pin': None}
    output = {'schema': reader.RESULT, 'status': status, 'evidence_package': None,
        'resense_context': None, 'candidates': [], 'issues': [], **reader.FLAGS}
    assert reader.checked_result(output) == output
    # complete() performs the actual output-schema validation and transition;
    # the decision is not stubbed and no evaluator or kernel function is patched.
    state = finish(graph, state, 'read_context', output, request='original_reader_' + status, now=11)
    assert activation(state, selected)['status'] == 'ready'
    assert not any(item['node_id'] == unselected for item in state['activations'].values())
    decisions = [receipt for receipt in state['predicate_receipts'] if receipt['phase'] == 'decision']
    assert decisions and decisions[-1]['result']['truth'] == truth
    state = finish(graph, state, selected, {'ok': True}, request='original_human_' + status, now=12)
    assert activation(state, selected, status='completed')['output'] == {'ok': True}
    assert state['status'] == 'succeeded' and native.check_run(graph, state) == state
    assert graph == before
    assert graph['nodes'][0]['execution']['output_schema'] == published == reader.output_schema()
