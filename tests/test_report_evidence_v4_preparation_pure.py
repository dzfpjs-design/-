"""New opt-in delivery/projection only; root runs these synthetic tests.

No provider, journal durability, published authority or model quality is
inferred from these pure fixtures. Existing V1/V2/V3 tests remain unchanged.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_pipeline_v4 as adapter
from generic_agents import report_evidence_strict_map as strict
from generic_agents.cloud_evidence_profiles import (evidence_policy, evidence_options,
    validate_evidence_output, declared_map_transport)
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options, prepare_cloud_json
from generic_agents.report_evidence_plan import (PLAN, PLAN_V2, build_evidence_source,
    plan_evidence_reads, check_plan, EvidenceCapacityExceeded)
from tests.test_report_evidence_v3_preparation_pure import capability, reordered, uuid


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure V4 preparation must not execute IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap = capability(maximum=131072)
    cap.data['execution']['report_evidence_output'] = {'schema': 'report-evidence-output-v2',
        'map_transport': strict.DELIVERY, 'max_tokens': 131072}
    catalog = build_evidence_source('a' * 64, [['w:p1', ' 原字＋+，空白不改。 '], ['w:p2', ''], ['o:p1', '合成OCR。']])
    prep = preparation.build_map_preparation(cap, catalog, {'object_names': []},
        generation_version=preparation.GENERATION_V4)
    return cap, catalog, prep


def response_case(*, usage=True):
    cap, catalog, prep = scene(); chunk = prep['plan']['chunks'][0]
    wire = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '职责', 'statement': '原字＋+，空白不改。',
        'span_refs': [chunk['span_refs'][0]], 'status': 'explicit', 'explanation': ''}]}
    args = json.dumps(wire, ensure_ascii=False, indent=2)
    envelope = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 'original_tool_id', 'type': 'function', 'function': {'name': strict.TOOL_NAME, 'arguments': args}}]}}]}
    if usage: envelope['usage'] = {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}
    body = json.dumps(envelope, ensure_ascii=True, indent=2).encode()
    metadata = strict.inspect_strict_map_response(body, 200)['metadata']
    binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': uuid(1), 'workspace_id': uuid(2),
        'draft_id': uuid(3), 'stage': 'map-1', 'reserved_call_id': uuid(4), 'source_hash': 'a' * 64,
        'catalog_hash': catalog['descriptor']['catalog_hash'], 'generation_hash': 'b' * 64,
        'dispatch_hash': 'c' * 64, 'messages_hash': chunk['messages_hash']}
    descriptor = {'binding': binding, 'binding_hash': digest(binding),
        'wire_sha256': hashlib.sha256(body.decode().encode('utf-8', errors='surrogatepass')).hexdigest()}
    policy = copy.deepcopy(prep['generation_contract']['input_policy']); policy.pop('single_batch')
    loaded = {'content': body.decode(), 'receipt': {**metadata, 'evidence_profile': policy}}
    fake = NS(_spec=lambda row, key: prep['generation_contract'])
    return catalog, chunk, wire, args, body, metadata, binding, descriptor, loaded, fake


def test_v4_manifest_is_explicit_deepseek_only_without_widening_generic_or_legacy():
    cap, _, prep = scene(); execution = cap.data['execution']
    assert validate_evidence_output(execution) is execution
    assert declared_map_transport(execution) == strict.DELIVERY
    assert evidence_policy(execution, 'map-v4')['max_output_tokens'] == 8192
    assert cloud_json_options(execution)['max_tokens'] == 8192
    assert evidence_options(execution, cloud_json_options(execution), 'map-v4') == {
        'model': 'deepseek-v4-pro', 'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'stream': False}
    assert preparation.GENERATION == preparation.GENERATION_V2
    assert pipeline.CURRENT_MAP_GENERATION == preparation.GENERATION_V3
    for stage in ('map-v3', 'reduce-v2'):
        with pytest.raises(ValueError): evidence_policy(execution, stage)
    old = capability(maximum=131072).data['execution']
    assert declared_map_transport(old) is None
    assert evidence_policy(old, 'reduce-v2')['max_output_tokens'] == 131072
    with pytest.raises(ValueError): evidence_policy(old, 'map-v4')
    assert prep['plan']['schema'] == PLAN_V2


@pytest.mark.parametrize('change', ['qwen', 'thinking', 'generic', 'extra', 'transport', 'bool', 'unknown'])
def test_invalid_optin_is_not_a_fallback_or_capacity_split(change):
    cap, catalog, _ = scene(); e = cap.data['execution']
    if change == 'qwen': e.update(provider='qwen', model='qwen-plus'); e.pop('thinking_mode')
    elif change == 'thinking': e['thinking_mode'] = 'enabled'
    elif change == 'generic': e['max_tokens'] = 131072
    elif change == 'extra': e['report_evidence_output']['extra'] = True
    elif change == 'transport': e['report_evidence_output']['map_transport'] = 'other'
    elif change == 'bool': e['report_evidence_output']['max_tokens'] = True
    else: e['report_evidence_output']['schema'] = 'future'
    with pytest.raises(ValueError):
        preparation.build_map_preparation(cap, catalog, {}, generation_version=preparation.GENERATION_V4)


def test_whole_request_tools_options_and_original_messages_are_separate_frozen_hashes():
    cap, catalog, prep = scene(); before = copy.deepcopy([cap.data, catalog, prep])
    assert preparation.check_map_preparation(reordered(prep), reordered(catalog), cap) == prep
    spec = prep['generation_contract']
    assert spec['schema'] == preparation.GENERATION_V4 and spec['evidence_stage'] == 'map-v4'
    assert spec['projection_schema'] == adapter.MAP_TOOL_PROJECTION
    assert spec['schema_proof']['local_schema_hash'] == digest(spec['output_schema'])
    for chunk in prep['plan']['chunks']:
        actual = preparation.rebuild_map_request(reordered(prep), catalog, cap, chunk['chunk_id'])
        requested = actual['messages'][1:]
        redacted, _, images = prepare_cloud_json(cap, requested, spec['output_schema']); assert images == 0
        expected = strict.prepare_strict_map_request(redacted, spec['output_schema'],
            evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), 'map-v4'))
        assert actual['messages_hash'] == digest(expected['request']['messages']) == chunk['messages_hash']
        assert actual['request_hash'] == hashlib.sha256(expected['body']).hexdigest() == chunk['request_hash']
        assert actual['request_hash'] != actual['messages_hash']
        assert actual['request_body'] == expected['body']
        assert actual['input_summary'] == measure_request_body(expected['body'], spec['input_policy'], 8192)
        assert actual['input_summary']['prepared_request_bytes'] > len(json.dumps(actual['messages'], ensure_ascii=False).encode())
        assert 'prepared_messages_bytes' not in actual['input_summary']
        assert json.loads(actual['messages'][1]['content'])['schema'] == 'report-evidence-map-task-v4'
    assert prep['input_summary']['maximum_total_calls'] == len(prep['plan']['chunks'])
    assert [cap.data, catalog, prep] == before


@pytest.mark.parametrize('change', ['request_hash', 'messages_hash', 'proof', 'mixed_plan', 'mixed_generation'])
def test_rehashed_frozen_request_or_protocol_changes_are_rejected(change):
    cap, catalog, prep = scene()
    if change in {'request_hash', 'messages_hash'}:
        chunk = prep['plan']['chunks'][0]; chunk[change] = 'f' * 64
        chunk['chunk_hash'] = digest({k: v for k, v in chunk.items() if k != 'chunk_hash'})
        prep['plan']['plan_hash'] = digest({k: v for k, v in prep['plan'].items() if k != 'plan_hash'})
    elif change == 'proof': prep['generation_contract']['schema_proof']['provider_schema_hash'] = 'f' * 64
    elif change == 'mixed_plan': prep['plan']['schema'] = PLAN
    else: prep['generation_contract']['schema'] = preparation.GENERATION_V3
    prep['preparation_hash'] = digest({k: v for k, v in prep.items() if k != 'preparation_hash'})
    with pytest.raises(ValueError): preparation.check_map_preparation(prep, catalog, cap)


def test_gateway_changed_source_is_not_silently_adopted_or_replanned(monkeypatch):
    cap, catalog, _ = scene(); real = preparation.prepare_cloud_json; calls = []
    def changed(*args):
        messages, count, images = real(*args); calls.append(messages)
        value = json.loads(messages[-1]['content']); value['spans'][0][1] = 'changed valid JSON source'
        messages[-1]['content'] = json.dumps(value, ensure_ascii=False)
        return messages, count, images
    monkeypatch.setattr(preparation, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError, match='网关改变'):
        preparation.build_map_preparation(cap, catalog, {}, generation_version=preparation.GENERATION_V4)
    assert len(calls) == 1


def test_plan_v2_is_explicit_and_keeps_callback_capacity_errors_distinct():
    catalog = build_evidence_source('a' * 64, [['w:p1', '甲'], ['w:p2', '乙']])
    def current(payload):
        return {'messages_hash': digest(payload), 'request_hash': digest({'tools': True, 'payload': payload}),
            'input_summary': {'prepared_request_bytes': 20, 'token_bound': 100, 'max_input_bytes': 900000}}
    plan = plan_evidence_reads(catalog, current, 100, plan_version=PLAN_V2)
    assert check_plan(plan, catalog, current, plan_version=PLAN_V2) == plan
    with pytest.raises(ValueError): plan_evidence_reads(catalog, current, 100)  # no field-presence inference
    with pytest.raises(ValueError): check_plan(plan, catalog, current)
    count = []
    def invalid(payload): count.append(payload); raise ValueError('configuration_error')
    with pytest.raises(ValueError, match='configuration_error'):
        plan_evidence_reads(catalog, invalid, 100, plan_version=PLAN_V2)
    assert len(count) == 1
    def too_large(payload): raise EvidenceCapacityExceeded('explicit_capacity')
    with pytest.raises(EvidenceCapacityExceeded):
        plan_evidence_reads(catalog, too_large, 1, plan_version=PLAN_V2)


@pytest.mark.parametrize('usage', [True, False])
def test_http_arguments_and_journal_identities_are_separate_and_cold_rebuild_exact(usage):
    catalog, chunk, wire, args, body, metadata, binding, descriptor, loaded, fake = response_case(usage=usage)
    parsed = adapter.parsed_response(fake, None, 'map-1', loaded, descriptor)
    assert parsed['value'] == wire
    value = adapter.build_projection(wire, binding=binding, expected_binding=copy.deepcopy(binding),
        descriptor=descriptor, metadata=metadata, evidence_profile=loaded['receipt']['evidence_profile'],
        catalog=catalog, allowed_span_refs=chunk['span_refs'])
    assert value['http_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert value['arguments_sha256'] == hashlib.sha256(args.encode()).hexdigest() != value['http_body_sha256']
    assert value['arguments_projection']['wire_sha256'] == value['arguments_sha256']
    assert value['claims'] == value['arguments_projection']['claims']
    assert value['claims'][0]['key'].startswith('mc_')
    assert value['projection_hash'] == digest({k: v for k, v in value.items() if k != 'projection_hash'})
    assert adapter.build_projection(copy.deepcopy(wire), binding=reordered(binding), expected_binding=binding,
        descriptor=reordered(descriptor), metadata=reordered(metadata), evidence_profile=loaded['receipt']['evidence_profile'], catalog=reordered(catalog),
        allowed_span_refs=chunk['span_refs']) == value
    if not usage:
        assert metadata['usage_available'] is False  # never an adoption veto


@pytest.mark.parametrize('change', ['receipt_http_sha', 'receipt_args_sha', 'profile', 'scope', 'outside_batch', 'bad_kind'])
def test_original_metadata_and_local_wire_constraints_cannot_be_repaired(change):
    catalog, chunk, wire, args, body, metadata, binding, descriptor, loaded, fake = response_case()
    expected = copy.deepcopy(binding); refs = chunk['span_refs']
    if change.startswith('receipt_'):
        field = 'response_body_sha256' if change.endswith('http_sha') else 'arguments_sha256'
        loaded['receipt'][field] = 'f' * 64
    elif change == 'profile': loaded['receipt']['evidence_profile']['max_output_tokens'] = 128
    elif change == 'scope': expected['draft_id'] = uuid(99)
    elif change == 'outside_batch': refs = []
    else: wire['claims'][0]['kind'] = '近义分类'
    with pytest.raises(ValueError):
        adapter.parsed_response(fake, None, 'map-1', loaded, descriptor)
        adapter.build_projection(wire, binding=binding, expected_binding=expected,
            descriptor=descriptor, metadata=metadata, evidence_profile=loaded['receipt']['evidence_profile'], catalog=catalog, allowed_span_refs=refs)


def test_v4_fresh_gate_and_explicit_reduce_block_do_not_change_legacy_defaults():
    cap, _, prep = scene(); preview = {'preparation_cap': cap.data, 'map_preparation': prep}
    pipeline._require_current_map(preview)
    with pytest.raises(HTTPException) as rejected: pipeline._block_v4_reduce(preview)
    assert rejected.value.status_code == 409 and rejected.value.detail['code'] == 'evidence_reduce_unsupported'
    assert preparation.GENERATION == preparation.GENERATION_V2
    assert hashlib.sha256(preparation.EVIDENCE_MAP_TASK_V1.encode()).hexdigest() == '573772f882562bc644ad129944878273b7963c7d69a04b09e9ab09f39d1e2189'
    unknown = copy.deepcopy(preview); unknown['map_preparation']['generation_contract']['schema'] = 'future'
    with pytest.raises(HTTPException): pipeline._require_current_map(unknown)
