"""Actual existing reducer with explicitly versioned exclusive references.

Only synthetic values and pure transitions; no Invocation, device, IAM or DB.
"""
import copy
import json

import pytest

from generic_agents import full_semantic_graph_v1 as kernel
from tests.test_full_semantic_graph_v1 import (
    guards, object_schema, human, control, edge, base, graph as parallel_graph,
    finish, activation, output_reference, loop_graph)


def exclusive(*, nullable=False, unit=None, consumer='consume', candidates=None, kind='boolean'):
    return {'schema': kernel.EXCLUSIVE_REFERENCE, 'source': 'exclusive_output', 'kind': kind,
        'nullable': nullable, 'unit': unit, 'consumer_node_id': consumer,
        'candidates': candidates or [{'node_id': 'left', 'field': 'ok'}, {'node_id': 'right', 'field': 'ok'}]}


def consumer(nullable=False, unit=None):
    field = {'type': ['boolean', 'null'] if nullable else 'boolean'}
    if unit is not None: field['x-unit'] = unit
    return {'id': 'consume', 'kind': 'ability', 'source_step_id': 'consumer_source',
        'guards': guards(), 'actor_id': 'operator_consume', 'execution': {
            'capability_id': 'registered_test_cap', 'capability_hash': 'a' * 64, 'agent_role': 'data',
            'input_bindings': {'approved': 'choice'}, 'arguments': {},
            'input_schema': object_schema(approved=field), 'output_schema': object_schema(ok={'type': 'boolean'}),
            'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}}


def choice_graph(*, nullable=False, unit=None, empty=False):
    left, right = human('left'), human('right')
    for node in (left, right):
        field = {'type': ['boolean', 'null'] if nullable else 'boolean'}
        if unit is not None: field['x-unit'] = unit
        node['output_schema'] = object_schema(ok=field)
    nodes = [human('choose'), control('decision', 'decision', predicate_id='which'), left, right,
        consumer(nullable, unit), control('end', 'end')]
    edges = [edge('choose', 'decision'), edge('decision', 'left', 'true'),
        edge('decision', 'right', 'false'), edge('left', 'consume'), edge('right', 'consume'), edge('consume', 'end')]
    result = base(nodes, edges, 'choose', ['consume'])
    result['references'] = {'which': output_reference('choose', 'ok'), 'choice': exclusive(nullable=nullable, unit=unit)}
    result['predicates'] = {'which': {'op': 'ref', 'key': 'which'}}
    if empty:
        result['nodes'].append(control('skip', 'decision', predicate_id='which'))
        result['edges'] = [e for e in result['edges'] if not (e['from'] == 'decision' and e['on'] == 'false')]
        result['edges'] += [edge('decision', 'skip', 'false'), edge('skip', 'right', 'true'), edge('skip', 'consume', 'false')]
    return result


def ambiguous_graph():
    result = parallel_graph()
    result['nodes'].insert(-1, consumer())
    result['edges'] = [e for e in result['edges'] if e['from'] != 'j'] + [edge('j', 'consume'), edge('consume', 'end')]
    result['references'] = {'choice': exclusive(candidates=[{'node_id': 'a', 'field': 'ok'}, {'node_id': 'b', 'field': 'ok'}])}
    result['goals'][0]['required_nodes'] = ['consume']
    return result


@pytest.mark.parametrize('chosen', [True, False])
@pytest.mark.parametrize('payload', [True, False, None])
def test_real_conditional_output_freezes_exact_single_producer_and_nullable_none_is_a_product(chosen, payload):
    graph = choice_graph(nullable=True)
    original = copy.deepcopy(graph)
    assert kernel.validate_graph(graph) == graph
    state = finish(graph, kernel.new_run(graph, {}, 10), 'choose', {'ok': chosen})
    source = 'left' if chosen else 'right'
    state = finish(graph, state, source, {'ok': payload})
    current = activation(state, 'consume')
    selected = next(a for a in state['activations'].values() if a['node_id'] == source)
    snapshot = current['inputs_snapshot']
    assert kernel.ability_inputs(graph, state, current['id']) == {'approved': payload}
    assert snapshot['bindings']['approved']['producer_activation_id'] == selected['id']
    assert snapshot['bindings']['approved']['value_hash'] == kernel._hash(payload)
    assert not any(a['node_id'] == ('right' if chosen else 'left') for a in state['activations'].values())
    assert kernel.check_run(graph, json.loads(json.dumps(state))) == state
    state = finish(graph, state, 'consume', {'ok': True})
    assert state['status'] == 'succeeded' and graph == original


def test_zero_output_waits_with_visible_reason_and_never_dispatches():
    graph = choice_graph(empty=True)
    state = finish(graph, kernel.new_run(graph, {}, 10), 'choose', {'ok': False})
    current = activation(state, 'consume', status='waiting_guard')
    assert state['status'] == 'waiting' and state['error'] == 'exclusive_reference_missing:choice'
    assert current['inputs_snapshot'] is None and state['eligible_ability_ids'] == []
    assert kernel.advance(graph, state, 10) == state
    assert kernel.check_run(graph, state) == state
    with pytest.raises(ValueError, match='ability_not_ready'): kernel.ability_inputs(graph, state, current['id'])


@pytest.mark.parametrize('consumer_kind', ['ability', 'human'])
def test_two_completed_products_are_a_durable_observable_block_not_first_value_or_retry_error(consumer_kind):
    graph = ambiguous_graph()
    if consumer_kind == 'human':
        graph['nodes'][next(i for i, n in enumerate(graph['nodes']) if n['id'] == 'consume')] = human('consume')
    state = kernel.new_run(graph, {}, 10)
    state = finish(graph, state, 'a', {'ok': True})
    assert not state['eligible_ability_ids']
    state = finish(graph, state, 'b', {'ok': True})
    current = activation(state, 'consume', status='waiting_guard')
    assert state['status'] == 'blocked' and state['error'] == current['error'] == 'exclusive_reference_ambiguous:choice'
    assert current['inputs_snapshot'] is None
    assert state['eligible_ability_ids'] == state['ready_human_ids'] == []
    assert kernel.check_run(graph, json.loads(json.dumps(state))) == state
    assert kernel.advance(graph, state, 10) == state
    if consumer_kind == 'ability':
        with pytest.raises(ValueError, match='ability_not_ready'): kernel.ability_inputs(graph, state, current['id'])
    else:
        with pytest.raises(ValueError, match='activation_stale'):
            kernel.complete(graph, state, current['id'], {'ok': True}, 'blocked_complete', 'operator_consume',
                current['version'], current['epoch'], 10)


def test_explicit_matching_unit_is_kept_without_conversion():
    graph = choice_graph(unit='flag')
    assert kernel.validate_graph(graph)['references']['choice']['unit'] == 'flag'
    state = finish(graph, kernel.new_run(graph, {}, 10), 'choose', {'ok': True})
    state = finish(graph, state, 'left', {'ok': False})
    current = activation(state, 'consume')
    assert kernel.ability_inputs(graph, state, current['id']) == {'approved': False}


def test_two_fields_on_one_activation_are_two_products_even_when_both_are_null():
    producer = human('left'); producer['output_schema'] = object_schema(ok={'type': ['boolean', 'null']},
        another={'type': ['boolean', 'null']})
    graph = base([producer, consumer(True), control('end', 'end')],
        [edge('left', 'consume'), edge('consume', 'end')], 'left', ['consume'])
    graph['references']['choice'] = exclusive(nullable=True,
        candidates=[{'node_id': 'left', 'field': 'ok'}, {'node_id': 'left', 'field': 'another'}])
    state = finish(graph, kernel.new_run(graph, {}, 10), 'left', {'ok': None, 'another': None})
    assert state['status'] == 'blocked' and state['eligible_ability_ids'] == []


@pytest.mark.parametrize('fault', ['schema', 'extra', 'duplicate', 'kind', 'nullable', 'unit',
    'optional_field', 'consumer', 'source', 'self', 'future', 'wrong_use'])
def test_reference_family_type_unit_upstream_and_consumer_are_closed(fault):
    graph = choice_graph(unit='flag')
    ref = graph['references']['choice']
    if fault == 'schema': ref['schema'] = 'guess-v195'
    elif fault == 'extra': ref['fallback'] = True
    elif fault == 'duplicate': ref['candidates'][1] = copy.deepcopy(ref['candidates'][0])
    elif fault == 'kind': ref['kind'] = 'integer'
    elif fault == 'nullable': ref['nullable'] = True
    elif fault == 'unit': ref['unit'] = 'other'
    elif fault == 'optional_field': next(n for n in graph['nodes'] if n['id'] == 'left')['output_schema']['required'] = []
    elif fault == 'consumer': ref['consumer_node_id'] = 'missing'
    elif fault == 'source': ref['candidates'][0]['source'] = 'input'
    elif fault == 'self': ref['candidates'][0]['node_id'] = 'consume'
    elif fault == 'future':
        # `choose` is before both producers. Its new human dependency cannot
        # borrow a value from a later output, even though both nodes exist.
        ref['consumer_node_id'] = 'choose'
        next(n for n in graph['nodes'] if n['id'] == 'consume')['execution']['input_bindings'] = {}
        next(n for n in graph['nodes'] if n['id'] == 'consume')['execution']['arguments'] = {'approved': True}
    else: ref['consumer_node_id'] = 'left'
    with pytest.raises(ValueError): kernel.validate_graph(graph)


def test_old_iteration_and_other_consumer_never_supply_an_exclusive_reference():
    graph = loop_graph(); state = kernel.new_run(graph, {}, 10)
    state = finish(graph, state, 'h', {'again': True})
    current = activation(state, 'h')
    reference = exclusive(consumer='h', candidates=[{'node_id': 'h', 'field': 'again'},
        {'node_id': 'h', 'field': 'another'}])
    # Explicit context seam over a REAL reducer loop: the only complete `h`
    # belongs to iteration zero, while the ready activation is iteration one.
    result = kernel._exclusive_resolution(reference, state, kernel._metadata(graph), current['context'], 'h')
    assert result == {'status': 'missing', 'count': 0}
    assert kernel._exclusive_resolution(reference, state, kernel._metadata(graph),
        {'loops': {'retry': 0}, 'forks': {}}, 'other') == {'status': 'missing', 'count': 0}


def test_reference_does_not_borrow_the_latest_product_from_another_fork_scope():
    graph = ambiguous_graph()
    state = finish(graph, kernel.new_run(graph, {}, 10), 'a', {'ok': True})
    meta = kernel._metadata(graph)
    context = copy.deepcopy(next(a for a in state['activations'].values() if a['node_id'] == 'a')['context'])
    ref = graph['references']['choice']
    assert kernel._exclusive_resolution(ref, state, meta, context, 'consume')['status'] == 'unique'
    # Exact matching seam over a real fork activation. A different batch's
    # context must not fall back to that original scope's latest completion.
    context['forks']['f'] = 's2'
    assert kernel._exclusive_resolution(ref, state, meta, context, 'consume') == {'status': 'missing', 'count': 0}


def test_resealed_snapshot_cannot_switch_candidate_or_typed_value():
    graph = choice_graph(nullable=True)
    state = finish(graph, kernel.new_run(graph, {}, 10), 'choose', {'ok': True})
    state = finish(graph, state, 'left', {'ok': False})
    current = activation(state, 'consume')
    before = copy.deepcopy(state)
    snapshot = current['inputs_snapshot']
    snapshot['bindings']['approved']['producer_activation_id'] = next(a['id'] for a in state['activations'].values() if a['node_id'] == 'choose')
    snapshot['snapshot_hash'] = kernel._hash({k: v for k, v in snapshot.items() if k != 'snapshot_hash'})
    state['state_hash'] = kernel._hash({k: v for k, v in state.items() if k != 'state_hash'})
    with pytest.raises(ValueError, match='exclusive_snapshot_changed'): kernel.check_run(graph, state)
    assert kernel.check_run(graph, before) == before


def test_legacy_graph_and_reference_bytes_are_not_rewritten_or_extended():
    graph = parallel_graph(); original = json.dumps(graph, ensure_ascii=False, sort_keys=True)
    state = kernel.new_run(graph, {}, 10)
    state = finish(graph, finish(graph, state, 'a'), 'b')
    assert state['status'] == 'succeeded'
    assert json.dumps(kernel.validate_graph(graph), ensure_ascii=False, sort_keys=True) == original
