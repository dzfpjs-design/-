"""Lossless typed JSON beside a JSONB query projection; no domain authority.

The canonical string is captured before a JSONB write.  Its hash is a storage
check, not a replacement for any original command, receipt or content hash.
Legacy JSONB is readable but can never manufacture a canonical envelope.
"""
import hashlib
import json
import math
import re
from decimal import Decimal
from functools import lru_cache
from uuid import UUID, uuid4


SCHEMA = 'typed-json-payload-v131'
MARKER_PREFIX = 'typed-json-v131:'
LEGACY_MARKER = 'legacy-jsonb-v1'
ERROR = '保存内容的原始数值、所属记录或完整性无法核对'
_OWNER_KEYS = {'table', 'field', 'owner_id', 'workspace_id'}
_ENVELOPE_KEYS = {'schema', 'owner', 'marker', 'canonical', 'payload_hash'}
_HASH = re.compile(r'[0-9a-f]{64}')


class TypedPayloadError(ValueError):
    """Safe failure text; never include payload values or parser diagnostics."""


def _need(condition):
    if not condition:
        raise TypedPayloadError(ERROR)


def _string(value):
    _need(type(value) is str and '\x00' not in value)
    value.encode('utf-8', errors='strict')


def _json_value(value, ancestors=(), *, decimals=False):
    kind = type(value)
    if value is None or kind is bool or kind is int:
        return
    if kind is float:
        _need(math.isfinite(value))
        return
    if decimals and kind is Decimal:
        _need(value.is_finite())
        return
    if kind is str:
        _string(value)
        return
    _need(kind in (dict, list))
    # One active-path set per validation; never cache by identity or across calls.
    active = ancestors if type(ancestors) is set else {id(parent) for parent in ancestors}
    identity = id(value)
    _need(identity not in active)
    active.add(identity)
    try:
        if kind is dict:
            for key, item in value.items():
                _string(key)
                _json_value(item, active, decimals=decimals)
        else:
            for item in value:
                _json_value(item, active, decimals=decimals)
    finally:
        active.remove(identity)


def canonical_json(value):
    """Use contracts.digest's exact JSON spelling for valid JSON values."""
    try:
        _json_value(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise TypedPayloadError(ERROR) from None


def validate_owner(owner):
    """Validate the closed actual-row identity and return a detached mapping."""
    try:
        _need(type(owner) is dict and set(owner) == _OWNER_KEYS)
        for key in ('table', 'field', 'owner_id'):
            _string(owner[key])
            _need(bool(owner[key]))
        if owner['workspace_id'] is not None:
            _string(owner['workspace_id'])
            _need(bool(owner['workspace_id']))
        return {key: owner[key] for key in ('table', 'field', 'owner_id', 'workspace_id')}
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise TypedPayloadError(ERROR) from None


def validate_marker(marker, *, allow_legacy=False):
    try:
        _need(type(allow_legacy) is bool)
        _string(marker)
        if allow_legacy and marker == LEGACY_MARKER:
            return marker
        _need(marker.startswith(MARKER_PREFIX))
        identity = marker[len(MARKER_PREFIX):]
        _need(str(UUID(identity)) == identity)
        return marker
    except (TypeError, ValueError, UnicodeError, AttributeError):
        raise TypedPayloadError(ERROR) from None


def new_marker():
    return MARKER_PREFIX + str(uuid4())


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise TypedPayloadError(ERROR)


def _parse(text, *, decimals=False):
    _string(text)
    options = {'object_pairs_hook': _pairs, 'parse_constant': _constant}
    if decimals:
        # Compare the actual JSON decimal spellings, not rounded Python floats.
        options.update(parse_int=Decimal, parse_float=Decimal)
    value = json.loads(text, **options)
    _json_value(value, decimals=decimals)
    return value


def _dump_validated(value):
    # Only local values just returned by _parse reach this helper. Their exact
    # JSON types, finite numbers, strings and keys have already been checked.
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)


def _projection_parse(text):
    # Its result is never returned to a caller. It must match the already
    # validated original tree exactly; unequal or extra strings/keys reject.
    # Duplicate keys, constants and malformed source text still fail here.
    _string(text)
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant,
        parse_int=Decimal, parse_float=Decimal)


