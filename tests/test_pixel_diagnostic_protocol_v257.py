"""Pure exploratory protocol tests; no DB timing, model, Task or quality adoption proof."""
import copy
import hashlib
import io
import struct
from uuid import uuid4

import numpy as np
from PIL import Image
from pydantic import ValidationError
import pytest

from generic_agents import pixel_diagnostic_protocol_v257 as protocol
from generic_agents import pixel_change_quality_v249 as core


def sha(raw): return hashlib.sha256(raw).hexdigest()


def png(raw, width=2, height=2, mode='L'):
    out = io.BytesIO()
    Image.frombytes(mode, (width, height), raw).save(out, format='PNG')
    return out.getvalue()


def npy(value, *, allow_pickle=False):
    out = io.BytesIO(); np.save(out, value, allow_pickle=allow_pickle); return out.getvalue()


def scene(*, probability=None, probability_raw=None, labels_raw=None, valid_raw=None, labels_png=None, valid_png=None):
    probability = np.array([[.4, .6], [.2, .9]], dtype=np.float32) if probability is None else probability
    labels_raw = bytes([1, 0, 255, 0]) if labels_raw is None else labels_raw
    valid_raw = bytes([255]) * 4 if valid_raw is None else valid_raw
    probability_raw = npy(probability) if probability_raw is None else probability_raw
    labels_png = png(labels_raw) if labels_png is None else labels_png
    valid_png = png(valid_raw) if valid_png is None else valid_png
    known = bytes(1 if item in {0, 1} else 0 for item in labels_raw)
    binding = {'workspace_id': str(uuid4()), 'instance_id': str(uuid4()), 'run_id': str(uuid4()),
        'run_version': 3, 'generation': 1, 'result_hash': 'e' * 64,
        'annotation_id': str(uuid4()), 'annotation_record_hash': 'f' * 64, 'annotation_actor_id': str(uuid4()),
        'width': 2, 'height': 2, 'before_sha256': 'c' * 64, 'after_sha256': 'd' * 64,
        'labels_png_sha256': sha(labels_png), 'known_mask_png_sha256': sha(png(known)),
        'valid_mask_png_sha256': sha(valid_png), 'probability_npy_sha256': sha(probability_raw),
        'weights_sha256': 'a' * 64, 'processor_sha256': 'b' * 64,
        'annotation_labels_sha256': sha(labels_raw), 'annotation_known_mask_sha256': sha(known)}
    manifest = protocol.frozen_manifest(binding, '0.5', '0.25')
    return manifest, probability_raw, labels_png, valid_png


def seal_manifest(value):
    value['manifest_hash'] = protocol._digest({k: v for k, v in value.items() if k != 'manifest_hash'})
    return value


def seal_result(value):
    value['result_hash'] = protocol._digest({k: v for k, v in value.items() if k != 'result_hash'})
    return value


def test_freeze_and_evaluate_commands_are_closed_and_preserve_unicode_nonce_text():
    freeze = {'schema': 'pixel-diagnostic-freeze-v257', 'request_id': '诊断/原申请?保留-001',
        'annotation_record_hash': 'a' * 64, 'candidate_threshold_text': '0.250000'}
    evaluate = {'schema': 'pixel-diagnostic-evaluate-v257', 'request_id': freeze['request_id'], 'study_record_hash': 'b' * 64}
    assert protocol.Freeze.model_validate(freeze).model_dump(mode='json') == freeze
    assert protocol.Evaluate.model_validate(evaluate).model_dump(mode='json') == evaluate
    for cls, body in [(protocol.Freeze, freeze), (protocol.Evaluate, evaluate)]:
        with pytest.raises(ValidationError): cls.model_validate({**body, 'release_authorized': True})
        for bad in ['short', 'x' * 101, 'bad-NUL-\x00-identifier', 'bad-surrogate-\ud800-identifier', True]:
            with pytest.raises(ValidationError): cls.model_validate({**body, 'request_id': bad})


