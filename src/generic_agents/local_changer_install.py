"""Durable local prepared-checkpoint installation; no pickle or remote fallback."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from .contracts import digest
from .models import SpecialistInstallation
from .services import scoped, require, audit
from .local_specialists import weights_dir, guard_record, node_lock
from .changer_pair_codec import ROOT, RECIPE as PIPELINE, RECIPE_HASH as PIPELINE_HASH, ready, _run
from .geo_codec import file_digest

RECIPE = json.loads((ROOT / 'model-packs/changer-r18-binary-v1.json').read_text())
RECIPE_HASH = digest(RECIPE)
PACK = RECIPE['pack_id']


def model_path():
    return weights_dir(RECIPE) / RECIPE['files'][0]['name']


def safe_path(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('本地变化模型路径不能使用符号链接')
    return path


def verify(path):
    path = safe_path(path)
    item = RECIPE['files'][0]
    if not path.is_file() or path.stat().st_size != item['bytes'] or file_digest(path) != item['sha256']:
        raise ValueError('本地变化模型缺失或摘要不符；不转云')
    if RECIPE['pipeline_hash'] != PIPELINE_HASH or file_digest(Path(__file__)) != RECIPE['installer_sha256']:
        raise ValueError('本地变化安装或处理配方改变，旧记录保留')
    ready(path)
    return path


def prepare(progress, guard):
    source = safe_path(ROOT / '.runtime/changer-candidate/changer-r18.safetensors')
    receipt = json.loads((ROOT / '.runtime/changer-candidate/conversion.json').read_text())
    if (receipt.get('original_sha256') != PIPELINE['original']['sha256']
            or receipt.get('safetensors_sha256') != RECIPE['files'][0]['sha256']
            or receipt.get('weights_only') is not True or receipt.get('all_tensors_exact_readback') is not True
            or receipt.get('unsafe_fallback_used') is not False):
        raise ValueError('本地变化模型准备回执未核验；请由运维准备固定文件，不转云')
    verify(source)
    target = safe_path(model_path()); target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard(); progress('hash_check', source='prepared_local', production_license_verified=False)
    if target.is_file():
        verify(target); return target
    with tempfile.NamedTemporaryFile(prefix='changer-copy-', dir=target.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            with source.open('rb') as input_file:
                while block := input_file.read(1024 * 1024):
                    guard(); out.write(block)
            out.flush(); os.fsync(out.fileno()); temporary.chmod(0o600)
            verify(temporary); guard(); os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return verify(target)


def probe(path, guard):
    python, runner = ready(path)
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL'}}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTORCH_ENABLE_MPS_FALLBACK='0')
    with tempfile.TemporaryDirectory(prefix='ga-changer-install-') as temporary:
        root = Path(temporary); inputs = root / 'inputs'; output = root / 'out'; inputs.mkdir(); output.mkdir()
        for name in ('before', 'after'):
            Image.new('RGB', (64, 64), (120, 130, 140)).save(inputs / (name + '.png'))
        Image.new('L', (64, 64), 255).save(inputs / 'valid.png')
        result = _run([str(python), str(runner), '--input', str(inputs), '--out', str(output),
                       '--threshold', '.5', '--weights', str(path)], root, env, guard, 60, 'smoke')
        if (result.get('schema') != 'local-changer-comparison-v1' or result.get('learned_model') is not True
                or result.get('cloud_calls') != 0 or result.get('backend') != 'cpu'
                or result.get('weights_sha256') != RECIPE['files'][0]['sha256']
                or result.get('common_valid_pixels') != 4096 or result.get('unknown_pixels') != 0):
            raise ValueError('本机变化模型加载检查未产生固定来源的真实结果')
    return {'backend': 'cpu', 'deployment': 'local', 'inference_performed': True, 'cloud_calls': 0,
            'probe_kind': 'synthetic_identical_RGB_engineering_only', 'weights_sha256': result['weights_sha256'],
            'pipeline_hash': PIPELINE_HASH, 'production_license_verified': False, 'domain_quality_verified': False}


def process(db, p, job):
    require(p, 'admin'); row = scoped(db, SpecialistInstallation, job.data['installation_id'], p, True)
    generation = job.data['generation']
    if row.status in {'ready', 'cancelled'} or row.data.get('generation') != generation:
        return
    if row.pack_id != PACK or row.data.get('recipe_hash') != RECIPE_HASH or row.data.get('source') != 'prepared_local' or row.data.get('backend') != 'cpu':
        raise ValueError('变化安装不属于固定本地CPU准备包')
    identity = row.id
    def guard():
        return guard_record(db, p, SpecialistInstallation, identity, generation)
    def progress(stage, **value):
        current = guard(); current.status = 'installing'; current.version += 1
        current.data = {**current.data, 'stage': stage, 'progress': value}; db.commit()
    with node_lock():
        progress('prepared_artifact_check')
        path = prepare(progress, guard)
        progress('loading'); smoke = probe(path, guard)
        current = guard(); verify(path); current.status = 'ready'; current.version += 1
        current.data = {**current.data, 'stage': 'smoke_check', 'smoke': smoke, 'error': None,
                        'artifact_hashes': {item['name']: item['sha256'] for item in RECIPE['files']},
                        'domain_evaluation': 'pending', 'pipeline_hash': PIPELINE_HASH,
                        'paired_workflow_registration': 'pending'}
        audit(db, p, 'changer.install.ready', identity, {'recipe_hash': RECIPE_HASH, 'generation': generation,
              'engineering_probe_only': True}); db.commit()


def installed(db, p, identity):
    row = scoped(db, SpecialistInstallation, identity, p)
    if (row.pack_id != PACK or row.status != 'ready' or row.data.get('recipe_hash') != RECIPE_HASH
            or row.data.get('pipeline_hash') != PIPELINE_HASH or not row.data.get('smoke', {}).get('inference_performed')):
        raise HTTPException(409, '请先完成本地变化模型安装与加载检查')
    try:
        verify(model_path())
    except (OSError, ValueError) as error:
        raise HTTPException(409, str(error))
    return row


def trace(row):
    return {'installation_id': row.id, 'pack_id': PACK, 'generation': row.data['generation'],
            'recipe_hash': RECIPE_HASH, 'pipeline_hash': PIPELINE_HASH,
            'weights_sha256': RECIPE['files'][0]['sha256'], 'backend': 'cpu'}
