"""Offline exact-output validation and binary PNG derivation, in native runtime."""
import argparse
import hashlib
import json
from pathlib import Path


def validate(root, expected):
    import numpy as np
    from PIL import Image
    root = Path(root)
    height, width = expected['height'], expected['width']
    value = np.load(root / 'probability.npy', allow_pickle=False)
    if (value.dtype != np.float32 or value.shape != (height, width)
            or not bool(np.isfinite(value).all()) or bool(((value < 0) | (value > 1)).any())):
        raise ValueError('本地变化概率文件不完整或超出范围')
    threshold = float(expected['threshold_text'])
    candidate = value >= threshold
    quantized = np.rint(value * 65534 + 1).astype('uint16')
    overlay = np.zeros((height, width, 4), dtype='uint8')
    overlay[candidate] = [240, 100, 39, 190]
    for name, array in [('probability.png', quantized), ('candidate.png', overlay)]:
        with Image.open(root / name) as image:
            if image.format != 'PNG' or not np.array_equal(np.array(image), array):
                raise ValueError('变化预览与原始概率不一致')
    if int(candidate.sum()) != expected['candidate_pixels']:
        raise ValueError('变化像素数量与原始概率不一致')
    with Image.open(root / 'valid.png') as image:
        if image.mode != 'L' or image.size != (width, height) or not bool((np.array(image) == 255).all()):
            raise ValueError('完整图像存在范围与原确认不一致')
    Image.fromarray(candidate.astype('uint8') * 255).save(root / 'binary-mask.png')
    return {name: {'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest(),
                   'size_bytes': (root / name).stat().st_size}
            for name in ('valid.png', 'probability.npy', 'probability.png', 'candidate.png', 'binary-mask.png')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--expected', required=True)
    args = parser.parse_args()
    expected = json.loads(Path(args.expected).read_text())
    print(json.dumps(validate(args.root, expected), allow_nan=False))


if __name__ == '__main__':
    main()
