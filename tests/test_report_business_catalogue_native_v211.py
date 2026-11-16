"""Public synthetic native/preparation IO; not paid/source/IAM/runtime proof.

All original HTTP bodies below are authored synthetic fixtures. Tests must not
connect, dispatch, read credentials or turn them into real capture evidence.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_business_catalogue_native_v211 as native
from generic_agents import report_business_catalogue_preparation_v211 as preparation
from generic_agents import report_business_catalogue_wire_v211 as wire
from generic_agents import report_design_grouped_native_v179 as legacy_native
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents import report_saved_map_design_preparation_v176 as original_preparation
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded
from generic_agents.report_evidence_plan import build_evidence_source
from generic_agents.report_evidence_strict_map import METADATA_KEYS
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_catalogue_v211 import scene as wire_catalogue_scene
from tests.test_report_business_catalogue_v211 import single as complete_claim_scene
from tests.test_report_saved_map_design_preparation_v176 import source_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure catalogue preparation/native must not connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, source = source_scene()
    cap.data['execution']['report_evidence_output'] = {
        'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
        'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source


def catalogue_scene():
    # The wire codec deliberately accepts opaque extensions; a real saved Map
    # uses the stricter five-field V3 claim, as do the native request validators.
    candidate, bound = wire_catalogue_scene()
    for claim in bound['input_claims']:
        claim.pop('raw_extension')
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], bound['source_snapshot_hash'])
    return candidate, bound


def arguments(bound):
    return {key: bound[key] for key in ('indexed', 'input_claims', 'source_snapshot_hash')}


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'report_evidence_output': {'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def options():
    return native.dispatch_options(execution(), {'model': native.MODEL, 'max_tokens': 8192,
        'response_format': {'type': 'json_object'}})


def payload(bound):
    value = {key: 'b' * 64 for key in native._PAYLOAD_HASHES}
    value.update(schema=native.REQUEST_PAYLOAD_SCHEMA, source_snapshot_hash=bound['source_snapshot_hash'],
        source_hash=bound['catalog']['descriptor']['source_hash'],
        catalog_hash=bound['catalog']['descriptor']['catalog_hash'],
        input_claims_hash=bound['indexed']['input_claims_hash'], context={},
        user_instruction='仅公开合成目录建议，不授予业务或额外模型调用权限。', catalogue_note='',
        spans=[[ref, bound['catalog']['index'][ref]['text']]
            for ref in dict.fromkeys(ref for claim in bound['input_claims'] for ref in claim['span_refs'])],
        indexed_inputs=codec.model_payload(**arguments(bound)))
    return value


def messages(bound, schema):
    return [{'role': 'system', 'content': native.envelope._OLD_PREFIX + json.dumps(schema, ensure_ascii=False)},
        {'role': 'system', 'content': '{"task":"synthetic catalogue candidate only"}'},
        {'role': 'user', 'content': ' ' + json.dumps(payload(bound), ensure_ascii=False) + ' '}]


def envelope(candidate):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': json.dumps(candidate, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}}


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def reseal(value):
    value['payload_hash'] = digest(value['payload'])
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['preparation_hash'] = digest({key: item for key, item in value.items()
        if key != 'preparation_hash'})


def test_new_catalogue_registration_and_stage_are_independent_of_old_designs():
    value = execution(); before = copy.deepcopy(value)
    policy = native.input_policy(value)
    assert native.STAGE == 'catalogue-v211' and native.MODEL == 'qwen3.8-max-0902'
    assert policy['max_input_bytes'] == 900000 and policy['max_output_tokens'] == 32768
    assert native.DELIVERY != legacy_native.DELIVERY
    assert native.OUTPUT_DECLARATION != legacy_native.OUTPUT_DECLARATION
    assert value == before
    with pytest.raises(ValueError): native.stage_policy(legacy_native.STAGE)
    with pytest.raises(ValueError): legacy_native.input_policy(value)


@pytest.mark.parametrize('fault', ['old_declaration', 'old_transport', 'other_provider', 'other_model',
    'thinking', 'embedding', 'extra_declaration', 'bool_output', 'float_output', 'base_overcap'])
def test_old_or_weakened_registration_does_not_enable_catalogue(fault):
    value = execution(); declaration = value['report_evidence_output']
    if fault == 'old_declaration': declaration['schema'] = legacy_native.OUTPUT_DECLARATION
    elif fault == 'old_transport': declaration['reduce_transport'] = legacy_native.DELIVERY
    elif fault == 'other_provider': value['provider'] = 'deepseek'
    elif fault == 'other_model': value['model'] = 'qwen3.8-flash'
    elif fault == 'thinking': value['thinking_mode'] = False
    elif fault == 'embedding': value['task'] = 'embedding'
    elif fault == 'extra_declaration': declaration['map_transport'] = native.DELIVERY
    elif fault == 'bool_output': declaration['max_tokens'] = True
    elif fault == 'float_output': declaration['max_tokens'] = 32768.0
    else: value['max_tokens'] = 32768
    with pytest.raises(ValueError): native.input_policy(value)


def test_dynamic_schema_and_source_proof_do_not_confuse_count_with_source_identity():
    _, bound = catalogue_scene(); args = arguments(bound); schema = wire.output_schema(**args)
    proof = native.identity_schema_proof(schema, native.STAGE, **args)
    shape = native.schema_shape_proof(schema, native.STAGE)
    assert shape['source_binding_verified'] is False
    assert 'source_snapshot_hash' not in shape and 'indexed_inputs_hash' not in shape
    assert proof['schema'] == native.PROOF_SCHEMA and proof['input_count'] == len(bound['input_claims'])
    assert native.check_schema_proof(proof, schema, native.STAGE, **args) == proof
    assert proof['schema_policy']['provider_support_verified'] is False
    assert proof['schema_policy']['removed_assertions']
    assert schema['properties']['allocation_groups']['items']['properties']['input_positions']['uniqueItems'] is True
    provider_positions = proof['provider_schema']['properties']['allocation_groups']['items']['properties']['input_positions']
    assert 'uniqueItems' not in provider_positions
    assert provider_positions['items']['maximum'] == len(bound['input_claims']) - 1
    other = copy.deepcopy(args); other['input_claims'][0]['statement'] += '新的原声明'
    other['indexed'] = codec.build_indexed_inputs(other['input_claims'], other['source_snapshot_hash'])
    assert wire.output_schema(**other) == schema
    with pytest.raises(ValueError): native.check_schema_proof(proof, schema, native.STAGE, **other)
    with pytest.raises(ValueError): native.check_schema_proof(shape, schema, native.STAGE, **args)
    changed = copy.deepcopy(schema); changed['properties']['input_count']['const'] = True
    with pytest.raises(ValueError): native.schema_shape_proof(changed, native.STAGE)


def test_exact_request_keeps_catalogue_server_only_and_every_original_index_and_span():
    _, bound = catalogue_scene(); args = arguments(bound); schema = wire.output_schema(**args)
    prepared = messages(bound, schema); before = copy.deepcopy(prepared)
    assert native.request_inputs(prepared, evidence_catalogue=bound['catalog']) == args
    result = native.prepare_request(prepared, schema, options(), native.STAGE,
        evidence_catalogue=bound['catalog'], **args)
    assert result['request']['messages'][1:] == before[1:]
    assert result['request']['messages'][0]['content'] == native.system_prefix(native.STAGE)
    assert result['body'] == canonical_json(result['request']).encode('utf-8')
    assert result['body_sha256'] == hashlib.sha256(result['body']).hexdigest()
    assert result['body_bytes'] == len(result['body']) <= native.MAX_INPUT_BYTES
    sent = json.loads(result['request']['messages'][-1]['content'])
    assert 'catalog' not in sent and 'evidence_catalogue' not in result['request']
    assert sent['catalog_hash'] == bound['catalog']['descriptor']['catalog_hash']
    assert sent['indexed_inputs'] == payload(bound)['indexed_inputs']
    assert sent['spans'] == payload(bound)['spans']
    assert prepared == before


@pytest.mark.parametrize('fault', ['missing', 'corrupt_catalogue', 'different_source'])
def test_native_requires_complete_server_catalogue_matching_actual_payload_before_request(fault):
    _, bound = catalogue_scene(); args = arguments(bound); schema = wire.output_schema(**args)
    server = copy.deepcopy(bound['catalog'])
    if fault == 'missing': server = None
    elif fault == 'corrupt_catalogue': server['index']['w:p1/s1']['text'] += '改变原文'
    else:
        server = build_evidence_source('f' * 64, [[ref, bound['catalog']['index'][ref + '/s1']['text']]
            for ref in bound['catalog']['descriptor']['ordered_source_refs']])
    prepared = messages(bound, schema); before = copy.deepcopy(prepared)
    with pytest.raises(ValueError): native.prepare_request(prepared, schema, options(), native.STAGE,
        evidence_catalogue=server, **args)
    assert prepared == before


def test_920_claims_and_all_their_spans_fit_without_sending_large_unreferenced_original_catalogue():
    _, bound = complete_claim_scene(920)
    for claim in bound['input_claims']:
        claim.pop('raw_extension')
    blocks = [[ref, bound['catalog']['index'][ref + '/s1']['text']]
        for ref in bound['catalog']['descriptor']['ordered_source_refs']]
    private_text = 'SERVER_ONLY_UNREFERENCED_ORIGINAL_BLOCK_' * 23000
    blocks.append(['w:p921', private_text])
    bound['catalog'] = build_evidence_source('b' * 64, blocks)
    bound['source_snapshot_hash'] = codec.digest({'original_catalogue': bound['catalog']['descriptor']})
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], bound['source_snapshot_hash'])
    assert len(canonical_json(bound['catalog']).encode('utf-8')) > native.MAX_INPUT_BYTES
    args = arguments(bound); schema = wire.output_schema(**args)
    result = native.prepare_request(messages(bound, schema), schema, options(), native.STAGE,
        evidence_catalogue=bound['catalog'], **args)
    assert len(result['body']) < native.MAX_INPUT_BYTES == 900000
    sent = json.loads(result['request']['messages'][-1]['content'])
    assert sent['indexed_inputs'] == codec.model_payload(**args)
    assert len(sent['indexed_inputs']['input_claims']) == 920
    assert sent['spans'] == payload(bound)['spans'] and len(sent['spans']) == 920
    assert 'catalog' not in sent and 'evidence_catalogue' not in result['request']
    assert b'SERVER_ONLY_UNREFERENCED_ORIGINAL_BLOCK_' not in result['body']
    assert sent['catalog_hash'] == bound['catalog']['descriptor']['catalog_hash']


@pytest.mark.parametrize('fault', ['old_payload', 'extra', 'catalog_in_payload', 'catalog_hash',
    'wrong_ref', 'position_bool', 'position_order', 'claim_changed', 'claims_hash', 'duplicate_json',
    'missing_span', 'changed_span', 'expanded_span_records'])
def test_actual_request_binding_rejects_missing_changed_or_borrowed_numbered_source(fault):
    _, bound = catalogue_scene(); args = arguments(bound); schema = wire.output_schema(**args)
    prepared = messages(bound, schema); value = payload(bound)
    if fault == 'old_payload': value['schema'] = legacy_native.REQUEST_PAYLOAD_SCHEMA
    elif fault == 'extra': value['design_note'] = '旧设计目的'
    elif fault == 'catalog_in_payload': value['catalog'] = copy.deepcopy(bound['catalog'])
    elif fault == 'catalog_hash': value['catalog_hash'] = 'f' * 64
    elif fault == 'wrong_ref': value['indexed_inputs']['input_claims'][0]['claim']['span_refs'] = ['w:p999/s1']
    elif fault == 'position_bool': value['indexed_inputs']['input_claims'][0]['input_position'] = False
    elif fault == 'position_order': value['indexed_inputs']['input_claims'].reverse()
    elif fault == 'claim_changed': value['indexed_inputs']['input_claims'][-1]['claim']['statement'] += '改变尾项'
    elif fault == 'claims_hash': value['input_claims_hash'] = 'f' * 64
    elif fault == 'missing_span': value['spans'].pop()
    elif fault == 'changed_span': value['spans'][0][1] += '替换原文'
    elif fault == 'expanded_span_records':
        value['spans'] = [{'span_ref': row[0], **copy.deepcopy(bound['catalog']['index'][row[0]])}
            for row in value['spans']]
    prepared[-1]['content'] = json.dumps(value, ensure_ascii=False)
    if fault == 'duplicate_json': prepared[-1]['content'] = '{"catalog":{},"catalog":{}}'
    before = copy.deepcopy(prepared)
    with pytest.raises(ValueError): native.prepare_request(prepared, schema, options(), native.STAGE,
        evidence_catalogue=bound['catalog'], **args)
    assert prepared == before


def test_full_native_content_becomes_only_unconfirmed_catalogue_projection_and_keeps_raw_metadata():
    candidate, bound = catalogue_scene(); body = raw(envelope(candidate)); before = body[:]
    inspected = native.inspect_response(body, 200, native.STAGE,
        response_complete=True, http_content_encoding=None)
    assert set(inspected['metadata']) == set(METADATA_KEYS)
    assert inspected['metadata']['delivery'] == native.DELIVERY
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert inspected['usage'] == {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}
    projection = native.parse_arguments(body, 200, native.STAGE,
        response_complete=True, http_content_encoding=None, **bound)
    assert projection['schema'] == wire.PROJECTION and projection['wire'] == candidate
    assert len(projection['assignments']) == len(bound['input_claims'])
    assert projection['pending_positions'] == [6] and projection['scope_confirmation_blocked'] is True
    assert len(projection['businesses']) == 2 and len(projection['merge_suggestions']) == 1
    assert 'result' not in projection and 'manual_value' not in projection
    for key in ('semantic_quality_verified', 'human_confirmed', 'can_confirm', 'can_apply',
            'can_publish', 'dispatch_allowed', 'report_coverage_verified'):
        assert projection[key] is False
    assert body == before


@pytest.mark.parametrize('fault', ['length', 'incomplete', 'encoding', 'provider_error', 'tools',
    'missing_tail', 'repeat_position', 'old_v166', 'duplicate_json'])
def test_native_rejects_incomplete_transport_and_semantically_invalid_whole_content_without_repair(fault):
    candidate, bound = catalogue_scene(); value = envelope(candidate)
    complete, encoding = True, None
    if fault == 'length': value['choices'][0]['finish_reason'] = 'length'
    elif fault == 'incomplete': complete = False
    elif fault == 'encoding': encoding = 'gzip'
    elif fault == 'provider_error': value['error'] = {'message': 'synthetic provider rejected schema'}
    elif fault == 'tools': value['choices'][0]['message']['tool_calls'] = [{'function': {'arguments': '{}'}}]
    elif fault == 'missing_tail':
        candidate['allocation_groups'][0]['input_positions'].remove(7)
        value = envelope(candidate)
    elif fault == 'repeat_position':
        candidate['allocation_groups'][1]['input_positions'].append(7)
        value = envelope(candidate)
    elif fault == 'old_v166': value['choices'][0]['message']['content'] = '{"result":{"schema":"report-object-semantic-candidate-wire-v166"}}'
    else: value['choices'][0]['message']['content'] = '{"input_count":8,"input_count":8}'
    body = raw(value); before = body[:]
    with pytest.raises(ValueError): native.parse_arguments(body, 200, native.STAGE,
        response_complete=complete, http_content_encoding=encoding, **bound)
    assert body == before


def test_preparation_preserves_real_supplied_source_and_one_complete_measured_request():
    cap, source = scene(); before = canonical_json([cap.data, source]); note = ' 原目录待人工核对😀\n不授权执行。 '
    _, _, original_spans = original_preparation._source(source)
    assert all(type(row) is list and len(row) == 2 for row in original_spans)
    assert original_spans == [[ref, source['catalog']['index'][ref]['text']]
        for ref in source['allowed_span_refs']]
    value = preparation.build_preparation(cap, source, note=note)
    rebuilt = preparation.rebuild_request(value, cap, source, note=note)
    original = source['snapshot']; claims = [original['input_items'][key] for key in original['input_order']]
    args = {'indexed': value['indexed_inputs'], 'input_claims': claims,
        'source_snapshot_hash': original['snapshot_hash']}
    assert value['schema'] == preparation.PREPARATION
    assert value['source_catalog'] == source['catalog'] and 'catalog' not in value['payload']
    assert value['payload']['spans'] == original['spans'] == original_spans
    assert value['payload']['context'] == original['context']
    assert value['payload']['user_instruction'] == original['user_instruction']
    assert value['payload']['catalogue_note'] == note
    assert value['payload']['indexed_inputs'] == codec.model_payload(**args)
    assert [row['claim'] for row in value['payload']['indexed_inputs']['input_claims']] == claims
    assert value['input_order'] == original['input_order']
    assert rebuilt['request_body'] == canonical_json(rebuilt['request']).encode('utf-8')
    assert hashlib.sha256(rebuilt['request_body']).hexdigest() == value['request_hash']
    assert value['input_summary']['prepared_request_bytes'] == len(rebuilt['request_body'])
    spec = preparation.stage_spec(value)
    assert spec['wire_result_schema'] == wire.WIRE and spec['projection_schema'] == wire.PROJECTION
    assert spec['evidence_stage'] == native.STAGE and spec['schema_proof']['input_count'] == len(claims)
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    for key in ('semantic_quality_verified', 'consumer_authorized', 'can_apply', 'can_publish'):
        assert value[key] is False
    assert value['output_capacity']['fit_guaranteed'] is False
    assert canonical_json([cap.data, source]) == before


@pytest.mark.parametrize('fault', ['claim_tail', 'catalog', 'schema', 'request_hash', 'authority', 'purpose'])
def test_resealed_preparation_cannot_replace_cold_source_or_borrow_another_purpose(fault):
    cap, source = scene(); value = preparation.build_preparation(cap, source)
    if fault == 'claim_tail': value['payload']['indexed_inputs']['input_claims'][-1]['claim']['statement'] += 'changed'
    elif fault == 'catalog': value['source_catalog']['descriptor']['catalog_hash'] = 'f' * 64
    elif fault == 'schema': value['generation_contract']['output_schema']['properties']['input_count']['const'] += 1
    elif fault == 'request_hash': value['request_hash'] = 'f' * 64
    elif fault == 'authority': value['can_apply'] = True
    else: value['generation_contract']['task'] = '输出旧完整V166方案'
    reseal(value); before = canonical_json(value)
    with pytest.raises(ValueError): preparation.check_preparation(value, cap, source)
    assert canonical_json(value) == before


def test_new_note_changes_quote_without_modifying_old_exact_body_or_reusing_old_declaration():
    cap, source = scene(); value = preparation.build_preparation(cap, source, note='原目录说明')
    before = preparation.rebuild_request(value, cap, source, note='原目录说明')['request_body']
    with pytest.raises(ValueError): preparation.check_preparation(value, cap, source, note='新目录说明')
    changed = preparation.build_preparation(cap, source, note='新目录说明')
    assert changed['request_hash'] != value['request_hash']
    assert preparation.rebuild_request(value, cap, source, note='原目录说明')['request_body'] == before
    cap.data['execution']['report_evidence_output']['schema'] = legacy_native.OUTPUT_DECLARATION
    with pytest.raises(ValueError): preparation.build_preparation(cap, source)


def test_complete_body_capacity_is_fail_closed_not_truncated(monkeypatch):
    cap, source = scene(); value = preparation.build_preparation(cap, source)
    count = value['input_summary']['prepared_request_bytes']; before = canonical_json(source)
    monkeypatch.setattr(preparation, 'MAX_REQUEST_BYTES', count)
    assert preparation.build_preparation(cap, source)['request_hash'] == value['request_hash']
    monkeypatch.setattr(preparation, 'MAX_REQUEST_BYTES', count - 1)
    with pytest.raises(EvidenceCapacityExceeded): preparation.build_preparation(cap, source)
    assert canonical_json(source) == before


@pytest.mark.parametrize('note', [None, True, 'x' * 4001, '\x00', '\ud800'])
def test_invalid_notes_do_not_get_trimmed_or_substituted(note):
    cap, source = scene()
    with pytest.raises((ValueError, UnicodeError)): preparation.build_preparation(cap, source, note=note)
