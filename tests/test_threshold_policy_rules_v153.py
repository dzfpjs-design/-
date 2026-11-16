"""Pure policy projections and monitoring; no calls, DB or adoption claims."""
from copy import deepcopy
import math

import pytest

from generic_agents.threshold_policy_rules_v153 import input_spec, monitor_statistics, project


def scope():
    return {'field': '温度', 'field_label': '原登记温度', 'unit': '摄氏度',
        'operator': 'gt', 'baseline_threshold': 20, 'candidate_threshold': 18}


def extractor(kind='number'):
    return {'input_schema': {'type': 'object', 'properties': {
        'attributes': {'type': 'object', 'properties': {
            '温度': {'type': kind, 'title': '原字段说明', 'unit': '摄氏度'}}}}}}


def sample(status='completed', label='should_alert', actual=True, baseline=True):
    return {'status': status, 'label': label, 'actual_matched': actual,
        'baseline_projection_matched': baseline}


@pytest.mark.parametrize('operator,value,expected', [
    ('gt', 19, (False, True)), ('gt', 20, (False, True)),
    ('gt', 21, (True, True)), ('gt', 18, (False, False)),
    ('lt', 19, (True, False)), ('lt', 20, (False, False)),
    ('lt', 17, (True, True)), ('lt', 18, (True, False)),
])
def test_projection_uses_strict_gt_or_lt_without_claiming_an_actual_baseline_call(operator, value, expected):
    original = scope(); original['operator'] = operator; attributes = {'温度': value}
    saved_scope, saved_attributes = deepcopy(original), deepcopy(attributes)
    result = project(original, attributes)
    assert result == {'value': value, 'baseline_matched': expected[0], 'candidate_matched': expected[1]}
    assert type(result['baseline_matched']) is bool and type(result['candidate_matched']) is bool
    assert original == saved_scope and attributes == saved_attributes


def test_projection_preserves_float_kind_and_negative_zero_instead_of_coercing_input():
    original = scope(); original.update(baseline_threshold=0.0, candidate_threshold=-1)
    result = project(original, {'温度': -0.0})
    assert type(result['value']) is float and math.copysign(1, result['value']) == -1
    assert result['baseline_matched'] is False and result['candidate_matched'] is True
    assert type(original['baseline_threshold']) is float
    assert project(scope(), {'温度': 21.0})['value'] == 21.0
    assert type(project(scope(), {'温度': 21.0})['value']) is float


def test_finite_integer_projection_does_not_overflow_via_an_unnecessary_float_conversion():
    value = 10 ** 400
    result = project(scope(), {'温度': value})
    assert result == {'value': value, 'baseline_matched': True, 'candidate_matched': True}
    assert type(result['value']) is int


@pytest.mark.parametrize('value', [True, False, '21', None, float('nan'), float('inf'), float('-inf')],
    ids=['true', 'false', 'string', 'null', 'nan', 'positive-infinity', 'negative-infinity'])
def test_projection_rejects_nonfacts_instead_of_coercing_them(value):
    with pytest.raises(ValueError):project(scope(), {'温度': value})


@pytest.mark.parametrize('field', ['baseline_threshold', 'candidate_threshold'])
@pytest.mark.parametrize('value', [True, '20', None, float('nan'), float('inf')],
    ids=['boolean', 'string', 'null', 'nan', 'infinity'])
def test_both_frozen_thresholds_must_be_actual_finite_numbers(field, value):
    original = scope(); original[field] = value
    with pytest.raises(ValueError):project(original, {'温度': 21})


@pytest.mark.parametrize('attributes', [{}, {'其它字段': 21}, {'温度': 21, '其它字段': 22}, [21], None])
def test_projection_requires_exactly_the_original_single_field(attributes):
    with pytest.raises(ValueError):project(scope(), attributes)


@pytest.mark.parametrize('operator', ['ge', 'lte', '', True, None])
def test_projection_does_not_guess_a_rule_direction(operator):
    original = scope(); original['operator'] = operator
    with pytest.raises(ValueError):project(original, {'温度': 21})


@pytest.mark.parametrize('kind', ['integer', 'number'])
@pytest.mark.parametrize('timezone', ['UTC', 'Asia/Shanghai', 'America/Los_Angeles'])
def test_input_spec_reads_real_field_type_and_declared_source_timezone_without_mutation(kind, timezone):
    original, manifest, source = scope(), extractor(kind), {'specification': {'timezone': timezone}}
    before = deepcopy((original, manifest, source))
    assert input_spec(original, manifest, source) == {'field': '温度', 'field_label': '原登记温度',
        'unit': '摄氏度', 'value_kind': kind, 'source_timezone': timezone, 'clock_unknown_allowed': True}
    assert (original, manifest, source) == before


def test_input_spec_uses_only_the_explicit_missing_timezone_default_and_keeps_unknown_clock_allowed():
    assert input_spec(scope(), extractor(), {'specification': {}})['source_timezone'] == 'Asia/Shanghai'
    assert input_spec(scope(), extractor(), {})['clock_unknown_allowed'] is True
    original = scope(); original['unit'] = None
    assert input_spec(original, extractor(), {})['unit'] is None


@pytest.mark.parametrize('timezone', ['', None, True, 'Invalid/Timezone', '../etc/passwd'])
def test_invalid_present_timezone_does_not_silently_fall_back(timezone):
    with pytest.raises(ValueError):input_spec(scope(), extractor(), {'specification': {'timezone': timezone}})


