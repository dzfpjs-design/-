"""Owned-PG security and concurrency checks using public synthetic source only.

The Root-owned isolated driver supplies the database/account fixtures. Every
extra client logs in normally; no principal, dependency or pipeline is stubbed.
No worker/model is run, no seed is exported, and no browser/restart is claimed.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import AuthSession, Audit, CloudCall, Membership, SetupReportDraft, User, Workspace
from generic_agents.report_source_requirement_models import SetupRequirementCheck
from generic_agents.security import password_hash, token_hash
from tests.test_report_source_requirements_postgres import (
    BASE, SyntheticHTTP, body, fence, full_source_scene, local_runtime,
    reading_scene, reply, start, v5_scene,
)


PUBLIC_PASSWORD = 'SourceRequirement_public_synthetic_20261018!'


@contextmanager
def normal_client(app, credentials, workspace_id):
    client = TestClient(app)
    try:
        login = client.post('/api/login', json={'username': credentials[0], 'password': credentials[1]})
        assert login.status_code == 200
        client.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': workspace_id})
        me = reply(client.get('/api/me'))
        assert me['username'] == credentials[0] and me['workspace_id'] == workspace_id
        assert client.cookies.get('ga_session')
        yield client
    finally:
        client.close()


def pending(scene, monkeypatch):
    fence()
    http = SyntheticHTTP(monkeypatch)
    _, _, accepted = start(scene, http)
    identity = accepted['draft']['id']
    view = reply(scene['client'].get(BASE + '/' + identity + '/requirement-review'))
    assert view['candidate_available'] is False and view['binding']['candidate_hash'] is None
    assert http.requests == []
    return http, identity, view


def save(client, identity, command):
    return client.put(BASE + '/' + identity + '/requirement-checks/' + command['check_id'], json=command)


@contextmanager
def viewer_membership(principal):
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(
            Membership.user_id == principal.user_id, Membership.workspace_id == principal.workspace_id))
        original_role = member.role
        member.role = 'viewer'; db.commit()
    try:
        yield
    finally:
        # Restore only the deliberately changed membership. The tested login's
        # revocation remains durable; no auth session is un-revoked here.
        with Session(engine()) as db:
            member = db.scalar(select(Membership).where(
                Membership.user_id == principal.user_id, Membership.workspace_id == principal.workspace_id))
            member.role = original_role; db.commit()


def draft_snapshot(identity):
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, identity)
        return copy.deepcopy((row.version, row.status, row.data))


def records(identity, check_id):
    with Session(engine()) as db:
        rows = db.scalars(select(SetupRequirementCheck).where(
            SetupRequirementCheck.draft_id == identity, SetupRequirementCheck.check_id == check_id)
            .order_by(SetupRequirementCheck.item_version)).all()
        return [{'version': row.item_version, 'request_id': row.request_id,
                 'data': copy.deepcopy(row.data)} for row in rows]


def assert_no_model_or_extra_save(http, scene, identity, check_id, original_draft):
    assert http.requests == []
    assert draft_snapshot(identity) == original_draft
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall).where(
            CloudCall.workspace_id == scene['p'].workspace_id)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.workspace_id == scene['p'].workspace_id,
            Audit.actor_id == scene['p'].user_id,
            Audit.action == 'setup.source_requirement.saved', Audit.object_id == check_id)) == 1


def test_other_actor_workspace_and_missing_workspace_or_csrf_cannot_touch_saved_check(v5_scene, monkeypatch, account):
    scene = v5_scene
    http, identity, view = pending(scene, monkeypatch)
    command = body(view)
    saved = reply(save(scene['client'], identity, command))
    before = draft_snapshot(identity)
    before_records = records(identity, command['check_id'])
    # Inline account/membership setup is synthetic; authentication still uses
    # the ordinary password verifier, cookie session, workspace and CSRF path.
    username = 'source_requirements_other_' + uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(PUBLIC_PASSWORD))
        workspace = Workspace(name='公开合成隔离工作区 ' + uuid4().hex)
        db.add_all([user, workspace]); db.flush()
        other_workspace = workspace.id
        db.add_all([
            Membership(user_id=user.id, workspace_id=scene['p'].workspace_id, role='operator'),
            Membership(user_id=scene['p'].user_id, workspace_id=other_workspace, role='operator'),
        ])
        db.commit()
    with ExitStack() as clients:
        other_actor = clients.enter_context(normal_client(scene['client'].app,
            (username, PUBLIC_PASSWORD), scene['p'].workspace_id))
        other_scope = clients.enter_context(normal_client(scene['client'].app, account, other_workspace))
        for client in (other_actor, other_scope):
            url = BASE + '/' + identity
            for response in (
                client.get(url + '/requirement-review'),
                client.get(url + '/requirement-receipts/' + command['request_id']),
                client.get(url + '/requirement-checks/' + command['check_id'] + '/history'),
                save(client, identity, command),
            ):
                assert response.status_code == 404
                assert response.json()['detail']['code'] == 'not_found'
                assert response.headers['cache-control'] == 'private, no-store'
        owner = clients.enter_context(normal_client(scene['client'].app, account, scene['p'].workspace_id))
        workspace_header = owner.headers.pop('X-Workspace-ID')
        try:
            for response in (owner.get(BASE + '/' + identity + '/requirement-review'), save(owner, identity, command)):
                assert response.status_code == 403
                assert response.json()['detail']['code'] == 'explicit_workspace_required'
        finally:
            owner.headers['X-Workspace-ID'] = workspace_header
        csrf_header = owner.headers.pop('X-CSRF-Token')
        try:
            response = save(owner, identity, command)
            assert response.status_code == 403 and response.json()['detail'] == 'CSRF validation failed'
        finally:
            owner.headers['X-CSRF-Token'] = csrf_header
        assert reply(owner.get(BASE + '/' + identity + '/requirement-receipts/' + command['request_id']))['saved']['receipt'] == saved['saved']['receipt']
    assert records(identity, command['check_id']) == before_records
    assert_no_model_or_extra_save(http, scene, identity, command['check_id'], before)


def test_viewer_may_restore_own_history_but_cannot_write_and_revoked_session_cannot_replay(v5_scene, monkeypatch, account):
    scene = v5_scene
    http, identity, view = pending(scene, monkeypatch)
    with normal_client(scene['client'].app, account, scene['p'].workspace_id) as client:
        command = body(view)
        saved = reply(save(client, identity, command))
        before = draft_snapshot(identity)
        before_records = records(identity, command['check_id'])
        with viewer_membership(scene['p']):
            assert reply(client.get('/api/me'))['role'] == 'viewer'
            restored = reply(save(client, identity, command))
            assert restored['replayed'] is True and restored['saved']['receipt'] == saved['saved']['receipt']
            assert restored['saved']['review_status']['status'] == 'history'
            assert reply(client.get(BASE + '/' + identity + '/requirement-receipts/' + command['request_id'])) == restored
            changed = copy.deepcopy(command)
            changed.update(request_id=str(uuid4()), expected_version=1)
            changed['value']['reason'] += ' 新的人工判断。'
            rejected = save(client, identity, changed)
            assert rejected.status_code == 403 and rejected.json()['detail']['code'] == 'permission_changed'
            # Revoke this real login's exact persisted cookie session, not a fake
            # principal. Even an old, byte-identical nonce must authenticate first.
            with Session(engine()) as db:
                session = db.scalar(select(AuthSession).where(
                    AuthSession.user_id == scene['p'].user_id,
                    AuthSession.token_hash == token_hash(client.cookies.get('ga_session'))))
                assert session is not None and session.revoked is False
                session.revoked = True; db.commit()
            assert client.get(BASE + '/' + identity + '/requirement-receipts/' + command['request_id']).status_code == 401
            assert save(client, identity, command).status_code == 401
    assert records(identity, command['check_id']) == before_records
    assert_no_model_or_extra_save(http, scene, identity, command['check_id'], before)


@pytest.mark.parametrize('same_nonce', [False, True], ids=['different_nonce_cas', 'same_nonce_replay'])
def test_two_normal_sessions_race_without_duplicate_check_versions_or_receipts(v5_scene, monkeypatch, account, same_nonce):
    scene = v5_scene
    http, identity, view = pending(scene, monkeypatch)
    first = body(view)
    second = copy.deepcopy(first)
    if not same_nonce:
        second['request_id'] = str(uuid4())
        second['value']['reason'] += ' 另一会话的独立判断。'
    assert first['expected_version'] == second['expected_version'] == 0
    assert records(identity, first['check_id']) == []
    before = draft_snapshot(identity)
    with ExitStack() as clients:
        sessions = [clients.enter_context(normal_client(scene['client'].app, account, scene['p'].workspace_id)) for _ in range(2)]
        assert sessions[0].cookies.get('ga_session') != sessions[1].cookies.get('ga_session')
        barrier = Barrier(2)
        def submit(client, command):
            barrier.wait(timeout=10)
            return save(client, identity, command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, client, command) for client, command in zip(sessions, (first, second))]
            responses = [future.result(timeout=30) for future in futures]
        if same_nonce:
            assert [response.status_code for response in responses] == [200, 200]
            results = [reply(response) for response in responses]
            assert sorted(result['replayed'] for result in results) == [False, True]
            assert results[0]['saved']['receipt'] == results[1]['saved']['receipt']
            winner_index = next(index for index, result in enumerate(results) if result['replayed'] is False)
        else:
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner_index = next(index for index, response in enumerate(responses) if response.status_code == 200)
            loser = responses[1 - winner_index]
            assert loser.json()['detail']['code'] == 'requirement_version_conflict'
            assert loser.json()['detail']['current_version'] == 1
        winner = reply(responses[winner_index])
        original = (first, second)[winner_index]
        rows = records(identity, first['check_id'])
        assert len(rows) == 1 and rows[0]['version'] == 1 and rows[0]['request_id'] == original['request_id']
        assert rows[0]['data']['checked']['record'] == original['value']
        assert rows[0]['data']['receipt'] == winner['saved']['receipt']
        history = reply(sessions[0].get(BASE + '/' + identity + '/requirement-checks/' + first['check_id'] + '/history'))
        assert [entry['version'] for entry in history['entries']] == [1]
        assert history['entries'][0]['receipt'] == winner['saved']['receipt']
        replay = reply(save(sessions[1 - winner_index], identity, original))
        assert replay['replayed'] is True and replay['saved']['review_status']['status'] == 'history'
        assert replay['saved']['receipt'] == winner['saved']['receipt']
        assert records(identity, first['check_id']) == rows
    assert_no_model_or_extra_save(http, scene, identity, first['check_id'], before)
