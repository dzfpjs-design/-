"""Pinned ONNX CPU speech activity. No remote Python, telemetry or network."""
import argparse,contextlib,hashlib,json,math,socket,sys,time
from pathlib import Path
from importlib.metadata import version
from local_audio_runner import decode
ROOT=Path(__file__).resolve().parents[2]
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def deny_network(*args,**kwargs):raise RuntimeError('本地语音区间进程禁止网络访问')

from speech_activity import speech_segments

def resample(wave,rate):
 import av,numpy as np
 from fractions import Fraction
 if rate==16000:return wave.astype(np.float32,copy=True)
 frame=av.AudioFrame.from_ndarray(wave[np.newaxis,:].astype(np.float32),format='fltp',layout='mono');frame.sample_rate=rate;frame.pts=0;frame.time_base=Fraction(1,rate)
 converter=av.AudioResampler(format='fltp',layout='mono',rate=16000)
 frames=[f.to_ndarray()[0] for f in converter.resample(frame)]+[f.to_ndarray()[0] for f in converter.resample(None)]
 if not frames:raise ValueError('音频重采样没有输出')
 return np.concatenate(frames).astype(np.float32)

def infer(path,wave,threshold):
 import numpy as np,onnxruntime as ort
 ort.disable_telemetry_events();options=ort.SessionOptions();options.inter_op_num_threads=1;options.intra_op_num_threads=1;options.log_severity_level=3
 session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
 if session.get_providers()!=['CPUExecutionProvider'] or [x.name for x in session.get_inputs()]!=['input','state','sr']:raise ValueError('VAD提供者或输入与固定CPU配方不一致')
 session.disable_fallback();state=np.zeros((2,1,128),np.float32);context=np.zeros((1,64),np.float32);scores=[]
 for offset in range(0,len(wave),512):
  chunk=wave[offset:offset+512]
  if len(chunk)<512:chunk=np.pad(chunk,(0,512-len(chunk)))
  input_=np.concatenate([context,chunk[np.newaxis,:]],axis=1)
  output,state=session.run(None,{'input':input_,'state':state,'sr':np.array(16000,np.int64)})
  if output.shape!=(1,1) or state.shape!=(2,1,128) or not np.isfinite(state).all():raise ValueError('VAD循环状态或分数结构不一致')
  score=float(output[0,0]);scores.append(score);context=input_[:,-64:]
 return scores,speech_segments(scores,len(wave),threshold)

def main():
 p=argparse.ArgumentParser();p.add_argument('--weights',required=True);p.add_argument('--audio',required=True);p.add_argument('--request',required=True);args=p.parse_args()
 recipe=json.loads((ROOT/'model-packs/silero-vad-onnx-v1.json').read_text())
 if sha(Path(__file__).with_name('local_audio_runner.py'))!=recipe['decoder_sha256']:raise ValueError('固定解码器已变化，未进行新推理')
 for name,pin in recipe['environment'].items():
  if version(name)!=pin:raise ValueError('本地VAD环境与固定配方不一致：'+name)
 weights=Path(args.weights)
 for item in recipe['files']:
  f=weights/item['name']
  if not f.is_file() or f.is_symlink() or f.stat().st_size!=item['bytes'] or sha(f)!=item['sha256']:raise ValueError('VAD文件摘要不符：'+item['name'])
 if sha(Path(__file__).with_name('speech_activity.py'))!=recipe['aggregation_sha256']:raise ValueError('固定语音区间聚合器已变化')
 request=json.loads(Path(args.request).read_text());channel=request['channel'];threshold=request['threshold'];audio=Path(args.audio)
 if channel not in {'mono','mix','1','2','3','4','5','6','7','8'} or not .1<=threshold<=.9:raise ValueError('VAD通道或阈值不支持')
 if not audio.is_file() or audio.is_symlink() or not 0<audio.stat().st_size<=20*1024*1024:raise ValueError('音频原件不安全或大小不支持')
 socket.socket.connect=deny_network;socket.socket.connect_ex=deny_network;socket.create_connection=deny_network
 started=time.monotonic()
 with contextlib.redirect_stdout(sys.stderr):
  wave,metadata=decode(audio,channel);wave=resample(wave,metadata['sample_rate']);scores,spans=infer(weights/'silero_vad_16k_op15.onnx',wave,threshold)
 if abs(len(wave)/16000-metadata['duration_seconds'])>1/16000+.0000001:raise ValueError('重采样长度与原件时间轴不一致')
 intervals=[{'start_sample':s['start'],'end_sample':s['end'],'start_seconds':s['start']/16000,'end_seconds':s['end']/16000,'timing':'vad_candidate_32ms','speaker_identity':False,'word_timestamps':False} for s in spans]
 print(json.dumps({'schema':'local-speech-activity-v1','deployment':'local','backend':'cpu','providers':['CPUExecutionProvider'],'cloud_calls':0,'input_sha256':sha(audio),'model_sha256':recipe['files'][0]['sha256'],'audio':metadata,'activity':{'sample_rate':16000,'resampled_samples':len(wave),'window_samples':512,'step_seconds':.032,'configuration':request,'parameters':recipe['parameters'],'probabilities':scores,'intervals':intervals,'inference_performed':True,'probabilities_calibrated':False,'source_time':'unknown','domain_quality':'not_verified','environment_audio_preserved':True,'environment_classifier_used':False,'transcription_performed':False},'elapsed_seconds':time.monotonic()-started},ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
