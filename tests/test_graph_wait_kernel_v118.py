"""Pure synthetic cuts only; actual admission/scope/DB ordering is external."""
import copy
import json
from uuid import UUID

import pytest

from generic_agents import full_semantic_graph_v1 as legacy
from generic_agents import graph_wait_kernel_v118 as waits
from tests.test_full_semantic_graph_v1 import (
    activation, base, control, edge, guards, human, input_reference,
    object_schema, output_reference, wait_graph,
)
from tests.test_full_semantic_graph_batch_events import parallel_wait_graph


ENTITY = str(UUID(int=1))
OTHER = str(UUID(int=2))
PRODUCER = str(UUID(int=3))
BINDING = 'a' * 64
SCOPE = 'b' * 64
CONTRACT = 'c' * 64


def cut(sequence=10, at=100):
    return {'sequence': sequence, 'at': at}


def start(graph=None, inputs=None, *, checkpoint=None, entity_ids=None):
    graph = wait_graph() if graph is None else graph
    state, authority = waits.new_run(graph, {'request': ENTITY} if inputs is None else inputs,
        cut() if checkpoint is None else checkpoint, binding_hash=BINDING,
        entity_ids=[ENTITY] if entity_ids is None else entity_ids, scope_hash=SCOPE)
    return graph, state, authority


def receipt(identity='event', *, sequence=11, received_at=101, correlation=ENTITY,
        event_type='review.response', again=False, producer_id=PRODUCER):
    return {'event_id': identity, 'event_type': event_type, 'correlation': correlation,
        'output': {'again': again}, 'sequence': sequence, 'received_at': received_at,
        'producer_id': producer_id, 'contract_hash': CONTRACT}


def advance(graph, state, authority, checkpoint=None, receipts=None, **kwargs):
    return waits.transition(graph, state, authority, cut(11, 101) if checkpoint is None else checkpoint,
        [] if receipts is None else receipts, scope_hash=SCOPE, **kwargs)


def command(state, node, *, output=None, request_id='complete'):
    item = activation(state, node)
    return {'activation_id': item['id'], 'output': {'ok': True} if output is None else output,
        'request_id': request_id, 'actor_id': 'operator_' + node,
        'expected_version': item['version'], 'cancel_epoch': item['epoch']}


def checked(graph, state, authority, *, entity_ids=None):
    return waits.check(graph, state, authority, binding_hash=BINDING,
        entity_ids=[ENTITY] if entity_ids is None else entity_ids, scope_hash=SCOPE)


def parallel_human_wait(*, guarded=False, stop_after_message=False):
    wait = control('w', 'wait', subscription_id='response', timeout_seconds=5)
    person = human('h')
    nodes = [control('f', 'fork', join_id='j', branch_ids=['A', 'B']), wait, person,
        control('j', 'join', fork_id='f', policy='all'), control('end', 'end')]
    edges = [edge('f', 'w', 'branch', 'A'), edge('f', 'h', 'branch', 'B'),
        edge('w', 'j'), edge('w', 'j', 'timeout'), edge('h', 'j'), edge('j', 'end')]
    value = base(nodes, edges, 'f', ['w', 'h'])
    value['input_schema'] = object_schema(request={'type': 'string'})
    value['references']['request'] = input_reference('request')
    value['subscriptions'] = [{'id': 'response', 'event_type': 'review.response', 'correlation_ref': 'request',
        'output_schema': object_schema(again={'type': 'boolean'})}]
    if guarded:
        value['references']['approval'] = output_reference('h', 'ok')
        value['predicates']['approved'] = {'op': 'ref', 'key': 'approval'}
        wait['guards']['start'] = 'approved'
    if stop_after_message:
        value['predicates']['stop_now'] = {'op': 'value', 'kind': 'boolean', 'value': True}
        halt = control('halt', 'decision', predicate_id='stop_now')
        halt['guards']['stop'] = 'stop_now'; value['nodes'].append(halt)
        value['edges'] = [item for item in value['edges'] if not (item['from'] == 'w' and item['on'] == 'success')]
        value['edges'] += [edge('w', 'halt'), edge('halt', 'j', 'true'), edge('halt', 'j', 'false')]
    return value


