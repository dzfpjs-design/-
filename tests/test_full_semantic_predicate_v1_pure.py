"""Synthetic scalar predicates only: no source, authorization or runtime proof."""
import copy
import json

import pytest

from generic_agents.contracts import digest
from generic_agents import full_semantic_predicate_v1 as predicates


def ref(key):
    return {'op': 'ref', 'key': key}


def value(kind, scalar):
    return {'op': 'value', 'kind': kind, 'value': scalar}


def compare(op, left, right):
    return {'op': op, 'left': left, 'right': right}


def schema(**kinds):
    return {key: {'kind': kind, 'nullable': False} for key, kind in kinds.items()}


def evaluate(ast, references=None, values=None):
    return predicates.evaluate_predicate(ast, {} if references is None else references,
        {} if values is None else values)


@pytest.mark.parametrize('scalar,truth', [(True, 'true'), (False, 'false')])
def test_boolean_constants_and_references_are_typed_roots(scalar, truth):
    assert evaluate(value('boolean', scalar))['truth'] == truth
    assert evaluate(ref('ready'), schema(ready='boolean'), {'ready': scalar})['truth'] == truth


def test_missing_and_nullable_null_are_unknown_but_explicit_false_is_false():
    references = {'ready': {'kind': 'boolean', 'nullable': True}}
    missing = evaluate(ref('ready'), references, {})
    null = evaluate(ref('ready'), references, {'ready': None})
    false = evaluate(ref('ready'), references, {'ready': False})
    assert missing['truth'] == null['truth'] == 'unknown'
    assert false['truth'] == 'false'
    assert missing['missing_keys'] == null['missing_keys'] == ['ready']
    assert false['missing_keys'] == []
    assert len({item['context_hash'] for item in [missing, null, false]}) == 3


@pytest.mark.parametrize('op,left,right,truth', [
    ('all', False, None, 'false'), ('all', True, None, 'unknown'),
    ('all', None, None, 'unknown'), ('all', True, True, 'true'),
    ('any', True, None, 'true'), ('any', False, None, 'unknown'),
    ('any', None, None, 'unknown'), ('any', False, False, 'false'),
])
def test_kleene_logic_and_full_reference_observability(op, left, right, truth):
    references = {key: {'kind': 'boolean', 'nullable': True} for key in ['left', 'right']}
    ast = {'op': op, 'args': [ref('left'), ref('right'), ref('left')]}
    receipt = evaluate(ast, references, {'left': left, 'right': right})
    assert receipt['truth'] == truth
    assert receipt['referenced_keys'] == ['left', 'right']
    assert receipt['missing_keys'] == [key for key, item in [('left', left), ('right', right)] if item is None]


@pytest.mark.parametrize('scalar,truth', [(True, 'false'), (False, 'true'), (None, 'unknown')])
def test_not_preserves_unknown(scalar, truth):
    assert evaluate({'op': 'not', 'arg': ref('ready')},
        {'ready': {'kind': 'boolean', 'nullable': True}}, {'ready': scalar})['truth'] == truth


def test_reference_order_is_first_depth_first_occurrence_even_after_decisive_value():
    ast = {'op': 'any', 'args': [value('boolean', True), {'op': 'all', 'args': [ref('b'),
        {'op': 'not', 'arg': ref('a')}, ref('b')]}, compare('eq', ref('c'), value('integer', 1))]}
    receipt = evaluate(ast, schema(a='boolean', b='boolean', c='integer'))
    assert receipt['truth'] == 'true'
    assert receipt['referenced_keys'] == receipt['missing_keys'] == ['b', 'a', 'c']


@pytest.mark.parametrize('op,left,right,truth', [
    ('eq', 3, 3.0, 'true'), ('ne', 3, 3.1, 'true'), ('gt', 4, 3.5, 'true'),
    ('gte', 3, 3.0, 'true'), ('lt', 3, 3.5, 'true'), ('lte', 3, 3.0, 'true'),
    ('gt', 3, 3.5, 'false'), ('eq', 2 ** 70, 2 ** 70 + 1, 'false'),
])
def test_integer_and_number_comparisons_do_not_coerce_types_or_lose_integer_precision(op, left, right, truth):
    ast = compare(op, ref('count'), value('number', right))
    assert evaluate(ast, schema(count='integer'), {'count': left})['truth'] == truth


