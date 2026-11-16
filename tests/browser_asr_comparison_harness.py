"""Explicit browser verification against real API and disposable PostgreSQL."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from sqlalchemy import select
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.models import Base, CloudCall

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/evidence/qwen-asr-quality-v06/browser'
ACCESS = ROOT/'.runtime/m38-asr-comparison-access.json'
CONTROL = ROOT/'.runtime/m38-asr-comparison-control.json'


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/(name+'.json')).write_text(json.dumps(data, indent=2)+'\n')


def durable():
    with engine().connect() as connection:
        return {table.name: {'count': len(rows), 'digest': digest(sorted(rows))}
                for table in sorted(Base.metadata.tables.values(), key=lambda table: table.name)
                for rows in [[digest(json.loads(json.dumps(dict(row._mapping), default=str))) for row in connection.execute(select(table))]]}


def test_browser_harness(client, account):
    if os.getenv('GA_M38_ASR_BROWSER_HARNESS') != '1':
        pytest.skip('Explicit disposable runtime only')
    assert engine().url.database.startswith('ga_test_')
    assert not ACCESS.exists() and not (OUT/'harness-ready.json').exists(), 'Resume live handle, no duplicate harness'
    assert CloudCall.__tablename__ in durable(), 'Snapshot preflight before browser work'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = 'http://localhost:'+str(port)
    env = {**os.environ, 'GA_STORAGE_DIR': str(settings().storage_dir), 'GA_ALLOWED_ORIGINS': url,
           'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': ''}
    process = None
    log = open(ROOT/'.runtime/m38-asr-comparison-api.log', 'a')
    def start():
        child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'generic_agents.api:app', '--host', '127.0.0.1', '--port', str(port)],
                                 cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            for _ in range(80):
                assert child.poll() is None, 'Isolated API exited'
                try:
                    if httpx.get('http://127.0.0.1:'+str(port)+'/health', trust_env=False, timeout=1).is_success:
                        return child
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            raise AssertionError('Isolated API not ready')
        except BaseException:
            if child.poll() is None: child.terminate(); child.wait(timeout=10)
            raise
    try:
        process = start()
        username, password, workspace = account
        with os.fdopen(os.open(ACCESS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump({'username': username, 'password': password, 'workspace_id': workspace, 'url': url}, stream)
        save('harness-ready', {'url': url, 'api_pid': process.pid, 'harness_pid': os.getpid(), 'isolated_database': True})
        print('Isolated ASR comparison API ready; private access file saved', flush=True)
        sequence = 0; before = None; export_before = None; deadline = time.monotonic()+1800
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Original API no longer live'
            control = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            if control.get('sequence', 0) <= sequence:
                time.sleep(.25); continue
            sequence = control['sequence']
            if control['command'] == 'restart':
                before = durable(); save('before-restart', before)
                export_before = client.get('/api/specialists/evaluations/asr/export').content
                process.terminate(); process.wait(timeout=10); process = start()
                save('harness-restarted', {'url': url, 'api_pid': process.pid, 'state': 'restarted'})
            elif control['command'] == 'finish':
                assert before is not None and durable() == before
                assert client.get('/api/specialists/evaluations/asr/export').content == export_before
                assert before[CloudCall.__tablename__]['count'] == 0
                save('after-restart', durable())
                save('verification', {'real_api_restart': True, 'database_all_table_digests_unchanged': True,
                     'export_bytes_unchanged': True, 'cloud_calls': 0, 'new_model_inference': False,
                     'application_database_changed': False, 'isolated_database': True,
                     'candidate_business_registered': False, 'production_verified': False})
                print('Isolated API restart, all table digests and export bytes verified', flush=True)
                break
            else:
                raise AssertionError('Unknown harness command')
        else:
            raise AssertionError('Harness expired without verification')
    except BaseException as error:
        save('interrupted', {'error_type': type(error).__name__, 'message': str(error)[:400], 'verified': False})
        raise
    finally:
        if process and process.poll() is None: process.terminate(); process.wait(timeout=10)
        log.close(); ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
