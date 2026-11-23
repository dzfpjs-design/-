"""Valid different admin and full original audit metadata in isolated PG.

Policy rows are synthetic custody fixtures, not proof of real Goal issuance.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import workspace_advice_policy_recovery_candidate_v2 as recovery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall
from generic_agents.security import Principal
from tests.conftest import application
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_policy_recovery_candidate_v2_pg import policy_receipts


def _snapshot(db, workspace, task_ids):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == workspace,
        Audit.object_id.in_(task_ids), Audit.action.in_([quota.AUTHORIZATION, store.QUOTA_POLICY]))))
    return {row.id: (row.workspace_id, row.object_id, row.actor_id, row.action,
        row.created_at, row.version, digest(row.data)) for row in rows}


def test_real_different_admin_cannot_recover_others_original_and_metadata_survives_new_connection(client, policy_receipts):
    principal, rows = policy_receipts
    name, password = 'other_admin_' + uuid4().hex[:12], 'Test-' + uuid4().hex
    created = client.post('/api/users', json={'username': name, 'password': password, 'role': 'admin'})
    assert created.status_code == 201, created.text
    with TestClient(application) as other:
        login = other.post('/api/login', json={'username': name, 'password': password})
        assert login.status_code == 200, login.text
        with Session(engine()) as db:
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == created.json()['id'],
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
                .order_by(AuthSession.created_at.desc()))
            assert session is not None
            foreign = Principal(created.json()['id'], principal.workspace_id, 'admin', session.id)
            task_ids = [row[0] for row in rows]; before = _snapshot(db, principal.workspace_id, task_ids)
            for task_id, body, reference in rows:
                with pytest.raises(HTTPException): recovery.recover_only(db, foreign, task_id, body)
                assert recovery.recover_only(db, principal, task_id, body)['quota_policy_ref'] == reference
            db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert _snapshot(db, principal.workspace_id, task_ids) == before
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id)))
