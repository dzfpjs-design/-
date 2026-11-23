"""DeepSeek-pinned initial GoalEvaluation on a disposable PG and loopback only.

This reuses the actual six-node graph journey and its independent reception;
it makes no model call and does not claim a second or mature evaluation.
"""
import copy
import socket
import pytest

import tests.test_authored_service_workflow_pg_v270 as authored_fixture
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_auto_evaluation_real_journey_fixture_v356 import (
    published_goal_data as original_published_goal_data,
    test_real_published_six_port_task_with_simulated_scope_and_two_independent_approvals as original_real_journey,
)


@pytest.fixture(autouse=True)
def reject_non_loopback_network(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
                raise PermissionError('test permits loopback only')
        return original(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)


@pytest.fixture
def published_deepseek_goal_data(managed, monkeypatch):
    model = copy.deepcopy(authored_fixture.model_manifest())
    model['name'] = 'synthetic_deepseek'
    model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    monkeypatch.setattr(authored_fixture, 'model_manifest', lambda: copy.deepcopy(model))
    publication = original_published_goal_data.__wrapped__(managed)
    assert publication.model['data']['execution']['provider'] == 'deepseek'
    return publication


def test_deepseek_pinned_graph_reaches_real_initial_evaluation(published_deepseek_goal_data, monkeypatch):
    original_real_journey(published_deepseek_goal_data, monkeypatch)
