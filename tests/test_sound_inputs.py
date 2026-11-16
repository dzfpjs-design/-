"""Pure original-audio contracts; these tests perform no model inference."""
import hashlib
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import sound_inputs as source
from generic_agents.sound_api import SoundReview, audio_response
from starlette.requests import Request


def intent(**changes):
    value = {'schema': 'sound-upload-v1', 'request_id': str(uuid4()), 'name': '公开声音.wav',
        'media_type': 'audio/wav', 'size_bytes': 12, 'input_sha256': hashlib.sha256(b'RIFF0000WAVE').hexdigest(),
        'configuration': {'channel': 'mono', 'threshold': .5}}
    return {**value, **changes}


def test_upload_retains_legal_exponent_threshold_without_javascript_rehash():
    raw = json.dumps(intent(configuration={'channel': 'mix', 'threshold': 1e-7}), ensure_ascii=False)
    parsed = source.parse_intent(raw)
    assert parsed.configuration.threshold == 1e-7
    assert parsed.configuration.channel == 'mix'


@pytest.mark.parametrize('value', [0, 1, -1, float('nan'), float('inf')])
def test_threshold_rejects_nonfinite_or_closed_boundaries(value):
    with pytest.raises(HTTPException) as caught:
        source.parse_intent(json.dumps(intent(configuration={'channel': 'mono', 'threshold': value})))
    assert caught.value.status_code == 422


@pytest.mark.parametrize('changes', [{'size_bytes': True}, {'name': '../外部.wav'}, {'request_id': str(uuid4()).upper()},
    {'configuration': {'channel': '9', 'threshold': .5}}, {'unknown': 'cannot broaden upload'}])
def test_upload_contract_rejects_ambiguous_or_extra_fields(changes):
    with pytest.raises(HTTPException) as caught:
        source.parse_intent(json.dumps(intent(**changes)))
    assert caught.value.status_code == 422


def test_original_header_is_checked_without_decoding_or_model_installation():
    source.inspect_header(b'RIFF0000WAVE', 'audio/wav')
    with pytest.raises(HTTPException) as caught:
        source.inspect_header(b'OggSnot-a-wav', 'audio/wav')
    assert caught.value.status_code == 415


def test_review_preserves_unmodified_note_and_requires_strict_cas():
    value = {'schema': 'sound-review-v1', 'request_id': 'sound-review-original', 'version': 2,
        'verdict': 'uncertain', 'note': '尚不能认定现场事件；原分数保留。'}
    assert SoundReview.model_validate(value).model_dump(mode='json') == value
    with pytest.raises(ValueError):
        SoundReview.model_validate({**value, 'version': True})


def test_byte_range_reports_actual_partial_bytes_but_keeps_full_original_sha():
    from types import SimpleNamespace
    raw = b'RIFF0000WAVE'
    evidence = SimpleNamespace(sha256=hashlib.sha256(raw).hexdigest(), media_type='audio/wav')
    request = Request({'type': 'http', 'headers': [(b'range', b'bytes=2-4')]})
    response = audio_response(request, evidence, raw, '公开.wav')
    assert response.status_code == 206 and response.body == raw[2:5]
    assert response.headers['content-range'] == 'bytes 2-4/12'
    assert response.headers['x-content-sha256'] == evidence.sha256
    with pytest.raises(HTTPException) as caught:
        audio_response(Request({'type': 'http', 'headers': [(b'range', b'bytes=0-1,3-4')]}), evidence, raw, '公开.wav')
    assert caught.value.status_code == 416
