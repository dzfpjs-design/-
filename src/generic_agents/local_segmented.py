"""Local VAD-source snapshots, resumable per-segment ASR and strict time provenance."""
import copy,hashlib,json,os,re,subprocess,tempfile,time,sys
from pathlib import Path
from fastapi import HTTPException
from .contracts import digest
from .models import SpeechActivityRun,SegmentedAudioRun,SpecialistInstallation
from .services import scoped,audit
from .local_specialists import node_lock,guard_record,process_record
from .local_vad import checked as checked_vad,RECIPE_HASH as VAD_HASH
from .local_audio import ready as ready_asr,RECIPE_HASH as ASR_HASH
from .local_audio_paths import audio_paths
ROOT=Path(__file__).resolve().parents[2];RUNNER=Path(__file__).with_name('local_segmented_runner.py');RECIPE=json.loads((ROOT/'model-packs/silero-sensevoice-segments-v1.json').read_text());RECIPE_HASH=digest(RECIPE)
_SANDBOX=Path('/usr/bin/sandbox-exec')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ready(*,_paths=None):
 paths=audio_paths() if _paths is None else _paths
 if sys.platform!='darwin' or not _SANDBOX.is_file():raise ValueError('分段ASR尚未验证此节点系统网络隔离，不能转云或取消隔离')
 if RECIPE['vad_recipe_hash']!=VAD_HASH or RECIPE['asr_recipe_hash']!=ASR_HASH or sha(RUNNER)!=RECIPE['runner_sha256'] or any(sha(RUNNER.with_name(n))!=v for n,v in RECIPE['source_files'].items()):raise ValueError('固定分段转写配方或预处理代码不一致')
 ready_asr(_paths=paths)
def capture(source):
 if source.status!='succeeded' or source.data['recipe_hash']!=VAD_HASH:raise HTTPException(409,'请先完成真实本地语音区间检测')
 d=source.data;a=d['result']['activity'];return copy.deepcopy({'source_run_id':source.id,'source_generation':d['generation'],'evidence_id':source.evidence_id,'name':d['name'],'media_type':d['media_type'],'size_bytes':d['size_bytes'],'input_sha256':d['input_sha256'],'result_hash':d['result_hash'],'recipe_hash':d['recipe_hash'],'installation':d['installation'],'configuration':d['configuration'],'resampled_samples':a['resampled_samples'],'intervals':a['intervals']})
def source_value(db,p,identity):
 source=scoped(db,SpeechActivityRun,identity,p);_,content=checked_vad(db,p,source);s=capture(source);return s,content

def installations(db,p,source=None):
 from .local_vad_install import installed as vad_install,trace as vad_trace
 from .local_audio_install import installed as asr_install,trace as asr_trace
 vad=vad_install(db,p);asr=asr_install(db,p);values={'vad':vad_trace(vad),'asr':asr_trace(asr)}
 if source is not None and values['vad']!=source['installation']:raise HTTPException(409,'原区间使用的VAD安装代次已变化，请保留原件并核对')
 ready();return values

def valid_configuration(source,configuration):
 indexes=configuration.get('indexes');language=configuration.get('language')
 if not isinstance(indexes,list) or len(indexes)>250 or indexes!=sorted(indexes) or len(indexes)!=len(set(indexes)) or any(type(i)!=int or not 0<=i<len(source['intervals']) for i in indexes) or language not in {'auto','zh','yue','en','ja','ko'} or not indexes and source['intervals']:raise ValueError('请按原时间顺序明确选择转写区间和语言')
def validate_segment(value,source,configuration):
 if not isinstance(value,dict) or type(value.get('index'))!=int or value['index'] not in configuration['indexes']:raise ValueError('转写段不是原选定区间')
 interval=source['intervals'][value['index']];start=max(0,interval['start_sample']-RECIPE['context_samples']);end=min(source['resampled_samples'],interval['end_sample']+RECIPE['context_samples'])
 expected={'candidate_start_sample':interval['start_sample'],'candidate_end_sample':interval['end_sample'],'clip_start_sample':start,'clip_end_sample':end,'clip_start_seconds':start/16000,'clip_end_seconds':end/16000,'language':configuration['language'],'inference_performed':True,'word_timestamps':False,'speaker_identity':False,'timing':'vad_candidate_with_context_not_word_alignment','cloud_calls':0}
 if any(value.get(k)!=v for k,v in expected.items()) or set(value)!=set(expected)|{'index','pcm_sha256','text','coarse_tags'} or not isinstance(value.get('text'),str) or len(value['text'])>6000 or not re.fullmatch('[a-f0-9]{64}',value.get('pcm_sha256','')) or not isinstance(value.get('coarse_tags'),list) or len(value['coarse_tags'])>50 or any(not isinstance(t,str) or len(t)>100 for t in value['coarse_tags']):raise ValueError('分段转写文字、采样边界或本地能力声明不一致')
