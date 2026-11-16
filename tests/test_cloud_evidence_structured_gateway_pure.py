"""Real gateway control flow with synthetic HTTP/ledger only; root executes."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.cloud_evidence_profiles import evidence_policy
from generic_agents.report_evidence_map_slots_v5 import MapSlotsWireV5, encode
from generic_agents.report_evidence_reduction_wire_v6 import ReductionWireV6, encode_reduction_wire_v6
from tests.test_report_evidence_wire_v3_pure import reduce_case
from tests.test_cloud_evidence_strict_gateway_pure import transport, no_external_execution, CALL, KEY, USAGE


SCHEMA = MapSlotsWireV5.model_json_schema()
VIEW = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '职责', 'statement': '合成事实。',
    'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''}]}
VALUE = encode(VIEW, [2])
MESSAGES = [{'role': 'user', 'content': '{"source":"合成事实。"}'}]


def case(stage):
    if stage == 'map-v5': return SCHEMA, VALUE
    value, _ = reduce_case()
    return ReductionWireV6.model_json_schema(), encode_reduction_wire_v6(value)


def execution():
    return {'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'max_tokens': 8192,
        'thinking_mode': 'disabled', 'secret_ref': 'DEEPSEEK_API_KEY',
        'report_evidence_output': {'schema': 'report-evidence-output-v3', 'max_tokens': 131072,
            'map_transport': tool.DELIVERY, 'reduce_transport': tool.DELIVERY}}


def setup(state, stage, *, usage=True, arguments=None):
    value = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 'synthetic_report_call', 'type': 'function', 'function': {
            'name': tool.stage_policy(stage)['tool_name'],
            'arguments': json.dumps(case(stage)[1], ensure_ascii=False) if arguments is None else arguments}}]}}]}
    if usage: value['usage'] = copy.deepcopy(USAGE)
    state.body = json.dumps(value, ensure_ascii=False).encode()
    return value


def call(state, stage, *, e=None, received=None, reserved=None):
    cap = NS(id='synthetic-capability', workspace_id='synthetic-workspace',
        data={'execution': execution() if e is None else e, 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    principal = NS(user_id='synthetic-actor', workspace_id=cap.workspace_id)
    def on_reserved(identity):
        state.events.append('on-reserved')
        if reserved: reserved(identity)
    def on_response(content, receipt):
        assert 'new-parse' not in state.events and 'local-schema' not in state.events
        state.events.append('callback'); state.callbacks.append((content, copy.deepcopy(receipt)))
        if received: received(content, receipt)
    return gateway.cloud_json(cap, MESSAGES, case(stage)[0], principal, evidence_stage=stage,
        full_report=True, on_reserved=on_reserved, on_response=on_response)


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_fixed_tool_full_actual_post_is_reserved_once_and_original_response_precedes_parse(transport, monkeypatch, stage):
    setup(transport, stage); parse = tool.parse_arguments
    def counted(*args): transport.events.append('new-parse'); return parse(*args)
    monkeypatch.setattr(tool, 'parse_arguments', counted)
    value, receipt = call(transport, stage)
    assert value == case(stage)[1] and receipt['delivery'] == tool.DELIVERY
    assert transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'new-parse', 'local-schema', 'finish']
    assert len(transport.requests) == len(transport.reservations) == len(transport.callbacks) == len(transport.finished) == 1
    raw = transport.requests[0]['content']; request = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == transport.reservations[0][1]
    assert request['tools'][0]['function']['name'] == tool.stage_policy(stage)['tool_name']
    assert '$def' in request['tools'][0]['function']['parameters']
    assert request['max_tokens'] == evidence_policy(execution(), stage)['max_output_tokens']
    assert transport.callbacks[0][0].encode() == transport.body
    assert receipt['call_id'] == CALL and receipt['evidence_profile']['stage'] == stage
    assert transport.call.charged_tokens == USAGE['total_tokens']


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
@pytest.mark.parametrize('mode', ['http', 'length', 'wrong_tool', 'bad_args', 'duplicate_args', 'multi_tool'])
def test_bad_response_is_saved_before_rejection_and_never_retried(transport, stage, mode):
    value = setup(transport, stage); item = value['choices'][0]; fn = item['message']['tool_calls'][0]['function']
    if mode == 'http': transport.status = 400
    elif mode == 'length': item['finish_reason'] = 'length'
    elif mode == 'wrong_tool': fn['name'] = 'run_device'
    elif mode == 'bad_args': fn['arguments'] = 'not-json'
    elif mode == 'duplicate_args': fn['arguments'] = '{"schema":"x","schema":"y"}'
    else: item['message']['tool_calls'].append(copy.deepcopy(item['message']['tool_calls'][0]))
    transport.body = json.dumps(value, ensure_ascii=False).encode()
    with pytest.raises(gateway.ModelBlocked): call(transport, stage)
    assert len(transport.requests) == len(transport.callbacks) == len(transport.finished) == 1
    assert transport.callbacks[0][0].encode() == transport.body
    assert transport.call.status == 'failed'


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_missing_usage_keeps_full_reserved_charge_and_valid_arguments(transport, stage):
    setup(transport, stage, usage=False); value, receipt = call(transport, stage)
    assert value == case(stage)[1] and receipt['usage_available'] is False and receipt['usage_unavailable'] is True
    assert transport.call.charged_tokens == transport.reservations[0][0]


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_callback_failure_never_parses_or_fakes_success(transport, monkeypatch, stage):
    setup(transport, stage)
    def forbidden(*args): raise AssertionError('must persist before parsing')
    def failed(*args): raise RuntimeError('synthetic durability failure')
    monkeypatch.setattr(tool, 'parse_arguments', forbidden)
    with pytest.raises(RuntimeError): call(transport, stage, received=failed)
    assert len(transport.requests) == len(transport.finished) == 1 and transport.call.status == 'failed'


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_reservation_gate_failure_is_held_and_never_sends(transport, stage):
    setup(transport, stage)
    def failed(*args): raise gateway.ModelBlocked('synthetic frozen request mismatch')
    with pytest.raises(gateway.ModelBlocked): call(transport, stage, reserved=failed)
    assert len(transport.reservations) == 1 and not transport.requests and not transport.callbacks
    assert transport.call.charged_tokens == transport.call.reserved_tokens


@pytest.mark.parametrize('stage', ['map-v5', 'reduce-v3'])
def test_old_registered_profile_never_silently_uses_new_gateway(transport, stage):
    setup(transport, stage); value = execution(); value['report_evidence_output'] = {
        'schema': 'report-evidence-output-v2', 'max_tokens': 131072, 'map_transport': 'deepseek-strict-map-tool-v1'}
    with pytest.raises(gateway.ModelBlocked): call(transport, stage, e=value)
    assert not transport.reservations and not transport.requests
