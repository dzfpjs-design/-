"""Deterministically exercise the legal reader-before-Goal claim order.

Real setup and two-level approval are shared with the weather integration.
The fixture interrupts only the test's first worker call. No Job, SDK result,
goal query, or device receipt is fabricated.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m, workflow_provenance_wait_v197 as provenance
from generic_agents.db import engine
from tests import test_nested_same_goal_resense_runtime_pg as runtime
from tests.test_nested_async_managed_source_pg import (
    confirmed_catalogue, enum_scene, fence, full_source_scene, local_runtime,
    managed, reading_scene, scene,
)


class BeforeInitialGoal(Exception):
    pass


def test_reader_first_waits_without_sdk_then_wakes_same_job_after_original_goal(
        managed, enum_scene, monkeypatch, account):
    first = {}

    def capture(w, identity, kind):
        assert kind == 'check_native_business_goal'
        with Session(engine()) as db:
            job = db.get(m.Job, identity)
            assert job.workspace_id == w and job.status == 'queued' and job.attempts == 0
            task = db.get(m.Task, job.data['task_id'])
            assert task.data['native_goal_query_id'] is None
            first.update(w=w, goal=identity, task=task.id)
        raise BeforeInitialGoal()

    with monkeypatch.context() as scheduling:
        scheduling.setattr(runtime, '_run', capture)
        with pytest.raises(BeforeInitialGoal):
            runtime.test_recursive_same_goal_resense_settles_original_http_and_data_ack(
                managed, enum_scene, monkeypatch, account)

    w, t, g = first['w'], first['task'], first['goal']
    original_reader_job = runtime._job(w, runtime.GRAPH, task_id=t)
    assert runtime._run(w, original_reader_job, runtime.GRAPH) == provenance.PHASE
    with Session(engine()) as db:
        goal_job = db.get(m.Job, g)
        assert goal_job.status == 'queued' and goal_job.attempts == 0
        reader_job = db.get(m.Job, original_reader_job)
        key = reader_job.data['invocation_key']
        ledger = reader_job.data[provenance.KEY]
        wait, = ledger['history']
        dependency, = wait['dependencies']
        assert dependency['kind'] == 'native_goal_check' and dependency['job_id'] == g
        assert ledger['active_hash'] == wait['record_hash']
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == w,
            m.Invocation.idempotency_key == key)) is None
        assert db.scalar(select(m.Acquisition.id).where(m.Acquisition.workspace_id == w)) is None
    assert provenance.poll_once(w, job_id=original_reader_job) == 0

    assert runtime._run(w, g, 'check_native_business_goal') == 'succeeded'
    assert provenance.poll_once(w, job_id=original_reader_job) == 1
    assert provenance.poll_once(w, job_id=original_reader_job) == 0
    assert runtime._run(w, original_reader_job, runtime.GRAPH) == 'succeeded'
    with Session(engine()) as db:
        task = db.get(m.Task, t)
        call, = db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == w,
            m.Invocation.idempotency_key == key))
        assert call.status == 'succeeded' and call.data['job_id'] == original_reader_job
        output = call.data['output']
        assert output['status'] == 'ready' and output['resense_context']['scope']['task_id'] == t
        assert output['resense_context']['query']['query_id'] == task.data['native_goal_query_id']
        ledger = db.get(m.Job, original_reader_job).data[provenance.KEY]
        assert ledger['history'] == [wait] and ledger['active_hash'] is None
        assert ledger['woken_hashes'] == [wait['record_hash']]
