import copy
import importlib.util
import sys
from pathlib import Path
from collections import Counter
import pytest
from generic_agents.asr_character_quality import character_score, aggregate_characters

scripts = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('benchmark_mandarin_asr', scripts / 'benchmark_mandarin_asr.py')
benchmark = importlib.util.module_from_spec(spec); spec.loader.exec_module(benchmark)


@pytest.mark.parametrize('reference,hypothesis,expected', [
    ('林场发现火情', '林场发现疫情', (1, 0, 0)),
    ('巡护员到达', '巡护到达', (0, 1, 0)),
    ('森林', '森林火', (0, 0, 1)),
    ('森林', '', (0, 2, 0)),
    ('十二', '12', (2, 0, 0)),
])
def test_actual_character_units_not_whole_chinese_sentence_tokens(reference, hypothesis, expected):
    value = character_score(reference, hypothesis)
    assert 'wer' not in value
    assert tuple(value['cer'][k] for k in ['substitutions', 'deletions', 'insertions']) == expected


def test_unicode_and_weighted_character_aggregation():
    result = character_score('林场Ａ１２。', '林场a12')
    assert result['reference_characters'] == list('林场a12') and result['cer']['rate'] == 0
    assert aggregate_characters([character_score('林', '木'), character_score('森林林林林林林林林', '森林林林林林林林林')])['cer']['rate'] == .1
    with pytest.raises(ValueError): character_score('。 ', '森林')
    with pytest.raises(ValueError): aggregate_characters([])


def test_author_literal_quotes_and_invalid_schema_are_not_changed():
    row = '1\t123.wav\t"原文"\t"原文"\t原 文\t160000\tMALE\n'
    parsed = benchmark.label_rows(row.encode())
    assert parsed[0]['reference_text'] == '"原文"'
    for bad in ['1\t123.wav\t', row.replace('MALE', 'UNKNOWN'), row + row]:
        with pytest.raises(ValueError): benchmark.label_rows(bad.encode())


def fixture_samples():
    return [{'id': str(i), 'file': str(i)+'.wav', 'sentence_id': str(i//2),
             'author_gender_label': 'FEMALE' if i%2 else 'MALE', 'duration_seconds': 10} for i in range(160)]


def test_selection_is_frozen_balanced_and_no_duplicate_sentences():
    rows = fixture_samples(); selected = benchmark.select(rows)
    assert selected == benchmark.select(list(reversed(rows)))
    assert len(selected) == len(set(s['sentence_id'] for s in selected)) == 40
    assert Counter(s['author_gender_label'] for s in selected) == {'MALE': 20, 'FEMALE': 20}
    with pytest.raises(ValueError, match='Cannot fill'):
        benchmark.select([r for r in rows if r['author_gender_label']=='MALE'])


def saved_result():
    sample = {**fixture_samples()[0], 'sha256': 'fixed', 'reference_text': '森林'}
    raw = {'input_sha256': 'fixed', 'deployment': 'local', 'backend': 'cpu', 'cloud_calls': 0,
           'audio': {'duration_seconds': 10, 'sample_rate': 16000}, 'transcription': {'text': '森林火'}}
    row = {'id': sample['id'], 'input_sha256': 'fixed', 'status': 'succeeded', 'result': raw,
           'result_sha256': benchmark.digest(raw), 'score': character_score('森林','森林火'), 'wall_seconds': 6}
    return {'samples': [sample], 'limits': ['No domain acceptance']}, {'fingerprint': {'pin':'fixed'}, 'records': [row]}


@pytest.mark.parametrize('change', ['result', 'score', 'input', 'cloud', 'duration', 'duplicate'])
def test_saved_output_corruption_cannot_enter_report(change):
    manifest, checkpoint = saved_result(); row = checkpoint['records'][0]
    if change == 'result': row['result']['transcription']['text'] = '森林'
    if change == 'score': row['score']['cer']['errors'] = 0
    if change == 'input': row['input_sha256'] = 'another'
    if change == 'cloud': row['result']['cloud_calls']=1; row['result_sha256']=benchmark.digest(row['result'])
    if change == 'duration': row['result']['audio']['duration_seconds']=11; row['result_sha256']=benchmark.digest(row['result'])
    if change == 'duplicate': checkpoint['records'].append(copy.deepcopy(row))
    with pytest.raises(ValueError): benchmark.validate_records(manifest, checkpoint)


def test_report_discloses_unstarted_failure_and_success_separately():
    manifest, checkpoint = saved_result(); benchmark.validate_records(manifest, checkpoint)
    manifest['samples'] += [{**manifest['samples'][0], 'id':'two'}, {**manifest['samples'][0], 'id':'three'}]
    checkpoint['records'].append({'id':'two', 'status':'failed', 'input_sha256':'fixed'})
    report=benchmark.report(manifest, checkpoint)
    assert (report['planned'], report['succeeded'], report['failed'], report['not_started'], report['complete']) == (3,1,1,1,False)
    assert not report['quality_verified'] and not report['word_error_rate_applicable']
    assert report['scores']['cer']['rate']==.5 and report['scores']['cases']==1
