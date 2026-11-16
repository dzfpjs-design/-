"""Synthetic literal/shape checks only, not saved-report or quality evidence."""
import copy
import socket
import subprocess

import pytest

from generic_agents import report_object_semantics_v166 as new
from generic_agents.report_evidence_manual_v5 import ManualReviewValueV5
from generic_agents.report_evidence_semantics import validate_evidence_result
from generic_agents.report_evidence_wire_v3 import candidate_with_ids
from generic_agents.contracts import digest
from tests.test_report_evidence_semantics_pure import catalog, allowed, CONTEXT
from tests.test_report_evidence_wire_v3_pure import candidate_wire


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Object semantic validation cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    sources = catalog()
    old = {'schema': 'report-evidence-manual-review-v5', 'result': candidate_wire(sources),
        'note': '原私人理由，保留不改写。'}
    actual = candidate_with_ids(old['result'], lambda prefix, path: prefix + digest(path)[:32])
    actual['schema'] = 'report-evidence-candidate-v4'
    assert validate_evidence_result(actual, sources, allowed(sources), CONTEXT) == actual
    value = new.new_from_value(old)
    return old, value, {'catalog': sources, 'allowed_span_refs': allowed(sources), 'context': copy.deepcopy(CONTEXT)}


def validate(value, inputs):
    return new.validate_value(value, inputs['catalog'], inputs['allowed_span_refs'], inputs['context'])


def test_fork_is_lossless_and_never_rewrites_old_value_or_claims_confirmation():
    old, value, inputs = scene()
    before = copy.deepcopy((old, value, inputs))
    restored = copy.deepcopy(value['result']); restored['schema'] = old['result']['schema']
    for obj in restored['objects']:
        identity = obj.pop('business_identity')
        assert identity['status'] == 'not_established' and identity['field_labels'] == []
        assert identity['citations'] == [] and '不能据此认定原文没有标识' in identity['explanation']
    assert restored == old['result'] and value['note'] == old['note']
    assert all(item['acknowledge_platform_identity'] is False for item in value['object_identity_confirmations'])
    assert validate(value, inputs) == value
    with pytest.raises(ValueError):
        new.confirm_value(value)
    assert (old, value, inputs) == before


def test_all_optional_and_numeric_only_objects_validate_without_a_dummy_required_field():
    old, value, inputs = scene()
    for obj in value['result']['objects']:
        for field in obj['fields']:
            field['required'] = False
    value['result']['objects'][1]['fields'] = [value['result']['objects'][1]['fields'][1]]
    before = copy.deepcopy(value)
    assert validate(value, inputs) == before and value == before
    assert all(not field['required'] for obj in value['result']['objects'] for field in obj['fields'])
    assert all(field['kind'] != '文本' for field in value['result']['objects'][1]['fields'])
    old_shape = copy.deepcopy(old)
    old_shape['result']['objects'][0]['fields'][0]['required'] = False
    with pytest.raises(ValueError):
        ManualReviewValueV5.model_validate(old_shape)


def test_referenced_optional_business_label_never_becomes_required_or_unique():
    _, value, inputs = scene()
    obj = value['result']['objects'][0]
    obj['fields'][0]['required'] = False
    obj['business_identity'] = {'status': 'referenced', 'field_labels': ['名称'],
        'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': '人工引用这个原字段，未认定其业务唯一性。'}
    for item in value['object_identity_confirmations']:
        item['acknowledge_platform_identity'] = True
    before = copy.deepcopy(value)
    assert new.confirm_value(validate(value, inputs)) == before and value == before
    assert obj['fields'][0]['required'] is False
    assert not {'unique', 'primary_key', 'external_id', 'record_id'} & set(obj)


@pytest.mark.parametrize('fault', ['empty_fields', 'duplicate_field', 'duplicate_object',
    'existing_name', 'blank_name', 'unknown_field', 'duplicate_identity_field',
    'referenced_without_cite', 'referenced_empty', 'not_established_with_field',
    'blank_identity_explanation', 'extra_identity_authority'])
