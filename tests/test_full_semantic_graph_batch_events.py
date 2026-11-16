"""Synthetic receipt batches; source authority and durable admission are external."""
import copy
import json

import pytest

from generic_agents import full_semantic_graph_v1 as runner
from tests.test_full_semantic_graph_v1 import (
    activation, base, control, edge, input_reference, object_schema, wait_graph,
)


def receipt(identity='received', *, event_type='review.response', correlation='r1',
        output=None, received_at=12):
    return {'event_id': identity, 'event_type': event_type, 'correlation': correlation,
        'output': {'again': False} if output is None else output, 'received_at': received_at}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)


def parallel_wait_graph():
    value = base([
        control('f', 'fork', join_id='j', branch_ids=['A', 'B']),
        control('wa', 'wait', subscription_id='response_a', timeout_seconds=5),
        control('wb', 'wait', subscription_id='response_b', timeout_seconds=5),
        control('j', 'join', fork_id='f', policy='all'), control('end', 'end')],
        [edge('f', 'wa', 'branch', 'A'), edge('f', 'wb', 'branch', 'B'),
            edge('wa', 'j'), edge('wa', 'j', 'timeout'),
            edge('wb', 'j'), edge('wb', 'j', 'timeout'), edge('j', 'end')],
        'f', ['wa', 'wb'])
    value['input_schema'] = object_schema(request_a={'type': 'string'}, request_b={'type': 'string'})
    value['references'] = {key: input_reference(key) for key in ('request_a', 'request_b')}
    value['subscriptions'] = [{'id': 'response_' + suffix, 'event_type': 'review.' + suffix,
        'correlation_ref': 'request_' + suffix, 'output_schema': object_schema(again={'type': 'boolean'})}
        for suffix in ('a', 'b')]
    return value


def test_timely_match_is_loaded_before_a_wrong_correlation_can_trigger_timeout():
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    rows = [receipt('wrong', correlation='another', received_at=12), receipt('right', received_at=13)]
    original = copy.deepcopy((graph, state, rows))
    # The old single-receipt operation intentionally retains its own semantics.
    single = runner.event(graph, state, 'wrong', 'review.response', 'another', {'again': False}, 20, received_at=12)
    assert activation(single, 'w', status='timed_out')
    result = runner.events(graph, state, rows, 20)
    assert activation(result, 'w', status='completed')['completed_at'] == 20
    assert activation(result, 'review') and result['clock'] == 20
    assert {item['event_id']: item['consumed_by'] is not None for item in result['events']} == {'wrong': False, 'right': True}
    assert (graph, state, rows) == original
    assert runner.check_run(graph, result) == result
    rows[1]['output']['again'] = True
    assert activation(result, 'w', status='completed')['output'] == {'again': False}
    assert result['events'][1]['output'] == {'again': False}


@pytest.mark.parametrize('reverse', [False, True])
def test_all_parallel_waits_see_the_whole_timely_batch_before_one_advance(reverse):
    graph = parallel_wait_graph(); state = runner.new_run(graph, {'request_a': 'a', 'request_b': 'b'}, 10)
    rows = [receipt('wrong', event_type='review.a', correlation='other', received_at=11),
        receipt('a', event_type='review.a', correlation='a', received_at=12),
        receipt('b', event_type='review.b', correlation='b', received_at=13)]
    if reverse: rows.reverse()
    result = runner.events(graph, state, rows, 20)
    assert result['status'] == 'succeeded'
    assert activation(result, 'wa', status='completed') and activation(result, 'wb', status='completed')
    assert all(item['status'] != 'timed_out' for item in result['activations'].values())
    assert len({item['consumed_by'] for item in result['events'] if item['consumed_by'] is not None}) == 2
    assert runner.check_run(graph, result) == result


@pytest.mark.parametrize(('correlation', 'received_at', 'now'), [
    ('r1', 12, 12), ('other', 12, 20), ('r1', 12, 20), ('r1', 15, 20),
    ('r1', 9, 20), ('r1', 12.0, 20.0),
])
def test_one_row_is_byte_exactly_equivalent_to_the_explicit_single_receipt(correlation, received_at, now):
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    row = receipt(correlation=correlation, received_at=received_at)
    single = runner.event(graph, state, row['event_id'], row['event_type'], correlation,
        row['output'], now, received_at=received_at)
    batch = runner.events(graph, state, [row], now)
    assert encoded(batch) == encoded(single)
    assert batch['state_hash'] == single['state_hash']


def test_empty_batch_is_exactly_advance():
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    assert encoded(runner.events(graph, state, [], 20)) == encoded(runner.advance(graph, state, 20))


def test_duplicate_rows_are_one_receipt_and_recovery_keeps_the_original_timestamp():
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    row = receipt(received_at=12.0)
    result = runner.events(graph, state, [row, copy.deepcopy(row)], 20)
    assert len(result['events']) == 1 and type(result['events'][0]['received_at']) is float
    assert runner.events(graph, result, [row, copy.deepcopy(row)], 20) == result
    assert runner.event(graph, result, row['event_id'], row['event_type'], row['correlation'], row['output'], 20) == result
    assert type(result['events'][0]['received_at']) is float


@pytest.mark.parametrize('change', [
    lambda row: row.update(extra=True),
    lambda row: row.pop('received_at'),
    lambda row: row.update(received_at=None),
    lambda row: row.update(received_at=True),
    lambda row: row.update(received_at=21),
    lambda row: row.update(received_at=float('inf')),
    lambda row: row.update(event_id=None),
    lambda row: row.update(event_type='unknown'),
    lambda row: row.update(correlation=None),
    lambda row: row.update(correlation=1),
    lambda row: row.update(output={}),
    lambda row: row.update(output={'again': 1}),
])
def test_invalid_later_row_rejects_the_whole_batch_without_mutating_any_input(change):
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    bad = receipt('bad'); change(bad)
    rows = [receipt('valid'), bad]; original_state = encoded(state); original_graph = encoded(graph)
    original_rows = copy.deepcopy(rows)
    with pytest.raises(ValueError): runner.events(graph, state, rows, 20)
    assert encoded(state) == original_state and encoded(graph) == original_graph
    assert rows == original_rows


