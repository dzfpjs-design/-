"""Faithful, bounded CPU execution of the pinned torchange 0.0.4 AnyChange.

Only namespace packaging and explicit safe state loading surround the unchanged
wheel algorithm. This is not an import of the complete torchange distribution,
not SAM2, not a probability producer, and not a claim of domain effectiveness.
The caller owns prior source/weight licensing and the wall-time/memory sandbox.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
from importlib import metadata
from importlib.machinery import ModuleSpec
import io
import json
import math
import os
from pathlib import Path
import random
import resource
import stat
import struct
import sys
import tempfile
import time
import types

WHEEL_SHA = '4ce55dab4fdabec975c824e59e6f11ab9227df1a015e6adf08335e9bfe6a7846'
WEIGHTS = {'bytes': 375042383, 'sha256': 'ec2df62732614e57411cdcf32a23ffdf28910380d03139ee0f4fcbe91eb8c912'}
PARAMETERS = {'model_type': 'vit_b', 'change_confidence_threshold': 145,
    'points_per_side': 32, 'points_per_batch': 64, 'pred_iou_thresh': 0.5,
    'stability_score_thresh': 0.95, 'stability_score_offset': 1.0, 'box_nms_thresh': 0.7,
    'area_thresh': 0.8, 'use_normalized_feature': True, 'bitemporal_match': True,
    'auto_threshold': False, 'match_hist': False, 'min_mask_region_area': 0,
    'mask_encoding': 'uncompressed_rle'}
LIMITS = {'min_side': 32, 'max_pixels': 1200000, 'max_edge': 1536,
    'max_input_bytes': 20971520, 'max_native_objects_bytes': 67108864, 'max_plan_bytes': 131072}
SOURCE_FILES = {
    '__init__.py', 'anychange.py', 'base.py', 'simple_maskgen.py', 'viz.py',
    'segment_anything/__init__.py', 'segment_anything/automatic_mask_generator.py',
    'segment_anything/build_sam.py', 'segment_anything/predictor.py',
    'segment_anything/modeling/__init__.py', 'segment_anything/modeling/common.py',
    'segment_anything/modeling/image_encoder.py', 'segment_anything/modeling/mask_decoder.py',
    'segment_anything/modeling/prompt_encoder.py', 'segment_anything/modeling/sam.py',
    'segment_anything/modeling/transformer.py', 'segment_anything/utils/__init__.py',
    'segment_anything/utils/amg.py', 'segment_anything/utils/onnx.py', 'segment_anything/utils/transforms.py'}
VERSIONS = ('torch', 'torchvision', 'numpy', 'Pillow', 'scikit-image', 'scipy', 'safetensors', 'matplotlib')
MESSAGES = {'plan_invalid': '执行计划未通过固定契约核对',
    'source_invalid': '公开算法源码清单或原件不能核对',
    'weights_invalid': '固定模型权重或安全载入未通过核对',
    'input_invalid': '两张RGB原图或有效范围不满足原像素输入契约',
    'output_exists': '输出目录或固定产物已存在，未覆盖原件',
    'runtime_unavailable': '隔离CPU环境或原作者依赖不可用',
    'native_failed': '原作者CPU算法未完成，未使用替代算法',
    'native_invalid': '原作者输出未通过RLE、分数或像素范围核对',
    'output_failed': '原始输出未能完整发布，不能视为成功',
    'state_changed': '执行期间的输入、源码、权重或模型状态发生变化'}


class Failure(ValueError):
    def __init__(self, code):
        super().__init__(MESSAGES[code]); self.code = code


def need(ok, code):
    if not ok: raise Failure(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _path(value, code):
    path = Path(value).absolute()
    need(path.is_absolute() and '..' not in path.parts, code)
    return path


@contextmanager
def directory(value, code):
    """Open every directory component without following a symlink."""
    path = _path(value, code)
    fd = None
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        yield fd
    except OSError:
        raise Failure(code) from None
    finally:
        if fd is not None: os.close(fd)


@contextmanager
def regular(value, code, *, maximum=None, exact=None, minimum=1):
    path = _path(value, code)
    with directory(path.parent, code) as parent:
        fd = None
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_size >= minimum
                and (maximum is None or info.st_size <= maximum)
                and (exact is None or info.st_size == exact), code)
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                yield stream
        except OSError:
            raise Failure(code) from None
        finally:
            if fd is not None: os.close(fd)


def fingerprint(value, code, *, maximum=None, exact=None, minimum=1):
    with regular(value, code, maximum=maximum, exact=exact, minimum=minimum) as stream:
        before = os.fstat(stream.fileno()); checksum = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''): checksum.update(block)
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        need(identity(before) == identity(after), code)
        return {'sha256': checksum.hexdigest(), 'size_bytes': before.st_size, 'identity': identity(before)}


def read_bytes(value, code, *, maximum):
    with regular(value, code, maximum=maximum) as stream:
        raw = stream.read(maximum + 1)
        need(0 < len(raw) <= maximum, code)
        return raw


def parse_json(raw, code):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, code); result[key] = value
        return result
    def nonfinite(_): raise Failure(code)
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
        canonical(result)
        return result
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise Failure(code) from None


def checked_plan(raw):
    value = parse_json(raw, 'plan_invalid')
    need(type(value) is dict and set(value) == {'schema', 'distribution', 'version', 'wheel_sha256',
        'source_files', 'weights', 'native_parameters', 'limits', 'plan_hash'}, 'plan_invalid')
    need(value['schema'] == 'anychange-execution-plan-v258' and value['distribution'] == 'torchange'
        and value['version'] == '0.0.4' and value['wheel_sha256'] == WHEEL_SHA
        and canonical(value['weights']) == canonical(WEIGHTS)
        and canonical(value['native_parameters']) == canonical(PARAMETERS)
        and canonical(value['limits']) == canonical(LIMITS), 'plan_invalid')
    files = value['source_files']
    need(type(files) is dict and set(files) == SOURCE_FILES and all(sha(v) for v in files.values())
        and sha(value['plan_hash']) and value['plan_hash'] == digest({k: v for k, v in value.items() if k != 'plan_hash'}), 'plan_invalid')
    return value


def verify_source(root, expected):
    subtree = _path(root, 'source_invalid') / 'torchange/models/segment_any_change'
    with directory(subtree, 'source_invalid'): pass
    allowed_dirs = {'.'}
    for name in SOURCE_FILES:
        allowed_dirs.update(parent.as_posix() for parent in Path(name).parents)
    found = set()
    try:
        for parent, dirs, names in os.walk(subtree, followlinks=False):
            need(Path(parent).relative_to(subtree).as_posix() in allowed_dirs, 'source_invalid')
            for name in dirs:
                path = Path(parent) / name
                need(not path.is_symlink() and path.relative_to(subtree).as_posix() in allowed_dirs, 'source_invalid')
            for name in names:
                path = Path(parent) / name; relative = path.relative_to(subtree).as_posix()
                # Native extension modules and bytecode can shadow a verified
                # .py during import; the whole subtree, not just .py, is closed.
                need(not path.is_symlink() and relative in SOURCE_FILES, 'source_invalid')
                found.add(relative)
    except OSError:
        raise Failure('source_invalid') from None
    need(found == SOURCE_FILES, 'source_invalid')
    actual = {name: fingerprint(subtree / name, 'source_invalid', maximum=2 * 1024 * 1024, minimum=0)['sha256'] for name in sorted(found)}
    need(actual == expected, 'source_invalid')
    return subtree, actual


def checked_png_header(raw, kind):
    need(len(raw) >= 33 and raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[8:16] == b'\0\0\0\rIHDR', 'input_invalid')
    width, height = struct.unpack('>II', raw[16:24])
    need(raw[24:26] == (b'\x08\x02' if kind == 'rgb' else b'\x08\x00')
        and min(width, height) >= LIMITS['min_side'] and max(width, height) <= LIMITS['max_edge']
        and width * height <= LIMITS['max_pixels'], 'input_invalid')
    return width, height


def load_inputs(root):
    path = _path(root, 'input_invalid')
    with directory(path, 'input_invalid') as fd:
        need(set(os.listdir(fd)) == {'before.png', 'after.png', 'valid.png'}, 'input_invalid')
    raw = {name: read_bytes(path / (name + '.png'), 'input_invalid', maximum=LIMITS['max_input_bytes'])
        for name in ('before', 'after', 'valid')}
    shape = checked_png_header(raw['before'], 'rgb')
    need(checked_png_header(raw['after'], 'rgb') == shape
        and checked_png_header(raw['valid'], 'valid') == shape, 'input_invalid')
    fingerprints = {name: fingerprint(path / (name + '.png'), 'input_invalid', maximum=LIMITS['max_input_bytes']) for name in raw}
    need(all(hashlib.sha256(raw[name]).hexdigest() == entry['sha256'] for name, entry in fingerprints.items()), 'input_invalid')
    return path, raw, fingerprints, shape


def namespace_import(root):
    """Only two ancestor namespaces; execute the real author's child __init__."""
    need(not any(name == 'torchange' or name.startswith('torchange.') for name in sys.modules), 'runtime_unavailable')
    for name, suffix in [('torchange', 'torchange'), ('torchange.models', 'torchange/models')]:
        module = types.ModuleType(name); module.__package__ = name
        module.__path__ = [str(root / suffix)]
        module.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        module.__spec__.submodule_search_locations = module.__path__
        sys.modules[name] = module
    real = importlib.import_module('torchange.models.segment_any_change')
    amg = importlib.import_module('torchange.models.segment_any_change.segment_anything.utils.amg')
    return real.AnyChange, amg.rle_to_mask


