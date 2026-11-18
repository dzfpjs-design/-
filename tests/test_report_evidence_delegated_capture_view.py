"""Private capture inspection in an owned PG with synthetic provider bytes."""
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Job, Membership, SetupReportDraft, User, Workspace
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from generic_agents import gateway
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import owned_disposable_database
from tests.test_report_evidence_delegated_wire_postgres import owned_capture_roots, start_delegated, install_http
from tests.test_vertical_slice import publish


def _start(scene, monkeypatch, mode):
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    _, _, created = start_delegated(scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests, mode)
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        stage = row.data['maps'][0]
        return created['id'], row.version, stage['chunk_id'], requests


def _url(draft_id, stage):
    return f'/api/setup/report-evidence-readings/{draft_id}/delegated-captures/{stage}'


@pytest.mark.parametrize('mode,outcome,complete', [
    ('stop', 'normal_end_unverified', True), ('length', 'length', True),
    ('multiple', 'multiple_choices', True), ('inner_invalid', 'invalid_content_json', True),
    ('outer_invalid', 'invalid_envelope', True), ('disconnect', 'transport_interrupted', False),
])
def test_real_worker_capture_inspection_is_readonly_and_never_retries(reading_scene, monkeypatch, mode, outcome, complete):
    scene = reading_scene
    draft_id, revision, stage, requests = _start(scene, monkeypatch, mode)
    before_requests = len(requests)
    client = scene['client']
    with Session(engine()) as db:
        before_calls = db.scalar(select(func.count()).select_from(CloudCall))
        before_jobs = db.scalar(select(func.count()).select_from(Job))
        before_row = db.get(SetupReportDraft, draft_id)
        before = (before_row.version, digest(before_row.data))
    response = client.get(_url(draft_id, stage), params={'revision': revision})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    view = response.json()
    assert view['schema'] == 'report-delegated-http-capture-view-v1'
    assert view['view_hash'] == digest({k: v for k, v in view.items() if k != 'view_hash'})
    assert view['outcome'] == outcome and view['capture']['response_complete'] is complete
    assert view['scope']['revision'] == revision and view['scope']['stage'] == stage
    assert view['can_apply'] is view['can_publish'] is view['can_resend'] is False
    assert view['recovery']['status'] != 'eligible'
    assert '合成声明' not in response.text and 'synthetic-test-only' not in response.text
    assert view['can_view_body'] is False and view['body_access'] == 'download_disabled'
    body = client.get(_url(draft_id, stage) + '/body', params={
        'revision': revision, 'capture_hash': view['capture']['capture_hash']})
    assert body.status_code == 404
    assert client.get(_url(draft_id, stage), params={'revision': revision + 1}).status_code == 409
    assert client.get(_url(draft_id, stage) + '/body', params={
        'revision': revision, 'capture_hash': '0' * 64}).status_code == 404
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft_id)
        assert (row.version, digest(row.data)) == before
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before_calls
        assert db.scalar(select(func.count()).select_from(Job)) == before_jobs
    assert before_requests > 0 and len(requests) == before_requests


def test_pending_stage_is_not_evidence_of_never_sent(reading_scene, monkeypatch):
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    _, _, created = start_delegated(scene, monkeypatch)
    stage = created['maps'][0]['chunk_id']
    response = scene['client'].get(_url(created['id'], stage), params={'revision': created['revision']})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view['outcome'] == 'not_recorded' and view['capture'] is None and view['ledger'] is None
    assert view['scope']['call_id'] is None and view['body_access'] == 'not_recorded'
    assert not view['can_view_body'] and not view['can_resend']


def test_cross_account_cross_workspace_and_revoked_session_cannot_read(reading_scene, monkeypatch):
    scene = reading_scene
    draft_id, revision, stage, _ = _start(scene, monkeypatch, 'length')
    from generic_agents.api import app
    password = uuid4().hex
    with Session(engine()) as db:
        same = User(username='same_' + uuid4().hex, password_hash=password_hash(password))
        outside = User(username='outside_' + uuid4().hex, password_hash=password_hash(password))
        workspace = Workspace(name='合成其他工作区')
        db.add_all([same, outside, workspace]); db.flush()
        db.add_all([Membership(user_id=same.id, workspace_id=scene['p'].workspace_id, role='admin'),
                    Membership(user_id=outside.id, workspace_id=workspace.id, role='admin')])
        db.commit(); same_name, outside_name, other_workspace = same.username, outside.username, workspace.id
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': same_name, 'password': password}); assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert other.get(_url(draft_id, stage), params={'revision': revision}).status_code == 404
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': outside_name, 'password': password}); assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': other_workspace})
        assert other.get(_url(draft_id, stage), params={'revision': revision}).status_code == 404
    me = scene['client'].get('/api/me').json()
    with Session(engine()) as db:
        sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == me['user_id'])).all()
        assert sessions
        for auth in sessions:
            auth.revoked = True
        db.commit()
    assert scene['client'].get(_url(draft_id, stage), params={'revision': revision}).status_code == 401


