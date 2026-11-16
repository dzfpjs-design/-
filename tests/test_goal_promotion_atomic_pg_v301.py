"""Actual PG commit/fence and worker-tail wiring, with minimal storage parents.

The head and publication Audits below are explicitly synthetic transaction
payloads. They do NOT authenticate a Goal, Qe, candidate, MATURED business proof,
source, SDK or real assessment. Only the worker integration case replaces
process_promotion with this declared IO stub; attach, current IAM, actual SQL,
Job claim, stage_success, real before_commit and PG commits are unmocked.
"""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from generic_agents import leases
from generic_agents import worker_goal_promotion_atomic_v301 as atomic
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, Job, Membership, Task, User
from generic_agents.security import Principal


SELECTED = 'native_goal.evaluation_selected_v288'
MATURED = 'goal_evaluation.matured_v299'
HEAD = 'current_goal_evaluation_v288'


@pytest.fixture(autouse=True)
def disposable_pg_only(disposable_database):
    assert engine().dialect.name == 'postgresql'
    assert engine().url.database.startswith('ga_test_')


@pytest.fixture
def scene(account):
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'worker')
        package = Capability(workspace_id=p.workspace_id, namespace='atomic_transaction_fixture',
            name='package', release_version='1.0.0', kind='domain_package', status='draft',
            content_hash=digest({'fixture': 'FK parent only'}), data={'fixture': 'FK parent only'})
        db.add(package); db.flush()
        old = {'generation': 1, 'head_hash': 'a' * 64, 'fixture': 'transaction payload only'}
        new = {'generation': 2, 'head_hash': 'b' * 64, 'fixture': 'transaction payload only'}
        task = Task(workspace_id=p.workspace_id, title='发布事务原子性验证', owner_id=p.user_id,
            package_id=package.id, status='workflow_active', data={HEAD: old}, version=4)
        goal = Job(workspace_id=p.workspace_id, kind='check_native_business_goal', status='succeeded',
            idempotency_key='atomic-fixture-goal:' + str(uuid4()), attempts=1,
            data={'actor_id': p.user_id, 'fixture': 'terminal parent storage only'})
        db.add_all([task, goal]); db.flush()
        identity = str(uuid4())
        payload = {'schema': atomic.SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
            'actor_id': p.user_id, 'goal_check_job_id': goal.id,
            'evaluation_ref': {'id': str(uuid4()), 'record_hash': 'c' * 64},
            'prepared_audit_id': str(uuid4()), 'prepared_hash': 'd' * 64,
            'memory_id': str(uuid4()), 'report_hash': 'e' * 64,
            'promoter_job_id': identity, 'invocation_key': 'goal-evaluation-maturity:' + goal.id}
        payload['record_hash'] = digest(payload)
        at = db.scalar(select(func.clock_timestamp()))
        job = Job(id=identity, workspace_id=p.workspace_id, kind=atomic.KIND, status='running',
            idempotency_key=payload['invocation_key'], data=payload, attempts=1, version=6,
            lease_token=str(uuid4()), lease_until=at + timedelta(minutes=3))
        db.add(job); db.commit()
        return NS(p=p, job_id=job.id, task_id=task.id, goal_id=goal.id, token=job.lease_token,
            old=old, new=new, version=job.version)


@contextmanager
def publication_transaction(s):
    with Session(engine(), expire_on_commit=False) as db:
        with leases.local_goal_phase(db, s.job_id, s.token) as phase:
            leases.attach(db, s.job_id, s.token)
            db.info['worker_actor'] = s.p
            assert db.get(Job, s.job_id).status == 'running'
            db.commit()  # The ordinary original recovery transaction.
            phase.activate()
            # Same ordering as the actual promoter, not own-Job-first final IO.
            task = db.scalar(select(Task).where(Task.id == s.task_id).with_for_update())
            db.scalar(select(Job).where(Job.id == s.goal_id).with_for_update())
            job = db.scalar(select(Job).where(Job.id == s.job_id).with_for_update())
            yield db, task, job


def synthetic_publication(db, s, task):
    """Declared transaction fixture; deliberately not valid Goal proof."""
    task.data = {**task.data, HEAD: s.new}; task.version += 1
    db.add(Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
        action=SELECTED, object_id=task.id, data=s.new))
    db.add(Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
        action=MATURED, object_id=task.id, data={'promoter_job_id': s.job_id,
            'goal_check_job_id': s.goal_id, 'fixture': 'transaction-only; not publication authority'}))


def durable(s):
    with Session(engine()) as db:
        task, job = db.get(Task, s.task_id), db.get(Job, s.job_id)
        count = db.scalar(select(func.count()).select_from(Audit).where(
            Audit.workspace_id == s.p.workspace_id, Audit.object_id == s.task_id,
            Audit.action.in_([MATURED, SELECTED])))
        return {'head': task.data[HEAD], 'task_version': task.version, 'status': job.status,
            'lease': job.lease_until, 'token': job.lease_token, 'version': job.version,
            'attempts': job.attempts, 'data': job.data, 'audits': count}


