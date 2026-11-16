"""Synthetic pure trace algebra only; no registry, SDK, IAM or ACK proof.

The paired PG suite creates all actual producer Jobs and SDK records. Fake
identities here exercise only a server collector passed to the real kernel.
"""
import copy
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import graph_wait_kernel_v118 as waits
from generic_agents import workflow_job_dependencies_v264 as deps
from generic_agents.contracts import digest
from tests.test_full_semantic_graph_v1 import (
    graph, nested_graph, loop_graph, wait_graph, activation, ability, object_schema,
)
from tests.test_graph_wait_kernel_v118 import cut, receipt, command, ENTITY, BINDING, SCOPE


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def observer_state(observer):
    return {'frontiers': copy.deepcopy(observer.frontiers), 'arrivals': copy.deepcopy(observer.arrivals)}


def complete_pair(value, state, node_id, output, *, observer=None, request_id=None, now=10):
    item = activation(state, node_id); node = next(v for v in value['nodes'] if v['id'] == node_id)
    args = (value, state, item['id'], output, request_id or 'done_' + item['id'], node['actor_id'],
        item['version'], item['epoch'], now)
    plain = kernel.complete(*args)
    traced = kernel.complete(*args, **deps.observer_options(observer))
    assert canonical(plain) == canonical(traced) and digest(plain) == digest(traced)
    assert kernel.check_run(value, traced) == traced
    return traced


def make_abilities(value, nodes, output_key='ok'):
    value = copy.deepcopy(value)
    for index, node in enumerate(value['nodes']):
        if node['id'] in nodes:
            replacement = ability(node['id'], {})
            replacement['execution']['arguments'] = {'approved': True}
            replacement['execution']['output_schema'] = object_schema(**{output_key: {'type': 'boolean'}})
            value['nodes'][index] = replacement
    return value


@pytest.mark.parametrize('factory', [graph, nested_graph, loop_graph, wait_graph])
def test_observer_off_and_on_initialize_have_identical_old_canonical_state_and_hash(factory):
    value = factory(); inputs = {'request': 'r1'} if factory is wait_graph else {}
    raw = copy.deepcopy((value, inputs)); events = []
    plain = kernel.new_run(value, inputs, 10)
    traced = kernel.new_run(value, inputs, 10, _observer=events.append)
    assert canonical(plain) == canonical(traced) and plain['state_hash'] == traced['state_hash']
    assert (value, inputs) == raw and events
    assert all(not set(event) & {'output', 'inputs', 'context', 'correlation', 'values'} for event in events)
    assert kernel._hash(kernel.validate_graph(value)) == traced['graph_hash']


def test_real_nested_join_arrivals_union_exact_frontiers_and_do_not_wait_for_other_ready_branch():
    value = make_abilities(nested_graph(), {'x', 'y', 'b'}); observer = deps.ControlObserver()
    state = kernel.new_run(value, {}, 10, _observer=observer)
    assert state == kernel.new_run(value, {}, 10)
    x, y, b = (activation(state, node)['id'] for node in ('x', 'y', 'b'))
    jobs = {node: str(uuid4()) for node in ('x', 'y', 'b')}
    for node in ('x', 'b', 'y'):
        item = activation(state, node)
        current = deps.ControlObserver(observer_state(observer), producer_job_id=jobs[node], activation_id=item['id'])
        state = complete_pair(value, state, node, {'ok': True}, observer=current)
        observer = current
        if node == 'x':
            assert observer.frontiers[y] == [] and observer.frontiers[b] == []
            inner = next(scope for scope in state['fork_scopes'].values() if scope['fork_id'] == 'inner')
            assert observer.frontiers[inner['join_activation_id']] == [jobs['x']]
    outer = next(scope for scope in state['fork_scopes'].values() if scope['fork_id'] == 'f')
    inner = next(scope for scope in state['fork_scopes'].values() if scope['fork_id'] == 'inner')
    assert observer.frontiers[inner['join_activation_id']] == sorted([jobs['x'], jobs['y']])
    assert observer.frontiers[outer['join_activation_id']] == sorted(jobs.values())
    assert state['status'] == 'succeeded' and all(s['closed'] for s in state['fork_scopes'].values())