def test_invalid_stage_and_capture_hash_fail_closed(reading_scene, monkeypatch):
    scene = reading_scene
    draft_id, revision, stage, _ = _start(scene, monkeypatch, 'length')
    assert scene['client'].get(_url(draft_id, 'map-99'), params={'revision': revision}).status_code == 404
    assert scene['client'].get(_url(draft_id, stage) + '/body', params={
        'revision': revision, 'capture_hash': '../outside'}).status_code == 404


@pytest.mark.parametrize('mode,expected', [('oversize', 'oversize'), ('gzip', 'unsupported_encoding'),
                                           ('secret_header', 'unsupported_encoding')])
def test_oversize_and_unsupported_encoding_are_visible_as_quarantined_bytes(reading_scene, monkeypatch, mode, expected):
    from generic_agents.report_evidence_http_capture_v1 import MAP_LIMIT
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-test-only')
    _, _, created = start_delegated(scene, monkeypatch)
    original = httpx.Client
    requests = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * (MAP_LIMIT + 1) if mode == 'oversize' else b'synthetic compressed body'
    def handler(request):
        requests.append(request)
        headers = ({'content-encoding': 'gzip'} if mode == 'gzip' else
                   {'content-encoding': 'sk-synthetic-provider-secret'} if mode == 'secret_header' else {})
        return httpx.Response(200, headers=headers, stream=Body())
    monkeypatch.setattr(gateway.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        revision = row.version; stage = row.data['maps'][0]['chunk_id']
    response = scene['client'].get(_url(created['id'], stage), params={'revision': revision})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['outcome'] == expected and not value['can_apply'] and not value['can_resend']
    assert value['capture']['response_complete'] is (mode != 'oversize')
    assert value['capture']['content_encoding_safe'] is True
    assert value['capture']['encoding_supported'] is (mode == 'oversize')
    assert value['capture']['prefix_only'] is (mode == 'oversize')
    assert 'sk-synthetic-provider-secret' not in response.text
    assert not value['can_view_body'] and value['body_access'] == 'download_disabled'
    body = scene['client'].get(_url(created['id'], stage) + '/body', params={
        'revision': revision, 'capture_hash': value['capture']['capture_hash']})
    assert body.status_code == 404
    assert len(requests) == 1


def test_response_body_secret_is_never_returned_by_status_or_download(reading_scene, monkeypatch):
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-test-only')
    _, _, created = start_delegated(scene, monkeypatch)
    original = httpx.Client
    requests = []
    secret = 'sk-synthetic-secret-in-provider-body'
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            body = {'usage': {'total_tokens': 17}, 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps({'text': secret})}}]}
            yield json.dumps(body).encode()
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Body())
    monkeypatch.setattr(gateway.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        revision = row.version; stage = row.data['maps'][0]['chunk_id']
    response = scene['client'].get(_url(created['id'], stage), params={'revision': revision})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['outcome'] == 'normal_end_unverified' and value['body_access'] == 'download_disabled'
    assert not value['can_view_body'] and secret not in response.text
    body = scene['client'].get(_url(created['id'], stage) + '/body', params={
        'revision': revision, 'capture_hash': value['capture']['capture_hash']})
    assert body.status_code == 404 and secret not in body.text and len(requests) == 1


@pytest.mark.parametrize('tamper', ['request_hash', 'usage', 'finish_reason'])
def test_ledger_or_receipt_mismatch_never_returns_private_capture(reading_scene, monkeypatch, tamper):
    scene = reading_scene
    draft_id, revision, stage, requests = _start(scene, monkeypatch, 'length')
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft_id)
        call = db.get(CloudCall, row.data['maps'][0]['reserved_call_id'])
        data = dict(call.data)
        if tamper == 'request_hash':
            data['request_hash'] = 'f' * 64
        else:
            data['receipt'] = dict(data['receipt'])
            if tamper == 'usage': data['receipt']['usage'] = {'total_tokens': 999}
            else: data['receipt']['finish_reason'] = 'stop'
        call.data = data; db.commit()
    before = len(requests)
    response = scene['client'].get(_url(draft_id, stage), params={'revision': revision})
    assert response.status_code == 409
    assert '合成声明' not in response.text and 'synthetic-test-only' not in response.text
    assert len(requests) == before


def test_concurrent_capture_change_is_not_misreported_as_provider_json_error(reading_scene, monkeypatch):
    from generic_agents import report_evidence_http_capture_v1 as capture
    scene = reading_scene
    draft_id, revision, stage, requests = _start(scene, monkeypatch, 'length')
    def changed(*args, **kwargs):
        raise capture.CaptureError('capture_changed')
    monkeypatch.setattr(capture, 'read_body', changed)
    before = len(requests)
    response = scene['client'].get(_url(draft_id, stage), params={'revision': revision})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'delegated_capture_changed'
    assert len(requests) == before


