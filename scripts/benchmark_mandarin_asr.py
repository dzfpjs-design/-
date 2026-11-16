"""Real, offline Mandarin QA with pinned author labels; never a production gate."""
import argparse
import hashlib
import json
import platform
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from benchmark_local_asr import digest, file_hash, save, load_checkpoint
from generic_agents.asr_character_quality import CHARACTER_PROTOCOL, character_score, aggregate_characters

AUTHOR_REVISION = '70bb2e84b976b7e960aa89f1c648e09c59f894dd'
ARCHIVE_SHA256 = '09d19ad18f5d7e91076880807e866cd16abd924c7052b55f71cdae91714fc166'
LABEL_GIT_BLOB = '3c520b03f63be24c7096021605a95a351038d541'
LABEL_SHA256 = '5734461648f816181d7dab5fc79204b18c4b9bc2cd5138225b25c72d18385d21'
CONFIGURATION = {'channel': 'mono', 'language': 'zh'}
SELECTION = 'fleurs-cmn-asr-v1: SHA256(seed|wav name) rank; 20 FEMALE + 20 MALE; unique sentence IDs; author duration 4-30s; no output-based substitution'


def label_rows(content):
    # Match the fixed author loader: these are literal TSV fields, not CSV quotes.
    rows = [line.strip().split('\t') for line in content.decode('utf-8').splitlines()]
    result = []; seen = set()
    for row in rows:
        if len(row) != 7 or not row[0].isdigit() or not re.fullmatch(r'\d+\.wav', row[1]):
            raise ValueError('Author TSV schema differs from seven original columns')
        if row[1] in seen or row[6] not in {'MALE', 'FEMALE'}:
            raise ValueError('Duplicate recording or unsupported author gender label')
        seen.add(row[1]); frames = int(row[5])
        if frames <= 0: raise ValueError('Invalid author num_samples')
        character_score(row[3], '')
        result.append({'id': Path(row[1]).stem, 'file': row[1], 'sentence_id': row[0],
                       'raw_reference_text': row[2], 'reference_text': row[3],
                       'author_character_text': row[4], 'author_gender_label': row[6],
                       'author_num_samples': frames, 'duration_seconds': frames / 16000})
    if not result: raise ValueError('No author transcription rows')
    return result


def select(rows):
    ranked = sorted(rows, key=lambda s: hashlib.sha256(('fleurs-cmn-asr-v1|' + s['file']).encode()).hexdigest())
    selected = []; sentences = set(); counts = {'FEMALE': 0, 'MALE': 0}
    for sample in ranked:
        label = sample['author_gender_label']
        if not 4 <= sample['duration_seconds'] <= 30 or sample['sentence_id'] in sentences or counts[label] >= 20:
            continue
        selected.append(sample); sentences.add(sample['sentence_id']); counts[label] += 1
    if counts != {'FEMALE': 20, 'MALE': 20}: raise ValueError('Cannot fill frozen balanced distinct-sentence cohort')
    return selected


def prepare(archive, labels, out):
    if (out / 'manifest.json').exists(): raise ValueError('Existing frozen manifest cannot be replaced')
    if file_hash(archive) != ARCHIVE_SHA256: raise ValueError('Archive differs from fixed author SHA256')
    content = labels.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    if blob != LABEL_GIT_BLOB or hashlib.sha256(content).hexdigest() != LABEL_SHA256:
        raise ValueError('Labels differ from fixed author Git blob and SHA256')
    rows = label_rows(content); samples = select(rows); wanted = {s['file']: s for s in samples}; found = set()
    with tarfile.open(archive, 'r:gz') as corpus:
        for member in corpus:
            if not member.isfile() or not re.fullmatch(r'test/\d+\.wav', member.name): continue
            filename = Path(member.name).name
            if filename not in wanted: continue
            if filename in found or not 0 < member.size <= 20 * 1024 * 1024:
                raise ValueError('Duplicate, empty or oversized selected original')
            raw = corpus.extractfile(member).read(); sample = wanted[filename]
            path = out / 'samples' / filename; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            sample.update(path='samples/' + filename, sha256=hashlib.sha256(raw).hexdigest())
            found.add(filename)
    if found != set(wanted): raise ValueError('Selected recording missing from author archive')
    manifest = {'schema': 'mandarin-asr-manifest-v1', 'dataset': 'Google FLEURS cmn_hans_cn test',
                'source': 'https://huggingface.co/datasets/google/fleurs/tree/' + AUTHOR_REVISION + '/data/cmn_hans_cn',
                'author_revision': AUTHOR_REVISION, 'archive_sha256': ARCHIVE_SHA256,
                'label_git_blob': LABEL_GIT_BLOB, 'label_sha256': LABEL_SHA256,
                'reference_column': 'author transcription, TSV index 3; raw index 2 preserved separately',
                'license': 'CC BY 4.0',
                'attribution': 'FLEURS (2022): Alexis Conneau, Min Ma, Simran Khanuja, Yu Zhang, Vera Axelrod, Siddharth Dalmia, Jason Riesa, Clara Rivera, Ankur Bapna; Google Research',
                'sample_count': len(samples), 'selection': SELECTION,
                'duration_source': 'author num_samples at 16kHz; validated against real decoder during inference',
                'configuration': CONFIGURATION, 'protocol': CHARACTER_PROTOCOL, 'samples': samples,
                'limits': ['Public Mandarin read speech, not forestry/field/noise/dialect acceptance',
                           'Balanced gender and length selection is not the natural corpus distribution',
                           'No speaker IDs available; 40 recordings must not be called 40 speakers',
                           'Training overlap unknown; no candidate model or human baseline comparison']}
    save(out / 'manifest.json', manifest)
    (out / 'author-test.tsv').write_bytes(content)
    print(json.dumps({'prepared_samples': len(samples), 'manifest_sha256': digest(manifest)}))


