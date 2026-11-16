"""Pure public-snapshot text, not collector/source/IAM/PG/model authority proof."""
import copy
import math

import pytest

from generic_agents import task_evidence_packet_text_v199 as text
from generic_agents.typed_payload_codec_v131 import canonical_json


def snapshot():
    fields = [('integer', '整数记录', None), ('float', '温度原数值', '°C'),
        ('negative_zero', '负零记录', None), ('boolean', '布尔记录', None),
        ('zero', '零记录', None), ('null', '明确为空的记录', None),
        ('missing', '未提供的字段', None), ('nested', '原结构化记录', None)]
    attributes = {'integer': 1, 'float': 1.0, 'negative_zero': -0.0, 'boolean': False,
        'zero': 0, 'null': None, 'nested': {'text': '原多行\n正文完整保留', 'values': [1, 1.0, -0.0, False, 0, None]}}
    return {'schema': text.SCHEMA, 'task_title': '同一任务的原证据资料', 'simulation_only': True,
        'evidence_status': 'conflict', 'reference_at': '2026-10-24T15:20:30.123456+08:00',
        'required_fields': [{'key': key, 'label': label, 'unit': unit} for key, label, unit in fields],
        'objects': [{'object_ref': '对象1', 'label': '样地甲', 'archive_attributes': copy.deepcopy(attributes),
            'observations': [{'source_label': '原HTTP模拟来源', 'observed_at': '2026-10-24T07:20:30.123456Z',
                'attributes': {**copy.deepcopy(attributes), 'float': 38.0},
                'anomalies': [{'field': 'float', 'value': 38.0, 'severity': 'warning'}],
                'verification': 'candidate', 'simulation_only': True},
                {'source_label': '原独立人工核对记录', 'observed_at': None,
                    'attributes': {'float': 36.5}, 'anomalies': [],
                    'verification': 'human_confirmed', 'simulation_only': False}]},
            {'object_ref': '对象2', 'label': '样地乙', 'archive_attributes': {}, 'observations': []}],
        'gaps': [{'object_ref': '对象1', 'fields': ['missing'], 'status': '待补充'},
            {'object_ref': '对象2', 'fields': ['float', 'integer'], 'status': '待核对原资料'}],
        'issues': ['两来源数值不同，尚未确认其原因。', '时间未知的记录不能冒充同一采集时刻。\n原说明继续保留。']}


def minimal(*, value=1, present=True):
    return {'schema': text.SCHEMA, 'task_title': '原任务', 'simulation_only': False,
        'evidence_status': 'insufficient', 'reference_at': None,
        'required_fields': [{'key': 'reading', 'label': '读数', 'unit': None}],
        'objects': [{'object_ref': '对象1', 'label': '原对象',
            'archive_attributes': {'reading': value} if present else {}, 'observations': []}],
        'gaps': [], 'issues': []}


def test_whole_packet_keeps_both_objects_all_observations_anomalies_gaps_and_original_time():
    value = snapshot(); before = canonical_json(value)
    result = text.render_packet(value)
    assert type(result) is bytes and len(result) <= 20000
    body = result.decode('utf-8')
    for original in ['样地甲', '样地乙', '原HTTP模拟来源', '原独立人工核对记录',
            '2026-10-24T15:20:30.123456+08:00', '2026-10-24T07:20:30.123456Z',
            '两来源数值不同，尚未确认其原因。', '待核对原资料']:
        assert original in body
    assert '"温度原数值"；值：38.0；单位："°C"；原级别："warning"' in body
    assert canonical_json(value['issues'][1]) in body
    assert '仅工程模拟（SIM）' in body and '观测候选，未经人工确认' in body
    assert '该条来源标记为人工已确认' in body and '不扩大为整体事实或执行授权' in body
    assert '档案记录（与本次观测分列）' in body and '不拿档案值冒充新观测' in body
    assert '不表示全部历史或完整样本' in body
    assert canonical_json(value) == before