def test_new_run_keeps_original_v1_state_and_freezes_the_actual_open_cut():
    graph, state, authority = start()
    assert state == legacy.new_run(graph, {'request': ENTITY}, 100)
    assert set(state) == set(legacy.new_run(graph, {'request': ENTITY}, 100))
    window = authority['windows'][activation(state, 'w', status='waiting_event')['id']]
    assert window['open_sequence'] == 10 and window['opened_at'] == 100 and window['deadline'] == 105
    assert window['status'] == 'open' and window['event_id'] is None
    assert checked(graph, state, authority) == authority
    detached = checked(graph, state, authority); detached['windows'].clear()
    assert authority['windows']


def test_open_cut_excludes_previous_admissions_without_retiming_them():
    graph, state, authority = start()
    old = receipt('pre_open', sequence=10, received_at=100)
    result, sidecar = advance(graph, state, authority, cut(10, 104), [old])
    assert activation(result, 'w', status='waiting_event')
    assert not result['events'] and not sidecar['receipts']
    result, sidecar = advance(graph, result, sidecar, cut(11, 104), [old, receipt(received_at=104)])
    assert activation(result, 'review')
    assert [item['event_id'] for item in result['events']] == ['event']
    assert sidecar['receipts']['event']['received_at'] == 104


@pytest.mark.parametrize('reverse', [False, True])
def test_full_batch_is_loaded_before_late_processing_can_timeout_parallel_waits(reverse):
    graph = parallel_wait_graph()
    graph, state, authority = start(graph, {'request_a': ENTITY, 'request_b': OTHER}, entity_ids=[OTHER, ENTITY])
    rows = [receipt('a', event_type='review.a', sequence=11, received_at=101),
        receipt('b', event_type='review.b', sequence=12, received_at=102, correlation=OTHER)]
    if reverse: rows.reverse()
    before = copy.deepcopy((graph, state, authority, rows))
    result, sidecar = advance(graph, state, authority, cut(12, 110), rows)
    assert result['status'] == 'succeeded'
    assert all(window['status'] == 'consumed' and window['closed_sequence'] == 12 for window in sidecar['windows'].values())
    assert all(item['status'] != 'timed_out' for item in result['activations'].values())
    assert checked(graph, result, sidecar, entity_ids=[ENTITY, OTHER]) == sidecar
    assert (graph, state, authority, rows) == before


def test_receipt_selection_uses_sequence_not_uuid_or_array_order():
    graph, state, authority = start()
    rows = [receipt('a_later', sequence=12, received_at=101, again=False),
        receipt('z_first', sequence=11, received_at=101, again=True)]
    result, sidecar = advance(graph, state, authority, cut(12, 102), rows)
    item = activation(result, 'w', status='completed')
    assert item['output'] == {'again': True}
    assert sidecar['windows'][item['id']]['event_id'] == 'z_first'
    assert next(row for row in result['events'] if row['event_id'] == 'a_later')['consumed_by'] is None


def test_same_cut_loop_cannot_consume_an_already_buffered_second_message():
    graph, state, authority = start(wait_graph(loop=True))
    rows = [receipt('first', sequence=11, received_at=101, again=True),
        receipt('old_for_next_round', sequence=12, received_at=102)]
    state, authority = advance(graph, state, authority, cut(12, 110), rows)
    second = activation(state, 'w', status='waiting_event')
    assert second['context']['loops']['again'] == 1
    assert authority['windows'][second['id']]['open_sequence'] == 12
    assert authority['windows'][second['id']]['opened_at'] == 110
    assert next(item for item in state['events'] if item['event_id'] == 'old_for_next_round')['consumed_by'] is None
    state, authority = advance(graph, state, authority, cut(13, 111), [receipt('fresh', sequence=13, received_at=111)])
    assert state['status'] == 'succeeded'
    assert state['goal_receipts'][0]['required_activation_ids'] == [second['id']]
    checked(graph, state, authority)


def test_unknown_start_guard_opens_only_when_resolved_and_excludes_earlier_message():
    graph, state, authority = start(parallel_human_wait(guarded=True))
    assert activation(state, 'w', status='waiting_guard') and authority['windows'] == {}
    done = command(state, 'h')
    state, authority = advance(graph, state, authority, cut(12, 103), [receipt(received_at=102)],
        operation='complete', command=done)
    window = authority['windows'][activation(state, 'w', status='waiting_event')['id']]
    assert window['opened_at'] == 103 and window['deadline'] == 108 and window['open_sequence'] == 12
    assert state['events'] == []
    state, authority = advance(graph, state, authority, cut(13, 104), [receipt('current', sequence=13, received_at=104)])
    assert state['status'] == 'succeeded'
    checked(graph, state, authority)