def test_bounded_loop_keeps_each_real_transition_frontier_not_previous_iteration_or_global_union():
    value = make_abilities(loop_graph(), {'h'}, 'again'); observer = deps.ControlObserver()
    state = kernel.new_run(value, {}, 10, _observer=observer); old_ids = []
    for index, again in enumerate((True, True, False)):
        item = activation(state, 'h'); old_ids.append(item['id']); job_id = str(uuid4())
        current = deps.ControlObserver(observer_state(observer), producer_job_id=job_id, activation_id=item['id'])
        state = complete_pair(value, state, 'h', {'again': again}, observer=current, now=10 + index)
        assert current.frontiers[item['id']] == [job_id]
        if again:
            successor = activation(state, 'h')
            assert successor['id'] not in old_ids and current.frontiers[successor['id']] == [job_id]
        observer = current
    assert state['status'] == 'succeeded'


def test_human_event_and_typed_source_wait_propagate_control_without_inventing_producer_jobs():
    value = wait_graph(); observer = deps.ControlObserver()
    state = kernel.new_run(value, {'request': 'r1'}, 10, _observer=observer)
    plain = kernel.event(value, state, 'response', 'review.response', 'r1', {'again': False}, 11)
    current = deps.ControlObserver(observer_state(observer))
    traced = kernel.event(value, state, 'response', 'review.response', 'r1', {'again': False}, 11, _observer=current)
    assert canonical(plain) == canonical(traced)
    traced = complete_pair(value, traced, 'review', {'ok': True}, observer=current, now=12)
    assert all(values == [] for values in current.frontiers.values())
    plain_pair = waits.new_run(value, {'request': ENTITY}, cut(), binding_hash=BINDING,
        entity_ids=[ENTITY], scope_hash=SCOPE)
    typed_observer = deps.ControlObserver()
    traced_pair = waits.new_run(value, {'request': ENTITY}, cut(), binding_hash=BINDING,
        entity_ids=[ENTITY], scope_hash=SCOPE, _observer=typed_observer)
    assert canonical(plain_pair) == canonical(traced_pair)
    state, authority = traced_pair; next_observer = deps.ControlObserver(observer_state(typed_observer))
    args = (value, state, authority, cut(11, 101), [receipt()])
    plain_pair = waits.transition(*args, scope_hash=SCOPE)
    traced_pair = waits.transition(*args, scope_hash=SCOPE, _observer=next_observer)
    assert canonical(plain_pair) == canonical(traced_pair)
    state, authority = traced_pair; final_observer = deps.ControlObserver(observer_state(next_observer))
    args = (value, state, authority, cut(11, 101), [])
    completion = command(state, 'review')
    plain_pair = waits.transition(*args, scope_hash=SCOPE, operation='complete', command=completion)
    traced_pair = waits.transition(*args, scope_hash=SCOPE, operation='complete', command=completion, _observer=final_observer)
    assert canonical(plain_pair) == canonical(traced_pair)
    assert set(final_observer.frontiers) == set(traced_pair[0]['activations'])
    assert all(values == [] for values in final_observer.frontiers.values())


def test_cached_completion_and_observer_event_mutation_do_not_change_original_state_or_add_frontier():
    value = graph(); observer = deps.ControlObserver()
    state = kernel.new_run(value, {}, 10, _observer=observer)
    item = activation(state, 'a'); state = complete_pair(value, state, 'a', {'ok': True}, observer=observer, request_id='original')
    events = []
    replay = kernel.complete(value, state, item['id'], {'ok': True}, 'original', 'operator_a', 0, 0, 10, _observer=events.append)
    assert replay == state and events == []
    def mutate(event): event.clear(); event['untrusted'] = 'not state data'
    assert kernel.new_run(value, {}, 10, _observer=mutate) == kernel.new_run(value, {}, 10)
    assert all(values == [] for values in observer.frontiers.values())


def test_collector_fails_at_bounds_without_silently_truncating_trace_or_crossing_collectors():
    a = deps.ControlObserver(); b = deps.ControlObserver()
    event = {'kind': 'spawn', 'activation_id': 'a1', 'node_id': 'n', 'scope_id': None, 'branch_id': None}
    a(event)
    assert b.events == [] and b.frontiers == {} and event['activation_id'] == 'a1'
    with pytest.raises(HTTPException): a(event)
    a.events = [{}] * deps.MAX_TRACE
    with pytest.raises(HTTPException): a({'kind': 'complete', 'activation_id': 'a1'})
    assert len(a.events) == deps.MAX_TRACE
