"""Pure fixed-profile/wire checks, not live provider or paid-ledger evidence.

Only HTTP transport and budget boundaries below are explicit test doubles.
The real gateway prepares the messages, policy, timeout and posted JSON. Root
executes these tests; no call, registration or successful customer run is claimed.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import cloud_input_profiles as profiles
from generic_agents import cloud_budget, gateway, services, setup_understanding as understanding
from generic_agents.contracts import Manifest, digest

PROFILE = 'qwen38-nonthinking-v1'
KEY = 'V261-SYNTHETIC-NONCREDENTIAL'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('pure profile tests prohibit external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def execution(**changes):
    return {'provider': 'qwen', 'model': 'qwen3.8-max-0902', 'max_tokens': 2048,
        'secret_ref': 'DASHSCOPE_API_KEY', **changes}


def policy(*, full=True, long=False, version=None, timeout=None):
    return {'version': version or ('cloud-full-report-2026-10-14' if long else 'cloud-bounded-text-v1'),
        'max_input_bytes': 900000 if long else 50000,
        'transport_timeout_seconds': timeout if timeout is not None else 180 if long else 60,
        'max_output_tokens': 8192, 'max_images': 0 if full else 3,
        'context_token_bound': 1000000 if long else None, 'full_report': full,
        'long_context_verified': long}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.mark.parametrize('e,options,expected_policy', [
    (execution(), {'model': 'qwen3.8-max-0902', 'max_tokens': 2048,
        'response_format': {'type': 'json_object'}}, policy()),
    (execution(model='qwen3.8-max'), {'model': 'qwen3.8-max', 'max_tokens': 2048,
        'response_format': {'type': 'json_object'}}, policy()),
    (execution(model='qwen-plus'), {'model': 'qwen-plus', 'max_tokens': 2048,
        'response_format': {'type': 'json_object'}}, policy(long=True)),
    (execution(model='qwen-plus-2025-12-01'), {'model': 'qwen-plus-2025-12-01', 'max_tokens': 2048,
        'response_format': {'type': 'json_object'}}, policy(long=True)),
    (execution(provider='deepseek', model='deepseek-v4-pro', secret_ref='DEEPSEEK_API_KEY', thinking_mode='disabled'),
        {'model': 'deepseek-v4-pro', 'max_tokens': 2048,
            'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'}}, policy(long=True)),
    (execution(provider='deepseek', model='deepseek-v4-pro', secret_ref='DEEPSEEK_API_KEY'),
        {'model': 'deepseek-v4-pro', 'max_tokens': 2048, 'response_format': {'type': 'json_object'}}, policy()),
])
def test_every_old_absent_profile_keeps_exact_option_and_policy_bytes(e, options, expected_policy):
    original = copy.deepcopy(e)
    actual_options = gateway.cloud_json_options(e)
    actual_policy = profiles.input_policy(e, full_report=True)
    assert canonical(actual_options) == canonical(options)
    assert canonical(actual_policy) == canonical(expected_policy)
    assert hashlib.sha256(canonical(actual_options)).digest() == hashlib.sha256(canonical(options)).digest()
    assert hashlib.sha256(canonical(actual_policy)).digest() == hashlib.sha256(canonical(expected_policy)).digest()
    expected_dispatch = {**options, 'enable_thinking': False} if e['provider'] == 'qwen' and expected_policy['long_context_verified'] else options
    assert profiles.request_options(e, actual_options, full_report=True) == expected_dispatch
    assert e == original


@pytest.mark.parametrize('model', ['qwen3.8-max', 'qwen3.8-max-0902'])
@pytest.mark.parametrize('full', [False, True])
def test_explicit_profile_changes_only_thinking_and_full_report_timeout(model, full):
    e = execution(model=model, request_profile=PROFILE); before = copy.deepcopy(e)
    options = gateway.cloud_json_options(e)
    assert options == {'model': model, 'max_tokens': 2048,
        'response_format': {'type': 'json_object'}, 'enable_thinking': False}
    assert 'request_profile' not in options
    expected = policy(full=full, version='cloud-qwen38-nonthinking-v1' if full else None,
        timeout=180 if full else 60)
    assert profiles.input_policy(e, full_report=full) == expected
    assert expected['max_input_bytes'] == 50000 and expected['max_output_tokens'] == 8192
    assert expected['context_token_bound'] is None and expected['long_context_verified'] is False
    assert e == before


@pytest.mark.parametrize('change', [
    {'request_profile': 'unknown'}, {'request_profile': True}, {'request_profile': None},
    {'request_profile': ['qwen38-nonthinking-v1']},
    {'provider': 'deepseek'}, {'model': 'qwen-plus'}, {'model': []}, {'task': 'embedding'},
])
def test_profile_is_fixed_typed_and_cannot_be_attached_to_another_model(change):
    e = execution(request_profile=PROFILE, **{k: v for k, v in change.items() if k != 'request_profile'})
    if 'request_profile' in change:
        e['request_profile'] = change['request_profile']
    with pytest.raises(ValueError):
        profiles.validate_request_profile(e)
    with pytest.raises(ValueError):
        profiles.input_policy(e, full_report=True)
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json_options(e)


def test_frozen_v5_contract_reconstructs_profile_without_a_client_timeout_or_context_claim():
    e = execution(request_profile=PROFILE)
    cap = NS(data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    value = understanding.generation_contract(cap, 5)
    rebuilt = understanding.relation_report_contract('qwen', copy.deepcopy(value['dispatch_options']))
    assert value == rebuilt and value['version'] == 'setup-grounded-v5'
    assert value['input_policy'] == {'single_batch': True,
        **policy(version='cloud-qwen38-nonthinking-v1', timeout=180)}
    legacy_cap = NS(data={'execution': execution(), 'data_policy': cap.data['data_policy']})
    old = understanding.generation_contract(legacy_cap, 5)
    assert old['input_policy'] == {'single_batch': True, **policy()}
    assert 'enable_thinking' not in old['dispatch_options']
    for key in set(value) - {'dispatch_options', 'input_policy'}:
        assert value[key] == old[key]


@pytest.mark.parametrize('thinking', [True, 'false', 0, 1, None])
def test_frozen_qwen38_boolean_false_is_not_coerced_or_repaired(thinking):
    options = gateway.cloud_json_options(execution(request_profile=PROFILE))
    options['enable_thinking'] = thinking
    with pytest.raises(ValueError):
        understanding.full_report_contract('qwen', options)


@pytest.mark.parametrize('kind,change', [
    ('tool', {}), ('foundation_model', {'request_profile': 'other'}),
    ('foundation_model', {'request_profile': False}), ('foundation_model', {'provider': 'deepseek'}),
    ('foundation_model', {'model': []}), ('foundation_model', {'task': 'embedding'}),
])
def test_registration_rejects_bad_profile_before_any_database_write(kind, change):
    e = execution(request_profile=PROFILE); e.update(change)
    manifest = Manifest(kind=kind, namespace='profile_test', name='qwen38', version='1.0.0',
        owner='纯合同测试', description='非真实注册或付费证据', execution=e,
        data_policy={'cloud_allowed': True})
    class NoDB:
        def __getattr__(self, _name):
            raise AssertionError('invalid registration must stop before touching DB')
    with pytest.raises(HTTPException) as error:
        services.register(NoDB(), NS(role='admin'), manifest)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as checked:
        services._validate_request_profile(manifest)
    assert checked.value.status_code == 422


@pytest.mark.parametrize('explicit', [False, True])
def test_real_gateway_posts_exact_false_and_180_only_for_the_explicit_profile(monkeypatch, explicit):
    e = execution(**({'request_profile': PROFILE} if explicit else {}))
    cap = NS(id='11111111-1111-4111-8111-111111111111',
        workspace_id='22222222-2222-4222-8222-222222222222',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    p = NS(user_id='33333333-3333-4333-8333-333333333333', workspace_id=cap.workspace_id)
    messages = [{'role': 'user', 'content': json.dumps({'excerpt': '仅此真实接口的合成文字'}, ensure_ascii=False)}]
    schema = {'type': 'object', 'required': ['ok'], 'additionalProperties': False,
        'properties': {'ok': {'type': 'boolean'}}}
    before_messages, before_execution = copy.deepcopy(messages), copy.deepcopy(e)
    prepared, _, _ = gateway.prepare_cloud_json(cap, messages, schema)
    capture = {}; call_id = '44444444-4444-4444-8444-444444444444'
    def reserve(principal, selected, bound, request_hash):
        assert principal is p and selected is cap
        capture['reservation'] = {'bound': bound, 'request_hash': request_hash}
        return call_id
    def finish(identity, receipt=None, status='failed'):
        capture['settlement'] = (identity, copy.deepcopy(receipt), status)
    class Response:
        status_code = 200
        headers = {'x-request-id': 'synthetic-profile-wire'}
        def json(self):
            return {'choices': [{'message': {'content': '{"ok":true}'}}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}
    class HTTP:
        def __init__(self, **kwargs): capture['client'] = kwargs
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def post(self, url, **kwargs):
            capture['post'] = {'url': url, **copy.deepcopy(kwargs)}
            return Response()
    monkeypatch.setenv('DASHSCOPE_API_KEY', KEY)
    monkeypatch.setattr(gateway, 'settings', lambda: NS(dashscope_api_key=None, deepseek_api_key=None))
    monkeypatch.setattr(gateway.httpx, 'Client', HTTP)
    monkeypatch.setattr(cloud_budget, 'reserve', reserve)
    monkeypatch.setattr(cloud_budget, 'finish', finish)
    result, receipt = gateway.cloud_json(cap, messages, schema, p, full_report=True)
    assert result == {'ok': True} and receipt['call_id'] == call_id
    assert capture['client'] == {'timeout': 180 if explicit else 60, 'follow_redirects': False, 'trust_env': False}
    assert capture['post']['url'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    expected_options = gateway.cloud_json_options(e)
    assert capture['post']['json'] == {**expected_options, 'messages': prepared}
    assert ('enable_thinking' in capture['post']['json']) is explicit
    if explicit:
        assert capture['post']['json']['enable_thinking'] is False
    assert capture['post']['json']['messages'] == prepared
    assert capture['reservation']['request_hash'] == digest(prepared)
    expected_bound = profiles.measure_input(prepared, profiles.input_policy(e, full_report=True), 2048)['token_bound']
    assert capture['reservation']['bound'] == expected_bound
    assert capture['settlement'][0] == call_id and capture['settlement'][2] == 'succeeded'
    assert messages == before_messages and e == before_execution