def validate_checkpoints(segments,source,configuration):
 if not isinstance(segments,list) or len(segments)>len(configuration['indexes']) or [s.get('index') for s in segments]!=configuration['indexes'][:len(segments)]:raise ValueError('保存的转写检查点不是按原区间顺序完成')
 for s in segments:validate_segment(s,source,configuration)
 if sum(len(s['text']) for s in segments)>12000:raise ValueError('分段转写总文字超过60秒片段允许长度')
def result_value(row):
 return {'schema':'local-segmented-audio-v1','deployment':'local','backend':'cpu','cloud_calls':0,'pipeline_hash':RECIPE_HASH,'input_sha256':row.data['source']['input_sha256'],'source_snapshot_hash':row.data['source_snapshot_hash'],'configuration':row.data['configuration'],'segments':row.data['segments'],'vad_used':True,'asr_inference_performed':bool(row.data['segments']),'word_timestamps':False,'speaker_identity':False,'environment_classifier_used':False,'source_time':'unknown','domain_quality':'not_verified','context_samples':RECIPE['context_samples'],'skip_reason':None if row.data['segments'] else 'no_vad_candidate_intervals'}
def checked(db,p,row):
 source,content=source_value(db,p,row.source_run_id)
 if source!=row.data['source'] or digest(source)!=row.data['source_snapshot_hash'] or row.data['recipe_hash']!=RECIPE_HASH:raise HTTPException(409,'转写的原音频、语音区间或配方与冻结来源不一致')
 try:
  valid_configuration(source,row.data['configuration']);validate_checkpoints(row.data['segments'],source,row.data['configuration'])
  if digest(row.data['segments'])!=row.data['checkpoint_hash']:raise ValueError('已保存转写段摘要不符')
  if row.status=='succeeded' and (len(row.data['segments'])!=len(row.data['configuration']['indexes']) or row.data['result']!=result_value(row) or digest(row.data['result'])!=row.data['result_hash']):raise ValueError('转写结果不完整或与检查点不一致')
 except (ValueError,TypeError,KeyError,AttributeError) as e:raise HTTPException(409,'已保存分段转写未通过来源和区间检查') from e
 return source,content