def decode_pngs(raw, shape, np, Image):
    decoded = {}
    for name, content in raw.items():
        try:
            with Image.open(io.BytesIO(content)) as image:
                need(image.format == 'PNG' and image.mode == ('L' if name == 'valid' else 'RGB')
                    and getattr(image, 'n_frames', 1) == 1 and image.size == shape, 'input_invalid')
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                array = np.array(image)
            need(array.dtype == np.dtype('uint8') and array.shape == ((shape[1], shape[0]) if name == 'valid' else (shape[1], shape[0], 3)), 'input_invalid')
            if name == 'valid': need(bool(((array == 0) | (array == 255)).all()), 'input_invalid')
            decoded[name] = array
        except (ValueError, OSError, Image.DecompressionBombError):
            raise Failure('input_invalid') from None
    return decoded


def model_state_hash(model):
    checksum = hashlib.sha256()
    for category, items in [('parameter', model.named_parameters()), ('buffer', model.named_buffers())]:
        for name, value in items:
            checksum.update(canonical({'kind': category, 'name': name, 'shape': list(value.shape), 'dtype': str(value.dtype)}))
            checksum.update(value.detach().contiguous().cpu().numpy().tobytes())
    return checksum.hexdigest()


def load_model(AnyChange, weights, torch):
    model = AnyChange(model_type='vit_b', sam_checkpoint=None)
    need(model.device.type == 'cpu' and all(t.device.type == 'cpu' for t in model.sam.parameters()), 'runtime_unavailable')
    try:
        with regular(weights, 'weights_invalid', exact=WEIGHTS['bytes']) as stream:
            values = torch.load(stream, weights_only=True, map_location='cpu')
        need(isinstance(values, dict) and values and all(type(k) is str and isinstance(v, torch.Tensor)
            and v.device.type == 'cpu' for k, v in values.items()), 'weights_invalid')
        expected = model.sam.state_dict()
        need(set(values) == set(expected) and all(values[k].shape == expected[k].shape
            and values[k].dtype == expected[k].dtype and torch.isfinite(values[k]).all().item() for k in expected), 'weights_invalid')
        model.sam.load_state_dict(values, strict=True, assign=False)
        need(all(torch.equal(value, model.sam.state_dict()[name]) for name, value in values.items()), 'weights_invalid')
        del values, expected
    except Failure: raise
    except Exception: raise Failure('weights_invalid') from None
    model.sam.eval()
    need(not model.sam.training and all(not part.training for part in model.sam.modules())
        and all(t.device.type == 'cpu' and t.dtype == torch.float32
            for t in [*model.sam.parameters(), *model.sam.buffers()]), 'runtime_unavailable')
    # The author captures affine views before our safe state load. assign=False
    # must retain the real parameter storage, not a stale random affine copy.
    closure = dict(zip(model.inv_transform.__code__.co_freevars, [cell.cell_contents for cell in model.inv_transform.__closure__]))
    affine = model.sam.image_encoder.neck[3]
    need(set(closure) == {'w', 'b'} and closure['w'].data_ptr() == affine.weight.data_ptr()
        and closure['b'].data_ptr() == affine.bias.data_ptr(), 'weights_invalid')
    model.set_hyperparameters(change_confidence_threshold=145, auto_threshold=False,
        use_normalized_feature=True, area_thresh=0.8, match_hist=False,
        object_sim_thresh=60, bitemporal_match=True)
    model.make_mask_generator(points_per_side=32, points_per_batch=64, pred_iou_thresh=0.5,
        stability_score_thresh=0.95, stability_score_offset=1.0, box_nms_thresh=0.7, min_mask_region_area=0)
    return model


