"""Pure pixel quality evidence, with no model call, file IO or release authority.

NumPy is imported only by array operations. File hashes and source identities
are caller-supplied provenance, not custody proved by this scorer. Integration
must check original files, model/processor receipts and an immutable plan.
"""
import copy
import hashlib
import json
import math
import re

SCORE = 'pixel-change-quality-score-v249'
COMPARISON = 'pixel-change-quality-comparison-v249'
MANIFEST = 'pixel-change-quality-manifest-v249'
PLAN = 'pixel-change-quality-plan-v249'
MAX_PIXELS = 16_000_000
SPLITS = {'exploratory', 'calibration', 'heldout'}
PROVENANCE_KEYS = {'dataset_id', 'dataset_revision', 'domain_scope', 'source_id', 'scene_id',
    'split', 'derived_group_id', 'manifest_hash', 'before_sha256', 'after_sha256',
    'label_sha256', 'valid_mask_sha256', 'pixel_grid_sha256',
    'model_sha256', 'processor_sha256', 'probability_sha256'}
SAMPLE_KEYS = {'sample_id', 'source_id', 'scene_id', 'split', 'derived_group_id',
    'before_sha256', 'after_sha256', 'label_sha256', 'valid_mask_sha256',
    'pixel_grid_sha256', 'width', 'height'}
COUNTS = {'total_pixels', 'source_valid_pixels', 'unknown_pixels', 'ignored_label_pixels',
    'excluded_pixels', 'evaluated_pixels', 'tp', 'fp', 'tn', 'fn',
    'actual_positive_pixels', 'predicted_positive_pixels'}
METRICS = {'precision', 'recall', 'specificity', 'f1', 'iou', 'balanced_accuracy', 'accuracy'}


def need(value, message='pixel quality contract is invalid'):
    if not value: raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def exact(value, keys):
    return type(value) is dict and set(value) == set(keys)


def text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 500 and '\0' not in value


def sha(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def checked_mapping(value):
    need(exact(value, {'positive', 'negative', 'ignore'}), 'explicit positive/negative/ignore label mapping is required')
    groups = []
    for key in ('positive', 'negative', 'ignore'):
        values = value[key]
        need(type(values) is list and len(values) <= 256
             and all(type(item) is int and 0 <= item <= 255 for item in values)
             and len(values) == len(set(values)), 'label codes must be unique uint8 integers, never booleans')
        need(key == 'ignore' or bool(values), 'positive and negative label classes must be declared')
        groups.append(set(values))
    need(not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]), 'label classes overlap')
    return {key: sorted(value[key]) for key in ('positive', 'negative', 'ignore')}


def checked_provenance(value):
    need(exact(value, PROVENANCE_KEYS), 'source provenance fields are incomplete or unknown')
    for key, item in value.items():
        need(sha(item) if key.endswith('_sha256') or key == 'manifest_hash' else text(item),
             'invalid source identity or SHA256')
    need(value['split'] in SPLITS, 'split must be explicit')
    return copy.deepcopy(value)


def checked_threshold(value):
    need(type(value) in {int, float} and math.isfinite(value) and 0 < value < 1,
         'probability threshold must be finite and strictly between zero and one')
    return float(value)


def array_sha256(value):
    """Canonical decoded-array hash; it is distinct from a PNG/NPY file SHA."""
    import numpy as np
    need(isinstance(value, np.ndarray) and not np.ma.isMaskedArray(value), 'plain arrays are required')
    if value.dtype.kind == 'f' and value.dtype.itemsize == 4:
        dtype, name = '<f4', 'float32-le'
    elif value.dtype == np.dtype('uint8'):
        dtype, name = 'uint8', 'uint8'
    elif value.dtype == np.dtype('bool'):
        dtype, name = 'uint8', 'bool-as-0-or-1'
    else: raise ValueError('unsupported decoded-array dtype')
    hashed = hashlib.sha256(canonical({'shape': list(value.shape), 'dtype': name, 'order': 'C'}).encode('utf-8') + b'\0')
    hashed.update(np.ascontiguousarray(value, dtype=dtype).tobytes(order='C'))
    return hashed.hexdigest()


def metrics(counts):
    tp, fp, tn, fn = (counts[key] for key in ('tp', 'fp', 'tn', 'fn'))
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    recall, specificity = ratio(tp, tp + fn), ratio(tn, tn + fp)
    return {'precision': ratio(tp, tp + fp), 'recall': recall, 'specificity': specificity,
        'f1': ratio(2 * tp, 2 * tp + fp + fn), 'iou': ratio(tp, tp + fp + fn),
        'balanced_accuracy': ((recall + specificity) / 2 if recall is not None and specificity is not None else None),
        'accuracy': ratio(tp + tn, tp + fp + tn + fn)}


