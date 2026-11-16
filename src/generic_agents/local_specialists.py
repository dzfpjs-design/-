"""Immutable local model packs, isolated inference, durable installation/checkpoints."""
import fcntl
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import HTTPException
from .config import settings
from .contracts import digest
from .evidence import evidence_bytes
from .models import SpecialistInstallation, SpecialistRun
from .services import scoped, audit
from .security import require

ROOT = Path(__file__).resolve().parents[2]
RECIPE = json.loads((ROOT / 'model-packs/rtdetr-v2-r18.json').read_text())
RECIPE_HASH = digest(RECIPE)
SAM_RECIPE = json.loads((ROOT / 'model-packs/sam2-1-tiny.json').read_text())
AUDIO_RECIPE = json.loads((ROOT / 'model-packs/sensevoice-small-v1.json').read_text())
VAD_RECIPE = json.loads((ROOT / 'model-packs/silero-vad-onnx-v1.json').read_text())
CHANGER_RECIPE = json.loads((ROOT / 'model-packs/changer-r18-binary-v1.json').read_text())
SOUND_RECIPE = json.loads((ROOT / 'model-packs/yamnet-mediapipe-v1.json').read_text())
RECIPES = {r['pack_id']:r for r in [RECIPE,SAM_RECIPE,AUDIO_RECIPE,VAD_RECIPE,CHANGER_RECIPE,SOUND_RECIPE]}


def recipe_for(row):
    return RECIPES[row.pack_id] if isinstance(row,SpecialistInstallation) else RECIPES[row.data.get('pack_id',RECIPE['pack_id'])]
SOURCES = {'official': 'https://huggingface.co', 'mirror': 'https://hf-mirror.com'}


def weights_dir(recipe=RECIPE):
    return settings().specialist_dir / digest(recipe) / 'weights'


def verify_files(path,recipe=RECIPE):
    for item in recipe['files']:
        file = path / item['name']
        if not file.is_file() or file.stat().st_size != item['bytes']:
            raise ValueError('模型文件缺失或长度不符：' + item['name'])
        if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('模型文件摘要不符：' + item['name'])
    config = json.loads((path / 'config.json').read_text())
    if config.get('model_type') != ('rt_detr_v2' if recipe==RECIPE else 'sam2_video') or config.get('auto_map') or config.get('use_timm_backbone'):
        raise ValueError('模型结构与固定配方不一致')


