"""Root-run source-only release-gate cases, using invented graph contracts.

No DB, clock, Event rows, network, providers or runtime state are consulted.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents.workflow_graph_event_sources import (
    INVALID, UNAVAILABLE, require_supported_event_sources,
)
from tests.test_full_semantic_graph_v1 import graph, wait_graph


def denied(value, expected):
    before = copy.deepcopy(value)
    with pytest.raises(HTTPException) as failure:
        require_supported_event_sources(value)
    assert failure.value.status_code == 409
    assert failure.value.detail == expected
    assert value == before


def test_real_non_wait_graph_passes_without_mutation_or_a_fabricated_registry():
    value = graph(); before = copy.deepcopy(value)
    assert require_supported_event_sources(value) is None
    assert value == before


def test_pure_wait_remains_expressible_but_its_production_release_is_blocked():
    value = wait_graph()
    assert any(node['kind'] == 'wait' for node in value['nodes'])
    assert value['subscriptions']
    denied(value, UNAVAILABLE)


def test_actual_adoption_capability_gate_rejects_wait_before_any_database_or_tool_access():
    from generic_agents.report_full_semantic_graph_adoption import capabilities
    with pytest.raises(HTTPException) as failure:
        capabilities(None, None, wait_graph(), [])
    assert failure.value.status_code == 409 and failure.value.detail == UNAVAILABLE


def test_actual_adoption_capability_gate_keeps_no_wait_human_graph_available():
    from generic_agents.report_full_semantic_graph_adoption import capabilities
    assert capabilities(None, None, graph(), []) == {}


@pytest.mark.parametrize('event_type', [
    'observation.data', 'observation.failed', 'perception.anomaly',
    'perception.candidate', 'perception.sensor_packet', 'perception.daily',
    'data.gap', 'data.gap_coordination', 'video.data', 'video.presence',
    'perception.acquisition_escalation', 'synthetic.registered_name',
])
def test_legacy_or_user_chosen_event_names_do_not_declare_a_supported_typed_source(event_type):
    value = wait_graph(); value['subscriptions'][0]['event_type'] = event_type
    denied(value, UNAVAILABLE)


def test_stripping_subscription_or_wait_markers_individually_cannot_bypass_gate():
    value = wait_graph(); value['subscriptions'] = []
    denied(value, UNAVAILABLE)
    value = wait_graph(); value['nodes'] = [node for node in value['nodes'] if node['kind'] != 'wait']
    denied(value, UNAVAILABLE)


@pytest.mark.parametrize('value', [
    None, [], {}, {'schema': 'full-semantic-graph-v1'},
    {'schema': 'other', 'nodes': [], 'subscriptions': []},
    {'schema': 'full-semantic-graph-v1', 'nodes': {}, 'subscriptions': []},
    {'schema': 'full-semantic-graph-v1', 'nodes': [], 'subscriptions': {}},
    {'schema': 'full-semantic-graph-v1', 'nodes': [None], 'subscriptions': []},
    {'schema': 'full-semantic-graph-v1', 'nodes': [{}], 'subscriptions': []},
    {'schema': 'full-semantic-graph-v1', 'nodes': [{'kind': []}], 'subscriptions': []},
    {'schema': 'full-semantic-graph-v1', 'nodes': [{'kind': 'unknown_future_wait'}], 'subscriptions': []},
])
def test_malformed_or_unknown_event_scope_does_not_pass_as_a_no_wait_graph(value):
    denied(value, INVALID)
