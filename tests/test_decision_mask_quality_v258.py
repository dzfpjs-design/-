"""Synthetic arrays only: no model/data custody, native run or quality claim."""
import copy

import pytest

from generic_agents import decision_mask_quality_v258 as subject
from generic_agents import pixel_change_quality_v249 as quality


@pytest.fixture
def np():
    # Root runs this file in the separately provisioned NumPy environment.
    import numpy
    return numpy


def sha(label): return quality.digest({'synthetic_test_identity': label})
def sealed(value, key): return {**value, key: quality.digest(value)}


def provenance(**changes):
    return {'dataset_id': 'synthetic-building-proxy', 'dataset_revision': 'fixed',
            'domain_scope': 'building_addition_removal_proxy_not_all_change_ground_truth',
            'source_id': 'original-pair', 'scene_id': 'new-area', 'split': 'heldout',
            'derived_group_id': 'new-geographic-group', 'manifest_hash': sha('manifest'),
            'before_sha256': sha('before'), 'after_sha256': sha('after'), 'label_sha256': sha('GT file'),
            'valid_mask_sha256': sha('valid file'), 'pixel_grid_sha256': sha('grid'),
            'model_sha256': sha('model'), 'processor_sha256': sha('processor'),
            'native_output_sha256': sha('actual native output file'), **changes}


def policy(rule='official_native_binary_decision', **parameters):
    return sealed({'rule_id': rule, 'parameters': parameters}, 'policy_hash')


MAPPING = {'positive': [1], 'negative': [0], 'ignore': [255]}


def score(np, decisions, labels, valid=None, *, decision_policy=None, **source):
    mask = np.array(decisions, dtype=bool); gt = np.array(labels, dtype='uint8')
    valid = np.ones(mask.shape, dtype=bool) if valid is None else np.array(valid, dtype=bool)
    return subject.score_decision_mask(mask, gt, valid, label_mapping=MAPPING,
        provenance=provenance(**source), decision_policy=decision_policy or policy())


def test_exact_counts_reuse_existing_metrics_without_probability_semantics(np, monkeypatch):
    called = []; original = quality.metrics
    def observed(counts):
        called.append(copy.deepcopy(counts)); return original(counts)
    monkeypatch.setattr(quality, 'metrics', observed)
    result = score(np, [[True, True], [False, False]], [[1, 0], [0, 1]])
    assert {key: result['counts'][key] for key in ('tp', 'fp', 'tn', 'fn')} == dict.fromkeys(('tp', 'fp', 'tn', 'fn'), 1)
    assert called == [result['counts']]
    assert result['metrics']['precision'] == result['metrics']['recall'] == result['metrics']['f1'] == .5
    assert result['metrics']['iou'] == pytest.approx(1 / 3)
    assert result['decision_semantics'] == 'native_boolean_decision_not_probability'
    assert 'probability' not in result['array_hashes'] and 'probability_sha256' not in result['provenance']
    assert 'threshold' not in result and 'forestry_generalization' not in result
    assert result['array_dtypes'] == subject.ARRAY_DTYPES
    assert result['release_authorized'] is result['field_truth_verified'] is False
    assert subject.checked_score(result) == result


def test_explicit_probability_gte_policy_preserves_actual_ties_and_array_hash(np):
    raw = np.array([[.5, .499], [.7, .1]], dtype='float32')
    labels = np.array([[1, 1], [0, 0]], dtype='uint8'); valid = np.ones(raw.shape, dtype=bool)
    original = raw.tobytes()
    decision = raw >= .5
    declared = policy('frozen_raw_probability_gte_threshold', threshold=.5,
                      source_probability_array_sha256=quality.array_sha256(raw), original_dtype='float32')
    result = subject.score_decision_mask(decision, labels, valid, label_mapping=MAPPING,
                                        provenance=provenance(), decision_policy=declared)
    probability_source = provenance(); probability_source['probability_sha256'] = probability_source.pop('native_output_sha256')
    old = quality.score_pixels(raw, labels, valid, threshold=.5, label_mapping=MAPPING, provenance=probability_source)
    assert result['counts'] == old['counts'] and result['metrics'] == old['metrics']
    assert decision[0, 0] and raw.tobytes() == original
    assert result['decision_policy']['parameters']['source_probability_array_sha256'] == quality.array_sha256(raw)
    with pytest.raises(ValueError): subject.compare_decision_scores(result, old)


@pytest.mark.parametrize('labels,decisions,expected', [
    ([[1, 0]], [[False, False]], {'precision': None, 'recall': 0, 'f1': 0, 'iou': 0}),
    ([[0, 0]], [[False, False]], {'precision': None, 'recall': None, 'f1': None, 'iou': None, 'balanced_accuracy': None}),
    ([[1, 1]], [[True, True]], {'precision': 1, 'recall': 1, 'specificity': None, 'balanced_accuracy': None}),
])
def test_undefined_denominators_remain_null(np, labels, decisions, expected):
    result = score(np, decisions, labels)
    for key, value in expected.items(): assert result['metrics'][key] == value


