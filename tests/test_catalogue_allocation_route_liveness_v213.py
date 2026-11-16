"""Route scheduling and bounded-body tests, not source/IAM/paid acceptance.

These invoke the real async routes, Starlette Request streams and strict DTO
decoders. Only the synchronous service boundary is replaced with a public test
result. The test bodies create no database session, model transport or
successful Consumer and do not exercise source or paid authority.
"""
import asyncio
import copy
import hashlib
import json
import threading
from contextlib import suppress
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_business_catalogue_allocation_end_v213 as ending
from tests.test_report_business_catalogue_allocation_consumer_v213 import frozen


ROUTES = ('preview', 'start', 'end')


class RecordingTransaction:
    """A transaction boundary spy, deliberately not a database/session fake."""

    def __init__(self):
        self.events = []

    def commit(self):
        self.events.append(('commit', threading.get_ident()))

    def rollback(self):
        self.events.append(('rollback', threading.get_ident()))


def case_for(name):
    consumer_id = str(uuid4())
    if name == 'end':
        principal = SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()), role='operator')
        payload = {'request_id': str(uuid4()), 'expected_version': 3,
                   'confirm': True, 'retain_reserved_budget': True}
        return SimpleNamespace(name=name, principal=principal, payload=payload,
            consumer_id=consumer_id, path=ending.PREFIX + '/' + consumer_id + '/end-interrupted',
            model=ending.EndInterruptedBody, result={'test_boundary_only': 'end'})
    principal, value = frozen()
    payload = copy.deepcopy(value['body'])
    model = consumer.PreviewAllocationBody
    result = consumer.preview_view(value)
    if name == 'start':
        payload.update(request_id=payload['activity_id'], preview_hash=value['preview_hash'],
            plan_hash=value['preparation']['plan_hash'], batch_hash=value['preparation']['batch_hash'],
            consent=True)
        model = consumer.StartAllocationBody
        result = {'test_boundary_only': 'start'}
    return SimpleNamespace(name=name, principal=principal, payload=payload, consumer_id=consumer_id,
        path=consumer.PREFIX + '/' + name + '-allocation', model=model, result=result, frozen=value)


def encoded(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def streamed_request(path, chunks):
    """Omit Content-Length: the route must bound bytes actually received."""
    progress = SimpleNamespace(reads=0)

    async def receive():
        await asyncio.sleep(0)
        index = progress.reads
        progress.reads += 1
        if index >= len(chunks):
            raise AssertionError('route read past the complete test request')
        return {'type': 'http.request', 'body': chunks[index], 'more_body': index < len(chunks) - 1}

    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST',
             'scheme': 'http', 'path': path, 'raw_path': path.encode('ascii'), 'root_path': '',
             'query_string': b'', 'headers': [(b'content-type', b'application/json')],
             'client': ('127.0.0.1', 12345), 'server': ('testserver', 80)}
    return Request(scope, receive), progress


async def invoke(case, request, transaction):
    if case.name == 'preview':
        return await consumer.preview_allocation_route(request, case.principal, transaction)
    if case.name == 'start':
        return await consumer.start_allocation_route(request, case.principal, transaction)
    return await ending.end_route(case.consumer_id, request, case.principal, transaction)


def replace_service(monkeypatch, case, transaction, behavior):
    """Keep real wire parsing; replace precisely the synchronous service call."""
    raw = encoded(case.payload)

    def check_and_call(*args, **kwargs):
        assert args[0] is transaction and args[1] is case.principal
        if case.name == 'end':
            assert args[2] == case.consumer_id
            body, exact, sha = args[3:]
        elif case.name == 'start':
            body, exact, sha = args[2:]
        else:
            assert kwargs == {'lock': False}
            body = args[2]
            exact = sha = None
        assert type(body) is case.model
        assert body.model_dump(mode='json') == case.payload
        if case.name != 'preview':
            assert exact.encode('utf-8') == raw
            assert sha == hashlib.sha256(raw).hexdigest()
        return behavior()

    if case.name == 'preview':
        monkeypatch.setattr(consumer, 'prepare_allocation', check_and_call)
    elif case.name == 'start':
        monkeypatch.setattr(consumer, '_start_allocation', check_and_call)
    else:
        monkeypatch.setattr(ending, 'end', check_and_call)


