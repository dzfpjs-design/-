"""Pure public synthetic shapes, not real report/ledger/adoption evidence.

No provider response, successful Job, source authority or human approval is
manufactured. The synthetic origin only tests shape/cross-binding. Root alone
executes these tests; real catalog/command/audit verification remains external.
"""
import copy
import json
import socket
import subprocess
from uuid import UUID, uuid5

import pytest

from generic_agents.contracts import digest
from generic_agents import report_object_configuration_v169 as subject
from generic_agents import report_full_semantic_configuration_v1 as legacy
from generic_agents import report_evidence_wire_v3 as old_wire
from generic_agents.report_object_semantics_v166 import CANDIDATE, SCHEMA as MANUAL_SCHEMA
from tests.test_report_evidence_semantics_pure import catalog
from tests.test_report_evidence_wire_v3_pure import candidate_wire


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure V169 configuration checks cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def uid(number):
    return '169abc00-0000-4000-8000-' + f'{number:012x}'


def scene():
    sources = catalog()
    result = candidate_wire(sources)
    result['schema'] = CANDIDATE
    result['domain'] = ' 原领域🙂\n'
    result['questions'] = ['业务标识仍待原文及核查人明确。']
    for obj in result['objects']:
        obj['business_identity'] = {'status': 'not_established', 'field_labels': [],
            'citations': [], 'explanation': '尚未明确业务标识，不代表原文没有标识。'}
        for field in obj['fields']:
            field['required'] = False
    result['objects'][1]['fields'] = [result['objects'][1]['fields'][1]]
    confirmations = [{'object_name': obj['name'], 'acknowledge_platform_identity': True}
        for obj in result['objects']]
    private_value_hash = digest({'schema': MANUAL_SCHEMA, 'result': result,
        'object_identity_confirmations': confirmations, 'note': '私人核查理由，只能保留摘要。'})
    origin = {'schema': subject.ORIGIN_SCHEMA, 'report_id': uid(2), 'actor_id': uid(3),
        'workspace_id': uid(4), 'revision_id': uid(5), 'revision_version': 4,
        'revision_record_hash': '1' * 64, 'basis_hash': '2' * 64,
        'confirmed_command_id': uid(6), 'confirmed_command_hash': '3' * 64,
        'confirmed_wire_sha256': '4' * 64, 'confirmed_value_hash': private_value_hash,
        'confirmed_audit_id': uid(7), 'confirmed_audit_hash': '5' * 64}
    context = {'handoff_id': uid(1), 'report_id': uid(2), 'report_revision': 8,
        'review_receipt_hash': origin['confirmed_command_hash'], 'manual_value_hash': private_value_hash,
        'model_result_hash': '6' * 64, 'source_hash': result['source_hash'],
        'catalog_hash': result['catalog_hash'], 'context_hash': '7' * 64}
    return result, confirmations, origin, context


def build(parts=None):
    result, confirmations, origin, context = scene() if parts is None else parts
    return subject.build_design(result, confirmations, origin, **context)


def reseal(value):
    value['design_hash'] = digest({key: item for key, item in value.items() if key != 'design_hash'})


def test_complete_optional_candidate_and_opaque_manual_hash_are_preserved_without_private_note(monkeypatch):
    parts = scene(); before = copy.deepcopy(parts)
    def old_path(*args, **kwargs):
        pytest.fail('V169 cannot enter an old candidate/object conversion')
    monkeypatch.setattr(legacy, '_result', old_path)
    monkeypatch.setattr(old_wire, 'candidate_with_ids', old_path)
    design = build(parts)
    assert set(design) == subject.DESIGN_KEYS and len(design) == 16
    assert design['schema'] == subject.SCHEMA
    assert design['execution_policy'] == subject.POLICY == 'requires_full_semantic_runtime_v1'
    assert design['result'] == parts[0] and design['review_origin'] == parts[2]
    assert design['object_identity_confirmations'] == parts[1]
    assert design['manual_value_hash'] == parts[3]['manual_value_hash']
    assert all(field['required'] is False for obj in design['result']['objects'] for field in obj['fields'])
    assert [field['kind'] for field in design['result']['objects'][1]['fields']] == ['整数']
    assert design['result']['objects'][1]['fields'][0]['unit'] == '株'
    assert 'note' not in json.dumps(design, ensure_ascii=False)
    assert subject.check_design(design, expected_result=parts[0], expected_design_hash=design['design_hash'],
        **parts[3]) == design
    assert build(parts) == design and parts == before
    design['result']['objects'][0]['fields'][0]['citations'].clear()
    design['review_origin']['revision_version'] = 99
    assert parts == before


