"""Independent SQL/Audit origin must survive removal of Task-local pointers.

Root executes this only on its owned PostgreSQL. The positive prefix uses the
real public grant, report, worker, Event and Task creation journey. Corruptions
are negative integrity tests; no grant, Invocation or successful result is made
up, and the original reservation/creation/delivery Audits are never changed.
"""
import copy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Audit, Invocation, Job, Task
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_signal_graph_routing_v134 import (
    routing_scene, waiting_release, graph_seed, v7_scene, reading_scene,
    local_runtime, full_source_scene, enable, report, deliver, task_detail,
    pause, path, wire, reply,
)


@pytest.mark.parametrize('corruption', ['null_sql_pointer', 'delete_sql_row'])
def test_actual_created_audit_prevents_paused_task_origin_downgrade(routing_scene, corruption):
    from generic_agents.signal_graph_routing_models_v134 import SignalGraphDelivery
    from generic_agents.signal_graph_routing_v134 import task_state
    from uuid import uuid4

    value = routing_scene; client = value['client']
    _, grant_body, enabled = enable(value)
    _, _, _, signal_id = report(value)
    delivery = deliver(value, signal_id)
    assert delivery['status'] == 'created' and delivery['task_id']
    task = task_detail(value, delivery['task_id'])
    original_origin = copy.deepcopy(task['signal_graph_origin_v134'])
    pending = next(row for row in task['graph_pending_humans'] if row['node_id'] == 'a')
    human = {'request_id': str(uuid4()), 'activation_id': pending['activation_id'],
        'version': pending['version'], 'cancel_epoch': task['graph_runtime']['cancel_epoch'],
        'output': {'ok': True}}
    job_id = next(iter(task['graph_jobs'].values()))['job_id']
    pause(value, enabled)
    valid_paused = task_detail(value, task['id'])
    assert valid_paused['signal_graph_route_v134']['status'] == 'paused'
    me = reply(client.get('/api/me'))
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'read-only-origin-projection-v134')

    with Session(engine()) as db:
        row = db.get(Task, task['id']); stored = db.get(SignalGraphDelivery, delivery['id'])
        assert row.owner_id == stored.actor_id == value['actor_id']
        assert row.data['signal_graph_origin_v134'] == original_origin
        entries = list(db.scalars(select(Audit).where(Audit.workspace_id == value['workspace_id'],
            Audit.object_id == delivery['id'], Audit.action.in_([
                'signal_graph_routing.reserved', 'signal_graph_routing.created', 'signal_graph_routing.delivery']))))
        assert {entry.action for entry in entries} == {
            'signal_graph_routing.reserved', 'signal_graph_routing.created', 'signal_graph_routing.delivery'}
        created = next(entry for entry in entries if entry.action == 'signal_graph_routing.created')
        assert created.data['task_id'] == row.id
        original_audits = {entry.id: copy.deepcopy(entry.data) for entry in entries}
        data = copy.deepcopy(row.data); data.pop('signal_graph_origin_v134'); row.data = data
        if corruption == 'null_sql_pointer':
            stored.task_id = None
        else:
            db.delete(stored)
        calls = set(db.scalars(select(Invocation.id)))
        db.commit()

    # No current role, source version, Goal body or graph execution pin changed.
    # The independent original Audit alone must stop the apparent legacy path.
    # Run the actual worker first: the old vulnerable source must demonstrate
    # real dispatch, rather than fail only on a later read-status assertion.
    assert process_once(job_id, deliver=False)
    response = client.get('/api/workspace/tasks/' + task['id'])
    assert response.status_code == 409, response.text
    denied = client.post('/api/workspace/tasks/' + task['id'] + '/graph/complete', json=human)
    assert denied.status_code == 409, denied.text
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert not row.data['workflow_runtime']['receipts']
        assert not row.data.get('native_goal_acceptance')
        assert not any(activation['node_id'] == 'c' for activation in row.data['graph_runtime']['activations'].values())
        assert db.get(Job, job_id).status in {'failed', 'blocked', 'cancelled'}
        assert set(db.scalars(select(Invocation.id))) == calls
        assert {identity: db.get(Audit, identity).data for identity in original_audits} == original_audits
        projection = task_state(db, p, row)
        assert projection is not None and projection['status'] == 'unavailable'
        assert projection['can_submit_new_result'] is False and projection['can_retry_ability'] is False
    # Original command lookup still returns the original actor-bound grant; a
    # damaged Task can never be treated as a fresh enable or a new request.
    recovered = client.get(path(value) + '/requests/' + grant_body['request_id'])
    if corruption == 'null_sql_pointer':
        assert wire(recovered)['result']['receipt'] == enabled['receipt']
    else:
        # Deleting the SQL quota row also corrupts the independent grant
        # inventory. Fail closed instead of inventing a replacement receipt.
        assert recovered.status_code == 409, recovered.text
