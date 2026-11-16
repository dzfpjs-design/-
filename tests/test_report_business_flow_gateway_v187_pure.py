"""Actual V187 gateway/capture/bridge with public synthetic I/O boundaries.

The request, policy, local schema, native envelope and tmp-path capture are real
product functions. Only HTTP, credential lookup, CloudCall and Session use the
existing narrow fixture. These tests prove neither paid/source authority nor
provider quality, Postgres durability, human consent or whole-report coverage.
Root executes this file; its author does not invoke the project or provider.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_business_flow_native_v187 as native
from generic_agents import report_business_flow_preparation_v187 as preparation
from generic_agents import report_business_flow_native_v184 as previous
from generic_agents import report_business_flow_wire_v184 as wire
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
from tests.test_saved_map_design_gateway_pure_v176 import native_transport


def execution():
    return {'provider': native.PROVIDER, 'model': native.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def scene():
    # Author a new in-memory public fixture, not a relabelled stored activity.
    cap, source, plan = protocol.scene()
    cap.data['execution'] = execution()
    return cap, source, plan


def invoke(state, monkeypatch, **kwargs):
    """Reuse only fixture construction/HTTP boundaries; run the actual gateway."""
    kwargs.setdefault('e', execution())
    kwargs.setdefault('stage', native.STAGE)
    with monkeypatch.context() as patch:
        patch.setenv('DEEPSEEK_API_KEY', '')
        patch.setattr(gateway, 'settings', lambda: NS(dashscope_api_key=None,
            deepseek_api_key=NS(get_secret_value=lambda: transport_fixture.KEY)))
        # The fixture asserts exact headers and endpoint inside Client.stream.
        patch.setattr(transport_fixture, 'native', native)
        patch.setattr(transport_harness, 'native', native)
        patch.setattr(transport_harness, 'preparation', preparation)
        patch.setattr(transport_harness, 'scene', scene)
        patch.setattr(transport_harness, 'answer', protocol.answer)
        return transport_harness.invoke(state, **kwargs)


def test_exact_deepseek_request_policy_and_total_output_are_reserved_once(native_transport, monkeypatch):
    calls = []
    actual = gateway._cloud_native_report

    def routed(*args, **kwargs):
        calls.append((args[4], kwargs['stage']))
        return actual(*args, **kwargs)

    monkeypatch.setattr(gateway, '_cloud_native_report', routed)
    value, receipt = invoke(native_transport, monkeypatch)
    state = native_transport
    assert calls == [(transport_fixture.KEY, native.STAGE)]
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.reservations) == len(state.requests) == len(state.callbacks) == 1
    body = state.requests[0]['content']
    request = json.loads(body)
    request_hash = hashlib.sha256(body).hexdigest()
    policy = evidence_policy(execution(), native.STAGE)
    assert body == state.expected['body']
    assert request_hash == state.local_preparation['request_hash']
    assert state.expected['endpoint'] == native.ENDPOINT == 'https://api.deepseek.com/chat/completions'
    assert state.reservations == [(len(body) + 65536 + 1024, request_hash)]
    assert measure_request_body(body, policy, 65536)['token_bound'] == state.reservations[0][0]
    assert {k: v for k, v in request.items() if k != 'messages'} == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert native.request_inputs(request['messages']) == state.bound
    schema = wire.output_schema(state.bound['scope_binding'])
    assert request['messages'][0]['content'] == (
        native.system_prefix(native.STAGE) + native.SCHEMA_PROMPT_PREFIX + canonical_json(schema))
    proof = state.expected['schema_proof']
    assert proof == native.identity_schema_proof(schema, native.STAGE, **state.bound)
    assert proof['provider_schema'] == schema
    assert proof['provider_json_schema_enforcement'] is False
    assert policy['output_reservation'] == native.OUTPUT_RESERVATION
    assert state.context['profile_hash'] == digest(policy)
    assert state.call.data['request_endpoint'] == native.ENDPOINT
    assert state.call.data['input_policy_hash'] == digest({'single_batch': True, **policy})
    assert state.call.data['schema_proof_hash'] == digest(proof)
    assert state.call.data['dispatch_options_hash'] == digest({k: v for k, v in request.items() if k != 'messages'})
    assert value == protocol.answer(state.local_preparation) and value['schema'] == wire.WIRE
    assert receipt['provider'] == native.PROVIDER == 'deepseek'
    assert receipt['model'] == native.MODEL and receipt['call_id'] == CALL
    assert state.call.data['delivery'] == receipt['delivery'] == native.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert receipt['arguments_sha256'] != receipt['response_body_sha256']
    assert state.finished[-1][1] == 'succeeded'


def test_complete_original_capture_cold_reads_only_its_deepseek_family(native_transport, monkeypatch):
    state = native_transport
    _, original_receipt = invoke(state, monkeypatch)
    policy = native.input_policy(execution())
    saved = capture.load(state.root, state.context)
    content, receipt, cold = bridge.response_context(state.context, native.PROVIDER, native.MODEL,
        policy, native.STAGE)
    assert content.encode('utf-8') == state.response
    assert receipt == original_receipt and cold['capture_hash'] == saved['capture_hash']
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    for provider, model, stage in [('qwen', native.MODEL, native.STAGE),
            (native.PROVIDER, previous.MODEL, native.STAGE),
            (native.PROVIDER, native.MODEL, previous.STAGE),
            (native.PROVIDER, native.MODEL, None)]:
        with pytest.raises(ValueError):
            bridge.response_context(state.context, provider, model, policy, stage)
    changed_policy = copy.deepcopy(policy)
    changed_policy['max_output_tokens'] -= 1
    with pytest.raises(ValueError):
        bridge.response_context(state.context, native.PROVIDER, native.MODEL, changed_policy, native.STAGE)
    changed_context = copy.deepcopy(state.context)
    changed_context['endpoint'] = previous.ENDPOINT
    with pytest.raises(ValueError):
        bridge.response_context(changed_context, native.PROVIDER, native.MODEL, policy, native.STAGE)
    assert len(state.requests) == len(state.reservations) == 1
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response


@pytest.mark.parametrize('stage', [None, 'business-flow-v184', 'business-flow-v185', 'map-v172'])
def test_new_declaration_never_borrows_an_old_or_generic_stage(native_transport, monkeypatch, stage):
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, stage=stage)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('fault', ['provider', 'model', 'key', 'declaration', 'endpoint', 'old_transport'])
def test_wrong_registration_cannot_reserve_or_fall_back_to_qwen(native_transport, monkeypatch, fault):
    e = execution()
    if fault == 'provider': e['provider'] = 'qwen'
    elif fault == 'model': e['model'] = previous.MODEL
    elif fault == 'key': e['secret_ref'] = 'DASHSCOPE_API_KEY'
    elif fault == 'declaration': e['report_evidence_output']['schema'] = previous.OUTPUT_DECLARATION
    elif fault == 'endpoint': e['report_evidence_output']['endpoint'] = previous.ENDPOINT
    else: e['report_evidence_output']['reduce_transport'] = previous.DELIVERY
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, e=e)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('field', ['delivery', 'endpoint', 'schema_proof_hash', 'profile_hash'])
def test_capture_cannot_borrow_old_endpoint_delivery_or_hashes_before_post(native_transport, monkeypatch, field):
    def mutate(context):
        if field == 'delivery': context[field] = previous.DELIVERY
        elif field == 'endpoint': context[field] = previous.ENDPOINT
        else: context[field] = 'f' * 64
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, mutate_context=mutate)
    assert len(native_transport.reservations) == 1
    assert native_transport.requests == native_transport.callbacks == []


def test_invalid_local_plan_is_saved_once_before_rejection_and_never_retried(native_transport, monkeypatch):
    cap, source, plan = scene()
    prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
    invalid = protocol.answer(prep)
    invalid['business']['flows'][0]['completion']['outputs'] = ['值']
    with pytest.raises(gateway.ModelBlocked):
        invoke(native_transport, monkeypatch, value=invalid)
    state = native_transport
    saved = capture.load(state.root, state.context)
    assert saved['ready'] and saved['response_complete']
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    assert len(state.reservations) == len(state.requests) == len(state.callbacks) == 1
    assert state.events.index('callback') < state.events.index('finish')
    assert state.finished[-1][1] == 'failed'
    content, receipt, _ = bridge.response_context(state.context, native.PROVIDER, native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert content.encode('utf-8') == state.response and receipt['provider'] == 'deepseek'
    assert len(state.requests) == 1


def test_interrupted_eof_preserves_partial_bytes_without_repost_or_cold_success(native_transport, monkeypatch):
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
