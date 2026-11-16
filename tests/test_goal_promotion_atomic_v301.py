"""Internal terminal tail with explicit SQL/IAM/fence IO adapters.

These are not PG, publication-authority, Goal, source or real-worker tests.
Actual commit atomicity and the worker skip path have a separate PG file.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import worker_goal_promotion_atomic_v301 as atomic
from generic_agents.contracts import digest
from generic_agents.models import Job
from generic_agents.security import Principal


AT = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)


@pytest.fixture
def scene(monkeypatch):
    p = Principal(str(uuid4()), str(uuid4()), 'admin', 'worker')
    identity, goal, task = str(uuid4()), str(uuid4()), str(uuid4())
    payload = {'schema': atomic.SCHEMA, 'workspace_id': p.workspace_id,
        'task_id': task, 'actor_id': p.user_id, 'goal_check_job_id': goal,
        'evaluation_ref': {'id': str(uuid4()), 'record_hash': 'a' * 64},
        'prepared_audit_id': str(uuid4()), 'prepared_hash': 'b' * 64,
        'memory_id': str(uuid4()), 'report_hash': 'c' * 64,
        'promoter_job_id': identity, 'invocation_key': 'goal-evaluation-maturity:' + goal}
    payload['record_hash'] = digest(payload)
    job = Job(id=identity, workspace_id=p.workspace_id, kind=atomic.KIND,
        status='running', attempts=2, version=7, lease_token=str(uuid4()),
        lease_until=AT + timedelta(minutes=3), idempotency_key=payload['invocation_key'],
        data={**payload, 'lease_recoveries': 1, 'recovery_only': False,
            'historical_error': 'retained public fixture'})
    state = NS(p=p, job=job, payload=copy.deepcopy(payload), events=[], inside=False,
        owns=True, allowed=True, persistent=True, actual=True,
        row=NS(job_id=job.id, at=AT), owner={'job_id': job.id, 'lease_token': job.lease_token})
    class IO:
        info = {'worker_actor': p}
        @property
        @contextmanager
        def no_autoflush(self):
            assert not state.inside
            state.inside = True
            try: yield
            finally: state.inside = False
        def execute(self, statement):
            assert state.inside and job.status == 'running'
            sql = str(statement)
            params = statement.compile().params.values()
            assert job.id in params and p.workspace_id in params and p.user_id in params
            assert job.lease_token in params and atomic.KIND in params and atomic.SCHEMA in params
            assert 'clock_timestamp()' in sql and 'jobs.lease_until >' in sql
            assert 'jobs.attempts =' in sql and 'jobs.idempotency_key =' in sql
            assert 'FOR UPDATE' not in sql and 'UPDATE jobs' not in sql
            state.events.append('actual-running-row')
            return NS(one_or_none=lambda: state.row if state.actual else None)
        def flush(self): pytest.fail('tail must leave all ORM changes unflushed')
        def commit(self): pytest.fail('only caller may commit publication and success')
        def get(self, *args): pytest.fail('tail cannot refresh the original Job')
        def refresh(self, *args): pytest.fail('tail cannot overwrite staged terminal values')
    state.db = IO()
    monkeypatch.setattr(atomic, 'inspect', lambda row: NS(persistent=state.persistent,
        deleted=False, session=state.db))
    monkeypatch.setattr(atomic, 'fenced', lambda db: state.owns)
    monkeypatch.setattr(atomic, 'ownership', lambda db: copy.deepcopy(state.owner))
    def current_actor(db, principal):
        assert db is state.db and principal is p and state.inside and job.status == 'running'
        state.events.append('current-IAM-explicit-spy')
        if not state.allowed: raise HTTPException(403, 'explicit current IAM denial')
    monkeypatch.setattr(atomic, 'lock_current_actor', current_actor)
    return state


def test_stages_only_original_terminal_fields_after_all_reads(scene):
    s = scene; old = copy.deepcopy(s.job.data)
    original = (s.job.id, s.job.workspace_id, s.job.kind, s.job.version,
        s.job.attempts, s.job.lease_token, s.job.idempotency_key)
    assert atomic.stage_success(s.db, s.p, s.job) is None
    assert s.events == ['current-IAM-explicit-spy', 'actual-running-row']
    assert s.job.status == 'succeeded' and s.job.lease_until is None
    assert s.job.data == {**old, 'finished_at': AT.isoformat()}
    assert (s.job.id, s.job.workspace_id, s.job.kind, s.job.version,
        s.job.attempts, s.job.lease_token, s.job.idempotency_key) == original
    assert not s.inside


@pytest.mark.parametrize('fault', ['wrong-kind', 'wrong-schema', 'wrong-actor',
    'other-workspace', 'other-job', 'terminal', 'bad-payload-hash', 'unattached',
    'missing-fence', 'other-token', 'other-worker-actor', 'missing-real-row',
    'naive-db-clock', 'viewer'])
def test_invalid_identity_or_live_ownership_never_stages_partial_success(scene, fault):
    s = scene
    if fault == 'wrong-kind': s.job.kind = 'check_native_business_goal'
    elif fault == 'wrong-schema': s.job.data = {**s.job.data, 'schema': 'other'}
    elif fault == 'wrong-actor': s.job.data = {**s.job.data, 'actor_id': str(uuid4())}
    elif fault == 'other-workspace': s.job.workspace_id = str(uuid4())
    elif fault == 'other-job': s.owner['job_id'] = str(uuid4())
    elif fault == 'terminal': s.job.status = 'succeeded'
    elif fault == 'bad-payload-hash': s.job.data = {**s.job.data, 'report_hash': 'd' * 64}
    elif fault == 'unattached': s.persistent = False
    elif fault == 'missing-fence': s.owns = False
    elif fault == 'other-token': s.owner['lease_token'] = str(uuid4())
    elif fault == 'other-worker-actor': s.db.info = {'worker_actor': Principal(s.p.user_id, s.p.workspace_id, 'operator', 'worker')}
    elif fault == 'missing-real-row': s.actual = False
    elif fault == 'naive-db-clock': s.row.at = AT.replace(tzinfo=None)
    elif fault == 'viewer': s.p = Principal(s.p.user_id, s.p.workspace_id, 'viewer', 'worker')
    before = (s.job.status, s.job.lease_until, copy.deepcopy(s.job.data), s.job.version)
    with pytest.raises(HTTPException) as error:
        atomic.stage_success(s.db, s.p, s.job)
    assert error.value.status_code == 409
    assert (s.job.status, s.job.lease_until, s.job.data, s.job.version) == before


def test_actual_current_iam_denial_propagates_without_terminal_mutation(scene):
    s = scene; s.allowed = False; before = copy.deepcopy(s.job.data)
    with pytest.raises(HTTPException) as error:
        atomic.stage_success(s.db, s.p, s.job)
    assert error.value.status_code == 403
    assert s.events == ['current-IAM-explicit-spy']
    assert s.job.status == 'running' and s.job.lease_until is not None and s.job.data == before


def test_cannot_stage_twice_or_use_stored_completion_marker_as_authority(scene):
    s = scene
    s.job.data = {**s.job.data, 'atomic_promotion_completed': True}
    atomic.stage_success(s.db, s.p, s.job)
    before = copy.deepcopy(s.job.data)
    with pytest.raises(HTTPException): atomic.stage_success(s.db, s.p, s.job)
    assert s.job.data == before and s.events.count('actual-running-row') == 1
