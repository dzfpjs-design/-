"""Scoped same-Job wakeup with real wait ledger/kernel checks and IO seams.

No PG authority, worker lease, SDK, cloud or acquisition is proved here. The
original producer and consumer remain distinct; polling never grants dispatch.
"""
import copy
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, db as database, worker, workspace_api
from generic_agents import workflow_task_consumer_v265 as consumer
from generic_agents import workflow_provenance_wait_v197 as waits
from tests.test_workflow_provenance_wait_v197 import scene as wait_scene, AT
from tests.test_workflow_task_context_v196 import identity, same_sql


WORKSPACE, INSTANCE, TASK, JOB = [identity('scoped-wake-' + part)
    for part in ('workspace', 'instance', 'task', 'job')]


@pytest.mark.parametrize('workspace,job', [(None, JOB), (WORKSPACE, 'invalid'),
    (WORKSPACE, JOB.upper()), (WORKSPACE, True)])
def test_exact_poll_rejects_missing_or_malformed_scope_before_session(monkeypatch, workspace, job):
    def forbidden(*a, **kw): pytest.fail('invalid targeted poll opened IO')
    monkeypatch.setattr(database, 'engine', forbidden)
    with pytest.raises((HTTPException, ValueError)):
        waits.poll_once(workspace, job_id=job)


@pytest.mark.parametrize('workspace,target', [(None, None), (WORKSPACE, None), (WORKSPACE, JOB)])
def test_poll_retains_old_selector_and_adds_exact_workspace_job_predicate(monkeypatch, workspace, target):
    queries, commits = [], []

    class EmptySession:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalars(self, query): queries.append(query); return []
        def commit(self): commits.append(True)

    import sqlalchemy.orm
    monkeypatch.setattr(database, 'engine', lambda: object())
    monkeypatch.setattr(sqlalchemy.orm, 'Session', lambda *a: EmptySession())
    assert waits.poll_once(workspace, job_id=target) == 0 and commits == [True]
    expected = select(m.Job).where(m.Job.kind == waits.scope.KIND, m.Job.status == waits.PHASE,
        m.Job.available_at <= func.clock_timestamp()).order_by(m.Job.available_at, m.Job.id).limit(64)
    if workspace is not None: expected = expected.where(m.Job.workspace_id == workspace)
    if target is not None: expected = expected.where(m.Job.id == target)
    assert len(queries) == 1 and same_sql(queries[0], expected)


def test_consumer_page_selects_provenance_only_for_graph_nodes_inside_original_scope():
    queries = []

    class Page:
        def execute(self, statement): queries.append(statement); return []

    assert consumer._page(Page(), WORKSPACE, INSTANCE, 'graph', TASK, None, 20) == []
    text = str(queries[0].compile(compile_kwargs={'literal_binds': True}))
    assert all(value in text for value in (WORKSPACE, INSTANCE, TASK, waits.PHASE, waits.scope.KIND))
    assert 'LIMIT 20' in text and "'failed'" not in text and "'unknown'" not in text
    assert "jobs.kind = 'execute_full_semantic_graph_node' AND jobs.status = 'waiting_provenance'" in text


