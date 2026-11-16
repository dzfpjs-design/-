"""Synthetic shared-protocol integration, not HTTP/provider/authority proof.

No actual report, paid reply, DB or environment is read. Fabricated bindings
here explicitly stand in for the authenticated caller boundary; PG tests must
independently exercise real reservations, streamed captures and recovery.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents import cloud_evidence_profiles as profiles
from generic_agents import report_evidence_preparation as maps
from generic_agents import report_evidence_reduction_v3 as reduction
from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_projection_v5 as projection
from generic_agents import report_evidence_qwen_json_schema_v1 as native
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_journal as journal
from generic_agents import report_evidence_pipeline_v5 as adapter
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import build_evidence_source, PLAN_V2


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure shared protocol cannot connect or spawn')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def uid(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def capability():
    return SimpleNamespace(data={'execution': {'provider': 'qwen', 'model': 'qwen3.8-flash',
        'secret_ref': 'QWEN_TOKENPLAN_API_KEY', 'max_tokens': 8192,
        'report_evidence_output': {'schema': 'report-evidence-output-v5', 'max_tokens': 32768,
            'map_transport': native.DELIVERY, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}},
        'data_policy': {'redact_fields': []}})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def scene():
    cap = capability()
    catalog = build_evidence_source('a' * 64, [('w:p1', ' 保留原标点＋+与空白。 '),
        ('w:p2', ''), ('o:p1', '合成同源OCR视图。')])
    prep = maps.build_map_preparation(cap, catalog, {'businesses': [], 'object_names': []},
        '合成任务，不代表报告质量。', generation_version=maps.GENERATION_V7)
    profile = copy.deepcopy(prep['generation_contract']['input_policy']); profile.pop('single_batch')
    inputs = []
    for index, chunk in enumerate(prep['plan']['chunks']):
        wire = {'schema': slots.WIRE_SCHEMA, 'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
        wire['slots']['slot_003'] = {'state': 'present', 'claim': {'kind': '职责',
            'statement': '原文需要人工核对。', 'span_refs': [chunk['span_refs'][0]],
            'status': 'explicit', 'explanation': ''}}
        content = json.dumps(wire, ensure_ascii=False, indent=1)
        body = encoded({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]})
        metadata = native.inspect_response(body, 200, 'map-v7', response_complete=True,
            http_content_encoding=None)['metadata']
        binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': uid(1),
            'workspace_id': uid(2), 'draft_id': uid(3), 'stage': chunk['chunk_id'],
            'reserved_call_id': uid(10 + index), 'source_hash': catalog['descriptor']['source_hash'],
            'catalog_hash': catalog['descriptor']['catalog_hash'], 'generation_hash': 'b' * 64,
            'dispatch_hash': 'c' * 64, 'messages_hash': chunk['messages_hash']}
        receipt = {**metadata, 'call_id': binding['reserved_call_id'], 'evidence_profile': profile}
        _, _, descriptor = bridge._expected(journal, binding, body.decode('utf-8'), receipt)
        kwargs = {'binding': binding, 'expected_binding': copy.deepcopy(binding), 'descriptor': descriptor,
            'metadata': metadata, 'evidence_profile': profile, 'allowed_span_refs': chunk['span_refs']}
        projected = projection.build_map_projection(wire, catalog=catalog, protocol_version=7, **kwargs)
        inputs.append({'wire': wire, **copy.deepcopy(kwargs), 'projection': projected})
    return cap, catalog, prep, inputs


def test_explicit_profile_identity_and_old_defaults_are_independent():
    cap = capability(); execution = cap.data['execution']
    assert profiles.validate_evidence_output(execution) is execution
    assert profiles.declared_map_transport(execution) == native.DELIVERY
    assert profiles.evidence_policy(execution, 'map-v7')['max_output_tokens'] == 8192
    assert profiles.evidence_policy(execution, 'reduce-v5')['max_output_tokens'] == 32768
    assert maps.GENERATION == maps.GENERATION_V2
    for stage in ('map-v3', 'map-v5', 'map-v6'):
        with pytest.raises(ValueError):
            profiles.evidence_policy(execution, stage)
    old = {'provider': 'qwen', 'model': 'qwen-plus', 'max_tokens': 8192}
    assert profiles.declared_map_transport(old) is None
    with pytest.raises(ValueError):
        profiles.evidence_policy(old, 'map-v7')


@pytest.mark.parametrize('mutation', [lambda x: x.pop('endpoint'), lambda x: x.update(extra=True),
    lambda x: x.update(endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'),
    lambda x: x.update(max_tokens=True), lambda x: x.update(reduce_transport='deepseek-strict-report-tool-v3')])
def test_profile_is_five_closed_fields_no_endpoint_or_carrier_fallback(mutation):
    execution = capability().data['execution']; mutation(execution['report_evidence_output'])
    with pytest.raises(ValueError):
        profiles.validate_evidence_output(execution)


def test_plan2_complete_sources_full_post_and_identity_schema_cold_rebuild():
    cap, catalog, prep, _ = scene()
    assert prep['plan']['schema'] == PLAN_V2
    assert [ref for chunk in prep['plan']['chunks'] for ref in chunk['span_refs']] == list(catalog['index'])
    assert maps.check_map_preparation(prep, catalog, cap) == prep
    spec = prep['generation_contract']
    assert spec['schema'] == maps.GENERATION_V7 and spec['evidence_stage'] == 'map-v7'
    assert spec['schema_proof']['payload_origin'] == 'message.content'
    assert spec['schema_proof']['local_schema_hash'] == spec['schema_proof']['provider_schema_hash'] == digest(spec['output_schema'])
    for chunk in prep['plan']['chunks']:
        request = maps.rebuild_map_request(prep, catalog, cap, chunk['chunk_id'])
        assert hashlib.sha256(request['request_body']).hexdigest() == chunk['request_hash']
        assert request['messages_hash'] == digest(request['request']['messages']) == chunk['messages_hash']
        assert request['request_body'] == encoded(request['request'])
        assert request['input_summary']['prepared_request_bytes'] == len(request['request_body'])
        assert request['input_summary']['token_bound'] == len(request['request_body']) + 8192 + 1024
        assert 'tools' not in request['request'] and request['request']['n'] == 1


def test_reduce5_rebuild_uses_every_original_map_and_all_cited_spans():
    cap, catalog, prep, inputs = scene()
    reduced = reduction.build_reduction_preparation(cap, prep, catalog, inputs)
    spec = reduced['generation_contract']
    assert spec['schema'] == 'report-evidence-reduction-generation-v5'
    assert spec['evidence_stage'] == 'reduce-v5' and spec['dispatch_options']['max_tokens'] == 32768
    assert reduced['input_order'] == ['map-1/claims/0', 'map-2/claims/0']
    assert len(reduced['payload']['input_claims']) == 2
    assert reduced['output_capacity']['completion_required'] == 'stop'
    assert reduced['output_capacity']['tool_name'] is None
    assert reduction.check_reduction_preparation(reduced, catalog, cap, prep, inputs) == reduced
    actual = reduction.rebuild_reduction_request(reduced, catalog, cap, prep, inputs)
    assert actual['request_body'] == encoded(actual['request'])
    assert actual['request_hash'] == hashlib.sha256(actual['request_body']).hexdigest()
    assert actual['input_summary']['token_bound'] == len(actual['request_body']) + 32768 + 1024
    assert spec['schema_proof']['local_schema_hash'] == digest(spec['output_schema'])
    for bad in (inputs[:-1], list(reversed(inputs))):
        with pytest.raises(ValueError):
            reduction.build_reduction_preparation(cap, prep, catalog, bad)


def test_protocol7_hash_origin_is_content_and_rejects_tool_metadata_mix():
    _, catalog, _, inputs = scene(); item = inputs[0]
    expected = {key: item[key] for key in ('binding', 'expected_binding', 'descriptor', 'metadata', 'evidence_profile', 'allowed_span_refs')}
    value = projection.check_map_projection(item['projection'], item['wire'], catalog=catalog, protocol_version=7, **expected)
    assert value['slot_positions'] == [2]
    assert value['tool_metadata']['finish_reason'] == 'stop'
    assert value['tool_metadata']['tool_calls_hash'] is None
    assert value['arguments_sha256'] != value['http_body_sha256']
    for version in (5, 6):
        with pytest.raises(ValueError):
            projection.build_map_projection(item['wire'], catalog=catalog, protocol_version=version, **expected)
    bad = copy.deepcopy(expected); bad['metadata']['tool_call_count'] = 1
    with pytest.raises(ValueError):
        projection.build_map_projection(item['wire'], catalog=catalog, protocol_version=7, **bad)


def test_native_frozen_evidence_change_is_rejected_not_capacity_split(monkeypatch):
    cap, catalog, prep, _ = scene(); original = maps.prepare_cloud_json
    def changed(*args, **kwargs):
        messages, size, images = original(*args, **kwargs)
        value = json.loads(messages[-1]['content']); value['context'] = {'changed': True}
        messages[-1]['content'] = json.dumps(value)
        return messages, size, images
    monkeypatch.setattr(maps, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError, match='网关改变'):
        maps.rebuild_map_request(prep, catalog, cap, prep['plan']['chunks'][0]['chunk_id'])


def test_cold_adapter_requires_capture_ready_safe_flags_status_and_exact_receipt(monkeypatch):
    cap, _, prep, inputs = scene(); item = inputs[0]
    content = json.dumps(item['wire'], ensure_ascii=False, indent=1)
    body = encoded({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]})
    receipt = {**item['metadata'], 'usage': {}, 'original_usage_shape': 'missing',
        'usage_unavailable': True, 'usage_unavailable_reason': 'missing', 'provider': 'qwen',
        'model': 'qwen3.8-flash', 'request_id': None, 'call_id': item['binding']['reserved_call_id'],
        'evidence_profile': item['evidence_profile'], 'response_complete': True,
        'http_content_encoding': None, 'capture_hash': 'f' * 64}
    _, _, descriptor = bridge._expected(journal, item['binding'], body.decode(), receipt)
    loaded = {'content': body.decode(), 'receipt': receipt, 'descriptor': descriptor}
    row = SimpleNamespace(data={'preview': {'map_preparation': prep}})
    pipeline = SimpleNamespace(_journal_for=lambda *_: journal,
        _stage=lambda *_: {'reserved_call_id': item['binding']['reserved_call_id']},
        journal_binding=lambda *_: item['binding'], _spec=lambda *_: prep['generation_contract'],
        _check_receipt_identity=lambda *_: None)
    state = {'ready': True, 'content_encoding_safe': True, 'response_complete': True,
        'body_sha256': hashlib.sha256(body).hexdigest(), 'body_bytes': len(body),
        'http_status': 200, 'request_id': None, 'http_content_encoding': None}
    monkeypatch.setattr(bridge, 'load_verified', lambda *_: state)
    actual, inspected = adapter.inspect_response(pipeline, row, 'map-1', loaded, descriptor)
    assert actual == body and inspected['response_complete'] is True
    for field, value in (('ready', False), ('content_encoding_safe', False), ('response_complete', False),
            ('http_status', 400), ('body_sha256', '0' * 64)):
        previous = state[field]; state[field] = value
        with pytest.raises(ValueError):
            adapter.inspect_response(pipeline, row, 'map-1', loaded, descriptor)
        state[field] = previous
    for field in ('response_complete', 'http_content_encoding', 'capture_hash'):
        bad = copy.deepcopy(loaded); del bad['receipt'][field]
        with pytest.raises(ValueError):
            adapter.inspect_response(pipeline, row, 'map-1', bad, descriptor)
