"""Pinned, CPU-only VAD with OS network isolation, cancellation and source checks."""
import hashlib,json,math,os,subprocess,sys,tempfile,time
from pathlib import Path
from fastapi import HTTPException
from .contracts import digest
from .models import SpeechActivityRun,Evidence
from .evidence import evidence_bytes
from .services import scoped,audit
from .local_specialists import guard_record,node_lock,process_record
from .local_audio_paths import vad_paths
ROOT=Path(__file__).resolve().parents[2];RECIPE=json.loads((ROOT/'model-packs/silero-vad-onnx-v1.json').read_text());RECIPE_HASH=digest(RECIPE);RUNNER=Path(__file__).with_name('local_vad_runner.py')
_SANDBOX=Path('/usr/bin/sandbox-exec')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def ready(*,_paths=None):
 paths=vad_paths() if _paths is None else _paths
 if sys.platform!='darwin' or not _SANDBOX.is_file():raise ValueError('本节点尚未验证VAD操作系统网络隔离，不能自动改为无隔离或云端运行')
 if not paths.python.is_file():raise ValueError('本地VAD独立环境未准备，不能转云')
 if sha(RUNNER)!=RECIPE['runner_sha256'] or sha(RUNNER.with_name('local_audio_runner.py'))!=RECIPE['decoder_sha256'] or sha(RUNNER.with_name('speech_activity.py'))!=RECIPE['aggregation_sha256']:raise ValueError('VAD运行器或固定解码器摘要已变化')
 for x in RECIPE['files']:
  f=paths.weights/x['name']
  if not f.is_file() or f.is_symlink() or f.stat().st_size!=x['bytes'] or sha(f)!=x['sha256']:raise ValueError('VAD固定文件缺失或摘要不符：'+x['name'])

