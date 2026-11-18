"""Root-run only: authenticated catalogue persistence on the exclusive owned PG.

Uses public synthetic documents and the existing model MockTransport. This does
not demonstrate real model quality, provider transmission, or real documents.
"""
import copy
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
import traceback
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents import report_evidence_pipeline as evidence_pipeline
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_enum_postgres_v172 import enum_scene, confirmed_catalogue
from tests import test_report_evidence_enum_postgres_v172 as enum_tests


def test_pending_catalogue_question_and_original_survive_new_http_session(enum_scene, monkeypatch, account):
    client = enum_scene['client']
    # Give an actionable diagnostic for real failed/unknown map stages. The
    # existing helper otherwise only exposes the later reduce-preview 409.
    original_started = enum_tests.started
    failures = []
    original_failure_detail = evidence_pipeline.legacy.local_failure_detail
    def capture_failure(error):
        original_error = error
        chain = []
        while error is not None and len(chain) < 4:
            chain.append({'type':type(error).__name__, 'trace':[
                (Path(frame.filename).name, frame.lineno, frame.name)
                for frame in traceback.extract_tb(error.__traceback__)][-8:]})
            error = error.__cause__
        failures.append(chain)
        return original_failure_detail(original_error)
    monkeypatch.setattr(evidence_pipeline.legacy, 'local_failure_detail', capture_failure)
    def checked_started(scene, provider):
        result = original_started(scene, provider)
        middle = result[3]
        summary = {'status': middle.get('status'), 'error_code': middle.get('error_code'),
            'error': middle.get('error'), 'provider_requests': len(provider.requests), 'exceptions': failures,
            'maps': [{key: stage.get(key) for key in ('status','error_code','error')}
                     for stage in middle.get('maps', [])]}
        if middle.get('status') != 'awaiting_merge':
            raise AssertionError('failure_types_and_stack=' + repr(failures) + '; provider_requests=' + str(len(provider.requests)) + '; status=' + str(middle.get('status')))
        assert all(stage.get('status') == 'succeeded' for stage in middle['maps']), summary
        return result
    monkeypatch.setattr(enum_tests, 'started', checked_started)
    original_post = client.post
    def save_with_synthetic_question(url, **kwargs):
        if url.startswith('/api/setup/report-business-catalogues/v211/drafts/') and url.endswith('/save'):
            kwargs = copy.deepcopy(kwargs)
            doc = kwargs['json']['document']
            doc['questions'].append({'key':'q_pending', 'kind':'missing_info',
                'input_positions':[0,1], 'candidate_business_keys':['b1'],
                'question':'两处合成数据不一致，待核。', 'status':'open', 'resolution':''})
            doc['allocation_groups'][0]['question_keys'].append('q_pending')
        return original_post(url, **kwargs)
    monkeypatch.setattr(client, 'post', save_with_synthetic_question)
    provider, source = confirmed_catalogue(enum_scene, monkeypatch, account)
    before_calls = len(provider.requests)
    base = '/api/setup/report-business-catalogues/v211/plans/' + source['plan_id']
    params = {'scope_id':'b1'}
    first = client.get(base + '/business-flow-questions', params=params)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data['status'] == 'available' and data['total'] == 1
    assert data['catalogue']['version'] == source['draft_version']
    assert data['plan_version'] == source['plan_version'] and data['plan_hash'] == source['plan_hash']
    assert data['questions'][0]['question'] == '两处合成数据不一致，待核。'
    assert data['questions'][0]['status'] == 'open' and data['sent_to_model'] is False
    evidence = client.get(base + '/business-flow-question-evidence', params={**params,'question_key':'q_pending'})
    assert evidence.status_code == 200, evidence.text
    assert [record['input_position'] for record in evidence.json()['records']] == [0,1]
    assert all(record['source_spans'][0]['record']['text'] for record in evidence.json()['records'])
    assert evidence.json()['catalogue'] == data['catalogue']
    assert client.get(base + '/business-flow-questions', params={'scope_id':'other'}).status_code == 404
    with TestClient(app) as cold:
        auth = cold.post('/api/login', json={'username':account[0], 'password':account[1]})
        assert auth.status_code == 200
        cold.headers.update({'X-CSRF-Token':auth.json()['csrf_token'], 'X-Workspace-ID':account[2]})
        assert cold.get(base + '/business-flow-questions', params=params).json() == data
        assert cold.get(base + '/business-flow-question-evidence', params={**params,'question_key':'q_pending'}).json() == evidence.json()
    # Confirmed versions cannot be changed in place; a stale edit must not
    # silently alter the context already tied to the confirmed plan.
    draft_path = '/api/setup/report-business-catalogues/v211/drafts/' + source['draft_id']
    saved = client.get(draft_path).json()
    altered = copy.deepcopy(saved['document'])
    altered['questions'][0]['question'] = '不应覆盖已确认目录'
    rejected = original_post(draft_path + '/save', json={'request_id':str(uuid4()),
        'expected_version':saved['version'], 'expected_record_hash':saved['record_hash'],
        'document':altered, 'actions':[]})
    assert rejected.status_code == 409
    assert client.get(base + '/business-flow-questions', params=params).json() == data
    assert len(provider.requests) == before_calls

    # A genuinely fresh backend process reads the same isolated database and
    # private evidence directory. The root-owned runner installs sitecustomize
    # to deny all non-loopback sockets in both this process and its children.
    assert os.environ.get('GA_F11_DUAL_TESTS_OWNED') == '63131'
    assert os.environ.get('GA_OWNED_SYSTEM_ID', '').isdigit()
    plugin_path = Path(os.environ['GA_OWNED_EXPECTED_DIR']).parent
    assert (plugin_path / 'sitecustomize.py').is_file()
    assert str(plugin_path) in os.environ.get('PYTHONPATH', '').split(os.pathsep)
    evidence_dir = Path(settings().storage_dir)
    assert evidence_dir.is_absolute() and evidence_dir == evidence_dir.resolve()
    env = os.environ.copy()
    env.update(GA_STORAGE_DIR=str(evidence_dir), GA_COOKIE_SECURE='false',
        DASHSCOPE_API_KEY='', DEEPSEEK_API_KEY='', QWEN_TOKENPLAN_API_KEY='')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',0)); listener.listen(16)
        port = listener.getsockname()[1]
        server = subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app',
            '--fd',str(listener.fileno()),'--log-level','error'], env=env,
            pass_fds=(listener.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            origin = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 25
            while True:
                if server.poll() is not None:
                    raise AssertionError('isolated fresh backend exited before health check')
                try:
                    with opener.open(origin+'/health',timeout=1) as response:
                        assert json.load(response)['status'] == 'ok'
                    break
                except (OSError,TimeoutError):
                    if time.monotonic() >= deadline:
                        raise AssertionError('isolated fresh backend did not become ready')
                    time.sleep(0.1)
            login_body = json.dumps({'username':account[0],'password':account[1]}).encode()
            request = urllib.request.Request(origin+'/api/login', data=login_body,
                headers={'Content-Type':'application/json'}, method='POST')
            with opener.open(request,timeout=10) as response:
                csrf = json.load(response)['csrf_token']
                cookies = SimpleCookie(); cookies.load(response.headers['Set-Cookie'])
                cookie = cookies[settings().session_cookie_name].value
            headers = {'Cookie':settings().session_cookie_name+'='+cookie,
                'X-CSRF-Token':csrf,'X-Workspace-ID':account[2]}
            def fresh_get(path, query):
                request = urllib.request.Request(origin+path+'?'+urllib.parse.urlencode(query),headers=headers)
                with opener.open(request,timeout=15) as response:
                    return json.load(response)
            assert fresh_get(base+'/business-flow-questions',params) == data
            assert fresh_get(base+'/business-flow-question-evidence',
                {**params,'question_key':'q_pending'}) == evidence.json()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=10)
    assert len(provider.requests) == before_calls
