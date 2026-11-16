"""Synthetic pure graphs only; real transport, authority and DB proofs are separate."""
import copy
import json

import pytest

from generic_agents import full_semantic_graph_v1 as runner


def guards(**values):
    return {'start': None, 'precondition': None, 'stop': None, 'exception': None, **values}


def object_schema(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def human(identity):
    return {'id': identity, 'kind': 'human', 'source_step_id': 'source_' + identity,
        'guards': guards(), 'actor_id': 'operator_' + identity, 'role': '核对_' + identity,
        'output_schema': object_schema(ok={'type': 'boolean'})}


def control(identity, kind, **fields):
    return {'id': identity, 'kind': kind, 'source_step_id': None, 'guards': guards(), **fields}


def edge(source, target, on='success', branch=None, loop=None):
    return {'id': source + '_' + on + '_' + (branch or '') + '_' + target, 'from': source,
        'to': target, 'on': on, 'branch_id': branch, 'loop_id': loop}


def base(nodes, edges, entry, required):
    return {'schema': runner.SCHEMA, 'id': 'synthetic_graph', 'entry': entry,
        'input_schema': object_schema(), 'references': {}, 'predicates': {}, 'guards': guards(),
        'nodes': nodes, 'edges': edges, 'loops': [], 'subscriptions': [],
        'goals': [{'id': 'business_complete', 'required_nodes': required}]}


def graph():
    return base([control('f', 'fork', join_id='j', branch_ids=['A', 'B']), human('a'), human('b'),
        control('j', 'join', fork_id='f', policy='all'), control('end', 'end')],
        [edge('f', 'a', 'branch', 'A'), edge('f', 'b', 'branch', 'B'),
            edge('a', 'j'), edge('b', 'j'), edge('j', 'end')], 'f', ['a', 'b'])


def nested_graph():
    value = graph(); value['nodes'].remove(next(node for node in value['nodes'] if node['id'] == 'a'))
    value['nodes'] += [control('inner', 'fork', join_id='inner_join', branch_ids=['X', 'Y']),
        human('x'), human('y'), control('inner_join', 'join', fork_id='inner', policy='all')]
    value['edges'] = [item for item in value['edges'] if item['from'] != 'a' and item['to'] != 'a']
    value['edges'] += [edge('f', 'inner', 'branch', 'A'), edge('inner', 'x', 'branch', 'X'),
        edge('inner', 'y', 'branch', 'Y'), edge('x', 'inner_join'), edge('y', 'inner_join'), edge('inner_join', 'j')]
    value['goals'][0]['required_nodes'] = ['x', 'y', 'b']
    return value


def activation(state, node_id, *, status='ready'):
    found = [item for item in state['activations'].values() if item['node_id'] == node_id and item['status'] == status]
    assert len(found) == 1, (node_id, status, state['status'])
    return found[0]


def finish(graph, state, node_id, output=None, request=None, now=10):
    item = activation(state, node_id)
    node = next(node for node in graph['nodes'] if node['id'] == node_id)
    return runner.complete(graph, state, item['id'], {'ok': True} if output is None else output,
        request or 'complete_' + item['id'], node['actor_id'], item['version'], item['epoch'], now)


def test_real_fork_state_has_two_simultaneously_ready_activations_and_no_implicit_order():
    value = graph(); before = copy.deepcopy(value); state = runner.new_run(value, {}, 10)
    assert {state['activations'][identity]['node_id'] for identity in state['ready_human_ids']} == {'a', 'b'}
    assert state['status'] == 'running' and state['goal_receipts'] == []
    assert runner.check_run(value, json.loads(json.dumps(state))) == state
    assert value == before
    reordered = copy.deepcopy(value); reordered['nodes'].reverse(); reordered['edges'].reverse()
    other = runner.new_run(reordered, {}, 10)
    assert {other['activations'][identity]['node_id'] for identity in other['ready_human_ids']} == {'a', 'b'}


def test_duplicate_A_never_substitutes_for_B_and_requests_are_exactly_once():
    value = graph(); initial = runner.new_run(value, {}, 10); a = activation(initial, 'a')
    first = finish(value, initial, 'a', request='A_once')
    scope = next(iter(first['fork_scopes'].values()))
    assert set(scope['arrived']) == {'A'} and scope['closed'] is False
    assert activation(first, 'b')['version'] == 0
    assert runner.complete(value, first, a['id'], {'ok': True}, 'A_once', 'operator_a', 0, 0, 10) == first
    with pytest.raises(ValueError):
        runner.complete(value, first, a['id'], {'ok': False}, 'A_once', 'operator_a', 0, 0, 10)
    with pytest.raises(ValueError):
        runner.complete(value, first, a['id'], {'ok': True}, 'A_different', 'operator_a', 0, 0, 10)
    final = finish(value, first, 'b')
    assert final['status'] == 'succeeded' and len(final['goal_receipts']) == 1
    assert runner.check_run(value, final) == final


def test_nested_forks_join_only_their_own_complete_branch_sets():
    value = nested_graph(); state = runner.new_run(value, {}, 10)
    assert {item['node_id'] for item in runner.pending(value, state) if item['kind'] == 'human'} == {'x', 'y', 'b'}
    state = finish(value, state, 'b'); state = finish(value, state, 'x')
    assert not any(scope['closed'] for scope in state['fork_scopes'].values())
    state = finish(value, state, 'y')
    assert state['status'] == 'succeeded'
    assert all(scope['closed'] and set(scope['arrived']) == set(scope['expected']) for scope in state['fork_scopes'].values())
    assert runner.check_run(value, state) == state


def test_human_draft_version_is_independent_of_sibling_completion_and_survives_roundtrip():
    value = graph(); state = runner.new_run(value, {}, 10); item = activation(state, 'a')
    state = runner.save_draft(value, state, item['id'], {'note': '未完成的人工核对🙂'},
        'save_A', 'operator_a', item['version'], item['epoch'], 10)
    state = finish(value, state, 'b')
    assert activation(state, 'a')['draft'] == {'note': '未完成的人工核对🙂'}
    assert activation(state, 'a')['version'] == 1
    state = finish(value, json.loads(json.dumps(state)), 'a')
    assert state['status'] == 'succeeded' and state['activations'][item['id']]['draft'] is None


def test_schema_and_actor_failures_preserve_original_state_and_no_receipt_is_fabricated():
    value = graph(); state = runner.new_run(value, {}, 10); item = activation(state, 'a'); before = copy.deepcopy(state)
    with pytest.raises(ValueError): runner.complete(value, state, item['id'], {'ok': 1}, 'bad_schema', 'operator_a', 0, 0, 10)
    with pytest.raises(ValueError): runner.complete(value, state, item['id'], {'ok': True}, 'bad_actor', 'operator_b', 0, 0, 10)
    assert state == before and state['commands'] == []


def output_reference(node_id, field, kind='boolean', nullable=False):
    return {'kind': kind, 'nullable': nullable, 'source': 'output', 'field': field, 'node_id': node_id}


def input_reference(field, kind='string', nullable=False):
    return {'kind': kind, 'nullable': nullable, 'source': 'input', 'field': field, 'node_id': None}


def test_unknown_precondition_waits_for_sibling_output_and_never_becomes_false():
    value = graph(); value['references']['b_ok'] = output_reference('b', 'ok')
    value['predicates']['needs_b'] = {'op': 'ref', 'key': 'b_ok'}
    next(node for node in value['nodes'] if node['id'] == 'a')['guards']['precondition'] = 'needs_b'
    state = runner.new_run(value, {}, 10)
    assert activation(state, 'a', status='waiting_guard')
    assert state['predicate_receipts'][-1]['result']['truth'] == 'unknown'
    state = finish(value, state, 'b')
    assert activation(state, 'a')
    assert any(receipt['result']['truth'] == 'unknown' for receipt in state['predicate_receipts'])
    state = finish(value, state, 'a'); assert state['status'] == 'succeeded'


@pytest.mark.parametrize('guard,status', [('stop', 'stopped'), ('exception', 'failed')])
def test_explicit_true_stop_and_exception_guards_fence_all_activations(guard, status):
    value = graph(); value['predicates']['halt'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    value['guards'][guard] = 'halt'; state = runner.new_run(value, {}, 10)
    assert state['status'] == status and state['cancel_epoch'] == 1
    assert not state['eligible_ability_ids'] and not state['ready_human_ids']


@pytest.mark.parametrize('supplied,truth', [({}, 'unknown'), ({'start': None}, 'unknown'), ({'start': False}, 'false')])
def test_start_unknown_null_and_false_all_wait_without_starting_work(supplied, truth):
    value = graph(); value['input_schema'] = object_schema(start={'type': ['boolean', 'null']})
    value['input_schema']['required'] = []
    value['references']['start'] = input_reference('start', 'boolean', True)
    value['predicates']['can_start'] = {'op': 'ref', 'key': 'start'}; value['guards']['start'] = 'can_start'
    state = runner.new_run(value, supplied, 10)
    assert state['status'] == 'waiting' and not state['ready_human_ids']
    assert state['predicate_receipts'][0]['result']['truth'] == truth


def loop_graph():
    work = human('h'); work['output_schema'] = object_schema(again={'type': 'boolean'})
    back = edge('d', 'h', 'true', loop='retry')
    value = base([work, control('d', 'decision', predicate_id='repeat'), control('end', 'end')],
        [edge('h', 'd'), back, edge('d', 'end', 'false')], 'h', ['h'])
    value['references']['again'] = output_reference('h', 'again')
    value['predicates']['repeat'] = {'op': 'ref', 'key': 'again'}
    value['loops'] = [{'id': 'retry', 'edge_id': back['id'], 'max_iterations': 2}]
    return value


def test_bounded_loop_never_resolves_previous_iteration_output_or_goal_receipt():
    value = loop_graph(); state = runner.new_run(value, {}, 10)
    state = finish(value, state, 'h', {'again': True})
    current = activation(state, 'h')
    assert current['context']['loops']['retry'] == 1
    assert runner._resolved(value, state, runner._metadata(value), current['context']) == {}
    state = finish(value, state, 'h', {'again': False})
    assert state['status'] == 'succeeded'
    assert state['goal_receipts'][0]['required_activation_ids'] == [current['id']]
    assert runner.check_run(value, state) == state


def test_loop_limit_is_a_saved_failure_and_never_silently_takes_the_exit():
    value = loop_graph(); value['loops'][0]['max_iterations'] = 1
    state = runner.new_run(value, {}, 10); state = finish(value, state, 'h', {'again': True})
    state = finish(value, state, 'h', {'again': True})
    assert state['status'] == 'failed' and state['error'] == 'loop_limit:retry'
    assert state['goal_receipts'] == []


def test_missing_decision_value_waits_instead_of_selecting_the_false_edge():
    value = loop_graph(); value['nodes'][0]['output_schema']['required'] = []
    state = runner.new_run(value, {}, 10); state = finish(value, state, 'h', {})
    assert activation(state, 'd', status='waiting_guard')
    assert state['status'] == 'waiting' and state['goal_receipts'] == []
    assert state['predicate_receipts'][-1]['result']['truth'] == 'unknown'


def wait_graph(loop=False):
    wait = control('w', 'wait', subscription_id='response', timeout_seconds=5)
    value = base([wait, human('review'), human('timeout_review'), control('end', 'end')],
        [edge('w', 'review'), edge('w', 'timeout_review', 'timeout'), edge('review', 'end'), edge('timeout_review', 'end')],
        'w', ['w', 'review'])
    value['input_schema'] = object_schema(request={'type': 'string'})
    value['references']['request'] = input_reference('request')
    value['subscriptions'] = [{'id': 'response', 'event_type': 'review.response', 'correlation_ref': 'request',
        'output_schema': object_schema(again={'type': 'boolean'})}]
    if loop:
        value['nodes'] = [wait, control('d', 'decision', predicate_id='repeat'), control('end', 'end')]
        back = edge('d', 'w', 'true', loop='again')
        value['edges'] = [edge('w', 'd'), edge('w', 'end', 'timeout'), back, edge('d', 'end', 'false')]
        value['references']['again'] = output_reference('w', 'again')
        value['predicates']['repeat'] = {'op': 'ref', 'key': 'again'}
        value['loops'] = [{'id': 'again', 'edge_id': back['id'], 'max_iterations': 2}]
        value['goals'][0]['required_nodes'] = ['w']
    return value


def test_correlated_events_dedupe_and_timeout_have_distinct_routes():
    value = wait_graph(); state = runner.new_run(value, {'request': 'r1'}, 10)
    state = runner.event(value, state, 'wrong', 'review.response', 'another', {'again': False}, 11)
    assert activation(state, 'w', status='waiting_event')['deadline'] == 15
    state = runner.event(value, state, 'right', 'review.response', 'r1', {'again': False}, 12)
    assert activation(state, 'review')
    same = runner.event(value, state, 'right', 'review.response', 'r1', {'again': False}, 12)
    assert same == state
    with pytest.raises(ValueError): runner.event(value, state, 'right', 'review.response', 'r1', {'again': True}, 12)
    state = finish(value, state, 'review', now=13); assert state['status'] == 'succeeded'
    late = runner.new_run(value, {'request': 'r1'}, 10)
    late = runner.event(value, late, 'late', 'review.response', 'r1', {'again': False}, 15)
    assert activation(late, 'timeout_review') and activation(late, 'w', status='timed_out')
    assert late['events'][0]['consumed_by'] is None
    late = finish(value, late, 'timeout_review', now=16)
    assert late['status'] == 'blocked' and late['error'] == 'required_receipts_missing'


def test_event_from_a_previous_loop_cannot_satisfy_the_new_wait():
    value = wait_graph(loop=True); state = runner.new_run(value, {'request': 'same'}, 10)
    state = runner.event(value, state, 'e1', 'review.response', 'same', {'again': True}, 11)
    second = activation(state, 'w', status='waiting_event')
    assert second['context']['loops']['again'] == 1 and second['output'] is None
    state = runner.event(value, state, 'e1', 'review.response', 'same', {'again': True}, 11)
    assert activation(state, 'w', status='waiting_event')['id'] == second['id']
    state = runner.event(value, state, 'e2', 'review.response', 'same', {'again': False}, 12)
    assert state['status'] == 'succeeded'
    assert state['goal_receipts'][0]['required_activation_ids'] == [second['id']]


def test_event_before_wait_creation_is_retained_but_cannot_authorize_a_future_wait():
    value = wait_graph(); value['nodes'].append(human('start')); value['entry'] = 'start'
    value['edges'].append(edge('start', 'w'))
    state = runner.new_run(value, {'request': 'r1'}, 10)
    state = runner.event(value, state, 'too_early', 'review.response', 'r1', {'again': False}, 11)
    state = finish(value, state, 'start', now=12)
    assert activation(state, 'w', status='waiting_event') and state['events'][0]['consumed_by'] is None
    state = runner.event(value, state, 'current', 'review.response', 'r1', {'again': False}, 13)
    assert activation(state, 'review')


def test_persisted_event_time_survives_delayed_delivery_without_moving_processing_clock_back():
    value = wait_graph(); state = runner.new_run(value, {'request': 'r1'}, 10)
    # A newer nonmatching receipt advanced the processing clock, not this deadline.
    state = runner.event(value, state, 'other', 'review.response', 'other', {'again': False}, 14)
    state = runner.event(value, state, 'persisted', 'review.response', 'r1', {'again': False}, 20, received_at=12)
    assert activation(state, 'review') and state['clock'] == 20
    receipt = next(e for e in state['events'] if e['event_id'] == 'persisted')
    assert receipt['received_at'] == 12 and receipt['consumed_by'] is not None
    assert runner.check_run(value, state) == state
    with pytest.raises(ValueError):
        runner.event(value, state, 'persisted', 'review.response', 'r1', {'again': False}, 21, received_at=13)
    with pytest.raises(ValueError):
        runner.event(value, state, 'future', 'review.response', 'r1', {'again': False}, 21, received_at=22)


def test_delivering_a_historical_source_event_cannot_make_it_new_for_a_later_wait():
    value = wait_graph(); value['nodes'].append(human('start')); value['entry'] = 'start'
    value['edges'].append(edge('start', 'w'))
    state = runner.new_run(value, {'request': 'r1'}, 10)
    state = finish(value, state, 'start', now=12)
    state = runner.event(value, state, 'old_source', 'review.response', 'r1', {'again': False}, 13, received_at=11)
    assert activation(state, 'w', status='waiting_event') and state['events'][0]['consumed_by'] is None
    assert runner.check_run(value, state) == state


def test_cancel_preserves_draft_and_late_complete_cannot_advance_or_resume():
    value = graph(); state = runner.new_run(value, {}, 10); item = activation(state, 'a')
    state = runner.save_draft(value, state, item['id'], '人工输入保留', 'draft', 'operator_a', 0, 0, 10)
    original_version = state['version']
    state = runner.cancel(value, state, 'cancel_once', 'owner', original_version, 0, 11, reason='停止核对')
    assert state['status'] == 'cancelled' and state['cancel_epoch'] == 1
    assert state['activations'][item['id']]['draft'] == '人工输入保留'
    assert runner.cancel(value, state, 'cancel_once', 'owner', original_version, 0, 11, reason='停止核对') == state
    with pytest.raises(ValueError): runner.complete(value, state, item['id'], {'ok': True}, 'late', 'operator_a', 1, 0, 12)
    assert runner.advance(value, state, 12)['status'] == 'cancelled'


def ability(identity, bindings):
    return {'id': identity, 'kind': 'ability', 'source_step_id': 'source_' + identity,
        'guards': guards(), 'actor_id': 'operator_' + identity,
        'execution': {'capability_id': 'registered_ability', 'capability_hash': 'a' * 64,
            'agent_role': 'data', 'input_bindings': bindings, 'arguments': {},
            'input_schema': object_schema(approved={'type': 'boolean'}),
            'output_schema': object_schema(ok={'type': 'boolean'}),
            'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 2}}


def test_ability_input_snapshot_freezes_exact_producer_and_values_for_root_dispatch():
    work = human('producer'); work['output_schema'] = object_schema(ok={'type': 'boolean'})
    task = ability('tool', {'approved': 'approval'})
    value = base([work, task, control('end', 'end')],
        [edge('producer', 'tool'), edge('tool', 'end')], 'producer', ['producer', 'tool'])
    value['references']['approval'] = output_reference('producer', 'ok')
    state = runner.new_run(value, {}, 10); producer = activation(state, 'producer')
    state = finish(value, state, 'producer'); tool = activation(state, 'tool')
    assert state['eligible_ability_ids'] == [tool['id']]
    snapshot = tool['inputs_snapshot']
    assert snapshot['bindings']['approved']['producer_activation_id'] == producer['id']
    assert snapshot['bindings']['approved']['value_hash'] == runner._hash(True)
    assert runner.ability_inputs(value, state, tool['id']) == {'approved': True}
    assert runner.check_run(value, state) == state
    final = finish(value, state, 'tool'); assert final['status'] == 'succeeded'


def test_loop_ability_freezes_the_current_producer_and_preserves_previous_input_snapshot():
    value = loop_graph(); task = ability('tool', {'approved': 'again'})
    value['nodes'].append(task)
    value['edges'] = [item for item in value['edges'] if item['from'] != 'h']
    value['edges'] += [edge('h', 'tool'), edge('tool', 'd')]
    value['goals'][0]['required_nodes'] = ['h', 'tool']
    state = runner.new_run(value, {}, 10); first_human = activation(state, 'h')
    state = finish(value, state, 'h', {'again': True}); first_tool = activation(state, 'tool')
    first_snapshot = copy.deepcopy(first_tool['inputs_snapshot'])
    assert first_snapshot['bindings']['approved']['producer_activation_id'] == first_human['id']
    state = finish(value, state, 'tool'); second_human = activation(state, 'h')
    state = finish(value, state, 'h', {'again': False}); second_tool = activation(state, 'tool')
    assert second_tool['inputs_snapshot']['bindings']['approved']['producer_activation_id'] == second_human['id']
    assert runner.ability_inputs(value, state, second_tool['id']) == {'approved': False}
    assert state['activations'][first_tool['id']]['inputs_snapshot'] == first_snapshot
    assert runner.check_run(value, state) == state
    assert finish(value, state, 'tool')['status'] == 'succeeded'


@pytest.mark.parametrize('kind,field_schema,payload', [
    ('array', {'type': 'array', 'items': {'type': 'object', 'properties': {'value': {'type': 'number'}},
        'required': ['value'], 'additionalProperties': False}}, [{'value': 7.5}, {'value': 3}]),
    ('object', {'type': 'object', 'properties': {'entity_id': {'type': 'string'}},
        'required': ['entity_id'], 'additionalProperties': False}, {'entity_id': 'frozen_synthetic_entity'}),
])
def test_structured_ability_inputs_are_frozen_as_json_without_becoming_predicate_scalars(kind, field_schema, payload):
    task = ability('tool', {'approved': 'records'})
    task['execution']['input_schema'] = object_schema(approved=field_schema)
    value = base([task, control('end', 'end')], [edge('tool', 'end')], 'tool', ['tool'])
    value['input_schema'] = object_schema(records=field_schema)
    value['references']['records'] = input_reference('records', kind)
    value['predicates']['true'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    task['guards']['precondition'] = 'true'
    state = runner.new_run(value, {'records': payload}, 10); item = activation(state, 'tool')
    assert runner.ability_inputs(value, state, item['id']) == {'approved': payload}
    assert item['inputs_snapshot']['bindings']['approved']['value_hash'] == runner._hash(payload)
    assert runner.check_run(value, state) == state
    invalid = copy.deepcopy(value); invalid['predicates']['bad'] = {'op': 'ref', 'key': 'records'}
    with pytest.raises(ValueError): runner.validate_graph(invalid)


def test_structured_output_binding_freezes_exact_producer_without_stringifying():
    producer = human('producer'); field = {'type': 'array', 'items': {'type': 'number'}}
    producer['output_schema'] = object_schema(records=field)
    task = ability('tool', {'approved': 'records'}); task['execution']['input_schema'] = object_schema(approved=field)
    value = base([producer, task, control('end', 'end')], [edge('producer', 'tool'), edge('tool', 'end')], 'producer', ['producer', 'tool'])
    value['references']['records'] = output_reference('producer', 'records', 'array')
    state = runner.new_run(value, {}, 10); source = activation(state, 'producer')
    state = finish(value, state, 'producer', {'records': [2, 3.5]}); item = activation(state, 'tool')
    assert runner.ability_inputs(value, state, item['id']) == {'approved': [2, 3.5]}
    assert item['inputs_snapshot']['bindings']['approved']['producer_activation_id'] == source['id']
    assert runner.check_run(value, state) == state


@pytest.mark.parametrize('change', [
    lambda value: value.update(extra=True),
    lambda value: value['nodes'][0].update(extra=True),
    lambda value: value['nodes'][3].update(policy='any'),
    lambda value: value['edges'][0].update(branch_id='B'),
    lambda value: value['edges'][2].update(to='end'),
    lambda value: value['nodes'][1]['output_schema'].update(properties={'ok': {'$ref': 'https://invalid.example/schema'}}),
])
def test_malformed_graphs_never_become_serial_or_partial_execution(change):
    value = graph(); change(value); before = copy.deepcopy(value)
    with pytest.raises(ValueError): runner.validate_graph(value)
    assert value == before


def test_unbounded_cycles_and_cross_branch_merges_are_rejected():
    value = loop_graph(); value['edges'][1]['loop_id'] = None; value['loops'] = []
    with pytest.raises(ValueError): runner.validate_graph(value)
    value = graph(); value['edges'][1]['to'] = 'a'
    with pytest.raises(ValueError): runner.validate_graph(value)


def test_corrupt_state_and_backward_time_are_rejected_without_mutating_anything():
    value = graph(); state = runner.new_run(value, {}, 10); before = copy.deepcopy(state)
    with pytest.raises(ValueError): runner.advance(value, state, 9)
    broken = copy.deepcopy(state); broken['activations']['a2']['version'] += 1
    with pytest.raises(ValueError): runner.check_run(value, broken)
    assert state == before
