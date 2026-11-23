"""Real two-task original policy recovery in disposable PostgreSQL only.

No cloud request, physical device or broker is used. The long test may take
tens of minutes and must run with the repository's disposable-PG safeguards.
"""
from contextlib import ExitStack
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents import workspace_advice_policy_recovery_candidate_v2 as recovery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Task
from generic_agents.security import Principal
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_authored_service_workflow_pg_v270 import good, publish
from tests.test_same_goal_advice_manual_append_pg import managed, scene, reject_non_loopback_network
from tests.two_task_mature_goal_helpers_candidate import setup_two_scopes, create_approved_task, mature_initial_task


def test_two_real_mature_tasks_recover_original_policies_without_reissuance(deepseek_3600_publication, monkeypatch):
    """Original policies are real, while the read-only helper is not wired to an API."""
    publication = deepseek_3600_publication
    source = publication.pins['reasoning']
    pin = {key: source[key] for key in ('id', 'content_hash', 'release_version')}
    registered = publish(publication.scene.client, contracts.build_manifest(source['data'], pin))
    assert registered['status'] == 'published'
    with ExitStack() as stack:
        context = setup_two_scopes(publication, stack, monkeypatch)
        tasks = [create_approved_task(context, target, index) for index, target in enumerate(context.targets, 1)]
        assert tasks[0].id != tasks[1].id
        mature = []
        for task in tasks:
            mature.append(mature_initial_task(context, task))
            print('SYNTHETIC_TASK_MATURED_' + str(len(mature)), flush=True)
        assert mature[0].entity_id != mature[1].entity_id
        assert not set(mature[0].observation_ids) & set(mature[1].observation_ids)
        assert mature[0].q0[0] != mature[1].q0[0]
        workspace = context.scene.workspace_id
        with Session(engine()) as db:
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == context.scene.actor_id,
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
                .order_by(AuthSession.created_at.desc()))
            assert session is not None
            actor = Principal(context.scene.actor_id, workspace, 'admin', session.id)
            now = db.scalar(select(func.clock_timestamp()))
        request_id = str(uuid4()); expiry = (now + timedelta(minutes=30)).isoformat()
        bodies = [{'request_id': request_id, 'expected_goal_hash': item.goal_hash,
            'expected_mature_head_hash': item.head['head_hash'], 'expires_at': expiry,
            'limits': {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2,
                'max_cost_minor_units': 100, 'currency': 'CNY'}, 'reason': '隔离测试旧政策跨任务同号'}
            for item in mature]
        results = []
        for item, body in zip(mature, bodies):
            with Session(engine()) as db:
                result = quota.authorize(db, actor, item.task_id, body); db.commit()
                assert result['cloud_send_authorized'] is False
                results.append(result)
        assert results[0]['quota_policy_ref'] != results[1]['quota_policy_ref']
        with Session(engine()) as db:
            for item, body, original in zip(mature, bodies, results):
                assert quota.authorize(db, actor, item.task_id, body)['quota_policy_ref'] == original['quota_policy_ref']
                with pytest.raises(HTTPException):
                    candidate.acquire(db, actor, action=candidate.V1_POLICY_REQUEST, task_id=item.task_id, body=body)
            rows = list(db.scalars(select(Audit).where(Audit.workspace_id == workspace,
                Audit.action == quota.AUTHORIZATION, Audit.data['request_id'].as_string() == request_id)))
            assert len(rows) == 2 and {row.object_id for row in rows} == {task.id for task in tasks}
            protected = list(db.scalars(select(Audit).where(Audit.workspace_id == workspace,
                Audit.object_id.in_([task.id for task in tasks]),
                Audit.action.in_([quota.AUTHORIZATION, store.QUOTA_POLICY]))))
            before = {row.id: (row.action, row.object_id, row.actor_id, digest(row.data)) for row in protected}
            with monkeypatch.context() as isolated:
                isolated.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('不得重新发放原政策'))
                for item, body, original in zip(mature, bodies, results):
                    actual = recovery.recover_only(db, actor, item.task_id, body)
                    assert actual['quota_policy_ref'] == original['quota_policy_ref']
                    assert actual['cloud_send_authorized'] is False
                    with pytest.raises(HTTPException):
                        recovery.recover_only(db, actor, item.task_id, {**body, 'reason': '另一个正文'})
                    with pytest.raises(HTTPException):
                        recovery.recover_only(db, actor, item.task_id, {**body, 'request_id': str(uuid4())})
                    wrong = Principal(str(uuid4()), workspace, 'admin', actor.session_id)
                    with pytest.raises(HTTPException): recovery.recover_only(db, wrong, item.task_id, body)
            after_rows = list(db.scalars(select(Audit).where(Audit.workspace_id == workspace,
                Audit.object_id.in_([task.id for task in tasks]),
                Audit.action.in_([quota.AUTHORIZATION, store.QUOTA_POLICY]))))
            assert {row.id: (row.action, row.object_id, row.actor_id, digest(row.data)) for row in after_rows} == before
            assert db.scalar(select(func.count()).select_from(CloudCall).where(CloudCall.workspace_id == workspace)) == 0
            assert all(db.get(Task, item.task_id) is not None for item in mature)
            db.rollback()
