"""Root-run EMPTY owned-PG Map5/Reduce3 integration, never model quality.

The reused fixture normally logs in an admin, uploads a public synthetic DOCX,
and persists its real conversion/OCR source chain; only local source IO is fake.
This file replaces provider HTTP transport only with httpx.MockTransport. Real
registration/publication, preview, reserve, callbacks, worker lease, journals,
schema/projection/complete reduction and human persistence remain unmocked.
One test injects a one-shot SQLAlchemy flush error after the original journal
checkpoint; no pipeline/validator function is replaced by a synthetic success.
No actual paid response, real credential, original report or quality gold used.
"""
import copy
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from generic_agents import gateway, report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents import report_evidence_journal as journal_v1
from generic_agents import report_evidence_journal_v2 as journal_v2
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupReportDraft
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, candidate as candidate5, fees
from tests.test_vertical_slice import publish
from generic_agents.worker import process_once


BASE = '/api/setup/report-evidence-readings'


def capture(scene, name, response):
    """Optional root-only export of ORIGINAL public synthetic serializer bytes."""
    directory = os.environ.get('GA_PUBLIC_DTO_OUT')
    if not directory:
        return
    assert re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name)
    root = Path(directory)
    assert root.is_absolute() and not root.is_symlink()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert len(response.content) <= 8 * 1024 * 1024
    path = root / (scene['body']['request_id'] + '-' + name + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(response.content); output.flush(); os.fsync(output.fileno())


def get(scene, identity, label=None):
    response = scene['client'].get(BASE + '/' + identity)
    value = reply(response)
    if label: capture(scene, label, response)
    return value


def journal_files():
    """Only this root-owned temporary synthetic storage; both namespaces."""
    fence(); root = Path(settings().storage_dir)
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for name in ('report-evidence-journal', 'report-evidence-reduction-journal-v2')
        for path in (root / name).rglob('*') if path.is_file()}


def call_ids(draft):
    return [stage['reserved_call_id'] for stage in [*draft['maps'], draft['reduction']] if stage['reserved_call_id']]


@pytest.fixture
def v5_scene(reading_scene, monkeypatch):
    fence(); scene = reading_scene
    assert reply(scene['client'].get('/api/me'))['role'] == 'admin'
    manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    manifest.update(name='synthetic_bounded_report_' + uuid4().hex[:10],
        description='Synthetic strict HTTP only; no original-report quality evidence')
    declaration = {'schema': 'report-evidence-output-v3', 'map_transport': tool.DELIVERY,
        'reduce_transport': tool.DELIVERY, 'max_tokens': 131072}
    manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192,
        report_evidence_output=declaration)
    cap = publish(scene['client'], manifest)
    assert cap['status'] == 'published' and cap['validated_hash'] == cap['content_hash']
    assert cap['data']['execution']['report_evidence_output'] == declaration
    # Only this empty-PG synthetic fixture's real ledger may reserve fake HTTP
    # calls. The root driver/environment remains zero-budget and keyless.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


