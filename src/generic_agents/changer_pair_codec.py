"""Pinned local GeoTIFF alignment followed by true learned Changer inference.

Internal candidate path only; not yet enabled as a published business capability.
Historical RGB comparison recipes and results are never reinterpreted.
"""
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .config import settings
from .contracts import digest
from .geo_codec import ROOT, file_digest, call as geo_call
from .geo_pair_codec import call as align_pair, RECIPES as ALIGN_RECIPES, ready as align_ready

RECIPE = json.loads((ROOT / 'runtime-packs/geo-changer-candidate-v2.json').read_text())
RECIPE_HASH = digest(RECIPE)


def _run(command, root, env, guard, timeout, stem):
    with (root / (stem + '.json')).open('w+') as out, (root / (stem + '.log')).open('w+') as err:
        child = subprocess.Popen(command, env=env, cwd=root, stdout=out, stderr=err, start_new_session=True)
        started = time.monotonic()
        try:
            while child.poll() is None:
                guard()
                if time.monotonic() - started > timeout:
                    raise ValueError('本地变化处理超时；保留原件，不转云')
                time.sleep(.2)
            guard()
            if child.returncode:
                err.seek(0); lines = err.read()[-1000:].splitlines()
                if lines and lines[-1].startswith('ValueError: '):
                    raise ValueError(lines[-1].removeprefix('ValueError: '))
                raise ValueError('本地变化模型或空间处理失败；原件保留，不转云')
            out.seek(0); raw = out.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError('本地变化元数据超过额度')
            return json.loads(raw)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)


def ready(weights=None):
    if (RECIPE['default_deployment'] != 'local' or RECIPE['cloud_fallback'] is not False
            or RECIPE['network'] is not False):
        raise ValueError('Changer local deployment policy differs from the frozen recipe')
    if digest(ALIGN_RECIPES['geo-pair-v2']) != RECIPE['alignment_recipe_hash']:
        raise ValueError('local Changer alignment recipe changed')
    align_ready('geo-pair-v2')
    python = settings().specialist_python.absolute()
    if not python.is_file():
        raise ValueError('本地变化检测环境尚未安装；不会切换云端')
    for name, expected in RECIPE['files'].items():
        if file_digest(Path(__file__).with_name(name)) != expected:
            raise ValueError('本地变化检测代码与固定配方不一致')
    weight = Path(weights) if weights is not None else ROOT / '.runtime/changer-candidate/changer-r18.safetensors'
    if not weight.is_file() or weight.stat().st_size != RECIPE['weights']['bytes'] or file_digest(weight) != RECIPE['weights']['sha256']:
        raise ValueError('本地变化模型未准备或摘要改变；原件保留，不转云')
    return python, Path(__file__).with_name('local_changer_pair_runner.py')


def call(before, after, request, output_dir, guard=lambda: None, *, weights=None):
    from .local_specialists import node_lock
    with node_lock():
        return _call(before, after, request, output_dir, guard, weights=weights)


