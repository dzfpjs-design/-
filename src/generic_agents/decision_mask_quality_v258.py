"""Pure quality evidence for native boolean decisions, never probabilities.

File hashes and source declarations are supplied by the caller; this module
does not prove custody, training separation or field truth. A Changer decision
may be derived explicitly from its frozen probability array, with that array's
hash and the exact >= rule in decision_policy. A native binary mask must never
be advertised as a probability map. SpaceNet building proxies remain building
proxies: an FP against them need not be a false alarm for every change class.
"""
import copy
import math

from . import pixel_change_quality_v249 as quality

SCORE = 'decision-mask-quality-score-v258'
COMPARISON = 'decision-mask-quality-comparison-v258'
SEMANTICS = 'native_boolean_decision_not_probability'
LABEL_INTERPRETATION = 'relative_to_declared_labels_not_verified_field_truth'
PROVENANCE_KEYS = (quality.PROVENANCE_KEYS - {'probability_sha256'}) | {'native_output_sha256'}
MAX_PIXELS = quality.MAX_PIXELS
MAX_POLICY_PARAMETERS = 64
ARRAY_DTYPES = {'decision_mask': 'bool', 'labels': 'uint8', 'valid_mask': 'bool', 'evaluated_mask': 'bool'}


def checked_provenance(value):
    quality.need(quality.exact(value, PROVENANCE_KEYS), 'native decision provenance fields are incomplete or unknown')
    for key, item in value.items():
        quality.need(quality.sha(item) if key.endswith('_sha256') or key == 'manifest_hash' else quality.text(item),
                     'invalid source identity or SHA256')
    quality.need(value['split'] in quality.SPLITS, 'split must be explicit')
    return copy.deepcopy(value)


def checked_policy(value):
    """Exact rule plus a finite, flat JSON parameter mapping and its own hash."""
    quality.need(quality.exact(value, {'rule_id', 'parameters', 'policy_hash'})
                 and quality.text(value['rule_id']) and quality.sha(value['policy_hash']),
                 'an explicit closed native decision policy is required')
    parameters = value['parameters']
    quality.need(type(parameters) is dict and len(parameters) <= MAX_POLICY_PARAMETERS,
                 'decision parameters must be a bounded flat primitive mapping')
    for key, item in parameters.items():
        quality.need(quality.text(key), 'decision parameter keys must be nonempty text')
        quality.need(item is None or type(item) in {bool, int, float, str},
                     'decision parameters must be JSON primitives, never arrays or nested objects')
        if type(item) is float:
            quality.need(math.isfinite(item), 'decision parameters must be finite')
        elif type(item) is str:
            quality.need(len(item) <= 500 and '\0' not in item, 'decision parameter text is invalid')
            item.encode('utf-8')
    quality.need(value['policy_hash'] == quality.digest({key: value[key] for key in ('rule_id', 'parameters')}),
                 'decision policy hash differs from its exact rule and parameters')
    return copy.deepcopy(value)


def _coverage_identity(source, mapping, shape, hashes):
    stable_source = {key: item for key, item in source.items()
                     if key not in {'model_sha256', 'processor_sha256', 'native_output_sha256'}}
    return {'source': stable_source, 'mapping': mapping, 'shape': shape,
            'labels_array_sha256': hashes['labels'], 'valid_mask_array_sha256': hashes['valid_mask'],
            'evaluated_mask_array_sha256': hashes['evaluated_mask']}


def _checked_counts(counts, shape):
    quality.need(quality.exact(counts, quality.COUNTS) and all(type(n) is int and n >= 0 for n in counts.values()),
                 'decision counts must be nonnegative JSON integers')
    quality.need(counts['total_pixels'] == shape[0] * shape[1]
                 == counts['source_valid_pixels'] + counts['unknown_pixels']
                 == counts['evaluated_pixels'] + counts['excluded_pixels']
                 and counts['excluded_pixels'] == counts['unknown_pixels'] + counts['ignored_label_pixels']
                 and counts['evaluated_pixels'] == sum(counts[key] for key in ('tp', 'fp', 'tn', 'fn'))
                 and counts['actual_positive_pixels'] == counts['tp'] + counts['fn']
                 and counts['predicted_positive_pixels'] == counts['tp'] + counts['fp'],
                 'decision counts or pixel coverage are inconsistent')


