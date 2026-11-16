"""Actual indexed preparation over public synthetic saved-Map protocol IO.

The fixture authors a supplied V174 snapshot in memory; it does not establish
real source ownership, paid delivery, IAM, consent, budget or semantic quality.
No provider/DB/filesystem calls occur. Root executes the independent runtime.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_design_grouped_native_v178 as native
from generic_agents import report_design_grouped_wire_v178 as wire
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents import report_saved_map_design_preparation_v176 as legacy
from generic_agents import report_saved_map_design_preparation_v178 as subject
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_preparation_v176 import source_scene, seal


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure indexed preparation cannot use external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, source = source_scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source


def original_claims(source):
    snapshot = source['snapshot']
    return [copy.deepcopy(snapshot['input_items'][key]) for key in snapshot['input_order']]


def reseal(preparation):
    preparation['payload_hash'] = digest(preparation['payload'])
    preparation['generation_contract_hash'] = digest(preparation['generation_contract'])
    preparation['preparation_hash'] = digest({key: value for key, value in preparation.items()
        if key != 'preparation_hash'})


def test_complete_indexed_claims_and_all_scope_text_remain_exact_without_proof_duplication():
    cap, source = scene(); before = canonical_json([cap.data, source])
    note = '  原编号不构成采用授权；仅当条件满足才提示。  '
    prep = subject.build_preparation(cap, source, note=note)
    snapshot = source['snapshot']; claims = original_claims(source)
    indexed = prep['indexed_inputs']; payload = prep['payload']; model = payload['indexed_inputs']
    assert indexed == codec.build_indexed_inputs(claims, snapshot['snapshot_hash'])
    assert model == codec.model_payload(indexed, input_claims=claims, source_snapshot_hash=snapshot['snapshot_hash'])
    assert set(model) == {'schema', 'source_snapshot_hash', 'input_count', 'input_claims'}
    assert [row['input_position'] for row in model['input_claims']] == list(range(len(claims)))
    assert all(set(row) == {'input_position', 'claim'} for row in model['input_claims'])
    assert canonical_json([row['claim'] for row in model['input_claims']]) == canonical_json(claims)
    assert claims[0] == claims[1] and len(claims) == 4  # duplicates retain distinct positions
    assert prep['input_order'] == snapshot['input_order']
    assert payload['spans'] == snapshot['spans'] and payload['context'] == source['context']
    assert canonical_json(payload['context']) == canonical_json(snapshot['context'])
    assert payload['user_instruction'] == snapshot['user_instruction'] and payload['design_note'] == note
    assert payload['context']['counter'] == 1.0 and '"zero":-0.0' in canonical_json(payload['context'])
    assert '未被原声明引用的旁支' not in json.dumps(payload, ensure_ascii=False)
    assert 'claim_hash' not in canonical_json(payload) and 'ordered_claim_hashes' not in canonical_json(payload)
    assert canonical_json([cap.data, source]) == before


def test_schema_is_complete_v166_plus_actual_count_and_position_bounds_not_static_capacity():
    cap, source = scene(); prep = subject.build_preparation(cap, source)
    claims = original_claims(source); count = len(claims); spec = subject.stage_spec(prep)
    expected = wire.output_schema(prep['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=source['snapshot']['snapshot_hash'])
    assert spec['output_schema'] == expected
    assert set(expected['properties']) == {'result', 'input_count', 'reconciliation_groups'}
    assert expected['properties']['input_count']['const'] == count
    groups = expected['properties']['reconciliation_groups']
    group = expected['$defs'][groups['items']['$ref'].split('/')[-1]]
    positions = group['properties']['input_positions']
    assert groups['maxItems'] == count and positions['maxItems'] == count
    assert positions['items']['maximum'] == count - 1 and positions['uniqueItems'] is True
    assert spec['schema_proof'] == native.identity_schema_proof(expected, native.STAGE,
        indexed=prep['indexed_inputs'], input_claims=claims, source_snapshot_hash=source['snapshot']['snapshot_hash'])
    assert spec['schema'] == subject.GENERATION and spec['evidence_stage'] == 'reduce-v178'
    assert spec['input_count'] == count and spec['indexed_inputs_hash'] == prep['indexed_inputs']['inputs_hash']
    assert spec['model_payload_hash'] == prep['indexed_inputs']['model_payload_hash']
    assert prep['output_capacity']['required_input_positions'] == count


def test_actual_http_body_is_measured_including_schema_messages_options_and_note():
    cap, source = scene(); note = ' 精确保存本次原文说明。 '
    prep = subject.build_preparation(cap, source, note=note)
    rebuilt = subject.rebuild_request(prep, cap, source, note=note)
    body = rebuilt['request_body']
    assert body == canonical_json(rebuilt['request']).encode('utf-8')
    assert hashlib.sha256(body).hexdigest() == rebuilt['request_hash'] == prep['request_hash']
    assert rebuilt['messages_hash'] == prep['messages_hash'] != prep['request_hash']
    assert prep['input_summary'] == {'prepared_request_bytes': len(body),
        'token_bound': len(body) + 32768 + 1024, 'max_input_bytes': 900000}
    assert len(body) > prep['indexed_inputs']['model_payload_bytes']
    assert json.loads(rebuilt['messages'][-1]['content']) == prep['payload']
    assert prep['call_count'] == 1 and prep['requires_separate_authorization'] is True
    for key in ('semantic_quality_verified', 'consumer_authorized', 'can_apply', 'can_publish'):
        assert prep[key] is False
    assert prep['output_capacity']['fit_guaranteed'] is False


@pytest.mark.parametrize('fault', ['claim', 'position', 'count', 'snapshot', 'schema_count',
    'schema_bound', 'refs', 'context_type', 'request_hash', 'authority'])
def test_self_resealed_preparation_cannot_replace_cold_actual_inputs(fault):
    cap, source = scene(); prep = subject.build_preparation(cap, source)
    if fault == 'claim': prep['indexed_inputs']['input_claims'][0]['claim']['statement'] += 'changed'
    elif fault == 'position': prep['indexed_inputs']['input_claims'][0]['input_position'] = 1
    elif fault == 'count': prep['indexed_inputs']['input_count'] += 1
    elif fault == 'snapshot': prep['source_snapshot_hash'] = '0' * 64
    elif fault == 'schema_count': prep['generation_contract']['output_schema']['properties']['input_count']['const'] = 1920
    elif fault == 'schema_bound':
        schema = prep['generation_contract']['output_schema']; ref = schema['properties']['reconciliation_groups']['items']['$ref']
        schema['$defs'][ref.split('/')[-1]]['properties']['input_positions']['items']['maximum'] = 1919
    elif fault == 'refs': prep['payload']['indexed_inputs']['input_claims'][0]['claim']['span_refs'] = ['w:p1/s999']
    elif fault == 'context_type': prep['payload']['context']['counter'] = 1
    elif fault == 'request_hash': prep['request_hash'] = '0' * 64
    else: prep['can_apply'] = True
    reseal(prep)
    before = canonical_json(prep)
    with pytest.raises(ValueError): subject.check_preparation(prep, cap, source)
    assert canonical_json(prep) == before


@pytest.mark.parametrize('fault', ['source_hash', 'allowed_refs', 'context_type', 'instruction', 'missing_claim'])
def test_changed_or_incomplete_cold_source_is_not_filled_from_a_saved_preview(fault):
    cap, source = scene(); prep = subject.build_preparation(cap, source)
    if fault == 'source_hash': source['snapshot']['source_hash'] = '0' * 64
    elif fault == 'allowed_refs': source['allowed_span_refs'].append('w:p1/s999')
    elif fault == 'context_type': source['context']['counter'] = 1
    elif fault == 'instruction': source['snapshot']['user_instruction'] += 'changed'
    else: source['snapshot']['input_items'].pop(source['snapshot']['input_order'][0])
    seal(source['snapshot'])
    with pytest.raises(ValueError): subject.check_preparation(prep, cap, source)


def test_real_gateway_redaction_cannot_modify_claim_text_silently():
    cap, source = scene(); cap.data['data_policy']['redact_fields'] = ['statement']
    before = canonical_json(source)
    with pytest.raises(ValueError): subject.build_preparation(cap, source)
    assert canonical_json(source) == before


def test_exact_body_limit_boundary_never_truncates_claims_or_raises_the_policy_cap(monkeypatch):
    cap, source = scene(); before = canonical_json(source)
    prep = subject.build_preparation(cap, source); count = prep['input_summary']['prepared_request_bytes']
    monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', count)
    assert subject.build_preparation(cap, source)['request_hash'] == prep['request_hash']
    monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', count - 1)
    with pytest.raises(EvidenceCapacityExceeded): subject.build_preparation(cap, source)
    assert canonical_json(source) == before and native.input_policy(cap.data['execution'])['max_input_bytes'] == 900000


def test_note_change_requires_new_preview_and_old_raw_request_is_immutable():
    cap, source = scene(); prep = subject.build_preparation(cap, source, note='原说明')
    original = subject.rebuild_request(prep, cap, source, note='原说明')['request_body']
    with pytest.raises(ValueError): subject.check_preparation(prep, cap, source, note='新说明')
    changed = subject.build_preparation(cap, source, note='新说明')
    assert changed['request_hash'] != prep['request_hash']
    assert subject.rebuild_request(prep, cap, source, note='原说明')['request_body'] == original


@pytest.mark.parametrize('note', [None, True, 'x' * 4001, '\x00', '\ud800'])
def test_invalid_note_is_rejected_not_trimmed_or_substituted(note):
    cap, source = scene()
    with pytest.raises((ValueError, UnicodeError)): subject.build_preparation(cap, source, note=note)


def test_v176_preparation_and_capability_keep_their_original_independent_family():
    old_cap, source = source_scene(); before = canonical_json([old_cap.data, source])
    old = legacy.build_preparation(old_cap, source)
    new_cap = copy.deepcopy(old_cap)
    new_cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    new = subject.build_preparation(new_cap, source)
    with pytest.raises(ValueError): subject.build_preparation(old_cap, source)
    with pytest.raises(ValueError): legacy.build_preparation(new_cap, source)
    with pytest.raises(ValueError): subject.check_preparation(old, new_cap, source)
    with pytest.raises(ValueError): legacy.check_preparation(new, old_cap, source)
    assert legacy.check_preparation(old, old_cap, source) == old
    assert canonical_json([old_cap.data, source]) == before


def test_staged_and_rebuilt_results_are_detached_from_originals_and_each_other():
    cap, source = scene(); before = canonical_json([cap.data, source])
    prep = subject.build_preparation(cap, source); original = canonical_json(prep)
    staged = subject.stage_spec(prep); checked = subject.check_preparation(prep, cap, source)
    rebuilt = subject.rebuild_request(prep, cap, source)
    staged['dispatch_options']['max_tokens'] = 128
    checked['indexed_inputs']['input_claims'][0]['claim']['span_refs'].clear()
    checked['payload']['indexed_inputs']['input_claims'][0]['claim']['statement'] = 'changed'
    rebuilt['request']['messages'].clear()
    assert canonical_json(prep) == original and canonical_json([cap.data, source]) == before
