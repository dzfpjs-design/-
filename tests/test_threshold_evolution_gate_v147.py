"""Frozen threshold comparison oracles; no DB, gateway calls or Invocation proof.

The manifests use the real managed-rule assembler and the match oracle calls
the existing saved calculator. Provenance, independent sample lineage and
actual gateway parity belong to the outer freeze/integration checks.
"""
import copy
import json
import math
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.local_rules_api import CreateRule, _assemble_manifest, _calculate_saved
from generic_agents.threshold_evolution_gate_v147 import compare_frozen_thresholds


FIELD = 'temperature'
ENTITY_ID = '00000000-0000-4000-8000-000000000001'
PACKAGE_ID = '00000000-0000-4000-8000-000000000002'
OWNER_ID = '00000000-0000-4000-8000-000000000003'
BASE_REQUEST = UUID('00000000-0000-4000-8000-000000000004')
CANDIDATE_REQUEST = UUID('00000000-0000-4000-8000-000000000005')
SUMMARY_KEYS = {'tp', 'tn', 'fp', 'fn', 'unknown', 'invalid', 'alerts'}
ROW_KEYS = {'id', 'expected', 'value', 'baseline_matched', 'candidate_matched', 'issue'}


def manifest(threshold, *, operator='gt', request_id=BASE_REQUEST):
    body = CreateRule.model_validate({
        'request_id': request_id, 'title': '合成温度阈值比较', 'entity_id': ENTITY_ID,
        'rationale': '仅验证冻结比较机制，不代表现场阈值或领域收益',
        'rules': [{'id': 'warm', 'field': FIELD, 'unit': 'C',
                   'operator': operator, 'threshold': threshold}],
    })
    fields = {FIELD: {'type': 'number', 'unit': 'C', 'minimum': -100, 'maximum': 100}}
    return _assemble_manifest(body, request_id, digest(body.model_dump(mode='json')),
        OWNER_ID, PACKAGE_ID, 'site', ENTITY_ID, fields, {FIELD: 'C'}).model_dump(mode='json')


def pair(baseline=30, candidate=40, *, operator='gt'):
    return (manifest(baseline, operator=operator),
            manifest(candidate, operator=operator, request_id=CANDIDATE_REQUEST))


def sample(index, value, expected):
    return {'id': f'00000000-0000-4000-9000-{index:012x}',
            'values': {FIELD: value}, 'expected': expected}


def reducing_false_positives():
    return [sample(1, 50, 'should_alert'), sample(2, 60, 'should_alert'),
            sample(3, 35, 'should_not_alert'), sample(4, 20, 'should_not_alert')]


def reducing_false_negatives():
    return [sample(1, 35, 'should_alert'), sample(2, 50, 'should_alert'),
            sample(3, 10, 'should_not_alert'), sample(4, 20, 'should_not_alert')]


def summary(*, tp=0, tn=0, fp=0, fn=0, unknown=0, invalid=0, alerts=0):
    return dict(tp=tp, tn=tn, fp=fp, fn=fn, unknown=unknown, invalid=invalid, alerts=alerts)


def wire(value):
    # Also usable to detect mutation of deliberately invalid pure-call inputs.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def compare(baseline, candidate, samples, *, additional=0):
    before = wire([baseline, candidate, samples])
    result = compare_frozen_thresholds(baseline, candidate, samples,
        {'max_additional_reviews': additional})
    assert wire([baseline, candidate, samples]) == before, 'comparison must not edit its frozen inputs'
    assert set(result) == {'per_sample', 'baseline', 'candidate', 'eligible_for_trial', 'issues'}
    assert type(result['eligible_for_trial']) is bool
    assert isinstance(result['issues'], list)
    assert all(isinstance(issue, str) and issue for issue in result['issues'])
    assert [row['id'] for row in result['per_sample']] == [row['id'] for row in samples]
    assert len(result['per_sample']) == len(samples), 'unknown or invalid rows must not be dropped'
    for side in ('baseline', 'candidate'):
        counts = result[side]
        assert set(counts) == SUMMARY_KEYS
        assert all(type(value) is int and value >= 0 for value in counts.values())
        assert sum(counts[key] for key in SUMMARY_KEYS - {'alerts'}) == len(samples)
    for original, row in zip(samples, result['per_sample']):
        assert set(row) == ROW_KEYS
        assert row['expected'] == original['expected']
        assert row['issue'] is None or isinstance(row['issue'], str) and bool(row['issue'])
        for key in ('baseline_matched', 'candidate_matched'):
            assert row[key] is None or type(row[key]) is bool
    return result


