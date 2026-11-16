"""Pure indexed-input manifest and exact reconciliation fragment for V178.

Callers supply already checked, ordered source claims and the actual frozen
snapshot hash. This codec cannot prove source ownership, HTTP delivery, candidate
semantics, quality, admission or execution authority. It neither expands model
groups nor repairs, sorts, drops or renumbers an original model answer.

Only model_payload belongs in a future model input. The independent platform
manifest retains hashes. Its fragment byte count does NOT prove the complete
HTTP request fits: all context/spans/schema overhead must still pass the real,
unchanged request-size and cost gates. There is no I/O or provider call here.
"""
import copy
import hashlib
import json
import math
import re
from functools import wraps

SCHEMA = 'report-indexed-inputs-v178'
MODEL_PAYLOAD_SCHEMA = 'report-indexed-model-inputs-v178'
RECONCILIATION_SCHEMA = 'report-indexed-reconciliation-v178'
MAX_INPUTS = 1920
KINDS = ('global_claims', 'businesses', 'objects', 'relationships', 'questions')
DISPOSITIONS = ('kept', 'duplicate', 'uncertain')
_HASH = re.compile(r'^[0-9a-f]{64}$')
_GROUP_KEYS = {'output_kind', 'output_index', 'disposition', 'input_positions', 'reason'}
_MANIFEST_KEYS = {'schema', 'source_snapshot_hash', 'input_count', 'input_claims_hash',
    'ordered_claim_hashes', 'input_claims', 'model_payload_hash', 'model_payload_bytes', 'inputs_hash'}


class IndexedInputsError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('原编号输入或模型完整对照不符合合同；未排序、修写或采用：' + code)


def _need(condition, code):
    if not condition: raise IndexedInputsError(code)


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except IndexedInputsError: raise
        except (TypeError, ValueError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise IndexedInputsError('invalid_structure') from None
    return checked


def _json(value, ancestors=()):
    kind = type(value)
    if value is None or kind in {bool, int}: return
    if kind is float:
        _need(math.isfinite(value), 'nonfinite_json'); return
    if kind is str:
        _need('\x00' not in value, 'invalid_string')
        value.encode('utf-8', errors='strict'); return
    _need(kind in {dict, list} and not any(value is prior for prior in ancestors), 'non_json_value')
    parents = (*ancestors, value)
    if kind is dict:
        for key, item in value.items():
            _need(type(key) is str, 'non_string_key'); _json(key, parents); _json(item, parents)
    else:
        for item in value: _json(item, parents)


@_checked
def canonical(value):
    _json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value): return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def _same(left, right): return canonical(left) == canonical(right)


def _inputs(input_claims, source_snapshot_hash):
    _need(type(source_snapshot_hash) is str and _HASH.fullmatch(source_snapshot_hash) is not None,
        'source_snapshot_hash')
    _need(type(input_claims) is list and 1 <= len(input_claims) <= MAX_INPUTS
        and all(type(claim) is dict for claim in input_claims), 'ordered_input_claims')
    canonical(input_claims)
    return copy.deepcopy(input_claims)


def _model_payload(claims, snapshot_hash):
    return {'schema': MODEL_PAYLOAD_SCHEMA, 'source_snapshot_hash': snapshot_hash,
        'input_count': len(claims), 'input_claims': [
            {'input_position': position, 'claim': copy.deepcopy(claim)}
            for position, claim in enumerate(claims)]}


@_checked
def build_indexed_inputs(input_claims, source_snapshot_hash):
    """Bind exact positional copies; do not merge new fields into raw claims."""
    claims = _inputs(input_claims, source_snapshot_hash)
    hashes = [digest(claim) for claim in claims]
    payload = _model_payload(claims, source_snapshot_hash)
    value = {'schema': SCHEMA, 'source_snapshot_hash': source_snapshot_hash,
        'input_count': len(claims), 'input_claims_hash': digest(claims),
        'ordered_claim_hashes': hashes, 'input_claims': [
            {'input_position': position, 'claim_hash': hashes[position], 'claim': copy.deepcopy(claim)}
            for position, claim in enumerate(claims)],
        'model_payload_hash': digest(payload),
        'model_payload_bytes': len(canonical(payload).encode('utf-8'))}
    return {**value, 'inputs_hash': digest(value)}


