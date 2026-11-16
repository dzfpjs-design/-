"""Production compiler checks; not provider acceptance or model quality."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_evidence_ref_schema_v2 as v2
from generic_agents import report_evidence_ref_schema_v3 as subject
from generic_agents.report_evidence_map_slots_v5 import MapSlotsWireV5, SLOT_NAMES


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure compiler cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def hashed(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def obj(properties):
    return {'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}


def local_schema():
    value = obj({'choice': {'$ref': '#/$defs/Choice'},
        'ordinary': {'$ref': '#/$defs/Present'}})
    value['$defs'] = {
        'Absent': obj({'state': {'type': 'string', 'const': 'absent'}}),
        'Present': obj({'state': {'type': 'string', 'const': 'present'},
            'text': {'type': 'string', 'minLength': 1, 'maxLength': 20}}),
        'Choice': {'anyOf': [{'$ref': '#/$defs/Absent'}, {'$ref': '#/$defs/Present'}]},
    }
    return value


def wire():
    slots = {name: {'state': 'absent'} for name in SLOT_NAMES}
    slots['slot_003'] = {'state': 'present', 'claim': {'kind': '其他',
        'statement': '公开合成声明，不代表模型质量。', 'span_refs': ['w:p1/s1'],
        'status': 'explicit', 'explanation': ''}}
    return {'schema': 'report-evidence-map-slots-v5', 'slots': slots}


def reordered(value):
    if type(value) is dict:
        return {key: reordered(item) for key, item in reversed(list(value.items()))}
    if type(value) is list:
        return [reordered(item) for item in value]
    return value


def test_complete_public_map_schema_only_two_arms_inline_shared_eighty_slots_unchanged():
    local = MapSlotsWireV5.model_json_schema(); before = copy.deepcopy(local)
    base = v2.compile_ref_schema(local); proof = subject.compile_ref_schema(local)
    provider = proof['provider_schema']; expected = copy.deepcopy(base['provider_schema'])
    expected['$def']['SlotValue']['anyOf'] = [copy.deepcopy(expected['$def'][name])
        for name in ('AbsentSlot', 'PresentSlot')]
    assert provider == expected and local == before
    assert 'type' not in provider['$def']['SlotValue']
    assert all(arm['type'] == 'object' for arm in provider['$def']['SlotValue']['anyOf'])
    assert all(value == {'$ref': '#/$def/SlotValue'}
        for value in provider['$def']['Fixed80Slots']['properties'].values())
    assert provider['$def']['SlotValue']['anyOf'][1]['properties']['claim'] == {'$ref': '#/$def/MapClaimWireV3'}
    assert proof['audit']['counts']['inlined_arms'] == 2
    assert [change['path'] for change in proof['audit']['transformations']] == [
        '/$def/SlotValue/anyOf/0', '/$def/SlotValue/anyOf/1']
    for arm, change in zip(provider['$def']['SlotValue']['anyOf'], proof['audit']['transformations']):
        assert change['target_hash'] == hashed(arm)
        assert change['operation'] == 'inline_immediate_anyof_ref_arm'
    assert len(encoded(provider)) < 50000  # Shared slots do not expand eighty times.
    assert MapSlotsWireV5.model_validate(wire()).model_dump() == wire()


@pytest.mark.parametrize('mode', ['valid', 'all_absent', 'extra', 'missing_slot', 'bad_kind',
    'bad_state', 'empty_statement', 'long_statement', 'duplicate_refs', 'old_key'])
def test_provider_predicate_matches_v2_on_public_map_values_without_loosening_local_validator(mode):
    value = wire(); claim = value['slots']['slot_003']['claim']
    if mode == 'all_absent': value['slots']['slot_003'] = {'state': 'absent'}
    elif mode == 'extra': value['unknown'] = True
    elif mode == 'missing_slot': del value['slots']['slot_080']
    elif mode == 'bad_kind': claim['kind'] = 'unlisted'
    elif mode == 'bad_state': value['slots']['slot_003']['state'] = 'unknown'
    elif mode == 'empty_statement': claim['statement'] = ''
    elif mode == 'long_statement': claim['statement'] = '字' * 2001
    elif mode == 'duplicate_refs': claim['span_refs'] *= 2
    elif mode == 'old_key': claim['key'] = 'not_allowed'
    local = MapSlotsWireV5.model_json_schema()
    old = Draft202012Validator(v2.compile_ref_schema(local)['provider_schema'])
    new = Draft202012Validator(subject.compile_ref_schema(local)['provider_schema'])
    assert new.is_valid(value) == old.is_valid(value)
    if mode in {'empty_statement', 'long_statement'}:
        # The provider subset already omits unsupported lengths. The original
        # local schema remains strict, independently of the proposed rewrite.
        assert new.is_valid(value)
        with pytest.raises(ValueError): MapSlotsWireV5.model_validate(value)


def test_ordinary_refs_inline_arms_and_literal_property_names_are_not_rewritten():
    local = local_schema()
    local['properties']['literal'] = obj({'$def': {'type': 'string'}, '$ref': {'type': 'boolean'}})
    local['required'].append('literal')
    local['$defs']['Choice']['anyOf'].append({'type': 'string', 'enum': ['原文＋+']})
    proof = subject.compile_ref_schema(local); provider = proof['provider_schema']
    assert provider['properties']['ordinary'] == {'$ref': '#/$def/Present'}
    assert provider['properties']['choice'] == {'$ref': '#/$def/Choice'}
    assert provider['$def']['Choice']['anyOf'][-1] == {'type': 'string', 'enum': ['原文＋+']}
    assert set(provider['properties']['literal']['properties']) == {'$def', '$ref'}


def test_escaped_definition_names_keep_canonical_pointer_and_original_audit_reference():
    local = local_schema(); local['$defs']['A/B~C'] = local['$defs'].pop('Absent')
    local['$defs']['Choice']['anyOf'][0]['$ref'] = '#/$defs/A~1B~0C'
    proof = subject.compile_ref_schema(local)
    assert proof['audit']['transformations'][0]['original'] == {'$ref': '#/$def/A~1B~0C'}
    assert 'A/B~C' in proof['provider_schema']['$def']


def test_nested_typed_targets_are_compiled_once_and_copied_without_leaving_reference_arms():
    local = local_schema()
    local['$defs']['Text'] = {'type': 'string', 'enum': ['原字＋+']}
    local['$defs']['Flag'] = {'type': 'boolean'}
    local['$defs']['Present']['properties']['nested'] = {'anyOf': [
        {'$ref': '#/$defs/Text'}, {'$ref': '#/$defs/Flag'}]}
    local['$defs']['Present']['required'].append('nested')
    base = v2.compile_ref_schema(local); proof = subject.compile_ref_schema(local)
    present = proof['provider_schema']['$def']['Present']
    assert present['properties']['nested']['anyOf'] == [
        {'type': 'string', 'enum': ['原字＋+']}, {'type': 'boolean'}]
    assert proof['provider_schema']['$def']['Choice']['anyOf'][1] == present
    assert proof['audit']['counts']['inlined_arms'] == 4
    for nested in ('原字＋+', True, 0, 'changed'):
        value = {'choice': {'state': 'present', 'text': '原字', 'nested': nested},
            'ordinary': {'state': 'present', 'text': '原字', 'nested': nested}}
        assert Draft202012Validator(proof['provider_schema']).is_valid(value) == Draft202012Validator(base['provider_schema']).is_valid(value)


def test_real_byte_expansion_exceeds_capacity_even_though_shared_original_fits():
    local = obj({'value': {'$ref': '#/$defs/Choice'}})
    local['$defs'] = {'Large': {**obj({}), 'description': 'x' * 20000},
        'Choice': {'anyOf': [{'$ref': '#/$defs/Large'} for _ in range(16)]}}
    base = v2.compile_ref_schema(local)
    assert len(encoded(base['provider_schema'])) < subject.MAX_SCHEMA_BYTES
    with pytest.raises(subject.RefArmSchemaError, match='post_schema_bytes'):
        subject.compile_ref_schema(local)


@pytest.mark.parametrize('mode', ['alias', 'union'])
def test_immediate_target_without_an_explicit_type_is_rejected_not_guessed(mode):
    local = local_schema()
    local['$defs']['Other'] = ({'$ref': '#/$defs/Absent'} if mode == 'alias'
        else {'anyOf': [{'type': 'boolean'}, {'type': 'string'}]})
    local['$defs']['Choice']['anyOf'][0] = {'$ref': '#/$defs/Other'}
    v2.compile_ref_schema(local)
    with pytest.raises(subject.RefArmSchemaError, match='arm_target_not_explicitly_typed'):
        subject.compile_ref_schema(local)


@pytest.mark.parametrize('mode', ['cycle', 'external', 'dangling', 'sibling', 'keyword', 'open_object'])
def test_original_v2_full_validation_rejects_bad_or_unused_definitions_before_rewrite(mode):
    local = local_schema(); arm = local['$defs']['Choice']['anyOf'][0]
    if mode == 'cycle': arm['$ref'] = '#/$defs/Choice'
    elif mode == 'external': arm['$ref'] = 'https://invalid.example/schema'
    elif mode == 'dangling': arm['$ref'] = '#/$defs/Missing'
    elif mode == 'sibling': arm['type'] = 'object'
    elif mode == 'keyword': local['$defs']['Unused'] = {'type': 'string', 'unknown': True}
    else: local['$defs']['Absent']['additionalProperties'] = True
    before = copy.deepcopy(local)
    with pytest.raises(subject.RefArmSchemaError, match='v2_'):
        subject.compile_ref_schema(local)
    assert local == before


@pytest.mark.parametrize('mode', ['bytes', 'nodes', 'depth', 'audit', 'proof'])
def test_expanded_actual_capacity_is_enforced_after_original_compact_schema_passes(monkeypatch, mode):
    local = local_schema(); local['$defs']['Present']['description'] = '公开合成' * 100
    base = v2.compile_ref_schema(local)
    if mode == 'bytes': monkeypatch.setattr(subject, 'MAX_SCHEMA_BYTES', len(encoded(base['provider_schema'])) + 1)
    elif mode == 'nodes': monkeypatch.setattr(subject, 'MAX_SCHEMA_NODES', base['audit']['counts']['schema_nodes'] + 1)
    elif mode == 'depth': monkeypatch.setattr(subject, 'MAX_SCHEMA_DEPTH', 1)
    elif mode == 'audit': monkeypatch.setattr(subject, 'MAX_AUDIT_BYTES', 1)
    else: monkeypatch.setattr(subject, 'MAX_PROOF_BYTES', 1)
    with pytest.raises(subject.RefArmSchemaError): subject.compile_ref_schema(local)


def test_cold_recompile_separates_local_provider_and_audit_hashes_and_is_dictionary_order_stable():
    local = local_schema(); proof = subject.compile_ref_schema(local)
    assert set(proof) == {'schema', 'local_schema_hash', 'provider_schema', 'provider_schema_hash', 'audit', 'audit_hash', 'proof_hash'}
    assert proof['schema'] == subject.PROOF_SCHEMA
    assert proof['local_schema_hash'] == hashed(local)
    assert proof['provider_schema_hash'] == hashed(proof['provider_schema'])
    assert proof['audit_hash'] == hashed(proof['audit'])
    assert proof['proof_hash'] == hashed({key: value for key, value in proof.items() if key != 'proof_hash'})
    assert subject.check_ref_schema_proof(proof, local) == proof
    assert subject.compile_ref_schema(reordered(local)) == proof
    assert proof['audit']['base_audit'] == v2.compile_ref_schema(local)['audit']


@pytest.mark.parametrize('mode', ['local', 'provider', 'audit', 'extra'])
def test_changed_context_or_publicly_resigned_proof_cannot_pass_cold_checker(mode):
    local = local_schema(); proof = subject.compile_ref_schema(local)
    if mode == 'local': local['$defs']['Present']['properties']['text']['maxLength'] += 1
    elif mode == 'provider': proof['provider_schema']['$def']['Choice']['anyOf'][0]['properties']['state']['enum'] = ['forged']
    elif mode == 'audit': proof['audit']['transformations'].pop()
    else: proof['unknown'] = True
    proof['provider_schema_hash'] = hashed(proof['provider_schema']); proof['audit_hash'] = hashed(proof['audit'])
    proof['proof_hash'] = hashed({key: value for key, value in proof.items() if key != 'proof_hash'})
    with pytest.raises(subject.RefArmSchemaError, match='schema_proof_mismatch'):
        subject.check_ref_schema_proof(proof, local)
