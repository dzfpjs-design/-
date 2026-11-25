"""Explicit loopback-only synthetic HTTP provider for disposable tests.

It is not a model, credential, application endpoint or production adapter.
Only SHA-256 of incoming bytes and the count are kept, never request text.
"""
import hashlib
import gzip
import json
import socket
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@contextmanager
def provider(port, *, mode='reply', content='{}', usage=None, on_received=None):
    if type(port) is not int or not 1 <= port <= 65535:raise ValueError('explicit test port required')
    if mode not in {'reply','drop','http_error','gzip','slow','slow_first_byte'}:raise ValueError('unsupported synthetic mode')
    if type(content) is not str:raise ValueError('synthetic content must be text')
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        allow_reuse_address=True
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            if self.path!='/chat/completions':self.send_error(404);return
            try:
                size=int(self.headers.get('Content-Length',''))
                if not 0 <= size <= 100000:raise ValueError()
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError()
            except (ValueError,TypeError):self.send_error(400);return
            self.server.requests.append(hashlib.sha256(raw).hexdigest())
            if on_received is not None:on_received()
            if mode=='drop':
                self.close_connection=True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close();return
            payload={'id':'synthetic-loopback-only','choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':content}}]}
            if usage is not None:payload['usage']=usage
            body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            if mode=='gzip':body=gzip.compress(body)
            if mode=='slow_first_byte':time.sleep(3.3)
            self.send_response(429 if mode=='http_error' else 200)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.send_header('Set-Cookie','synthetic-cookie=must-not-be-recorded')
            if mode=='gzip':self.send_header('Content-Encoding','gzip')
            self.end_headers()
            try:
                if mode=='slow':
                    for byte in body:
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.05)
                else:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=Server(('127.0.0.1',port),Handler);server.requests=[]
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
