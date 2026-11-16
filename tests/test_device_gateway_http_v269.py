"""Actual HTTP privacy bounds and real-PG rejection of forged transport scope.

No adopted Brain/parent plan is supplied by these fixtures. They must never
cross the send boundary; positive business dispatch remains a separate gate.
"""
from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import leases, worker
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents import device_gateway_http_v269 as transport
from generic_agents import workflow_registered_tool_plan_v201 as parent
from generic_agents import connectors
from tests.test_device_gateway_tools_v269 import managed, scene, load, invocation


@contextmanager
def response_server(raw, *, status=200, encoding=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            if encoding:
                self.send_header('Content-Encoding', encoding)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port) + '/receipt'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_original_raw_callback_gets_exact_safe_http_bytes_only():
    raw = b'{"schema":"unit-http-boundary","value":25.0}'
    captured = []
    with response_server(raw) as endpoint:
        value = transport._exchange('GET', endpoint, 'synthetic-http-secret', 'unit-key',
            on_response=lambda body, metadata: captured.append((body, metadata)))
    assert value['value'] == 25 and captured == [(raw, {'status_code': 200, 'content_type': 'application/json'})]


@pytest.mark.parametrize('target', ['value', 'key'])
def test_escaped_bearer_echo_is_blocked_before_raw_capture(target):
    secret = 'private"bearer\\escaped-v269'
    raw = json.dumps({'data': secret} if target == 'value' else {secret: 'data'}).encode()
    captured = []
    with response_server(raw) as endpoint:
        with pytest.raises(HTTPException) as error:
            transport._exchange('GET', endpoint, secret, 'unit-key', on_response=lambda *x: captured.append(x))
    assert not captured and secret not in str(error.value.detail)


@pytest.mark.parametrize('raw,encoding,status', [
    (b'{"v":1,"v":2}', None, 200), (b'{"v":1e309}', None, 200),
    (b'{"v":NaN}', None, 200), (b'\xff', None, 200),
    (b'{"v":0}', 'gzip', 200), (b'{"v":0}', None, 302),
    (b'{"v":"' + b'x' * (1024 * 1024) + b'"}', None, 200),
])
def test_invalid_compressed_redirect_or_oversized_http_never_reaches_capture(raw, encoding, status):
    captured = []
    with response_server(raw, encoding=encoding, status=status) as endpoint:
        with pytest.raises(HTTPException):
            transport._exchange('GET', endpoint, 'synthetic-http-secret', 'unit-key', on_response=lambda *x: captured.append(x))
    assert not captured


def test_legacy_transports_cannot_borrow_managed_credentials(monkeypatch):
    execution = {'credential_scope': 'device-gateway-tool-v269',
        'device_gateway_connection_id': 'unit-scope', 'endpoint': 'http://127.0.0.1:1/invoke',
        'secret_ref': 'GA_CONNECTOR_SECRET_UNRELATED'}
    monkeypatch.setenv('GA_CONNECTOR_SECRET_UNRELATED', 'must-never-be-used')
    for call in [lambda: connectors.headers(execution),
                 lambda: connectors.call_http(execution, {}, 'unit-key'),
                 lambda: connectors.query_receipt(execution, 'unit-key')]:
        with pytest.raises(HTTPException):
            call()


def test_matching_ownership_dictionary_without_installed_fence_is_not_authority(managed, monkeypatch):
    s = managed
    with Session(engine()) as db:
        call, inputs, key = invocation(db, s); db.commit()
        cap = load(db, s)
        db.info['worker_lease'] = {'job_id': 'forged', 'lease_token': 'forged'}
        monkeypatch.setattr(transport, '_secret', lambda *_: pytest.fail('unfenced input reached key read'))
        monkeypatch.setattr(transport, '_exchange', lambda *_args, **_kwargs: pytest.fail('unfenced input reached HTTP'))
        with pytest.raises(HTTPException):
            transport.call_device_tool(db, s.p, cap, inputs, key, invocation=call,
                before_authorize=lambda: pytest.fail('unfenced callback invoked'),
                on_send=lambda: pytest.fail('unfenced send marked'))


def test_real_job_and_child_looking_row_with_empty_callback_cannot_replace_parent_authority(managed, monkeypatch):
    s = managed
    with Session(engine(), expire_on_commit=False) as setup:
        call, inputs, key = invocation(setup, s)
        job = m.Job(workspace_id=s.workspace_id, kind='v269-boundary-negative',
            idempotency_key='transport-negative:' + key, data={'actor_id': s.actor_id})
        setup.add(job); setup.commit(); identity, call_id = job.id, call.id
    claimed = worker.claim(identity, workspace_id=s.workspace_id)
    assert claimed is not None
    with Session(engine(), expire_on_commit=False) as db:
        leases.attach(db, *claimed); db.info['worker_actor'] = s.p
        call = db.get(m.Invocation, call_id)
        approval = {field: 'synthetic-unproven-parent' for field in parent.CHILD_KEYS}
        approval.update(call.data['approval'])
        call.status = 'running'; call.data = {**call.data, 'job_id': claimed[0],
            'lease_token': claimed[1], 'approval': approval}
        db.commit()
        cap = load(db, s)
        monkeypatch.setattr(transport, '_secret', lambda *_: pytest.fail('missing real parent reached key read'))
        monkeypatch.setattr(transport, '_exchange', lambda *_args, **_kwargs: pytest.fail('missing real parent reached HTTP'))
        with pytest.raises(HTTPException):
            transport.call_device_tool(db, s.p, cap, inputs, key, invocation=call,
                before_authorize=lambda: None, on_send=lambda: pytest.fail('missing real parent marked sent'))
