"""Actual SQL IAM at the V127 active/historical identity seam.

Only User/Workspace/Membership tables use an isolated in-memory SQLite database.
The actual worker.actor and lock_current_actor queries execute unchanged. The
downstream domain guard is an explicit boundary spy with a real role require;
this is not PG row-lock, Managed Goal, provider or acquisition acceptance.
"""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from generic_agents import goal_queries, original_acquisition_coordination_v127 as m, worker
from generic_agents.models import Membership, User, Workspace
from generic_agents.security import Principal, require


@pytest.fixture
def iam_scene(monkeypatch):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    for model in (Workspace, User, Membership):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(id=str(uuid4()), name='synthetic coordination IAM')
        other = Workspace(id=str(uuid4()), name='other isolated workspace')
        user = User(id=str(uuid4()), username='original-event-owner', password_hash='not-a-login', active=True)
        member = Membership(id=str(uuid4()), user_id=user.id, workspace_id=workspace.id, role='operator')
        db.add_all([workspace, other, user, member]); db.commit()
        event = NS(workspace_id=workspace.id, data={'actor_id': user.id})
        run = NS(id='domain-run-boundary')
        calls = []
        def guard(actual_db, actual_p, actual_run):
            assert actual_db is db and actual_run is run
            assert actual_p.user_id == user.id and actual_p.workspace_id == workspace.id
            actual = db.scalar(select(Membership).where(Membership.workspace_id == actual_p.workspace_id,
                                                        Membership.user_id == actual_p.user_id))
            assert actual is not None and actual.role == actual_p.role
            require(actual_p, 'admin', 'operator')
            calls.append(actual_p)
        monkeypatch.setattr(goal_queries, 'guard', guard)
        yield NS(db=db, user=user, member=member, workspace=workspace, other=other,
                 event=event, run=run, calls=calls)
    engine.dispose()


@pytest.mark.parametrize('role', ['operator', 'admin'])
def test_active_coordination_passes_actual_current_membership_not_receiver_viewer(iam_scene, role):
    s = iam_scene; s.member.role = role; s.db.commit()
    m._active_goal_guard(s.db, s.event, s.run)
    assert len(s.calls) == 1 and s.calls[0].role == role
    assert s.calls[0].session_id == 'worker'


@pytest.mark.parametrize('revocation', ['viewer', 'inactive', 'missing_membership', 'other_workspace', 'missing_user'])
def test_current_revocation_cannot_be_repaired_by_event_identity(iam_scene, revocation):
    s = iam_scene
    if revocation == 'viewer': s.member.role = 'viewer'
    elif revocation == 'inactive': s.user.active = False
    elif revocation == 'missing_membership': s.db.delete(s.member)
    elif revocation == 'other_workspace': s.member.workspace_id = s.other.id
    else: s.db.delete(s.user)
    s.db.commit()
    with pytest.raises(HTTPException) as error:
        m._active_goal_guard(s.db, s.event, s.run)
    assert error.value.status_code == 403 and s.calls == []


@pytest.mark.parametrize('change', ['viewer', 'inactive', 'remove'])
def test_change_between_actor_lookup_and_authority_lock_is_rechecked_in_sql(iam_scene, monkeypatch, change):
    s = iam_scene; original_actor = worker.actor
    def stale_actor(db, workspace_id, user_id):
        original = original_actor(db, workspace_id, user_id)
        if change == 'viewer': s.member.role = 'viewer'
        elif change == 'inactive': s.user.active = False
        else: db.delete(s.member)
        db.flush()
        return original
    monkeypatch.setattr(worker, 'actor', stale_actor)
    with pytest.raises(HTTPException) as error:
        m._active_goal_guard(s.db, s.event, s.run)
    assert error.value.status_code == 403 and s.calls == []


def test_true_viewer_principal_still_has_no_domain_execution_permission(iam_scene):
    s = iam_scene; s.member.role = 'viewer'; s.db.commit()
    with pytest.raises(HTTPException) as error:
        goal_queries.guard(s.db, Principal(s.user.id, s.workspace.id, 'viewer', 'historical-reader'), s.run)
    assert error.value.status_code == 403
