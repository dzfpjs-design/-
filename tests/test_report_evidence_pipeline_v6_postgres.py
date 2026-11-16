"""Root-run owned PG64828/65028/65128/65228 V6 integration; never real model/quality evidence.

Normal admin login, public synthetic DOCX, persisted two-page fake local OCR,
normal register/validate/publish and actual gateway/reserve/worker/journal/
schema/projection paths are used. Only provider HTTP transport is synthetic.
Usage is fabricated protocol input, not a token-capacity or invoice claim.
No old protocol constant, proof, receipt or pipeline function is retagged.
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
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from generic_agents import gateway, report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from generic_agents import report_evidence_structured_tool_v3 as tool
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupReportDraft
from generic_agents.report_evidence_manual_v5 import MAX_REVIEW_BYTES, ManualReviewValueV5
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, candidate as candidate5, fees
from tests.test_report_evidence_pipeline_v5 import call_ids, journal_files, original
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-evidence-readings'
GEN6 = 'report-evidence-map-generation-v6'
REDUCE4 = 'report-evidence-reduction-generation-v4'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


class SerializerExport:
    """Original HTTP serializer bytes only; Root reviews before public copying."""
    def __init__(self, scene):
        directory = os.environ.get('GA_PUBLIC_DTO_OUT')
        self.root = Path(directory) if directory else None
        self.prefix, self.files = scene['body']['request_id'] + '-v6-', {}
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

    def finish(self, expected):
        assert len(self.files) == expected
        manifest = {'schema': 'report-evidence-v6-public-synthetic-serializer-manifest-v1',
            'files': copy.deepcopy(self.files), 'synthetic_transport_only': True,
            'real_model_quality_verified': False, 'physical_restart_verified': False}
        self.write('manifest', encoded(manifest))


@pytest.fixture
def v6_scene(reading_scene, monkeypatch):
    fence(); url = engine().url
    owned_port = os.environ.get('GA_F11_DUAL_TESTS_OWNED')
    assert owned_port in {'64828', '65028', '65128', '65228'} and url.port == int(owned_port)
    with Session(engine()) as db:
        assert tuple(db.execute(text("SELECT current_database(),shobj_description(oid,'pg_database') "
            'FROM pg_database WHERE datname=current_database()')).one()) == (
                'ga_f11_dual_tests', 'owned-disposable-f11-dual')
    scene = reading_scene
    assert reply(scene['client'].get('/api/me'))['role'] == 'admin'
    manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    manifest.update(name='synthetic_ref_arm_v6_' + uuid4().hex[:10],
        description='Public synthetic HTTP V6 protocol only; no model-quality assertion')
    declaration = {'schema': 'report-evidence-output-v4', 'map_transport': tool.DELIVERY,
        'reduce_transport': tool.DELIVERY, 'max_tokens': 32768}
    manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192,
        report_evidence_output=declaration)
    cap = publish(scene['client'], manifest)
    assert cap['status'] == 'published' and cap['validated_hash'] == cap['content_hash']
    assert cap['data']['execution']['report_evidence_output'] == declaration
    # Only this EMPTY synthetic ledger is locally opened. Driver/env stay0.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


def semantic_wire(payload, large=False):
    value = copy.deepcopy(payload)
    value['input_claims'] = [{key: item for key, item in claim.items() if key != 'key'}
        for claim in payload['input_claims']]
    candidate = candidate5(value)
    business = candidate['result']['businesses'][0]
    business['conditions'] = [{'kind': 'precondition', 'statement': '人工前置条件仍待核对',
        'step_indices': [0], 'evidence': {'status': 'inferred',
            'citations': copy.deepcopy(business['citations']),
            'explanation': '仅合成条件持久化用例，未把推断当作明示。'}}]
    wire = wire6.encode_reduction_wire_v6(candidate)
    if large:
        initial = copy.deepcopy(wire['result']['global_claims'])
        for number in range(96):
            phrase = '合成容量核对；完整字段只读，不代表报告事实。'
            statement = (phrase * (1900 // len(phrase) + 1))[:1900]
            wire['result']['global_claims'].append({'kind': '其他', 'statement': statement,
                'span_refs': [], 'status': 'unknown',
                'explanation': '第' + str(number + 1) + '条是合成容量声明，含义未知，不新增来源事实。'})
        assert len(wire['result']['global_claims']) <= 160
        assert wire['result']['global_claims'][:len(initial)] == initial
        assert MAX_REVIEW_BYTES < len(encoded(wire)) <= wire6.MAX_WIRE_BYTES
    return wire


class V6SyntheticHTTP:
    """Only MockTransport changes; original request bytes go to real gateway."""
    def __init__(self, monkeypatch, mode='success'):
        self.mode, self.requests, self.responses, self.values = mode, [], [], []
        self.expected_maps, self.map_count = None, 0
        monkeypatch.setenv('DEEPSEEK_API_KEY', ''); monkeypatch.setenv('DASHSCOPE_API_KEY', '')
        monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=NS(
            get_secret_value=lambda: 'SYNTHETIC_NOT_A_PROVIDER_KEY'), dashscope_api_key=None))
        original_client, owner = httpx.Client, self
        class Client:
            def __init__(self, **options):
                assert options.get('timeout') in (180, 600)
                assert options == {'timeout': options['timeout'], 'follow_redirects': False, 'trust_env': False}
                self.timeout = options['timeout']
                self.inner = original_client(transport=httpx.MockTransport(self.handle), **options)
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def post(self, endpoint, **options):
                assert endpoint == tool.ENDPOINT and set(options) == {'headers', 'content'}
                assert type(options['content']) is bytes
                return self.inner.post(endpoint, **options)
            def handle(self, request):
                assert str(request.url) == tool.ENDPOINT
                assert request.headers['authorization'] == 'Bearer SYNTHETIC_NOT_A_PROVIDER_KEY'
                posted = json.loads(request.content); payload = json.loads(posted['messages'][-1]['content'])
                reducing = posted['tool_choice']['function']['name'] == 'submit_report_evidence_reduction'
                stage = 'reduce-v4' if reducing else 'map-v6'
                assert self.timeout == (600 if reducing else 180)
                assert posted['max_tokens'] == (32768 if reducing else 8192)
                assert posted['thinking'] == {'type': 'disabled'} and posted['stream'] is False
                assert 'response_format' not in posted and len(posted['tools']) == 1
                function = posted['tools'][0]['function']
                assert function['strict'] is True and function['name'] == tool.stage_policy(stage)['tool_name']
                assert posted['tool_choice']['function']['name'] == function['name']
                owner.requests.append({'stage': stage, 'body': request.content})
                if owner.mode == 'unknown':
                    raise httpx.ReadTimeout('Synthetic unavailable response, never a real provider', request=request)
                if owner.mode == 'http400':
                    raw = encoded({'error': {'message': 'Public synthetic schema rejection'}})
                    owner.responses.append(raw)
                    return httpx.Response(400, content=raw, request=request)
                if reducing:
                    wire = semantic_wire(payload, owner.mode == 'large')
                else:
                    owner.map_count += 1
                    references = [item[0] for item in payload['spans']]
                    reference = 'o:p1/s1' if 'o:p1/s1' in references else references[0]
                    wire = {'schema': slots.WIRE_SCHEMA,
                        'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
                    wire['slots']['slot_003'] = {'state': 'present', 'claim': {'kind': '职责',
                        'statement': ' 合成待核声明＋+；不代表模型质量。 ', 'span_refs': [reference],
                        'status': 'explicit', 'explanation': ''}}
                owner.values.append(copy.deepcopy(wire))
                arguments = encoded(wire).decode('utf-8')
                entry = {'id': 'synthetic_v6_' + str(len(owner.requests)), 'type': 'function',
                    'function': {'name': function['name'], 'arguments': arguments}}
                entries = [entry]
                if reducing and owner.mode == 'reduce_multi': entries.append(copy.deepcopy(entry))
                last = not reducing and owner.map_count == owner.expected_maps
                finish = 'length' if last and owner.mode == 'map_length' else 'tool_calls'
                raw = encoded({'choices': [{'finish_reason': finish, 'message': {'role': 'assistant',
                    'content': None, 'tool_calls': entries}}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})
                assert len(raw) <= tool.stage_policy(stage)['max_response_bytes']
                owner.responses.append(raw)
                return httpx.Response(200, content=raw, request=request,
                    headers={'x-request-id': 'synthetic-v6-' + str(len(owner.requests))})
        monkeypatch.setattr(gateway.httpx, 'Client', Client)


def get(scene, identity):
    return reply(scene['client'].get(BASE + '/' + identity))


def start(scene, http, export=None):
    response = scene['client'].post(BASE + '/preview', json=scene['body']); preview = reply(response)
    if export: export.response('preview', response)
    spec = preview['map_preparation']['generation_contract']
    assert spec['schema'] == GEN6 == preparation.GENERATION_V6
    assert spec['evidence_stage'] == 'map-v6' and spec['delivery'] == tool.DELIVERY
    assert spec['schema_proof']['schema'] == 'report-evidence-strict-ref-arm-proof-v3'
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
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=body)
    preview = reply(response)
    if export: export.response('reduce-preview', response)
    spec = preview['reduction_preparation']['generation_contract']
    assert spec['schema'] == REDUCE4 and spec['evidence_stage'] == 'reduce-v4'
    assert spec['delivery'] == tool.DELIVERY and spec['dispatch_options']['max_tokens'] == 32768
    assert spec['schema_proof']['schema'] == 'report-evidence-strict-ref-arm-proof-v3'
    assert len(http.requests) == http.expected_maps
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command)
    accepted = reply(response, 202)
    if export: export.response('reduce-start', response)
    assert accepted['receipt']['schema'] == 'report-evidence-reduce-receipt-v3'
    assert accepted['receipt']['map_inputs_hash'] == preview['reduction_preparation']['map_inputs_hash']
    assert process_once(accepted['draft']['job_id'])
    response = scene['client'].get(BASE + '/' + middle['id']); final = reply(response)
    if export: export.response('reduced', response)
    return preview, command, accepted, final


def test_v6_normal_full_chain_two_human_saves_receipt_first_and_cold_serializers(v6_scene, monkeypatch, account):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch); export = SerializerExport(scene)
    preview, command, accepted, middle = mapped(scene, http, export)
    for index, (stage, chunk) in enumerate(zip(middle['maps'], preview['map_preparation']['plan']['chunks'])):
        assert stage['result']['slot_positions'] == [2]
        assert original(scene, middle, stage['chunk_id'])['content'].encode('utf-8') == http.responses[index]
        assert stage['receipt']['delivery'] == tool.DELIVERY and stage['receipt']['evidence_profile']['stage'] == 'map-v6'
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.data['delivery'] == tool.DELIVERY
            assert call.data['request_hash'] == chunk['request_hash'] != chunk['messages_hash']
            assert call.data['request_body_bytes'] == len(http.requests[index]['body'])
    rows, files, money = all_rows(), journal_files(), fees(call_ids(middle))
    response = scene['client'].get(BASE + '/' + middle['id'] + '/saved-facts', params={'stage': 'map-1'})
    facts = reply(response); export.response('saved-facts', response)
    assert facts['schema'] == 'report-evidence-saved-facts-v2' and facts['generation_schema'] == GEN6
    assert facts['facts'][0]['position'] == 2 and facts['facts'][0]['slot_name'] == 'slot_003'
    assert facts['tool_metadata']['delivery'] == tool.DELIVERY
    assert facts['can_apply'] is facts['can_publish'] is facts['eligible_for_reduce'] is False
    assert all_rows() == rows and journal_files() == files and fees(call_ids(middle)) == money
    _, reduce_command, reduce_accepted, final = reduced(scene, http, middle, export)
    assert final['status'] == 'review' and final['schema'] == 'report-evidence-reading-draft-v3'
    assert [request['stage'] for request in http.requests] == ['map-v6'] * http.expected_maps + ['reduce-v4']
    assert final['model_result']['schema'] == wire6.CANDIDATE and final['review_value'] is None
    model = copy.deepcopy(final['model_result']); metadata = final['reduction']['projection_metadata']
    assert metadata['schema'] == adapter.REDUCE_TOOL_PROJECTION and 'result' not in metadata
    assert final['reduction']['journal']['schema'] == 'report-evidence-journal-descriptor-v2'
    assert final['reduction']['receipt']['evidence_profile']['max_output_tokens'] == 32768
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        assert adapter.stage_projection(pipeline, row, 'reduce', pipeline._catalog(preview)) == {
            **metadata, 'result': model, 'reconciliation': final['reduction']['reconciliation']}
    human = manual_from_projection_v6(model, note='合成V6私人核对稿🙂')
    assert human['result'] == wire6.decode_reduction_wire_v6(http.values[-1])['result']
    assert human['result']['global_claims']
    assert all(human['result']['businesses'][0][key] for key in ('participants', 'interactions', 'conditions', 'coordination'))
    first_command = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': human}
    response = scene['client'].put(BASE + '/' + final['id'] + '/review', json=first_command)
    first = reply(response)
    assert first['current_matches'] and first['draft']['review_value'] == human
    later_value = copy.deepcopy(human); later_value['note'] += ' 后来人稿必须保留。'
    later_value['result']['businesses'][0]['steps'][0]['role'] = '合成后来核对职责'
    second = {'revision': first['draft']['revision'], 'request_id': str(uuid4()), 'value': later_value}
    response = scene['client'].put(BASE + '/' + final['id'] + '/review', json=second)
    later = reply(response); export.response('human-save', response); export.finish(8)
    assert later['draft']['model_result'] == model and later['draft']['review_value'] == later_value
    assert [entry['value'] for entry in later['draft']['review_history']] == [human, later_value]
    rows, money, files, count = all_rows(), fees(call_ids(final)), journal_files(), len(http.requests)
    replay = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=first_command))
    assert replay['receipt'] == first['receipt'] and replay['current_matches'] is False and replay['draft'] == later['draft']
    assert reply(scene['client'].get(BASE + '/' + final['id'] + '/review-receipts/' + first_command['request_id'])) == replay
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + final['id'] + '/reduce', json=reduce_command), 202)['receipt'] == reduce_accepted['receipt']
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json={**first_command, 'request_id': str(uuid4())}).status_code == 409
    changed = copy.deepcopy(first_command); changed['value']['note'] += 'nonce改体'
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


@pytest.mark.parametrize('mode', ['http400', 'map_length', 'reduce_multi'])
def test_v6_failed_original_http_is_durable_and_replay_or_free_parse_cannot_pay_again(v6_scene, monkeypatch, mode):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch, mode)
    if mode == 'reduce_multi':
        _, command, accepted, middle = mapped(scene, http)
        _, _, _, failed = reduced(scene, http, middle); key = 'reduce'; stage = failed['reduction']
    else:
        _, command, accepted = start(scene, http); assert process_once(accepted['draft']['job_id'])
        failed = get(scene, accepted['draft']['id'])
        stage = next(item for item in failed['maps'] if item['reserved_call_id'] and item['status'] == 'failed')
        key = stage['chunk_id']
    assert failed['status'] == 'failed' and stage['raw_response_saved']
    assert original(scene, failed, key)['content'].encode('utf-8') == http.responses[-1]
    assert stage['receipt']['delivery'] == tool.DELIVERY
    if mode == 'http400':
        assert len(http.requests) == 1 and stage['receipt']['http_status'] == 400
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.status == 'failed' and call.charged_tokens == call.reserved_tokens > 0
    rows, files, money, count = all_rows(), journal_files(), fees(call_ids(failed)), len(http.requests)
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    recovery = {'revision': failed['revision'], 'request_id': str(uuid4())}
    rejected = scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=recovery)
    expected = {'http400': (409, 'evidence_saved_response_incomplete'),
        'map_length': (422, 'evidence_saved_response_invalid'),
        'reduce_multi': (422, 'evidence_saved_response_invalid')}[mode]
    assert rejected.status_code == expected[0] and rejected.json()['detail']['code'] == expected[1]
    assert get(scene, failed['id']) == failed and all_rows() == rows
    assert journal_files() == files and fees(call_ids(failed)) == money and len(http.requests) == count


def test_v6_unknown_response_stops_remaining_stages_and_never_retries_reserved_call(v6_scene, monkeypatch):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch, 'unknown')
    _, command, accepted = start(scene, http); assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'response_unknown' and len(http.requests) == 1 and http.responses == []
    assert failed['maps'][0]['reserved_call_id'] and failed['maps'][0]['journal'] is None
    assert all(item['reserved_call_id'] is None for item in failed['maps'][1:])
    rows, money, files = all_rows(), fees(call_ids(failed)), journal_files()
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    for path, body in [('recover-saved', {'revision': failed['revision'], 'request_id': str(uuid4())}),
            ('resume-preview', {'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())})]:
        assert scene['client'].post(BASE + '/' + failed['id'] + '/' + path, json=body).status_code == 409
    assert all_rows() == rows and fees(call_ids(failed)) == money and journal_files() == files and len(http.requests) == 1


def test_v6_and_old_v5_previews_cannot_be_mixed_into_fresh_paid_authorization(v6_scene, monkeypatch):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch)
    current = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    old_manifest = copy.deepcopy(scene['cap']['data']); old_manifest['name'] += '_legacy'
    declaration = old_manifest['execution']['report_evidence_output']
    declaration.update(schema='report-evidence-output-v3', map_transport='deepseek-strict-report-tool-v2',
        reduce_transport='deepseek-strict-report-tool-v2')
    old_cap = publish(scene['client'], old_manifest)
    old_body = {**scene['body'], 'request_id': 'fe_' + str(uuid4()), 'model_id': old_cap['id']}
    legacy = reply(scene['client'].post(BASE + '/preview', json=old_body))
    assert current['map_preparation']['generation_contract']['schema'] == GEN6
    assert legacy['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V5
    assert current['map_preparation']['generation_contract']['schema_proof']['schema'] != legacy['map_preparation']['generation_contract']['schema_proof']['schema']
    rows = all_rows()
    for body, other in ((scene['body'], legacy), (old_body, current)):
        response = scene['client'].post(BASE, json={**body, 'preview_hash': other['preview_hash'], 'consent': True})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'preview_changed'
    assert all_rows() == rows and http.requests == [] and http.responses == []


def test_v6_actual_db_flush_interruption_after_raw_checkpoint_recovers_without_new_call(v6_scene, monkeypatch):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch)
    _, _, accepted = start(scene, http); armed = [True]
    def interrupted(db, _context, _instances):
        if not armed[0] or len(http.requests) != http.expected_maps: return
        for row in db.dirty:
            if isinstance(row, SetupReportDraft) and row.id == accepted['draft']['id']:
                stage = row.data['maps'][-1]
                if stage['status'] == 'succeeded' and stage['journal'] is not None:
                    armed[0] = False
                    raise RuntimeError('Synthetic local flush interruption after original journal checkpoint')
    event.listen(Session, 'before_flush', interrupted)
    try: assert process_once(accepted['draft']['job_id'])
    finally: event.remove(Session, 'before_flush', interrupted)
    assert armed[0] is False
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and failed['maps'][-1]['raw_response_saved']
    files, money, count = journal_files(), fees(call_ids(failed)), len(http.requests)
    body = {'revision': failed['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body))
    assert recovered['draft']['status'] == 'awaiting_merge'
    rows = all_rows()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body)) == recovered
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + body['request_id'])) == recovered
    assert all_rows() == rows and journal_files() == files and fees(call_ids(failed)) == money and len(http.requests) == count


def test_v6_large_semantic_model_remains_readable_without_relaxing_human512_cap(v6_scene, monkeypatch):
    scene = v6_scene; http = V6SyntheticHTTP(monkeypatch, 'large')
    preview, _, _, middle = mapped(scene, http)
    _, _, _, final = reduced(scene, http, middle)
    model = copy.deepcopy(final['model_result'])
    assert final['status'] == 'review' and len(encoded(model)) > MAX_REVIEW_BYTES
    assert len(model['global_claims']) == len(middle['maps']) + 96
    assert all(item['status'] == 'unknown' and item['span_refs'] == [] and item['explanation'].strip()
        for item in model['global_claims'][-96:])
    assert 512 * 1024 < len(http.responses[-1]) <= 1024 * 1024
    assert final['model_result_hash'] == digest(model) and final['review_value'] is None
    assert final['reduction']['receipt']['evidence_profile']['max_output_tokens'] == 32768
    assert all(item['receipt']['evidence_profile']['max_output_tokens'] == 8192 for item in final['maps'])
    assert original(scene, final, 'reduce')['content'].encode('utf-8') == http.responses[-1]
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        rebuilt = adapter.stage_projection(pipeline, row, 'reduce', pipeline._catalog(preview))
        assert rebuilt['result'] == model and rebuilt['projection_hash'] == final['reduction']['projection_metadata']['projection_hash']
    human = {'schema': 'report-evidence-manual-review-v5',
        'result': wire6.decode_reduction_wire_v6(http.values[-1])['result'], 'note': '合成容量核对；不能缩短冒充原候选'}
    assert ManualReviewValueV5.model_validate(human).model_dump() == human
    with pytest.raises(ValueError, match='capacity exceeded'): manual_from_projection_v6(model)
    rows, money, files, count = all_rows(), fees(call_ids(final)), journal_files(), len(http.requests)
    response = scene['client'].get(BASE + '/' + final['id']); assert reply(response) == final
    export = SerializerExport(scene); export.response('large-semantic', response); export.finish(1)
    rejected = scene['client'].put(BASE + '/' + final['id'] + '/review', json={
        'revision': final['revision'], 'request_id': str(uuid4()), 'value': human})
    assert rejected.status_code == 422 and rejected.json()['detail']['code'] == 'evidence_review_invalid'
    assert get(scene, final['id']) == final and all_rows() == rows
    assert fees(call_ids(final)) == money and journal_files() == files and len(http.requests) == count
