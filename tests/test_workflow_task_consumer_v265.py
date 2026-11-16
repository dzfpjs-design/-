"""Pure entry/selection contracts; no Task, SDK, ACK or model success is made.

Root runs the separate PG suite for real authority, native receipt and worker
journeys. These tests replace only unused IO/entry functions with failures or
recorders so scope omissions cannot silently select the global scheduler.
"""
import sys
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from generic_agents import cli
from generic_agents import workflow_service_poll_v195 as service_poll
from generic_agents import workflow_task_consumer_v265 as consumer


WORKSPACE = '1a0bc001-a462-42da-b822-a89e541bdc8d'
INSTANCE = '2a0bc001-a462-42da-b822-a89e541bdc8d'
TASK = '3a0bc001-a462-42da-b822-a89e541bdc8d'
JOB = '4a0bc001-a462-42da-b822-a89e541bdc8d'


@pytest.fixture(autouse=True)
def forbid_io(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('This entry contract must not use a real database or scheduler')
    for module in (cli, consumer, service_poll):
        monkeypatch.setattr(module, 'engine', denied)
        monkeypatch.setattr(module, 'Session', denied)


@pytest.mark.parametrize('args', [(), ('--workspace-id', WORKSPACE),
    ('--workspace-id', WORKSPACE, '--instance-id', INSTANCE),
    ('--instance-id', INSTANCE, '--family', 'compact')])
def test_cli_requires_every_scope_before_any_worker_or_database(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['ga', 'workflow-task-worker', *args])
    monkeypatch.setattr(consumer, 'run', lambda *a, **k: pytest.fail('unscoped workflow entry'))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


@pytest.mark.parametrize('family,task_id', [('compact', None), ('graph', TASK)])
def test_cli_passes_exact_scope_without_global_or_setup_fallback(monkeypatch, family, task_id):
    calls = []
    arguments = ['ga', 'workflow-task-worker', '--workspace-id', WORKSPACE,
        '--instance-id', INSTANCE, '--family', family]
    if task_id is not None: arguments += ['--task-id', task_id]
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(consumer, 'run', lambda *a, **k: calls.append((a, k)) or 0)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert calls == [((WORKSPACE, INSTANCE), {'family': family, 'task_id': task_id})]


@pytest.mark.parametrize('command,args', [
    ('worker', ['--instance-id', INSTANCE]),
    ('setup-worker', ['--workspace-id', WORKSPACE, '--family', 'compact']),
    ('workflow-task-worker', ['--workspace-id', WORKSPACE, '--instance-id', INSTANCE,
        '--family', 'compact', '--subscription-id', TASK]),
])
def test_new_scope_flags_cannot_change_another_worker_entry(monkeypatch, command, args):
    monkeypatch.setattr(sys, 'argv', ['ga', command, *args])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


@pytest.mark.parametrize('patch', [
    {'workspace_id': None}, {'instance_id': INSTANCE.upper()}, {'family': 'all'},
    {'task_id': TASK.replace('-', '')}, {'limit': True},
])
def test_direct_step_has_no_global_or_coerced_scope(patch):
    arguments = dict(workspace_id=WORKSPACE, instance_id=INSTANCE, family='compact')
    arguments.update(patch)
    with pytest.raises(HTTPException):
        consumer.step(**arguments)


def test_preexisting_stop_requires_no_io_and_no_signal_install_in_worker_thread(monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(consumer.signal, 'signal', lambda *a: pytest.fail('worker thread installed a signal'))
    assert consumer.step(WORKSPACE, INSTANCE, family='compact', stop_event=stop) == {
        'processed': False, 'after': None, 'delivery_after': None,
        'waiting': [], 'errors': [], 'delivered': 0}
    results = []; errors = []
    def background():
        try: results.append(consumer.run(WORKSPACE, INSTANCE, family='compact', stop_event=stop))
        except BaseException as error: errors.append(error)
    thread = Thread(target=background)
    thread.start(); thread.join(timeout=5)
    assert not thread.is_alive() and not errors and results == [0]


def test_unrelated_goal_job_keeps_its_existing_readonly_status_reason():
    # This is an early non-workflow guard, not a fabricated workflow proof.
    assert consumer.admission_reason(None, None, None,
        SimpleNamespace(kind='check_native_business_goal', status='queued')) is None


def _display_session(*, isolation='repeatable read', readonly='on', transaction=True, dirty=False, new=False, deleted=False):
    """Only display dispatch is tested here, not successful source authority."""
    queries = []
    def scalar(statement):
        query = str(statement); queries.append(query)
        return {'SHOW transaction_isolation': isolation, 'SHOW transaction_read_only': readonly}[query]
    return SimpleNamespace(new={1} if new else set(), dirty={1} if dirty else set(),
        deleted={1} if deleted else set(), in_transaction=lambda: transaction, scalar=scalar), queries


@pytest.mark.parametrize('options', [
    {'readonly': 'off'}, {'isolation': 'read committed'}, {'transaction': False},
    {'dirty': True}, {'new': True}, {'deleted': True},
])
def test_writer_or_dirty_display_retains_original_reason_without_lookup_or_reader(monkeypatch, options):
    db, queries = _display_session(**options)
    def forbidden(*args, **kwargs): pytest.fail('non-RRRO display attempted a source lookup or another reader')
    monkeypatch.setattr(consumer, '_prove', forbidden)
    monkeypatch.setattr(consumer, '_readiness', forbidden)
    monkeypatch.setattr(consumer, '_reader', forbidden)
    assert consumer.admission_reason(db, None, None,
        SimpleNamespace(kind='execute_workflow_step', status='queued')) is None
    assert all(query.startswith('SHOW transaction_') for query in queries)


def test_clean_rr_ro_display_reuses_the_exact_caller_without_independent_snapshot(monkeypatch):
    from generic_agents import media_daily_task_adapter as actor_reader
    db, queries = _display_session()
    p = SimpleNamespace(workspace_id=WORKSPACE)
    task = SimpleNamespace(id=TASK, workspace_id=WORKSPACE, data={'instance_id': INSTANCE})
    job = SimpleNamespace(id=JOB, kind='execute_workflow_step', status='queued')
    seen = []
    def forbidden(*args, **kwargs): pytest.fail('display rented another connection or scheduler snapshot')
    monkeypatch.setattr(consumer, '_reader', forbidden)
    monkeypatch.setattr(consumer, '_readiness', forbidden)
    monkeypatch.setattr(actor_reader, 'current_actor', lambda actual, principal:
        seen.append(('actor', actual is db, principal is p)))
    def prove(actual, workspace, instance, family, job_id, task_id):
        assert actual is db and (workspace, instance, family, job_id, task_id) == (WORKSPACE, INSTANCE, 'compact', JOB, TASK)
        seen.append(('proof', True)); return p, task, None, {'saved': True}, job
    def readiness(actual, principal, saved_task, saved_job, business):
        assert actual is db and principal is p and saved_task is task and saved_job is job
        assert business == {'saved': True}
        seen.append(('readiness', True))
        return {'state': 'waiting_producer', 'reason': consumer.PRODUCER_WAIT}
    monkeypatch.setattr(consumer, '_prove', prove)
    monkeypatch.setattr(consumer, 'readiness', readiness)
    assert consumer.admission_reason(db, p, task, job) == consumer.PRODUCER_WAIT
    assert queries == ['SHOW transaction_isolation', 'SHOW transaction_read_only']
    assert seen == [('actor', True, True), ('proof', True), ('readiness', True)]


@pytest.mark.parametrize('workspace,job', [(None, JOB), (WORKSPACE, JOB.upper()),
    (WORKSPACE, 'not-a-job'), (WORKSPACE, True)])
def test_targeted_service_poll_refuses_missing_scope_before_io(workspace, job):
    with pytest.raises(ValueError):
        service_poll.schedule_once(workspace, job_id=job)


@pytest.mark.parametrize('target', [None, JOB])
def test_service_poll_default_selector_and_exact_target_share_original_waiting_query(monkeypatch, target):
    queries = []; commits = []
    class EmptySession:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalars(self, query): queries.append(query); return []
        def commit(self): commits.append(True)
    monkeypatch.setattr(service_poll, 'engine', lambda: object())
    monkeypatch.setattr(service_poll, 'Session', lambda *a: EmptySession())
    if target is None:
        assert service_poll.schedule_once() == 0
    else:
        assert service_poll.schedule_once(WORKSPACE, job_id=target) == 0
    assert commits == [True] and len(queries) == 1
    statement = queries[0].compile(dialect=postgresql.dialect())
    values = list(statement.params.values())
    assert 'execute_full_semantic_graph_node' in values and 'waiting_service' in values
    assert 'clock_timestamp()' in str(statement) and 'LIMIT' in str(statement)
    assert (WORKSPACE in values) is (target is not None)
    assert (JOB in values) is (target is not None)
    # No candidate was returned; these assertions grant no waiting-service
    # execution/receipt authority and do not replace its actual Task/Job gates.
