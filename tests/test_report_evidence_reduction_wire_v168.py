"""Public synthetic pure checks, not report quality or provider delivery proof.

No successful report, Job, paid response, persisted source or human approval is
constructed here. Root alone executes these authored tests.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_reduction_wire_v168 as subject
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import build_evidence_source
from generic_agents.report_evidence_wire_v3 import CandidateWireV5, ReconciliationWireV5
from generic_agents.report_object_semantics_v166 import CandidateObjects, CANDIDATE
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_evidence_semantics_pure import catalog, allowed, CONTEXT
from tests.test_report_evidence_wire_v3_pure import candidate_wire


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure V168 checks cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    sources = catalog()
    result = candidate_wire(sources)
    result['schema'] = CANDIDATE
    for obj in result['objects']:
        obj['business_identity'] = {'status': 'not_established', 'field_labels': [],
            'citations': [], 'explanation': '该合成候选尚未确立业务标识，不能认定原文没有标识。'}
        for field in obj['fields']:
            field['required'] = False
    # A numeric-only, all-optional object must not receive a dummy text key.
    result['objects'][1]['fields'] = [result['objects'][1]['fields'][1]]
    items = {}
    for number, ref in enumerate(['w:p1/s1', 'w:p2/s1', 'o:p1/s1'], 1):
        items[f'map-{number}/claims/0'] = {'kind': '对象' if number < 3 else '职责',
            'statement': sources['index'][ref]['text'], 'span_refs': [ref],
            'status': 'explicit', 'explanation': ''}
    wire = {'result': result, 'reconciliation': [
        {'input_position': i, 'disposition': 'kept', 'output_kind': kind,
            'output_index': index, 'reason': '保留这个合成原声明及其全部证据，仍须人工判断。'}
        for i, kind, index in [(0, 'objects', 0), (1, 'objects', 1), (2, 'global_claims', 0)]]}
    inputs = {'catalog': sources, 'allowed_span_refs': allowed(sources),
        'context': copy.deepcopy(CONTEXT), 'input_order': list(items), 'input_items': items}
    return wire, inputs


def raw(value, **options):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **options).encode('utf-8')


def build(value, inputs):
    return subject.build_reduction_projection(raw(value), **inputs)


def test_native_model_is_independent_closed_required_and_preserves_actual_nullable_types():
    assert subject.ReductionWireV168.model_fields['result'].annotation is CandidateObjects
    assert CandidateWireV5 not in CandidateObjects.__mro__
    assert subject.ReductionWireV168.model_fields['reconciliation'].annotation == list[ReconciliationWireV5]
    schema = subject.ReductionWireV168.model_json_schema()
    assert schema['properties']['result'] == {'$ref': '#/$defs/CandidateObjects'}
    assert schema['$defs']['CandidateObjects']['properties']['schema']['const'] == CANDIDATE
    for name, field in [('SpanField', 'unit'), ('StepWireV5', 'next_index'), ('StepWireV5', 'else_index')]:
        assert {'type': 'null'} in schema['$defs'][name]['properties'][field]['anyOf']
    def visit(node):
        if type(node) is dict:
            assert 'default' not in node
            if 'properties' in node:
                assert node.get('additionalProperties') is False
                assert set(node['required']) == set(node['properties'])
            for item in node.values(): visit(item)
        elif type(node) is list:
            for item in node: visit(item)
    visit(schema)


def test_all_optional_complete_result_retains_original_objects_conditions_and_no_confirmation():
    value, inputs = scene(); before = copy.deepcopy((value, inputs))
    content = raw(value, indent=2)
    proof = subject.build_reduction_projection(content, **inputs)
    assert canonical_json(proof['result']) == canonical_json(value['result'])
    assert proof['result']['schema'] == CANDIDATE
    assert proof['reconciliation'] == value['reconciliation']
    assert proof['result']['objects'][1]['fields'][0]['kind'] == '整数'
    assert all(field['required'] is False for obj in proof['result']['objects'] for field in obj['fields'])
    assert proof['result']['businesses'][0]['conditions'][0]['kind'] == 'precondition'
    assert proof['result']['businesses'][0]['steps'][0]['branch_condition'] == ''
    assert proof['result']['businesses'][0]['steps'][0]['else_index'] is None
    for flag in ('provider_delivery_verified', 'semantic_quality_verified',
                 'platform_identity_confirmed', 'can_apply', 'can_publish'):
        assert proof[flag] is False
    assert not {'codec', 'codec_value_hash', 'journal_binding', 'invocation_id'} & set(proof)
    assert subject.check_reduction_projection(proof, content, **inputs) == proof
    assert (value, inputs) == before


def test_raw_bytes_value_hashes_and_cold_rebuild_do_not_confuse_equivalent_json_spelling():
    value, inputs = scene(); pretty = raw(value, indent=2); compact = raw(value, separators=(',', ':'))
    proof = subject.build_reduction_projection(pretty, **inputs)
    other = subject.build_reduction_projection(compact, **inputs)
    assert proof['wire_bytes_sha256'] == hashlib.sha256(pretty).hexdigest()
    assert proof['wire_bytes_sha256'] != other['wire_bytes_sha256']
    assert proof['wire_value_hash'] == other['wire_value_hash'] == digest(value)
    assert proof['result_hash'] == digest(value['result'])
    assert proof['reconciliation_hash'] == digest(value['reconciliation'])
    assert proof['projection_hash'] == digest({k: v for k, v in proof.items() if k != 'projection_hash'})
    with pytest.raises(ValueError): subject.check_reduction_projection(proof, compact, **inputs)


@pytest.mark.parametrize('field', ['result_hash', 'context_hash', 'wire_bytes_sha256',
    'semantic_quality_verified', 'provider_delivery_verified', 'can_apply', 'extra'])
def test_self_resealed_proof_cannot_replace_original_bytes_inputs_or_permission(field):
    value, inputs = scene(); proof = build(value, inputs)
    proof[field] = True if field in {'semantic_quality_verified', 'provider_delivery_verified', 'can_apply', 'extra'} else 'f' * 64
    proof['projection_hash'] = digest({k: v for k, v in proof.items() if k != 'projection_hash'})
    with pytest.raises(ValueError): subject.check_reduction_projection(proof, raw(value), **inputs)


@pytest.mark.parametrize('content', [b'', b'\xff', b'\xef\xbb\xbf{}', b'[]', b'{}',
    b'{"result":{},"result":{}}', b'{"result":{},"resul\\u0074":{}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}',
    b'{"x":"\\ud800"}', b'{"x":"\\u0000"}', b'{'],
    ids=['empty', 'utf8', 'bom', 'array', 'empty-object', 'duplicate', 'escaped-duplicate',
         'nan', 'infinity', 'overflow', 'surrogate', 'nul', 'truncated'])
def test_unsafe_original_json_is_rejected_without_repair_or_clipping(content):
    _, inputs = scene()
    with pytest.raises(ValueError): subject.build_reduction_projection(content, **inputs)


@pytest.mark.parametrize('fault', ['old-schema', 'extra-key', 'missing-unit', 'tagged-unit',
    'tagged-edge', 'bool-required', 'float-position', 'bool-position', 'string-position',
    'negative-output', 'float-output', 'blank-reason', 'extra-confirmation'])
def test_required_new_wire_fields_and_actual_bool_integer_nullable_types_are_not_coerced(fault):
    value, inputs = scene(); field = value['result']['objects'][0]['fields'][0]
    item = value['reconciliation'][0]
    if fault == 'old-schema': value['result']['schema'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'extra-key': value['result']['global_claims'][0]['key'] = 'invented'
    elif fault == 'missing-unit': del field['unit']
    elif fault == 'tagged-unit': field['unit'] = {'kind': 'unspecified'}
    elif fault == 'tagged-edge': value['result']['businesses'][0]['steps'][1]['next_index'] = {'kind': 'end'}
    elif fault == 'bool-required': field['required'] = 1
    elif fault == 'float-position': item['input_position'] = 0.0
    elif fault == 'bool-position': item['input_position'] = False
    elif fault == 'string-position': item['input_position'] = '0'
    elif fault == 'negative-output': item['output_index'] = -1
    elif fault == 'float-output': item['output_index'] = 0.0
    elif fault == 'blank-reason': item['reason'] = ' '
    else: value['object_identity_confirmations'] = []
    with pytest.raises(ValueError): build(value, inputs)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'extra', 'wrong-target',
    'questions-as-kept', 'uncertain-as-object', 'lost-one-ref', 'lost-all-refs'])
def test_every_input_is_accounted_once_and_kept_or_duplicate_retains_all_input_evidence(fault):
    value, inputs = scene(); entry = value['reconciliation'][0]
    if fault == 'missing': value['reconciliation'].pop()
    elif fault == 'duplicate': value['reconciliation'][1]['input_position'] = 0
    elif fault == 'extra': value['reconciliation'].append({**entry, 'input_position': 3})
    elif fault == 'wrong-target': entry['output_index'] = 99
    elif fault == 'questions-as-kept':
        value['result']['questions'] = ['原声明仍待核。']; entry.update(output_kind='questions', output_index=0)
    elif fault == 'uncertain-as-object': entry['disposition'] = 'uncertain'
    elif fault == 'lost-one-ref': inputs['input_items'][inputs['input_order'][0]]['span_refs'].append('o:p1/s1')
    else: entry.update(disposition='duplicate', output_kind='objects', output_index=1)
    with pytest.raises(ValueError): build(value, inputs)


def test_duplicate_target_retains_both_sources_and_reconciliation_order_is_not_rewritten():
    value, inputs = scene(); first = inputs['input_order'][0]
    inputs['input_items'][first]['span_refs'].append('o:p1/s1')
    value['result']['objects'][0]['citations'].append({'span_ref': 'o:p1/s1'})
    value['reconciliation'][0]['disposition'] = 'duplicate'
    value['reconciliation'].reverse()
    proof = build(value, inputs)
    assert proof['reconciliation'] == value['reconciliation']
    assert proof['reconciliation'][2]['disposition'] == 'duplicate'
    assert inputs['input_items'][first]['span_refs'] == ['w:p1/s1', 'o:p1/s1']


def test_unknown_original_is_only_an_unresolved_question_and_never_a_retained_fact():
    value, inputs = scene(); original = inputs['input_items'][inputs['input_order'][0]]
    original.update(status='unknown', explanation='这项原声明尚不能核对。')
    value['result']['questions'] = ['请核对原声明，完整原文及引用留在固定输入中。']
    with pytest.raises(ValueError): build(value, inputs)
    value['reconciliation'][0].update(disposition='uncertain', output_kind='questions', output_index=0)
    before = copy.deepcopy(inputs)
    assert build(value, inputs)['result']['questions'] == value['result']['questions']
    assert inputs == before


def test_inferred_original_needs_inferred_target_and_explicit_structural_explanation():
    value, inputs = scene(); original = inputs['input_items'][inputs['input_order'][2]]
    original.update(status='inferred', explanation='此职责只是合成推断。')
    with pytest.raises(ValueError): build(value, inputs)
    value['result']['global_claims'][0].update(status='inferred', explanation='原推断仍须人工核对。')
    assert build(value, inputs)['result']['global_claims'][0]['status'] == 'inferred'
    value, inputs = scene(); original = inputs['input_items'][inputs['input_order'][0]]
    original.update(status='inferred', explanation='原对象含义仍属推断。')
    with pytest.raises(ValueError): build(value, inputs)
    value['result']['objects'][0]['inference_reason'] = '保留原推断，不认定已得到原文明示。'
    assert build(value, inputs)['result']['objects'][0]['inference_reason']


@pytest.mark.parametrize('fault', ['empty-order', 'tuple-order', 'order', 'missing-item',
    'old-key', 'gap', 'outside-input-ref', 'scope-order', 'extra-scope', 'duplicate-scope',
    'context-type', 'context-number', 'source-hash', 'catalog-hash'])
def test_complete_keyless_input_index_and_exact_frozen_scope_are_required(fault):
    value, inputs = scene()
    if fault == 'empty-order': inputs['input_order'] = []; inputs['input_items'] = {}
    elif fault == 'tuple-order': inputs['input_order'] = tuple(inputs['input_order'])
    elif fault == 'order': inputs['input_order'].reverse()
    elif fault == 'missing-item': inputs['input_items'].pop(inputs['input_order'][0])
    elif fault == 'old-key': inputs['input_items'][inputs['input_order'][0]]['key'] = 'old_internal_key'
    elif fault == 'gap':
        old = inputs['input_order'][0]; new = 'map-1/claims/1'
        inputs['input_order'][0] = new; inputs['input_items'][new] = inputs['input_items'].pop(old)
    elif fault == 'outside-input-ref': inputs['input_items'][inputs['input_order'][0]]['span_refs'] = ['w:p3/s1']
    elif fault == 'scope-order': inputs['allowed_span_refs'].reverse()
    elif fault == 'extra-scope': inputs['allowed_span_refs'].insert(2, 'w:p3/s1')
    elif fault == 'duplicate-scope': inputs['allowed_span_refs'].append(inputs['allowed_span_refs'][0])
    elif fault == 'context-type': inputs['context'] = []
    elif fault == 'context-number': inputs['context']['bad'] = float('nan')
    elif fault == 'source-hash': value['result']['source_hash'] = 'f' * 64
    else: value['result']['catalog_hash'] = 'f' * 64
    with pytest.raises(ValueError): build(value, inputs)


@pytest.mark.parametrize('fault', ['self-edge', 'outside-edge', 'implicit-else', 'same-person',
    'missing-person', 'wrong-condition-step', 'blank-condition', 'unknown-evidence',
    'duplicate-steps', 'single-sequence', 'wrong-unit', 'identity-outside', 'unsupported-cardinality'])
def test_existing_condition_direction_unit_and_object_identity_guards_still_apply(fault):
    value, inputs = scene(); business = value['result']['businesses'][0]
    if fault == 'self-edge': business['steps'][0]['next_index'] = 0
    elif fault == 'outside-edge': business['steps'][0]['next_index'] = 99
    elif fault == 'implicit-else': business['steps'][0]['else_index'] = 1
    elif fault == 'same-person': business['interactions'][0]['to_participant_index'] = 0
    elif fault == 'missing-person': business['interactions'][0]['to_participant_index'] = 99
    elif fault == 'wrong-condition-step': business['conditions'][0]['step_indices'] = [99]
    elif fault == 'blank-condition': business['conditions'][0]['statement'] = ' '
    elif fault == 'unknown-evidence': business['conditions'][0]['evidence'].update(status='unknown', explanation='前置尚未核对')
    elif fault == 'duplicate-steps': business['coordination'][0]['step_indices'] = [0, 0]
    elif fault == 'single-sequence': business['coordination'][0]['step_indices'] = [0]
    elif fault == 'wrong-unit': value['result']['objects'][1]['fields'][0]['unit'] = '亩'
    elif fault == 'identity-outside': value['result']['objects'][0]['business_identity'].update(
        status='referenced', field_labels=['名称'], citations=[{'span_ref': 'w:p3/s1'}])
    else: value['result']['relationships'][0]['cardinality'] = 'one_per_source'
    with pytest.raises(ValueError): build(value, inputs)


@pytest.mark.parametrize('sentence', ['记录单不关联核对单。', '记录单可能关联核对单。',
    '记录单和核对单分别登记。', '核对单关联记录单。'],
    ids=['negated', 'uncertain', 'cooccurrence', 'reversed'])
def test_negative_uncertain_or_reversed_source_cannot_become_a_positive_object_relationship(sentence):
    value, inputs = scene(); old = inputs['catalog']
    blocks = [[ref, ''.join(item['text'] for item in old['index'].values() if item['source_ref'] == ref)]
        for ref in old['descriptor']['ordered_source_refs']]
    blocks[0][1] = '记录单包含名称字段。' + sentence
    sources = build_evidence_source(old['descriptor']['source_hash'], blocks)
    inputs['catalog'] = sources
    value['result']['catalog_hash'] = sources['descriptor']['catalog_hash']
    inputs['input_items'][inputs['input_order'][0]]['statement'] = blocks[0][1]
    with pytest.raises(ValueError): build(value, inputs)


def test_explicit_cycle_is_retained_and_a_negative_condition_is_not_claimed_to_be_semantically_verified():
    value, inputs = scene(); business = value['result']['businesses'][0]
    business['steps'][1]['next_index'] = 0
    business['conditions'][0]['statement'] = '材料不齐全时不得开始核对；该文字含义仍需人工判定。'
    proof = build(value, inputs)
    assert proof['result']['businesses'][0] == business
    assert proof['semantic_quality_verified'] is False


def test_output_and_original_input_mutation_never_alias_a_new_cold_projection():
    value, inputs = scene(); before = copy.deepcopy((value, inputs))
    proof = build(value, inputs)
    proof['result']['objects'][0]['fields'].clear()
    proof['reconciliation'].clear(); proof['input_order'].reverse()
    assert (value, inputs) == before
    rebuilt = build(value, inputs)
    assert rebuilt['result'] == value['result'] and rebuilt['reconciliation'] == value['reconciliation']


def test_capacity_rejects_whole_original_wire_and_complete_projection_without_truncation(monkeypatch):
    value, inputs = scene(); original = copy.deepcopy((value, inputs))
    with pytest.raises(ValueError):
        subject.build_reduction_projection(b' ' * (subject.MAX_WIRE_BYTES + 1), **inputs)
    proof = build(value, inputs)
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', len(canonical_json(proof).encode('utf-8')) - 1)
    with pytest.raises(ValueError): build(value, inputs)
    assert (value, inputs) == original