@contextmanager
def node_lock():
    root = settings().specialist_dir
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'node.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('本地推理节点正在处理其他任务，请稍后重试')
        try:
            yield lock.fileno()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def download_pack(source, progress,recipe=RECIPE):
    if source not in SOURCES:
        raise ValueError('未登记的模型下载来源')
    path = weights_dir() if recipe==RECIPE else weights_dir(recipe)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with httpx.Client(timeout=httpx.Timeout(30, connect=8), follow_redirects=True, trust_env=False) as client:
        for item in recipe['files']:
            file = path / item['name']
            if file.is_file() and file.stat().st_size == item['bytes'] and hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256']:
                progress('hash_check', file=item['name'], downloaded=item['bytes'], total=item['bytes'])
                continue
            part = path / (item['name'] + '.part')
            offset = part.stat().st_size if part.exists() else 0
            if offset >= item['bytes']:
                # A complete stale/bad part is not a verified artifact; retry it from byte zero.
                offset = 0
            url = SOURCES[source] + '/' + recipe['repository'] + '/resolve/' + recipe['revision'] + '/' + item['name']
            headers = {'Range': f'bytes={offset}-'} if offset else {}
            with client.stream('GET', url, headers=headers) as response:
                response.raise_for_status()
                if offset and response.status_code == 206:
                    if response.headers.get('content-range', '').split('/')[0] != f'bytes {offset}-{item["bytes"]-1}':
                        raise ValueError('断点下载范围不一致，已保留下载文件')
                else:
                    offset = 0
                with part.open('ab' if offset else 'wb') as out:
                    size = offset
                    reported = size
                    for chunk in response.iter_bytes(256 * 1024):
                        size += len(chunk)
                        if size > item['bytes']:
                            raise ValueError('下载内容超出固定配方大小')
                        out.write(chunk)
                        if size - reported >= 1024 * 1024 or size == item['bytes']:
                            progress('downloading', file=item['name'], downloaded=size, total=item['bytes'])
                            reported = size
                    out.flush()
                    os.fsync(out.fileno())
            if part.stat().st_size != item['bytes'] or hashlib.sha256(part.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('下载文件摘要不匹配，未启用；重试将重新下载该文件')
            os.replace(part, file)
            progress('hash_check', file=item['name'], downloaded=item['bytes'], total=item['bytes'])
    verify_files(path) if recipe==RECIPE else verify_files(path,recipe)
    return path


def execute(path, backend, threshold=.5, content=None, guard=lambda: None,recipe=RECIPE):
    verify_files(path) if recipe==RECIPE else verify_files(path,recipe)
    # Resolving a venv's python symlink would execute the base interpreter without its packages.
    python = settings().specialist_python.absolute()
    if not python.is_file():
        raise ValueError('本地专业模型隔离运行环境尚未安装')
    # Never inherit model credentials or permit lazy downloads in the specialist child.
    env = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'PYTORCH_ENABLE_MPS_FALLBACK': '0'})
    with tempfile.TemporaryDirectory(prefix='ga-local-infer-') as temporary:
        temp = Path(temporary)
        command = [str(python), str(Path(__file__).with_name('local_detector_runner.py' if recipe==RECIPE else 'local_segmentation_runner.py')),
                   '--weights', str(path.resolve()), '--backend', backend, '--threshold', str(threshold)]
        if content is not None:
            image = temp / 'input.bin'
            image.write_bytes(content)
            command += ['--image', str(image)]
        with (temp / 'output.json').open('w+') as out, (temp / 'error.log').open('w+') as err:
            proc = subprocess.Popen(command, stdout=out, stderr=err, env=env, cwd=temp, start_new_session=True)
            started = time.monotonic()
            try:
                while proc.poll() is None:
                    if time.monotonic() - started > 180:
                        raise ValueError('本地模型推理超时，原件保留，可重试')
                    guard()
                    time.sleep(.3)
                guard()
                if proc.returncode:
                    err.seek(0)
                    reason = err.read()[-2000:]
                    if 'MPS is unavailable' in reason:
                        raise ValueError('本机MPS不可用，请明确选择CPU重试')
                    raise ValueError('本地模型加载或推理失败；未切换云端')
                out.seek(0)
                result = json.loads(out.read(recipe.get('max_output_bytes',256*1024)+1))
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=3)
    if recipe==RECIPE:validate_output(result, backend, threshold, content)
    else:
        from .segmentation import validate_result
        validate_result(result,backend,threshold,content)
    return result


def validate_output(result, backend, threshold, content):
    if result.get('schema') != 'local-detection-v1' or result.get('backend') != backend or result.get('cloud_calls') != 0:
        raise ValueError('专业输出契约不匹配')
    if result.get('threshold') != threshold or result.get('image_sha256') != (hashlib.sha256(content).hexdigest() if content is not None else None):
        raise ValueError('专业输出不属于本次输入')
    width, height = result.get('width'), result.get('height')
    if not isinstance(width, int) or not isinstance(height, int) or not 0 < width * height <= 16000000:
        raise ValueError('专业输出尺寸无效')
    rows = result.get('detections')
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError('专业输出目标数量无效')
    for row in rows:
        score = row.get('score'); box = row.get('bbox')
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score) or not threshold <= score <= 1:
            raise ValueError('专业输出分数无效')
        if not isinstance(box, list) or len(box) != 4 or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in box):
            raise ValueError('专业输出位置无效')
        if not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height:
            raise ValueError('专业输出超出原图范围')
        if not isinstance(row.get('label_id'), int) or isinstance(row['label_id'], bool) or not 0 <= row['label_id'] < 80 or not isinstance(row.get('label'), str):
            raise ValueError('专业输出类别无效')


def guard_record(db, p, cls, object_id, generation):
    db.expire_all()
    row = scoped(db, cls, object_id, p, True)
    if row.data.get('generation') != generation or row.status == 'cancelled':
        raise ValueError('本次处理已取消或被新版本替代')
    if cls.__name__ == 'VideoRun':
        from sqlalchemy import select
        from .models import StreamSession
        from .frame_supplement_v236 import execution_guard, need
        sessions = list(db.scalars(select(StreamSession).where(StreamSession.workspace_id == p.workspace_id,
            StreamSession.video_run_id == row.id).limit(3)))
        context = row.data.get('source_context')
        context_session_id = context.get('session_id') if isinstance(context, dict) else None
        session_ids = {session.id for session in sessions}
        if isinstance(context_session_id, str) and context_session_id:
            session_ids.add(context_session_id)
        for session_id in session_ids:
            if execution_guard(db, p, session_id=session_id):
                need(len(sessions) == 1 and sessions[0].id == session_id
                    and context_session_id == session_id)
    db.commit()  # Lease and actor checks run here; release locks before waiting.
    return row


