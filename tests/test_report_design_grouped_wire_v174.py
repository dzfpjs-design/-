"""Public synthetic compact-wire checks; no HTTP, PG, provider or quality proof."""
import copy
import hashlib
import json

import pytest

from generic_agents import report_design_grouped_wire_v174 as subject
from generic_agents import report_evidence_reduction_wire_v168 as original
from generic_agents.contracts import digest
from generic_agents.report_object_semantics_v166 import CandidateObjects
from tests.test_report_evidence_reduction_wire_v168 import scene as original_scene, no_external_io


def raw(value, **options):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **options).encode('utf-8')


def scene():
    old, inputs = original_scene()
    groups = [{key: copy.deepcopy(item[key]) for key in
        ('output_kind', 'output_index', 'disposition', 'reason')} | {
        'input_positions': [item['input_position']]} for item in old['reconciliation']]
    return {'result': copy.deepcopy(old['result']), 'reconciliation_groups': groups}, inputs


def build(value, inputs):
    return subject.build_reduction_projection(raw(value), **inputs)


def joined_scene(count=6):
    value, inputs = scene()
    source = inputs['input_items'][inputs['input_order'][0]]
    for i in range(1, count):
        inputs['input_items'][f'map-1/claims/{i}'] = copy.deepcopy(source)
        inputs['input_items'][f'map-1/claims/{i}']['statement'] += f' 合成独立输入{i}，不认证语义。'
    inputs['input_order'] = [f'map-1/claims/{i}' for i in range(count)] + ['map-2/claims/0', 'map-3/claims/0']
    value['reconciliation_groups'][0]['input_positions'] = list(range(count))
    value['reconciliation_groups'][1]['input_positions'] = [count]
    value['reconciliation_groups'][2]['input_positions'] = [count + 1]
    return value, inputs


def test_new_wire_is_independent_and_platform_expansion_preserves_all_original_gates():
    value, inputs = joined_scene(); before = copy.deepcopy((value, inputs)); content = raw(value, indent=2)
    projection = subject.build_reduction_projection(content, **inputs)
    assert subject.DesignGroupedWireV174.model_fields['result'].annotation is CandidateObjects
    assert projection['schema'] == subject.PROJECTION != original.PROJECTION
    assert projection['wire_schema'] == subject.WIRE and projection['wire'] == value
    assert projection['wire_bytes_sha256'] == hashlib.sha256(content).hexdigest()
    assert projection['wire_value_hash'] == digest(value)
    assert projection['reconciliation_groups'] == value['reconciliation_groups']
    assert len(projection['expanded_reconciliation']) == len(inputs['input_order']) == 8
    assert [item['input_position'] for item in projection['expanded_reconciliation']] == list(range(8))
    assert len(value['reconciliation_groups']) == 3
    assert projection['expanded_reconciliation'][0]['reason'] == projection['expanded_reconciliation'][5]['reason']
    # Actual old validator accepts the deterministic platform view; its old
    # projection is not returned or rebranded as the model's original answer.
    old_wire = {'result': value['result'], 'reconciliation': projection['expanded_reconciliation']}
    old_projection = original.build_reduction_projection(raw(old_wire), **inputs)
    assert old_projection['result'] == projection['result']
    assert projection['expansion_hash'] == digest({'schema': subject.EXPANSION,
        'input_order': inputs['input_order'], 'reconciliation': projection['expanded_reconciliation']})
    assert projection['expanded_reconciliation_hash'] == digest(projection['expanded_reconciliation'])
    assert projection['reconciliation_groups_hash'] == digest(value['reconciliation_groups'])
    assert all(projection[key] is False for key in ('provider_delivery_verified', 'semantic_quality_verified',
        'platform_identity_confirmed', 'can_apply', 'can_publish'))
    assert all(not f['required'] for obj in projection['result']['objects'] for f in obj['fields'])
    assert projection['result']['objects'][1]['fields'][0]['kind'] == '整数'
    assert subject.check_reduction_projection(projection, content, **inputs) == projection
    assert (value, inputs) == before