def test_all_binding_kinds_use_independent_new_family_uuid_domains():
    design = build(); all_ids = []
    for bi, binding in enumerate(design['business_bindings']):
        assert set(binding) == {'candidate_index', 'name', 'step_ids', 'participant_ids',
            'interaction_ids', 'condition_ids', 'coordination_ids'}
        for kind in ('step', 'participant', 'interaction', 'condition', 'coordination'):
            expected = [str(uuid5(UUID(design['handoff_id']), f'{subject.SCHEMA}/{kind}/{bi}/{index}'))
                for index in range(len(binding[kind + '_ids']))]
            old = [str(uuid5(UUID(design['handoff_id']), f'{legacy.SCHEMA}/{kind}/{bi}/{index}'))
                for index in range(len(binding[kind + '_ids']))]
            assert binding[kind + '_ids'] == expected
            assert not set(expected) & set(old)
            all_ids.extend(expected)
    assert len(all_ids) == len(set(all_ids))


def test_derived_flow_preserves_null_zero_cycles_and_branch_without_execution_grant():
    parts = scene(); business = parts[0]['businesses'][0]
    business['steps'][0].update(branch_condition=' 满足原条件时 ', else_index=1)
    business['steps'][1]['next_index'] = 0
    design = build(parts); before = copy.deepcopy(design)
    flow = subject.derive_flows(design)[business['name']]
    ids = design['business_bindings'][0]['step_ids']
    assert flow['steps'][0]['nextId'] == flow['steps'][0]['elseId'] == ids[1]
    assert flow['steps'][1]['nextId'] == ids[0] and flow['steps'][1]['elseId'] == 'end'
    assert flow['steps'][0]['condition'] == business['steps'][0]['branch_condition']
    assert flow['confirmedAt'] is flow['confirmedSignature'] is None
    assert flow['fullSemanticSource'] == {'schema': subject.FLOW_REF, 'handoff_id': design['handoff_id'],
        'design_hash': design['design_hash'], 'candidate_index': 0, 'business_hash': digest(business)}
    assert subject.SIGNATURE == 'report-object-semantic-signature-v169'
    assert design == before


def test_origin_is_closed_detached_and_has_no_self_authenticated_ledger_claim():
    origin = scene()[2]; before = copy.deepcopy(origin)
    checked = subject.check_origin(origin)
    assert checked == origin and set(checked) == subject.ORIGIN_KEYS
    checked['actor_id'] = uid(99)
    assert origin == before
    # Another shape-valid actor is not proof of authority; the caller must read
    # the actual command/audit, or pin the expected complete design hash.
    assert subject.check_origin(checked) == checked


@pytest.mark.parametrize('fault', ['family', 'uuid_case', 'actor_int', 'version_bool',
    'version_zero', 'hash_case', 'missing_hash', 'private_note'])
def test_origin_requires_exact_uuid_hash_integer_and_closed_fields(fault):
    origin = scene()[2]
    if fault == 'family': origin['schema'] = 'report-full-semantic-design-v1'
    elif fault == 'uuid_case': origin['actor_id'] = origin['actor_id'].upper()
    elif fault == 'actor_int': origin['actor_id'] = 1
    elif fault == 'version_bool': origin['revision_version'] = True
    elif fault == 'version_zero': origin['revision_version'] = 0
    elif fault == 'hash_case': origin['basis_hash'] = 'A' * 64
    elif fault == 'missing_hash': origin.pop('confirmed_audit_hash')
    else: origin['note'] = '私人核查理由不能共享'
    before = copy.deepcopy(origin)
    with pytest.raises(ValueError): subject.check_origin(origin)
    assert origin == before


@pytest.mark.parametrize('key', ['report_id', 'confirmed_command_hash', 'confirmed_value_hash'])
def test_origin_report_command_and_complete_manual_hash_must_match_context(key):
    parts = scene()
    parts[2][key] = uid(99) if key == 'report_id' else 'f' * 64
    assert subject.check_origin(parts[2]) == parts[2]
    before = copy.deepcopy(parts)
    with pytest.raises(ValueError, match='origin_context'): build(parts)
    assert parts == before


