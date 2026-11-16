"""Unconnected pure V6 codec tests; root alone executes them.

Imports only public pure examples. No actual report, gold, paid response,
journal, API, DB, key, provider, user workflow or approval is simulated.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents.contracts import digest
from generic_agents import report_evidence_reduction_wire_v6 as subject
from generic_agents.report_evidence_ref_schema_v2 import compile_ref_schema, check_ref_schema_proof
from generic_agents.report_evidence_wire_v3 import CandidateWireV5, ReduceResultWireV5
from tests.test_report_evidence_wire_v3_pure import reduce_case

@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure codec cannot execute or connect')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    v5, original = reduce_case()
    items = {f'map-{number}/claims/{index}': copy.deepcopy(claim)
        for number, group in enumerate(original['map_inputs'], 1)
        for index, claim in enumerate(group['projection']['claims'])}
    expected = {'catalog': original['catalog'], 'allowed_span_refs': original['allowed_span_refs'],
        'context': original['context'], 'input_order': list(items), 'input_items': items}
    return v5, subject.encode_reduction_wire_v6(v5), expected


def raw(value, **options):
    return json.dumps(value, ensure_ascii=False, **options).encode('utf-8')


def projected(value, expected):
    return subject.build_reduction_projection(raw(value), **expected)


def changed(value, path, replacement):
    result = copy.deepcopy(value); parent = result
    for key in path[:-1]: parent = parent[key]
    parent[path[-1]] = replacement
    return result


def test_full_schema_is_required_closed_nonnullable_default_free_and_compiles_audited_provider_subset():
    schema = subject.ReductionWireV6.model_json_schema()
    old = CandidateWireV5.model_json_schema()
    nullable = {(name, field) for name, definition in old['$defs'].items()
        for field, field_schema in definition.get('properties', {}).items()
        if {'type': 'null'} in field_schema.get('anyOf', [])}
    assert nullable == {('StepWireV5', 'next_index'), ('StepWireV5', 'else_index'), ('SpanField', 'unit')}
    def audit_original(value):
        if type(value) is dict:
            assert 'default' not in value
            if 'properties' in value:
                assert value.get('additionalProperties') is False
                assert set(value['required']) == set(value['properties'])
            for item in value.values(): audit_original(item)
        elif type(value) is list:
            for item in value: audit_original(item)
    audit_original(old)
    def visit(value):
        if type(value) is dict:
            assert 'default' not in value and value.get('type') != 'null'
            assert type(value.get('type')) is not list
            if 'properties' in value:
                assert value['type'] == 'object' and value['additionalProperties'] is False
                assert set(value['required']) == set(value['properties'])
            for item in value.values(): visit(item)
        elif type(value) is list:
            for item in value: visit(item)
    visit(schema)
    proof = subject.provider_schema_proof()
    assert proof['local_schema_hash'] == digest(schema)
    assert proof == compile_ref_schema(json.loads(subject._json_bytes(schema)))
    assert check_ref_schema_proof(proof, schema) == proof
    assert proof['proof_hash'] == digest({key: item for key, item in proof.items() if key != 'proof_hash'})
    assert proof['audit']['transformations'] and schema == subject.ReductionWireV6.model_json_schema()
    provider = proof['provider_schema']
    assert '$defs' not in provider and set(provider['$def']) == set(schema['$defs'])
    assert provider['properties']['result'] == {'$ref': '#/$def/CandidateWireV6'}
    assert provider['$def']['BusinessWireV6']['properties']['steps']['items'] == {'$ref': '#/$def/StepWireV6'}
    assert provider['$def']['StepWireV6']['properties']['next_index']['anyOf'] == [
        {'$ref': '#/$def/EndTarget'}, {'$ref': '#/$def/StepTarget'}]
    visit(proof['provider_schema'])
    assert schema['$defs']['StepWireV6']['properties']['next_index']['anyOf']
    assert schema['$defs']['FieldWireV6']['properties']['unit']['anyOf']
    assert schema['$defs']['MapClaimWireV3']['properties']['kind']['enum'] == [
        '职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他']
    assert schema['$defs']['MapClaimWireV3']['properties']['statement']['maxLength'] == 2000


def test_exact_inverse_retains_every_semantic_field_unicode_and_none_zero_or_unit_string():
    v5, value, expected = scene(); before = copy.deepcopy((v5, value, expected))
    assert subject.decode_reduction_wire_v6(value) == v5
    assert subject.encode_reduction_wire_v6(subject.decode_reduction_wire_v6(value)) == value
    assert value['result']['objects'][0]['fields'][0]['unit'] == {'kind': 'unspecified'}
    assert value['result']['objects'][1]['fields'][1]['unit'] == {'kind': 'specified', 'value': '株'}
    assert value['result']['businesses'][0]['steps'][1]['next_index'] == {'kind': 'end'}
    zero = copy.deepcopy(value); zero['result']['businesses'][0]['steps'][1]['next_index'] = {'kind': 'step', 'index': 0}
    assert subject.decode_reduction_wire_v6(zero)['result']['businesses'][0]['steps'][1]['next_index'] == 0
    assert subject.encode_reduction_wire_v6(subject.decode_reduction_wire_v6(zero)) == zero
    projected(value, expected)
    assert (v5, value, expected) == before


@pytest.mark.parametrize('target', [None, 0, '', False, {'kind': 'step', 'index': False},
    {'kind': 'step', 'index': 1.0}, {'kind': 'step', 'index': -1}, {'kind': 'step'},
    {'kind': 'end', 'index': 0}, {'kind': 'step', 'index': '0'}, {'kind': 'auto'}])
def test_missing_or_ambiguous_target_never_becomes_an_end_or_integer(target):
    _, value, _ = scene()
    value['result']['businesses'][0]['steps'][0]['next_index'] = target
    with pytest.raises(ValueError): subject.decode_reduction_wire_v6(value)


@pytest.mark.parametrize('unit', [None, '', 0, False, {'kind': 'specified', 'value': ''},
    {'kind': 'specified', 'value': ' 株 '}, {'kind': 'specified', 'value': 'x' * 41},
    {'kind': 'unspecified', 'value': ''}, {'kind': 'specified'}, {'kind': 'unknown'}])
def test_unit_missing_none_empty_and_explicit_words_are_not_guessed_or_trimmed(unit):
    _, value, _ = scene(); value['result']['objects'][1]['fields'][1]['unit'] = unit
    with pytest.raises(ValueError): subject.decode_reduction_wire_v6(value)


@pytest.mark.parametrize('where', ['target', 'unit', 'extra_key', 'old_schema', 'bad_kind', 'bad_status'])
def test_required_fields_extra_technical_key_and_old_or_unknown_versions_reject(where):
    _, value, _ = scene()
    if where == 'target': del value['result']['businesses'][0]['steps'][0]['next_index']
    elif where == 'unit': del value['result']['objects'][0]['fields'][0]['unit']
    elif where == 'extra_key': value['result']['global_claims'][0]['key'] = 'invented'
    elif where == 'old_schema': value['result']['schema'] = 'report-evidence-candidate-wire-v5'
    elif where == 'bad_kind': value['result']['global_claims'][0]['kind'] = '近义类别'
    else: value['result']['global_claims'][0]['status'] = 'confirmed'
    with pytest.raises(ValueError): subject.decode_reduction_wire_v6(value)


def test_original_v5_models_are_unchanged_and_do_not_accept_tags_or_empty_units():
    v5, value, _ = scene(); candidate_schema = copy.deepcopy(CandidateWireV5.model_json_schema())
    assert ReduceResultWireV5.model_validate(v5).model_dump() == v5
    with pytest.raises(ValueError): ReduceResultWireV5.model_validate(value)
    v5['result']['objects'][1]['fields'][1]['unit'] = ''
    with pytest.raises(ValueError): subject.encode_reduction_wire_v6(v5)
    assert CandidateWireV5.model_json_schema() == candidate_schema
    assert {'type': 'null'} in candidate_schema['$defs']['StepWireV5']['properties']['next_index']['anyOf']


def test_raw_bytes_codec_and_projection_hashes_are_separate_and_cold_rebuild_is_exact():
    v5, value, expected = scene(); original = raw(value, indent=2)
    proof = subject.build_reduction_projection(original, **expected)
    assert proof['wire_bytes_sha256'] == hashlib.sha256(original).hexdigest()
    assert proof['wire_value_hash'] == digest(value) and proof['codec_value_hash'] == digest(v5)
    assert proof['wire_value_hash'] != proof['codec_value_hash']
    assert proof['projection_hash'] == digest({key: item for key, item in proof.items() if key != 'projection_hash'})
    assert proof['result']['schema'] == subject.CANDIDATE
    assert subject.check_reduction_projection(proof, original, **expected) == proof
    reordered_index = copy.deepcopy(expected)
    reordered_index['catalog']['index'] = dict(reversed(list(reordered_index['catalog']['index'].items())))
    assert subject.check_reduction_projection(proof, original, **reordered_index) == proof
    same_value_different_bytes = raw(value, separators=(',', ':'))
    other = subject.build_reduction_projection(same_value_different_bytes, **expected)
    assert other['wire_value_hash'] == proof['wire_value_hash'] and other['codec_value_hash'] == proof['codec_value_hash']
    assert other['wire_bytes_sha256'] != proof['wire_bytes_sha256']
    assert other['result']['global_claims'][0]['key'] != proof['result']['global_claims'][0]['key']
    for field in ('wire_bytes_sha256', 'wire_value_hash', 'codec_value_hash', 'context_hash'):
        bad = copy.deepcopy(proof); bad[field] = 'f' * 64
        bad['projection_hash'] = digest({key: item for key, item in bad.items() if key != 'projection_hash'})
        with pytest.raises(ValueError): subject.check_reduction_projection(bad, original, **expected)
    bad = copy.deepcopy(proof); bad['result']['businesses'][0]['steps'][0]['key'] = 'self_signed'
    bad['projection_hash'] = digest({key: item for key, item in bad.items() if key != 'projection_hash'})
    with pytest.raises(ValueError): subject.check_reduction_projection(bad, original, **expected)
    changed_context = {**expected, 'context': {**expected['context'], 'unused_context': 'changed'}}
    with pytest.raises(ValueError): subject.check_reduction_projection(proof, original, **changed_context)


def test_explicit_multi_step_cycle_and_business_local_participant_links_are_preserved():
    _, value, expected = scene(); first = value['result']['businesses'][0]
    first['steps'][1]['next_index'] = {'kind': 'step', 'index': 0}
    second = copy.deepcopy(first); second['name'] = '另一登记核对'; value['result']['businesses'].append(second)
    proof = projected(value, expected)
    for business in proof['result']['businesses']:
        assert business['steps'][0]['next_key'] == business['steps'][1]['key']
        assert business['steps'][1]['next_key'] == business['steps'][0]['key']
        assert business['interactions'][0]['from_participant'] == business['participants'][0]['key']
    assert proof['result']['businesses'][0]['steps'][0]['key'] != proof['result']['businesses'][1]['steps'][0]['key']


@pytest.mark.parametrize('mode', ['self', 'step_range', 'participant_range', 'duplicate_steps',
    'outside_ref', 'missing_reconcile', 'duplicate_reconcile', 'bool_reconcile', 'output_range', 'status_upgrade', 'input_order', 'allowed_order'])
def test_complete_old_local_semantic_reference_and_reconciliation_checks_still_apply(mode):
    _, value, expected = scene(); business = value['result']['businesses'][0]
    if mode == 'self': business['steps'][0]['next_index'] = {'kind': 'step', 'index': 0}
    elif mode == 'step_range': business['steps'][0]['next_index'] = {'kind': 'step', 'index': 99}
    elif mode == 'participant_range': business['interactions'][0]['from_participant_index'] = 99
    elif mode == 'duplicate_steps': business['coordination'][0]['step_indices'] = [0, 0]
    elif mode == 'outside_ref': business['steps'][0]['citations'] = [{'span_ref': 'w:p999/s1'}]
    elif mode == 'missing_reconcile': value['reconciliation'].pop()
    elif mode == 'duplicate_reconcile': value['reconciliation'][1]['input_position'] = 0
    elif mode == 'bool_reconcile': value['reconciliation'][0]['input_position'] = False
    elif mode == 'output_range': value['reconciliation'][0]['output_index'] = 99
    elif mode == 'status_upgrade':
        expected['input_items']['map-3/claims/0'].update(status='inferred', explanation='合成推断仍未核实。')
    elif mode == 'input_order': expected['input_order'].reverse()
    else: expected['allowed_span_refs'].reverse()
    with pytest.raises(ValueError): projected(value, expected)


def test_reconciliation_wire_order_and_all_unchanged_arrays_survive_without_sort_or_dedup():
    v5, value, expected = scene(); value['reconciliation'].reverse(); v5['reconciliation'].reverse()
    assert subject.decode_reduction_wire_v6(value) == v5
    proof = projected(value, expected)
    assert [entry['input_id'] for entry in proof['reconciliation']] == list(reversed(expected['input_order']))
    assert [item['name'] for item in proof['result']['objects']] == [item['name'] for item in v5['result']['objects']]


@pytest.mark.parametrize('raw_value', [b'', b'\xff', b'{"result":{},"result":{}}', b'{"x":NaN}', b'{"x":1e309}'])
def test_strict_original_byte_parsing_does_not_repair_duplicate_bad_utf8_or_nonfinite_json(raw_value):
    _, _, expected = scene()
    with pytest.raises(ValueError): subject.build_reduction_projection(raw_value, **expected)


def test_capacity_limits_fail_before_adoption_without_changing_old_wire_limit():
    _, value, expected = scene()
    with pytest.raises(ValueError): subject.build_reduction_projection(b' ' * (subject.MAX_WIRE_BYTES + 1), **expected)
    value['result']['global_claims'] *= 161
    with pytest.raises(ValueError): subject.decode_reduction_wire_v6(value)
    from generic_agents.report_evidence_wire_v3 import MAX_WIRE_VALUE_BYTES
    assert MAX_WIRE_VALUE_BYTES == 256 * 1024
    assert subject.MAX_WIRE_BYTES == 1024 * 1024
    assert subject.MAX_PROJECTION_BYTES == 8 * 1024 * 1024


def test_final_projection_capacity_includes_its_hash_and_rejects_before_return(monkeypatch):
    _, value, expected = scene()
    proof = projected(value, expected)
    without_hash = {key: item for key, item in proof.items() if key != 'projection_hash'}
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', len(subject._json_bytes(without_hash)))
    with pytest.raises(ValueError, match='reduction_v6_capacity_exceeded'):
        projected(value, expected)


def test_proposal_schema_is_distinct_and_never_accepted_as_product_wire():
    _, value, _ = scene()
    assert subject.WIRE == 'report-evidence-candidate-wire-v6'
    assert subject.CODEC == 'report-evidence-reduction-codec-v6'
    assert subject.PROJECTION == 'report-evidence-reduction-projection-v6'
    assert subject.CANDIDATE == 'report-evidence-candidate-projection-v6'
    assert subject.IDS == 'report-evidence-reduction-ids-v6'
    value['result']['schema'] += '-proposed'
    with pytest.raises(ValueError):
        subject.decode_reduction_wire_v6(value)
