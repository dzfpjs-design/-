"""Frozen pre-V154 codec oracle and public decode equivalence; no ORM or DB.

The private reference below preserves the pre-optimization V131 algorithms. It
deliberately does not call production private helpers: later parser/validation
changes must retain acceptance, original types, canonical bytes and safe errors.
This reference is a codec compatibility oracle, never domain authority or a PG
JSONB implementation. Decimal strings model the public projection-text seam.
"""
import copy
import hashlib
import json
import math
import re
from decimal import Decimal
from uuid import UUID

import pytest

from generic_agents.typed_payload_codec_v131 import (
    TypedPayloadError, canonical_json, decode_payload, encode_payload,
)


OLD_SCHEMA = 'typed-json-payload-v131'
OLD_PREFIX = 'typed-json-v131:'
OLD_LEGACY = 'legacy-jsonb-v1'
OLD_ERROR = '保存内容的原始数值、所属记录或完整性无法核对'
OLD_OWNER_KEYS = {'table', 'field', 'owner_id', 'workspace_id'}
OLD_ENVELOPE_KEYS = {'schema', 'owner', 'marker', 'canonical', 'payload_hash'}
OLD_HASH = re.compile(r'[0-9a-f]{64}')
OWNER = {'table': 'tasks', 'field': 'data', 'owner_id': 'original-owner', 'workspace_id': 'workspace'}
MARKER = OLD_PREFIX + '00000000-0000-4000-8000-000000000001'
OTHER_MARKER = OLD_PREFIX + '00000000-0000-4000-8000-000000000002'


class _OriginalRejected(ValueError):
    pass


def _old_need(condition):
    if not condition:
        raise _OriginalRejected(OLD_ERROR)


def _old_string(value):
    _old_need(type(value) is str and '\x00' not in value)
    value.encode('utf-8', errors='strict')


def _old_json_value(value, ancestors=(), *, decimals=False):
    kind = type(value)
    if value is None or kind is bool or kind is int:
        return
    if kind is float:
        _old_need(math.isfinite(value))
        return
    if decimals and kind is Decimal:
        _old_need(value.is_finite())
        return
    if kind is str:
        _old_string(value)
        return
    _old_need(kind in (dict, list) and not any(value is parent for parent in ancestors))
    parents = (*ancestors, value)
    if kind is dict:
        for key, item in value.items():
            _old_string(key)
            _old_json_value(item, parents, decimals=decimals)
    else:
        for item in value:
            _old_json_value(item, parents, decimals=decimals)