@_checked
def check_indexed_inputs(indexed, *, input_claims, source_snapshot_hash):
    """Rebuild against actual caller-held source, never a self-resealed count."""
    expected = build_indexed_inputs(input_claims, source_snapshot_hash)
    _need(type(indexed) is dict and set(indexed) == _MANIFEST_KEYS
        and _same(indexed, expected), 'indexed_inputs_mismatch')
    return expected


@_checked
def model_payload(indexed, *, input_claims, source_snapshot_hash):
    """Only explicit positions and full claims; no duplicated proof hashes.

    A fragment larger than the real HTTP cap is not truncated here. The complete
    future request must be measured and rejected by its existing hard cap.
    """
    frozen = check_indexed_inputs(indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    value = _model_payload(_inputs(input_claims, source_snapshot_hash), source_snapshot_hash)
    _need(digest(value) == frozen['model_payload_hash']
        and len(canonical(value).encode('utf-8')) == frozen['model_payload_bytes'], 'model_payload_mismatch')
    return value


@_checked
def reconciliation_schema(indexed, *, input_claims, source_snapshot_hash):
    """Independent count/groups fragment, not a complete V166 candidate schema.

    JSON Schema bounds positions/array size. Strict Python validation below is
    also required for ascending order, cross-group uniqueness and exact cover.
    This schema is not an old native schema proof or provider registration.
    """
    frozen = check_indexed_inputs(indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    count = frozen['input_count']
    return {'title': RECONCILIATION_SCHEMA, 'type': 'object', 'additionalProperties': False,
        'required': ['input_count', 'reconciliation_groups'], 'properties': {
            'input_count': {'type': 'integer', 'const': count},
            'reconciliation_groups': {'type': 'array', 'minItems': 1, 'maxItems': count,
                'items': {'type': 'object', 'additionalProperties': False,
                    'required': ['output_kind', 'output_index', 'disposition', 'input_positions', 'reason'],
                    'properties': {'output_kind': {'type': 'string', 'enum': list(KINDS)},
                        'output_index': {'type': 'integer', 'minimum': 0},
                        'disposition': {'type': 'string', 'enum': list(DISPOSITIONS)},
                        'input_positions': {'type': 'array', 'minItems': 1, 'maxItems': count,
                            'uniqueItems': True, 'items': {'type': 'integer', 'minimum': 0, 'maximum': count - 1}},
                        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}}}}}


@_checked
def validate_groups(groups, indexed, *, input_count, input_claims, source_snapshot_hash):
    """Return original group order unchanged; do not expand model answers.

    Positions strictly increase WITHIN each group. Group order itself is the
    original model order and may interleave positions from other output targets.
    Checking actual output targets/references/meaning remains the caller's job.
    """
    frozen = check_indexed_inputs(indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    count = frozen['input_count']
    _need(type(input_count) is int and input_count == count, 'reported_input_count')
    _need(type(groups) is list and 1 <= len(groups) <= count, 'groups_size')
    canonical(groups)
    seen, targets = set(), set()
    for group in groups:
        _need(type(group) is dict and set(group) == _GROUP_KEYS, 'group_shape')
        _need(type(group['output_kind']) is str and group['output_kind'] in KINDS
            and type(group['disposition']) is str and group['disposition'] in DISPOSITIONS
            and type(group['output_index']) is int and group['output_index'] >= 0,
            'group_target')
        target = (group['output_kind'], group['output_index'], group['disposition'])
        _need(target not in targets, 'duplicate_group_target'); targets.add(target)
        reason, positions = group['reason'], group['input_positions']
        _need(type(reason) is str and 1 <= len(reason) <= 1000 and bool(reason.strip()), 'group_reason')
        _need(type(positions) is list and 1 <= len(positions) <= count, 'positions_size')
        previous = -1
        for position in positions:
            _need(type(position) is int and 0 <= position < count, 'position_bounds')
            _need(position > previous, 'positions_not_strictly_increasing')
            _need(position not in seen, 'position_repeated_across_groups')
            seen.add(position); previous = position
    _need(seen == set(range(count)), 'positions_incomplete')
    return copy.deepcopy(groups)


@_checked
def validate_reconciliation(value, indexed, *, input_claims, source_snapshot_hash):
    _need(type(value) is dict and set(value) == {'input_count', 'reconciliation_groups'}, 'reconciliation_shape')
    groups = validate_groups(value['reconciliation_groups'], indexed, input_count=value['input_count'],
        input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    return {'input_count': value['input_count'], 'reconciliation_groups': groups}