def call(content,source,configuration,pending,guard=lambda:None,on_started=lambda pid:None,on_segment=lambda value:None,*,_paths=None):
 paths=audio_paths() if _paths is None else _paths
 ready(_paths=paths);valid_configuration(source,configuration)
 env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}};env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',MODELSCOPE_OFFLINE='1',TORCH_FORCE_WEIGHTS_ONLY_LOAD='1',NUMBA_NUM_THREADS='4',OMP_NUM_THREADS='4',PYTORCH_ENABLE_MPS_FALLBACK='0')
 seen=[];done=None
 with tempfile.TemporaryDirectory(prefix='ga-segments-') as directory:
  d=Path(directory).resolve();os.chmod(d,0o700);cache=d/'cache';cache.mkdir(mode=0o700)
  env.update(HOME=str(d),TMPDIR=str(d),TMP=str(d),TEMP=str(d),XDG_CACHE_HOME=str(cache),HF_HOME=str(cache/'hf'),HUGGINGFACE_HUB_CACHE=str(cache/'hf/hub'),TORCH_HOME=str(cache/'torch'),MODELSCOPE_CACHE=str(cache/'modelscope'),NUMBA_CACHE_DIR=str(cache/'numba'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
  (d/'original.bin').write_bytes(content);(d/'request.json').write_text(json.dumps({'source':source,'configuration':configuration,'pending':pending}))
  with (d/'stdout').open('wb') as output,(d/'stderr').open('wb') as errors,(d/'stdout').open('rb') as reader:
   command=[str(_SANDBOX),'-p','(version 1)(allow default)(deny network*)',str(paths.python),'-B',str(RUNNER),'--weights',str(paths.weights),'--audio',str(d/'original.bin'),'--request',str(d/'request.json')]
   child=subprocess.Popen(command,stdout=output,stderr=errors,env=env,cwd=d,stdin=subprocess.DEVNULL);start=time.monotonic();buffer=b''
   try:
    on_started(child.pid)
    while True:
     guard();buffer+=reader.read(65536)
     if (d/'stdout').stat().st_size>512*1024 or (d/'stderr').stat().st_size>4*1024*1024 or len(buffer)>128*1024:raise ValueError('分段转写进程输出超限')
     while b'\n' in buffer:
      line,buffer=buffer.split(b'\n',1)
      try:event=json.loads(line)
      except (ValueError,UnicodeError):raise ValueError('分段转写事件不完整')
      if not isinstance(event,dict) or done is not None:raise ValueError('分段转写事件顺序错误')
      if event.get('event')=='segment':
       value=event.get('value');validate_segment(value,source,configuration)
       if len(seen)>=len(pending) or value['index']!=pending[len(seen)]:raise ValueError('ASR检查点重复、缺失或区间乱序')
       on_segment(value);seen.append(value)
      elif event.get('event')=='done':done=event
      else:raise ValueError('未知分段转写事件')
     if child.poll() is not None:
      remaining=reader.read()
      if remaining:buffer+=remaining;continue
      break
     if time.monotonic()-start>900:raise ValueError('分段转写超过15分钟，已完成段与原件保留，可按原设置重试')
     time.sleep(.1)
    guard()
    if child.returncode!=0:
     message=(d/'stderr').read_text(errors='replace').splitlines();raise ValueError('本地分段转写失败：'+(message[-1][:300] if message else '进程退出；已有段保留'))
    if buffer or not done or done.get('input_sha256')!=source['input_sha256'] or done.get('indexes')!=pending or done.get('model_load_count')!=(1 if pending else 0) or done.get('cloud_calls')!=0 or len(seen)!=len(pending):raise ValueError('分段ASR未返回完整本地处理收据')
   finally:
    if child.poll() is None:child.terminate()
    try:child.wait(timeout=3)
    except subprocess.TimeoutExpired:child.kill();child.wait()
 return seen,done

def _process(db,p,job):
 row=scoped(db,SegmentedAudioRun,job.data['run_id'],p,True);generation=job.data['generation'];identity=row.id
 if row.status in {'succeeded','cancelled'} or row.data['generation']!=generation:return
 source,content=checked(db,p,row)
 if installations(db,p,source)!=row.data['installations']:raise ValueError('分段转写使用的本地安装版本已变化')
 row.status='running';row.version+=1;row.data={**row.data,'stage':'loading_asr','error':None};db.commit()
 def guard():
  from .workflow_segmented_audio import guard_task
  current=guard_task(db,p,guard_record(db,p,SegmentedAudioRun,identity,generation))
  s=scoped(db,SpeechActivityRun,current.source_run_id,p)
  if capture(s)!=source:raise ValueError('处理期间原VAD来源已变化，已有段保留')
  for kind,trace in current.data['installations'].items():
   install=scoped(db,SpecialistInstallation,trace['installation_id'],p)
   if install.status!='ready' or install.data['generation']!=trace['generation'] or install.data['recipe_hash']!=trace['recipe_hash']:raise ValueError('处理期间本地安装版本变化，已有段保留')
  return current
 def started(pid):
  current=guard();current.data={**current.data,'pid':pid};current.version+=1;db.commit()
 def save_segment(value):
  current=guard();segments=current.data['segments']+[value];validate_checkpoints(segments,source,current.data['configuration']);current.version+=1;current.data={**current.data,'segments':segments,'checkpoint_hash':digest(segments),'stage':'transcribing_segments'};audit(db,p,'audio.segment.checkpoint',identity,{'segment_index':value['index'],'cloud_calls':0});db.commit()
 pending=row.data['configuration']['indexes'][len(row.data['segments']):]
 with node_lock():_,receipt=call(content,source,row.data['configuration'],pending,guard,started,save_segment)
 current=guard();current.status='succeeded';current.version+=1;current.data={**current.data,'stage':'awaiting_review','receipt':receipt,'result':None,'result_hash':None,'error':None};result=result_value(current);current.data={**current.data,'result':result,'result_hash':digest(result)};audit(db,p,'audio.segments.saved',identity,{'segments':len(current.data['segments']),'cloud_calls':0,'entity_state_updated':False});db.commit()
def process(db,p,job):return process_record(db,p,job,SegmentedAudioRun,'run_id',_process)
