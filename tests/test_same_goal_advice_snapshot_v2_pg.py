"""Read-only snapshot tests on disposable PostgreSQL; no public route."""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Job, Membership, Plan, Task
from generic_agents.security import Principal
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def ro(db, isolation='REPEATABLE READ'):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + isolation + ', READ ONLY'))


@pytest.mark.parametrize('empty', [False, True])
def test_repeatable_read_snapshot_has_no_row_locks_or_dml(synthetic_custody, empty):
    s = synthetic_custody; statements = []
    if empty:
        with Session(engine()) as db:
            db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
            db.delete(db.get(Plan, s['plan_id'])); db.commit()
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(' '.join(statement.upper().split()))
    bind = engine(); event.listen(bind, 'before_cursor_execute', capture)
    try:
        with Session(bind) as db:
            ro(db); task = db.get(Task, s['task_id'])
            assert common.read_snapshot(db, s['principal'], task) == {'v1_calls': 0, 'queued_v2': 0 if empty else 1}
            assert db.scalar(select(1)) == 1
    finally:
        event.remove(bind, 'before_cursor_execute', capture)
    assert not any(' FOR UPDATE' in sql or ' FOR SHARE' in sql or ' FOR NO KEY UPDATE' in sql
        or ' FOR KEY SHARE' in sql for sql in statements)
    assert not any(sql.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for sql in statements)


def test_read_committed_read_only_is_rejected_without_aborting_transaction(synthetic_custody):
    s = synthetic_custody
    with Session(engine()) as db:
        ro(db, 'READ COMMITTED'); task = db.get(Task, s['task_id'])
        with pytest.raises(HTTPException) as error: common.read_snapshot(db, s['principal'], task)
        assert error.value.status_code == 409
        assert db.scalar(select(1)) == 1


def test_viewer_and_revocation_follow_the_snapshot_boundary(synthetic_custody):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id)); member.role = 'viewer'; db.commit()
    viewer = Principal(p.user_id, p.workspace_id, 'viewer', p.session_id)
    with Session(engine()) as snapshot:
        ro(snapshot); task = snapshot.get(Task, s['task_id'])
        assert common.read_snapshot(snapshot, viewer, task)['queued_v2'] == 1
        with Session(engine()) as writer:
            writer.get(AuthSession, p.session_id).revoked = True; writer.commit()
        # The established RR snapshot cannot observe the later commit; it is
        # display only and is never a grant to send or mutate.
        assert common.read_snapshot(snapshot, viewer, task)['queued_v2'] == 1
    with Session(engine()) as fresh:
        ro(fresh)
        with pytest.raises(HTTPException): common.read_snapshot(fresh, viewer, fresh.get(Task, s['task_id']))


def test_snapshot_rejects_worker_marker_and_unsupported_saved_state(synthetic_custody):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        ro(db)
        worker = Principal(p.user_id, p.workspace_id, 'admin', 'internal-worker-marker')
        with pytest.raises(HTTPException): common.read_snapshot(db, worker, db.get(Task, s['task_id']))
    with Session(engine()) as db:
        db.get(Job, s['job_id']).status = 'running'; db.commit()
    with Session(engine()) as db:
        ro(db)
        with pytest.raises(HTTPException): common.read_snapshot(db, p, db.get(Task, s['task_id']))


def test_snapshot_rechecks_natural_session_expiry(synthetic_custody, monkeypatch):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        db.get(AuthSession, p.session_id).expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=3)
        db.commit()
    original = common.v2._lock_current_reader; entered = []
    def delayed(db, actor, **kwargs):
        result = original(db, actor, **kwargs)
        if not entered:
            entered.append(True); db.execute(select(func.pg_sleep(3.2)))
        return result
    monkeypatch.setattr(common.v2, '_lock_current_reader', delayed)
    with Session(engine()) as db:
        ro(db)
        with pytest.raises(HTTPException): common.read_snapshot(db, p, db.get(Task, s['task_id']))
    assert entered


def test_target_title_pending_is_preserved_during_snapshot(synthetic_custody):
    s = synthetic_custody
    with Session(engine()) as db:
        ro(db); task = db.get(Task, s['task_id']); task.title = '未保存的标题'
        assert common.read_snapshot(db, s['principal'], task)['queued_v2'] == 1
        assert task.title == '未保存的标题' and task in db.dirty
        db.rollback()