@pytest.mark.parametrize('name', ROUTES)
def test_blocked_sync_service_leaves_the_same_event_loop_responsive(name, monkeypatch):
    case = case_for(name)
    transaction = RecordingTransaction()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    service_threads = []

    def blocked_service():
        service_threads.append(threading.get_ident())
        started.set()
        try:
            # Bounded even if the route regresses and blocks the event loop.
            assert release.wait(10), 'synchronous service blocked the event loop before release'
            return case.frozen if name == 'preview' else case.result
        finally:
            finished.set()

    replace_service(monkeypatch, case, transaction, blocked_service)
    wire = encoded(case.payload)
    request, _ = streamed_request(case.path, [wire[:7], wire[7:]])

    async def scenario():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        task = asyncio.create_task(invoke(case, request, transaction))

        async def await_service_entry():
            while not started.is_set():
                if task.done():
                    await task
                    pytest.fail('route finished without calling the service boundary')
                await asyncio.sleep(0.005)

        try:
            await asyncio.wait_for(await_service_entry(), timeout=15)
            if task.done():
                await task  # Preserve an actionable service/DTO failure.
                pytest.fail('route completed while its service was meant to be blocked')
            assert not finished.is_set()
            pulse = loop.create_future()
            loop.call_soon(pulse.set_result, threading.get_ident())
            assert await asyncio.wait_for(pulse, timeout=5) == loop_thread
            assert not task.done() and not finished.is_set()
            assert len(service_threads) == 1 and service_threads[0] != loop_thread
            release.set()
            assert await asyncio.wait_for(task, timeout=5) == case.result
            if name == 'end':
                assert transaction.events == [('commit', service_threads[0])]
            else:
                # The start service is mocked as a whole; this test makes no
                # assertion about its real database persistence or paid work.
                assert transaction.events == []
        finally:
            release.set()
            # Drain the worker after every assertion failure; never leave a
            # blocked test service or an unobserved task in the next test.
            with suppress(BaseException):
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)

    asyncio.run(scenario())
    assert finished.is_set()


@pytest.mark.parametrize('name', ROUTES)
def test_chunked_over_capacity_request_stops_at_413_before_service(name, monkeypatch):
    case = case_for(name)
    transaction = RecordingTransaction()
    service_calls = []

    def must_not_run():
        service_calls.append(True)
        raise AssertionError('over-capacity body reached a service')

    replace_service(monkeypatch, case, transaction, must_not_run)
    wire = encoded(case.payload)
    assert len(wire) < consumer.MAX_WIRE_BYTES
    # First two chunks are a valid JSON value plus whitespace, exactly at the
    # cap. The third crosses the cap; the fourth must never be requested.
    request, progress = streamed_request(case.path, [wire,
        b' ' * (consumer.MAX_WIRE_BYTES - len(wire)), b' ', b'unread-after-limit'])
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(case, request, transaction))
    assert error.value.status_code == 413
    assert progress.reads == 3
    assert service_calls == [] and transaction.events == []


@pytest.mark.parametrize('name', ROUTES)
def test_route_uses_the_closed_strict_dto_before_service(name, monkeypatch):
    case = case_for(name)
    transaction = RecordingTransaction()
    service_calls = []

    def must_not_run():
        service_calls.append(True)
        raise AssertionError('unknown request field reached a service')

    replace_service(monkeypatch, case, transaction, must_not_run)
    request, _ = streamed_request(case.path, [encoded({**case.payload, 'unknown_privilege': True})])
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(case, request, transaction))
    assert error.value.status_code == 422
    assert service_calls == [] and transaction.events == []


class BoundaryAbort(BaseException):
    """Check cleanup beyond Exception without a process-level interrupt."""


@pytest.mark.parametrize('failure_type', [RuntimeError, BoundaryAbort])
def test_end_rolls_back_in_the_same_worker_thread_and_preserves_the_failure(failure_type, monkeypatch):
    case = case_for('end')
    transaction = RecordingTransaction()
    failure = failure_type('public service-boundary failure')
    service_threads = []
    caller_thread = threading.get_ident()

    def fail_service():
        service_threads.append(threading.get_ident())
        raise failure

    replace_service(monkeypatch, case, transaction, fail_service)
    request, _ = streamed_request(case.path, [encoded(case.payload)])
    with pytest.raises(failure_type) as error:
        asyncio.run(invoke(case, request, transaction))
    assert error.value is failure
    assert len(service_threads) == 1 and service_threads[0] != caller_thread
    assert transaction.events == [('rollback', service_threads[0])]
