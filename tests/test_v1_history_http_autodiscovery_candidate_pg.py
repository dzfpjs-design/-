"""Real HTTP and PostgreSQL, synthetic substituted Goal/source/view only.

This opt-in experiment cannot run against a production database and does not
authorize a new operation, model call, retry, or sender.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Plan, uid
from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from tests.test_v1_history_reserved_candidate_pg import history
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

_REAL_VIEW = legacy._view


@pytest.mark.parametrize('with_intent', [False, True])
def test_http_original_history_and_exact_replay_no_writes(history, client, monkeypatch, with_intent):
    s, created, owned, reserved, old_id, data = history
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    if with_intent:
        with Session(engine()) as db:
            intents.issue(db, s['principal'], s['task_id'], created['plan_id'], owned['job_id'],
                owned['lease_token'], reserved['cloud_call_id']); db.commit()
    with Session(engine()) as db:
        before = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Plan, Audit, CloudCall))
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    response = client.get(base)
    assert response.status_code == 200, response.text
    assert response.json()['records'] == [{'id': old_id, 'status': 'cancelled'}]
    assert client.get(base + '/' + old_id).json()['id'] == old_id
    assert client.get(base + '/operations/' + data['operations'][0]['request_id']).json()['id'] == old_id
    assert client.post(base, json=data['operations'][0]['body']).json()['id'] == old_id
    assert client.post(base + '/' + old_id + '/cancel', json=data['operations'][1]['body']).json()['id'] == old_id
    altered = copy.deepcopy(data['operations'][0]['body']); altered['request_id'] = str(uuid4())
    assert client.post(base, json=altered).status_code == 409
    altered = copy.deepcopy(data['operations'][0]['body']); altered['reason'] += '篡改'
    assert client.post(base, json=altered).status_code == 409
    altered = copy.deepcopy(data['operations'][1]['body']); altered['request_id'] = str(uuid4())
    assert client.post(base + '/' + old_id + '/cancel', json=altered).status_code == 409
    with Session(engine()) as db:
        assert tuple(db.scalar(select(func.count()).select_from(model)) for model in (Plan, Audit, CloudCall)) == before
        assert db.get(Plan, old_id).data == data


def test_http_default_closed_and_tampered_original_rejected(history, client, monkeypatch):
    s, _, _, reserved, _, _ = history
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    assert client.get(base).status_code == 409
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    with Session(engine()) as db:
        db.get(CloudCall, reserved['cloud_call_id']).reserved_tokens += 1; db.commit()
    assert client.get(base).status_code == 409


def test_opt_in_never_operates_without_disposable_database_guard(history, client, monkeypatch):
    from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
    from fastapi import HTTPException
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    def reject(_): raise HTTPException(409, 'not disposable')
    monkeypatch.setattr(claims, 'require_test_database', reject)
    base = '/api/workspace/tasks/' + history[0]['task_id'] + '/business-advice/preparations'
    assert client.get(base).status_code == 409


def test_http_unowned_workspace_and_revoked_session_cannot_use_discovery(history, client, monkeypatch):
    s = history[0]; monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    assert client.get(base, headers={'X-Workspace-ID': uid()}).status_code in {401, 403, 404}
    with Session(engine()) as db:
        db.get(AuthSession, s['principal'].session_id).revoked = True; db.commit()
    assert client.get(base).status_code == 401


def test_http_unknown_v2_history_remains_closed(history, client, monkeypatch):
    s, created, _, _, _, _ = history
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    with Session(engine()) as db:
        db.add(Audit(id=uid(), workspace_id=s['principal'].workspace_id, actor_id=s['principal'].user_id,
            action='same_goal_advice.unlimited_unknown', object_id=created['plan_id'],
            data={'task_id':s['task_id'], 'plan_id':created['plan_id']})); db.commit()
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    assert client.get(base).status_code == 409


def test_http_original_view_is_rendered_on_discovered_history(history, client, monkeypatch):
    s, _, _, _, old_id, _ = history
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    monkeypatch.setattr(legacy, '_view', _REAL_VIEW)
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    response = client.get(base)
    assert response.status_code == 200, response.text
    item = response.json()['records'][0]
    assert item['plan_id'] == old_id and item['phase'] == 'cancelled'
    assert item['message'] == '准备已取消，原资料和操作记录保留'


def test_history_survives_new_authenticated_http_session(history, account, monkeypatch):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    s, _, _, _, old_id, data = history
    monkeypatch.setattr(settings(), 'v2_history_compat_test_enabled', True)
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    for _ in range(2):
        with TestClient(app) as browser:
            login = browser.post('/api/login', json={'username':account[0], 'password':account[1]})
            assert login.status_code == 200
            browser.headers.update({'X-CSRF-Token':login.json()['csrf_token'], 'X-Workspace-ID':account[2]})
            assert browser.get(base + '/' + old_id).json()['id'] == old_id
            assert browser.post(base, json=data['operations'][0]['body']).json()['id'] == old_id
    with Session(engine()) as db:
        assert db.get(Plan, old_id).data == data
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 1