@pytest.mark.parametrize('bad', [True, .25, '0', '1', '0.0', '0.000000', '0.0000001', '.25', '0.25e0', ' 0.25', '0.25\n'])
def test_threshold_original_text_does_not_coerce_or_accept_nonclosed_spellings(bad):
    body = {'schema': 'pixel-diagnostic-freeze-v257', 'request_id': 'freeze-original-001',
        'annotation_record_hash': 'a' * 64, 'candidate_threshold_text': bad}
    with pytest.raises(ValidationError): protocol.Freeze.model_validate(body)


@pytest.mark.parametrize('key,value', [('run_version', True), ('generation', 1.0), ('width', True),
    ('height', '2'), ('width', 16000001), ('width', 0), ('run_id', 'not-a-uuid'),
    ('annotation_actor_id', 'ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF'), ('result_hash', 'A' * 64)])
def test_freeze_binding_is_strict_closed_original_grid_and_source_identity(key, value):
    manifest, *_ = scene(); binding = manifest['binding']; binding[key] = value
    with pytest.raises(ValueError): protocol.frozen_manifest(binding, '0.5', '0.25')


def test_manifest_rejects_extra_pins_and_false_boolean_or_label_mapping_coercion():
    manifest, *_ = scene()
    for mutate in [lambda m: m.update(extra=True), lambda m: m.update(release_authorized=0),
        lambda m: m.update(split='heldout'), lambda m: m['label_mapping'].update(positive=[True]),
        lambda m: m['binding'].update(extra='untrusted')]:
        changed = copy.deepcopy(manifest); mutate(changed); seal_manifest(changed)
        with pytest.raises(ValueError): protocol.checked_manifest(changed)


def test_real_float32_scores_reuse_the_existing_core_without_quality_or_release_claims():
    manifest, probability, labels, valid = scene(); original = copy.deepcopy(manifest)
    result = protocol.evaluate_frozen(manifest, probability, labels, valid)
    assert manifest == original and result['manifest_hash'] == manifest['manifest_hash']
    assert result['baseline_score']['counts']['tp'] == 0 and result['candidate_score']['counts']['tp'] == 1
    assert result['baseline_score']['counts']['evaluated_pixels'] == 3
    assert result['candidate_score']['counts']['ignored_label_pixels'] == 1
    assert result['baseline_score']['provenance']['split'] == 'exploratory'
    assert result['annotation_exposure'] == 'model_result_available'
    assert result['release_authorized'] is False and result['field_truth_verified'] is False
    assert result['comparison'] == core.compare_scores(result['baseline_score'], result['candidate_score'])
    assert protocol.checked_result(manifest, result) == result


def test_all_unknown_labels_have_no_evaluable_pixels_null_metrics_and_no_comparison():
    manifest, probability, labels, valid = scene(labels_raw=bytes([255]) * 4)
    result = protocol.evaluate_frozen(manifest, probability, labels, valid)
    for key in ['baseline_score', 'candidate_score']:
        assert result[key]['status'] == 'no_evaluable_pixels'
        assert result[key]['counts']['evaluated_pixels'] == 0 and all(value is None for value in result[key]['metrics'].values())
    assert result['comparison'] is None and protocol.checked_result(manifest, result) == result


@pytest.mark.parametrize('input_index', [1, 2, 3])
def test_input_sha_mismatch_is_rejected_before_numeric_use(input_index):
    values = list(scene()); values[input_index] += b'extra'
    with pytest.raises(ValueError, match='SHA256'): protocol.evaluate_frozen(*values)


@pytest.mark.parametrize('case', ['float64', 'object', 'wrong-shape', 'structured', 'trailing'])
def test_npy_dtype_shape_and_payload_contract_rejects_before_numpy_load(monkeypatch, case):
    if case == 'float64': raw = npy(np.zeros((2, 2), dtype=np.float64))
    elif case == 'object': raw = npy(np.array([[None, None], [None, None]], dtype=object), allow_pickle=True)
    elif case == 'wrong-shape': raw = npy(np.zeros((3, 2), dtype=np.float32))
    elif case == 'structured': raw = npy(np.zeros((2, 2), dtype=[('value', '<f4')]))
    else: raw = npy(np.zeros((2, 2), dtype=np.float32)) + b'not-another-array'
    values = scene(probability_raw=raw)
    def forbidden(*args, **kwargs): raise AssertionError('unsafe NPY was loaded before its header check')
    monkeypatch.setattr(np, 'load', forbidden)
    with pytest.raises(ValueError): protocol.evaluate_frozen(*values)