@pytest.mark.parametrize('value,spelling', [(1, '1'), (1.0, '1.0'), (-0.0, '-0.0'),
    (False, 'false'), (0, '0'), (None, 'null（明确为空）')])
def test_original_json_number_boolean_zero_and_explicit_null_remain_distinct(value, spelling):
    original = minimal(value=value)
    validated = text.validate_snapshot(original)
    actual = validated['objects'][0]['archive_attributes']['reading']
    assert type(actual) is type(value)
    if type(value) is float and value == 0:
        assert math.copysign(1, actual) == -1
    assert '  "读数"：' + spelling + '；单位：未声明（不推测单位）' in text.render_packet(original).decode('utf-8')


def test_unprovided_attribute_is_not_null_zero_or_current_time():
    value = minimal(present=False)
    body = text.render_packet(value).decode('utf-8')
    assert '"读数"：未提供（缺项；未补用默认值）' in body
    assert '原参考时刻：未提供（时间未知；未补用当前时钟）' in body
    assert '"读数"：0' not in body and '"读数"：null' not in body
    assert value['reference_at'] is None and value['objects'][0]['archive_attributes'] == {}


def test_detached_validation_and_mapping_order_are_lossless_without_changing_list_order():
    original = snapshot(); before = canonical_json(original)
    detached = text.validate_snapshot(original)
    assert detached is not original and detached['objects'] is not original['objects']
    detached['objects'][0]['archive_attributes']['nested']['values'][1] = 9.5
    assert canonical_json(original) == before
    def reverse_mappings(value):
        if type(value) is dict:
            return {key: reverse_mappings(child) for key, child in reversed(list(value.items()))}
        if type(value) is list:
            return [reverse_mappings(child) for child in value]
        return value
    assert text.render_packet(reverse_mappings(original)) == text.render_packet(original)
    reordered = copy.deepcopy(original)
    reordered['objects'][0]['observations'].reverse()
    body = text.render_packet(reordered).decode('utf-8')
    assert body.index('原独立人工核对记录') < body.index('原HTTP模拟来源')


@pytest.mark.parametrize('status', ['sufficient', 'insufficient', 'conflict'])
def test_query_status_never_becomes_fact_verification_or_execution_permission(status):
    value = minimal(); value['evidence_status'] = status
    body = text.render_packet(value).decode('utf-8')
    assert '不等于已核实事实' in body and '不授予执行许可' in body
    assert '本包未标记为SIM；不据此证明现场事实' in body
    assert '业务质量或现场正确性结论' in body
    if status == 'sufficient':
        assert '原查询资料充足（不等于事实已核实）' in body


@pytest.mark.parametrize('fault', ['top_extra', 'observation_extra', 'anomaly_internal_rule_id',
    'unknown_status', 'unknown_verification', 'object_uuid', 'wrong_object_ordinal', 'unit_bool',
    'time_no_offset', 'time_impossible', 'simulation_not_bool', 'simulation_stripped',
    'duplicate_field', 'archive_undeclared_field', 'observation_undeclared_field',
    'anomaly_undeclared_field', 'gap_undeclared_field', 'gap_unknown_object', 'issue_nonstring'])