def call(content,configuration,guard=lambda:None,on_started=lambda pid:None,*,_paths=None):
 paths=vad_paths() if _paths is None else _paths
 ready(_paths=paths);env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
 env.update(OMP_NUM_THREADS='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
 with tempfile.TemporaryDirectory(prefix='ga-vad-') as directory:
  d=Path(directory).resolve();os.chmod(d,0o700);cache=d/'cache';cache.mkdir(mode=0o700)
  env.update(HOME=str(d),TMPDIR=str(d),TMP=str(d),TEMP=str(d),XDG_CACHE_HOME=str(cache),HF_HOME=str(cache/'hf'),HUGGINGFACE_HUB_CACHE=str(cache/'hf/hub'),TORCH_HOME=str(cache/'torch'),MODELSCOPE_CACHE=str(cache/'modelscope'),NUMBA_CACHE_DIR=str(cache/'numba'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
  (d/'original.bin').write_bytes(content);(d/'request.json').write_text(json.dumps(configuration))
  with (d/'stdout').open('w+b') as output,(d/'stderr').open('w+b') as errors:
   cmd=[str(_SANDBOX),'-p','(version 1)(allow default)(deny network*)',str(paths.python),'-B',str(RUNNER),'--weights',str(paths.weights),'--audio',str(d/'original.bin'),'--request',str(d/'request.json')]
   child=subprocess.Popen(cmd,stdout=output,stderr=errors,env=env,cwd=d,stdin=subprocess.DEVNULL);started=time.monotonic()
   try:
    on_started(child.pid)
    while child.poll() is None:
     guard()
     if time.monotonic()-started>120:raise ValueError('本地VAD超过120秒，原件保留，请分段或检查节点')
     if output.tell()>256*1024 or errors.tell()>1024*1024:raise ValueError('VAD进程输出超限')
     time.sleep(.05)
    guard();output.seek(0);raw=output.read(256*1024+1)
    if child.returncode!=0:
     errors.seek(0);message=errors.read(1024*1024).decode(errors='replace').splitlines();raise ValueError('本地VAD处理失败：'+(message[-1][:300] if message else '子进程退出；不会转云'))
    if len(raw)>256*1024:raise ValueError('VAD结果超限')
    try:result=json.loads(raw)
    except (ValueError,UnicodeError):raise ValueError('VAD结果不是完整JSON')
   finally:
    if child.poll() is None:child.terminate()
    try:child.wait(timeout=3)
    except subprocess.TimeoutExpired:child.kill();child.wait()
 validate(result,content,configuration);return result

def validate(result,content,configuration):
 if not isinstance(result,dict):raise ValueError('VAD结果结构无效')
 if result.get('schema')!='local-speech-activity-v1' or result.get('deployment')!='local' or result.get('backend')!='cpu' or result.get('providers')!=['CPUExecutionProvider'] or result.get('cloud_calls')!=0 or result.get('input_sha256')!=hashlib.sha256(content).hexdigest() or result.get('model_sha256')!=RECIPE['files'][0]['sha256']:raise ValueError('VAD来源或本地CPU契约不一致')
 a=result.get('activity',{});audio=result.get('audio',{})
 if not isinstance(a,dict) or not isinstance(audio,dict):raise ValueError('VAD音频或窗口结构无效')
 length=a.get('resampled_samples');probs=a.get('probabilities',[])
 if not isinstance(probs,list):raise ValueError('VAD音频或窗口结构无效')
 if not isinstance(length,int) or not 4000<=length<=960000 or a.get('sample_rate')!=16000 or a.get('window_samples')!=512 or a.get('step_seconds')!=.032 or a.get('configuration')!=configuration or a.get('parameters')!=RECIPE['parameters'] or len(probs)!=math.ceil(length/512) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in probs):raise ValueError('VAD窗口、参数或分数结构不一致')
 if audio.get('selected_channel')!=configuration['channel'] or not .25<=audio.get('duration_seconds',0)<=60 or abs(length/16000-audio['duration_seconds'])>1/16000+.0000001:raise ValueError('VAD时间轴或明确通道与原件不一致')
 if a.get('inference_performed') is not True or a.get('probabilities_calibrated') is not False or a.get('source_time')!='unknown' or a.get('domain_quality')!='not_verified' or a.get('environment_audio_preserved') is not True or a.get('environment_classifier_used') is not False or a.get('transcription_performed') is not False:raise ValueError('VAD结果错误声明其他专业能力或质量')
 # Verify the published intervals against the saved probabilities using the pinned algorithm.
 from .speech_activity import speech_segments
 if sha(RUNNER.with_name('speech_activity.py'))!=RECIPE['aggregation_sha256']:raise ValueError('固定VAD聚合器摘要已变化')
 expected=speech_segments(probs,length,configuration['threshold']);intervals=a.get('intervals')
 if not isinstance(intervals,list) or len(intervals)!=len(expected):raise ValueError('VAD区间与固定聚合算法不一致')
 for s,wanted in zip(intervals,expected):
  if s!={'start_sample':wanted['start'],'end_sample':wanted['end'],'start_seconds':wanted['start']/16000,'end_seconds':wanted['end']/16000,'timing':'vad_candidate_32ms','speaker_identity':False,'word_timestamps':False}:raise ValueError('VAD区间被改写或时间精度声明错误')

def checked(db,p,row):
 ev,content=evidence_bytes(db,p,row.evidence_id)
 if len(content)!=ev.size_bytes or ev.sha256!=row.data['input_sha256'] or row.data['recipe_hash']!=RECIPE_HASH:raise HTTPException(409,'VAD原音频或固定配方与记录不一致')
 if row.status=='succeeded':
  if digest(row.data['result'])!=row.data['result_hash']:raise HTTPException(409,'已保存VAD结果摘要不符')
  try:validate(row.data['result'],content,row.data['configuration'])
  except (ValueError,KeyError,TypeError) as e:raise HTTPException(409,'已保存VAD结果未通过一致性检查') from e
 return ev,content

def _process(db,p,job):
 row=scoped(db,SpeechActivityRun,job.data['run_id'],p,True);generation=job.data['generation']
 if row.status in {'succeeded','cancelled'} or row.data['generation']!=generation:return
 from .local_vad_install import installed,trace
 installation=installed(db,p,row.data['installation']['installation_id'])
 if trace(installation)!=row.data['installation']:raise ValueError('VAD安装代次或配方已经变化，请核对原来源')
 _,content=checked(db,p,row);row.status='running';row.version+=1;row.data={**row.data,'stage':'detecting_speech','error':None};db.commit()
 def guard():
  from .workflow_segmented_audio import guard_task
  return guard_task(db,p,guard_record(db,p,SpeechActivityRun,row.id,generation))
 def started(pid):
  current=guard();current.data={**current.data,'pid':pid};current.version+=1;db.commit()
 with node_lock():result=call(content,row.data['configuration'],guard,started)
 current=guard();current.status='succeeded';current.version+=1;current.data={**current.data,'result':result,'result_hash':digest(result),'stage':'awaiting_review','error':None}
 audit(db,p,'audio.activity.saved',current.id,{'intervals':len(result['activity']['intervals']),'cloud_calls':0,'entity_state_updated':False});db.commit()
def process(db,p,job):return process_record(db,p,job,SpeechActivityRun,'run_id',_process)
