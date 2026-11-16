"""Small synthetic safety contracts, not official-wheel/model execution.

Twenty tiny .py files exercise the closed source-layout verifier only. Passing
these tests cannot prove AnyChange/SAM inference, numerical fidelity, model
quality, licensing or a real native run. No source fixture is ever imported.
"""
import copy
import hashlib
import io
import json
import struct
from types import SimpleNamespace

import pytest

from generic_agents import anychange_runner_v258 as runner


@pytest.fixture
def source(tmp_path):
    # Resolve pytest's own temporary root once: macOS /var aliases are not
    # source symlinks. Intentional leaf/parent symlinks below remain rejected.
    root = tmp_path.resolve(strict=True) / 'synthetic-wheel-source'
    subtree = root / 'torchange/models/segment_any_change'
    hashes = {}
    for name in sorted(runner.SOURCE_FILES):
        path = subtree / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = b'' if name == 'segment_anything/utils/__init__.py' else (
            '# Synthetic verifier fixture, not official algorithm: ' + name + '\n').encode()
        path.write_bytes(raw); hashes[name] = hashlib.sha256(raw).hexdigest()
    return SimpleNamespace(root=root, subtree=subtree, hashes=hashes)


def plan(source):
    body = {'schema': 'anychange-execution-plan-v258', 'distribution': 'torchange',
            'version': '0.0.4', 'wheel_sha256': runner.WHEEL_SHA,
            'source_files': copy.deepcopy(source.hashes), 'weights': copy.deepcopy(runner.WEIGHTS),
            'native_parameters': copy.deepcopy(runner.PARAMETERS), 'limits': copy.deepcopy(runner.LIMITS)}
    return {**body, 'plan_hash': runner.digest(body)}


def reseal(value):
    body = {key: item for key, item in value.items() if key != 'plan_hash'}
    return {**body, 'plan_hash': runner.digest(body)}


def failure(code, function, *args, **kwargs):
    with pytest.raises(runner.Failure) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code


def test_plan_and_twenty_synthetic_sources_verify_without_importing_any_algorithm(source):
    body = plan(source)
    assert runner.checked_plan(runner.canonical(body)) == body
    subtree, hashes = runner.verify_source(source.root, body['source_files'])
    assert subtree == source.subtree and hashes == source.hashes
    assert hashes['segment_anything/utils/__init__.py'] == hashlib.sha256(b'').hexdigest()
    # An empty source module is legitimate; ordinary evidence files are not.
    failure('input_invalid', runner.fingerprint,
            source.subtree / 'segment_anything/utils/__init__.py', 'input_invalid')


@pytest.mark.parametrize('kind', ['top_extra', 'native_extra', 'angle', 'bool_points',
    'weights_extra', 'limits_extra', 'source_extra', 'source_missing', 'bad_source_sha', 'hash'])
def test_rehashed_unknown_keys_or_changed_frozen_parameters_are_rejected(source, kind):
    body = plan(source)
    if kind == 'top_extra': body['user_override'] = True
    elif kind == 'native_extra': body['native_parameters']['new_rule'] = True
    elif kind == 'angle': body['native_parameters']['change_confidence_threshold'] = 155
    elif kind == 'bool_points': body['native_parameters']['points_per_side'] = True
    elif kind == 'weights_extra': body['weights']['alternate_file'] = 'other.pth'
    elif kind == 'limits_extra': body['limits']['unbounded'] = True
    elif kind == 'source_extra': body['source_files']['extra.py'] = '0' * 64
    elif kind == 'source_missing': body['source_files'].pop('base.py')
    elif kind == 'bad_source_sha': body['source_files']['base.py'] = 'not-a-sha'
    body = reseal(body)
    if kind == 'hash': body['plan_hash'] = '0' * 64
    failure('plan_invalid', runner.checked_plan, runner.canonical(body))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_plan_values_are_rejected_during_json_parse(source, value):
    body = plan(source); body['native_parameters']['change_confidence_threshold'] = value
    raw = json.dumps(body, allow_nan=True).encode()
    failure('plan_invalid', runner.checked_plan, raw)


def test_duplicate_json_keys_are_rejected_even_when_the_last_value_is_unchanged(source):
    body = plan(source); raw = runner.canonical(body)
    duplicate = raw[:-1] + b',"schema":"anychange-execution-plan-v258"}'
    failure('plan_invalid', runner.checked_plan, duplicate)


@pytest.mark.parametrize('name', ['base.cpython-313-darwin.so', '__init__.pyd', 'README.txt',
    'unexpected.py', '__pycache__/base.pyc'])
def test_source_tree_rejects_extension_shadow_bytecode_and_all_extra_files(source, name):
    extra = source.subtree / name; extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b'not executable; synthetic source-shadow counterexample')
    failure('source_invalid', runner.verify_source, source.root, source.hashes)


