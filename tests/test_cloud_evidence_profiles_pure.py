"""Root executes these pure profile / MockHTTP gateway tests.

Every answer, key, reserve, ledger Session and finish is synthetic. No provider,
database, process or installation is contacted; old environment budgets are not
raised. Mock order proves gateway control flow, not journal disk durability.
"""
import copy
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway, cloud_budget, db as database
from generic_agents.cloud_evidence_profiles import (evidence_policy, evidence_options,
    validate_evidence_output, MAX_RESPONSE_BYTES)
from generic_agents.cloud_input_profiles import input_policy, measure_input
from generic_agents.contracts import digest


KEY = 'SYNTHETIC_NOT_A_PROVIDER_CREDENTIAL'
CALL = '10000000-0000-4000-8000-000000000001'
SCHEMA = {'type': 'object', 'properties': {'value': {'type': 'string'}},
    'required': ['value'], 'additionalProperties': False}
MESSAGES = [{'role': 'user', 'content': '{"evidence":"全新合成证据。"}'}]
USAGE = {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': 390}


def execution(provider='deepseek', maximum=None, *, nested=True):
    value = {'provider': provider, 'model': 'deepseek-v4-pro' if provider == 'deepseek' else 'qwen-plus',
        'max_tokens': 8192 if nested or maximum is None else maximum,
        'secret_ref': 'DEEPSEEK_API_KEY' if provider == 'deepseek' else 'DASHSCOPE_API_KEY'}
    if provider == 'deepseek': value['thinking_mode'] = 'disabled'
    if nested:
        value['report_evidence_output'] = {'schema': 'report-evidence-output-v1',
            'max_tokens': maximum if maximum is not None else (131072 if provider == 'deepseek' else 32768)}
    return value


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure tests may not open a network, database or subprocess')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(database, 'engine', forbidden)


@pytest.mark.parametrize('provider,maximum', [('deepseek', 131072), ('qwen', 32768)])
def test_registered_stage_profiles_use_distinct_selected_output_and_timeout_without_mutation(provider, maximum):
    e = execution(provider); before = copy.deepcopy(e)
    base = gateway.cloud_json_options(e); original = copy.deepcopy(base)
    assert base['max_tokens'] == e['max_tokens'] == 8192
    assert validate_evidence_output(e) is e
    for stage, output, timeout in [('map-v3', 8192, 180), ('reduce-v2', maximum, 600)]:
        policy = evidence_policy(e, stage)
        assert policy == {'version': 'cloud-evidence-2026-10-17/' + stage, 'stage': stage,
            'max_input_bytes': 900000, 'transport_timeout_seconds': timeout, 'max_output_tokens': output,
            'max_images': 0, 'context_token_bound': 1000000, 'full_report': True, 'long_context_verified': True}
        options = evidence_options(e, base, stage)
        assert options['max_tokens'] == output and options['model'] == e['model']
        assert options['response_format'] == {'type': 'json_object'}
        if provider == 'deepseek': assert options['thinking'] == {'type': 'disabled'}
        else: assert options['enable_thinking'] is False
        options['response_format']['type'] = 'changed-local-copy'
        assert base == original and e == before


@pytest.mark.parametrize('provider,maximum', [('deepseek', 128), ('qwen', 2048), ('deepseek', 8192)])
def test_map_never_silently_increases_a_smaller_registered_output(provider, maximum):
    e = execution(provider, maximum)
    assert evidence_policy(e, 'map-v3')['max_output_tokens'] == maximum
    assert evidence_options(e, gateway.cloud_json_options(e), 'reduce-v2')['max_tokens'] == maximum


@pytest.mark.parametrize('provider,bad', [('deepseek', True), ('deepseek', 8192.0), ('deepseek', '8192'),
    ('deepseek', None), ('deepseek', 127), ('deepseek', 131073), ('qwen', 32769)])
def test_generic_output_remains_an_explicit_integer_at_most_8192_even_with_nested_budget(provider, bad):
    e = execution(provider); e['max_tokens'] = bad
    with pytest.raises(ValueError): evidence_policy(e, 'map-v3')
    with pytest.raises(ValueError): evidence_policy(e, 'reduce-v2')
    del e['max_tokens']
    with pytest.raises(ValueError): evidence_policy(e, 'reduce-v2')


@pytest.mark.parametrize('change', ['unknown_stage', 'unknown_provider', 'unknown_model', 'embedding',
    'enabled_thinking', 'missing_thinking', 'wrong_thinking_type', 'qwen_thinking'])
def test_unregistered_model_stage_or_thinking_never_expands_the_profile(change):
    e, stage = execution(), 'reduce-v2'
    if change == 'unknown_stage': stage = 'reduce-v3'
    elif change == 'unknown_provider': e['provider'] = 'other'
    elif change == 'unknown_model': e['model'] = 'deepseek-chat'
    elif change == 'embedding': e['task'] = 'embedding'
    elif change == 'enabled_thinking': e['thinking_mode'] = 'enabled'
    elif change == 'missing_thinking': e.pop('thinking_mode')
    elif change == 'wrong_thinking_type': e['thinking_mode'] = False
    else: e = execution('qwen'); e['thinking_mode'] = 'disabled'
    with pytest.raises(ValueError): evidence_policy(e, stage)


@pytest.mark.parametrize('field,value', [('model', 'other-model'), ('max_tokens', 2048),
    ('max_tokens', '8192'), ('thinking', {'type': 'enabled'})])
def test_base_options_cannot_substitute_a_model_or_output_registration(field, value):
    e = execution(); base = gateway.cloud_json_options(e); base[field] = value
    with pytest.raises(ValueError): evidence_options(e, base, 'reduce-v2')


@pytest.mark.parametrize('provider,maximum', [('deepseek', 128), ('deepseek', 8192), ('qwen', 2048)])
def test_no_nested_registration_keeps_generic_budget_in_both_evidence_stages(provider, maximum):
    e = execution(provider, maximum, nested=False); before = copy.deepcopy(e)
    assert validate_evidence_output(e) is e
    for stage in ('map-v3', 'reduce-v2'):
        assert evidence_policy(e, stage)['max_output_tokens'] == maximum
        assert evidence_options(e, gateway.cloud_json_options(e), stage)['max_tokens'] == maximum
    assert e == before


@pytest.mark.parametrize('provider,maximum', [('deepseek', 8193), ('deepseek', 131072), ('qwen', 32768)])
def test_no_nested_registration_cannot_bypass_the_generic_8192_ceiling(provider, maximum):
    e = execution(provider, maximum, nested=False)
    for stage in ('map-v3', 'reduce-v2'):
        with pytest.raises(ValueError): evidence_policy(e, stage)


@pytest.mark.parametrize('mode', ['none', 'list', 'missing_schema', 'unknown_schema', 'extra',
    'missing_max', 'bool', 'float', 'string', 'low', 'deepseek_high', 'qwen_high'])
def test_nested_registration_is_closed_versioned_and_provider_bounded(mode):
    e = execution('qwen' if mode == 'qwen_high' else 'deepseek')
    declaration = e['report_evidence_output']
    if mode == 'none': e['report_evidence_output'] = None
    elif mode == 'list': e['report_evidence_output'] = []
    elif mode == 'missing_schema': declaration.pop('schema')
    elif mode == 'unknown_schema': declaration['schema'] = 'report-evidence-output-v2'
    elif mode == 'extra': declaration['can_apply'] = True
    elif mode == 'missing_max': declaration.pop('max_tokens')
    else: declaration['max_tokens'] = {'bool': True, 'float': 32768.0, 'string': '32768',
        'low': 127, 'deepseek_high': 131073, 'qwen_high': 32769}[mode]
    before = copy.deepcopy(e)
    with pytest.raises(ValueError): validate_evidence_output(e)
    for stage in ('map-v3', 'reduce-v2'):
        with pytest.raises(ValueError): evidence_policy(e, stage)
    assert e == before


def test_registration_validator_does_not_add_new_rules_to_legacy_generic_records():
    old = {'provider': 'legacy-provider', 'max_tokens': '2048', 'task': 'embedding'}
    assert validate_evidence_output(old) is old
    # Other registration validators still own old generic rules. An explicit
    # opt-in must pass this new allowlist/thinking profile as well.
    value = execution(); value['thinking_mode'] = 'enabled'
    with pytest.raises(ValueError): validate_evidence_output(value)


@pytest.fixture
def transport(monkeypatch):
    # The fixture replaces every transport / persistence boundary used below.
    state = NS(events=[], requests=[], finished=[], finish_attempts=[], callbacks=[], reservations=[],
        reason='stop', content='{"value":"新合成结果。"}', http_status=200, client_options=[], call=None,
        extra_choices=[], payload_override=None)
    secret = NS(get_secret_value=lambda: KEY)
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(gateway, 'settings', lambda: NS(deepseek_api_key=secret, dashscope_api_key=secret))
    def reserve(p, cap, bound, request_hash):
        assert p.user_id == 'synthetic-actor' and cap.workspace_id == p.workspace_id
        state.events.append('reserve'); state.reservations.append((bound, request_hash))
        state.call = NS(status='reserved', reserved_tokens=bound,
            data={'actor_id': p.user_id, 'request_hash': request_hash})
        return CALL
    def finish(call_id, receipt=None, status='failed'):
        assert call_id == CALL
        state.finish_attempts.append((copy.deepcopy(receipt), status))
        if state.call.status != 'reserved': return
        state.events.append('finish'); state.finished.append((copy.deepcopy(receipt), status)); state.call.status = status
    monkeypatch.setattr(cloud_budget, 'reserve', reserve)
    monkeypatch.setattr(cloud_budget, 'finish', finish)
    marker = object(); monkeypatch.setattr(database, 'engine', lambda: marker)
    class LedgerSession:
        def __init__(self, connection): assert connection is marker
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def scalar(self, statement): return state.call
        def commit(self): state.events.append('ledger-commit')
    import sqlalchemy.orm
    monkeypatch.setattr(sqlalchemy.orm, 'Session', LedgerSession)
    class Client:
        def __init__(self, **options):
            state.client_options.append(options)
            assert options['follow_redirects'] is options['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, endpoint, **options):
            assert endpoint in {'https://api.deepseek.com/chat/completions',
                'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'}
            assert options['headers'] == {'Authorization': 'Bearer ' + KEY}
            state.events.append('http'); state.requests.append(copy.deepcopy(options['json']))
            payload = {'usage': copy.deepcopy(USAGE), 'choices': [
                {'finish_reason': state.reason, 'message': {'content': state.content}}] + copy.deepcopy(state.extra_choices)}
            if state.payload_override is not None:
                payload = copy.deepcopy(state.payload_override)
            return NS(status_code=state.http_status, headers={'x-request-id': 'synthetic-request'}, json=lambda: payload)
    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    original_validate = gateway.validate
    def validate(schema, value):
        state.events.append('validate'); return original_validate(schema, value)
    monkeypatch.setattr(gateway, 'validate', validate)
    return state


def call(transport, *, stage='reduce-v2', e=None, full_report=True, received=None, reserved=None):
    e = execution() if e is None else e
    cap = NS(id='synthetic-cap', workspace_id='synthetic-workspace',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    principal = NS(user_id='synthetic-actor', workspace_id=cap.workspace_id)
    def on_reserved(identity):
        assert identity == CALL and transport.events[-1] == 'ledger-commit'
        transport.events.append('on-reserved')
        if reserved: reserved(identity)
    def on_response(content, receipt):
        assert 'http' in transport.events and 'validate' not in transport.events
        transport.events.append('callback'); transport.callbacks.append((content, copy.deepcopy(receipt)))
        if received: received(content, receipt)
    return gateway.cloud_json(cap, MESSAGES, SCHEMA, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=on_reserved, on_response=on_response)


@pytest.mark.parametrize('provider,stage,output,timeout', [('deepseek', 'map-v3', 8192, 180),
    ('deepseek', 'reduce-v2', 131072, 600), ('qwen', 'reduce-v2', 32768, 600)])
def test_actual_gateway_selects_one_profile_hashes_exact_options_and_reserves_real_output(transport, provider, stage, output, timeout):
    e = execution(provider); policy = evidence_policy(e, stage)
    value, receipt = call(transport, stage=stage, e=e)
    prepared, _, _ = gateway.prepare_cloud_json(NS(data={'execution': e, 'data_policy': {'redact_fields': []}}), MESSAGES, SCHEMA)
    expected_options = evidence_options(e, gateway.cloud_json_options(e), stage)
    assert transport.requests == [{**expected_options, 'messages': prepared}]
    assert transport.client_options == [{'timeout': timeout, 'follow_redirects': False, 'trust_env': False}]
    assert transport.reservations == [(measure_input(prepared, policy, output)['token_bound'], digest(prepared))]
    assert transport.call.data['dispatch_options_hash'] == digest(expected_options)
    assert transport.call.data['input_policy_hash'] == digest({'single_batch': True, **policy})
    assert transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'validate', 'finish']
    assert value == {'value': '新合成结果。'}
    assert receipt == {'provider': provider, 'model': e['model'], 'usage': USAGE,
        'request_id': 'synthetic-request', 'call_id': CALL, 'evidence_profile': policy,
        'finish_reason': 'stop', 'response_choice_count': 1}
    assert transport.callbacks == [(transport.content, receipt)] and transport.finished == [(receipt, 'succeeded')]


@pytest.mark.parametrize('full_report', [False, True])
def test_legacy_none_preserves_old_receipt_options_bounds_and_does_not_apply_finish_gate(transport, full_report):
    e = execution(); transport.reason = 'length'
    value, receipt = call(transport, stage=None, e=e, full_report=full_report)
    cap = NS(data={'execution': e, 'data_policy': {'redact_fields': []}})
    prepared, text_bytes, images = gateway.prepare_cloud_json(cap, MESSAGES, SCHEMA)
    bound = (measure_input(prepared, input_policy(e, full_report=True), 8192)['token_bound'] if full_report
        else text_bytes + len(json.dumps(SCHEMA).encode()) + 8192 + 512 + images * 8192)
    assert transport.requests == [{**gateway.cloud_json_options(e), 'messages': prepared}]
    assert transport.reservations == [(bound, digest(prepared))]
    assert transport.client_options[0]['timeout'] == (180 if full_report else 60)
    assert set(receipt) == {'provider', 'model', 'usage', 'request_id', 'call_id'}
    assert transport.callbacks == [(transport.content, receipt)]
    assert value == {'value': '新合成结果。'} and transport.finished == [(receipt, 'succeeded')]
    if full_report:
        assert transport.call.data['input_policy_hash'] == digest({'single_batch': True, **input_policy(e, full_report=True)})
    else: assert 'input_policy_hash' not in transport.call.data


def test_legacy_none_keeps_callbacks_optional(transport):
    e = execution(maximum=8192, nested=False)
    cap = NS(id='synthetic-cap', workspace_id='synthetic-workspace',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    value, receipt = gateway.cloud_json(cap, MESSAGES, SCHEMA,
        NS(user_id='synthetic-actor', workspace_id=cap.workspace_id), full_report=True)
    assert value == {'value': '新合成结果。'} and 'evidence_profile' not in receipt
    assert transport.events == ['reserve', 'http', 'validate', 'finish']


def test_reduce_output_reservation_cannot_overflow_context_even_when_text_is_below_byte_limit(transport):
    e = execution(); policy = evidence_policy(e, 'reduce-v2')
    cap = NS(id='synthetic-cap', workspace_id='synthetic-workspace',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    messages = [{'role': 'user', 'content': 'x' * 880000}]
    prepared, _, _ = gateway.prepare_cloud_json(cap, messages, SCHEMA)
    # The same input fits Map; its much larger explicit Reduce reservation does
    # not fit. A byte-only guard would incorrectly reserve and dispatch.
    assert measure_input(prepared, evidence_policy(e, 'map-v3'), 8192)['prepared_messages_bytes'] < 900000
    with pytest.raises(ValueError): measure_input(prepared, policy, 131072)
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(cap, messages, SCHEMA, NS(user_id='synthetic-actor', workspace_id=cap.workspace_id),
            full_report=True, evidence_stage='reduce-v2', on_reserved=lambda *args: None,
            on_response=lambda *args: None)
    assert transport.requests == transport.reservations == transport.finished == []


def test_unexpected_extra_choice_keeps_first_original_callback_before_rejection(transport):
    transport.extra_choices = [{'finish_reason': 'stop', 'message': {'content': '{"value":"extra"}'}}]
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert transport.callbacks[0][0] == transport.content and 'validate' not in transport.events
    assert transport.events.index('callback') < transport.events.index('finish')
    assert transport.finished[0][0]['usage'] == USAGE and transport.finished[0][1] == 'failed'
    assert transport.callbacks[0][1]['response_choice_count'] == transport.finished[0][0]['response_choice_count'] == 2
    assert len(transport.requests) == 1


@pytest.mark.parametrize('choices,count,reason', [([], 0, None), (None, None, None),
    (['not-an-output-object'], 1, None), ([None], 1, None),
    ([{'finish_reason': 'stop', 'message': {}}], 1, 'stop'),
    ([{'finish_reason': 'stop'}], 1, 'stop'),
    ([{'finish_reason': 'stop', 'message': None}], 1, 'stop')])
def test_unreadable_choice_passes_explicit_none_to_callback_and_finishes_original_usage(transport, choices, count, reason):
    transport.payload_override = {'usage': copy.deepcopy(USAGE), 'choices': choices}
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert len(transport.requests) == len(transport.callbacks) == len(transport.finished) == 1
    original, receipt = transport.callbacks[0]
    assert original is None  # No empty string, JSON serialization or invented answer.
    assert receipt['response_choice_count'] == count and receipt['finish_reason'] == reason
    assert receipt['provider'] == 'deepseek' and receipt['model'] == 'deepseek-v4-pro'
    assert receipt['usage'] == USAGE and receipt['call_id'] == CALL
    assert transport.finished == [(receipt, 'failed')] and 'validate' not in transport.events
    assert transport.events.index('callback') < transport.events.index('finish')


def test_missing_content_still_finishes_receipt_when_real_journal_rejects_none_wire(transport):
    from generic_agents.report_evidence_journal import JournalError
    transport.payload_override = {'usage': copy.deepcopy(USAGE), 'choices': [{'finish_reason': 'stop'}]}
    def no_wire(content, receipt):
        assert content is None
        raise JournalError('journal_content_type_invalid')
    with pytest.raises(gateway.ModelBlocked):
        call(transport, received=no_wire)
    assert len(transport.requests) == len(transport.finished) == 1
    receipt, status = transport.finished[0]
    assert receipt['usage'] == USAGE and receipt['response_choice_count'] == 1 and status == 'failed'
    assert transport.callbacks[0][0] is None and 'validate' not in transport.events


def test_callback_cannot_hide_multiple_provider_choices_from_saved_receipt(transport):
    transport.extra_choices = [{'finish_reason': 'stop', 'message': {'content': '{"value":"extra"}'}}]
    def tamper(content, receipt): receipt['response_choice_count'] = 1
    with pytest.raises(gateway.ModelBlocked): call(transport, received=tamper)
    assert transport.callbacks[0][1]['finish_reason'] == 'stop'
    assert transport.callbacks[0][1]['response_choice_count'] == transport.finished[0][0]['response_choice_count'] == 2
    assert transport.finished[0][0]['usage'] == USAGE and transport.finished[0][1] == 'failed'
    assert len(transport.requests) == 1 and 'validate' not in transport.events


@pytest.mark.parametrize('reason', ['length', None, 'content_filter', 'tool_calls', 'stop '])
def test_non_stop_even_with_valid_json_is_durable_callback_then_failed_with_real_usage_and_no_retry(transport, reason):
    transport.reason = reason
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert len(transport.requests) == len(transport.reservations) == len(transport.callbacks) == 1
    assert 'validate' not in transport.events
    assert transport.events.index('callback') < transport.events.index('finish')
    receipt, status = transport.finished[0]
    assert status == 'failed' and receipt['usage'] == USAGE and receipt['finish_reason'] == reason
    assert receipt['evidence_profile'] == evidence_policy(execution(), 'reduce-v2')


@pytest.mark.parametrize('mode', ['ascii_size', 'utf8_size', 'unicode', 'invalid_json', 'invalid_schema'])
def test_unsafe_or_invalid_answer_is_not_truncated_repaired_or_repaid_and_preserves_usage(transport, mode):
    if mode == 'ascii_size': transport.content = json.dumps({'value': 'x' * MAX_RESPONSE_BYTES})
    elif mode == 'utf8_size': transport.content = json.dumps({'value': '汉' * (MAX_RESPONSE_BYTES // 3 + 1)}, ensure_ascii=False)
    elif mode == 'unicode': transport.content = '{"value":"' + chr(0xD800) + '"}'
    elif mode == 'invalid_json': transport.content = '{"value":'
    else: transport.content = '{"value":5}'
    original = transport.content
    with pytest.raises(gateway.ModelBlocked): call(transport)
    assert transport.callbacks[0][0] == original and transport.content == original
    assert len(transport.requests) == len(transport.finished) == 1
    assert transport.finished[0][0]['usage'] == USAGE and transport.finished[0][1] == 'failed'
    assert transport.finished[0][0]['evidence_profile'] == evidence_policy(execution(), 'reduce-v2')
    if mode in {'ascii_size', 'utf8_size', 'unicode', 'invalid_json'}: assert 'validate' not in transport.events


def test_exact_utf8_byte_boundary_is_accepted_without_a_smaller_hidden_character_limit(transport):
    prefix, suffix = '{"value":"', '"}'
    transport.content = prefix + 'x' * (MAX_RESPONSE_BYTES - len(prefix + suffix)) + suffix
    assert len(transport.content.encode('utf-8')) == MAX_RESPONSE_BYTES
    value, receipt = call(transport)
    assert value['value'] == 'x' * (MAX_RESPONSE_BYTES - len(prefix + suffix))
    assert transport.callbacks[0][0] == transport.content and transport.finished == [(receipt, 'succeeded')]


def test_callback_failure_still_finishes_original_usage_then_propagates_without_second_http(transport):
    error = RuntimeError('synthetic callback durability failure')
    def fail(*args): raise error
    with pytest.raises(RuntimeError) as caught: call(transport, received=fail)
    assert caught.value is error and len(transport.requests) == 1
    receipt, status = transport.finished[0]
    assert receipt['usage'] == USAGE and receipt['evidence_profile']['stage'] == 'reduce-v2' and status == 'failed'


def test_callback_cannot_rewrite_provider_finish_reason_profile_or_usage_to_turn_length_into_success(transport):
    transport.reason = 'length'
    def tamper(content, receipt):
        receipt['finish_reason'] = 'stop'; receipt['usage']['total_tokens'] = 0
        receipt['evidence_profile']['max_output_tokens'] = 1
    with pytest.raises(gateway.ModelBlocked): call(transport, received=tamper)
    receipt, status = transport.finished[0]
    assert receipt['finish_reason'] == 'length' and receipt['usage'] == USAGE
    assert receipt['evidence_profile']['max_output_tokens'] == 131072 and status == 'failed'


@pytest.mark.parametrize('mode', ['non_full', 'missing_reserved', 'missing_response', 'legacy_large', 'bad_stage',
    'nested_bool', 'nested_extra', 'nested_unknown'])
def test_invalid_opt_in_never_reserves_or_opens_http(transport, mode):
    e = execution(); cap = NS(id='synthetic-cap', workspace_id='synthetic-workspace',
        data={'execution': e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    stage, full = ('unknown' if mode == 'bad_stage' else 'reduce-v2'), mode != 'non_full'
    reserved = None if mode == 'missing_reserved' else lambda *args: None
    received = None if mode == 'missing_response' else lambda *args: None
    if mode == 'legacy_large': stage = None; e['max_tokens'] = 131072
    elif mode == 'nested_bool': e['report_evidence_output']['max_tokens'] = True
    elif mode == 'nested_extra': e['report_evidence_output']['extra'] = True
    elif mode == 'nested_unknown': e['report_evidence_output']['schema'] = 'other'
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(cap, MESSAGES, SCHEMA, NS(user_id='synthetic-actor', workspace_id=cap.workspace_id),
            full_report=full, evidence_stage=stage, on_reserved=reserved, on_response=received)
    assert transport.requests == transport.reservations == transport.finished == []
