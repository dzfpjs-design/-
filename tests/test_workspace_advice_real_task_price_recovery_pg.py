"""Real mature graph task's v310 price receipt recovery, isolated only."""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_task_price_recovery_candidate_v2 as recovery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence
from generic_agents.security import Principal
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_manual_append_pg import matured_manual_case, managed, scene
from tests.test_same_goal_advice_price_v307 import html


def test_real_graph_task_price_original_is_recovered_with_other_model_same_nonce(matured_manual_case, monkeypatch):
    case = matured_manual_case; model = case.publication.model
    pin = {key: model[key] for key in ('id', 'content_hash', 'release_version')}
    body = {'request_id': str(uuid4()), 'model_pin': pin, 'validity_seconds': 3600,
        'reason': '隔离真实任务原价恢复'}
    second_manifest = copy.deepcopy(manifest()); second_manifest['name'] = 'synthetic_other_model_same_nonce'
    second_manifest['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    second = publish(case.client, second_manifest)
    second_body = {**body, 'model_pin': {key: second[key] for key in ('id', 'content_hash', 'release_version')}}
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    task_url = '/api/workspace/tasks/' + case.task_id + '/business-advice/price-options'
    created = case.client.post(task_url, json=body); assert created.status_code == 201, created.text
    direct = case.client.post('/api/workspace/business-advice/price-bases', json=second_body)
    assert direct.status_code == 201, direct.text
    assert created.json()['basis_ref'] != direct.json()['basis_ref']
    with Session(engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        wrapper = db.scalar(select(Audit).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == case.task_id, Audit.action == options.PRICE_ACTION,
            Audit.data['request_id'].as_string() == body['request_id']))
        original = db.get(Audit, created.json()['basis_ref']['id'])
        assert wrapper and original
        evidence_id, upload_id = original.data['official_evidence_ref']['evidence_id'], original.data['upload_audit_ref']['id']
        objects = [wrapper, original, db.get(Evidence, evidence_id), db.get(Audit, upload_id)]
        assert all(objects)
        before = {obj.id: (obj.version, obj.created_at, digest(obj.data)) for obj in objects}
        ids = (wrapper.id, original.id, evidence_id, upload_id)
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('任务回执恢复不能重新抓取'))
    monkeypatch.setattr(prices, 'authorize', lambda *a, **k: pytest.fail('任务回执恢复不能重新登记'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, actor, case.task_id, body)
        assert result['basis_ref'] == created.json()['basis_ref']
        with pytest.raises(HTTPException):
            recovery.recover_only(db, actor, case.task_id, {**body, 'reason': '另一个正文'})
        with pytest.raises(HTTPException):
            recovery.recover_only(db, actor, case.task_id, {**body, 'request_id': str(uuid4())})
        db.commit()
    engine().dispose()
    with Session(engine()) as db:
        objects = [db.get(Audit, ids[0]), db.get(Audit, ids[1]), db.get(Evidence, ids[2]), db.get(Audit, ids[3])]
        assert all(objects) and {obj.id: (obj.version, obj.created_at, digest(obj.data)) for obj in objects} == before
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
