"""Real mature graph task v310 quota receipt recovery, isolated only."""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import workspace_advice_task_quota_recovery_candidate_v2 as recovery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Task
from generic_agents.security import Principal
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_manual_append_pg import matured_manual_case, managed, scene
from tests.test_same_goal_advice_price_v307 import html


def test_real_task_quota_wrapper_recovers_original_despite_old_other_model_same_nonce(matured_manual_case, monkeypatch):
    case = matured_manual_case; source = case.publication.pins['reasoning']
    publish(case.client, contracts.build_manifest(source['data'], {key: source[key]
        for key in ('id', 'content_hash', 'release_version')}))
    model = case.publication.model; pin = {key: model[key] for key in ('id', 'content_hash', 'release_version')}
    second_manifest = copy.deepcopy(manifest()); second_manifest['name'] = 'synthetic_quota_other_model'
    second_manifest['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    second = publish(case.client, second_manifest)
    second_pin = {key: second[key] for key in ('id', 'content_hash', 'release_version')}
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    price = case.client.post('/api/workspace/business-advice/price-bases', json={
        'request_id': str(uuid4()), 'model_pin': pin, 'validity_seconds': 3600, 'reason': '隔离原价材料'})
    assert price.status_code == 201, price.text
    with Session(engine()) as db:
        task = db.get(Task, case.task_id)
        body = {'request_id': str(uuid4()), 'expected_goal_hash': task.data['goal_hash'],
            'expected_mature_head_hash': case.head['head_hash'], 'max_cost_minor_units': 100,
            'currency': 'CNY', 'validity_seconds': 300, 'reason': '隔离真实原额度外层'}
    other_price = case.client.post('/api/workspace/business-advice/price-bases', json={
        'request_id': body['request_id'], 'model_pin': second_pin, 'validity_seconds': 3600,
        'reason': '隔离旧版不同模型同号原价'})
    assert other_price.status_code == 201, other_price.text
    url = '/api/workspace/tasks/' + case.task_id + '/business-advice/quota-options'
    created = case.client.post(url, json=body); assert created.status_code == 201, created.text
    with Session(engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        wrapper = db.scalar(select(Audit).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == case.task_id, Audit.action == options.QUOTA_ACTION,
            Audit.data['request_id'].as_string() == body['request_id']))
        assert wrapper is not None
        auth = db.get(Audit, wrapper.data['authorization_ref']['id'])
        policy = db.get(Audit, wrapper.data['policy_ref']['id'])
        basis = db.get(Audit, wrapper.data['price_basis_ref']['id'])
        evidence = db.get(Evidence, basis.data['official_evidence_ref']['evidence_id'])
        upload = db.get(Audit, basis.data['upload_audit_ref']['id'])
        objects = [wrapper, auth, policy, basis, evidence, upload]; assert all(objects)
        ids = [obj.id for obj in objects]
        before = {obj.id: (obj.version, obj.created_at, digest(obj.data)) for obj in objects}
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('恢复不得重新抓取'))
    monkeypatch.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('恢复不得重新发放政策'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, actor, case.task_id, body)
        assert result['quota_policy_ref'] == created.json()['quota_policy_ref']
        assert result['cloud_send_authorized'] is False
        with pytest.raises(HTTPException):
            recovery.recover_only(db, actor, case.task_id, {**body, 'reason': '另一个正文'})
        with pytest.raises(HTTPException):
            recovery.recover_only(db, actor, case.task_id, {**body, 'request_id': str(uuid4())})
        db.commit()
    engine().dispose()
    with Session(engine()) as db:
        objects = [db.get(Audit, identity) for identity in ids[:4]] + [db.get(Evidence, ids[4]), db.get(Audit, ids[5])]
        assert all(objects) and {obj.id: (obj.version, obj.created_at, digest(obj.data)) for obj in objects} == before
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
