"""Actual native reducer/projection integration; no worker, DB or authority proof.

Every normal transition below uses the real graph. Explicit resolver corruption
probes are labelled; they do not pretend a forged state is a valid saved run.
"""
import copy
import json
import math

import pytest

from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import guarded_port_projection_v198 as projection
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_full_semantic_graph_v1 import (
    guards, object_schema, human, control, edge, base, finish, activation,
    output_reference, graph as parallel_graph, loop_graph)
from tests.test_guarded_port_projection_v198 import scene as compound_scene
from tests.test_report_business_flow_wire_v184 import no_external


def projected(*, consumer='consume', producer='produce', kind='object', unit=None):
    return {'schema': projection.SCHEMA, 'source': projection.SOURCE,
        'kind': kind, 'nullable': False, 'unit': unit, 'consumer_node_id': consumer,
        'origin': {'node_id': producer, 'port_ordinal': 0, 'output_pointer': '/packet'},
        'projection': {'op': 'select', 'pointer': '/context', 'require_non_null': True}}


def ability(identity='consume', *, field=None):
    return {'id': identity, 'kind': 'ability', 'source_step_id': 'source_' + identity,
        'guards': guards(), 'actor_id': 'operator_' + identity, 'execution': {
            'capability_id': 'registered_projection_consumer', 'capability_hash': 'a' * 64,
            'agent_role': 'business', 'input_bindings': {'context': 'context'}, 'arguments': {},
            'input_schema': object_schema(context=field or object_schema(value={'type': 'number'})),
            'output_schema': object_schema(ok={'type': 'boolean'}), 'adapter': 'builtin',
            'release_version': '1.0.0', 'max_attempts': 1}}


def graph(*, choose=False, kind='object', unit=None):
    target = object_schema(value={'type': 'number'}) if kind == 'object' else {'type': kind}
    if unit is not None: target['x-unit'] = unit
    producer = human('produce')
    producer['output_schema'] = object_schema(packet=object_schema(context={**target, 'type': [kind, 'null']}))
    consume = ability(field=target)
    nodes = [producer, consume, control('end', 'end')]
    edges = [edge('produce', 'consume'), edge('consume', 'end')]
    entry = 'produce'
    if choose:
        nodes += [human('choose'), control('route', 'decision', predicate_id='selected')]
        edges += [edge('choose', 'route'), edge('route', 'produce', 'true'), edge('route', 'consume', 'false')]
        entry = 'choose'
    result = base(nodes, edges, entry, ['consume'])
    result['references']['context'] = projected(kind=kind, unit=unit)
    if choose:
        result['references']['selected_value'] = output_reference('choose', 'ok')
        result['predicates']['selected'] = {'op': 'ref', 'key': 'selected_value'}
    return result


@pytest.mark.parametrize('number', [1.0, -0.0])
def test_actual_ready_input_retains_original_unique_producer_and_typed_projection(number):
    value = graph(); before = canonical_json(value)
    assert kernel.validate_graph(value) == value
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': {'value': number}}})
    consumer = activation(state, 'consume'); producer = activation(state, 'produce', status='completed')
    assert canonical_json(kernel.ability_inputs(value, state, consumer['id'])) == canonical_json({'context': {'value': number}})
    assert type(consumer['inputs_snapshot']['arguments']['context']['value']) is float
    assert consumer['inputs_snapshot']['bindings']['context']['producer_activation_id'] == producer['id']
    assert consumer['inputs_snapshot']['bindings']['context']['value_hash'] == kernel._hash({'value': number})
    assert kernel.check_run(value, json.loads(json.dumps(state))) == state
    final = finish(value, state, 'consume', {'ok': True})
    assert final['status'] == 'succeeded' and canonical_json(value) == before


def test_actual_null_blocks_visibly_before_any_ability_is_eligible_without_default_context():
    value = graph()
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': None}})
    waiting = activation(state, 'consume', status='waiting_guard')
    assert state['status'] == 'blocked'
    assert state['error'] == waiting['error'] == 'guarded_port_projection_null:context'
    assert waiting['inputs_snapshot'] is None and state['eligible_ability_ids'] == []
    assert kernel.advance(value, state, 10) == state and kernel.check_run(value, state) == state
    with pytest.raises(ValueError, match='ability_not_ready'): kernel.ability_inputs(value, state, waiting['id'])


def test_branch_without_a_real_producer_waits_and_never_borrows_an_unexecuted_source():
    value = graph(choose=True)
    state = finish(value, kernel.new_run(value, {}, 10), 'choose', {'ok': False})
    waiting = activation(state, 'consume', status='waiting_guard')
    assert state['status'] == 'waiting' and state['error'] is None
    assert waiting['error'] == 'guarded_port_projection_missing:context'
    assert waiting['inputs_snapshot'] is None and not state['eligible_ability_ids']
    assert not any(item['node_id'] == 'produce' for item in state['activations'].values())
    assert kernel.check_run(value, state) == state