def _process_install(db, p, job):
    require(p, 'admin')
    row = scoped(db, SpecialistInstallation, job.data['installation_id'], p, True)
    generation = job.data['generation']
    if row.status == 'ready' or row.status == 'cancelled' or row.data.get('generation') != generation:
        return
    if row.pack_id==SOUND_RECIPE['pack_id']:
        from .local_sound import process_install
        return process_install(db,p,job)
    if row.pack_id==CHANGER_RECIPE['pack_id']:
        from .local_changer_install import process
        return process(db,p,job)
    if row.pack_id==VAD_RECIPE['pack_id']:
        from .local_vad_install import process
        return process(db,p,job)
    if row.pack_id==AUDIO_RECIPE['pack_id']:
        from .local_audio_install import process
        return process(db,p,job)
    recipe=recipe_for(row)
    if row.data['recipe_hash'] != digest(recipe):
        raise ValueError('安装配方已变化，须重新核对')
    object_id = row.id
    def progress(stage, **data):
        current = guard_record(db, p, SpecialistInstallation, object_id, generation)
        current.status = 'installing'; current.version += 1
        current.data = {**current.data, 'stage': stage, 'progress': data}
        db.commit()
    with node_lock():
        progress('license_check')
        path = download_pack(row.data['source'], progress) if recipe==RECIPE else download_pack(row.data['source'], progress,recipe)
        progress('loading')
        backend = row.data['backend']
        kwargs={} if recipe==RECIPE else {'recipe':recipe}
        result = execute(path, backend, guard=lambda: guard_record(db, p, SpecialistInstallation, object_id, generation),**kwargs)
        current = guard_record(db, p, SpecialistInstallation, object_id, generation)
        current.status = 'ready'; current.version += 1
        current.data = {**current.data, 'stage': 'smoke_check', 'smoke': {k:v for k,v in result.items() if k!='masks'},
                        'artifact_hashes': {f['name']: f['sha256'] for f in recipe['files']},
                        'error': None, 'domain_evaluation': 'pending'}
        audit(db, p, 'specialist.install.ready', current.id, {'recipe_hash': digest(recipe), 'backend': backend})
        db.commit()


def ready_install(db, p, object_id):
    row = scoped(db, SpecialistInstallation, object_id, p)
    db.refresh(row)
    if row.pack_id==SOUND_RECIPE['pack_id']:
        from .local_sound import installed
        return installed(db,p,row.id)
    if row.pack_id==CHANGER_RECIPE['pack_id']:
        from .local_changer_install import installed
        return installed(db,p,row.id)
    if row.pack_id==VAD_RECIPE['pack_id']:
        from .local_vad_install import installed
        return installed(db,p,row.id)
    if row.pack_id==AUDIO_RECIPE['pack_id']:
        from .local_audio_install import installed
        return installed(db,p,row.id)
    recipe=recipe_for(row)
    if row.status != 'ready' or row.data.get('recipe_hash') != digest(recipe):
        raise HTTPException(409, '本地模型尚未安装就绪或配方已升级')
    try:
        verify_files(weights_dir()) if recipe==RECIPE else verify_files(weights_dir(recipe),recipe)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return row


def detect(db, p, object_id, evidence_id, threshold=.5):
    row = ready_install(db, p, object_id)
    if row.pack_id==CHANGER_RECIPE['pack_id']:raise HTTPException(422,'变化模型需要双影像输入，不能作为单图识别模型调用')
    if row.pack_id in {AUDIO_RECIPE['pack_id'],VAD_RECIPE['pack_id'],SOUND_RECIPE['pack_id']}:raise HTTPException(422,'语音包不接受图像检测输入')
    ev, content = evidence_bytes(db, p, evidence_id)
    if ev.data.get('modality') != 'image':
        raise HTTPException(422, '此专业包只处理PNG或JPEG图像')
    db.commit()
    recipe=recipe_for(row)
    with node_lock():
        result = execute(weights_dir(),row.data['backend'],threshold,content) if recipe==RECIPE else execute(weights_dir(recipe),row.data['backend'],threshold,content,recipe=recipe)
    if (result['width'], result['height']) != (ev.data['width'], ev.data['height']):
        raise ValueError('专业输出尺寸与原件不一致')
    if recipe!=RECIPE:
        from .segmentation import save_masks
        result=save_masks(db,p,result,ev)
    return result