def test_even_an_empty_unlisted_directory_is_rejected(source):
    (source.subtree / 'unexpected_empty_package').mkdir()
    failure('source_invalid', runner.verify_source, source.root, source.hashes)


@pytest.mark.parametrize('kind', ['missing', 'modified'])
def test_source_files_must_all_exist_with_the_declared_actual_bytes(source, kind):
    path = source.subtree / 'base.py'
    if kind == 'missing': path.unlink()
    else: path.write_bytes(b'# changed synthetic source, not an official implementation\n')
    failure('source_invalid', runner.verify_source, source.root, source.hashes)


@pytest.mark.parametrize('kind', ['leaf', 'parent', 'root'])
def test_source_leaf_parent_and_root_symlinks_are_not_followed(source, kind):
    if kind == 'leaf':
        path = source.subtree / 'base.py'; target = source.root.parent / 'same-original-source.py'
        target.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(target)
    elif kind == 'parent':
        path = source.subtree / 'segment_anything/utils'; target = source.root.parent / 'same-original-utils'
        path.rename(target); path.symlink_to(target, target_is_directory=True)
    else:
        alias = source.root.parent / 'source-alias'; alias.symlink_to(source.root, target_is_directory=True)
        failure('source_invalid', runner.verify_source, alias, source.hashes)
        return
    failure('source_invalid', runner.verify_source, source.root, source.hashes)


def png_header(width, height, *, depth=8, color=2):
    # Header-only tests deliberately do not claim CRC or full PNG validation.
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>IIBBBBB',
        width, height, depth, color, 0, 0, 0) + b'\0' * 4


@pytest.mark.parametrize('width,height,depth,color,kind', [
    (0, 32, 8, 2, 'rgb'), (31, 32, 8, 2, 'rgb'), (32, 31, 8, 2, 'rgb'),
    (1537, 32, 8, 2, 'rgb'), (1200, 1001, 8, 2, 'rgb'),
    (32, 32, 16, 2, 'rgb'), (32, 32, 8, 6, 'rgb'), (32, 32, 8, 3, 'rgb'),
    (32, 32, 8, 2, 'valid'), (32, 32, 1, 0, 'valid'),
])
def test_png_header_limits_and_exact_rgb_or_gray8_contract(width, height, depth, color, kind):
    failure('input_invalid', runner.checked_png_header,
            png_header(width, height, depth=depth, color=color), kind)


def test_header_requires_png_signature_and_ihdr_and_accepts_exact_lower_boundary():
    assert runner.checked_png_header(png_header(32, 32), 'rgb') == (32, 32)
    assert runner.checked_png_header(png_header(32, 32, color=0), 'valid') == (32, 32)
    failure('input_invalid', runner.checked_png_header, b'not a png', 'rgb')
    raw = png_header(32, 32)
    failure('input_invalid', runner.checked_png_header, raw[:12] + b'XXXX' + raw[16:], 'rgb')


@pytest.fixture
def image_modules():
    # No torch/SAM/author module is imported; Root supplies NumPy and Pillow.
    import numpy
    from PIL import Image
    return numpy, Image


def input_pngs(np, Image, valid_value=255):
    arrays = {'before': np.zeros((32, 32, 3), dtype='uint8'),
              'after': np.full((32, 32, 3), 137, dtype='uint8'),
              'valid': np.full((32, 32), valid_value, dtype='uint8')}
    raw = {}
    for name, array in arrays.items():
        out = io.BytesIO(); Image.fromarray(array).save(out, format='PNG'); raw[name] = out.getvalue()
    return raw


@pytest.mark.parametrize('value', [1, 128, 254])
def test_decoded_valid_mask_rejects_unmapped_codes_without_silent_binarization(image_modules, value):
    np, Image = image_modules
    failure('input_invalid', runner.decode_pngs, input_pngs(np, Image, value), (32, 32), np, Image)


def test_full_png_decode_preserves_original_rgb_and_explicit_valid_codes(image_modules):
    np, Image = image_modules
    for value in (0, 255):
        decoded = runner.decode_pngs(input_pngs(np, Image, value), (32, 32), np, Image)
        assert decoded['before'].dtype == np.dtype('uint8') and not decoded['before'].any()
        assert (decoded['after'] == 137).all() and (decoded['valid'] == value).all()


def test_input_directory_has_only_the_three_originals_and_hashes_actual_bytes(tmp_path, image_modules):
    np, Image = image_modules; root = tmp_path.resolve(strict=True) / 'input'; root.mkdir()
    originals = input_pngs(np, Image)
    for name, raw in originals.items(): (root / (name + '.png')).write_bytes(raw)
    actual_root, raw, pins, shape = runner.load_inputs(root)
    assert actual_root == root and raw == originals and shape == (32, 32)
    assert all(pins[name]['sha256'] == hashlib.sha256(content).hexdigest() for name, content in originals.items())
    (root / 'labels.png').write_bytes(originals['valid'])
    failure('input_invalid', runner.load_inputs, root)
