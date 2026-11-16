"""An allocated last attempt can still execute its original dispatch."""
import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker
from generic_agents.db import engine
from tests.test_sensing_selection import asset, current, monitor, scan, setup, supplier


def _stock(workspace):
    with Session(engine()) as db:
        jobs = {j.id: (j.status, j.attempts, j.idempotency_key, copy.deepcopy(j.data))
            for j in db.scalars(select(m.Job).where(m.Job.workspace_id == workspace,
                m.Job.kind == 'dispatch_acquisition'))}
        acquisitions = {a.id: (a.status, a.idempotency_key, copy.deepcopy(a.data))
            for a in db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id == workspace))}
        return jobs, acquisitions


def test_refresh_keeps_original_authorized_last_attempt_then_escalates_after_terminal_rejection(
        client, supplier):
    entity, source = setup(client, supplier, actions=['read_existing'])
    first = asset(client, entity, source, actions=['read_existing'], latency_seconds=10)
    alternate = asset(client, entity, source, actions=['read_existing'], latency_seconds=20)
    supplier['modes'][first['external_id']] = 'rejected'
    policy = monitor(client, entity, source, [first, alternate],
        allowed_actions=['read_existing'], max_attempts=1)
    workspace = policy['workspace_id']
    identity, = scan(client, policy)
    original_plan = current(client)
    assert original_plan['status'] == 'awaiting_dispatch'
    before = _stock(workspace)
    dispatch, = before[0]
    assert before[0][dispatch][:3] == ('queued', 0, 'dispatch:' + identity)
    assert before[1][identity][0] == 'authorized'
    with Session(engine()) as db:
        approved = copy.deepcopy(db.get(m.MonitorPolicy, policy['id']).data)

    assert scan(client, policy) == []
    unchanged = current(client)
    assert unchanged['id'] == original_plan['id']
    assert unchanged['status'] == 'awaiting_dispatch' and not unchanged['data'].get('escalation_code')
    assert unchanged['data']['current_acquisition_id'] == identity
    assert unchanged['data']['budget']['daily_count'] == 1
    assert _stock(workspace) == before and supplier['calls'] == []

    assert worker.process_once(dispatch, kinds=['dispatch_acquisition'], workspace_id=workspace, deliver=False)
    terminal = current(client)
    assert terminal['id'] == original_plan['id'] and terminal['status'] == 'escalated'
    assert terminal['data']['escalation_code'] == 'attempts'
    after = _stock(workspace)
    assert set(after[0]) == {dispatch} and set(after[1]) == {identity}
    assert after[0][dispatch][:3] == ('failed', 1, 'dispatch:' + identity)
    assert after[1][identity][0] == 'rejected'
    assert after[1][identity][1] == before[1][identity][1]
    assert len(supplier['calls']) == 1 and supplier['calls'][0]['request_key'] == before[1][identity][1]
    assert scan(client, policy) == [] and _stock(workspace) == after
    assert len(supplier['calls']) == 1
    with Session(engine()) as db:
        assert db.get(m.MonitorPolicy, policy['id']).data == approved