def _old_canonical(value):
    try:
        _old_json_value(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise _OriginalRejected(OLD_ERROR) from None


def _old_owner(owner):
    try:
        _old_need(type(owner) is dict and set(owner) == OLD_OWNER_KEYS)
        for key in ('table', 'field', 'owner_id'):
            _old_string(owner[key])
            _old_need(bool(owner[key]))
        if owner['workspace_id'] is not None:
            _old_string(owner['workspace_id'])
            _old_need(bool(owner['workspace_id']))
        return {key: owner[key] for key in ('table', 'field', 'owner_id', 'workspace_id')}
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _OriginalRejected(OLD_ERROR) from None


def _old_marker(marker, *, allow_legacy=False):
    try:
        _old_need(type(allow_legacy) is bool)
        _old_string(marker)
        if allow_legacy and marker == OLD_LEGACY:
            return marker
        _old_need(marker.startswith(OLD_PREFIX))
        identity = marker[len(OLD_PREFIX):]
        _old_need(str(UUID(identity)) == identity)
        return marker
    except (TypeError, ValueError, UnicodeError, AttributeError):
        raise _OriginalRejected(OLD_ERROR) from None


def _old_pairs(items):
    result = {}
    for key, value in items:
        _old_need(key not in result)
        result[key] = value
    return result


def _old_constant(_value):
    raise _OriginalRejected(OLD_ERROR)


def _old_parse(text, *, decimals=False):
    _old_string(text)
    options = {'object_pairs_hook': _old_pairs, 'parse_constant': _old_constant}
    if decimals:
        options.update(parse_int=Decimal, parse_float=Decimal)
    value = json.loads(text, **options)
    _old_json_value(value, decimals=decimals)
    return value


def _old_same(left, right):
    if type(left) is Decimal or type(right) is Decimal:
        return type(left) is Decimal and type(right) is Decimal and left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_old_same(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_old_same(a, b) for a, b in zip(left, right))
    return left == right


def _old_encode(value, owner=OWNER, marker=MARKER):
    try:
        owner = _old_owner(owner)
        marker = _old_marker(marker)
        canonical = _old_canonical(value)
        return _old_canonical({'schema': OLD_SCHEMA, 'owner': owner, 'marker': marker,
            'canonical': canonical,
            'payload_hash': hashlib.sha256(canonical.encode('utf-8')).hexdigest()})
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise _OriginalRejected(OLD_ERROR) from None


def _old_decode(text, marker, owner, projection_text):
    try:
        owner = _old_owner(owner)
        marker = _old_marker(marker, allow_legacy=True)
        if marker == OLD_LEGACY:
            _old_need(text is None)
            return _old_parse(projection_text)
        envelope = _old_parse(text)
        _old_need(type(envelope) is dict and set(envelope) == OLD_ENVELOPE_KEYS)
        _old_need(envelope['schema'] == OLD_SCHEMA and envelope['marker'] == marker)
        _old_need(_old_owner(envelope['owner']) == owner)
        _old_need(_old_canonical(envelope) == text)
        canonical = envelope['canonical']
        _old_string(canonical)
        value = _old_parse(canonical)
        _old_need(_old_canonical(value) == canonical)
        supplied_hash = envelope['payload_hash']
        _old_need(type(supplied_hash) is str and OLD_HASH.fullmatch(supplied_hash) is not None)
        _old_need(hashlib.sha256(canonical.encode('utf-8')).hexdigest() == supplied_hash)
        _old_need(_old_same(_old_parse(canonical, decimals=True), _old_parse(projection_text, decimals=True)))
        return value
    except (TypeError, ValueError, UnicodeError, ArithmeticError, RecursionError, AttributeError):
        raise _OriginalRejected(OLD_ERROR) from None


def _observe(function, *args):
    try:
        result = function(*args)
    except (_OriginalRejected, TypedPayloadError) as error:
        safe = str(error) == OLD_ERROR
        assert safe, 'rejection must use only the fixed safe message'
        return 'rejected', None
    return 'accepted', _old_canonical(result)


def _assert_equivalent(text, marker=MARKER, owner=OWNER, projection='{}', *, accept=None):
    before = copy.deepcopy(owner)
    expected = _observe(_old_decode, text, marker, owner, projection)
    actual = _observe(decode_payload, text, marker, owner, projection)
    same = actual == expected
    assert same, 'public decode acceptance or typed canonical output differs from the frozen oracle'
    if accept is not None:
        matched = expected[0] == ('accepted' if accept else 'rejected')
        assert matched, 'the explicit compatibility oracle expectation must hold'
    assert owner == before, 'decoding must not change its supplied row owner'
    return actual


def _with_inner(inner):
    """Build outer canonical bytes around arbitrary original inner JSON text."""
    value = json.loads(_old_encode(None))
    value['canonical'] = inner
    value['payload_hash'] = hashlib.sha256(inner.encode('utf-8')).hexdigest()
    return _old_canonical(value)


NUMERIC_CASES = [
    ('int-as-decimal', 1, '1.000', True),
    ('float-as-integer', 1.0, '1', True),
    ('negative-zero-as-integer', -0.0, '0', True),
    ('integer-as-negative-zero', 0, '-0.0', True),
    ('large-float-decimal', 1e21, '1000000000000000000000', True),
    ('decimal-trailing-zeros', 0.1, '0.1000000000', True),
    ('unsafe-integer-exact', 9007199254740993, '9007199254740993.000', True),
    ('huge-integer-exponent', 10 ** 400, '1e400', True),
    ('huge-integer-2048-digits', 10 ** 2047, '1e2047', True),
    ('finite-float-boundary', 1e308, '1.0e308', True),
    ('bool-original', True, 'true', True),
    ('false-original', False, 'false', True),
    ('null-original', None, 'null', True),
    ('zero-exponent-spelling', 0.0, '-0e1000', True),
    ('bool-is-not-one', True, '1', False),
    ('one-is-not-bool', 1, 'true', False),
    ('zero-is-not-false', -0.0, 'false', False),
    ('different-unsafe-integer', 9007199254740993, '9007199254740992', False),
    ('different-decimal', 0.1, '0.10000000000000001', False),
    ('different-float', 0.30000000000000004, '0.3', False),
    ('nonzero-underflow-projection', 0.0, '1e-9999', False),
    ('projection-bool-in-container', {'v': [True]}, '{"v":[1]}', False),
    ('numeric-string-remains-string', '1.0', '"1.0"', True),
    ('numeric-string-is-not-number', '1.0', '1.0', False),
]


@pytest.mark.parametrize('label,value,projection,accept', NUMERIC_CASES,
                         ids=[case[0] for case in NUMERIC_CASES])
def test_exact_numeric_projection_equivalence(label, value, projection, accept):
    text = _old_encode(value)
    same_encoding = encode_payload(value, OWNER, MARKER) == text
    same_canonical = canonical_json(value) == _old_canonical(value)
    assert same_encoding and same_canonical, label
    _assert_equivalent(text, projection=projection, accept=accept)


BAD_JSON = [
    ('duplicate-key', '{"v":1,"v":1}'),
    ('escaped-duplicate-key', '{"v":1,"\\u0076":1}'),
    ('nested-escaped-duplicate', '{"x":[{"v":1,"\\u0076":1}]}'),
    ('nul-escape-value', '"\\u0000"'),
    ('nul-escape-key', '{"\\u0000":1}'),
    ('literal-nul', '"\x00"'),
    ('lone-high-surrogate', '"\\ud800"'),
    ('lone-low-surrogate', '"\\udfff"'),
    ('surrogate-key', '{"\\ud800":1}'),
    ('nan', 'NaN'), ('infinity', 'Infinity'), ('negative-infinity', '-Infinity'),
    ('float-overflow', '1e400'), ('trailing-data', '{} trailing'),
]


@pytest.mark.parametrize('label,raw', BAD_JSON, ids=[case[0] for case in BAD_JSON])
def test_invalid_legacy_and_typed_projection_keep_safe_rejection(label, raw):
    _assert_equivalent(None, OLD_LEGACY, projection=raw, accept=False)
    _assert_equivalent(_old_encode({}), projection=raw, accept=False)


@pytest.mark.parametrize('label,projection', [
    ('extra-nul-key', '{"safe":{"v":1},"\\u0000":0}'),
    ('extra-surrogate-key', '{"safe":{"v":1},"\\ud800":0}'),
    ('extra-nul-value', '{"safe":{"v":1},"extra":"\\u0000"}'),
    ('extra-surrogate-value', '{"safe":{"v":1},"extra":"\\udfff"}'),
], ids=['extra-nul-key', 'extra-surrogate-key', 'extra-nul-value', 'extra-surrogate-value'])
def test_projection_bad_extra_field_is_not_accepted_when_keys_mismatch(label, projection):
    # A projection-only parser may detect the mismatch before visiting extra
    # strings. It must still reject rather than silently drop these fields.
    _assert_equivalent(_old_encode({'safe': {'v': 1.0}}), projection=projection, accept=False)


def test_projection_unicode_escapes_without_duplicate_keys_remain_equivalent():
    value = {'key': {'中文': [1.0, '🙂']}}
    projection = '{"\\u006bey":{"\\u4e2d\\u6587":[1,"\\ud83d\\ude42"]}}'
    _assert_equivalent(_old_encode(value), projection=projection, accept=True)


@pytest.mark.parametrize('depth', [64, 1400], ids=['deep-extra', 'very-deep-extra'])
def test_deep_nonmatching_projection_never_becomes_a_partial_match(depth):
    projection = '{"extra":' + '[' * depth + '1' + ']' * depth + '}'
    _assert_equivalent(_old_encode({'safe': 1.0}), projection=projection, accept=False)


@pytest.mark.parametrize('label,inner', [
    ('duplicate-inner', '{"v":1,"v":1}'),
    ('escaped-duplicate-inner', '{"v":1,"\\u0076":1}'),
    ('noncanonical-integer', '1 '), ('noncanonical-float', '1e0'),
    ('noncanonical-sort', '{"z":1,"a":2}'),
    ('overflow-inner', '1e400'), ('nul-inner', '"\\u0000"'),
    ('surrogate-inner', '"\\ud800"'),
], ids=['duplicate-inner', 'escaped-duplicate-inner', 'space', 'float-spelling',
        'key-sort', 'overflow', 'nul', 'surrogate'])
def test_invalid_original_inner_text_rejects_even_with_matching_hash(label, inner):
    _assert_equivalent(_with_inner(inner), projection=inner, accept=False)


@pytest.mark.parametrize('change', ['schema', 'marker', 'owner', 'canonical-type',
    'hash-value', 'hash-case', 'hash-type', 'extra', 'missing', 'outer-space',
    'outer-order', 'outer-duplicate', 'outer-escaped-duplicate', 'owner-duplicate'])
def test_closed_envelope_owner_hash_and_canonical_bytes(change):
    text = _old_encode({'v': 1.0})
    value = json.loads(text)
    if change == 'schema': value['schema'] = 'typed-json-payload-v154'
    elif change == 'marker': value['marker'] = OTHER_MARKER
    elif change == 'owner': value['owner']['owner_id'] = 'foreign-owner'
    elif change == 'canonical-type': value['canonical'] = {'v': 1.0}
    elif change == 'hash-value': value['payload_hash'] = '0' * 64
    elif change == 'hash-case': value['payload_hash'] = 'A' * 64
    elif change == 'hash-type': value['payload_hash'] = True
    elif change == 'extra': value['extra'] = 'not permitted'
    elif change == 'missing': del value['payload_hash']
    if change == 'outer-space': text += '\n'
    elif change == 'outer-order': text = json.dumps(value, ensure_ascii=False)
    elif change == 'outer-duplicate': text = '{"schema":"ignored",' + text[1:]
    elif change == 'outer-escaped-duplicate': text = '{"\\u0073chema":"ignored",' + text[1:]
    elif change == 'owner-duplicate':
        owner = _old_canonical(OWNER)
        text = text.replace(owner, '{"\\u0066ield":"data",' + owner[1:], 1)
    else: text = _old_canonical(value)
    _assert_equivalent(text, projection='{"v":1}', accept=False)


@pytest.mark.parametrize('field', ['table', 'field', 'owner_id', 'workspace_id'])
def test_external_owner_identity_is_not_a_cache_key_alias(field):
    altered = {**OWNER, field: 'other-identity'}
    _assert_equivalent(_old_encode({'v': 1}), owner=altered, projection='{"v":1}', accept=False)


@pytest.mark.parametrize('marker', [OTHER_MARKER, MARKER.upper(), OLD_PREFIX + 'not-a-uuid',
    'typed-json-v154:00000000-0000-4000-8000-000000000001', None, True],
    ids=['other-valid', 'uppercase', 'bad-uuid', 'unknown-schema', 'missing', 'boolean'])
def test_marker_does_not_downgrade_missing_or_existing_typed_text(marker):
    _assert_equivalent(_old_encode({}), marker=marker, accept=False)
    _assert_equivalent(None, marker=marker, accept=False)


def test_null_workspace_and_valid_unicode_remain_exactly_readable():
    owner = {**OWNER, 'workspace_id': None}
    value = {'🙂': ['中文', '\ue000', True, None, 1, 1.0, -0.0]}
    text = _old_encode(value, owner)
    projection = '{"\\ud83d\\ude42":["中文","\\ue000",true,null,1.000,1,0]}'
    _assert_equivalent(text, owner=owner, projection=projection, accept=True)


def test_legacy_never_mints_original_types_or_accepts_a_typed_envelope():
    source = '{"float":1.0,"integer":1,"zero":0,"large":1000000000000000000000}'
    _assert_equivalent(None, OLD_LEGACY, projection=source, accept=True)
    result = decode_payload(None, OLD_LEGACY, OWNER, source)
    assert type(result['float']) is float and type(result['integer']) is int
    assert type(result['zero']) is int and type(result['large']) is int
    assert set(result) == {'float', 'integer', 'zero', 'large'}
    _assert_equivalent(_old_encode({}), OLD_LEGACY, projection='{}', accept=False)


@pytest.mark.parametrize('depth', [64, 1400], ids=['deep-valid', 'beyond-usual-recursion'])
def test_nested_array_acceptance_tracks_frozen_runtime_oracle(depth):
    inner = '[' * depth + '1.0' + ']' * depth
    # No environment-specific recursion limit is imposed or changed by this
    # test. Both implementations see identical text and the same runtime limit.
    _assert_equivalent(_with_inner(inner), projection=inner, accept=True if depth == 64 else None)


def test_wide_array_decimal_spelling_and_one_changed_leaf():
    values = [{'i': index, 'z': -0.0, 'f': 1.0} for index in range(512)]
    text = _old_encode(values)
    projection = '[' + ','.join('{"i":' + str(index) + '.000,"z":0,"f":1}'
                                for index in range(512)) + ']'
    _assert_equivalent(text, projection=projection, accept=True)
    changed = projection.replace('"i":511.000', '"i":510.000')
    _assert_equivalent(text, projection=changed, accept=False)


def test_overflow_projection_and_runtime_integer_limit_are_not_silent_float_aliases():
    _assert_equivalent(_old_encode(10 ** 400), projection='1e400', accept=True)
    _assert_equivalent(_old_encode(10 ** 400), projection='1e401', accept=False)
    for exponent in ('1e99999999999999999999999', '-1e99999999999999999999999'):
        _assert_equivalent(_old_encode(1), projection=exponent, accept=False)
    original = '9' * 5000
    # Python's configurable int-digit guard is not altered. This records its
    # pre-optimization acceptance/rejection, without claiming a PG size limit.
    _assert_equivalent(_with_inner(original), projection=original)


@pytest.mark.parametrize('legacy', [False, True], ids=['typed', 'legacy'])
def test_repeated_reads_never_share_mutable_containers_or_previous_owner(legacy):
    shared = {'items': [1.0, -0.0, {'flag': True}]}
    value = {'left': shared, 'right': shared}
    projection = _old_canonical(value)
    text, marker = (None, OLD_LEGACY) if legacy else (_old_encode(value), MARKER)
    first = decode_payload(text, marker, OWNER, projection)
    second = decode_payload(text, marker, OWNER, projection)
    assert first is not second and first['left'] is not second['left']
    assert first['left'] is not first['right']
    first['left']['items'][2]['flag'] = False
    first['right']['items'].append('independent edit')
    third = decode_payload(text, marker, OWNER, projection)
    same = _old_canonical(second) == _old_canonical(third) == _old_canonical(value)
    assert same, 'a previous returned mutable tree must not affect a later decode'
    assert third['left']['items'][2]['flag'] is True
    assert type(third['left']['items'][0]) is float
    assert math.copysign(1, third['left']['items'][1]) == -1
    if not legacy:
        _assert_equivalent(text, owner={**OWNER, 'owner_id': 'other-row'}, projection=projection, accept=False)
