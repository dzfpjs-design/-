"""Actual pure graph/queue checks with explicit Task/publication/IAM IO spies.

No real PG, lease, SDK, reception or Goal success is asserted. The normal exact
worker remains the executor; these tests cover scoped discovery and readiness.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m, native_goals, services, workspace_api
from generic_agents import workflow_graph as graph
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import goal_evaluation_provider_v288 as provider
from generic_agents import workflow_task_consumer_v265 as subject
from generic_agents.contracts import digest
from tests.test_full_semantic_graph_v1 import base, human, control, edge


def uid(number): return '28800000-0000-4000-8000-' + format(number, '012x')
def sql(statement): return str(statement.compile(compile_kwargs={'literal_binds': True}))


@pytest.fixture
def scope(monkeypatch):
    p = NS(user_id=uid(1), workspace_id=uid(2), role='operator')
    value = base([human('review'), control('end', 'end')], [edge('review', 'end')], 'review', ['review'])
    state = kernel.new_run(value, {}, 0)
    context = {'instance_id': uid(3), 'release_id': uid(4), 'business_id': 'business',
        'entity_ids': [uid(5)], 'goal_snapshot': {'synthetic_boundary': True}}
    task = NS(id=uid(6), workspace_id=p.workspace_id, package_id=uid(7), owner_id=uid(8),
        data={'instance_id': uid(3), 'instance_release_id': uid(4), 'business_id': 'business',
            'graph_runtime': state, 'native_goal_context': copy.deepcopy(context),
            'native_goal_context_hash': digest(context)})
    release = NS(id=uid(4), instance_id=uid(3), package_id=task.package_id)
    business = {'id': 'business', 'execution_mode': 'confirmed_graph_workflow'}
    job = NS(id=uid(9), workspace_id=p.workspace_id, kind=subject.NATIVE_GOAL_KIND, status='queued',
        idempotency_key='native-goal:' + task.id + ':' + digest(context) + ':definition',
        data={'task_id': task.id, 'actor_id': p.user_id, 'context_hash': digest(context)})
    from sqlalchemy import select
    choices = {sql(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == job.id)): job,
        sql(select(m.Task).where(m.Task.workspace_id == p.workspace_id, m.Task.id == task.id)): task}
    class ReadOnlyIO:
        def __init__(self): self.reads = []
        def scalar(self, statement):
            self.reads.append(sql(statement)); return choices[sql(statement)]
        def commit(self): pytest.fail('selection cannot commit or create a Goal')
        def add(self, _row): pytest.fail('selection cannot manufacture authority')
    db = ReadOnlyIO(); proofs = []
    def created(_db, workspace, instance, actual, family):
        assert (_db, workspace, instance, actual, family) == (db, p.workspace_id, release.instance_id, task, 'graph')
        proofs.append('created'); return release, business
    monkeypatch.setattr(subject, '_created', created)
    monkeypatch.setattr(subject, '_principal', lambda *_a: p)
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda _db, reader, identity:
        (task, release, business) if (_db, reader, identity) == (db, p, task.id) else pytest.fail('scope mismatch'))
    monkeypatch.setattr(graph, 'graph_for', lambda actual_release, actual_business:
        copy.deepcopy(value) if (actual_release, actual_business) == (release, business) else pytest.fail('graph scope mismatch'))
    monkeypatch.setattr(native_goals, 'saved_context', lambda _db, reader, actual:
        copy.deepcopy(context) if (_db, reader, actual) == (db, p, task) else pytest.fail('Goal context scope mismatch'))
    def published(_db, reader, identity, kind):
        assert (_db, reader, identity, kind) == (db, p, task.package_id, 'domain_package')
        proofs.append('published')
    monkeypatch.setattr(services, 'published', published)
    return NS(p=p, task=task, release=release, business=business, job=job, db=db, proofs=proofs)


def test_initial_goal_job_is_a_scoped_native_queue_not_a_graph_activation(scope):
    s = scope; before = copy.deepcopy((s.task.data, s.job.data))
    assert subject.NATIVE_GOAL_KIND not in subject.KINDS['compact']
    assert subject.NATIVE_GOAL_KIND in subject.KINDS['graph']
    assert 'activation_id' not in s.job.data and 'graph_hash' not in s.job.data
    assert subject._prove(s.db, s.p.workspace_id, s.release.instance_id, 'graph', s.job.id, s.task.id) == (
        s.p, s.task, s.release, s.business, s.job)
    assert s.proofs == ['created', 'published']
    assert (s.task.data, s.job.data) == before


@pytest.mark.parametrize('damage', ['context', 'key', 'actor', 'failed'])
def test_goal_marker_or_other_actor_cannot_replace_original_queue_authority(scope, damage):
    s = scope
    if damage == 'context': s.job.data['context_hash'] = '0' * 64
    elif damage == 'key': s.job.idempotency_key = 'graph:' + s.task.id + ':invented:1'
    elif damage == 'actor': s.job.data['actor_id'] = uid(99)
    else: s.job.status = 'failed'
    with pytest.raises(HTTPException): subject._prove(s.db, s.p.workspace_id, s.release.instance_id,
        'graph', s.job.id, s.task.id)


def test_original_timer_goal_queue_uses_its_due_time_hash_instead_of_an_activation(scope):
    s = scope; due = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    trigger = {'schema': 'native-goal-time-v1', 'context_hash': s.task.data['native_goal_context_hash'], 'due_at': due.isoformat()}
    s.job.data.update(time_trigger=trigger, time_trigger_hash=digest(trigger))
    s.job.idempotency_key = 'native-goal-time:' + s.task.id + ':' + digest(trigger); s.job.available_at = due
    assert subject._prove(s.db, s.p.workspace_id, s.release.instance_id, 'graph', s.job.id)[-1] is s.job
    s.job.data['time_trigger_hash'] = '0' * 64
    with pytest.raises(HTTPException): subject._prove(s.db, s.p.workspace_id, s.release.instance_id, 'graph', s.job.id)


def test_goal_scope_does_not_bypass_the_published_graph_package(scope, monkeypatch):
    def unavailable(*_args): raise HTTPException(409, 'original publication revoked')
    monkeypatch.setattr(services, 'published', unavailable)
    s = scope
    with pytest.raises(HTTPException): subject._prove(s.db, s.p.workspace_id, s.release.instance_id, 'graph', s.job.id)


@pytest.mark.parametrize('ready,status', [(True, 'queued'), (False, 'queued'), (False, 'running')])
def test_evaluation_goal_readiness_waits_without_sdk_queries_or_attempt_mutation(monkeypatch, ready, status):
    task = NS(id=uid(6)); job = NS(kind=subject.NATIVE_GOAL_KIND, status=status,
        attempts=0, data={provider.JOB_REF: {'id': uid(10), 'record_hash': 'a' * 64}})
    seen = []
    monkeypatch.setattr(provider, 'job_ready', lambda db, p, actual: seen.append(actual) or ready)
    class NoSQL:
        def scalar(self, _statement): pytest.fail('readiness must use the original Goal producer checker')
        def scalars(self, _statement): pytest.fail('a Goal Job has no graph SDK invocation')
    result = subject.readiness(NoSQL(), None, task, job)
    assert result == ({'state': 'ready', 'reason': None} if ready else
        {'state': 'waiting_producer', 'reason': subject.PRODUCER_WAIT})
    assert seen == [job] and job.attempts == 0 and job.status == status


def test_goal_discovery_sql_keeps_instance_task_and_status_boundaries():
    class Page:
        def __init__(self): self.queries = []
        def execute(self, statement): self.queries.append(sql(statement)); return []
    db = Page()
    assert subject._page(db, uid(2), uid(3), 'graph', uid(6), None, 20) == []
    assert len(db.queries) == 1
    query = db.queries[0]
    assert all(value in query for value in (uid(2), uid(3), uid(6), subject.NATIVE_GOAL_KIND))
    assert "'failed'" not in query and "'unknown'" not in query and 'LIMIT 20' in query
