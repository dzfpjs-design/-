"""Current-cookie revocation only: in-memory ASGI and transaction doubles, no DB."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from generic_agents import models as m
from generic_agents import security
from generic_agents import session_revoke_api as revoke


A, B, U, W = ['00000000-0000-4000-8000-' + str(n).zfill(12) for n in range(1, 5)]
ORIGIN = 'http://127.0.0.1:18107'


class MemoryDB:
    def __init__(self):
        self.sessions = {sid: m.AuthSession(id=sid, user_id=U, kind='cookie',
            token_hash=security.token_hash('cookie-' + sid),
            csrf_hash=security.token_hash('csrf-' + sid), revoked=False,
            expires_at=m.now() + timedelta(hours=1)) for sid in (A, B)}
        self.user = m.User(id=U, username='synthetic', active=True, password_hash='not-used')
        self.membership = m.Membership(id=W, user_id=U, workspace_id=W, role='operator')
        self.pending, self.audits, self.lock_queries = [], [], []
        self.commits = self.rollbacks = 0
        self.override_lock = False
        self.lock_row = None
        self.fail_commit = False
        self.committed = {sid: False for sid in self.sessions}

    def scalar(self, query):
        params = query.compile().params
        if 'token_hash_1' in params:
            return next((row for row in self.sessions.values()
                if row.token_hash == params['token_hash_1'] and not row.revoked
                and row.expires_at > m.now()), None)
        self.lock_queries.append(query)
        return self.lock_row if self.override_lock else self.sessions.get(params['id_1'])

    def get(self, cls, key):
        return self.user if cls is m.User and key == U else None

    def scalars(self, query):
        return SimpleNamespace(all=lambda: [self.membership] if self.membership else [])

    def add(self, row):
        assert isinstance(row, m.Audit), 'revocation must never create a session'
        self.pending.append(row)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError('synthetic commit failure')
        self.commits += 1
        self.audits.extend(self.pending)
        self.pending = []
        self.committed = {sid: row.revoked for sid, row in self.sessions.items()}

    def rollback(self):
        self.rollbacks += 1
        self.pending = []
        for sid, value in self.committed.items():
            self.sessions[sid].revoked = value


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import socket
    import subprocess
    import sqlalchemy
    def deny(*args, **kwargs):
        pytest.fail('pure session revocation forbids external effects')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)
    monkeypatch.setattr(sqlalchemy, 'create_engine', deny)
    monkeypatch.setattr(security, 'new_session', deny)
    monkeypatch.setattr(security, 'settings', lambda: SimpleNamespace(allowed_origins=ORIGIN, session_cookie_name='ga_session'))


def request_for(sid=A, *, headers=()):
    raw = [(b'cookie', ('ga_session=cookie-' + sid).encode()),
           (b'x-csrf-token', ('csrf-' + sid).encode()),
           (b'origin', ORIGIN.encode()), (b'x-workspace-id', W.encode()), *headers]
    return Request({'type': 'http', 'method': 'POST', 'path': '/session/revoke',
                    'headers': raw, 'query_string': b''})


def client_for(db, sid=A):
    app = FastAPI()
    app.include_router(revoke.router, prefix='/api/setup')
    app.dependency_overrides[revoke.session] = lambda: db
    client = TestClient(app)
    client.cookies.set('ga_session', 'cookie-' + sid)
    client.headers.update({'X-CSRF-Token': 'csrf-' + sid, 'Origin': ORIGIN, 'X-Workspace-ID': W})
    return client


def private(response, status):
    assert response.status_code == status
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('role', ['viewer', 'operator', 'admin'])
def test_cookie_revoke_affects_only_authenticated_session_and_minimal_atomic_audit(role):
    db = MemoryDB(); db.membership.role = role
    with client_for(db) as client:
        before = dict(client.cookies)
        response = client.post('/api/setup/session/revoke')
        private(response, 200)
        assert response.json() == {'revoked': True} and dict(client.cookies) == before
    assert db.sessions[A].revoked and not db.sessions[B].revoked and db.commits == 1
    assert len(db.audits) == 1
    audit = db.audits[0]
    assert (audit.actor_id, audit.workspace_id, audit.object_id, audit.action, audit.data) == (
        U, W, A, 'session.current_revoked', {})
    query = db.lock_queries[0]
    assert query._for_update_arg is not None
    assert query.get_execution_options()['populate_existing'] is True
    assert query.compile(dialect=postgresql.dialect()).params == {'id_1': A}


@pytest.mark.parametrize('authorization', ['', 'Bearer anything', 'Basic ignored'])
def test_any_authorization_is_rejected_before_cookie_auth(monkeypatch, authorization):
    db = MemoryDB()
    monkeypatch.setattr(revoke, 'authenticated', lambda *args: pytest.fail('authorization must not reach cookie auth'))
    with client_for(db) as client:
        response = client.post('/api/setup/session/revoke', headers={'Authorization': authorization})
        private(response, 401)
    assert not any(r.revoked for r in db.sessions.values()) and not db.lock_queries


def test_missing_cookie_rejected_without_lock_or_new_session():
    db = MemoryDB()
    with client_for(db) as client:
        client.cookies.clear()
        private(client.post('/api/setup/session/revoke'), 401)
    assert not db.lock_queries and not db.audits


@pytest.mark.parametrize('headers', [
    {'X-CSRF-Token': 'wrong'}, {'X-CSRF-Token': 'csrf-' + B}, {'Origin': 'http://untrusted.invalid'}])
def test_original_csrf_and_origin_checks_are_not_relaxed(headers):
    db = MemoryDB()
    with client_for(db) as client:
        private(client.post('/api/setup/session/revoke', headers=headers), 403)
    assert not db.lock_queries and not any(r.revoked for r in db.sessions.values())


def test_existing_session_bound_restored_csrf_is_accepted():
    db = MemoryDB(); csrf = security.session_restore_csrf('cookie-' + A, db.sessions[A])
    with client_for(db) as client:
        private(client.post('/api/setup/session/revoke', headers={'X-CSRF-Token': csrf}), 200)
    assert db.sessions[A].revoked and not db.sessions[B].revoked


@pytest.mark.parametrize('state,status', [('revoked', 401), ('expired', 401), ('disabled', 401), ('membership', 403)])
def test_fresh_auth_rechecks_live_session_user_and_membership(state, status):
    db = MemoryDB()
    if state == 'revoked': db.sessions[A].revoked = True
    if state == 'expired': db.sessions[A].expires_at = m.now() - timedelta(seconds=1)
    if state == 'disabled': db.user.active = False
    if state == 'membership': db.membership = None
    with client_for(db) as client:
        private(client.post('/api/setup/session/revoke'), status)
    assert not db.lock_queries and not db.audits


@pytest.mark.parametrize('options', [{'json': {'session_id': B}}, {'json': {}}, {'content': '{broken'}, {'params': {'target': B}}])
def test_no_body_query_or_arbitrary_target_is_accepted(options):
    db = MemoryDB()
    with client_for(db) as client:
        private(client.post('/api/setup/session/revoke', **options), 422)
    assert not db.lock_queries and not any(r.revoked for r in db.sessions.values())


@pytest.mark.parametrize('mutation', ['missing', 'id', 'user', 'kind', 'token'])
def test_locked_row_binding_fails_closed_and_never_changes_another_session(mutation):
    db = MemoryDB(); request = request_for(); p = security.authenticated(request, db)
    row = m.AuthSession(id=A, user_id=U, kind='cookie', token_hash=security.token_hash('cookie-' + A), revoked=False)
    if mutation == 'id': row.id = B
    if mutation == 'user': row.user_id = 'foreign-user'
    if mutation == 'kind': row.kind = 'api'
    if mutation == 'token': row.token_hash = security.token_hash('cookie-' + B)
    db.override_lock = True; db.lock_row = None if mutation == 'missing' else row
    with pytest.raises(HTTPException) as error:
        revoke.revoke_current_session(request, p, db, None)
    assert error.value.status_code == 401
    assert error.value.headers['Cache-Control'] == 'private, no-store'
    assert not row.revoked and not any(r.revoked for r in db.sessions.values())
    assert not db.audits and db.commits == 0


def test_pre_authorized_duplicate_is_idempotent_but_fresh_replay_is_unauthorized():
    db = MemoryDB(); request = request_for(); p = security.authenticated(request, db)
    first = revoke.revoke_current_session(request, p, db, None)
    second = revoke.revoke_current_session(request, p, db, None)
    private(first, 200); private(second, 200)
    assert first.body == second.body == b'{"revoked":true}'
    assert len(db.audits) == 1 and db.commits == 2 and not db.sessions[B].revoked
    with pytest.raises(HTTPException) as error:
        revoke.cookie_principal(request, db)
    assert error.value.status_code == 401


def test_commit_failure_rolls_back_session_and_audit_together():
    db = MemoryDB(); request = request_for(); p = security.authenticated(request, db); db.fail_commit = True
    with pytest.raises(HTTPException) as error:
        revoke.revoke_current_session(request, p, db, None)
    assert error.value.status_code == 500
    assert error.value.headers['Cache-Control'] == 'private, no-store'
    assert error.value.detail == 'Current session revocation not confirmed'
    assert not db.sessions[A].revoked and not db.sessions[B].revoked
    assert db.rollbacks == 1 and not db.audits and not db.pending
