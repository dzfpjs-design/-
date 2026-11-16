"""Synthetic original-argument counts, not provider/source/semantic proof.

The production parser and complete existing indexed wire model are used. No
candidate is returned, repaired, saved or approved by the diagnostic function.
"""
import copy
import json
import socket
import subprocess

import pytest

from generic_agents import report_design_response_diagnostics_v180 as subject
from generic_agents.report_design_grouped_wire_v178 import DesignGroupedWireV178
from generic_agents.report_design_grouped_wire_v179 import DesignGroupedWireV179


KEYS = {'schema', 'available', 'expected_count', 'declared_count', 'covered_count',
    'missing_count', 'duplicate_count', 'out_of_range_count', 'business_count',
    'step_count', 'problem', 'semantic_quality_verified', 'can_apply'}
COUNTS = {'declared_count', 'covered_count', 'missing_count', 'duplicate_count',
    'out_of_range_count', 'business_count', 'step_count'}


def scene(count=920, covered=904):
    citation = {'span_ref': 'w:p1/s1'}
    result = {'schema': 'report-object-semantic-candidate-wire-v166',
        'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64, 'domain': '公共合成记录核对',
        'global_claims': [], 'objects': [{
            'name': '读数记录', 'fields': [{'label': '读数', 'kind': '数值',
                'required': False, 'unit': None, 'citations': [citation],
                'inference_reason': ''}], 'citations': [citation], 'inference_reason': '',
            'business_identity': {'status': 'not_established', 'field_labels': [],
                'citations': [], 'explanation': '公共合成资料未明确业务标识，继续待核。'}}],
        'relationships': [], 'businesses': [{'operation': 'upsert', 'original_name': '',
            'name': '人工核对', 'trigger': '提出核对', 'output': '待核记录',
            'steps': [{'name': '对照依据', 'purpose': '人工对照原记录', 'agent': '人工确认',
                'role': '核查人', 'output': '核查意见', 'next_index': None,
                'branch_condition': '', 'else_index': None, 'citations': [citation],
                'inference_reason': ''}], 'citations': [citation], 'inference_reason': '',
            'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}],
        'questions': ['原资料和完整语义仍须核查']}
    return {'result': result, 'input_count': count, 'reconciliation_groups': [{
        'output_kind': 'businesses', 'output_index': 0, 'disposition': 'kept',
        'input_positions': list(range(covered)), 'reason': '公共合成分组，仅用于安全计数测试。'}]}


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def unavailable(value, expected, problem):
    answer = subject.diagnose_response(value, expected_count=expected)
    assert set(answer) == KEYS
    assert answer['schema'] == subject.SCHEMA
    assert answer['available'] is False and answer['problem'] == problem
    assert all(answer[key] is None for key in COUNTS)
    assert answer['semantic_quality_verified'] is False and answer['can_apply'] is False
    assert 'result' not in answer and 'arguments' not in answer
    return answer


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure response diagnostics cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def test_complete_indexed_shape_safely_reports_920_904_16_without_approval_or_raw_output():
    value = scene(); before = copy.deepcopy(value); content = raw(value)
    for model in (DesignGroupedWireV178, DesignGroupedWireV179):
        assert model.model_validate(value).model_dump() == value
    answer = subject.diagnose_response(content, expected_count=920)
    assert answer == {'schema': subject.SCHEMA, 'available': True, 'expected_count': 920,
        'declared_count': 920, 'covered_count': 904, 'missing_count': 16,
        'duplicate_count': 0, 'out_of_range_count': 0, 'business_count': 1, 'step_count': 1,
        'problem': None, 'semantic_quality_verified': False, 'can_apply': False}
    assert set(answer) == KEYS and value == before and raw(value) == content


@pytest.mark.parametrize('count', [1, 3, 1920])
def test_actual_count_bound_and_complete_coverage_never_imply_semantic_quality(count):
    answer = subject.diagnose_response(raw(scene(count, count)), expected_count=count)
    assert answer['available'] is True and answer['covered_count'] == count
    assert answer['missing_count'] == answer['duplicate_count'] == 0
    assert answer['semantic_quality_verified'] is False and answer['can_apply'] is False


def test_duplicates_are_extra_occurrences_and_never_count_as_new_coverage():
    value = scene(5, 3)
    value['reconciliation_groups'][0]['input_positions'] = [0, 0, 2]
    value['reconciliation_groups'].append({**copy.deepcopy(value['reconciliation_groups'][0]),
        'input_positions': [2, 2, 4]})
    answer = subject.diagnose_response(raw(value), expected_count=5)
    assert answer['available'] is True and answer['covered_count'] == 3
    assert answer['missing_count'] == 2 and answer['duplicate_count'] == 3
    assert answer['out_of_range_count'] == 0 and answer['can_apply'] is False


def test_duplicate_object_names_allowed_by_existing_shape_are_not_misreported_as_semantic_validation():
    value = scene(3, 2)
    value['result']['objects'].append(copy.deepcopy(value['result']['objects'][0]))
    assert DesignGroupedWireV179.model_validate(value).model_dump() == value
    answer = subject.diagnose_response(raw(value), expected_count=3)
    assert answer['available'] is True and answer['covered_count'] == 2
    assert answer['semantic_quality_verified'] is False and answer['can_apply'] is False
    assert not any('schema_pass' in key or 'candidate' in key for key in answer)


@pytest.mark.parametrize('expected', [None, True, False, 0, -1, 1.0, '920', 1921, 10**100])
def test_expected_count_must_come_as_a_strict_bounded_actual_integer(expected):
    answer = unavailable(raw(scene()), expected, 'expected_count_invalid')
    assert answer['expected_count'] is None


@pytest.mark.parametrize('content', [None, '', bytearray(b'{}'), memoryview(b'{}'), b''])
def test_missing_or_wrong_bytes_type_is_unavailable_not_an_empty_valid_candidate(content):
    assert unavailable(content, 920, 'arguments_unavailable')['expected_count'] == 920


def test_oversized_original_bytes_are_not_truncated_or_partly_counted():
    content = raw(scene()) + b' ' * subject.MAX_ARGUMENT_BYTES
    assert unavailable(content, 920, 'arguments_too_large')['expected_count'] == 920


@pytest.mark.parametrize('content', [b'\xff', b'{', b'null', b'[]', b'{}'])
def test_bad_utf8_truncated_or_non_wire_json_is_unavailable(content):
    problem = 'invalid_json' if content in (b'\xff', b'{') else 'invalid_wire_shape'
    unavailable(content, 920, problem)


@pytest.mark.parametrize('fault', ['duplicate_root', 'escaped_duplicate_nested', 'nan',
    'infinity', 'float_position', 'float_count', 'float_target', 'overflow', 'surrogate', 'nul'])
def test_ambiguous_json_and_non_integer_numeric_tokens_are_not_coerced(fault):
    content = raw(scene(3, 2))
    if fault == 'duplicate_root': content = content[:-1] + b',"input_count":3}'
    elif fault == 'escaped_duplicate_nested':
        content = content.replace(b'"required": false', b'"required": false, "requir\\u0065d": true', 1)
    elif fault == 'nan': content = content.replace(b'"input_count": 3', b'"input_count": NaN')
    elif fault == 'infinity': content = content.replace(b'"input_count": 3', b'"input_count": Infinity')
    elif fault == 'float_position': content = content.replace(b'"input_positions": [0, 1]', b'"input_positions": [0, 1.0]')
    elif fault == 'float_count': content = content.replace(b'"input_count": 3', b'"input_count": 3.0')
    elif fault == 'float_target': content = content.replace(b'"output_index": 0', b'"output_index": 0.0')
    elif fault == 'overflow': content = content.replace(b'"input_count": 3', b'"input_count": 1e10000')
    elif fault == 'surrogate': content = content.replace(b'"domain":', b'"private\\ud800":0,"domain":', 1)
    else: content = content.replace(b'"domain":', b'"private\\u0000":0,"domain":', 1)
    assert content != raw(scene(3, 2))
    unavailable(content, 3, 'invalid_json')


@pytest.mark.parametrize('position', [True, False, '1', None, -1, 1920])
def test_illegal_position_type_or_static_range_hides_all_answer_counts(position):
    value = scene(3, 2); value['reconciliation_groups'][0]['input_positions'] = [position]
    unavailable(raw(value), 3, 'invalid_wire_shape')


def test_actual_source_range_is_not_replaced_by_the_model_or_static_1920_ceiling():
    value = scene(3, 2); value['reconciliation_groups'][0]['input_positions'] = [0, 3]
    answer = unavailable(raw(value), 3, 'position_out_of_range')
    assert answer['out_of_range_count'] is None
    value['input_count'] = 4
    unavailable(raw(value), 3, 'declared_count_mismatch')


@pytest.mark.parametrize('fault', ['count_bool', 'count_string', 'count_missing', 'old_wire',
    'candidate_old_marker', 'object_fields_empty', 'object_bad_required', 'object_bad_identity',
    'object_extra_private', 'all_objects_invalid', 'business_bad_steps', 'step_index_bool',
    'group_empty', 'reason_missing', 'extra_root', 'too_many_groups'])
def test_any_malformed_complete_object_business_or_group_shape_makes_all_counts_unavailable(fault):
    value = scene(3, 2)
    if fault == 'count_bool': value['input_count'] = True
    elif fault == 'count_string': value['input_count'] = '3'
    elif fault == 'count_missing': value.pop('input_count')
    elif fault == 'old_wire': value.pop('input_count')
    elif fault == 'candidate_old_marker': value['result']['schema'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'object_fields_empty': value['result']['objects'][0]['fields'] = []
    elif fault == 'object_bad_required': value['result']['objects'][0]['fields'][0]['required'] = 0
    elif fault == 'object_bad_identity': value['result']['objects'][0]['business_identity']['field_labels'] = ['缺失字段']
    elif fault == 'object_extra_private': value['result']['objects'][0]['private_note'] = 'NEVER EXPOSE'
    elif fault == 'all_objects_invalid': value['result']['objects'] = [{'name': '只剩名称'}] * 4
    elif fault == 'business_bad_steps': value['result']['businesses'][0]['steps'] = [{}]
    elif fault == 'step_index_bool': value['result']['businesses'][0]['steps'][0]['next_index'] = True
    elif fault == 'group_empty': value['reconciliation_groups'][0]['input_positions'] = []
    elif fault == 'reason_missing': value['reconciliation_groups'][0].pop('reason')
    elif fault == 'extra_root': value['private_provider_answer'] = 'NEVER EXPOSE'
    else: value['reconciliation_groups'] *= 4
    before = copy.deepcopy(value)
    answer = unavailable(raw(value), 3, 'invalid_wire_shape')
    assert answer['expected_count'] == 3 and value == before
    assert 'NEVER EXPOSE' not in json.dumps(answer)


def test_unexpected_counting_failure_returns_fixed_unavailable_without_exception_text(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('NEVER EXPOSE provider answer or private material')
    monkeypatch.setattr(subject, '_checked_counts', fail)
    answer = unavailable(raw(scene()), 920, 'diagnostic_unavailable')
    assert 'NEVER EXPOSE' not in json.dumps(answer)
