"""Root-run disposable-PG native JSON-schema integration, SOURCE authored only.

All source documents, OCR and HTTP answers are explicitly synthetic. Normal
API upload/source selection, model registration/publication, reservation, worker,
native capture, journal, codec/projection and review paths are real. Only local
document/OCR IO inherited from reading_scene and provider transport are fake.
Fake usage proves protocol accounting, not real cost, token fit or model quality.
Independent sessions/clients below are not a claim of physical restart.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from generic_agents import gateway, report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids, journal_files, original
from tests.test_report_evidence_pipeline_v6_postgres import semantic_wire
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-evidence-readings'
GEN7 = 'report-evidence-map-generation-v7'
REDUCE5 = 'report-evidence-reduction-generation-v5'
DELIVERY = 'qwen-tokenplan-native-json-schema-v1'
ENDPOINT = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
FAKE_KEY = 'PUBLIC_SYNTHETIC_TOKENPLAN_NOT_A_CREDENTIAL'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


class SerializerExport:
    """Eight original HTTP replies only; Root reviews privacy before publication."""
    def __init__(self, scene):
        directory = os.environ.get('GA_PUBLIC_DTO_OUT')
        self.root = Path(directory) if directory else None
        self.prefix, self.files = scene['body']['request_id'] + '-v7-', {}
        if self.root is not None:
            assert self.root.is_absolute() and not self.root.is_symlink()
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def write(self, name, raw):
        assert type(raw) is bytes and len(raw) <= 8 * 1024 * 1024
        filename = self.prefix + name + '.json'
        assert filename not in self.files
        self.files[filename] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        if self.root is not None:
            fd = os.open(self.root / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())

    def response(self, name, response):
        self.write(name, response.content)

    def finish(self):
        expected = {'preview', 'start', 'mapped', 'saved-facts', 'reduce-preview', 'reduce-start', 'reduced', 'human-save'}
        assert set(self.files) == {self.prefix + name + '.json' for name in expected}
        self.write('manifest', encoded({'schema': 'report-evidence-v7-public-synthetic-serializer-manifest-v1',
            'files': copy.deepcopy(self.files), 'synthetic_transport_only': True,
            'real_model_quality_verified': False, 'physical_restart_verified': False}))


@pytest.fixture
def v7_scene(reading_scene, monkeypatch):
    fence()
    marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED', '')
    assert marker.isdigit() and engine().url.port == int(marker)
    with Session(engine()) as db:
        assert tuple(db.execute(text("SELECT current_database(),shobj_description(oid,'pg_database') "
            'FROM pg_database WHERE datname=current_database()')).one()) == (
                'ga_f11_dual_tests', 'owned-disposable-f11-dual')
    scene = reading_scene
    assert reply(scene['client'].get('/api/me'))['role'] == 'admin'
    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='synthetic_native_v7_' + uuid4().hex[:10],
        description='PUBLIC synthetic native HTTP only; not actual model/report quality')
    declaration = {'schema': 'report-evidence-output-v5', 'max_tokens': 32768,
        'map_transport': DELIVERY, 'reduce_transport': DELIVERY, 'endpoint': ENDPOINT}
    manifest['execution'].update(provider='qwen', model='qwen3.8-flash',
        secret_ref='QWEN_TOKENPLAN_API_KEY', max_tokens=8192, report_evidence_output=declaration)
    assert 'thinking_mode' not in manifest['execution']
    cap = publish(scene['client'], manifest)
    assert cap['status'] == 'published' and cap['validated_hash'] == cap['content_hash']
    assert cap['data']['execution']['report_evidence_output'] == declaration
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


class NativeStream(httpx.SyncByteStream):
    def __init__(self, raw, request, interrupted=False):
        self.raw, self.request, self.interrupted = raw, request, interrupted

    def __iter__(self):
        for offset in range(0, len(self.raw), 16384):
            yield self.raw[offset:offset + 16384]
        if self.interrupted:
            # All received bytes form valid JSON, but EOF was NEVER observed.
            raise httpx.ReadError('PUBLIC synthetic interrupted stream before EOF', request=self.request)


class NativeHTTP:
    """Real streaming HTTPX + MockTransport; never stubs capture or projections."""
    def __init__(self, monkeypatch, mode='success'):
        self.mode, self.requests, self.responses, self.values = mode, [], [], []
        self.expected_maps, self.map_count = None, 0
        for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_TOKENPLAN_API_KEY'):
            monkeypatch.setenv(name, '')
        monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=None,
            dashscope_api_key=None, qwen_tokenplan_api_key=NS(get_secret_value=lambda: FAKE_KEY)))
        original_client, owner = httpx.Client, self
        class Client:
            def __init__(self, **options):
                assert options.get('timeout') in (180, 600)
                assert options.get('follow_redirects') is False and options.get('trust_env') is False
                self.timeout = options['timeout']
                assert set(options) <= {'timeout', 'follow_redirects', 'trust_env', 'transport', 'headers'}
                # Replace ONLY transport, even if production explicitly installs
                # its own zero-retry HTTPTransport. No real socket can be used.
                options.pop('transport', None)
                self.inner = original_client(transport=httpx.MockTransport(self.handle), **options)
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def stream(self, method, endpoint, **options):
                assert method == 'POST' and endpoint == ENDPOINT
                assert set(options) == {'headers', 'content'} and type(options['content']) is bytes
                return self.inner.stream(method, endpoint, **options)
            def handle(self, request):
                assert str(request.url) == ENDPOINT
                assert request.headers['authorization'] == 'Bearer ' + FAKE_KEY
                assert request.headers['accept-encoding'] == 'identity'
                posted = json.loads(request.content)
                assert not ({'tools', 'tool_choice', 'thinking'} & set(posted))
                assert posted['model'] == 'qwen3.8-flash' and posted['enable_thinking'] is False
                assert posted['stream'] is False and type(posted['n']) is int and posted['n'] == 1
                response_format = posted['response_format']
                assert response_format['type'] == 'json_schema'
                schema = response_format['json_schema']
                assert set(schema) == {'name', 'strict', 'schema'} and schema['strict'] is True
                reducing = schema['name'] == 'report_evidence_reduction_wire_v6'
                assert schema['name'] == ('report_evidence_reduction_wire_v6' if reducing else 'report_evidence_map_slots_v5')
                assert schema['schema'] == (wire6.ReductionWireV6 if reducing else slots.MapSlotsWireV5).model_json_schema()
                assert self.timeout == (600 if reducing else 180)
                assert posted['max_tokens'] == (32768 if reducing else 8192)
                stage = 'reduce-v5' if reducing else 'map-v7'
                payload = json.loads(posted['messages'][-1]['content'])
                owner.requests.append({'stage': stage, 'body': request.content})
                if owner.mode == 'unknown':
                    raise httpx.ReadTimeout('PUBLIC synthetic no response headers', request=request)
                if reducing:
                    wire = semantic_wire(payload)
                else:
                    owner.map_count += 1
                    refs = [item[0] for item in payload['spans']]
                    reference = 'o:p1/s1' if 'o:p1/s1' in refs else refs[0]
                    wire = {'schema': slots.WIRE_SCHEMA,
                        'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
                    wire['slots']['slot_003'] = {'state': 'present', 'claim': {'kind': '职责',
                        'statement': ' 合成原生声明＋+；只测协议，不代表模型质量。 ',
                        'span_refs': [reference], 'status': 'explicit', 'explanation': ''}}
                owner.values.append(copy.deepcopy(wire))
                raw = encoded({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': encoded(wire).decode('utf-8')}}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})
                status, headers = 200, {'x-request-id': 'public-native-v7-' + str(len(owner.requests))}
                if owner.mode == 'http400':
                    status, raw = 400, encoded({'error': {'message': 'PUBLIC synthetic HTTP rejection'}})
                if owner.mode == 'gzip':
                    import gzip
                    raw = gzip.compress(raw, mtime=0); headers['content-encoding'] = 'gzip'
                if owner.mode == 'unsafe_encoding':
                    headers['content-encoding'] = '\x00gzip'
                if owner.mode == 'deadline':
                    assert len(raw) < 16384, 'deadline fixture must be one received short transport chunk'
                owner.responses.append(raw)
                return httpx.Response(status, headers=headers, request=request,
                    stream=NativeStream(raw, request, interrupted=owner.mode == 'partial'))
        monkeypatch.setattr(gateway.httpx, 'Client', Client)


def get(scene, identity):
    return reply(scene['client'].get(BASE + '/' + identity))


def start(scene, http, export=None):
    response = scene['client'].post(BASE + '/preview', json=scene['body']); preview = reply(response)
    if export: export.response('preview', response)
    spec = preview['map_preparation']['generation_contract']
    assert spec['schema'] == GEN7 and spec['evidence_stage'] == 'map-v7' and spec['delivery'] == DELIVERY
    assert spec['schema_proof']['payload_origin'] == 'message.content'
    assert spec['schema_proof']['provider_schema'] == spec['output_schema']
    assert preview['map_preparation']['plan']['schema'] == 'report-evidence-read-plan-v2'
    assert preview['source_snapshot']['coverage']['full_report_confirmed'] is False
    assert spec['dispatch_options']['max_tokens'] == 8192
    http.expected_maps = preview['input_summary']['map_call_count']
    assert 1 <= http.expected_maps <= 24 and http.requests == []
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    response = scene['client'].post(BASE, json=command); accepted = reply(response, 202)
    if export: export.response('start', response)
    assert accepted['draft']['schema'] == 'report-evidence-reading-draft-v3'
    return preview, command, accepted


def mapped(scene, http, export=None):
    preview, command, accepted = start(scene, http, export)
    assert process_once(accepted['draft']['job_id'])
    response = scene['client'].get(BASE + '/' + accepted['draft']['id']); middle = reply(response)
    if export: export.response('mapped', response)
    assert middle['status'] == 'awaiting_merge' and middle['phase'] == 'awaiting_reduce'
    assert all(stage['status'] == 'succeeded' for stage in middle['maps'])
    assert len(http.requests) == http.expected_maps and middle['model_result'] is None
    return preview, command, accepted, middle


def reduced(scene, http, middle, export=None):
    body = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=body); preview = reply(response)
    if export: export.response('reduce-preview', response)
    spec = preview['reduction_preparation']['generation_contract']
    assert spec['schema'] == REDUCE5 and spec['evidence_stage'] == 'reduce-v5'
    assert spec['delivery'] == DELIVERY and spec['dispatch_options']['max_tokens'] == 32768
    assert spec['schema_proof']['payload_origin'] == 'message.content'
    assert len(http.requests) == http.expected_maps
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command); accepted = reply(response, 202)
    if export: export.response('reduce-start', response)
    assert accepted['receipt']['schema'] == 'report-evidence-reduce-receipt-v3'
    assert process_once(accepted['draft']['job_id'])
    response = scene['client'].get(BASE + '/' + middle['id']); final = reply(response)
    if export: export.response('reduced', response)
    return preview, command, accepted, final


def test_v7_normal_native_chain_two_human_saves_cas_nonce_and_cold_serializers(v7_scene, monkeypatch, account):
    scene = v7_scene; http = NativeHTTP(monkeypatch); export = SerializerExport(scene)
    preview, command, accepted, middle = mapped(scene, http, export)
    for index, (stage, chunk) in enumerate(zip(middle['maps'], preview['map_preparation']['plan']['chunks'])):
        assert stage['result']['slot_positions'] == [2]
        assert original(scene, middle, stage['chunk_id'])['content'].encode('utf-8') == http.responses[index]
        receipt = stage['receipt']
        assert receipt['delivery'] == DELIVERY and receipt['finish_reason'] == 'stop'
        assert receipt['response_choice_count'] == 1 and receipt['tool_call_count'] == 0
        assert all(receipt[key] is None for key in ('tool_name', 'tool_type', 'tool_call_id', 'tool_calls_hash'))
        assert receipt['evidence_profile']['stage'] == 'map-v7'
        assert receipt['response_complete'] is True and receipt['http_content_encoding'] in (None, '', 'identity')
        assert receipt['capture_hash'] == capture_view(scene, middle, stage['chunk_id'])['capture_hash']
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.data['delivery'] == DELIVERY and call.data['request_endpoint'] == ENDPOINT
            assert call.data['request_hash'] == chunk['request_hash'] != chunk['messages_hash']
            assert call.data['request_body_bytes'] == len(http.requests[index]['body'])
    rows, files, money = all_rows(), journal_files(), fees(call_ids(middle))
    response = scene['client'].get(BASE + '/' + middle['id'] + '/saved-facts', params={'stage': 'map-1'})
    facts = reply(response); export.response('saved-facts', response)
    assert facts['schema'] == 'report-evidence-saved-facts-v2' and facts['generation_schema'] == GEN7
    assert facts['facts'][0]['position'] == 2 and facts['facts'][0]['slot_name'] == 'slot_003'
    assert facts['tool_metadata']['delivery'] == DELIVERY
    assert facts['can_apply'] is facts['can_publish'] is facts['eligible_for_reduce'] is False
    assert all_rows() == rows and journal_files() == files and fees(call_ids(middle)) == money
    _, reduce_command, reduce_accepted, final = reduced(scene, http, middle, export)
    assert final['status'] == 'review' and final['schema'] == 'report-evidence-reading-draft-v3'
    assert [request['stage'] for request in http.requests] == ['map-v7'] * http.expected_maps + ['reduce-v5']
    assert final['model_result']['schema'] == wire6.CANDIDATE and final['review_value'] is None
    model = copy.deepcopy(final['model_result']); metadata = final['reduction']['projection_metadata']
    assert metadata['schema'] == adapter.REDUCE_TOOL_PROJECTION and 'result' not in metadata
    assert final['reduction']['journal']['schema'] == 'report-evidence-journal-descriptor-v2'
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        assert adapter.stage_projection(pipeline, row, 'reduce', pipeline._catalog(preview)) == {
            **metadata, 'result': model, 'reconciliation': final['reduction']['reconciliation']}
    human = manual_from_projection_v6(model, note='公开合成V7私人核对稿🙂')
    assert human['result'] == wire6.decode_reduction_wire_v6(http.values[-1])['result']
    assert all(human['result']['businesses'][0][key] for key in ('participants', 'interactions', 'conditions', 'coordination'))
    first_command = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': human}
    first = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=first_command))
    later_value = copy.deepcopy(human); later_value['note'] += ' 后来人工稿必须保留。'
    later_value['result']['businesses'][0]['steps'][0]['role'] = '合成后来值班员'
    second = {'revision': first['draft']['revision'], 'request_id': str(uuid4()), 'value': later_value}
    response = scene['client'].put(BASE + '/' + final['id'] + '/review', json=second)
    later = reply(response); export.response('human-save', response); export.finish()
    assert later['draft']['model_result'] == model and later['draft']['review_value'] == later_value
    assert [entry['value'] for entry in later['draft']['review_history']] == [human, later_value]
    rows, money, files, count = all_rows(), fees(call_ids(final)), journal_files(), len(http.requests)
    replay = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=first_command))
    assert replay['receipt'] == first['receipt'] and replay['current_matches'] is False and replay['draft'] == later['draft']
    assert reply(scene['client'].get(BASE + '/' + final['id'] + '/review-receipts/' + first_command['request_id'])) == replay
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + final['id'] + '/reduce', json=reduce_command), 202)['receipt'] == reduce_accepted['receipt']
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json={**first_command, 'request_id': str(uuid4())}).status_code == 409
    changed = copy.deepcopy(first_command); changed['value']['note'] += '改体'
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=changed).status_code == 409
    assert all_rows() == rows and journal_files() == files and fees(call_ids(final)) == money and len(http.requests) == count
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], final['id']), scene['p']) == later['draft']
    with TestClient(app) as fresh:
        login = reply(fresh.post('/api/login', json={'username': account[0], 'password': account[1]}))
        fresh.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(fresh.get(BASE + '/' + final['id'])) == later['draft']
    assert journal_files() == files and fees(call_ids(final)) == money and len(http.requests) == count
    handoff = {'report_revision': later['draft']['revision'], 'review_receipt_hash': later['receipt']['receipt_hash'],
        'candidate_hash': digest(later_value['result']), 'source_hash': preview['source_hash'],
        'validation_report_hash': 'f' * 64, 'configuration_revision': scene['body']['revision'],
        'configuration_hash': 'f' * 64, 'business_indexes': [0], 'request_id': str(uuid4()), 'preview_hash': 'f' * 64, 'consent': True}
    rows = all_rows()
    blocked = scene['client'].post('/api/setup/report-drafts/' + final['id'] + '/configuration-handoff', json=handoff)
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'full_semantics_mapping_required'
    assert all_rows() == rows and final['can_apply'] is final['can_publish'] is False


CAPTURE_KEYS = set(('schema actor_id workspace_id draft_id draft_revision generation_schema stage stage_status '
    'reserved_call_id delivery request_hash messages_hash source_hash catalog_hash profile_hash schema_proof_hash '
    'token_bound capture_state response_complete http_status http_content_encoding content_encoding_safe '
    'headers_hash body_bytes body_sha256 capture_hash recovery_eligible blocker can_apply can_publish view_hash').split())


def storage_files():
    """All bytes of this synthetic disposable storage, never another workspace."""
    fence(); root = Path(settings().storage_dir)
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
        for path in root.rglob('*') if path.is_file()}


def call_job_rows():
    rows = all_rows()
    return {name: rows[name] for name in (Job.__tablename__, CloudCall.__tablename__)}


def map_wire_path(draft, key='map-1'):
    stage = next(item for item in draft['maps'] if item['chunk_id'] == key)
    return (Path('report-evidence-journal') / draft['workspace_id'] / draft['id']
        / key / stage['reserved_call_id'] / 'wire.bin')


def capture_view(scene, draft, key='map-1'):
    value = reply(scene['client'].get(BASE + '/' + draft['id'] + '/captures/' + key))
    assert set(value) == CAPTURE_KEYS and value['schema'] == 'report-evidence-native-capture-view-v1'
    assert value['actor_id'] == draft['actor_id'] and value['workspace_id'] == draft['workspace_id']
    assert value['draft_id'] == draft['id'] and value['draft_revision'] == draft['revision']
    assert value['generation_schema'] == GEN7 and value['stage'] == key and value['delivery'] == DELIVERY
    assert value['can_apply'] is value['can_publish'] is False
    return value


def original_capture(scene, draft, key='map-1'):
    from generic_agents import report_evidence_native_bridge_v1 as bridge
    from generic_agents import report_evidence_http_capture_v1 as capture
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], draft['id'])
        context = bridge.context(pipeline, row, key)
        state = capture.load(bridge.root(), context)
        body = capture.read_body(bridge.root(), context, state['capture_hash'])
    return context, state, body


@pytest.mark.parametrize('mode', ['partial', 'unknown', 'deadline'])
def test_v7_partial_even_whole_valid_json_and_missing_headers_never_become_success(v7_scene, monkeypatch, mode):
    from generic_agents import report_evidence_http_capture_v1 as capture
    from generic_agents import report_evidence_native_bridge_v1 as bridge
    scene = v7_scene; http = NativeHTTP(monkeypatch, mode)
    progress_hashes = []
    actual_append = capture.NativeHttpCapture.append
    def observed_append(sink, raw):
        value = actual_append(sink, raw)
        progress_hashes.append(capture.load(bridge.root(), sink.context)['capture_hash'])
        return value
    monkeypatch.setattr(capture.NativeHttpCapture, 'append', observed_append)
    _, command, accepted = start(scene, http)
    if mode == 'deadline':
        # Only the gateway clock changes: HTTPX, leases and the test runner keep
        # their real clocks. The first whole received chunk crosses 180 seconds.
        ticks = iter((0.0, 181.0))
        monkeypatch.setattr(gateway, 'time', NS(monotonic=lambda: next(ticks, 181.0)))
    assert process_once(accepted['draft']['job_id'])
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] in {'failed', 'response_unknown'} and stopped['model_result'] is None
    assert len(http.requests) == 1 and len(call_ids(stopped)) == 1
    assert all(stage['status'] != 'succeeded' and stage['result_hash'] is None for stage in stopped['maps'])
    rows, files, money = all_rows(), storage_files(), fees(call_ids(stopped))
    view = capture_view(scene, stopped)
    assert view['capture_state'] == 'partial' and view['response_complete'] is False
    assert view['recovery_eligible'] is False and view['blocker']
    if mode in {'partial', 'deadline'}:
        _, state, raw = original_capture(scene, stopped)
        assert raw == http.responses[0] and type(json.loads(raw)) is dict
        assert state['response_complete'] is False and state['ready'] is False
        assert view['body_sha256'] == hashlib.sha256(raw).hexdigest()
        response = scene['client'].get(BASE + '/' + stopped['id'] + '/capture-body',
            params={'stage': 'map-1', 'capture_hash': view['capture_hash']})
        assert response.status_code == 200 and response.content == raw
        assert response.headers['content-type'].startswith('application/octet-stream')
        assert response.headers['content-disposition'].startswith('attachment;')
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert progress_hashes and progress_hashes[-1] != view['capture_hash']
        stale = scene['client'].get(BASE + '/' + stopped['id'] + '/capture-body',
            params={'stage': 'map-1', 'capture_hash': progress_hashes[-1]})
        assert stale.status_code == 409
    else:
        assert view['http_status'] is None and view['body_bytes'] == 0
    # Both status/raw reads and receipt-first replay are truly read-only.
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    attempt = scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved',
        json={'revision': stopped['revision'], 'request_id': str(uuid4())})
    assert attempt.status_code == 409 and attempt.json()['detail']['code'] == 'evidence_saved_response_incomplete'
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money
    assert len(http.requests) == 1 and get(scene, stopped['id']) == stopped
    if mode == 'partial':
        username, password = 'public_v7_other_' + uuid4().hex[:10], uuid4().hex
        assert scene['client'].post('/api/users', json={'username': username,
            'password': password, 'role': 'operator'}).status_code == 201
        with TestClient(app) as other:
            login = reply(other.post('/api/login', json={'username': username, 'password': password}))
            other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
            before = all_rows(), storage_files(), fees(call_ids(stopped))
            assert other.get(BASE + '/' + stopped['id'] + '/captures/map-1').status_code == 404
            assert other.get(BASE + '/' + stopped['id'] + '/capture-body',
                params={'stage': 'map-1', 'capture_hash': view['capture_hash']}).status_code == 404
            assert (all_rows(), storage_files(), fees(call_ids(stopped))) == before


@pytest.mark.parametrize('fault', ['before_receipt', 'before_journal', 'half_journal'])
def test_v7_early_complete_capture_or_half_journal_recovers_free_and_keeps_future_pending(v7_scene, monkeypatch, fault):
    from generic_agents import report_evidence_native_bridge_v1 as bridge
    from generic_agents import report_evidence_http_capture_v1 as capture
    scene = v7_scene; http = NativeHTTP(monkeypatch)
    preview, _, accepted = start(scene, http)
    assert http.expected_maps >= 2, 'fixture must include genuinely uncalled later chunks'
    fired = []
    with monkeypatch.context() as failure:
        if fault == 'before_receipt':
            actual_finish = capture.NativeHttpCapture.finish
            def interrupted(sink, *, eof):
                value = actual_finish(sink, eof=eof)
                fired.append(True)
                raise RuntimeError('PUBLIC synthetic crash after complete capture before gateway receipt')
            failure.setattr(capture.NativeHttpCapture, 'finish', interrupted)
        elif fault == 'before_journal':
            def interrupted(*args, **kwargs):
                fired.append(True)
                raise OSError('PUBLIC synthetic interruption after complete capture before journal')
            failure.setattr(bridge, 'write_missing_journal', interrupted)
        else:
            adapter_io = pipeline.journal
            actual_new = adapter_io._new_file
            def interrupted(directory, name, raw):
                if name == 'receipt.bin' and not fired:
                    fired.append(True)
                    raise OSError('PUBLIC synthetic interruption after wire fsync before receipt')
                return actual_new(directory, name, raw)
            failure.setattr(adapter_io, '_new_file', interrupted)
        assert process_once(accepted['draft']['job_id'])
    assert fired and len(http.requests) == 1
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] in {'failed', 'response_unknown'}
    assert stopped['maps'][0]['status'] != 'succeeded'
    assert all(stage['status'] == 'pending' and stage['reserved_call_id'] is None for stage in stopped['maps'][1:])
    _, state, raw = original_capture(scene, stopped)
    assert state['state'] == 'complete' and state['response_complete'] is True
    assert raw == http.responses[0]
    if fault == 'before_receipt':
        # Genuine gateway exception/finally must have preserved a real receipt;
        # no hand-built successful receipt can hide the old receipt{} mismatch.
        with Session(engine()) as db:
            call = db.get(CloudCall, stopped['maps'][0]['reserved_call_id'])
            assert call.status == 'failed' and call.data['receipt']['call_id'] == call.id
            assert call.data['receipt']['capture_hash'] == state['capture_hash']
            assert call.data['receipt']['usage']['total_tokens'] == 20
    # New independent Session and normal GET must not silently promote files.
    files, rows, money = storage_files(), all_rows(), fees(call_ids(stopped))
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], stopped['id']), scene['p']) == stopped
    view = capture_view(scene, stopped)
    assert view['recovery_eligible'] and view['capture_hash'] == state['capture_hash']
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money
    own_wire = str(map_wire_path(stopped))
    old_wire = {name: values for name, values in files.items() if name == own_wire}
    assert bool(old_wire) == (fault == 'half_journal')
    command = {'revision': stopped['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved', json=command))
    current, receipt = recovered['draft'], recovered['receipt']
    assert receipt['schema'] == 'report-evidence-recovery-receipt-v2'
    assert receipt['new_cloud_calls'] == 0 and receipt['completion_scope'] == 'partial_maps'
    assert receipt['remaining_chunks'] == [chunk['chunk_id'] for chunk in preview['map_preparation']['plan']['chunks'][1:]]
    assert len(receipt['recovered_stages']) == 1
    detail = receipt['recovered_stages'][0]
    assert detail == {'stage': 'map-1', 'call_id': stopped['maps'][0]['reserved_call_id'],
        'capture_hash': state['capture_hash'], 'journal_descriptor_hash': current['maps'][0]['journal']['descriptor_hash'],
        'result_hash': current['maps'][0]['result_hash']}
    assert current['status'] == 'failed' and current['model_result'] is None
    assert current['maps'][0]['status'] == 'succeeded' and current['maps'][0]['result']['slot_positions'] == [2]
    assert all(stage['status'] == 'pending' and stage['reserved_call_id'] is None for stage in current['maps'][1:])
    assert call_ids(current) == call_ids(stopped) and len(http.requests) == 1
    assert fees(call_ids(current)) == money
    # New files may be added, but existing wire/capture/source bytes/inodes/times
    # cannot be rewritten to make a half-journal appear recoverable.
    after_files = storage_files()
    assert all(after_files[name] == values for name, values in files.items())
    assert all(after_files[name] == values for name, values in old_wire.items())
    assert call_job_rows() == {name: rows[name] for name in (Job.__tablename__, CloudCall.__tablename__)}
    before_replay = all_rows(), storage_files()
    assert reply(scene['client'].post(BASE + '/' + current['id'] + '/recover-saved', json=command)) == recovered
    assert reply(scene['client'].get(BASE + '/' + current['id'] + '/recovery-receipts/' + command['request_id'])) == recovered
    assert (all_rows(), storage_files()) == before_replay and len(http.requests) == 1
    changed = {**command, 'revision': current['revision']}
    assert scene['client'].post(BASE + '/' + current['id'] + '/recover-saved', json=changed).status_code == 409


def test_v7_conflicting_half_journal_cannot_be_repaired_or_overwritten(v7_scene, monkeypatch):
    scene = v7_scene; http = NativeHTTP(monkeypatch)
    _, _, accepted = start(scene, http)
    actual_new, fired = pipeline.journal._new_file, []
    with monkeypatch.context() as failure:
        def interrupted(directory, name, raw):
            if name == 'receipt.bin' and not fired:
                fired.append(True)
                raise OSError('PUBLIC synthetic half journal')
            return actual_new(directory, name, raw)
        failure.setattr(pipeline.journal, '_new_file', interrupted)
        assert process_once(accepted['draft']['job_id'])
    stopped = get(scene, accepted['draft']['id'])
    assert fired and len(http.requests) == 1
    wires = [Path(settings().storage_dir) / map_wire_path(stopped)]
    assert wires[0].is_file()
    # Deliberate corruption of this one public synthetic fixture only.
    wires[0].write_bytes(b'PUBLIC_CONFLICT_DO_NOT_OVERWRITE')
    rows, files, money = all_rows(), storage_files(), fees(call_ids(stopped))
    response = scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved',
        json={'revision': stopped['revision'], 'request_id': str(uuid4())})
    assert response.status_code == 422 and response.json()['detail']['code'] == 'evidence_saved_response_invalid'
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money
    assert len(http.requests) == 1 and wires[0].read_bytes() == b'PUBLIC_CONFLICT_DO_NOT_OVERWRITE'


@pytest.mark.parametrize('mode', ['http400', 'gzip', 'unsafe_encoding'])
def test_v7_complete_error_or_unsafe_encoding_is_preserved_never_adopted(v7_scene, monkeypatch, mode):
    scene = v7_scene; http = NativeHTTP(monkeypatch, mode)
    _, _, accepted = start(scene, http)
    assert process_once(accepted['draft']['job_id'])
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] in {'failed', 'response_unknown'} and stopped['model_result'] is None
    assert len(http.requests) == 1 and all(stage['status'] != 'succeeded' for stage in stopped['maps'])
    _, state, raw = original_capture(scene, stopped)
    assert raw == http.responses[0] and state['response_complete'] is True
    assert state['can_adopt'] is False
    rows, files, money = all_rows(), storage_files(), fees(call_ids(stopped))
    view = capture_view(scene, stopped)
    assert view['response_complete'] is True and not view['recovery_eligible'] and view['blocker']
    if mode == 'http400':
        assert view['http_status'] == 400 and state['ready'] is True
    else:
        assert state['state'] == 'quarantined' and state['ready'] is False
        if mode == 'unsafe_encoding':
            assert view['http_content_encoding'] is None and view['content_encoding_safe'] is False
        else:
            assert view['http_content_encoding'] == 'gzip' and view['content_encoding_safe'] is True
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money
    result = scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved',
        json={'revision': stopped['revision'], 'request_id': str(uuid4())})
    expected = (422, 'evidence_saved_response_invalid') if mode == 'http400' else (409, 'evidence_saved_response_incomplete')
    assert result.status_code == expected[0] and result.json()['detail']['code'] == expected[1]
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money and len(http.requests) == 1


def test_v7_explicit_native_and_legacy_gen6_previews_do_not_mix_or_retag(v7_scene, monkeypatch):
    scene = v7_scene; http = NativeHTTP(monkeypatch)
    old_manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    old_manifest.update(name='synthetic_unchanged_gen6_' + uuid4().hex[:10],
        description='Public legacy Gen6 preview remains its original strict-tool protocol')
    old_manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192,
        report_evidence_output={'schema': 'report-evidence-output-v4', 'max_tokens': 32768,
            'map_transport': 'deepseek-strict-report-tool-v3', 'reduce_transport': 'deepseek-strict-report-tool-v3'})
    old_cap = publish(scene['client'], old_manifest)
    old_body = {**scene['body'], 'model_id': old_cap['id'], 'request_id': 'fe_' + str(uuid4())}
    old = reply(scene['client'].post(BASE + '/preview', json=old_body))
    native = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert old['map_preparation']['generation_contract']['schema'] == 'report-evidence-map-generation-v6'
    assert old['map_preparation']['generation_contract']['delivery'] == 'deepseek-strict-report-tool-v3'
    assert native['map_preparation']['generation_contract']['schema'] == GEN7
    rows, files = all_rows(), storage_files()
    for body, wrong in ((scene['body'], old), (old_body, native)):
        response = scene['client'].post(BASE, json={**body, 'preview_hash': wrong['preview_hash'], 'consent': True})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'preview_changed'
    assert reply(scene['client'].post(BASE + '/preview', json=old_body)) == old
    assert all_rows() == rows and storage_files() == files and http.requests == []
