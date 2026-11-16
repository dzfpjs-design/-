"""Pixel metric/provenance contracts; no model, dataset or field-quality claim."""
import copy

import pytest

from generic_agents import pixel_change_quality_v249 as quality


@pytest.fixture
def np():
    # The application does not acquire a new mandatory NumPy import. Root runs
    # these array tests in an explicitly provisioned isolated NumPy runtime.
    import numpy
    return numpy


def sha(label): return quality.digest({'public_test_identity': label})


def sealed(value, key): return {**value, key: quality.digest(value)}


def provenance(**changes):
    return {'dataset_id': 'public-urban-fixture', 'dataset_revision': 'fixed-revision',
        'domain_scope': 'public_urban_change', 'source_id': 'original-source-pair',
        'scene_id': 'heldout-city', 'split': 'heldout', 'derived_group_id': 'heldout-city-parent',
        'manifest_hash': sha('manifest'), 'before_sha256': sha('before'), 'after_sha256': sha('after'),
        'label_sha256': sha('GT bytes'), 'valid_mask_sha256': sha('source-valid bytes'),
        'pixel_grid_sha256': sha('pixel-grid'), 'model_sha256': sha('model'),
        'processor_sha256': sha('processor'), 'probability_sha256': sha('NPY bytes'), **changes}


MAPPING = {'positive': [255], 'negative': [0], 'ignore': [128]}