class SyntheticHTTP:
    """One real HTTP-client request per actual reserve; no network or retries."""
    def __init__(self, monkeypatch, mode='success'):
        self.mode, self.requests, self.responses, self.values, self.payloads = mode, [], [], [], []
        self.expected_maps, self.map_count = None, 0
        monkeypatch.setenv('DEEPSEEK_API_KEY', ''); monkeypatch.setenv('DASHSCOPE_API_KEY', '')
        monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=NS(
            get_secret_value=lambda: 'SYNTHETIC_NOT_A_PROVIDER_KEY'), dashscope_api_key=None))
        original_client = httpx.Client; owner = self
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
                body = request.content; posted = json.loads(body)
                payload = json.loads(posted['messages'][-1]['content'])
                reducing = posted['tool_choice']['function']['name'] == 'submit_report_evidence_reduction'
                stage = 'reduce-v3' if reducing else 'map-v5'
                assert self.timeout == (600 if reducing else 180)
                assert posted['max_tokens'] == (131072 if reducing else 8192)
                assert posted['thinking'] == {'type': 'disabled'} and posted['stream'] is False
                assert 'response_format' not in posted and len(posted['tools']) == 1
                assert posted['tools'][0]['function']['strict'] is True
                assert posted['tool_choice']['function']['name'] == tool.stage_policy(stage)['tool_name']
                owner.requests.append({'stage': stage, 'body': body}); owner.payloads.append(copy.deepcopy(payload))
                if owner.mode == 'unknown':
                    raise httpx.ReadTimeout('synthetic reserved response unavailable', request=request)
                if reducing:
                    # Build an explicitly synthetic candidate response from the
                    # real frozen payload. Input keys stay in the request; the
                    # new output schema deliberately has no model-owned IDs.
                    semantic = copy.deepcopy(payload)
                    semantic['input_claims'] = [{key: value for key, value in claim.items() if key != 'key'}
                        for claim in payload['input_claims']]
                    value5 = candidate5(semantic)
                    business = value5['result']['businesses'][0]
                    business['conditions'] = [{'kind': 'precondition', 'statement': '人工前置条件仍待核对',
                        'step_indices': [0], 'evidence': {'status': 'inferred',
                        'citations': copy.deepcopy(business['citations']),
                        'explanation': '仅合成条件保存用例，未把原文推断当作明示。'}}]
                    value = wire6.encode_reduction_wire_v6(value5)
                else:
                    owner.map_count += 1
                    refs = [item[0] for item in payload['spans']]
                    reference = 'o:p1/s1' if 'o:p1/s1' in refs else refs[0]
                    value = {'schema': slots.WIRE_SCHEMA,
                        'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
                    value['slots']['slot_003'] = {'state': 'present', 'claim': {
                        'kind': '职责', 'statement': ' 合成待核声明＋+；不代表模型质量。 ',
                        'span_refs': [reference], 'status': 'explicit', 'explanation': ''}}
                owner.values.append(copy.deepcopy(value))
                arguments = json.dumps(value, ensure_ascii=False, indent=2)
                if reducing and owner.mode == 'large_reduce': arguments = ' ' * 270000 + arguments
                tool_call = {'id': 'synthetic_' + str(len(owner.requests)), 'type': 'function',
                    'function': {'name': tool.stage_policy(stage)['tool_name'], 'arguments': arguments}}
                last_map = not reducing and owner.map_count == owner.expected_maps
                finish = 'length' if owner.mode == 'map_length' and last_map else 'tool_calls'
                tools = [tool_call]
                if reducing and owner.mode == 'reduce_multi': tools.append(copy.deepcopy(tool_call))
                usage = {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}
                if owner.mode == 'bad_usage': usage['unsafe_extra'] = 'synthetic\x00usage'
                response = json.dumps({'choices': [{'finish_reason': finish,
                    'message': {'role': 'assistant', 'content': None, 'tool_calls': tools}}], 'usage': usage},
                    ensure_ascii=True, indent=2).encode('ascii')
                owner.responses.append(response)
                return httpx.Response(200, content=response, headers={'x-request-id': 'synthetic-' + str(len(owner.requests))}, request=request)
        monkeypatch.setattr(gateway.httpx, 'Client', Client)


def start(scene, http):
    response = scene['client'].post(BASE + '/preview', json=scene['body']); preview = reply(response)
    assert preview['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V5
    assert preview['map_preparation']['plan']['schema'] == 'report-evidence-read-plan-v2'
    http.expected_maps = preview['input_summary']['map_call_count']
    assert 1 <= http.expected_maps <= 24 and len(http.requests) == 0
    assert preview['source_snapshot']['coverage']['full_report_confirmed'] is False
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    capture(scene, 'v5-preview', response)
    response = scene['client'].post(BASE, json=command); accepted = reply(response, 202)
    assert accepted['draft']['schema'] == 'report-evidence-reading-draft-v3'
    capture(scene, 'v5-start', response)
    return preview, command, accepted


def mapped(scene, http):
    preview, command, accepted = start(scene, http)
    assert process_once(accepted['draft']['job_id'])
    value = get(scene, accepted['draft']['id'], 'v5-mapped')
    assert value['status'] == 'awaiting_merge' and value['phase'] == 'awaiting_reduce'
    assert all(stage['status'] == 'succeeded' for stage in value['maps'])
    assert value['model_result'] is None and value['review_value'] is None
    assert value['reduction']['preview'] is None and value['reduction']['reserved_call_id'] is None
    assert len(http.requests) == http.expected_maps
    return preview, command, accepted, value


def reduce(scene, http, middle):
    body = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=body)
    preview = reply(response); capture(scene, 'v5-reduce-preview', response)
    assert preview['reduction_preparation']['generation_contract']['evidence_stage'] == 'reduce-v3'
    assert preview['reduction_preparation']['generation_contract']['dispatch_options']['max_tokens'] == 131072
    assert len(http.requests) == http.expected_maps  # Preview never dispatches.
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command)
    accepted = reply(response, 202); capture(scene, 'v5-reduce-start', response)
    assert accepted['receipt']['schema'] == 'report-evidence-reduce-receipt-v3'
    assert accepted['receipt']['map_inputs_hash'] == preview['reduction_preparation']['map_inputs_hash']
    assert process_once(accepted['draft']['job_id'])
    return preview, command, accepted, get(scene, middle['id'], 'v5-reduced')


