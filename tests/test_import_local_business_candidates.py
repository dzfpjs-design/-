"""Only synthetic inputs and a loopback-only synthetic HTTP service."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/import_local_business_candidates.py'
spec = importlib.util.spec_from_file_location('local_candidate_import', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HASH = 'a' * 64
CREDS = {'cookie': 'session=synthetic-secret', 'csrf_token': 'synthetic-csrf',
         'workspace_id': 'workspace-one', 'user_id': 'user-one'}


def candidate():
    step = {'key': 'observe', 'name': '收集信息', 'purpose': '', 'agent': '业务智能体', 'role': '',
            'output': '结果', 'condition': '', 'next_key': 'end', 'else_key': 'end',
            'inference_reason': '建议', 'sources': []}
    def business(key, name):
        return {'key': key, 'name': name, 'trigger': '触发', 'output': '结果',
                'inference_reason': '建议', 'sources': [], 'steps': [copy.deepcopy(step)]}
    return {'schema': 'setup-business-candidates-v1', 'origin': 'local_summary',
            'material': {'id': None, 'version': None, 'hash': HASH,
                         'engine': 'native-docx-v1', 'status': 'needs_binding'},
            'candidates': [business('one', '合成业务'), business('two', '另一项')]}


def material():
    return {'id': 'material-one', 'hash': HASH, 'version': 3, 'engine': 'native-docx-v1',
            'status': 'needs_input', 'archived': False, 'note': '保留', 'kind': '业务资料'}


class FakeAPI:
    def __init__(self):
        self.materials = [material()]
        self.revision = 7
        self.configuration = {'businesses': ['已有业务'], 'flows': {'已有业务': {'steps': []}},
                              'materials': [{'id': 'unrelated', 'note': '不可覆盖'}]}
        self.requests = []
        self.mode = ''
        self.identity_calls = 0

    def request(self, method, path, body=None):
        self.requests.append((method, path))
        if path == '/api/me':
            self.identity_calls += 1
            return 200, {'workspace_id': 'workspace-one', 'user_id': 'user-one',
                         'role': 'operator' if self.mode != 'viewer' else 'viewer'}
        if path.startswith('/api/setup/materials?'):
            return 200, copy.deepcopy(self.materials)
        if path == '/api/setup/candidates/preview':
            material_value = copy.deepcopy(body['material'])
            if self.mode == 'changed_preview': material_value['version'] += 1
            candidates = copy.deepcopy(body['candidates'])
            for item in candidates:
                item.update(citations=[], unresolved_sources=[])
                for step in item['steps']:step.update(citations=[], unresolved_sources=[])
            if self.mode == 'changed_step':candidates[0]['steps'][0]['purpose'] = '发生改变'
            return 200, {'material': material_value, 'candidates': candidates}
        if path == '/api/setup/draft':
            return 200, {'revision': self.revision, 'configuration': copy.deepcopy(self.configuration)}
        if path == '/api/setup/candidates':
            if self.mode == 'conflict':return 409, None
            if self.mode == 'request_timeout':return 408, None
            assert body['revision'] == self.revision
            if not self.configuration:
                self.configuration = {'version': 1, 'resources': [], 'connections': [], 'materials': [],
                                      'instanceProfile': {}, 'businesses': [], 'flows': {}}
            self.configuration['businessCandidates'] = copy.deepcopy(body['bundle'])
            if self.mode == 'unexpected_change':self.configuration['businesses'] = []
            self.revision += 1
            if self.mode == 'lost_reply':raise ConnectionResetError('private server error')
            return 200, {'revision': self.revision, 'configuration': copy.deepcopy(self.configuration)}
        raise AssertionError('unexpected synthetic request')


def calls(api, target):
    return sum(path == target for _method, path in api.requests)


@pytest.mark.parametrize('origin', [
    'https://localhost:1234', 'http://example.com:1234', 'http://127.0.0.2:1234',
    'http://localhost', 'http://user@localhost:1234', 'http://localhost:1234/path',
    'http://localhost:1234/?redirect=x', 'http://localhost:1234/#x', 'http://[::1]:1234',
    'http://localhost:1234\n',
])
def test_origin_rejects_nonlocal_or_ambiguous_addresses(origin):
    with pytest.raises(module.ImportStopped):module._origin(origin)


def test_origin_accepts_only_explicit_loopback():
    assert module._origin('http://127.0.0.1:1234') == 1234
    assert module._origin('http://localhost:4321/') == 4321


def test_private_candidate_file_safety(tmp_path):
    path = tmp_path / 'candidate.json'
    path.write_text(json.dumps(candidate()), encoding='utf-8')
    path.chmod(0o600)
    assert module._read_private(path) == candidate()
    path.chmod(0o644)
    with pytest.raises(module.ImportStopped):module._read_private(path)
    path.chmod(0o600)
    link = tmp_path / 'link.json';link.symlink_to(path)
    with pytest.raises(module.ImportStopped):module._read_private(link)
    with pytest.raises(module.ImportStopped):module._read_private(Path('relative.json'))


def test_auth_descriptor_and_invalid_headers_do_not_disclose_values(tmp_path):
    path = tmp_path / 'credentials';path.write_text(json.dumps(CREDS));path.chmod(0o600)
    with path.open('rb') as stream:assert module._credentials(stream.fileno()) == CREDS
    path.chmod(0o644)
    with path.open('rb') as stream:
        with pytest.raises(module.ImportStopped) as error:module._credentials(stream.fileno())
    assert CREDS['cookie'] not in str(error.value)
    path.write_text(json.dumps({**CREDS, 'cookie': 'synthetic-secret\r\nHeader: bad'}));path.chmod(0o600)
    with path.open('rb') as stream:
        with pytest.raises(module.ImportStopped):module._credentials(stream.fileno())


def test_success_adds_candidates_only_and_keeps_source_unbound_in_file():
    api = FakeAPI(); original = candidate(); before = copy.deepcopy(api.configuration)
    assert '等待确认' in module.import_candidates(api, CREDS, original)
    assert original['material']['id'] is None
    saved = api.configuration.pop('businessCandidates')
    assert api.configuration == before
    assert saved['material'] == {k: material()[k] for k in ('id', 'version', 'hash', 'engine', 'status')}
    assert len(saved['candidates']) == 2
    assert calls(api, '/api/setup/candidates') == 1
    assert all(method != 'PUT' for method, _path in api.requests)


def test_initializes_a_new_empty_draft_without_misreporting_success():
    api = FakeAPI();api.configuration = {}
    assert '等待确认' in module.import_candidates(api, CREDS, candidate())
    assert api.configuration['businesses'] == []
    assert 'businessCandidates' in api.configuration


@pytest.mark.parametrize('mode', ['viewer', 'changed_preview', 'changed_step'])
def test_bad_identity_or_changed_preview_never_writes(mode):
    api = FakeAPI();api.mode = mode
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 0


def test_missing_or_ambiguous_material_fails_closed():
    api = FakeAPI();api.materials = []
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, candidate())
    api = FakeAPI();api.materials = [material(), {**material(), 'id': 'another'}]
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 0


def test_existing_candidate_and_revision_conflict_never_overwrite():
    api = FakeAPI();api.configuration['businessCandidates'] = {'existing': True}
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 0
    api = FakeAPI();api.mode = 'conflict'
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 1
    assert 'businessCandidates' not in api.configuration


@pytest.mark.parametrize('mode', ['request_timeout', 'unexpected_change'])
def test_uncertain_result_never_claims_success_or_repeats(mode):
    api = FakeAPI();api.mode = mode
    with pytest.raises(module.ImportStopped, match='尚未确认'):
        module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 1
    assert calls(api, '/api/setup/draft') == 2


def test_duplicate_candidate_keys_never_write():
    api = FakeAPI();value = candidate();value['candidates'][1]['key'] = value['candidates'][0]['key']
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, value)
    assert calls(api, '/api/setup/candidates') == 0


def test_candidate_missing_explicit_default_never_writes():
    api = FakeAPI();value = candidate();del value['candidates'][0]['steps'][0]['condition']
    with pytest.raises(module.ImportStopped):module.import_candidates(api, CREDS, value)
    assert calls(api, '/api/setup/candidates') == 0


def test_lost_reply_is_unknown_even_when_readback_matches_and_never_replayed():
    api = FakeAPI();api.mode = 'lost_reply'
    with pytest.raises(module.ImportStopped, match='已显示相同候选，但无法确认'):
        module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 1
    assert calls(api, '/api/setup/draft') == 2
    assert api.configuration['businessCandidates']['material']['id'] == 'material-one'
    with pytest.raises(module.ImportStopped, match='已有业务候选'):
        module.import_candidates(api, CREDS, candidate())
    assert calls(api, '/api/setup/candidates') == 1


def test_real_http_client_uses_loopback_and_does_not_print_secrets(tmp_path):
    state = FakeAPI()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):pass
        def handle_request(self):
            assert self.headers.get('Cookie') == CREDS['cookie']
            assert self.headers.get('X-CSRF-Token') == CREDS['csrf_token']
            assert self.headers.get('X-Workspace-ID') == CREDS['workspace_id']
            size = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(size)) if size else None
            code, value = state.request(self.command, self.path, body)
            payload = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)));self.end_headers();self.wfile.write(payload)
        do_GET = handle_request
        do_POST = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    assert server.server_port not in (8766, 55486)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    path = tmp_path / 'candidate.json';path.write_text(json.dumps(candidate()));path.chmod(0o600)
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), '--origin', f'http://localhost:{server.server_port}',
                                 '--candidate', str(path), '--credentials-fd', '0'],
                                input=json.dumps(CREDS), text=True, capture_output=True, timeout=15,
                                env={**os.environ, 'HTTP_PROXY':'http://untrusted.invalid:1234'})
        assert result.returncode == 0
        assert '等待确认' in result.stdout
        assert CREDS['cookie'] not in result.stdout + result.stderr
        assert CREDS['csrf_token'] not in result.stdout + result.stderr
        assert calls(state, '/api/setup/candidates') == 1
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
