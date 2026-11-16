"""Loopback TCP acceptance plus pin/SNI seams; never public network or DB.

Root runs these. The server exists only for the test and the existing exact
development URL gate is configured explicitly, never disabled globally.
"""
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS

import httpcore
import pytest

from generic_agents import business_data_reader as reader
from generic_agents import gateway


@pytest.fixture
def endpoint(monkeypatch):
    @contextmanager
    def start(body=b'{"id":0}', *, status=200, content_type='application/json',
              extra=None, declared=True):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append({'method': self.command, 'path': self.path, 'headers': dict(self.headers)})
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                if declared: self.send_header('Content-Length', str(len(body)))
                for name, value in (extra or {}).items(): self.send_header(name, value)
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port) + '/dataset'
        monkeypatch.setattr(gateway, 'settings', lambda: NS(development_connector_urls=url, allowed_connector_hosts=''))
        try: yield url, seen
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
    return start


def test_real_tcp_get_preserves_original_geojson_and_only_safe_metadata(endpoint):
    raw = b'{"type":"FeatureCollection","features":[]}'
    checks, responses = [], []
    with endpoint(raw, content_type='application/geo+json; charset=utf-8',
            extra={'ETag': '"revision-a"', 'Last-Modified': 'Sun, 25 Oct 2026 00:00:00 GMT',
                'Set-Cookie': 'private-response-cookie', 'Authorization': 'private-header'}) as (url, seen):
        result = reader.fetch_json({'endpoint': url}, before_request=lambda: checks.append('authority'),
            on_response=lambda content, metadata: responses.append((content, metadata)))
    assert len(checks) == 2  # Before DNS and again immediately before actual dial.
    assert len(seen) == 1 and seen[0]['method'] == 'GET' and seen[0]['path'] == '/dataset'
    assert seen[0]['headers']['Accept-Encoding'] == 'identity'
    assert result.content == raw and result.media_type == 'application/geo+json'
    assert result.headers == {'status_code': 200, 'content_type': 'application/geo+json',
        'etag': '"revision-a"', 'last-modified': 'Sun, 25 Oct 2026 00:00:00 GMT'}
    assert responses == [(raw, result.headers)] and result.received_at.endswith('+00:00')


@pytest.mark.parametrize('fault', ['redirect', 'encoding', 'declared_oversize', 'stream_oversize', 'partial', 'type'])
def test_real_tcp_never_follows_redirect_expands_encoding_or_saves_partial_body(endpoint, monkeypatch, fault):
    options = {}; body = b'{"id":0}'; responses = []
    if fault == 'redirect': options = {'status': 302, 'extra': {'Location': 'http://127.0.0.1/private'}}
    if fault == 'encoding': options = {'extra': {'Content-Encoding': 'gzip'}}
    if fault in {'declared_oversize', 'stream_oversize'}:
        monkeypatch.setattr(reader, 'MAX_HTTP_BYTES', 4)
        options['declared'] = fault != 'stream_oversize'
    if fault == 'partial': options['status'] = 206
    if fault == 'type': options['content_type'] = 'text/html'
    with endpoint(body, **options) as (url, seen):
        with pytest.raises(reader.ReaderError):
            reader.fetch_json({'endpoint': url}, on_response=lambda *_args: responses.append(True))
        assert len(seen) == 1
    assert responses == []


def test_real_tcp_revocation_after_dns_prevents_request_and_does_not_capture(endpoint):
    checks = []
    def authorize():
        checks.append(True)
        if len(checks) == 2: raise ValueError('not permitted')
    with endpoint() as (url, seen):
        with pytest.raises(reader.ReaderError): reader.fetch_json({'endpoint': url}, before_request=authorize)
        assert seen == []


@pytest.mark.parametrize('url', ['https://127.0.0.1/private', 'http://127.0.0.1:9/not-allowlisted',
    'https://metadata.invalid/secret?token=do-not-echo', 'https://user:password@public.example/data'])
def test_ssrf_and_credential_urls_do_not_open_socket(monkeypatch, url):
    monkeypatch.setattr(gateway, 'settings', lambda: NS(development_connector_urls='',
        allowed_connector_hosts='127.0.0.1,metadata.invalid,public.example'))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_args, **_kwargs:
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))])
    monkeypatch.setattr(reader._PinnedBackend, 'connect_tcp', lambda *_args, **_kwargs: pytest.fail('SSRF dial'))
    with pytest.raises(reader.ReaderError) as caught: reader.fetch_json({'endpoint': url})
    assert 'password' not in str(caught.value) and 'do-not-echo' not in str(caught.value)


def test_actual_backend_dials_only_pinned_numeric_sockaddr_without_dns(monkeypatch):
    events = []
    class Socket:
        def settimeout(self, value): assert 0 < value <= 5
        def setsockopt(self, *_args): pass
        def connect(self, sockaddr): events.append(sockaddr)
        def getpeername(self): return ('8.8.8.8', 443)
        def close(self): pass
    monkeypatch.setattr(socket, 'socket', lambda *_args: Socket())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_args, **_kwargs: pytest.fail('second DNS at dial'))
    backend = reader._PinnedBackend('public.example', 443,
        [(socket.AF_INET, ('8.8.8.8', 443))], time.monotonic() + 30)
    stream = backend.connect_tcp('public.example', 443, timeout=5)
    assert events == [('8.8.8.8', 443)] and stream.host == 'public.example'
    with pytest.raises(httpcore.ConnectError): backend.connect_tcp('other.example', 443)


def test_changed_second_dns_to_private_is_rejected_before_any_dial(monkeypatch):
    calls = []
    def dns(*_args, **_kwargs):
        calls.append(True)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
            ('8.8.8.8' if len(calls) == 1 else '127.0.0.1', 443))]
    monkeypatch.setattr(gateway, 'settings', lambda: NS(development_connector_urls='', allowed_connector_hosts='public.example'))
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(reader._PinnedBackend, 'connect_tcp', lambda *_args, **_kwargs: pytest.fail('rebound dial'))
    with pytest.raises(reader.ReaderError): reader.fetch_json({'endpoint': 'https://public.example/dataset'})
    assert len(calls) == 2


def test_tls_preserves_hostname_and_refuses_unverified_context():
    calls = []
    class Socket:
        def settimeout(self, value): assert value > 0
        def close(self): calls.append('closed')
    class Context:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(server_hostname); return sock
    stream = reader._PinnedStream(Socket(), 'public.example', time.monotonic() + 30)
    assert stream.start_tls(Context(), server_hostname='public.example').host == 'public.example'
    assert calls == ['public.example']
    with pytest.raises(httpcore.ConnectError): stream.start_tls(Context(), server_hostname='8.8.8.8')
    assert calls[-1] == 'closed'


def test_expired_total_deadline_cannot_start_dns(monkeypatch):
    monkeypatch.setattr(reader, 'HTTP_TIMEOUT_SECONDS', -1)
    monkeypatch.setattr(reader, '_resolve', lambda *_args: pytest.fail('expired DNS'))
    with pytest.raises(reader.ReaderError): reader.fetch_json({'endpoint': 'https://public.example/dataset'})
