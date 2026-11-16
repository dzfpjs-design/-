"""Real async approval adapters with explicit blocking transaction boundaries.

These are event-loop/transaction tests, not PostgreSQL or authenticated HTTP
acceptance.  The DTO parser and Starlette threadpool implementation stay real.
"""
import asyncio
import hashlib
import json
import socket
import subprocess
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from generic_agents import layered_approval_api_v175 as api


ID = '11111111-1111-1111-1111-111111111111'
UNIT = '22222222-2222-2222-2222-222222222222'


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('approval async adapter tests must not connect or spawn processes')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)


def raw_for(kind):
    value = ({'request_id': ID, 'unit_id': UNIT, 'expected_version': 0,
        'name': '组织范围', 'parent_id': None, 'enabled': True} if kind == 'setting' else
        {'request_id': ID, 'expected_version': 1, 'reason': '核对原审批\u00a0依据'})
    # Intentionally non-canonical whitespace and Unicode remain original wire.
    return ('  ' + json.dumps(value, ensure_ascii=False, indent=1) + '\n').encode('utf-8')


def request_for(raw, events):
    delivered = False

    async def receive():
        nonlocal delivered
        assert not delivered
        delivered = True
        events.append(('body', get_ident()))
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)


class DB:
    def __init__(self, events, commit_error=None):
        self.events = events
        self.commits = 0
        self.commit_error = commit_error

    def commit(self):
        self.events.append(('commit', get_ident()))
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error


async def invoke(kind, request, principal, db):
    if kind == 'setting':
        return await api.setting(request, api.UnitCommand, 'unit', principal, db)
    return await api.task_command(UNIT, request, api.TaskCommand, 'submit', principal, db)


def patch_store(monkeypatch, kind, function):
    monkeypatch.setattr(api.store, 'configure' if kind == 'setting' else 'perform', function)


def check_arguments(kind, args, db, principal, raw):
    assert args[:2] == (db, principal)
    if kind == 'setting':
        assert args[2] == 'unit'
        body, text, sha = args[3:]
        assert isinstance(body, api.UnitCommand) and body.unit_id == UNIT
        assert body.expected_version == 0 and body.enabled is True
    else:
        assert args[2:4] == (UNIT, 'submit')
        body, text, sha = args[4:]
        assert isinstance(body, api.TaskCommand) and body.expected_version == 1
    assert body.request_id == ID
    assert text.encode('utf-8') == raw and sha == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('kind', ['setting', 'task'])
def test_blocked_store_keeps_event_loop_responsive_and_commits_in_same_worker(monkeypatch, kind):
    events = []; principal = SimpleNamespace(user_id=ID, workspace_id=UNIT, role='operator')
    raw = raw_for(kind); db = DB(events); response = {'request_id': ID, 'saved': True}
    released = Event()

    async def scenario():
        loop_thread = get_ident()
        loop = asyncio.get_running_loop(); entered = asyncio.Event()

        def blocked_store(*args):
            check_arguments(kind, args, db, principal, raw)
            events.append(('store', get_ident()))
            loop.call_soon_threadsafe(entered.set)
            # Old async adapters block the loop here: the other coroutine is
            # unable to observe entered and release the simulated SQL wait.
            assert released.wait(timeout=5), 'store SQL wait blocked the event loop'
            events.append(('store_done', get_ident()))
            assert db.commits == 0
            return response

        async def release_from_event_loop():
            await entered.wait()
            assert get_ident() == loop_thread and db.commits == 0
            events.append(('other_coroutine', get_ident()))
            released.set()

        patch_store(monkeypatch, kind, blocked_store)
        result, _ = await asyncio.wait_for(asyncio.gather(
            invoke(kind, request_for(raw, events), principal, db), release_from_event_loop()), timeout=10)
        assert result is response
        assert [name for name, _ in events] == ['body', 'store', 'other_coroutine', 'store_done', 'commit']
        threads = dict(events)
        assert threads['body'] == threads['other_coroutine'] == loop_thread
        assert threads['store'] == threads['store_done'] == threads['commit'] != loop_thread
        assert db.commits == 1

    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['setting', 'task'])
@pytest.mark.parametrize('status', [403, 409, 422])
def test_store_http_rejection_is_unchanged_and_never_commits(monkeypatch, kind, status):
    events = []; raw = raw_for(kind); db = DB(events); principal = object()
    rejection = HTTPException(status, {'original_request': ID, 'reason': '原门拒绝'})
    loop_thread = get_ident()

    def rejected(*args):
        check_arguments(kind, args, db, principal, raw)
        events.append(('store_rejected', get_ident()))
        raise rejection

    patch_store(monkeypatch, kind, rejected)
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(kind, request_for(raw, events), principal, db))
    assert error.value is rejection and db.commits == 0
    assert events[0] == ('body', loop_thread) and events[1][1] != loop_thread


def test_task_value_error_retains_original_409_and_does_not_commit(monkeypatch):
    events = []; db = DB(events)

    def rejected(*args):
        events.append(('store_rejected', get_ident()))
        raise ValueError('original transition rejected')

    patch_store(monkeypatch, 'task', rejected)
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke('task', request_for(raw_for('task'), events), object(), db))
    assert error.value.status_code == 409
    assert error.value.detail == '当前审批状态或提交内容不允许本次操作，原记录与输入保留'
    assert db.commits == 0


def test_setting_value_error_keeps_existing_exception_semantics(monkeypatch):
    events = []; db = DB(events); rejection = ValueError('original setting rejection')

    def rejected(*args):
        raise rejection

    patch_store(monkeypatch, 'setting', rejected)
    with pytest.raises(ValueError) as error:
        asyncio.run(invoke('setting', request_for(raw_for('setting'), events), object(), db))
    assert error.value is rejection and db.commits == 0


@pytest.mark.parametrize('kind', ['setting', 'task'])
def test_commit_failure_is_not_relabelled_as_unapplied_store_rejection(monkeypatch, kind):
    events = []; failed_commit = ValueError('original commit failure')
    db = DB(events, failed_commit); raw = raw_for(kind); principal = object()

    def saved(*args):
        check_arguments(kind, args, db, principal, raw)
        events.append(('store_done', get_ident()))
        return {'request_id': ID}

    patch_store(monkeypatch, kind, saved)
    with pytest.raises(ValueError) as error:
        asyncio.run(invoke(kind, request_for(raw, events), principal, db))
    assert error.value is failed_commit and db.commits == 1
    threads = dict(events)
    assert threads['store_done'] == threads['commit'] != threads['body']


@pytest.mark.parametrize('kind', ['setting', 'task'])
def test_bad_original_dto_is_rejected_before_store_or_commit(monkeypatch, kind):
    events = []; db = DB(events)

    def forbidden(*args):
        raise AssertionError('bad original wire must not reach SQL work')

    patch_store(monkeypatch, kind, forbidden)
    raw = b'{"request_id":"not-a-valid-uuid","expected_version":true}'
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(kind, request_for(raw, events), object(), db))
    assert error.value.status_code == 422 and db.commits == 0
    assert [name for name, _ in events] == ['body']
