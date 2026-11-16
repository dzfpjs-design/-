"""Synthetic pure V179 checks; no report/provider/source/quality authority."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_design_grouped_wire_v179 as subject
from generic_agents import report_design_grouped_wire_v174 as old
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents.contracts import digest
from generic_agents.report_object_semantics_v166 import CandidateObjects
from tests.test_report_design_grouped_wire_v174 import scene as old_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure indexed wire cannot connect or launch processes')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value, **options):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **options).encode('utf-8')


def binding(inputs):
    claims = [copy.deepcopy(inputs['input_items'][key]) for key in inputs['input_order']]
    snapshot = digest({'synthetic_snapshot': inputs['catalog']['descriptor'],
        'input_order': inputs['input_order'], 'input_items': inputs['input_items']})
    return {'indexed': codec.build_indexed_inputs(claims, snapshot),
        'input_claims': claims, 'source_snapshot_hash': snapshot}


def scene():
    value, inputs = old_scene()
    value['input_count'] = len(inputs['input_order'])
    return value, inputs


def build(value, inputs, *, bound=None, content=None):
    bound = binding(inputs) if bound is None else bound
    return subject.build_reduction_projection(raw(value) if content is None else content,
        **inputs, indexed=bound['indexed'], source_snapshot_hash=bound['source_snapshot_hash'])


def many_scene(count=920):
    value, inputs = scene()
    samples = [copy.deepcopy(inputs['input_items'][key]) for key in inputs['input_order']]
    items = {}
    for position in range(count):
        identity = f'map-{position // 80 + 1}/claims/{position % 80}'
        sample = 0 if position < count - 2 else position - count + 3
        items[identity] = copy.deepcopy(samples[sample])
    inputs.update(input_order=list(items), input_items=items)
    value['input_count'] = count
    value['reconciliation_groups'][0]['input_positions'] = list(range(count - 2))
    value['reconciliation_groups'][1]['input_positions'] = [count - 2]
    value['reconciliation_groups'][2]['input_positions'] = [count - 1]
    return value, inputs


@pytest.mark.parametrize('count', [1, 920, 1920])
def test_schema_has_actual_count_and_position_bounds_with_complete_unchanged_v166(count):
    value, inputs = scene(); bound = binding(inputs)
    claims = [copy.deepcopy(bound['input_claims'][0]) for _ in range(count)]
    frozen = codec.build_indexed_inputs(claims, bound['source_snapshot_hash'])
    schema = subject.output_schema(frozen, input_claims=claims,
        source_snapshot_hash=bound['source_snapshot_hash'])
    assert subject.DesignGroupedWireV179.model_fields['result'].annotation is CandidateObjects
    assert set(schema['properties']) == set(schema['required']) == {
        'result', 'input_count', 'reconciliation_groups'}
    assert schema['additionalProperties'] is False
    assert schema['properties']['input_count']['const'] == count
    assert schema['properties']['input_count']['minimum'] == count
    assert schema['properties']['input_count']['maximum'] == count
    assert schema['properties']['reconciliation_groups']['maxItems'] == count
    positions = schema['$defs']['ReconciliationGroupV179']['properties']['input_positions']
    assert positions['items']['maximum'] == count - 1
    assert positions['uniqueItems'] is True and positions['maxItems'] == count
    candidate = CandidateObjects.model_json_schema()
    expected = copy.deepcopy(candidate); expected.pop('$defs', None)
    assert schema['$defs']['CandidateObjects'] == expected
    for key, definition in candidate.get('$defs', {}).items():
        assert schema['$defs'][key] == definition
    assert value['result']['schema'] == schema['$defs']['CandidateObjects']['properties']['schema']['const']


def test_new_original_bytes_and_platform_quality_view_are_independently_identified():
    value, inputs = scene(); bound = binding(inputs)
    before = copy.deepcopy((value, inputs, bound)); content = raw(value, indent=2)
    projection = build(value, inputs, bound=bound, content=content)
    assert projection['schema'] == subject.PROJECTION != old.PROJECTION
    assert projection['wire_schema'] == subject.WIRE != old.WIRE
    assert projection['wire'] == value and projection['input_count'] == 3
    assert projection['wire_bytes_sha256'] == hashlib.sha256(content).hexdigest()
    assert projection['wire_value_hash'] == digest(value)
    assert projection['source_snapshot_hash'] == bound['source_snapshot_hash']
    assert projection['indexed_inputs_hash'] == bound['indexed']['inputs_hash']
    assert projection['input_claims_hash'] == digest(bound['input_claims'])
    assert projection['model_payload_hash'] == bound['indexed']['model_payload_hash']
    derived = {'result': value['result'], 'reconciliation_groups': value['reconciliation_groups']}
    derived_bytes = codec.canonical(derived).encode('utf-8')
    old_projection = old.build_reduction_projection(derived_bytes, **inputs)
    assert projection['platform_quality_view_schema'] == old.PROJECTION
    assert projection['platform_quality_view_wire_sha256'] == hashlib.sha256(derived_bytes).hexdigest()
    assert projection['platform_quality_view_hash'] == old_projection['projection_hash']
    assert projection['platform_quality_view_wire_sha256'] != projection['wire_bytes_sha256']
    assert projection['result'] == old_projection['result'] == value['result']
    assert projection['expanded_reconciliation'] == old_projection['expanded_reconciliation']
    assert all(projection[key] is False for key in ('provider_delivery_verified', 'semantic_quality_verified',
        'platform_identity_confirmed', 'can_apply', 'can_publish'))
    assert all(field['required'] is False for obj in projection['result']['objects'] for field in obj['fields'])
    assert projection['result']['objects'][1]['fields'][0]['kind'] == '整数'
    assert subject.check_reduction_projection(projection, content, **inputs,
        indexed=bound['indexed'], source_snapshot_hash=bound['source_snapshot_hash']) == projection
    assert (value, inputs, bound) == before


def test_920_inputs_cannot_be_invented_as_1920_or_platform_repaired():
    value, inputs = many_scene(); bound = binding(inputs)
    projection = build(value, inputs, bound=bound)
    assert projection['input_count'] == 920
    assert [row['input_position'] for row in projection['expanded_reconciliation']] == list(range(920))
    assert len(projection['wire']['reconciliation_groups']) == 3
    for fault in ('count', 'invented_positions', 'missing', 'duplicate'):
        damaged = copy.deepcopy(value)
        if fault == 'count': damaged['input_count'] = 1920
        elif fault == 'invented_positions': damaged['reconciliation_groups'][0]['input_positions'] = list(range(1920))
        elif fault == 'missing': damaged['reconciliation_groups'][0]['input_positions'].pop()
        else: damaged['reconciliation_groups'][0]['input_positions'].append(917)
        before = copy.deepcopy(damaged)
        with pytest.raises(ValueError): build(damaged, inputs, bound=bound)
        assert damaged == before


def test_group_order_is_original_and_only_expansion_is_platform_ordered():
    value, inputs = scene(); first = build(value, inputs)
    value['reconciliation_groups'].reverse(); second = build(value, inputs)
    assert second['wire']['reconciliation_groups'] == value['reconciliation_groups']
    assert first['wire_value_hash'] != second['wire_value_hash']
    assert first['expanded_reconciliation'] == second['expanded_reconciliation']
    assert first['expansion_hash'] == second['expansion_hash']


@pytest.mark.parametrize('fault', ['count_float', 'count_bool', 'count_string', 'count_missing',
    'missing', 'duplicate_inside', 'duplicate_across', 'out_of_range', 'negative', 'bool', 'float',
    'string', 'unsorted', 'empty_positions', 'duplicate_target', 'blank_reason', 'target_float',
    'extra_note', 'old_wire', 'old_candidate', 'missing_unit', 'tagged_unit', 'required_int'])
def test_strict_dynamic_and_complete_candidate_contract_is_not_weakened(fault):
    value, inputs = many_scene(5); group = value['reconciliation_groups'][0]
    if fault == 'count_float': value['input_count'] = 5.0
    elif fault == 'count_bool': value['input_count'] = True
    elif fault == 'count_string': value['input_count'] = '5'
    elif fault == 'count_missing': value.pop('input_count')
    elif fault == 'missing': group['input_positions'].pop()
    elif fault == 'duplicate_inside': group['input_positions'][1] = 0
    elif fault == 'duplicate_across': value['reconciliation_groups'][1]['input_positions'] = [0, 3]
    elif fault == 'out_of_range': group['input_positions'].append(5)
    elif fault == 'negative': group['input_positions'][0] = -1
    elif fault == 'bool': group['input_positions'][0] = False
    elif fault == 'float': group['input_positions'][0] = 0.0
    elif fault == 'string': group['input_positions'][0] = '0'
    elif fault == 'unsorted': group['input_positions'].reverse()
    elif fault == 'empty_positions': group['input_positions'] = []
    elif fault == 'duplicate_target':
        split = copy.deepcopy(group); split['input_positions'] = [2]
        group['input_positions'].pop(); value['reconciliation_groups'].append(split)
    elif fault == 'blank_reason': group['reason'] = ' '
    elif fault == 'target_float': group['output_index'] = 0.0
    elif fault == 'extra_note': value['note'] = 'private'
    elif fault == 'old_wire': value = old_scene()[0]
    elif fault == 'old_candidate': value['result']['schema'] = 'report-evidence-candidate-wire-v5'
    elif fault == 'missing_unit': value['result']['objects'][0]['fields'][0].pop('unit')
    elif fault == 'tagged_unit': value['result']['objects'][0]['fields'][0]['unit'] = {'kind': 'unspecified'}
    else: value['result']['objects'][0]['fields'][0]['required'] = 1
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): build(value, inputs)
    assert value == before


@pytest.mark.parametrize('fault', ['lost_ref', 'lost_scope', 'missing_item', 'wrong_target',
    'changed_source', 'unconfirmed_identity', 'unknown_promoted', 'inference_promoted'])
def test_existing_source_reference_unknown_inferred_and_identity_gates_still_apply(fault):
    value, inputs = scene()
    if fault == 'lost_ref': inputs['input_items'][inputs['input_order'][0]]['span_refs'].append('o:p1/s1')
    elif fault == 'lost_scope': inputs['allowed_span_refs'].pop()
    elif fault == 'missing_item': inputs['input_items'].pop(inputs['input_order'][0])
    elif fault == 'wrong_target': value['reconciliation_groups'][0]['output_index'] = 999
    elif fault == 'changed_source': inputs['catalog']['descriptor']['source_hash'] = 'f' * 64
    elif fault == 'unconfirmed_identity': value['object_identity_confirmations'] = []
    elif fault == 'unknown_promoted': inputs['input_items'][inputs['input_order'][0]].update(
        status='unknown', explanation='仍待核对。')
    else: inputs['input_items'][inputs['input_order'][2]].update(status='inferred', explanation='只属推断。')
    with pytest.raises((ValueError, KeyError)): build(value, inputs)


def test_unresolved_inputs_can_only_remain_questions_and_not_invented_kept_facts():
    value, inputs = scene(); inputs['input_items'][inputs['input_order'][0]].update(
        status='unknown', explanation='不能判断，保留原始声明。')
    value['result']['questions'].append('请核对第一项原声明及原引用。')
    value['reconciliation_groups'][0].update(output_kind='questions',
        output_index=len(value['result']['questions']) - 1, disposition='uncertain')
    assert build(value, inputs)['result']['questions'] == value['result']['questions']


def test_resealed_manifest_or_projection_cannot_replace_actual_inputs_or_raw_wire():
    value, inputs = scene(); bound = binding(inputs); content = raw(value, indent=2)
    projection = build(value, inputs, bound=bound, content=content)
    changed = copy.deepcopy(bound)
    changed['input_claims'][0]['statement'] += '另一个输入'
    changed['indexed'] = codec.build_indexed_inputs(changed['input_claims'], changed['source_snapshot_hash'])
    with pytest.raises(ValueError): build(value, inputs, bound=changed)
    with pytest.raises(ValueError): subject.check_reduction_projection(projection, raw(value), **inputs,
        indexed=bound['indexed'], source_snapshot_hash=bound['source_snapshot_hash'])
    for key in ('input_count', 'indexed_inputs_hash', 'wire_value_hash', 'platform_quality_view_hash',
                'expansion_hash', 'can_apply'):
        damaged = copy.deepcopy(projection)
        damaged[key] = True if key == 'can_apply' else 1920 if key == 'input_count' else 'f' * 64
        damaged['projection_hash'] = digest({k: v for k, v in damaged.items() if k != 'projection_hash'})
        with pytest.raises(ValueError): subject.check_reduction_projection(damaged, content, **inputs,
            indexed=bound['indexed'], source_snapshot_hash=bound['source_snapshot_hash'])


@pytest.mark.parametrize('content', [b'', b'\xff', b'[]', b'{}', b'{',
    b'{"result":{},"result":{}}', b'{"result":{},"resul\\u0074":{}}',
    b'{"x":NaN}', b'{"x":1e9999}', b'{"x":"\\ud800"}', b'{"x":"\\u0000"}'])
def test_bad_original_json_is_not_repaired(content):
    _, inputs = scene()
    with pytest.raises(ValueError): build({}, inputs, content=content)


def test_original_response_capacity_and_old_protocol_are_unchanged():
    value, inputs = scene(); bound = binding(inputs)
    assert subject.MAX_WIRE_BYTES == old.MAX_WIRE_BYTES == 1024 * 1024
    with pytest.raises(ValueError): subject.parse_wire(b' ' * (subject.MAX_WIRE_BYTES + 1), **bound)
    with pytest.raises(ValueError): old.parse_wire(raw(value))
    assert 'input_count' not in old.DesignGroupedWireV174.model_json_schema()['properties']

