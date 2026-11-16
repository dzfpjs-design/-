import copy
import hashlib
import json
import shutil
import pytest
from generic_agents import local_model_evaluations as evaluations


def test_public_reader_matches_verified_real_results():
    result = evaluations.read_asr_comparison()
    assert len(result['cases']) == 40 and not result['candidate_business_ready']
    assert [m['cer']['errors'] for m in result['models']] == [108, 87]
    assert [m['digit_cer']['errors'] for m in result['models']] == [9, 24]
    audio, case = evaluations.original_audio(result['cases'][0]['id'])
    assert hashlib.sha256(audio).hexdigest() == case['input_sha256']
    assert '待业务接入' in evaluations.export_markdown(result).decode()
    with pytest.raises(LookupError): evaluations.original_audio('../secret')


@pytest.mark.parametrize('target', ['manifest.json', 'checkpoint.json', 'sample', 'missing'])
def test_changed_or_missing_evidence_never_looks_complete(tmp_path, monkeypatch, target):
    source = evaluations.EVIDENCE
    for name, *_ in evaluations.PACKS:
        shutil.copytree(source/name, tmp_path/name)
    base = tmp_path/evaluations.PACKS[0][0]
    if target == 'sample':
        first = json.loads((base/'manifest.json').read_text())['samples'][0]
        (base/first['path']).write_bytes(b'changed audio')
    elif target == 'missing':
        (base/'checkpoint.json').unlink()
    else:
        p = base/target; data = json.loads(p.read_text())
        if target == 'manifest.json': data['samples'][0]['raw_reference_text'] = 'changed label'
        else: data['records'][0]['result']['transcription']['text'] = 'changed output'
        p.write_text(json.dumps(data))
    monkeypatch.setattr(evaluations, 'EVIDENCE', tmp_path)
    with pytest.raises(ValueError): evaluations.read_asr_comparison()


def test_authenticated_read_export_audio_and_foreign_scope(client):
    response = client.get('/api/specialists/evaluations/asr')
    assert response.status_code == 200
    result = response.json()
    audio = client.get('/api/specialists/evaluations/asr/audio/'+result['cases'][0]['id'])
    assert audio.status_code == 200 and hashlib.sha256(audio.content).hexdigest() == audio.headers['x-content-sha256']
    assert client.get('/api/specialists/evaluations/asr/audio/'+result['cases'][0]['id']+'?workspace_id=foreign').status_code == 403
    assert client.get('/api/specialists/evaluations/asr/audio/unknown').status_code == 404
    exported = client.get('/api/specialists/evaluations/asr/export')
    assert exported.status_code == 200 and hashlib.sha256(exported.content).hexdigest() == exported.headers['x-content-sha256']
    assert client.get('/api/specialists/evaluations/asr/export').content == exported.content
    client.cookies.clear()
    assert client.get('/api/specialists/evaluations/asr').status_code == 401
    assert client.get('/api/specialists/evaluations/asr/export').status_code == 401
    assert client.get('/api/specialists/evaluations/asr/audio/'+result['cases'][0]['id']).status_code == 401


def test_corrupt_source_api_shows_error_not_empty_success(client, monkeypatch):
    monkeypatch.setattr(evaluations, 'read_asr_comparison', lambda: (_ for _ in ()).throw(ValueError('changed')))
    assert client.get('/api/specialists/evaluations/asr').status_code == 503
    assert client.get('/api/specialists/evaluations/asr/export').status_code == 503