def test_unknown_and_ignored_coverage_remain_disjoint_and_empty_cannot_win(np):
    result = score(np, [[True, True, True, False]], [[1, 255, 1, 0]], [[False, True, True, False]])
    c = result['counts']
    assert (c['unknown_pixels'], c['ignored_label_pixels'], c['excluded_pixels'], c['evaluated_pixels'], c['tp']) == (2, 1, 3, 1, 1)
    for empty in (score(np, [[True]], [[255]]), score(np, [[False]], [[1]], [[False]])):
        assert empty['status'] == 'no_evaluable_pixels'
        assert all(value is None for value in empty['metrics'].values())
        with pytest.raises(ValueError): subject.compare_decision_scores(empty, empty)
    with pytest.raises(ValueError): score(np, [[False]], [[17]], [[False]])


@pytest.mark.parametrize('kind', ['uint8_mask', 'float32_mask', 'float64_mask', 'float_nan_mask',
    'float_labels', 'nan_labels', 'integer_valid', 'shape', 'three_dimensions', 'empty', 'masked_array', 'list'])
def test_plain_bool_decisions_and_exact_dtypes_are_required_without_casts(np, kind):
    mask = np.array([[True]]); labels = np.array([[1]], dtype='uint8'); valid = np.array([[True]])
    if kind == 'uint8_mask': mask = mask.astype('uint8')
    elif kind == 'float32_mask': mask = mask.astype('float32')
    elif kind == 'float64_mask': mask = mask.astype('float64')
    elif kind == 'float_nan_mask': mask = np.array([[float('nan')]], dtype='float32')
    elif kind == 'float_labels': labels = labels.astype('float32')
    elif kind == 'nan_labels': labels = np.array([[float('nan')]])
    elif kind == 'integer_valid': valid = valid.astype('uint8')
    elif kind == 'shape': labels = np.array([[1, 0]], dtype='uint8')
    elif kind == 'three_dimensions': mask = mask[:, :, None]
    elif kind == 'empty': mask = mask[:0]; labels = labels[:0]; valid = valid[:0]
    elif kind == 'masked_array': mask = np.ma.array(mask, mask=False)
    else: mask = [[True]]
    with pytest.raises(ValueError): subject.score_decision_mask(mask, labels, valid,
        label_mapping=MAPPING, provenance=provenance(), decision_policy=policy())


def test_readonly_strided_inputs_are_not_modified_and_pixel_budget_precedes_allocation(np, monkeypatch):
    mask = np.array([[True, False, True, False]])[:, ::2]
    labels = np.array([[1, 0]], dtype='uint8'); valid = np.array([[True, False]])
    before = [value.tobytes() for value in (mask, labels, valid)]
    for value in (mask, labels, valid): value.flags.writeable = False
    result = subject.score_decision_mask(mask, labels, valid, label_mapping=MAPPING,
                                        provenance=provenance(), decision_policy=policy())
    assert before == [value.tobytes() for value in (mask, labels, valid)]
    assert result['array_hashes']['decision_mask'] == quality.array_sha256(mask)
    monkeypatch.setattr(subject, 'MAX_PIXELS', 1)
    with pytest.raises(ValueError): subject.score_decision_mask(mask, labels, valid,
        label_mapping=MAPPING, provenance=provenance(), decision_policy=policy())
    with pytest.raises(ValueError): subject.checked_score(result)


@pytest.mark.parametrize('bad', [None, {}, {'rule_id': '', 'parameters': {}, 'policy_hash': '0' * 64},
    {'rule_id': 'native', 'parameters': {}, 'policy_hash': '0' * 64},
    {'rule_id': 'native', 'parameters': {}, 'policy_hash': '0' * 64, 'threshold': .5}])
def test_closed_explicit_policy_with_correct_hash_is_mandatory(np, bad):
    mask = np.array([[True]]); labels = np.array([[1]], dtype='uint8')
    with pytest.raises(ValueError): subject.score_decision_mask(mask, labels, mask,
        label_mapping=MAPPING, provenance=provenance(), decision_policy=bad)


@pytest.mark.parametrize('parameters', [{'nested': {}}, {'list': []}, {'nan': float('nan')},
    {'infinite': float('inf')}, {'': 1}, {'nul': '\0'}, {'long': 'x' * 501}, {'numpy_scalar': 'replace'}])
def test_policy_parameters_are_finite_flat_primitives(np, parameters):
    if 'numpy_scalar' in parameters: parameters = {'numpy_scalar': np.float32(.5)}
    # Invalid nonfinite values cannot be signed by the canonical helper either.
    candidate = {'rule_id': 'native', 'parameters': parameters, 'policy_hash': '0' * 64}
    with pytest.raises(ValueError): subject.checked_policy(candidate)


