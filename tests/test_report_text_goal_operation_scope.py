"""Actual operation entry points and inert DOCX reuse at explicit IO seams.

The real public routes/helpers/decorators and DOCX parser execute. Authority,
material reads, goal operations and transaction commits are deliberately counted
spies: this checks cache boundaries, never IAM, PostgreSQL or real Job success.
No generic_agents.api, private report or PostgreSQL fixture is imported.
"""
import inspect
import socket
import subprocess
from collections import Counter
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from generic_agents import workspace_api as workspace
from generic_agents import native_goals
from generic_agents import native_goal_time
from generic_agents import layered_approval_store_v175 as approvals
from generic_agents import signal_dual_handoffs
from generic_agents import workflow_graph
from generic_agents import native_report_reading as reading
from generic_agents import report_text_parse_scope as scopes
from generic_agents.contracts import digest
from tests.test_report_text_parse_scope import doc


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Pure operation-scope tests cannot connect or spawn a process')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


@pytest.fixture
def parsed(monkeypatch):
    actual = reading._read_docx_uncached
    calls = []
    def counted(content):
        calls.append(content)
        return actual(content)
    monkeypatch.setattr(reading, '_read_docx_uncached', counted)
    return calls


@pytest.fixture
def io(parsed):
    value = NS(content=doc('同一操作保留完整正文，不代表授权通过'), calls=[], parsed=parsed,
        fail_at=None, replacement=None)
    def touch(label):
        # These are separate nested consumers, matching the existing release /
        # material scopes. Without the operation's REAL outer scope they would
        # each parse independently and expire before their sibling runs.
        value.calls.append(label)
        with scopes.report_text_parse_scope():
            result = reading.read_docx(value.content)
        assert result['can_apply'] is False
        if value.fail_at == label:
            raise HTTPException(409, 'synthetic source or authority boundary failure')
        return result
    def commit():
        touch('commit')
        if value.replacement is not None:
            value.content = value.replacement
    value.touch = touch
    value.db = NS(commit=commit)
    value.p = NS(user_id='scope-reader', workspace_id='scope-workspace', role='operator')
    value.task = NS(id='scope-task', data={}, status='workflow_completed')
    value.release = NS(data={'snapshot': {'roles': []}})
    value.business = {'execution_mode': 'confirmed_graph_workflow'}
    return value


def install_routes(monkeypatch, c):
    def saved(db, p, identity, *args):
        assert db is c.db and p is c.p and identity == c.task.id
        c.touch('saved_execution')
        return c.task, c.release, c.business
    def execution(db, p, identity, lock=False, **kwargs):
        assert lock is True
        c.touch('execution_guard')
        return saved(db, p, identity)
    def detail(db, p, task, **kwargs):
        assert db is c.db and p is c.p and task is c.task
        c.touch('approval_detail')
        return {'execution': {'initialized': True}}
    def public(task, release, business, p, *, db):
        assert (task, release, business, p, db) == (c.task, c.release, c.business, c.p, c.db)
        c.touch('graph_public_view')
        return {'inert_public_result': True}
    def view(db, p, task):
        assert db is c.db and p is c.p and task is c.task
        return {'source_text': c.touch('goal_view')['blocks'][0]['text']}
    def request_check(db, p, task, body):
        assert db is c.db and p is c.p and task is c.task and body is c.body
        c.touch('request_check_guard')
    c.body = native_goals.GoalCommand(request_id='public_scope_check_0001', expected_version=1,
        reason='公开合成正文的纯解析作用域检查')
    monkeypatch.setattr(workspace, 'saved_execution', saved)
    monkeypatch.setattr(workspace, 'execution', execution)
    monkeypatch.setattr(approvals, 'detail', detail)
    monkeypatch.setattr(workflow_graph, 'public_view', public)
    monkeypatch.setattr(native_goals, 'view', view)
    monkeypatch.setattr(native_goals, 'request_check', request_check)


