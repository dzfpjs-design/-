"""Bounded local PNG pair adapter; reuses, never edits, the frozen Changer runner."""
import hashlib
import io
import json
import math
import os
import tempfile
import warnings
from pathlib import Path
from PIL import Image
from .changer_pair_codec import RECIPE, ready, _run
from .contracts import digest
from .local_specialists import node_lock

FILES = {'valid_mask': ('valid.png', 'image/png'),
         'probability_data': ('probability.npy', 'application/octet-stream'),
         'probability_preview': ('probability.png', 'image/png'),
         'candidate_overlay': ('candidate.png', 'image/png'),
         'binary_mask': ('binary-mask.png', 'image/png')}
_CODE = ('pixel_change_codec_v249.py', 'pixel_change_output_runner_v249.py')
SOURCE_HASHES = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in _CODE}
RUNTIME_HASH = digest({'schema': 'pixel-change-runtime-v249', 'source_hashes': SOURCE_HASHES,
                       'changer_recipe_hash': digest(RECIPE), 'mask': 'user_confirmed_full_image_present',
                       'coordinate_space': 'pixel', 'cloud_shadow_screening': 'not_applied'})


def png_metadata(content):
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError('请选择不超过20MB的RGB PNG原图')
    # Pillow exposes some 16-bit RGB PNGs as RGB uint8. Reject their original
    # IHDR rather than silently losing source precision during model decoding.
    if (len(content) < 33 or content[:8] != b'\x89PNG\r\n\x1a\n'
            or content[8:16] != b'\x00\x00\x00\rIHDR' or content[24:26] != b'\x08\x02'):
        raise ValueError('请选择8位RGB PNG原图；灰度、透明或16位图片不能自动转换')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as image:
            if (image.format != 'PNG' or image.mode != 'RGB' or getattr(image, 'n_frames', 1) != 1
                    or min(image.size) < 32 or image.width * image.height > RECIPE['max_comparison_pixels']):
                raise ValueError('两张原图需要单帧RGB PNG，每边至少32像素且总像素不超过1600万')
            width, height = image.size
            image.verify()
    def windows(side):
        return 1 if side <= 512 else math.ceil((side - 512) / 448) + 1
    if windows(width) * windows(height) > RECIPE['max_tiles']:
        raise ValueError('影像超过本地分析分块上限；请选择更小的原图，不会自动缩放')
    return width, height


def verify_adapter():
    if any(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected
           for name, expected in SOURCE_HASHES.items()):
        raise ValueError('像素比较处理程序已变化；原请求保留，请核对后重新提交')


def validate_receipt(value, width, height, threshold, hashes):
    exact = {'schema': 'local-changer-comparison-v1', 'learned_model': True, 'backend': 'cpu',
             'cloud_calls': 0, 'width': width, 'height': height, 'total_pixels': width * height,
             'common_valid_pixels': width * height, 'unknown_pixels': 0, 'probability_threshold': threshold,
             'aligned_input_sha256': hashes, 'weights_sha256': RECIPE['weights']['sha256'],
             'graph_sha256': RECIPE['files']['local_changer_model.py'], 'candidate_only': True,
             'semantics': 'learned_binary_change_candidates_only', 'object_state_updated': False,
             'resizing': 'none', 'cloud_shadow_screening': 'not_applied'}
    if not isinstance(value, dict) or any(digest(value.get(k)) != digest(v) for k, v in exact.items()):
        raise ValueError('本地分析结果与原图、模型或处理范围不一致')
    if (type(value.get('candidate_pixels')) is not int or not 0 <= value['candidate_pixels'] <= width * height
            or type(value.get('tiles_inferred')) is not int or not 1 <= value['tiles_inferred'] <= RECIPE['max_tiles']
            or type(value.get('inference_seconds')) not in {int, float}
            or not math.isfinite(value['inference_seconds']) or value['inference_seconds'] < 0):
        raise ValueError('本地分析结果计数或耗时不完整')


def call(before, after, threshold_text, *, weights, guard=lambda: None):
    width, height = png_metadata(before)
    if png_metadata(after) != (width, height):
        raise ValueError('两张原图像素尺寸不同；不会自动配准或缩放')
    verify_adapter()
    python, runner = ready(weights)
    env = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               PYTORCH_ENABLE_MPS_FALLBACK='0', PROJ_NETWORK='OFF')
    threshold = float(threshold_text)
    with node_lock(), tempfile.TemporaryDirectory(prefix='ga-pixel-change-') as temporary:
        root = Path(temporary); inputs = root / 'inputs'; out = root / 'out'; inputs.mkdir(); out.mkdir()
        (inputs / 'before.png').write_bytes(before); (inputs / 'after.png').write_bytes(after)
        Image.new('L', (width, height), 255).save(inputs / 'valid.png')
        hashes = {k: hashlib.sha256((inputs / (k + '.png')).read_bytes()).hexdigest() for k in ('before', 'after', 'valid')}
        guard()
        result = _run([str(python), str(runner), '--input', str(inputs), '--out', str(out), '--threshold', threshold_text,
                       '--weights', str(weights)], root, env, guard, RECIPE['timeout_seconds'], 'model')
        validate_receipt(result, width, height, threshold, hashes)
        (out / 'valid.png').write_bytes((inputs / 'valid.png').read_bytes())
        expected = root / 'expected.json'
        expected.write_text(json.dumps({'width': width, 'height': height, 'threshold_text': threshold_text,
                                        'candidate_pixels': result['candidate_pixels']}, allow_nan=False))
        saved = _run([str(python), str(Path(__file__).with_name('pixel_change_output_runner_v249.py')),
                      '--root', str(out), '--expected', str(expected)], root, env, guard, 60, 'outputs')
        if type(saved) is not dict or set(saved) != {name for name, _ in FILES.values()}:
            raise ValueError('本地分析产物不完整')
        artifacts = {}
        for kind, (name, media) in FILES.items():
            path = out / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 70 * 1024 * 1024:
                raise ValueError('本地分析产物格式或大小不符合限制')
            raw = path.read_bytes()
            if digest(saved[name]) != digest({'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}):
                raise ValueError('本地分析产物保存核对失败')
            artifacts[kind] = (raw, media)
        if hashes != {k: hashlib.sha256((inputs / (k + '.png')).read_bytes()).hexdigest() for k in hashes}:
            raise ValueError('本地分析期间原图发生变化')
        verify_adapter(); guard()
        return result, artifacts