def checked_native(value, width, height, np, torch, rle_to_mask):
    need(type(value) is dict and set(value) == {'boxes', 'scores', 'labels', 'masks'}, 'native_invalid')
    boxes, scores, labels, masks = [value[k] for k in ('boxes', 'scores', 'labels', 'masks')]
    need(all(isinstance(v, torch.Tensor) and v.device.type == 'cpu' for v in (boxes, scores, labels))
        and boxes.dtype == torch.float32 and scores.dtype == torch.float32 and labels.dtype == torch.int64
        and boxes.ndim == 2 and boxes.shape[1] == 4 and scores.ndim == labels.ndim == 1
        and len(boxes) == len(scores) == len(labels) and type(masks) is list and len(masks) == len(scores)
        and len(scores) <= 1000 and bool(torch.isfinite(boxes).all()) and bool(torch.isfinite(scores).all())
        and bool(((scores >= -1) & (scores <= 1)).all()) and bool((labels == 1).all()), 'native_invalid')
    original_boxes = boxes.detach().numpy(); original_scores = scores.detach().numpy()
    need(original_scores.dtype == np.dtype('float32') and original_scores.shape == (len(masks),), 'native_invalid')
    union = np.zeros((height, width), dtype=np.bool_)
    for index, rle in enumerate(masks):
        need(type(rle) is dict and set(rle) == {'size', 'counts'} and type(rle['size']) is list
            and len(rle['size']) == 2 and all(type(n) is int for n in rle['size'])
            and rle['size'] == [height, width] and type(rle['counts']) is list
            and 1 <= len(rle['counts']) <= width * height + 1
            and all(type(n) is int and n >= 0 for n in rle['counts']) and sum(rle['counts']) == width * height, 'native_invalid')
        mask = rle_to_mask(rle)
        need(isinstance(mask, np.ndarray) and mask.dtype == np.dtype('bool') and mask.shape == union.shape, 'native_invalid')
        yy, xx = np.nonzero(mask)
        need(len(xx) > 0, 'native_invalid')
        native_box = original_boxes[index]
        need(bool(np.equal(native_box, np.floor(native_box)).all())
            and native_box.tolist() == [int(xx.min()), int(yy.min()), int(xx.max()), int(yy.max())], 'native_invalid')
        union |= mask
    objects = {'schema': 'anychange-native-objects-v258', 'box_semantics': 'inclusive_xyxy_original_pixel_indices',
        'score_semantics': 'negative_region_mean_feature_cosine_not_probability', 'mask_encoding': 'uncompressed_rle_column_major',
        'boxes': original_boxes.tolist(), 'scores': original_scores.tolist(), 'labels': labels.tolist(), 'masks': masks}
    return objects, union, original_scores


