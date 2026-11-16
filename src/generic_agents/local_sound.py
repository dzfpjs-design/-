"""Credential-free YAMNet execution and the existing specialist installation lifecycle."""
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
import signal
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import wave

from fastapi import HTTPException
from .contracts import digest
from .runtime_paths import local_runtime_root, validate_local_runtime_root
from .local_audio_paths import _python_path

ROOT = Path(__file__).resolve().parents[2]
RECIPE = json.loads((ROOT / 'model-packs/yamnet-mediapipe-v1.json').read_text())
RECIPE_HASH = digest(RECIPE)
PACK = RECIPE['pack_id']
RUNNER = Path(__file__).with_name('local_sound_runner.py')
DECODER = Path(__file__).with_name('local_audio_runner.py')
_SANDBOX = Path('/usr/bin/sandbox-exec')
CHANNELS = {'mono', 'mix', *map(str, range(1, 9))}


@dataclass(frozen=True)
class SoundPaths:
    root: Path
    python: Path
    weights: Path


def paths():
    root = local_runtime_root()
    return SoundPaths(root, _python_path(root / 'venvs/yamnet-litert/bin/python'),
                      validate_local_runtime_root(root / 'sound-bootstrap/weights'))


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def configuration(value):
    if (not isinstance(value, dict) or set(value) != {'channel', 'threshold'}
            or not isinstance(value['channel'], str) or value['channel'] not in CHANNELS
            or isinstance(value['threshold'], bool) or not isinstance(value['threshold'], (int, float))
            or not math.isfinite(value['threshold']) or not 0 < value['threshold'] < 1):
        raise ValueError('请选择明确声道和0到1之间的候选分数门槛')
    return value


def _runtime_ready(selected):
    if sys.platform != 'darwin' or not _SANDBOX.is_file():
        raise ValueError('本节点尚未验证声音进程的操作系统网络隔离，不转云或降级隔离')
    _python_path(selected.python)
    validate_local_runtime_root(selected.weights)
    if not selected.python.is_file():
        raise ValueError('本地环境声音运行环境未准备，请重新执行声音模型安装')
    if (not RUNNER.is_file() or RUNNER.is_symlink() or file_sha(RUNNER) != RECIPE['runner_sha256']
            or not DECODER.is_file() or DECODER.is_symlink() or file_sha(DECODER) != RECIPE['decoder_sha256']):
        raise ValueError('声音运行器或受限解码器与固定配方不一致')


def runtime_available():
    """Read-only path/isolation check; installed() still requires an actual native probe."""
    try:
        _runtime_ready(paths())
        return True
    except (HTTPException, OSError, ValueError, RuntimeError):
        return False


def ready(*, _paths=None):
    selected = paths() if _paths is None else _paths
    _runtime_ready(selected)
    for item in RECIPE['files']:
        path = selected.weights / item['name']
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != item['bytes']
                or file_sha(path) != item['sha256']):
            raise ValueError('环境声音模型或类别文件缺失或摘要不符：' + item['name'])
    return selected


def clean_environment(directory):
    # Do not pass credentials, proxy variables, search paths, user HOME or package configuration.
    return {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': os.environ.get('LANG', 'C.UTF-8'),
            'HOME': str(directory), 'TMPDIR': str(directory), 'TMP': str(directory), 'TEMP': str(directory),
            'XDG_CACHE_HOME': str(directory / 'cache'), 'PYTHONNOUSERSITE': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
            'MKL_NUM_THREADS': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
            'PIP_CONFIG_FILE': '/dev/null', 'NETRC': '/dev/null', 'PIP_KEYRING_PROVIDER': 'disabled'}


