"""Real V181 gateway/capture seams over PUBLIC synthetic HTTP and ledger.

No provider, credentials, DB, durable source selection, human consent or model
quality is exercised. Central functions and real tmp-path capture stay intact;
only the existing narrow transport/session fixture is faked. Root runs this.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_scoped_design_native_v181 as native
from generic_agents import report_scoped_design_preparation_v181 as preparation
from generic_agents import report_scoped_design_wire_v181 as wire
from generic_agents import report_design_grouped_native_v179 as previous
from generic_agents import report_design_grouped_native_v176 as legacy
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import (
    validate_evidence_output, declared_map_transport, evidence_policy, evidence_options)
from generic_agents.cloud_input_profiles import input_policy, measure_request_body
from generic_agents.contracts import Manifest, digest
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE
from tests.test_saved_map_design_gateway_pure_v176 import (
    native_transport, capability, ACTOR, WORKSPACE, DRAFT, invoke as invoke_legacy)
from tests.test_report_scoped_design_v181 import scene, arguments, response, envelope


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def invoke(state, *, e=None, stage=native.STAGE, schema=None, value=None,
           mutate_payload=None, mutate_context=None, full_report=True, callbacks=True,
           finish_reason='stop', raw_http=None):
    source_cap, source, plan = scene()
    prep = preparation.build_preparation(source_cap, source, plan, 'custom:alpha')
    bound = arguments(prep, source)
    e = execution() if e is None else e
    schema = prep['generation_contract']['output_schema'] if schema is None else schema
    answer = response(prep, source) if value is None else value
    payload = copy.deepcopy(prep['payload'])
    if mutate_payload is not None: mutate_payload(payload)
    messages = [{'role': 'system', 'content': '{"task":"synthetic partial scope only"}'},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    http_value = json.loads(envelope(answer, finish_reason))
    http_value['usage'] = copy.deepcopy(USAGE)
    state.response = (json.dumps(http_value, ensure_ascii=False).encode('utf-8')
        if raw_http is None else raw_http)
    state.bound = copy.deepcopy(bound)
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
            'reserved_call_id': identity, 'source_hash': bound['scope_binding']['source_hash'],
            'catalog_hash': bound['scope_binding']['catalog_hash'],
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


def test_central_gateway_forwards_all_four_actual_inputs_to_native_request_and_parse(native_transport, monkeypatch):
    seen_prepare, seen_parse = [], []
    original_prepare, original_parse = native.prepare_request, native.parse_arguments
    def prepare(*args, **kwargs):
        seen_prepare.append(copy.deepcopy(kwargs)); return original_prepare(*args, **kwargs)
    def parse(*args, **kwargs):
        seen_parse.append(copy.deepcopy(kwargs)); return original_parse(*args, **kwargs)
    monkeypatch.setattr(native, 'prepare_request', prepare)
    monkeypatch.setattr(native, 'parse_arguments', parse)
    result, receipt = invoke(native_transport)
    state = native_transport; bound = state.bound
    keys = {'indexed', 'input_claims', 'source_snapshot_hash', 'scope_binding'}
    assert seen_prepare and all(set(item) == keys and item == bound for item in seen_prepare)
    assert len(seen_parse) == 1
    assert {key: seen_parse[0][key] for key in keys} == bound
    assert set(seen_parse[0]) == keys | {'response_complete', 'http_content_encoding'}
    assert seen_parse[0]['response_complete'] is True
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    actual_body = state.requests[0]['content']; request = json.loads(actual_body)
    assert actual_body == state.expected['body']
    assert state.reservations[0] == (len(actual_body) + 32768 + 1024, hashlib.sha256(actual_body).hexdigest())
    assert measure_request_body(actual_body, evidence_policy(execution(), native.STAGE), 32768)['token_bound'] == state.reservations[0][0]
    assert native.request_inputs(request['messages']) == bound
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in actual_body.decode('utf-8')
    assert 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in actual_body.decode('utf-8')
    provider_schema = request['response_format']['json_schema']['schema']
    local_schema = wire.output_schema(**bound)
    assert provider_schema == state.expected['schema_proof']['provider_schema']
    assert provider_schema != local_schema
    positions = provider_schema['$defs']['ReconciliationGroupV181']['properties']['input_positions']
    assert 'uniqueItems' not in positions and positions['items']['maximum'] == 1
    assert local_schema['$defs']['ReconciliationGroupV181']['properties']['input_positions']['uniqueItems'] is True
    assert provider_schema['$defs']['CandidateObjects']['properties']['businesses']['maxItems'] == 1
    assert provider_schema['properties']['input_count']['const'] == result['input_count'] == 2
    assert len(result['result']['businesses']) == 1 and result['scope_id'] == 'custom:alpha'
    assert request['max_tokens'] == 32768 and request['enable_thinking'] is False
    assert request['n'] == 1 and request['stream'] is False and 'tools' not in request
    assert state.call.data['schema_proof_hash'] == digest(state.expected['schema_proof'])
    assert state.call.data['delivery'] == receipt['delivery'] == native.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert state.finished[-1][1] == 'succeeded'


def test_registration_does_not_widen_generic_calls_or_enable_whole_report_map():
    e = execution(); before = copy.deepcopy(e)
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='scoped_design',
        version='1.0.0', owner='Synthetic fixture', description='No source or cloud consent',
        execution=e, allowed_agent_roles=['business'], data_policy={'cloud_allowed': True})
    assert manifest.execution == e and validate_evidence_output(e) is e
    assert input_policy(e, full_report=True)['max_input_bytes'] == 50000
    assert input_policy(e, full_report=True)['max_output_tokens'] == 8192
    with pytest.raises(ValueError): declared_map_transport(e)
    assert e == before


@pytest.mark.parametrize('stage', [None, 'reduce-v179', 'reduce-v178', 'reduce-v176', 'map-v172',
    'reduce-v172', 'map-v168', 'reduce-v168', 'reduce-v5'])
def test_scoped_declaration_never_borrows_old_or_generic_stage(native_transport, stage):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, stage=stage)
    assert native_transport.events == native_transport.reservations == native_transport.requests == []


@pytest.mark.parametrize('family', ['v179', 'v178', 'v176', 'v172', 'v168'])
def test_old_declaration_cannot_enter_new_scoped_stage(native_transport, family):
    e = execution()
    if family in {'v179', 'v178', 'v176'}:
        marker = 'report-indexed-design-output-' + family if family != 'v176' else legacy.OUTPUT_DECLARATION
        delivery = 'qwen-public-indexed-design-native-' + family if family != 'v176' else legacy.DELIVERY
        e['report_evidence_output'].update(schema=marker, reduce_transport=delivery)
    else:
        delivery = 'qwen-public-claim-enum-native-v172' if family == 'v172' else 'qwen-public-claim-native-v168'
        e['report_evidence_output'] = {'schema': 'report-evidence-output-' + family,
            'max_tokens': 32768, 'map_transport': delivery, 'reduce_transport': delivery, 'endpoint': native.ENDPOINT}
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.events == []


@pytest.mark.parametrize('fault', ['scope', 'count', 'position', 'claim', 'snapshot', 'spans',
    'weaken_schema', 'not_full', 'no_callbacks', 'request_capacity'])
def test_actual_payload_and_complete_schema_gates_stop_before_reservation(native_transport, fault):
    kwargs = {}
    def mutate(payload):
        if fault == 'scope': payload['scope']['scope_id'] = 'custom:beta'
        elif fault == 'count': payload['indexed_inputs']['input_count'] = 1920
        elif fault == 'position': payload['indexed_inputs']['input_claims'][0]['input_position'] = False
        elif fault == 'claim': payload['indexed_inputs']['input_claims'][0]['claim']['statement'] += 'changed'
        elif fault == 'snapshot': payload['source_snapshot_hash'] = 'f' * 64
        elif fault == 'spans': payload['spans'][0][1] += 'invented source'
        elif fault == 'request_capacity': payload['design_note'] = 'x' * 900000
    if fault == 'weaken_schema':
        cap, source, plan = scene(); value = preparation.build_preparation(cap, source, plan, 'custom:alpha')
        schema = value['generation_contract']['output_schema']
        schema['$defs']['CandidateObjects']['properties']['businesses']['maxItems'] = 40
        kwargs['schema'] = schema
    elif fault == 'not_full': kwargs['full_report'] = False
    elif fault == 'no_callbacks': kwargs['callbacks'] = False
    else: kwargs['mutate_payload'] = mutate
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert native_transport.events == native_transport.requests == []


@pytest.mark.parametrize('field', ['request_hash', 'schema_proof_hash', 'profile_hash', 'token_bound',
    'messages_hash', 'actor_id', 'workspace_id', 'delivery'])
def test_capture_binding_cannot_be_relabelled_before_single_post(native_transport, field):
    def mutate(ctx):
        if field in {'messages_hash', 'actor_id', 'workspace_id'}:
            ctx['binding'][field] = 'e' * 64 if field == 'messages_hash' else '20000000-0000-4000-8000-000000000001'
        elif field == 'delivery': ctx[field] = previous.DELIVERY
        else: ctx[field] = ctx[field] + 1 if field == 'token_bound' else 'e' * 64
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, mutate_context=mutate)
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


@pytest.mark.parametrize('fault', ['scope', 'old_wire', 'invented_count', 'missing_position', 'duplicate', 'two_businesses', 'length'])
def test_bad_raw_answer_is_saved_once_before_local_rejection_without_fallback(native_transport, fault):
    cap, source, plan = scene(); prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
    answer = response(prep, source); kwargs = {}
    if fault == 'scope': answer['scope_id'] = 'custom:beta'
    elif fault == 'old_wire': answer.pop('scope_id'); answer.pop('plan_hash')
    elif fault == 'invented_count': answer['input_count'] = 1920
    elif fault == 'missing_position': answer['reconciliation_groups'].pop()
    elif fault == 'duplicate': answer['reconciliation_groups'][1]['input_positions'] = [0]
    elif fault == 'two_businesses': answer['result']['businesses'].append(copy.deepcopy(answer['result']['businesses'][0]))
    else: kwargs['finish_reason'] = 'length'
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, value=answer, **kwargs)
    state = native_transport; saved = capture.load(state.root, state.context)
    assert saved['ready'] and saved['response_complete']
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    assert state.events.index('callback') < state.events.index('finish') and state.finished[-1][1] == 'failed'
    body, receipt, cold = bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == state.response and cold['capture_hash'] == saved['capture_hash']
    assert receipt['call_id'] == CALL and len(state.requests) == 1


@pytest.mark.parametrize('limit', [0, True, 256 * 1024, 1024 * 1024 - 1, 1024 * 1024 + 1])
def test_scoped_capture_requires_exact_one_mib_bound(native_transport, limit):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport,
        mutate_context=lambda ctx: ctx.update(max_bytes=limit))
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


@pytest.mark.parametrize('extra', [0, 1])
def test_actual_http_boundary_keeps_exact_limit_and_quarantines_overflow(native_transport, extra):
    state = native_transport; body = b'x' * (wire.MAX_WIRE_BYTES + extra)
    with pytest.raises(gateway.ModelBlocked): invoke(state, raw_http=body)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == len(body) and saved['body_sha256'] == hashlib.sha256(body).hexdigest()
    assert len(state.requests) == len(state.reservations) == 1
    assert saved['ready'] is (extra == 0) and saved['response_complete'] is (extra == 0)
    assert len(state.callbacks) == (1 if extra == 0 else 0)
    assert state.finished[-1][1] == 'failed'
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == body
    assert saved['can_adopt'] is False
    if extra:
        assert saved['prefix_only'] is True and saved['reason'] == 'capacity_exceeded'
        with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
            native.input_policy(execution()), native.STAGE)


def test_complete_cold_read_keeps_delivery_and_policy_and_rejects_old_family(native_transport):
    state = native_transport; _, original = invoke(state)
    body, receipt, _ = bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == state.response and receipt == original
    for stage in ('reduce-v179', 'reduce-v178', 'reduce-v176', 'reduce-v172', 'reduce-v168', None):
        with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
            native.input_policy(execution()), stage)
    changed = native.input_policy(execution()); changed['max_output_tokens'] -= 1
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL, changed, native.STAGE)
    assert len(state.requests) == 1


def test_old_saved_map_capture_remains_cold_readable_but_never_scoped(native_transport):
    state = native_transport; _, expected = invoke_legacy(state)
    e = execution(); e['report_evidence_output'].update(schema=legacy.OUTPUT_DECLARATION, reduce_transport=legacy.DELIVERY)
    body, receipt, _ = bridge.response_context(state.context, 'qwen', legacy.MODEL,
        legacy.input_policy(e), legacy.STAGE)
    assert body.encode('utf-8') == state.response and receipt == expected
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == 1 and state.call.data['delivery'] == legacy.DELIVERY


def test_interrupted_capture_is_not_a_scoped_answer_or_a_second_post(native_transport):
    state = native_transport; state.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(state)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == len(state.reservations) == 1 and state.callbacks == []
