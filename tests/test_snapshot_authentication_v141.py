"""Explicit auth-session doubles test dependency safety without a DB or SQL.

Root's test_snapshot_read_pool_v141 separately exercises real auth and PG.
These tests do not replace that concurrency/connection-lifetime evidence.
"""
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents import db as database
from generic_agents import signal_dual_read_session, material_ocr_read_session
from generic_agents.security import Principal, authenticated
from generic_agents.snapshot_authentication_v141 import snapshot_authenticated


class AuthRead:
    def __init__(self, *, pending=None, close_error=None):
        self.new = set(); self.dirty = set(); self.deleted = set()
        if pending is not None: getattr(self, pending).add(object())
        self.close_error = close_error; self.closes = 0

    def close(self):
        self.closes += 1
        if self.close_error is not None: raise self.close_error

    def commit(self):
        raise AssertionError('A completed authentication read must never commit')


def principal():
    return Principal('original-actor', 'original-workspace', 'operator', 'original-auth-session')


def test_clean_auth_read_is_returned_before_snapshot_without_replacing_principal():
    original, db = principal(), AuthRead()
    assert snapshot_authenticated(original, db) is original
    assert db.closes == 1 and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('pending', ['new', 'dirty', 'deleted'])
def test_any_pending_auth_write_blocks_release_instead_of_discarding_it(pending):
    db = AuthRead(pending=pending)
    with pytest.raises(HTTPException) as failure:
        snapshot_authenticated(principal(), db)
    assert failure.value.status_code == 409
    assert db.closes == 0 and len(getattr(db, pending)) == 1


def test_close_failure_propagates_unchanged():
    original_error = RuntimeError('synthetic auth connection return failure')
    db = AuthRead(close_error=original_error)
    with pytest.raises(RuntimeError) as failure:
        snapshot_authenticated(principal(), db)
    assert failure.value is original_error and db.closes == 1


@pytest.mark.parametrize('reads', [signal_dual_read_session, material_ocr_read_session], ids=['task-dual', 'ocr'])
@pytest.mark.parametrize('failure,status', [('unauthorized', 401), ('dirty', 409), ('close_error', 500)])
def test_failed_auth_release_never_checks_out_a_snapshot(monkeypatch, reads, failure, status):
    db = AuthRead(pending='dirty' if failure == 'dirty' else None,
        close_error=RuntimeError('synthetic close failure') if failure == 'close_error' else None)
    calls = []

    def auth_session():
        calls.append('auth_session'); yield db

    def original_auth(auth_db: Session = Depends(database.session)):
        assert auth_db is db; calls.append('authenticated')
        if failure == 'unauthorized': raise HTTPException(401, 'Authentication required')
        return principal()

    def forbidden_snapshot_engine():
        calls.append('snapshot_checkout')
        raise AssertionError('A failed auth dependency must not start the snapshot')

    monkeypatch.setattr(reads, 'engine', forbidden_snapshot_engine)
    app = FastAPI()
    app.dependency_overrides[database.session] = auth_session
    app.dependency_overrides[authenticated] = original_auth

    @app.get('/snapshot')
    def data(original: Principal = Depends(authenticated), snapshot: Session = Depends(reads.read_session)):
        calls.append('handler')
        raise AssertionError('A failed dependency must not enter the read handler')

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/snapshot')
    assert response.status_code == status
    assert calls == ['auth_session', 'authenticated']
    assert db.closes == (1 if failure == 'close_error' else 0)
    if failure == 'dirty': assert len(db.dirty) == 1


def test_dependency_cache_releases_the_original_auth_session_once():
    original, db, calls = principal(), AuthRead(), []

    def auth_session():
        calls.append('auth_session'); yield db

    def original_auth(auth_db: Session = Depends(database.session)):
        assert auth_db is db; calls.append('authenticated'); return original

    app = FastAPI()
    app.dependency_overrides[database.session] = auth_session
    app.dependency_overrides[authenticated] = original_auth

    @app.get('/principal')
    def data(first: Principal = Depends(authenticated), released: Principal = Depends(snapshot_authenticated)):
        assert first is released is original and db.closes == 1
        return {'user_id': released.user_id}

    with TestClient(app) as client:
        response = client.get('/principal')
    assert response.status_code == 200 and response.json() == {'user_id': original.user_id}
    assert calls == ['auth_session', 'authenticated'] and db.closes == 1
