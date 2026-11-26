"""Default-off, session and cross-workspace checks for v2 preparation API."""
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall


def test_disabled_context_has_no_actions_and_post_does_not_write(client, account):
    base = '/api/workspace/tasks/' + str(uuid4()) + '/business-advice-v2'
    assert settings().v2_advice_setup_test_enabled is False
    assert client.get(base + '/availability').json() == {'enabled': False}
    assert client.get(base + '/context').json() == {'enabled': False}
    body = {'request': {'request_id': str(uuid4()), 'expected_goal_hash': 'a'*64,
        'expected_mature_head_hash': 'b'*64, 'document_source': None, 'reason': '测试'},
        'policy_ref': {'id': str(uuid4()), 'record_hash': 'c'*64}}
    assert client.post(base + '/preparations', json=body).status_code == 404
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


def test_enabled_never_allows_unknown_task_or_cross_workspace(client, account, monkeypatch):
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    base = '/api/workspace/tasks/' + str(uuid4()) + '/business-advice-v2'
    assert client.get(base + '/preparations').status_code == 404
    assert client.get(base + '/preparations/' + str(uuid4())).status_code == 404
    client.headers['X-Workspace-ID'] = str(uuid4())
    assert client.get(base + '/context').status_code in {403, 404}
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