def test_policy_allows_actual_native_units_and_primitive_configuration_without_probability_claim(np):
    declared = policy('official_angle_rule', angle_degrees=155.0, enabled=True, mode='native', optional=None, batches=4)
    assert subject.checked_policy(declared) == declared
    result = score(np, [[True]], [[1]], decision_policy=declared)
    assert result['decision_policy']['parameters']['angle_degrees'] == 155
    assert 'threshold' not in result
    too_many = policy(**{f'key_{i}': i for i in range(subject.MAX_POLICY_PARAMETERS + 1)})
    with pytest.raises(ValueError): subject.checked_policy(too_many)


def test_native_output_provenance_never_fills_a_fake_probability_pin(np):
    source = provenance(); source['probability_sha256'] = source.pop('native_output_sha256')
    mask = np.array([[True]]); labels = np.array([[1]], dtype='uint8')
    with pytest.raises(ValueError): subject.score_decision_mask(mask, labels, mask,
        label_mapping=MAPPING, provenance=source, decision_policy=policy())
    with pytest.raises(ValueError): subject.checked_provenance(provenance(split='unknown'))


@pytest.mark.parametrize('changed', ['source_id', 'scene_id', 'split', 'derived_group_id', 'before_sha256',
    'after_sha256', 'label_sha256', 'valid_mask_sha256', 'pixel_grid_sha256', 'manifest_hash', 'domain_scope'])
def test_comparison_rejects_different_declared_scope_or_coverage(np, changed):
    left = score(np, [[False, True]], [[1, 0]])
    other = 'calibration' if changed == 'split' else sha(changed) if changed.endswith('sha256') or changed == 'manifest_hash' else 'other-' + changed
    right = score(np, [[True, False]], [[1, 0]], **{changed: other})
    with pytest.raises(ValueError): subject.compare_decision_scores(left, right)


def test_actual_gt_and_valid_changes_cannot_hide_behind_same_declared_file_hashes(np):
    left = score(np, [[False, True]], [[1, 0]])
    for right in (score(np, [[True, False]], [[0, 1]]),
                  score(np, [[True, False]], [[1, 0]], [[True, False]])):
        with pytest.raises(ValueError): subject.compare_decision_scores(left, right)


def test_comparison_reports_native_policy_output_and_model_differences(np):
    left = score(np, [[False, True]], [[1, 0]], decision_policy=policy('native', enabled=True))
    right = score(np, [[True, False]], [[1, 0]], decision_policy=policy('native', enabled=1),
                  model_sha256=sha('other model'), processor_sha256=sha('other recipe'),
                  native_output_sha256=sha('other actual mask file'))
    comparison = subject.compare_decision_scores(left, right)
    assert all(value['changed'] is True for value in comparison['differences'].values())
    assert comparison['metric_deltas']['f1'] == 1
    assert comparison['domain_generalization'] == 'not_established'
    assert comparison['label_interpretation'] == 'relative_to_declared_labels_not_verified_field_truth'
    assert comparison['statistical_significance'] == 'not_tested'
    assert subject.checked_comparison(comparison, left, right) == comparison
    forged = copy.deepcopy(comparison); forged['metric_deltas']['f1'] = 0
    forged = sealed({key: value for key, value in forged.items() if key != 'comparison_hash'}, 'comparison_hash')
    with pytest.raises(ValueError): subject.checked_comparison(forged, left, right)


@pytest.mark.parametrize('kind', ['count_bool', 'counts_math', 'metrics', 'dtypes', 'claim', 'extra', 'policy'])
def test_cold_checker_rejects_rehashed_semantic_corruption(np, kind):
    result = score(np, [[True, False]], [[1, 0]])
    if kind == 'count_bool': result['counts']['tp'] = True
    elif kind == 'counts_math': result['counts']['unknown_pixels'] += 1
    elif kind == 'metrics': result['metrics']['precision'] = .25
    elif kind == 'dtypes': result['array_dtypes']['decision_mask'] = 'float32'
    elif kind == 'claim': result['field_truth_verified'] = True
    elif kind == 'extra': result['probability_semantics'] = 'calibrated'
    else: result['decision_policy']['parameters']['new'] = 'unsigned'
    result = sealed({key: value for key, value in result.items() if key != 'score_hash'}, 'score_hash')
    with pytest.raises(ValueError): subject.checked_score(result)


def test_identical_decisions_compare_without_claiming_independent_quality(np):
    result = score(np, [[True, False]], [[1, 0]])
    comparison = subject.compare_decision_scores(result, result)
    assert all(value['changed'] is False for value in comparison['differences'].values())
    assert all(value == 0 for value in comparison['metric_deltas'].values())
    assert comparison['quality_evidence_only'] is True
    assert comparison['release_authorized'] is comparison['field_truth_verified'] is False
