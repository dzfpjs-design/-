"""Only synthetic evidence; candidate construction performs no I/O."""
from copy import deepcopy

import pytest

from generic_agents.report_full_reading_review_candidate import build_candidate
from generic_agents import report_full_reading_review_candidate as review_candidate
from generic_agents.report_full_reading_semantics import validate_full_result
from test_report_full_reading_semantics import example_result, example_source_index


CONTEXT = {'object_names': [], 'businesses': []}


def unsupported_result():
    value = example_result()
    value['objects'][1]['fields'][1]['unit'] = '米'
    relation = deepcopy(value['relationships'][0])
    relation['name'] = '核对单关联记录单'
    relation['source_type'], relation['target_type'] = relation['target_type'], relation['source_type']
    value['relationships'].insert(0, relation)
    return value


def test_only_unsupported_items_are_quarantined_with_original_paths_and_evidence():
    value = unsupported_result(); before = deepcopy(value)
    result = build_candidate(value, example_source_index(), CONTEXT)
    assert value == before == result['original']
    assert result['status'] == 'needs_review' and result['confirmed'] is False
    unit, relation = result['quarantine']
    assert unit == {'kind': 'unit', 'path': '/objects/1/fields/1/unit',
                    'reason': 'unit_evidence_not_established', 'original_value': '米',
                    'citations': before['objects'][1]['fields'][1]['citations']}
    assert relation == {'kind': 'relationship', 'path': '/relationships/0',
                        'reason': 'relationship_evidence_not_established',
                        'original_value': before['relationships'][0]}
    assert result['candidate']['objects'][1]['fields'][1]['unit'] is None
    assert result['candidate']['relationships'] == [before['relationships'][1]]
    assert validate_full_result(result['candidate'], example_source_index(), CONTEXT) == result['candidate']
    result['original']['objects'].clear()
    result['quarantine'][1]['original_value']['citations'].clear()
    assert value == before


def test_valid_answer_is_unchanged_and_not_automatically_confirmed():
    value = example_result()
    result = build_candidate(value, example_source_index(), CONTEXT)
    assert result['candidate'] == value == result['original']
    assert result['quarantine'] == [] and result['confirmed'] is False


def test_multiple_removed_relations_retain_their_original_indices():
    value = unsupported_result()
    value['relationships'][1]['citations'][0]['quote'] = '记录单包含名称字段。'
    result = build_candidate(value, example_source_index(), CONTEXT)
    assert result['candidate']['relationships'] == []
    assert [item['path'] for item in result['quarantine'] if item['kind'] == 'relationship'] == [
        '/relationships/0', '/relationships/1']
    assert [item['original_value'] for item in result['quarantine'] if item['kind'] == 'relationship'] == value['relationships']


@pytest.mark.parametrize('mutate', [
    lambda v: v['businesses'][0]['steps'][0].update(next_key='missing'),
    lambda v: v['relationships'][0].update(cardinality='one_per_source'),
    lambda v: v['relationships'][0]['citations'][0].update(quote='伪造的引用'),
    lambda v: v['relationships'][0].update(target_type='未知对象'),
    lambda v: v['relationships'][0].update(name=v['relationships'][1]['name']),
    lambda v: v['objects'][1]['fields'][1].update(unit=42),
    lambda v: v['objects'][1]['fields'][1]['citations'][0].update(source_ref='w:p99'),
])
def test_other_invalidities_are_never_hidden_by_quarantine(mutate):
    value = unsupported_result(); mutate(value); before = deepcopy(value)
    with pytest.raises(ValueError):
        build_candidate(value, example_source_index(), CONTEXT)
    assert value == before


@pytest.mark.parametrize('value', [None, [], 'text', {'schema': 'wrong'}])
def test_invalid_json_shapes_fail_closed(value):
    with pytest.raises(ValueError):
        build_candidate(value, example_source_index(), CONTEXT)


def test_missing_source_index_or_invalid_context_fails_closed():
    with pytest.raises(ValueError):
        build_candidate(unsupported_result(), {}, CONTEXT)
    with pytest.raises(ValueError):
        build_candidate(unsupported_result(), example_source_index(), {'object_names': [None]})


def test_repeated_labels_exceed_budget_before_expensive_guards(monkeypatch):
    value = example_result(); before = deepcopy(value); sources = example_source_index()
    sources['w:p2']['text'] += '数量（株） ' * 20_000
    def forbidden(*args, **kwargs):
        pytest.fail('expensive validator ran before the budget check')
    monkeypatch.setattr(review_candidate, 'unit_declared', forbidden)
    monkeypatch.setattr(review_candidate, 'validate_full_result', forbidden)
    with pytest.raises(ValueError, match='单位证据计算量超过安全上限'):
        build_candidate(value, sources, CONTEXT)
    assert value == before


def test_many_field_citations_to_one_large_source_are_charged_separately(monkeypatch):
    value = example_result(); sources = example_source_index()
    base = value['objects'][1]['fields'][1]
    for n in range(5):
        field = deepcopy(base); field['label'] += str(n)
        value['objects'][1]['fields'].append(field)
    sources['w:p2']['text'] += 'x' * 180_000
    def forbidden(*args, **kwargs):
        pytest.fail('expensive validator ran before the budget check')
    monkeypatch.setattr(review_candidate, 'unit_declared', forbidden)
    monkeypatch.setattr(review_candidate, 'validate_full_result', forbidden)
    with pytest.raises(ValueError, match='来源扫描量超过安全上限'):
        build_candidate(value, sources, CONTEXT)


def test_long_whitespace_regex_backtracking_exceeds_budget_before_guard(monkeypatch):
    value = example_result(); before = deepcopy(value); sources = example_source_index()
    sources['w:p2']['text'] += '数量单位' + ' ' * 20_000 + 'X'
    def forbidden(*args, **kwargs):
        pytest.fail('unit guard ran before the whitespace work budget')
    monkeypatch.setattr(review_candidate, 'unit_declared', forbidden)
    monkeypatch.setattr(review_candidate, 'validate_full_result', forbidden)
    with pytest.raises(ValueError, match='单位证据计算量超过安全上限'):
        build_candidate(value, sources, CONTEXT)
    assert value == before


def test_many_unit_declarations_with_long_whitespace_exceed_budget(monkeypatch):
    value = example_result(); sources = example_source_index()
    sources['w:p2']['text'] += '数量' + ('单位' + ' ' * 300 + 'X') * 20
    monkeypatch.setattr(review_candidate, 'unit_declared', lambda *a: pytest.fail('guard ran'))
    with pytest.raises(ValueError, match='单位证据计算量超过安全上限'):
        build_candidate(value, sources, CONTEXT)


def test_multiple_normal_length_sources_still_pass_without_quarantine():
    value = example_result(); sources = example_source_index()
    sources['w:p2']['text'] += '字' * (270 - len(sources['w:p2']['text']))
    for n in range(7):
        obj = deepcopy(value['objects'][1]); obj['name'] += str(n)
        value['objects'].append(obj)
    result = build_candidate(value, sources, CONTEXT)
    assert result['candidate'] == value
    assert result['quarantine'] == []
