"""Compare frozen managed thresholds without issuing execution authority.

The outer freeze owns actual provenance, sample independence, publication and
permissions. This module validates the saved managed-rule shape and uses its
existing calculator; it never invokes a gateway, creates work or reads a DB.
"""
import copy
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from jsonschema.exceptions import SchemaError

from .local_rules_api import SCHEMA, Definition, _assemble_manifest, _calculate_saved
from .typed_payload_codec_v131 import canonical_json


_ERROR = '冻结比较输入不符合约定，请核对保存的规则、样本和判据'
_INVALID = '此样本不符合原规则的有限数值与字段契约，不能当作未报警'
_EXPECTED = {'should_alert', 'should_not_alert', 'uncertain'}


def _need(condition):
    if not condition:
        raise ValueError(_ERROR)


def _manifest(value):
    """Check the same saved shape as local_rules_api, without authoring audits."""
    try:
        _need(type(value) is dict)
        original = canonical_json(value)
        saved = copy.deepcopy(value)
        specification = saved['specification']
        metadata = specification['managed_numeric_rule']
        rules = specification['rules']
        _need(type(metadata) is dict and metadata.get('schema') == SCHEMA)
        _need(type(rules) is list and len(rules) == 1)
        definition = Definition.model_validate({
            'title': metadata['title'], 'rationale': metadata['rationale'],
            'entity_id': metadata['entity_id'], 'rules': rules,
        })
        request_id = UUID(metadata['creation_request_id'])
        UUID(metadata['package_id'])
        UUID(metadata['entity_id'])
        _need(type(metadata['entity_type']) is str and bool(metadata['entity_type'].strip()))
        fields = saved['input_schema']['properties']['attributes']['properties']
        rule = definition.rules[0]
        _need(type(fields) is dict and set(fields) == {rule.field})
        field_schema = fields[rule.field]
        _need(type(field_schema) is dict and field_schema.get('type') in {'number', 'integer'})
        _need(field_schema.get('unit') is None or field_schema['unit'] == rule.unit)
        expected = _assemble_manifest(definition, request_id, metadata['creation_fingerprint'],
            saved['owner'], metadata['package_id'], metadata['entity_type'], metadata['entity_id'],
            fields, {rule.field: rule.unit}).model_dump(mode='json')
        _need(canonical_json(expected) == original)
        return saved, rule.field
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, OverflowError, RecursionError, SchemaError):
        raise ValueError(_ERROR) from None


def _fixed_manifest(saved):
    """Ignore only new identity/authoring descriptions and the single threshold."""
    fixed = copy.deepcopy(saved)
    for key in ('name', 'owner', 'description'):
        del fixed[key]
    metadata = fixed['specification']['managed_numeric_rule']
    for key in ('title', 'rationale', 'creation_request_id', 'creation_fingerprint'):
        del metadata[key]
    del fixed['specification']['rules'][0]['threshold']
    return canonical_json(fixed)


def _summary():
    return {'tp': 0, 'tn': 0, 'fp': 0, 'fn': 0, 'unknown': 0, 'invalid': 0, 'alerts': 0}


def _view_value(values, field):
    """Keep JSON values typed; non-JSON invalid values cannot enter a receipt."""
    value = values.get(field) if type(values) is dict else None
    try:
        canonical_json(value)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None
    return copy.deepcopy(value)


def _count(counts, expected, matched):
    counts['alerts'] += int(matched)
    if expected == 'uncertain':
        counts['unknown'] += 1
    elif expected == 'should_alert':
        counts['tp' if matched else 'fn'] += 1
    else:
        counts['fp' if matched else 'tn'] += 1


def compare_frozen_thresholds(baseline: dict, candidate: dict, samples: list, criteria: dict) -> dict:
    """Return every paired row and the frozen mechanism gate, never a grant.

    Invalid manifests/criteria/sample envelopes raise ValueError. Invalid sample
    values instead remain visible with null matches and an invalid count on
    both sides. NaN/Infinity have no valid JSON representation, so their display
    value is null with an issue; the supplied inputs are never changed.
    """
    baseline, field = _manifest(baseline)
    candidate, candidate_field = _manifest(candidate)
    _need(field == candidate_field and _fixed_manifest(baseline) == _fixed_manifest(candidate))
    _need(type(samples) is list)
    _need(type(criteria) is dict and set(criteria) == {'max_additional_reviews'})
    additional = criteria['max_additional_reviews']
    _need(type(additional) is int and 0 <= additional <= len(samples))
    rows = []
    baseline_counts, candidate_counts = _summary(), _summary()
    baseline_cap, candidate_cap = SimpleNamespace(data=baseline), SimpleNamespace(data=candidate)

    for item in samples:
        _need(type(item) is dict and set(item) == {'id', 'values', 'expected'})
        identity, expected = item['id'], item['expected']
        _need(type(identity) is str and bool(identity) and type(expected) is str and expected in _EXPECTED)
        # Validate these output strings without admitting arbitrary non-JSON IDs.
        canonical_json([identity, expected])
        values = item['values']
        row = {'id': identity, 'expected': expected, 'value': _view_value(values, field),
            'baseline_matched': None, 'candidate_matched': None, 'issue': None}
        try:
            _need(type(values) is dict)
            detached = copy.deepcopy(values)
            baseline_result = _calculate_saved(baseline_cap, detached)
            candidate_result = _calculate_saved(candidate_cap, detached)
            baseline_match = baseline_result[0]['matched']
            candidate_match = candidate_result[0]['matched']
        except (HTTPException, ValueError, TypeError, OverflowError, RecursionError):
            row['issue'] = _INVALID
            baseline_counts['invalid'] += 1
            candidate_counts['invalid'] += 1
        else:
            row['baseline_matched'], row['candidate_matched'] = baseline_match, candidate_match
            _count(baseline_counts, expected, baseline_match)
            _count(candidate_counts, expected, candidate_match)
        rows.append(row)

    issues = []
    if baseline_counts['tp'] + baseline_counts['fn'] < 2:
        issues.append('比较至少需要2个本应报警的有效已标样本')
    if baseline_counts['tn'] + baseline_counts['fp'] < 2:
        issues.append('比较至少需要2个本不应报警的有效已标样本')
    if baseline_counts['unknown']:
        issues.append('仍有尚不能判断的样本，不能启用试用')
    if baseline_counts['invalid']:
        issues.append('仍有无效样本，必须保留并核对原依据')
    if candidate_counts['fn'] > baseline_counts['fn']:
        issues.append('候选漏报数增加，不能启用试用')
    if candidate_counts['fp'] > baseline_counts['fp']:
        issues.append('候选误报数增加，不能启用试用')
    if not (candidate_counts['fp'] < baseline_counts['fp'] or candidate_counts['fn'] < baseline_counts['fn']):
        issues.append('候选误报和漏报均未减少')
    if candidate_counts['alerts'] > baseline_counts['alerts'] + additional:
        issues.append('候选核查数超过事先冻结的允许增加量')
    return {'per_sample': rows, 'baseline': baseline_counts, 'candidate': candidate_counts,
        'eligible_for_trial': not issues, 'issues': issues}
