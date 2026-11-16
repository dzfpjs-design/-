"""Research-only CPU comparison of four transcribed training-era components.

No official factory, backbone forward, decode-head comparison, preprocessing,
images, labels, product modules, default paths or registration is involved.
Reference imports/decorators are omitted; the exact compatibility boundary is
saved with the input plan BEFORE component initialization or forward execution.
"""
import argparse
import ast
import builtins
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

WEIGHT_SHA = '507932767138639cc64079bcfd5df2861aeeef9c26e924b799e2d4de92bce2a6'
WEIGHT_BYTES = 45624736
LOCAL_SHA = '1cbcc80a70015112f42dc4da66b4995dda5e788f015ce7b06ad79047a37a846b'
SOURCES = (
    ('training-era-da8b536/changer.py',
     'c6d4388374a0c326bec0b9f49f3336f8b33547f47dfbf874c099fff1c7602888',
     'web-transcripts/training-era-da8b536/changer.py.txt',
     'https://raw.githubusercontent.com/likyoo/open-cd/da8b536/opencd/models/decode_heads/changer.py',
     ('FDAF', 'MixFFN')),
    ('training-era-da8b536/interaction_layer.py',
     'd0a4c4669645c2003f2eacf88f0fe32533a35aa834c5795dff1985aa213e1c6b',
     'web-transcripts/training-era-da8b536/interaction_layer.py.txt',
     'https://raw.githubusercontent.com/likyoo/open-cd/da8b536/opencd/models/utils/interaction_layer.py',
     ('SpatialExchange', 'ChannelExchange')),
)
LOCAL_CLASSES = ('ResidualBlock', 'InteractionResNet18', 'ConvBN', 'DualAlignment',
                 'PointwiseResidual', 'ChangeHead', 'ChangerExR18')
SEED = 251031
ATOL, RTOL = 1e-6, 1e-5
MAX_REPORT = 4 * 1024 * 1024
EXCHANGE_SHAPES = ((1, 64, 16, 20), (2, 128, 17, 23), (1, 512, 8, 11))
WARP_SHAPES = ((1, 4, 9, 13), (2, 64, 7, 11), (1, 3, 32, 48))
FDAF_SHAPES = ((1, 64, 128, 128), (1, 64, 64, 80), (2, 64, 17, 23))
MIX_SHAPES = ((1, 128, 128, 128), (1, 128, 64, 80), (2, 128, 17, 23))

# This deliberately does NOT implement MMCV initialization, registries, norms,
# training wrappers, stochastic DropPath or any BaseDecodeHead/ConvModule.
COMPAT_SOURCE = '''class BaseModule(nn.Module):
    def __init__(self, init_cfg=None):
        if init_cfg is not None:
            raise ValueError("only init_cfg=None is in the comparison scope")
        super().__init__()
        self.init_cfg = None

Sequential = nn.Sequential
Conv2d = nn.Conv2d

def build_activation_layer(cfg):
    if type(cfg) is not dict or cfg != {"type": "GELU"}:
        raise ValueError("only the actual default GELU is implemented")
    return nn.GELU()

def build_dropout(cfg):
    if (type(cfg) is not dict or set(cfg) != {"type", "drop_prob"}
            or cfg["type"] != "DropPath" or type(cfg["drop_prob"]) not in {int, float}
            or cfg["drop_prob"] != 0):
        raise ValueError("only DropPath probability zero is implemented")
    return nn.Identity()

class FeatureFusionNeck:
    @staticmethod
    def fusion(x1, x2, policy):
        if policy != "concat":
            raise ValueError("only explicit channel concat is implemented")
        return torch.cat((x1, x2), dim=1)
'''


def need(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, limit, expected_sha=None, expected_size=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'input is not a bounded regular file')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
             == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
             and len(raw) == before.st_size, 'input changed while reading')
    need(expected_sha is None or sha(raw) == expected_sha, 'reviewed source or weights SHA changed')
    need(expected_size is None or len(raw) == expected_size, 'fixed weights byte size changed')
    return raw