def _invoke(content, selected_configuration, mode, guard, on_started, selected):
    configuration(selected_configuration)
    if not isinstance(content, bytes) or not 0 < len(content) <= RECIPE['limits']['max_bytes']:
        raise ValueError('原音为空或超过20MB，不能截断处理')
    ready(_paths=selected) if mode == 'classify' else _runtime_ready(selected)
    guard()
    maximum = RECIPE['limits']['max_result_bytes'] if mode == 'classify' else 65536
    with tempfile.TemporaryDirectory(prefix='ga-sound-') as name:
        directory = Path(name).resolve()
        os.chmod(directory, 0o700)
        (directory / 'cache').mkdir(mode=0o700)
        original = directory / 'original.bin'
        original.write_bytes(content)
        original.chmod(0o600)
        request = directory / 'request.json'
        request.write_text(json.dumps(selected_configuration, allow_nan=False))
        request.chmod(0o600)
        command = [str(_SANDBOX), '-p', '(version 1)(allow default)(deny network*)',
                   str(selected.python), '-I', '-B', str(RUNNER), '--mode', mode,
                   '--audio', str(original), '--request', str(request)]
        if mode == 'classify':
            command.extend(['--weights', str(selected.weights)])
        with (directory / 'stdout').open('w+b') as output, (directory / 'stderr').open('w+b') as errors:
            guard()
            child = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=errors, env=clean_environment(directory))
            started = time.monotonic()
            try:
                on_started(child.pid)
                while child.poll() is None:
                    guard()
                    if time.monotonic() - started > RECIPE['limits']['timeout_seconds']:
                        raise ValueError('声音处理超过180秒，原音保留，可核对后重试')
                    if os.fstat(output.fileno()).st_size > maximum or os.fstat(errors.fileno()).st_size > 1024 * 1024:
                        raise ValueError('声音进程输出超限，未接受不完整结果')
                    time.sleep(.1)
                guard()
                output.seek(0)
                raw = output.read(maximum + 1)
                if child.returncode != 0:
                    # Do not expose native stack traces, server paths or environment contents.
                    raise ValueError('本地声音解码或模型检查失败；原音保留，请核对格式、声道及固定运行环境')
                if not raw or len(raw) > maximum:
                    raise ValueError('声音结果为空或超限')
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ValueError('声音结果不是完整JSON') from None
            finally:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
    return result


def _number(value, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < minimum or maximum is not None and value > maximum):
        raise ValueError('声音结果中的数值无效')
    return value


def validate_metadata(audio, channel):
    expected = {'sample_rate', 'channels', 'samples', 'duration_seconds', 'selected_channel', 'rms',
                'clipped_fraction', 'peak', 'silence_detected', 'channel_policy', 'noise_quality', 'source_time'}
    if not isinstance(audio, dict) or set(audio) != expected:
        raise ValueError('声音原始格式记录不完整')
    for name, low, high in [('sample_rate', 8000, 96000), ('channels', 1, 8), ('samples', 1, 5760000)]:
        if type(audio[name]) is not int or not low <= audio[name] <= high:
            raise ValueError('声音采样率、声道或采样数无效')
    if (audio['selected_channel'] != channel or channel not in CHANNELS
            or channel == 'mono' and audio['channels'] != 1
            or channel not in {'mono', 'mix'} and int(channel) > audio['channels']):
        raise ValueError('声音记录未采用明确选择的原声道')
    duration = _number(audio['duration_seconds'], .25, 60)
    if duration != audio['samples'] / audio['sample_rate']:
        raise ValueError('声音时长与完整采样数不一致')
    _number(audio['rms']); _number(audio['peak']); _number(audio['clipped_fraction'], 0, 1)
    if (type(audio['silence_detected']) is not bool or audio['silence_detected'] != (audio['rms'] < 1e-5)
            or audio['channel_policy'] != 'explicit' or audio['noise_quality'] != 'not_measured'
            or audio['source_time'] != 'unknown'):
        raise ValueError('声音质量或未知录制时间记录无效')
    return audio