@pytest.mark.parametrize('manifest', [
    {}, {'input_schema': {}}, {'input_schema': {'properties': {}}},
    {'input_schema': {'properties': {'attributes': {}}}},
    {'input_schema': {'properties': {'attributes': {'properties': {}}}}},
    {'input_schema': {'properties': {'attributes': {'properties': {'温度': {'format': 'number'}}}}}},
    extractor('string'), extractor(None), extractor(['number', 'null']), extractor(True),
])
def test_input_spec_never_infers_number_from_threshold_or_an_absent_schema(manifest):
    with pytest.raises(ValueError):input_spec(scope(), manifest, {})


def test_mixed_monitoring_keeps_pending_unknown_failed_and_stopped_in_the_denominator():
    rows = [sample(actual=True, baseline=True), sample(actual=False, baseline=True),
        sample(label='should_not_alert', actual=True, baseline=False),
        sample(label='uncertain', actual=True, baseline=False), sample(label=None, actual=False, baseline=False),
        sample('queued', 'should_alert', None, None), sample('reserved', None, None, None),
        sample('failed', None, None, None), sample('stopped', None, None, None)]
    before = deepcopy(rows); criteria = {'max_additional_reviews': 1}
    assert monitor_statistics(rows, criteria) == {
        'total': 9, 'completed': 5, 'failed': 1, 'stopped': 1, 'pending': 2,
        'unlabelled': 6, 'labelled': 3,
        'baseline_projection': {'tp': 2, 'tn': 1, 'fp': 0, 'fn': 0},
        'candidate': {'tp': 1, 'tn': 0, 'fp': 1, 'fn': 1}, 'regressed': True}
    assert rows == before and criteria == {'max_additional_reviews': 1}


@pytest.mark.parametrize('label', [None, 'uncertain'])
def test_completed_but_unlabelled_results_never_become_tn_improvement_or_automatic_rollback(label):
    result = monitor_statistics([sample(label=label, actual=True, baseline=False)], {'max_additional_reviews': 0})
    assert result['total'] == result['completed'] == result['unlabelled'] == 1
    assert result['labelled'] == 0 and result['regressed'] is False
    assert result['candidate'] == result['baseline_projection'] == {'tp': 0, 'tn': 0, 'fp': 0, 'fn': 0}


@pytest.mark.parametrize('allowance,expected', [(0, True), (1, True), (2, False), (3, False)])
def test_only_excess_positive_additional_false_alerts_cross_the_frozen_review_allowance(allowance, expected):
    rows = [sample(label='should_not_alert', actual=True, baseline=False) for _ in range(2)]
    result = monitor_statistics(rows, {'max_additional_reviews': allowance})
    assert result['regressed'] is expected
    assert result['candidate']['fp'] == 2 and result['baseline_projection']['fp'] == 0


def test_false_negative_regression_cannot_be_bought_with_more_review_allowance():
    result = monitor_statistics([sample(actual=False, baseline=True)], {'max_additional_reviews': 30})
    assert result['candidate']['fn'] == 1 and result['baseline_projection']['fn'] == 0
    assert result['regressed'] is True


def test_tp_increase_and_fewer_false_alerts_are_not_misclassified_as_regression():
    rows = [sample(actual=True, baseline=False),
            sample(label='should_not_alert', actual=False, baseline=True)]
    result = monitor_statistics(rows, {'max_additional_reviews': 0})
    assert result['regressed'] is False
    assert result['candidate'] == {'tp': 1, 'tn': 1, 'fp': 0, 'fn': 0}
    assert result['baseline_projection'] == {'tp': 0, 'tn': 0, 'fp': 1, 'fn': 1}


def test_empty_monitoring_has_no_invented_success_or_rollback():
    result = monitor_statistics([], {'max_additional_reviews': 0})
    assert set(result) == {'total', 'completed', 'failed', 'stopped', 'pending', 'unlabelled', 'labelled',
        'baseline_projection', 'candidate', 'regressed'}
    assert all(result[key] == 0 for key in ('total', 'completed', 'failed', 'stopped', 'pending', 'unlabelled', 'labelled'))
    assert result['regressed'] is False


@pytest.mark.parametrize('field', ['actual_matched', 'baseline_projection_matched'])
@pytest.mark.parametrize('value', [None, 0, 1, 1.0, 'true'])
def test_completed_requires_two_actual_boolean_results(field, value):
    row = sample(); row[field] = value
    with pytest.raises(ValueError):monitor_statistics([row], {'max_additional_reviews': 0})


@pytest.mark.parametrize('status', ['queued', 'reserved', 'failed', 'stopped'])
@pytest.mark.parametrize('field', ['actual_matched', 'baseline_projection_matched'])
@pytest.mark.parametrize('value', [False, True])
def test_noncompleted_records_cannot_smuggle_completed_results(status, field, value):
    row = sample(status, None, None, None); row[field] = value
    with pytest.raises(ValueError):monitor_statistics([row], {'max_additional_reviews': 0})


@pytest.mark.parametrize('change', ['extra', 'missing', 'status', 'label', 'boolean-label', 'not-list'])
def test_monitor_samples_are_closed_and_labels_are_not_coerced(change):
    row = sample(); rows = [row]
    if change == 'extra':row['approved'] = True
    elif change == 'missing':del row['label']
    elif change == 'status':row['status'] = 'succeeded'
    elif change == 'label':row['label'] = 'confirmed'
    elif change == 'boolean-label':row['label'] = True
    else:rows = (row,)
    with pytest.raises(ValueError):monitor_statistics(rows, {'max_additional_reviews': 0})


@pytest.mark.parametrize('criteria', [{}, {'max_additional_reviews': True}, {'max_additional_reviews': 1.0},
    {'max_additional_reviews': '1'}, {'max_additional_reviews': -1}, {'max_additional_reviews': None},
    {'max_additional_reviews': 0, 'allow_unknown': True}])
def test_monitor_criteria_are_strict_frozen_values_not_fresh_guesses(criteria):
    with pytest.raises(ValueError):monitor_statistics([], criteria)
