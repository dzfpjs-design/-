"""Real V211 gateway/capture/bridge validators with synthetic HTTP/ledger IO.

Reuses the established narrow fake session/provider boundary, not source or
budget authority. Real temporary capture files retain the synthetic HTTP body.
Root executes; nothing here contacts a provider or reads private credentials.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_business_catalogue_native_v211 as native
from generic_agents import report_business_catalogue_wire_v211 as wire
from generic_agents import report_design_grouped_native_v176 as legacy_native
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options)
from generic_agents.cloud_input_profiles import input_policy
from generic_agents.contracts import Manifest, digest
from generic_agents.report_evidence_plan import build_evidence_source
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE
from tests.test_saved_map_design_gateway_pure_v176 import (
    native_transport, capability, ACTOR, WORKSPACE, DRAFT, invoke as invoke_legacy)
from tests.test_report_business_catalogue_native_v211 import (
    catalogue_scene, arguments, payload, envelope)
from tests.test_report_evidence_http_capture_v1_pure import context as capture_context


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def invoke(state, *, e=None, stage=native.STAGE, value=None, mutate_payload=None,
           mutate_context=None, full_report=True, callbacks=True, finish_reason='stop',
           with_catalogue=True, mutate_catalogue=None):
    candidate, bound = catalogue_scene(); args = arguments(bound)
    e = execution() if e is None else e
    schema = wire.output_schema(**args)
    candidate = candidate if value is None else value
    user = payload(bound)
    catalogue = copy.deepcopy(bound['catalog'])
    if mutate_catalogue is not None:
        catalogue = mutate_catalogue(catalogue)
    if mutate_payload is not None:
        mutate_payload(user)
    messages = [{'role': 'system', 'content': '{"task":"synthetic catalogue candidate only"}'},
        {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]
    response = envelope(candidate); response['usage'] = USAGE
    response['choices'][0]['finish_reason'] = finish_reason
    state.response = json.dumps(response, ensure_ascii=False).encode('utf-8')
    cap = capability(e); principal = NS(user_id=ACTOR, workspace_id=WORKSPACE)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        actual_inputs = native.request_inputs(prepared, evidence_catalogue=catalogue)
        assert actual_inputs == args
        actual = native.prepare_request(prepared, schema,
            evidence_options(e, gateway.cloud_json_options(e), stage), stage,
            evidence_catalogue=catalogue, **actual_inputs)
        policy = evidence_policy(e, stage)
        binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': ACTOR,
            'workspace_id': WORKSPACE, 'draft_id': DRAFT, 'stage': 'reduce',
            'reserved_call_id': identity, 'source_hash': user['source_hash'],
            'catalog_hash': user['catalog_hash'],
            'generation_hash': digest({'execution': e, 'schema': schema}),
            'dispatch_hash': digest({'stage': stage, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(stage)['max_response_bytes']}
        if mutate_context is not None:
            mutate_context(state.context)
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] and saved['response_complete']
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.events.append('callback'); state.callbacks.append((original, copy.deepcopy(receipt)))

    extra = {'evidence_catalogue': catalogue} if with_catalogue else {}
    return gateway.cloud_json(cap, messages, schema, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=reserved if callbacks else None,
        on_response=received if callbacks else None, **extra)


def test_registered_catalogue_gateway_keeps_one_real_request_capture_budget_and_machine_projection(native_transport):
    state = native_transport; e = execution(); before = copy.deepcopy(e)
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='business_catalogue',
        version='1.0.0', owner='Synthetic public fixture', description='Not source or execution authority',
        execution=e, allowed_agent_roles=['business'], data_policy={'cloud_allowed': True})
    assert manifest.execution == e and validate_evidence_output(e) is e
    assert input_policy(e, full_report=True)['max_input_bytes'] == 50000
    assert input_policy(e, full_report=True)['max_output_tokens'] == 8192
    with pytest.raises(ValueError): declared_map_transport(e)
    result, receipt = invoke(state)
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    body = state.requests[0]['content']; request = json.loads(body)
    assert body == state.expected['body']
    assert state.reservations[0] == (len(body) + 32768 + 1024, hashlib.sha256(body).hexdigest())
    bound = native.request_inputs(request['messages'], evidence_catalogue=catalogue_scene()[1]['catalog'])
    local_schema = wire.output_schema(**bound)
    proof = native.identity_schema_proof(local_schema, native.STAGE, **bound)
    assert request['response_format']['json_schema']['schema'] == proof['provider_schema']
    assert request['max_tokens'] == 32768 and request['enable_thinking'] is False
    sent = json.loads(request['messages'][-1]['content'])
    assert 'catalog' not in sent and 'evidence_catalogue' not in request
    assert sent['spans'] == payload(catalogue_scene()[1])['spans']
    assert 'tools' not in request and request['stream'] is False
    assert result['schema'] == wire.PROJECTION and result['wire']['schema'] == wire.WIRE
    assert len(result['assignments']) == len(bound['input_claims']) == result['input_count']
    assert result['pending_positions'] == [6] and result['human_confirmed'] is False
    assert result['dispatch_allowed'] is False and result['can_apply'] is False
    assert state.call.data['schema_proof_hash'] == digest(proof)
    assert state.call.data['input_policy_hash'] == digest({'single_batch': True, **native.input_policy(e)})
    assert receipt['delivery'] == native.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert receipt['arguments_sha256'] != receipt['response_body_sha256']
    assert state.finished[-1][1] == 'succeeded' and e == before


@pytest.mark.parametrize('fault', ['old_stage', 'no_stage', 'old_declaration', 'old_transport',
    'provider', 'secret', 'model', 'callbacks', 'scope', 'missing_catalog', 'wrong_tail'])
def test_unregistered_or_wrong_source_catalogue_is_blocked_before_reservation(native_transport, fault):
    e = execution(); kwargs = {'e': e}
    if fault == 'old_stage': kwargs['stage'] = legacy_native.STAGE
    elif fault == 'no_stage': kwargs['stage'] = None
    elif fault == 'old_declaration': e['report_evidence_output']['schema'] = legacy_native.OUTPUT_DECLARATION
    elif fault == 'old_transport': e['report_evidence_output']['reduce_transport'] = legacy_native.DELIVERY
    elif fault == 'provider': e['provider'] = 'deepseek'
    elif fault == 'secret': e['secret_ref'] = 'DEEPSEEK_API_KEY'
    elif fault == 'model': e['model'] = 'qwen3.8-flash'
    elif fault == 'callbacks': kwargs['callbacks'] = False
    elif fault == 'scope': kwargs['full_report'] = False
    elif fault == 'missing_catalog': kwargs['with_catalogue'] = False
    else: kwargs['mutate_payload'] = lambda value: value['indexed_inputs']['input_claims'].pop()
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert not native_transport.requests and not native_transport.reservations


@pytest.mark.parametrize('fault', ['corrupt', 'wrong_source', 'none', 'catalogue_in_http', 'lost_span'])
def test_server_catalogue_integrity_and_all_reference_spans_are_checked_before_reservation(native_transport, fault):
    kwargs = {}
    if fault == 'corrupt':
        def changed(catalogue):
            catalogue['index']['w:p1/s1']['text'] += '改原文'
            return catalogue
        kwargs['mutate_catalogue'] = changed
    elif fault == 'wrong_source':
        kwargs['mutate_catalogue'] = lambda catalogue: build_evidence_source('f' * 64,
            [[ref, catalogue['index'][ref + '/s1']['text']]
                for ref in catalogue['descriptor']['ordered_source_refs']])
    elif fault == 'none':
        kwargs['mutate_catalogue'] = lambda _: None
    elif fault == 'catalogue_in_http':
        kwargs['mutate_payload'] = lambda value: value.update(catalog=catalogue_scene()[1]['catalog'])
    else:
        kwargs['mutate_payload'] = lambda value: value['spans'].pop()
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert not native_transport.requests and not native_transport.reservations


@pytest.mark.parametrize('server_value', [None, {}])
def test_old_stage_rejects_even_explicit_none_server_catalogue_before_any_native_work(native_transport, server_value):
    # Existing old-stage tests prove normal requests still work. This tests the
    # additional keyword boundary itself; no old request/capture is redefined.
    cap = capability(execution()); p = NS(user_id=ACTOR, workspace_id=WORKSPACE)
    with pytest.raises(gateway.ModelBlocked):
        gateway.cloud_json(cap, [], {}, p, full_report=True, evidence_stage=legacy_native.STAGE,
            evidence_catalogue=server_value, on_reserved=lambda _: None, on_response=lambda *_: None)
    assert not native_transport.requests and not native_transport.reservations


@pytest.mark.parametrize('fault', ['endpoint', 'request_hash', 'source_stage', 'delivery', 'schema_proof'])
def test_capture_identity_mismatch_preserves_reservation_and_never_posts(native_transport, fault):
    def change(ctx):
        if fault == 'endpoint': ctx['endpoint'] += '/other'
        elif fault == 'request_hash': ctx['request_hash'] = 'f' * 64
        elif fault == 'source_stage': ctx['binding']['stage'] = 'map-001'
        elif fault == 'delivery': ctx['delivery'] = legacy_native.DELIVERY
        else: ctx['schema_proof_hash'] = 'f' * 64
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, mutate_context=change)
    assert len(native_transport.reservations) == 1 and not native_transport.requests
    assert native_transport.finished[-1][1] == 'failed'


@pytest.mark.parametrize('fault', ['missing_tail', 'repeat_position', 'finish_length', 'old_v166'])
def test_complete_but_invalid_same_call_is_durable_failed_without_decoder_fallback(native_transport, fault):
    value, _ = catalogue_scene(); kwargs = {}
    if fault == 'missing_tail': value['allocation_groups'][0]['input_positions'].remove(7)
    elif fault == 'repeat_position': value['allocation_groups'][1]['input_positions'].append(7)
    elif fault == 'finish_length': kwargs['finish_reason'] = 'length'
    else: value = {'result': {'schema': 'report-object-semantic-candidate-wire-v166'}}
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, value=value, **kwargs)
    saved = capture.load(native_transport.root, native_transport.context)
    assert saved['ready'] and saved['response_complete']
    assert capture.read_body(native_transport.root, native_transport.context, saved['capture_hash']) == native_transport.response
    assert len(native_transport.requests) == len(native_transport.reservations) == len(native_transport.callbacks) == 1
    assert native_transport.finished[-1][1] == 'failed'
    body, receipt, state = bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == native_transport.response
    assert receipt['call_id'] == CALL and state['capture_hash'] == saved['capture_hash']
    assert len(native_transport.requests) == 1


def test_original_catalogue_capture_cold_read_does_not_relabel_stage_policy_or_provider(native_transport):
    _, expected = invoke(native_transport)
    policy = native.input_policy(execution())
    body, receipt, _ = bridge.response_context(native_transport.context, 'qwen', native.MODEL, policy, native.STAGE)
    assert body.encode('utf-8') == native_transport.response and receipt == expected
    for stage in ('reduce-v179', 'reduce-v178', 'reduce-v176', 'reduce-v172', None):
        with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL, policy, stage)
    changed = copy.deepcopy(policy); changed['max_output_tokens'] -= 1
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL, changed, native.STAGE)
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'deepseek', native.MODEL, policy, native.STAGE)
    assert len(native_transport.requests) == 1


def test_old_capture_remains_readable_and_cannot_be_promoted_to_catalogue(native_transport):
    _, expected = invoke_legacy(native_transport)
    e = execution(); e['report_evidence_output'].update(
        schema=legacy_native.OUTPUT_DECLARATION, reduce_transport=legacy_native.DELIVERY)
    body, receipt, _ = bridge.response_context(native_transport.context, 'qwen', legacy_native.MODEL,
        legacy_native.input_policy(e), legacy_native.STAGE)
    assert body.encode('utf-8') == native_transport.response and receipt == expected
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(native_transport.requests) == 1


def test_partial_original_capture_is_never_decoded_or_replayed(native_transport):
    native_transport.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport)
    saved = capture.load(native_transport.root, native_transport.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(native_transport.requests) == 1 and native_transport.finished[-1][1] == 'failed'


def test_catalogue_capture_context_is_exact_reduce_public_endpoint_and_original_context_is_unchanged():
    ctx = capture_context('reduce'); ctx.update(delivery=native.DELIVERY, endpoint=native.ENDPOINT)
    before = copy.deepcopy(ctx)
    assert capture._context(ctx) == ctx and ctx == before
    mutations = [
        lambda value: value.update(endpoint=capture.ENDPOINT),
        lambda value: value.update(endpoint='https://api.deepseek.com/chat/completions'),
        lambda value: value.update(endpoint=native.ENDPOINT + '/other'),
        lambda value: value.update(delivery=native.DELIVERY + '-other'),
        lambda value: value.update(max_bytes=capture.MAP_LIMIT),
        lambda value: value.update(token_bound=True),
    ]
    for mutate in mutations:
        changed = copy.deepcopy(ctx); mutate(changed)
        with pytest.raises(capture.CaptureError): capture._context(changed)
    wrong_stage = capture_context('map-1')
    wrong_stage.update(delivery=native.DELIVERY, endpoint=native.ENDPOINT)
    with pytest.raises(capture.CaptureError): capture._context(wrong_stage)
