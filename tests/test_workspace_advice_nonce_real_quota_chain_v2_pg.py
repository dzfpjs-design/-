"""Slow isolated test of a real mature Goal and the v310 quota Audit chain.

Uses synthetic local observations and a synthetic HTML price response. It does
not send to a model, use a real MQTT broker, or install the candidate guard.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Task
from generic_agents.security import Principal
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import (
    matured_manual_case, managed, scene, reject_non_loopback_network,
)
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_same_goal_advice_price_v307 import html


def test_authentic_v310_chain_replays_without_treating_two_audits_as_two_commands(matured_manual_case, monkeypatch):
    case = matured_manual_case
    source = case.publication.pins['reasoning']
    publish(case.client, contracts.build_manifest(source['data'], {
        key: source[key] for key in ('id', 'content_hash', 'release_version')}))
    model = case.publication.model
    model_pin = {key: model[key] for key in ('id', 'content_hash', 'release_version')}
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    response = case.client.post('/api/workspace/business-advice/price-bases', json={
        'request_id': str(uuid4()), 'model_pin': model_pin, 'validity_seconds': 3600,
        'reason': '隔离合成价格材料'})
    assert response.status_code == 201, response.text
    with Session(engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        task = db.get(Task, case.task_id)
        body = {'request_id': str(uuid4()), 'expected_goal_hash': task.data['goal_hash'],
            'expected_mature_head_hash': case.head['head_hash'], 'max_cost_minor_units': 100,
            'currency': 'CNY', 'validity_seconds': 900, 'reason': '隔离真实授权链测试'}
        result = options.configure_quota(db, actor, task.id, body)
        db.commit()
        assert result['cloud_send_authorized'] is False
    with Session(engine()) as db:
        records = list(db.scalars(select(Audit).where(Audit.workspace_id == case.workspace_id,
            Audit.action.in_([candidate.QUOTA_WRAPPER, candidate.V1_POLICY_REQUEST]),
            Audit.data['request_id'].as_string() == body['request_id'])))
        assert len(records) == 2
        inner = next(record for record in records if record.action == candidate.V1_POLICY_REQUEST)
        inner_body = inner.data['body']
        candidate.acquire(db, actor, action=candidate.QUOTA_WRAPPER, task_id=case.task_id, body=body)
        candidate.acquire(db, actor, action=candidate.QUOTA_WRAPPER, task_id=case.task_id,
            subject_id=case.task_id, body=body)
        candidate.acquire(db, actor, action=candidate.V1_POLICY_REQUEST, task_id=case.task_id, body=inner_body)
        assert options.configure_quota(db, actor, case.task_id, body)['quota_policy_ref'] == result['quota_policy_ref']
        assert quota.authorize(db, actor, case.task_id, inner_body)['quota_policy_ref'] == result['quota_policy_ref']
        with pytest.raises(HTTPException):
            candidate.acquire(db, actor, action=candidate.V2_POLICY_REQUEST, task_id=case.task_id, body=inner_body)
        with pytest.raises(HTTPException):
            candidate.acquire(db, actor, action=candidate.QUOTA_WRAPPER, task_id=case.task_id,
                subject_id=str(uuid4()), body=body)
        with pytest.raises(HTTPException):
            candidate.acquire(db, actor, action=candidate.QUOTA_WRAPPER, task_id=case.task_id,
                advice_id=str(uuid4()), body=body)
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
        db.rollback()
