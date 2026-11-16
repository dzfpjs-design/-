"""Public synthetic pure contracts only, not origin/PG/instance acceptance.

Tests call the real new type and JSON Schema validators. Root executes them;
these fixtures contain no real report or evidence-authority assertion.
"""
import copy
import hashlib

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from generic_agents import object_type_contract_v170 as new


def key(prefix, label):
    return prefix + '_' + hashlib.sha256(label.encode('utf-8')).hexdigest()[:16]


def value():
    return {'schema': 'report-object-type-v170', 'name': '核查记录',
        'fields': [
            {'label': '数量', 'kind': '整数', 'required': False, 'unit': '株'},
            {'label': '面积', 'kind': '数值', 'required': False, 'unit': '平方米'}],
        'business_identity': {'status': 'not_established', 'field_labels': [], 'citations': [],
            'explanation': '业务标识尚未明确，不能推定原文没有业务编号。'},
        'platform_identity_confirmation': {'object_name': '核查记录',
            'acknowledge_platform_identity': True}}


def validator(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_numeric_only_all_optional_type_is_exact_and_never_needs_a_dummy_name():
    original = value(); before = copy.deepcopy(original)
    checked = new.check_object_type(original)
    assert checked == original == before
    assert set(checked) == {'schema', 'name', 'fields', 'business_identity', 'platform_identity_confirmation'}
    assert all(field['required'] is False and field['kind'] != '文本' for field in checked['fields'])
    schema = new.build_attribute_schema(original)
    assert schema == {'type': 'object', 'title': original['name'], 'additionalProperties': False,
        'required': [], 'properties': {
            key('field', '数量'): {'type': 'integer', 'title': '数量', 'unit': '株'},
            key('field', '面积'): {'type': 'number', 'title': '面积', 'unit': '平方米'}}}
    assert list(validator(schema).iter_errors({})) == []
    assert original == before and len(original['fields']) == 2


def test_referenced_business_identity_does_not_make_a_field_required_unique_or_a_value():
    original = value()
    original['business_identity'] = {'status': 'referenced', 'field_labels': ['数量'],
        'citations': [{'span_ref': 'w:p4/s1'}],
        'explanation': '仅引用已有字段说明业务标识，尚未建立唯一性。'}
    before = copy.deepcopy(original)
    checked = new.check_object_type(original)
    contract = new.object_contract(original)
    assert checked == before and contract['business_identity'] == before['business_identity']
    assert contract['schema']['required'] == []
    assert list(validator(contract['schema']).iter_errors({})) == []
    assert set(contract) == {'key', 'name', 'fields', 'schema', 'business_identity', 'platform_identity_confirmation'}
    assert contract['key'] == key('object', original['name'])
    assert contract['fields'] == [{'key': key('field', field['label']), **field} for field in original['fields']]
    assert not {'unique', 'primary_key', 'external_id', 'record_id', 'source_verified', 'can_compile'} & set(contract)
    assert original == before
    # The span is only syntax here. A real issued origin/source verifier, not
    # this pure module or this public fixture, must establish its authority.


def test_only_explicit_required_fields_enter_the_json_schema_required_list():
    original = value()
    original['fields'].extend([
        {'label': '备注', 'kind': '文本', 'required': False, 'unit': None},
        {'label': '确认说明', 'kind': '文本', 'required': True, 'unit': None}])
    schema = new.build_attribute_schema(original)
    assert schema['required'] == [key('field', '确认说明')]
    assert schema['properties'][key('field', '备注')] == {'type': 'string', 'title': '备注', 'maxLength': 2000}
    assert schema['properties'][key('field', '确认说明')] == {
        'type': 'string', 'title': '确认说明', 'maxLength': 2000, 'minLength': 1, 'pattern': r'\S'}
    check = validator(schema)
    assert list(check.iter_errors({}))
    assert list(check.iter_errors({key('field', '确认说明'): '   '}))
    assert list(check.iter_errors({key('field', '确认说明'): '已记录'})) == []


@pytest.mark.parametrize('kind,unit,valid,invalid', [
    ('文本', None, '原文字段', 12), ('整数', '株', 2, True),
    ('数值', '平方米', 2.5, '2.5'), ('是否', None, False, 0),
    ('日期', None, '2026-10-22', '2026-99-99')])
def test_declared_types_and_exact_units_drive_real_schema_validation(kind, unit, valid, invalid):
    original = value()
    original['fields'] = [{'label': '原字段', 'kind': kind, 'required': False, 'unit': unit}]
    before = copy.deepcopy(original)
    schema = new.build_attribute_schema(original)
    prop = schema['properties'][key('field', '原字段')]
    assert ('unit' not in prop) if unit is None else prop['unit'] == unit
    check = validator(schema)
    assert list(check.iter_errors({})) == []
    assert list(check.iter_errors({key('field', '原字段'): valid})) == []
    assert list(check.iter_errors({key('field', '原字段'): invalid}))
    assert list(check.iter_errors({key('field', '原字段'): None})), 'Optional must not silently become nullable'
    assert list(check.iter_errors({'extra': valid})), 'Unknown attributes remain forbidden'
    assert original == before


def test_json_schema_integer_accepts_integral_float_without_rewriting_the_attribute():
    attributes = {key('field', '数量'): 1.0}
    assert list(validator(new.build_attribute_schema(value())).iter_errors(attributes)) == []
    assert type(attributes[key('field', '数量')]) is float


@pytest.mark.parametrize('fault', [
    'missing-field-key', 'string-required', 'integer-required', 'unknown-kind',
    'unit-integer', 'unit-bool', 'unit-empty', 'unit-space', 'unit-leading-space',
    'unit-trailing-space', 'text-unit', 'field-leading-space', 'name-leading-space',
    'empty-fields', 'duplicate-field', 'ack-false', 'ack-integer', 'ack-string',
    'ack-wrong-name', 'ack-extra', 'unknown-identity', 'unknown-identity-field',
    'referenced-no-citation', 'identity-unestablished-with-fields', 'identity-blank-explanation',
    'extra-private-note', 'extra-field-citation', 'old-marker', 'missing-identity',
    'nul', 'nonfinite', 'tuple-fields'])
def test_strict_closed_contract_rejects_coercion_missing_identity_or_silent_repairs(fault):
    original = value(); field = original['fields'][0]; ack = original['platform_identity_confirmation']
    identity = original['business_identity']
    if fault == 'missing-field-key': field.pop('unit')
    elif fault == 'string-required': field['required'] = 'false'
    elif fault == 'integer-required': field['required'] = 0
    elif fault == 'unknown-kind': field['kind'] = '字符串'
    elif fault == 'unit-integer': field['unit'] = 1
    elif fault == 'unit-bool': field['unit'] = False
    elif fault == 'unit-empty': field['unit'] = ''
    elif fault == 'unit-space': field['unit'] = ' '
    elif fault == 'unit-leading-space': field['unit'] = ' 株'
    elif fault == 'unit-trailing-space': field['unit'] = '株 '
    elif fault == 'text-unit': field['kind'] = '文本'
    elif fault == 'field-leading-space': field['label'] = ' 数量'
    elif fault == 'name-leading-space': original['name'] = ' 核查记录'
    elif fault == 'empty-fields': original['fields'] = []
    elif fault == 'duplicate-field': original['fields'].append(copy.deepcopy(field))
    elif fault == 'ack-false': ack['acknowledge_platform_identity'] = False
    elif fault == 'ack-integer': ack['acknowledge_platform_identity'] = 1
    elif fault == 'ack-string': ack['acknowledge_platform_identity'] = 'true'
    elif fault == 'ack-wrong-name': ack['object_name'] = '别的对象'
    elif fault == 'ack-extra': ack['unique'] = True
    elif fault == 'unknown-identity': identity['status'] = 'unique'
    elif fault == 'unknown-identity-field': identity.update(status='referenced', field_labels=['不存在'], citations=[{'span_ref': 'w:p4/s1'}])
    elif fault == 'referenced-no-citation': identity.update(status='referenced', field_labels=['数量'])
    elif fault == 'identity-unestablished-with-fields': identity['field_labels'] = ['数量']
    elif fault == 'identity-blank-explanation': identity['explanation'] = ' '
    elif fault == 'extra-private-note': original['note'] = '不得共享的私人说明'
    elif fault == 'extra-field-citation': field['citations'] = [{'span_ref': 'w:p4/s1'}]
    elif fault == 'old-marker': original['schema'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'missing-identity': original.pop('business_identity')
    elif fault == 'nul': identity['explanation'] = '未知\x00说明'
    elif fault == 'nonfinite': field['unit'] = float('nan')
    else: original['fields'] = tuple(original['fields'])
    before = copy.deepcopy(original)
    for function in (new.check_object_type, new.build_attribute_schema, new.object_contract):
        with pytest.raises((ValueError, TypeError)):
            function(original)
    if fault != 'nonfinite':
        assert original == before
    else:
        assert original['fields'][0]['unit'] is before['fields'][0]['unit']


def test_returned_values_schema_and_contract_have_no_mutable_aliases():
    original = value(); before = copy.deepcopy(original)
    checked = new.check_object_type(original)
    schema = new.build_attribute_schema(original)
    contract = new.object_contract(original)
    checked['fields'][0]['unit'] = '改写单位'
    checked['business_identity']['explanation'] = '改写身份'
    checked['platform_identity_confirmation']['object_name'] = '另一个对象'
    schema['properties'][key('field', '数量')]['type'] = 'string'
    schema['required'].append(key('field', '数量'))
    contract['fields'][0]['required'] = True
    contract['schema']['properties'][key('field', '面积')]['unit'] = '改写单位'
    contract['business_identity']['field_labels'].append('数量')
    assert original == before
    assert new.check_object_type(original) == before
    assert new.object_contract(original)['schema'] == new.build_attribute_schema(original)
    assert new.build_attribute_schema(original)['properties'][key('field', '数量')]['type'] == 'integer'


@pytest.mark.parametrize('fault', ['field-limit', 'label-limit', 'unit-limit', 'name-limit', 'bad-reference'])
def test_existing_field_capacity_and_identity_reference_syntax_remain(fault):
    original = value()
    if fault == 'field-limit':
        original['fields'] = [{'label': '字段' + str(index), 'kind': '整数', 'required': False, 'unit': None}
            for index in range(41)]
    elif fault == 'label-limit': original['fields'][0]['label'] = '字' * 81
    elif fault == 'unit-limit': original['fields'][0]['unit'] = '字' * 41
    elif fault == 'name-limit': original['name'] = '字' * 101
    else:
        original['business_identity'].update(status='referenced', field_labels=['数量'], citations=[{'span_ref': 'missing-span'}])
    with pytest.raises((ValueError, TypeError)):
        new.check_object_type(original)


def test_legacy_required_text_contract_is_still_independent_and_rejects_this_shape():
    from generic_agents.instance_compiler import ObjectType
    original = value()
    with pytest.raises(ValueError):
        ObjectType.model_validate({'name': original['name'], 'fields': original['fields']})
    assert new.check_object_type(original) == original