def metadata(content, channel='mono', guard=lambda: None, *, _paths=None):
    selected = paths() if _paths is None else _paths
    result = _invoke(content, {'channel': channel, 'threshold': .5}, 'metadata', guard, lambda pid: None, selected)
    if (not isinstance(result, dict) or set(result) != {'schema', 'input_sha256', 'audio'}
            or result['schema'] != 'local-sound-metadata-v1'
            or result['input_sha256'] != hashlib.sha256(content).hexdigest()):
        raise ValueError('原音检查结果与本次原件不一致')
    return validate_metadata(result['audio'], channel)


def validate(result, content, selected_configuration):
    configuration(selected_configuration)
    keys = {'schema', 'deployment', 'backend', 'cloud_calls', 'input_sha256', 'recipe_hash', 'model',
            'audio', 'classification', 'elapsed_seconds'}
    if (not isinstance(result, dict) or set(result) != keys or result['schema'] != 'local-environment-sound-v1'
            or result['deployment'] != 'local' or result['backend'] != 'cpu' or type(result['cloud_calls']) is not int
            or result['cloud_calls'] != 0 or result['input_sha256'] != hashlib.sha256(content).hexdigest()
            or result['recipe_hash'] != RECIPE_HASH):
        raise ValueError('声音结果的原件、模型配方或本地执行范围不一致')
    _number(result['elapsed_seconds'], 0, 180)
    model = result['model']
    mk = {'pack_id', 'model_sha256', 'labels_sha256', 'labels_count', 'labels', 'native_input', 'native_output',
          'metadata', 'quantized_tensors', 'weight_precision', 'runtime_versions'}
    if not isinstance(model, dict) or set(model) != mk:
        raise ValueError('声音模型追溯记录不完整')
    labels = model['labels']
    if (not isinstance(labels, list) or len(labels) != 521 or any(not isinstance(x, str) or not x or len(x) > 300 for x in labels)
            or len(set(labels)) != 521 or hashlib.sha256(('\n'.join(labels) + '\n').encode()).hexdigest() != RECIPE['files'][1]['sha256']):
        raise ValueError('声音类别顺序或原始标签摘要无效')
    expected_model = {'pack_id': PACK, 'model_sha256': RECIPE['files'][0]['sha256'],
                      'labels_sha256': RECIPE['files'][1]['sha256'], 'labels_count': 521,
                      'native_input': {'name': 'waveform_binary', 'shape': [15600], 'dtype': 'float32'},
                      'native_output': {'name': 'tower0/network/layer32/final_output', 'shape': [1, 521], 'dtype': 'float32'},
                      'metadata': {key: RECIPE['license_evidence'][key] for key in ['name', 'author', 'version', 'license']},
                      'quantized_tensors': 89, 'weight_precision': 'internally_quantized_not_all_fp32'}
    if digest({key: model[key] for key in expected_model}) != digest(expected_model):
        raise ValueError('声音模型形状、标签、元数据或内部量化声明不符')
    versions = model['runtime_versions']
    if (not isinstance(versions, dict) or set(versions) != {*RECIPE['environment'], 'flatbuffers'}
            or any(versions.get(k) != v for k, v in RECIPE['environment'].items())
            or not isinstance(versions['flatbuffers'], str) or not 1 <= len(versions['flatbuffers']) <= 80):
        raise ValueError('声音运行依赖版本不一致')
    audio = result['audio']
    analysis = {'analysis_sample_rate', 'analysis_samples', 'resampling'}
    if not isinstance(audio, dict) or not analysis <= audio.keys():
        raise ValueError('声音重采样记录不完整')
    validate_metadata({key: value for key, value in audio.items() if key not in analysis}, selected_configuration['channel'])
    samples = audio['analysis_samples']
    if (type(samples) is not int or not 1 <= samples <= 960000 or type(audio['analysis_sample_rate']) is not int
            or audio['analysis_sample_rate'] != 16000 or abs(samples / 16000 - audio['duration_seconds']) > 2 / 16000
            or audio['resampling'] != ('original_16000_no_resample' if audio['sample_rate'] == 16000 else 'pyav_audioresampler_fltp_16000')
            or audio['sample_rate'] == 16000 and samples != audio['samples']):
        raise ValueError('声音重采样长度、时间或策略不一致')
    c = result['classification']
    fixed = {'sample_rate': 16000, 'window_samples': 15600, 'hop_samples': 7680,
             'threshold': selected_configuration['threshold'], 'top_k': 5,
             'score_semantics': 'uncalibrated_classification_score', 'timing': 'analysis_windows_not_event_boundaries',
             'inference_performed': True, 'all_samples_covered': True, 'vad_used': False, 'asr_used': False,
             'domain_quality': 'not_verified'}
    if (not isinstance(c, dict) or set(c) != {*fixed, 'windows', 'candidates'}
            or digest({key: c[key] for key in fixed}) != digest(fixed)):
        raise ValueError('环境声音完整推理、分数或窗口语义不一致')
    positions = []
    start = 0
    while True:
        positions.append((start, min(samples, start + 15600)))
        if start + 15600 >= samples: break
        start += 7680
    windows = c['windows']
    if not isinstance(windows, list) or len(windows) != len(positions) or len(windows) > 124:
        raise ValueError('声音窗口未完整覆盖原音或重复处理了末尾')
    grouped = {}
    for index, (item, (start, end)) in enumerate(zip(windows, positions)):
        expected = {'index': index, 'start_sample': start, 'end_sample': end, 'padded_samples': 15600 - (end - start),
                    'start_seconds': start / 16000, 'end_seconds': end / 16000}
        if (not isinstance(item, dict) or set(item) != {*expected, 'scores', 'top_classes'}
                or digest({key: item[key] for key in expected}) != digest(expected)):
            raise ValueError('声音候选窗口边界或补零量不一致')
        scores = item['scores']
        if not isinstance(scores, list) or len(scores) != 521:
            raise ValueError('声音窗口未保留全部521个原始分数')
        for score in scores:
            _number(score, 0, 1)
            if struct.unpack('<f', struct.pack('<f', score))[0] != score:
                raise ValueError('声音分数不是原始float32数值')
        order = sorted(range(521), key=lambda i: (-scores[i], i))[:5]
        top = [{'class_index': i, 'label': labels[i], 'score': scores[i]} for i in order]
        if digest(item['top_classes']) != digest(top):
            raise ValueError('声音首选类别与原始分数或稳定顺序不一致')
        for i, score in enumerate(scores):
            if score >= selected_configuration['threshold']:
                entry = grouped.setdefault(i, {'class_index': i, 'label': labels[i], 'max_score': score, 'window_indices': []})
                entry['max_score'] = max(entry['max_score'], score)
                entry['window_indices'].append(index)
    if digest(c['candidates']) != digest([grouped[i] for i in sorted(grouped)]):
        raise ValueError('声音候选聚合与全部原始窗口分数不一致')
    return result


