#!/usr/bin/env python3
"""Run one synthetic provider attempt through a private fixed-host tunnel.

The restore guard validates file integrity, not whether a caller-supplied
checkpoint was truly generated from synthetic data. Until that provenance has
an independent trust boundary, live dispatch is disabled. Fake is the default
and only executable mode; historical live evidence remains unchanged.
"""
import argparse
import base64
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import uuid

from scripts.synthetic_once_proxy import tunnel


def call(args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def safe_environment(pg):
    # Do not copy the parent environment: it may contain real credentials or
    # an application PG URL. Keep only runtime basics and force our PATH.
    env = {key:os.environ[key] for key in ('TMPDIR','LANG','LC_ALL') if key in os.environ}
    env['PATH'] = str(pg) + ':/usr/bin:/bin:/usr/sbin:/sbin'
    return env


def metadata(root, value):
    temporary = root / 'status.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', closefd=False) as out:
            json.dump(value, out, sort_keys=True); out.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, root / 'status.json')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True, help='private generated synthetic checkpoint')
    parser.add_argument('--pg-bin', required=True, help='directory with initdb, pg_ctl and psql')
    parser.add_argument('--mode', choices=('fake', 'live'), default='fake')
    parser.add_argument('--credential-fd', type=int, help='read end of a provider credential pipe, live only')
    args = parser.parse_args()
    if args.mode == 'live':
        raise RuntimeError('live mode disabled: synthetic checkpoint provenance is not independently verified')
    if args.credential_fd is not None:
        raise RuntimeError('fake mode accepts no credential')
    pg = Path(args.pg_bin).resolve(strict=True)
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_absolute() or not checkpoint.is_dir() or checkpoint.is_symlink():
        raise RuntimeError('invalid synthetic checkpoint')
    if not Path('/usr/bin/sandbox-exec').is_file():
        raise RuntimeError('this offline harness requires macOS sandbox-exec')
    repo = Path(__file__).resolve().parents[1]
    if os.path.lexists(repo/'.env'):
        raise RuntimeError('run only in a clean isolated worktree without .env')
    base_env = safe_environment(pg)
    root = Path(tempfile.mkdtemp(prefix='advice-race-pg-'))
    evidence = Path(tempfile.mkdtemp(prefix='ga-test-evidence-'))
    spool = Path(tempfile.mkdtemp(prefix='advice-v2-response-spool-'))
    for path in (root, evidence, spool): os.chmod(path, 0o700)
    # Only this high-entropy local credential can consume the one tunnel.
    # It is independent of the provider credential and never written to disk.
    proxy_token = secrets.token_hex(32)
    expected_auth = b'Basic ' + base64.b64encode(('once:'+proxy_token).encode('ascii'))
    provider_listener = None
    fake_ca = None
    if args.mode == 'fake':
        fake_ca, fake_key = root/'fake-ca.pem', root/'fake-tls-key.pem'
        call(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
              '-keyout', fake_key, '-out', fake_ca, '-days', '1', '-subj', '/CN=api.deepseek.com',
              '-addext', 'subjectAltName=DNS:api.deepseek.com'],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, env=base_env)
        os.chmod(fake_key, 0o600); os.chmod(fake_ca, 0o600)
        class Fake(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/chat/completions' or not 0 < length <= 16*1024*1024 or self.headers.get('Authorization') != 'Bearer synthetic-only':
                    self.send_error(400); return
                self.rfile.read(length)
                body=b'{"error":{"message":"synthetic provider failure"},"usage":{"prompt_tokens":3,"completion_tokens":0,"total_tokens":3}}'
                self.send_response(429); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_): pass
        provider_listener = http.server.HTTPServer(('127.0.0.1',0), Fake)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(fake_ca, fake_key)
        provider_listener.socket = tls.wrap_socket(provider_listener.socket, server_side=True)
        threading.Thread(target=provider_listener.handle_request, daemon=True).start()
    listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(8); listener.settimeout(2400)
    proxy_port = listener.getsockname()[1]
    proxy_errors = []
    def serve_tunnel():
        # Authentication failures do not consume the one attempt. A successful
        # fixed CONNECT closes the listener even if the upstream later fails.
        try:
            while True:
                client, _ = listener.accept()
                try:
                    with client:
                        fake_connector = (lambda: socket.create_connection(provider_listener.server_address, timeout=5)) if provider_listener else None
                        tunnel(client, root, connect_provider=fake_connector, expected_auth=expected_auth)
                    break
                except ValueError:
                    continue
        except Exception as exc:
            proxy_errors.append(type(exc).__name__)
        finally:
            listener.close()
    threading.Thread(target=serve_tunnel, daemon=True).start()
    credential_fd, write_fd = os.pipe(); os.write(write_fd, b'synthetic-only'); os.close(write_fd)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    # Neither the generated request ID nor the cluster is reused on reruns.
    run_id = str(uuid.uuid4()); database = 'ga_test_' + uuid.uuid4().hex[:12]
    status = {'schema':'isolated-synthetic-advice-live-v1','state':'initializing','mode':args.mode,
        'run_id':run_id,'database':database,'port':port,'storage':str(evidence),'spool':str(spool)}
    metadata(root, status)
    print('Private synthetic run created; status directory:', root, flush=True)
    data = root / 'data'; running = False
    try:
        call([pg/'initdb', '-D', data, '-U', 'advice_test', '-A', 'trust', '--no-locale', '-E', 'UTF8'],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, env=base_env)
        def start():
            call([pg/'pg_ctl','-D',data,'-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -k {root}','-w','start'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, env=base_env)
        def stop():
            call([pg/'pg_ctl','-D',data,'-m','fast','-w','stop'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, env=base_env)
        start(); running = True
        psql = [pg/'psql','-h','127.0.0.1','-p',str(port),'-U','advice_test','-d','postgres']
        sid = subprocess.check_output([str(a) for a in psql]+['-Atc','select system_identifier from pg_control_system()'], text=True, env=base_env).strip()
        status['system_identifier'] = sid
        metadata(root, status)
        call(psql + ['-v','ON_ERROR_STOP=1','-c',f'CREATE DATABASE "{database}"'], stdout=subprocess.DEVNULL, env=base_env)
        environment = dict(base_env)
        environment.update(GA_TEST_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/postgres',
            GA_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{port}/{database}',
            GA_TEST_PG_DATA_DIR=str(data), GA_TEST_PG_SYSTEM_IDENTIFIER=sid, GA_STORAGE_DIR=str(evidence),
            GA_SYNTHETIC_FRESH_CHECKPOINT=str(checkpoint), GA_SYNTHETIC_RUN_ROOT=str(root),
            GA_SYNTHETIC_RUN_ID=run_id, GA_SYNTHETIC_SPOOL=str(spool), GA_V2_RESPONSE_SPOOL_ROOT=str(spool),
            GA_SYNTHETIC_KEY_FD=str(credential_fd), GA_SYNTHETIC_PROXY_PORT=str(proxy_port),
            GA_SYNTHETIC_PROXY_TOKEN=proxy_token, GA_SYNTHETIC_PROVIDER_MODE=args.mode,
            GA_V2_RESPONSE_SPOOL_ENABLED='true', GA_COOKIE_SECURE='false', GA_CLOUD_ALLOWED_PROVIDERS='',
            GA_REDIS_URL='redis://127.0.0.1:1/0', DEEPSEEK_API_KEY='', DASHSCOPE_API_KEY='',
            PYTHONPATH=str(repo/'src')+':'+str(repo))
        if fake_ca: environment['GA_SYNTHETIC_FAKE_CA'] = str(fake_ca)
        profile = (f'(version 1) (allow default) (deny network*) '
                   f'(allow network-outbound (remote ip "localhost:{port}")) '
                   f'(allow network-outbound (remote ip "localhost:{proxy_port}"))')
        def child(module, logfile, *, needs_credential=False):
            child_environment = dict(environment)
            if not needs_credential: child_environment.pop('GA_SYNTHETIC_KEY_FD', None)
            with (root/logfile).open('w') as log:
                os.chmod(root/logfile, 0o600)
                return subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-m',module],
                    cwd=repo, env=child_environment, pass_fds=(credential_fd,) if needs_credential else (), stdout=log,
                    stderr=subprocess.STDOUT, timeout=2400).returncode
        status['state']='running_synthetic_provider'; metadata(root,status)
        if child('tests.synthetic_advice_live_bootstrap','flow.log', needs_credential=True) != 0:
            status['state']='failed_preserved_no_retry'; metadata(root,status); return 1
        # Stop and restart the dedicated PostgreSQL process, not just a Session.
        stop(); running = False
        start(); running = True
        status['state']='checking_after_database_restart'; metadata(root,status)
        if child('tests.synthetic_advice_live_verify','verify.log') != 0:
            status['state']='recovery_unverified_no_retry'; metadata(root,status); return 1
        status['state']='recovery_passed'; metadata(root,status)
        print('Synthetic run recovery verified; private data retained.', flush=True)
        return 0
    except BaseException:
        status['state']='interrupted_preserved_no_retry'; metadata(root,status)
        raise
    finally:
        try: os.close(credential_fd)
        except OSError: pass
        if running:
            try: stop()
            except Exception: pass


if __name__ == '__main__':
    sys.exit(main())