def test_group_order_is_original_but_expansion_is_deterministic_input_order():
    value, inputs = joined_scene(); first = build(value, inputs)
    value['reconciliation_groups'].reverse(); second = build(value, inputs)
    assert second['wire']['reconciliation_groups'] == value['reconciliation_groups']
    assert first['wire_value_hash'] != second['wire_value_hash']
    assert first['reconciliation_groups_hash'] != second['reconciliation_groups_hash']
    assert first['expansion_hash'] == second['expansion_hash']
    assert first['expanded_reconciliation'] == second['expanded_reconciliation']


def test_920_public_synthetic_inputs_have_complete_coverage_without_920_model_rows():
    value, inputs = scene()
    samples = [copy.deepcopy(inputs['input_items'][key]) for key in inputs['input_order']]
    items = {}
    for position in range(920):
        identity = f'map-{position // 80 + 1}/claims/{position % 80}'
        items[identity] = copy.deepcopy(samples[0 if position < 918 else position - 917])
    inputs.update(input_order=list(items), input_items=items)
    value['reconciliation_groups'][0]['input_positions'] = list(range(918))
    value['reconciliation_groups'][1]['input_positions'] = [918]
    value['reconciliation_groups'][2]['input_positions'] = [919]
    projection = build(value, inputs)
    assert len(projection['reconciliation_groups']) == 3
    assert [row['input_position'] for row in projection['expanded_reconciliation']] == list(range(920))
    expanded_wire = {'result': value['result'], 'reconciliation': projection['expanded_reconciliation']}
    assert len(raw(value)) < len(raw(expanded_wire))
    assert projection['wire'] == value and projection['semantic_quality_verified'] is False


@pytest.mark.parametrize('count', [0, True, 1.0, 1921])
def test_expansion_keeps_the_original_strict_nonzero_input_count_limit(count):
    value, _ = scene()
    with pytest.raises(ValueError): subject.expand_groups(value['reconciliation_groups'], count)


@pytest.mark.parametrize('fault', ['missing', 'duplicate_inside', 'duplicate_across', 'out_of_range',
    'negative', 'bool', 'float', 'string', 'unsorted', 'empty_positions', 'duplicate_target', 'empty_groups'])
def test_positions_are_exact_unique_strict_and_group_targets_are_not_split(fault):
    value, inputs = joined_scene(); group = value['reconciliation_groups'][0]
    if fault == 'missing': group['input_positions'].pop()
    elif fault == 'duplicate_inside': group['input_positions'][1] = 0
    elif fault == 'duplicate_across': value['reconciliation_groups'][1]['input_positions'] = [0, 6]
    elif fault == 'out_of_range': group['input_positions'].append(8)
    elif fault == 'negative': group['input_positions'][0] = -1
    elif fault == 'bool': group['input_positions'][0] = False
    elif fault == 'float': group['input_positions'][0] = 0.0
    elif fault == 'string': group['input_positions'][0] = '0'
    elif fault == 'unsorted': group['input_positions'].reverse()
    elif fault == 'empty_positions': group['input_positions'] = []
    elif fault == 'duplicate_target':
        split = copy.deepcopy(group); split['input_positions'] = [5]
        group['input_positions'].pop(); value['reconciliation_groups'].append(split)
    else: value['reconciliation_groups'] = []
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): build(value, inputs)
    assert value == before


@pytest.mark.parametrize('content', [b'', b'\xff', b'[]', b'{}',
    b'{"result":{},"result":{}}', b'{"result":{},"resul\\u0074":{}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}',
    b'{"x":"\\ud800"}', b'{"x":"\\u0000"}', b'{'])
def test_original_json_failures_are_not_repaired(content):
    _, inputs = scene()
    with pytest.raises(ValueError): subject.build_reduction_projection(content, **inputs)


