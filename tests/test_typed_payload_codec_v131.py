"""Pure codec contracts; no database, migration, service or ORM authority mocks."""
import copy
import hashlib
import json
import math
from decimal import Decimal
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import (
    ERROR, LEGACY_MARKER, MARKER_PREFIX, SCHEMA, TypedPayloadError,
    canonical_json, decode_payload, encode_payload, new_marker,
    validate_marker, validate_owner,
)


OWNER = {'table': 'tasks', 'field': 'data', 'owner_id': 'original-task', 'workspace_id': 'workspace-a'}
MARKER = MARKER_PREFIX + '00000000-0000-4000-8000-000000000001'


def envelope(value):
    return encode_payload(value, OWNER, MARKER)


def changed(text, **updates):
    value = json.loads(text)
    value.update(updates)
    return canonical_json(value)


def rejected(function, *args, **kwargs):
    with pytest.raises(TypedPayloadError) as error:
        function(*args, **kwargs)
    assert str(error.value) == ERROR


def test_canonical_spelling_is_exactly_existing_digest_and_python_codepoint_sort():
    value = {'🙂': 1e21, '\ue000': -0.0, 'nested': {'integer': 1, 'float': 1.0, 'fraction': 0.1}}
    before = copy.deepcopy(value)
    raw = canonical_json(value)
    assert raw == json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    assert hashlib.sha256(raw.encode('utf-8')).hexdigest() == digest(value)
    assert raw.index('\ue000') < raw.index('🙂')
    assert '-0.0' in raw and '1e+21' in raw and '"float":1.0' in raw
    assert value == before and math.copysign(1, value['\ue000']) == -1


def test_closed_envelope_and_detached_typed_roundtrip_against_lossy_jsonb_spelling():
    original = {'float': 1.0, 'negative_zero': -0.0, 'large_float': 1e21,
        'array': [True, None, 0.1, {'中文': '保留'}]}
    owner_before = copy.deepcopy(OWNER)
    text = envelope(original)
    stored = json.loads(text)
    assert set(stored) == {'schema', 'owner', 'marker', 'canonical', 'payload_hash'}
    assert stored['schema'] == SCHEMA and stored['owner'] == OWNER and stored['marker'] == MARKER
    assert stored['payload_hash'] == digest(original)
    assert text == canonical_json(stored)
    projection = '{"array":[true,null,0.1,{"中文":"保留"}],"negative_zero":0,"large_float":1000000000000000000000,"float":1}'
    result = decode_payload(text, MARKER, OWNER, projection)
    assert type(result['float']) is float and type(result['large_float']) is float
    assert math.copysign(1, result['negative_zero']) == -1
    assert canonical_json(result) == canonical_json(original)
    result['array'][3]['中文'] = '独立改稿'
    assert original['array'][3]['中文'] == '保留' and OWNER == owner_before
    assert decode_payload(text, MARKER, OWNER, projection)['array'][3]['中文'] == '保留'


@pytest.mark.parametrize(('value', 'projection', 'expected_type'), [
    (1, '1.0', int), (1.0, '1', float), (-0.0, '0', float), (0, '-0.0', int),
    (1e21, '1000000000000000000000', float), (0.1, '0.100000', float),
    (9007199254740993, '9007199254740993.000', int),
    (10 ** 300, '1e300', int), (True, 'true', bool), (None, 'null', type(None)),
    ('1.0', '"1.0"', str), ([], '[]', list), ({}, '{}', dict),
])
def test_projection_numeric_equivalence_never_replaces_the_original_python_type(value, projection, expected_type):
    result = decode_payload(envelope(value), MARKER, OWNER, projection)
    assert type(result) is expected_type and canonical_json(result) == canonical_json(value)


@pytest.mark.parametrize(('value', 'projection'), [
    (True, '1'), (1, 'true'), (False, '0.0'), (0, 'false'),
    (9007199254740993, '9007199254740992'),
    (0.30000000000000004, '0.3'), (0.1, '0.10000000000000001'),
    ([1, 2], '[2,1]'), ({'v': 1}, '{"v":1,"extra":0}'),
    ({'v': [True]}, '{"v":[1]}'), ('1', '1'), (None, '"null"'),
])
def test_projection_is_exact_for_large_integers_containers_and_bool(value, projection):
    rejected(decode_payload, envelope(value), MARKER, OWNER, projection)


def test_owner_and_marker_are_exact_and_return_no_mutable_input_alias():
    result = validate_owner(OWNER)
    result['owner_id'] = 'changed'
    assert OWNER['owner_id'] == 'original-task'
    nullable = {**OWNER, 'workspace_id': None}
    assert decode_payload(encode_payload({'a': 1}, nullable, MARKER), MARKER, nullable, '{"a":1}') == {'a': 1}
    markers = {new_marker() for _ in range(4)}
    assert len(markers) == 4
    for marker in markers:
        assert validate_marker(marker) == marker
        assert UUID(marker[len(MARKER_PREFIX):]).version == 4
    assert validate_marker(LEGACY_MARKER, allow_legacy=True) == LEGACY_MARKER
    rejected(validate_marker, LEGACY_MARKER)
    rejected(encode_payload, {}, OWNER, LEGACY_MARKER)


