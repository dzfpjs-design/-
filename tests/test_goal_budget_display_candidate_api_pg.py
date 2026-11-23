"""Real authenticated GET in a disposable PG, including operator disable."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession, Membership
from tests.test_native_query_readonly_pg import ready_query, persisted_state
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network


def test_independent_readonly_get_preserves_old_response_and_revocation(client, monkeypatch, reject_non_loopback_network):
    case = ready_query(client); task_id = case['task']['id']
    original = client.get('/api/workspace/tasks/'+task_id+'/goal')
    assert original.status_code == 200
    assert settings().goal_budget_display_preview_enabled is True
    monkeypatch.setattr(settings(), 'goal_budget_display_preview_enabled', False)
    assert client.get('/api/workspace/tasks/'+task_id+'/goal/budget-display').status_code == 404
    monkeypatch.setattr(settings(), 'goal_budget_display_preview_enabled', True)
    path = '/api/workspace/tasks/'+task_id+'/goal/budget-display'
    assert client.get('/api/workspace/tasks/image/goal/budget-display').status_code == 404
    response = client.get(path); assert response.status_code == 200, response.text
    assert {'no-store', 'private'} <= {v.strip() for v in response.headers['cache-control'].split(',')}
    data = response.json()
    assert set(data) == {'task_id','calls','steps','observed_at','basis'}
    assert data['task_id'] == task_id and data['basis'] == 'saved_snapshot'
    assert data['calls'] == 1 and data['steps'] == 3
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        concurrent = list(pool.map(lambda _: client.get(path), range(3)))
    assert all(item.status_code == 200 and item.json()['calls'] == 1 for item in concurrent)
    print('ISOLATED_BUDGET_GET_THREE_READS_SECONDS', round(time.monotonic()-started, 3), flush=True)
    again = client.get('/api/workspace/tasks/'+task_id+'/goal')
    assert again.status_code == 200 and again.json().keys() == original.json().keys()
    assert all(again.json()[key] == original.json()[key] for key in
        ('task_id','version','goal','goal_confirmed','acceptance','task_status'))
    assert persisted_state(task_id) == case['state']
    me = client.get('/api/me').json()
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.user_id == me['user_id'],
            Membership.workspace_id == me['workspace_id']))
        membership.role = 'viewer'; db.commit()
    viewer = client.get(path); assert viewer.status_code == 200, viewer.text
    assert viewer.json()['calls'] == 1
    with Session(engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == me['user_id'], AuthSession.revoked.is_(False)))
        session.revoked = True; db.commit()
    assert client.get(path).status_code == 401
    print('REAL_BUDGET_DISPLAY_GET_AND_REVOCATION_VERIFIED', flush=True)
def test_revocation_during_snapshot_is_visible_on_next_request(client, monkeypatch, reject_non_loopback_network):
    if engine().pool.size() < 2:
        pytest.skip('the concurrent writer needs a second isolated pool connection')
    from generic_agents import goal_budget_display_candidate_api as module
    case = ready_query(client); me = client.get('/api/me').json()
    monkeypatch.setattr(settings(), 'goal_budget_display_preview_enabled', True)
    entered = threading.Event(); resume = threading.Event(); underlying = module.read_snapshot
    def hold(db, principal, task):
        result = underlying(db, principal, task); entered.set()
        assert resume.wait(10), 'snapshot test gate timed out'
        return result
    monkeypatch.setattr(module, 'read_snapshot', hold)
    path = '/api/workspace/tasks/'+case['task']['id']+'/goal/budget-display'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.get, path)
        try:
            assert entered.wait(10)
            with Session(engine()) as db:
                original = db.scalar(select(AuthSession).where(AuthSession.user_id == me['user_id'],
                    AuthSession.revoked.is_(False)))
                original.revoked = True; db.commit()
        finally:
            resume.set()
        first = future.result(timeout=10)
    assert first.status_code == 200  # As of its already established snapshot.
    assert client.get(path).status_code == 401  # New snapshot reauthenticates.
