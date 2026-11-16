"""Pure fixed-rule projection and finite-policy monitoring.

The baseline result is a numerical projection of a frozen rule, never an
Invocation or execution receipt. No function grants authority, reads a clock,
chooses a live policy or changes the supplied records.
"""
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ERROR = '有限策略的固定数值、输入声明或监控分母无法核对'
_SAMPLE_KEYS = {'status', 'label', 'actual_matched', 'baseline_projection_matched'}
_STATUSES = {'queued', 'reserved', 'completed', 'failed', 'stopped'}
_LABELS = {'uncertain', 'should_alert', 'should_not_alert'}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _finite(value):
    # A Python integer is finite without first projecting it through a float.
    return type(value) is int or type(value) is float and math.isfinite(value)


def _field(scope):
    _need(type(scope) is dict)
    value = scope.get('field')
    _need(type(value) is str and bool(value.strip()))
    return value


def project(scope, attributes):
    """Project the one original value against both fixed numerical thresholds."""
    field = _field(scope)
    _need(type(attributes) is dict and set(attributes) == {field})
    value = attributes[field]
    operator = scope.get('operator')
    baseline = scope.get('baseline_threshold')
    candidate = scope.get('candidate_threshold')
    _need(type(operator) is str and operator in {'gt', 'lt'}
          and _finite(value) and _finite(baseline) and _finite(candidate))
    return {'value': value,
        'baseline_matched': value > baseline if operator == 'gt' else value < baseline,
        'candidate_matched': value > candidate if operator == 'gt' else value < candidate}


def input_spec(scope, extractor_manifest, source_manifest):
    """Describe the actual selected field schema; absent types are never guessed."""
    field = _field(scope)
    label = scope.get('field_label')
    unit = scope.get('unit')
    _need(type(label) is str and bool(label.strip()) and (unit is None or type(unit) is str))
    _need(type(extractor_manifest) is dict and type(source_manifest) is dict)
    schema = extractor_manifest.get('input_schema')
    _need(type(schema) is dict and type(schema.get('properties')) is dict)
    attributes = schema['properties'].get('attributes')
    _need(type(attributes) is dict and type(attributes.get('properties')) is dict)
    definition = attributes['properties'].get(field)
    _need(type(definition) is dict)
    kind = definition.get('type')
    _need(type(kind) is str and kind in {'integer', 'number'})
    specification = source_manifest.get('specification', {})
    _need(type(specification) is dict)
    timezone = specification.get('timezone', 'Asia/Shanghai')
    _need(type(timezone) is str and bool(timezone))
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(ERROR) from None
    return {'field': field, 'field_label': label, 'unit': unit,
        'value_kind': kind, 'source_timezone': timezone, 'clock_unknown_allowed': True}


def _classify(counts, label, matched):
    expected = label == 'should_alert'
    counts['tp' if expected and matched else 'fn' if expected else 'fp' if matched else 'tn'] += 1


def monitor_statistics(samples, criteria):
    """Keep every occupied input in the denominator; regress only on clear labels."""
    _need(type(samples) is list and type(criteria) is dict
          and set(criteria) == {'max_additional_reviews'})
    allowance = criteria['max_additional_reviews']
    _need(type(allowance) is int and allowance >= 0)
    result = {'total': len(samples), 'completed': 0, 'failed': 0, 'stopped': 0,
        'pending': 0, 'unlabelled': 0, 'labelled': 0,
        'baseline_projection': {key: 0 for key in ('tp', 'tn', 'fp', 'fn')},
        'candidate': {key: 0 for key in ('tp', 'tn', 'fp', 'fn')},
        'regressed': False}
    for sample in samples:
        _need(type(sample) is dict and set(sample) == _SAMPLE_KEYS)
        status, label = sample['status'], sample['label']
        _need(type(status) is str and status in _STATUSES
              and (label is None or type(label) is str and label in _LABELS))
        actual, baseline = sample['actual_matched'], sample['baseline_projection_matched']
        if status == 'completed':
            _need(type(actual) is bool and type(baseline) is bool)
            result['completed'] += 1
        else:
            _need(actual is None and baseline is None)
            result['pending' if status in {'queued', 'reserved'} else status] += 1
        if status == 'completed' and label in {'should_alert', 'should_not_alert'}:
            result['labelled'] += 1
            _classify(result['baseline_projection'], label, baseline)
            _classify(result['candidate'], label, actual)
        else:
            result['unlabelled'] += 1
    baseline, candidate = result['baseline_projection'], result['candidate']
    result['regressed'] = (candidate['fn'] > baseline['fn']
        or max(0, candidate['fp'] - baseline['fp']) > allowance)
    return result
