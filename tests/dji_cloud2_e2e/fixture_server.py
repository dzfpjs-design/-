"""Disposable TLS service for the SDK sandbox test; never use real credentials."""
import base64
import hashlib
import http.server
import json
import os
import ssl
import threading
import time

SECRET = 'synthetic-e2e-token'
ROLE = os.environ.get('FIXTURE_ROLE', '')
MODE = os.environ.get('FIXTURE_MODE', '')
TARGET = os.environ.get('FIXTURE_TARGET', '')


def record(kind, authorized):
    # The token and raw headers must never be written to Docker logs.
    print(json.dumps({'role': ROLE, 'kind': kind, 'synthetic_auth_seen': bool(authorized)}), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        ws = self.headers.get('Upgrade', '').lower() == 'websocket'
        token = self.headers.get('X-Workspace-Token') == SECRET
        proto = self.headers.get('Sec-WebSocket-Protocol', '')
        encoded = base64.urlsafe_b64encode(('X-Workspace-Token=' + SECRET).encode()).rstrip(b'=').decode()
        record('websocket' if ws else 'http', token or encoded in proto)
        if ROLE == 'target':
            self.send_response(204); self.send_header('Content-Length', '0'); self.end_headers(); return
        if not (encoded in proto if ws else token):
            record('unauthorized', False)
            self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
        if MODE == 'delayed' and not ws:
            time.sleep(5)
        if MODE == 'redirect':
            self.send_response(302); self.send_header('Location', TARGET)
            self.send_header('Content-Length', '0'); self.end_headers(); return
        if ws:
            key = self.headers.get('Sec-WebSocket-Key', '')
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101); self.send_header('Upgrade', 'websocket'); self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept); self.send_header('Sec-WebSocket-Protocol', 'v1.notify')
            self.end_headers(); self.close_connection = True; return
        body = b'{"code":0,"data":{"pagination":{"page_num":1,"page_size":1000,"total":0},"list":[]}}'
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    def log_message(self, *_args):
        pass


context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/opt/fixture/cert.pem', '/opt/fixture/key.pem')
server = http.server.ThreadingHTTPServer(('0.0.0.0', 18443), Handler)
server.socket = context.wrap_socket(server.socket, server_side=True)
record('ready', False)
server.serve_forever()
