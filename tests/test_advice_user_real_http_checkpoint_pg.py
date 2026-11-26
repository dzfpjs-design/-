"""Isolated real HTTP-to-worker flow over a frozen synthetic checkpoint.

A loopback HTTP stub substitutes for the model; no external service or real
workspace is used. Never patch source authorization, clocks, or evidence.
"""
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from contextlib import contextmanager
import socket
import subprocess
import sys
from threading import Thread
import time
import httpx
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as old
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.goal_evaluation_provider_v288 import KEY, binding
from generic_agents.models import Audit, AuthSession, Capability, CloudCall, Task, User
from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_user_dispatch_api_candidate_v2 as dispatch_api
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet
from generic_agents.security import Principal
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.test_authored_service_workflow_pg_v270 import publish
from generic_agents.api import app


_mark_at = time.monotonic()


def mark(stage):
    """Record timing only; never record source text, identifiers, or request data."""
    global _mark_at
    now=time.monotonic()
    path=os.environ.get('GA_TEST_TIMING_LOG')
    if path:
        with open(path,'a',encoding='utf-8') as out:
            out.write(json.dumps({'stage':stage,'seconds_since_last':round(now-_mark_at,2)})+'\n')
        os.chmod(path,0o600)
    _mark_at=now


@contextmanager
def live_http():
    """Serve this isolated app over an actual loopback TCP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', int(os.environ['GA_TEST_SYNTHETIC_HTTP_PORT']))); sock.listen(64)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=180, trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True; thread.join(timeout=10); sock.close()
        assert not thread.is_alive()


def run_worker_process():
    """One independently started worker process; no production credentials."""
    cfg = settings()
    allowed = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'PYTHONPATH',
        'GA_TEST_DATABASE_URL', 'GA_TEST_PG_DATA_DIR', 'GA_TEST_PG_SYSTEM_IDENTIFIER', 'GA_TEST_SYNTHETIC_PROVIDER_PORT')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        'GA_DATABASE_URL': str(cfg.database_url), 'GA_REDIS_URL': cfg.redis_url,
        'GA_STORAGE_DIR': str(cfg.storage_dir), 'GA_COOKIE_SECURE': 'false',
        'GA_CLOUD_ALLOWED_PROVIDERS': '', 'DEEPSEEK_API_KEY': '', 'DASHSCOPE_API_KEY': '',
        'GA_V2_ADVICE_SETUP_TEST_ENABLED': 'true', 'GA_V2_USER_SEND_TEST_ENABLED': 'true',
        'GA_V2_USER_SEND_WORKER_ENABLED': 'true', 'GA_V2_TERMINAL_HISTORY_TEST_ENABLED': 'true',
        'GA_V2_RESPONSE_SPOOL_ENABLED': 'true', 'GA_V2_RESPONSE_SPOOL_ROOT': str(cfg.v2_response_spool_root),
        'GA_DEVICE_GATEWAY_KEY_FILE': str(cfg.device_gateway_key_file),
        'GA_DEVICE_GATEWAY_ALLOWED_ORIGINS': cfg.device_gateway_allowed_origins,
    })
    # The child must never reuse a parent SQLAlchemy connection after startup.
    engine().dispose()
    return subprocess.run([sys.executable, '-m', 'generic_agents.same_goal_advice_user_send_worker_candidate_v2', '--once'],
        env=env, capture_output=True, text=True, timeout=240)


@pytest.mark.skipif(not all(os.environ.get(key) for key in ('GA_SYNTHETIC_FRESH_CHECKPOINT','GA_TEST_SYNTHETIC_PROVIDER_PORT','GA_TEST_SYNTHETIC_HTTP_PORT')), reason='isolated checkpoint and loopback services required')
def test_real_http_to_worker_and_recovered_result(monkeypatch, spool_root):
    mark('begin')
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
    monkeypatch.setattr(settings(), 'v2_user_send_test_enabled', True)
    monkeypatch.setattr(settings(), 'v2_user_send_worker_enabled', True)
    app.include_router(dispatch_api.router, prefix='/api/workspace')
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    from generic_agents.same_goal_advice_intent_index_migration_candidate_v2 import rehearse
    assert rehearse()['status'] in {'installed', 'already_installed'}
    # Terminal-aware reads fail closed unless the isolated schema exists.
    with Session(engine()) as db:
        claims.install(db); storage.install(db); terminal.install(db); db.commit()
    monkeypatch.setattr(settings(), 'v2_terminal_history_test_enabled', True)
    mark('database_ready')
    login = json.loads((settings().storage_dir / '__synthetic_checkpoint_login__.json').read_text())
    with live_http() as client:
        auth = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert auth.status_code == 200
        mark('login')
        client.headers.update({'X-CSRF-Token': auth.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
        with Session(engine()) as db:
            user = db.scalar(select(User).where(User.username == login['username']))
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
                .order_by(AuthSession.created_at.desc()))
            p = Principal(user.id, login['workspace_id'], 'admin', session.id)
            task = db.get(Task, task_id); head = task.data[KEY]
            caps = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
                Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
            origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
            assert len(origins) == 1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', o) for o in origins)
            monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', next(iter(origins)))
            source = db.scalar(select(RuntimeTaskDocumentSourceSet).where(
                RuntimeTaskDocumentSourceSet.task_id == task_id, RuntimeTaskDocumentSourceSet.workspace_id == p.workspace_id))
            assert source is not None
            selected = binding(db, p, task)
            node = next(n for n in selected['binding']['graph']['nodes'] if n['id'] == 'reasoning')
            capability = db.get(Capability, node['execution']['capability_id'])
            manifest = contracts.build_manifest(capability.data, old._pin(capability))
            goal_hash, head_hash = task.data['goal_hash'], head['head_hash']
            source_ref = {'kind': 'approved_source_set', 'source_set_ref': {
                'source_set_id': source.id, 'source_set_hash': source.source_set_hash}}
            clock = db.scalar(select(func.clock_timestamp()))
        assert publish(client, manifest)['status'] == 'published'
        mark('capability_ready')
        base = f'/api/workspace/tasks/{task_id}/business-advice-v2'
        monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', False)
        assert client.get(base + '/availability').json() == {'enabled':False,'has_unfinished':False}
        monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
        context_response = client.get(base + '/context')
        assert context_response.status_code == 200, context_response.status_code
        assert context_response.json()['sources'][0]['selection'] == source_ref
        assert context_response.json()['can_start'] is False
        mark('context_ready')
        policy_body = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': head_hash, 'limits': {'max_tokens': 512,
                'max_request_bytes': 50000, 'max_evidence_items': 2},
            'expires_at': (clock + timedelta(hours=2)).isoformat(), 'reason': '合成资料授权测试'}
        csrf = client.headers.pop('X-CSRF-Token')
        assert client.post(base + '/policy', json=policy_body).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        issued = client.post(base + '/policy', json=policy_body)
        assert issued.status_code == 201, issued.status_code
        request = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
            'expected_mature_head_hash': head_hash, 'document_source': source_ref, 'reason': '合成准备'}
        prepared = client.post(base + '/preparations', json={'request': request, 'policy_ref': issued.json()['policy_ref']})
        assert prepared.status_code == 201, prepared.status_code
        mark('application_prepared')
        plan = prepared.json()['plan_id']; url = base + '/preparations/' + plan
        monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', False)
        assert client.get(base + '/availability').json() == {'enabled':False,'has_unfinished':True}
        monkeypatch.setattr(settings(), 'v2_advice_setup_test_enabled', True)
        assert client.get(url).json()['phase'] == 'waiting'
        processed = client.post(url + '/documents')
        assert processed.status_code == 200, processed.status_code
        assert client.post(url + '/documents').json()['replayed'] is True
        mark('documents_prepared')
        state = client.get(url).json()
        assert state['phase'] == 'review' and state['can_start'] is False
        docs = client.get(url + '/documents')
        assert docs.status_code == 200 and len(docs.json()['documents']) == 2
        mark('documents_loaded')
        for item in docs.json()['documents']:
            assert item['text']
            unauthorized = client.post(url + '/consent', json={'evidence_id': item['id'],
                'expected_version': item['version'], 'reason': '合成核对', 'confirmed': False})
            assert unauthorized.status_code == 422
            authorized = client.post(url + '/consent', json={'evidence_id': item['id'],
                'expected_version': item['version'], 'reason': '合成核对', 'confirmed': True})
            assert authorized.status_code == 200, authorized.status_code
        mark('documents_authorized')
        request_preview = {'request_id': str(uuid4()), 'expected_plan_version': state['version'],
            'expected_goal_hash': goal_hash, 'expected_mature_head_hash': head_hash, 'reason': '合成预览'}
        first = client.post(url + '/preview', json=request_preview)
        assert first.status_code == 201, first.text[:300]
        assert first.json()['can_start'] is False and first.json()['cloud_send_authorized'] is False
        mark('preview_ready')
        with Session(engine()) as db:
            event = db.scalar(select(Audit).where(Audit.action == preview.ACTION, Audit.object_id == plan))
            assert event is not None
            opinion = _opinion(event.data['preview_record']['cloud_request']['decision_input'])
        dispatch = url + '/dispatch'
        start = {'request_id': str(uuid4()), 'confirmed': True, 'start': {
            'request_id': str(uuid4()), 'expected_plan_version': 3,
            'expected_goal_hash': goal_hash, 'expected_mature_head_hash': head_hash,
            'preview_hash': first.json()['preview_hash'], 'cloud_authorized': True,
            'reason': '合成用户确认'}}
        without_confirmation = {**start, 'confirmed': False}
        assert client.post(dispatch + '/start', json=without_confirmation).status_code == 422
        accepted = client.post(dispatch + '/start', json=start)
        assert accepted.status_code == 201, accepted.text[:300]
        assert accepted.json()['state'] == 'waiting'
        mark('confirmed')
        assert client.get(dispatch).json()['state'] == 'waiting'
        port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
        request_stats=os.environ.get('GA_TEST_SYNTHETIC_REQUEST_STATS')
        with provider(port, content=json.dumps(opinion), request_log=Path(request_stats) if request_stats else None,
                usage={'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}) as server:
            run = run_worker_process()
            mark('worker_returned')
            assert run.returncode == 0, run.stderr[-1200:]
            assert len(server.requests) == 1
            engine().dispose()
            ready = client.get(dispatch)
            assert ready.status_code == 200 and ready.json()['state'] == 'ready', ready.text[:500]
            assert ready.json()['usage_tokens'] == 5
            mark('result_ready')
            run_again = run_worker_process()
            assert run_again.returncode == 0, run_again.stderr[-1200:]
            assert len(server.requests) == 1
            mark('repeat_check_complete')
            # Explicit logout/new login and database connection reset prove
            # that the user-visible result is not in process memory.
            assert client.post('/api/logout').status_code == 200
            engine().dispose(); client.cookies.clear()
            login_again = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
            assert login_again.status_code == 200
            client.headers['X-CSRF-Token'] = login_again.json()['csrf_token']
            restored = client.get(dispatch)
            assert restored.status_code == 200 and restored.json() == ready.json(), restored.text[:500]
            assert len(server.requests) == 1
            mark('relogin_recovery_complete')
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 1