def original(scene, draft, key):
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], draft['id'])
        stage = pipeline._stage(row, key); journal = pipeline._journal_for(row, key)
        binding = pipeline.journal_binding(row, key, stage['reserved_call_id'])
        return journal.read_response(pipeline._journal_root(row, key), binding, pipeline._descriptor(row, key))


def test_v5_real_map_reduce_manual_cas_nonce_facts_and_independent_readback(v5_scene, monkeypatch, account):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch)
    preview, command, accepted, middle = mapped(scene, http)
    for position, (stage, chunk) in enumerate(zip(middle['maps'], preview['map_preparation']['plan']['chunks'])):
        assert stage['result']['slot_positions'] == [2]
        assert original(scene, middle, stage['chunk_id'])['content'].encode('utf-8') == http.responses[position]
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.data['request_hash'] == chunk['request_hash'] != chunk['messages_hash']
            assert call.data['request_body_bytes'] == len(http.requests[position]['body'])
    facts_rows, facts_files, facts_fees = all_rows(), journal_files(), fees(call_ids(middle))
    response = scene['client'].get(BASE + '/' + middle['id'] + '/saved-facts', params={'stage': 'map-1'})
    facts = reply(response); capture(scene, 'v5-saved-facts', response)
    assert facts['schema'] == 'report-evidence-saved-facts-v2'
    assert facts['draft_id'] == middle['id'] and facts['generation_schema'] == preparation.GENERATION_V5
    assert facts['can_apply'] is facts['can_publish'] is facts['eligible_for_reduce'] is False
    assert all_rows() == facts_rows and journal_files() == facts_files and fees(call_ids(middle)) == facts_fees
    reduce_preview, reduce_command, reduce_accepted, final = reduce(scene, http, middle)
    assert final['schema'] == 'report-evidence-reading-draft-v3' and final['status'] == 'review'
    assert final['model_result']['schema'] == wire6.CANDIDATE
    assert final['review_value'] is None and final['review_history'] == []
    assert len(http.requests) == http.expected_maps + 1
    assert [item['stage'] for item in http.requests] == ['map-v5'] * http.expected_maps + ['reduce-v3']
    metadata = final['reduction']['projection_metadata']
    assert metadata['schema'] == adapter.REDUCE_TOOL_PROJECTION
    assert 'result' not in metadata and 'reconciliation' not in metadata
    assert final['reduction']['journal']['schema'] == 'report-evidence-journal-descriptor-v2'
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        rebuilt = adapter.stage_projection(pipeline, row, 'reduce', pipeline._catalog(preview))
        assert rebuilt == {**metadata, 'result': final['model_result'], 'reconciliation': final['reduction']['reconciliation']}
    model = copy.deepcopy(final['model_result']); files, money = journal_files(), fees(call_ids(final))
    # Explicit human fixture from the known synthetic wire. This is NOT a UI
    # model6->manual5 feature or automatic adoption of an arbitrary model reply.
    human_result = wire6.decode_reduction_wire_v6(http.values[-1])['result']
    human = {'schema': 'report-evidence-manual-review-v5', 'result': human_result, 'note': '私人核对稿🙂'}
    for field in ('participants', 'interactions', 'conditions', 'coordination'):
        assert human['result']['businesses'][0][field]
    review = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': human}
    response = scene['client'].put(BASE + '/' + final['id'] + '/review', json=review)
    first = reply(response); capture(scene, 'v5-human-save', response)
    assert first['current_matches'] and first['draft']['review_value'] == human
    later_value = copy.deepcopy(human); later_value['note'] += ' 后来输入仍保留。'
    later_value['result']['global_claims'][0]['statement'] += ' 人工补充仍待核。'
    later_command = {'revision': first['draft']['revision'], 'request_id': str(uuid4()), 'value': later_value}
    later = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=later_command))
    assert later['draft']['model_result'] == model and later['draft']['review_value'] == later_value
    assert [item['value'] for item in later['draft']['review_history']] == [human, later_value]
    snapshot = all_rows()
    replay = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review))
    assert replay['receipt'] == first['receipt'] and replay['current_matches'] is False
    assert replay['draft'] == later['draft']
    assert reply(scene['client'].get(BASE + '/' + final['id'] + '/review-receipts/' + review['request_id'])) == replay
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + final['id'] + '/reduce', json=reduce_command), 202)['receipt'] == reduce_accepted['receipt']
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json={**review, 'request_id': str(uuid4())}).status_code == 409
    changed = copy.deepcopy(review); changed['value']['note'] += '改体'
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=changed).status_code == 409
    assert all_rows() == snapshot and journal_files() == files and fees(call_ids(final)) == money
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], final['id']), scene['p']) == later['draft']
    # New normal client/session, not a claimed process or physical PG restart.
    with TestClient(app) as fresh:
        login = reply(fresh.post('/api/login', json={'username': account[0], 'password': account[1]}))
        fresh.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(fresh.get(BASE + '/' + final['id'])) == later['draft']
    assert fees(call_ids(final)) == money and journal_files() == files
    before_handoff = all_rows()
    handoff = {'report_revision': later['draft']['revision'], 'review_receipt_hash': later['receipt']['receipt_hash'],
        'candidate_hash': digest(later_value['result']), 'source_hash': preview['source_hash'],
        'validation_report_hash': 'f' * 64, 'configuration_revision': scene['body']['revision'],
        'configuration_hash': 'f' * 64, 'business_indexes': [0], 'request_id': str(uuid4()), 'preview_hash': 'f' * 64, 'consent': True}
    blocked = scene['client'].post('/api/setup/report-drafts/' + final['id'] + '/configuration-handoff', json=handoff)
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'full_semantics_mapping_required'
    assert all_rows() == before_handoff and final['can_apply'] is final['can_publish'] is False


