"""Fixed native Qwen ASR, offline CPU float32, no remote code or domain acceptance."""
import argparse
import contextlib
import hashlib
import json
import socket
import sys
import time
from importlib.metadata import version
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def deny_network(*args, **kwargs): raise RuntimeError('本地专用ASR推理禁止联网')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--weights', required=True)
    parser.add_argument('--audio', required=True); parser.add_argument('--request', required=True); args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    recipe = json.loads((root/'model-packs/qwen3-asr-0.6b-hf-v1.json').read_text())
    for package, pin in recipe['environment'].items():
        if version(package) != pin: raise ValueError('专用ASR运行时版本不同：'+package)
    if sha(Path(__file__).with_name('local_audio_runner.py')) != recipe['decoder_sha256']:
        raise ValueError('原音频解码器与固定配方不同')
    weights = Path(args.weights)
    for item in recipe['files']:
        p = weights/item['name']
        if not p.is_file() or p.is_symlink() or p.stat().st_size != item['bytes'] or sha(p) != item['sha256']:
            raise ValueError('专用ASR固定文件不一致：'+item['name'])
    cfg = json.loads((weights/'config.json').read_text())
    if cfg.get('model_type') != 'qwen3_asr' or cfg.get('architectures') != ['Qwen3ASRForConditionalGeneration'] or cfg.get('auto_map'):
        raise ValueError('仅接受已核验的原生ASR架构，不运行仓库代码')
    request = json.loads(Path(args.request).read_text())
    if set(request) != {'channel','language'} or request['language'] != 'zh' or request['channel'] != 'mono':
        raise ValueError('此候选仅核验单通道普通话，无热词或额外提示')
    audio = Path(args.audio)
    if not audio.is_file() or audio.is_symlink() or not 0 < audio.stat().st_size <= 20*1024*1024:
        raise ValueError('原音频为空、路径不安全或超过20MB')
    socket.socket.connect = deny_network; socket.socket.connect_ex = deny_network; socket.create_connection = deny_network
    start = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        from local_audio_runner import decode
        wave, metadata = decode(audio, request['channel'])
        if metadata['sample_rate'] != 16000 or metadata['duration_seconds'] > 30:
            raise ValueError('此候选只核验16kHz、30秒以内原录音，不自动转换规格')
        raw = text = ''; generated_count = 0; inference = not metadata['silence_detected']
        if inference:
            import torch
            from transformers import AutoProcessor, AutoModelForMultimodalLM
            torch.set_num_threads(4); torch.set_num_interop_threads(4)
            processor = AutoProcessor.from_pretrained(str(weights), local_files_only=True, trust_remote_code=False)
            if recipe['attention_implementation'] != 'sdpa': raise ValueError('CPU注意力实现与已固定配方不同')
            model = AutoModelForMultimodalLM.from_pretrained(str(weights), local_files_only=True, trust_remote_code=False,
                use_safetensors=True, dtype=torch.float32, attn_implementation=recipe['attention_implementation'])
            model.to('cpu'); model.eval()
            if model.device.type != 'cpu' or model.dtype != torch.float32:
                raise ValueError('专用ASR未按CPU float32运行')
            inputs = processor.apply_transcription_request(audio=wave, language='Chinese', prompt=None,
                sampling_rate=16000, return_tensors='pt').to(model.device, model.dtype)
            with torch.inference_mode(): output = model.generate(**inputs, max_new_tokens=256, do_sample=False)
            generated = output[0, inputs['input_ids'].shape[1]:]; generated_count = len(generated)
            eos = model.generation_config.eos_token_id
            eos = [eos] if isinstance(eos,int) else eos
            if not eos or generated_count >= 256 and int(generated[-1]) not in eos:
                raise ValueError('专用ASR输出达到长度上限，不能把截断当完整结果')
            raw = processor.decode(generated, skip_special_tokens=False)
            text = processor.decode(generated, return_format='transcription_only').strip()
            if not isinstance(text,str) or len(text)>12000 or not isinstance(raw,str) or len(raw)>24000:
                raise ValueError('专用ASR输出格式或长度超限')
    result = {'schema':'local-audio-transcription-v1','deployment':'local','backend':'cpu','cloud_calls':0,
        'input_sha256':sha(audio),'audio':metadata,'elapsed_seconds':time.monotonic()-start,
        'model':{'pack_id':recipe['pack_id'],'revision':recipe['revision'],'dtype':'float32',
                 'recipe_sha256':hashlib.sha256(json.dumps(recipe,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                 'native_architecture':True,'remote_code':False,'attention_implementation':recipe['attention_implementation'],
                 'generated_tokens':generated_count,'context_or_hotwords_used':False},
        'transcription':{'text':text,'raw_text':raw,'coarse_tags':[],
            'segments':[{'start_seconds':0,'end_seconds':metadata['duration_seconds'],'text':text,'timing':'input_clip_only'}],
            'word_timestamps':False,'speaker_identity':False,'vad_used':False,'environment_classifier_used':False,
            'language_requested':'zh','learned_model':True,'inference_performed':inference,
            'skip_reason':None if inference else 'near_silence','domain_quality':'not_verified'}}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__ == '__main__': main()