def score(np, probabilities, labels, valid=None, *, threshold=.5, **source):
    p = np.asarray(probabilities, dtype='float32'); gt = np.asarray(labels, dtype='uint8')
    mask = np.ones(p.shape, dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    return quality.score_pixels(p, gt, mask, threshold=threshold,
        label_mapping=MAPPING, provenance=provenance(**source))


def test_failed_all_negative_prediction_keeps_false_negatives_and_precision_undefined(np):
    result = score(np, [[0, 0], [0, 0]], [[255, 0], [255, 0]])
    assert {key: result['counts'][key] for key in ('tp', 'fp', 'tn', 'fn')} == {'tp': 0, 'fp': 0, 'tn': 2, 'fn': 2}
    assert result['metrics'] == {'precision': None, 'recall': 0, 'specificity': 1,
        'f1': 0, 'iou': 0, 'balanced_accuracy': .5, 'accuracy': .5}
    assert result['release_authorized'] is result['field_truth_verified'] is False


def test_exact_confusion_counts_scores_and_production_greater_equal_tie(np):
    result = score(np, [[.5, .9], [.1, .2]], [[255, 0], [0, 255]])
    assert {key: result['counts'][key] for key in ('tp', 'fp', 'tn', 'fn')} == {'tp': 1, 'fp': 1, 'tn': 1, 'fn': 1}
    assert result['metrics']['precision'] == result['metrics']['recall'] == result['metrics']['f1'] == .5
    assert result['metrics']['iou'] == pytest.approx(1 / 3)
    assert quality.checked_score(result) == result


def test_no_positive_labels_is_not_perfect_positive_class_quality(np):
    result = score(np, [[0, 0]], [[0, 0]])
    assert result['metrics']['precision'] is result['metrics']['recall'] is None
    assert result['metrics']['f1'] is result['metrics']['iou'] is result['metrics']['balanced_accuracy'] is None
    assert result['metrics']['specificity'] == result['metrics']['accuracy'] == 1


def test_no_negative_labels_makes_balanced_accuracy_undefined(np):
    result = score(np, [[1, 1]], [[255, 255]])
    assert result['metrics']['precision'] == result['metrics']['recall'] == result['metrics']['f1'] == 1
    assert result['metrics']['specificity'] is result['metrics']['balanced_accuracy'] is None


def test_unknown_and_ignored_counts_are_disjoint_and_zero_coverage_cannot_win(np):
    result = score(np, [[float('nan'), .9, .8, .1]], [[255, 128, 255, 0]], [[False, True, True, False]])
    c = result['counts']
    assert c['total_pixels'] == 4 and c['unknown_pixels'] == 2 and c['ignored_label_pixels'] == 1
    assert c['excluded_pixels'] == 3 and c['evaluated_pixels'] == c['tp'] == 1
    empty = score(np, [[float('nan')]], [[255]], [[False]])
    assert empty['status'] == 'no_evaluable_pixels' and all(value is None for value in empty['metrics'].values())
    with pytest.raises(ValueError): quality.compare_scores(empty, empty)


@pytest.mark.parametrize('kind', ['float64_probability', 'quantized_probability', 'float_labels',
    'uint8_mask', 'shape', 'three_dimensions', 'empty', 'masked_array'])
def test_wrong_dtypes_shapes_and_implicit_masks_are_rejected(np, kind):
    p = np.array([[.5]], dtype='float32'); gt = np.array([[255]], dtype='uint8'); mask = np.array([[True]])
    if kind == 'float64_probability': p = p.astype('float64')
    elif kind == 'quantized_probability': p = np.array([[32768]], dtype='uint16')
    elif kind == 'float_labels': gt = gt.astype('float32')
    elif kind == 'uint8_mask': mask = np.array([[255]], dtype='uint8')
    elif kind == 'shape': gt = np.array([[0, 255]], dtype='uint8')
    elif kind == 'three_dimensions': p = p[:, :, None]
    elif kind == 'empty': p = p[:0]; gt = gt[:0]; mask = mask[:0]
    else: p = np.ma.array(p, mask=False)
    with pytest.raises(ValueError):
        quality.score_pixels(p, gt, mask, threshold=.5, label_mapping=MAPPING, provenance=provenance())


@pytest.mark.parametrize('value,valid', [(float('nan'), True), (float('inf'), False),
    (float('-inf'), False), (-.01, False), (1.01, False)])
def test_nonfinite_or_out_of_domain_probabilities_are_not_hidden_by_exclusion(np, value, valid):
    with pytest.raises(ValueError): score(np, [[value]], [[255]], [[valid]])


@pytest.mark.parametrize('threshold', [True, False, '0.5', 0, 1, float('nan'), float('inf')])
def test_threshold_is_explicit_finite_production_compatible_scalar(np, threshold):
    with pytest.raises(ValueError): score(np, [[.5]], [[255]], threshold=threshold)


def test_explicit_gt_mapping_is_not_the_platform_mask_encoding(np):
    result = score(np, [[.9]], [[255]])
    assert result['counts']['actual_positive_pixels'] == 1
    with pytest.raises(ValueError): score(np, [[.1]], [[17]], [[False]])
    for mapping in ({'positive': [True], 'negative': [0], 'ignore': []},
                    {'positive': [255], 'negative': [255], 'ignore': []},
                    {'positive': [255], 'negative': [0], 'ignore': [255]}):
        with pytest.raises(ValueError): quality.checked_mapping(mapping)


def test_readonly_input_arrays_are_not_mutated_and_pixel_budget_is_strict(np, monkeypatch):
    p = np.array([[.5, float('nan')]], dtype='float32'); gt = np.array([[255, 0]], dtype='uint8'); valid = np.array([[True, False]])
    snapshots = [value.tobytes() for value in (p, gt, valid)]
    for value in (p, gt, valid): value.flags.writeable = False
    quality.score_pixels(p, gt, valid, threshold=.5, label_mapping=MAPPING, provenance=provenance())
    assert snapshots == [value.tobytes() for value in (p, gt, valid)]
    monkeypatch.setattr(quality, 'MAX_PIXELS', 1)
    with pytest.raises(ValueError): quality.score_pixels(p, gt, valid,
        threshold=.5, label_mapping=MAPPING, provenance=provenance())


@pytest.mark.parametrize('changed', ['scene_id', 'split', 'derived_group_id', 'before_sha256',
    'label_sha256', 'valid_mask_sha256', 'pixel_grid_sha256', 'manifest_hash'])
def test_comparison_requires_same_actual_scope_and_declared_coverage(np, changed):
    baseline = score(np, [[.1, .9]], [[255, 0]])
    other = 'calibration' if changed == 'split' else sha(changed) if changed.endswith('sha256') or changed == 'manifest_hash' else 'other-' + changed
    candidate = score(np, [[.9, .1]], [[255, 0]], **{changed: other})
    with pytest.raises(ValueError): quality.compare_scores(baseline, candidate)


def test_actual_pixel_mask_or_label_change_is_caught_even_with_same_declared_hashes(np):
    baseline = score(np, [[.1, .9]], [[255, 0]])
    for candidate in (score(np, [[.1, .9]], [[255, 0]], [[True, False]]),
                      score(np, [[.1, .9]], [[0, 255]])):
        with pytest.raises(ValueError): quality.compare_scores(baseline, candidate)


def test_different_models_and_processing_are_explicit_comparison_factors(np):
    baseline = score(np, [[.1, .9]], [[255, 0]])
    candidate = score(np, [[.9, .1]], [[255, 0]], model_sha256=sha('other model'), processor_sha256=sha('other processor'))
    compared = quality.compare_scores(baseline, candidate)
    assert compared['metric_deltas']['f1'] == 1
    assert compared['differences']['model_sha256']['changed'] is True
    assert compared['differences']['processor_sha256']['changed'] is True
    assert compared['release_authorized'] is False and compared['statistical_significance'] == 'not_tested'


def manifest():
    samples = []
    for identity, split in (('c', 'calibration'), ('h', 'heldout')):
        samples.append({'sample_id': identity, 'source_id': 'source-' + identity, 'scene_id': 'city-' + identity,
            'split': split, 'derived_group_id': 'parent-' + identity,
            'before_sha256': sha('before-' + identity), 'after_sha256': sha('after-' + identity),
            'label_sha256': sha('identical empty GT is allowed'), 'valid_mask_sha256': sha('same all-valid mask'),
            'pixel_grid_sha256': sha('same grid shape'), 'width': 2, 'height': 1})
    return sealed({'schema': quality.MANIFEST, 'dataset': {'id': 'public-urban-fixture', 'revision': 'fixed',
        'source_ref': 'public author archive; synthetic identities in this unit test', 'license_ref': 'caller must verify',
        'domain_scope': 'public_urban_change'}, 'label_mapping': MAPPING, 'samples': samples}, 'manifest_hash')


def plan(m, **changes):
    return sealed({'schema': quality.PLAN, 'manifest_hash': m['manifest_hash'],
        'study_kind': 'threshold_calibration',
        'baseline': {'profile_id': 'baseline', 'model_sha256': sha('weights'), 'processor_sha256': sha('processor'), 'threshold': .5},
        'candidate': {'profile_id': 'candidate', 'model_sha256': sha('weights'), 'processor_sha256': sha('processor'), 'threshold': .25},
        'selection_sample_ids': ['c'], 'evaluation_sample_ids': ['h'],
        'acceptance_policy_hash': sha('predeclared policy'), **changes}, 'plan_hash')


@pytest.mark.parametrize('key', ['source_id', 'scene_id', 'derived_group_id', 'before_sha256', 'after_sha256'])
def test_heldout_cannot_reuse_exposed_parent_or_original_images_under_new_sample_ids(key):
    m = manifest(); m['samples'][1][key] = m['samples'][0][key]
    m = sealed({k: value for k, value in m.items() if k != 'manifest_hash'}, 'manifest_hash')
    with pytest.raises(ValueError): quality.checked_manifest(m)


def test_equal_empty_labels_and_all_valid_masks_alone_are_not_input_leakage():
    m = manifest(); assert quality.checked_manifest(m) == m
    assert quality.checked_plan(plan(m), m)['selection_sample_ids'] == ['c']
    with pytest.raises(ValueError): quality.checked_plan(plan(m, selection_sample_ids=['h']), m)
    with pytest.raises(ValueError): quality.checked_plan(plan(m, evaluation_sample_ids=[]), m)


def test_threshold_calibration_cannot_change_model_while_declared_model_comparison_can():
    m = manifest(); p = plan(m); changed = copy.deepcopy(p['candidate']); changed['processor_sha256'] = sha('different normalization')
    with pytest.raises(ValueError): quality.checked_plan(plan(m, candidate=changed), m)
    accepted = plan(m, study_kind='model_comparison', candidate=changed, selection_sample_ids=[])
    assert quality.checked_plan(accepted, m)['candidate'] == changed


def test_frozen_heldout_scoring_binds_arm_and_reuses_probability_for_threshold_attribution(np):
    m = manifest(); p = plan(m); probability = np.array([[.4, .1]], dtype='float32')
    labels = np.array([[255, 0]], dtype='uint8'); valid = np.ones((1, 2), dtype=bool)
    base = quality.score_frozen_sample(probability, labels, valid, manifest=m, plan=p,
        sample_id='h', side='baseline', probability_sha256=sha('same raw NPY'))
    candidate = quality.score_frozen_sample(probability, labels, valid, manifest=m, plan=p,
        sample_id='h', side='candidate', probability_sha256=sha('same raw NPY'))
    assert quality.compare_frozen_scores(base, candidate, manifest=m, plan=p, sample_id='h')['metric_deltas']['f1'] == 1
    with pytest.raises(ValueError): quality.score_frozen_sample(probability, labels, valid,
        manifest=m, plan=p, sample_id='c', side='candidate', probability_sha256=sha('same raw NPY'))
    changed = quality.score_frozen_sample(np.array([[.45, .1]], dtype='float32'), labels, valid,
        manifest=m, plan=p, sample_id='h', side='candidate', probability_sha256=sha('other NPY'))
    with pytest.raises(ValueError): quality.compare_frozen_scores(base, changed, manifest=m, plan=p, sample_id='h')


def test_frozen_model_comparison_allows_explicitly_different_processor_and_probability(np):
    m = manifest(); candidate = {'profile_id': 'other-model', 'model_sha256': sha('other-model'),
        'processor_sha256': sha('different-author-normalization'), 'threshold': .5}
    p = plan(m, study_kind='model_comparison', candidate=candidate)
    labels = np.array([[255, 0]], dtype='uint8'); valid = np.ones((1, 2), dtype=bool)
    base = quality.score_frozen_sample(np.array([[.01, .01]], dtype='float32'), labels, valid,
        manifest=m, plan=p, sample_id='h', side='baseline', probability_sha256=sha('baseline NPY'))
    alternative = quality.score_frozen_sample(np.array([[.7, .1]], dtype='float32'), labels, valid,
        manifest=m, plan=p, sample_id='h', side='candidate', probability_sha256=sha('candidate NPY'))
    result = quality.compare_frozen_scores(base, alternative, manifest=m, plan=p, sample_id='h')
    assert result['metric_deltas']['f1'] == 1 and result['release_authorized'] is False
    assert result['differences']['processor_sha256']['changed'] is True


def test_resealed_invalid_counts_or_metric_do_not_pass_a_comparison(np):
    value = score(np, [[.9, .1]], [[255, 0]])
    for mutate in (lambda item: item['counts'].__setitem__('tp', True),
                   lambda item: item['metrics'].__setitem__('precision', None),
                   lambda item: item.__setitem__('release_authorized', True)):
        broken = copy.deepcopy(value); mutate(broken)
        broken = sealed({key: item for key, item in broken.items() if key != 'score_hash'}, 'score_hash')
        with pytest.raises(ValueError): quality.compare_scores(value, broken)