def test_tampered_capture_file_and_stale_hash_fail_closed(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene = reading_scene
    draft_id, revision, stage, requests = _start(scene, monkeypatch, 'length')
    response = scene['client'].get(_url(draft_id, stage), params={'revision': revision})
    assert response.status_code == 200
    view = response.json()
    path = delegated.root() / scene['p'].workspace_id / draft_id / stage / 'body.bin'
    original = path.read_bytes()
    assert original
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    before = len(requests)
    response = scene['client'].get(_url(draft_id, stage), params={'revision': revision})
    assert response.status_code == 409
    assert len(requests) == before


def test_legacy_evidence_tasks_do_not_acquire_delegated_capture_contract(reading_scene, monkeypatch):
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    manifest.update(name='synthetic_legacy_capture_' + uuid4().hex[:10], description='合成测试')
    manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192)
    cap = publish(scene['client'], manifest)
    body = {**scene['body'], 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    root = '/api/setup/report-evidence-readings'
    preview = scene['client'].post(root + '/preview', json=body); assert preview.status_code == 200, preview.text
    start = scene['client'].post(root, json={**body, 'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert start.status_code == 202, start.text
    row = start.json()['draft']
    response = scene['client'].get(_url(row['id'], row['maps'][0]['chunk_id']), params={'revision': row['revision']})
    assert response.status_code == 404


def test_readonly_recovery_preflight_then_free_restore_without_new_cloud_call(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    _, _, created = start_delegated(scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests)
    original = delegated.response_context
    once = {'fail': True}
    def interrupted(*args, **kwargs):
        if once['fail']:
            once['fail'] = False
            raise ValueError('synthetic interruption after durable wire')
        return original(*args, **kwargs)
    monkeypatch.setattr(delegated, 'response_context', interrupted)
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        assert row.status == 'response_unknown'
        revision = row.version; stage = row.data['maps'][0]['chunk_id']
        before = (row.version, digest(row.data), db.scalar(select(func.count()).select_from(CloudCall)))
    sent = len(requests)
    response = scene['client'].get(_url(created['id'], stage), params={'revision': revision})
    assert response.status_code == 200, response.text
    view = response.json(); check = view['recovery']
    assert check['status'] == 'eligible' and check['target_stage'] == stage
    assert check['remaining_count'] >= 1 and len(check['basis_hash']) == 64
    assert view['can_resend'] is False and len(requests) == sent
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        assert (row.version, digest(row.data), db.scalar(select(func.count()).select_from(CloudCall))) == before
    restored = scene['client'].post('/api/setup/report-evidence-readings/' + created['id'] + '/recover-saved',
        json={'revision': revision, 'request_id': str(uuid4())})
    assert restored.status_code == 200, restored.text
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        assert row.data['maps'][0]['status'] == 'succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before[2]
    assert len(requests) == sent


def test_recovery_preflight_rejects_started_suffix_without_changing_original(reading_scene, monkeypatch):
    import copy
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    _, _, created = start_delegated(scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests)
    original = delegated.response_context; once = {'fail': True}
    def interrupted(*args, **kwargs):
        if once['fail']:
            once['fail'] = False
            raise ValueError('synthetic interruption after durable wire')
        return original(*args, **kwargs)
    monkeypatch.setattr(delegated, 'response_context', interrupted)
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        assert len(row.data['maps']) >= 2
        data = copy.deepcopy(row.data); data['maps'][1]['inflight'] = True; row.data = data; db.commit()
        revision = row.version; stage = row.data['maps'][0]['chunk_id']; before = digest(row.data)
    sent = len(requests)
    response = scene['client'].get(_url(created['id'], stage), params={'revision': revision})
    assert response.status_code == 200, response.text
    check = response.json()['recovery']
    assert check['status'] == 'blocked' and check['blocker'] == 'stage_order_unverified'
    with Session(engine()) as db:
        assert digest(db.get(SetupReportDraft, created['id']).data) == before
    assert len(requests) == sent


@pytest.mark.parametrize('total', [20, 'SYNTHETIC_PROVIDER_SECRET_ONLY'])
def test_fl_draft_receipts_hide_provider_header_and_arbitrary_usage_but_keep_original(
        reading_scene, monkeypatch, total):
    """Provider-controlled metadata must not bypass the private-wire boundary."""
    from generic_agents import report_evidence_pipeline as pipeline
    secret = 'SYNTHETIC_PROVIDER_SECRET_ONLY'
    scene = reading_scene
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-test-only')
    _, _, created = start_delegated(scene, monkeypatch)
    original_client = httpx.Client

    def respond(request):
        sent = json.loads(request.content)
        payload = json.loads(sent['messages'][-1]['content'])
        if payload['schema'] == 'report-evidence-read-payload-v1':
            value = {'schema': 'report-evidence-map-v3', 'claims': [{
                'kind': '职责', 'statement': '合成声明', 'span_refs': [payload['spans'][0][0]],
                'status': 'explicit', 'explanation': ''}]}
        else:
            from tests.test_report_evidence_pipeline_v3 import candidate
            value = candidate(payload)
        envelope = {
            'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)},
                         'finish_reason': 'stop'}],
            'usage': {'total_tokens': total, 'prompt_tokens': 12,
                      'provider_extra': secret, 'completion_tokens': secret},
        }
        wire = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
        return httpx.Response(200, headers={'x-request-id': secret}, stream=httpx.ByteStream(wire))

    monkeypatch.setattr(gateway.httpx, 'Client',
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    assert process_once(created['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, created['id'])
        stage = row.data['maps'][0]
        assert stage['journal'] is not None, (row.status, row.data.get('error_code'), stage.get('error_code'))
        original = pipeline._parsed(row, stage['chunk_id'])['receipt']
        call = db.get(CloudCall, stage['reserved_call_id'])
        assert original['request_id'] == secret
        assert original['usage']['provider_extra'] == secret
        assert call.data['receipt']['request_id'] == secret
        assert call.data['receipt']['usage']['provider_extra'] == secret
        revision, chunk_id = row.version, stage['chunk_id']

    base = '/api/setup/report-evidence-readings'
    paths = [base, base + '/' + created['id'], base + '/' + created['id'] + '/status',
             base + '/by-request/' + created['request_id'],
             _url(created['id'], chunk_id) + '?revision=' + str(revision)]
    for path in paths:
        response = scene['client'].get(path)
        assert response.status_code == 200
        assert secret not in response.text
    detail = scene['client'].get(base + '/' + created['id']).json()
    public_receipt = detail['maps'][0]['receipt']
    assert public_receipt['request_id'] is None
    assert public_receipt['usage'].get('prompt_tokens') == 12
    assert 'provider_extra' not in public_receipt['usage']
    assert 'completion_tokens' not in public_receipt['usage']
    if type(total) is int:
        assert public_receipt['usage']['total_tokens'] == total
    else:
        assert 'total_tokens' not in public_receipt['usage']


def test_fe_stage_receipt_projection_retains_legacy_contract(monkeypatch):
    """The private projection must not alter existing fe_ receipt semantics."""
    from copy import deepcopy
    from types import SimpleNamespace
    from generic_agents import report_evidence_pipeline as pipeline
    receipt = {'provider': 'deepseek', 'model': 'synthetic', 'request_id': 'synthetic-id',
        'call_id': str(uuid4()), 'usage': {'total_tokens': 20, 'provider_detail': 'legacy'},
        'evidence_profile': {}, 'finish_reason': 'stop', 'response_choice_count': 1}
    stage = {'status': 'succeeded', 'reserved_call_id': receipt['call_id'], 'result_hash': None,
        'result_count': 0, 'error': None, 'error_code': None}
    original = deepcopy(receipt)
    monkeypatch.setattr(pipeline, '_stage', lambda _row, _key: stage)
    monkeypatch.setattr(pipeline, '_descriptor', lambda _row, _key: {'consumable': True})
    monkeypatch.setattr(pipeline, '_journal_for', lambda _row, _key: SimpleNamespace(
        read_response=lambda *_args: {'receipt': deepcopy(receipt)}))
    monkeypatch.setattr(pipeline, '_stage_journal_root', lambda *_args: None)
    monkeypatch.setattr(pipeline, 'journal_binding', lambda *_args: {})
    row = SimpleNamespace(request_id='fe_' + str(uuid4()))
    result = pipeline._stage_view(row, 'map-1')
    assert result['receipt'] == original
    assert receipt == original

    # Unknown upstream strings are never surfaced merely because a journal
    # can be read; the immutable original remains available to server checks.
    receipt['request_id'] = 'SYNTHETIC_PROVIDER_SECRET_ONLY'
    receipt['finish_reason'] = 'SYNTHETIC_PROVIDER_SECRET_ONLY'
    receipt['response_choice_count'] = 'SYNTHETIC_PROVIDER_SECRET_ONLY'
    receipt['usage'] = {'total_tokens': 'SYNTHETIC_PROVIDER_SECRET_ONLY', 'prompt_tokens': 12}
    original = deepcopy(receipt)
    row.request_id = 'fl_' + str(uuid4())
    public = pipeline._stage_view(row, 'map-1')['receipt']
    assert public['request_id'] is None and public['finish_reason'] is None
    assert public['response_choice_count'] is None and public['usage'] == {'prompt_tokens': 12}
    assert receipt == original
