"""Workspace write authorization remains current through the database commit."""
import copy
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from generic_agents import setup_api, setup_business_candidates, setup_content_review
from generic_agents.db import engine
from generic_agents.models import AuthSession, Membership, SetupDraft, User, now
from generic_agents.security import Principal, token_hash
from tests.test_setup_business_candidates import bundle, material


def request_for(client, endpoint):
    if endpoint == 'content_review':
        from tests.test_setup_content_review import stage
        saved = stage(client)
        return setup_content_review, '/api/setup/candidates/content-review', 'post', {
            'revision': saved['revision'], 'business': '日常检查', 'action': 'confirm'}
    source = material(client)
    candidate = bundle(source)
    current = client.get('/api/setup/draft').json()
    if endpoint == 'append':
        return setup_business_candidates, '/api/setup/candidates', 'post', {
            'revision': current['revision'], 'bundle': candidate}
    if endpoint == 'revalidate':
        reply = client.post('/api/setup/candidates', json={'revision': current['revision'], 'bundle': candidate})
        assert reply.status_code == 200, reply.text
        return setup_business_candidates, '/api/setup/candidates/revalidate', 'post', {
            'revision': reply.json()['revision']}
    value = copy.deepcopy(current['configuration'])
    value = value or {'version': 1, 'resources': [], 'connections': [], 'materials': [],
                      'instanceProfile': {}, 'businesses': [], 'flows': {}}
    value.setdefault('instanceProfile', {})['name'] = '合成变更'
    return setup_api, '/api/setup/draft', 'put', {'revision': current['revision'], 'configuration': value}


@pytest.mark.parametrize('endpoint', ['append', 'revalidate', 'draft', 'content_review'])
@pytest.mark.parametrize('revocation', ['role', 'session', 'account', 'expired'])
def test_revoked_after_auth_cannot_write(client, account, monkeypatch, endpoint, revocation):
    module, path, method, body = request_for(client, endpoint)
    workspace = account[2]
    with Session(engine()) as db:
        original = copy.deepcopy(db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == workspace)).data)
        revision = db.scalar(select(SetupDraft.version).where(SetupDraft.workspace_id == workspace))
    real_require = module.require

    def revoke_after_dependency(p, *roles):
        real_require(p, *roles)
        with Session(engine()) as revoker:
            if revocation == 'role':
                row = revoker.scalar(select(Membership).where(Membership.user_id == p.user_id,
                    Membership.workspace_id == p.workspace_id)); row.role = 'viewer'
            elif revocation == 'account':
                row = revoker.get(User, p.user_id); row.active = False
            else:
                row = revoker.get(AuthSession, p.session_id)
                if revocation == 'session': row.revoked = True
                else: row.expires_at = now() - timedelta(seconds=1)
            revoker.commit()

    monkeypatch.setattr(module, 'require', revoke_after_dependency)
    assert getattr(client, method)(path, json=body).status_code == 403
    with Session(engine()) as db:
        saved = db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == workspace))
        assert saved.version == revision and saved.data == original


@pytest.mark.parametrize('endpoint', ['append', 'revalidate', 'draft', 'content_review'])
@pytest.mark.parametrize('revocation', ['role', 'session', 'account'])
def test_identity_locks_are_held_until_commit(client, monkeypatch, endpoint, revocation):
    module, path, method, body = request_for(client, endpoint)
    original_lock = setup_content_review._lock_actor
    observed = []

    def check_locked(db, p):
        original_lock(db, p)
        with Session(engine()) as other:
            other.execute(text("SET LOCAL lock_timeout = '250ms'"))
            if revocation == 'role':
                sql = text("UPDATE memberships SET role='viewer' WHERE user_id=:u AND workspace_id=:w")
                params = {'u': p.user_id, 'w': p.workspace_id}
            elif revocation == 'account':
                sql = text('UPDATE users SET active=false WHERE id=:u'); params = {'u': p.user_id}
            else:
                sql = text('UPDATE auth_sessions SET revoked=true WHERE id=:s'); params = {'s': p.session_id}
            with pytest.raises(OperationalError):
                other.execute(sql, params)
            other.rollback()
        observed.append(True)

    monkeypatch.setattr(setup_content_review, '_lock_actor', check_locked)
    reply = getattr(client, method)(path, json=body)
    assert reply.status_code == 200, reply.text
    assert observed == [True]


@pytest.mark.parametrize('endpoint', ['append', 'revalidate', 'draft', 'content_review'])
def test_concurrent_identity_change_returns_conflict_without_writing(client, account, monkeypatch, endpoint):
    module, path, method, body = request_for(client, endpoint)
    workspace = account[2]
    with Session(engine()) as db:
        saved = db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == workspace))
        before, revision = copy.deepcopy(saved.data), saved.version
    original_lock = setup_content_review._lock_actor
    observed = []

    def revoke_in_progress(db, p):
        # An independent revoker owns the row; no wait and no partial draft.
        with Session(engine()) as revoker:
            revoker.execute(text("UPDATE memberships SET role='viewer' WHERE user_id=:u AND workspace_id=:w"),
                            {'u': p.user_id, 'w': p.workspace_id})
            try:
                return original_lock(db, p)
            finally:
                revoker.rollback()
                observed.append(True)

    monkeypatch.setattr(setup_content_review, '_lock_actor', revoke_in_progress)
    reply = getattr(client, method)(path, json=body)
    assert reply.status_code == 409, reply.text
    assert '权限正在更新' in reply.text and 'SQL' not in reply.text
    assert observed == [True]
    with Session(engine()) as db:
        saved = db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == workspace))
        assert saved.version == revision and saved.data == before


