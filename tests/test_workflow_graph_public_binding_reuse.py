"""Public graph projection with real frozen binding/graph validation.

Current goal/route authority reads use explicit IO spies, called every time.
This proves local duplicate removal, not database authority or live HTTP UX.
"""
import copy
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_inputs as inputs
from generic_agents import workflow_graph as workflow
from generic_agents import workflow_graph_goal_view_v133 as goal_view
from generic_agents import signal_graph_routing_v134 as routing
from generic_agents import workflow_graph_waits as waits
from tests.test_business_flow_runtime_pure_dedup import scene


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure graph view test prohibits DB/network/process IO')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def projection_scene(monkeypatch):
    c = scene(monkeypatch, with_ability=False)
    c.snapshot['profile'] = {'domain': '公开合成业务'}
    c.p.role = 'operator'
    state = workflow.graph_api.new_run(c.binding['graph'], {'source_input': '已保存完整输入'}, 10)
    first = state['ready_human_ids'][0]
    node = next(item for item in c.binding['graph']['nodes']
        if item['id'] == state['activations'][first]['node_id'])
    c.p.user_id = node['actor_id']
    task = NS(id='public-task', owner_id=c.p.user_id, workspace_id=c.p.workspace_id,
        status='workflow_waiting', version=3, created_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
        title='核对观测与处置结果', data={'graph_runtime': state, 'graph_jobs': {}})
    reads = []; full_bindings = []; descriptions = []; enabled_bindings = []
    actual_binding = workflow.business_binding
    actual_describe = inputs.describe
    actual_enabled = waits.enabled

    def binding(release, business):
        assert release is c.release and business is c.business
        result = actual_binding(release, business)
        full_bindings.append(result)
        return result

    def describe(value, current, activation):
        descriptions.append(value)
        return actual_describe(value, current, activation)

    def enabled(value):
        enabled_bindings.append(value)
        return actual_enabled(value)

    flags = {'goal': 'active', 'route': 'active'}
    def goal(database, principal, actual):
        assert database is c.db and principal is c.p and actual is task
        reads.append('goal')
        active = flags['goal'] == 'active'
        return {'schema': 'workflow-graph-goal-gate-v133', 'status': flags['goal'],
            'reason': None if active else 'actual current goal boundary rejected',
            'can_submit_new_result': active, 'can_retry_ability': active}

    def route(database, principal, actual):
        assert database is c.db and principal is c.p and actual is task
        reads.append('route')
        active = flags['route'] == 'active'
        return {'schema': 'signal-graph-task-state-v134', 'status': flags['route'],
            'message': 'actual current route boundary', 'can_submit_new_result': active,
            'can_retry_ability': active, 'delivery_id': 'original-delivery'}

    def duplicate_graph_lookup(*args):
        pytest.fail('public_view must use the graph of its one complete checked binding')

    monkeypatch.setattr(workflow, 'business_binding', binding)
    monkeypatch.setattr(workflow, 'graph_for', duplicate_graph_lookup)
    monkeypatch.setattr(inputs, 'describe', describe)
    monkeypatch.setattr(waits, 'enabled', enabled)
    monkeypatch.setattr(goal_view, 'project', goal)
    monkeypatch.setattr(routing, 'task_state', route)
    return NS(**{**vars(c), 'task': task, 'reads': reads, 'full_bindings': full_bindings,
        'descriptions': descriptions, 'enabled_bindings': enabled_bindings, 'flags': flags})


def test_public_view_checks_one_full_binding_and_preserves_original_inputs_and_authority_reads(monkeypatch):
    c = projection_scene(monkeypatch)
    before = copy.deepcopy((c.task.data, c.release.data))
    result = workflow.public_view(c.task, c.release, c.business, c.p, db=c.db)
    assert len(c.full_bindings) == 1
    binding = c.full_bindings[0]
    assert binding == c.binding and binding is not c.binding
    assert {'source', 'execution_bindings', 'translation', 'role_bindings', 'capability_pins'} <= set(binding)
    assert c.descriptions and all(value is binding for value in c.descriptions)
    assert c.enabled_bindings == [binding] and c.enabled_bindings[0] is binding
    assert c.reads == ['goal', 'route']
    assert result['graph_goal_gate_v133']['status'] == 'active'
    assert result['signal_graph_route_v134']['delivery_id'] == 'original-delivery'
    assert result['business'] == c.business and result['roles'] == c.snapshot['roles']
    assert result['profile'] == c.snapshot['profile'] and result['completed'] is False
    human = result['graph_pending_humans'][0]
    assert human['can_edit'] is True
    assert human['required_inputs'] == inputs.describe(binding, c.task.data['graph_runtime'], human['activation_id'])
    assert human['inputs_available'] is all(item['available'] for item in human['required_inputs'])
    assert result['permissions']['can_run_ability'] is False
    assert (c.task.data, c.release.data) == before


@pytest.mark.parametrize('fault', ['plan', 'source_binding', 'business', 'run'])
def test_bad_frozen_binding_or_run_fails_before_current_authority_projection(monkeypatch, fault):
    c = projection_scene(monkeypatch)
    if fault == 'plan': c.snapshot['semantic_runtime_plan']['plan_hash'] = '0' * 64
    elif fault == 'source_binding':
        c.snapshot['semantic_runtime_plan']['graph_adoptions'][0]['binding']['flow']['source_flow']['nodes'][0]['name'] = '改换原节点'
    elif fault == 'business': c.business['name'] = '未绑定业务'
    else: c.task.data['graph_runtime']['graph_hash'] = '0' * 64
    with pytest.raises((ValueError, HTTPException)):
        workflow.public_view(c.task, c.release, c.business, c.p, db=c.db)
    assert c.reads == []


def test_following_view_rechecks_full_binding_and_current_goal_route_boundaries(monkeypatch):
    c = projection_scene(monkeypatch)
    workflow.public_view(c.task, c.release, c.business, c.p, db=c.db)
    c.flags.update(goal='unavailable', route='paused')
    result = workflow.public_view(c.task, c.release, c.business, c.p, db=c.db)
    assert len(c.full_bindings) == 2
    assert c.full_bindings[0] is not c.full_bindings[1]
    assert c.reads == ['goal', 'route', 'goal', 'route']
    assert result['graph_goal_gate_v133']['can_submit_new_result'] is False
    assert result['signal_graph_route_v134']['can_retry_ability'] is False
    c.snapshot['semantic_runtime_plan']['plan_hash'] = '0' * 64
    with pytest.raises((ValueError, HTTPException)):
        workflow.public_view(c.task, c.release, c.business, c.p, db=c.db)
    assert c.reads == ['goal', 'route', 'goal', 'route']
