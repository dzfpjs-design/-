"""Actual server goal projection: read-only explanation, never execution authority."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Task
from generic_agents.worker import process_once
from tests.test_workflow_graph_goal_guard_v133 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime,
    full_source_scene, reply, _task, _stop,
)


def test_actual_projection_separates_call_and_human_limits_and_does_not_write(waiting_release):
    value = waiting_release
    client = value['client']
    zero, _ = _task(value, calls=0)
    path = '/api/workspace/tasks/' + zero['id']
    with Session(engine()) as db:
        before = (digest(db.get(Task, zero['id']).data), sorted(db.scalars(select(Audit.id))))
    gate = reply(client.get(path))['graph_goal_gate_v133']
    assert gate == {'schema': 'workflow-graph-goal-gate-v133', 'status': 'budget_blocked',
        'reason': 'max_tool_calls', 'can_submit_new_result': True, 'can_retry_ability': False}
    with Session(engine()) as db:
        assert before == (digest(db.get(Task, zero['id']).data), sorted(db.scalars(select(Audit.id))))
    _stop(client, zero)
    gate = reply(client.get(path))['graph_goal_gate_v133']
    assert gate['status'] == 'stopped'
    assert not gate['can_submit_new_result'] and not gate['can_retry_ability']

    limited, _ = _task(value, steps=1)
    assert process_once(next(iter(limited['graph_jobs'].values()))['job_id'], deliver=False)
    gate = reply(client.get('/api/workspace/tasks/' + limited['id']))['graph_goal_gate_v133']
    assert gate['status'] == 'budget_blocked' and gate['reason'] == 'max_steps'
    assert not gate['can_submit_new_result'] and not gate['can_retry_ability']
