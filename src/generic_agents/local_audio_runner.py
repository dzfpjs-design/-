"""Pinned local CPU ASR. No remote code, implicit download, or word timestamps."""
import argparse,contextlib,hashlib,json,os,socket,sys,time
from importlib.metadata import version
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def deny_network(*args,**kwargs):raise RuntimeError('本地音频进程禁止网络访问')

def decode(path,channel):
    import av,numpy as np
    with av.open(str(path),mode='r',options={'protocol_whitelist':'file'}) as container:
        streams=list(container.streams.audio)
        if list(container.streams.video):raise ValueError('此入口仅处理音频，视频请使用视频处理入口')
        if len(streams)!=1:raise ValueError('请上传仅含一条音轨的音频，不能替用户选择音轨')
        stream=streams[0];rate=stream.codec_context.sample_rate;channels=stream.codec_context.channels
        if not rate or not 8000<=rate<=96000 or not channels or not 1<=channels<=8:raise ValueError('音频采样率或通道数不支持')
        if channel=='mono' and channels!=1:raise ValueError('音频有多个通道，请明确选择通道或合并，不自动丢弃通道')
        if channel not in {'mono','mix'}:
            if not channel.isdigit() or not 1<=int(channel)<=channels:raise ValueError('指定音频通道不存在')
        frames=[];samples=0;convert=av.AudioResampler(format='fltp',layout=stream.codec_context.layout.name,rate=rate)
        for frame in container.decode(stream):
            for f in convert.resample(frame):
                samples+=f.samples
                if samples>rate*60:raise ValueError('音频超过60秒，请保留原件并分段后处理')
                frames.append(f.to_ndarray())
        for f in convert.resample(None):
            samples+=f.samples
            if samples>rate*60:raise ValueError('音频超过60秒')
            frames.append(f.to_ndarray())
        if not frames:raise ValueError('音频没有可解码的采样数据')
        source=np.concatenate(frames,axis=1)
        if source.shape!=(channels,samples) or not np.isfinite(source).all():raise ValueError('音频包含无效数据或通道布局变化')
        mono=source.mean(axis=0) if channel=='mix' else source[0 if channel=='mono' else int(channel)-1]
        if samples<rate*.25:raise ValueError('音频短于0.25秒，保留原件但不生成转写')
        rms=float(np.sqrt(np.mean(mono.astype('float64')**2)))
        clipped=float(np.mean(np.abs(mono)>=.999))
        metadata={'sample_rate':rate,'channels':channels,'samples':samples,'duration_seconds':samples/rate,'selected_channel':channel,'rms':rms,'clipped_fraction':clipped,'peak':float(np.max(np.abs(mono))),'silence_detected':rms<1e-5,'channel_policy':'explicit','noise_quality':'not_measured','source_time':'unknown'}
        return mono.copy(),metadata

def recognize(weights,wave,metadata,language,recipe):
    import numpy as np,torch,torchaudio,yaml
    from funasr import AutoModel
    config=yaml.safe_load((weights/'config.yaml').read_text())
    expected={'model':'SenseVoiceSmall','encoder':'SenseVoiceEncoderSmall','frontend':'WavFrontend','tokenizer':'SentencepiecesTokenizer'}
    if any(config.get(k)!=v for k,v in expected.items()):raise ValueError('模型配置与固定语音后端不一致')
    kwargs={k:config[k] for k in (*expected,'model_conf','encoder_conf','frontend_conf','tokenizer_conf')}
    kwargs['frontend_conf']={**kwargs['frontend_conf'],'cmvn_file':str(weights/'am.mvn')}
    kwargs['tokenizer_conf']={**kwargs['tokenizer_conf'],'bpemodel':str(weights/'chn_jpn_yue_eng_ko_spectok.bpe.model')}
    kwargs.update(device='cpu',ncpu=4,disable_update=True,disable_pbar=True,trust_remote_code=False)
    model,params=AutoModel.build_model(**kwargs)
    state=torch.load(weights/'model.pt',map_location='cpu',weights_only=True)
    if not isinstance(state,dict) or not state or not all(isinstance(k,str) and isinstance(v,torch.Tensor) for k,v in state.items()):raise ValueError('语音权重不是纯张量字典，不进行不安全加载')
    model.load_state_dict(state,strict=True);model.eval()
    data=torch.from_numpy(wave)
    if metadata['sample_rate']!=16000:data=torchaudio.functional.resample(data,metadata['sample_rate'],16000)
    if metadata['silence_detected']:
        output=[{'text':''}]
    else:
        with torch.inference_mode():
            output,_=model.inference(data_in=[data.numpy()],key=['local_audio'],**params,language=language,use_itn=True)
    if not isinstance(output,list) or len(output)!=1 or not isinstance(output[0].get('text'),str):raise ValueError('语音模型结果结构不符合片段契约')
    raw=output[0]['text']
    import re
    tags=re.findall(r'<\|[^|]+\|>',raw);text=re.sub(r'<\|[^|]+\|>','',raw).strip()
    if len(text)>12000:raise ValueError('音频结果超过允许长度')
    # Clip boundaries describe the evidence interval, never word or utterance alignment.
    return {'text':text,'raw_text':raw,'coarse_tags':tags,'segments':[{'start_seconds':0,'end_seconds':metadata['duration_seconds'],'text':text,'timing':'input_clip_only'}],'word_timestamps':False,'speaker_identity':False,'vad_used':False,'environment_classifier_used':False,'language_requested':language,'learned_model':True,'inference_performed':not metadata['silence_detected'],'skip_reason':'near_silence' if metadata['silence_detected'] else None,'domain_quality':'not_verified'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--weights',required=True);p.add_argument('--audio',required=True);p.add_argument('--request',required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[2];recipe=json.loads((root/'model-packs/sensevoice-small-v1.json').read_text())
    for name,pin in recipe['environment'].items():
        if version(name)!=pin:raise ValueError('语音环境与固定配方不一致：'+name)
    weights=Path(a.weights)
    for item in recipe['files']:
        f=weights/item['name']
        if not f.is_file() or f.is_symlink() or f.stat().st_size!=item['bytes'] or sha(f)!=item['sha256']:raise ValueError('语音文件摘要不符合固定配方：'+item['name'])
    request=json.loads(Path(a.request).read_text());channel=request.get('channel','mono');language=request.get('language','auto')
    if language not in {'auto','zh','en','yue','ja','ko'}:raise ValueError('语音识别语言不支持')
    audio=Path(a.audio)
    if not audio.is_file() or audio.is_symlink() or not 0<audio.stat().st_size<=20*1024*1024:raise ValueError('音频文件为空、不安全或超过20MB')
    socket.socket.connect=deny_network;socket.socket.connect_ex=deny_network;socket.create_connection=deny_network
    start=time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        wave,meta=decode(audio,channel)
        result=recognize(weights,wave,meta,language,recipe)
    print(json.dumps({'schema':'local-audio-transcription-v1','deployment':'local','backend':'cpu','cloud_calls':0,'input_sha256':sha(audio),'audio':meta,'transcription':result,'elapsed_seconds':time.monotonic()-start},ensure_ascii=False,allow_nan=False))

if __name__=='__main__':main()
