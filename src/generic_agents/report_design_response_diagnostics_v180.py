"""Bounded, non-authorizing counts over original indexed design arguments.

The caller supplies the ACTUAL cold-source expected_count and original argument
bytes. This cannot verify HTTP termination, original byte authenticity, source
ownership/references, semantic quality, exact reconciliation or adoption rights.
It never returns, repairs or constructs a candidate. Indexed V178 and V179 share
the same full local argument shape; no family or source authority is inferred.
"""
import json

from .report_design_grouped_wire_v178 import DesignGroupedWireV178, MAX_INPUTS, MAX_WIRE_BYTES
from .report_evidence_reduction_wire_v168 import _bytes


SCHEMA = 'report-design-response-diagnostics-v180'
MAX_ARGUMENT_BYTES = MAX_WIRE_BYTES
PROBLEMS = frozenset({'expected_count_invalid', 'arguments_unavailable', 'arguments_too_large',
    'invalid_json', 'invalid_wire_shape', 'declared_count_mismatch', 'position_out_of_range',
    'diagnostic_unavailable'})
_COUNTS = ('declared_count', 'covered_count', 'missing_count', 'duplicate_count',
    'out_of_range_count', 'business_count', 'step_count')


def _expected(value):
    return type(value) is int and 1 <= value <= MAX_INPUTS


def _unavailable(expected_count, problem):
    return {'schema': SCHEMA, 'available': False,
        'expected_count': expected_count if _expected(expected_count) else None,
        **{key: None for key in _COUNTS}, 'problem': problem,
        'semantic_quality_verified': False, 'can_apply': False}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _non_integer(_):
    # All numeric fields in this complete argument shape are strict indices or
    # counts. Keep boolean required flags intact; never coerce float positions.
    raise ValueError('non_integer_json_token')


def _checked_counts(value, expected_count):
    # Reuse the complete ACTUAL indexed argument model, not just a hand-picked
    # reconciliation array. Invalid object fields cannot become zero objects
    # or a misleading apparently successful count of the surviving fields.
    checked = DesignGroupedWireV178.model_validate(value).model_dump()
    if _bytes(checked) != _bytes(value):
        raise ValueError('coerced_wire_shape')
    if checked['input_count'] != expected_count:
        return _unavailable(expected_count, 'declared_count_mismatch')
    groups = checked['reconciliation_groups']
    if len(groups) > expected_count or any(len(group['input_positions']) > expected_count for group in groups):
        raise ValueError('dynamic_array_bounds')
    positions = [position for group in groups for position in group['input_positions']]
    if any(type(position) is not int or not 0 <= position < expected_count for position in positions):
        return _unavailable(expected_count, 'position_out_of_range')
    covered = len(set(positions))
    return {'schema': SCHEMA, 'available': True, 'expected_count': expected_count,
        'declared_count': checked['input_count'], 'covered_count': covered,
        'missing_count': expected_count - covered, 'duplicate_count': len(positions) - covered,
        'out_of_range_count': 0, 'business_count': len(checked['result']['businesses']),
        'step_count': sum(len(business['steps']) for business in checked['result']['businesses']),
        'problem': None, 'semantic_quality_verified': False, 'can_apply': False}


def diagnose_response(original_arguments_bytes, *, expected_count):
    """Return closed safe counts, or unavailable with NO partial answer counts.

    Coverage is the number of distinct in-range positions. Duplicate count is
    total occurrences minus that distinct count. Missing coverage/duplicates
    are diagnostic clues, not a weakened production validator. Production must
    retain its original full source/schema/coverage/semantic and approval gates.
    """
    if not _expected(expected_count):
        return _unavailable(None, 'expected_count_invalid')
    if type(original_arguments_bytes) is not bytes or not original_arguments_bytes:
        return _unavailable(expected_count, 'arguments_unavailable')
    if len(original_arguments_bytes) > MAX_ARGUMENT_BYTES:
        return _unavailable(expected_count, 'arguments_too_large')
    try:
        value = json.loads(original_arguments_bytes.decode('utf-8', errors='strict'),
            object_pairs_hook=_pairs, parse_constant=_non_integer, parse_float=_non_integer)
        _bytes(value)  # Complete Unicode, finite JSON and depth checks.
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return _unavailable(expected_count, 'invalid_json')
    try:
        return _checked_counts(value, expected_count)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        return _unavailable(expected_count, 'invalid_wire_shape')
    except Exception:
        # A presentation diagnostic must not leak provider/material text from
        # unexpected validation errors. Fatal BaseExceptions still propagate.
        return _unavailable(expected_count, 'diagnostic_unavailable')