def test_later_window_after_human_work_does_not_fill_inbox_with_preopen_messages():
    graph = wait_graph(); graph['nodes'].append(human('start')); graph['entry'] = 'start'
    graph['edges'].append(edge('start', 'w'))
    graph, state, authority = start(graph)
    assert authority['windows'] == {}
    state, authority = advance(graph, state, authority, cut(12, 103), [receipt(received_at=102)],
        operation='complete', command=command(state, 'start'))
    assert state['events'] == []
    assert authority['windows'][activation(state, 'w', status='waiting_event')['id']]['open_sequence'] == 12


@pytest.mark.parametrize('arrival,expected', [(104.999999, 'completed'), (105, 'timed_out')])
def test_deadline_is_strict_and_source_time_is_never_replaced_by_processing_time(arrival, expected):
    graph, state, authority = start()
    result, sidecar = advance(graph, state, authority, cut(11, 110), [receipt(received_at=arrival)])
    item = activation(result, 'w', status=expected)
    window = sidecar['windows'][item['id']]
    assert window['deadline'] == 105 and window['closed_at'] == 110
    assert window['status'] == ('consumed' if expected == 'completed' else 'timed_out')
    if expected == 'completed': assert result['events'][0]['received_at'] == arrival
    else: assert result['events'] == []


def test_loop_bound_still_halts_and_does_not_use_previous_round_output():
    graph, state, authority = start(wait_graph(loop=True))
    for index in range(3):
        state, authority = advance(graph, state, authority, cut(11 + index, 101 + index), [
            receipt('round_' + str(index), sequence=11 + index, received_at=101 + index, again=True)])
    assert state['status'] == 'failed' and state['error'] == 'loop_limit:again'
    assert len(authority['windows']) == 3 and all(window['status'] == 'consumed' for window in authority['windows'].values())
    checked(graph, state, authority)


def test_duplicate_delivery_is_exact_and_metadata_cannot_change_after_recovery():
    graph, state, authority = start(); row = receipt()
    state, authority = advance(graph, state, authority, cut(11, 101), [row, copy.deepcopy(row)])
    same, sidecar = advance(graph, state, authority, cut(11, 101), [row])
    assert same == state and sidecar == authority and len(state['events']) == 1
    for key, value in [('sequence', 12), ('producer_id', str(UUID(int=9))), ('contract_hash', 'd' * 64), ('received_at', 102)]:
        changed = copy.deepcopy(row); changed[key] = value
        with pytest.raises(ValueError): advance(graph, state, authority, cut(12, 102), [changed])


def test_exact_command_retry_does_not_drain_or_advance_a_new_cut():
    graph, state, authority = start()
    state, authority = advance(graph, state, authority, cut(11, 101), [receipt()])
    done = command(state, 'review')
    state, authority = advance(graph, state, authority, cut(12, 102), operation='complete', command=done)
    assert state['status'] == 'succeeded'
    same, sidecar = waits.transition(graph, state, authority, {'not': 'a new cut'}, None,
        scope_hash=SCOPE, operation='complete', command=done)
    assert same == state and sidecar == authority
    changed = copy.deepcopy(done); changed['output'] = {'ok': False}
    with pytest.raises(ValueError, match='request_conflict'):
        advance(graph, state, authority, cut(13, 103), operation='complete', command=changed)


def test_complete_original_cas_is_checked_before_any_cut_can_expire_another_wait():
    graph, state, authority = start(parallel_human_wait())
    done = command(state, 'h'); done['expected_version'] = 7
    original = copy.deepcopy((state, authority))
    with pytest.raises(ValueError, match='activation_stale'):
        advance(graph, state, authority, cut(11, 110), operation='complete', command=done)
    assert (state, authority) == original
    assert activation(state, 'w', status='waiting_event')


def test_complete_cannot_override_a_stop_reached_by_the_whole_batch():
    graph, state, authority = start(parallel_human_wait(stop_after_message=True))
    before = copy.deepcopy((state, authority))
    with pytest.raises(ValueError, match='activation_stale'):
        advance(graph, state, authority, cut(11, 101), [receipt()], operation='complete', command=command(state, 'h'))
    assert (state, authority) == before and state['commands'] == []
    stopped, closed = advance(graph, state, authority, cut(11, 101), [receipt()])
    assert stopped['status'] == 'stopped' and stopped['cancel_epoch'] == 1
    checked(graph, stopped, closed)