def test_compound_assembly_preserves_every_source_array_item_and_snapshot_arity():
    source_schema, output, ref = compound_scene()
    ref['origin']['node_id'] = 'produce'; ref['consumer_node_id'] = 'consume'
    producer = human('produce'); producer['output_schema'] = source_schema
    expected_context = source_schema['properties']['packet']['properties']['candidates']['items']
    target = object_schema(evidence_ids={'type': 'array', 'items': {'type': 'string'}},
        candidates={'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': object_schema(
            candidate_ref={'type': 'string'}, summary={'type': 'string'}, context=expected_context)})
    value = base([producer, ability(field=target), control('end', 'end')],
        [edge('produce', 'consume'), edge('consume', 'end')], 'produce', ['consume'])
    value['references']['context'] = ref
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', output)
    current = activation(state, 'consume'); actual = kernel.ability_inputs(value, state, current['id'])['context']
    assert len(actual['candidates']) == len(output['packet']['candidates']) == 2
    assert [item['candidate_ref'] for item in actual['candidates']] == ['candidate_1', 'candidate_2']
    assert canonical_json([item['context'] for item in actual['candidates']]) == canonical_json(output['packet']['candidates'])
    assert actual['evidence_ids'] == output['packet']['evidence_ids']
    assert math.copysign(1, actual['candidates'][1]['context']['reading']) == -1
    assert kernel.check_run(value, state) == state


@pytest.mark.parametrize('fault', ['kind', 'nullable', 'origin_pointer', 'source_kind', 'consumer', 'wrong_consumer', 'future'])
def test_native_definition_rejects_wrong_derivation_scope_type_and_consumer(fault):
    value = graph(); ref = value['references']['context']
    if fault == 'kind': ref['kind'] = 'string'
    elif fault == 'nullable': ref['nullable'] = True
    elif fault == 'origin_pointer': ref['origin']['output_pointer'] = '/unknown'
    elif fault == 'source_kind': ref['origin']['node_id'] = 'end'
    elif fault == 'consumer': ref['consumer_node_id'] = 'unknown'
    elif fault == 'wrong_consumer':
        other = ability('other'); value['nodes'].append(other)
        value['edges'] = [edge('produce', 'consume'), edge('consume', 'other'), edge('other', 'end')]
    else:
        value['entry'] = 'consume'; value['edges'] = [edge('consume', 'produce'), edge('produce', 'end')]
    with pytest.raises(ValueError): kernel.validate_graph(value)


@pytest.mark.parametrize('fault', ['unit', 'target_nullable'])
def test_native_consumer_contract_cannot_change_projected_units_or_nullability(fault):
    value = graph(kind='number', unit='°C')
    consume = next(node for node in value['nodes'] if node['id'] == 'consume')
    target = consume['execution']['input_schema']['properties']['context']
    if fault == 'unit': target['x-unit'] = 'K'
    else: target['type'] = ['number', 'null']
    with pytest.raises(ValueError): kernel.validate_graph(value)


def test_actual_target_schema_mismatch_blocks_without_dispatch_or_a_retry_exception():
    value = graph()
    consumer = next(node for node in value['nodes'] if node['id'] == 'consume')
    consumer['execution']['input_schema'] = copy.deepcopy(consumer['execution']['input_schema'])
    target = consumer['execution']['input_schema']
    target['properties']['context']['properties']['value']['minimum'] = 2
    assert kernel.validate_graph(value) == value  # Same scalar type, stronger target constraint.
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': {'value': 1.0}}})
    current = activation(state, 'consume', status='waiting_guard')
    assert state['status'] == 'blocked'
    assert current['error'] == state['error'] == 'guarded_port_projection_input_contract'
    assert current['inputs_snapshot'] is None and not state['eligible_ability_ids']
    assert kernel.check_run(value, state) == state and kernel.advance(value, state, 10) == state


@pytest.mark.parametrize('number,changed', [(1.0, 1), (-0.0, 0.0)])
def test_resealed_snapshot_cannot_change_numeric_type_or_sign_while_producer_is_unchanged(number, changed):
    value = graph(); state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': {'value': number}}})
    original = copy.deepcopy(state); current = activation(state, 'consume'); snapshot = current['inputs_snapshot']
    snapshot['arguments']['context']['value'] = changed
    snapshot['input_hash'] = kernel._hash(snapshot['arguments'])
    snapshot['bindings']['context']['value_hash'] = kernel._hash(snapshot['arguments']['context'])
    snapshot['snapshot_hash'] = kernel._hash({key: item for key, item in snapshot.items() if key != 'snapshot_hash'})
    state['state_hash'] = kernel._hash({key: item for key, item in state.items() if key != 'state_hash'})
    with pytest.raises(ValueError): kernel.check_run(value, state)
    assert kernel.check_run(value, original) == original