@pytest.mark.parametrize('owner', [None, [], {}, {**OWNER, 'extra': 1},
    {**OWNER, 'table': ''}, {**OWNER, 'field': False}, {**OWNER, 'owner_id': 1},
    {**OWNER, 'workspace_id': ''}, {**OWNER, 'workspace_id': False},
    {**OWNER, 'owner_id': '\ud800'}, {**OWNER, 'table': 'private\x00table'}])
def test_invalid_owner_is_rejected_with_safe_text(owner):
    rejected(validate_owner, owner)
    rejected(encode_payload, {}, owner, MARKER)


@pytest.mark.parametrize('marker', [None, False, 1, '', 'unknown', 'typed-json-v132:x',
    MARKER.upper(), MARKER + ' ', MARKER_PREFIX + '00000000000040008000000000000001'])
def test_missing_unknown_or_noncanonical_marker_never_downgrades(marker):
    rejected(validate_marker, marker, allow_legacy=True)
    rejected(decode_payload, None, marker, OWNER, '{}')


@pytest.mark.parametrize('field', ['table', 'field', 'owner_id', 'workspace_id'])
def test_foreign_owner_or_marker_cannot_reuse_a_valid_text(field):
    text = envelope({'v': 1.0})
    rejected(decode_payload, text, MARKER, {**OWNER, field: 'foreign'}, '{"v":1}')
    rejected(decode_payload, text, MARKER_PREFIX + '00000000-0000-4000-8000-000000000002', OWNER, '{"v":1}')


def test_legacy_is_read_only_unsealed_and_does_not_infer_original_float_types():
    source = '{"n":1000000000000000000000,"z":0,"f":1.0}'
    result = decode_payload(None, LEGACY_MARKER, OWNER, source)
    assert result == json.loads(source)
    assert type(result['n']) is int and type(result['z']) is int and type(result['f']) is float
    result['n'] = 0
    assert decode_payload(None, LEGACY_MARKER, OWNER, source)['n'] == 10 ** 21
    rejected(decode_payload, envelope({'n': 1}), LEGACY_MARKER, OWNER, '{"n":1}')
    rejected(decode_payload, None, MARKER, OWNER, '{}')
    rejected(decode_payload, '', MARKER, OWNER, '{}')


@pytest.mark.parametrize('patch', [
    {'schema': 'typed-json-payload-v132'}, {'owner': {**OWNER, 'extra': 1}},
    {'marker': LEGACY_MARKER}, {'canonical': {}}, {'canonical': '1.0 '},
    {'canonical': '{"a":1,"a":2}'}, {'canonical': 'NaN'},
    {'payload_hash': '0' * 64}, {'payload_hash': True}, {'payload_hash': None},
    {'payload_hash': 'A' * 64}, {'extra': 'not permitted'},
])
def test_envelope_and_inner_canonical_tampering_or_partial_fields_are_rejected(patch):
    rejected(decode_payload, changed(envelope({'v': 1.0}), **patch), MARKER, OWNER, '{"v":1}')


@pytest.mark.parametrize('key', ['schema', 'owner', 'marker', 'canonical', 'payload_hash'])
def test_every_envelope_field_is_required(key):
    value = json.loads(envelope({}))
    del value[key]
    rejected(decode_payload, canonical_json(value), MARKER, OWNER, '{}')


def test_outer_whitespace_key_order_and_duplicate_keys_are_not_canonical_text():
    text = envelope({'v': 1})
    for other in (' ' + text, text + '\n', json.dumps(json.loads(text), ensure_ascii=False),
            '{"schema":"ignored",' + text[1:]):
        rejected(decode_payload, other, MARKER, OWNER, '{"v":1}')
    # A duplicate written with a Unicode escape is still the same decoded key.
    rejected(decode_payload, '{"schema":"ignored","\\u0073chema":"ignored"}', MARKER, OWNER, '{}')


@pytest.mark.parametrize('projection', ['NaN', 'Infinity', '-Infinity', '{"x":1,"x":1}',
    '{"x":1,"\\u0078":1}', '"\\u0000"', '"\\ud800"', '"\x00"', '{} trailing', b'{}'])
def test_invalid_projection_is_rejected_for_both_typed_and_legacy(projection):
    rejected(decode_payload, envelope({}), MARKER, OWNER, projection)
    rejected(decode_payload, None, LEGACY_MARKER, OWNER, projection)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'),
    {'secret': '\x00'}, {'\udfff': 1}, ['\ud800'], {'nested': [float('nan')]},
    {1: 'not a JSON string key'}, Decimal('1.0'), (1, 2), b'private bytes'])
def test_invalid_python_json_never_leaks_values_in_errors(value):
    rejected(canonical_json, value)
    rejected(encode_payload, value, OWNER, MARKER)


def test_cycles_bad_text_and_safe_errors_leave_inputs_untouched():
    cyclic = []
    cyclic.append(cyclic)
    rejected(canonical_json, cyclic)
    assert cyclic[0] is cyclic
    for text in (None, b'private', '"\ud800"', '\x00', '{bad private input'):
        rejected(decode_payload, text, MARKER, OWNER, '{}')
    original = {'nested': [1, {'zero': -0.0}], 'flag': True}
    before = copy.deepcopy(original)
    text = envelope(original)
    rejected(decode_payload, text, MARKER, OWNER, '{"nested":[1,{"zero":0}],"flag":1}')
    assert original == before and canonical_json(original) == canonical_json(before)