@pytest.mark.parametrize('change', [
    lambda row: row.update(output={'again': True}),
    lambda row: row.update(correlation='another'),
    lambda row: row.update(received_at=13),
])
@pytest.mark.parametrize('already_persisted', [False, True])
def test_identity_or_original_timestamp_conflict_is_atomic(change, already_persisted):
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    row = receipt(); changed = copy.deepcopy(row); change(changed)
    if already_persisted:
        state = runner.events(graph, state, [row], 12)
        rows = [receipt('new', correlation='other'), changed]
    else: rows = [row, changed]
    before = encoded(state)
    with pytest.raises(ValueError): runner.events(graph, state, rows, 20)
    assert encoded(state) == before


@pytest.mark.parametrize('rows', [None, {}, (), 'events', [None], [[]]])
def test_batch_and_rows_are_closed_json_containers(rows):
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    before = encoded(state)
    with pytest.raises(ValueError): runner.events(graph, state, rows, 20)
    assert encoded(state) == before


def test_capacity_failure_cannot_leave_the_first_new_receipt_buffered():
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    rows = [receipt('old_' + str(index), correlation='other', received_at=10)
        for index in range(runner.MAX_EVENTS - 1)]
    state = runner.events(graph, state, rows, 10); before = encoded(state)
    first = receipt('last_slot', correlation='other', received_at=10)
    second = receipt('overflow', correlation='other', received_at=10)
    with pytest.raises(ValueError): runner.events(graph, state, [first, second], 10)
    assert encoded(state) == before
    full = runner.events(graph, state, [first], 10)
    assert len(full['events']) == runner.MAX_EVENTS
    assert runner.events(graph, full, [first], 10) == full
    with pytest.raises(ValueError): runner.events(graph, full, [second], 10)
    with pytest.raises(ValueError): runner.events(graph, state, [first] * (runner.MAX_EVENTS + 1), 10)


def test_numeric_correlation_and_output_keep_their_original_json_types():
    graph = wait_graph(); graph['input_schema'] = object_schema(request={'type': 'number'})
    graph['references']['request'] = input_reference('request', 'number')
    graph['subscriptions'][0]['output_schema'] = object_schema(value={'type': 'number'})
    state = runner.new_run(graph, {'request': 1.0}, 10)
    rows = [receipt('integer', correlation=1, output={'value': 2}, received_at=12),
        receipt('float', correlation=1.0, output={'value': 2.0}, received_at=12.0)]
    result = runner.events(graph, state, rows, 20)
    assert type(result['events'][0]['correlation']) is int
    assert type(result['events'][1]['correlation']) is float
    assert type(result['events'][1]['received_at']) is float
    assert result['events'][0]['consumed_by'] is None
    wait = activation(result, 'w', status='completed')
    assert result['events'][1]['consumed_by'] == wait['id'] and type(wait['output']['value']) is float
    assert runner.check_run(graph, result) == result


def test_cancelled_run_preserves_late_receipts_without_resuming_any_activation():
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    state = runner.cancel(graph, state, 'cancel', 'owner', state['version'], 0, 11, '停止处理')
    before = copy.deepcopy(state); row = receipt()
    result = runner.events(graph, state, [row], 20)
    assert result['status'] == 'cancelled' and result['cancel_epoch'] == 1
    assert result['activations'] == state['activations'] and result['next_activation'] == state['next_activation']
    assert result['events'][0]['consumed_by'] is None
    single = runner.event(graph, state, row['event_id'], row['event_type'], row['correlation'], row['output'], 20, received_at=12)
    assert encoded(result) == encoded(single) and state == before
    assert runner.events(graph, result, [row], 20) == result
    with pytest.raises(ValueError): runner.events(graph, result, [receipt('future', received_at=21)], 20)


def test_consumed_or_pre_activation_receipts_cannot_satisfy_a_later_loop_wait():
    graph = wait_graph(loop=True); state = runner.new_run(graph, {'request': 'r1'}, 10)
    rows = [receipt('first', output={'again': True}, received_at=12),
        receipt('before_next_activation', received_at=13)]
    result = runner.events(graph, state, rows, 20)
    waiting = activation(result, 'w', status='waiting_event')
    assert waiting['created_at'] == 20 and waiting['deadline'] == 25
    assert result['events'][1]['consumed_by'] is None
    assert runner.events(graph, result, rows, 20) == result
    finished = runner.events(graph, result, [receipt('next', received_at=21)], 21)
    assert finished['status'] == 'succeeded'
    assert finished['events'][-1]['consumed_by'] == waiting['id']
    assert runner.check_run(graph, finished) == finished


@pytest.mark.parametrize('reverse', [False, True])
def test_match_selection_uses_original_time_then_identity_instead_of_batch_order(reverse):
    graph = wait_graph(); state = runner.new_run(graph, {'request': 'r1'}, 10)
    rows = [receipt('z', output={'again': False}, received_at=12),
        receipt('a', output={'again': True}, received_at=12), receipt('older', received_at=13)]
    if reverse: rows.reverse()
    result = runner.events(graph, state, rows, 20)
    selected = [row['event_id'] for row in result['events'] if row['consumed_by'] is not None]
    assert selected == ['a'] and activation(result, 'w', status='completed')['output'] == {'again': True}