def call(content, selected_configuration, guard=lambda: None, on_started=lambda pid: None, *, _paths=None):
    selected = paths() if _paths is None else _paths
    result = _invoke(content, selected_configuration, 'classify', guard, on_started, selected)
    return validate(result, content, selected_configuration)


def execute(content, selected_configuration, guard=lambda: None, on_started=lambda pid: None, *, _paths=None):
    return call(content, selected_configuration, guard, on_started, _paths=_paths)


def probe():
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 15600)
    return raw.getvalue()


def trace(row):
    return {'installation_id': row.id, 'pack_id': PACK, 'generation': row.data['generation'],
            'recipe_hash': RECIPE_HASH, 'backend': 'cpu', 'model_sha256': RECIPE['files'][0]['sha256'],
            'labels_sha256': RECIPE['files'][1]['sha256']}


def checked_trace(db, p, saved_trace, *, live=False):
    """Cold-check one original installation generation, without renewing old authorization.

    Historical reads deliberately do not require current ready state, generation,
    role, runtime or files. Fresh execution additionally requires installed()'s
    current row/bytes checks and this exact immutable queue/Job/ready chain.
    """
    from sqlalchemy import select
    from .models import SpecialistInstallation, Job, Audit
    def need(value):
        if not value:
            raise HTTPException(409, '声音模型的原安装任务或独立检查凭据无法核对')
    keys = {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'backend', 'model_sha256', 'labels_sha256'}
    need(isinstance(saved_trace, dict) and set(saved_trace) == keys
         and isinstance(saved_trace.get('installation_id'), str) and saved_trace['installation_id']
         and type(saved_trace.get('generation')) is int and saved_trace['generation'] >= 1)
    identity = saved_trace['installation_id']; generation = saved_trace['generation']
    expected = {'installation_id': identity, 'pack_id': PACK, 'generation': generation,
                'recipe_hash': RECIPE_HASH, 'backend': 'cpu', 'model_sha256': RECIPE['files'][0]['sha256'],
                'labels_sha256': RECIPE['files'][1]['sha256']}
    need(digest(saved_trace) == digest(expected))
    installation = db.scalar(select(SpecialistInstallation).where(
        SpecialistInstallation.workspace_id == p.workspace_id, SpecialistInstallation.id == identity)
        .execution_options(populate_existing=True))
    need(installation is not None and installation.workspace_id == p.workspace_id and installation.pack_id == PACK)
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'install_specialist',
        Job.idempotency_key == 'specialist-install:' + identity + ':' + str(generation)).limit(2)))
    need(len(jobs) == 1)
    job = jobs[0]
    need(job.workspace_id == p.workspace_id and job.kind == 'install_specialist' and job.status == 'succeeded'
         and job.idempotency_key == 'specialist-install:' + identity + ':' + str(generation)
         and isinstance(job.data, dict) and job.data.get('installation_id') == identity
         and type(job.data.get('generation')) is int and job.data['generation'] == generation
         and isinstance(job.data.get('actor_id'), str) and job.data['actor_id'])
    actor_id = job.data['actor_id']
    queued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'specialist.install.queued', Audit.object_id == identity,
        Audit.data['generation'].as_string() == str(generation)).limit(2)))
    completed = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'sound.install.ready', Audit.object_id == identity,
        Audit.data['generation'].as_string() == str(generation)).limit(2)))
    need(len(queued) == 1 and len(completed) == 1)
    queued_audit = queued[0]; ready_audit = completed[0]
    source = queued_audit.data.get('source') if isinstance(queued_audit.data, dict) else None
    need(isinstance(source, str) and source in {'official', 'prepared_local'})
    expected_queue = {'source': source, 'recipe_hash': RECIPE_HASH, 'installation_id': identity,
                      'generation': generation, 'job_id': job.id, 'backend': 'cpu'}
    expected_ready = {'recipe_hash': RECIPE_HASH, 'generation': generation,
                      'artifact_hashes': {item['name']: item['sha256'] for item in RECIPE['files']},
                      'engineering_probe_only': True}
    for proof, action, body in [(queued_audit, 'specialist.install.queued', expected_queue),
                                (ready_audit, 'sound.install.ready', expected_ready)]:
        need(proof.workspace_id == p.workspace_id and proof.object_id == identity and proof.action == action
             and proof.actor_id == actor_id and digest(proof.data) == digest(body))
    if live:
        need(installation.status == 'ready' and isinstance(installation.data, dict)
             and type(installation.data.get('generation')) is int and installation.data['generation'] == generation
             and installation.data.get('recipe_hash') == RECIPE_HASH and installation.data.get('backend') == 'cpu'
             and installation.data.get('source') == source and installation.data.get('job_id') == job.id
             and installation.data.get('artifact_hashes') == expected_ready['artifact_hashes']
             and isinstance(installation.data.get('smoke'), dict)
             and installation.data['smoke'].get('inference_performed') is True
             and installation.data.get('smoke', {}).get('runner_sha256') == RECIPE['runner_sha256'])
        try:
            ready()
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from None
    proof = {'schema': 'local-sound-installation-proof-v1', 'workspace_id': p.workspace_id,
             **expected, 'install_job_id': job.id, 'installer_id': actor_id, 'source': source,
             'queued_audit_id': queued_audit.id, 'queued_audit_hash': digest(queued_audit.data),
             'ready_audit_id': ready_audit.id, 'ready_audit_hash': digest(ready_audit.data)}
    return {'installation': installation, 'job': job, 'queued_audit': queued_audit, 'ready_audit': ready_audit, 'proof': proof}


