"""Normal API + real local tool: compare nonzero DataRun budget totals."""
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import goal_budget_snapshot_candidate_v2 as candidate
from generic_agents import goal_queries, native_goal_queries, native_goals
from generic_agents.db import engine
from generic_agents.models import AuthSession, DataRun, Invocation, Task
from generic_agents.security import Principal
from tests.test_native_query_readonly_pg import ready_query
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network


def test_real_api_query_call_totals_and_untracked_datarun(client, reject_non_loopback_network):
    value = ready_query(client); task_id = value['task']['id']
    me = client.get('/api/me').json()
    with Session(engine()) as db:
        auth = db.scalar(select(AuthSession).where(AuthSession.user_id == me['user_id'],
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp()))
        assert auth is not None
        principal = Principal(me['user_id'], me['workspace_id'], me['role'], auth.id)
        task = db.get(Task, task_id)
        expected_calls = goal_queries.call_count(db, principal, task)
        expected_steps = native_goals.total_steps(db, principal, task)
        query_id = value['state']['queries'][0][0]
        run = db.get(DataRun, query_id)
        assert run.data['native_query_contract']['task_id'] == task.id
        assert (run.data.get('goal_delegation', {}).get('task_id') == task.id or
            run.data.get('native_task_query_contract', {}).get('task_id') == task.id)
        actual_calls = db.scalars(select(Invocation).where(Invocation.workspace_id == principal.workspace_id,
            Invocation.idempotency_key.like('data-tool:' + query_id + ':%'))).all()
        assert len(actual_calls) == 1 and actual_calls[0].status == 'succeeded'
        assert expected_calls == 1 and native_goal_queries.step_count(db, principal, task) == 1
        assert expected_steps >= 1
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        task, run = db.get(Task, task_id), db.get(DataRun, query_id)
        expected = {'calls': expected_calls, 'steps': expected_steps, 'queued_v2': 0}
        assert candidate.read_snapshot(db, principal, task) == expected
        original = copy.deepcopy(run.data)
        run.data['pending_note'] = '未保存的本地修改'
        assert run not in db.dirty
        with pytest.raises(HTTPException): candidate.read_snapshot(db, principal, task)
        assert run.data['pending_note'] == '未保存的本地修改'
        db.rollback()
    with Session(engine()) as db:
        assert db.get(DataRun, query_id).data == original
    print('REAL_NATIVE_DATARUN_INVOCATION_SNAPSHOT_MATCH',
        {'calls': expected_calls, 'steps': expected_steps}, flush=True)