def test_closed_public_snapshot_rejects_internal_proof_unknown_fields_or_false_labeling(fault):
    value = snapshot(); obj = value['objects'][0]; observation = obj['observations'][0]
    if fault == 'top_extra': value['task_id'] = 'private-internal-identity'
    elif fault == 'observation_extra': observation['headers'] = {'Authorization': 'private-token'}
    elif fault == 'anomaly_internal_rule_id': observation['anomalies'][0]['rule_id'] = 'private-rule-identity'
    elif fault == 'unknown_status': value['evidence_status'] = 'verified_fact'
    elif fault == 'unknown_verification': observation['verification'] = 'confirmed'
    elif fault == 'object_uuid': obj['object_ref'] = '19900000-0000-4000-8000-000000000001'
    elif fault == 'wrong_object_ordinal': obj['object_ref'] = '对象2'
    elif fault == 'unit_bool': value['required_fields'][0]['unit'] = False
    elif fault == 'time_no_offset': observation['observed_at'] = '2026-10-24T07:20:30'
    elif fault == 'time_impossible': value['reference_at'] = '2026-02-30T00:00:00Z'
    elif fault == 'simulation_not_bool': value['simulation_only'] = 1
    elif fault == 'simulation_stripped': value['simulation_only'] = False
    elif fault == 'duplicate_field': value['required_fields'].append(copy.deepcopy(value['required_fields'][0]))
    elif fault == 'archive_undeclared_field': obj['archive_attributes']['internal_hash'] = 'f' * 64
    elif fault == 'observation_undeclared_field': observation['attributes']['secret'] = 'do-not-render'
    elif fault == 'anomaly_undeclared_field': observation['anomalies'][0]['field'] = 'not_requested'
    elif fault == 'gap_undeclared_field': value['gaps'][0]['fields'] = ['not_requested']
    elif fault == 'gap_unknown_object': value['gaps'][0]['object_ref'] = '对象999'
    else: value['issues'] = [{'message': '未约定的内部问题对象'}]
    with pytest.raises(text.PacketTextError) as error:
        text.render_packet(value)
    assert str(error.value) == text.ERROR
    assert all(secret not in str(error.value) for secret in ('private-token', 'do-not-render', 'internal_hash'))


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), b'not-json', {'nested': {1: 'nonstring key'}}, '\ud800', 'contains\x00nul'])
def test_invalid_json_values_are_rejected_without_coercion_or_partial_body(bad):
    value = minimal(value=bad)
    with pytest.raises(text.PacketTextError, match=text.ERROR):
        text.validate_snapshot(value)
    with pytest.raises(text.PacketTextError, match=text.ERROR):
        text.render_packet(value)


def test_cycles_depth_and_huge_integer_do_not_enter_float_conversion_or_silent_repair():
    cycle = []; cycle.append(cycle)
    with pytest.raises(text.PacketTextError): text.render_packet(minimal(value=cycle))
    nested = 1
    for _ in range(24): nested = [nested]
    with pytest.raises(text.PacketTextError): text.render_packet(minimal(value=nested))
    integer = 10 ** 400 + 1
    assert '"读数"：' + str(integer) + '；单位：' in text.render_packet(minimal(value=integer)).decode('utf-8')


@pytest.mark.parametrize('character', ['x', '汉'])
def test_exact_20000_utf8_byte_boundary_is_allowed_and_one_more_byte_refuses_the_whole_packet(character):
    value = minimal(); value['issues'] = ['x']
    initial = text.render_packet(value)
    remaining = 20000 - len(initial)
    width = len(character.encode('utf-8'))
    value['issues'][0] += character * (remaining // width) + 'x' * (remaining % width)
    exact = text.render_packet(value)
    assert len(exact) == 20000 and exact.decode('utf-8').endswith('\n')
    before = canonical_json(value)
    value['issues'][0] += 'x'
    with pytest.raises(text.PacketTextError) as error: text.render_packet(value)
    assert str(error.value) == text.ERROR
    value['issues'][0] = value['issues'][0][:-1]
    assert canonical_json(value) == before and text.render_packet(value) == exact


def test_repeated_source_rows_and_issues_are_not_deduplicated_or_replaced_by_a_summary():
    value = snapshot()
    observation = copy.deepcopy(value['objects'][0]['observations'][0])
    value['objects'][0]['observations'].append(observation)
    value['issues'].append(value['issues'][0])
    body = text.render_packet(value).decode('utf-8')
    assert body.count('："原HTTP模拟来源"') == 2
    assert body.count(canonical_json(value['issues'][0])) == 2
    assert '观测记录 3' in body
