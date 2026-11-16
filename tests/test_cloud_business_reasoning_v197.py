"""Exactly-one-transport tests with explicit HTTP and billing IO spies.

No public network, provider, database, credential or real payment is used here.
"""
import copy
import json
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import HTTPException

from generic_agents import cloud_business_reasoning_v197 as subject
from generic_agents import business_reasoning_v197 as reasoning
from generic_agents.gateway import ModelBlocked
from generic_agents.leases import LeaseLost
from tests.test_business_reasoning_v197 import scene, ident


def envelope(value, *, usage=True):
    result = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}
    if usage: result['usage'] = {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
    return json.dumps(result, ensure_ascii=False).encode('utf-8')


def install(monkeypatch, body, *, status=200, post_error=None):
    from generic_agents import cloud_budget, cloud_dispatch_v130, db as actual_db
    s = scene(); events = []; finished = []; captures = []; posts = []
    ledger = NS(id=ident(90), workspace_id=s.p.workspace_id, capability_id=s.model.id, data={})
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def scalar(self, _query): return ledger
        def commit(self): events.append('ledger_options_saved')
    def reserve(p, cap, bound, request_hash):
        assert p.user_id == s.p.user_id and cap.id == s.model.id and bound > 1024
        ledger.reserved_tokens = ledger.charged_tokens = bound
        ledger.status = 'reserved'
        ledger.data.update(actor_id=p.user_id, request_hash=request_hash, day='2026-10-24'); events.append('reserved')
        return ledger.id
    def finish(call_id, receipt=None, status='failed'):
        assert call_id == ledger.id; finished.append((status, copy.deepcopy(receipt)))
    class Client:
        def __init__(self, **options):
            assert set(options) == {'timeout', 'follow_redirects', 'trust_env'}
            timeout = options['timeout']
            assert isinstance(timeout, httpx.Timeout)
            assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 180, 30, 10)
            assert options['follow_redirects'] is False and options['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def post(self, endpoint, *, headers, json):
            assert endpoint == subject.ENDPOINTS['qwen']
            assert headers == {'Authorization': 'Bearer public-noncredential'}
            posts.append(copy.deepcopy(json)); events.append('post')
            if post_error is not None: raise post_error
            return NS(status_code=status, content=body, headers={'x-request-id': 'public-request-id'})
    monkeypatch.setattr(subject, 'Session', lambda _engine: DB())
    monkeypatch.setattr(actual_db, 'engine', lambda: None)
    monkeypatch.setattr(cloud_budget, 'reserve', reserve)
    monkeypatch.setattr(cloud_budget, 'finish', finish)
    monkeypatch.setattr(cloud_dispatch_v130, 'before_transport', lambda: events.append('before_transport'))
    monkeypatch.setattr(subject.httpx, 'Client', Client)
    schema = reasoning.model_value_schema(s.inputs['evidence_ids'], [row['candidate_ref'] for row in s.inputs['candidates']])
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': '公开明确授权的证据与提案'}]}]
    def reserved(call_id): assert call_id == ledger.id; events.append('on_reserved')
    def response(raw, receipt):
        events.append('on_response'); captures.append((raw, copy.deepcopy(receipt)))
        assert raw.encode('utf-8', errors='surrogateescape') == body
        assert receipt['usage'] == {} and receipt['usage_unavailable'] is True
    def call(**options):
        return subject.call(s.model, options.pop('messages', messages), options.pop('schema', schema), s.p,
            key='public-noncredential', on_reserved=options.pop('on_reserved', reserved),
            on_response=options.pop('on_response', response), **options)
    return NS(s=s, ledger=ledger, events=events, finished=finished, captures=captures, posts=posts,
        call=call, schema=schema, messages=messages)


def test_single_real_call_shape_captures_full_http_before_parsing_and_uses_actual_usage(monkeypatch):
    s = scene(); body = envelope(s.output); f = install(monkeypatch, body)
    parse = subject.parse_response
    def checked_parse(*args): f.events.append('parse'); return parse(*args)
    monkeypatch.setattr(subject, 'parse_response', checked_parse)
    value, receipt = f.call()
    assert value == s.output and receipt['usage']['total_tokens'] == 30
    assert f.events == ['reserved', 'ledger_options_saved', 'on_reserved', 'before_transport', 'post', 'on_response', 'parse']
    assert len(f.posts) == 1 and len(f.captures) == 1 and len(f.finished) == 1
    assert f.finished[0][0] == 'succeeded' and f.finished[0][1]['usage']['total_tokens'] == 30
    assert f.posts[0]['response_format'] == {'type': 'json_object'}
    assert f.posts[0]['model'] == 'qwen-plus' and f.posts[0]['max_tokens'] == 1024
    assert f.ledger.data['dispatch_options_hash']


@pytest.mark.parametrize('fault', ['empty_choices', 'multiple_choices', 'truncated', 'duplicate_envelope',
    'duplicate_content', 'nonfinite_content', 'missing_content', 'tool_call', 'bad_json', 'invalid_utf8', 'oversize', 'schema_reference'])
def test_original_bad_answer_is_captured_before_rejection_and_never_retried(monkeypatch, fault):
    s = scene(); value = json.loads(envelope(s.output))
    if fault == 'empty_choices': value['choices'] = []
    elif fault == 'multiple_choices': value['choices'] *= 2
    elif fault == 'truncated': value['choices'][0]['finish_reason'] = 'length'
    elif fault == 'duplicate_envelope': pass
    elif fault == 'duplicate_content': value['choices'][0]['message']['content'] = '{"summary":"a","summary":"b"}'
    elif fault == 'nonfinite_content': value['choices'][0]['message']['content'] = '{"summary":1e999}'
    elif fault == 'missing_content': value['choices'][0]['message']['content'] = None
    elif fault == 'tool_call': value['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'dispatch'}}]
    elif fault == 'schema_reference': value['choices'][0]['message']['content'] = json.dumps({**s.output, 'approved': True})
    body = json.dumps(value).encode()
    if fault == 'duplicate_envelope': body = b'{"choices":[],"choices":[]}'
    elif fault == 'bad_json': body = b'{'
    elif fault == 'invalid_utf8': body = b'\xff'
    elif fault == 'oversize': body = b'x' * (subject.MAX_RESPONSE_BYTES + 1)
    f = install(monkeypatch, body)
    with pytest.raises((ModelBlocked, HTTPException, ValueError)): f.call()
    assert len(f.captures) == 1 and f.captures[0][0].encode('utf-8', errors='surrogateescape') == body
    assert len(f.posts) == 1 and len(f.finished) == 1 and f.finished[0][0] == 'failed'


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': True},
    {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 31}])
