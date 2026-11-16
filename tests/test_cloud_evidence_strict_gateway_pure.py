"""Synthetic strict-tool gateway tests; root alone executes this file.

All HTTP responses, credentials, ledger rows and callbacks are fabricated. The
fake ledger deliberately mirrors legacy finish's bool/int weakness, so these
tests detect whether the new gateway really retains an unproved reservation.
They prove control flow, not journal fsync, provider quality or paid authority.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import httpx
import pytest

from generic_agents import gateway, cloud_budget, db as database
from generic_agents.cloud_evidence_profiles import evidence_policy, evidence_options
from generic_agents.cloud_input_profiles import measure_input, measure_request_body
from generic_agents.contracts import digest
from generic_agents.report_evidence_wire_v3 import MapResultWireV3
from generic_agents import report_evidence_strict_map as strict


CALL = '10000000-0000-4000-8000-000000000003'
KEY = 'SYNTHETIC_NO_PROVIDER_CREDENTIAL'
TOOL = 'submit_report_evidence_map'
ENDPOINT = 'https://api.deepseek.com/beta/chat/completions'
SCHEMA = MapResultWireV3.model_json_schema()
MESSAGES = [{'role': 'user', 'content': '{"source":"全新合成引文。"}'}]
VALUE = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '业务',
    'statement': '全新合成引文。', 'span_refs': ['w:p1/s1'],
    'status': 'explicit', 'explanation': ''}]}
USAGE = {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': 390}


def execution():
    return {'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'max_tokens': 8192,
        'thinking_mode': 'disabled', 'secret_ref': 'DEEPSEEK_API_KEY',
        'report_evidence_output': {'schema': 'report-evidence-output-v2',
            'map_transport': 'deepseek-strict-map-tool-v1', 'max_tokens': 131072}}


def payload(*, args=None, usage=USAGE, reason='tool_calls'):
    return {'usage': copy.deepcopy(usage), 'choices': [{'index': 0, 'finish_reason': reason,
        'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'synthetic_tool_call', 'type': 'function', 'function': {
                'name': TOOL, 'arguments': json.dumps(VALUE, ensure_ascii=False) if args is None else args}}]}}]}


def body(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure strict tests cannot open a socket, DB or subprocess')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(database, 'engine', forbidden)


@pytest.fixture
def transport(monkeypatch):
    state = NS(events=[], requests=[], reservations=[], callbacks=[], finished=[],
        body=body(payload()), status=200, error=None, call=None, clients=[])
    secret = NS(get_secret_value=lambda: KEY)
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=secret, dashscope_api_key=None))

    def reserve(p, cap, bound, request_hash):
        assert p.user_id == 'synthetic-actor' and p.workspace_id == cap.workspace_id
        state.events.append('reserve'); state.reservations.append((bound, request_hash))
        state.call = NS(status='reserved', reserved_tokens=bound, charged_tokens=bound,
            data={'actor_id': p.user_id, 'request_hash': request_hash})
        return CALL

    def finish(identity, receipt=None, status='failed'):
        assert identity == CALL
        if state.call.status != 'reserved': return
        state.events.append('finish')
        charged = (receipt or {}).get('usage', {}).get('total_tokens', state.call.reserved_tokens)
        if not isinstance(charged, int) or charged < 0: charged = state.call.reserved_tokens
        state.call.charged_tokens = charged; state.call.status = status
        state.finished.append((copy.deepcopy(receipt), status))

    monkeypatch.setattr(cloud_budget, 'reserve', reserve)
    monkeypatch.setattr(cloud_budget, 'finish', finish)
    marker = object(); monkeypatch.setattr(database, 'engine', lambda: marker)

    class Session:
        def __init__(self, connection): assert connection is marker
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalar(self, statement): return state.call
        def commit(self): state.events.append('ledger-commit')

    import sqlalchemy.orm
    monkeypatch.setattr(sqlalchemy.orm, 'Session', Session)

    class Client:
        def __init__(self, **options): state.clients.append(options)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, endpoint, **options):
            assert endpoint == ENDPOINT
            assert set(options) == {'headers', 'content'} and type(options['content']) is bytes
            assert options['headers'] == {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
            state.events.append('http'); state.requests.append(copy.deepcopy(options))
            if state.error: raise state.error
            return NS(content=state.body, status_code=state.status,
                headers={'x-request-id': 'synthetic-provider-request'})

    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    original_parse, original_validate = strict.parse_strict_map_arguments, gateway.validate

    def parse(*args):
        state.events.append('parse-arguments'); return original_parse(*args)

    def validate(*args):
        state.events.append('local-schema'); return original_validate(*args)

    monkeypatch.setattr(strict, 'parse_strict_map_arguments', parse)
    monkeypatch.setattr(gateway, 'validate', validate)
    return state


def call(state, *, e=None, stage='map-v4', full_report=True, received=None, reserved=None):
    e = execution() if e is None else e
    cap = NS(id='synthetic-capability', workspace_id='synthetic-workspace',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    p = NS(user_id='synthetic-actor', workspace_id=cap.workspace_id)

    def on_reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        state.events.append('on-reserved')
        if reserved: reserved(identity)

    def on_response(original, receipt):
        assert 'parse-arguments' not in state.events and 'local-schema' not in state.events
        state.events.append('callback'); state.callbacks.append((original, copy.deepcopy(receipt)))
        if received: received(original, receipt)

    return gateway.cloud_json(cap, MESSAGES, SCHEMA, p, evidence_stage=stage,
        full_report=full_report, on_reserved=on_reserved, on_response=on_response)


def test_complete_canonical_post_not_messages_only_is_reserved_then_sent_once(transport):
    e = execution(); original = copy.deepcopy(e)
    cap = NS(data={'execution': e, 'data_policy': {'redact_fields': []}})
    messages, _, _ = gateway.prepare_cloud_json(cap, MESSAGES, SCHEMA)
    options = evidence_options(e, gateway.cloud_json_options(e), 'map-v4')
    prepared = strict.prepare_strict_map_request(messages, SCHEMA, options)
    measured = measure_request_body(prepared['body'], evidence_policy(e, 'map-v4'), options['max_tokens'])
    value, receipt = call(transport, e=e)
    assert value == VALUE and e == original
    assert transport.requests[0]['content'] == prepared['body']
    assert hashlib.sha256(transport.requests[0]['content']).hexdigest() == prepared['body_sha256']
    assert transport.reservations == [(measured['token_bound'], prepared['body_sha256'])]
    assert measured['token_bound'] == len(prepared['body']) + 8192 + 1024
    request = json.loads(prepared['body'])
    assert request['tool_choice'] == {'type': 'function', 'function': {'name': TOOL}}
    assert request['tools'][0]['function']['strict'] is True
    assert request['stream'] is False and request['thinking'] == {'type': 'disabled'}
    assert 'response_format' not in request and len(request['tools']) == 1
    assert transport.call.data['dispatch_options_hash'] == digest({key: item for key, item in request.items() if key != 'messages'})
    assert transport.call.data['input_policy_hash'] == digest({'single_batch': True, **evidence_policy(e, 'map-v4')})
    assert transport.call.data['request_body_bytes'] == len(prepared['body'])
    assert transport.call.data['request_endpoint'] == ENDPOINT
    assert transport.call.data['schema_proof_hash'] == digest(prepared['schema_proof'])
    assert transport.clients == [{'timeout': 180, 'follow_redirects': False, 'trust_env': False}]
    assert transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'parse-arguments', 'local-schema', 'finish']
    assert receipt['finish_reason'] == 'tool_calls' and receipt['response_choice_count'] == receipt['tool_call_count'] == 1
    assert receipt['usage'] == USAGE and receipt['usage_unavailable'] is False
    assert receipt['response_body_sha256'] == hashlib.sha256(transport.body).hexdigest()
    assert receipt['response_body_bytes'] == len(transport.body)
    assert receipt['arguments_sha256'] == hashlib.sha256(json.dumps(VALUE, ensure_ascii=False).encode()).hexdigest()
    assert transport.callbacks == [(transport.body.decode('utf-8'), receipt)]
    assert transport.finished == [(receipt, 'succeeded')]


@pytest.mark.parametrize('mode', ['multi_tool', 'multi_choice', 'wrong_name', 'wrong_type',
    'missing_args', 'no_tools', 'empty_choices', 'stop', 'length', 'content_filter', 'no_finish'])
def test_actual_invalid_tool_envelope_is_durable_without_first_tool_adoption_or_second_post(transport, mode):
    response = payload(); choice = response['choices'][0]; tools = choice['message']['tool_calls']
    if mode == 'multi_tool': tools.append(copy.deepcopy(tools[0]))
    elif mode == 'multi_choice': response['choices'].append(copy.deepcopy(choice))
    elif mode == 'wrong_name': tools[0]['function']['name'] = 'other_tool'
    elif mode == 'wrong_type': tools[0]['type'] = 'other'
    elif mode == 'missing_args': tools[0]['function'].pop('arguments')
    elif mode == 'no_tools': choice['message'].pop('tool_calls')
    elif mode == 'empty_choices': response['choices'] = []
    else: choice['finish_reason'] = None if mode == 'no_finish' else mode
    transport.body = body(response)
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert len(transport.requests) == len(transport.callbacks) == len(transport.finished) == 1
    original, receipt = transport.callbacks[0]
    assert original.encode('utf-8', errors='surrogateescape') == transport.body
    assert receipt['usage'] == USAGE and transport.finished == [(receipt, 'failed')]
    assert transport.events.index('callback') < transport.events.index('parse-arguments')
    assert 'local-schema' not in transport.events
    if mode == 'multi_tool': assert receipt['tool_call_count'] == 2
    if mode == 'multi_choice': assert receipt['response_choice_count'] == 2
    if mode in {'stop', 'length', 'content_filter'}: assert receipt['finish_reason'] == mode


@pytest.mark.parametrize('mode', ['bad_json', 'bad_utf8', 'http400', 'oversize', 'bad_args_json', 'bad_kind', 'local_length'])
def test_all_available_original_http_bytes_precede_failure_and_are_not_repaired(transport, mode):
    response = payload()
    if mode == 'bad_json': transport.body = b'{"usage":'
    elif mode == 'bad_utf8': transport.body = b'\xff\xfe' + body(response)
    elif mode == 'http400': transport.status = 400; transport.body = body(response)
    elif mode == 'oversize': transport.body = body(payload(args=' ' * (256 * 1024 + 1)))
    elif mode == 'bad_args_json': transport.body = body(payload(args='{"claims":'))
    else:
        value = copy.deepcopy(VALUE)
        if mode == 'bad_kind': value['claims'][0]['kind'] = '流程'
        else: value['claims'][0]['statement'] = 'x' * 2001
        transport.body = body(payload(args=json.dumps(value, ensure_ascii=False)))
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert len(transport.requests) == len(transport.callbacks) == len(transport.finished) == 1
    original, receipt = transport.callbacks[0]
    assert original.encode('utf-8', errors='surrogateescape') == transport.body
    assert receipt['response_body_sha256'] == hashlib.sha256(transport.body).hexdigest()
    assert receipt['response_body_bytes'] == len(transport.body)
    assert transport.finished[0][1] == 'failed'
    assert transport.events.index('callback') < transport.events.index('parse-arguments')
    if mode == 'http400': assert receipt['http_status'] == 400
    if mode in {'bad_kind', 'local_length'}: assert 'local-schema' in transport.events


@pytest.mark.parametrize('usage', [None, [], True, {'total_tokens': True},
    {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': True},
    {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': -1},
    {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': 390.0},
    {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': 0},
    {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': float('nan')}])
def test_missing_or_unproved_usage_keeps_full_reservation_without_inventing_zero(transport, usage):
    transport.body = body(payload(usage=usage))
    # A malformed JSON constant may invalidate the envelope; either way fees
    # cannot become zero or disappear merely because usage is untrustworthy.
    try: call(transport)
    except gateway.ModelBlocked: pass
    receipt = transport.callbacks[0][1]
    assert receipt['usage'] == {} and receipt['usage_unavailable'] is True
    assert receipt['usage_available'] is False and receipt['usage_unavailable_reason']
    assert transport.call.charged_tokens == transport.call.reserved_tokens > 0
    assert transport.callbacks[0][0].encode('utf-8', errors='surrogateescape') == transport.body


def test_missing_usage_is_reported_as_missing_not_as_zero(transport):
    response = payload(); response.pop('usage'); transport.body = body(response)
    value, receipt = call(transport)
    assert value == VALUE and receipt['original_usage_shape'] == 'missing'
    assert receipt['usage_unavailable_reason'] == 'missing' and receipt['usage'] == {}
    assert transport.call.charged_tokens == transport.call.reserved_tokens


@pytest.mark.parametrize('extra', ['\x00', '\ud800'])
def test_unstorable_extra_usage_does_not_quarantine_otherwise_valid_arguments_or_release_reservation(transport, extra):
    usage = {**USAGE, 'extra': {'nested': [extra]}}
    # Escaped provider JSON is valid HTTP UTF8. The decoded usage string cannot
    # be stored in JSONB; preserve it only in the unchanged original body.
    transport.body = json.dumps(payload(usage=usage), ensure_ascii=True).encode('ascii')
    original = transport.body
    value, receipt = call(transport)
    assert value == VALUE and transport.body == original
    assert receipt['usage'] == {} and receipt['usage_unavailable'] is True
    assert receipt['usage_available'] is False and receipt['usage_unavailable_reason'] == 'usage_json_invalid'
    assert receipt['envelope_valid'] is True and receipt['envelope_errors'] == []
    assert transport.call.charged_tokens == transport.call.reserved_tokens
    assert transport.callbacks[0][0].encode('utf-8', errors='surrogateescape') == original
    from generic_agents.report_reading_drafts import jsonb_compatible
    assert jsonb_compatible(receipt)  # Not merely a successful mock callback.
    assert transport.finished == [(receipt, 'succeeded')]


def test_callback_cannot_rewrite_original_tool_counts_finish_or_fees_into_success(transport):
    response = payload(reason='length'); response['choices'][0]['message']['tool_calls'] *= 2
    transport.body = body(response)
    def tamper(original, receipt):
        receipt['finish_reason'] = 'tool_calls'; receipt['tool_call_count'] = 1
        receipt['usage']['total_tokens'] = 0
    with pytest.raises(gateway.ModelBlocked): call(transport, received=tamper)
    saved = transport.finished[0][0]
    assert saved['finish_reason'] == 'length' and saved['tool_call_count'] == 2
    assert saved['usage'] == USAGE and transport.finished[0][1] == 'failed'


def test_callback_failure_finishes_original_receipt_without_semantic_parse_or_retry(transport):
    error = RuntimeError('synthetic journal durability failure')
    def fail(*args): raise error
    with pytest.raises(RuntimeError) as caught: call(transport, received=fail)
    assert caught.value is error and len(transport.requests) == 1
    assert 'parse-arguments' not in transport.events and 'local-schema' not in transport.events
    assert transport.finished[0][0]['usage'] == USAGE and transport.finished[0][1] == 'failed'


def test_actual_reserved_rejection_or_network_timeout_never_implicitly_retries(transport):
    def reject(*args): raise gateway.ModelBlocked('synthetic frozen request mismatch')
    with pytest.raises(gateway.ModelBlocked): call(transport, reserved=reject)
    assert transport.requests == transport.callbacks == [] and transport.finished == [(None, 'failed')]


def test_transport_timeout_preserves_unknown_reserved_exposure_without_fake_response(transport):
    transport.error = httpx.ReadTimeout('synthetic timeout')
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert len(transport.requests) == 1 and transport.callbacks == []
    assert transport.finished == [(None, 'failed')]
    assert transport.call.charged_tokens == transport.call.reserved_tokens


@pytest.mark.parametrize('mode', ['non_full', 'old_manifest', 'thinking', 'other_provider', 'other_model'])
def test_new_transport_requires_explicit_manifest_provider_model_and_call_authorization(transport, mode):
    e = execution()
    if mode == 'old_manifest': e['report_evidence_output'] = {'schema': 'report-evidence-output-v1', 'max_tokens': 131072}
    elif mode == 'thinking': e['thinking_mode'] = 'enabled'
    elif mode == 'other_provider': e['provider'] = 'qwen'; e['secret_ref'] = 'DASHSCOPE_API_KEY'
    elif mode == 'other_model': e['model'] = 'deepseek-chat'
    with pytest.raises(gateway.ModelBlocked): call(transport, e=e, full_report=mode != 'non_full')
    assert transport.reservations == transport.requests == []


@pytest.mark.parametrize('which', ['on_reserved', 'on_response'])
def test_strict_callbacks_are_mandatory_before_any_reservation(transport, which):
    cap = NS(id='synthetic-capability', workspace_id='synthetic-workspace',
        data={'execution': execution(), 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    callbacks = {'on_reserved': lambda *args: None, 'on_response': lambda *args: None}; callbacks[which] = None
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(cap, MESSAGES, SCHEMA, NS(user_id='synthetic-actor', workspace_id=cap.workspace_id),
            evidence_stage='map-v4', full_report=True, **callbacks)
    assert transport.reservations == transport.requests == []


def test_request_size_limit_counts_large_tool_or_option_bytes_before_reserve(transport, monkeypatch):
    original = strict.prepare_strict_map_request
    def too_large(*args):
        prepared = original(*args)
        request = copy.deepcopy(prepared['request']); request['synthetic_large_option'] = 'x' * 900000
        prepared['body'] = body(request); prepared['body_sha256'] = hashlib.sha256(prepared['body']).hexdigest()
        return prepared
    monkeypatch.setattr(strict, 'prepare_strict_map_request', too_large)
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert transport.reservations == transport.requests == []


def test_complete_request_byte_and_context_boundaries_leave_old_measurement_unchanged():
    policy = evidence_policy(execution(), 'map-v4')
    exact = b'x' * policy['max_input_bytes']
    assert measure_request_body(exact, policy, 8192) == {
        'prepared_request_bytes': 900000, 'token_bound': 909216, 'max_input_bytes': 900000}
    with pytest.raises(ValueError): measure_request_body(exact + b'x', policy, 8192)
    with pytest.raises(ValueError): measure_request_body(b'\xff', policy, 8192)
    for value in (b'', 'request', bytearray(b'{}')):
        with pytest.raises(ValueError): measure_request_body(value, policy, 8192)
    for maximum in (True, 8192.0, 8193, 127):
        with pytest.raises(ValueError): measure_request_body(b'{}', policy, maximum)
    tight = {**policy, 'context_token_bound': 9217}
    with pytest.raises(ValueError): measure_request_body(b'{}', tight, 8192)
    old = measure_input([{'role': 'user', 'content': 'old'}], policy, 8192)
    assert set(old) == {'prepared_messages_bytes', 'token_bound', 'max_input_bytes'}