def _process_run(db, p, job):
    row = scoped(db, SpecialistRun, job.data['run_id'], p, True)
    generation = job.data['generation']
    if row.status in {'succeeded', 'cancelled'} or row.data.get('generation') != generation:
        return
    installation = ready_install(db, p, row.installation_id)
    if installation.pack_id==CHANGER_RECIPE['pack_id']:raise HTTPException(422,'变化模型不能执行单图样例任务')
    if installation.pack_id==SOUND_RECIPE['pack_id']:raise HTTPException(422,'环境声音包需要保存的原音频，不能执行单图样例任务')
    recipe=recipe_for(installation)
    ev, content = evidence_bytes(db, p, row.evidence_id)
    if ev.sha256 != row.data['input_sha256'] or ev.data.get('modality') != 'image':
        raise ValueError('原件或输入模态变化，未继续推理')
    if installation.data['recipe_hash'] != row.data['recipe_hash'] or installation.data['backend'] != row.data['backend']:
        raise ValueError('专业模型版本或后端变化，须重新测试')
    object_id = row.id
    row.status = 'running'; row.version += 1; db.commit()
    with node_lock():
        kwargs={} if recipe==RECIPE else {'recipe':recipe}
        result = execute(weights_dir() if recipe==RECIPE else weights_dir(recipe), row.data['backend'], row.data['threshold'], content,
                         guard=lambda: guard_record(db, p, SpecialistRun, object_id, generation),**kwargs)
    if (result['width'], result['height']) != (ev.data['width'], ev.data['height']):
        raise ValueError('专业输出尺寸与原件不一致')
    current = guard_record(db, p, SpecialistRun, object_id, generation)
    ready_install(db, p, current.installation_id)
    evidence_bytes(db, p, current.evidence_id)
    if recipe != RECIPE:
        from .segmentation import save_masks
        result = save_masks(db, p, result, ev)
    current.status = 'succeeded'; current.version += 1
    current.data = {**current.data, 'result': result, 'result_hash': digest(result), 'error': None}
    audit(db, p, 'specialist.run.saved', current.id, {'result_hash': digest(result), 'cloud_calls': 0})
    db.commit()


def process_record(db, p, job, cls, key, handler):
    try:
        return handler(db, p, job)
    except (ValueError, HTTPException, httpx.HTTPError, OSError) as error:
        if isinstance(error, httpx.HTTPError):
            reason = '模型下载来源连接失败。下载文件保留，可核对来源后重试；不会自动切换镜像或云推理。'
        elif isinstance(error, OSError):
            import errno
            reason = ({errno.ENOSPC:'本机磁盘空间不足',errno.EACCES:'本机文件或运行环境权限不足',errno.EPERM:'本机文件或运行环境权限不足',errno.ENOENT:'本机固定文件或隔离依赖缺失'}.get(error.errno,'本机文件或进程操作失败')+'。原请求与已有文件保留，请核对节点后重试；不会自动转云。')
        elif isinstance(error, HTTPException):
            reason = str(error.detail)[:1000]
        else:
            reason = str(error)[:1000]
        current = scoped(db, cls, job.data[key], p, True)
        if current.data.get('generation') == job.data['generation'] and current.status in {'queued', 'installing', 'running'}:
            current.status = 'failed'; current.version += 1
            current.data = {**current.data, 'error': reason}
            audit(db, p, 'specialist.processing.failed', current.id, {'reason': reason})
            db.commit()
        raise


def process_install(db, p, job):
    return process_record(db, p, job, SpecialistInstallation, 'installation_id', _process_install)


def process_run(db, p, job):
    return process_record(db, p, job, SpecialistRun, 'run_id', _process_run)


def ready_detection_install(db, p, object_id):
    row = ready_install(db, p, object_id)
    if row.pack_id != RECIPE['pack_id']:
        raise HTTPException(422, '此视频处理路径需要已就绪的本地目标检测包，实例掩膜包不支持视频跟踪')
    return row