def test_missing_or_invalid_usage_is_conservatively_unavailable_never_fabricated_zero(monkeypatch, usage):
    s = scene(); body = json.loads(envelope(s.output)); body['usage'] = usage
    f = install(monkeypatch, json.dumps(body).encode()); _value, receipt = f.call()
    assert receipt['usage'] == {} and receipt['usage_unavailable'] is True
    assert f.finished[0][1]['usage'] == {} and len(f.posts) == 1


def test_unknown_usage_and_non200_original_envelope_are_retained_without_fallback(monkeypatch):
    f = install(monkeypatch, b'{"error":"public provider refusal"}', status=503)
    with pytest.raises(ModelBlocked): f.call()
    assert f.captures[0][1]['http_status'] == 503 and f.finished[0][0] == 'failed'
    assert f.finished[0][1]['usage'] == {} and len(f.posts) == 1


@pytest.mark.parametrize('phase', ['reservation_guard', 'capture'])
def test_lease_or_capture_failure_never_starts_another_call_or_overwrites_original(monkeypatch, phase):
    s = scene(); f = install(monkeypatch, envelope(s.output))
    def refused(*_args): raise LeaseLost('public lease boundary refused')
    with pytest.raises(LeaseLost):
        f.call(**({'on_reserved': refused} if phase == 'reservation_guard' else {'on_response': refused}))
    assert len(f.posts) == (0 if phase == 'reservation_guard' else 1)
    assert f.finished[-1][0] == 'failed'


