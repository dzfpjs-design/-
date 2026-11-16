"""QA checkpoint corruption/recovery tests; no mocked inference called real."""
import copy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('benchmark_local_asr', Path(__file__).resolve().parents[1] / 'scripts/benchmark_local_asr.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def fixture_records():
    manifest = {'samples': [{'id': 's1', 'sha256': 'clip-hash', 'reference_text': 'a b',
                            'author_sex_label': 'F', 'duration_seconds': 3}], 'limits': ['no domain acceptance']}
    raw = {'deployment': 'local', 'cloud_calls': 0, 'input_sha256': 'clip-hash', 'transcription': {'text': 'a'}}
    row = {'id': 's1', 'input_sha256': 'clip-hash', 'status': 'succeeded', 'result': raw,
           'result_sha256': benchmark.digest(raw), 'score': benchmark.score('a b', 'a'), 'wall_seconds': 2}
    return manifest, {'schema': 'local-asr-checkpoint-v1', 'fingerprint': {'manifest_sha256': 'fixed'}, 'records': [row]}


def test_saved_result_reopened_and_rescored_from_original(tmp_path):
    manifest, value = fixture_records()
    benchmark.save(tmp_path / 'checkpoint.json', value)
    restored = benchmark.load_checkpoint(tmp_path, value['fingerprint'])
    assert restored == value
    benchmark.validate_records(manifest, restored)
    report = benchmark.report(manifest, restored)
    assert report['complete'] and report['scores']['wer']['rate'] == .5
    assert report['quality_verified'] is False
    assert report['real_time_factor'] == 2/3


def test_configuration_change_cannot_resume_original_checkpoint(tmp_path):
    _, value = fixture_records()
    benchmark.save(tmp_path / 'checkpoint.json', value)
    with pytest.raises(ValueError, match='configuration changed'):
        benchmark.load_checkpoint(tmp_path, {'manifest_sha256': 'different'})


@pytest.mark.parametrize('change', ['raw', 'score', 'foreign', 'duplicate', 'input', 'cloud'])
def test_corrupted_result_cannot_contribute_quality_score(change):
    manifest, saved = fixture_records(); value = copy.deepcopy(saved); row = value['records'][0]
    if change == 'raw': row['result']['transcription']['text'] = 'a b'
    if change == 'score': row['score']['wer']['errors'] = 0
    if change == 'foreign': row['id'] = 'other'
    if change == 'duplicate': value['records'].append(copy.deepcopy(row))
    if change == 'input': row['input_sha256'] = 'new-input'
    if change == 'cloud':
        row['result']['cloud_calls'] = 1; row['result_sha256'] = benchmark.digest(row['result'])
    with pytest.raises(ValueError): benchmark.validate_records(manifest, value)


def test_failure_and_incomplete_case_are_disclosed_not_counted_correct():
    manifest, checkpoint = fixture_records()
    manifest['samples'].append({**manifest['samples'][0], 'id': 's2'})
    checkpoint['records'].append({'id': 's2', 'status': 'failed', 'input_sha256': 'clip-hash', 'error': 'no result'})
    report = benchmark.report(manifest, checkpoint)
    assert (report['planned'], report['succeeded'], report['failed'], report['complete']) == (2, 1, 1, False)
    assert report['scores']['cases'] == 1 and report['scores']['wer']['rate'] == .5


def test_original_flac_duration_is_read_from_streaminfo():
    # Independent constructed STREAMINFO for a 5-second mono 16kHz recording.
    bits = (16000 << 44) | (15 << 36) | 80000
    raw = b'fLaC' + bytes([0, 0, 0, 34]) + bytes(10) + bits.to_bytes(8, 'big') + bytes(16)
    assert benchmark.flac_info(raw) == (16000, 1, 5)
    with pytest.raises(ValueError, match='STREAMINFO'): benchmark.flac_info(b'not-flac')
