"""Read-only, fixed public evidence; never a callable workspace capability."""
import hashlib
import json
import math
from pathlib import Path
from .contracts import digest
from .asr_character_quality import character_score, aggregate_characters
from .local_audio import validate as validate_baseline
from .local_qwen_audio import validate as validate_candidate

EVIDENCE = Path(__file__).resolve().parents[2]/'docs/evidence'
MANIFEST_SHA = '6b7017e781e218d51fba256e94a6055c7ede32215efabe8fc2fb62f54820728a'
PACKS = (
    ('mandarin-asr-quality-v06', 'SenseVoiceSmall', '平台已支持的本地模型',
     'b80425846099301484bc3f226378773cc50912226c9a7ea3fb4addbf8a0caf29',
     '3e28da4e6711f17687be46e1b91b5427bd959a31b36f1e663875b9d2dee741fc'),
    ('qwen-asr-quality-v06', 'Qwen3-ASR-0.6B', '候选，待业务接入',
     '7eaea278e530ced267f71c8692e0c632e5b55fa24bc1f3044b70b52e43b794d2',
     '9419f31274d5fe41a6afd02df6ab5935aa070753e6854a1e94970977ade0efd4'),
)


def checked_bytes(path, expected=None, max_bytes=2*1024*1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('公开对照原件缺失、路径异常或大小超限')
    content = path.read_bytes()
    if len(content) > max_bytes or expected and hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('公开对照原件摘要不一致，暂不能展示或导出')
    return content


def read_asr_comparison():
    base = EVIDENCE/PACKS[0][0]
    manifest = json.loads(checked_bytes(base/'manifest.json'))
    if digest(manifest) != MANIFEST_SHA or len(manifest['samples']) != 40:
        raise ValueError('公开对照输入或作者标签与固定版本不同')
    configuration = manifest['configuration']
    samples = manifest['samples']
    audios = {}
    for sample in samples:
        if sample['path'] != 'samples/'+sample['id']+'.wav' or not sample['id'].isdigit():
            raise ValueError('公开录音路径不在固定清单内')
        audios[sample['id']] = checked_bytes(base/sample['path'], sample['sha256'])
    models = []
    outputs = []
    for index, (directory, title, status, checkpoint_sha, report_sha) in enumerate(PACKS):
        source = EVIDENCE/directory
        checkpoint = json.loads(checked_bytes(source/'checkpoint.json', checkpoint_sha))
        report = json.loads(checked_bytes(source/'report.json', report_sha))
        if not report['complete'] or report['succeeded'] != 40 or report['failed'] or len(checkpoint['records']) != 40:
            raise ValueError('公开对照尚未完整，不显示为已完成')
        if checkpoint['fingerprint'] != report['fingerprint'] or checkpoint['fingerprint']['manifest_sha256'] != MANIFEST_SHA:
            raise ValueError('公开对照结果不是同一输入版本')
        rows = checkpoint['records']; scores = []
        for sample, row in zip(samples, rows, strict=True):
            result = row['result']
            if row['id'] != sample['id'] or row['status'] != 'succeeded' or row['input_sha256'] != sample['sha256'] or digest(result) != row['result_sha256']:
                raise ValueError('公开对照保存结果或顺序异常')
            (validate_candidate if index else validate_baseline)(result, audios[sample['id']], configuration)
            if result['transcription']['inference_performed'] is not True or abs(result['audio']['duration_seconds']-sample['duration_seconds']) > 1e-6:
                raise ValueError('公开对照不是真实完整录音推理')
            score = character_score(sample['reference_text'], result['transcription']['text'])
            if score != row['score'] or not math.isfinite(row['wall_seconds']) or row['wall_seconds'] <= 0:
                raise ValueError('公开对照错误率或处理时间异常')
            scores.append(score)
        if aggregate_characters(scores) != report['scores']:
            raise ValueError('公开对照统计与逐条结果不同')
        models.append({'name': title, 'status': status, 'candidate': bool(index), 'cases': 40,
                       'cer': report['scores']['cer'], 'digit_cer': report['groups']['reference_contains_digit']['cer'],
                       'wall_seconds': report['wall_seconds'], 'real_time_factor': report['real_time_factor'],
                       'checkpoint_sha256': checkpoint_sha, 'report_sha256': report_sha})
        outputs.append(rows)
    cases = []
    for i, sample in enumerate(samples):
        cases.append({'id': sample['id'], 'name': f'录音 {i+1:02d}', 'seconds': sample['duration_seconds'],
                      'input_sha256': sample['sha256'], 'reference': sample['raw_reference_text'],
                      'contains_digits': any(c.isdigit() for c in sample['reference_text']),
                      'outputs': [{'text': rows[i]['result']['transcription']['text'], 'cer': rows[i]['score']['cer'],
                                   'seconds': rows[i]['wall_seconds']} for rows in outputs]})
    return {'schema': 'public-local-asr-comparison-v1', 'models': models, 'cases': cases,
            'source': 'Google FLEURS 公开普通话原录音，CC BY 4.0', 'audio_seconds': 442.12,
            'manifest_sha256': MANIFEST_SHA, 'deployment': 'local', 'cloud_fallback': False,
            'candidate_business_ready': False, 'domain_quality_verified': False,
            'scope': '同一40条原录音和作者标签，无提示词或热词；本机CPU新进程计时。整体错误率更低，但数字子集更差。',
            'limits': '公开朗读结果不代表现场噪声、方言、林业专名或业务理解。没有说话人ID，不能称40个说话人；生产许可、硬件及运维成本仍需核查。',
            'sources': [{'name': '语料与作者标签', 'url': 'https://huggingface.co/datasets/google/fleurs/tree/70bb2e84b976b7e960aa89f1c648e09c59f894dd'},
                        {'name': 'Qwen专用候选固定来源', 'url': 'https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf/tree/7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c'}]}


def original_audio(identity):
    comparison = read_asr_comparison()
    case = next((r for r in comparison['cases'] if r['id'] == identity), None)
    if case is None:
        raise LookupError('不在公开对照录音清单内')
    return checked_bytes(EVIDENCE/PACKS[0][0]/'samples'/(identity+'.wav'), case['input_sha256']), case


def export_markdown(comparison):
    lines = ['# 本地专用语音模型公开对照', '', comparison['source'], '', comparison['scope'], '', comparison['limits'], '',
             '候选待业务接入，默认模型未替换；专业小模型本地运行，不自动转云。', '',
             '| 模型 | 字符错误率 | 数字子集错误率 | 处理秒数 |', '| --- | --- | --- | --- |']
    for model in comparison['models']:
        lines.append(f"| {model['name']} | {model['cer']['rate']:.2%} | {model['digit_cer']['rate']:.2%} | {model['wall_seconds']:.2f} |")
    for case in comparison['cases']:
        lines += ['', '## '+case['name'], '', f"原录音 {case['seconds']:.2f} 秒，SHA256 {case['input_sha256']}", '', '作者标签：'+case['reference']]
        for model, output in zip(comparison['models'], case['outputs'], strict=True):
            lines += ['', model['name']+'：'+output['text'], f"CER {output['cer']['rate']:.2%}；处理 {output['seconds']:.2f} 秒"]
    lines += ['', '## 固定来源', '']+[f"- [{s['name']}]({s['url']})" for s in comparison['sources']]
    lines += ['', '输入清单 SHA256：'+comparison['manifest_sha256']]
    for model in comparison['models']:
        lines += [model['name']+' 原结果 SHA256：'+model['checkpoint_sha256']]
    return ('\n'.join(lines)+'\n').encode('utf-8')
