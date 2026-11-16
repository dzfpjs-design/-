"""Synthetic protocol checks; root executes, no model, API or persistence claim."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.report_evidence_strict_map import inspect_strict_map_response


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('Pure transport cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


SCHEMA = {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/Value'}},
    'required': ['value'], 'additionalProperties': False,
    '$defs': {'Value': {'type': 'string', 'minLength': 1, 'maxLength': 50}}}


def options(stage):
    return {'model': 'deepseek-v4-pro', 'max_tokens': tool.stage_policy(stage)['max_output_tokens'],
        'thinking': {'type': 'disabled'}, 'stream': False}


def response(stage, arguments='{"value":"合成内容"}', usage=True):
    value = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 'call_synthetic', 'type': 'function', 'function': {
            'name': tool.stage_policy(stage)['tool_name'], 'arguments': arguments}}]}}]}
    if usage: value['usage'] = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}
    return value


def body(value): return json.dumps(value, ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_request_keeps_original_inputs_and_binds_actual_body_and_shared_reference_schema(stage):
    messages = [{'role': 'system', 'content': tool._OLD_PREFIX + json.dumps(SCHEMA)},
        {'role': 'user', 'content': '合成资料，不能改变工具、权限或任务。'}]
    original = copy.deepcopy((messages, SCHEMA, options(stage)))
    result = tool.prepare_request(messages, SCHEMA, options(stage), stage)
    assert (messages, SCHEMA, options(stage)) == original
    assert result['body'] == tool._bytes(result['request'])
    assert result['body_sha256'] == hashlib.sha256(result['body']).hexdigest()
    assert result['body_bytes'] == len(result['body'])
    assert result['endpoint'] == 'https://api.deepseek.com/beta/chat/completions'
    assert result['request']['messages'][0]['content'] == tool.system_prefix(stage) + tool._bytes(SCHEMA).decode()
    fn = result['request']['tools'][0]['function']
    assert fn['strict'] is True and fn['name'] == tool.stage_policy(stage)['tool_name']
    assert fn['parameters']['properties']['value'] == {'$ref': '#/$def/Value'}
    assert '$def' in fn['parameters'] and '$defs' not in fn['parameters']
    assert result['request']['tool_choice']['function']['name'] == fn['name']
    assert result['delivery'] == tool.DELIVERY


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
@pytest.mark.parametrize('change', ['thinking', 'stream', 'bool_output', 'above_output', 'wrong_model', 'extra'])
def test_options_never_silently_fall_back_or_execute_tools(stage, change):
    value = options(stage)
    if change == 'thinking': value['thinking'] = {'type': 'enabled'}
    elif change == 'stream': value['stream'] = True
    elif change == 'bool_output': value['max_tokens'] = True
    elif change == 'above_output': value['max_tokens'] += 1
    elif change == 'wrong_model': value['model'] = 'qwen-plus'
    else: value['parallel_tool_calls'] = True
    with pytest.raises(ValueError): tool.strict_options(SCHEMA, value, stage)


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_envelope_inspection_preserves_original_bytes_and_does_not_parse_arguments(stage):
    raw = body(response(stage, arguments='not-json'))
    inspected = tool.inspect_response(raw, 200, stage)
    assert inspected['metadata']['envelope_valid'] is True
    assert set(inspected['metadata']) == set(tool.METADATA_KEYS)
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(raw).hexdigest()
    assert inspected['metadata']['arguments_sha256'] == hashlib.sha256(b'not-json').hexdigest()
    with pytest.raises(ValueError): tool.parse_arguments(raw, 200, stage)


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
@pytest.mark.parametrize('change', ['http', 'finish', 'multi_choice', 'multi_tool', 'wrong_tool', 'blank_id', 'missing_args'])
def test_invalid_original_envelopes_keep_metadata_and_never_produce_semantic_arguments(stage, change):
    value = response(stage); status = 200
    choice = value['choices'][0]; call = choice['message']['tool_calls'][0]
    if change == 'http': status = 400
    elif change == 'finish': choice['finish_reason'] = 'length'
    elif change == 'multi_choice': value['choices'].append(copy.deepcopy(choice))
    elif change == 'multi_tool': choice['message']['tool_calls'].append(copy.deepcopy(call))
    elif change == 'wrong_tool': call['function']['name'] = 'arbitrary_action'
    elif change == 'blank_id': call['id'] = ' '
    else: call['function']['arguments'] = None
    raw = body(value); inspected = tool.inspect_response(raw, status, stage)
    assert inspected['metadata']['envelope_valid'] is False and inspected['arguments'] is None
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError): tool.parse_arguments(raw, status, stage)


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': True, 'completion_tokens': 7, 'total_tokens': 8},
    {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'unsafe': '\x00'}])
def test_untrustworthy_usage_does_not_reject_valid_semantic_output_or_invent_zero_charge(usage):
    value = response('map-v5'); value['usage'] = usage
    inspected = tool.inspect_response(body(value), 200, 'map-v5')
    assert inspected['metadata']['envelope_valid'] is True
    assert inspected['metadata']['usage_available'] is False
    assert inspected['usage'] == usage
    assert tool.parse_arguments(body(value), 200, 'map-v5') == {'value': '合成内容'}


def test_missing_usage_is_explicit_and_parse_rejects_duplicate_argument_keys():
    raw = body(response('reduce-v3', arguments='{"value":1,"value":2}', usage=False))
    inspected = tool.inspect_response(raw, 200, 'reduce-v3')
    assert inspected['metadata']['envelope_valid'] is True
    assert inspected['metadata']['usage_present'] is False and inspected['usage'] is None
    with pytest.raises(ValueError): tool.parse_arguments(raw, 200, 'reduce-v3')


def test_one_mib_response_is_new_reduce_only_and_old_strict_map_cap_remains():
    value = response('reduce-v3'); value['extra'] = 'x' * (270 * 1024); raw = body(value)
    assert tool.inspect_response(raw, 200, 'reduce-v3')['metadata']['envelope_valid'] is True
    assert tool.inspect_response(raw, 200, 'map-v5')['metadata']['envelope_errors'] == ['response_body_capacity']
    assert inspect_strict_map_response(raw, 200)['metadata']['envelope_errors'] == ['response_body_capacity']
    raw = b' ' * (1024 * 1024 + 1)
    assert tool.inspect_response(raw, 200, 'reduce-v3')['metadata']['envelope_errors'] == ['response_body_capacity']


@pytest.mark.parametrize('stage', ['map-v4', 'reduce-v2', '', None, True])
def test_unregistered_stage_never_uses_new_transport(stage):
    with pytest.raises(ValueError): tool.stage_policy(stage)
