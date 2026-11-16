"""ASGI-level regression: multipart must be bounded even before login."""
import asyncio
import tempfile

import pytest
import starlette.formparsers as formparsers
from generic_agents.api import app
from generic_agents.dji_wayline_upload_limit import DjiWaylineUploadLimit
import generic_agents.dji_wayline_upload_limit as transport


@pytest.mark.parametrize('length', [None, b'0'])
def test_unauthenticated_stream_is_bounded_and_spool_closed(monkeypatch, length):
    monkeypatch.setattr(transport, 'MAX_ARCHIVE_BYTES', 128)
    monkeypatch.setattr(transport, 'MULTIPART_OVERHEAD_BYTES', 300)
    spools = []
    original = tempfile.SpooledTemporaryFile

    def track(*args, **kwargs):
        obj = original(*args, **kwargs)
        spools.append(obj)
        return obj

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', track)
    prefix = (b'--BOUNDARY\r\nContent-Disposition: form-data; name="file"; '
              b'filename="a.kmz"\r\nContent-Type: application/octet-stream\r\n\r\n')
    chunks = [prefix + b'x' * 32, b'y' * 500]
    headers = [(b'content-type', b'multipart/form-data; boundary=BOUNDARY')]
    if length is not None:
        headers.append((b'content-length', length))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'POST', 'scheme': 'http', 'path': '/api/dji/waylines',
             'raw_path': b'/api/dji/waylines', 'query_string': b'', 'root_path': '',
             'headers': headers, 'server': ('test', 80), 'client': ('test', 1234)}
    messages = []
    calls = []
    async def receive():
        i = len(calls)
        calls.append(i)
        return {'type': 'http.request', 'body': chunks[i], 'more_body': i == 0}
    async def send(message):
        messages.append(message)
    asyncio.run(app(scope, receive, send))
    assert next(m['status'] for m in messages if m['type'] == 'http.response.start') == 413, messages
    assert len(calls) == 2
    assert spools and all(f.closed for f in spools)


def test_oversized_declared_length_rejected_without_reading(monkeypatch):
    monkeypatch.setattr(transport, 'MAX_ARCHIVE_BYTES', 128)
    monkeypatch.setattr(transport, 'MULTIPART_OVERHEAD_BYTES', 300)
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/dji/waylines',
             'headers': [(b'content-length', b'429')]}
    messages = []
    async def receive():
        raise AssertionError('body must not be read')
    async def send(message):
        messages.append(message)
    asyncio.run(DjiWaylineUploadLimit(app)(scope, receive, send))
    assert messages[0]['status'] == 413


def test_real_http_client_rejects_anonymous_forged_length_before_spooling(monkeypatch):
    from fastapi.testclient import TestClient
    spools = []
    original = tempfile.SpooledTemporaryFile
    def track(*args, **kwargs):
        obj = original(*args, **kwargs)
        spools.append(obj)
        return obj
    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', track)
    with TestClient(app) as client:
        response = client.post('/api/dji/waylines', headers={'content-length': '0'},
            data={'request_id': '00000000-0000-4000-8000-000000000001'},
            files={'file': ('a.kmz', b'x' * (22 * 1024 * 1024), 'application/octet-stream')})
    assert response.status_code == 413
    assert all(obj.closed for obj in spools)