@pytest.fixture
def scoped(monkeypatch):
    # Explicit source/IAM/publication boundaries come from the original pure
    # wait fixture. Its real graph kernel, identity and wait audit proofs run.
    s = wait_scene.__wrapped__(monkeypatch)
    assert s.wait() is True
    s.business['execution_mode'] = 'confirmed_graph_workflow'
    s.task.status = 'workflow_waiting'
    actions = []
    original_scalars = s.db.scalars
    exact = select(m.Job).where(m.Job.kind == waits.scope.KIND, m.Job.status == waits.PHASE,
        m.Job.available_at <= func.clock_timestamp()).order_by(m.Job.available_at, m.Job.id).limit(64)
    exact = exact.where(m.Job.workspace_id == s.p.workspace_id, m.Job.id == s.job.id)

    def wait_scalars(statement):
        if same_sql(statement, exact):
            return [s.job] if s.job.status == waits.PHASE else []
        return original_scalars(statement)

    monkeypatch.setattr(s.db, 'scalars', wait_scalars)

    class ReadSession:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalar(self, statement):
            for expected, row in [
                (select(m.DomainInstance).where(m.DomainInstance.workspace_id == s.p.workspace_id,
                    m.DomainInstance.id == s.release.instance_id), NS(id=s.release.instance_id)),
                (select(m.Job).where(m.Job.workspace_id == s.p.workspace_id, m.Job.id == s.job.id), s.job),
                (select(m.Task).where(m.Task.workspace_id == s.p.workspace_id, m.Task.id == s.task.id), s.task),
            ]:
                if same_sql(statement, expected): return row
            # Only the original pending-producer scheduling check is unused
            # once V197 independently proved and woke the same original Job.
            assert statement.column_descriptions[0]['entity'] is m.Job
            assert statement.column_descriptions[0]['name'] == 'id'
            return None
        def scalars(self, statement):
            assert statement.column_descriptions[0]['entity'] is m.Invocation
            return []

    read = ReadSession()
    monkeypatch.setattr(consumer, 'engine', lambda: object())
    monkeypatch.setattr(consumer, 'Session', lambda *_: read)

    @contextmanager
    def reader(): yield read

    monkeypatch.setattr(consumer, '_reader', reader)
    monkeypatch.setattr(consumer, '_delivery_page', lambda *a: [])

    def page(_db, workspace, instance, family, task_id, after, limit):
        assert (workspace, instance, family, task_id) == (s.p.workspace_id, s.release.instance_id, 'graph', s.task.id)
        return [(s.job.id, AT)]

    monkeypatch.setattr(consumer, '_page', page)

    def created(_db, workspace, instance, task, family):
        assert _db is read and workspace == s.p.workspace_id and instance == s.release.instance_id
        assert task is s.task and family in {None, 'graph'}
        return s.release, s.business

    monkeypatch.setattr(consumer, '_created', created)
    monkeypatch.setattr(consumer, '_principal', lambda *a: s.p)

    def saved(_db, p, task_id):
        assert _db is read and p is s.p and task_id == s.task.id
        return s.task, s.release, s.business

    monkeypatch.setattr(workspace_api, 'saved_execution', saved)
    monkeypatch.setattr(consumer, '_receipt_cap', lambda *a: None)
    original_prove = consumer._prove

    def prove(*args, **kwargs):
        result = original_prove(*args, **kwargs)
        actions.append(('proved', s.job.status)); return result

    monkeypatch.setattr(consumer, '_prove', prove)

    def poll(workspace, *, job_id):
        assert workspace == s.p.workspace_id and job_id == s.job.id
        actions.append(('exact poll', job_id))
        return waits.schedule_once(s.db, workspace_id=workspace, job_id=job_id)

    monkeypatch.setattr(waits, 'poll_once', poll)

    def process(**kwargs):
        assert kwargs == {'job_id': s.job.id, 'kinds': list(consumer.KINDS['graph']),
            'deliver': False, 'workspace_id': s.p.workspace_id, '_stop_event': None}
        assert s.job.status == 'queued' and s.job.data[waits.KEY]['active_hash'] is None
        actions.append(('exact worker', s.job.id)); return True

    monkeypatch.setattr(worker, 'process_once', process)
    monkeypatch.setattr(consumer, 'drain_task', lambda *a, **kw: 0)
    s.actions = actions
    s.tick = lambda: consumer.step(s.p.workspace_id, s.release.instance_id,
        family='graph', task_id=s.task.id)
    return s


def test_waiting_original_producer_keeps_same_job_attempts_and_never_calls_worker(scoped):
    s = scoped; before = copy.deepcopy((s.job.data, s.task.data)); attempts = s.job.attempts
    result = s.tick()
    assert not result['processed'] and result['errors'] == []
    assert result['waiting'] == [{'job_id': s.job.id, 'state': 'waiting_producer', 'reason': consumer.PRODUCER_WAIT}]
    assert s.actions == [('proved', waits.PHASE), ('exact poll', s.job.id), ('proved', waits.PHASE)]
    assert (s.job.data, s.task.data) == before and s.job.attempts == attempts
    assert s.db.additions == [m.Audit]


def test_completed_original_producer_wakes_same_job_then_reproves_before_exact_worker(scoped):
    s = scoped; s.check.status = 'succeeded'; s.task.data['native_goal_query_id'] = s.run.id
    attempts, original_id, key = s.job.attempts, s.job.id, s.job.idempotency_key
    result = s.tick()
    assert result['processed'] and result['waiting'] == [] and result['errors'] == []
    assert s.actions == [('proved', waits.PHASE), ('exact poll', original_id),
        ('proved', 'queued'), ('exact worker', original_id)]
    assert s.job.id == original_id and s.job.idempotency_key == key and s.job.attempts == attempts
    assert [a.action for a in s.db.events] == [waits.WAIT, waits.WAKE]
    assert s.db.additions == [m.Audit, m.Audit]


def test_failed_dependency_is_retained_blocked_without_worker_retry(scoped):
    s = scoped; s.check.status = 'failed'; attempts = s.job.attempts
    result = s.tick()
    assert not result['processed'] and s.job.status == 'blocked' and s.check.status == 'failed'
    assert result['waiting'] == [{'job_id': s.job.id, 'state': 'blocked_integrity', 'reason': consumer.ERROR}]
    assert s.job.attempts == attempts and all(action[0] != 'exact worker' for action in s.actions)
    assert [a.action for a in s.db.events] == [waits.WAIT]


def test_resealed_wrong_activation_input_fails_real_scope_proof_before_poll(scoped):
    s = scoped; s.job.data = {**s.job.data, 'input_hash': '0' * 64}
    result = s.tick()
    assert not result['processed'] and result['errors'] == [{'job_id': s.job.id, 'reason': consumer.ERROR}]
    assert s.actions == [] and s.job.status == waits.PHASE
