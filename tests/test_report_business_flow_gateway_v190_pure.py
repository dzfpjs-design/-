"""Actual V190 gateway/capture/bridge with public synthetic I/O boundaries.

Preparation, request bytes, native schema/policy, response parsing and tmp-path
capture are real. HTTP, credential lookup, CloudCall and Session use the public
narrow fixture. This does not prove provider quality, source authority, consent,
Postgres durability, causal availability or an end-to-end human workflow.
Root executes this file; its author does not invoke the project or provider.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_business_flow_native_v190 as native
from generic_agents import report_business_flow_native_v187 as previous
from generic_agents import report_business_flow_preparation_v190 as preparation
from generic_agents import report_business_flow_wire_v190 as wire
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import evidence_policy
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_report_business_flow_gateway_v182 as transport_harness
from tests import test_report_business_flow_protocol_v184 as protocol
from tests import test_saved_map_design_gateway_pure_v176 as transport_fixture
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL
from tests.test_report_business_flow_wire_v190 import envelope
from tests.test_saved_map_design_gateway_pure_v176 import native_transport


def execution():
    return {'provider': native.PROVIDER, 'model': native.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def scene():
    # Build a fresh public scope through actual V190 preparation, not old bytes.
    cap, source, plan = protocol.scene()
    cap.data['execution'] = execution()
    return cap, source, plan


def answer(prepared):
    return envelope(protocol.answer(prepared), prepared['scope_binding'])


def invoke(state, monkeypatch, **kwargs):
    """Reuse public transport construction, keeping actual V190 product code."""
    kwargs.setdefault('e', execution())
    kwargs.setdefault('stage', native.STAGE)
    with monkeypatch.context() as patch:
        patch.setenv('DEEPSEEK_API_KEY', '')
        patch.setattr(gateway, 'settings', lambda: NS(dashscope_api_key=None,
            deepseek_api_key=NS(get_secret_value=lambda: transport_fixture.KEY)))
        # Client.stream reads this source global for its exact endpoint check.
        patch.setattr(transport_fixture, 'native', native)
        patch.setattr(transport_harness, 'native', native)
        patch.setattr(transport_harness, 'preparation', preparation)
        patch.setattr(transport_harness, 'scene', scene)
        patch.setattr(transport_harness, 'answer', answer)
        return transport_harness.invoke(state, **kwargs)


def test_actual_v190_gateway_preserves_exact_deepseek_request_and_cold_capture_once(native_transport, monkeypatch):
    routed_calls = []
    actual = gateway._cloud_native_report

    def routed(*args, **kwargs):
        routed_calls.append((args[4], kwargs['stage']))
        return actual(*args, **kwargs)

    monkeypatch.setattr(gateway, '_cloud_native_report', routed)
    value, original_receipt = invoke(native_transport, monkeypatch)
    state = native_transport
    assert routed_calls == [(transport_fixture.KEY, native.STAGE)]
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.reservations) == len(state.requests) == len(state.callbacks) == 1
    body = state.requests[0]['content']
    request = json.loads(body)
    request_hash = hashlib.sha256(body).hexdigest()
    policy = evidence_policy(execution(), native.STAGE)
    prepared = state.local_preparation
    assert prepared['schema'] == preparation.PREPARATION == 'report-business-flow-preparation-v190'
    assert body == state.expected['body'] and request_hash == prepared['request_hash']
    assert state.expected['endpoint'] == native.ENDPOINT == 'https://api.deepseek.com/chat/completions'
    assert state.reservations == [(len(body) + 65536 + 1024, request_hash)]
    assert measure_request_body(body, policy, 65536)['token_bound'] == state.reservations[0][0]
    assert {k: v for k, v in request.items() if k != 'messages'} == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert native.request_inputs(request['messages']) == state.bound
    assert json.loads(request['messages'][-1]['content']) == prepared['payload']
    assert prepared['payload']['schema'] == native.REQUEST_PAYLOAD_SCHEMA
    schema = wire.output_schema(state.bound['scope_binding'])
    assert schema == prepared['generation_contract']['output_schema']
    assert request['messages'][0]['content'] == (
        native.system_prefix(native.STAGE) + native.SCHEMA_PROMPT_PREFIX + canonical_json(schema))
    proof = state.expected['schema_proof']
    assert proof == native.identity_schema_proof(schema, native.STAGE, **state.bound)
    assert proof['provider_schema'] == schema and proof['provider_json_schema_enforcement'] is False
    assert policy['output_reservation'] == native.OUTPUT_RESERVATION
    assert state.context['binding']['stage'] == 'reduce'
    assert state.context['profile_hash'] == digest(policy)
    assert state.context['delivery'] == state.call.data['delivery'] == native.DELIVERY
    assert state.call.data['request_endpoint'] == native.ENDPOINT
    assert state.call.data['input_policy_hash'] == digest({'single_batch': True, **policy})
    assert state.call.data['schema_proof_hash'] == digest(proof)
    assert state.call.data['dispatch_options_hash'] == digest({k: v for k, v in request.items() if k != 'messages'})
    assert value == answer(prepared) and value['schema'] == wire.WIRE
    assert original_receipt['provider'] == native.PROVIDER == 'deepseek'
    assert original_receipt['model'] == native.MODEL and original_receipt['call_id'] == CALL
    assert original_receipt['delivery'] == native.DELIVERY
    assert original_receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert state.finished[-1][1] == 'succeeded'
    saved = capture.load(state.root, state.context)
    content, receipt, cold = bridge.response_context(state.context, native.PROVIDER, native.MODEL,
        policy, native.STAGE)
    assert content.encode('utf-8') == state.response and receipt == original_receipt
    assert cold['capture_hash'] == saved['capture_hash']
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    assert len(state.requests) == len(state.reservations) == 1


@pytest.mark.parametrize('stage', [None, 'business-flow-v187', 'map-v172'])
def test_v190_declaration_cannot_use_old_or_generic_stage(native_transport, monkeypatch, stage):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, stage=stage)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


def test_valid_old_v187_declaration_cannot_enter_v190_gateway(native_transport, monkeypatch):
    old = copy.deepcopy(execution())
    old['report_evidence_output'].update(schema=previous.OUTPUT_DECLARATION,
        reduce_transport=previous.DELIVERY, endpoint=previous.ENDPOINT)
    assert previous.input_policy(old)['stage'] == previous.STAGE
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, e=old)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


def test_interrupted_v190_eof_preserves_original_partial_bytes_without_repost(native_transport, monkeypatch):
    state = native_transport
    state.interrupted = True
    with pytest.raises(gateway.ModelBlocked):
        invoke(state, monkeypatch)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response[:17]
    with pytest.raises(ValueError):
        bridge.response_context(state.context, native.PROVIDER, native.MODEL,
            native.input_policy(execution()), native.STAGE)
    assert len(state.reservations) == len(state.requests) == 1 and state.callbacks == []