def test_current_iteration_and_exact_consumer_are_required_even_if_an_old_product_exists():
    value = loop_graph(); state = finish(value, kernel.new_run(value, {}, 10), 'h', {'again': True})
    current = activation(state, 'h'); ref = projected(consumer='d', producer='h', kind='boolean')
    ref['origin']['output_pointer'] = ''; ref['projection']['pointer'] = '/again'
    result = kernel._projection_resolution(ref, state, kernel._metadata(value), current['context'], 'd')
    assert result == {'status': 'missing', 'count': 0}
    assert kernel._projection_resolution(ref, state, kernel._metadata(value),
        {'loops': {'retry': 0}, 'forks': {}}, 'another') == {'status': 'missing', 'count': 0}


def test_different_fork_history_and_duplicate_product_corruption_never_select_latest_or_first():
    value = parallel_graph()
    state = finish(value, kernel.new_run(value, {}, 10), 'a', {'ok': True})
    producer = activation(state, 'a', status='completed'); context = copy.deepcopy(producer['context'])
    ref = projected(consumer='end', producer='a', kind='boolean')
    ref['origin']['output_pointer'] = ''; ref['projection']['pointer'] = '/ok'
    assert kernel._projection_resolution(ref, state, kernel._metadata(value), context, 'end')['status'] == 'unique'
    other = copy.deepcopy(context); other['forks']['f'] = 'different_batch'
    assert kernel._projection_resolution(ref, state, kernel._metadata(value), other, 'end') == {'status': 'missing', 'count': 0}
    # Resolver-only corruption probe: this duplicate is NOT claimed to pass
    # check_run. Even if handed two products, resolution must not pick one.
    damaged = copy.deepcopy(state); duplicate = copy.deepcopy(producer); duplicate['id'] = 'a999'
    damaged['activations'][duplicate['id']] = duplicate
    assert kernel._projection_resolution(ref, damaged, kernel._metadata(value), context, 'end') == {'status': 'ambiguous', 'count': 2}


@pytest.mark.parametrize('flag', [True, False, None])
def test_decision_consumer_uses_real_projected_boolean_or_blocks_on_null(flag):
    producer = human('produce'); producer['output_schema'] = object_schema(packet=object_schema(context={'type': ['boolean', 'null']}))
    value = base([producer, control('decide', 'decision', predicate_id='selected'), human('yes'), human('no'), control('end', 'end')],
        [edge('produce', 'decide'), edge('decide', 'yes', 'true'), edge('decide', 'no', 'false'),
            edge('yes', 'end'), edge('no', 'end')], 'produce', ['produce'])
    value['references']['context'] = projected(consumer='decide', kind='boolean')
    value['predicates']['selected'] = {'op': 'ref', 'key': 'context'}
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': flag}})
    if flag is None:
        assert state['status'] == 'blocked' and state['ready_human_ids'] == []
        assert state['error'] == 'guarded_port_projection_null:context'
    else:
        selected = 'yes' if flag else 'no'
        assert activation(state, selected)['status'] == 'ready'
        assert not any(item['node_id'] == ('no' if flag else 'yes') for item in state['activations'].values())
    assert kernel.check_run(value, state) == state


@pytest.mark.parametrize('flag', [True, False, None])
def test_human_guard_consumes_only_its_explicit_projected_value(flag):
    producer = human('produce'); producer['output_schema'] = object_schema(packet=object_schema(context={'type': ['boolean', 'null']}))
    consume = human('consume'); consume['guards']['precondition'] = 'selected'
    value = base([producer, consume, control('end', 'end')],
        [edge('produce', 'consume'), edge('consume', 'end')], 'produce', ['consume'])
    value['references']['context'] = projected(kind='boolean')
    value['predicates']['selected'] = {'op': 'ref', 'key': 'context'}
    state = finish(value, kernel.new_run(value, {}, 10), 'produce', {'packet': {'context': flag}})
    if flag is True:
        assert activation(state, 'consume')['id'] in state['ready_human_ids']
    else:
        assert activation(state, 'consume', status='waiting_guard') and not state['ready_human_ids']
        if flag is None: assert state['status'] == 'blocked' and state['error'] == 'guarded_port_projection_null:context'
    assert not state['eligible_ability_ids'] and kernel.check_run(value, state) == state


def test_legacy_graph_bytes_and_execution_remain_unchanged_without_projection_family():
    value = parallel_graph(); before = canonical_json(value)
    assert canonical_json(kernel.validate_graph(value)) == before
    state = finish(value, finish(value, kernel.new_run(value, {}, 10), 'a'), 'b')
    assert state['status'] == 'succeeded' and canonical_json(value) == before
