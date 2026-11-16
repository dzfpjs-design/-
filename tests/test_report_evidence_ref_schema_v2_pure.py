"""Synthetic pure proposal tests. No provider, IO protocol or integration claims."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from generic_agents import report_evidence_ref_schema_v2 as compiler


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def obj(properties):
    return {'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}


def schema(slots=2):
    claim = obj({'kind': {'type': 'string', 'enum': ['职责', '其他']},
        'statement': {'type': 'string', 'minLength': 1, 'maxLength': 600},
        'span_refs': {'type': 'array', 'items': {'type': 'string',
            'minLength': 1, 'maxLength': 250}, 'minItems': 1, 'maxItems': 64},
        'status': {'type': 'string', 'enum': ['explicit', 'inferred', 'unknown']},
        'explanation': {'type': 'string', 'minLength': 0, 'maxLength': 600}})
    claim['title'] = 'Synthetic claim'
    value = obj({f'slot_{i:02d}': {'$ref': '#/$defs/Slot'} for i in range(slots)})
    value['$defs'] = {
        'Claim': claim,
        'Absent': obj({'kind': {'type': 'string', 'const': 'absent'}}),
        'Present': obj({'kind': {'type': 'string', 'const': 'present'},
            'claim': {'$ref': '#/$defs/Claim'}}),
        'Slot': {'anyOf': [{'$ref': '#/$defs/Absent'}, {'$ref': '#/$defs/Present'}]},
    }
    return value


def expanded_provider(node, definitions):
    if type(node) is list:
        return [expanded_provider(value, definitions) for value in node]
    if type(node) is not dict:
        return node
    if '$ref' in node:
        assert set(node) == {'$ref'}
        name = node['$ref'][len('#/$def/'):].replace('~1', '/').replace('~0', '~')
        return expanded_provider(definitions[name], definitions)
    return {key: expanded_provider(value, definitions)
        for key, value in node.items() if key != '$def'}


def reordered(value):
    if type(value) is dict:
        return {key: reordered(value[key]) for key in reversed(list(value))}
    if type(value) is list:
        return [reordered(item) for item in value]
    return value


def test_eighty_slots_retain_references_and_audit_each_definition_once():
    from generic_agents.report_evidence_strict_map import compile_schema
    local = schema(80)
    before = copy.deepcopy(local)
    proof = compiler.compile_ref_schema(local)
    provider, audit = proof['provider_schema'], proof['audit']
    assert local == before
    assert len(provider['$def']) == 4
    assert all(item == {'$ref': '#/$def/Slot'} for item in provider['properties'].values())
    assert audit['counts']['definitions'] == 4
    assert audit['counts']['references'] == 83
    removals = [item for item in audit['transformations']
        if item['operation'] == 'remove_provider_unsupported_bound']
    assert len(removals) == 8
    assert len({item['path'] for item in removals}) == 8
    assert all(item['path'].startswith('/$defs/Claim/') for item in removals)
    legacy = compile_schema(local)['provider_schema']
    assert expanded_provider(provider, provider['$def']) == legacy
    assert len(encoded(provider)) * 4 < len(encoded(legacy))
    assert len(encoded(provider)) < compiler.MAX_SCHEMA_BYTES
    assert compiler.check_ref_schema_proof(proof, local) == proof


def test_hashes_are_separate_reproducible_and_key_order_independent():
    local = schema()
    proof = compiler.compile_ref_schema(local)
    assert proof == compiler.compile_ref_schema(reordered(local))
    assert set(proof) == {'schema', 'local_schema_hash', 'provider_schema',
        'provider_schema_hash', 'audit', 'audit_hash', 'proof_hash'}
    assert proof['schema'] == compiler.PROOF_SCHEMA
    assert proof['local_schema_hash'] == digest(local)
    assert proof['provider_schema_hash'] == digest(proof['provider_schema'])
    assert proof['audit_hash'] == digest(proof['audit'])
    assert proof['proof_hash'] == digest({key: value for key, value in proof.items()
        if key != 'proof_hash'})
    assert len({proof[key] for key in ('local_schema_hash',
        'provider_schema_hash', 'audit_hash', 'proof_hash')}) == 4


def test_escaped_definition_and_property_names_round_trip_without_aliasing():
    name = '作者/a~b'
    token = name.replace('~', '~0').replace('/', '~1')
    local = obj({'x/y~z': {'$ref': '#/$defs/' + token}})
    local['$defs'] = {name: obj({'value': {'type': 'string', 'const': '原文'}})}
    proof = compiler.compile_ref_schema(local)
    assert proof['provider_schema']['properties']['x/y~z'] == {'$ref': '#/$def/' + token}
    assert proof['provider_schema']['$def'][name]['properties']['value']['enum'] == ['原文']
    assert any(item['path'] == '/properties/x~1y~0z/$ref'
        for item in proof['audit']['transformations'])


@pytest.mark.parametrize('ref', ['https://example.invalid/schema', '#', '#/$defs/Missing',
    '#/$def/Claim', '#/$defs/Claim/properties/kind', '#/$defs/Claim~2', '#/$defs/'])
def test_external_dangling_noncanonical_and_nested_references_fail(ref):
    local = schema()
    local['properties']['slot_00'] = {'$ref': ref}
    with pytest.raises(compiler.RefSchemaError, match='external_or_unknown_ref'):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('sibling,value', [('description', 'annotation'),
    ('title', 'annotation'), ('type', 'object'), ('maxLength', 10)])
def test_every_ref_sibling_is_rejected(sibling, value):
    local = schema()
    local['properties']['slot_00'][sibling] = value
    with pytest.raises(compiler.RefSchemaError, match='unsupported_ref_sibling'):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('unused,mutual', [(False, False), (False, True), (True, True)])
def test_self_mutual_and_unused_cycles_are_rejected(unused, mutual):
    local = obj({} if unused else {'value': {'$ref': '#/$defs/A'}})
    local['$defs'] = {'A': {'$ref': '#/$defs/B' if mutual else '#/$defs/A'}}
    if mutual:
        local['$defs']['B'] = {'$ref': '#/$defs/A'}
    with pytest.raises(compiler.RefSchemaError, match='cyclic_schema_ref'):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('node', [
    {'type': 'null'}, {'type': ['string', 'null']},
    {'type': 'string', 'default': 'guess'}, {'type': 'object', 'properties': {},
        'required': [], 'additionalProperties': True},
    {'oneOf': [{'type': 'string'}]}, {'type': 'string', 'format': 'date-time'},
    {'type': 'array', 'prefixItems': []}, {'type': 'string', '$defs': {}},
    {'type': 'string', '$def': {}}, {'type': 'string', 'nullable': True},
])
def test_unsupported_shapes_and_unused_definitions_remain_reviewed(node):
    local = obj({})
    local['$defs'] = {'Unused': node}
    with pytest.raises(compiler.RefSchemaError):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('properties,required,additional', [
    ({'x': {'type': 'string'}}, [], False),
    ({'x': {'type': 'string'}}, ['x', 'x'], False),
    ({'x': {'type': 'string'}}, ['x', 'y'], False),
    ({'x': {'type': 'string'}}, ['x'], {}),
])
def test_closed_object_fields_must_all_be_required(properties, required, additional):
    local = {'type': 'object', 'properties': properties, 'required': required,
        'additionalProperties': additional}
    with pytest.raises(compiler.RefSchemaError, match='strict_object_required'):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('node', [
    {'type': 'integer', 'enum': [True]}, {'type': 'string', 'enum': ['x', 'x']},
    {'type': 'string', 'const': 'x', 'enum': ['y']},
    {'type': 'string', 'minLength': True},
    {'type': 'array', 'items': {'type': 'string'}, 'minItems': 2, 'maxItems': 1},
    {'type': 'integer', 'default': 1.0}, {'type': 'number', 'minimum': True},
    {'type': 'number', 'multipleOf': 0}, {'type': 'number', 'maximum': float('inf')},
])
def test_invalid_enum_bounds_and_bool_number_values_are_not_repaired(node):
    with pytest.raises(compiler.RefSchemaError):
        compiler.compile_ref_schema(obj({'value': node}))


def test_numeric_constraints_and_enum_meanings_are_preserved():
    node = {'type': 'integer', 'enum': [0, 1], 'default': 0,
        'minimum': 0, 'maximum': 4, 'exclusiveMinimum': -1,
        'exclusiveMaximum': 5, 'multipleOf': 1}
    local = obj({'index': node, 'flag': {'type': 'boolean', 'enum': [False, True]}})
    proof = compiler.compile_ref_schema(local)
    assert proof['provider_schema']['properties']['index'] == node
    assert proof['provider_schema']['properties']['flag']['enum'] == [False, True]


def test_logical_reference_depth_is_bounded_without_expansion():
    local = obj({'value': {'$ref': '#/$defs/N0'}})
    local['$defs'] = {f'N{i}': {'$ref': f'#/$defs/N{i + 1}'} for i in range(31)}
    local['$defs']['N31'] = {'type': 'boolean'}
    with pytest.raises(compiler.RefSchemaError, match='schema_graph_capacity'):
        compiler.compile_ref_schema(local)
    local['$defs']['N30'] = {'type': 'boolean'}
    del local['$defs']['N31']
    proof = compiler.compile_ref_schema(local)
    assert proof['audit']['counts']['max_logical_depth'] == 32


def test_stored_node_capacity_is_checked_even_when_bytes_and_depth_fit():
    nodes = [{'type': 'boolean'} for _ in range(10000)]
    while len(nodes) > 1:
        nodes = [{'anyOf': nodes[i:i + 16]} for i in range(0, len(nodes), 16)]
    local = obj({'value': nodes[0]})
    assert len(encoded(local)) < compiler.MAX_SCHEMA_BYTES
    with pytest.raises(compiler.RefSchemaError, match='schema_graph_capacity'):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('mode', ['bytes', 'definitions', 'depth'])
def test_schema_capacity_failures_are_explicit(mode):
    local = obj({'value': {'type': 'string'}})
    if mode == 'bytes':
        local['description'] = 'x' * compiler.MAX_SCHEMA_BYTES
        code = 'schema_capacity_or_shape'
    elif mode == 'definitions':
        local['$defs'] = {f'N{i}': {'type': 'boolean'} for i in range(129)}
        code = 'schema_definitions'
    else:
        node = {'type': 'boolean'}
        for _ in range(32):
            node = {'type': 'array', 'items': node}
        local['properties']['value'] = node
        code = 'schema_graph_capacity'
    with pytest.raises(compiler.RefSchemaError, match=code):
        compiler.compile_ref_schema(local)


@pytest.mark.parametrize('mutation', ['local', 'provider', 'audit', 'hash', 'extra'])
def test_cold_rebuild_rejects_even_publicly_resigned_changed_proofs(mutation):
    local = schema()
    proof = compiler.compile_ref_schema(local)
    if mutation == 'local':
        local['$defs']['Claim']['properties']['kind']['enum'].reverse()
    elif mutation == 'provider':
        proof['provider_schema']['$def']['Claim']['properties']['kind']['enum'] = ['其他']
        proof['provider_schema_hash'] = digest(proof['provider_schema'])
    elif mutation == 'audit':
        proof['audit']['transformations'].pop()
        proof['audit_hash'] = digest(proof['audit'])
    elif mutation == 'hash':
        proof['local_schema_hash'] = '0' * 64
    else:
        proof['provider_accepted'] = True
    proof['proof_hash'] = digest({key: value for key, value in proof.items()
        if key != 'proof_hash'})
    with pytest.raises(compiler.RefSchemaError, match='schema_proof_mismatch'):
        compiler.check_ref_schema_proof(proof, local)
