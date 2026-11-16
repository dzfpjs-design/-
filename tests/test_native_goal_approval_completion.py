"""Read-only completion projection; explicit independent-ledger IO boundary.

The native view is real. Its verified approval ledger, worker and query views
are declared IO spies, not substitutes for Root's actual PG acceptance proof.
No legacy native receipt or original check is written by these tests.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import native_goals as goals, layered_approval_store_v175 as approval
from generic_agents import native_goal_queries, native_goal_tools, native_goal_time
from generic_agents import instance_relation_types, workspace_api, workflow_worker_status
from generic_agents import goal_conflict_observation_service_v259 as conflict_branches
from generic_agents import models as m
from generic_agents.contracts import GoalSpec, digest
from tests.test_entity_release_compat_pure import World


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('completion projection tests cannot execute database/network/process IO')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(monkeypatch, status='accepted'):
    w = World(monkeypatch)
    task = w.task(); task.owner_id = w.p.user_id; task.version = 12
    task.status = 'workflow_completed'; task.title = '已保存的原业务任务'
    field = next(iter(w.entity.data))
    spec = GoalSpec(purpose='核对原登记', entity_ids=[w.entity.id], required_fields=[field],
        subgoals=[{'id': 'records', 'description': '核对登记', 'criterion_ids': ['enough']}],
        criteria=[{'id': 'enough', 'description': '原资料充分', 'kind': 'evidence_sufficient'}]).model_dump()
    report = {'schema': 'native-goal-check-v1', 'checked_at': '2026-10-24T01:00:00+00:00',
        'evidence': {'status': 'sufficient'}, 'gaps': [],
        'evaluation': {'completed': False, 'ready_for_acceptance': True,
            'basis_hash': 'a' * 64, 'flow_finished': True, 'data_received': True}}
    memory = NS(id='old-check', workspace_id=w.p.workspace_id,
        data={'task_id': task.id, 'report': report, 'report_hash': digest(report)})
    w.db.rows[m.Memory] = {memory.id: memory}
    task.data.update(goal_spec=spec, native_goal_context={'original': 'saved context'},
        native_goal_check_id=memory.id, workflow_runtime={'receipts': [{'note': '原人工结果'}]})
    independent = {'request_id': 'original-independent-nonce', 'actor_id': 'independent-acceptor',
        'at': '2026-10-24T01:02:00+00:00', 'reason': '已独立核对结果与原依据', 'decision': 'accept',
        'basis': {'schema': 'layered-approval-acceptance-basis-v175', 'task_id': task.id,
            'submission_hash': 'b' * 64, 'basis_hash': 'c' * 64,
            'physical_state_verified': False, 'evaluation': {'completed': True}}}
    managed = None if status is None else {'schema': 'layered-approval-task-view-v175',
        'task_id': task.id, 'managed_id': 'permanent-managed-record', 'version': 9,
        'status': status, 'submission_hash': 'b' * 64,
        'acceptance': copy.deepcopy(independent) if status == 'accepted' else None}
    query = {'id': 'original-query', 'status': 'sufficient', 'current_valid': False,
        'historical': False, 'reason': '原任务已经验收，不能继续旧协调',
        'scope_error': '原任务已经验收，不能继续旧协调',
        'result': {'original': 'immutable query result'}, 'derived_records': [{'original': 'value'}]}
    calls = []
    def detail(db, p, selected, *, with_basis):
        assert db is w.db and p is w.p and selected is task and with_basis is False
        calls.append('approval')
        return copy.deepcopy(managed)
    def scoped(db, model, identity, p, lock=False):
        assert lock is False
        return w.db.scoped(db, model, identity, p, lock)
    def guard(*args, **kwargs):
        calls.append('guard')
        assert kwargs == {'lock_sources': False}
        if status not in (None, 'executing'):
            raise HTTPException(409, '原审批不在执行状态')
    def assessment(*args, **kwargs):
        calls.append('assessment')
        assert kwargs == {'lock_sources': False}
        return {'status': 'sufficient'}, {**copy.deepcopy(report['evaluation']), 'basis_hash': 'd' * 64}
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *a: (task, w.r2, {}))
    monkeypatch.setattr(goals, 'scoped', scoped)
    monkeypatch.setattr(approval, 'detail', detail)
    monkeypatch.setattr(workflow_worker_status, 'check', lambda *a: {'state': 'saved'})
    monkeypatch.setattr(native_goal_tools, 'for_task', lambda *a: {'tools': [], 'query_tools': [],
        'query_tools_error': None, 'relation_types': [], 'relation_types_error': None})
    def query_view(*args, **kwargs):
        assert not kwargs, 'query view contract and original closed state must remain unchanged'
        calls.append('query')
        return copy.deepcopy(query)
    monkeypatch.setattr(native_goal_queries, 'view', query_view)
    monkeypatch.setattr(instance_relation_types, 'relation_metadata', lambda *a: {'entities': []})
    monkeypatch.setattr(goals, 'saved_context', lambda *a: calls.append('saved_context'))
    monkeypatch.setattr(goals, 'guard', guard)
    monkeypatch.setattr(goals, 'assessment', assessment)
    monkeypatch.setattr(native_goal_time, 'timeout', lambda selected: selected.data.get('native_goal_timed_out'))
    monkeypatch.setattr(native_goal_time, 'view', lambda *a: {'state': 'saved original timer'})
    # This projection fixture predates conflict-observation branches and has
    # none. Keep that independent inventory an explicit read boundary; do not
    # teach its bounded release-only FakeDB to accept arbitrary new queries.
    def no_conflict_inventory(db, p, task_id):
        assert db is w.db and p is w.p and task_id == task.id
        return []
    monkeypatch.setattr(conflict_branches, '_inventory', no_conflict_inventory)
    return NS(**vars(w), task=task, report=report, memory=memory, independent=independent,
        managed=managed, query=query, calls=calls)


def test_verified_independent_acceptance_is_separate_and_original_native_check_remains_historical(monkeypatch):
    c = scene(monkeypatch)
    before = copy.deepcopy((c.task.data, c.memory.data))
    value = goals.view(c.db, c.p, c.task)
    completion = value['approval_completion']
    assert value['acceptance_source'] == 'layered_approval_v175'
    assert completion['accepted'] is True and completion['status'] == 'accepted'
    assert completion['acceptance'] == c.independent
    assert completion['managed_id'] == c.managed['managed_id']
    assert completion['version'] == c.managed['version']
    assert completion['can_execute'] is False and completion['physical_state_verified'] is False
    assert value['acceptance'] is None and value['data_received'] is False
    assert value['check_historical'] is True and value['check'] == c.report
    assert value['check']['evaluation']['completed'] is False
    assert value['scope_error'] is None and value['can_manage'] is False and value['can_stop'] is False
    assert c.calls == ['approval', 'query']
    assert value['data_query']['current_valid'] is False
    assert value['data_query']['reason'] == c.query['reason']
    assert value['data_query']['result'] == c.query['result']
    assert value['data_query']['historical'] is True
    assert value['data_query']['closed_reason'] == '独立验收已保存，原查询仅供回看'
    assert (c.task.data, c.memory.data) == before
    completion['acceptance']['reason'] = 'caller mutation'
    value['check']['evaluation']['completed'] = True
    assert (c.task.data, c.memory.data) == before


@pytest.mark.parametrize('status', ['draft', 'in_review', 'approved', 'executing', 'acceptance_returned'])
def test_nonaccepted_managed_status_never_claims_acceptance_or_bypasses_live_gate(monkeypatch, status):
    c = scene(monkeypatch, status)
    value = goals.view(c.db, c.p, c.task)
    assert value['acceptance_source'] is None
    assert value['approval_completion']['accepted'] is False
    assert value['approval_completion']['acceptance'] is None
    assert value['check_historical'] is False
    assert 'guard' in c.calls
    assert ('assessment' in c.calls) is (status == 'executing')
    assert 'closed_reason' not in value['data_query']


def test_legacy_actual_native_acceptance_keeps_native_source_and_original_receipt(monkeypatch):
    c = scene(monkeypatch, None)
    accepted = {'reason': '原旧接口的真实回执边界', 'at': '2026-10-24T01:00:00+00:00'}
    c.task.data.update(native_goal_acceptance=accepted, native_goal_acceptance_hash=digest(accepted),
        native_goal_data_receipt_id='old-native-outcome')
    value = goals.view(c.db, c.p, c.task)
    assert value['acceptance_source'] == 'native_goal' and value['acceptance'] == accepted
    assert value['data_received'] is True
    assert 'approval_completion' not in value and 'check_historical' not in value
    assert value['check'] == c.report
    assert c.calls == ['approval', 'query']


def test_legacy_unaccepted_and_closed_history_do_not_become_independent_acceptances(monkeypatch):
    c = scene(monkeypatch, None)
    c.task.data['native_goal_cancelled'] = {'reason': '原停止理由'}
    c.task.data['native_goal_timed_out'] = {'reason': '原到期记录'}
    before = copy.deepcopy(c.task.data)
    value = goals.view(c.db, c.p, c.task)
    assert value['acceptance_source'] is None and value['acceptance'] is None
    assert 'approval_completion' not in value
    assert value['cancelled'] == before['native_goal_cancelled']
    assert value['timed_out'] == before['native_goal_timed_out']
    assert c.task.data == before


@pytest.mark.parametrize('fault', ['family', 'task', 'missing_receipt', 'decision', 'missing_basis', 'basis_family', 'basis_task', 'submission'])
def test_incomplete_or_cross_bound_independent_completion_fails_closed(monkeypatch, fault):
    c = scene(monkeypatch)
    if fault == 'family': c.managed['schema'] = 'unknown-approval-family'
    elif fault == 'task': c.managed['task_id'] = 'another-task'
    elif fault == 'missing_receipt': c.managed['acceptance'] = None
    elif fault == 'decision': c.managed['acceptance']['decision'] = 'return'
    elif fault == 'missing_basis': c.managed['acceptance']['basis'] = None
    elif fault == 'basis_family': c.managed['acceptance']['basis']['schema'] = 'unknown-basis'
    elif fault == 'basis_task': c.managed['acceptance']['basis']['task_id'] = 'another-task'
    else: c.managed['acceptance']['basis']['submission_hash'] = 'f' * 64
    with pytest.raises(HTTPException) as error:
        goals.view(c.db, c.p, c.task)
    assert error.value.status_code == 409
    assert c.calls == ['approval']


def test_missing_or_corrupted_independent_ledger_is_not_downgraded_to_legacy(monkeypatch):
    c = scene(monkeypatch)
    def blocked(*args, **kwargs):
        raise HTTPException(409, 'permanent approval audit is missing')
    monkeypatch.setattr(approval, 'detail', blocked)
    with pytest.raises(HTTPException) as error:
        goals.view(c.db, c.p, c.task)
    assert error.value.status_code == 409