@pytest.mark.parametrize('name,method,target,parameters,annotations', [
    ('detail', 'GET', '/api/workspace/tasks/{identity}', ['identity', 'p', 'db'],
        {'identity': str, 'p': workspace.P, 'db': workspace.TaskReadDB}),
    ('native_goal', 'GET', '/api/workspace/tasks/{identity}/goal', ['identity', 'p', 'db'],
        {'identity': str, 'p': workspace.P, 'db': workspace.DB}),
    ('check_native_goal', 'POST', '/api/workspace/tasks/{identity}/goal/check', ['identity', 'body', 'p', 'db'],
        {'identity': str, 'body': native_goals.GoalCommand, 'p': workspace.P, 'db': workspace.DB}),
])
def test_real_router_keeps_original_public_endpoint_and_typed_signature(name, method, target, parameters, annotations):
    endpoint = getattr(workspace, name)
    # The exported router lazily includes this REAL original public router;
    # iterating only the outer router misses its registered APIRoutes on the
    # installed FastAPI version. No reconstructed endpoint/router is used.
    registered = [route for route in workspace.workspace_routes.routes if isinstance(route, APIRoute)
        and route.path == target and method in route.methods]
    assert len(registered) == 1 and registered[0].endpoint is endpoint
    signature = inspect.signature(endpoint)
    assert list(signature.parameters) == parameters
    for key, annotation in annotations.items():
        assert signature.parameters[key].annotation is annotation
    assert not inspect.iscoroutinefunction(endpoint)
    assert registered[0].dependant.call is endpoint
    if name == 'check_native_goal':
        fields = registered[0].dependant.body_params
        assert len(fields) == 1 and fields[0].name == 'body'
        assert fields[0].field_info.annotation is native_goals.GoalCommand


@pytest.mark.parametrize('name,expected_calls', [
    ('detail', ['saved_execution', 'approval_detail', 'graph_public_view']),
    ('native_goal', ['saved_execution', 'goal_view']),
    ('check_native_goal', ['execution_guard', 'saved_execution', 'request_check_guard', 'commit', 'goal_view']),
])
def test_each_real_public_operation_parses_once_but_repeats_every_guard_read_and_commit(monkeypatch, io, name, expected_calls):
    c = io
    install_routes(monkeypatch, c)
    endpoint = getattr(workspace, name)
    args = (c.task.id, c.body, c.p, c.db) if name == 'check_native_goal' else (c.task.id, c.p, c.db)
    first = endpoint(*args)
    assert c.calls == expected_calls and c.parsed == [c.content]
    assert scopes._active() is None
    second = endpoint(*args)
    assert second == first
    assert c.calls == expected_calls * 2 and c.parsed == [c.content, c.content]
    assert scopes._active() is None


def test_goal_check_reloads_changed_bytes_after_commit_without_caching_a_source_decision(monkeypatch, io):
    c = io
    install_routes(monkeypatch, c)
    old = c.content
    c.replacement = doc('提交后原件已变化，按新字节重算，不能沿用旧校验')
    result = workspace.check_native_goal(c.task.id, c.body, c.p, c.db)
    assert result['source_text'] == '提交后原件已变化，按新字节重算，不能沿用旧校验'
    assert c.parsed == [old, c.replacement]
    assert c.calls == ['execution_guard', 'saved_execution', 'request_check_guard', 'commit', 'goal_view']
    assert scopes._active() is None


def test_route_exception_expires_scope_and_next_operation_still_checks_authority(monkeypatch, io):
    c = io
    install_routes(monkeypatch, c)
    c.fail_at = 'request_check_guard'
    with pytest.raises(HTTPException): workspace.check_native_goal(c.task.id, c.body, c.p, c.db)
    assert c.calls == ['execution_guard', 'saved_execution', 'request_check_guard']
    assert c.parsed == [c.content] and scopes._active() is None
    c.fail_at = None
    workspace.check_native_goal(c.task.id, c.body, c.p, c.db)
    assert c.parsed == [c.content, c.content]
    assert c.calls.count('execution_guard') == c.calls.count('request_check_guard') == 2
    assert c.calls.count('commit') == 1 and scopes._active() is None