@pytest.mark.parametrize('literal', ["{'descr':'<f4','fortran_order':False,'shape':(1000000000,1000000000)}",
    "{'descr':'<f4','fortran_order':False,'shape':(True,2)}",
    "{'descr':'<f4','descr':'<f4','fortran_order':False,'shape':(2,2)}",
    "{'descr':'<f4','fortran_order':0,'shape':(2,2)}", '__import__("os").getcwd()'])
def test_npy_malicious_or_ambiguous_literal_header_cannot_request_allocation(monkeypatch, literal):
    header = (literal + '\n').encode(); raw = b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header
    values = scene(probability_raw=raw)
    monkeypatch.setattr(np, 'load', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not load')))
    with pytest.raises(ValueError): protocol.evaluate_frozen(*values)


def test_npy_header_and_file_byte_budgets_are_checked_before_array_loading(monkeypatch):
    header = b' ' * 4097
    raw = b'\x93NUMPY\x02\x00' + struct.pack('<I', len(header)) + header
    values = scene(probability_raw=raw)
    monkeypatch.setattr(np, 'load', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not load')))
    with pytest.raises(ValueError, match='header'): protocol.evaluate_frozen(*values)
    with pytest.raises(ValueError, match='byte budget'): protocol._blob(bytes(protocol.MAX_BYTES + 1), 'a' * 64)


@pytest.mark.parametrize('change', ['rgb-label', 'bad-label', 'one-valid', 'wrong-size', 'invalid-marked'])
def test_png_grid_mode_mapping_and_invalid_source_pixels_are_not_silently_coerced(change):
    if change == 'rgb-label': values = scene(labels_png=png(bytes([1]) * 12, mode='RGB'))
    elif change == 'bad-label': values = scene(labels_raw=bytes([1, 0, 2, 0]))
    elif change == 'one-valid': values = scene(valid_raw=bytes([1]) * 4)
    elif change == 'wrong-size': values = scene(labels_png=png(bytes([1, 0]), 2, 1))
    else: values = scene(valid_raw=bytes([0, 255, 255, 255]))
    with pytest.raises(ValueError): protocol.evaluate_frozen(*values)


def test_decoded_annotation_raw_hashes_are_checked_not_only_png_file_hashes():
    manifest, probability, labels, valid = scene()
    for key in ['annotation_labels_sha256', 'annotation_known_mask_sha256']:
        changed = copy.deepcopy(manifest); changed['binding'][key] = '0' * 64; seal_manifest(changed)
        with pytest.raises(ValueError): protocol.evaluate_frozen(changed, probability, labels, valid)


@pytest.mark.parametrize('number,valid', [(float('nan'), True), (float('inf'), False), (-.1, False), (1.1, True)])
def test_nonfinite_and_range_rules_are_exactly_the_quality_core_rules(number, valid):
    array = np.array([[.4, .6], [number, .9]], dtype=np.float32)
    values = scene(probability=array, valid_raw=bytes([255, 255, 255 if valid else 0, 255]))
    with pytest.raises(ValueError): protocol.evaluate_frozen(*values)


def test_nan_is_allowed_only_at_original_invalid_unknown_pixels():
    array = np.array([[.4, .6], [float('nan'), .9]], dtype=np.float32)
    result = protocol.evaluate_frozen(*scene(probability=array, valid_raw=bytes([255, 255, 0, 255])))
    assert result['baseline_score']['counts']['unknown_pixels'] == 1


@pytest.mark.parametrize('case', ['provenance', 'coverage', 'threshold', 'metric', 'comparison'])
def test_a_self_rehashed_result_cannot_replace_frozen_source_coverage_or_arithmetic(case):
    manifest, raw, labels_png, valid_png = scene(); result = protocol.evaluate_frozen(manifest, raw, labels_png, valid_png)
    changed = copy.deepcopy(result)
    if case in {'provenance', 'coverage', 'threshold'}:
        probability = np.load(io.BytesIO(raw), allow_pickle=False)
        labels = np.array([[1, 0], [255, 0]], dtype=np.uint8)
        valid = np.ones((2, 2), dtype=bool); provenance = protocol._provenance(manifest); threshold = .25
        if case == 'provenance': provenance['source_id'] = 'PixelChangeRun:' + str(uuid4())
        elif case == 'coverage': labels[0, 0] = 255
        else: threshold = .75
        changed['candidate_score'] = core.score_pixels(probability, labels, valid, threshold=threshold,
            label_mapping=manifest['label_mapping'], provenance=provenance)
    elif case == 'metric':
        changed['candidate_score']['metrics']['accuracy'] = 1
        changed['candidate_score']['score_hash'] = core.digest({k: v for k, v in changed['candidate_score'].items() if k != 'score_hash'})
    else:
        changed['comparison']['metric_deltas']['f1'] = 1
        changed['comparison']['comparison_hash'] = core.digest({k: v for k, v in changed['comparison'].items() if k != 'comparison_hash'})
    seal_result(changed)
    with pytest.raises(ValueError): protocol.checked_result(manifest, changed)


def test_saved_manifest_and_result_remain_readable_after_evaluator_upgrade_but_execution_blocks(monkeypatch):
    values = scene(); result = protocol.evaluate_frozen(*values); manifest = values[0]
    changed = {**manifest['evaluator'], 'protocol_sha256': '0' * 64, 'numpy_version': '0.0.0-old'}
    monkeypatch.setattr(protocol, '_current_evaluator', lambda: changed)
    assert protocol.checked_manifest(manifest) == manifest and protocol.checked_result(manifest, result) == result
    with pytest.raises(ValueError, match='current evaluator'): protocol.evaluate_frozen(*values)


def test_freezing_does_not_load_probability_or_claim_a_database_commit(monkeypatch):
    values = scene(); binding = values[0]['binding']
    monkeypatch.setattr(np, 'load', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('freeze must not load array')))
    manifest = protocol.frozen_manifest(binding, '0.5', '0.25')
    assert manifest['evaluator'] == protocol._current_evaluator()
    assert manifest['annotation_quality'] == 'not_independently_verified'
    assert not any(key in manifest for key in ['job_id', 'committed', 'independent_holdout', 'improved_model'])


def test_same_declared_array_hashes_do_not_allow_inconsistent_evaluated_counts():
    manifest, raw, labels, valid = scene(); result = protocol.evaluate_frozen(manifest, raw, labels, valid)
    candidate = result['candidate_score']; counts = candidate['counts']
    counts.update(tp=1, fp=0, tn=1, fn=0, evaluated_pixels=2, excluded_pixels=2,
        ignored_label_pixels=2, actual_positive_pixels=1, predicted_positive_pixels=1)
    candidate['metrics'] = core.metrics(counts)
    candidate['score_hash'] = core.digest({k: v for k, v in candidate.items() if k != 'score_hash'})
    result['comparison'] = core.compare_scores(result['baseline_score'], candidate)
    seal_result(result)
    with pytest.raises(ValueError, match='same annotated coverage'): protocol.checked_result(manifest, result)


def test_main_api_history_and_commands_need_no_numpy_pillow_or_version_probe(monkeypatch):
    import builtins
    manifest, raw, labels, valid = scene(); result = protocol.evaluate_frozen(manifest, raw, labels, valid)
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'numpy' or name.startswith('numpy.') or name == 'PIL' or name.startswith('PIL.'):
            raise AssertionError('Main API historical validation must not load numeric/image dependencies')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(protocol, '_current_evaluator', lambda: (_ for _ in ()).throw(AssertionError('history must not probe versions')))
    assert protocol.checked_manifest(manifest) == manifest
    assert protocol.checked_result(manifest, result) == result
    assert protocol.Freeze.model_validate({'schema': 'pixel-diagnostic-freeze-v257', 'request_id': 'api-main-freeze-001',
        'annotation_record_hash': manifest['binding']['annotation_record_hash'], 'candidate_threshold_text': '0.25'})
    assert protocol.Evaluate.model_validate({'schema': 'pixel-diagnostic-evaluate-v257', 'request_id': 'api-main-eval-001',
        'study_record_hash': 'a' * 64})