def assert_unpublished(s):
    value = durable(s)
    assert value['head'] == s.old and value['task_version'] == 4
    assert value['status'] == 'running' and value['lease'] is not None
    assert value['token'] == s.token and value['version'] == s.version and value['attempts'] == 1
    assert 'finished_at' not in value['data'] and value['audits'] == 0


def test_staged_terminal_and_head_are_invisible_and_rollback_together(scene):
    s = scene
    with publication_transaction(s) as (db, task, job):
        synthetic_publication(db, s, task)
        atomic.stage_success(db, s.p, job)
        assert job.status == 'succeeded' and task.data[HEAD] == s.new
        assert_unpublished(s)  # Independent real connection sees old durable state.
        db.rollback()
    assert_unpublished(s)


def test_actual_before_commit_sees_running_then_pg_publishes_head_and_success_atomically(scene):
    s = scene; witnesses = []
    with publication_transaction(s) as (db, task, job):
        synthetic_publication(db, s, task)
        atomic.stage_success(db, s.p, job)
        def original_running_row(session):
            # Core connection avoids autoflush of the staged successful ORM Job.
            actual = session.connection().execute(select(Job.status, Job.lease_token).where(Job.id == s.job_id)).one()
            witnesses.append(tuple(actual))
        event.listen(db, 'before_commit', original_running_row)
        db.commit()
    assert witnesses == [('running', s.token)]
    value = durable(s)
    assert value['head'] == s.new and value['task_version'] == 5
    assert value['status'] == 'succeeded' and value['lease'] is None and value['audits'] == 2
    assert value['token'] == s.token and value['version'] == s.version and value['attempts'] == 1
    assert value['data']['finished_at']


def test_actual_expired_row_rejects_staging_and_rolls_back_pending_publication(scene):
    s = scene
    with publication_transaction(s) as (db, task, job):
        synthetic_publication(db, s, task)
        # Same-transaction fault injection; no bypass of the helper's real SQL.
        db.connection().execute(update(Job).where(Job.id == s.job_id).values(
            lease_until=func.clock_timestamp() - timedelta(seconds=1)))
        with pytest.raises(HTTPException) as error: atomic.stage_success(db, s.p, job)
        assert error.value.status_code == 409
        assert job.status == 'running' and 'finished_at' not in job.data
        db.rollback()
    assert_unpublished(s)


@pytest.mark.parametrize('fault', ['expired', 'taken-over', 'revoked'])
def test_existing_commit_fence_rejects_fault_after_staging_without_partial_publication(scene, fault):
    s = scene
    with publication_transaction(s) as (db, task, job):
        synthetic_publication(db, s, task); atomic.stage_success(db, s.p, job)
        # Explicit fault injection into the original connection does not
        # autoflush the dirty Job. Production must go straight to commit.
        connection = db.connection()
        if fault == 'expired':
            connection.execute(update(Job).where(Job.id == s.job_id).values(
                lease_until=func.clock_timestamp() - timedelta(seconds=1)))
        elif fault == 'taken-over':
            connection.execute(update(Job).where(Job.id == s.job_id).values(lease_token=str(uuid4())))
        else:
            connection.execute(update(Membership).where(Membership.workspace_id == s.p.workspace_id,
                Membership.user_id == s.p.user_id).values(role='viewer'))
        expected = leases.ExecutionUnauthorized if fault == 'revoked' else leases.LeaseLost
        with pytest.raises(expected): db.commit()
        db.rollback()
    assert_unpublished(s)


def test_real_worker_commits_once_and_never_enters_second_terminal_session(scene, monkeypatch):
    """Real worker/lease/SQL, declared publication-handler IO stub only."""
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    from generic_agents import worker
    s = scene; attached = []
    with engine().begin() as connection:
        connection.execute(update(Job).where(Job.id == s.job_id).values(
            status='queued', attempts=0, lease_token=None, lease_until=None))
    original_attach = leases.attach
    def counted_attach(db, job_id, token):
        attached.append((id(db), job_id, token))
        return original_attach(db, job_id, token)
    monkeypatch.setattr(leases, 'attach', counted_attach)
    def actual_handler_boundary(db, p, job):
        assert p == s.p and job.id == s.job_id and job.status == 'running'
        task = db.scalar(select(Task).where(Task.id == s.task_id).with_for_update())
        db.scalar(select(Job).where(Job.id == s.goal_id).with_for_update())
        assert db.scalar(select(Job.id).where(Job.id == s.job_id).with_for_update()) == job.id
        synthetic_publication(db, s, task)
        return 'succeeded'
    monkeypatch.setattr(maturity, 'process_promotion', actual_handler_boundary)
    assert worker.process_once(s.job_id, deliver=False) is True
    value = durable(s)
    assert value['head'] == s.new and value['status'] == 'succeeded' and value['lease'] is None
    assert value['audits'] == 2 and value['attempts'] == 1
    assert len(attached) == 1 and attached[0][1] == s.job_id
    # A process exit after the atomic commit leaves no reclaimable original Job
    # and cannot create another publication or a second terminal transaction.
    assert worker.process_once(s.job_id, deliver=False) is False
    assert durable(s) == value and len(attached) == 1