@pytest.mark.parametrize('owner,name,parameters', [
    (native_goals, 'process', ['db', 'p', 'job']),
    (approvals, 'perform', ['db', 'p', 'task_id', 'operation', 'body', 'raw', 'sha']),
    (approvals, 'detail', ['db', 'p', 'task', 'with_basis']),
    (approvals, 'outcome', ['db', 'p', 'task', 'row', 'lock_sources']),
])
def test_sync_decorators_preserve_callable_signature(owner, name, parameters):
    endpoint = getattr(owner, name)
    assert list(inspect.signature(endpoint).parameters) == parameters
    assert inspect.signature(endpoint) == inspect.signature(endpoint.__wrapped__)
    assert not inspect.iscoroutinefunction(endpoint)


def install_worker(monkeypatch, c):
    context = {'synthetic_frozen_context_boundary': True}
    job = NS(data={'task_id': c.task.id, 'context_hash': digest(context)})
    def require(p, *roles):
        assert p is c.p and roles == ('admin', 'operator')
        c.touch('worker_require')
    def scoped(db, model, identity, p, lock=False):
        assert db is c.db and p is c.p and model is native_goals.Task
        assert identity == c.task.id and lock is True
        c.touch('worker_task_read')
        return c.task
    def timeout(task):
        assert task is c.task
        c.touch('worker_timeout_read')
        return False
    def admitted(task, actual_job):
        assert task is c.task and actual_job is job
        c.touch('worker_timer_admission')
        return True
    def guard(db, p, task, **kwargs):
        assert db is c.db and p is c.p and task is c.task
        assert kwargs == {'allow_expired': True}
        c.touch('worker_live_guard')
        return context
    def expire(db, p, task):
        assert db is c.db and p is c.p and task is c.task
        c.touch('worker_expiry_boundary')
        # The normal expiry return deliberately stops before data-agent or
        # queue IO. This test is not an executed successful Goal/Worker.
        return True
    monkeypatch.setattr(native_goals, 'require', require)
    monkeypatch.setattr(native_goals, 'scoped', scoped)
    monkeypatch.setattr(native_goals, 'guard', guard)
    monkeypatch.setattr(native_goal_time, 'timeout', timeout)
    monkeypatch.setattr(native_goal_time, 'admitted', admitted)
    monkeypatch.setattr(native_goal_time, 'expire', expire)
    return job


def test_actual_worker_handler_scope_spans_distinct_iam_guard_and_source_reads(monkeypatch, io):
    c = io
    job = install_worker(monkeypatch, c)
    expected = ['worker_require', 'worker_task_read', 'worker_timeout_read',
        'worker_timer_admission', 'worker_live_guard', 'worker_expiry_boundary']
    for index in range(2):
        assert native_goals.process(c.db, c.p, job) is None
        assert c.calls == expected * (index + 1)
        assert c.parsed == [c.content] * (index + 1)
        assert scopes._active() is None


def test_worker_guard_failure_is_not_cached_or_changed_to_success(monkeypatch, io):
    c = io
    job = install_worker(monkeypatch, c)
    c.fail_at = 'worker_live_guard'
    for _ in range(2):
        with pytest.raises(HTTPException) as caught:
            native_goals.process(c.db, c.p, job)
        assert caught.value.status_code == 409 and scopes._active() is None
    assert len(c.parsed) == 2
    assert c.calls.count('worker_require') == c.calls.count('worker_live_guard') == 2
    assert 'worker_expiry_boundary' not in c.calls