def test_revoked_first_write_does_not_create_a_blank_draft(client, account, monkeypatch):
    source = material(client)
    proposal = bundle(source)
    with Session(engine()) as db:
        assert db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == account[2])) is None
    real_require = setup_business_candidates.require
    def revoke_after_dependency(p, *roles):
        real_require(p, *roles)
        with Session(engine()) as db:
            session = db.get(AuthSession, p.session_id); session.revoked = True; db.commit()
    monkeypatch.setattr(setup_business_candidates, 'require', revoke_after_dependency)
    reply = client.post('/api/setup/candidates', json={'revision': 1, 'bundle': proposal})
    assert reply.status_code == 403
    with Session(engine()) as db:
        assert db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == account[2])) is None


def test_three_party_legacy_identity_first_and_revocation_do_not_deadlock(client, account, monkeypatch):
    from generic_agents.config import settings
    _, path, method, body = request_for(client, 'append')
    token = client.cookies.get(settings().session_cookie_name)
    with Session(engine()) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        principal = Principal(session.user_id, account[2], 'admin', session.id)
    original_lock = setup_content_review._lock_actor
    identity_held, draft_held, revoker_started = threading.Event(), threading.Event(), threading.Event()
    failures, details = [], {}

    def legacy():
        try:
            with Session(engine()) as db:
                original_lock(db, principal)
                identity_held.set()
                assert draft_held.wait(5)
                db.execute(text("SET LOCAL lock_timeout = '4s'"))
                db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == account[2]).with_for_update())
                db.rollback()
        except BaseException as exc:
            failures.append(type(exc).__name__)

    def revoke():
        try:
            assert identity_held.wait(5)
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout = '4s'"))
                details['revoker_pid'] = db.scalar(text('SELECT pg_backend_pid()'))
                revoker_started.set()
                db.execute(text("UPDATE memberships SET role='viewer' WHERE user_id=:u AND workspace_id=:w"),
                           {'u': principal.user_id, 'w': principal.workspace_id})
                db.commit()
        except BaseException as exc:
            failures.append(type(exc).__name__)

    def candidate_lock(db, p):
        draft_held.set()
        assert revoker_started.wait(5)
        # Verify the third transaction is actually waiting, not just started.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with Session(engine()) as observer:
                waiting = observer.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),
                                          {'pid': details['revoker_pid']})
            if waiting:
                break
            time.sleep(.01)
        assert waiting
        return original_lock(db, p)

    first = threading.Thread(target=legacy); second = threading.Thread(target=revoke)
    first.start(); assert identity_held.wait(5); second.start()
    monkeypatch.setattr(setup_content_review, '_lock_actor', candidate_lock)
    began = time.monotonic()
    response = getattr(client, method)(path, json=body)
    first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive() and not failures
    assert time.monotonic() - began < 5
    # Either our write acquired its shared lock first, or it failed closed
    # while revocation was queued; both must complete without a deadlock.
    assert response.status_code in (200, 409), response.text


def test_voice_application_uses_existing_draft_first_order(client, monkeypatch):
    from uuid import uuid4
    draft = client.get('/api/setup/draft').json()
    events = []
    load = setup_api.draft
    def checked_draft(db, p, lock=False):
        if lock:
            events.append('draft')
        return load(db, p, lock)
    monkeypatch.setattr(setup_api, 'draft', checked_draft)
    reply = client.post('/api/setup/voice/apply', json={'revision': draft['revision'],
        'audio_version': 1, 'audio_run_id': str(uuid4()), 'request_id': str(uuid4()),
        'text': '合成输入', 'target': 'goal', 'mode': 'append'})
    assert reply.status_code == 404, reply.text
    assert events == ['draft']


def test_automation_review_uses_existing_draft_first_order(client, monkeypatch):
    from uuid import uuid4
    from tests.test_setup_automation_recovery_v2 import install_legacy, state
    view = install_legacy(client)
    events = []
    actor = setup_api.automation_review_actor
    load = setup_api.draft
    def checked_actor(db, p):
        events.append('actor')
        return actor(db, p)
    def checked_draft(db, p, lock=False):
        if lock:
            events.append('draft')
            assert 'actor' not in events
        return load(db, p, lock)
    monkeypatch.setattr(setup_api, 'automation_review_actor', checked_actor)
    monkeypatch.setattr(setup_api, 'draft', checked_draft)
    reply = client.post('/api/setup/automation/review', json={'revision': view['revision'],
        'business_id': state(view)['business_id'], 'request_id': str(uuid4())})
    assert reply.status_code == 200, reply.text
    assert events[:2] == ['draft', 'actor']