def fingerprint(manifest):
    recipe = json.loads((ROOT / 'model-packs/sensevoice-small-v1.json').read_text())
    return {'manifest_sha256': digest(manifest), 'model_pack': recipe['pack_id'], 'model_pack_version': recipe['version'],
            'recipe_sha256': digest(recipe), 'configuration': CONFIGURATION, 'protocol': CHARACTER_PROTOCOL,
            'model_runner_sha256': file_hash(ROOT / 'src/generic_agents/local_audio_runner.py'),
            'character_metric_sha256': file_hash(ROOT / 'src/generic_agents/asr_character_quality.py'),
            'metric_support_sha256': file_hash(ROOT / 'src/generic_agents/asr_quality.py'),
            'checkpoint_support_sha256': file_hash(ROOT / 'scripts/benchmark_local_asr.py'),
            'benchmark_code_sha256': file_hash(Path(__file__))}


def validate_records(manifest, checkpoint):
    samples = {s['id']: s for s in manifest['samples']}; seen = set()
    for record in checkpoint['records']:
        identity = record['id']
        if identity in seen or identity not in samples: raise ValueError('Duplicate or foreign result')
        seen.add(identity); sample = samples[identity]
        if record['input_sha256'] != sample['sha256']: raise ValueError('Saved input differs from original')
        if record['status'] not in {'running', 'failed', 'succeeded'}: raise ValueError('Invalid saved status')
        if record['status'] == 'succeeded':
            result = record['result']
            if record['result_sha256'] != digest(result) or result['input_sha256'] != sample['sha256']:
                raise ValueError('Saved original result digest changed')
            if result.get('deployment') != 'local' or result.get('backend') != 'cpu' or result.get('cloud_calls') != 0:
                raise ValueError('Original result not local CPU inference')
            if result['audio']['sample_rate'] != 16000 or abs(result['audio']['duration_seconds'] - sample['duration_seconds']) > 1e-6:
                raise ValueError('Decoded duration differs from author sample count')
            if character_score(sample['reference_text'], result['transcription']['text']) != record['score']:
                raise ValueError('Saved CER differs from original reference and output')


