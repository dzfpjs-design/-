"""Actual disposable-PG lease/heartbeat and provenance-write concurrency.

Use the normal account/conftest on Root's explicitly selected owned server;
this file neither chooses a URL nor loads env files. Source adoption in the
small provenance fixture is an explicit IO boundary. It proves lease and SQL
ordering, not an approved real business, provider, equipment or packet result.
"""
import copy
import threading
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import leases, worker
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, Invocation, Job, Membership, Task, User
from generic_agents.security import Principal
from tests.test_full_semantic_graph_v1 import base, control, edge, guards, object_schema
from tests.test_workflow_service_registration_v195 import owned_postgres_only


@pytest.fixture(autouse=True)
def disposable_only(owned_postgres_only):
    # No environment/default fallback is permitted. The ordinary conftest has
    # already selected its new ga_test_* database before any fixture creates IO.
    assert engine().url.database.startswith('ga_test_')


def original_job(account, *, kind=leases._GRAPH_HANDLER_KIND, queued=False):
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'worker')
        at = db.scalar(select(func.clock_timestamp()))
        job = Job(workspace_id=p.workspace_id, kind=kind, status='queued' if queued else 'running',
            attempts=0 if queued else 1, idempotency_key='graph-phase:' + uuid4().hex,
            available_at=at, lease_token=None if queued else str(uuid4()),
            lease_until=None if queued else at + timedelta(seconds=30),
            data={'actor_id': p.user_id, 'task_id': str(uuid4()), 'kind': leases._GRAPH_HANDLER_KIND})
        db.add(job); db.commit()
        return NS(p=p, job_id=job.id, token=job.lease_token)


@contextmanager
def recovered(s):
    with Session(engine(), expire_on_commit=False) as db:
        with leases.graph_handler_lease_phase(db, s.job_id, s.token) as phase:
            leases.attach(db, s.job_id, s.token)
            db.info['worker_actor'] = s.p
            # This first transaction deliberately retains the recovery lock.
            assert db.get(Job, s.job_id).status == 'running'
            db.commit()
            yield db, phase


def probe(db, s):
    row = Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
        action='test.graph_handler_lease_phase', object_id=s.job_id, data={'proposal_only': True})
    db.add(row); db.flush()
    return row.id


def test_long_graph_transaction_renews_past_original_deadline_and_commits_under_original_token(account, monkeypatch):
    s = original_job(account)
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', 0.05)
    with engine().begin() as connection:
        connection.execute(update(Job).where(Job.id == s.job_id).values(
            lease_until=func.clock_timestamp() + timedelta(seconds=2)))
    with recovered(s) as (db, phase):
        phase.activate()
        with leases.heartbeat(engine(), s.job_id, s.token):
            original_deadline = db.get(Job, s.job_id).lease_until
            db.execute(text('SELECT pg_sleep(2.4)'))
            with engine().connect() as connection:
                current_token, deadline, at = connection.execute(select(
                    Job.lease_token, Job.lease_until, func.clock_timestamp()).where(Job.id == s.job_id)).one()
            assert at > original_deadline and deadline > at and deadline > original_deadline
            assert current_token == s.token and worker.claim(s.job_id) is None
            audit_id = probe(db, s); db.commit()
        assert phase.active and not phase.closed
    assert phase.closed and not phase.active
    with Session(engine()) as db:
        assert db.get(Audit, audit_id) is not None
        assert db.get(Job, s.job_id).lease_token == s.token


@pytest.mark.parametrize('fault', ['expired', 'takeover', 'revoked_actor', 'demoted_actor'])
def test_final_commit_still_rolls_back_flushed_work_after_expiry_takeover_or_revocation(account, fault):
    s = original_job(account)
    expected = leases.ExecutionUnauthorized if fault in {'revoked_actor', 'demoted_actor'} else leases.LeaseLost
    with recovered(s) as (db, phase):
        phase.activate(); db.get(Job, s.job_id)
        audit_id = probe(db, s)
        with engine().begin() as connection:
            if fault == 'expired':
                connection.execute(update(Job).where(Job.id == s.job_id).values(
                    lease_until=func.clock_timestamp() - timedelta(seconds=1)))
            elif fault == 'takeover':
                connection.execute(update(Job).where(Job.id == s.job_id).values(lease_token=str(uuid4())))
            elif fault == 'revoked_actor':
                connection.execute(update(User).where(User.id == s.p.user_id).values(active=False))
            else:
                connection.execute(update(Membership).where(Membership.user_id == s.p.user_id,
                    Membership.workspace_id == s.p.workspace_id).values(role='viewer'))
        with pytest.raises(expected): db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Audit, audit_id) is None