def coverage_identity(provenance, mapping, shape, hashes):
    # Different models/thresholds may be compared; different ground truth,
    # source pixels, split, grid or evaluated mask must never be compared.
    source = {key: value for key, value in provenance.items()
              if key not in {'model_sha256', 'processor_sha256', 'probability_sha256'}}
    return {'source': source, 'mapping': mapping, 'shape': shape,
        'labels_array_sha256': hashes['labels'], 'valid_mask_array_sha256': hashes['valid_mask'],
        'evaluated_mask_array_sha256': hashes['evaluated_mask']}


def score_pixels(probability, labels, valid_mask, *, threshold, label_mapping, provenance):
    """Score actual float32 probability >= threshold against explicit GT codes.

    Infinity is always corruption. NaN is allowed only where the independently
    supplied source-valid mask is false. An unmapped GT code is never silently
    treated as background, including outside the evaluation area.
    """
    import numpy as np
    mapping = checked_mapping(label_mapping); source = checked_provenance(provenance)
    threshold = checked_threshold(threshold)
    for value in (probability, labels, valid_mask):
        need(isinstance(value, np.ndarray) and not np.ma.isMaskedArray(value)
             and value.ndim == 2 and value.shape == probability.shape, 'aligned two-dimensional arrays are required')
    need(probability.dtype.kind == 'f' and probability.dtype.itemsize == 4,
         'raw float32 probabilities are required, not a quantized preview or implicit cast')
    need(labels.dtype == np.dtype('uint8') and valid_mask.dtype == np.dtype('bool'),
         'labels must be uint8 and the explicit source-valid mask must be bool')
    height, width = probability.shape
    need(height > 0 and width > 0 and height * width <= MAX_PIXELS, 'pixel count is empty or over budget')
    finite = np.isfinite(probability)
    need(not bool(np.isinf(probability).any()) and bool(finite[valid_mask].all()),
         'nonfinite probabilities exist in source-valid pixels')
    need(bool(((probability[finite] >= 0) & (probability[finite] <= 1)).all()),
         'probabilities are outside zero to one')
    positive = np.isin(labels, mapping['positive']); negative = np.isin(labels, mapping['negative'])
    ignored = np.isin(labels, mapping['ignore'])
    need(bool((positive | negative | ignored).all()), 'ground truth contains an unmapped code')
    evaluated = valid_mask & ~ignored
    predicted = probability >= threshold
    tp = int((evaluated & positive & predicted).sum()); fp = int((evaluated & negative & predicted).sum())
    tn = int((evaluated & negative & ~predicted).sum()); fn = int((evaluated & positive & ~predicted).sum())
    unknown = int((~valid_mask).sum()); ignore_count = int((valid_mask & ignored).sum())
    counts = {'total_pixels': height * width, 'source_valid_pixels': int(valid_mask.sum()),
        'unknown_pixels': unknown, 'ignored_label_pixels': ignore_count,
        'excluded_pixels': unknown + ignore_count, 'evaluated_pixels': tp + fp + tn + fn,
        'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn,
        'actual_positive_pixels': tp + fn, 'predicted_positive_pixels': tp + fp}
    hashes = {'probability': array_sha256(probability), 'labels': array_sha256(labels),
        'valid_mask': array_sha256(valid_mask), 'evaluated_mask': array_sha256(evaluated)}
    shape = [height, width]
    value = {'schema': SCORE, 'status': 'scored' if counts['evaluated_pixels'] else 'no_evaluable_pixels',
        'provenance': source, 'threshold': threshold, 'decision_rule': 'probability_gte_threshold',
        'label_mapping': mapping, 'shape': shape, 'counts': counts, 'metrics': metrics(counts),
        'array_hashes': hashes, 'coverage_hash': digest(coverage_identity(source, mapping, shape, hashes)),
        'quality_evidence_only': True, 'release_authorized': False,
        'field_truth_verified': False, 'inference_label_isolation': 'must_be_verified_from_producer_receipts'}
    return {**value, 'score_hash': digest(value)}


