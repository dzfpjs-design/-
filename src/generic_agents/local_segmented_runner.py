"""One local CPU ASR load, fixed VAD time axis, durable per-segment events."""
import argparse,contextlib,hashlib,json,re,socket,sys,time
from pathlib import Path
from importlib.metadata import version
from local_audio_runner import decode,sha,deny_network
from local_vad_runner import resample
ROOT=Path(__file__).resolve().parents[2]
def emit(value):print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)
def model(weights,recipe):
 import torch,yaml
 from funasr import AutoModel
 config=yaml.safe_load((weights/'config.yaml').read_text());expected={'model':'SenseVoiceSmall','encoder':'SenseVoiceEncoderSmall','frontend':'WavFrontend','tokenizer':'SentencepiecesTokenizer'}
 if any(config.get(k)!=v for k,v in expected.items()):raise ValueError('固定ASR配置不符')
 kwargs={k:config[k] for k in (*expected,'model_conf','encoder_conf','frontend_conf','tokenizer_conf')}
 kwargs['frontend_conf']={**kwargs['frontend_conf'],'cmvn_file':str(weights/'am.mvn')};kwargs['tokenizer_conf']={**kwargs['tokenizer_conf'],'bpemodel':str(weights/'chn_jpn_yue_eng_ko_spectok.bpe.model')}
 kwargs.update(device='cpu',ncpu=4,disable_update=True,disable_pbar=True,trust_remote_code=False)
 loaded,params=AutoModel.build_model(**kwargs);state=torch.load(weights/'model.pt',map_location='cpu',weights_only=True)
 if not isinstance(state,dict) or not state or not all(isinstance(k,str) and isinstance(v,torch.Tensor) for k,v in state.items()):raise ValueError('ASR权重不是固定纯张量字典')
 loaded.load_state_dict(state,strict=True);loaded.eval();return loaded,params

def main():
 p=argparse.ArgumentParser();p.add_argument('--weights',required=True);p.add_argument('--audio',required=True);p.add_argument('--request',required=True);args=p.parse_args()
 recipe=json.loads((ROOT/'model-packs/silero-sensevoice-segments-v1.json').read_text());asr=json.loads((ROOT/'model-packs/sensevoice-small-v1.json').read_text())
 for name,pin in recipe['environment'].items():
  if version(name)!=pin:raise ValueError('分段转写环境不符合固定配方：'+name)
 for name,pin in recipe['source_files'].items():
  if sha(Path(__file__).with_name(name))!=pin:raise ValueError('分段转写固定预处理代码已变化')
 weights=Path(args.weights)
 for item in asr['files']:
  f=weights/item['name']
  if not f.is_file() or f.is_symlink() or f.stat().st_size!=item['bytes'] or sha(f)!=item['sha256']:raise ValueError('固定ASR模型文件摘要不符')
 audio=Path(args.audio);request=json.loads(Path(args.request).read_text());source=request['source'];pending=request['pending'];language=request['configuration']['language']
 if not audio.is_file() or audio.is_symlink() or not 0<audio.stat().st_size<=20*1024*1024 or sha(audio)!=source['input_sha256']:raise ValueError('分段音频原件不一致')
 if language not in {'auto','zh','yue','en','ja','ko'} or not isinstance(pending,list) or len(pending)!=len(set(pending)) or pending!=sorted(pending) or any(type(i)!=int or not 0<=i<len(source['intervals']) for i in pending):raise ValueError('转写区间或语言无效')
 socket.socket.connect=deny_network;socket.socket.connect_ex=deny_network;socket.create_connection=deny_network;started=time.monotonic()
 with contextlib.redirect_stdout(sys.stderr):
  wave,metadata=decode(audio,source['configuration']['channel']);wave=resample(wave,metadata['sample_rate'])
  if len(wave)!=source['resampled_samples']:raise ValueError('分段解码与VAD原时间轴不一致')
  if pending:loaded,params=model(weights,asr)
 total_text=0
 for index in pending:
  interval=source['intervals'][index];start=max(0,interval['start_sample']-recipe['context_samples']);end=min(len(wave),interval['end_sample']+recipe['context_samples'])
  if not 0<=start<end<=len(wave) or end-start<4000:raise ValueError('分段转写区间无效或过短')
  import numpy as np,torch
  samples=np.asarray(wave[start:end],dtype='<f4');pcm_sha=hashlib.sha256(samples.tobytes()).hexdigest()
  with contextlib.redirect_stdout(sys.stderr),torch.inference_mode():output,_=loaded.inference(data_in=[samples],key=['source_segment_'+str(index)],**params,language=language,use_itn=True)
  if not isinstance(output,list) or len(output)!=1 or output[0].get('key')!='source_segment_'+str(index) or not isinstance(output[0].get('text'),str):raise ValueError('ASR返回的区间或文字不一致')
  raw=output[0]['text'];tags=re.findall(r'<\|[^|]+\|>',raw);text=re.sub(r'<\|[^|]+\|>','',raw).strip();total_text+=len(text)
  if len(text)>6000 or total_text>12000:raise ValueError('分段转写文字超限，已有段保留')
  emit({'event':'segment','value':{'index':index,'candidate_start_sample':interval['start_sample'],'candidate_end_sample':interval['end_sample'],'clip_start_sample':start,'clip_end_sample':end,'clip_start_seconds':start/16000,'clip_end_seconds':end/16000,'pcm_sha256':pcm_sha,'text':text,'coarse_tags':tags,'language':language,'inference_performed':True,'word_timestamps':False,'speaker_identity':False,'timing':'vad_candidate_with_context_not_word_alignment','cloud_calls':0}})
 emit({'event':'done','input_sha256':source['input_sha256'],'indexes':pending,'model_load_count':1 if pending else 0,'cloud_calls':0,'elapsed_seconds':time.monotonic()-started})
if __name__=='__main__':main()
