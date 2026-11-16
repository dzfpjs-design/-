"""Actual V178 gateway/capture/bridge seams with synthetic transport only.

The existing narrowly faked CloudCall/session/HTTP boundary and real tmp capture
are reused. Nothing here grants source access, contacts a provider or proves
model quality. Root runs this file independently of repository conftest.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_design_grouped_native_v176 as legacy_native
from generic_agents import report_design_grouped_native_v178 as native
from generic_agents import report_design_grouped_wire_v178 as wire
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options)
from generic_agents.cloud_input_profiles import input_policy, measure_request_body
from generic_agents.contracts import Manifest, digest
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE
from tests.test_saved_map_design_gateway_pure_v176 import (
    native_transport, capability, ACTOR, WORKSPACE, DRAFT, invoke as invoke_legacy)
from tests.test_report_design_grouped_native_v178 import (
    source_scene, payload, envelope)


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def invoke(state, *, e=None, stage=native.STAGE, schema=None, value=None,
           mutate_payload=None, mutate_context=None, full_report=True, callbacks=True,
           finish_reason='stop'):
    answer, _, bound, actual_schema = source_scene()
    e = execution() if e is None else e
    schema = actual_schema if schema is None else schema
    answer = answer if value is None else value
    user = payload(bound)
    if mutate_payload is not None: mutate_payload(user)
    messages = [{'role': 'system', 'content': '{"task":"synthetic indexed candidate only"}'},
        {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]
    response = envelope(answer); response['usage'] = USAGE
    response['choices'][0]['finish_reason'] = finish_reason
    state.response = json.dumps(response, ensure_ascii=False).encode('utf-8')
    cap = capability(e); principal = NS(user_id=ACTOR, workspace_id=WORKSPACE)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        actual_inputs = native.request_inputs(prepared)
        assert actual_inputs == bound
        actual = native.prepare_request(prepared, schema,
            evidence_options(e, gateway.cloud_json_options(e), stage), stage, **actual_inputs)
        policy = evidence_policy(e, stage)
        binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': ACTOR,
            'workspace_id': WORKSPACE, 'draft_id': DRAFT, 'stage': 'reduce',
            'reserved_call_id': identity, 'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
            'generation_hash': digest({'execution': e, 'schema': schema}),
            'dispatch_hash': digest({'stage': stage, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(stage)['max_response_bytes']}
        if mutate_context is not None: mutate_context(state.context)
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(original, receipt):
        assert original.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] and saved['response_complete']
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.events.append('callback'); state.callbacks.append((original, copy.deepcopy(receipt)))

    return gateway.cloud_json(cap, messages, schema, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=reserved if callbacks else None,
        on_response=received if callbacks else None)


def test_actual_registered_gateway_binds_indexed_claims_schema_body_budget_and_original_callback(native_transport):
    state = native_transport; e = execution(); before = copy.deepcopy(e)
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='indexed_design',
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
    assert measure_request_body(body, evidence_policy(e, native.STAGE), 32768)['token_bound'] == state.reservations[0][0]
    assert request['model'] == native.MODEL and request['max_tokens'] == 32768
    assert request['enable_thinking'] is False and request['stream'] is False and request['n'] == 1
    assert 'tools' not in request
    bound = native.request_inputs(request['messages'])
    schema = request['response_format']['json_schema']['schema']
    assert schema == wire.output_schema(**bound)
    assert request['response_format']['json_schema']['strict'] is True
    assert schema['properties']['input_count']['const'] == len(bound['input_claims']) == result['input_count']
    positions = schema['$defs']['ReconciliationGroupV178']['properties']['input_positions']
    assert positions['items']['maximum'] == result['input_count'] - 1 and positions['uniqueItems'] is True
    proof = state.expected['schema_proof']
    assert proof['indexed_inputs_hash'] == bound['indexed']['inputs_hash']
    assert proof['source_snapshot_hash'] == bound['source_snapshot_hash']
    assert state.call.data['schema_proof_hash'] == digest(proof)
    assert state.call.data['input_policy_hash'] == digest({'single_batch': True, **native.input_policy(e)})
    assert receipt['delivery'] == native.DELIVERY and receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert receipt['arguments_sha256'] != receipt['response_body_sha256']
    assert state.finished[0][1] == 'succeeded' and e == before


@pytest.mark.parametrize('stage', [None, 'reduce-v176', 'map-v172', 'reduce-v172', 'map-v168',
    'reduce-v168', 'reduce-v5', 'map-v178'])
def test_new_declaration_cannot_borrow_generic_or_old_stage(native_transport, stage):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, stage=stage)
    assert native_transport.events == native_transport.requests == native_transport.reservations == []


@pytest.mark.parametrize('family', ['v176', 'v172', 'v168'])
def test_old_declaration_cannot_borrow_new_stage(native_transport, family):
    e = execution()
    if family == 'v176':
        e['report_evidence_output'].update(schema=legacy_native.OUTPUT_DECLARATION,
            reduce_transport=legacy_native.DELIVERY)
    else:
        delivery = 'qwen-public-claim-enum-native-v172' if family == 'v172' else 'qwen-public-claim-native-v168'
        e['report_evidence_output'] = {'schema': 'report-evidence-output-' + family,
            'max_tokens': 32768, 'map_transport': delivery, 'reduce_transport': delivery, 'endpoint': native.ENDPOINT}
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.events == []


@pytest.mark.parametrize('fault', ['count', 'position', 'claim', 'snapshot', 'missing_index', 'weaken_schema',
    'not_full', 'no_callbacks'])
def test_source_bound_preparation_and_callback_gates_precede_reservation(native_transport, fault):
    kwargs = {}
    def mutate(value):
        if fault == 'count': value['indexed_inputs']['input_count'] = 1920
        elif fault == 'position': value['indexed_inputs']['input_claims'][0]['input_position'] = False
        elif fault == 'claim': value['indexed_inputs']['input_claims'][0]['claim']['statement'] += 'changed'
        elif fault == 'snapshot': value['source_snapshot_hash'] = 'f' * 64
        elif fault == 'missing_index': value.pop('indexed_inputs')
    if fault == 'weaken_schema':
        schema = source_scene()[3]
        schema['$defs']['ReconciliationGroupV178']['properties']['input_positions']['items']['maximum'] = 1919
        kwargs['schema'] = schema
    elif fault == 'not_full': kwargs['full_report'] = False
    elif fault == 'no_callbacks': kwargs['callbacks'] = False
    else: kwargs['mutate_payload'] = mutate
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert native_transport.events == []


@pytest.mark.parametrize('field', ['request_hash', 'schema_proof_hash', 'profile_hash', 'token_bound',
    'messages_hash', 'actor_id', 'workspace_id'])
def test_actual_capture_binding_is_checked_before_post(native_transport, field):
    def mutate(ctx):
        if field in {'messages_hash', 'actor_id', 'workspace_id'}:
            ctx['binding'][field] = 'e' * 64 if field == 'messages_hash' else '20000000-0000-4000-8000-000000000001'
        else: ctx[field] = ctx[field] + 1 if field == 'token_bound' else 'e' * 64
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, mutate_context=mutate)
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


@pytest.mark.parametrize('fault', ['old_wire', 'invented_count', 'missing_position', 'unsorted', 'length'])
def test_original_http_is_durable_before_parse_failure_and_no_fallback_or_repost(native_transport, fault):
    value = source_scene()[0]; kwargs = {}
    if fault == 'old_wire': value.pop('input_count')
    elif fault == 'invented_count': value['input_count'] = 1920
    elif fault == 'missing_position': value['reconciliation_groups'].pop()
    elif fault == 'unsorted': value['reconciliation_groups'][0]['input_positions'] = [1, 0]
    else: kwargs['finish_reason'] = 'length'
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, value=value, **kwargs)
    saved = capture.load(native_transport.root, native_transport.context)
    assert saved['ready'] and saved['response_complete']
    assert capture.read_body(native_transport.root, native_transport.context, saved['capture_hash']) == native_transport.response
    assert len(native_transport.requests) == len(native_transport.reservations) == len(native_transport.callbacks) == 1
    assert native_transport.events.index('callback') < native_transport.events.index('finish')
    assert native_transport.finished[-1][1] == 'failed'
    body, receipt, state = bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == native_transport.response and state['capture_hash'] == saved['capture_hash']
    assert receipt['call_id'] == CALL and len(native_transport.requests) == 1


def test_complete_same_call_cold_read_is_new_stage_only_and_never_dispatches(native_transport):
    _, expected = invoke(native_transport)
    body, receipt, _ = bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == native_transport.response and receipt == expected
    for stage in ('reduce-v176', 'reduce-v172', 'reduce-v168', None):
        with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
            native.input_policy(execution()), stage)
    assert len(native_transport.requests) == 1


def test_cold_read_cannot_relabel_frozen_policy_with_caller_values(native_transport):
    invoke(native_transport)
    policy = native.input_policy(execution()); policy['max_output_tokens'] -= 1
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        policy, native.STAGE)
    assert len(native_transport.requests) == 1


def test_old_cold_transport_remains_legacy_and_cannot_be_redecoded_as_indexed(native_transport):
    _, expected = invoke_legacy(native_transport)
    old_execution = execution()
    old_execution['report_evidence_output'].update(schema=legacy_native.OUTPUT_DECLARATION,
        reduce_transport=legacy_native.DELIVERY)
    body, receipt, _ = bridge.response_context(native_transport.context, 'qwen', legacy_native.MODEL,
        legacy_native.input_policy(old_execution), legacy_native.STAGE)
    assert body.encode('utf-8') == native_transport.response and receipt == expected
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(native_transport.requests) == 1 and native_transport.call.data['delivery'] == legacy_native.DELIVERY


def test_partial_capture_never_produces_a_new_response_or_second_post(native_transport):
    native_transport.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport)
    saved = capture.load(native_transport.root, native_transport.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    with pytest.raises(ValueError): bridge.response_context(native_transport.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(native_transport.requests) == len(native_transport.reservations) == 1