def test_draft_and_cancel_sync_do_not_drain_or_require_a_new_clock():
    graph, state, authority = start(parallel_human_wait()); item = activation(state, 'h')
    drafted = legacy.save_draft(graph, state, item['id'], {'note': '人工劳动保留'}, 'draft',
        'operator_h', item['version'], item['epoch'], state['clock'])
    draft_authority = waits.sync(graph, drafted, authority, authority['cursor'], scope_hash=SCOPE)
    assert draft_authority == authority and drafted['events'] == []
    cancelled = legacy.cancel(graph, drafted, 'cancel', 'owner', drafted['version'], 0, drafted['clock'], '主动停止')
    with pytest.raises(ValueError, match='epoch'): checked(graph, cancelled, draft_authority)
    closed = waits.sync(graph, cancelled, draft_authority, draft_authority['cursor'], scope_hash=SCOPE)
    assert closed['cancel_epoch'] == 1
    assert all(window['status'] == 'cancelled' and window['closed_sequence'] == 10 for window in closed['windows'].values())
    assert cancelled['activations'][item['id']]['draft'] == {'note': '人工劳动保留'}
    checked(graph, cancelled, closed)
    late, late_authority = advance(graph, cancelled, closed, cut(11, 101), [receipt()])
    assert late['status'] == 'cancelled' and late['events'] == []
    assert all(window['status'] == 'cancelled' for window in late_authority['windows'].values())


@pytest.mark.parametrize('checkpoint', [cut(9, 101), cut(11, 99), {'sequence': True, 'at': 101},
    {'sequence': 11, 'at': float('inf')}, {'sequence': 11, 'at': 101, 'extra': True}])
def test_cut_backwards_or_malformed_is_rejected_without_fabricating_time(checkpoint):
    graph, state, authority = start(); before = copy.deepcopy((state, authority))
    with pytest.raises(ValueError): advance(graph, state, authority, checkpoint)
    assert (state, authority) == before


@pytest.mark.parametrize('mutate', [
    lambda row: row.update(extra='grant'), lambda row: row.update(sequence=True),
    lambda row: row.update(received_at=102), lambda row: row.update(received_at=99),
    lambda row: row.update(correlation=OTHER), lambda row: row.update(correlation=None),
    lambda row: row.update(output={'again': 1}), lambda row: row.update(contract_hash='bad'),
    lambda row: row.update(producer_id='unregistered'), lambda row: row.update(event_type='unknown')])
def test_invalid_later_receipt_rejects_entire_batch_without_partial_buffer(mutate):
    graph, state, authority = start(); bad = receipt('bad'); mutate(bad)
    before = copy.deepcopy((graph, state, authority))
    with pytest.raises(ValueError): advance(graph, state, authority, cut(11, 101), [receipt(), bad])
    assert (graph, state, authority) == before


def test_same_sequence_different_event_and_same_ignored_id_changed_body_are_rejected():
    graph, state, authority = start()
    with pytest.raises(ValueError, match='sequence_conflict'):
        advance(graph, state, authority, cut(11, 101), [receipt('one'), receipt('two')])
    before = receipt('ignored', sequence=10, received_at=100)
    changed = receipt('ignored', sequence=11, received_at=101)
    with pytest.raises(ValueError, match='receipt_changed'):
        advance(graph, state, authority, cut(11, 101), [before, changed])


def test_authority_is_bound_to_task_snapshot_scope_and_exact_frozen_graph_input():
    graph, state, authority = start()
    for changed in ({'binding_hash': 'd' * 64}, {'scope_hash': 'd' * 64}, {'entity_ids': [OTHER]}):
        arguments = {'binding_hash': BINDING, 'entity_ids': [ENTITY], 'scope_hash': SCOPE, **changed}
        with pytest.raises(ValueError): waits.check(graph, state, authority, **arguments)
    modified = copy.deepcopy(authority); modified['windows'].clear()
    with pytest.raises(ValueError, match='authority_hash'): checked(graph, state, modified)
    modified = waits._seal(modified)
    with pytest.raises(ValueError, match='missing_window'): checked(graph, state, modified)
    unsafe = copy.deepcopy(authority); unsafe['entity_ids'] = [ENTITY, ENTITY]
    with pytest.raises(ValueError): checked(graph, state, waits._seal(unsafe))