@pytest.mark.parametrize('kind', ['check_native_business_goal', 'dispatch_acquisition', 'advance_full_semantic_graph'])
def test_phase_uses_actual_db_kind_not_retained_json_or_handler_claim(account, kind):
    s = original_job(account, kind=kind)
    with recovered(s) as (db, phase):
        phase.activate()
        with pytest.raises(leases.LeaseLost): db.execute(select(Job.id).where(Job.id == s.job_id))
        db.rollback()


def test_inactive_and_json_phases_retain_begin_lock_and_dispatch_can_still_lock_job(account):
    s = original_job(account)
    with recovered(s) as (db, phase):
        # Activation while the recovery transaction is open is forbidden.
        db.execute(select(Job.id).where(Job.id == s.job_id))
        with pytest.raises(ValueError): phase.activate()
        with pytest.raises(DBAPIError): leases.renew(engine(), s.job_id, s.token)
        db.commit()
        phase.activate()
        db.execute(select(Job.id).where(Job.id == s.job_id))
        assert leases.renew(engine(), s.job_id, s.token) is True
        # An explicit dispatch/final fence is still exclusive in this phase.
        leases._check(db.connection(), s.job_id, s.token)
        with pytest.raises(DBAPIError): leases.renew(engine(), s.job_id, s.token)
        db.commit()
    with Session(engine()) as db:
        leases.attach(db, s.job_id, s.token)
        db.info[leases._GRAPH_HANDLER_PHASE] = {'active': True}
        db.get(Job, s.job_id)
        with pytest.raises(DBAPIError): leases.renew(engine(), s.job_id, s.token)
        db.rollback()


def test_phase_cleanup_and_activation_require_exact_server_binding_and_committed_transaction(account):
    s = original_job(account)
    with recovered(s) as (db, phase):
        original = copy.deepcopy(db.info['worker_lease'])
        db.info['worker_lease']['lease_token'] = 'other-token'
        with pytest.raises(ValueError): phase.activate()
        db.info['worker_lease'] = original
        with pytest.raises(ValueError):
            with leases.graph_handler_lease_phase(db, s.job_id, s.token): pass
        phase.activate()
    assert phase.closed and leases._GRAPH_HANDLER_PHASE not in db.info
    with pytest.raises(ValueError): phase.activate()
    with pytest.raises(RuntimeError, match='handler failure'):
        with recovered(s) as (db, failed_phase):
            failed_phase.activate()
            db.execute(select(Job.id).where(Job.id == s.job_id))
            raise RuntimeError('handler failure')
    assert failed_phase.closed and leases._GRAPH_HANDLER_PHASE not in db.info
    assert leases.LEASE_SECONDS == 180 and leases.HEARTBEAT_SECONDS == 30


def test_real_worker_activates_only_after_recovery_commit_and_finalizes_same_job(account, monkeypatch):
    s = original_job(account, queued=True)
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', 0.05)
    seen = []
    from generic_agents import workflow_graph, worker_native_audio_v136
    def local_handler(db, p, job):
        phase = db.info[leases._GRAPH_HANDLER_PHASE]
        assert type(phase) is leases._GraphHandlerPhase and phase.active and not phase.closed
        assert p.user_id == s.p.user_id and job.id == s.job_id
        seen.append((job.id, job.lease_token))
        db.execute(text('SELECT pg_sleep(2.4)'))
        probe(db, s)
        return 'succeeded'
    # This deliberately substitutes ONLY business execution/outbox work; claim,
    # recovery, attach, actor checks, heartbeat, transactions and finalization run.
    monkeypatch.setattr(workflow_graph, 'process', local_handler)
    monkeypatch.setattr(worker_native_audio_v136, 'enqueue_completed', lambda *_: False)
    assert worker.process_once(s.job_id, deliver=False, workspace_id=s.p.workspace_id) is True
    with Session(engine()) as db:
        job = db.get(Job, s.job_id)
        assert job.status == 'succeeded' and job.attempts == 1 and job.lease_until is None
        assert seen == [(job.id, job.lease_token)]
        assert db.scalar(select(func.count()).select_from(Invocation).where(
            Invocation.workspace_id == s.p.workspace_id)) == 0


