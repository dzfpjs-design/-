"""Closed, typed predicates over caller-authorized, frozen scalar references.

This pure evaluator grants no authority and never resolves a key as a path. The
caller must establish reference authorization, source validity and execution
consent before calling it. Missing and permitted null values remain unknown.
"""
import hashlib
import json
import math
import re
from datetime import date


SCHEMA = 'full-semantic-predicate-result-v1'
MAX_REFERENCES = 256
MAX_NODES = 256
MAX_DEPTH = 12
MAX_ARGS = 64
MAX_KEY_LENGTH = 200
KINDS = frozenset({'boolean', 'string', 'integer', 'number', 'date'})
COMPARISONS = frozenset({'eq', 'ne', 'gt', 'gte', 'lt', 'lte'})
_NUMERIC = frozenset({'integer', 'number'})
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_UNKNOWN = object()


def _need(condition, code):
    if not condition:
        raise ValueError(code)


def _text(value):
    _need(type(value) is str and '\x00' not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value), 'predicate_text')
    return value


def _key(value):
    _text(value)
    _need(1 <= len(value) <= MAX_KEY_LENGTH, 'predicate_reference_key')
    return value


def _scalar(kind, value):
    _need(type(kind) is str and kind in KINDS, 'predicate_scalar_kind')
    if kind == 'boolean':
        _need(type(value) is bool, 'predicate_boolean_value')
    elif kind == 'integer':
        _need(type(value) is int, 'predicate_integer_value')
    elif kind == 'number':
        _need(type(value) is int or type(value) is float and math.isfinite(value),
            'predicate_number_value')
    elif kind == 'string':
        _text(value)
    else:
        _text(value)
        _need(_DATE.fullmatch(value) is not None, 'predicate_date_value')
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError('predicate_date_value') from None
        _need(parsed.isoformat() == value, 'predicate_date_value')
    return value


def _schema(reference_schema):
    _need(type(reference_schema) is dict and len(reference_schema) <= MAX_REFERENCES,
        'predicate_reference_schema')
    checked = {}
    for key, spec in reference_schema.items():
        _key(key)
        _need(type(spec) is dict and set(spec) == {'kind', 'nullable'},
            'predicate_reference_spec')
        _need(type(spec['kind']) is str and spec['kind'] in KINDS
            and type(spec['nullable']) is bool, 'predicate_reference_spec')
        checked[key] = {'kind': spec['kind'], 'nullable': spec['nullable']}
    return checked


def _node(value, reference_schema, budget, depth=1, *, operand=False):
    _need(depth <= MAX_DEPTH, 'predicate_depth_limit')
    budget[0] += 1
    _need(budget[0] <= MAX_NODES, 'predicate_node_limit')
    _need(type(value) is dict and type(value.get('op')) is str, 'predicate_node')
    op = value['op']
    if operand:
        _need(op in {'ref', 'value'}, 'predicate_comparison_operand')
    if op == 'ref':
        _need(set(value) == {'op', 'key'}, 'predicate_ref_shape')
        key = _key(value['key'])
        _need(key in reference_schema, 'predicate_reference_not_authorized')
        return {'op': op, 'key': key}, reference_schema[key]['kind']
    if op == 'value':
        _need(set(value) == {'op', 'kind', 'value'}, 'predicate_value_shape')
        scalar = _scalar(value['kind'], value['value'])
        return {'op': op, 'kind': value['kind'], 'value': scalar}, value['kind']
    if op in {'all', 'any'}:
        _need(set(value) == {'op', 'args'} and type(value['args']) is list
            and 1 <= len(value['args']) <= MAX_ARGS, 'predicate_args')
        args = []
        for arg in value['args']:
            checked, kind = _node(arg, reference_schema, budget, depth + 1)
            _need(kind == 'boolean', 'predicate_boolean_operand')
            args.append(checked)
        return {'op': op, 'args': args}, 'boolean'
    if op == 'not':
        _need(set(value) == {'op', 'arg'}, 'predicate_not_shape')
        checked, kind = _node(value['arg'], reference_schema, budget, depth + 1)
        _need(kind == 'boolean', 'predicate_boolean_operand')
        return {'op': op, 'arg': checked}, 'boolean'
    _need(op in COMPARISONS, 'predicate_operator')
    _need(set(value) == {'op', 'left', 'right'}, 'predicate_comparison_shape')
    left, left_kind = _node(value['left'], reference_schema, budget, depth + 1, operand=True)
    right, right_kind = _node(value['right'], reference_schema, budget, depth + 1, operand=True)
    numeric = left_kind in _NUMERIC and right_kind in _NUMERIC
    _need(left_kind == right_kind or numeric, 'predicate_comparison_types')
    _need(op in {'eq', 'ne'} or numeric or left_kind == right_kind == 'date',
        'predicate_ordering_type')
    return {'op': op, 'left': left, 'right': right}, 'boolean'


