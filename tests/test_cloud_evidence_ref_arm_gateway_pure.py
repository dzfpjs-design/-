"""Real gateway control flow with synthetic HTTP/ledger; no actual provider."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_evidence_structured_tool_v3 as tool
from generic_agents.cloud_evidence_profiles import evidence_policy
from tests.test_cloud_evidence_structured_gateway_pure import case as old_case, MESSAGES
from tests.test_cloud_evidence_strict_gateway_pure import transport, no_external_execution, CALL, USAGE


def case(stage):
    return old_case('map-v5' if stage == 'map-v6' else 'reduce-v3')


def execution():
    return {'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'max_tokens': 8192,
        'thinking_mode': 'disabled', 'secret_ref': 'DEEPSEEK_API_KEY',
        'report_evidence_output': {'schema': 'report-evidence-output-v4', 'max_tokens': 32768,
            'map_transport': tool.DELIVERY, 'reduce_transport': tool.DELIVERY}}


def setup(state, stage):
    value = {'usage': copy.deepcopy(USAGE), 'choices': [{'finish_reason': 'tool_calls',
        'message': {'tool_calls': [{'id': 'synthetic_ref_arm_call', 'type': 'function',
            'function': {'name': tool.stage_policy(stage)['tool_name'],
                'arguments': json.dumps(case(stage)[1], ensure_ascii=False)}}]}}]}
    state.body = json.dumps(value, ensure_ascii=False).encode()
    return value


def call(state, stage, received=None):
    cap = NS(id='synthetic-capability', workspace_id='synthetic-workspace', data={
        'execution': execution(), 'data_policy': {'cloud_allowed': True, 'redact_fields': []}})
    def reserved(identity):
        assert identity == CALL; state.events.append('on-reserved')
    def response(content, receipt):
        assert 'new-parse' not in state.events and 'local-schema' not in state.events
        state.events.append('callback'); state.callbacks.append((content, copy.deepcopy(receipt)))
        if received: received(content, receipt)
    return gateway.cloud_json(cap, MESSAGES, case(stage)[0],
        NS(user_id='synthetic-actor', workspace_id=cap.workspace_id), full_report=True,
        evidence_stage=stage, on_reserved=reserved, on_response=response)


@pytest.mark.parametrize('stage', ['map-v6', 'reduce-v4'])
def test_new_stage_reserves_exact_full_post_and_saves_original_body_before_parsing(transport, monkeypatch, stage):
    setup(transport, stage); parse = tool.parse_arguments
    def counted(*args): transport.events.append('new-parse'); return parse(*args)
    monkeypatch.setattr(tool, 'parse_arguments', counted)
    result, receipt = call(transport, stage)
    assert result == case(stage)[1] and receipt['delivery'] == tool.DELIVERY
    assert transport.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'new-parse', 'local-schema', 'finish']
    assert len(transport.reservations) == len(transport.requests) == len(transport.callbacks) == 1
    raw = transport.requests[0]['content']; request = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == transport.reservations[0][1]
    assert request['max_tokens'] == (8192 if stage == 'map-v6' else 32768)
    assert transport.reservations[0][0] == len(raw) + request['max_tokens'] + 1024
    assert receipt['evidence_profile'] == evidence_policy(execution(), stage)
    assert transport.callbacks[0][0].encode() == transport.body
    if stage == 'map-v6':
        schema = request['tools'][0]['function']['parameters']
        assert all(arm['type'] == 'object' for arm in schema['$def']['SlotValue']['anyOf'])
        assert all(value == {'$ref': '#/$def/SlotValue'} for value in schema['$def']['Fixed80Slots']['properties'].values())


@pytest.mark.parametrize('stage', ['map-v6', 'reduce-v4'])
@pytest.mark.parametrize('mode', ['http400', 'length', 'multi', 'wrong_tool', 'bad_json', 'duplicate_args'])
def test_failed_new_protocol_keeps_whole_body_receipt_and_one_reservation_without_retry(transport, stage, mode):
    value = setup(transport, stage); choice = value['choices'][0]; function = choice['message']['tool_calls'][0]['function']
    if mode == 'http400': transport.status = 400
    elif mode == 'length': choice['finish_reason'] = 'length'
    elif mode == 'multi': value['choices'].append(copy.deepcopy(choice))
    elif mode == 'wrong_tool': function['name'] = 'unregistered'
    elif mode == 'bad_json': function['arguments'] = '{'
    else: function['arguments'] = '{"schema":"first","schema":"second"}'
    transport.body = json.dumps(value, ensure_ascii=False).encode()
    with pytest.raises(gateway.ModelBlocked): call(transport, stage)
    assert len(transport.requests) == len(transport.reservations) == len(transport.callbacks) == len(transport.finished) == 1
    assert transport.callbacks[0][0].encode() == transport.body
    assert transport.finished[0][1] == 'failed'


@pytest.mark.parametrize('stage', ['map-v6', 'reduce-v4'])
def test_missing_usage_keeps_full_reservation_without_rejecting_legal_arguments(transport, stage):
    value = setup(transport, stage); value.pop('usage'); transport.body = json.dumps(value, ensure_ascii=False).encode()
    result, receipt = call(transport, stage)
    assert result == case(stage)[1] and receipt['usage'] == {} and receipt['usage_unavailable'] is True
    assert transport.call.charged_tokens == transport.call.reserved_tokens


def test_response_callback_failure_does_not_parse_retry_or_release_the_unknown_call(transport, monkeypatch):
    setup(transport, 'map-v6')
    def forbidden(*args): raise AssertionError('Semantic parser must not run')
    monkeypatch.setattr(tool, 'parse_arguments', forbidden)
    def failed(*args): raise RuntimeError('synthetic persistence failure')
    with pytest.raises(RuntimeError): call(transport, 'map-v6', received=failed)
    assert len(transport.requests) == len(transport.callbacks) == len(transport.reservations) == 1
    assert transport.callbacks[0][0].encode() == transport.body and transport.finished[0][1] == 'failed'
