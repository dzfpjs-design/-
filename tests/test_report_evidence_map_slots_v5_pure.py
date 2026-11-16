"""Synthetic pure slot-wire checks. Root alone executes; no provider call.

These do not invoke a gateway, DB, journal, model, source file, or provider.
Compiler shape proves a fixed structural ceiling, never 8192-token adequacy,
semantic completeness or actual provider support/delivery.
"""
import copy
import hashlib
import json

import pytest

from generic_agents import report_evidence_map_slots_v5 as subject

def claim(number=0):
    return {'kind': '职责', 'statement': f'合成职责{number}，仅用于协议测试。',
        'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''}


def wire(positions=(0, 2, 79)):
    slots = {name: {'state': 'absent'} for name in subject.SLOT_NAMES}
    for position in positions:
        slots[subject.SLOT_NAMES[position]] = {'state': 'present', 'claim': claim(position)}
    return {'schema': subject.WIRE_SCHEMA, 'slots': slots}


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def decode(value):
    return subject.decode(value, arguments_sha256=hashlib.sha256(raw(value)).hexdigest())


def test_schema_has_exact_eighty_required_closed_slots_and_shared_named_anyof_definition():
    local = subject.MapSlotsWireV5.model_json_schema()
    fixed = local['$defs']['Fixed80Slots']
    assert len(fixed['required']) == 80
    assert set(fixed['required']) == set(fixed['properties']) == set(subject.SLOT_NAMES)
    assert fixed['additionalProperties'] is False
    assert all(value == {'$ref': '#/$defs/SlotValue'} for value in fixed['properties'].values())
    assert local['$defs']['SlotValue']['anyOf'] == [
        {'$ref': '#/$defs/AbsentSlot'}, {'$ref': '#/$defs/PresentSlot'}]
    for tag, name in [('absent', 'AbsentSlot'), ('present', 'PresentSlot')]:
        branch = local['$defs'][name]
        assert branch['additionalProperties'] is False
        assert set(branch['required']) == set(branch['properties']) == ({'state'} if tag == 'absent' else {'state', 'claim'})
    proof = subject.schema_proof()
    assert proof['schema'] == 'report-evidence-strict-ref-schema-proof-v2'
    assert proof['local_schema_hash'] == subject.digest(local)
    assert proof['proof_hash'] == subject.digest({key: value for key, value in proof.items() if key != 'proof_hash'})
    provider = proof['provider_schema']
    assert '$defs' not in provider and '$def' in provider
    assert all(value == {'$ref': '#/$def/SlotValue'} for value in provider['$def']['Fixed80Slots']['properties'].values())
    fields = provider['$def']['MapClaimWireV3']
    assert fields['additionalProperties'] is False
    assert set(fields['required']) == set(fields['properties']) == {'kind', 'statement', 'span_refs', 'status', 'explanation'}
    assert fields['properties']['kind']['enum'] == ['职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他']
    value = wire()
    assert subject.MapSlotsWireV5.model_validate(value).model_dump() == value
    assert subject.SlotValue.model_validate(value['slots']['slot_001']).model_dump() == value['slots']['slot_001']


def test_holes_are_preserved_and_json_object_order_never_becomes_claim_order():
    original = wire(); before = copy.deepcopy(original)
    original['slots'] = dict(reversed(list(original['slots'].items())))
    value = decode(original)
    assert value['slot_positions'] == [0, 2, 79]
    assert value['view'] == {'schema': 'report-evidence-map-v3', 'claims': [claim(0), claim(2), claim(79)]}
    assert subject.encode(value['view'], value['slot_positions']) == original
    assert subject.check(value, original, arguments_sha256=value['arguments_sha256']) == value
    assert original == before


@pytest.mark.parametrize('positions', [(), (79,), tuple(range(80))])
def test_empty_one_and_eighty_claims_have_exact_capacity_without_dropping_values(positions):
    original = wire(positions); value = decode(original)
    assert value['slot_positions'] == list(positions)
    assert len(value['view']['claims']) == len(positions)
    assert subject.encode(value['view'], value['slot_positions']) == original


@pytest.mark.parametrize('mode', ['unknown_schema', 'missing_slot', 'extra_slot', 'unknown_tag',
    'bool_tag', 'absent_with_claim', 'present_missing_claim', 'extra_top', 'array_slots'])
def test_unknown_extra_missing_and_bool_shapes_are_rejected_without_normalization(mode):
    value = wire()
    if mode == 'unknown_schema': value['schema'] = 'report-evidence-map-v3'
    elif mode == 'missing_slot': del value['slots']['slot_080']
    elif mode == 'extra_slot': value['slots']['slot_081'] = {'state': 'present', 'claim': claim(81)}
    elif mode == 'unknown_tag': value['slots']['slot_002']['state'] = 'unused'
    elif mode == 'bool_tag': value['slots']['slot_002']['state'] = False
    elif mode == 'absent_with_claim': value['slots']['slot_002']['claim'] = claim()
    elif mode == 'present_missing_claim': del value['slots']['slot_001']['claim']
    elif mode == 'extra_top': value['claims'] = [claim()]
    else: value['slots'] = list(value['slots'].values())
    before = copy.deepcopy(value)
    with pytest.raises(ValueError): decode(value)
    assert value == before


@pytest.mark.parametrize('mode', ['kind', 'status', 'blank', 'empty_explicit', 'duplicate_ref',
    'bad_ref', 'long_statement', 'long_explanation', 'too_many_refs', 'inferred_blank', 'unknown_blank', 'nul', 'surrogate', 'extra_key'])
def test_original_five_fields_keep_all_legacy_local_constraints(mode):
    value = wire((0,)); item = value['slots']['slot_001']['claim']
    if mode == 'kind': item['kind'] = '流程'
    elif mode == 'status': item['status'] = 'confirmed'
    elif mode == 'blank': item['statement'] = ' \t\n'
    elif mode == 'empty_explicit': item['span_refs'] = []
    elif mode == 'duplicate_ref': item['span_refs'] *= 2
    elif mode == 'bad_ref': item['span_refs'] = ['w:p1/s0']
    elif mode == 'long_statement': item['statement'] = '甲' * 2001
    elif mode == 'long_explanation': item['explanation'] = '甲' * 1001
    elif mode == 'too_many_refs': item['span_refs'] = [f'w:p1/s{i}' for i in range(1, 18)]
    elif mode in {'inferred_blank', 'unknown_blank'}: item.update(status=mode.split('_')[0], explanation=' \t')
    elif mode == 'nul': item['statement'] = '原样\x00不修复'
    elif mode == 'surrogate': item['statement'] = '\ud800'
    else: item['key'] = 'model_should_not_generate_ids'
    before = copy.deepcopy(value)
    with pytest.raises((ValueError, UnicodeError)): decode(value)
    assert value == before


@pytest.mark.parametrize('positions', [[True, 2, 79], [0, 0, 79], [2, 0, 79], [0, 2], [0, 2, 80], [0, 2, 79.0], None])
def test_inverse_requires_exact_explicit_positions_instead_of_guessing_holes(positions):
    value = decode(wire())
    with pytest.raises(ValueError): subject.encode(value['view'], positions)
    # A caller can deliberately encode another positioning, but it is a
    # different wire and cannot pass check against the original slot wire.
    shifted = subject.encode(value['view'], [1, 3, 78])
    assert shifted != value['wire']


def test_original_argument_bytes_and_wire_value_and_codec_view_hashes_are_distinct():
    original = wire((0,)); body = raw(original)
    value = subject.decode_arguments(body)
    assert value['arguments_sha256'] == hashlib.sha256(body).hexdigest()
    assert value['wire_value_hash'] == subject.digest(original)
    assert value['view_hash'] == subject.digest(value['view'])
    assert len({value['arguments_sha256'], value['wire_value_hash'], value['view_hash'], value['codec_hash']}) == 4
    differently_formatted = json.dumps(original, ensure_ascii=True, indent=1).encode('ascii')
    other = subject.decode_arguments(differently_formatted)
    assert other['wire'] == value['wire'] and other['view'] == value['view']
    assert other['arguments_sha256'] != value['arguments_sha256']
    assert other['wire_value_hash'] == value['wire_value_hash'] and other['view_hash'] == value['view_hash']


@pytest.mark.parametrize('mode', ['view', 'positions', 'wire', 'arguments_hash', 'extra'])
def test_public_resealing_does_not_replace_original_wire_or_argument_hash(mode):
    original = wire(); value = decode(original)
    if mode == 'view': value['view']['claims'][0]['statement'] = 'changed'
    elif mode == 'positions': value['slot_positions'] = [1, 3, 78]
    elif mode == 'wire': value['wire'] = subject.encode(value['view'], [1, 3, 78])
    elif mode == 'arguments_hash': value['arguments_sha256'] = 'f' * 64
    else: value['extra'] = True
    value['codec_hash'] = subject.digest({key: item for key, item in value.items() if key != 'codec_hash'})
    with pytest.raises(ValueError): subject.check(value, original, arguments_sha256=hashlib.sha256(raw(original)).hexdigest())


def test_duplicate_keys_are_rejected_from_original_raw_bytes_at_all_object_levels():
    body = raw(wire((0,)))
    variants = [body.replace(b'"schema":', b'"schema":"report-evidence-map-slots-v5","schema":', 1),
        body.replace(b'"slot_001":', b'"slot_001":{"state":"absent"},"slot_001":', 1),
        body.replace(b'"state":"present"', b'"state":"absent","state":"present"', 1),
        body.replace('"kind":"职责"'.encode(), '"kind":"职责","kind":"职责"'.encode(), 1)]
    for changed in variants:
        assert changed != body
        with pytest.raises(ValueError): subject.decode_arguments(changed)


@pytest.mark.parametrize('body', [b'', b'\xff', b'[]', b'{"x":NaN}', b'{"x":Infinity}', b'x' * (256 * 1024 + 1)])
def test_invalid_or_oversize_original_bytes_are_never_salvaged(body):
    with pytest.raises(ValueError): subject.decode_arguments(body)


def test_codec_does_not_invent_source_validation_or_deduplicate_identical_claims():
    original = wire((0, 3))
    original['slots']['slot_001']['claim']['span_refs'] = ['w:p999999/s1']
    original['slots']['slot_004']['claim'] = copy.deepcopy(original['slots']['slot_001']['claim'])
    # Source existence and each Map's actual allowlist remain the caller's
    # unchanged binding/projection duty; this pure codec must not claim it.
    value = decode(original)
    assert value['view']['claims'][0]['span_refs'] == ['w:p999999/s1']
    assert len(value['view']['claims']) == 2
    assert value['view']['claims'][0] == value['view']['claims'][1] == original['slots']['slot_004']['claim']
    assert value['slot_positions'] == [0, 3]
    assert subject.encode(value['view'], value['slot_positions']) == original


def test_original_array_of_eighty_eight_is_not_silently_truncated_or_adopted():
    old_reply = {'schema': 'report-evidence-map-v3', 'claims': [claim(i) for i in range(88)]}
    before = copy.deepcopy(old_reply)
    with pytest.raises(ValueError): decode(old_reply)
    with pytest.raises(ValueError): subject.encode(old_reply, list(range(80)))
    assert old_reply == before