def _same_projection(left, right):
    # Decimal is deliberately distinct from bool.  JSONB can erase integer /
    # float spelling and the sign of zero; it cannot turn true into numeric 1.
    if type(left) is Decimal or type(right) is Decimal:
        return (type(left) is Decimal and type(right) is Decimal
            and left.is_finite() and right.is_finite() and left == right)
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_same_projection(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same_projection(a, b) for a, b in zip(left, right))
    return left == right


def encode_payload(value, owner, marker):
    try:
        owner = validate_owner(owner)
        marker = validate_marker(marker)
        canonical = canonical_json(value)
        envelope = {'schema': SCHEMA, 'owner': owner, 'marker': marker,
            'canonical': canonical,
            'payload_hash': hashlib.sha256(canonical.encode('utf-8')).hexdigest()}
        return canonical_json(envelope)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise TypedPayloadError(ERROR) from None


def _decode_payload_uncached(text, marker, owner, projection_text):
    """Recover only from original TEXT; JSONB supplies a semantic cross-check.

    A legacy return is exactly the old JSON parse, without certification or any
    write.  A typed marker must never fall back after missing or corrupt TEXT.
    """
    try:
        owner = validate_owner(owner)
        marker = validate_marker(marker, allow_legacy=True)
        if marker == LEGACY_MARKER:
            _need(text is None)
            return _parse(projection_text)
        envelope = _parse(text)
        _need(type(envelope) is dict and set(envelope) == _ENVELOPE_KEYS)
        _need(envelope['schema'] == SCHEMA and envelope['marker'] == marker)
        _need(validate_owner(envelope['owner']) == owner)
        _need(_dump_validated(envelope) == text)
        canonical = envelope['canonical']
        _string(canonical)
        value = _parse(canonical)
        _need(_dump_validated(value) == canonical)
        supplied_hash = envelope['payload_hash']
        _need(type(supplied_hash) is str and _HASH.fullmatch(supplied_hash) is not None)
        _need(hashlib.sha256(canonical.encode('utf-8')).hexdigest() == supplied_hash)
        _need(_same_projection(_projection_parse(canonical), _projection_parse(projection_text)))
        return value
    except (TypeError, ValueError, UnicodeError, ArithmeticError, RecursionError, AttributeError):
        raise TypedPayloadError(ERROR) from None


_DECODE_CACHE_MAX_BYTES = 1024 * 1024


@lru_cache(maxsize=16)
def _verified_decode_json(text, marker, owner_values, projection_text):
    """Only exact complete successful storage checks enter this bounded cache.

    Keys retain both full storage strings and actual row identity, never just a
    payload hash. Cached JSON is immutable; no ORM or permission is retained.
    """
    owner = dict(zip(('table', 'field', 'owner_id', 'workspace_id'), owner_values))
    value = _decode_payload_uncached(text, marker, owner, projection_text)
    return _dump_validated(value)


def decode_payload(text, marker, owner, projection_text):
    """Decode the actual row, reusing only byte-identical prior storage proof.

    Every call returns a newly parsed tree. A different row owner, marker,
    envelope or JSONB spelling gets the complete original checks. Large values,
    legacy rows and non-string inputs retain the original uncached path.
    Exceptions are never cached. This does not cache a database read or grant.
    """
    checked_owner = validate_owner(owner)
    checked_marker = validate_marker(marker, allow_legacy=True)
    if (checked_marker == LEGACY_MARKER or type(text) is not str
            or type(projection_text) is not str):
        return _decode_payload_uncached(text, marker, checked_owner, projection_text)
    try:
        cacheable = len(text.encode('utf-8')) + len(projection_text.encode('utf-8')) <= _DECODE_CACHE_MAX_BYTES
    except UnicodeError:
        cacheable = False
    if not cacheable:
        return _decode_payload_uncached(text, marker, checked_owner, projection_text)
    owner_values = tuple(checked_owner[key] for key in ('table', 'field', 'owner_id', 'workspace_id'))
    verified = _verified_decode_json(text, checked_marker, owner_values, projection_text)
    try:
        return json.loads(verified)
    except (TypeError, ValueError, UnicodeError, ArithmeticError, RecursionError, AttributeError):
        raise TypedPayloadError(ERROR) from None