def _call(before, after, request, output_dir, guard, *, weights=None):
    guard()
    output = Path(output_dir)
    if not output.is_dir() or any(output.iterdir()):
        raise ValueError('变化结果目录必须为空；不覆盖历史结果')
    threshold = request.get('probability_threshold')
    if type(threshold) not in {int, float} or not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('请设置0至1之间的变化概率阈值，不使用RGB差异阈值')
    python, runner = ready(weights) if weights is not None else ready()
    source_hashes = [file_digest(path) for path in (before, after)]
    # The old alignment runner produces its own RGB baseline as intermediate
    # evidence; its candidate/difference outputs are not used as learned output.
    alignment_request = {key: request[key] for key in (
        'roi', 'alignment', 'before_configuration', 'after_configuration', 'after_allowed_window')}
    alignment_request['threshold'] = 1
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               PYTORCH_ENABLE_MPS_FALLBACK='0', PROJ_NETWORK='OFF')
    with tempfile.TemporaryDirectory(prefix='ga-changer-pair-') as temporary:
        root = Path(temporary)
        aligned, result_dir, spatial_dir = root / 'aligned', root / 'learned', root / 'spatial'
        aligned.mkdir(); result_dir.mkdir(); spatial_dir.mkdir()
        metadata = align_pair(before, after, alignment_request, aligned, guard, recipe_id='geo-pair-v2')
        command = [str(python), str(runner), '--input', str(aligned), '--out', str(result_dir), '--threshold', str(threshold)]
        if weights is not None:
            command.extend(['--weights', str(Path(weights).absolute())])
        learned = _run(command, root, env, guard, RECIPE['timeout_seconds'], 'native')
        validate_result(learned, metadata, aligned, threshold)
        spatial = {key: metadata[key] for key in ('roi', 'alignment', 'resampling', 'registration_accuracy',
                   'radiometry', 'after_read_window', 'after_allowed_window', 'transform', 'crs', 'crs_wkt')}
        export_request = root / 'spatial-request.json'
        export_request.write_text(json.dumps({**spatial, **learned}, allow_nan=False))
        exported = _run([str(settings().geo_python.absolute()), str(Path(__file__).with_name('local_changer_geo_exporter.py')),
                         '--aligned', str(aligned), '--learned', str(result_dir), '--request', str(export_request),
                         '--out', str(spatial_dir)], root, env, guard, 60, 'geospatial')
        if (not isinstance(exported, dict) or exported.get('schema') != 'local-changer-geospatial-export-v1'
                or exported.get('exact_probability_readback') is not True or exported.get('mask_readback') is not True
                or exported.get('cloud_calls') != 0
                or any(exported.get(key) != learned[key] for key in ('total_pixels', 'common_valid_pixels', 'unknown_pixels', 'candidate_pixels'))):
            raise ValueError('geospatial export receipt differs from saved learned evidence')
        if source_hashes != [file_digest(path) for path in (before, after)]:
            raise ValueError('变化检测期间原件改变，结果未接受')
        geometry, geometry_issue = None, None
        try:
            geometry = geo_call('geometry', result_dir / 'candidate.png', {
                'metadata': {'georeference_status': 'affine_available', 'map_image_confirmed': True,
                             'transform': metadata['transform'], 'crs_wkt': metadata['crs_wkt']},
                'bbox': [0, 0, metadata['width'], metadata['height']]}, guard=guard)
        except ValueError as error:
            guard(); geometry_issue = str(error)[:1000]
        guard()
        ready(weights) if weights is not None else ready()
        for name in ('before', 'after', 'valid'):
            shutil.copyfile(aligned / (name + '.png'), output / (name + '.png'))
        for name in ('probability.png', 'probability.npy', 'candidate.png'):
            shutil.copyfile(result_dir / name, output / name)
        for name in ('probability.tif', 'change-mask.tif'):
            shutil.copyfile(spatial_dir / name, output / name)
        for path in output.iterdir():
            path.chmod(0o600)
    return {**spatial, **learned, 'runtime_hash': RECIPE_HASH, 'source_sha256': source_hashes,
            'geometry': geometry, 'geometry_issue': geometry_issue, 'geospatial_export': exported,
            'business_capability_enabled': False,
            'artifacts': {path.name: {'sha256': file_digest(path), 'bytes': path.stat().st_size}
                          for path in sorted(Path(output_dir).iterdir())}}


def validate_result(value, metadata, aligned_dir, threshold):
    if (not isinstance(value, dict) or value.get('schema') != 'local-changer-comparison-v1'
            or value.get('learned_model') is not True or value.get('cloud_calls') != 0
            or value.get('backend') != 'cpu' or value.get('candidate_only') is not True
            or value.get('algorithm') != RECIPE['algorithm']
            or value.get('weights_sha256') != RECIPE['weights']['sha256']
            or value.get('graph_sha256') != RECIPE['files']['local_changer_model.py']
            or value.get('probability_threshold') != threshold
            or value.get('semantics') != 'learned_binary_change_candidates_only'
            or value.get('domain_quality') != 'not_verified'
            or value.get('probability_calibration') != 'not_verified'
            or value.get('object_state_updated') is not False):
        raise ValueError('本地学习型变化检测来源声明不匹配')
    for key in ('width', 'height', 'total_pixels', 'common_valid_pixels', 'unknown_pixels'):
        if type(value.get(key)) is not int or value[key] != metadata[key]:
            raise ValueError('变化结果与共同有效区域不一致')
    if type(value.get('candidate_pixels')) is not int or not 0 <= value['candidate_pixels'] <= value['common_valid_pixels']:
        raise ValueError('变化候选像素数无效')
    expected = {name: file_digest(Path(aligned_dir) / (name + '.png')) for name in ('before', 'after', 'valid')}
    if value.get('aligned_input_sha256') != expected:
        raise ValueError('变化结果不属于本次配准影像')
    low, high = value.get('probability_min'), value.get('probability_max')
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in (low, high)) or not 0 <= low <= high <= 1:
        raise ValueError('变化概率范围无效')