def test_v5_reduce_new_journal_retains_more_than_256k_without_widening_old_map_journal(v5_scene, monkeypatch):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch, 'large_reduce')
    _, _, _, middle = mapped(scene, http)
    _, _, _, final = reduce(scene, http, middle)
    assert final['status'] == 'review'
    assert 256 * 1024 < len(http.responses[-1]) <= 1024 * 1024
    loaded = original(scene, final, 'reduce')
    assert loaded['content'].encode('utf-8') == http.responses[-1]
    assert loaded['descriptor']['schema'] == 'report-evidence-journal-descriptor-v2'
    assert journal_v1.MAX_RESPONSE_BYTES == 256 * 1024 and journal_v2.MAX_RESPONSE_BYTES == 1024 * 1024
    assert all(stage['journal']['schema'] == 'report-evidence-journal-descriptor-v1' for stage in final['maps'])
    paths = journal_files()
    assert any(name.startswith('report-evidence-reduction-journal-v2/') for name in paths)
    assert any(name.startswith('report-evidence-journal/') for name in paths)


@pytest.mark.parametrize('mode', ['map_length', 'reduce_multi'])
def test_v5_invalid_completion_original_body_saved_before_gate_and_free_recovery_never_washes_it(v5_scene, monkeypatch, mode):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch, mode)
    if mode == 'map_length':
        _, _, accepted = start(scene, http); assert process_once(accepted['draft']['job_id'])
        failed = get(scene, accepted['draft']['id']); key = failed['maps'][-1]['chunk_id']
        stage = failed['maps'][-1]
    else:
        _, _, _, middle = mapped(scene, http)
        _, _, _, failed = reduce(scene, http, middle); key = 'reduce'; stage = failed['reduction']
    assert failed['status'] == 'failed' and stage['raw_response_saved']
    assert original(scene, failed, key)['content'].encode('utf-8') == http.responses[-1]
    files, money, requests = journal_files(), fees(call_ids(failed)), len(http.requests)
    recovery = {'revision': failed['revision'], 'request_id': str(uuid4())}
    assert scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=recovery).status_code == 422
    assert get(scene, failed['id']) == failed and len(http.requests) == requests
    assert journal_files() == files and fees(call_ids(failed)) == money


