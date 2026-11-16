"""Frozen, exploratory threshold diagnostics over already saved pixel evidence.

No model executes here. Labels have model-result exposure; neither arithmetic
nor a self-hash establishes independent ground truth, a DB freeze-before-run
transaction, permissions or adoption. The owning store must attest those facts.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from importlib.metadata import PackageNotFoundError, version
import io
import json
from pathlib import Path
import re
import struct
import warnings
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

MAX_PIXELS = 16_000_000
MAX_BYTES = MAX_PIXELS * 4 + 4096
MAX_HEADER = 4096
MANIFEST = 'pixel-diagnostic-manifest-v257'
RESULT = 'pixel-diagnostic-result-v257'
BINDING_KEYS = {'workspace_id', 'instance_id', 'run_id', 'run_version', 'generation', 'result_hash',
    'annotation_id', 'annotation_record_hash', 'annotation_actor_id', 'width', 'height',
    'before_sha256', 'after_sha256', 'labels_png_sha256', 'known_mask_png_sha256', 'valid_mask_png_sha256',
    'probability_npy_sha256', 'weights_sha256', 'processor_sha256', 'annotation_labels_sha256',
    'annotation_known_mask_sha256'}
UUID_KEYS = {'workspace_id', 'instance_id', 'run_id', 'annotation_id', 'annotation_actor_id'}
INTEGER_KEYS = {'run_version', 'generation', 'width', 'height'}
MAPPING = {'positive': [1], 'negative': [0], 'ignore': [255]}
MANIFEST_KEYS = {'schema', 'binding', 'baseline_threshold_text', 'candidate_threshold_text', 'decision_rule',
    'label_mapping', 'split', 'annotation_exposure', 'annotation_quality', 'purpose',
    'release_authorized', 'field_truth_verified', 'evaluator', 'manifest_hash'}
RESULT_KEYS = {'schema', 'manifest_hash', 'baseline_score', 'candidate_score', 'comparison',
    'release_authorized', 'field_truth_verified', 'annotation_exposure', 'result_hash'}


def _need(ok, message='Frozen pixel diagnostic evidence does not match its contract'):
    if not ok: raise ValueError(message)


def _text(value):
    _need(type(value) is str and '\x00' not in value)
    try: value.encode('utf-8')
    except UnicodeError: raise ValueError('Text is not valid Unicode') from None
    return value


def _sha(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _threshold(value):
    _need(type(value) is str and re.fullmatch(r'0\.[0-9]{1,6}', value) is not None
        and 0 < float(value) < 1, 'Threshold must be an original decimal text strictly between zero and one')
    return value


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Freeze(_Closed):
    schema: Literal['pixel-diagnostic-freeze-v257']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    annotation_record_hash: StrictStr = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    candidate_threshold_text: StrictStr

    @field_validator('request_id')
    @classmethod
    def request_text(cls, value): return _text(value)

    @field_validator('candidate_threshold_text')
    @classmethod
    def threshold_text(cls, value): return _threshold(value)


class Evaluate(_Closed):
    schema: Literal['pixel-diagnostic-evaluate-v257']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    study_record_hash: StrictStr = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')

    @field_validator('request_id')
    @classmethod
    def request_text(cls, value): return _text(value)


def _binding(value):
    from uuid import UUID
    _need(type(value) is dict and set(value) == BINDING_KEYS)
    for key in UUID_KEYS:
        item = value[key]
        _need(type(item) is str)
        try: _need(str(UUID(item)) == item)
        except (ValueError, AttributeError, TypeError): raise ValueError('Binding UUID is not canonical') from None
    for key in INTEGER_KEYS: _need(type(value[key]) is int and value[key] >= 1)
    _need(value['width'] * value['height'] <= MAX_PIXELS, 'Pixel budget exceeded')
    for key in BINDING_KEYS - UUID_KEYS - INTEGER_KEYS: _need(_sha(value[key]))
    return copy.deepcopy(value)


def _current_evaluator():
    try:
        return {'protocol_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'quality_core_sha256': hashlib.sha256(Path(__file__).with_name('pixel_change_quality_v249.py').read_bytes()).hexdigest(),
            'numpy_version': version('numpy'), 'pillow_version': version('Pillow')}
    except (PackageNotFoundError, OSError):
        raise ValueError('Diagnostic evaluator source or dependency is unavailable') from None


def frozen_manifest(binding, baseline_threshold_text, candidate_threshold_text):
    value = {'schema': MANIFEST, 'binding': _binding(binding),
        'baseline_threshold_text': _threshold(baseline_threshold_text),
        'candidate_threshold_text': _threshold(candidate_threshold_text),
        'decision_rule': 'probability_gte_threshold', 'label_mapping': copy.deepcopy(MAPPING),
        'split': 'exploratory', 'annotation_exposure': 'model_result_available',
        'annotation_quality': 'not_independently_verified', 'purpose': 'annotated_pixel_diagnostic',
        'release_authorized': False, 'field_truth_verified': False, 'evaluator': _current_evaluator()}
    return checked_manifest({**value, 'manifest_hash': _digest(value)})


def checked_manifest(value):
    _need(type(value) is dict and set(value) == MANIFEST_KEYS and value['schema'] == MANIFEST)
    _binding(value['binding']); _threshold(value['baseline_threshold_text']); _threshold(value['candidate_threshold_text'])
    _need(value['decision_rule'] == 'probability_gte_threshold' and value['split'] == 'exploratory'
        and value['annotation_exposure'] == 'model_result_available'
        and value['annotation_quality'] == 'not_independently_verified' and value['purpose'] == 'annotated_pixel_diagnostic'
        and value['release_authorized'] is False and value['field_truth_verified'] is False)
    mapping = value['label_mapping']
    _need(type(mapping) is dict and set(mapping) == set(MAPPING))
    for key, codes in MAPPING.items():
        _need(type(mapping[key]) is list and len(mapping[key]) == 1
            and type(mapping[key][0]) is int and mapping[key][0] == codes[0])
    evaluator = value['evaluator']
    _need(type(evaluator) is dict and set(evaluator) == {'protocol_sha256', 'quality_core_sha256', 'numpy_version', 'pillow_version'}
        and _sha(evaluator['protocol_sha256']) and _sha(evaluator['quality_core_sha256']))
    for key in ('numpy_version', 'pillow_version'):
        _text(evaluator[key]); _need(1 <= len(evaluator[key]) <= 100 and bool(evaluator[key].strip()))
    _need(_sha(value['manifest_hash']) and value['manifest_hash'] == _digest({k: v for k, v in value.items() if k != 'manifest_hash'}))
    # History is validated against its frozen fingerprint, not today's runtime.
    return copy.deepcopy(value)


def _blob(raw, expected):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'Evidence file is empty, mutable or over byte budget')
    _need(hashlib.sha256(raw).hexdigest() == expected, 'Evidence file SHA256 differs from the frozen binding')


def _npy_header(raw, width, height):
    _need(len(raw) >= 10 and raw[:6] == b'\x93NUMPY', 'Probability file is not an NPY array')
    fmt = tuple(raw[6:8])
    _need(fmt in {(1, 0), (2, 0), (3, 0)}, 'Unsupported NPY version')
    prefix = 10 if fmt == (1, 0) else 12
    _need(len(raw) >= prefix)
    header_size = struct.unpack('<H' if prefix == 10 else '<I', raw[8:prefix])[0]
    _need(0 < header_size <= MAX_HEADER and len(raw) >= prefix + header_size, 'NPY header exceeds its bounded contract')
    try:
        text = raw[prefix:prefix + header_size].decode('utf-8' if fmt == (3, 0) else 'latin1')
        node = ast.parse(text.strip(), mode='eval').body
        _need(isinstance(node, ast.Dict) and len(node.keys) == 3
            and all(isinstance(key, ast.Constant) and type(key.value) is str for key in node.keys)
            and len({key.value for key in node.keys}) == 3, 'NPY header fields must be unique')
        header = ast.literal_eval(node)
    except (ValueError, SyntaxError, UnicodeError, RecursionError):
        raise ValueError('NPY header is not a bounded literal array descriptor') from None
    _need(type(header) is dict and set(header) == {'descr', 'fortran_order', 'shape'})
    _need(type(header['descr']) is str and header['descr'] in {'<f4', '>f4', '=f4'}
        and type(header['fortran_order']) is bool, 'Only plain float32 probability arrays are supported')
    shape = header['shape']
    _need(type(shape) is tuple and len(shape) == 2 and all(type(n) is int and n > 0 for n in shape)
        and shape == (height, width) and shape[0] * shape[1] <= MAX_PIXELS, 'NPY shape differs from the frozen pixel grid')
    _need(len(raw) == prefix + header_size + width * height * 4, 'NPY payload size or trailing bytes differ from its declared grid')


def _png(raw, width, height, allowed):
    from PIL import Image, UnidentifiedImageError
    _need(len(raw) >= 33 and raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[8:16] == b'\x00\x00\x00\rIHDR', 'Mask must be a PNG')
    dims = struct.unpack('>IIBBBBB', raw[16:29])
    _need(dims[:2] == (width, height) and dims[2:6] == (8, 0, 0, 0) and dims[6] in {0, 1}, 'Mask must be original-size gray8 PNG')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                _need(image.format == 'PNG' and image.mode == 'L' and image.size == (width, height)); image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                _need(image.format == 'PNG' and image.mode == 'L' and image.size == (width, height)); image.load(); pixels = image.tobytes()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('PNG mask cannot be decoded and verified') from None
    _need(len(pixels) == width * height and not set(pixels) - allowed, 'Mask contains a value outside its explicit label mapping')
    return pixels


def _provenance(manifest):
    b = manifest['binding']
    return {'dataset_id': 'pixel-diagnostic-' + b['workspace_id'], 'dataset_revision': b['annotation_record_hash'],
        'domain_scope': 'annotated_pixel_diagnostic_not_independent_ground_truth',
        'source_id': 'PixelChangeRun:' + b['run_id'], 'scene_id': b['run_id'], 'split': 'exploratory',
        'derived_group_id': 'PixelChangeRun:' + b['run_id'], 'manifest_hash': manifest['manifest_hash'],
        'before_sha256': b['before_sha256'], 'after_sha256': b['after_sha256'], 'label_sha256': b['labels_png_sha256'],
        'valid_mask_sha256': b['valid_mask_png_sha256'], 'pixel_grid_sha256': _digest({'coordinate_space': 'pixel',
            'run_id': b['run_id'], 'width': b['width'], 'height': b['height']}), 'model_sha256': b['weights_sha256'],
        'processor_sha256': b['processor_sha256'], 'probability_sha256': b['probability_npy_sha256']}


def evaluate_frozen(manifest, probability_raw, labels_png, valid_png):
    manifest = checked_manifest(manifest)
    _need(_digest(manifest['evaluator']) == _digest(_current_evaluator()), 'Frozen evaluator differs from the current evaluator; history remains readable')
    binding = manifest['binding']; width, height = binding['width'], binding['height']
    for raw, expected in [(probability_raw, binding['probability_npy_sha256']),
            (labels_png, binding['labels_png_sha256']), (valid_png, binding['valid_mask_png_sha256'])]: _blob(raw, expected)
    _npy_header(probability_raw, width, height)  # Before np.load can allocate a claimed shape.
    labels_raw = _png(labels_png, width, height, {0, 1, 255}); valid_raw = _png(valid_png, width, height, {0, 255})
    _need(hashlib.sha256(labels_raw).hexdigest() == binding['annotation_labels_sha256'])
    known_raw = bytes(1 if value in {0, 1} else 0 for value in labels_raw)
    _need(hashlib.sha256(known_raw).hexdigest() == binding['annotation_known_mask_sha256'])
    _need(all(present != 0 or label == 255 for label, present in zip(labels_raw, valid_raw)), 'Source-invalid pixels must remain unknown')
    import numpy as np
    from PIL import __version__ as pillow_version
    from . import pixel_change_quality_v249 as core
    _need(np.__version__ == manifest['evaluator']['numpy_version'] and pillow_version == manifest['evaluator']['pillow_version'])
    try: probability = np.load(io.BytesIO(probability_raw), allow_pickle=False, max_header_size=MAX_HEADER)
    except (ValueError, OSError, EOFError): raise ValueError('Probability array could not be read safely') from None
    _need(isinstance(probability, np.ndarray) and probability.dtype.kind == 'f' and probability.dtype.itemsize == 4
        and probability.shape == (height, width))
    labels = np.frombuffer(labels_raw, dtype=np.uint8).reshape(height, width)
    valid = np.frombuffer(valid_raw, dtype=np.uint8).reshape(height, width) == 255
    scores = [core.score_pixels(probability, labels, valid, threshold=float(manifest[key]),
        label_mapping=manifest['label_mapping'], provenance=_provenance(manifest))
        for key in ('baseline_threshold_text', 'candidate_threshold_text')]
    comparison = core.compare_scores(*scores) if scores[0]['counts']['evaluated_pixels'] else None
    value = {'schema': RESULT, 'manifest_hash': manifest['manifest_hash'], 'baseline_score': scores[0],
        'candidate_score': scores[1], 'comparison': comparison, 'release_authorized': False,
        'field_truth_verified': False, 'annotation_exposure': 'model_result_available'}
    return checked_result(manifest, {**value, 'result_hash': _digest(value)})


def checked_result(manifest, value):
    from . import pixel_change_quality_v249 as core
    manifest = checked_manifest(manifest)
    _need(type(value) is dict and set(value) == RESULT_KEYS and value['schema'] == RESULT
        and value['manifest_hash'] == manifest['manifest_hash'] and value['release_authorized'] is False
        and value['field_truth_verified'] is False and value['annotation_exposure'] == 'model_result_available')
    scores = [core.checked_score(value[key]) for key in ('baseline_score', 'candidate_score')]
    for score, key in zip(scores, ('baseline_threshold_text', 'candidate_threshold_text')):
        _need(_digest(score['provenance']) == _digest(_provenance(manifest))
            and score['shape'] == [manifest['binding']['height'], manifest['binding']['width']]
            and score['threshold'] == float(manifest[key]) and _digest(score['label_mapping']) == _digest(MAPPING))
    _need(_digest(scores[0]['array_hashes']) == _digest(scores[1]['array_hashes'])
        and scores[0]['coverage_hash'] == scores[1]['coverage_hash'])
    fixed_counts = ('total_pixels', 'source_valid_pixels', 'unknown_pixels', 'ignored_label_pixels',
        'excluded_pixels', 'evaluated_pixels', 'actual_positive_pixels')
    _need(all(scores[0]['counts'][key] == scores[1]['counts'][key] for key in fixed_counts),
        'Both thresholds must score the same annotated coverage')
    if scores[0]['counts']['evaluated_pixels']:
        _need(_digest(value['comparison']) == _digest(core.compare_scores(*scores)))
    else:
        _need(scores[1]['status'] == 'no_evaluable_pixels' and value['comparison'] is None)
    _need(_sha(value['result_hash']) and value['result_hash'] == _digest({k: v for k, v in value.items() if k != 'result_hash'}))
    return copy.deepcopy(value)
