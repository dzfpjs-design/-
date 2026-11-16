"""Real auth+PG concurrency on the production read-snapshot dependencies.

A two-slot pool and an auth barrier amplify the real double-checkout path.
The barrier changes scheduling only: credentials, auth SQL and snapshots are
original production functions. This is not a full-workspace/browser claim.
"""
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from generic_agents import db as database
from generic_agents import signal_dual_read_session, material_ocr_read_session
from generic_agents.security import authenticated, Principal


@pytest.mark.parametrize('reads', [signal_dual_read_session, material_ocr_read_session], ids=['task-dual', 'ocr'])
def test_concurrent_real_auth_and_read_snapshot_never_hold_two_pool_slots_per_request(client, monkeypatch, reads):
    me = client.get('/api/me'); assert me.status_code == 200
    owned = database.engine().url
    pool = create_engine(owned, pool_size=2, max_overflow=0, pool_timeout=.7,
                         pool_pre_ping=True, hide_parameters=True)
    label = contextvars.ContextVar('owned_snapshot_request', default='outside')
    lock = threading.Lock(); active = {}; current = {}; peak = {}
    def checkout(connection, record, proxy):
        name = label.get()
        with lock:
            assert id(connection) not in active
            active[id(connection)] = name; current[name] = current.get(name, 0) + 1
            peak[name] = max(peak.get(name, 0), current[name])
    def checkin(connection, record):
        with lock:
            name = active.pop(id(connection)); current[name] -= 1
    event.listen(pool, 'checkout', checkout); event.listen(pool, 'checkin', checkin)
    gate = threading.Barrier(2)
    # Same cached database.session object is used by original auth and any
    # production snapshot-auth release dependency; never replace the principal.
    def scheduled_auth(request: Request, db: Session = Depends(database.session)):
        value = authenticated(request, db)
        gate.wait(timeout=10)
        return value
    app = FastAPI()
    @app.middleware('http')
    async def identify(request, call_next):
        token = label.set(request.headers.get('x-owned-request', 'missing'))
        try: return await call_next(request)
        finally: label.reset(token)
    app.dependency_overrides[authenticated] = scheduled_auth
    @app.get('/snapshot')
    def original_dependencies(p: Principal = Depends(authenticated), db: Session = Depends(reads.read_session)):
        assert db.scalar(text('SELECT count(*) FROM users WHERE id=:id'), {'id': p.user_id}) == 1
        return {'user_id': p.user_id, 'workspace_id': p.workspace_id,
                'isolation': db.scalar(text('SHOW transaction_isolation')),
                'read_only': db.scalar(text('SHOW transaction_read_only'))}
    monkeypatch.setattr(database, 'engine', lambda: pool)
    monkeypatch.setattr(reads, 'engine', lambda: pool)
    headers = {key: value for key, value in client.headers.items()
               if key.lower() in {'authorization', 'x-csrf-token', 'x-workspace-id'}}
    try:
        with TestClient(app, raise_server_exceptions=False, cookies=client.cookies, headers=headers) as local:
            def read(index): return local.get('/snapshot', headers={'x-owned-request': 'request-' + str(index)})
            with ThreadPoolExecutor(max_workers=2) as workers:
                replies = list(workers.map(read, range(2)))
        assert [reply.status_code for reply in replies] == [200, 200], 'auth connections must be returned before snapshot checkout'
        for reply in replies:
            assert reply.json() == {'user_id': me.json()['user_id'], 'workspace_id': me.json()['workspace_id'],
                                    'isolation': 'repeatable read', 'read_only': 'on'}
        assert peak == {'request-0': 1, 'request-1': 1}
        assert current == {'request-0': 0, 'request-1': 0} and not active and pool.pool.checkedout() == 0
    finally:
        assert pool.pool.checkedout() == 0
        pool.dispose()
