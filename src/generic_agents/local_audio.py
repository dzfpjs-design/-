"""Credential-free bounded local ASR, authoritative originals and lease cancellation."""
import hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from fastapi import HTTPException
from .config import settings
from .contracts import digest
from .models import AudioRun,Evidence
from .evidence import evidence_bytes
from .services import scoped,audit
from .local_specialists import guard_record,process_record,node_lock
from .local_audio_paths import audio_paths

ROOT=Path(__file__).resolve().parents[2]
RECIPE=json.loads((ROOT/'model-packs/sensevoice-small-v1.json').read_text())
RECIPE_HASH=digest(RECIPE)
RUNNER=Path(__file__).with_name('local_audio_runner.py')
_SANDBOX=Path('/usr/bin/sandbox-exec')

def file_sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def ready(*,_paths=None):
    paths=audio_paths() if _paths is None else _paths
    if sys.platform!='darwin' or not _SANDBOX.is_file():raise ValueError('本节点尚未验证ASR操作系统网络隔离，不能自动改为无隔离或云端运行')
    if not paths.python.is_file():raise ValueError('本地语音环境未准备，不能自动转云')
    if file_sha(RUNNER)!=RECIPE['runner_sha256']:raise ValueError('语音运行器摘要与固定配方不一致')
    for x in RECIPE['files']:
        p=paths.weights/x['name']
        if not p.is_file() or p.is_symlink() or p.stat().st_size!=x['bytes'] or file_sha(p)!=x['sha256']:raise ValueError('本地语音文件缺失或摘要不符：'+x['name'])

