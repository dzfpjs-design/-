"""Bounded candidate adapter, never silently replaces a registered business model."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from .contracts import digest
from .local_audio import file_sha, validate as validate_common

ROOT = Path(__file__).resolve().parents[2]
RECIPE = json.loads((ROOT/'model-packs/qwen3-asr-0.6b-hf-v1.json').read_text())
RECIPE_HASH = digest(RECIPE)
PYTHON = ROOT/'.runtime/venvs/qwen-asr/bin/python'
WEIGHTS = ROOT/'.runtime/qwen-asr-bootstrap/weights'
RUNNER = Path(__file__).with_name('local_qwen_audio_runner.py')


def ready():
    if not PYTHON.is_file(): raise ValueError('本地专用ASR环境未准备，不会转云')
    if file_sha(RUNNER) != RECIPE['runner_sha256'] or file_sha(RUNNER.with_name('local_audio_runner.py')) != RECIPE['decoder_sha256']:
        raise ValueError('本地专用ASR代码与固定配方不同')
    for item in RECIPE['files']:
        p = WEIGHTS/item['name']
        if p.is_symlink() or not p.is_file() or p.stat().st_size != item['bytes'] or file_sha(p) != item['sha256']:
            raise ValueError('本地专用ASR文件与作者固定版本不一致：'+item['name'])


def validate(result, content, configuration):
    validate_common(result, content, configuration)
    model = result.get('model',{})
    if configuration != {'channel':'mono','language':'zh'} or result['audio']['sample_rate'] != 16000 or result['audio']['duration_seconds']>30:
        raise ValueError('专用ASR输入规格与已核验范围不同')
    if (model.get('pack_id') != RECIPE['pack_id'] or model.get('revision') != RECIPE['revision']
        or model.get('recipe_sha256') != RECIPE_HASH or model.get('dtype') != 'float32'
        or model.get('native_architecture') is not True or model.get('remote_code') is not False
        or model.get('attention_implementation') != RECIPE['attention_implementation']
        or model.get('context_or_hotwords_used') is not False): raise ValueError('专用ASR执行来源不符合配方')


def call(content, configuration, guard=lambda:None, on_started=lambda pid:None):
    if configuration != {'channel':'mono','language':'zh'}:
        raise ValueError('专用ASR候选目前仅核验单通道普通话')
    ready()
    env = {k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
        PYTORCH_ENABLE_MPS_FALLBACK='0',TORCH_FORCE_WEIGHTS_ONLY_LOAD='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',
        TQDM_DISABLE='1',HF_HUB_DISABLE_PROGRESS_BARS='1')
    with tempfile.TemporaryDirectory(prefix='ga-qwen-asr-') as directory:
        d = Path(directory); (d/'original.bin').write_bytes(content); (d/'request.json').write_text(json.dumps(configuration))
        with (d/'stdout').open('w+b') as output, (d/'stderr').open('w+b') as errors:
            child = subprocess.Popen([str(PYTHON),str(RUNNER),'--weights',str(WEIGHTS),
                '--audio',str(d/'original.bin'),'--request',str(d/'request.json')],stdout=output,stderr=errors,env=env)
            start = time.monotonic()
            try:
                on_started(child.pid)
                while child.poll() is None:
                    guard()
                    if time.monotonic()-start>180: raise ValueError('本地专用ASR超过180秒，保留原件，不会转云')
                    if output.tell()>256*1024 or errors.tell()>1024*1024: raise ValueError('专用ASR进程输出超限')
                    time.sleep(.1)
                guard(); output.seek(0); raw = output.read(256*1024+1)
                if child.returncode:
                    errors.seek(0); lines=errors.read(1024*1024).decode(errors='replace').splitlines()
                    raise ValueError('本地专用ASR失败：'+(lines[-1][:500] if lines else '进程退出；不转云'))
                if len(raw)>256*1024: raise ValueError('专用ASR输出超限')
                result=json.loads(raw)
            finally:
                if child.poll() is None: child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
    validate(result,content,configuration); return result