def checked_score(value):
    keys = {'schema', 'status', 'provenance', 'threshold', 'decision_rule', 'label_mapping', 'shape',
        'counts', 'metrics', 'array_hashes', 'coverage_hash', 'quality_evidence_only',
        'release_authorized', 'field_truth_verified', 'inference_label_isolation', 'score_hash'}
    need(exact(value, keys) and value['schema'] == SCORE and sha(value['score_hash'])
         and value['score_hash'] == digest({k: v for k, v in value.items() if k != 'score_hash'}))
    source = checked_provenance(value['provenance']); mapping = checked_mapping(value['label_mapping'])
    checked_threshold(value['threshold'])
    shape = value['shape']; counts = value['counts']; hashes = value['array_hashes']
    need(type(shape) is list and len(shape) == 2 and all(type(n) is int and n > 0 for n in shape)
        and shape[0] * shape[1] <= MAX_PIXELS and exact(counts, COUNTS)
        and all(type(n) is int and n >= 0 for n in counts.values()))
    need(counts['total_pixels'] == shape[0] * shape[1]
        == counts['source_valid_pixels'] + counts['unknown_pixels']
        == counts['evaluated_pixels'] + counts['excluded_pixels']
        and counts['excluded_pixels'] == counts['unknown_pixels'] + counts['ignored_label_pixels']
        and counts['evaluated_pixels'] == sum(counts[key] for key in ('tp', 'fp', 'tn', 'fn'))
        and counts['actual_positive_pixels'] == counts['tp'] + counts['fn']
        and counts['predicted_positive_pixels'] == counts['tp'] + counts['fp'])
    need(exact(hashes, {'probability', 'labels', 'valid_mask', 'evaluated_mask'}) and all(sha(item) for item in hashes.values())
        and digest(value['metrics']) == digest(metrics(counts)) and exact(value['metrics'], METRICS)
        and value['coverage_hash'] == digest(coverage_identity(source, mapping, shape, hashes))
        and value['status'] == ('scored' if counts['evaluated_pixels'] else 'no_evaluable_pixels')
        and value['decision_rule'] == 'probability_gte_threshold' and value['quality_evidence_only'] is True
        and value['release_authorized'] is False and value['field_truth_verified'] is False
        and value['inference_label_isolation'] == 'must_be_verified_from_producer_receipts')
    return copy.deepcopy(value)


def compare_scores(baseline, candidate):
    baseline, candidate = checked_score(baseline), checked_score(candidate)
    need(baseline['coverage_hash'] == candidate['coverage_hash'], 'different source, split, GT or pixel coverage cannot be compared')
    need(baseline['counts']['evaluated_pixels'] > 0, 'no evaluable coverage, no quality comparison')
    changes = {key: (candidate['metrics'][key] - baseline['metrics'][key]
               if candidate['metrics'][key] is not None and baseline['metrics'][key] is not None else None)
               for key in sorted(METRICS)}
    value = {'schema': COMPARISON, 'baseline_score_hash': baseline['score_hash'],
        'candidate_score_hash': candidate['score_hash'], 'coverage_hash': baseline['coverage_hash'],
        'differences': {key: {'baseline': left, 'candidate': right, 'changed': left != right}
            for key, left, right in (
                ('model_sha256', baseline['provenance']['model_sha256'], candidate['provenance']['model_sha256']),
                ('processor_sha256', baseline['provenance']['processor_sha256'], candidate['provenance']['processor_sha256']),
                ('threshold', baseline['threshold'], candidate['threshold']),
                ('probability_array_sha256', baseline['array_hashes']['probability'], candidate['array_hashes']['probability']))},
        'metric_deltas': changes, 'quality_evidence_only': True, 'release_authorized': False,
        'statistical_significance': 'not_tested', 'forestry_generalization': 'not_established'}
    return {**value, 'comparison_hash': digest(value)}


def checked_manifest(value):
    need(exact(value, {'schema', 'dataset', 'label_mapping', 'samples', 'manifest_hash'})
        and value['schema'] == MANIFEST
        and value['manifest_hash'] == digest({k: v for k, v in value.items() if k != 'manifest_hash'}))
    dataset = value['dataset']
    need(exact(dataset, {'id', 'revision', 'source_ref', 'license_ref', 'domain_scope'})
        and all(text(item) for item in dataset.values()))
    checked_mapping(value['label_mapping'])
    samples = value['samples']
    need(type(samples) is list and 1 <= len(samples) <= 10000)
    identities = set(); partitions = {key: set() for key in SPLITS}
    for sample in samples:
        need(exact(sample, SAMPLE_KEYS))
        for key, item in sample.items():
            if key in {'width', 'height'}: need(type(item) is int and item > 0)
            elif key.endswith('_sha256'): need(sha(item))
            else: need(text(item))
        need(sample['sample_id'] not in identities and sample['split'] in SPLITS
             and sample['width'] * sample['height'] <= MAX_PIXELS)
        identities.add(sample['sample_id'])
        # Tags keep semantic namespaces distinct. Labels and all-valid masks
        # may legitimately share bytes across unrelated negative scenes.
        partitions[sample['split']].update({('source', sample['source_id']), ('scene', sample['scene_id']),
            ('group', sample['derived_group_id']), ('image', sample['before_sha256']), ('image', sample['after_sha256'])})
    need(not partitions['heldout'] & (partitions['calibration'] | partitions['exploratory']),
         'heldout source/scene/group/original imagery overlaps exposed data')
    return copy.deepcopy(value)


