"""Root-run PG consumer journeys over ordinary authenticated workflow Tasks.

Only the upstream graph document/provider fixtures and an explicitly synthetic
HTTP provider are substituted. Native inference, workflow authority, SDKs,
independent ACKs and durable worker execution remain production paths. Shared
V264 fixtures are frozen and reused; this file owns no production changes.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event as ThreadEvent, Lock, Thread
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import worker
from generic_agents import workflow_task_consumer_v265 as consumer
from generic_agents import workflow_pixel_receipt_status_v263 as pixel_status
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from tests.test_instance_workspace import complete, configuration, prepared, object_row, OBJECTS
from tests.test_vertical_slice import manifest, publish
from tests.test_workflow_job_dependencies_v264_pg import (
    compact_scene, parallel_graph_seed, full_source_scene, v7_scene,
    reading_scene, local_runtime, _release, _new_task, _task, _graph_job,
    _parallel_graph_task, _producer_final_commit, _lost_final_commit, _one_sdk,
)
from tests.test_workflow_pixel_receipt_status_v263_pg import (
    flow_scene, native_scene, configured_flow, create_task, good,
    pytestmark as native_pytestmark,
)
from tests.test_workflow_abilities import enable, tool
from tests.test_workflow_sound_native_pg import sound_scene, original_scene
from tests.test_workflow_sound_receipt_gateway_v261_pg import configured_flow as configured_sound_flow


def _created_task(workspace_id, task_id):
    with Session(engine()) as db:
        task = db.get(m.Task, task_id)
        assert task is not None and task.workspace_id == workspace_id
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == workspace_id,
            m.Audit.action == 'workflow.created', m.Audit.object_id == task_id)))
        assert len(proofs) == 1
        assert proofs[0].data == {'release': task.data['instance_release_id'],
            'business': task.data['business_id']}
        assert proofs[0].actor_id == task.owner_id
        return SimpleNamespace(task_id=task.id, workspace_id=task.workspace_id,
            instance_id=task.data['instance_id'], actor_id=proofs[0].actor_id,
            release_id=task.data['instance_release_id'])


def _row_pin(row):
    extra = ()
    if isinstance(row, m.Job):
        extra = (row.kind, row.attempts, row.lease_token, row.lease_until, row.available_at)
    elif isinstance(row, m.Invocation):
        extra = (row.capability_id, row.idempotency_key)
    elif isinstance(row, m.CloudCall):
        extra = (row.capability_id, row.reserved_tokens, row.charged_tokens)
    return (row.version, getattr(row, 'status', None), digest(row.data), *extra)


def _queue_and_call_stock(workspace_id, *, except_tasks=()):
    """All unselected Jobs and SDK/paid rows, including non-workflow kinds."""
    except_tasks = set(except_tasks)
    with Session(engine()) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == workspace_id)))
        selected = {row.id for row in jobs if row.data.get('task_id') in except_tasks}
        return {
            'jobs': {row.id: _row_pin(row) for row in jobs if row.id not in selected},
            'sdk': {row.id: _row_pin(row) for row in db.scalars(select(m.Invocation).where(
                m.Invocation.workspace_id == workspace_id)) if row.data.get('job_id') not in selected},
            'cloud': {row.id: _row_pin(row) for row in db.scalars(select(m.CloudCall).where(
                m.CloudCall.workspace_id == workspace_id))},
        }


def _task_stock(workspace_id, task_ids):
    task_ids = set(task_ids)
    with Session(engine()) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == workspace_id,
            m.Job.data['task_id'].as_string().in_(task_ids))))
        job_ids = {row.id for row in jobs}
        return {
            'tasks': {row.id: _row_pin(row) for row in db.scalars(select(m.Task).where(
                m.Task.workspace_id == workspace_id, m.Task.id.in_(task_ids)))},
            'jobs': {row.id: _row_pin(row) for row in jobs},
            'sdk': {row.id: _row_pin(row) for row in db.scalars(select(m.Invocation).where(
                m.Invocation.workspace_id == workspace_id)) if row.data.get('job_id') in job_ids},
        }


def _only_original_ack_refresh_added(s, before, after, observation_id):
    """ACK may queue its real object projection; this consumer must not run it."""
    from generic_agents import state_refresh
    from generic_agents.security import Principal
    assert after['sdk'] == before['sdk'] and after['cloud'] == before['cloud']
    assert set(before['jobs']) <= set(after['jobs'])
    assert {key: after['jobs'][key] for key in before['jobs']} == before['jobs']
    added = set(after['jobs']) - set(before['jobs'])
    assert len(added) == 1
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        assert observation is not None and observation.workspace_id == s.workspace_id
        source_event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation.id))
        assert source_event.status == 'received' and source_event.data['observation_id'] == observation.id
        entity = db.get(m.Entity, observation.entity_id)
        assert entity.workspace_id == s.workspace_id
        authorized = source_event.data.get('actor_id') or observation.data['submitted_by']
        assert db.get(m.User, authorized) is not None
        assert db.scalar(select(m.Membership.id).where(m.Membership.workspace_id == s.workspace_id,
            m.Membership.user_id == authorized)) is not None
        receiver = Principal(authorized, s.workspace_id, 'viewer', 'data-gap-receiver')
        current = state_refresh.target(db, receiver, entity)
        task = db.get(m.Task, s.task_id)
        assert task.workspace_id == s.workspace_id
        assert current is not None and current['instance_id'] == task.data['instance_id']
        for identity in added:
            job = db.get(m.Job, identity)
            assert job.workspace_id == s.workspace_id and job.kind == state_refresh.KIND
            assert job.status == 'queued' and job.attempts == 0 and job.lease_token is None and job.lease_until is None
            request = state_refresh.checked(job)
            assert set(request) == {'schema', *current, 'authorized_by', 'reason',
                'trigger_observation_id', 'trigger_observation_version', 'due_state_hash'}
            assert {key: request[key] for key in current} == current
            assert request['schema'] == state_refresh.SCHEMA
            assert request['authorized_by'] == job.data['actor_id'] == authorized
            assert request['trigger_observation_id'] == observation.id
            assert request['trigger_observation_version'] == observation.version
            assert request['reason'] == '新登记处理结果已保存' and request['due_state_hash'] is None
            assert job.idempotency_key == ('state-refresh:' + observation.id + ':' + str(observation.version)
                + ':' + digest(request['reason'])[:12])
            assert 'state_result' not in job.data and 'state_result_hash' not in job.data


def _queued_without_sdk(job_id):
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.status == 'queued' and job.attempts == 0 and job.lease_token is None
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == job.workspace_id,
            m.Invocation.idempotency_key == job.idempotency_key)) is None


def _expire_original_lease(job_id, token):
    """Only a disposable crash deadline changes; no successful state is made."""
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.status == 'running' and job.lease_token == token and job.attempts == 1
        job.lease_until = db.scalar(text("SELECT clock_timestamp() - INTERVAL '1 second'"))
        db.commit()


_OWN_TASK = object()


def _step(s, *, family='compact', task_id=_OWN_TASK, after=None, delivery_after=None, stop_event=None):
    scope = _created_task(s.workspace_id, s.task_id)
    outcome = consumer.step(s.workspace_id, scope.instance_id, family=family,
        task_id=s.task_id if task_id is _OWN_TASK else task_id, after=after,
        delivery_after=delivery_after, limit=20, stop_event=stop_event)
    assert {'processed', 'after', 'delivery_after', 'waiting', 'errors', 'delivered'} <= set(outcome)
    assert type(outcome['processed']) is bool and type(outcome['delivered']) is int and outcome['delivered'] >= 0
    for key in ('after', 'delivery_after'):
        assert outcome[key] is None or (isinstance(outcome[key], tuple) and len(outcome[key]) == 2)
    assert type(outcome['waiting']) is list and type(outcome['errors']) is list
    for item in outcome['waiting']:
        assert item['state'] in {'waiting_producer', 'waiting_data', 'blocked_integrity'}
        assert type(item['reason']) is str and item['reason']
    for item in outcome['errors']:
        assert type(item['reason']) is str and item['reason']
    return outcome


def _advance(s, *, family='compact', count=12):
    results = []
    for _ in range(count):
        results.append(_step(s, family=family))
        saved = _task(s.client, s.task_id)
        if saved['status'] == 'workflow_completed' or (family == 'compact' and saved['status'] == 'workflow_waiting'):
            return saved, results
        assert saved['status'] != 'workflow_blocked', saved.get('workflow_runtime', {})
    pytest.fail('normal scoped consumer did not reach the human or workflow completion')


def _wait(outcome, job_id, state):
    assert any(item['job_id'] == job_id and item['state'] == state and item['reason']
        for item in outcome['waiting']), outcome


def _finish_human(s, saved):
    assert saved['status'] == 'workflow_waiting'
    finished = good(complete(s.client, saved, note='已核对真实后台步骤、保存结果和独立接收依据'))
    assert finished['status'] == 'workflow_completed'
    reopened = _task(s.client, s.task_id)
    assert reopened['workflow_runtime'] == finished['workflow_runtime']
    with Session(engine()) as db:
        row = db.get(m.Task, s.task_id)
        assert row.data['workflow_runtime'] == reopened['workflow_runtime']
    return finished


@pytest.fixture
def paired_compact_scene(client, account):
    """Create a second instance from the actual saved engineering draft.

    No PUT resets source/schema adoptions. Explicit new-instance intent avoids
    the legacy same-hash reuse path; the original release/Task remain intact.
    Tests obtain graph fixtures lazily only after this fixture has completed.
    """
    # The graph fixture legitimately registers a business-role tool under its
    # default identity. This data-role capability has a separate real registry
    # identity, rather than reusing an incompatible published manifest.
    capability = tool(client, namespace='paired_consumer_data_v265')
    original_release = _release(client, capability)
    original, entity = _new_task(client, original_release)
    s = SimpleNamespace(client=client, workspace_id=account[2], capability=capability,
        release=original_release, task_id=original['id'],
        parent_id=original['workflow_runtime']['automatic']['job_id'], entity=entity)
    current = good(s.client.get('/api/setup/draft'))
    creator = _created_task(s.workspace_id, s.task_id).actor_id
    body = {'revision': current['revision'], 'objects': copy.deepcopy(OBJECTS),
        'role_bindings': {'核验负责人': creator}}
    checked = good(s.client.post('/api/instances/check', json=body))
    assert checked['configuration_hash'] == s.release['configuration_hash']
    release = good(s.client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert release['instance_id'] != s.release['instance_id']
    assert release['available_business_ids'] and release['configuration_hash'] == s.release['configuration_hash']
    enable(s.client, release)
    foreign, _ = _new_task(s.client, release)
    return SimpleNamespace(**vars(s), foreign_release=release, foreign_task=foreign)


def test_normal_consumer_serially_completes_real_compact_steps_until_human(compact_scene):
    s = compact_scene
    scope = _created_task(s.workspace_id, s.task_id)
    assert scope.instance_id == s.release['instance_id']
    stock = _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id])
    saved, results = _advance(s)
    assert sum(value['processed'] for value in results) == 2
    assert not any(value['errors'] for value in results)
    receipts = saved['workflow_runtime']['receipts']
    assert len(receipts) == 2 and receipts[0]['output']['value'] == 12 and receipts[1]['output']['value'] == 1
    for receipt in receipts:
        call, _ = _one_sdk(receipt['request_id'])
        assert call == receipt['invocation_id']
    assert _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id]) == stock
    _finish_human(s, saved)


def test_consumer_keeps_real_child_unclaimed_during_producer_final_commit(compact_scene):
    s = compact_scene
    with _producer_final_commit(s.parent_id):
        saved = _task(s.client, s.task_id)
        child_id = saved['workflow_runtime']['automatic']['job_id']
        stock = _queue_and_call_stock(s.workspace_id)
        for _ in range(3):
            result = _step(s)
            assert result['processed'] is False
            _wait(result, child_id, 'waiting_producer')
            _queued_without_sdk(child_id)
        assert _queue_and_call_stock(s.workspace_id) == stock
    final, _ = _advance(s)
    _one_sdk(child_id)
    _finish_human(s, final)


def test_task_get_reuses_caller_rr_ro_while_parent_holds_other_pool_connection(compact_scene, monkeypatch):
    """A real two-connection pool must serve GET without a third checkout."""
    s = compact_scene
    prior_engine = engine()
    assert prior_engine.pool.checkedout() == 0
    prior_engine.dispose()
    parent_backends = []; displays = []

    def parent_backend(db):
        if any(isinstance(row, m.Job) and row.id == s.parent_id and row.status == 'succeeded'
                and row.data.get('finished_at') for row in db.identity_map.values()):
            parent_backends.append(db.scalar(text('SELECT pg_backend_pid()')))

    with monkeypatch.context() as patch:
        patch.setattr(settings(), 'database_pool_size', 2)
        patch.setattr(settings(), 'database_max_overflow', 0)
        engine.cache_clear()
        bounded = engine()
        assert bounded.pool.size() == 2 and bounded.pool._max_overflow == 0
        original_reason = consumer.admission_reason

        def display(db, p, task, job):
            if task.id != s.task_id: return original_reason(db, p, task, job)
            transaction = db.get_transaction()
            assert transaction is not None and not db.new and not db.dirty and not db.deleted
            assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            assert db.scalar(text('SHOW transaction_read_only')) == 'on'
            backend = db.scalar(text('SELECT pg_backend_pid()'))
            assert parent_backends and backend != parent_backends[-1]
            assert bounded.pool.checkedout() == 2
            result = original_reason(db, p, task, job)
            assert db.get_transaction() is transaction and not db.new and not db.dirty and not db.deleted
            displays.append((backend, result))
            return result

        patch.setattr(consumer, 'admission_reason', display)
        event.listen(Session, 'before_commit', parent_backend)
        try:
            # The request pool surrounds the held transaction: a timeout first
            # releases the real parent, then joins the GET, rather than hiding
            # starvation behind the fixture's 60-second producer deadline.
            with ThreadPoolExecutor(max_workers=1) as requests:
                with _producer_final_commit(s.parent_id):
                    assert bounded.pool.checkedout() == 1
                    started = monotonic()
                    response = requests.submit(s.client.get,
                        '/api/workspace/tasks/' + s.task_id).result(timeout=8)
                    assert monotonic() - started < 8
                    saved = good(response)
                    child_id = saved['workflow_runtime']['automatic']['job_id']
                    assert displays and displays[-1][1] == consumer.PRODUCER_WAIT
                    assert saved['automatic_worker']['state'] == saved['automatic_worker']['job_status'] == 'queued'
                    assert saved['automatic_worker']['reason'] == consumer.PRODUCER_WAIT
                    _queued_without_sdk(child_id)
        finally:
            event.remove(Session, 'before_commit', parent_backend)
            bounded.dispose()
            engine.cache_clear()


def test_two_real_consumers_preserve_one_sdk_per_original_job(compact_scene):
    s = compact_scene
    start = Barrier(2)

    def consume():
        start.wait(timeout=20)
        return [_step(s) for _ in range(8)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        outcomes = [future.result(timeout=45) for future in futures]
    saved = _task(s.client, s.task_id)
    assert saved['status'] == 'workflow_waiting' and len(saved['workflow_runtime']['receipts']) == 2
    for receipt in saved['workflow_runtime']['receipts']:
        _one_sdk(receipt['request_id'])
    assert not any(result['errors'] for thread in outcomes for result in thread)
    _finish_human(s, saved)


def test_consumer_uses_normal_registered_http_handler_and_server_secret_reference(registered_http_scene):
    s = registered_http_scene
    saved, results = _advance(s)
    assert sum(value['processed'] for value in results) == 2 and not any(value['errors'] for value in results)
    receipts = saved['workflow_runtime']['receipts']
    assert len(s.calls) == len(receipts) == 2
    assert receipts[0]['output']['value'] == 12 and receipts[1]['output']['value'] == 1
    for receipt, wire in zip(receipts, s.calls):
        with Session(engine()) as db:
            job = db.get(m.Job, receipt['request_id'])
            assert wire['key'] == job.idempotency_key
        assert wire['input'] == receipt['input_snapshot'] and wire['output'] == receipt['output']
        _one_sdk(receipt['request_id'])
    _finish_human(s, saved)


@pytest.mark.parametrize('gate', ['paused', 'revoked'])
def test_consumer_rechecks_actual_pause_and_membership_without_new_sdk(compact_scene, gate):
    s = compact_scene
    if gate == 'paused':
        instance = next(row for row in good(s.client.get('/api/instances')) if row['id'] == s.release['instance_id'])
        good(s.client.post('/api/instances/' + instance['id'] + '/pause', json={'version': instance['version']}))
    else:
        with Session(engine()) as db:
            member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id))
            member.role = 'viewer'  # Disposable actual revocation, never an authority substitute.
            db.commit()
    with Session(engine()) as db:
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id)))
    for _ in range(3):
        _step(s)
    with Session(engine()) as db:
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id)))
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))
        assert db.get(m.Entity, s.entity['id']).data == s.entity['attributes']


def test_other_real_admin_can_explicitly_execute_created_task_but_revocation_blocks_new_sdk(client, account):
    """Creation proof is not the executor identity; both are actual API acts."""
    from tests.test_workflow_abilities import conf
    capability = tool(client)
    release, _, checked = prepared(client, conf(capability, mode='执行前确认'))
    assert release['available_business_ids'], checked
    enable(client, release)
    original, _ = _new_task(client, release)
    creator = _created_task(account[2], original['id']).actor_id
    username, password = 'v265_executor_' + uuid4().hex[:12], uuid4().hex
    executor = good(client.post('/api/users', json={
        'username': username, 'password': password, 'role': 'admin'}), 201)
    assert executor['id'] != creator
    with TestClient(client.app) as runner:
        login = good(runner.post('/api/login', json={'username': username, 'password': password}))
        runner.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': account[2]})
        assert good(runner.get('/api/me'))['user_id'] == executor['id']
        def request_run(task):
            return good(runner.post('/api/workspace/tasks/' + task['id'] + '/run-ability', json={
                'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
                'request_id': str(uuid4()), 'retry': False}))
        requested = request_run(original)
        s = SimpleNamespace(client=client, workspace_id=account[2], release=release, task_id=original['id'])
        job_id = requested['workflow_runtime']['automatic']['job_id']
        with Session(engine()) as db:
            assert db.get(m.Task, s.task_id).owner_id == creator
            job = db.get(m.Job, job_id)
            assert job.data['actor_id'] == executor['id'] != creator
        finished, _ = _advance(s)
        receipt = finished['workflow_runtime']['receipts'][0]
        assert receipt['actor'] == executor['id'] and receipt['output']['value'] == 12
        _one_sdk(job_id)
        _finish_human(s, finished)

        next_task, _ = _new_task(client, release)
        queued = request_run(next_task)
        next_id = queued['workflow_runtime']['automatic']['job_id']
        with Session(engine()) as db:
            membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == account[2],
                m.Membership.user_id == executor['id']))
            membership.role = 'viewer'  # Actual revocation, not a fake handler or authority record.
            db.commit()
        revoked = SimpleNamespace(client=client, workspace_id=account[2], release=release, task_id=next_task['id'])
        before = _task_stock(account[2], [s.task_id, revoked.task_id])
        outcome = _step(revoked)
        assert not outcome['processed'] and outcome['errors']
        _queued_without_sdk(next_id)
        assert _task_stock(account[2], [s.task_id, revoked.task_id]) == before


def test_consumer_recovers_original_job_after_real_finalizer_lease_loss(compact_scene):
    s = compact_scene
    with _lost_final_commit(s.parent_id) as lost:
        assert _step(s)['processed']
    assert len(lost) == 1
    saved = _task(s.client, s.task_id)
    receipt = copy.deepcopy(saved['workflow_runtime']['receipts'][0])
    child_id = saved['workflow_runtime']['automatic']['job_id']
    for _ in range(2):
        _wait(_step(s), child_id, 'waiting_producer')
        _queued_without_sdk(child_id)
    _expire_original_lease(s.parent_id, lost[0])
    final, _ = _advance(s)
    call, output = _one_sdk(s.parent_id, attempts=2)
    assert call == receipt['invocation_id'] and output == receipt['output']
    _one_sdk(child_id)
    with Session(engine()) as db:
        assert db.get(m.Job, s.parent_id).lease_token != lost[0]
        assert len(list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id)))) == 2
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))
    _finish_human(s, final)


def test_stopped_consumer_run_and_step_do_not_issue_another_sdk(compact_scene):
    s = compact_scene
    stop = ThreadEvent()
    stop.set()
    before = _queue_and_call_stock(s.workspace_id)
    assert _step(s, stop_event=stop)['processed'] is False
    consumer.run(s.workspace_id, s.release['instance_id'], family='compact', task_id=s.task_id, stop_event=stop)
    assert _queue_and_call_stock(s.workspace_id) == before


def test_run_finishes_current_real_job_without_claiming_queued_child_after_stop(compact_scene):
    s = compact_scene
    stop = ThreadEvent()

    def stop_after_original_current_job(db):
        if any(isinstance(row, m.Job) and row.id == s.parent_id and row.data.get('task_id') == s.task_id
            and row.kind == 'execute_workflow_step' and row.status == 'succeeded'
            and row.data.get('finished_at') and row.data.get('scope', {}).get('visit') == 0
            for row in db.identity_map.values()):
            stop.set()

    event.listen(Session, 'before_commit', stop_after_original_current_job)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(consumer.run, s.workspace_id, s.release['instance_id'],
                family='compact', task_id=s.task_id, stop_event=stop)
            try:
                future.result(timeout=45)
            finally:
                stop.set()
    finally:
        stop.set()
        event.remove(Session, 'before_commit', stop_after_original_current_job)
    saved = _task(s.client, s.task_id)
    assert saved['status'] == 'workflow_queued' and len(saved['workflow_runtime']['receipts']) == 1
    _one_sdk(s.parent_id)
    child_id = saved['workflow_runtime']['automatic']['job_id']
    _queued_without_sdk(child_id)
    before = _queue_and_call_stock(s.workspace_id)
    assert _step(s, stop_event=stop)['processed'] is False
    assert _queue_and_call_stock(s.workspace_id) == before
    _queued_without_sdk(child_id)


def test_consumer_runs_real_adopted_parallel_graph_without_manual_job_dispatch(parallel_graph_seed):
    s = _parallel_graph_task(parallel_graph_seed)
    stock = _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id])
    untouched = _task_stock(s.workspace_id, [s.task_id])
    assert _step(s, family='compact')['processed'] is False
    assert _task_stock(s.workspace_id, [s.task_id]) == untouched
    final, results = _advance(s, family='graph')
    assert final['completed'] and final['status'] == 'workflow_completed'
    assert sum(result['processed'] for result in results) == 3
    assert not any(result['errors'] for result in results)
    for node in ('a', 'b', 'c'):
        _one_sdk(_graph_job(final, node))
    assert _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id]) == stock


def test_scope_keeps_other_task_instance_family_and_nonworkflow_queue_untouched(paired_compact_scene, request):
    s = paired_compact_scene
    other_task, _ = _new_task(s.client, s.release)
    other_instance_task = s.foreign_task
    graph = _parallel_graph_task(request.getfixturevalue('parallel_graph_seed'))
    scope = _created_task(s.workspace_id, s.task_id)
    with Session(engine()) as db:
        # Negative admission probes, never successful paid/device/installation
        # fixtures. Any attempted dispatch would change this exact inventory.
        for kind in ('execute_plan', 'cloud_budget', 'dispatch_acquisition', 'install_specialist', 'process_observation'):
            db.add(m.Job(workspace_id=s.workspace_id, kind=kind, status='queued',
                idempotency_key='v265-excluded-' + uuid4().hex,
                data={'actor_id': scope.actor_id, 'negative_inert_scope_probe_v265': True}))
        db.add(m.Job(workspace_id=s.workspace_id, kind='execute_full_semantic_graph_node', status='waiting_service',
            idempotency_key='v265-excluded-service-' + uuid4().hex,
            data={'actor_id': scope.actor_id, 'task_id': graph.task_id, 'negative_inert_scope_probe_v265': True}))
        db.commit()
    others = [other_task['id'], other_instance_task['id'], graph.task_id]
    task_stock = _task_stock(s.workspace_id, others)
    stock = _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id])
    final, _ = _advance(s)
    assert len(final['workflow_runtime']['receipts']) == 2
    assert _task_stock(s.workspace_id, others) == task_stock
    assert _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id]) == stock


def test_instance_scope_cycles_real_tasks_and_keeps_other_instance_unclaimed(paired_compact_scene):
    s = paired_compact_scene
    second, _ = _new_task(s.client, s.release)
    foreign = s.foreign_task
    selected = [s.task_id, second['id']]
    stock = _queue_and_call_stock(s.workspace_id, except_tasks=selected)
    foreign_stock = _task_stock(s.workspace_id, [foreign['id']])
    after = delivery_after = None
    results = []
    for _ in range(12):
        outcome = _step(s, task_id=None, after=after, delivery_after=delivery_after)
        results.append(outcome)
        after, delivery_after = outcome['after'], outcome['delivery_after']
        saved = [_task(s.client, identity) for identity in selected]
        if all(row['status'] == 'workflow_waiting' for row in saved):
            break
    assert all(row['status'] == 'workflow_waiting' for row in saved)
    assert sum(row['processed'] for row in results) == 4
    assert not any(row['errors'] for row in results)
    for task in saved:
        assert len(task['workflow_runtime']['receipts']) == 2
        for receipt in task['workflow_runtime']['receipts']:
            _one_sdk(receipt['request_id'])
    assert _task_stock(s.workspace_id, [foreign['id']]) == foreign_stock
    assert _queue_and_call_stock(s.workspace_id, except_tasks=selected) == stock
    _queued_without_sdk(foreign['workflow_runtime']['automatic']['job_id'])


def test_graph_consumer_never_polls_a_foreign_instance_waiting_service_probe(paired_compact_scene, request, monkeypatch):
    from generic_agents import workflow_service_poll_v195 as service_poll

    s = _parallel_graph_task(request.getfixturevalue('parallel_graph_seed'))
    foreign = paired_compact_scene.foreign_task
    foreign_scope = _created_task(s.workspace_id, foreign['id'])
    assert foreign_scope.instance_id != _created_task(s.workspace_id, s.task_id).instance_id
    with Session(engine()) as db:
        # This is only an inert negative selector probe. It supplies no real
        # waiting-service authority and cannot stand in for a service success.
        probe = m.Job(workspace_id=s.workspace_id, kind='execute_full_semantic_graph_node',
            status='waiting_service', idempotency_key='v265-foreign-service-' + uuid4().hex,
            data={'task_id': foreign['id'], 'actor_id': foreign_scope.actor_id,
                'negative_inert_scope_probe_v265': True})
        db.add(probe)
        db.commit()
    before = _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id])
    original = service_poll.schedule_once
    polled = []

    def observed(*args, **kwargs):
        polled.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(service_poll, 'schedule_once', observed)
    final, _ = _advance(s, family='graph')
    assert final['completed'] and not polled
    assert _queue_and_call_stock(s.workspace_id, except_tasks=[s.task_id]) == before


@contextmanager
def _held_receiver(monkeypatch):
    """Delay delivery only; release always invokes the actual receiver."""
    original = worker.deliver_events
    held = ThreadEvent()
    held.set()
    calls = []

    def delayed(*args, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        if held.is_set():
            return  # Deliberately no receiver call, ACK, Memory or success result.
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(worker, 'deliver_events', delayed)
        yield SimpleNamespace(release=held.clear, calls=calls, receiver=original)


def _pixel_task(s):
    meta = configured_flow(s)
    task, entity = create_task(s, meta.release)
    return SimpleNamespace(client=s.client, workspace_id=s.workspace_id, instance_id=s.instance_id,
        task_id=task['id'], release=meta.release, meta=meta, entity=entity,
        parent_id=task['workflow_runtime']['automatic']['job_id'])


def _pixel_entry(s):
    return good(s.client.get('/api/workspace/tasks/' + s.task_id + '/pixel-results'))['records'][0]


@native_pytestmark
def test_delivery_cursor_drains_perception_to_human_without_any_queued_successor(flow_scene):
    s = flow_scene
    before_task = _task_stock(s.workspace_id, [s.task_id])
    before_calls = _queue_and_call_stock(s.workspace_id)
    assert _task(s.client, s.task_id)['status'] == 'workflow_waiting'
    original = _pixel_entry(s)
    assert original['reception']['state'] == 'waiting_data'
    result = _step(s, task_id=None)
    assert result['processed'] is False and result['delivered'] == 1
    assert result['delivery_after'] is not None
    assert _pixel_entry(s)['reception']['state'] == 'received'
    after_ack = _queue_and_call_stock(s.workspace_id)
    _only_original_ack_refresh_added(s, before_calls, after_ack, original['reception']['observation_id'])
    replay = _step(s, task_id=None, after=result['after'], delivery_after=result['delivery_after'])
    assert replay['processed'] is False and replay['delivered'] == 0
    assert _task_stock(s.workspace_id, [s.task_id]) == before_task
    assert _queue_and_call_stock(s.workspace_id) == after_ack


@native_pytestmark
def test_stopped_creator_does_not_discard_other_actual_issuers_pending_native_ack(flow_scene, monkeypatch):
    source = flow_scene
    c = configuration(); flow = c['flows']['资料核验']; first, human = flow['steps']
    first.update(name='确认后计算两图候选', agent='多模感知智能体', role='', nextId=human['id'],
        execution={'capability_id': source.registered['capability']['id'],
            'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    c['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '执行前确认'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, checked = prepared(source.client, c, instance_id=source.instance_id)
    assert release['available_business_ids'], checked
    enable(source.client, release)
    task, _ = create_task(source, release)
    creator = _created_task(source.workspace_id, task['id']).actor_id
    username, password = 'v265_native_issuer_' + uuid4().hex[:12], uuid4().hex
    issuer = good(source.client.post('/api/users', json={
        'username': username, 'password': password, 'role': 'admin'}), 201)
    assert issuer['id'] != creator
    with TestClient(source.client.app) as runner:
        login = good(runner.post('/api/login', json={'username': username, 'password': password}))
        runner.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': source.workspace_id})
        queued = good(runner.post('/api/workspace/tasks/' + task['id'] + '/run-ability', json={
            'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
            'request_id': str(uuid4()), 'retry': False}))
        s = SimpleNamespace(client=runner, workspace_id=source.workspace_id, release=release, task_id=task['id'])
        job_id = queued['workflow_runtime']['automatic']['job_id']
        with _held_receiver(monkeypatch) as receiver:
            assert _step(s)['processed']
            entry = _pixel_entry(s)
            assert entry['reception']['state'] == 'waiting_data'
            with Session(engine()) as db:
                run = db.get(m.WorkflowPixelRun, entry['run']['id'])
                observation = db.get(m.Observation, entry['reception']['observation_id'])
                assert run.owner_id == observation.data['submitted_by'] == issuer['id']
                assert db.get(m.Job, job_id).data['actor_id'] == issuer['id']
                call_id = run.invocation_id
                original_call = copy.deepcopy(db.get(m.Invocation, call_id).data)
                db.get(m.User, creator).active = False  # Actual personnel stop after legitimate production.
                db.commit()
            receiver.release()
            outcome = _step(s)
            assert not outcome['processed'] and outcome['delivered'] == 1 and not outcome['errors']
        assert _pixel_entry(s)['reception']['state'] == 'received'
        for _ in range(2):
            assert not _step(s)['processed']
        _one_sdk(job_id)
        with Session(engine()) as db:
            assert not db.get(m.User, creator).active
            assert db.get(m.Invocation, call_id).data == original_call
            assert db.get(m.Task, s.task_id).owner_id == creator


@native_pytestmark
def test_delayed_real_pixel_ack_keeps_status_job_at_zero_then_consumer_continues(flow_scene, monkeypatch):
    s = _pixel_task(flow_scene)
    other = _task_stock(s.workspace_id, [flow_scene.task_id])
    with _held_receiver(monkeypatch) as receiver:
        assert _step(s)['processed']
        saved = _task(s.client, s.task_id)
        child_id = saved['workflow_runtime']['automatic']['job_id']
        assert saved['workflow_runtime']['current_step_id'] == s.meta.data['id']
        entry = _pixel_entry(s)
        assert entry['reception']['state'] == 'waiting_data'
        for _ in range(3):
            result = _step(s)
            assert result['processed'] is False
            _wait(result, child_id, 'waiting_data')
            _queued_without_sdk(child_id)
        progress = _task(s.client, s.task_id)['automatic_worker']
        assert progress['state'] == progress['job_status'] == 'queued'
        assert progress['reason'] == consumer.ACK_WAIT and progress['processor']['state'] == 'unknown'
        receiver.release()
        final, results = _advance(s)
        assert sum(value['delivered'] for value in results) >= 1
        assert receiver.calls and all(value.get('workspace_id') == s.workspace_id
            and value.get('event_kind') == 'observation.data'
            and value.get('observation_id') == entry['reception']['observation_id'] for value in receiver.calls)
    received = _pixel_entry(s)
    assert received['reception']['state'] == 'received' and received['reception']['memory_id']
    data = next(row for row in final['workflow_runtime']['receipts'] if row['step_id'] == s.meta.data['id'])
    business = next(row for row in final['workflow_runtime']['receipts'] if row['step_id'] == s.meta.business['id'])
    assert data['output']['registered_count'] == data['output']['received_count'] == 1
    assert data['output']['waiting_count'] == 0 and data['output']['expected_sources_verified'] is False
    assert business['input_snapshot'] == {'attributes': data['output']}
    _one_sdk(child_id)
    assert _task_stock(s.workspace_id, [flow_scene.task_id]) == other
    _finish_human(s, final)


@native_pytestmark
def test_preexisting_successful_waiting_sdk_is_not_reissued_or_rewritten_by_consumer(flow_scene):
    s = _pixel_task(flow_scene)
    # Historical pre-consumer state is made by the ordinary frozen worker and
    # actual SDK. It is not a fabricated successful Invocation or ACK.
    assert worker.process_once(s.parent_id, deliver=False, workspace_id=s.workspace_id)
    saved = _task(s.client, s.task_id)
    data_id = saved['workflow_runtime']['automatic']['job_id']
    assert worker.process_once(data_id, deliver=False, workspace_id=s.workspace_id)
    data_call, waiting = _one_sdk(data_id)
    assert waiting['waiting_count'] == 1 and waiting['received_count'] == 0
    saved = _task(s.client, s.task_id)
    business_id = saved['workflow_runtime']['automatic']['job_id']
    final, _ = _advance(s)
    assert _pixel_entry(s)['reception']['state'] == 'received'
    business = next(row for row in final['workflow_runtime']['receipts'] if row['step_id'] == s.meta.business['id'])
    assert business['input_snapshot'] == {'attributes': waiting}
    assert business['output']['attributes'] == waiting
    assert _one_sdk(data_id) == (data_call, waiting)
    _one_sdk(business_id)
    with Session(engine()) as db:
        assert db.get(m.Invocation, data_call).data['output'] == waiting
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id,
            m.Job.data['task_id'].as_string() == s.task_id)))
        assert len(jobs) == 3
    _finish_human(s, final)


@native_pytestmark
@pytest.mark.parametrize('damage', ['independent_memory', 'input_audit'])
def test_damaged_pixel_ack_or_source_stays_explicitly_blocked_and_unrepaired(flow_scene, monkeypatch, damage):
    s = _pixel_task(flow_scene)
    with _held_receiver(monkeypatch):
        assert _step(s)['processed']
    saved = _task(s.client, s.task_id)
    data_id = saved['workflow_runtime']['automatic']['job_id']
    entry = _pixel_entry(s)
    # Establish a real independent ACK before a disposable negative corruption.
    worker.deliver_events(workspace_id=s.workspace_id, observation_id=entry['reception']['observation_id'],
        event_kind='observation.data')
    entry = _pixel_entry(s)
    assert entry['reception']['state'] == 'received'
    with Session(engine()) as db:
        run = db.get(m.WorkflowPixelRun, entry['run']['id'])
        removed_id = entry['reception']['memory_id'] if damage == 'independent_memory' else run.data['input_audit_id']
        removed_class = m.Memory if damage == 'independent_memory' else m.Audit
        db.delete(db.get(removed_class, removed_id))
        db.commit()
        observation = db.get(m.Observation, entry['reception']['observation_id'])
        delivered = db.get(m.Event, entry['reception']['event_id'])
        observation_pin, event_pin = _row_pin(observation), _row_pin(delivered)
    stock = _queue_and_call_stock(s.workspace_id)
    for _ in range(3):
        result = _step(s)
        assert result['processed'] is False
        _wait(result, data_id, 'blocked_integrity')
        _queued_without_sdk(data_id)
    assert _queue_and_call_stock(s.workspace_id) == stock
    with Session(engine()) as db:
        assert db.get(removed_class, removed_id) is None
        assert _row_pin(db.get(m.Observation, entry['reception']['observation_id'])) == observation_pin
        assert _row_pin(db.get(m.Event, entry['reception']['event_id'])) == event_pin


def test_true_empty_receipt_status_is_zero_and_does_not_wait_for_an_invented_ack(client, account):
    capability = publish(client, pixel_status.manifest().model_dump(mode='json'))
    c = configuration()
    flow = c['flows']['资料核验']
    first = flow['steps'][0]
    first.update(agent='数据管理智能体', role='', name='核查当次实际登记成果',
        execution={'capability_id': capability['id'], 'automation_action': 'data', 'arguments': {}, 'bindings': {}})
    c['automation']['policies']['资料核验']['rules']['data'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    release, _, checked = prepared(client, c)
    assert release['available_business_ids'], checked
    enable(client, release)
    task, _ = _new_task(client, release)
    s = SimpleNamespace(client=client, workspace_id=account[2], task_id=task['id'], release=release)
    final, results = _advance(s)
    assert sum(value['processed'] for value in results) == 1
    assert not any(value['waiting'] or value['errors'] for value in results)
    output = final['workflow_runtime']['receipts'][0]['output']
    assert output['registered_count'] == output['received_count'] == output['waiting_count'] == output['blocked_count'] == 0
    assert output['all_registered_received'] is False and output['expected_sources_verified'] is False
    _one_sdk(task['workflow_runtime']['automatic']['job_id'])
    _finish_human(s, final)


def test_real_sound_consumer_drains_only_the_selected_task_ack_then_runs_status(sound_scene, account, monkeypatch):
    source = sound_scene
    meta = configured_sound_flow(source)
    entity = object_row(source.client, meta.release)
    task = good(source.client.post('/api/workspace/instances/' + meta.release['instance_id'] + '/tasks', json={
        'release_id': meta.release['id'], 'business_id': meta.release['available_business_ids'][0],
        'entity_ids': [entity['id']], 'sound_ids': [source.attached['id']],
        'title': '真实声音收件与自然后台续调工程验收', 'request_id': str(uuid4())}), 201)
    s = SimpleNamespace(client=source.client, workspace_id=account[2], task_id=task['id'], release=meta.release)
    other = _task_stock(s.workspace_id, [source.task['id']])
    with _held_receiver(monkeypatch) as receiver:
        assert _step(s)['processed']
        saved = _task(s.client, s.task_id)
        data_id = saved['workflow_runtime']['automatic']['job_id']
        entry = good(s.client.get('/api/workspace/tasks/' + s.task_id + '/sounds'))['records'][0]
        assert entry['reception']['state'] == 'waiting_data'
        for _ in range(2):
            result = _step(s)
            assert result['processed'] is False
            _wait(result, data_id, 'waiting_data')
            _queued_without_sdk(data_id)
        receiver.release()
        final, results = _advance(s)
        assert sum(value['delivered'] for value in results) >= 1
        assert receiver.calls and all(value.get('workspace_id') == s.workspace_id
            and value.get('event_kind') == 'observation.data'
            and value.get('observation_id') == entry['reception']['observation_id'] for value in receiver.calls)
    received = good(s.client.get('/api/workspace/tasks/' + s.task_id + '/sounds'))['records'][0]
    assert received['reception']['state'] == 'received' and received['reception']['memory_id']
    data = next(row for row in final['workflow_runtime']['receipts'] if row['step_id'] == meta.data['id'])
    assert data['output']['registered_count'] == data['output']['received_count'] == 1
    assert data['output']['expected_sources_verified'] is False
    _one_sdk(data_id)
    assert _task_stock(s.workspace_id, [source.task['id']]) == other
    _finish_human(s, final)


@pytest.fixture
def registered_http_scene(client, account, monkeypatch):
    """A real registered HTTP adapter and a loopback synthetic provider only."""
    token = uuid4().hex
    calls = []
    locked = Lock()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/invoke' or self.headers.get('Authorization') != 'Bearer ' + token:
                self.send_error(403)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 * 1024:
                self.send_error(422)
                return
            body = json.loads(self.rfile.read(size))
            values = [row['attributes'][body['field']] for row in body['records']
                if type(row.get('attributes', {}).get(body['field'])) in {int, float}]
            operation = body['operation']
            value = len(values) if operation == 'count' else (
                sum(values) if operation == 'sum' else sum(values) / len(values) if values else None)
            result = {'value': value, 'sample_count': len(values),
                'provenance': [{'entity_id': row['entity_id'], 'field': body['field']}
                    for row in body['records']]}
            with locked:
                calls.append({'key': self.headers.get('Idempotency-Key'), 'input': copy.deepcopy(body),
                    'output': copy.deepcopy(result)})
            encoded = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    endpoint = 'http://127.0.0.1:' + str(server.server_port) + '/invoke'
    monkeypatch.setattr(settings(), 'development_connector_urls', endpoint)
    monkeypatch.setenv('GA_CONNECTOR_SECRET_WORKFLOW_CONSUMER_V265', token)
    thread.start()
    try:
        capability = publish(client, manifest('tool', namespace='workflow_consumer_engineering_v265',
            name='public_http_calculation', description='合成HTTP服务核对当次登记数量',
            allowed_agent_roles=['data'], specification={},
            input_schema={'type': 'object', 'required': ['records', 'field', 'operation'],
                'additionalProperties': False, 'properties': {
                    'records': {'type': 'array', 'items': {'type': 'object'}},
                    'field': {'type': 'string'}, 'operation': {'enum': ['mean', 'sum', 'count']}}},
            output_schema={'type': 'object', 'required': ['value', 'sample_count', 'provenance'],
                'properties': {'value': {'type': ['number', 'null']}, 'sample_count': {'type': 'integer'},
                    'provenance': {'type': 'array'}}},
            execution={'adapter': 'http', 'endpoint': endpoint,
                'secret_ref': 'GA_CONNECTOR_SECRET_WORKFLOW_CONSUMER_V265',
                'side_effect': False, 'input_bindings': {}},
            data_policy={'cloud_allowed': False, 'external_allowed': True}))
        # Explicit normal authoring consent is separate from the registered
        # adapter's policy. The generic builtin fixture has no external grant.
        from tests.test_workflow_abilities import conf
        c = conf(capability); flow = c['flows']['资料核验']
        first, human = flow['steps']
        first['execution']['allow_external'] = True
        second = copy.deepcopy(first)
        first['nextId'] = 'second_calculation'
        second.update(id='second_calculation', name='复核登记数量', output='数量复核结果', nextId=human['id'])
        second['execution']['arguments']['operation'] = 'count'
        flow['steps'] = [first, second, human]
        flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
        release, _, checked = prepared(client, c)
        assert release['available_business_ids'], checked
        enable(client, release)
        task, entity = _new_task(client, release)
        yield SimpleNamespace(client=client, workspace_id=account[2], instance_id=release['instance_id'],
            release=release, task_id=task['id'], parent_id=task['workflow_runtime']['automatic']['job_id'],
            capability=capability, entity=entity, calls=calls, endpoint=endpoint)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
