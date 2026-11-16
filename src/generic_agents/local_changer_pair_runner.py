"""Offline, bounded Changer inference on explicitly aligned RGB evidence.

Only the pinned tensor-only checkpoint is accepted. This runner never downloads
weights or imports Open-CD factories. Outputs are candidates, not object facts.
"""
import argparse
import hashlib
import json
import math
import os
import tempfile
import time
import warnings
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def starts(length, tile, stride):
    if length <= tile:
        return [0]
    result = list(range(0, length - tile + 1, stride))
    if result[-1] != length - tile:
        result.append(length - tile)
    return result


def tiled_probabilities(model, before, after, valid, recipe, infer, guard=lambda: None):
    import numpy as np
    height, width = valid.shape
    if before.shape != (height, width, 3) or after.shape != before.shape:
        raise ValueError('aligned RGB and common-valid mask dimensions do not match')
    if before.dtype != np.uint8 or after.dtype != np.uint8 or valid.dtype != np.bool_:
        raise ValueError('aligned inputs must be RGB bytes and a binary validity mask')
    if (min(height, width) < 32 or height * width > recipe['max_comparison_pixels']
            or not bool(valid.any())):
        raise ValueError('a bounded region of at least 32 pixels per side with common-valid coverage is required')
    tile = recipe['tile_pixels']
    rows = starts(height, tile, recipe['stride_pixels'])
    columns = starts(width, tile, recipe['stride_pixels'])
    if len(rows) * len(columns) > recipe['max_tiles']:
        raise ValueError('local change tile budget exceeded; choose a smaller region, no downsampling applied')
    total = np.zeros((height, width), dtype=np.float32)
    count = np.zeros((height, width), dtype=np.uint16)
    tiles = 0
    for y in rows:
        for x in columns:
            guard()
            ys, xs = slice(y, min(y + tile, height)), slice(x, min(x + tile, width))
            local_valid = valid[ys, xs]
            if not bool(local_valid.any()):
                continue
            probability = infer(model, before[ys, xs], after[ys, xs])
            if (probability.shape != local_valid.shape or probability.dtype != np.float32
                    or not bool(np.isfinite(probability).all())
                    or bool(((probability < 0) | (probability > 1)).any())):
                raise ValueError('local change tile returned invalid probabilities')
            total[ys, xs] += probability
            count[ys, xs] += 1
            tiles += 1
    guard()
    if bool((valid & (count == 0)).any()):
        raise ValueError('not all common-valid pixels were processed; no no-change conclusion accepted')
    result = np.full((height, width), np.nan, dtype=np.float32)
    result[valid] = total[valid] / count[valid]
    return result, tiles


def png_input(path, mode, recipe):
    import numpy as np
    from PIL import Image
    path = Path(path)
    if not path.is_file() or path.stat().st_size > recipe['max_comparison_pixels'] * 4 + 1024 * 1024:
        raise ValueError('aligned PNG file missing or exceeds the local byte budget')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if (image.format != 'PNG' or image.mode != mode
                    or not 0 < image.width * image.height <= recipe['max_comparison_pixels']):
                raise ValueError('only bounded explicitly rendered RGB/validity PNG inputs are accepted')
            result = np.array(image)
    if mode == 'L' and not bool(((result == 0) | (result == 255)).all()):
        raise ValueError('common-valid mask must contain only 0 or 255')
    return result