@pytest.mark.parametrize('op,truth', [('eq', 'false'), ('ne', 'true'), ('gt', 'false'),
    ('gte', 'false'), ('lt', 'true'), ('lte', 'true')])
def test_strict_dates_support_ordering(op, truth):
    ast = compare(op, ref('day'), value('date', '2024-03-01'))
    assert evaluate(ast, schema(day='date'), {'day': '2024-02-29'})['truth'] == truth


@pytest.mark.parametrize('kind,scalar', [('boolean', False), ('string', '合成值🙂')])
def test_boolean_and_string_allow_only_equality(kind, scalar):
    for op in ['eq', 'ne']:
        assert evaluate(compare(op, value(kind, scalar), value(kind, scalar)))['truth'] == ('true' if op == 'eq' else 'false')
    for op in ['gt', 'gte', 'lt', 'lte']:
        with pytest.raises(ValueError):
            predicates.validate_predicate(compare(op, value(kind, scalar), value(kind, scalar)), {})


def test_comparison_of_unknown_is_unknown_even_for_ne():
    for op in ['eq', 'ne', 'gt', 'gte', 'lt', 'lte']:
        receipt = evaluate(compare(op, ref('count'), value('integer', 1)), schema(count='integer'))
        assert receipt['truth'] == 'unknown' and receipt['missing_keys'] == ['count']


def test_result_is_closed_cold_rebuildable_and_never_returns_raw_context():
    ast = compare('eq', ref('material'), value('string', 'synthetic private marker'))
    references = schema(material='string', unused='integer')
    values = {'material': 'synthetic private marker', 'unused': 9}
    before = copy.deepcopy((ast, references, values))
    result = evaluate(ast, references, values)
    assert result == evaluate(copy.deepcopy(ast), copy.deepcopy(references), copy.deepcopy(values))
    assert set(result) == {'schema', 'ast_hash', 'reference_schema_hash', 'context_hash',
        'truth', 'referenced_keys', 'missing_keys', 'result_hash'}
    assert result['schema'] == 'full-semantic-predicate-result-v1'
    assert result['ast_hash'] == digest(ast)
    assert result['reference_schema_hash'] == digest(references)
    assert result['context_hash'] == digest(values)
    assert result['result_hash'] == digest({key: item for key, item in result.items() if key != 'result_hash'})
    assert 'synthetic private marker' not in json.dumps(result)
    assert (ast, references, values) == before
    assert evaluate(ast, references, {**values, 'unused': 10})['context_hash'] != result['context_hash']
    assert evaluate(ast, dict(reversed(list(references.items()))), dict(reversed(list(values.items())))) == result


def test_validated_ast_is_independent_and_keys_are_literal_not_paths():
    references = {'node.output': {'kind': 'boolean', 'nullable': False}}
    ast = {'op': 'not', 'arg': ref('node.output')}
    checked = predicates.validate_predicate(ast, references)
    assert checked == ast and checked is not ast and checked['arg'] is not ast['arg']
    checked['arg']['key'] = 'changed'
    assert ast['arg']['key'] == 'node.output'
    assert evaluate(ast, references, {'node.output': True})['truth'] == 'false'
    with pytest.raises(ValueError):
        evaluate(ast, references, {'node': {'output': True}})


@pytest.mark.parametrize('ast', [None, [], True, {}, {'op': []}, {'op': 'python', 'code': 'True'},
    {'op': 'value', 'kind': 'boolean', 'value': True, 'extra': 1},
    {'op': 'value', 'kind': 'integer', 'value': 1}, {'op': 'ref', 'key': 'unknown'},
    {'op': 'not', 'arg': value('integer', 1)}, {'op': 'not', 'arg': value('boolean', True), 'extra': 1},
    {'op': 'all', 'args': []}, {'op': 'any', 'args': ()},
    {'op': 'all', 'args': [value('string', 'true')]},
    compare('eq', value('integer', 1), value('boolean', True)),
    compare('eq', value('string', '2024-01-01'), value('date', '2024-01-01')),
    compare('eq', {'op': 'not', 'arg': value('boolean', True)}, value('boolean', False)),
    {'op': 'eq', 'left': value('integer', 1)},
])
def test_malformed_unknown_syntax_and_incompatible_types_are_rejected_without_mutation(ast):
    before = copy.deepcopy(ast)
    with pytest.raises(ValueError):
        predicates.validate_predicate(ast, {})
    assert ast == before


