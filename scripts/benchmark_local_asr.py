"""Offline QA, not an application API: frozen public labels and resumable real ASR."""
import argparse
import hashlib
import json
import os
import platform
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from generic_agents.asr_quality import NORMALIZATION, aggregate, score

ARCHIVE_MD5 = '32fa31d27d2e1cad72775fee3f4849a9'
SOURCE = 'https://www.openslr.org/12/'
SELECTION = 'asr-public-v1: every test-clean speaker; SHA256(seed|utterance_id) first eligible 2-12s mono 16kHz clip'
CONFIGURATION = {'channel': 'mono', 'language': 'en'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')
        f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def flac_info(raw):
    if raw[:4] != b'fLaC' or raw[4] & 127 or int.from_bytes(raw[5:8], 'big') != 34:
        raise ValueError('Expected original FLAC STREAMINFO')
    bits = int.from_bytes(raw[18:26], 'big')
    rate, channels, frames = bits >> 44, ((bits >> 41) & 7) + 1, bits & ((1 << 36) - 1)
    if not rate: raise ValueError('Missing FLAC sample rate')
    return rate, channels, frames / rate


def prepare(archive, out):
    if (out / 'manifest.json').exists(): raise ValueError('Existing frozen manifest; use run, never replace labels')
    if file_hash(archive, 'md5') != ARCHIVE_MD5: raise ValueError('Archive differs from author checksum')
    transcripts, speakers, clips = {}, {}, {}
    with tarfile.open(archive, 'r:gz') as corpus:
        for member in corpus:
            if not member.isfile() or member.size > 20 * 1024 * 1024: continue
            stream = corpus.extractfile(member)
            if member.name == 'LibriSpeech/SPEAKERS.TXT':
                for line in stream.read().decode().splitlines():
                    if line.strip() and not line.startswith(';'):
                        fields = [x.strip() for x in line.split('|')]
                        if fields[2] == 'test-clean': speakers[fields[0]] = fields[1]
            elif re.fullmatch(r'LibriSpeech/test-clean/\d+/\d+/\d+-\d+\.trans\.txt', member.name):
                for line in stream.read().decode().splitlines():
                    identity, text = line.split(' ', 1)
                    if identity in transcripts: raise ValueError('Duplicate label')
                    score(text, '')  # Empty or punctuation-only ground truth is invalid.
                    transcripts[identity] = text
            elif re.fullmatch(r'LibriSpeech/test-clean/\d+/\d+/\d+-\d+-\d+\.flac', member.name):
                raw = stream.read()
                rate, channels, duration = flac_info(raw)
                if rate != 16000 or channels != 1 or not 2 <= duration <= 12: continue
                identity = Path(member.name).stem; speaker = identity.split('-')[0]
                key = hashlib.sha256(('asr-public-v1|' + identity).encode()).hexdigest()
                if speaker not in clips or key < clips[speaker][0]:
                    clips[speaker] = (key, identity, raw, duration)
    if not speakers or set(clips) != set(speakers): raise ValueError('Selection must cover every author test-clean speaker')
    samples = []
    for speaker in sorted(speakers, key=int):
        _, identity, raw, duration = clips[speaker]
        if identity not in transcripts: raise ValueError('Selected recording has no original label')
        path = out / 'samples' / (identity + '.flac'); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        samples.append({'id': identity, 'speaker': speaker, 'author_sex_label': speakers[speaker],
                        'path': 'samples/' + path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'duration_seconds': duration, 'reference_text': transcripts[identity]})
    manifest = {'schema': 'public-asr-manifest-v1', 'source': SOURCE, 'license': 'CC BY 4.0',
                'attribution': 'LibriSpeech: Vassil Panayotov, Guoguo Chen, Daniel Povey, Sanjeev Khudanpur (2015); source OpenSLR12, derived from LibriVox',
                'archive_author_md5': ARCHIVE_MD5, 'archive_sha256': file_hash(archive),
                'selection': SELECTION, 'sample_count': len(samples), 'configuration': CONFIGURATION,
                'normalization': NORMALIZATION, 'samples': samples,
                'limits': ['English read speech only, no Chinese/domain/noise quality acceptance',
                           'Model training overlap unknown', 'Small deterministic subset; no candidate comparison or manual baseline']}
    save(out / 'manifest.json', manifest)
    print(json.dumps({'prepared_samples': len(samples), 'manifest_sha256': digest(manifest)}))


def fingerprint(manifest):
    recipe = json.loads((ROOT / 'model-packs/sensevoice-small-v1.json').read_text())
    return {'manifest_sha256': digest(manifest), 'recipe_sha256': digest(recipe),
            'model_runner_sha256': file_hash(ROOT / 'src/generic_agents/local_audio_runner.py'),
            'metric_code_sha256': file_hash(ROOT / 'src/generic_agents/asr_quality.py'),
            'benchmark_code_sha256': file_hash(Path(__file__)),
            'configuration': CONFIGURATION, 'normalization': NORMALIZATION}


def load_checkpoint(out, frozen):
    path = out / 'checkpoint.json'
    if not path.exists(): return {'schema': 'local-asr-checkpoint-v1', 'fingerprint': frozen, 'records': []}
    value = json.loads(path.read_text())
    if value.get('schema') != 'local-asr-checkpoint-v1' or value.get('fingerprint') != frozen:
        raise ValueError('Saved benchmark configuration changed; cannot resume original results')
    return value


def validate_records(manifest, checkpoint):
    samples = {s['id']: s for s in manifest['samples']}; seen = set()
    for record in checkpoint['records']:
        identity = record['id']
        if identity in seen or identity not in samples: raise ValueError('Duplicate or foreign saved result')
        seen.add(identity); expected = samples[identity]
        if record['input_sha256'] != expected['sha256']: raise ValueError('Saved input digest changed')
        if record['status'] not in {'running', 'failed', 'succeeded'}: raise ValueError('Invalid saved status')
        if record['status'] == 'succeeded':
            result = record['result']
            if digest(result) != record['result_sha256'] or result['input_sha256'] != expected['sha256']:
                raise ValueError('Saved raw result digest changed')
            if result.get('deployment') != 'local' or result.get('cloud_calls') != 0:
                raise ValueError('Saved result was not offline local inference')
            if score(expected['reference_text'], result['transcription']['text']) != record['score']:
                raise ValueError('Saved ASR metric differs from original labels and output')


def report(manifest, checkpoint):
    records = checkpoint['records']; completed = [r for r in records if r['status'] == 'succeeded']
    samples = {s['id']: s for s in manifest['samples']}
    seconds = sum(samples[r['id']]['duration_seconds'] for r in completed)
    wall = sum(r['wall_seconds'] for r in completed)
    groups = {}
    for label in sorted({s['author_sex_label'] for s in manifest['samples']}):
        group = [r['score'] for r in completed if samples[r['id']]['author_sex_label'] == label]
        groups[label] = aggregate(group) if group else None
    return {'schema': 'local-asr-report-v1', 'fingerprint': checkpoint['fingerprint'],
            'planned': len(samples), 'succeeded': len(completed),
            'failed': sum(r['status'] == 'failed' for r in records),
            'running': sum(r['status'] == 'running' for r in records),
            'complete': len(completed) == len(samples), 'quality_verified': False,
            'scores': aggregate([r['score'] for r in completed]) if completed else None,
            'groups_by_author_sex_label': groups, 'audio_seconds': seconds, 'wall_seconds': wall,
            'real_time_factor': wall / seconds if seconds else None,
            'wall_includes': 'cold process/model startup, fixed-weight integrity checks, decode, inference; excludes dataset download and initial ready check',
            'machine': {'system': platform.system(), 'architecture': platform.machine()},
            'cloud_calls': 0, 'deployment': 'local', 'limits': manifest['limits'],
            'cost': 'No paid inference API; electricity/hardware/operation costs not measured'}


def run(out, max_new, retry_failed):
    from generic_agents.local_audio import call, ready
    from generic_agents.local_specialists import node_lock
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest['configuration'] != CONFIGURATION or manifest['normalization'] != NORMALIZATION:
        raise ValueError('Frozen evaluation protocol differs from runner')
    for sample in manifest['samples']:
        path = out / sample['path']
        if path.resolve().parent != (out / 'samples').resolve() or path.is_symlink() or file_hash(path) != sample['sha256']:
            raise ValueError('Original sample missing, changed, or outside frozen corpus')
    frozen = fingerprint(manifest); checkpoint = load_checkpoint(out, frozen)
    validate_records(manifest, checkpoint); ready()
    processed = 0
    for sample in manifest['samples']:
        old = next((r for r in checkpoint['records'] if r['id'] == sample['id']), None)
        if old and (old['status'] == 'succeeded' or old['status'] == 'failed' and not retry_failed): continue
        if max_new is not None and processed >= max_new: break
        previous_attempts = [] if old is None else old.get('previous_attempts', []) + [{k: v for k, v in old.items() if k != 'previous_attempts'}]
        if old: checkpoint['records'].remove(old)
        row = {'id': sample['id'], 'input_sha256': sample['sha256'], 'status': 'running',
               'started_unix': time.time(), 'previous_attempts': previous_attempts}
        checkpoint['records'].append(row); save(out / 'checkpoint.json', checkpoint)
        start = time.monotonic()
        try:
            with node_lock(): result = call((out / sample['path']).read_bytes(), CONFIGURATION)
            row.update(status='succeeded', result=result, result_sha256=digest(result),
                       score=score(sample['reference_text'], result['transcription']['text']))
        except Exception as error:
            row.update(status='failed', error=str(error)[:800])
        finally:
            row['wall_seconds'] = time.monotonic() - start
            save(out / 'checkpoint.json', checkpoint)
            save(out / 'report.json', report(manifest, checkpoint))
        processed += 1
        print(json.dumps({'id': row['id'], 'status': row['status'], 'completed': sum(r['status'] == 'succeeded' for r in checkpoint['records']),
                          'wall_seconds': round(row['wall_seconds'], 3)}), flush=True)
    validate_records(manifest, checkpoint)
    summary = report(manifest, checkpoint); save(out / 'report.json', summary)
    print(json.dumps({k: summary[k] for k in ('planned', 'succeeded', 'failed', 'complete', 'cloud_calls')}), flush=True)
    return 1 if summary['failed'] else 0


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--out', type=Path, required=True); parser.add_argument('--archive', type=Path)
    parser.add_argument('--max-new', type=int); parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if args.max_new is not None and args.max_new < 0: parser.error('--max-new must be nonnegative')
    if args.action == 'prepare':
        if not args.archive: parser.error('--archive is required for preparation')
        prepare(args.archive, args.out); return 0
    return run(args.out, args.max_new, args.retry_failed)


if __name__ == '__main__': sys.exit(main())
