"""One root-run EMPTY-PG capacity case; no actual model/quality evidence.

Reuse the frozen normal-login/upload/source/publish/reserve/worker fixtures.
Only the MockTransport Reduce response gains explicitly synthetic, unknown
semantic declarations. No schema/projection/gateway/ledger path is stubbed.
The body is large because of semantic field values, not whitespace padding.
Fabricated HTTP usage is protocol input; this does not show that a real model
can fit this body within 32768 output tokens or produce useful report content.
"""
import copy
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from generic_agents import gateway, report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.report_evidence_manual_v5 import ManualReviewValueV5, MAX_REVIEW_BYTES
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from generic_agents.worker import process_once
from tests.test_vertical_slice import publish
from tests.test_report_evidence_pipeline_v3 import candidate as candidate5
from tests.test_report_evidence_pipeline_v5 import (
    BASE, SyntheticHTTP, v5_scene, reading_scene, full_source_scene, local_runtime,
    mapped, get, original, capture, reply, all_rows, fees, call_ids, journal_files,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


class LargeSemanticHTTP(SyntheticHTTP):
    """Change only the synthetic HTTP handler's actual Reduce reply bytes."""
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        client_type, owner = gateway.httpx.Client, self
        original_handle = client_type.handle

        def handle(client, request):
            posted = json.loads(request.content)
            if posted['tool_choice']['function']['name'] != 'submit_report_evidence_reduction':
                return original_handle(client, request)
            # The old helper deliberately pins its Reduce fixture to 131072.
            # This separate real MockTransport branch receives the ORIGINAL
            # 32768 request; no rewritten request is fed to the old handler.
            assert client.timeout == 600 and posted['max_tokens'] == 32768
            assert posted['thinking'] == {'type': 'disabled'} and posted['stream'] is False
            assert 'response_format' not in posted and len(posted['tools']) == 1
            assert posted['tools'][0]['function']['strict'] is True
            assert posted['tools'][0]['function']['name'] == 'submit_report_evidence_reduction'
            payload = json.loads(posted['messages'][-1]['content'])
            owner.requests.append({'stage': 'reduce-v3', 'body': request.content})
            owner.payloads.append(copy.deepcopy(payload))
            semantic = copy.deepcopy(payload)
            semantic['input_claims'] = [{key: value for key, value in claim.items() if key != 'key'}
                for claim in payload['input_claims']]
            value5 = candidate5(semantic)
            business = value5['result']['businesses'][0]
            business['conditions'] = [{'kind': 'precondition', 'statement': '人工前置条件仍待核对',
                'step_indices': [0], 'evidence': {'status': 'inferred',
                'citations': copy.deepcopy(business['citations']),
                'explanation': '仅合成条件保存用例，未把原文推断当作明示。'}}]
            wire = wire6.encode_reduction_wire_v6(value5)
            before = copy.deepcopy(wire)
            assert len(wire['result']['global_claims']) <= 64
            # 96 * 1900 Chinese code points alone occupy >= 547200 UTF-8 bytes,
            # so no guessed header/ID overhead or whitespace padding is needed.
            # Every new item is explicitly UNKNOWN, carries no invented refs,
            # and has its own nonempty explanation. These repeated synthetic
            # statements exercise capacity, not business interpretation quality.
            for index in range(96):
                text = '合成容量核对；只验证完整字段能读取，不代表报告事实。'
                statement = (text * (1900 // len(text) + 1))[:1900]
                assert len(statement) == 1900 and len(statement.encode('utf-8')) == 5700
                wire['result']['global_claims'].append({'kind': '其他', 'statement': statement,
                    'span_refs': [], 'status': 'unknown',
                    'explanation': '第' + str(index + 1) + '条仅为合成容量声明，真实含义未知，不增加来源事实或批准。'})
            assert len(wire['result']['global_claims']) <= 160
            assert wire['reconciliation'] == before['reconciliation']
            assert wire['result']['global_claims'][:len(before['result']['global_claims'])] == before['result']['global_claims']
            assert wire['result']['questions'] == before['result']['questions']
            arguments = encoded(wire)
            assert MAX_REVIEW_BYTES < len(arguments) <= wire6.MAX_WIRE_BYTES
            envelope = {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'synthetic_large_reduce', 'type': 'function', 'function': {
                    'name': 'submit_report_evidence_reduction', 'arguments': arguments.decode('utf-8')}}]}}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
            body = encoded(envelope)
            assert MAX_REVIEW_BYTES < len(body) <= 1024 * 1024
            owner.values.append(wire); owner.responses.append(body)
            return httpx.Response(200, content=body, headers={'x-request-id': 'synthetic-large-reduce'}, request=request)

        monkeypatch.setattr(client_type, 'handle', handle)