def test_actual_approval_perform_caches_no_command_inventory_or_write_preconditions(monkeypatch, io):
    c = io
    body, raw, sha = NS(request_id='scope-request'), b'public synthetic original', 'a' * 64
    def prior(db, p, kind, identity, request_id, actual_raw, actual_sha):
        assert (db, p, kind, identity, request_id, actual_raw, actual_sha) == (
            c.db, c.p, 'task_submit', c.task.id, body.request_id, raw, sha)
        c.touch('command_inventory')
        return None
    def require(p, *roles):
        assert p is c.p and roles == ('admin', 'operator')
        c.touch('approval_require')
    def prelock(db, p, identity):
        assert db is c.db and p is c.p and identity == c.task.id
        c.touch('approval_task_lock')
    def scoped(db, model, identity, p, lock=False):
        assert db is c.db and p is c.p and model is approvals.Task and identity == c.task.id and lock is True
        c.touch('approval_task_read')
        return c.task
    def managed(db, p, task, *, lock=False):
        assert db is c.db and p is c.p and task is c.task and lock is True
        c.touch('managed_inventory')
        return None  # The real perform precondition must reject, never write.
    monkeypatch.setattr(approvals, 'prior', prior)
    monkeypatch.setattr(approvals, 'require', require)
    monkeypatch.setattr(signal_dual_handoffs, 'prelock_task', prelock)
    monkeypatch.setattr(approvals, 'scoped', scoped)
    monkeypatch.setattr(approvals, 'managed', managed)
    expected = ['command_inventory', 'approval_require', 'approval_task_lock',
        'approval_task_read', 'managed_inventory']
    for index in range(2):
        with pytest.raises(HTTPException) as caught:
            approvals.perform(c.db, c.p, c.task.id, 'submit', body, raw, sha)
        assert caught.value.status_code == 409
        assert c.calls == expected * (index + 1)
        assert c.parsed == [c.content] * (index + 1)
        assert scopes._active() is None


def test_actual_approval_detail_scopes_sibling_material_reads_without_retaining_inventory(monkeypatch, io):
    c = io
    def managed(db, p, task):
        assert db is c.db and p is c.p and task is c.task
        c.touch('managed_first_material_read')
        c.touch('managed_second_material_read')
        return None
    monkeypatch.setattr(approvals, 'managed', managed)
    for index in range(2):
        assert approvals.detail(c.db, c.p, c.task) is None
        assert Counter(c.calls) == Counter({'managed_first_material_read': index + 1,
            'managed_second_material_read': index + 1})
        assert c.parsed == [c.content] * (index + 1) and scopes._active() is None


def test_actual_approval_outcome_shares_parse_but_rechecks_guard_source_and_assessment(monkeypatch, io):
    c = io
    c.task.data = {'native_goal_context': {'synthetic_source_boundary': True},
        'workflow_runtime': {'receipts': []}}
    row = NS(id='scope-managed')
    def guard(db, p, task, *, lock_sources=True):
        assert db is c.db and p is c.p and task is c.task and lock_sources is False
        c.touch('outcome_approval_guard')
    def execution(db, p, identity, *, lock_sources=True):
        assert db is c.db and p is c.p and identity == c.task.id and lock_sources is False
        c.touch('outcome_release_and_source_read')
        return c.task, c.release, c.business
    def original_inputs(db, p, task, *, lock_sources=True):
        assert db is c.db and p is c.p and task is c.task and lock_sources is False
        c.touch('outcome_original_inputs')
    def assessment(db, p, task, *, accepted=False, lock_sources=True):
        assert db is c.db and p is c.p and task is c.task and accepted is True and lock_sources is False
        c.touch('outcome_goal_assessment')
        return None, {'completed': True, 'synthetic_io_boundary_only': True}
    def row_hash(db, p, actual):
        assert db is c.db and p is c.p and actual is row
        c.touch('outcome_submission_read')
        return 'b' * 64
    def checked(actual):
        assert actual is row
        c.touch('outcome_record_inventory')
        return {'supplements': []}
    monkeypatch.setattr(approvals, 'guard', guard)
    monkeypatch.setattr(workspace, 'execution', execution)
    monkeypatch.setattr(native_goals, 'verify_acceptance_inputs', original_inputs)
    monkeypatch.setattr(native_goals, 'assessment', assessment)
    monkeypatch.setattr(approvals, 'row_hash', row_hash)
    monkeypatch.setattr(approvals, 'checked', checked)
    c.task.version = 8
    expected = ['outcome_approval_guard', 'outcome_release_and_source_read',
        'outcome_original_inputs', 'outcome_goal_assessment',
        'outcome_submission_read', 'outcome_record_inventory']
    for index in range(2):
        value = approvals.outcome(c.db, c.p, c.task, row, lock_sources=False)
        assert value['evaluation']['synthetic_io_boundary_only'] is True
        assert value['physical_state_verified'] is False
        assert c.calls == expected * (index + 1)
        assert c.parsed == [c.content] * (index + 1) and scopes._active() is None
