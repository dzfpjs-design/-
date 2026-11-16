"""Research-only metadata inspection of ONE checksum-pinned public checkpoint.

No downloader, upstream model factory, configuration evaluation, inference,
conversion output, product registration or default-model modification. The
numeric NumPy safety profile is copied from the reviewed converter deliberately:
importing a preparation/conversion module could create operational directories.
Run only in the already trusted isolated Torch/NumPy inspection runtime.
"""
import argparse
from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import pickletools
import re
import stat
import tempfile

SHA256 = 'da3f569306dadd1fac5b64abc2a3d484571cd0ecf6410e1f152275cfe49a3618'
SIZE_BYTES = 136880383
STATE_COUNT = 173
GLOBALS = {'_codecs encode', 'collections OrderedDict', 'numpy dtype',
           'numpy.core.multiarray scalar', 'torch FloatStorage', 'torch LongStorage',
           'torch._utils _rebuild_tensor_v2'}
MAX_PICKLE_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
VERSION_KEY = re.compile(r'(?:^|_)(?:git|version|revision|commit)(?:_|$)', re.IGNORECASE)
STATE_KEY = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8', errors='strict')


def fingerprint(stream):
    stream.seek(0)
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(chunk)
    stream.seek(0)
    return checksum.hexdigest()


def identity(stream):
    info = os.fstat(stream.fileno())
    need(stat.S_ISREG(info.st_mode), 'checkpoint must be a regular file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def validate_original(stream):
    original = identity(stream)
    need(original[2] == SIZE_BYTES and fingerprint(stream) == SHA256,
         'only the exact reviewed public checkpoint is allowed')
    with zipfile_from_stream(stream) as archive:
        entries = archive.infolist()
        need(1 <= len(entries) <= 2000, 'archive metadata exceeds bounds')
        names = [entry.filename for entry in entries]
        need(len(set(names)) == len(names), 'duplicate archive members')
        metadata = [entry for entry in entries if entry.filename.endswith('/data.pkl')]
        need(len(metadata) == 1 and 0 < metadata[0].file_size <= MAX_PICKLE_BYTES,
             'pickle metadata missing, duplicated or oversized')
        detected, count = set(), 0
        for operation, argument, _ in pickletools.genops(archive.read(metadata[0])):
            count += 1
            need(count <= 200000, 'pickle opcode limit exceeded')
            if operation.name == 'GLOBAL':
                detected.add(argument)
            elif operation.name in {'INST', 'OBJ', 'EXT1', 'EXT2', 'EXT4', 'STACK_GLOBAL', 'PERSID'}:
                raise ValueError('unsupported dynamic pickle opcode')
        need(detected == GLOBALS, 'fixed checkpoint GLOBAL set changed')
    stream.seek(0)
    need(identity(stream) == original, 'checkpoint changed during validation')
    return original, count


def zipfile_from_stream(stream):
    # Standard-library reader only; never extract members or import archive code.
    import zipfile
    stream.seek(0)
    return zipfile.ZipFile(stream)


def plain(value, *, max_string_bytes=65536, max_nodes=10000):
    """Bounded data-only JSON projection; no repr, eval or custom serializers."""
    remaining = [max_nodes]

    def visit(item, depth):
        remaining[0] -= 1
        need(remaining[0] >= 0 and depth <= 32, 'selected metadata exceeds structural bounds')
        if item is None or type(item) in {bool, int}:
            return item
        if type(item) is float:
            need(math.isfinite(item), 'nonfinite selected metadata')
            return item
        if type(item) is str:
            need(len(item.encode('utf-8', errors='strict')) <= max_string_bytes,
                 'selected metadata string exceeds bounds')
            return item
        if type(item) in {list, tuple}:
            return [visit(child, depth + 1) for child in item]
        if type(item) in {dict, OrderedDict}:
            need(all(type(key) is str and len(key.encode('utf-8')) <= 256 for key in item),
                 'selected metadata keys must be bounded strings')
            return {key: visit(child, depth + 1) for key, child in item.items()}
        raise ValueError('selected metadata is not plain data')

    return visit(value, 0)


def selected_metadata(checkpoint):
    meta = checkpoint.get('meta')
    need(type(meta) in {dict, OrderedDict} and 'config' in meta,
         'checkpoint does not contain the full meta.config; no overlay substitution')
    raw_config = meta['config']
    need(type(raw_config) in {str, dict, OrderedDict}, 'unsupported meta.config data type')
    config = plain(raw_config, max_string_bytes=MAX_CONFIG_BYTES, max_nodes=50000)
    config_raw = config.encode('utf-8') if type(config) is str else encoded(config)
    need(0 < len(config_raw) <= MAX_CONFIG_BYTES, 'full meta.config exceeds bounds; not truncated')
    version_fields = {}
    for key, value in meta.items():
        need(type(key) is str, 'metadata keys must be strings')
        if VERSION_KEY.search(key):
            need(len(version_fields) < 64, 'too many version metadata fields')
            version_fields['meta.' + key] = plain(value, max_nodes=2000)
    class_fields = {}
    for parent_name, parent in (('meta', meta), ('checkpoint', checkpoint)):
        for key in ('CLASSES', 'classes', 'class_names', 'PALETTE', 'palette'):
            if key in parent:
                class_fields[parent_name + '.' + key] = plain(parent[key], max_nodes=2000)
    dataset_meta = meta.get('dataset_meta')
    if dataset_meta is not None:
        need(type(dataset_meta) in {dict, OrderedDict}, 'unsupported dataset metadata type')
        for key in ('classes', 'class_names', 'palette'):
            if key in dataset_meta:
                class_fields['meta.dataset_meta.' + key] = plain(dataset_meta[key], max_nodes=2000)
    need(len(encoded(version_fields)) <= 256 * 1024 and len(encoded(class_fields)) <= 256 * 1024,
         'selected class or version metadata exceeds bounds')
    return {'meta_config': {'source': 'meta.config', 'source_type': type(raw_config).__name__,
                'complete': True, 'utf8_bytes': len(config_raw),
                'sha256': hashlib.sha256(config_raw).hexdigest(), 'value': config},
            'version_metadata': version_fields, 'class_metadata': class_fields,
            'class_index_mapping_inferred': False}


def state_summary(checkpoint, torch):
    state = checkpoint.get('state_dict')
    need(type(state) in {dict, OrderedDict} and len(state) == STATE_COUNT,
         'official state dictionary does not contain exactly 173 keys')
    rows, total_bytes = [], 0
    for key in sorted(state):
        need(type(key) is str and STATE_KEY.fullmatch(key) is not None, 'unexpected tensor key')
        value = state[key]
        need(type(value) is torch.Tensor and value.device.type == 'cpu'
             and value.layout == torch.strided and value.ndim <= 4,
             'only bounded dense CPU state tensors are allowed')
        need(value.dtype in {torch.float32, torch.int64} and bool(torch.isfinite(value).all()),
             'unexpected state dtype or nonfinite values')
        count = value.numel(); size = count * value.element_size(); total_bytes += size
        need(total_bytes <= SIZE_BYTES, 'state tensor bytes exceed bounds')
        array = value.detach().contiguous().numpy()
        raw = memoryview(array).cast('B')
        rows.append({'key': key, 'dtype': str(value.dtype), 'numpy_dtype': array.dtype.str,
            'shape': list(value.shape), 'numel': count, 'size_bytes': size,
            'sha256_native_cpu_bytes': hashlib.sha256(raw).hexdigest()})
    return {'key_count': len(rows), 'tensor_bytes': total_bytes, 'keys': rows,
            'inventory_sha256': hashlib.sha256(encoded(rows)).hexdigest()}


def inspect(source):
    # No directory creation or product import occurs, including on import.
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        original, opcode_count = validate_original(stream)
        import numpy as np
        import torch
        need(torch.__version__.split('+')[0] == '2.8.0', 'reviewed Torch 2.8.0 runtime required')
        torch.set_num_threads(2)
        prior = torch.serialization.get_safe_globals()
        allowed = [(np._core.multiarray.scalar, 'numpy.core.multiarray.scalar'),
                   np.dtype, np.dtypes.Float64DType]
        try:
            with torch.serialization.safe_globals(allowed):
                checkpoint = torch.load(stream, weights_only=True, map_location='cpu')
        finally:
            need(torch.serialization.get_safe_globals() == prior, 'scoped safety profile leaked')
        need(type(checkpoint) is dict, 'unexpected checkpoint container')
        metadata = selected_metadata(checkpoint)
        state = state_summary(checkpoint, torch)
        need(identity(stream) == original and fingerprint(stream) == SHA256,
             'checkpoint changed while inspecting')
    return {'schema': 'changer-public-config-inspection-v251', 'research_only': True,
        'source_sha256': SHA256, 'source_bytes': SIZE_BYTES,
        'pickle_global_allowlist': sorted(GLOBALS), 'pickle_opcode_count': opcode_count,
        'torch_version': torch.__version__, 'numpy_version': np.__version__,
        'weights_only': True, 'map_location': 'cpu', 'scoped_numeric_globals_only': True,
        'ambient_safe_global_count': len(prior), 'global_allowlist_restored': True,
        'unsafe_fallback_used': False, 'config_executed': False,
        'model_created': False, 'model_inference_executed': False,
        'product_registered': False, 'default_model_changed': False,
        **metadata, 'state': state}


def save_report(target, report):
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False).encode('utf-8') + b'\n'
    need(len(raw) <= MAX_REPORT_BYTES, 'complete report exceeds bounds; not truncated')
    parent = target.parent.resolve(strict=True)
    need(parent.is_dir(), 'output parent must already exist')
    target = parent / target.name
    descriptor, temporary = tempfile.mkstemp(prefix='.changer-config-inspect-', dir=parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        # Atomic, no replacement: never overwrite an earlier report or symlink.
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        check = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(check, 'rb') as stream:
            need(stream.read(MAX_REPORT_BYTES + 1) == raw, 'report exact readback failed')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'report_bytes': len(raw), 'report_sha256': hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True, help='Exact public checkpoint path; never downloaded')
    parser.add_argument('--out', type=Path, required=True, help='New JSON file in an existing directory; never overwritten')
    args = parser.parse_args()
    try:
        report = inspect(args.checkpoint)
        saved = save_report(args.out, report)
        print(json.dumps({'schema': report['schema'], 'research_only': True,
            'source_sha256': SHA256, 'state_key_count': report['state']['key_count'],
            'config_bytes': report['meta_config']['utf8_bytes'], **saved,
            'model_inference_executed': False, 'product_registered': False}))
        return 0
    except Exception as error:
        # No raw config, filesystem path, untrusted exception text or training
        # history is printed on failure. There is no unsafe retry/fallback.
        print(json.dumps({'schema': 'changer-public-config-inspection-v251',
            'inspection_failed': True, 'error_type': type(error).__name__,
            'research_only': True, 'unsafe_fallback_used': False,
            'model_inference_executed': False, 'product_registered': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