def reduce_32768(scene, http, middle):
    body = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=body)
    preview = reply(response); capture(scene, 'v5-large-reduce-preview', response)
    generation = preview['reduction_preparation']['generation_contract']
    assert generation['evidence_stage'] == 'reduce-v3'
    assert generation['dispatch_options']['max_tokens'] == 32768
    assert generation['input_policy']['max_output_tokens'] == 32768
    assert len(http.requests) == http.expected_maps
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    response = scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command)
    accepted = reply(response, 202); capture(scene, 'v5-large-reduce-start', response)
    assert accepted['receipt']['schema'] == 'report-evidence-reduce-receipt-v3'
    assert process_once(accepted['draft']['job_id'])
    return get(scene, middle['id'], 'v5-large-reduced')


def test_v5_legal_large_semantic_candidate_is_readable_but_whole_human_save_is_refused(v5_scene, monkeypatch):
    # A second normal public registration proves the output budget is dynamic;
    # the old fixture's 131072 capability remains untouched and unused.
    manifest = copy.deepcopy(v5_scene['cap']['data'])
    manifest['name'] = 'synthetic_large_reduce_32k_' + uuid4().hex[:10]
    manifest['execution']['report_evidence_output']['max_tokens'] = 32768
    cap = publish(v5_scene['client'], manifest)
    assert cap['status'] == 'published' and cap['validated_hash'] == cap['content_hash']
    assert cap['data']['execution']['report_evidence_output']['max_tokens'] == 32768
    scene = {**v5_scene, 'cap': cap, 'body': {**copy.deepcopy(v5_scene['body']), 'model_id': cap['id']}}
    http = LargeSemanticHTTP(monkeypatch)
    preview, _, _, middle = mapped(scene, http)
    final = reduce_32768(scene, http, middle)
    assert final['schema'] == 'report-evidence-reading-draft-v3' and final['status'] == 'review'
    model = copy.deepcopy(final['model_result'])
    assert model['schema'] == wire6.CANDIDATE and len(encoded(model)) > MAX_REVIEW_BYTES
    assert len(model['global_claims']) == len(middle['maps']) + 96
    assert all(claim['status'] == 'unknown' and claim['span_refs'] == [] and claim['explanation'].strip()
        for claim in model['global_claims'][-96:])
    assert final['model_result_hash'] == digest(model)
    assert final['review_value'] is None and final['review_history'] == []
    assert final['reduction']['journal']['schema'] == 'report-evidence-journal-descriptor-v2'
    assert final['reduction']['receipt']['evidence_profile']['max_output_tokens'] == 32768
    assert all(stage['receipt']['evidence_profile']['max_output_tokens'] == 8192 for stage in final['maps'])
    metadata = final['reduction']['projection_metadata']
    assert metadata['schema'] == adapter.REDUCE_TOOL_PROJECTION and 'result' not in metadata
    assert metadata['arguments_bytes'] == len(encoded(http.values[-1])) <= wire6.MAX_WIRE_BYTES
    assert len(encoded({**metadata, 'result': model,
        'reconciliation': final['reduction']['reconciliation']})) <= adapter.MAX_PROJECTION_BYTES
    assert original(scene, final, 'reduce')['content'].encode('utf-8') == http.responses[-1]
    assert len(http.requests) == http.expected_maps + 1
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id'])
        rebuilt = adapter.stage_projection(pipeline, row, 'reduce', pipeline._catalog(preview))
        assert rebuilt == {**metadata, 'result': model, 'reconciliation': final['reduction']['reconciliation']}

    response = scene['client'].get(BASE + '/' + final['id'])
    assert reply(response) == final
    capture(scene, 'v5-large-semantic', response)  # ORIGINAL serializer bytes, optional root-only 0600 export.

    # Explicit human fixture from this synthetic wire, not automatic adoption.
    # Its schema/local values are legal; the unchanged human byte cap refuses
    # the entire value. No application code is modified to permit its save.
    human = {'schema': 'report-evidence-manual-review-v5',
        'result': wire6.decode_reduction_wire_v6(http.values[-1])['result'], 'note': '仅合成人稿容量核对'}
    assert ManualReviewValueV5.model_validate(human).model_dump() == human
    assert len(encoded(human)) > MAX_REVIEW_BYTES
    with pytest.raises(ValueError, match='capacity exceeded'):
        manual_from_projection_v6(model)
    rows, money, files, requests = all_rows(), fees(call_ids(final)), journal_files(), len(http.requests)
    rejected = scene['client'].put(BASE + '/' + final['id'] + '/review', json={
        'revision': final['revision'], 'request_id': str(uuid4()), 'value': human})
    assert rejected.status_code == 422
    assert rejected.json()['detail']['code'] == 'evidence_review_invalid'
    assert all_rows() == rows and fees(call_ids(final)) == money and journal_files() == files
    assert get(scene, final['id']) == final and len(http.requests) == requests
    assert final['can_apply'] is final['can_publish'] is False