def write_new(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False).encode('utf-8') + b'\n'
    need(len(raw) <= MAX_REPORT, 'complete report exceeds bounds; not truncated')
    parent = path.parent.resolve(strict=True)
    need(parent.is_dir(), 'output parent must exist')
    descriptor, temporary = tempfile.mkstemp(prefix='.changer-components-', dir=parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)  # Atomic; no replacement.
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        need(read_regular(path, MAX_REPORT) == raw, 'output exact readback failed')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'bytes': len(raw), 'sha256': sha(raw)}


def selected_ast(raw, names, *, local=False):
    tree = ast.parse(raw.decode('utf-8'))
    selected = [copy.deepcopy(node) for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    need(len(selected) == len(names) and {node.name for node in selected} == set(names),
         'selected definitions missing or duplicated')
    removed = {}
    for node in selected:
        if isinstance(node, ast.ClassDef):
            expected_base = 'nn.Module' if local else 'BaseModule'
            need(len(node.bases) == 1 and ast.unparse(node.bases[0]) == expected_base,
                 'component base class changed')
        removed[node.name] = [ast.unparse(decorator) for decorator in node.decorator_list]
        node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    return module, {'definitions': list(names), 'registry_decorators_removed': removed,
                    'selected_ast_sha256': sha(ast.dump(module, include_attributes=False).encode())}


def source_bundle(root, local_model):
    manifest_raw = read_regular(root / 'SOURCES.json', 1024 * 1024)
    manifest = json.loads(manifest_raw)
    need(type(manifest) is dict and manifest.get('schema') == 'changer-official-web-transcription-v251',
         'public transcription manifest missing or changed')
    modules, sources = [], []
    for relative, expected, transcript_relative, url, names in SOURCES:
        raw = read_regular(root / relative, 1024 * 1024, expected)
        transcript = read_regular(root / transcript_relative, 2 * 1024 * 1024)
        text = transcript.decode('utf-8'); numbered = []
        need(url in '\n'.join(text.splitlines()[:3]), 'transcript URL does not match the source')
        for line in text.splitlines():
            match = re.fullmatch(r'L(\d+): ?(.*)', line)
            if match: numbered.append((int(match.group(1)), match.group(2)))
        need([index for index, _ in numbered] == list(range(len(numbered)))
             and ('\n'.join(line for _, line in numbered) + '\n').encode('utf-8') == raw,
             'web line transcription is incomplete or differs from reviewed source')
        module, extracted = selected_ast(raw, names)
        modules.append(module)
        sources.append({'path': relative, 'source_url': url, 'commit_ref': 'da8b536',
            'commit_ref_is_abbreviated': True, 'full_git_commit_verified': False,
            'source_sha256': sha(raw), 'source_bytes': len(raw), 'transcript_path': transcript_relative,
            'transcript_sha256': sha(transcript), 'complete_consecutive_web_lines': len(numbered),
            'transcribed': True, 'original_http_byte_sha256': None, **extracted})
    local_raw = read_regular(local_model, 1024 * 1024, LOCAL_SHA)
    local_ast, local_info = selected_ast(local_raw, (*LOCAL_CLASSES, 'exchange'), local=True)
    return modules, local_ast, {'manifest_sha256': sha(manifest_raw),
        'manifest_acquisition': manifest.get('acquisition'), 'source_files': sources,
        'training_era_files_listed_in_manifest': all(any(item.get('path') == row['path']
            for item in manifest.get('files', [])) for row in sources),
        'local_model_sha256': sha(local_raw), 'local_model_ast': local_info,
        'limitation': 'Reference is reviewed web-line transcription, not independently hashed original HTTP bytes; no full official package/factory is imported.'}


def tensor_info(value):
    need(value.device.type == 'cpu' and value.dtype.is_floating_point,
         'comparison inputs and outputs must be CPU floating tensors')
    array = value.detach().contiguous().numpy()
    return {'shape': list(value.shape), 'dtype': str(value.dtype),
            'sha256_native_cpu_bytes': sha(memoryview(array).cast('B'))}


def inputs(torch):
    values = []
    def random(shape, seed):
        return torch.randn(shape, generator=torch.Generator(device='cpu').manual_seed(seed),
                           device='cpu', dtype=torch.float32)
    for kind, shapes in (('exchange', EXCHANGE_SHAPES), ('fdaf', FDAF_SHAPES), ('mixffn', MIX_SHAPES)):
        for index, shape in enumerate(shapes):
            seed = SEED + 1000 * ('exchange', 'fdaf', 'mixffn').index(kind) + index * 10
            parts = [random(shape, seed)]
            if kind != 'mixffn': parts.append(random(shape, seed + 1))
            values.append({'id': kind + '-' + str(index), 'kind': kind, 'seed': seed, 'parts': parts})
    for index, shape in enumerate(WARP_SHAPES):
        for mode in ('zero', 'random'):
            seed = SEED + 3000 + index * 10
            flow_shape = (shape[0], 2, shape[2], shape[3])
            flow = torch.zeros(flow_shape, dtype=torch.float32) if mode == 'zero' else random(flow_shape, seed + 1) * 0.4
            values.append({'id': 'warp-' + str(index) + '-' + mode, 'kind': 'warp',
                'seed': seed, 'flow_mode': mode, 'flow_scale': 0 if mode == 'zero' else 0.4,
                'parts': [random(shape, seed), flow]})
    return values


def execution_namespace(torch, name):
    allowed = ('__build_class__', 'super', 'dict', 'int', 'float', 'str', 'type', 'set', 'range',
               'len', 'enumerate', 'zip', 'setattr', 'getattr', 'staticmethod', 'ValueError')
    return {'__name__': name, '__builtins__': {key: getattr(builtins, key) for key in allowed},
            'torch': torch, 'nn': torch.nn, 'F': torch.nn.functional}


def unchanged_state(torch, module, state):
    actual = module.state_dict()
    need(set(actual) == set(state) and all(actual[key].dtype == value.dtype
         and torch.equal(actual[key], value) for key, value in state.items()),
         'state values are not exactly the frozen originals')


def strict_load(torch, module, state):
    outcome = module.load_state_dict(state, strict=True)
    need(not outcome.missing_keys and not outcome.unexpected_keys, 'strict state load failed')
    unchanged_state(torch, module, state)
    return {'strict': True, 'state_keys': len(state), 'missing_keys': [], 'unexpected_keys': [],
            'all_tensors_exact': True}


def compare(torch, name, reference, local):
    need(type(reference) is torch.Tensor and type(local) is torch.Tensor
         and reference.device.type == local.device.type == 'cpu'
         and reference.dtype == local.dtype == torch.float32
         and reference.shape == local.shape and reference.numel() > 0,
         'comparison requires matching nonempty CPU float32 tensors')
    finite = bool(torch.isfinite(reference).all() and torch.isfinite(local).all())
    difference = (reference - local).abs() if finite else None
    return {'comparison': name, 'shape': list(reference.shape), 'dtype': 'torch.float32',
        'elements': reference.numel(), 'finite': finite,
        'max_abs': float(difference.max()) if finite else None,
        'mean_abs': float(difference.double().mean()) if finite else None,
        'atol': ATOL, 'rtol': RTOL,
        'within_tolerance': finite and bool(torch.allclose(reference, local, atol=ATOL, rtol=RTOL, equal_nan=False)),
        'reference_sha256': tensor_info(reference)['sha256_native_cpu_bytes'],
        'local_sha256': tensor_info(local)['sha256_native_cpu_bytes']}


def verify(args, stage):
    stage['name'] = 'source_validation'
    target = args.out.absolute(); parent = target.parent.resolve(strict=True)
    target = parent / target.name; plan_path = parent / (target.name + '.plan.json')
    need(not target.exists() and not target.is_symlink() and not plan_path.exists() and not plan_path.is_symlink(),
         'output and plan must both be new files')
    modules, local_ast, provenance = source_bundle(args.sources, args.local_model)
    weight_raw = read_regular(args.weights, WEIGHT_BYTES, WEIGHT_SHA, WEIGHT_BYTES)
    script_sha = sha(read_regular(Path(__file__), 256 * 1024))
    stage['name'] = 'trusted_runtime_and_frozen_inputs'
    import numpy as np
    import torch
    import safetensors
    from safetensors.torch import load
    need(torch.__version__.split('+')[0] == '2.8.0', 'reviewed Torch 2.8.0 runtime required')
    torch.set_num_threads(1); torch.set_default_device('cpu'); torch.set_default_dtype(torch.float32)
    torch.use_deterministic_algorithms(True); torch.manual_seed(SEED)
    cases = inputs(torch)
    frozen_inputs = [{**{k: v for k, v in case.items() if k != 'parts'},
                      'inputs': [tensor_info(part) for part in case['parts']]} for case in cases]
    plan = {'schema': 'changer-public-component-plan-v251', 'research_only': True,
        'script_sha256': script_sha, 'python_version': sys.version.split()[0],
        'torch_version': torch.__version__, 'numpy_version': np.__version__,
        'safetensors_version': safetensors.__version__, 'backend': 'cpu', 'dtype': 'float32',
        'threads': 1, 'deterministic_algorithms': True, 'seed': SEED, 'atol': ATOL, 'rtol': RTOL,
        'weights_sha256': WEIGHT_SHA, 'weights_bytes': WEIGHT_BYTES, 'expected_local_state_keys': 173,
        'expected_reference_state_keys': {'FDAF': 3, 'MixFFN': 6, 'SpatialExchange': 0, 'ChannelExchange': 0},
        'provenance': provenance, 'compatibility_source': COMPAT_SOURCE,
        'compatibility_sha256': sha(COMPAT_SOURCE.encode()),
        'compatibility_scope': ['BaseModule init_cfg=None only', 'torch.nn.Sequential/Conv2d',
            'default GELU', 'DropPath probability zero as Identity', 'channel concat only'],
        'input_origin': 'fixed_seed_synthetic_feature_and_flow_tensors_no_images_or_labels',
        'inputs': frozen_inputs, 'tested': ['half spatial exchange', 'half channel exchange',
            'warp including non-square and zero/random flow', 'FDAF64 tuple/concat', 'MixFFN128 default identity'],
        'not_tested': ['official backbone numerical behavior', 'official Changer head logits',
            'full official factory/runtime', 'preprocessing', 'full model equivalence', 'semantic model quality']}
    plan['plan_hash'] = sha(canonical(plan))
    plan_file = write_new(plan_path, plan)  # MUST precede model construction/forwards.
    stage['name'] = 'strict_component_state_loading'
    reference = execution_namespace(torch, '_changer_training_components_v251')
    exec(compile(COMPAT_SOURCE, '<explicit-v251-compatibility>', 'exec'), reference)
    for module in modules:
        exec(compile(module, '<reviewed-training-component-ast>', 'exec'), reference)
    local = execution_namespace(torch, '_changer_local_components_v251')
    exec(compile(local_ast, '<reviewed-local-model-ast>', 'exec'), local)
    state = load(weight_raw)
    need(len(state) == 173 and all(type(v) is torch.Tensor and v.device.type == 'cpu'
        and v.layout == torch.strided and v.dtype in {torch.float32, torch.int64}
        and bool(torch.isfinite(v).all()) for v in state.values()),
        'fixed local safetensors state is not 173 finite dense CPU entries')
    model = local['ChangerExR18']().eval()
    loaded = {'local_model': strict_load(torch, model, state)}
    fdaf = reference['FDAF'](in_channels=64).eval()
    mix = reference['MixFFN'](embed_dims=128, feedforward_channels=128, ffn_drop=0.,
        dropout_layer={'type': 'DropPath', 'drop_prob': 0.}, act_cfg={'type': 'GELU'}).eval()
    loaded['official_FDAF'] = strict_load(torch, fdaf, model.decode_head.neck_layer.state_dict())
    loaded['official_MixFFN'] = strict_load(torch, mix, model.decode_head.discriminator.state_dict())
    spatial = reference['SpatialExchange'](p=0.5).eval()
    channel = reference['ChannelExchange'](p=0.5).eval()
    loaded['official_SpatialExchange'] = strict_load(torch, spatial, {})
    loaded['official_ChannelExchange'] = strict_load(torch, channel, {})
    need(all(loaded['official_' + key]['state_keys'] == count
             for key, count in plan['expected_reference_state_keys'].items()), 'reference state count changed')
    need(all(not part.training for item in (model, fdaf, mix, spatial, channel) for part in item.modules()),
         'all compared components must be in evaluation mode')
    stage['name'] = 'component_forwards'
    results = []; started = time.perf_counter()
    with torch.inference_mode():
        for case in cases:
            parts = case['parts']; name = case['id']
            if case['kind'] == 'exchange':
                for label, component, axis in [('spatial', spatial, 3), ('channel', channel, 1)]:
                    actual = component(*parts); candidate = local['exchange'](*parts, axis)
                    results.append(compare(torch, name + '-' + label,
                        torch.cat(actual, dim=1), torch.cat(candidate, dim=1)))
            elif case['kind'] == 'warp':
                results.append(compare(torch, name, reference['FDAF'].warp(*parts),
                                       local['DualAlignment'].warp(*parts)))
            elif case['kind'] == 'fdaf':
                candidate = model.decode_head.neck_layer(*parts)
                results.append(compare(torch, name + '-tuple', torch.cat(fdaf(*parts), dim=1), candidate))
                results.append(compare(torch, name + '-concat-policy', fdaf(*parts, fusion_policy='concat'), candidate))
            else:
                results.append(compare(torch, name, mix(*parts), model.decode_head.discriminator(*parts)))
    seconds = time.perf_counter() - started
    need([[tensor_info(part) for part in case['parts']] for case in cases]
         == [case['inputs'] for case in frozen_inputs], 'frozen inputs were mutated')
    unchanged_state(torch, model, state)
    unchanged_state(torch, fdaf, model.decode_head.neck_layer.state_dict())
    unchanged_state(torch, mix, model.decode_head.discriminator.state_dict())
    stage['name'] = 'report_write'
    matched = all(item['within_tolerance'] for item in results)
    report = {'schema': 'changer-public-component-result-v251', 'research_only': True,
        'plan_hash': plan['plan_hash'], 'plan_file': plan_file,
        'script_sha256': script_sha, 'provenance': provenance,
        'compatibility_sha256': plan['compatibility_sha256'], 'torch_version': torch.__version__,
        'weights_sha256': WEIGHT_SHA, 'strict_state_loads': loaded, 'comparisons': results,
        'comparison_count': len(results), 'all_components_within_tolerance': matched,
        'input_tensors_unchanged': True, 'model_state_unchanged': True,
        'component_seconds': seconds, 'backend': 'cpu', 'dtype': 'float32',
        'official_backbone_numerical_equivalence': 'not_tested',
        'official_head_logits_equivalence': 'not_tested', 'full_factory_equivalence': 'not_tested',
        'preprocessing_equivalence': 'not_tested', 'full_model_equivalence_verified': False,
        'semantic_quality_verified': False, 'product_registered': False, 'default_model_changed': False,
        'unsafe_pickle_used': False, 'not_tested': plan['not_tested']}
    saved = write_new(target, report)
    print(json.dumps({'schema': report['schema'], 'research_only': True,
        'plan_hash': plan['plan_hash'], 'comparison_count': len(results),
        'all_components_within_tolerance': matched, 'report': saved,
        'full_model_equivalence_verified': False, 'product_registered': False}))
    return 0 if matched else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True, help='Explicit public transcribed-source root')
    parser.add_argument('--weights', type=Path, required=True, help='Exact fixed 173-entry safetensors file')
    parser.add_argument('--local-model', type=Path, required=True, help='Explicit reviewed local model source')
    parser.add_argument('--out', type=Path, required=True, help='New JSON result; adjacent .plan.json is also new')
    args = parser.parse_args(); stage = {'name': 'arguments'}
    try:
        return verify(args, stage)
    except Exception as error:
        print(json.dumps({'schema': 'changer-public-component-result-v251', 'research_only': True,
            'comparison_failed': True, 'stage': stage['name'], 'error_type': type(error).__name__,
            'unsafe_fallback_used': False, 'full_model_equivalence_verified': False,
            'product_registered': False, 'default_model_changed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