def installed(db, p, identity=None):
    from sqlalchemy import select
    from .models import SpecialistInstallation
    from .services import scoped
    row = scoped(db, SpecialistInstallation, identity, p) if identity else db.scalar(select(SpecialistInstallation).where(
        SpecialistInstallation.workspace_id == p.workspace_id, SpecialistInstallation.pack_id == PACK))
    if (row is None or row.pack_id != PACK or row.status != 'ready' or row.data.get('recipe_hash') != RECIPE_HASH
            or row.data.get('backend') != 'cpu' or row.data.get('source') not in {'official', 'prepared_local'}
            or type(row.data.get('generation')) is not int or row.data['generation'] < 1
            or row.data.get('artifact_hashes') != {x['name']: x['sha256'] for x in RECIPE['files']}
            or not isinstance(row.data.get('smoke'), dict)
            or row.data['smoke'].get('inference_performed') is not True
            or row.data.get('smoke', {}).get('runner_sha256') != RECIPE['runner_sha256']):
        raise HTTPException(409, '请先安装并实际检查本地环境声音模型')
    checked_trace(db, p, trace(row), live=True)
    return row


def _download(item, directory, guard, progress):
    import httpx
    directory = validate_local_runtime_root(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / item['name']
    if target.is_symlink(): raise ValueError('声音模型目录不能使用符号链接')
    if target.is_file() and target.stat().st_size == item['bytes'] and file_sha(target) == item['sha256']:
        progress('hash_check', file=item['name'], downloaded=item['bytes'], total=item['bytes'], cache_verified=True)
        return
    part = directory / (item['name'] + '.part')
    if part.is_symlink(): raise ValueError('声音模型临时文件不能使用符号链接')
    # Fixed Google URLs only. Redirects, mirrors, ambient proxies and credentials are disabled.
    expected_urls = {entry['name']: entry['url'] for entry in RECIPE['files']}
    if item['url'] != expected_urls.get(item['name']) or not item['url'].startswith('https://storage.googleapis.com/'):
        raise ValueError('声音模型下载不属于固定官方来源')
    guard()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(30, connect=8)) as client:
        with client.stream('GET', item['url']) as response:
            response.raise_for_status()
            if response.status_code != 200: raise ValueError('声音模型官方响应不完整')
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(part, flags, 0o600), 'wb') as stream:
                size = 0
                for block in response.iter_bytes(256 * 1024):
                    guard(); size += len(block)
                    if size > item['bytes']: raise ValueError('声音模型下载超过固定长度')
                    stream.write(block)
                    progress('downloading', file=item['name'], downloaded=size, total=item['bytes'])
                stream.flush(); os.fsync(stream.fileno())
    guard()
    if part.stat().st_size != item['bytes'] or file_sha(part) != item['sha256']:
        raise ValueError('声音模型下载摘要不符，保留文件供核对，不加载')
    os.replace(part, target)
    descriptor = os.open(directory, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _prepare_runtime(selected, guard, progress):
    if selected.python.is_file() and not (selected.root / 'sound-bootstrap/runtime-preparing.json').is_file(): return
    if selected.python != selected.root / 'venvs/yamnet-litert/bin/python':
        raise ValueError('声音运行环境不属于显式组件目录，不自动改装')
    script = ROOT / 'scripts/prepare_local_sound.py'
    if script.is_symlink() or not script.is_file() or file_sha(script) != RECIPE['runtime_preparer_sha256']:
        raise ValueError('声音依赖安装脚本与固定配方不一致')
    with tempfile.TemporaryDirectory(prefix='ga-sound-runtime-') as name:
        directory = Path(name).resolve(); os.chmod(directory, 0o700)
        with (directory / 'log').open('w+b') as log:
            env = clean_environment(directory)
            child = subprocess.Popen([sys.executable, '-I', '-B', str(script), '--runtime-root', str(selected.root)],
                                     stdout=log, stderr=log, cwd=directory, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
            started = time.monotonic()
            try:
                while child.poll() is None:
                    guard()
                    if time.monotonic() - started > 1300 or os.fstat(log.fileno()).st_size > 2 * 1024 * 1024:
                        raise ValueError('声音运行环境安装超时或输出超限，保留已有文件')
                    time.sleep(.2)
                guard()
                if child.returncode != 0:
                    raise ValueError('声音隔离环境未能完成固定依赖安装，请核对安装记录后重试')
            finally:
                if child.poll() is None:
                    try: os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait(timeout=5)
    progress('runtime_ready', runtime_native_import_verified=False)


def process_install(db, p, job):
    from .models import SpecialistInstallation
    from .services import scoped, require, audit
    from .local_specialists import guard_record, node_lock
    from .leases import ownership
    require(p, 'admin')
    row = scoped(db, SpecialistInstallation, job.data['installation_id'], p, True)
    generation = job.data['generation']
    if row.status in {'ready', 'cancelled'} or row.data.get('generation') != generation: return
    if (job.kind != 'install_specialist' or ownership(db).get('job_id') != job.id
            or job.data.get('actor_id') != p.user_id or row.data.get('job_id') != job.id
            or row.pack_id != PACK or row.data.get('recipe_hash') != RECIPE_HASH
            or row.data.get('source') not in {'official', 'prepared_local'} or row.data.get('backend') != 'cpu'):
        raise ValueError('声音安装不是当前固定CPU包及真实安装租约')
    selected = paths()
    identity = row.id
    def guard(): return guard_record(db, p, SpecialistInstallation, identity, generation)
    def progress(stage, **value):
        current = guard(); current.status = 'installing'; current.version += 1
        current.data = {**current.data, 'stage': stage, 'progress': value}; db.commit()
    with node_lock():
        progress('runtime_check')
        _prepare_runtime(selected, guard, progress)
        progress('license_check', artifact_license='Apache-2.0', domain_quality='not_verified')
        if row.data['source'] == 'official':
            for item in RECIPE['files']: _download(item, selected.weights, guard, progress)
        ready(_paths=selected)
        progress('loading')
        content = probe()
        result = call(content, {'channel': 'mono', 'threshold': .5}, guard=guard,
                      on_started=lambda pid: progress('model_check', pid=pid), _paths=selected)
        current = guard(); current.status = 'ready'; current.version += 1
        current.data = {**current.data, 'stage': 'smoke_check', 'error': None, 'domain_evaluation': 'pending',
                        'artifact_hashes': {item['name']: item['sha256'] for item in RECIPE['files']},
                        'smoke': {'backend': 'cpu', 'deployment': 'local', 'inference_performed': True, 'cloud_calls': 0,
                                  'probe_kind': 'synthetic_silence_engineering_only', 'probe_sha256': hashlib.sha256(content).hexdigest(),
                                  'all_samples_covered': True, 'model': result['model'], 'result_hash': digest(result),
                                  'elapsed_seconds': result['elapsed_seconds'], 'runner_sha256': RECIPE['runner_sha256'],
                                  'domain_quality_verified': False, 'embedded_model_license': result['model']['metadata']['license']}}
        audit(db, p, 'sound.install.ready', current.id, {'recipe_hash': RECIPE_HASH, 'generation': generation,
                                                     'artifact_hashes': current.data['artifact_hashes'], 'engineering_probe_only': True})
        db.commit()