def _validated(ast, reference_schema):
    checked, kind = _node(ast, reference_schema, [0])
    _need(kind == 'boolean', 'predicate_root_type')
    return checked


def _hash(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('predicate_json') from None
    return hashlib.sha256(encoded).hexdigest()


def validate_predicate(ast, reference_schema):
    """Return an independent canonical AST, rejecting unknown syntax or types.

    Logical collections require at least one argument; empty groups cannot
    accidentally authorize execution through a vacuous all-expression.
    """
    schema = _schema(reference_schema)
    checked = _validated(ast, schema)
    _hash(checked)
    _hash(schema)
    return checked


def _values(values, reference_schema):
    _need(type(values) is dict, 'predicate_values')
    checked = {}
    for key, value in values.items():
        _key(key)
        _need(key in reference_schema, 'predicate_value_not_authorized')
        spec = reference_schema[key]
        if value is None:
            _need(spec['nullable'], 'predicate_null_not_allowed')
            checked[key] = None
        else:
            checked[key] = _scalar(spec['kind'], value)
    return checked


def _evaluate(ast, values, referenced, missing, seen):
    op = ast['op']
    if op == 'ref':
        key = ast['key']
        if key not in seen:
            seen.add(key)
            referenced.append(key)
            if key not in values or values[key] is None:
                missing.append(key)
        return values[key] if key in values and values[key] is not None else _UNKNOWN
    if op == 'value':
        return ast['value']
    if op == 'not':
        result = _evaluate(ast['arg'], values, referenced, missing, seen)
        return _UNKNOWN if result is _UNKNOWN else not result
    if op in {'all', 'any'}:
        # Evaluate every child before combining: decisive truth must not hide
        # missing references from the durable receipt's observability fields.
        results = [_evaluate(arg, values, referenced, missing, seen) for arg in ast['args']]
        if op == 'all':
            if any(result is False for result in results):
                return False
            return _UNKNOWN if any(result is _UNKNOWN for result in results) else True
        if any(result is True for result in results):
            return True
        return _UNKNOWN if any(result is _UNKNOWN for result in results) else False
    left = _evaluate(ast['left'], values, referenced, missing, seen)
    right = _evaluate(ast['right'], values, referenced, missing, seen)
    if left is _UNKNOWN or right is _UNKNOWN:
        return _UNKNOWN
    if op == 'eq':
        return left == right
    if op == 'ne':
        return left != right
    if op == 'gt':
        return left > right
    if op == 'gte':
        return left >= right
    if op == 'lt':
        return left < right
    return left <= right


def evaluate_predicate(ast, reference_schema, values):
    """Return a closed hash receipt, never raw values or executable authority.

    missing_keys includes both absent references and permitted null references.
    Their contexts hash differently. Every supplied value, including an unused
    authorized key, is type checked and included in the context hash.
    """
    schema = _schema(reference_schema)
    checked_ast = _validated(ast, schema)
    checked_values = _values(values, schema)
    referenced, missing = [], []
    truth = _evaluate(checked_ast, checked_values, referenced, missing, set())
    result = {'schema': SCHEMA, 'ast_hash': _hash(checked_ast),
        'reference_schema_hash': _hash(schema), 'context_hash': _hash(checked_values),
        'truth': 'unknown' if truth is _UNKNOWN else 'true' if truth else 'false',
        'referenced_keys': referenced, 'missing_keys': missing}
    return {**result, 'result_hash': _hash(result)}