def test_actual_complete_text_bound_and_invalid_schema_fail_before_reservation(monkeypatch):
    s = scene(); f = install(monkeypatch, envelope(s.output))
    with pytest.raises(ModelBlocked): f.call(messages=[{'role': 'user', 'content': 'x' * subject.MAX_TEXT_BYTES}])
    with pytest.raises(ModelBlocked): f.call(schema={'$ref': 'https://not-authorised.invalid/schema'})
    assert f.events == [] and f.posts == [] and f.finished == []


def test_read_only_saved_envelope_parser_does_not_need_transport_or_budget(monkeypatch):
    s = scene(); f = install(monkeypatch, envelope(s.output, usage=False))
    receipt = {'provider': 'qwen', 'model': 'qwen-plus', 'call_id': ident(90), 'request_id': None, 'http_status': 200}
    value, parsed = subject.parse_response(envelope(s.output, usage=False).decode(), f.schema, receipt)
    assert value == s.output and parsed['usage_unavailable'] is True
    assert f.posts == [] and f.events == [] and f.finished == []


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectTimeout])
def test_timeout_has_one_post_no_answer_and_actual_finish_retains_conservative_original_budget(monkeypatch, error_type):
    from generic_agents import cloud_budget
    from generic_agents.models import CloudCall, Job
    actual_finish = cloud_budget.finish
    private_message = 'never-store-message https://untrusted.invalid/?token=not-a-real-secret'
    failure = error_type(private_message)
    f = install(monkeypatch, b'', post_error=failure)
    budget = NS(data={'tokens': 0, 'calls': 1})
    settled = []
    class SettlementDB:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def scalar(self, statement):
            assert statement._for_update_arg is not None
            entity = statement.column_descriptions[0]['entity']
            if entity is CloudCall:
                assert f.ledger.id in statement.compile().params.values()
                budget.data['tokens'] = f.ledger.reserved_tokens
                return f.ledger
            assert entity is Job
            assert 'cloud-budget:2026-10-24' in statement.compile().params.values()
            return budget
        def commit(self): settled.append(copy.deepcopy(f.ledger.data['receipt']))
    # Exercise the production finish calculation at explicit DB IO boundaries;
    # no copied billing algorithm or fake successful response replaces it.
    monkeypatch.setattr(cloud_budget, 'Session', lambda _engine: SettlementDB())
    monkeypatch.setattr(cloud_budget, 'engine', lambda: None)
    monkeypatch.setattr(cloud_budget, 'finish', actual_finish)
    with pytest.raises(error_type) as caught: f.call()
    assert caught.value is failure
    assert f.events == ['reserved', 'ledger_options_saved', 'on_reserved', 'before_transport', 'post']
    assert len(f.posts) == 1 and f.captures == [] and len(settled) == 1
    assert f.ledger.status == 'failed'
    assert f.ledger.charged_tokens == f.ledger.reserved_tokens > 1024
    assert budget.data['tokens'] == f.ledger.reserved_tokens and budget.data['calls'] == 1
    receipt = settled[0]
    assert receipt['transport_error_type'] == error_type.__name__
    assert receipt['usage'] == {} and receipt['usage_unavailable'] is True
    assert receipt['request_id'] is None and 'http_status' not in receipt
    assert receipt['finish_reason'] is None and receipt['response_choice_count'] is None
    assert private_message not in json.dumps(receipt) and 'untrusted.invalid' not in json.dumps(receipt)
