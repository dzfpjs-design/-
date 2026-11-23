"""Historical collisions created by the real pre-migration price API.

This only demonstrates the compatibility boundary. No historical records
are repaired or selected, and the candidate remains unconnected.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents import workspace_advice_price_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Capability, CloudCall, Evidence
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_instance_workspace import prepared, enable, object_row, task_row
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_price_v307 import html
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context


def test_old_workspace_price_api_accepts_same_nonce_on_distinct_models(real_price_context, client, monkeypatch):
    s = real_price_context
    model = copy.deepcopy(manifest()); model['name'] = 'synthetic_second_nonce_price'
    model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    released = publish(client, model)
    second = {**s['body'], 'model_pin': {key: released[key] for key in
        ('id', 'content_hash', 'release_version')}}
    third_manifest = copy.deepcopy(model); third_manifest['name'] = 'synthetic_third_nonce_price'
    third_model = publish(client, third_manifest)
    third = {**s['body'], 'model_pin': {key: third_model[key] for key in
        ('id', 'content_hash', 'release_version')}}
    with Session(engine()) as db:
        first_result = prices.authorize(db, s['principal'], s['body'])
        second_result = prices.authorize(db, s['principal'], second)
        db.commit()
        assert first_result['basis_ref'] != second_result['basis_ref']
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('历史记录重放不应重新抓取'))
    with Session(engine()) as db:
        assert prices.authorize(db, s['principal'], s['body'])['basis_ref'] == first_result['basis_ref']
        assert prices.authorize(db, s['principal'], second)['basis_ref'] == second_result['basis_ref']
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == s['principal'].workspace_id,
            Audit.action == candidate.PRICE_BASIS,
            Audit.data['request_id'].as_string() == s['body']['request_id'])))
        assert len(rows) == 2 and {row.object_id for row in rows} == {
            s['body']['model_pin']['id'], second['model_pin']['id']}
        for body in (s['body'], second):
            with pytest.raises(HTTPException):
                candidate.acquire(db, s['principal'], action=candidate.PRICE_BASIS, task_id=None, body=body)
        assert recovery.recover_only(db, s['principal'], s['body'])['basis_ref'] == first_result['basis_ref']
        assert recovery.recover_only(db, s['principal'], second)['basis_ref'] == second_result['basis_ref']
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], third)
        with pytest.raises(HTTPException):
            recovery.recover_only(db, s['principal'], {**s['body'], 'reason': '不同正文'})
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))
        db.rollback()


def test_corrupt_price_receipt_gives_controlled_rejection_and_caller_can_roll_back(real_price_context):
    s = real_price_context; principal = s['principal']
    with Session(engine()) as db:
        original_ref = prices.authorize(db, principal, s['body'])['basis_ref']
        db.commit()
    with Session(engine()) as db:
        original = db.get(Audit, original_ref['id'])
        malformed = copy.deepcopy(original.data); malformed.pop('http_receipt')
        original.data = malformed; db.commit()
    pending_id = str(uuid4())
    with Session(engine()) as db:
        db.add(Audit(id=pending_id, workspace_id=principal.workspace_id, actor_id=principal.user_id,
            object_id=s['task_id'], action='synthetic.rollback_probe', data={'source': 'isolated test'}))
        db.flush()
        with pytest.raises(HTTPException) as error:
            candidate.acquire(db, principal, action=candidate.PRICE_BASIS, task_id=None, body=s['body'])
        assert error.value.status_code == 409
        assert 'Traceback' not in str(error.value.detail) and 'pydantic' not in str(error.value.detail).lower()
        with pytest.raises(HTTPException) as recovery_error:
            recovery.recover_only(db, principal, s['body'])
        assert recovery_error.value.status_code == 409
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Audit, pending_id) is None
        assert db.get(Audit, original_ref['id']) is not None


def test_only_original_price_recovery_respects_retirement_and_session_revocation(real_price_context, monkeypatch):
    s = real_price_context; principal = s['principal']
    with Session(engine()) as db:
        result = prices.authorize(db, principal, s['body'])
        db.get(Capability, s['body']['model_pin']['id']).status = 'retired'; db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('恢复原回执不得重新抓取'))
    with Session(engine()) as db:
        assert recovery.recover_only(db, principal, s['body'])['basis_ref'] == result['basis_ref']
        db.get(AuthSession, principal.session_id).revoked = True; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, s['body'])
        db.rollback()


def test_only_original_price_recovery_rejects_damaged_evidence(real_price_context, monkeypatch):
    s = real_price_context; principal = s['principal']
    with Session(engine()) as db:
        result = prices.authorize(db, principal, s['body'])
        row = db.get(Audit, result['basis_ref']['id'])
        db.get(Evidence, row.data['official_evidence_ref']['evidence_id']).sha256 = 'f' * 64
        db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('损坏证据不得触发新登记'))
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, s['body'])
        db.rollback()


def test_old_sensor_price_api_accepts_same_nonce_across_real_tasks_and_models(client, account, monkeypatch):
    release, _, _ = prepared(client); enable(client, release)
    obj = object_row(client, release, index=1)
    first_task, _ = task_row(client, release, obj); second_task, _ = task_row(client, release, obj)
    assert first_task['id'] != second_task['id']
    published = []
    for name in ('sensor_nonce_first', 'sensor_nonce_second'):
        model = copy.deepcopy(manifest()); model['name'] = name
        model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
            'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
        published.append(publish(client, model))
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    nonce = str(uuid4()); reason = '隔离跨任务旧价格回放'
    urls = ['/api/sensor-native/tasks/' + task['id'] + '/analyses/price' for task in (first_task, second_task)]
    bodies = [{'request_id': nonce, 'model_id': model['id'], 'reason': reason} for model in published]
    for url, body in zip(urls, bodies):
        response = client.post(url, json=body); assert response.status_code == 200, response.text
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('历史回执不得重新抓取'))
    for url, body in zip(urls, bodies):
        response = client.post(url, json=body); assert response.status_code == 200, response.text
    # The old sensor endpoint scopes the underlying price receipt to the
    # model, not to its Task: a second authorized Task can recover it too.
    for url, body in zip(reversed(urls), bodies):
        response = client.post(url, json=body); assert response.status_code == 200, response.text
    with Session(engine()) as db:
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == account[2],
            Audit.action == candidate.PRICE_BASIS, Audit.data['request_id'].as_string() == nonce)))
        assert len(rows) == 2 and {row.object_id for row in rows} == {model['id'] for model in published}
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == account[2])))