def test_v5_unknown_reservation_does_not_retry_or_pay_remaining_maps(v5_scene, monkeypatch):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch, 'unknown')
    _, _, accepted = start(scene, http); assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'response_unknown' and len(http.requests) == 1 and http.responses == []
    assert failed['maps'][0]['reserved_call_id'] and failed['maps'][0]['journal'] is None
    assert all(stage['status'] == 'pending' and stage['reserved_call_id'] is None for stage in failed['maps'][1:])
    snapshot = all_rows()
    assert scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json={
        'revision': failed['revision'], 'request_id': str(uuid4())}).status_code == 409
    assert scene['client'].post(BASE + '/' + failed['id'] + '/resume-preview', json={
        'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())}).status_code == 409
    assert all_rows() == snapshot and len(http.requests) == 1


def test_v5_unavailable_usage_keeps_original_bad_usage_and_conservative_ledger_reservation(v5_scene, monkeypatch):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch, 'bad_usage')
    _, _, _, middle = mapped(scene, http); _, _, _, final = reduce(scene, http, middle)
    assert final['status'] == 'review'
    for stage in [*final['maps'], final['reduction']]:
        assert stage['receipt']['usage_available'] is False and stage['receipt']['usage'] == {}
        key = 'reduce' if stage is final['reduction'] else stage['chunk_id']
        assert json.loads(original(scene, final, key)['content'])['usage']['unsafe_extra'] == 'synthetic\x00usage'
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.charged_tokens == call.reserved_tokens and call.data['usage_unavailable'] is True


def test_v5_actual_flush_failure_after_last_raw_checkpoint_recovers_without_new_http_or_fee(v5_scene, monkeypatch):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch)
    _, _, accepted = start(scene, http); armed = [True]
    def fail_local_save(db, flush_context, instances):
        if not armed[0] or len(http.requests) != http.expected_maps: return
        for row in db.dirty:
            if isinstance(row, SetupReportDraft) and row.id == accepted['draft']['id']:
                last = row.data['maps'][-1]
                if last['status'] == 'succeeded' and last['journal'] is not None:
                    armed[0] = False
                    raise RuntimeError('synthetic DB flush interruption after original journal checkpoint')
    event.listen(Session, 'before_flush', fail_local_save)
    try: assert process_once(accepted['draft']['job_id'])
    finally: event.remove(Session, 'before_flush', fail_local_save)
    assert armed[0] is False
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and failed['maps'][-1]['raw_response_saved']
    files, money, requests = journal_files(), fees(call_ids(failed)), len(http.requests)
    command = {'revision': failed['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=command))
    assert recovered['draft']['status'] == 'awaiting_merge'
    snapshot = all_rows()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=command)) == recovered
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + command['request_id'])) == recovered
    assert all_rows() == snapshot and len(http.requests) == requests
    assert fees(call_ids(failed)) == money and journal_files() == files


def test_v5_other_real_operator_cannot_read_or_authorize_private_owner_draft(v5_scene, monkeypatch):
    scene = v5_scene; http = SyntheticHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http)
    username, password = 'v5_operator_' + uuid4().hex[:12], uuid4().hex
    assert scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': 'operator'}).status_code == 201
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(other.get('/api/me'))['role'] == 'operator'
        snapshot, files, money = all_rows(), journal_files(), fees(call_ids(middle))
        for path in (BASE + '/' + middle['id'], BASE + '/by-request/' + scene['body']['request_id'],
                BASE + '/' + middle['id'] + '/saved-facts?stage=map-1'):
            assert other.get(path).status_code == 404
        assert other.post(BASE + '/' + middle['id'] + '/reduce-preview', json={
            'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}).status_code == 404
        assert all_rows() == snapshot and journal_files() == files and fees(call_ids(middle)) == money
    assert len(http.requests) == http.expected_maps
