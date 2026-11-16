"""Same-Job wait persistence with actual graph/pin/audit logic and IO seams.

The registered graph/source/IAM boundaries are explicit synthetic adapters;
these are not PostgreSQL, lease-concurrency or real device/provider evidence.
No graph_context (which needs an Invocation) is available to the scheduler.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import workflow_graph, workflow_graph_goal_guard_v133 as goal_guard
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import models as m
from generic_agents.contracts import digest
from tests.test_workflow_task_context_v196 import scene as original_scene, IO as OriginalIO, identity, same_sql
from tests.test_report_business_flow_wire_v184 import no_external
from tests.test_full_semantic_graph_v1 import base, control, edge


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class IO(OriginalIO):
    def __init__(self):
        super().__init__(); self.additions = []; self.queries = []; self.commits = 0; self.present_call = None
    def scalar(self, statement):
        if self.fail: raise self.fail
        s = self.scene; self.queries.append(statement)
        choices = [
            (select(m.Task).where(m.Task.workspace_id == s.task.workspace_id, m.Task.id == s.task.id)
                .with_for_update().execution_options(populate_existing=True), s.task),
            (select(m.Task).where(m.Task.workspace_id == s.task.workspace_id, m.Task.id == s.task.id)
                .execution_options(populate_existing=True), s.task),
            (select(m.InstanceRelease).where(m.InstanceRelease.workspace_id == s.task.workspace_id,
                m.InstanceRelease.id == s.release.id).execution_options(populate_existing=True), s.release),
            (select(m.Invocation).where(m.Invocation.workspace_id == s.job.workspace_id,
                m.Invocation.idempotency_key == s.job.data['invocation_key']).execution_options(populate_existing=True), self.present_call),
            (select(m.Job).where(m.Job.workspace_id == s.p.workspace_id, m.Job.id == s.job.id,
                m.Job.kind == waits.scope.KIND, m.Job.status == 'running', m.Job.lease_token == s.job.lease_token,
                m.Job.lease_until > func.clock_timestamp()).with_for_update().execution_options(populate_existing=True),
                s.job if s.job.status == 'running' else None),
            (select(m.Job).where(m.Job.workspace_id == s.p.workspace_id, m.Job.id == s.job.id,
                m.Job.kind == waits.scope.KIND, m.Job.status == 'running', m.Job.lease_token == s.job.lease_token,
                m.Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True),
                s.job if s.job.status == 'running' else None),
            (select(m.Job).where(m.Job.workspace_id == s.job.workspace_id, m.Job.id == s.job.id,
                m.Job.kind == waits.scope.KIND, m.Job.status == waits.PHASE).with_for_update()
                .execution_options(populate_existing=True), s.job if s.job.status == waits.PHASE else None),
            (select(m.Job).where(m.Job.workspace_id == s.job.workspace_id, m.Job.id == s.job.id,
                m.Job.kind == waits.scope.KIND).execution_options(populate_existing=True), s.job)]
        for (model, identifier), row in self.rows.items():
            if model is m.Job:
                choices.append((select(m.Job).where(m.Job.workspace_id == s.job.workspace_id, m.Job.id == identifier)
                    .execution_options(populate_existing=True), row))
            if model is m.Invocation:
                choices.append((select(m.Invocation).where(m.Invocation.workspace_id == s.job.workspace_id,
                    m.Invocation.id == identifier).execution_options(populate_existing=True), row))
        for expected, result in choices:
            if same_sql(statement, expected): return result
        raise AssertionError('Unexpected wait SQL: only exact current Task/Job/dependency/Invocation reads allowed')
    def scalars(self, statement):
        if self.fail: raise self.fail
        s = self.scene; self.queries.append(statement)
        expected = select(m.Audit).where(m.Audit.workspace_id == s.job.workspace_id,
            m.Audit.action.in_([waits.WAIT, waits.WAKE]), m.Audit.object_id == s.job.id)
        if same_sql(statement, expected):
            return [event for event in self.events if event.workspace_id == s.job.workspace_id
                and event.object_id == s.job.id and event.action in {waits.WAIT, waits.WAKE}]
        for workspace in (None, s.job.workspace_id):
            query = select(m.Job).where(m.Job.kind == waits.scope.KIND, m.Job.status == waits.PHASE,
                m.Job.available_at <= func.clock_timestamp()).order_by(m.Job.available_at, m.Job.id).limit(64)
            if workspace is not None: query = query.where(m.Job.workspace_id == workspace)
            if same_sql(statement, query): return [s.job] if s.job.status == waits.PHASE else []
        raise AssertionError('Unexpected wait inventory or scheduling SQL')
    def add(self, row):
        assert isinstance(row, m.Audit), 'No Invocation, Goal reservation, acquisition or new Job can be inserted'
        row.id = identity('wait-audit-' + str(len(self.events))); self.events.append(row); self.additions.append(type(row))
    def commit(self): self.commits += 1


@pytest.fixture
def scene(monkeypatch):
    s = original_scene.__wrapped__(monkeypatch)
    previous = s.db; s.db = IO(); s.db.rows = previous.rows; s.db.scene = s
    s.task = m.Task(id=s.task.id, workspace_id=s.task.workspace_id, package_id=s.task.package_id,
        owner_id=s.p.user_id, title='Synthetic original graph task', status=s.task.status,
        version=1, data=copy.deepcopy(s.task.data))
    s.job = m.Job(id=s.job.id, workspace_id=s.job.workspace_id, kind=s.job.kind, status='running',
        idempotency_key=s.key, attempts=1, available_at=AT, lease_token=identity('lease'),
        lease_until=AT + timedelta(hours=1), data=copy.deepcopy(s.job.data))
    s.db.rows[m.Task, s.task.id] = s.task; s.db.rows[m.Job, s.job.id] = s.job
    s.task.data['graph_jobs'][s.activation['id']]['phase'] = 'queued'
    s.check = m.Job(id=identity('goal-check-job'), workspace_id=s.p.workspace_id, status='queued', attempts=0,
        kind='check_native_business_goal', idempotency_key='native-goal:original',
        data={'task_id': s.task.id, 'actor_id': identity('original-goal-actor'),
            'context_hash': digest(s.task.data['native_goal_context'])})
    pin = {'job_id': s.check.id, 'idempotency_key': s.check.idempotency_key,
        'context_hash': s.check.data['context_hash'], 'actor_id': s.check.data['actor_id']}
    s.task.data.update(native_goal_query_id=None, native_goal_check_job_pin=pin, native_goal_check_job_pin_hash=digest(pin))
    s.db.rows[m.Job, s.check.id] = s.check
    monkeypatch.setattr(waits, 'ownership', lambda db: {'job_id': s.job.id, 'lease_token': s.job.lease_token})
    monkeypatch.setattr(workflow_graph, 'graph_for', lambda release, business: copy.deepcopy(s.graph))
    # Existing graph/Goal pure structure checks are retained. Only the live
    # source authority was the inherited explicit _frozen IO boundary.
    s.wait = lambda: waits.preflight(s.db, s.p, s.task, s.job, s.node, s.cap, s.inputs)
    s.schedule = lambda: waits.schedule_once(s.db, workspace_id=s.p.workspace_id)
    return s


def wake(s):
    s.check.status = 'succeeded'; s.task.data['native_goal_query_id'] = s.run.id
    return s.schedule()


def consumer_inputs(s, execution, inputs, schema):
    """Actual kernel input freezing; capability registration remains IO."""
    s.cap.data['execution'] = execution; s.cap.data['input_schema'] = schema
    s.cap.data['allowed_agent_roles'] = ['business'] if execution['adapter'] in {'workflow_service', 'business_reasoning'} else ['data']
    s.cap.content_hash = digest(s.cap.data)
    s.inputs = copy.deepcopy(inputs); node = copy.deepcopy(s.node)
    node['execution'].update(capability_hash=s.cap.content_hash, input_schema=schema, arguments=copy.deepcopy(inputs),
        adapter=execution['adapter'], agent_role=s.cap.data['allowed_agent_roles'][0])
    s.graph = base([node, control('context-end', 'end')], [edge(node['id'], 'context-end')], node['id'], [node['id']])
    s.state = kernel.new_run(s.graph, {}, 10); s.node = node
    activation_id = s.state['eligible_ability_ids'][0]; s.activation = s.state['activations'][activation_id]
    s.job.data.update(inputs=copy.deepcopy(inputs), input_hash=digest(inputs), graph_hash=s.state['graph_hash'], activation_id=activation_id)
    s.frozen.update(graph_hash=s.state['graph_hash'], activation_id=activation_id, node_id=node['id'])
    s.task.data['graph_runtime'] = copy.deepcopy(s.state)
    s.task.data['graph_jobs'] = {activation_id: {'job_id': s.job.id, 'invocation_key': s.key,
        'cancel_epoch': 0, 'input_hash': digest(inputs), 'phase': 'queued'}}


def producer(s):
    key = 'graph:original-producer'; job = m.Job(id=identity('context-producer-job'), workspace_id=s.p.workspace_id,
        kind=waits.scope.KIND, status='running', idempotency_key=key,
        data={'task_id': s.task.id, 'actor_id': identity('context-producer-actor'), 'invocation_key': key})
    call = m.Invocation(id=identity('context-producer-call'), workspace_id=s.p.workspace_id,
        capability_id=identity('producer-context-cap'), status='succeeded',
        idempotency_key=key, data={'job_id': job.id, 'output': {'actual_saved_output': 'synthetic IO'}})
    s.db.rows[m.Job, job.id] = job; s.db.rows[m.Invocation, call.id] = call
    context = {'producer': {'job_id': job.id, 'invocation_id': call.id, 'actor_id': job.data['actor_id'],
        'request_key_hash': digest(key)}, 'description': 'The separate reader cold-check is an explicit authority IO seam'}
    return job, call, context


def test_actual_goal_dependency_waits_same_job_with_independent_audit_and_zero_dispatch(scene):
    s = scene; job_id, key, attempts = s.job.id, s.job.idempotency_key, s.job.attempts
    assert s.wait() is True
    assert s.job.status == waits.PHASE and s.task.data['graph_jobs'][s.activation['id']]['phase'] == waits.PHASE
    ledger = s.job.data[waits.KEY]; record = ledger['history'][0]
    assert ledger['active_hash'] == record['record_hash'] and record['dependencies'][0]['job_id'] == s.check.id
    assert record['dependencies'][0]['pin'] == s.task.data['native_goal_check_job_pin']
    assert s.job.id == job_id and s.job.idempotency_key == key and s.job.attempts == attempts
    assert len(s.db.events) == 1 and s.db.events[0].action == waits.WAIT
    assert s.db.additions == [m.Audit] and s.db.commits == 0
    assert not s.task.data.get(goal_guard.KEY, {}).get('items') and s.db.present_call is None


def test_active_dependency_does_not_wake_then_terminal_requeues_original_and_worker_rechecks(scene):
    s = scene; s.wait(); before = copy.deepcopy((s.job.data, s.task.data)); available = s.job.available_at
    assert s.schedule() == 0 and (s.job.data, s.task.data) == before
    assert wake(s) == 1 and s.job.status == 'queued'
    assert s.job.attempts == 1 and s.job.available_at == available and s.job.idempotency_key == s.key
    assert [event.action for event in s.db.events] == [waits.WAIT, waits.WAKE]
    assert s.job.data[waits.KEY]['active_hash'] is None
    s.job.status = 'running'; s.job.attempts += 1
    assert s.wait() is False and len(s.db.events) == 2


@pytest.mark.parametrize('fault', ['missing_pin', 'pin_hash', 'job_kind', 'other_task', 'actor', 'context', 'failed', 'succeeded_without_query'])
def test_no_future_or_borrowed_goal_job_can_create_a_wait(scene, fault):
    s = scene
    if fault == 'missing_pin': s.task.data.pop('native_goal_check_job_pin')
    elif fault == 'pin_hash': s.task.data['native_goal_check_job_pin_hash'] = 'f' * 64
    elif fault == 'job_kind': s.check.kind = waits.scope.KIND
    elif fault == 'other_task': s.check.data['task_id'] = identity('other-task')
    elif fault == 'actor': s.check.data['actor_id'] = identity('other-actor')
    elif fault == 'context': s.check.data['context_hash'] = 'f' * 64
    else: s.check.status = 'failed' if fault == 'failed' else 'succeeded'
    with pytest.raises(HTTPException): s.wait()
    assert waits.KEY not in s.job.data and not s.db.events and s.db.present_call is None


@pytest.mark.parametrize('fault', ['wait_audit', 'ledger', 'record', 'dependency', 'transport_hash', 'input', 'duplicate_audit', 'wake_audit'])
def test_wait_or_compound_evidence_loss_blocks_and_is_visible_not_silently_waiting(scene, fault):
    s = scene; s.wait()
    if fault == 'wait_audit': s.db.events.clear()
    elif fault == 'ledger': s.job.data.pop(waits.KEY)
    elif fault == 'record': s.job.data[waits.KEY]['history'][0]['capability_hash'] = 'f' * 64
    elif fault == 'dependency': s.check.idempotency_key = 'different-original-job'
    elif fault == 'transport_hash': s.task.data['graph_jobs'][s.activation['id']]['provenance_wait_hash'] = 'f' * 64
    elif fault == 'input': s.job.data['inputs']['policy_pin'] = {'policy_id': identity('other-policy'), 'approved_hash': 'f' * 64}
    elif fault == 'duplicate_audit': s.db.events.append(copy.deepcopy(s.db.events[0]))
    else:
        assert wake(s) == 1
        s.job.status = waits.PHASE; s.task.data['graph_jobs'][s.activation['id']]['phase'] = waits.PHASE
        s.db.events[:] = [event for event in s.db.events if event.action != waits.WAKE]
    assert s.schedule() == 0 and s.job.status == 'blocked'
    transport = s.task.data['graph_jobs'][s.activation['id']]
    assert transport['phase'] == 'blocked' and transport['reason'] and s.db.present_call is None


@pytest.mark.parametrize('fault', ['graph_cancel', 'graph_stop', 'epoch', 'goal_cancel', 'goal_timeout'])
def test_cancelled_original_never_wakes_and_keeps_history(scene, fault):
    s = scene; s.wait(); ledger = copy.deepcopy(s.job.data[waits.KEY])
    if fault == 'graph_cancel': s.task.data['graph_runtime']['status'] = 'cancelled'
    elif fault == 'graph_stop': s.task.data['graph_runtime']['status'] = 'stopped'
    elif fault == 'epoch': s.task.data['graph_runtime']['cancel_epoch'] += 1
    elif fault == 'goal_cancel': s.task.data['native_goal_cancelled'] = True
    else: s.task.data['native_goal_timed_out'] = True
    assert s.schedule() == 0 and s.job.status == 'cancelled'
    assert s.task.data['graph_jobs'][s.activation['id']]['phase'] == 'cancelled'
    assert s.job.data[waits.KEY] == ledger


def test_replaced_transport_is_not_overwritten_on_a_corrupted_old_wait(scene):
    s = scene; s.wait(); transport = s.task.data['graph_jobs'][s.activation['id']]
    transport['job_id'] = identity('replacement-job'); transport['phase'] = 'queued'
    before = copy.deepcopy(transport)
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert s.task.data['graph_jobs'][s.activation['id']] == before


@pytest.mark.parametrize('fault', ['invocation', 'dispatch', 'missing_wake', 'active', 'input', 'epoch'])
def test_recovery_only_exception_requires_a_proven_original_never_dispatched_wait(scene, fault):
    s = scene; s.wait(); assert wake(s) == 1
    s.job.status = 'running'; s.job.attempts = 4; s.job.data['recovery_only'] = True
    assert waits.can_start_after_wait(s.db, s.task, s.job) is True
    assert s.job.attempts == 4 and s.db.present_call is None
    if fault == 'invocation': s.db.present_call = NS(id=identity('already-invoked'))
    elif fault == 'dispatch': s.task.data['graph_jobs'][s.activation['id']]['dispatch_committed'] = True
    elif fault == 'missing_wake': s.db.events[:] = [event for event in s.db.events if event.action != waits.WAKE]
    elif fault == 'active': s.job.data[waits.KEY]['active_hash'] = s.job.data[waits.KEY]['history'][-1]['record_hash']
    elif fault == 'input': s.job.data['input_hash'] = 'f' * 64
    else: s.task.data['graph_runtime']['cancel_epoch'] += 1
    assert waits.can_start_after_wait(s.db, s.task, s.job) is False
    assert s.job.attempts == 4


def test_old_family_is_zero_path_and_only_producer_pending_is_caught(scene, monkeypatch):
    s = scene; old = copy.deepcopy(s.cap); old.data['execution']['handler'] = 'aggregate_values'
    before = len(s.db.queries)
    assert waits.preflight(s.db, s.p, s.task, s.job, s.node, old, s.inputs) is False
    assert len(s.db.queries) == before and not s.db.events
    s.cap.data['execution'] = {'adapter': 'builtin', 'handler': 'require_observation_context_v197', 'side_effect': False}
    s.cap.data['input_schema'] = {'type': 'object', 'properties': {'context': {'type': ['object', 'null']}},
        'required': ['context'], 'additionalProperties': False}
    s.inputs = {'context': {'source': 'synthetic actual-boundary candidate'}}
    s.job.data['inputs'] = copy.deepcopy(s.inputs); s.job.data['input_hash'] = digest(s.inputs)
    s.task.data['graph_jobs'][s.activation['id']]['input_hash'] = digest(s.inputs)
    s.activation['inputs_snapshot']['arguments'] = copy.deepcopy(s.inputs)
    failure = HTTPException(409, 'synthetic original source rejected')
    def rejected(*args, **kwargs): raise failure
    monkeypatch.setattr(waits.reader, 'checked_context', rejected)
    with pytest.raises(HTTPException) as error: s.wait()
    assert error.value is failure and not s.db.events


@pytest.mark.parametrize('family', ['context_service', 'require_context', 'reasoning'])
def test_context_families_wait_on_original_producer_before_any_invocation(scene, monkeypatch, family):
    s = scene; original, call, context = producer(s); seen = []
    if family == 'reasoning':
        execution = {'adapter': 'business_reasoning', 'side_effect': False}
        inputs = {'candidates': [{'context': context}, {'context': None}, {'context': copy.deepcopy(context)}]}
        schema = {'type': 'object', 'properties': {'candidates': {'type': 'array', 'items': {'type': 'object'}}},
            'required': ['candidates'], 'additionalProperties': False}
    else:
        execution = ({'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': 'resense_from_context', 'side_effect': True} if family == 'context_service' else
            {'adapter': 'builtin', 'handler': 'require_observation_context_v197', 'side_effect': False})
        inputs = {'context': context}; schema = {'type': 'object', 'properties': {'context': {'type': ['object', 'null']}},
            'required': ['context'], 'additionalProperties': False}
    consumer_inputs(s, execution, inputs, schema)
    def cold_boundary(db, p, value, *, task, dispatch):
        assert db is s.db and task is s.task and dispatch is False
        seen.append(copy.deepcopy(value)); raise waits.reader.ProducerPending(original.id, call.id)
    monkeypatch.setattr(waits.reader, 'checked_context', cold_boundary)
    assert s.wait() is True and len(seen) == (2 if family == 'reasoning' else 1)
    deps = s.job.data[waits.KEY]['history'][-1]['dependencies']
    assert [row['source_path'] for row in deps] == (['/candidates/0/context', '/candidates/2/context'] if family == 'reasoning' else ['/context'])
    assert all(row['job_id'] == original.id and row['invocation_id'] == call.id for row in deps)
    assert s.schedule() == 0
    original.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued' and s.db.present_call is None


def test_nullable_context_has_no_fabricated_dependency_or_success_from_preflight(scene, monkeypatch):
    s = scene
    consumer_inputs(s, {'adapter': 'builtin', 'handler': 'require_observation_context_v197', 'side_effect': False},
        {'context': None}, {'type': 'object', 'properties': {'context': {'type': ['object', 'null']}},
            'required': ['context'], 'additionalProperties': False})
    monkeypatch.setattr(waits.reader, 'checked_context', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No nonnull context exists')))
    assert s.wait() is False and waits.KEY not in s.job.data and not s.db.events


def test_scheduler_does_not_call_live_graph_context_and_sql_errors_are_not_hidden(scene, monkeypatch):
    s = scene; s.wait()
    monkeypatch.setattr(waits.scope, 'graph_context', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Scheduler is unowned')))
    assert wake(s) == 1
    s.job.status = waits.PHASE; s.db.fail = RuntimeError('synthetic SQL connection error')
    with pytest.raises(RuntimeError) as error: s.schedule()
    assert error.value is s.db.fail
