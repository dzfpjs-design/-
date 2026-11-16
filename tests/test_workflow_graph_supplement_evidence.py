"""Pure topology checks for the separately proved original V288 route."""
import pytest
from fastapi import HTTPException

from generic_agents import workflow_graph_supplement_evidence as supplement


@pytest.fixture
def route():
    kinds = {'context_reader': 'ability', 'local_prepare': 'ability', 'route_acquisition': 'decision',
        'resense': 'ability', 'evaluation_packet': 'ability', 'route_evaluated': 'decision',
        'review_data': 'human', 'original_review': 'human', 'end': 'end'}
    names = list(kinds)
    graph = {'nodes': [{'id': name, 'kind': kind} for name, kind in kinds.items()], 'edges': [
        {'id': 'edge-' + str(index), 'from': left, 'to': right,
            'on': {'route_acquisition': 'true', 'route_evaluated': 'false'}.get(left, 'success'),
            'branch_id': None, 'loop_id': None}
        for index, (left, right) in enumerate(zip(names, names[1:]), 1)]}
    outputs = {'route_acquisition': {'truth': 'true'}, 'route_evaluated': {'truth': 'false'},
        'evaluation_packet': {'route': 'review_required'}}
    state = {'cancel_epoch': 0, 'goal_receipts': [], 'ready_human_ids': [], 'eligible_ability_ids': [],
        'activations': {'a' + str(index): {'id': 'a' + str(index), 'node_id': name, 'status': 'completed',
            'epoch': 0, 'scope_id': None, 'branch_id': None, 'output': outputs.get(name, {})}
            for index, name in enumerate(names, 1)}}
    return graph, {'step_mapping': [{'node_id': 'original_review'}]}, state, names


def test_completed_false_route_uses_exact_original_activation_links(route):
    graph, binding, state, names = route
    _, _, path, links = supplement._route(graph, binding, state)
    assert path == names
    assert links[5] == {'kind': 'link', 'source_activation_id': 'a6',
        'activation_id': 'a7', 'edge_id': 'edge-6'}


@pytest.mark.parametrize('fault', ['untraversed_node', 'true_route', 'wrong_edge', 'unfinished_end', 'fake_goal_receipt'])
def test_false_route_rejects_missing_or_substituted_graph_work(route, fault):
    graph, binding, state, _ = route
    if fault == 'untraversed_node':
        state['activations']['a10'] = {**state['activations']['a9'], 'id': 'a10', 'node_id': 'reasoning'}
    elif fault == 'true_route':
        state['activations']['a6']['output'] = {'truth': 'true'}
    elif fault == 'wrong_edge':
        graph['edges'][5]['on'] = 'true'
    elif fault == 'unfinished_end':
        state['activations']['a9']['status'] = 'ready'
    else:
        state['goal_receipts'] = [{'goal_id': 'business_complete'}]
    with pytest.raises(HTTPException):
        supplement._route(graph, binding, state)