@pytest.mark.parametrize('key,bad', [('report_revision', True), ('report_revision', 1),
    ('handoff_id', 'not-uuid'), ('context_hash', 'A' * 64),
    ('source_hash', 'f' * 64), ('catalog_hash', 'f' * 64)])
def test_context_types_and_original_source_identity_are_not_inferred_or_coerced(key, bad):
    parts = scene(); parts[3][key] = bad; before = copy.deepcopy(parts)
    with pytest.raises(ValueError): build(parts)
    assert parts == before


@pytest.mark.parametrize('fault', ['old_family', 'policy', 'id', 'bool_index', 'extra'])
def test_resealing_modified_design_metadata_does_not_bypass_cold_rebuild(fault):
    design = build()
    if fault == 'old_family': design['schema'] = legacy.SCHEMA
    elif fault == 'policy': design['execution_policy'] = 'runnable'
    elif fault == 'id': design['business_bindings'][0]['step_ids'][0] = uid(99)
    elif fault == 'bool_index': design['business_bindings'][0]['candidate_index'] = False
    else: design['business_bindings'][0]['note'] = '私人字段不能进入绑定'
    reseal(design)
    with pytest.raises(ValueError): subject.check_design(design)


@pytest.mark.parametrize('fault', ['required_int', 'text_unit', 'business_duplicate', 'self_edge',
    'outside_step', 'bool_step', 'else_without_condition', 'same_participant', 'duplicate_indices',
    'outside_indices', 'unknown_without_question', 'identity_unknown_field', 'duplicate_cite', 'old_candidate'])
def test_new_objects_and_common_local_business_structure_remain_strict(fault):
    parts = scene(); result = parts[0]; business = result['businesses'][0]
    if fault == 'required_int': result['objects'][0]['fields'][0]['required'] = 0
    elif fault == 'text_unit': result['objects'][0]['fields'][0]['unit'] = '株'
    elif fault == 'business_duplicate': result['businesses'].append(copy.deepcopy(business))
    elif fault == 'self_edge': business['steps'][0]['next_index'] = 0
    elif fault == 'outside_step': business['steps'][0]['next_index'] = 99
    elif fault == 'bool_step': business['steps'][0]['next_index'] = False
    elif fault == 'else_without_condition': business['steps'][0]['else_index'] = 1
    elif fault == 'same_participant': business['interactions'][0]['to_participant_index'] = 0
    elif fault == 'duplicate_indices': business['conditions'][0]['step_indices'] = [0, 0]
    elif fault == 'outside_indices': business['coordination'][0]['step_indices'] = [0, 99]
    elif fault == 'unknown_without_question':
        result['global_claims'][0].update(status='unknown', explanation='尚待核对')
        result['questions'] = []
    elif fault == 'identity_unknown_field':
        result['objects'][0]['business_identity'].update(status='referenced', field_labels=['不存在'],
            citations=[{'span_ref': 'w:p1/s1'}])
    elif fault == 'duplicate_cite': result['objects'][0]['business_identity']['citations'] = [
        {'span_ref': 'w:p1/s1'}, {'span_ref': 'w:p1/s1'}]
    else: result['schema'] = old_wire.CANDIDATE_WIRE
    before = copy.deepcopy(parts)
    with pytest.raises(ValueError): build(parts)
    assert parts == before


@pytest.mark.parametrize('fault', ['false', 'missing', 'duplicate', 'integer', 'other_object'])
def test_each_object_requires_exact_true_platform_identity_confirmation(fault):
    parts = scene(); confirmations = parts[1]
    if fault == 'false': confirmations[0]['acknowledge_platform_identity'] = False
    elif fault == 'missing': confirmations.pop()
    elif fault == 'duplicate': confirmations.append(copy.deepcopy(confirmations[0]))
    elif fault == 'integer': confirmations[0]['acknowledge_platform_identity'] = 1
    else: confirmations[0]['object_name'] = '别的对象'
    before = copy.deepcopy(parts)
    with pytest.raises(ValueError): build(parts)
    assert parts == before


