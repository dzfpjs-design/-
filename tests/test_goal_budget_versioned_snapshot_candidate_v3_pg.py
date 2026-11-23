"""Read-only candidate over synthetic v2 custody on disposable PostgreSQL.

The fixture is not a real policy issuance or Goal workflow. Unrelated Goal
components are stubbed; genuine v1/v2 custody, SQL counts, user/session,
workspace isolation and snapshot handling are exercised here.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import goal_budget_versioned_snapshot_candidate_v3 as candidate
from generic_agents import goal_queries, native_goals
from generic_agents import goal_evaluation_append_store_v297 as append
from generic_agents import native_goal_queries as native_queries
from generic_agents import native_step_execution as native_steps
from generic_agents import same_goal_continuation_execution_budget_v314 as continuation
from generic_agents import workflow_graph_goal_guard_v133 as graph
from generic_agents import same_goal_advice_unlimited_authority_v2 as authority
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Job, Plan, Task
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def simplified_other_goal_components(monkeypatch):
    monkeypatch.setattr(graph, 'verify_reservations', lambda *_: None)
    monkeypatch.setattr(graph, 'uninvoked_calls', lambda *_: 2)
    monkeypatch.setattr(append, 'additional_budget_calls', lambda *_: 3)
    monkeypatch.setattr(continuation, 'additional_budget_calls', lambda *_: 4)
    monkeypatch.setattr(continuation, 'step_count', lambda *_: 5)
    monkeypatch.setattr(native_steps, 'step_count', lambda *_: 6)
    monkeypatch.setattr(native_queries, 'step_count', lambda *_: 7)


def seed_goal(s):
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        task.data = {'workflow_runtime': {'receipts': ['r1', 'r2']}}
        db.commit()


def ro(db, level='REPEATABLE READ'):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level + ', READ ONLY'))


def test_saved_v2_count_is_display_only_and_legacy_read_stays_closed(
        synthetic_custody, simplified_other_goal_components):
    s = synthetic_custody; p = s['principal']; seed_goal(s)
    statements = []
    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(' '.join(sql.upper().split()))
    bind = engine(); event.listen(bind, 'before_cursor_execute', capture)
    try:
        with Session(bind) as db:
            ro(db); task = db.get(Task, s['task_id'])
            assert candidate.read_snapshot(db, p, task) == {'calls': 9, 'steps': 20, 'queued_v2': 1}
            assert task.title == '合成来源保管测试'
            # The old authorization counter must remain fail closed while the
            # v2 writer/worker lifecycle has not been integrated.
            with pytest.raises(HTTPException): goal_queries.call_count(db, p, task)
            with pytest.raises(HTTPException): native_goals.total_steps(db, p, task)
    finally:
        event.remove(bind, 'before_cursor_execute', capture)
    assert not any(sql.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for sql in statements)
    assert not any(token in sql for sql in statements for token in
        (' FOR UPDATE', ' FOR SHARE', ' FOR NO KEY UPDATE', ' FOR KEY SHARE'))


def test_existing_legacy_totals_match_and_dirty_titles_survive(
        synthetic_custody, simplified_other_goal_components):
    s = synthetic_custody; p = s['principal']; seed_goal(s)
    with Session(engine()) as db:
        db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
        db.delete(db.get(Plan, s['plan_id'])); db.commit()
    with Session(engine()) as db:
        ro(db); task = db.get(Task, s['task_id'])
        assert goal_queries.call_count(db, p, task) == 9
        assert native_goals.total_steps(db, p, task) == 20
        task.title = '尚未保存的标题'
        assert candidate.read_snapshot(db, p, task) == {'calls': 9, 'steps': 20, 'queued_v2': 0}
        assert task.title == '尚未保存的标题' and task in db.dirty
    with Session(engine()) as db:
        assert db.get(Task, s['task_id']).title == '合成来源保管测试'


@pytest.mark.parametrize('fault', ['read_committed', 'changed_graph', 'running_job', 'revoked_session'])
def test_damaged_or_unauthorized_state_is_never_a_successful_read(
        synthetic_custody, simplified_other_goal_components, fault):
    s = synthetic_custody; p = s['principal']; seed_goal(s)
    if fault in {'running_job', 'revoked_session'}:
        with Session(engine()) as db:
            if fault == 'running_job': db.get(Job, s['job_id']).status = 'running'
            else: db.get(AuthSession, p.session_id).revoked = True
            db.commit()
    with Session(engine()) as db:
        ro(db, 'READ COMMITTED' if fault == 'read_committed' else 'REPEATABLE READ')
        task = db.get(Task, s['task_id'])
        if fault == 'changed_graph': task.data['workflow_runtime']['receipts'].append('unsaved')
        with pytest.raises(HTTPException): candidate.read_snapshot(db, p, task)
        if fault == 'changed_graph': assert task.data['workflow_runtime']['receipts'][-1] == 'unsaved'
        assert db.scalar(select(1)) == 1


def test_policy_reader_can_check_one_queued_v2_without_issuing_authority(
        synthetic_custody, simplified_other_goal_components, monkeypatch):
    """Real saved policy/custody; source configuration is a test substitute."""
    s = synthetic_custody; p = s['principal']; seed_goal(s)
    prep = s['prep']; budget = prep['budget']
    def cap(pin, data=None):
        return SimpleNamespace(id=pin['id'], content_hash=pin['content_hash'],
            release_version=pin['release_version'], data=data or {})
    model = cap(prep['model_pin'], {'execution': {'provider': 'deepseek', 'max_tokens': budget['max_tokens']}})
    context = {'scope': {k: v for k, v in prep['scope'].items() if k != 'analysis_plan_id'},
        'source': cap(prep['source_business_capability_ref']), 'advice': cap(prep['advice_capability_ref']),
        'model': model, 'goal': {'spec': {'max_tool_calls': budget['goal_max_tool_calls'],
            'max_steps': budget['goal_max_steps']}}}
    monkeypatch.setattr(quota, '_configuration_base', lambda *_: context)
    monkeypatch.setattr(native_steps, 'step_count', lambda *_: 1)
    monkeypatch.setattr(native_queries, 'step_count', lambda *_: 1)
    monkeypatch.setattr(continuation, 'step_count', lambda *_: 1)
    monkeypatch.setattr(authority.store.provider, 'binding', lambda *_: {'binding': {'graph': {'nodes': [
        {'id': 'reasoning', 'actor_id': p.user_id}, {'id': 'packet', 'actor_id': p.user_id}]}}})
    with Session(engine()) as db:
        result = authority.current_policy(db, p, s['task_id'], budget['policy_ref'])
        assert result['preview_authorized'] is False and result['cloud_send_authorized'] is False
        assert result['policy_ref'] == budget['policy_ref']
        with pytest.raises(HTTPException): goal_queries.call_count(db, p, db.get(Task, s['task_id']))
        db.rollback()
    context['goal']['spec']['max_tool_calls'] = 9  # exact used calls: no capacity
    with Session(engine()) as db, pytest.raises(HTTPException):
        authority.current_policy(db, p, s['task_id'], budget['policy_ref'])
    context['goal']['spec']['max_tool_calls'] = budget['goal_max_tool_calls']
    with Session(engine()) as db:
        db.get(Job, s['job_id']).status = 'running'; db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):
        authority.current_policy(db, p, s['task_id'], budget['policy_ref'])
