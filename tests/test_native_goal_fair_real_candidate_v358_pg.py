"""Long isolated, simulated source path with real producer -> candidate.

Explicitly routes the append helper to fair_tick; the original helper defaults
 to old FIFO and is not itself evidence of fairness. This test is expensive.
"""
from threading import Event
from sqlalchemy import select
from generic_agents import models as m
from generic_agents import goal_evaluation_maturity_v299 as maturity
from generic_agents import native_goal_scoped_worker_v355 as scoped_worker
from generic_agents.native_goal_fair_worker_v358 import fair_tick
from tests.test_auto_evaluation_full_chain_v357 import (
    published_goal_data, managed, scene,
    test_real_published_six_port_task_with_simulated_scope_and_two_independent_approvals as real_chain,
)


def test_real_append_producer_candidate_pin_uses_fair_lane(published_goal_data, monkeypatch):
    from generic_agents.native_goal_time import timer
    original = maturity.queue_candidate
    observed = []
    fair_calls = []
    def capture(db, p, task, evaluation, producer):
        candidate = original(db, p, task, evaluation, producer)
        # Same transaction, after the real queue and its independently created audit.
        current = db.scalar(select(m.Task).where(m.Task.workspace_id == p.workspace_id, m.Task.id == task.id))
        pin = scoped_worker._pin(current)
        assert pin and pin['job_id'] == candidate.id
        candidate_ids = {j for j, _ in scoped_worker._rows(db,p.workspace_id,current,pin,timer(current),None,lane='candidate')}
        ordinary_ids = {j for j, _ in scoped_worker._rows(db,p.workspace_id,current,pin,timer(current),None,lane='ordinary')}
        assert candidate.id in candidate_ids and candidate.id not in ordinary_ids
        observed.append((producer.id,candidate.id))
        return candidate
    def use_fair(workspace, instance, task, stop=None, after=None):
        assert after is None
        result = fair_tick(workspace,instance,task,stop or Event())
        fair_calls.append(result)
        return {**result,'after':None}
    monkeypatch.setattr(maturity,'queue_candidate',capture)
    monkeypatch.setattr(scoped_worker,'tick',use_fair)
    real_chain(published_goal_data,monkeypatch)
    assert observed and fair_calls
