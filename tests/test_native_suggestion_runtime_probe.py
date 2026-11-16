"""Probe safety tests; mocked HTTP is never actual provider quality evidence."""
import json
from hashlib import sha256

import httpx
import pytest

from tests.native_suggestion_runtime_probe import install


def prepare(monkeypatch, tmp_path, sender):
    monkeypatch.setenv('GA_NATIVE_SUGGESTION_PROBE_DIR', str(tmp_path))
    monkeypatch.setenv('GA_NATIVE_SUGGESTION_FORBIDDEN_IDS', '["local-source-id-123"]')
    monkeypatch.setattr(httpx.Client, 'send', sender)
    install()


def request(content):
    return httpx.Request('POST', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        headers={'Authorization': 'Bearer PROBE_TEST_SECRET'},
        json={'model': 'qwen-plus', 'messages': [{'role': 'user', 'content': content}],
              'max_tokens': 128})


@pytest.mark.parametrize('value', ['NMS_PRIVATE_RAW_TEXT', 'local-source-id-123'])
def test_probe_blocks_private_content_before_transport(monkeypatch, tmp_path, value):
    calls = []
    def sender(client, req, *args, **kwargs):
        calls.append(req)
        return httpx.Response(200, json={'usage': {'total_tokens': 7}}, request=req)
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client, pytest.raises(AssertionError): client.send(request(value))
    assert calls == [] and not list(tmp_path.iterdir())


def test_probe_captures_only_allowed_body_and_enforces_call_limit(monkeypatch, tmp_path):
    calls = []
    def sender(client, req, *args, **kwargs):
        calls.append(req)
        return httpx.Response(200, json={'usage': {'total_tokens': 7}}, request=req)
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client:
        for _ in range(3): client.send(request('{"field_alias":"F1","type":"number"}'))
        with pytest.raises(AssertionError): client.send(request('F1'))
    assert len(calls) == 3
    artifacts = list(tmp_path.glob('request-*.json')); assert len(artifacts) == 3
    assert all('PROBE_TEST_SECRET' not in file.read_text() for file in artifacts)
    assert all(json.loads(file.read_text())['provider_usage']['total_tokens'] == 7 for file in artifacts)


def test_probe_unknown_transport_preserves_attempt_without_headers(monkeypatch, tmp_path):
    def sender(client, req, *args, **kwargs): raise httpx.ReadTimeout('private diagnostic', request=req)
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client, pytest.raises(httpx.ReadTimeout): client.send(request('F1'))
    proof = json.loads((tmp_path / 'request-1.json').read_text())
    assert proof['response_unknown'] and proof['transport_error_type'] == 'ReadTimeout'
    assert not proof['sent_to_real_provider']
    assert 'private diagnostic' not in (tmp_path / 'request-1.json').read_text()


def test_probe_explicit_two_call_limit_blocks_third_before_transport(monkeypatch, tmp_path):
    calls = []
    def sender(client, req, *args, **kwargs):
        calls.append(req)
        return httpx.Response(200, json={'usage': {'total_tokens': 7}}, request=req)
    monkeypatch.setenv('GA_NATIVE_SUGGESTION_HTTP_LIMIT', '2')
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client:
        client.send(request('F1'))
        client.send(request('F2'))
        with pytest.raises(AssertionError, match='frozen real-call limit'):
            client.send(request('F3'))
    assert len(calls) == 2
    assert len(list(tmp_path.glob('request-*.json'))) == 2


@pytest.mark.parametrize('capture', [False, True])
def test_probe_final_content_capture_is_opt_in_and_excludes_reasoning(monkeypatch, tmp_path, capture):
    final_content = '{"selected":[{"alias":"F1","template":"field_verification"}]}'
    def sender(client, req, *args, **kwargs):
        return httpx.Response(200, headers={'X-Secret': 'PRIVATE_RESPONSE_HEADER'}, json={
            'model': 'reported-version', 'usage': {'total_tokens': 7},
            'choices': [{'finish_reason': 'stop', 'message': {
                'content': final_content, 'reasoning_content': 'PRIVATE_REASONING'}}],
        }, request=req)
    if capture:
        monkeypatch.setenv('GA_NATIVE_SUGGESTION_CAPTURE_RESPONSE', '1')
    else:
        monkeypatch.delenv('GA_NATIVE_SUGGESTION_CAPTURE_RESPONSE', raising=False)
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client:
        client.send(request('F1'))
    artifact = (tmp_path / 'request-1.json').read_text()
    proof = json.loads(artifact)
    assert not any(secret in artifact for secret in [
        'PROBE_TEST_SECRET', 'PRIVATE_RESPONSE_HEADER', 'PRIVATE_REASONING'])
    if capture:
        assert proof['response_final_content'] == final_content
        assert proof['response_content_sha256'] == sha256(final_content.encode()).hexdigest()
        assert proof['reported_model'] == 'reported-version'
        assert proof['finish_reason'] == 'stop'
    else:
        assert 'response_final_content' not in proof
        assert 'response_content_sha256' not in proof


def test_probe_oversized_final_content_keeps_hash_without_content(monkeypatch, tmp_path):
    final_content = '中' * 7000
    def sender(client, req, *args, **kwargs):
        return httpx.Response(200, json={'choices': [{'message': {
            'content': final_content}}]}, request=req)
    monkeypatch.setenv('GA_NATIVE_SUGGESTION_CAPTURE_RESPONSE', '1')
    prepare(monkeypatch, tmp_path, sender)
    with httpx.Client() as client:
        client.send(request('F1'))
    proof = json.loads((tmp_path / 'request-1.json').read_text())
    assert proof['response_content_sha256'] == sha256(final_content.encode()).hexdigest()
    assert proof['response_content_not_saved_reason'] == 'exceeds_evidence_limit'
    assert 'response_final_content' not in proof