def test_external_expected_hash_result_and_context_reject_valid_resealed_changes():
    parts = scene(); original = build(parts)
    parts[0]['global_claims'][0]['statement'] += '后续独立修改'
    changed = build(parts)
    assert subject.check_design(changed) == changed  # Integrity is not authority.
    with pytest.raises(ValueError): subject.check_design(changed, expected_result=original['result'])
    with pytest.raises(ValueError): subject.check_design(changed, expected_design_hash=original['design_hash'])
    parts = scene(); parts[2]['actor_id'] = uid(99); changed = build(parts)
    assert subject.check_design(changed) == changed
    with pytest.raises(ValueError): subject.check_design(changed, expected_design_hash=original['design_hash'])
    bad_context = copy.deepcopy(parts[3]); bad_context['manual_value_hash'] = 'f' * 64
    with pytest.raises(ValueError): subject.check_design(original, **bad_context)
    with pytest.raises(ValueError): subject.check_design(original, source_hash=original['source_hash'])


def test_explicit_new_family_dispatch_preserves_identity_without_old_conversion_or_private_fields(monkeypatch):
    parts = scene(); original = build(parts)
    def old_path(*args, **kwargs):
        pytest.fail('Explicit V169 dispatch must not enter old candidate/object conversion')
    monkeypatch.setattr(legacy, '_result', old_path)
    monkeypatch.setattr(legacy, 'candidate_with_ids', old_path)
    monkeypatch.setattr(old_wire, 'candidate_with_ids', old_path)
    checked = legacy.check_design(original)
    assert checked == original and checked['schema'] == subject.SCHEMA
    flows = legacy.derive_flows(original)
    assert flows == subject.derive_flows(original)
    name = original['business_bindings'][0]['name']
    configuration = {'fullSemanticDesigns': [original], 'flows': flows}
    signature = legacy.signature_context(configuration, flows[name])
    assert signature['schema'] == subject.SIGNATURE
    assert signature['design_hash'] == original['design_hash']
    assert flows[name]['fullSemanticSource']['schema'] == subject.FLOW_REF
    relabeled = copy.deepcopy(original); relabeled['schema'] = legacy.SCHEMA
    reseal(relabeled)
    with pytest.raises(ValueError): legacy.check_design(relabeled)
    mixed = copy.deepcopy(configuration)
    mixed['flows'][name]['fullSemanticSource']['schema'] = legacy.FLOW_REF
    with pytest.raises(ValueError): legacy.signature_context(mixed, mixed['flows'][name])
    for position in ('result', 'review_origin', 'design'):
        if position == 'design':
            invalid = copy.deepcopy(original); invalid['note'] = '禁止共享的说明'
            reseal(invalid)
            with pytest.raises(ValueError): subject.check_design(invalid)
        else:
            invalid_parts = scene()
            invalid_parts[0 if position == 'result' else 2]['note'] = '禁止共享的说明'
            with pytest.raises(ValueError): build(invalid_parts)


def test_optional_referenced_business_identity_and_original_unit_never_become_unique_or_required():
    parts = scene(); obj = parts[0]['objects'][0]
    obj['business_identity'] = {'status': 'referenced', 'field_labels': ['名称'],
        'citations': [{'span_ref': 'w:p1/s1'}], 'explanation': '引用已有字段，不代表业务唯一性。'}
    design = build(parts)
    assert design['result']['objects'][0] == obj
    assert obj['fields'][0]['required'] is False
    assert design['result']['objects'][1]['fields'][0]['unit'] == '株'
    assert not {'unique', 'primary_key', 'external_id', 'record_id'} & set(obj)


def test_oversize_result_is_rejected_whole_instead_of_trimming_optional_fields():
    parts = scene(); prototype = parts[0]['businesses'][0]; parts[0]['businesses'] = []
    for number in range(20):
        business = copy.deepcopy(prototype); business['name'] = '容量业务' + str(number)
        business['steps'] = [copy.deepcopy(prototype['steps'][0]) for _ in range(16)]
        for step in business['steps']:
            step.update(purpose='中' * 1000, next_index=None, else_index=None)
        parts[0]['businesses'].append(business)
    before = copy.deepcopy(parts)
    with pytest.raises(ValueError, match='result_capacity'): build(parts)
    assert parts == before