def test_new_object_boundary_is_closed_without_old_required_text_gate(fault):
    _, value, inputs = scene(); obj = value['result']['objects'][0]
    if fault == 'empty_fields': obj['fields'] = []
    elif fault == 'duplicate_field': obj['fields'].append(copy.deepcopy(obj['fields'][0]))
    elif fault == 'duplicate_object': value['result']['objects'][1]['name'] = obj['name']
    elif fault == 'existing_name': inputs['context']['object_names'] = [obj['name']]
    elif fault == 'blank_name': obj['name'] = '  '
    elif fault == 'unknown_field': obj['business_identity'].update(status='referenced', field_labels=['不存在'], citations=obj['citations'])
    elif fault == 'duplicate_identity_field': obj['business_identity'].update(status='referenced', field_labels=['名称', '名称'], citations=obj['citations'])
    elif fault == 'referenced_without_cite': obj['business_identity'].update(status='referenced', field_labels=['名称'])
    elif fault == 'referenced_empty': obj['business_identity']['status'] = 'referenced'
    elif fault == 'not_established_with_field': obj['business_identity']['field_labels'] = ['名称']
    elif fault == 'blank_identity_explanation': obj['business_identity']['explanation'] = ' '
    else: obj['business_identity']['unique'] = True
    with pytest.raises(ValueError): validate(value, inputs)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'other_object', 'integer_ack', 'string_ack'])
def test_identity_acknowledgements_cannot_be_omitted_aliased_or_coerced(fault):
    _, value, inputs = scene(); acknowledgements = value['object_identity_confirmations']
    if fault == 'missing': acknowledgements.pop()
    elif fault == 'duplicate': acknowledgements.append(copy.deepcopy(acknowledgements[0]))
    elif fault == 'other_object': acknowledgements[0]['object_name'] = '别的对象'
    elif fault == 'integer_ack': acknowledgements[0]['acknowledge_platform_identity'] = 1
    else: acknowledgements[0]['acknowledge_platform_identity'] = 'true'
    with pytest.raises(ValueError): validate(value, inputs)


@pytest.mark.parametrize('fault', ['source', 'catalog', 'outside_scope', 'duplicate_cite',
    'ungrounded_field', 'wrong_unit', 'identity_outside_scope'])
def test_original_span_scope_grounding_and_exact_unit_rules_remain(fault):
    _, value, inputs = scene(); obj = value['result']['objects'][1]; field = obj['fields'][1]
    if fault == 'source': value['result']['source_hash'] = 'f' * 64
    elif fault == 'catalog': value['result']['catalog_hash'] = 'f' * 64
    elif fault == 'outside_scope': field['citations'] = [{'span_ref': 'w:p3/s1'}]
    elif fault == 'duplicate_cite': field['citations'] *= 2
    elif fault == 'ungrounded_field': field.update(citations=[], inference_reason='')
    elif fault == 'wrong_unit': field['unit'] = '亩'
    else: obj['business_identity'].update(status='referenced', field_labels=['名称'], citations=[{'span_ref': 'w:p3/s1'}])
    with pytest.raises(ValueError): validate(value, inputs)


@pytest.mark.parametrize('fault', ['self_step', 'bool_index', 'outside_step',
    'same_participant', 'missing_relation_type', 'unproved_cardinality', 'unknown_without_question'])
def test_business_local_references_relationship_and_uncertainty_checks_remain(fault):
    _, value, inputs = scene(); business = value['result']['businesses'][0]
    if fault == 'self_step': business['steps'][0]['next_index'] = 0
    elif fault == 'bool_index': business['steps'][0]['next_index'] = True
    elif fault == 'outside_step': business['steps'][0]['next_index'] = 99
    elif fault == 'same_participant': business['interactions'][0]['to_participant_index'] = 0
    elif fault == 'missing_relation_type': value['result']['relationships'][0]['target_type'] = '未声明对象'
    elif fault == 'unproved_cardinality': value['result']['relationships'][0]['cardinality'] = 'one_per_source'
    else: value['result']['global_claims'][0].update(status='unknown', explanation='尚不能核对')
    with pytest.raises(ValueError): validate(value, inputs)


def test_accepted_optional_values_are_copied_without_mutable_aliases_or_lost_references():
    _, value, inputs = scene()
    value['result']['objects'][0]['fields'][0]['required'] = False
    original = copy.deepcopy(value)
    checked = validate(value, inputs)
    checked['result']['objects'][0]['fields'][0]['citations'].clear()
    checked['result']['businesses'][0]['steps'][0]['next_index'] = None
    assert value == original


@pytest.mark.parametrize('fault', ['old_tag', 'extra_top', 'oversize_note', 'empty_question'])
def test_new_family_never_falls_back_to_old_tags_or_truncates_human_values(fault):
    _, value, inputs = scene()
    if fault == 'old_tag': value['result']['schema'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'extra_top': value['can_publish'] = True
    elif fault == 'oversize_note': value['note'] = '字' * 12001
    else: value['result']['questions'] = ['']
    with pytest.raises(ValueError): validate(value, inputs)