def provenance_scene(account, monkeypatch, *, pending=False, slow=False):
    s = original_job(account)
    reader = waits.reader
    inputs = {'policy_pin': None}
    manifest = {'kind': 'tool', 'allowed_agent_roles': ['data'],
        'execution': {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False},
        'input_schema': reader.input_schema(), 'output_schema': reader.output_schema()}
    cap = NS(id=str(uuid4()), content_hash=digest(manifest), data=manifest)
    node = {'id': 'read_current_context', 'kind': 'ability', 'source_step_id': 'read_current_context',
        'guards': guards(), 'actor_id': s.p.user_id, 'execution': {
            'capability_id': cap.id, 'capability_hash': cap.content_hash, 'agent_role': 'data',
            'input_bindings': {}, 'arguments': inputs, 'input_schema': reader.input_schema(),
            'output_schema': reader.output_schema(), 'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}}
    graph = kernel.validate_graph(base([node, control('end', 'end')],
        [edge(node['id'], 'end')], node['id'], [node['id']]))
    state = kernel.new_run(graph, {}, 10)
    activation = next(row for row in state['activations'].values() if row['node_id'] == node['id'])
    with Session(engine(), expire_on_commit=False) as db:
        package = Capability(workspace_id=s.p.workspace_id, namespace='lease_test', name='scope_' + uuid4().hex,
            kind='domain_package', release_version='1.0.0', content_hash=digest({}), data={})
        db.add(package); db.flush()
        task = Task(workspace_id=s.p.workspace_id, owner_id=s.p.user_id, package_id=package.id,
            title='公开合成租约检查，不代表业务采用', status='workflow_waiting', data={'graph_runtime': state})
        db.add(task); db.flush()
        job = db.get(Job, s.job_id); key = 'graph:' + task.id + ':' + activation['id'] + ':1'
        job.idempotency_key = key
        job.data = {'task_id': task.id, 'actor_id': s.p.user_id, 'activation_id': activation['id'],
            'activation_version': 0, 'graph_hash': state['graph_hash'], 'cancel_epoch': state['cancel_epoch'],
            'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'invocation_key': key, 'attempt': 1}
        task.data = {**task.data, 'graph_jobs': {activation['id']: {'job_id': job.id, 'invocation_key': key,
            'phase': 'queued', 'input_hash': digest(inputs), 'cancel_epoch': state['cancel_epoch'], 'attempt': 1}}}
        if not pending:
            # Only the non-null marker is needed by the actual reader wait
            # selection. No query result or successful business is fabricated.
            task.data = {**task.data, 'native_goal_query_id': str(uuid4())}
        else:
            goal = Job(workspace_id=s.p.workspace_id, kind='check_native_business_goal', status='running',
                idempotency_key='native-goal-phase:' + uuid4().hex,
                data={'task_id': task.id, 'actor_id': s.p.user_id, 'context_hash': digest(None)})
            db.add(goal); db.flush()
            pin = {'job_id': goal.id, 'idempotency_key': goal.idempotency_key,
                'context_hash': digest(None), 'actor_id': s.p.user_id}
            task.data = {**task.data, 'native_goal_check_job_pin': pin,
                'native_goal_check_job_pin_hash': digest(pin), 'native_goal_context_hash': digest(None)}
        db.commit(); s.task_id = task.id
    def frozen(db, p, task_id, activation_id):
        assert p == s.p and task_id == s.task_id and activation_id == activation['id']
        if slow: db.execute(text('SELECT pg_sleep(2.4)'))
        task = db.get(Task, task_id)
        actual_state = kernel.check_run(graph, task.data['graph_runtime'])
        return task, NS(), {}, graph, actual_state, actual_state['activations'][activation_id], node
    monkeypatch.setattr(waits.scope, '_frozen', frozen)
    return s, cap, node, inputs


def test_provenance_without_dependencies_keeps_job_unlocked_during_actual_sql_and_long_source_boundary(account, monkeypatch):
    s, cap, node, inputs = provenance_scene(account, monkeypatch, slow=True)
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', 0.05)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread() is threading.main_thread() and 'FROM jobs' in statement:
            statements.append(statement)
    with recovered(s) as (db, phase):
        phase.activate()
        with leases.heartbeat(engine(), s.job_id, s.token):
            event.listen(engine(), 'before_cursor_execute', capture)
            try:
                task = db.get(Task, s.task_id); job = db.get(Job, s.job_id)
                assert waits.preflight(db, s.p, task, job, node, cap, inputs) is False
            finally: event.remove(engine(), 'before_cursor_execute', capture)
            assert statements and not any('FOR UPDATE' in sql for sql in statements)
            assert leases.renew(engine(), s.job_id, s.token) is True
            assert waits.KEY not in job.data
            db.commit()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.workspace_id == s.p.workspace_id, Audit.action == waits.WAIT)) == 0
        assert db.scalar(select(func.count()).select_from(Invocation).where(
            Invocation.workspace_id == s.p.workspace_id)) == 0


@pytest.mark.parametrize('fault', [None, 'takeover', 'expired', 'epoch', 'input'])
def test_wait_registration_locks_only_after_dependencies_and_rechecks_original_pins(account, monkeypatch, fault):
    s, cap, node, inputs = provenance_scene(account, monkeypatch, pending=True)
    original_dependencies = waits._dependencies
    saw = []
    def dependencies(db, p, task, family, value):
        # Independent actual UPDATE succeeds before this long-read section;
        # the old _locked_preflight FOR UPDATE would instead time out here.
        assert leases.renew(engine(), s.job_id, s.token) is True
        result = original_dependencies(db, p, task, family, value)
        assert len(result) == 1 and result[0]['kind'] == 'native_goal_check'
        saw.append('dependencies_complete')
        if fault in {'takeover', 'expired'}:
            with engine().begin() as connection:
                connection.execute(update(Job).where(Job.id == s.job_id).values(**(
                    {'lease_token': str(uuid4())} if fault == 'takeover' else
                    {'lease_until': func.clock_timestamp() - timedelta(seconds=1)})))
        elif fault == 'epoch':
            # Explicit in-process corruption after the source boundary must
            # still fail the final actual activation/Job pin check.
            changed = copy.deepcopy(task.data)
            changed['graph_runtime']['cancel_epoch'] += 1
            task.data = changed
        elif fault == 'input':
            job = db.get(Job, s.job_id)
            job.data = {**job.data, 'inputs': {'policy_pin': {'changed': True}}}
        return result
    monkeypatch.setattr(waits, '_dependencies', dependencies)
    with recovered(s) as (db, phase):
        phase.activate()
        task = db.get(Task, s.task_id); job = db.get(Job, s.job_id)
        if fault is None:
            assert waits.preflight(db, s.p, task, job, node, cap, inputs) is True
            assert job.status == waits.PHASE and saw == ['dependencies_complete']
            assert job.data[waits.KEY]['history'][0]['dependencies'][0]['kind'] == 'native_goal_check'
            db.commit()
        else:
            with pytest.raises(HTTPException): waits.preflight(db, s.p, task, job, node, cap, inputs)
            db.rollback()
    with Session(engine()) as db:
        count = db.scalar(select(func.count()).select_from(Audit).where(
            Audit.workspace_id == s.p.workspace_id, Audit.action == waits.WAIT))
        assert count == (1 if fault is None else 0)
        assert db.scalar(select(func.count()).select_from(Invocation).where(
            Invocation.workspace_id == s.p.workspace_id)) == 0