def checked_plan(value, manifest):
    manifest = checked_manifest(manifest)
    keys = {'schema', 'manifest_hash', 'study_kind', 'baseline', 'candidate',
        'selection_sample_ids', 'evaluation_sample_ids', 'acceptance_policy_hash', 'plan_hash'}
    need(exact(value, keys) and value['schema'] == PLAN and value['study_kind'] in {'threshold_calibration', 'model_comparison'}
        and value['manifest_hash'] == manifest['manifest_hash'] and sha(value['acceptance_policy_hash'])
        and value['plan_hash'] == digest({k: v for k, v in value.items() if k != 'plan_hash'}))
    for side in ('baseline', 'candidate'):
        arm = value[side]
        need(exact(arm, {'profile_id', 'model_sha256', 'processor_sha256', 'threshold'})
            and text(arm['profile_id']) and sha(arm['model_sha256']) and sha(arm['processor_sha256']))
        checked_threshold(arm['threshold'])
    if value['study_kind'] == 'threshold_calibration':
        need(all(value['baseline'][key] == value['candidate'][key] for key in ('model_sha256', 'processor_sha256')),
             'threshold calibration cannot silently change weights or processor')
    need(value['baseline']['profile_id'] != value['candidate']['profile_id']
         or digest(value['baseline']) == digest(value['candidate']), 'one profile ID cannot declare different parameters')
    samples = {item['sample_id']: item for item in manifest['samples']}
    for key in ('selection_sample_ids', 'evaluation_sample_ids'):
        ids = value[key]
        need(type(ids) is list and (bool(ids) or key == 'selection_sample_ids' and value['study_kind'] == 'model_comparison')
            and all(type(item) is str for item in ids)
            and len(ids) == len(set(ids)) and set(ids) <= set(samples))
    allowed_selection = {'calibration'} if value['study_kind'] == 'threshold_calibration' else {'calibration', 'exploratory'}
    need(all(samples[identity]['split'] in allowed_selection for identity in value['selection_sample_ids']),
         'heldout data cannot select a candidate; threshold fitting requires the calibration split')
    need(set(value['evaluation_sample_ids']) == {identity for identity, sample in samples.items() if sample['split'] == 'heldout'},
         'all frozen heldout samples must remain in the evaluation inventory')
    return copy.deepcopy(value)


def _sample_provenance(manifest, sample, arm, probability_sha256):
    dataset = manifest['dataset']
    return {'dataset_id': dataset['id'], 'dataset_revision': dataset['revision'], 'domain_scope': dataset['domain_scope'],
        **{key: sample[key] for key in ('source_id', 'scene_id', 'split', 'derived_group_id',
            'before_sha256', 'after_sha256', 'label_sha256', 'valid_mask_sha256', 'pixel_grid_sha256')},
        'manifest_hash': manifest['manifest_hash'], 'model_sha256': arm['model_sha256'],
        'processor_sha256': arm['processor_sha256'], 'probability_sha256': probability_sha256}


def score_frozen_sample(probability, labels, valid_mask, *, manifest, plan,
                        sample_id, side, probability_sha256):
    """Preferred heldout seam; declared source and arm pins come from the plan."""
    manifest = checked_manifest(manifest); plan = checked_plan(plan, manifest)
    need(side in {'baseline', 'candidate'} and sample_id in plan['evaluation_sample_ids'])
    sample = next(item for item in manifest['samples'] if item['sample_id'] == sample_id)
    need(getattr(probability, 'shape', None) == (sample['height'], sample['width']),
         'sample shape differs from frozen manifest')
    return score_pixels(probability, labels, valid_mask, threshold=plan[side]['threshold'],
        label_mapping=manifest['label_mapping'],
        provenance=_sample_provenance(manifest, sample, plan[side], probability_sha256))


def compare_frozen_scores(baseline, candidate, *, manifest, plan, sample_id):
    manifest = checked_manifest(manifest); plan = checked_plan(plan, manifest)
    need(sample_id in plan['evaluation_sample_ids'])
    sample = next(item for item in manifest['samples'] if item['sample_id'] == sample_id)
    for side, result in (('baseline', baseline), ('candidate', candidate)):
        result = checked_score(result)
        need(result['provenance'] == _sample_provenance(manifest, sample, plan[side],
            result['provenance']['probability_sha256'])
            and result['threshold'] == float(plan[side]['threshold'])
            and result['shape'] == [sample['height'], sample['width']]
            and result['label_mapping'] == checked_mapping(manifest['label_mapping']),
            'score does not belong to this frozen heldout sample and arm')
    if plan['study_kind'] == 'threshold_calibration':
        need(baseline['array_hashes']['probability'] == candidate['array_hashes']['probability']
             and baseline['provenance']['probability_sha256'] == candidate['provenance']['probability_sha256'],
             'threshold calibration must compare the same original probability output')
    return compare_scores(baseline, candidate)
