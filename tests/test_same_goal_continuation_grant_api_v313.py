"""ASGI route/closed-body/commit boundaries with explicit source and IAM IO.

This does not prove an issued grant, source validation or tool execution.
"""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from generic_agents import same_goal_continuation_grant_api_v313 as api
from generic_agents.db import session
from generic_agents.security import authenticated


@pytest.fixture
def environment():
    app = FastAPI(); app.include_router(api.router, prefix='/api/workspace')
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()), role='admin')
    db = NS(commits=0)
    def commit(): db.commits += 1
    db.commit = commit
    app.dependency_overrides[authenticated] = lambda: p
    app.dependency_overrides[session] = lambda: db
    return NS(client=TestClient(app), db=db, p=p,
        base='/api/workspace/tasks/task/business-advice/preparations/advice/continuations/draft/tool-grants')


def command(operation):
    base = {'request_id': str(uuid4()), 'reason': '明确登记本次工具范围，不批准或执行任何操作'}
    if operation == 'revoke': return {**base, 'expected_grant_version': 1}
    return {**base, 'expected_task_version': 3, 'expected_continuation_version': 1,
        'revision_hash': 'a' * 64, 'source_hash': 'b' * 64,
        'capability_pin': {'id': str(uuid4()), 'content_hash': 'c' * 64, 'release_version': '1.0.0'},
        'field': 'temperature', 'operation': 'mean', 'validity_seconds': 7200,
        'policy_ref': {'policy_id': str(uuid4()), 'version_id': str(uuid4()),
            'revision': 1, 'record_hash': 'd' * 64}}


@pytest.mark.parametrize('path,target,name,refs', [
    ('/context', 'sources', 'context', ('task', 'advice', 'draft')),
    ('', 'store', 'listing', ('task', 'advice', 'draft')),
    ('/operations/request', 'store', 'get_operation', ('task', 'advice', 'draft', 'request')),
    ('/grant', 'store', 'get', ('task', 'advice', 'draft', 'grant')),
])
def test_scoped_historical_read_does_not_commit(environment, monkeypatch, path, target, name, refs):
    e = environment; calls = []
    def read(db, p, *args):
        assert db is e.db and p is e.p and args == refs
        calls.append(True); return {'original_only': True}
    monkeypatch.setattr(getattr(api, target), name, read)
    response = e.client.get(e.base + path)
    assert response.status_code == 200 and response.json() == {'original_only': True}
    assert calls == [True] and e.db.commits == 0


@pytest.mark.parametrize('operation,path', [('issue', ''), ('revoke', '/grant/revoke')])
def test_client_cannot_add_execution_authority_and_success_follows_commit(environment, monkeypatch, operation, path):
    e = environment; body = command(operation); calls = []
    def write(db, p, *args):
        refs = ('task', 'advice', 'draft') + (() if operation == 'issue' else ('grant',))
        assert db is e.db and p is e.p and args == (*refs, body) and e.db.commits == 0
        calls.append(True); return {'can_dispatch': False, 'execution_approved': False}
    monkeypatch.setattr(api.store, operation, write)
    response = e.client.post(e.base + path, json={**body, 'execution_approved': True, 'records': []})
    assert response.status_code == 422 and calls == [] and e.db.commits == 0
    response = e.client.post(e.base + path, json=body)
    assert response.status_code == (201 if operation == 'issue' else 200)
    assert response.json() == {'can_dispatch': False, 'execution_approved': False}
    assert calls == [True] and e.db.commits == 1


@pytest.mark.parametrize('operation,path', [('issue', ''), ('revoke', '/grant/revoke')])
def test_failed_commit_never_returns_saved_success(environment, monkeypatch, operation, path):
    e = environment; monkeypatch.setattr(api.store, operation, lambda *args: {'saved': True})
    def failure(): raise RuntimeError('grant durable commit failed')
    e.db.commit = failure
    with pytest.raises(RuntimeError, match='durable commit'):
        e.client.post(e.base + path, json=command(operation))


def test_denied_original_source_is_not_replaced_or_committed(environment, monkeypatch):
    e = environment
    def denied(*args): raise HTTPException(403, '当前账号不能查看此任务')
    monkeypatch.setattr(api.sources, 'context', denied)
    response = e.client.get(e.base + '/context')
    assert response.status_code == 403 and e.db.commits == 0