def test_unknown_original_can_only_be_grouped_into_an_unresolved_question():
    value, inputs = scene()
    inputs['input_items'][inputs['input_order'][0]].update(status='unknown', explanation='尚待核对。')
    with pytest.raises(ValueError): build(value, inputs)
    value['result']['questions'].append('请核对第一项原声明；原输入及引用独立保留。')
    value['reconciliation_groups'][0].update(output_kind='questions',
        output_index=len(value['result']['questions']) - 1, disposition='uncertain')
    assert build(value, inputs)['result']['questions'] == value['result']['questions']


def test_inferred_original_is_not_promoted_and_non_global_target_needs_reason():
    value, inputs = scene(); inputs['input_items'][inputs['input_order'][2]].update(
        status='inferred', explanation='合成职责只是推断。')
    with pytest.raises(ValueError): build(value, inputs)
    value['result']['global_claims'][0].update(status='inferred', explanation='仍只是待核推断。')
    assert build(value, inputs)['result']['global_claims'][0]['status'] == 'inferred'
    value, inputs = scene(); inputs['input_items'][inputs['input_order'][0]].update(
        status='inferred', explanation='对象含义尚属推断。')
    with pytest.raises(ValueError): build(value, inputs)
    value['result']['objects'][0]['inference_reason'] = '不能将此对象视为原文明示。'
    assert build(value, inputs)['result']['objects'][0]['inference_reason']


@pytest.mark.parametrize('fault', ['lost_ref', 'lost_scope', 'missing_item', 'changed_source',
    'empty_inputs', 'wrong_target', 'blank_reason', 'bool_target', 'extra_note', 'old_wire'])
def test_original_source_target_capacity_and_closed_contract_are_not_weakened(fault):
    value, inputs = scene()
    if fault == 'lost_ref': inputs['input_items'][inputs['input_order'][0]]['span_refs'].append('o:p1/s1')
    elif fault == 'lost_scope': inputs['allowed_span_refs'].pop()
    elif fault == 'missing_item': inputs['input_items'].pop(inputs['input_order'][0])
    elif fault == 'changed_source': inputs['catalog']['descriptor']['source_hash'] = 'f' * 64
    elif fault == 'empty_inputs': inputs.update(input_order=[], input_items={})
    elif fault == 'wrong_target': value['reconciliation_groups'][0]['output_index'] = 999
    elif fault == 'blank_reason': value['reconciliation_groups'][0]['reason'] = ' '
    elif fault == 'bool_target': value['reconciliation_groups'][0]['output_index'] = False
    elif fault == 'extra_note': value['private_note'] = '不可进入共享输出'
    else:
        value = {'result': value['result'], 'reconciliation': original_scene()[0]['reconciliation']}
    with pytest.raises(ValueError): build(value, inputs)


def test_raw_bytes_and_resealed_projection_tampering_are_independently_rejected():
    value, inputs = scene(); pretty = raw(value, indent=2); compact = raw(value, separators=(',', ':'))
    first = subject.build_reduction_projection(pretty, **inputs)
    second = subject.build_reduction_projection(compact, **inputs)
    assert first['wire_value_hash'] == second['wire_value_hash']
    assert first['wire_bytes_sha256'] != second['wire_bytes_sha256']
    assert first['expanded_reconciliation_hash'] == second['expanded_reconciliation_hash']
    with pytest.raises(ValueError): subject.check_reduction_projection(first, compact, **inputs)
    for field in ('expansion_schema', 'expansion_hash', 'expanded_reconciliation_hash',
            'reconciliation_groups_hash', 'wire_value_hash', 'semantic_quality_verified', 'can_apply'):
        damaged = copy.deepcopy(first)
        damaged[field] = True if field in {'semantic_quality_verified', 'can_apply'} else 'f' * 64
        damaged['projection_hash'] = digest({k: v for k, v in damaged.items() if k != 'projection_hash'})
        with pytest.raises(ValueError): subject.check_reduction_projection(damaged, pretty, **inputs)


def test_unmapped_output_does_not_add_a_new_every_output_must_have_group_rule():
    value, inputs = scene()
    value['result']['questions'].append('这是另一个保留的未决问题；不强迫虚构输入归组。')
    assert build(value, inputs)['result']['questions'] == value['result']['questions']