def assert_saved_calculator_matches(result, baseline, candidate, samples):
    for saved, side in ((baseline, 'baseline'), (candidate, 'candidate')):
        cap = SimpleNamespace(data=saved)
        actual = [_calculate_saved(cap, item['values'])[0] for item in samples]
        assert [row[side + '_matched'] for row in result['per_sample']] == [
            row['matched'] for row in actual]
        assert result[side]['alerts'] == sum(row['matched'] for row in actual)
        for item, row, calculated in zip(samples, result['per_sample'], actual):
            assert type(row['value']) is type(item['values'][FIELD])
            assert wire(row['value']) == wire(calculated['value'])


def test_reducing_false_positives_is_eligible_and_retains_every_actual_saved_match():
    baseline, candidate = pair()
    samples = reducing_false_positives()
    result = compare(baseline, candidate, samples)

    assert result['baseline'] == summary(tp=2, tn=1, fp=1, alerts=3)
    assert result['candidate'] == summary(tp=2, tn=2, alerts=2)
    assert result['eligible_for_trial'] is True
    assert result['issues'] == []
    assert all(row['issue'] is None for row in result['per_sample'])
    assert_saved_calculator_matches(result, baseline, candidate, samples)


@pytest.mark.parametrize(('additional', 'eligible'), [(0, False), (1, True)])
def test_reducing_false_negatives_uses_prefrozen_additional_reviews_not_a_tp_regression(additional, eligible):
    baseline, candidate = pair(40, 30)
    samples = reducing_false_negatives()
    result = compare(baseline, candidate, samples, additional=additional)

    assert result['baseline'] == summary(tp=1, tn=2, fn=1, alerts=1)
    assert result['candidate'] == summary(tp=2, tn=2, alerts=2)
    assert result['eligible_for_trial'] is eligible
    assert bool(result['issues']) is not eligible
    assert_saved_calculator_matches(result, baseline, candidate, samples)


def test_changing_criteria_requires_a_separate_comparison_and_does_not_rewrite_old_result():
    baseline, candidate = pair(40, 30)
    samples = reducing_false_negatives()
    blocked = compare(baseline, candidate, samples, additional=0)
    original = wire(blocked)
    allowed = compare(baseline, candidate, samples, additional=1)

    assert blocked['eligible_for_trial'] is False
    assert allowed['eligible_for_trial'] is True
    assert wire(blocked) == original
    assert blocked['per_sample'] == allowed['per_sample']
    assert blocked['baseline'] == allowed['baseline']
    assert blocked['candidate'] == allowed['candidate']


@pytest.mark.parametrize('operator', ['gt', 'lt'])
def test_strict_boundaries_and_integral_float_values_match_the_real_saved_calculator(operator):
    baseline, candidate = pair(0, 1, operator=operator)
    values = [-1, -0.0, 0, 0.0, 0.5, 1, 1.0, 2]
    samples = [sample(index, value, 'should_alert' if index % 2 else 'should_not_alert')
               for index, value in enumerate(values, 1)]
    result = compare(baseline, candidate, samples, additional=len(samples))

    assert_saved_calculator_matches(result, baseline, candidate, samples)
    assert result['per_sample'][2]['baseline_matched'] is False, 'equality must not match gt or lt'
    assert result['per_sample'][5]['candidate_matched'] is False
    assert result['per_sample'][6]['candidate_matched'] is False
    assert type(result['per_sample'][1]['value']) is float
    assert math.copysign(1, result['per_sample'][1]['value']) == -1


@pytest.mark.parametrize(('base', 'candidate_value', 'values', 'expected_base', 'expected_candidate'), [
    (30, 40, [35, 50, 35, 10], summary(tp=2, tn=1, fp=1, alerts=3),
     summary(tp=1, tn=2, fn=1, alerts=1)),
    (40, 30, [35, 50, 35, 10], summary(tp=1, tn=2, fn=1, alerts=1),
     summary(tp=2, tn=1, fp=1, alerts=3)),
])
def test_one_error_rate_cannot_be_traded_for_the_other_or_hidden_by_alert_count(
        base, candidate_value, values, expected_base, expected_candidate):
    baseline, candidate = pair(base, candidate_value)
    samples = [sample(index, value, 'should_alert' if index <= 2 else 'should_not_alert')
               for index, value in enumerate(values, 1)]
    result = compare(baseline, candidate, samples, additional=len(samples))

    assert result['baseline'] == expected_base
    assert result['candidate'] == expected_candidate
    assert result['eligible_for_trial'] is False
    assert result['issues']
    assert_saved_calculator_matches(result, baseline, candidate, samples)


