"""Synthetic provider-format tests; no original report or provider access."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from pydantic import ValidationError

from generic_agents import report_evidence_strict_map as strict
from generic_agents.contracts import digest
from generic_agents.report_evidence_wire_v3 import MapResultWireV3


VALUE = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '业务',
    'statement': '合成资料仅用于协议测试。', 'span_refs': ['w:p1/s1'],
    'status': 'explicit', 'explanation': ''}]}
USAGE = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure strict format tests cannot execute IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def envelope(*, arguments=None, usage=USAGE):
    return {'usage': copy.deepcopy(usage), 'choices': [{'finish_reason': 'tool_calls',
        'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'synthetic-call', 'type': 'function', 'function': {
                'name': strict.TOOL_NAME,
                'arguments': json.dumps(VALUE, ensure_ascii=False) if arguments is None else arguments}}]}}]}


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def test_projection_retains_enum_required_closed_objects_and_local_bounds():
    schema = MapResultWireV3.model_json_schema(); before = copy.deepcopy(schema)
    proof = strict.compile_schema(schema)
    provider = proof['provider_schema']
    claim = provider['properties']['claims']['items']
    assert claim['properties']['kind']['enum'] == [
        '职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他']
    assert claim['additionalProperties'] is False
    assert set(claim['required']) == set(claim['properties'])
    assert provider['properties']['schema']['enum'] == ['report-evidence-map-v3']
    assert schema == before and proof['local_schema_hash'] == digest(schema)
    assert proof['provider_schema_hash'] == digest(provider)
    assert proof['proof_hash'] == digest({k: v for k, v in proof.items() if k != 'proof_hash'})
    assert {item['operation'] for item in proof['transformations']} <= {
        'inline_local_ref', 'inline_definitions', 'remove_annotation',
        'remove_provider_unsupported_bound', 'string_const_to_singleton_enum'}
    assert any(item['operation'] == 'remove_provider_unsupported_bound' for item in proof['transformations'])
    bad = copy.deepcopy(VALUE); bad['claims'][0]['kind'] = '建议'
    with pytest.raises(ValidationError): MapResultWireV3.model_validate(bad)
    bad = copy.deepcopy(VALUE); bad['claims'][0]['statement'] = ''
    with pytest.raises(ValidationError): MapResultWireV3.model_validate(bad)


@pytest.mark.parametrize('change', ['external', 'cycle', 'unknown', 'optional', 'open', 'nested_defs'])
def test_unsupported_schema_is_rejected_without_mutating_input(change):
    schema = {'type': 'object', 'properties': {'x': {'type': 'string'}},
        'required': ['x'], 'additionalProperties': False}
    if change == 'external': schema['properties']['x'] = {'$ref': 'https://invalid.example/schema'}
    elif change == 'cycle':
        schema['$defs'] = {'A': {'$ref': '#/$defs/A'}}
        schema['properties']['x'] = {'$ref': '#/$defs/A'}
    elif change == 'unknown': schema['unevaluatedProperties'] = False
    elif change == 'optional': schema['required'] = []
    elif change == 'open': schema['additionalProperties'] = True
    else: schema['properties']['x']['$defs'] = {}
    before = copy.deepcopy(schema)
    with pytest.raises(strict.StrictMapError): strict.compile_schema(schema)
    assert schema == before


def test_request_freezes_named_nonexecuting_tool_and_exact_complete_post_bytes():
    schema = MapResultWireV3.model_json_schema()
    messages = [{'role': 'system', 'content': strict._OLD_PREFIX + json.dumps(schema, ensure_ascii=False)},
        {'role': 'user', 'content': '{"source":"合成全文"}'}]
    options = {'model': 'deepseek-v4-pro', 'max_tokens': 8192,
        'thinking': {'type': 'disabled'}, 'stream': False}
    before = copy.deepcopy([messages, schema, options])
    value = strict.prepare_strict_map_request(messages, schema, options)
    assert value['endpoint'] == 'https://api.deepseek.com/beta/chat/completions'
    assert value['request']['tool_choice'] == {'type': 'function', 'function': {'name': strict.TOOL_NAME}}
    assert value['request']['tools'][0]['function']['strict'] is True
    assert 'response_format' not in value['request']
    assert json.loads(value['body']) == value['request']
    assert value['body_sha256'] == hashlib.sha256(value['body']).hexdigest()
    assert value['body_bytes'] == len(value['body'])
    assert [messages, schema, options] == before


@pytest.mark.parametrize('mode', ['bad_utf8', 'bad_json', 'duplicate', 'array', 'capacity', 'http400'])
def test_available_body_always_yields_actual_hash_and_nonconsumable_error(mode):
    body = encode(envelope()); status = 200
    if mode == 'bad_utf8': body = b'\xff\xfe'
    elif mode == 'bad_json': body = b'{unfinished'
    elif mode == 'duplicate': body = b'{"choices":[],"choices":[]}'
    elif mode == 'array': body = b'[]'
    elif mode == 'capacity': body = b' ' * (strict.MAX_RESPONSE_BYTES + 1)
    else: status = 400
    value = strict.inspect_strict_map_response(body, status)
    assert set(value['metadata']) == set(strict.METADATA_KEYS)
    assert value['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert value['metadata']['response_body_bytes'] == len(body)
    assert value['metadata']['envelope_valid'] is False
    assert value['metadata']['envelope_errors']
    with pytest.raises(strict.StrictMapError): strict.parse_strict_map_arguments(body, status)


@pytest.mark.parametrize('mode', ['finish', 'two_choices', 'two_tools', 'wrong_name', 'missing_id', 'nonstring_args'])
def test_only_one_original_named_tool_is_eligible_without_rewriting_metadata(mode):
    value = envelope(); choice = value['choices'][0]; tool = choice['message']['tool_calls'][0]
    if mode == 'finish': choice['finish_reason'] = 'stop'
    elif mode == 'two_choices': value['choices'].append(copy.deepcopy(choice))
    elif mode == 'two_tools': choice['message']['tool_calls'].append(copy.deepcopy(tool))
    elif mode == 'wrong_name': tool['function']['name'] = 'other'
    elif mode == 'missing_id': tool['id'] = ''
    else: tool['function']['arguments'] = VALUE
    body = encode(value); inspected = strict.inspect_strict_map_response(body, 200)
    assert not inspected['metadata']['envelope_valid']
    with pytest.raises(strict.StrictMapError): strict.parse_strict_map_arguments(body, 200)


@pytest.mark.parametrize('arguments', ['{bad', '{"x":1,"x":2}', '[]', '{"x":NaN}'])
def test_argument_json_is_not_adopted_or_repaired_during_envelope_inspection(arguments):
    body = encode(envelope(arguments=arguments))
    value = strict.inspect_strict_map_response(body, 200)
    assert value['metadata']['envelope_valid'] is True
    assert value['arguments'] == arguments
    assert value['metadata']['arguments_sha256'] == hashlib.sha256(arguments.encode()).hexdigest()
    with pytest.raises(strict.StrictMapError): strict.parse_strict_map_arguments(body, 200)


@pytest.mark.parametrize('extra', ['bad\x00usage', '\ud800', float('nan'), {1: 'bad key'}])
def test_unsafe_usage_cannot_enter_a_receipt_or_veto_valid_arguments(extra):
    usage = {**USAGE, 'extra': extra}
    assert strict.strict_usage_available(usage) is False
    if type(extra) is str:
        body = encode(envelope(usage=usage)); value = strict.inspect_strict_map_response(body, 200)
        assert value['metadata']['usage_available'] is False
        assert strict.parse_strict_map_arguments(body, 200) == VALUE
        assert value['usage'] == usage


def test_deep_usage_never_raises_before_original_response_can_be_journaled():
    deep = 'leaf'
    for _ in range(500): deep = [deep]
    # Construct without deepcopy: that was the former pre-durability failure.
    value = envelope(); value['usage']['extra'] = deep; body = encode(value)
    inspected = strict.inspect_strict_map_response(body, 200)
    assert inspected['metadata']['envelope_valid'] is True
    assert inspected['metadata']['usage_available'] is False
    assert strict.parse_strict_map_arguments(body, 200) == VALUE


@pytest.mark.parametrize('bad_id', ['bad\x00id', '\ud800'])
def test_unsafe_identifier_is_null_in_safe_receipt_and_original_body_is_not_rewritten(bad_id):
    value = envelope(); value['choices'][0]['message']['tool_calls'][0]['id'] = bad_id
    body = encode(value); metadata = strict.inspect_strict_map_response(body, 200)['metadata']
    assert metadata['tool_call_id'] is None and metadata['envelope_valid'] is False
    assert metadata['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode('utf-8')
    assert '\x00' not in json.dumps(metadata, ensure_ascii=False)


@pytest.mark.parametrize('usage', [None, [], {},
    {'prompt_tokens': True, 'completion_tokens': 1, 'total_tokens': 2},
    {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 4},
    {'prompt_tokens': 2147483648, 'completion_tokens': 0, 'total_tokens': 2147483648}])
def test_usage_nonobject_bool_inconsistent_or_sql_overflow_is_unavailable(usage):
    assert strict.strict_usage_available(usage) is False
    body = encode(envelope(usage=usage))
    assert strict.inspect_strict_map_response(body, 200)['metadata']['usage_available'] is False
    assert strict.parse_strict_map_arguments(body, 200) == VALUE
