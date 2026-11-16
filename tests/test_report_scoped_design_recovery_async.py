"""Real async recovery/save adapters over explicit synchronous I/O seams.

Actual parsing, ownership, replay, role/CAS guards, transition, command hashes
and Starlette threadpool run. Inventory/SQL, historical response and manual
source validation are synthetic boundaries, not persistence or quality proof.
"""
import asyncio
import copy
import hashlib
import json
import socket
import subprocess
from threading import Event, get_ident
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from generic_agents import report_scoped_design_consumer_v181 as app


def uid(number):
    return str(UUID(int=number))


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Async I/O boundary tests must not contact a service or process')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def scene(monkeypatch, kind):
    events = []
    def mark(name):
        events.append((name, get_ident()))
    p = NS(user_id=uid(1), workspace_id=uid(2), role='operator')
    row = NS(id=uid(3), workspace_id=p.workspace_id, actor_id=p.user_id,
        plan_id=uid(4), plan_version=1, plan_hash='a' * 64, scope_id='public:scope',
        request_id=uid(5), producer_id=uid(6), model_id=uid(7), grant_hash='b' * 64,
        revision=1, version=3, status='review', job_id=uid(8), reserved_call_id=uid(9),
        data={'dispatch_started': True, 'command_refs': [], 'projection': {'synthetic': True},
            'preview': {'preparation': {'schema': app.profiles.MARKERS[app.profiles.FLOW]}}})
    operation = 'recover-saved' if kind == 'recover' else 'save-review'
    path = app.PREFIX + '/' + row.id + '/' + operation
    body = {'request_id': uid(10), 'expected_version': row.version}
    if kind == 'save':
        body.update(value={'result': {'business': {'questions': []}}, 'note': '原人工稿＋🙂'}, confirmed=False)
    exact = (' \n' + json.dumps(body, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
    commands = {}
    class DB:
        def __init__(self):
            self.rows = []
            self.commits = 0
        def add(self, value):
            assert isinstance(value, app.Command), 'No Job, Attempt or cloud call may be added'
            mark('add_command')
            self.rows.append(value)
        def commit(self):
            mark('commit')
            self.commits += 1
    db = DB()
    def nonce(actual_db, actual_p, request_id):
        assert actual_db is db and actual_p is p and request_id == body['request_id']
        mark('nonce')
    def any_nonce(actual_db, actual_p, request_id):
        assert actual_db is db and actual_p is p and request_id == body['request_id']
        mark('all_family_nonce')
    def inventory(actual_db, actual_p, *, exclusive):
        assert actual_db is db and actual_p is p and exclusive is True
        mark('inventory')
        return {row.id: row}, commands, {}
    def actor(actual_db, actual_p):
        assert actual_db is db and actual_p is p
        mark('current_actor')
    def historical(actual_db, actual_p, actual_row, *, recover_saved):
        assert actual_db is db and actual_p is p and actual_row is row and recover_saved is True
        mark('read_saved_original')
        return copy.deepcopy(row.data), 'review'
    def manual(value, preview):
        assert value == body['value'] and preview == row.data['preview']
        mark('manual_source_gate')
        return copy.deepcopy(value)
    def audit(actual_db, actual_p, action, identity, data):
        assert actual_db is db and actual_p is p
        mark('audit:' + action)
    monkeypatch.setattr(app, '_nonce', nonce)
    monkeypatch.setattr(app, '_any_nonce', any_nonce)
    monkeypatch.setattr(app, '_inventory', inventory)
    monkeypatch.setattr(app, 'lock_current_actor', actor)
    monkeypatch.setattr(app, '_read_paid', historical)
    monkeypatch.setattr(app, '_manual', manual)
    monkeypatch.setattr(app, 'audit', audit)
    return NS(events=events, mark=mark, p=p, row=row, operation=operation, path=path,
        body=body, raw=exact, commands=commands, db=db, nonce=nonce)


def request(c):
    delivered = False
    async def receive():
        nonlocal delivered
        assert delivered is False
        delivered = True
        c.mark('read_body')
        return {'type': 'http.request', 'body': c.raw, 'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'path': c.path, 'headers': []}, receive)


async def invoke(c):
    route = app.recover_route if c.operation == 'recover-saved' else app.save_review_route
    return await route(c.row.id, request(c), c.p, c.db)


@pytest.mark.parametrize('kind', ['recover', 'save'])
def test_blocked_original_transaction_keeps_loop_responsive_and_commit_in_the_same_worker(monkeypatch, kind):
    c = scene(monkeypatch, kind)
    released = Event()
    async def run():
        loop = asyncio.get_running_loop()
        loop_thread = get_ident()
        entered = asyncio.Event()
        def blocked_nonce(db, p, nonce):
            c.nonce(db, p, nonce)
            loop.call_soon_threadsafe(entered.set)
            assert released.wait(3), 'synchronous database wait blocked the HTTP event loop'
            c.mark('nonce_released')
        monkeypatch.setattr(app, '_nonce', blocked_nonce)
        async def service_other_request():
            await entered.wait()
            assert get_ident() == loop_thread and c.db.commits == 0
            c.mark('other_request')
            released.set()
        result, _ = await asyncio.wait_for(asyncio.gather(invoke(c), service_other_request()), timeout=6)
        assert result == app.result(c.row, c.operation)
        assert c.db.commits == 1 and len(c.db.rows) == 1
        command = c.db.rows[0]
        assert command.request_id == c.body['request_id'] and command.path == c.path and command.method == 'POST'
        assert command.exact_body.encode('utf-8') == c.raw
        assert command.wire_sha256 == hashlib.sha256(c.raw).hexdigest()
        assert command.body_hash == app.h(c.body) and command.command_hash == app.h(app._command_pin(command))
        assert c.events[0] == ('read_body', loop_thread)
        assert [name for name, _ in c.events][-1] == 'commit'
        worker_threads = {thread for name, thread in c.events if name not in {'read_body', 'other_request'}}
        assert len(worker_threads) == 1 and loop_thread not in worker_threads
        names = [name for name, _ in c.events]
        assert names.index('nonce') < names.index('other_request') < names.index('nonce_released')
        assert names.index('nonce_released') < names.index('all_family_nonce') < names.index('inventory')
        assert names.index('inventory') < names.index('current_actor') < names.index('add_command') < names.index('commit')
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['recover', 'save'])
@pytest.mark.parametrize('mismatch', [False, True])
def test_actual_original_nonce_replay_and_different_body_rejection_precede_fresh_work(monkeypatch, kind, mismatch):
    c = scene(monkeypatch, kind)
    original_result = {'id': c.row.id, 'original_only': True}
    command = NS(request_id=c.body['request_id'], actor_id=c.p.user_id, operation=c.operation,
        path=c.path, exact_body=c.raw.decode('utf-8'), wire_sha256=hashlib.sha256(c.raw).hexdigest(),
        data={'result': original_result})
    c.commands['original-command'] = command
    if mismatch:
        command.exact_body += ' '
        with pytest.raises(HTTPException) as error:
            asyncio.run(invoke(c))
        assert error.value.status_code == 409
    else:
        assert asyncio.run(invoke(c)) == original_result
    assert [name for name, _ in c.events] == ['read_body', 'nonce', 'all_family_nonce', 'inventory']
    assert c.db.commits == 0 and c.db.rows == [] and c.row.version == 3


@pytest.mark.parametrize('kind', ['recover', 'save'])
@pytest.mark.parametrize('fault', ['role', 'version'])
def test_original_role_and_version_guards_still_refuse_before_source_work_or_commit(monkeypatch, kind, fault):
    c = scene(monkeypatch, kind)
    if fault == 'role': c.p.role = 'viewer'
    else: c.row.version += 1
    before = copy.deepcopy(c.row.data)
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(c))
    assert error.value.status_code == (403 if fault == 'role' else 409)
    names = [name for name, _ in c.events]
    assert names[:4] == ['read_body', 'nonce', 'all_family_nonce', 'inventory']
    assert ('current_actor' in names) is (fault == 'version')
    assert 'read_saved_original' not in names and 'manual_source_gate' not in names
    assert c.db.rows == [] and c.db.commits == 0 and c.row.data == before