def infer_pair(input_dir, output_dir, threshold, recipe, weight_path=None):
    import numpy as np
    from PIL import Image
    from safetensors.torch import load_file
    import torch
    from local_changer_model import ChangerExR18, probabilities
    if type(threshold) not in {int, float} or not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('change probability threshold must be strictly between zero and one')
    for name, expected in recipe['environment'].items():
        if version(name) != expected:
            raise ValueError('local Changer dependencies differ from the pinned recipe')
    output = Path(output_dir)
    if not output.is_dir() or any((output / name).exists() for name in ('probability.npy', 'probability.png', 'candidate.png')):
        raise ValueError('local output directory must be prepared without old result artifacts')
    inputs = {name: Path(input_dir) / (name + '.png') for name in ('before', 'after', 'valid')}
    hashes = {name: file_hash(path) for name, path in inputs.items()}
    before, after = [png_input(inputs[name], 'RGB', recipe) for name in ('before', 'after')]
    valid = png_input(inputs['valid'], 'L', recipe) > 0
    # Reject input budgets/coverage before loading the trained model.
    height, width = valid.shape
    if (min(height, width) < 32 or before.shape != (height, width, 3)
            or after.shape != before.shape or not bool(valid.any())):
        raise ValueError('aligned dimensions or common-valid coverage are insufficient')
    if len(starts(height, recipe['tile_pixels'], recipe['stride_pixels'])) * len(starts(width, recipe['tile_pixels'], recipe['stride_pixels'])) > recipe['max_tiles']:
        raise ValueError('local change tile budget exceeded; no automatic downsampling')
    weight = Path(weight_path) if weight_path is not None else ROOT / '.runtime/changer-candidate/changer-r18.safetensors'
    if (not weight.is_file() or weight.stat().st_size != recipe['weights']['bytes']
            or file_hash(weight) != recipe['weights']['sha256']):
        raise ValueError('fixed local tensor-only Changer checkpoint is missing or has changed')
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    model = ChangerExR18().eval()
    state = load_file(weight, device='cpu')
    model.load_state_dict(state, strict=True)
    started = time.monotonic()
    result, tiles = tiled_probabilities(model, before, after, valid, recipe, probabilities)
    elapsed = time.monotonic() - started
    if hashes != {name: file_hash(path) for name, path in inputs.items()}:
        raise ValueError('aligned source evidence changed during inference')
    candidate = valid & (result >= threshold)
    quantized = np.zeros(result.shape, dtype=np.uint16)
    quantized[valid] = np.rint(result[valid] * 65534 + 1).astype(np.uint16)
    overlay = np.zeros((height, width, 4), dtype=np.uint8)
    overlay[candidate] = [240, 100, 39, 190]
    with tempfile.TemporaryDirectory(prefix='changer-write-', dir=output) as temporary:
        tmp = Path(temporary)
        np.save(tmp / 'probability.npy', result, allow_pickle=False)
        Image.fromarray(quantized).save(tmp / 'probability.png')
        Image.fromarray(overlay).save(tmp / 'candidate.png')
        for name in ('probability.npy', 'probability.png', 'candidate.png'):
            (tmp / name).chmod(0o600)
            os.replace(tmp / name, output / name)
    return {'schema': 'local-changer-comparison-v1', 'learned_model': True,
            'algorithm': recipe['algorithm'], 'backend': 'cpu', 'cloud_calls': 0,
            'width': width, 'height': height, 'total_pixels': width * height,
            'common_valid_pixels': int(valid.sum()), 'unknown_pixels': int((~valid).sum()),
            'candidate_pixels': int(candidate.sum()), 'probability_threshold': threshold,
            'probability_min': float(result[valid].min()), 'probability_max': float(result[valid].max()),
            'probability_png_encoding': '0=unknown; valid_probability=(value-1)/65534',
            'probability_npy_encoding': 'float32; NaN=unknown; no pickle',
            'tile_pixels': recipe['tile_pixels'], 'stride_pixels': recipe['stride_pixels'],
            'tile_merge': 'arithmetic_mean_in_overlap', 'tiles_inferred': tiles,
            'resizing': 'none', 'inference_seconds': elapsed, 'aligned_input_sha256': hashes,
            'weights_sha256': recipe['weights']['sha256'], 'graph_sha256': recipe['files']['local_changer_model.py'],
            'semantics': 'learned_binary_change_candidates_only', 'candidate_only': True,
            'domain_quality': 'not_verified', 'probability_calibration': 'not_verified',
            'tile_seam_quality': 'not_verified', 'nodata_neighbor_quality': 'not_verified',
            'cloud_shadow_screening': 'not_applied', 'object_state_updated': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--threshold', required=True, type=float)
    parser.add_argument('--weights')
    args = parser.parse_args()
    recipe = json.loads((ROOT / 'runtime-packs/geo-changer-candidate-v2.json').read_text())
    for name, expected in recipe['files'].items():
        if file_hash(Path(__file__).with_name(name)) != expected:
            raise ValueError('local Changer code differs from the frozen recipe')
    print(json.dumps(infer_pair(args.input, args.out, args.threshold, recipe, args.weights), allow_nan=False))


if __name__ == '__main__':
    main()