def test_different_threshold_without_any_fp_or_fn_improvement_is_not_eligible():
    baseline, candidate = pair(30, 40)
    samples = [sample(1, 50, 'should_alert'), sample(2, 60, 'should_alert'),
               sample(3, 10, 'should_not_alert'), sample(4, 20, 'should_not_alert')]
    result = compare(baseline, candidate, samples)

    assert result['baseline'] == result['candidate'] == summary(tp=2, tn=2, alerts=2)
    assert result['eligible_for_trial'] is False
    assert result['issues']


@pytest.mark.parametrize(('positives', 'negatives'), [(0, 0), (0, 4), (1, 3), (3, 1), (4, 0)])
def test_both_labels_need_at_least_two_samples_even_with_an_improvement(positives, negatives):
    baseline, candidate = pair()
    samples = [sample(index + 1, 50, 'should_alert') for index in range(positives)]
    samples += [sample(positives + index + 1, 35, 'should_not_alert') for index in range(negatives)]
    result = compare(baseline, candidate, samples)

    assert result['eligible_for_trial'] is False
    assert result['issues']


def test_uncertain_retains_actual_matches_and_alerts_without_becoming_a_negative_label():
    baseline, candidate = pair()
    samples = reducing_false_positives() + [sample(5, 45, 'uncertain')]
    result = compare(baseline, candidate, samples)

    assert result['baseline'] == summary(tp=2, tn=1, fp=1, unknown=1, alerts=4)
    assert result['candidate'] == summary(tp=2, tn=2, unknown=1, alerts=3)
    assert result['per_sample'][-1]['baseline_matched'] is True
    assert result['per_sample'][-1]['candidate_matched'] is True
    assert result['eligible_for_trial'] is False
    assert result['issues']
    assert_saved_calculator_matches(result, baseline, candidate, samples)


@pytest.mark.parametrize('value', [True, False, '35', None, float('nan'), float('inf'), -float('inf'), [], {}],
    ids=['true', 'false', 'numeric-string', 'null', 'nan', 'inf', 'negative-inf', 'array', 'object'])
def test_invalid_source_numeric_values_remain_visible_and_do_not_become_true_negatives(value):
    baseline, candidate = pair()
    samples = reducing_false_positives() + [sample(5, value, 'should_not_alert')]
    result = compare(baseline, candidate, samples)

    assert result['baseline'] == summary(tp=2, tn=1, fp=1, invalid=1, alerts=3)
    assert result['candidate'] == summary(tp=2, tn=2, invalid=1, alerts=2)
    invalid = result['per_sample'][-1]
    assert invalid['baseline_matched'] is None and invalid['candidate_matched'] is None
    assert invalid['issue']
    assert result['eligible_for_trial'] is False
    assert result['issues']


@pytest.mark.parametrize('values', [{}, {FIELD: 35, 'unregistered': 1}, {FIELD: 101}])
def test_missing_extra_or_schema_invalid_values_are_invalid_for_both_comparisons(values):
    baseline, candidate = pair()
    samples = reducing_false_positives() + [sample(5, 35, 'should_alert')]
    samples[-1]['values'] = values
    result = compare(baseline, candidate, samples)

    assert result['baseline'] == summary(tp=2, tn=1, fp=1, invalid=1, alerts=3)
    assert result['candidate'] == summary(tp=2, tn=2, invalid=1, alerts=2)
    assert result['per_sample'][-1]['baseline_matched'] is None
    assert result['per_sample'][-1]['candidate_matched'] is None
    assert result['per_sample'][-1]['issue']
    if not values:
        assert result['per_sample'][-1]['value'] is None
    assert result['eligible_for_trial'] is False


@pytest.mark.parametrize('additional', [True, False, 1.0, '1', None, -1, 5])
def test_additional_review_budget_is_a_strict_integer_within_the_frozen_sample_count(additional):
    baseline, candidate = pair()
    samples = reducing_false_positives()
    before = wire([baseline, candidate, samples])
    with pytest.raises((ValueError, TypeError)):
        compare_frozen_thresholds(baseline, candidate, samples,
            {'max_additional_reviews': additional})
    assert wire([baseline, candidate, samples]) == before


