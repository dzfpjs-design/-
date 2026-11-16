"""Actual prepare/body paths; HTTP, daily budget and ledger are explicit spies.

No provider, PG, credential, payment, money authority, worker or SDK is real.
The separate legacy transport suite retains the original capture failure cases.
"""
import copy

import pytest

from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import same_goal_advice_money_v305 as money
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.leases import LeaseLost
from tests.test_cloud_business_reasoning_v197 import install, envelope
from tests.test_business_reasoning_v197 import scene as original_scene
from tests.test_same_goal_advice_money_v305 import scene as money_scene, AT


def model_4096(f):
    f.s.model.data = copy.deepcopy(f.s.model.data)
    f.s.model.data['execution']['max_tokens'] = 4096
    f.s.model.content_hash = digest(f.s.model.data)
    return f.s.model


def test_default_and_explicit_none_keep_the_entire_legacy_preparation(money_scene):
    s = money_scene; model = type('Model', (), {'data': s.model})()
    before = copy.deepcopy(s.model)
    default = transport.prepare(model, s.messages, s.schema)
    explicit = transport.prepare(model, s.messages, s.schema, output_token_limit=None)
    assert explicit == default
    assert default['options']['max_tokens'] == 4096
    assert default['options'] == {'model': 'deepseek-v4-pro', 'max_tokens': 4096,
        'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'}}
    assert default['token_bound'] == default['text_bytes'] + 4096 + 1024
    assert s.model == before


@pytest.mark.parametrize('limit', [128, 1024, 4096])
def test_real_prepare_changes_only_actual_limit_options_hash_and_bound(money_scene, limit):
    s = money_scene; model = type('Model', (), {'data': s.model})()
    before = copy.deepcopy(s.model)
    original = transport.prepare(model, s.messages, s.schema)
    limited = transport.prepare(model, s.messages, s.schema, output_token_limit=limit)
    assert limited['messages'] == original['messages'] and limited['request_hash'] == original['request_hash']
    assert limited['text_bytes'] == original['text_bytes'] and limited['image_count'] == original['image_count'] == 0
    assert limited['options'] == {**original['options'], 'max_tokens': limit}
    assert limited['dispatch_options_hash'] == digest(limited['options'])
    assert limited['token_bound'] == original['token_bound'] - 4096 + limit
    assert s.model == before


def test_new_real_limited_prepare_matches_the_pure_money_quote_without_mutating_published_snapshot(money_scene):
    s = money_scene; before = copy.deepcopy(s.model)
    model = type('Model', (), {'data': s.model})()
    actual = transport.prepare(model, s.messages, s.schema, output_token_limit=1024)
    assert actual == s.prepared  # Previous pure shadow fixture, now actual prepare's opt-in.
    result = money.quote(s.price, now=AT,
        prepared=actual, model_pin=s.pin, model_snapshot=s.model, budget=s.budget)
    assert result['output_token_bound'] == 1024 and result['total_token_bound'] == actual['token_bound']
    assert all(result[key] is False for key in money.FLAGS)
    assert s.model == before and s.model['execution']['max_tokens'] == 4096


def test_none_call_keeps_original_three_argument_prepare_path_and_one_unchanged_body(monkeypatch):
    s = original_scene(); f = install(monkeypatch, envelope(s.output))
    expected = transport.prepare(f.s.model, f.messages, f.schema); original = transport.prepare; prepared_calls = []
    def legacy_signature(capability, messages, schema):
        prepared_calls.append((capability.id, messages, schema))
        return original(capability, messages, schema)
    monkeypatch.setattr(transport, 'prepare', legacy_signature)
    value, _receipt = f.call(output_token_limit=None)
    assert value == s.output and len(prepared_calls) == 1
    assert f.posts == [{**expected['options'], 'messages': expected['messages']}]
    assert f.ledger.reserved_tokens == expected['token_bound']
    assert f.ledger.data['dispatch_options_hash'] == expected['dispatch_options_hash']
    assert f.events == ['reserved', 'ledger_options_saved', 'on_reserved', 'before_transport', 'post', 'on_response']


def test_limited_call_reserves_and_posts_exactly_the_real_1024_body_and_keeps_first_reply(monkeypatch):
    s = original_scene(); body = envelope(s.output); f = install(monkeypatch, body)
    model = model_4096(f); before = copy.deepcopy(model.data)
    expected = transport.prepare(model, f.messages, f.schema, output_token_limit=1024)
    value, receipt = f.call(output_token_limit=1024)
    assert value == s.output and receipt['usage']['total_tokens'] == 30
    assert f.posts == [{**expected['options'], 'messages': expected['messages']}]
    assert f.posts[0]['max_tokens'] == 1024 and model.data == before
    assert f.ledger.reserved_tokens == expected['token_bound']
    assert f.ledger.data['request_hash'] == expected['request_hash']
    assert f.ledger.data['dispatch_options_hash'] == expected['dispatch_options_hash']
    assert len(f.posts) == len(f.captures) == len(f.finished) == 1
    assert f.captures[0][0].encode('utf-8') == body
    assert f.events.index('on_reserved') < f.events.index('before_transport') < f.events.index('post') < f.events.index('on_response')


@pytest.mark.parametrize('limit', [True, False, 127, 0, -1, 4097, 8193, 128.0, '128'])
def test_wrong_limit_fails_before_any_reservation_or_http(monkeypatch, limit):
    s = original_scene(); f = install(monkeypatch, envelope(s.output)); model = model_4096(f)
    before = copy.deepcopy(model.data)
    with pytest.raises(ModelBlocked): f.call(output_token_limit=limit)
    assert f.events == f.posts == f.captures == f.finished == [] and model.data == before


@pytest.mark.parametrize('registered_maximum', [True, 127, 8193])
def test_lower_limit_cannot_bypass_invalid_original_model_ceiling(monkeypatch, registered_maximum):
    s = original_scene(); f = install(monkeypatch, envelope(s.output))
    f.s.model.data['execution']['max_tokens'] = registered_maximum
    with pytest.raises(ModelBlocked): f.call(output_token_limit=128)
    assert f.events == f.posts == f.captures == f.finished == []


def test_limited_non200_still_preserves_original_http_once_without_provider_fallback(monkeypatch):
    body = b'{"error":"public fixture refusal"}'; f = install(monkeypatch, body, status=503)
    model_4096(f)
    with pytest.raises(ModelBlocked): f.call(output_token_limit=128)
    assert len(f.posts) == len(f.captures) == len(f.finished) == 1
    assert f.posts[0]['max_tokens'] == 128 and f.captures[0][0].encode('utf-8') == body
    assert f.finished[0][0] == 'failed'


def test_limited_reserved_guard_failure_never_enters_transport_or_issues_another_call(monkeypatch):
    s = original_scene(); f = install(monkeypatch, envelope(s.output)); model_4096(f)
    def blocked(_call_id): raise LeaseLost('public fixed guard denial')
    with pytest.raises(LeaseLost): f.call(output_token_limit=1024, on_reserved=blocked)
    assert f.posts == f.captures == [] and len(f.finished) == 1
    assert f.events == ['reserved', 'ledger_options_saved']

