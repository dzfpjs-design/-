"""Bounded local subprocess bridge; no API-environment NumPy or cloud fallback."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .config import settings
from .contracts import digest
from .local_specialists import node_lock


class DiagnosticRuntimeError(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code;self.message=message


def adapter_hash():
    return digest({name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ['pixel_diagnostic_bridge_v257.py','pixel_diagnostic_runner_v257.py']})


def _call(payload,files=None):
    configured=settings().pixel_diagnostic_python
    if configured is None or not configured.absolute().is_file():
        raise DiagnosticRuntimeError('runtime_unavailable','本地诊断环境尚未配置，需由管理员完成部署；原方案保留。')
    python=configured.absolute();root=Path(__file__).resolve().parents[1]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',
        OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    with tempfile.TemporaryDirectory(prefix='ga-pixel-diagnostic-v257-') as folder:
        directory=Path(folder);directory.chmod(0o700)
        raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>131072:raise DiagnosticRuntimeError('scoring_failed','诊断参数超过大小上限，原方案保留。')
        source=directory/'request.json';source.write_bytes(raw);source.chmod(0o600)
        if files is not None:
            if set(files)!={'probability.npy','labels.png','valid.png'}:
                raise DiagnosticRuntimeError('scoring_failed','诊断输入文件不完整，原方案保留。')
            for name,content in files.items():
                if type(content) is not bytes or not 0<len(content)<=64_004_096:
                    raise DiagnosticRuntimeError('scoring_failed','诊断原件大小不完整或超过上限，原方案保留。')
                path=directory/name;path.write_bytes(content);path.chmod(0o600)
        out=directory/'response.json'
        try:
            with (directory/'stdout.log').open('wb') as stdout,(directory/'stderr.log').open('wb') as stderr:
                stdout_path=directory/'stdout.log';stderr_path=directory/'stderr.log';stdout_path.chmod(0o600);stderr_path.chmod(0o600)
                result=subprocess.run([str(python),'-m','generic_agents.pixel_diagnostic_runner_v257',
                    '--input',str(source),'--out',str(out)],env=env,cwd=directory,
                    stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=30)
            if result.returncode or not out.is_file() or not 0<out.stat().st_size<=131072:
                raise DiagnosticRuntimeError('scoring_failed','本地诊断处理失败，原方案保留，可检查环境后恢复。')
            value=json.loads(out.read_bytes())
            if type(value) is not dict or type(value.get('ok')) is not bool:
                raise DiagnosticRuntimeError('scoring_failed','本地诊断返回未通过完整核对，原方案保留。')
            if not value['ok']:
                if set(value)!={'ok','code','message'} or value['code'] not in {'runtime_unavailable','evaluator_changed','scoring_failed'}:
                    raise DiagnosticRuntimeError('scoring_failed','本地诊断返回未通过完整核对，原方案保留。')
                raise DiagnosticRuntimeError(value['code'],value['message'])
            if set(value)!={'ok','value'}:raise ValueError('unknown native keys')
            return value['value']
        except subprocess.TimeoutExpired:
            raise DiagnosticRuntimeError('scoring_failed','本地诊断处理超时，原方案保留；没有调用云模型。') from None
        except (OSError,ValueError) as error:
            if isinstance(error,DiagnosticRuntimeError):raise
            raise DiagnosticRuntimeError('scoring_failed','本地诊断环境或输出文件不可用，原方案保留。') from None


def probe():
    value=_call({'operation':'probe'})
    if (type(value) is not dict or set(value)!={'protocol_sha256','quality_core_sha256','numpy_version','pillow_version'}
            or any(type(item) is not str or not item for item in value.values())):
        raise DiagnosticRuntimeError('runtime_unavailable','本地诊断环境指纹不完整，原方案保留。')
    for key in ['protocol_sha256','quality_core_sha256']:
        if len(value[key])!=64 or any(c not in '0123456789abcdef' for c in value[key]):
            raise DiagnosticRuntimeError('runtime_unavailable','本地诊断环境指纹不完整，原方案保留。')
    return value


def freeze_manifest(binding,baseline,candidate):
    from .pixel_diagnostic_protocol_v257 import checked_manifest
    return checked_manifest(_call({'operation':'freeze','binding':binding,
        'baseline_threshold_text':baseline,'candidate_threshold_text':candidate}))


def evaluate(manifest,probability_raw,labels_png,valid_png):
    from .pixel_diagnostic_protocol_v257 import checked_manifest,checked_result
    manifest=checked_manifest(manifest)
    if probe()!=manifest['evaluator']:
        raise DiagnosticRuntimeError('evaluator_changed','本地计算版本已改变；保留原方案，明确另存新方案后再计算。')
    try:
        with node_lock():
            return checked_result(manifest,_call({'operation':'evaluate','manifest':manifest},
                {'probability.npy':probability_raw,'labels.png':labels_png,'valid.png':valid_png}))
    except DiagnosticRuntimeError:
        raise
    except (ValueError,OSError) as error:
        message=('本地计算节点正在处理其他任务，请稍后明确重试本方案。' if '正在处理其他任务' in str(error)
                 else '本地计算节点不可用或结果未通过核对；原方案保留，没有调用云模型。')
        raise DiagnosticRuntimeError('scoring_failed',message) from None
