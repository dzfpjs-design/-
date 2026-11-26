"""Real authenticated HTTP + disposable PostgreSQL, synthetic documents only."""
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import threading
import time

import uvicorn

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.worker import process_once


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/import_local_business_candidates.py'
spec = importlib.util.spec_from_file_location('local_candidate_import_pg', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

_ports = [int(value) for value in os.environ.get('GA_IMPORT_TEST_HTTP_PORTS', '').split(',') if value]


@contextmanager
def local_server():
    listener = socket.socket()
    listener.bind(('127.0.0.1', _ports.pop(0) if _ports else 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    assert port not in (8766, 55486)
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    try:
        assert server.started
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
        listener.close()


def test_real_http_login_material_preview_atomic_save_and_restart(client, tmp_path):
    text = '合成资料第一段：定期检查对象。\n合成资料第二段：保存检查记录。\n'
    uploaded = client.post('/api/setup/materials', files={'file': ('synthetic.txt', text.encode())})
    assert uploaded.status_code == 201
    assert process_once(uploaded.json()['job_id'])
    row = client.get('/api/setup/materials/' + uploaded.json()['id']).json()
    assert row['status'] == 'parsed'
    before = client.get('/api/setup/draft').json()
    me = client.get('/api/me').json()
    creds = {'cookie': settings().session_cookie_name + '=' + client.cookies.get(settings().session_cookie_name),
             'csrf_token': client.headers['X-CSRF-Token'], 'workspace_id': me['workspace_id'], 'user_id': me['user_id']}
    candidate = {'schema': 'setup-business-candidates-v1', 'origin': 'local_summary',
        'material': {'id': None, 'version': None, 'hash': hashlib.sha256(text.encode()).hexdigest(),
                     'engine': row['engine'], 'status': 'needs_binding'},
        'candidates': [{'key': 'synthetic_check', 'name': '合成检查', 'trigger': '按计划', 'output': '检查记录',
            'inference_reason': '根据合成资料整理的建议',
            'sources': [{'source_text': '合成资料第一段：定期检查对象。', 'quote': '定期检查对象'}],
            'steps': [{'key': 'observe', 'name': '收集信息', 'purpose': '了解对象', 'agent': '多模感知智能体',
                'role': '', 'output': '观测记录', 'condition': '', 'next_key': 'end', 'else_key': 'end',
                'inference_reason': '建议步骤', 'sources': []}]}]}
    with local_server() as port:
        api = module.LocalAPI(port, creds)
        assert '等待确认' in module.import_candidates(api, creds, candidate)
        saved = module._require_ok(api, 'GET', '/api/setup/draft')
        assert saved['revision'] == before['revision'] + 1
        assert saved['configuration']['businessCandidates']['candidates'] == candidate['candidates']
        assert saved['configuration']['businesses'] == [] and saved['configuration']['flows'] == {}
        assert module._require_ok(api, 'GET', '/api/setup/materials/' + row['id'])['version'] == row['version']
    engine().dispose()
    with local_server() as port:
        restored = module._require_ok(module.LocalAPI(port, creds), 'GET', '/api/setup/draft')
        assert restored['revision'] == saved['revision']
        assert restored['configuration']['businessCandidates'] == saved['configuration']['businessCandidates']