def test_legacy_direct_advance_cannot_launder_a_timeout_into_new_sidecar():
    graph, state, authority = start()
    advanced = legacy.advance(graph, state, 110)
    assert activation(advanced, 'w', status='timed_out')
    with pytest.raises(ValueError, match='window_open'): checked(graph, advanced, authority)


def test_cold_sealed_window_rejects_earlier_open_cut_and_receipt_metadata_changes():
    graph, state, authority = start()
    changed = copy.deepcopy(authority)
    next(iter(changed['windows'].values()))['open_sequence'] = 9
    with pytest.raises(ValueError, match='window_clock'): checked(graph, state, waits._seal(changed))
    state, authority = advance(graph, state, authority, cut(11, 101), [receipt()])
    changed = copy.deepcopy(authority); changed['receipts']['event']['producer_id'] = str(UUID(int=5))
    with pytest.raises(ValueError, match='receipt_changed'): checked(graph, state, waits._seal(changed))
    assert checked(graph, json.loads(json.dumps(state)), json.loads(json.dumps(authority))) == authority


def test_batch_limit_is_a_hard_failure_never_a_partial_timeout():
    graph, state, authority = start(); before = copy.deepcopy((state, authority))
    with pytest.raises(ValueError, match='batch_limit'):
        advance(graph, state, authority, cut(11, 110), [receipt()] * (legacy.MAX_EVENTS + 1))
    assert (state, authority) == before


def test_cold_timeout_cannot_ignore_a_stored_timely_unconsumed_receipt():
    graph, state, authority = start()
    timed_out, sidecar = advance(graph, state, authority, cut(11, 110))
    row = receipt()
    forged = legacy.event(graph, timed_out, row['event_id'], row['event_type'], row['correlation'],
        row['output'], 110, received_at=row['received_at'])
    assert legacy.check_run(graph, forged) == forged  # The v1 wire alone cannot prove this cut.
    changed = copy.deepcopy(sidecar); changed['receipts'][row['event_id']] = waits._metadata(row)
    with pytest.raises(ValueError, match='skipped_timely_receipt'):
        checked(graph, forged, waits._seal(changed))


def test_cold_consumption_cannot_choose_uuid_order_ahead_of_available_lower_sequence():
    graph, state, authority = start()
    rows = [receipt('a_later', sequence=12, received_at=101), receipt('z_first', sequence=11, received_at=101, again=True)]
    _, sidecar = advance(graph, state, authority, cut(12, 102), rows)
    old_rows = [{key: row[key] for key in ('event_id', 'event_type', 'correlation', 'output', 'received_at')} for row in rows]
    forged = legacy.events(graph, state, old_rows, 102)
    item = activation(forged, 'w', status='completed')
    assert item['output'] == {'again': False}
    changed = copy.deepcopy(sidecar); changed['windows'][item['id']]['event_id'] = 'a_later'
    with pytest.raises(ValueError, match='skipped_timely_receipt'):
        checked(graph, forged, waits._seal(changed))


def initially_halted_graph(phase):
    graph = parallel_human_wait()
    graph['predicates']['initial_halt'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    next(node for node in graph['nodes'] if node['id'] == 'h')['guards'][phase] = 'initial_halt'
    return graph


def test_new_run_records_and_closes_sibling_window_before_initial_stop():
    graph, state, authority = start(initially_halted_graph('stop'))
    assert state['status'] == 'stopped' and state['cancel_epoch'] == 1
    item = activation(state, 'w', status='cancelled')
    window = authority['windows'][item['id']]
    assert window['status'] == 'cancelled' and window['epoch'] == 0
    assert window['open_sequence'] == window['closed_sequence'] == 10
    assert window['opened_at'] == window['closed_at'] == 100
    assert checked(graph, state, authority) == authority
    assert not state['eligible_ability_ids'] and not state['ready_human_ids']


def test_new_run_records_and_closes_sibling_window_before_initial_exception():
    graph, state, authority = start(initially_halted_graph('exception'))
    assert state['status'] == 'failed' and state['error'] == 'exception_guard'
    item = activation(state, 'w', status='cancelled')
    window = authority['windows'][item['id']]
    assert window['status'] == 'cancelled' and window['epoch'] == 0
    assert window['deadline'] == 105 and window['closed_at'] == 100
    assert authority['cancel_epoch'] == 1 and authority['receipts'] == {}
    assert checked(graph, state, authority) == authority
