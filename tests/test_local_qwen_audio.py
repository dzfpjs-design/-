"""Candidate guard tests; actual inference evidence is separately preserved."""
import copy
import json
from pathlib import Path

import pytest

from generic_agents import local_qwen_audio as local

ROOT = Path(__file__).resolve().parents[1]


def original():
    result = json.loads((ROOT/'docs/evidence/qwen-asr-sdpa-probe-v06/result.json').read_text())
    manifest = json.loads((ROOT/'docs/evidence/mandarin-asr-quality-v06/manifest.json').read_text())
    audio = (ROOT/'docs/evidence/mandarin-asr-quality-v06'/manifest['samples'][0]['path']).read_bytes()
    return result, audio


def test_real_candidate_output_obeys_local_trace():
    result, audio = original()
    local.validate(result, audio, {'channel': 'mono', 'language': 'zh'})
    assert local.RECIPE['cloud_fallback'] is False
    assert local.RECIPE['business_registration_verified'] is False


@pytest.mark.parametrize('section,key,value', [
    (None, 'deployment', 'cloud'), (None, 'cloud_calls', 1),
    (None, 'input_sha256', 'changed'), ('model', 'remote_code', True),
    ('model', 'native_architecture', False), ('model', 'revision', 'changed'),
    ('model', 'recipe_sha256', 'changed'), ('model', 'context_or_hotwords_used', True),
    ('model', 'attention_implementation', 'changed'),
])
def test_invalid_trace_is_rejected(section, key, value):
    result, audio = original()
    result = copy.deepcopy(result)
    (result[section] if section else result)[key] = value
    with pytest.raises(ValueError):
        local.validate(result, audio, {'channel': 'mono', 'language': 'zh'})


def test_unsupported_request_never_starts_inference(monkeypatch):
    monkeypatch.setattr(local, 'ready', lambda: pytest.fail('unsupported request reached runtime'))
    with pytest.raises(ValueError):
        local.call(b'audio', {'channel': 'mono', 'language': 'en'})


def test_missing_model_fails_before_any_process(monkeypatch, tmp_path):
    monkeypatch.setattr(local, 'WEIGHTS', tmp_path)
    monkeypatch.setattr(local.subprocess, 'Popen', lambda *a, **k: pytest.fail('missing model started process'))
    with pytest.raises(ValueError):
        local.call(b'audio', {'channel': 'mono', 'language': 'zh'})


def test_child_gets_offline_flags_and_no_cloud_credentials(monkeypatch):
    monkeypatch.setattr(local, 'ready', lambda: None)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-sentinel')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-only-sentinel')
    def reject_start(*args, **kwargs):
        env = kwargs['env']
        assert 'DEEPSEEK_API_KEY' not in env and 'DASHSCOPE_API_KEY' not in env
        assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
        raise OSError('test child unavailable')
    monkeypatch.setattr(local.subprocess, 'Popen', reject_start)
    with pytest.raises(OSError, match='test child unavailable'):
        local.call(b'audio', {'channel': 'mono', 'language': 'zh'})
