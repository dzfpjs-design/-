"""New synthetic native envelopes only; no real source, provider or paid call."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_design_grouped_native_v178 as subject
from generic_agents import report_design_grouped_native_v176 as old
from generic_agents import report_design_grouped_wire_v178 as wire
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents.contracts import digest
from generic_agents.report_evidence_strict_map import METADATA_KEYS
from tests.test_report_design_grouped_wire_v178 import scene, binding, raw


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure indexed native protocol cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def execution():
    return {'provider': 'qwen', 'model': subject.MODEL, 'max_tokens': 8192,
        'report_evidence_output': {'schema': subject.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': subject.DELIVERY, 'endpoint': subject.ENDPOINT}}


def options():
    return subject.dispatch_options(execution(), {'model': subject.MODEL,
        'max_tokens': 8192, 'response_format': {'type': 'json_object'}})


def source_scene():
    value, inputs = scene(); bound = binding(inputs)
    return value, inputs, bound, wire.output_schema(**bound)


def payload(bound):
    value = {key: 'b' * 64 for key in subject._PAYLOAD_HASHES}
    value.update(schema=subject.REQUEST_PAYLOAD_SCHEMA,
        source_snapshot_hash=bound['source_snapshot_hash'],
        input_claims_hash=bound['indexed']['input_claims_hash'], context={},
        user_instruction='合成输入不授权执行。', design_note='', spans=[],
        indexed_inputs=codec.model_payload(**bound))
    return value


def messages(bound, schema):
    return [{'role': 'system', 'content': subject.envelope._OLD_PREFIX + json.dumps(schema, ensure_ascii=False)},
        {'role': 'system', 'content': '{"task":"public synthetic candidate only"}'},
        {'role': 'user', 'content': ' ' + json.dumps(payload(bound), ensure_ascii=False) + ' '}]


def envelope(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': json.dumps(value, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}}


def parse(body, bound, **overrides):
    return subject.parse_arguments(body, overrides.get('status', 200), subject.STAGE,
        response_complete=overrides.get('complete', True),
        http_content_encoding=overrides.get('encoding'), **bound)


def test_independent_native_profile_preserves_existing_caps_without_old_registration():
    value = execution(); before = copy.deepcopy(value)
    policy = subject.input_policy(value)
    assert policy['stage'] == subject.STAGE == 'reduce-v178'
    assert policy['max_input_bytes'] == 900000 and policy['max_output_tokens'] == 32768
    assert subject.stage_policy(subject.STAGE)['max_response_bytes'] == 1024 * 1024
    assert subject.DELIVERY != old.DELIVERY and subject.OUTPUT_DECLARATION != old.OUTPUT_DECLARATION
    assert subject.PROOF_SCHEMA != old.PROOF_SCHEMA
    assert subject.native_policy(subject.STAGE)['unsupported_schema_policy'] == 'fail_no_fallback'
    assert digest(subject.native_policy(subject.STAGE)) != digest(old.stage_policy(old.STAGE))
    assert options() == {'model': subject.MODEL, 'max_tokens': 32768,
        'enable_thinking': False, 'stream': False, 'n': 1}
    assert value == before and old.STAGE == 'reduce-v176'


@pytest.mark.parametrize('fault', ['old_family', 'old_transport', 'old_endpoint', 'other_model',
    'thinking', 'embedding', 'map_transport', 'bool_output', 'float_output', 'generic_output'])
def test_old_or_broader_registrations_are_rejected_not_relabelled(fault):
    value = execution(); declaration = value['report_evidence_output']
    if fault == 'old_family': declaration['schema'] = old.OUTPUT_DECLARATION
    elif fault == 'old_transport': declaration['reduce_transport'] = old.DELIVERY
    elif fault == 'old_endpoint': declaration['endpoint'] += '/other'
    elif fault == 'other_model': value['model'] = 'qwen3.8-flash'
    elif fault == 'thinking': value['thinking_mode'] = False
    elif fault == 'embedding': value['task'] = 'embedding'
    elif fault == 'map_transport': declaration['map_transport'] = subject.DELIVERY
    elif fault == 'bool_output': declaration['max_tokens'] = True
    elif fault == 'float_output': declaration['max_tokens'] = 32768.0
    else: value['max_tokens'] = 32768
    with pytest.raises(ValueError): subject.input_policy(value)


def test_actual_source_bound_proof_is_distinct_from_declaration_only_shape_proof():
    _, _, bound, schema = source_scene(); before = copy.deepcopy(schema)
    proof = subject.identity_schema_proof(schema, subject.STAGE, **bound)
    shape = subject.schema_shape_proof(schema, subject.STAGE)
    assert proof['schema'] == subject.PROOF_SCHEMA and shape['schema'] == subject.SHAPE_SCHEMA
    assert shape['source_binding_verified'] is False
    assert not {'indexed_inputs_hash', 'source_snapshot_hash', 'model_payload_hash'} & set(shape)
    assert proof['input_count'] == bound['indexed']['input_count']
    assert proof['indexed_inputs_hash'] == bound['indexed']['inputs_hash']
    assert proof['source_snapshot_hash'] == bound['source_snapshot_hash']
    assert proof['native_policy_hash'] == digest(subject.native_policy(subject.STAGE))
    assert subject.check_schema_proof(proof, schema, subject.STAGE, **bound) == proof
    with pytest.raises(ValueError): subject.check_schema_proof(shape, schema, subject.STAGE, **bound)
    assert schema == before
    for key in ('input_count', 'indexed_inputs_hash', 'source_snapshot_hash', 'model_payload_hash', 'native_policy_hash'):
        changed = copy.deepcopy(proof)
        changed[key] = 1920 if key == 'input_count' else 'f' * 64
        changed['proof_hash'] = digest({k: v for k, v in changed.items() if k != 'proof_hash'})
        with pytest.raises(ValueError): subject.check_schema_proof(changed, schema, subject.STAGE, **bound)


@pytest.mark.parametrize('fault', ['count', 'count_float', 'count_bool', 'positions', 'unique',
    'v166', 'extra', 'old_schema'])
def test_dynamic_schema_cannot_be_weakened_or_resealed_as_another_count(fault):
    _, _, bound, schema = source_scene(); changed = copy.deepcopy(schema)
    if fault == 'count': changed['properties']['input_count'].update(const=1920, maximum=1920, minimum=1920)
    elif fault == 'count_float': changed['properties']['input_count']['const'] = 3.0
    elif fault == 'count_bool': changed['properties']['input_count']['const'] = True
    elif fault == 'positions':
        changed['$defs']['ReconciliationGroupV178']['properties']['input_positions']['items']['maximum'] = 1919
    elif fault == 'unique': changed['$defs']['ReconciliationGroupV178']['properties']['input_positions'].pop('uniqueItems')
    elif fault == 'v166': changed['$defs']['CandidateObjects']['properties']['schema']['const'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'extra': changed['properties']['extra'] = {'type': 'string'}
    else: changed = old.DesignGroupedWireV174.model_json_schema()
    with pytest.raises(ValueError): subject.identity_schema_proof(changed, subject.STAGE, **bound)
    with pytest.raises(ValueError): subject.schema_shape_proof(changed, subject.STAGE)


def test_same_n_with_different_actual_claims_cannot_borrow_existing_identity_proof():
    _, _, bound, schema = source_scene(); proof = subject.identity_schema_proof(schema, subject.STAGE, **bound)
    other = copy.deepcopy(bound); other['input_claims'][0]['statement'] += '不同完整原声明'
    other['indexed'] = codec.build_indexed_inputs(other['input_claims'], other['source_snapshot_hash'])
    # A count/shape alone is identical, but the actual source proof is not.
    assert wire.output_schema(**other) == schema
    with pytest.raises(ValueError): subject.check_schema_proof(proof, schema, subject.STAGE, **other)


def test_request_inputs_rebuilds_every_original_claim_and_native_request_preserves_exact_tail():
    _, _, bound, schema = source_scene(); prepared = messages(bound, schema)
    before = copy.deepcopy(prepared)
    assert subject.request_inputs(prepared) == bound
    result = subject.prepare_request(prepared, schema, options(), subject.STAGE, **bound)
    assert result['request']['messages'][1:] == before[1:]
    assert result['request']['messages'][0] == {'role': 'system', 'content': subject.system_prefix(subject.STAGE)}
    assert result['request']['response_format']['json_schema']['schema'] == schema
    positions = result['request']['response_format']['json_schema']['schema']['$defs'][
        'ReconciliationGroupV178']['properties']['input_positions']
    assert positions['uniqueItems'] is True
    assert result['body'] == codec.canonical(result['request']).encode('utf-8')
    assert result['body_sha256'] == hashlib.sha256(result['body']).hexdigest()
    assert result['body_bytes'] == len(result['body']) <= 900000
    assert prepared == before


@pytest.mark.parametrize('fault', ['outer_schema', 'outer_extra', 'outer_missing', 'outer_snapshot',
    'outer_claims_hash', 'inner_snapshot', 'inner_count', 'inner_count_float', 'position_bool',
    'position_float', 'position_order', 'extra_claim_hash', 'missing_claim_key', 'changed_claim',
    'raw_old_claims', 'not_last_user', 'duplicate_json'])
def test_actual_request_payload_cannot_lose_numbering_or_borrow_an_old_or_changed_source(fault):
    _, _, bound, schema = source_scene(); prepared = messages(bound, schema)
    value = payload(bound); inner = value['indexed_inputs']
    if fault == 'outer_schema': value['schema'] = 'report-saved-map-design-payload-v176'
    elif fault == 'outer_extra': value['input_order'] = []
    elif fault == 'outer_missing': value.pop('map_inputs_hash')
    elif fault == 'outer_snapshot': value['source_snapshot_hash'] = 'c' * 64
    elif fault == 'outer_claims_hash': value['input_claims_hash'] = 'c' * 64
    elif fault == 'inner_snapshot': inner['source_snapshot_hash'] = 'c' * 64
    elif fault == 'inner_count': inner['input_count'] = 1920
    elif fault == 'inner_count_float': inner['input_count'] = 3.0
    elif fault == 'position_bool': inner['input_claims'][0]['input_position'] = False
    elif fault == 'position_float': inner['input_claims'][0]['input_position'] = 0.0
    elif fault == 'position_order': inner['input_claims'].reverse()
    elif fault == 'extra_claim_hash': inner['input_claims'][0]['claim_hash'] = 'c' * 64
    elif fault == 'missing_claim_key': inner['input_claims'][0]['claim'].pop('explanation')
    elif fault == 'changed_claim': inner['input_claims'][0]['claim']['statement'] += '新内容'
    elif fault == 'raw_old_claims':
        value.pop('indexed_inputs'); value['input_claims'] = bound['input_claims']
    prepared[-1]['content'] = json.dumps(value, ensure_ascii=False)
    if fault == 'not_last_user': prepared[-1]['role'] = 'system'
    elif fault == 'duplicate_json': prepared[-1]['content'] = '{"indexed_inputs":{},"indexed_inputs":{}}'
    before = copy.deepcopy(prepared)
    with pytest.raises(ValueError): subject.request_inputs(prepared)
    with pytest.raises(ValueError): subject.prepare_request(prepared, schema, options(), subject.STAGE, **bound)
    assert prepared == before


def test_internally_consistent_different_payload_is_rejected_against_caller_actual_input():
    _, _, bound, schema = source_scene(); other = copy.deepcopy(bound)
    other['input_claims'][0]['statement'] += '不同原文'
    other['indexed'] = codec.build_indexed_inputs(other['input_claims'], other['source_snapshot_hash'])
    prepared = messages(other, schema)
    assert subject.request_inputs(prepared) == other  # Content binding alone is not source authority.
    with pytest.raises(ValueError): subject.prepare_request(prepared, schema, options(), subject.STAGE, **bound)


def test_oversize_complete_request_fails_without_clipping_inputs_or_increasing_cap():
    _, _, bound, schema = source_scene(); prepared = messages(bound, schema)
    value = payload(bound); value['user_instruction'] = 'x' * 900000
    prepared[-1]['content'] = json.dumps(value, ensure_ascii=False)
    before = copy.deepcopy(prepared)
    with pytest.raises(ValueError): subject.prepare_request(prepared, schema, options(), subject.STAGE, **bound)
    assert prepared == before and subject.MAX_INPUT_BYTES == 900000


def test_raw_http_content_hashes_usage_and_new_count_are_preserved_without_quality_claim():
    value, _, bound, _ = source_scene(); body = raw(envelope(value)); before = body[:]
    inspected = subject.inspect_response(body, 200, subject.STAGE,
        response_complete=True, http_content_encoding=None)
    assert set(inspected['metadata']) == set(METADATA_KEYS)
    assert inspected['metadata']['delivery'] == subject.DELIVERY
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert inspected['metadata']['arguments_sha256'] == hashlib.sha256(inspected['arguments'].encode()).hexdigest()
    assert inspected['usage'] == envelope(value)['usage']
    assert parse(body, bound) == value and body == before
    assert 'semantic_quality_verified' not in inspected


@pytest.mark.parametrize('fault', ['length', 'not_eof', 'gzip', 'tool', 'multi_choice',
    'provider_unsupported', 'old_wire', 'invented_count', 'missing_positions'])
def test_original_completion_schema_or_position_failure_has_no_fallback(fault, monkeypatch):
    value, _, bound, _ = source_scene(); response = envelope(value); overrides = {}
    def forbidden(*args, **kwargs): raise AssertionError('No old response decoder fallback')
    monkeypatch.setattr(old, 'parse_arguments', forbidden)
    if fault == 'length': response['choices'][0]['finish_reason'] = 'length'
    elif fault == 'not_eof': overrides['complete'] = False
    elif fault == 'gzip': overrides['encoding'] = 'gzip'
    elif fault == 'tool': response['choices'][0]['message']['tool_calls'] = [{'id': 'forbidden'}]
    elif fault == 'multi_choice': response['choices'].append(copy.deepcopy(response['choices'][0]))
    elif fault == 'provider_unsupported':
        response = {'error': {'message': 'uniqueItems unsupported', 'code': 'invalid_schema'}}
        overrides['status'] = 400
    else:
        if fault == 'old_wire': value.pop('input_count')
        elif fault == 'invented_count': value['input_count'] = 1920
        else: value['reconciliation_groups'].pop()
        response['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ValueError): parse(raw(response), bound, **overrides)


def test_envelope_inspection_keeps_old_or_bad_content_for_truthful_capture_without_adopting_it():
    value, _, bound, _ = source_scene(); value.pop('input_count')
    body = raw(envelope(value))
    inspected = subject.inspect_response(body, 200, subject.STAGE,
        response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['envelope_valid'] is True
    assert json.loads(inspected['arguments']) == value
    with pytest.raises(ValueError): parse(body, bound)


@pytest.mark.parametrize('stage', ['report-indexed-design-v178', 'map-v178', 'reduce-v176', 'reduce-v174', '', None])
def test_no_unknown_or_old_stage_alias(stage):
    with pytest.raises(ValueError): subject.stage_policy(stage)