def report(manifest, checkpoint):
    samples = {s['id']: s for s in manifest['samples']}; rows = checkpoint['records']
    completed = [r for r in rows if r['status'] == 'succeeded']
    audio = sum(samples[r['id']]['duration_seconds'] for r in completed)
    wall = sum(r['wall_seconds'] for r in completed)
    groups = {}
    for gender in ['FEMALE', 'MALE']:
        chosen = [r['score'] for r in completed if samples[r['id']]['author_gender_label'] == gender]
        groups['author_gender_' + gender] = aggregate_characters(chosen) if chosen else None
    for key, predicate in [('reference_contains_ascii_latin', lambda t: any('a' <= c.lower() <= 'z' for c in t)),
                           ('reference_contains_digit', lambda t: any(c.isdigit() for c in t))]:
        chosen = [r['score'] for r in completed if predicate(samples[r['id']]['reference_text'])]
        groups[key] = aggregate_characters(chosen) if chosen else None
    failed = sum(r['status'] == 'failed' for r in rows); running = sum(r['status'] == 'running' for r in rows)
    return {'schema': 'mandarin-asr-report-v1', 'fingerprint': checkpoint['fingerprint'], 'planned': len(samples),
            'succeeded': len(completed), 'failed': failed, 'running': running,
            'not_started': len(samples) - len(rows), 'complete': len(completed) == len(samples),
            'primary_metric': 'CER', 'word_error_rate_applicable': False,
            'scores': aggregate_characters([r['score'] for r in completed]) if completed else None,
            'groups': groups, 'audio_seconds': audio, 'wall_seconds': wall, 'real_time_factor': wall / audio if audio else None,
            'wall_includes': 'cold model startup, weight checks, decode, inference, scoring; excludes dataset download and initial ready check',
            'machine': {'system': platform.system(), 'architecture': platform.machine()},
            'deployment': 'local', 'cloud_calls': 0, 'quality_verified': False,
            'cost': 'No paid inference API; electricity/hardware/operation costs not measured', 'limits': manifest['limits']}


def run(out, max_new, retry_failed):
    from generic_agents.local_audio import call, ready
    from generic_agents.local_specialists import node_lock
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest['schema'] != 'mandarin-asr-manifest-v1' or manifest['configuration'] != CONFIGURATION or manifest['protocol'] != CHARACTER_PROTOCOL:
        raise ValueError('Frozen Mandarin protocol differs from current runner')
    for sample in manifest['samples']:
        path = out / sample['path']
        if path.resolve().parent != (out / 'samples').resolve() or path.is_symlink() or file_hash(path) != sample['sha256']:
            raise ValueError('Original Mandarin sample changed or unavailable')
    checkpoint = load_checkpoint(out, fingerprint(manifest)); validate_records(manifest, checkpoint); ready()
    processed = 0
    for sample in manifest['samples']:
        old = next((r for r in checkpoint['records'] if r['id'] == sample['id']), None)
        if old and (old['status'] == 'succeeded' or old['status'] == 'failed' and not retry_failed): continue
        if max_new is not None and processed >= max_new: break
        previous = [] if old is None else old.get('previous_attempts', []) + [{k: v for k, v in old.items() if k != 'previous_attempts'}]
        if old: checkpoint['records'].remove(old)
        row = {'id': sample['id'], 'input_sha256': sample['sha256'], 'status': 'running',
               'started_unix': time.time(), 'previous_attempts': previous}
        checkpoint['records'].append(row); save(out / 'checkpoint.json', checkpoint)
        start = time.monotonic()
        try:
            with node_lock(): result = call((out / sample['path']).read_bytes(), CONFIGURATION)
            if result['audio']['sample_rate'] != 16000 or abs(result['audio']['duration_seconds'] - sample['duration_seconds']) > 1e-6:
                raise ValueError('Actual decoded duration differs from frozen author label')
            row.update(status='succeeded', result=result, result_sha256=digest(result),
                       score=character_score(sample['reference_text'], result['transcription']['text']))
        except Exception as error: row.update(status='failed', error=str(error)[:800])
        finally:
            row['wall_seconds'] = time.monotonic() - start
            save(out / 'checkpoint.json', checkpoint); save(out / 'report.json', report(manifest, checkpoint))
        processed += 1
        print(json.dumps({'id': row['id'], 'status': row['status'],
                          'completed': sum(r['status'] == 'succeeded' for r in checkpoint['records']),
                          'wall_seconds': round(row['wall_seconds'], 3)}), flush=True)
    validate_records(manifest, checkpoint); summary = report(manifest, checkpoint); save(out / 'report.json', summary)
    print(json.dumps({k: summary[k] for k in ('planned', 'succeeded', 'failed', 'not_started', 'complete', 'cloud_calls')}), flush=True)
    return 1 if summary['failed'] else 0


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--out', type=Path, required=True); parser.add_argument('--archive', type=Path); parser.add_argument('--labels', type=Path)
    parser.add_argument('--max-new', type=int); parser.add_argument('--retry-failed', action='store_true'); args = parser.parse_args()
    if args.max_new is not None and args.max_new < 0: parser.error('--max-new must be nonnegative')
    if args.action == 'prepare':
        if not args.archive or not args.labels: parser.error('--archive and --labels required')
        prepare(args.archive, args.labels, args.out); return 0
    return run(args.out, args.max_new, args.retry_failed)


if __name__ == '__main__': sys.exit(main())