@pytest.mark.parametrize('kind,bad', [
    ('integer', True), ('integer', 1.0), ('number', True), ('number', float('nan')),
    ('number', float('inf')), ('number', -float('inf')), ('boolean', 1),
    ('boolean', 'true'), ('string', None), ('string', []), ('string', {}),
    ('string', 'NUL\x00'), ('string', '\ud800'), ('string', '\udfff'),
    ('date', '2024-2-29'), ('date', '2023-02-29'), ('date', '20240229'),
    ('date', '2024-W01-1'), ('date', '2024-01-01T00:00:00'), ('date', '0000-01-01'),
])
def test_invalid_constant_and_context_scalars_are_rejected(kind, bad):
    with pytest.raises(ValueError):
        predicates.validate_predicate(compare('eq', value(kind, bad), value(kind, bad)), {})
    with pytest.raises(ValueError):
        evaluate(value('boolean', True), schema(unused=kind), {'unused': bad})


@pytest.mark.parametrize('references', [None, [], {'a': {}}, {'a': {'kind': 'boolean'}},
    {'a': {'kind': 'boolean', 'nullable': 1}}, {'a': {'kind': 'array', 'nullable': False}},
    {'a': {'kind': [], 'nullable': False}}, {'a': {'kind': 'boolean', 'nullable': False, 'extra': 1}},
    {'': {'kind': 'boolean', 'nullable': False}}, {'\x00': {'kind': 'boolean', 'nullable': False}},
    {'\ud800': {'kind': 'boolean', 'nullable': False}}, {1: {'kind': 'boolean', 'nullable': False}},
])
def test_reference_schema_is_closed_and_strict(references):
    with pytest.raises(ValueError):
        predicates.validate_predicate(value('boolean', True), references)


@pytest.mark.parametrize('values', [[], None, {'extra': True}, {'ready': None},
    {'ready': 0}, {1: True}, {'\x00': True}])
def test_context_is_closed_and_every_provided_scalar_is_checked(values):
    with pytest.raises(ValueError):
        predicates.evaluate_predicate(value('boolean', True), schema(ready='boolean'), values)


def test_caps_allow_exact_boundaries_and_reject_excess_without_truncation():
    references = {str(index): {'kind': 'boolean', 'nullable': False} for index in range(256)}
    assert predicates.validate_predicate(ref('0'), references) == ref('0')
    with pytest.raises(ValueError):
        predicates.validate_predicate(ref('0'), {**references, 'extra': references['0']})
    long_key = 'k' * 200
    assert predicates.validate_predicate(ref(long_key), {long_key: references['0']}) == ref(long_key)
    with pytest.raises(ValueError):
        predicates.validate_predicate(ref(long_key + 'k'), {long_key + 'k': references['0']})
    ast = value('boolean', True)
    for _ in range(11):
        ast = {'op': 'not', 'arg': ast}
    predicates.validate_predicate(ast, {})
    with pytest.raises(ValueError):
        predicates.validate_predicate({'op': 'not', 'arg': ast}, {})
    group = {'op': 'all', 'args': [value('boolean', True) for _ in range(64)]}
    predicates.validate_predicate(group, {})
    with pytest.raises(ValueError):
        predicates.validate_predicate({'op': 'all', 'args': group['args'] + [value('boolean', True)]}, {})
    # 1 root + (1 group + 63 leaves) + 2 * (1 group + 64 leaves)
    # + (1 group + 60 leaves) = 256 nodes, with every args list within its cap.
    ast = {'op': 'all', 'args': [{'op': 'all', 'args': [value('boolean', True) for _ in range(count)]}
        for count in [63, 64, 64, 60]]}
    predicates.validate_predicate(ast, {})
    ast['args'][-1]['args'].append(value('boolean', True))
    with pytest.raises(ValueError):
        predicates.validate_predicate(ast, {})


def test_cyclic_ast_fails_with_value_error_at_depth_boundary():
    ast = {'op': 'not'}
    ast['arg'] = ast
    with pytest.raises(ValueError):
        predicates.validate_predicate(ast, {})