@pytest.mark.parametrize('additional', [0, 4])
def test_additional_review_budget_accepts_both_closed_interval_endpoints(additional):
    baseline, candidate = pair()
    result = compare(baseline, candidate, reducing_false_positives(), additional=additional)
    assert result['eligible_for_trial'] is True


@pytest.mark.parametrize('threshold', [True, '40', None, float('nan'), float('inf'), -float('inf'), 10**15 + 1])
def test_invalid_frozen_thresholds_are_rejected_without_coercion(threshold):
    baseline, candidate = pair()
    candidate['specification']['rules'][0]['threshold'] = threshold
    before = wire([baseline, candidate])

    with pytest.raises((ValueError, TypeError)):
        compare_frozen_thresholds(baseline, candidate, reducing_false_positives(),
            {'max_additional_reviews': 0})

    assert wire([baseline, candidate]) == before


@pytest.mark.parametrize('changed', ['rule_id', 'field', 'unit', 'operator', 'entity', 'schema', 'policy', 'second_rule'])
def test_a_frozen_pair_cannot_change_the_rule_scope_or_other_manifest_semantics(changed):
    baseline, candidate = pair()
    rule = candidate['specification']['rules'][0]
    if changed == 'rule_id':
        rule['id'] = 'different_rule'
    elif changed == 'field':
        rule['field'] = 'other_temperature'
    elif changed == 'unit':
        rule['unit'] = 'F'
    elif changed == 'operator':
        rule['operator'] = 'lt'
    elif changed == 'entity':
        candidate['specification']['managed_numeric_rule']['entity_id'] = '00000000-0000-4000-8000-000000000099'
    elif changed == 'schema':
        candidate['input_schema']['properties']['attributes']['properties'][FIELD]['maximum'] = 101
    elif changed == 'policy':
        candidate['data_policy']['cloud_allowed'] = True
    else:
        candidate['specification']['rules'].append({**copy.deepcopy(rule), 'id': 'second'})
    before = wire([baseline, candidate])

    with pytest.raises((ValueError, TypeError)):
        compare_frozen_thresholds(baseline, candidate, reducing_false_positives(),
            {'max_additional_reviews': 0})

    assert wire([baseline, candidate]) == before


def test_negative_zero_and_integral_float_thresholds_and_samples_keep_their_frozen_types():
    baseline, candidate = pair(1.0, -0.0)
    samples = [sample(1, 0.5, 'should_alert'), sample(2, 2.0, 'should_alert'),
               sample(3, -0.0, 'should_not_alert'), sample(4, -1, 'should_not_alert')]
    result = compare(baseline, candidate, samples, additional=1)

    assert result['eligible_for_trial'] is True
    assert result['baseline'] == summary(tp=1, tn=2, fn=1, alerts=1)
    assert result['candidate'] == summary(tp=2, tn=2, alerts=2)
    assert wire(baseline['specification']['rules'][0]['threshold']) == '1.0'
    assert wire(candidate['specification']['rules'][0]['threshold']) == '-0.0'
    assert type(result['per_sample'][2]['value']) is float
    assert math.copysign(1, result['per_sample'][2]['value']) == -1
    assert type(result['per_sample'][3]['value']) is int
    assert_saved_calculator_matches(result, baseline, candidate, samples)


@pytest.mark.parametrize('threshold', [-10**15, 10**15])
def test_finite_frozen_thresholds_accept_the_original_absolute_bound_without_retyping(threshold):
    baseline, candidate = pair(30, threshold)
    result = compare(baseline, candidate, reducing_false_positives(), additional=4)
    assert_saved_calculator_matches(result, baseline, candidate, reducing_false_positives())
    assert type(candidate['specification']['rules'][0]['threshold']) is int


def test_repeated_comparison_is_deterministic_and_results_do_not_alias_frozen_inputs():
    baseline, candidate = pair()
    samples = reducing_false_positives()
    result = compare(baseline, candidate, samples)
    original = wire([baseline, candidate, samples])
    again = compare(baseline, candidate, samples)
    assert result == again

    result['per_sample'][0]['value'] = -99
    result['per_sample'][0]['id'] = 'changed-result-only'
    result['baseline']['tp'] = 0
    result['issues'].append('result-only note')

    assert wire([baseline, candidate, samples]) == original
    assert again['per_sample'][0]['value'] == 50
    assert again['baseline']['tp'] == 2
    assert again['issues'] == []