def score_decision_mask(mask, labels, valid_mask, *, label_mapping, provenance, decision_policy):
    """Score a real bool[H,W] decision without a float/uint8 mask conversion.

    Labels are uint8 and source-valid coverage is bool. Unknown and ignored
    labels have disjoint counters. Decisions outside evaluated coverage do not
    affect counts, but remain in the complete decision-array hash. The caller's
    native_output_sha256 is a file/producer pin, not this decoded-array hash.
    """
    import numpy as np
    mapping = quality.checked_mapping(label_mapping)
    source = checked_provenance(provenance); policy = checked_policy(decision_policy)
    for value in (mask, labels, valid_mask):
        quality.need(isinstance(value, np.ndarray) and not np.ma.isMaskedArray(value)
                     and value.ndim == 2 and value.shape == mask.shape,
                     'aligned plain two-dimensional arrays are required')
    quality.need(mask.dtype == np.dtype('bool') and valid_mask.dtype == np.dtype('bool')
                 and labels.dtype == np.dtype('uint8'),
                 'native decisions and valid coverage must be bool; labels must be uint8 without implicit casts')
    height, width = mask.shape
    quality.need(height > 0 and width > 0 and height * width <= MAX_PIXELS, 'pixel count is empty or over budget')
    positive = np.isin(labels, mapping['positive']); negative = np.isin(labels, mapping['negative'])
    ignored = np.isin(labels, mapping['ignore'])
    quality.need(bool((positive | negative | ignored).all()), 'ground truth contains an unmapped code')
    evaluated = valid_mask & ~ignored
    tp = int((evaluated & positive & mask).sum()); fp = int((evaluated & negative & mask).sum())
    tn = int((evaluated & negative & ~mask).sum()); fn = int((evaluated & positive & ~mask).sum())
    unknown = int((~valid_mask).sum()); ignored_count = int((valid_mask & ignored).sum())
    counts = {'total_pixels': height * width, 'source_valid_pixels': int(valid_mask.sum()),
              'unknown_pixels': unknown, 'ignored_label_pixels': ignored_count,
              'excluded_pixels': unknown + ignored_count, 'evaluated_pixels': tp + fp + tn + fn,
              'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn, 'actual_positive_pixels': tp + fn,
              'predicted_positive_pixels': tp + fp}
    hashes = {'decision_mask': quality.array_sha256(mask), 'labels': quality.array_sha256(labels),
              'valid_mask': quality.array_sha256(valid_mask), 'evaluated_mask': quality.array_sha256(evaluated)}
    shape = [height, width]
    value = {'schema': SCORE, 'status': 'scored' if counts['evaluated_pixels'] else 'no_evaluable_pixels',
             'provenance': source, 'decision_semantics': SEMANTICS, 'decision_policy': policy,
             'label_mapping': mapping, 'label_interpretation': LABEL_INTERPRETATION, 'shape': shape,
             'counts': counts, 'metrics': quality.metrics(counts), 'array_hashes': hashes,
             'array_dtypes': copy.deepcopy(ARRAY_DTYPES),
             'coverage_hash': quality.digest(_coverage_identity(source, mapping, shape, hashes)),
             'quality_evidence_only': True, 'release_authorized': False, 'field_truth_verified': False,
             'inference_label_isolation': 'must_be_verified_from_producer_receipts'}
    return {**value, 'score_hash': quality.digest(value)}


