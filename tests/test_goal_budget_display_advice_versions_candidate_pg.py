"""Actual authenticated GET with synthetic saved advice and stub Goal context.

No claim is made that the stubbed Goal is a real release or model journey.
The purpose is to preserve API failure boundaries before v2 is deployable.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import workspace_api
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Job, Membership, Plan, Task
from tests.test_goal_budget_versioned_snapshot_candidate_v3_pg import simplified_other_goal_components
from tests.test_same_goal_advice_common_inventory_v2_pg import add_synthetic_v1
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def setup_context(monkeypatch, s):
    monkeypatch.setattr(settings(), 'goal_budget_display_preview_enabled', True)
    def saved(db, principal, identity):
        task = db.get(Task, identity)
        assert task.workspace_id == principal.workspace_id
        return task, None, None
    monkeypatch.setattr(workspace_api, 'saved_execution', saved)
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        task.data = {'native_goal_context': {'synthetic': True}, 'workflow_runtime': {'receipts': ['r1', 'r2']}}
        db.commit()


def test_real_http_get_preserves_synthetic_v1_counter_and_session_permissions(
        client, synthetic_custody, simplified_other_goal_components, monkeypatch):
    s = synthetic_custody; p = s['principal']; setup_context(monkeypatch, s)
    with Session(engine()) as db:
        add_synthetic_v1(db, s)
        db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
        db.delete(db.get(Plan, s['plan_id'])); db.commit()
    path = '/api/workspace/tasks/' + s['task_id'] + '/goal/budget-display'
    result = client.get(path)
    assert result.status_code == 200, result.text
    assert result.json()['calls'] == 9 and result.json()['steps'] == 20
    with Session(engine()) as db:
        row = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id)); row.role = 'viewer'; db.commit()
    assert client.get(path).status_code == 200
    with Session(engine()) as db:
        db.get(AuthSession, p.session_id).revoked = True; db.commit()
    assert client.get(path).status_code == 401


@pytest.mark.parametrize('prepared', [False, True])
def test_real_http_get_fails_closed_on_v2_and_tampered_worker_isolation(
        client, ready, simplified_other_goal_components, monkeypatch, prepared):
    s, created, _ = ready; p = s['principal']; setup_context(monkeypatch, s)
    if prepared:
        # Candidate Goal derivation and document-source validation are test
        # substitutes supplied by ready; actual DB receipt is produced here.
        with Session(engine()) as db:
            worker.process(db, p, s['task_id'], created['job_id']); db.commit()
    path = '/api/workspace/tasks/' + s['task_id'] + '/goal/budget-display'
    assert client.get(path).status_code == 409
    with Session(engine()) as db:
        db.get(Job, created['job_id']).available_at = db.scalar(select(func.clock_timestamp())); db.commit()
    assert client.get(path).status_code == 409
    with Session(engine()) as db:
        row = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id)); row.role = 'viewer'; db.commit()
    assert client.get(path).status_code == 409
    with Session(engine()) as db:
        db.get(AuthSession, p.session_id).revoked = True; db.commit()
    assert client.get(path).status_code == 401
