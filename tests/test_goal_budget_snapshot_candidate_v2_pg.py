"""Synthetic real PG coverage for the unrouted display-only budget reader."""
import copy
import os
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from generic_agents import goal_budget_snapshot_candidate_v2 as candidate
from generic_agents import goal_queries, native_goals
from generic_agents.db import engine
from generic_agents.models import AuthSession, DataRun, Invocation, Membership, Task, Workspace, uid
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297
from generic_agents.security import Principal
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


def readonly(db):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'),
    reason='requires isolated synthetic mature Goal and v1 Start checkpoint')
def test_real_goal_totals_snapshot_pending_and_revocation(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch); p = case.principal
    other_workspace, unrelated_id, related_id = uid(), uid(), uid()
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        expected_calls = goal_queries.call_count(db, p, task)
        expected_steps = native_goals.total_steps(db, p, task)
        assert expected_calls >= 1 and expected_steps >= 1
        data_calls = db.scalar(select(func.count()).select_from(Invocation).join(DataRun,
            Invocation.idempotency_key.like('data-tool:' + DataRun.id + ':%')).where(
                Invocation.workspace_id == p.workspace_id, DataRun.workspace_id == p.workspace_id,
                or_(DataRun.data['goal_delegation']['task_id'].as_string() == task.id,
                    DataRun.data['native_task_query_contract']['task_id'].as_string() == task.id)))
        from generic_agents.workflow_graph_goal_guard_v133 import pending_steps
        print('SYNTHETIC_REAL_START_COMPONENTS', {'data_calls': data_calls,
            'graph_pending_steps': pending_steps(task)}, flush=True)
        db.add(Workspace(id=other_workspace, name='隔离工作区'))
        db.flush()
        db.add_all([
            DataRun(id=unrelated_id, workspace_id=other_workspace, owner_id=p.user_id,
                question='另一个工作区的编辑', status='queued', data={'draft': '原值'}),
            DataRun(id=related_id, workspace_id=p.workspace_id, owner_id=p.user_id,
                question='当前目标的编辑', status='queued', data={'goal_delegation': {'task_id': task.id}}),
        ])
        db.commit()
    expected = {'calls': expected_calls, 'steps': expected_steps, 'queued_v2': 0}
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ ONLY'))
        assert candidate.read_snapshot(db, p, db.get(Task, case.task_id)) == expected
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY'))
        with pytest.raises(HTTPException): candidate.read_snapshot(db, p, db.get(Task, case.task_id))
        assert db.scalar(select(1)) == 1
    with Session(engine()) as owner, Session(engine()) as other_session:
        task = owner.get(Task, case.task_id)
        task.data['unsaved'] = '跨会话的未跟踪编辑'
        readonly(other_session)
        with pytest.raises(HTTPException): candidate.read_snapshot(other_session, p, task)
        owner.expunge(task)
        with pytest.raises(HTTPException): candidate.read_snapshot(other_session, p, task)
        assert task.data['unsaved'] == '跨会话的未跟踪编辑'
    with Session(engine()) as db:
        readonly(db); task = db.get(Task, case.task_id)
        assert candidate.read_snapshot(db, p, task) == expected
        other = db.get(DataRun, unrelated_id)
        task.title = '尚未保存的标题'
        other.data['draft'] = '尚未跟踪的另一工作区编辑'
        assert other not in db.dirty
        extra = DataRun(id=uid(), workspace_id=other_workspace, owner_id=p.user_id,
            question='另一工作区的新查询', status='queued', data={})
        db.add(extra)
        assert candidate.read_snapshot(db, p, task) == expected
        assert task.title == '尚未保存的标题' and task in db.dirty
        assert other.data['draft'] == '尚未跟踪的另一工作区编辑' and extra in db.new
        db.rollback()
    with Session(engine()) as db:
        assert db.get(DataRun, unrelated_id).data == {'draft': '原值'}
    with Session(engine()) as db:
        readonly(db); task = db.get(Task, case.task_id)
        collision = DataRun(id=related_id, workspace_id=other_workspace, owner_id=p.user_id,
            question='跨工作区主键冲突', status='queued', data={})
        db.add(collision)
        with pytest.raises(HTTPException): candidate.read_snapshot(db, p, task)
        assert collision in db.new
        db.rollback()
    with Session(engine()) as db:
        readonly(db); task = db.get(Task, case.task_id)
        invalid = DataRun(id=['非法编号'], workspace_id=other_workspace, owner_id=p.user_id,
            question='错误编号', status='queued', data={})
        db.add(invalid)
        with pytest.raises(HTTPException): candidate.read_snapshot(db, p, task)
        with db.no_autoflush: assert db.scalar(select(1)) == 1
        assert invalid in db.new
        db.rollback()
    for mode in ('new', 'tracked', 'untracked', 'deleted', 'task_graph', 'append_command'):
        with Session(engine()) as db:
            readonly(db); task = db.get(Task, case.task_id)
            run = db.get(DataRun, related_id); before = copy.deepcopy(run.data)
            if mode == 'new':
                pending = DataRun(id=uid(), workspace_id=p.workspace_id, owner_id=p.user_id,
                    question='当前目标未提交的查询', status='queued', data={'goal_delegation': {'task_id': task.id}})
                db.add(pending)
            elif mode == 'tracked': run.data = {**run.data, 'draft': '编辑'}
            elif mode == 'untracked': run.data['draft'] = '编辑'; assert run not in db.dirty
            elif mode == 'deleted': db.delete(run)
            elif mode == 'task_graph': task.data['graph_runtime'] = {'pending': '未跟踪的图预留编辑'}; assert task not in db.dirty
            else:
                pending = GoalEvaluationAppendCommandV297(id=uid(), workspace_id=p.workspace_id,
                    task_id=task.id, data={})
                db.add(pending)
            with pytest.raises(HTTPException) as error:
                candidate.read_snapshot(db, p, task)
            assert error.value.status_code == 409
            if mode in {'tracked', 'untracked'}: assert run.data['draft'] == '编辑'
            if mode == 'deleted': assert run in db.deleted
            if mode in {'new', 'append_command'}: assert pending in db.new
            if mode == 'task_graph': assert task.data['graph_runtime']['pending']
            db.rollback()
        with Session(engine()) as db:
            assert db.get(DataRun, related_id).data == before
    # Natural expiry in the final post-read verification must reject even when
    # the first identity check was still valid; no clock or stored row altered.
    short_id = uid()
    with Session(engine()) as db:
        db.add(AuthSession(id=short_id, user_id=p.user_id, token_hash=uid().replace('-', ''),
            csrf_hash=uid().replace('-', ''), expires_at=db.scalar(select(func.clock_timestamp())) + timedelta(seconds=3),
            revoked=False, kind='cookie')); db.commit()
    short = Principal(p.user_id, p.workspace_id, p.role, short_id)
    original_extra = candidate._extra_state; checks = []
    def delay_last(db, reader, workspace):
        original_extra(db, reader, workspace); checks.append(True)
        if len(checks) == 2: reader.execute(select(func.pg_sleep(3.2)))
    with monkeypatch.context() as patch:
        patch.setattr(candidate, '_extra_state', delay_last)
        with Session(engine()) as db:
            readonly(db)
            with pytest.raises(HTTPException): candidate.read_snapshot(db, short, db.get(Task, case.task_id))
    assert len(checks) == 2
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id)); member.role = 'viewer'; db.commit()
    viewer = Principal(p.user_id, p.workspace_id, 'viewer', p.session_id)
    with Session(engine()) as db:
        readonly(db); task = db.get(Task, case.task_id)
        assert candidate.read_snapshot(db, viewer, task) == expected
        with Session(engine()) as writer:
            writer.get(AuthSession, p.session_id).revoked = True; writer.commit()
        assert candidate.read_snapshot(db, viewer, task) == expected
    with Session(engine()) as db:
        readonly(db)
        with pytest.raises(HTTPException): candidate.read_snapshot(db, viewer, db.get(Task, case.task_id))
    print('REAL_V1_GOAL_BUDGET_SNAPSHOT_AND_PENDING_VERIFIED', flush=True)