def checked_score(value):
    """Cold-check closed evidence; no array library or source IO is required."""
    keys = {'schema', 'status', 'provenance', 'decision_semantics', 'decision_policy', 'label_mapping',
            'label_interpretation', 'shape', 'counts', 'metrics', 'array_hashes', 'array_dtypes',
            'coverage_hash', 'quality_evidence_only', 'release_authorized', 'field_truth_verified',
            'inference_label_isolation', 'score_hash'}
    quality.need(quality.exact(value, keys) and value['schema'] == SCORE and quality.sha(value['score_hash'])
                 and value['score_hash'] == quality.digest({key: item for key, item in value.items() if key != 'score_hash'}),
                 'native decision evidence hash or fields are invalid')
    source = checked_provenance(value['provenance']); mapping = quality.checked_mapping(value['label_mapping'])
    checked_policy(value['decision_policy'])
    shape = value['shape']
    quality.need(type(shape) is list and len(shape) == 2 and all(type(n) is int and n > 0 for n in shape)
                 and shape[0] * shape[1] <= MAX_PIXELS, 'decision shape is invalid or over budget')
    counts = value['counts']; hashes = value['array_hashes']; _checked_counts(counts, shape)
    quality.need(quality.exact(hashes, ARRAY_DTYPES) and all(quality.sha(item) for item in hashes.values())
                 and quality.digest(value['array_dtypes']) == quality.digest(ARRAY_DTYPES)
                 and quality.exact(value['metrics'], quality.METRICS)
                 and quality.digest(value['metrics']) == quality.digest(quality.metrics(counts))
                 and value['coverage_hash'] == quality.digest(_coverage_identity(source, mapping, shape, hashes))
                 and value['status'] == ('scored' if counts['evaluated_pixels'] else 'no_evaluable_pixels')
                 and value['decision_semantics'] == SEMANTICS and value['label_interpretation'] == LABEL_INTERPRETATION
                 and value['quality_evidence_only'] is True and value['release_authorized'] is False
                 and value['field_truth_verified'] is False
                 and value['inference_label_isolation'] == 'must_be_verified_from_producer_receipts',
                 'native decision evidence semantics, metrics or coverage are inconsistent')
    return copy.deepcopy(value)


def compare_decision_scores(baseline, candidate):
    """Compare only this family, on identical GT/grid/valid/source coverage."""
    baseline, candidate = checked_score(baseline), checked_score(candidate)
    quality.need(baseline['coverage_hash'] == candidate['coverage_hash'],
                 'different source, split, labels, grid or evaluated coverage cannot be compared')
    quality.need(baseline['counts']['evaluated_pixels'] > 0, 'no evaluable coverage, no quality comparison')
    differences = {}
    for key, left, right in (
        ('model_sha256', baseline['provenance']['model_sha256'], candidate['provenance']['model_sha256']),
        ('processor_sha256', baseline['provenance']['processor_sha256'], candidate['provenance']['processor_sha256']),
        ('native_output_sha256', baseline['provenance']['native_output_sha256'], candidate['provenance']['native_output_sha256']),
        ('decision_mask_array_sha256', baseline['array_hashes']['decision_mask'], candidate['array_hashes']['decision_mask']),
        ('decision_policy', baseline['decision_policy'], candidate['decision_policy']),
    ):
        differences[key] = {'baseline': copy.deepcopy(left), 'candidate': copy.deepcopy(right),
                            'changed': quality.digest(left) != quality.digest(right)}
    deltas = {key: (candidate['metrics'][key] - baseline['metrics'][key]
                   if candidate['metrics'][key] is not None and baseline['metrics'][key] is not None else None)
              for key in sorted(quality.METRICS)}
    value = {'schema': COMPARISON, 'baseline_score_hash': baseline['score_hash'],
             'candidate_score_hash': candidate['score_hash'], 'coverage_hash': baseline['coverage_hash'],
             'differences': differences, 'metric_deltas': deltas, 'decision_semantics': SEMANTICS,
             'label_interpretation': LABEL_INTERPRETATION, 'quality_evidence_only': True,
             'release_authorized': False, 'field_truth_verified': False,
             'statistical_significance': 'not_tested', 'domain_generalization': 'not_established'}
    return {**value, 'comparison_hash': quality.digest(value)}


def checked_comparison(value, baseline, candidate):
    expected = compare_decision_scores(baseline, candidate)
    quality.need(quality.exact(value, set(expected)) and quality.digest(value) == quality.digest(expected),
                 'comparison does not belong to these exact native decision scores')
    return copy.deepcopy(value)
