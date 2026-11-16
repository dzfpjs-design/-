"""Synthetic pure contracts, not provider/source/IAM or business-quality proof."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_catalogue_wire_v211 as subject
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents import report_scoped_design_plan_v180 as planner
from generic_agents.report_evidence_plan import build_evidence_source

SNAPSHOT = 'a' * 64


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure catalogue must not connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def claim(position, statement, *, kind='其他', status='explicit'):
    return {'kind': kind, 'statement': statement, 'span_refs': [f'w:p{position + 1}/s1'],
        'status': status, 'explanation': '' if status == 'explicit' else '原机器声明仍待人工对照',
        'raw_extension': {'count': 1.0, 'zero': -0.0, 'flag': True, 'nested': ['原始😀\n文字', {'v': 0}]}}


def business(key, name, seed):
    return {'key': key, 'name': name, 'purpose': '处理业务申请', 'boundary': '仅限本项业务',
        'seed_positions': [seed], 'name_basis': 'explicit', 'reason': '原片段含候选名称，职责仍待核对'}


def group(kind, positions, keys=None, questions=None):
    return {'kind': kind, 'business_keys': keys or [], 'input_positions': positions,
        'reason': '本组为机器建议，需核对原文后确认', 'question_keys': questions or []}


def scene():
    statements = ['设备调度', '核对申请', '资产盘点', '盘点采样间隔尚未明确',
        '两项业务共用授权记录', '项目建设背景', '责任归属未确定', '建议补充设备状态']
    claims = [claim(i, text, kind='业务' if i in {0, 2} else '其他',
        status='unknown' if i == 3 else 'inferred' if i == 7 else 'explicit')
        for i, text in enumerate(statements)]
    catalog = build_evidence_source('b' * 64, [[f'w:p{i + 1}', '原始资料：' + text + '。😀\n']
        for i, text in enumerate(statements)])
    value = {'schema': subject.WIRE, 'input_count': len(claims),
        'businesses': [business('b1', '设备调度', 0), business('b2', '资产盘点', 2)],
        'allocation_groups': [group('business', [0, 1, 7], ['b1']),
            group('business', [2, 3], ['b2'], ['q_info']), group('shared', [4], ['b1', 'b2']),
            group('background', [5]), group('pending', [6], questions=['q_owner'])],
        'questions': [{'key': 'q_info', 'kind': 'missing_info', 'input_positions': [3],
            'candidate_business_keys': ['b2'], 'question': '盘点采样间隔是多少？'},
            {'key': 'q_owner', 'kind': 'ownership', 'input_positions': [6],
                'candidate_business_keys': ['b1', 'b2'], 'question': '这项责任属于哪项业务？'}],
        'merge_suggestions': [{'business_keys': ['b1', 'b2'], 'reason': '仅提出合并供人核对，不自动采用'}]}
    return value, {'indexed': codec.build_indexed_inputs(claims, SNAPSHOT),
        'input_claims': claims, 'source_snapshot_hash': SNAPSHOT, 'catalog': catalog}


def single(count):
    claims = [claim(i, '设备调度' if i == 0 else f'必须保留的原声明 {i}') for i in range(count)]
    catalog = build_evidence_source('b' * 64,
        [[f'w:p{i + 1}', item['statement']] for i, item in enumerate(claims)])
    value = {'schema': subject.WIRE, 'input_count': count, 'businesses': [business('b1', '设备调度', 0)],
        'allocation_groups': [group('business', list(range(count)), ['b1'])],
        'questions': [], 'merge_suggestions': []}
    return value, {'indexed': codec.build_indexed_inputs(claims, SNAPSHOT),
        'input_claims': claims, 'source_snapshot_hash': SNAPSHOT, 'catalog': catalog}


def raw(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def build(value, bound):
    return subject.build_catalogue_projection(raw(value), **bound)


def test_full_grouped_projection_keeps_original_evidence_and_unresolved_work_without_merging():
    value, bound = scene(); before = codec.canonical([value, bound])
    original = raw(value); result = subject.build_catalogue_projection(original, **bound)
    assert result == subject.check_catalogue_projection(result, original, **bound)
    assert result['wire'] == value and result['input_count'] == 8
    assert result['raw_arguments_sha256'] == hashlib.sha256(original).hexdigest()
    assert [row['input_position'] for row in result['assignments']] == list(range(8))
    for position, row in enumerate(result['assignments']):
        original_claim = bound['input_claims'][position]
        assert row['claim'] == original_claim and row['span_refs'] == original_claim['span_refs']
        assert codec.canonical(row['claim']) == codec.canonical(original_claim)
        assert row['source_spans'] == [{'span_ref': ref, 'record': bound['catalog']['index'][ref]}
            for ref in original_claim['span_refs']]
        assert row['machine_only'] is True
    assert result['assignments'][2]['question_keys'] == []
    assert result['assignments'][3]['question_keys'] == ['q_info']
    ledger = result['ledger']
    assert set(ledger) == planner._BASE_KEYS and 'confirmation' not in ledger
    assert [row['scope_id'] for row in ledger['scopes']] == ['b1', 'b2']
    assert [row['claim_positions'] for row in ledger['scopes']] == [[0, 1, 7], [2, 3]]
    assert ledger['shared'] == [{'input_position': 4, 'scope_ids': ['b1', 'b2'],
        'reason': value['allocation_groups'][2]['reason']}]
    assert ledger['background'][0]['input_position'] == 5
    assert result['pending_positions'] == [6] and result['scope_confirmation_blocked'] is True
    assert result['scope_confirmation_blockers'] == ['pending_ownership']
    assert [q['key'] for q in result['missing_info_questions']] == ['q_info']
    assert result['merge_suggestions'][0]['machine_only'] is True
    assert all(result[field] is False for field in ('semantic_quality_verified', 'human_confirmed',
        'source_anchors_verified', 'report_coverage_verified', 'can_confirm', 'can_apply',
        'can_publish', 'dispatch_allowed'))
    assert codec.canonical([value, bound]) == before
    result['assignments'][0]['claim']['raw_extension']['nested'][1]['v'] = 99
    result['assignments'][0]['source_spans'][0]['record']['text'] = '篡改显示'
    result['wire']['businesses'][0]['name'] = '篡改机器建议'
    assert codec.canonical([value, bound]) == before
    with pytest.raises(subject.CatalogueWireError):
        subject.check_catalogue_projection(result, original, **bound)


def test_resolving_only_ownership_preserves_missing_info_and_never_fabricates_confirmation():
    value, bound = scene()
    value['allocation_groups'][-1] = group('business', [6], ['b2'])
    value['questions'] = [value['questions'][0]]
    result = build(value, bound)
    assert result['pending_positions'] == [] and result['ownership_complete'] is True
    assert result['scope_confirmation_blocked'] is False
    assert [q['key'] for q in result['missing_info_questions']] == ['q_info']
    assert result['ledger']['scopes'][1]['claim_positions'] == [2, 3, 6]
    with pytest.raises(planner.ScopedDesignPlanError):
        planner.validate_scope_ledger(bound['indexed'], SNAPSHOT, result['ledger'],
            input_claims=bound['input_claims'])
    assert result['human_confirmed'] is False and 'confirmation' not in result['ledger']


@pytest.mark.parametrize('count', [1, 3, 17, 920])
def test_real_canonical_manifest_count_drives_schema_and_every_original_position(count):
    value, bound = single(count)
    schema = subject.output_schema(bound['indexed'], input_claims=bound['input_claims'],
        source_snapshot_hash=SNAPSHOT)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    assert schema['properties']['input_count'] == {'type': 'integer', 'const': count,
        'minimum': count, 'maximum': count}
    positions = schema['properties']['allocation_groups']['items']['properties']['input_positions']
    assert positions['maxItems'] == count and positions['items']['maximum'] == count - 1
    assert positions['uniqueItems'] is True
    def closed(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for child in node.values(): closed(child)
        elif isinstance(node, list):
            for child in node: closed(child)
    closed(schema)
    result = build(value, bound)
    assert len(result['assignments']) == count
    assert result['ledger']['scopes'][0]['claim_positions'] == list(range(count))
    assert result['input_coverage_complete'] is True and result['machine_only'] is True
    for invalid in ({**value, 'input_count': count + 1}, {**value, 'source_snapshot_hash': SNAPSHOT}):
        assert list(Draft202012Validator(schema).iter_errors(invalid))
        with pytest.raises(subject.CatalogueWireError):
            subject.validate_candidate(invalid, **bound)


def test_snapshot_and_index_binding_does_not_pretend_raw_same_count_authenticates_source():
    value, bound = scene(); original = raw(value); result = build(value, bound)
    other = {**bound, 'source_snapshot_hash': 'c' * 64}
    with pytest.raises(subject.CatalogueWireError):
        subject.build_catalogue_projection(original, **other)
    other['indexed'] = codec.build_indexed_inputs(bound['input_claims'], other['source_snapshot_hash'])
    with pytest.raises(subject.CatalogueWireError):
        subject.check_catalogue_projection(result, original, **other)
    # The bytes contain no source authority. Only the actual consumer can prove
    # request/capture ownership. A fresh consistent supplied source can decode
    # the same bytes, but receives a different PLATFORM projection binding.
    other_result = subject.build_catalogue_projection(original, **other)
    assert other_result['raw_arguments_sha256'] == result['raw_arguments_sha256']
    assert other_result['projection_hash'] != result['projection_hash']
    assert other_result['source_anchors_verified'] is False


@pytest.mark.parametrize('count', [True, 3.0, 0, codec.MAX_INPUTS + 1])
def test_shape_only_entry_rejects_noninteger_and_overcapacity_counts(count):
    with pytest.raises(subject.CatalogueWireError): subject.schema_for_count(count)


def test_equal_json_with_different_original_bytes_keeps_distinct_capture_projection():
    value, bound = scene(); pretty = raw(value); compact = codec.canonical(value).encode('utf-8')
    assert pretty != compact
    left = subject.build_catalogue_projection(pretty, **bound)
    right = subject.build_catalogue_projection(compact, **bound)
    assert left['wire'] == right['wire'] and left['ledger'] == right['ledger']
    assert left['raw_arguments_sha256'] != right['raw_arguments_sha256']
    with pytest.raises(subject.CatalogueWireError):
        subject.check_catalogue_projection(left, compact, **bound)


@pytest.mark.parametrize('change', [
    lambda v: v.update(input_count=True),
    lambda v: v.update(input_count=8.0),
    lambda v: v.update(schema='report-object-semantic-candidate-wire-v166'),
    lambda v: v.update(source_snapshot_hash=SNAPSHOT),
    lambda v: v['businesses'][0].update(span_refs=['w:p999/s1']),
    lambda v: v['businesses'][0].update(name='原文没有的业务名称'),
    lambda v: v['businesses'][0].update(seed_positions=[4]),
    lambda v: v['businesses'][0].update(key='b2'),
    lambda v: v['businesses'][1].update(name='设备调度'),
    lambda v: v['allocation_groups'][0].update(input_positions=[False, 1, 7]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0.0, 1, 7]),
    lambda v: v['allocation_groups'][0].update(input_positions=[1, 0, 7]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0, 7]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0, 1, 4, 7]),
    lambda v: v['allocation_groups'][0].update(input_positions=[0, 1, 8]),
    lambda v: v['allocation_groups'][0].update(business_keys=['unknown']),
    lambda v: v['allocation_groups'][0].update(question_keys=['unknown']),
    lambda v: v['allocation_groups'][2].update(business_keys=['b1']),
    lambda v: v['allocation_groups'][3].update(business_keys=['b1']),
    lambda v: v['allocation_groups'][-1].update(question_keys=[]),
    lambda v: v['questions'][-1].update(kind='missing_info'),
    lambda v: v['questions'][0].update(input_positions=[0]),
    lambda v: v['questions'][0].update(page=123),
    lambda v: v['questions'][0].update(question=''),
    lambda v: v['merge_suggestions'][0].update(business_keys=['b1', 'b1']),
    lambda v: v['merge_suggestions'][0].update(business_keys=['b1', 'unknown']),
    lambda v: v['merge_suggestions'][0].update(approved=True),
])
def test_invalid_candidate_is_rejected_without_changing_original_answer_or_sources(change):
    value, bound = scene(); change(value)
    original = raw(value); before = codec.canonical([value, bound])
    with pytest.raises(subject.CatalogueWireError) as failure:
        subject.build_catalogue_projection(original, **bound)
    assert failure.value.response_must_be_retained is True
    assert original == raw(value) and codec.canonical([value, bound]) == before


def test_cannot_create_or_normalize_sixty_fifth_business_or_apply_merge_suggestions():
    value, bound = single(65)
    value['businesses'] = [{**business(f'b{i}', f'建议业务{i}', i), 'name_basis': 'inferred'} for i in range(65)]
    value['allocation_groups'] = [group('business', [i], [f'b{i}']) for i in range(65)]
    original = raw(value)
    with pytest.raises(subject.CatalogueWireError): build(value, bound)
    assert raw(value) == original and len(value['businesses']) == 65
    value, bound = scene(); with_merge = build(value, bound)
    value['merge_suggestions'] = []; without_merge = build(value, bound)
    assert with_merge['ledger'] == without_merge['ledger']
    assert [r['name'] for r in with_merge['ledger']['scopes']] == ['设备调度', '资产盘点']


def test_missing_business_details_need_explicit_question_and_empty_catalogue_stays_blocked():
    value, bound = scene(); value['businesses'][1]['purpose'] = ''
    assert build(value, bound)['missing_info_questions'][0]['key'] == 'q_info'
    value['questions'] = [value['questions'][1]]
    value['allocation_groups'][1]['question_keys'] = []
    with pytest.raises(subject.CatalogueWireError): build(value, bound)
    value, bound = single(1); value['businesses'] = []
    value['allocation_groups'] = [group('background', [0])]
    result = build(value, bound)
    assert result['scope_confirmation_blocked'] is True
    assert result['scope_confirmation_blockers'] == ['no_business_candidates']
    assert result['ledger']['scopes'] == []


def test_bad_json_original_catalog_or_resealed_projection_never_becomes_a_candidate():
    value, bound = scene(); original = raw(value); projection = build(value, bound)
    bad = [b'{"schema":"one","schema":"two"}', b'{} trailing', b'{"x":NaN}', b'\xff']
    for content in bad:
        with pytest.raises(subject.CatalogueWireError): subject.parse_wire(content, **bound)
    damaged = copy.deepcopy(bound); damaged['catalog']['index']['w:p1/s1']['text'] = '改写原文'
    with pytest.raises(subject.CatalogueWireError): build(value, damaged)
    projection['ledger']['scopes'][0]['name'] = '改写分派'
    projection['projection_hash'] = codec.digest({k: v for k, v in projection.items() if k != 'projection_hash'})
    with pytest.raises(subject.CatalogueWireError):
        subject.check_catalogue_projection(projection, original, **bound)


def test_literal_business_name_crosses_only_adjacent_real_spans_not_source_blocks_or_gaps():
    value, bound = single(1)
    bound['catalog'] = build_evidence_source('b' * 64, [['w:p1', '设备调度']], max_span_chars=2)
    bound['input_claims'][0]['span_refs'] = ['w:p1/s1', 'w:p1/s2']
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], SNAPSHOT)
    assert build(value, bound)['businesses'][0]['name_basis'] == 'explicit'
    bound['catalog'] = build_evidence_source('b' * 64, [['w:p1', '设备'], ['w:p2', '调度']], max_span_chars=2)
    bound['input_claims'][0]['span_refs'] = ['w:p1/s1', 'w:p2/s1']
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], SNAPSHOT)
    with pytest.raises(subject.CatalogueWireError): build(value, bound)
    bound['catalog'] = build_evidence_source('b' * 64, [['w:p1', '设备其他调度']], max_span_chars=2)
    bound['input_claims'][0]['span_refs'] = ['w:p1/s1', 'w:p1/s3']
    bound['indexed'] = codec.build_indexed_inputs(bound['input_claims'], SNAPSHOT)
    with pytest.raises(subject.CatalogueWireError): build(value, bound)
