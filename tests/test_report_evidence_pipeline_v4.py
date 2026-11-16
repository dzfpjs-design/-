"""Owned empty-PG strict Map integration; root alone executes this file.

Only the provider HTTP client and the existing source fixture's local
conversion/OCR are synthetic. The actual gateway, reserve/callback/settle,
worker lease, immutable journal, projection and API serializers run unchanged.
This is protocol/durability evidence, never real-report model quality.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import httpx
from sqlalchemy.orm import Session

from generic_agents import gateway
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v4 as adapter
from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_strict_map as strict
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply, capture
from tests.test_report_evidence_pipeline_v3 import all_rows, journal_files, fees
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-evidence-readings'


@pytest.fixture
def v4_scene(reading_scene):
    fence(); scene = reading_scene
    manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    manifest.update(name='synthetic_strict_evidence_' + uuid4().hex[:10],
        description='Synthetic strict protocol only; no real provider or quality result')
    manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192,
        report_evidence_output={'schema': 'report-evidence-output-v2',
            'map_transport': strict.DELIVERY, 'max_tokens': 131072})
    cap = publish(scene['client'], manifest)
    assert cap['data']['execution']['max_tokens'] == 8192
    assert cap['data']['execution']['report_evidence_output']['schema'] == 'report-evidence-output-v2'
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


class SyntheticHTTP:
    """Replace only HTTP transport; actual gateway and ledger remain active."""
    def __init__(self, monkeypatch, mode='success'):
        self.mode, self.requests, self.responses, self.values = mode, [], [], []
        monkeypatch.setenv('DEEPSEEK_API_KEY', '')
        monkeypatch.setenv('DASHSCOPE_API_KEY', '')
        monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=NS(
            get_secret_value=lambda: 'SYNTHETIC_NOT_A_PROVIDER_KEY'), dashscope_api_key=None))
        owner = self
        class Client:
            def __init__(self, **options):
                assert options == {'timeout': 180, 'follow_redirects': False, 'trust_env': False}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def post(self, endpoint, **options):
                assert endpoint == strict.ENDPOINT and set(options) == {'headers', 'content'}
                assert options['headers']['Authorization'] == 'Bearer SYNTHETIC_NOT_A_PROVIDER_KEY'
                body = options['content']; assert type(body) is bytes
                request = json.loads(body); payload = json.loads(request['messages'][-1]['content'])
                assert request['tool_choice']['function']['name'] == strict.TOOL_NAME
                assert request['stream'] is False and request['max_tokens'] == 8192
                assert 'response_format' not in request
                owner.requests.append(body); last = len(owner.requests) == 2
                if mode == 'unknown': raise httpx.ReadTimeout('synthetic response unavailable after reserve')
                value = {'schema': 'report-evidence-map-v3', 'claims': [{
                    'kind': '其他类别' if last and mode == 'bad_kind' else '职责',
                    'statement': ' 仅合成声明＋+；不代表原报告含义通过。 ',
                    'span_refs': [payload['spans'][0][0]], 'status': 'explicit', 'explanation': ''}]}
                owner.values.append(copy.deepcopy(value))
                tool = {'id': 'synthetic_tool_' + str(len(owner.requests)), 'type': 'function',
                    'function': {'name': strict.TOOL_NAME, 'arguments': json.dumps(value, ensure_ascii=False, indent=2)}}
                choice = {'finish_reason': 'length' if last and mode == 'length' else 'tool_calls',
                    'message': {'role': 'assistant', 'content': None, 'tool_calls': [tool]}}
                if last and mode == 'multi_tool': choice['message']['tool_calls'].append(copy.deepcopy(tool))
                envelope = {'choices': [choice], 'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
                if mode == 'missing_usage': envelope.pop('usage')
                if last and mode == 'invalid_usage': envelope['usage'] = {'prompt_tokens': True, 'completion_tokens': 8, 'total_tokens': 9}
                if last and mode == 'invalid_usage_extra': envelope['usage']['extra'] = 'synthetic\x00usage'
                response = json.dumps(envelope, ensure_ascii=True, indent=2).encode()
                owner.responses.append(response)
                return NS(content=response, status_code=400 if last and mode == 'http_400' else 200,
                    headers={'x-request-id': 'synthetic-' + str(len(owner.requests))})
        monkeypatch.setattr(gateway.httpx, 'Client', Client)


def start(scene):
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert preview['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V4
    assert preview['map_preparation']['plan']['schema'] == 'report-evidence-read-plan-v2'
    assert preview['input_summary']['map_call_count'] == 2
    assert 'prepared_request_bytes' in preview['input_summary'] and 'prepared_messages_bytes' not in preview['input_summary']
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE, json=command), 202)
    assert accepted['draft']['schema'] == 'report-evidence-reading-draft-v2'
    capture(scene, 'v4-preview', preview); capture(scene, 'v4-start-command', command); capture(scene, 'v4-queued', accepted)
    return preview, command, accepted


def get(scene, identity): return reply(scene['client'].get(BASE + '/' + identity))


def test_v4_actual_gateway_map_journal_cold_read_and_reduce_explicitly_blocked(v4_scene, monkeypatch):
    scene = v4_scene; http = SyntheticHTTP(monkeypatch)
    preview, command, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    final = get(scene, accepted['draft']['id'])
    assert final['status'] == 'awaiting_merge' and final['phase'] == 'awaiting_reduce'
    assert len(http.requests) == 2 and all(stage['status'] == 'succeeded' for stage in final['maps'])
    for position, (stage, chunk) in enumerate(zip(final['maps'], preview['map_preparation']['plan']['chunks'])):
        proof = stage['result']; receipt = stage['receipt']
        assert proof['schema'] == adapter.MAP_TOOL_PROJECTION and proof['claims'] == proof['arguments_projection']['claims']
        assert receipt['finish_reason'] == 'tool_calls' and receipt['response_choice_count'] == receipt['tool_call_count'] == 1
        assert proof['http_body_sha256'] == receipt['response_body_sha256']
        assert proof['arguments_sha256'] != proof['http_body_sha256']
        assert proof['journal_wire_sha256'] == stage['journal']['wire_sha256']
        assert proof['arguments_projection']['wire_sha256'] == receipt['arguments_sha256']
        with Session(engine()) as db:
            row = pipeline.owned(db, scene['p'], final['id'])
            original = pipeline.journal.read_response(pipeline._journal_root(),
                pipeline.journal_binding(row, stage['chunk_id'], stage['reserved_call_id']), stage['journal'])
            assert original['content'].encode('utf-8', errors='surrogateescape') == http.responses[position]
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.data['request_hash'] == chunk['request_hash'] != chunk['messages_hash']
            assert call.reserved_tokens == chunk['input_summary']['token_bound']
            assert call.data['request_body_bytes'] == len(http.requests[position])
            assert adapter.stage_projection(pipeline, row, stage['chunk_id'], pipeline._catalog(preview)) == proof
    snapshot, files, original_fees = all_rows(), journal_files(), fees([item['reserved_call_id'] for item in final['maps']])
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']))['draft'] == final
    with Session(engine()) as db: assert pipeline.draft_view(pipeline.owned(db, scene['p'], final['id']), scene['p']) == final
    reduce = {'revision': final['revision'], 'request_id': 'fer_' + str(uuid4())}
    blocked = scene['client'].post(BASE + '/' + final['id'] + '/reduce-preview', json=reduce)
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'evidence_reduce_unsupported'
    blocked = scene['client'].post(BASE + '/' + final['id'] + '/reduce', json={**reduce, 'preview_hash': 'f' * 64, 'consent': True})
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'evidence_reduce_unsupported'
    assert all_rows() == snapshot and journal_files() == files
    assert fees([item['reserved_call_id'] for item in final['maps']]) == original_fees and len(http.requests) == 2
    capture(scene, 'v4-mapped', final)


@pytest.mark.parametrize('mode', ['bad_kind', 'length', 'multi_tool', 'http_400'])
def test_v4_invalid_original_is_saved_failed_and_free_recovery_cannot_wash_it(v4_scene, monkeypatch, mode):
    scene = v4_scene; http = SyntheticHTTP(monkeypatch, mode)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id']); assert failed['status'] == 'failed'
    assert len(http.requests) == 2 and failed['maps'][0]['status'] == 'succeeded'
    assert failed['maps'][1]['raw_response_saved'] and failed['maps'][1]['result'] is None
    files, original_fees = journal_files(), fees([item['reserved_call_id'] for item in failed['maps']])
    facts = reply(scene['client'].get(BASE + '/' + failed['id'] + '/saved-facts?stage=map-2'))
    assert facts['eligible_for_reduce'] is facts['can_apply'] is facts['can_publish'] is False
    if mode == 'bad_kind':
        assert facts['original_protocol_valid'] is False
        assert facts['facts'][0]['fields']['kind'] == '其他类别'
    else:
        assert facts['availability'] == 'not_json' and facts['facts'] == []
    recovery = {'revision': failed['revision'], 'request_id': str(uuid4())}
    assert scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=recovery).status_code == 422
    assert get(scene, failed['id']) == failed and journal_files() == files
    assert fees([item['reserved_call_id'] for item in failed['maps']]) == original_fees and len(http.requests) == 2


@pytest.mark.parametrize('mode', ['missing_usage', 'invalid_usage', 'invalid_usage_extra'])
def test_v4_valid_arguments_without_trustworthy_usage_keep_conservative_reservation(v4_scene, monkeypatch, mode):
    scene = v4_scene; http = SyntheticHTTP(monkeypatch, mode)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    final = get(scene, accepted['draft']['id'])
    assert final['status'] == 'awaiting_merge' and len(http.requests) == 2
    stages = final['maps'] if mode == 'missing_usage' else [final['maps'][1]]
    for stage in stages:
        assert stage['receipt']['usage_available'] is False and stage['receipt']['usage'] == {}
        with Session(engine()) as db:
            call = db.get(CloudCall, stage['reserved_call_id'])
            assert call.charged_tokens == call.reserved_tokens and call.data['usage_unavailable'] is True
            row = pipeline.owned(db, scene['p'], final['id'])
            loaded = pipeline.journal.read_response(pipeline._journal_root(), pipeline.journal_binding(
                row, stage['chunk_id'], stage['reserved_call_id']), stage['journal'])
            original = json.loads(loaded['content'])
            if mode == 'invalid_usage_extra': assert original['usage']['extra'] == 'synthetic\x00usage'
            assert pipeline.draft_view(row, scene['p']) == final
    assert get(scene, final['id']) == final


def test_v4_exact_original_journal_can_recover_after_local_consume_failure_without_new_http(v4_scene, monkeypatch):
    scene = v4_scene; http = SyntheticHTTP(monkeypatch)
    _, _, accepted = start(scene); original = pipeline._consume
    def interrupt(db, p, row, key):
        if key == 'map-2': raise ValueError('synthetic local post-journal consume window')
        return original(db, p, row, key)
    monkeypatch.setattr(pipeline, '_consume', interrupt)
    assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and failed['maps'][1]['raw_response_saved'] and len(http.requests) == 2
    monkeypatch.setattr(pipeline, '_consume', original)
    files, original_fees = journal_files(), fees([item['reserved_call_id'] for item in failed['maps']])
    command = {'revision': failed['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=command))
    assert recovered['draft']['status'] == 'awaiting_merge'
    snapshot = all_rows()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=command)) == recovered
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + command['request_id'])) == recovered
    assert all_rows() == snapshot and journal_files() == files
    assert fees([item['reserved_call_id'] for item in failed['maps']]) == original_fees and len(http.requests) == 2


def test_v4_unknown_reserved_request_cannot_resume_or_recover_with_a_new_call(v4_scene, monkeypatch):
    scene = v4_scene; http = SyntheticHTTP(monkeypatch, 'unknown')
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'response_unknown' and len(http.requests) == 1 and http.responses == []
    assert failed['maps'][0]['reserved_call_id'] and failed['maps'][0]['journal'] is None
    assert failed['maps'][1]['status'] == 'pending' and failed['maps'][1]['reserved_call_id'] is None
    snapshot = all_rows()
    recovery = {'revision': failed['revision'], 'request_id': str(uuid4())}
    assert scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=recovery).status_code == 409
    resume = {'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())}
    blocked = scene['client'].post(BASE + '/' + failed['id'] + '/resume-preview', json=resume)
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'evidence_resume_blocked'
    assert all_rows() == snapshot and get(scene, failed['id']) == failed and len(http.requests) == 1