def call(content,configuration,guard=lambda:None,on_started=lambda pid:None,*,_paths=None):
    paths=audio_paths() if _paths is None else _paths
    ready(_paths=paths);env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',MODELSCOPE_OFFLINE='1',PYTORCH_ENABLE_MPS_FALLBACK='0',TORCH_FORCE_WEIGHTS_ONLY_LOAD='1',NUMBA_NUM_THREADS='4',OMP_NUM_THREADS='4')
    with tempfile.TemporaryDirectory(prefix='ga-audio-') as directory:
        d=Path(directory).resolve();os.chmod(d,0o700);cache=d/'cache';cache.mkdir(mode=0o700)
        env.update(HOME=str(d),TMPDIR=str(d),TMP=str(d),TEMP=str(d),XDG_CACHE_HOME=str(cache),HF_HOME=str(cache/'hf'),HUGGINGFACE_HUB_CACHE=str(cache/'hf/hub'),TORCH_HOME=str(cache/'torch'),MODELSCOPE_CACHE=str(cache/'modelscope'),NUMBA_CACHE_DIR=str(cache/'numba'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
        (d/'original.bin').write_bytes(content);(d/'request.json').write_text(json.dumps(configuration))
        with (d/'stdout').open('w+b') as output,(d/'stderr').open('w+b') as errors:
            command=[str(_SANDBOX),'-p','(version 1)(allow default)(deny network*)',str(paths.python),'-B',str(RUNNER),'--weights',str(paths.weights),'--audio',str(d/'original.bin'),'--request',str(d/'request.json')]
            child=subprocess.Popen(command,stdout=output,stderr=errors,env=env,cwd=d,stdin=subprocess.DEVNULL);start=time.monotonic()
            try:
                on_started(child.pid)
                while child.poll() is None:
                    guard()
                    if time.monotonic()-start>180:raise ValueError('本机语音处理超过180秒，原件保留，可缩短音频后重试')
                    if output.tell()>256*1024 or errors.tell()>1024*1024:raise ValueError('语音进程输出超限')
                    time.sleep(.1)
                guard();output.seek(0);raw=output.read(256*1024+1)
                if child.returncode!=0:
                    errors.seek(0);message=errors.read(1024*1024).decode(errors='replace').splitlines()
                    raise ValueError('本机语音处理失败：'+(message[-1][:500] if message else '子进程退出；不会转云'))
                if len(raw)>256*1024:raise ValueError('语音结果超限')
                try:result=json.loads(raw)
                except (ValueError,UnicodeError):raise ValueError('语音结果不是完整JSON')
            finally:
                if child.poll() is None:child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait()
    validate(result,content,configuration);return result

def validate(result,content,configuration):
    import math
    if result.get('schema')!='local-audio-transcription-v1' or result.get('deployment')!='local' or result.get('backend')!='cpu' or result.get('cloud_calls')!=0 or result.get('input_sha256')!=hashlib.sha256(content).hexdigest():raise ValueError('语音来源或本地执行契约不一致')
    audio=result.get('audio',{});t=result.get('transcription',{})
    if not isinstance(t.get('text'),str) or len(t['text'])>12000 or t.get('word_timestamps') is not False or t.get('speaker_identity') is not False or t.get('environment_classifier_used') is not False:raise ValueError('音频转写边界不符合契约')
    for key in ['duration_seconds','rms','clipped_fraction','peak']:
        if not isinstance(audio.get(key),(int,float)) or isinstance(audio[key],bool) or not math.isfinite(audio[key]):raise ValueError('音频质量记录无效')
    if not .25<=audio['duration_seconds']<=60 or audio['selected_channel']!=configuration['channel'] or t.get('language_requested')!=configuration['language']:raise ValueError('音频长度、通道或语言与确认配置不符')
    if audio['samples']/audio['sample_rate']!=audio['duration_seconds'] or len(t.get('segments',[]))!=1 or t['segments'][0]!={'start_seconds':0,'end_seconds':audio['duration_seconds'],'text':t['text'],'timing':'input_clip_only'}:raise ValueError('音频时间边界或采样数不一致')

def checked(db,p,row):
    stored=scoped(db,Evidence,row.evidence_id,p)
    expected=p.workspace_id+'/'+stored.sha256+'.bin'
    original=settings().storage_dir/expected
    if stored.storage_key!=expected or original.is_symlink() or not original.is_file() or original.stat().st_size!=stored.size_bytes or not 0<stored.size_bytes<=20*1024*1024:raise HTTPException(409,'音频原件路径、长度或保存状态不一致')
    ev,content=evidence_bytes(db,p,row.evidence_id)
    if len(content)!=ev.size_bytes:raise HTTPException(409,'音频原件长度校验失败')
    if ev.sha256!=row.data['input_sha256'] or row.data['recipe_hash']!=RECIPE_HASH:raise HTTPException(409,'音频原件或处理配方与保存记录不一致')
    if row.status=='succeeded':
        if digest(row.data['result'])!=row.data['result_hash']:raise HTTPException(409,'音频结果摘要不一致')
        try:validate(row.data['result'],content,row.data['configuration'])
        except (ValueError,KeyError,TypeError,ZeroDivisionError) as e:raise HTTPException(409,'已保存语音结果未通过一致性检查') from e
    return ev,content

def _process(db,p,job):
    row=scoped(db,AudioRun,job.data['run_id'],p,True);generation=job.data['generation']
    if row.status in {'cancelled','succeeded'} or row.data['generation']!=generation:return
    if row.data.get('installation'):
        from .local_audio_install import installed,trace
        saved=row.data['installation'];current=installed(db,p,saved['installation_id'])
        if trace(current)!=saved:raise ValueError('任务固定的语音安装版本已变化，保留原件，不替换模型')
    _,content=checked(db,p,row);identity=row.id;config=row.data['configuration']
    row.status='running';row.version+=1;row.data={**row.data,'stage':'local_decode_and_transcribe'};db.commit()
    with node_lock():result=call(content,config,lambda:guard_audio(db,p,identity,generation))
    row=guard_audio(db,p,identity,generation);row.status='succeeded';row.version+=1
    row.data={**row.data,'result':result,'result_hash':digest(result),'stage':'awaiting_human_review','error':None}
    audit(db,p,'audio.transcription.saved',row.id,{'input_sha256':row.data['input_sha256'],'recipe_hash':RECIPE_HASH,'cloud_calls':0});db.commit()

def process(db,p,job):return process_record(db,p,job,AudioRun,'run_id',_process)

def guard_audio(db,p,identity,generation):
    row=guard_record(db,p,AudioRun,identity,generation)
    if row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import guard_record as guard_graph_record
        return guard_graph_record(db,p,row)
    if row.data.get('workflow_task_id'):
        from .models import Task
        task=scoped(db,Task,row.data['workflow_task_id'],p)
        if task.status=='workflow_cancelled':
            row.status='cancelled';row.version+=1;row.data={**row.data,'stage':'cancelled_with_business'};db.commit();raise ValueError('原业务已取消，音频与处理记录保留')
        if task.status!='workflow_running' or task.data['workflow_runtime'].get('automatic',{}).get('job_id')!=row.data['workflow_job_id']:raise ValueError('原业务节点或处理租约已变化，音频结果不接受')
    return row