def new_output(path):
    out = _path(path, 'output_failed')
    with directory(out.parent, 'output_failed') as parent:
        try: os.mkdir(out.name, 0o700, dir_fd=parent)
        except FileExistsError: raise Failure('output_exists') from None
        except OSError: raise Failure('output_failed') from None
        os.fsync(parent)
    return out


def publish(fd, name, chunks, maximum):
    temporary = '.' + name + '.part'; output = None
    checksum = hashlib.sha256(); size = 0
    try:
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, 'wb') as stream:
            output = None
            for chunk in chunks:
                size += len(chunk); need(size <= maximum, 'output_failed')
                stream.write(chunk); checksum.update(chunk)
            stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=fd); os.fsync(fd)
        return {'filename': name, 'sha256': checksum.hexdigest(), 'size_bytes': size,
            'media_type': 'application/json' if name.endswith('.json') else 'application/octet-stream'}
    except FileExistsError: raise Failure('output_exists') from None
    except OSError: raise Failure('output_failed') from None
    finally:
        if output is not None: os.close(output)
        try: os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError: pass


def numpy_bytes(array, np):
    buffer = io.BytesIO(); np.save(buffer, array, allow_pickle=False); return buffer.getvalue()


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def run(args):
    started = time.monotonic(); sys.dont_write_bytecode = True
    script = fingerprint(__file__, 'source_invalid', maximum=1024 * 1024)
    plan_raw = read_bytes(args.plan, 'plan_invalid', maximum=LIMITS['max_plan_bytes']); plan = checked_plan(plan_raw)
    source_root = _path(args.source_root, 'source_invalid'); subtree, source_hashes = verify_source(source_root, plan['source_files'])
    weights = _path(args.weights, 'weights_invalid'); weight_pin = fingerprint(weights, 'weights_invalid', exact=WEIGHTS['bytes'])
    need(weight_pin['sha256'] == WEIGHTS['sha256'], 'weights_invalid')
    input_root, raw, input_pins, shape = load_inputs(args.input)
    # No author/dependency import occurred before the complete source and weight
    # checks. Do not let a dependency see credentials or produce plot caches at
    # a user's existing configuration path.
    retained = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    os.environ.clear(); os.environ.update(retained)
    os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', PYTORCH_ENABLE_MPS_FALLBACK='0', MPLBACKEND='Agg',
        OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2', PYTHONDONTWRITEBYTECODE='1')
    try: versions = {name: metadata.version(name) for name in VERSIONS}
    except metadata.PackageNotFoundError: raise Failure('runtime_unavailable') from None
    with tempfile.TemporaryDirectory(prefix='ga-anychange-mpl-') as plots:
        os.environ['MPLCONFIGDIR'] = plots
        try:
            import numpy as np
            import torch
            from PIL import Image
            torch.set_num_threads(2); torch.set_num_interop_threads(1)
            random.seed(0); np.random.seed(0); torch.manual_seed(0)
            need(not torch.cuda.is_available(), 'runtime_unavailable')
            AnyChange, rle_to_mask = namespace_import(source_root)
        except Failure: raise
        except Exception: raise Failure('runtime_unavailable') from None
        decoded = decode_pngs(raw, shape, np, Image)
        before_inputs = {name: hashlib.sha256(array.tobytes()).hexdigest() for name, array in decoded.items()}
        out = new_output(args.out)
        model = load_model(AnyChange, weights, torch); before_state = model_state_hash(model.sam)
        inference_started = time.monotonic()
        try:
            with torch.inference_mode():
                native = model(decoded['before'], decoded['after'])
        except Exception:
            raise Failure('native_failed') from None
        finally:
            model.clear_cached_embedding()
        inference_elapsed = time.monotonic() - inference_started
        need(model.embed_data1 is None and model.embed_data2 is None
            and model.maskgen.predictor.input_size is None and model.maskgen.predictor.original_size is None, 'state_changed')
        objects, union, scores = checked_native(native, shape[0], shape[1], np, torch, rle_to_mask)
        valid = decoded['valid'] == 255; decision = union & valid
        need(all(before_inputs[name] == hashlib.sha256(array.tobytes()).hexdigest() for name, array in decoded.items())
            and before_state == model_state_hash(model.sam), 'state_changed')
        need(script == fingerprint(__file__, 'source_invalid', maximum=1024 * 1024)
            and weight_pin == fingerprint(weights, 'weights_invalid', exact=WEIGHTS['bytes'])
            and all(pin == fingerprint(input_root / (name + '.png'), 'input_invalid', maximum=LIMITS['max_input_bytes']) for name, pin in input_pins.items()), 'state_changed')
        _, after_source = verify_source(source_root, plan['source_files']); need(after_source == source_hashes, 'state_changed')
        with directory(out, 'output_failed') as fd:
            encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            artifacts = {'native_objects': publish(fd, 'native_objects.json', (v.encode('utf-8') for v in encoder.iterencode(objects)), LIMITS['max_native_objects_bytes'])}
            for name, array in [('native_union', union), ('decision_mask', decision), ('raw_scores', scores)]:
                artifacts[name] = publish(fd, name + '.npy', [numpy_bytes(array, np)], 8 * LIMITS['max_pixels'] + 4096)
            # Verify published bytes before advertising them as saved artifacts.
            for ref in artifacts.values():
                actual = fingerprint(out / ref['filename'], 'output_failed', maximum=LIMITS['max_native_objects_bytes'])
                need(actual['sha256'] == ref['sha256'] and actual['size_bytes'] == ref['size_bytes'], 'output_failed')
            receipt = {'schema': 'anychange-native-result-v258',
                'source': {'distribution': 'torchange', 'version': '0.0.4', 'wheel_sha256': WHEEL_SHA,
                    'source_files': source_hashes, 'source_tree_hash': digest(source_hashes),
                    'wheel_verification': 'caller_verified_wheel_runner_verified_extracted_python_tree'},
                'weights': {**WEIGHTS, 'tensor_count': len(model.sam.state_dict())},
                'processor_sha256': script['sha256'], 'plan_hash': plan['plan_hash'], 'plan_sha256': hashlib.sha256(plan_raw).hexdigest(),
                'input_sha256': {name: pin['sha256'] for name, pin in input_pins.items()},
                'width': shape[0], 'height': shape[1],
                'counts': {'total_pixels': shape[0] * shape[1], 'source_valid_pixels': int(valid.sum()),
                    'source_invalid_pixels': int((~valid).sum()), 'native_objects': len(scores),
                    'native_union_pixels': int(union.sum()), 'decision_pixels': int(decision.sum())},
                'native_parameters': PARAMETERS, 'outputs': artifacts,
                'runtime': {'backend': 'cpu', 'torch_threads': 2, 'interop_threads': 1, 'seed': 0,
                    'dependency_versions': versions, 'complete_torchange_package_imported': False,
                    'ancestor_namespace_wrapper': True, 'author_child_init_executed': True,
                    'safe_state_loading': 'weights_only_cpu_strict_assign_false'},
                'execution': {'elapsed_seconds': time.monotonic() - started, 'native_elapsed_seconds': inference_elapsed,
                    'peak_rss_bytes': peak_rss(), 'peak_rss_scope': 'process_including_import_and_model_load'},
                'integrity': {'inputs_unchanged': True, 'source_files_unchanged': True, 'weights_unchanged': True,
                    'model_state_unchanged': True, 'pair_embedding_cache_cleared': True,
                    'cache_clear_method': 'author_clear_cached_embedding_in_finally', 'model_state_sha256': before_state},
                'decision_semantics': 'native_rle_union_and_source_valid_not_probability',
                'score_semantics': 'negative_region_mean_feature_cosine_not_probability',
                'threshold_rule': 'strict_score_gt_cos_angle_degrees', 'native_boxes': 'inclusive_xyxy_original_pixel_indices',
                'coordinate_space': 'pixel', 'georeference_available': False, 'source_clock_utc': 'unknown',
                'cloud_calls': 0, 'object_state_updated': False, 'candidate_only': True,
                'domain_quality_verified': False, 'release_authorized': False,
                'known_limits': {'limits': LIMITS, 'memory_and_wall_time_enforced_by_parent': True,
                    'no_pair_coregistration': True, 'no_dense_change_probability': True,
                    'wheel_not_proven_equal_to_github_commit': True, 'full_original_distribution_imported': False,
                    'capacity_or_field_quality_not_established': True}}
            receipt['receipt_hash'] = digest(receipt)
            publish(fd, 'result.json', [canonical(receipt)], LIMITS['max_plan_bytes'])
        return receipt


def main():
    parser = argparse.ArgumentParser(description='Pinned AnyChange CPU native decisions, never probabilities')
    parser.add_argument('--input', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--source-root', required=True); parser.add_argument('--weights', required=True)
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    try:
        result = run(args)
        sys.stdout.buffer.write(canonical(result) + b'\n'); sys.stdout.buffer.flush(); return 0
    except Failure as error:
        failure = {'schema': 'anychange-native-failure-v258', 'error_code': error.code,
            'message': MESSAGES[error.code], 'candidate_only': True, 'release_authorized': False}
    except Exception:
        failure = {'schema': 'anychange-native-failure-v258', 'error_code': 'native_failed',
            'message': MESSAGES['native_failed'], 'candidate_only': True, 'release_authorized': False}
    sys.stderr.buffer.write(canonical(failure) + b'\n'); sys.stderr.buffer.flush(); return 1


if __name__ == '__main__':
    raise SystemExit(main())
