"""Actual V182 gateway/native/capture seams with synthetic transport only.

Real preparation and schema use the public source fixture. HTTP, credentials,
CloudCall and Session are narrowly faked; tmp-path capture is real. No provider
quality, source ownership, consent, persistent consumer or DB is proved here.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import gateway
from generic_agents import report_business_flow_native_v182 as native
from generic_agents import report_business_flow_preparation_v182 as preparation
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents import report_scoped_design_native_v181 as prior
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.cloud_evidence_profiles import (
    evidence_options, evidence_policy, validate_evidence_output, declared_map_transport)
from generic_agents.cloud_input_profiles import input_policy, measure_request_body
from generic_agents.contracts import Manifest, digest
from tests.test_cloud_evidence_strict_gateway_pure import no_external_execution, CALL, USAGE
from tests.test_saved_map_design_gateway_pure_v176 import native_transport, capability, ACTOR, WORKSPACE, DRAFT
from tests.test_report_business_flow_protocol_v182 import scene, answer, response
from tests.test_report_scoped_design_gateway_v181 import invoke as invoke_prior, execution as prior_execution


def execution():
    return {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 16384,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}


def invoke(state, *, e=None, stage=native.STAGE, schema=None, value=None,
           mutate_payload=None, mutate_context=None, full_report=True, callbacks=True,
           finish='stop', raw_http=None):
    source_cap, source, plan = scene()
    prepared = preparation.build_preparation(source_cap, source, plan, 'custom:alpha')
    request = preparation.rebuild_request(prepared, source_cap, source, plan, 'custom:alpha')
    binding = prepared['scope_binding']; actual_schema = prepared['generation_contract']['output_schema']
    schema = actual_schema if schema is None else schema
    e = execution() if e is None else e
    messages = copy.deepcopy(request['messages'][1:])
    if mutate_payload is not None:
        payload = json.loads(messages[-1]['content']); mutate_payload(payload)
        messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    value = answer(prepared) if value is None else value
    http_value = json.loads(response(json.dumps(value, ensure_ascii=False), finish=finish))
    http_value['usage'] = copy.deepcopy(USAGE)
    state.response = json.dumps(http_value, ensure_ascii=False).encode('utf-8') if raw_http is None else raw_http
    state.bound = {'scope_binding': copy.deepcopy(binding)}
    state.local_preparation = prepared
    cap = capability(e); principal = NS(user_id=ACTOR, workspace_id=WORKSPACE)

    def reserved(identity):
        assert identity == CALL and state.events[-1] == 'ledger-commit'
        messages_with_schema, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        kwargs = native.request_inputs(messages_with_schema)
        assert kwargs == state.bound
        actual = native.prepare_request(messages_with_schema, schema,
            evidence_options(e, gateway.cloud_json_options(e), stage), stage, **kwargs)
        policy = evidence_policy(e, stage)
        journal_binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': ACTOR,
            'workspace_id': WORKSPACE, 'draft_id': DRAFT, 'stage': 'reduce', 'reserved_call_id': identity,
            'source_hash': binding['source_hash'], 'catalog_hash': binding['catalog_hash'],
            'generation_hash': digest(prepared['generation_contract']),
            'dispatch_hash': digest({'stage': stage, 'request_hash': actual['body_sha256']}),
            'messages_hash': digest(actual['request']['messages'])}
        state.expected = actual
        state.context = {'binding': journal_binding, 'request_hash': actual['body_sha256'],
            'endpoint': actual['endpoint'], 'delivery': native.DELIVERY,
            'schema_proof_hash': digest(actual['schema_proof']), 'profile_hash': digest(policy),
            'token_bound': len(actual['body']) + actual['request']['max_tokens'] + 1024,
            'max_bytes': native.stage_policy(stage)['max_response_bytes']}
        if mutate_context is not None: mutate_context(state.context)
        state.events.append('on-reserved')
        return capture.prepare(state.root, state.context)

    def received(content, receipt):
        assert content.encode('utf-8') == state.response
        saved = capture.load(state.root, state.context)
        assert saved['ready'] and saved['response_complete']
        assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
        state.events.append('callback'); state.callbacks.append((content, copy.deepcopy(receipt)))

    return gateway.cloud_json(cap, messages, schema, principal, full_report=full_report,
        evidence_stage=stage, on_reserved=reserved if callbacks else None,
        on_response=received if callbacks else None)


def test_actual_flow_gateway_uses_only_bound_scope_kwargs_full_native_body_and_one_call(native_transport, monkeypatch):
    prepares, parses = [], []
    original_prepare, original_parse = native.prepare_request, native.parse_arguments
    def prepare(*args, **kwargs):
        prepares.append(copy.deepcopy(kwargs)); return original_prepare(*args, **kwargs)
    def parse(*args, **kwargs):
        parses.append(copy.deepcopy(kwargs)); return original_parse(*args, **kwargs)
    monkeypatch.setattr(native, 'prepare_request', prepare)
    monkeypatch.setattr(native, 'parse_arguments', parse)
    value, receipt = invoke(native_transport); state = native_transport
    assert prepares and all(item == state.bound for item in prepares)
    assert len(parses) == 1 and parses[0] == {**state.bound,
        'response_complete': True, 'http_content_encoding': None}
    assert state.events == ['reserve', 'ledger-commit', 'on-reserved', 'http', 'callback', 'finish']
    assert len(state.reservations) == len(state.requests) == len(state.callbacks) == 1
    body = state.requests[0]['content']; request = json.loads(body)
    assert body == state.expected['body']
    assert hashlib.sha256(body).hexdigest() == state.local_preparation['request_hash']
    assert state.reservations[0] == (len(body) + 16384 + 1024, hashlib.sha256(body).hexdigest())
    assert measure_request_body(body, evidence_policy(execution(), native.STAGE), 16384)['token_bound'] == state.reservations[0][0]
    assert native.request_inputs(request['messages']) == state.bound
    assert request['model'] == native.MODEL and request['max_tokens'] == 16384
    assert request['enable_thinking'] is False and request['stream'] is False and request['n'] == 1
    assert 'tools' not in request and 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in body.decode('utf-8')
    assert 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in body.decode('utf-8')
    assert set(value) == {'schema', 'binding_hash', 'business', 'evidence'}
    assert len(value['business']['flows'][0]['nodes']) == 2
    assert state.call.data['dispatch_options_hash'] == digest({key: item for key, item in request.items() if key != 'messages'})
    assert state.call.data['schema_proof_hash'] == digest(state.expected['schema_proof'])
    assert state.call.data['delivery'] == receipt['delivery'] == native.DELIVERY
    assert receipt['response_body_sha256'] == hashlib.sha256(state.response).hexdigest()
    assert receipt['arguments_sha256'] != receipt['response_body_sha256']
    assert state.finished[-1][1] == 'succeeded'


def test_actual_provider_schema_makes_citations_reasons_actor_and_graph_shapes_visible(native_transport):
    invoke(native_transport); state = native_transport
    schema = json.loads(state.requests[0]['content'])['response_format']['json_schema']['schema']
    local = wire.output_schema(state.bound['scope_binding'])
    assert schema == state.expected['schema_proof']['provider_schema'] == local
    assert state.expected['schema_proof']['schema_policy']['removed_assertions'] == []
    assert set(schema['properties']) == {'schema', 'binding_hash', 'business', 'evidence'}
    assert schema['properties']['binding_hash']['const'] == digest(state.bound['scope_binding'])
    assert schema['$defs']['BusinessDefinitionV182']['properties']['name']['const'] == state.bound['scope_binding']['name']
    attribution = schema['$defs']['AttributionV182']
    assert set(attribution['required']) == {'citations', 'inferred', 'reason'}
    assert attribution['properties']['citations']['minItems'] == 1
    assert attribution['properties']['reason']['minLength'] == 1
    assert attribution['properties']['inferred']['type'] == 'boolean'
    assert len(schema['$defs']['NodeV182']['properties']['actor']['enum']) == 6
    assert len(schema['$defs']['FlowV182']['properties']['edges']['items']['anyOf']) == 4
    assert 'SpanEvidence' not in json.dumps(schema) and 'CandidateObjects' not in json.dumps(schema)


def test_registration_keeps_scope_policy_separate_from_generic_and_whole_report_map():
    e = execution(); before = copy.deepcopy(e)
    manifest = Manifest(kind='foundation_model', namespace='public_synthetic', name='business_flow',
        version='1.0.0', owner='Synthetic fixture', description='No source selection or cloud consent',
        execution=e, allowed_agent_roles=['business'], data_policy={'cloud_allowed': True})
    assert manifest.execution == e and validate_evidence_output(e) is e
    assert input_policy(e, full_report=True)['max_input_bytes'] == 50000
    assert input_policy(e, full_report=True)['max_output_tokens'] == 8192
    assert native.input_policy(e)['full_report'] is False
    assert native.stage_policy(native.STAGE)['max_response_bytes'] == wire.MAX_WIRE_BYTES == 1024 * 1024
    assert native.stage_policy(native.STAGE)['max_output_tokens'] == 16384
    with pytest.raises(ValueError): declared_map_transport(e)
    assert e == before


@pytest.mark.parametrize('stage', [None, 'reduce-scope-v181', 'reduce-v179', 'reduce-v178', 'reduce-v176',
    'reduce-v172', 'map-v172', 'reduce-v168', 'map-v168'])
def test_flow_declaration_cannot_borrow_old_or_generic_stages(native_transport, stage):
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, stage=stage)
    assert native_transport.events == native_transport.requests == native_transport.reservations == []


@pytest.mark.parametrize('family', ['v181', 'v179', 'v178', 'v176'])
def test_prior_declarations_cannot_enter_flow_stage(native_transport, family):
    e = execution()
    if family == 'v181': declaration, delivery = prior.OUTPUT_DECLARATION, prior.DELIVERY
    elif family == 'v176': declaration, delivery = 'report-saved-map-design-output-v176', 'qwen-public-saved-map-design-native-v176'
    else: declaration, delivery = 'report-indexed-design-output-' + family, 'qwen-public-indexed-design-native-' + family
    e['report_evidence_output'].update(schema=declaration, reduce_transport=delivery)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.events == []


@pytest.mark.parametrize('fault', ['delivery', 'output_limit', 'bool_output', 'endpoint', 'extra_map', 'secret'])
def test_wrong_registration_or_output_limit_never_reserves(native_transport, fault):
    e = execution(); declaration = e['report_evidence_output']
    if fault == 'delivery': declaration['reduce_transport'] = prior.DELIVERY
    elif fault == 'output_limit': declaration['max_tokens'] = 16385
    elif fault == 'bool_output': declaration['max_tokens'] = True
    elif fault == 'endpoint': declaration['endpoint'] += '/wrong'
    elif fault == 'extra_map': declaration['map_transport'] = native.DELIVERY
    else: e['secret_ref'] = 'QWEN_TOKENPLAN_API_KEY'
    if fault != 'secret':
        with pytest.raises(ValueError): validate_evidence_output(e)
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, e=e)
    assert native_transport.events == native_transport.requests == []


@pytest.mark.parametrize('fault', ['claim', 'span', 'binding', 'schema', 'not_full', 'no_callback'])
def test_actual_payload_source_hashes_and_callbacks_are_checked_before_reservation(native_transport, fault):
    kwargs = {}
    def mutate(payload):
        if fault == 'claim': payload['input_claims'][0]['statement'] += ' changed'
        elif fault == 'span': payload['spans'][0][1] += ' changed'
        else: payload['scope_binding']['selected_global_positions'][0] = False
    if fault == 'schema':
        cap, source, plan = scene(); prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
        schema = prep['generation_contract']['output_schema']
        schema['$defs']['AttributionV182']['properties']['citations']['minItems'] = 0
        kwargs['schema'] = schema
    elif fault == 'not_full': kwargs['full_report'] = False
    elif fault == 'no_callback': kwargs['callbacks'] = False
    else: kwargs['mutate_payload'] = mutate
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, **kwargs)
    assert native_transport.events == []


@pytest.mark.parametrize('field', ['delivery', 'profile_hash', 'schema_proof_hash', 'messages_hash', 'max_bytes'])
def test_original_capture_identity_is_checked_before_any_post(native_transport, field):
    def mutate(context):
        if field == 'delivery': context[field] = prior.DELIVERY
        elif field == 'messages_hash': context['binding'][field] = 'f' * 64
        elif field == 'max_bytes': context[field] += 1
        else: context[field] = 'f' * 64
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, mutate_context=mutate)
    assert len(native_transport.reservations) == 1 and native_transport.requests == []


@pytest.mark.parametrize('fault', ['unknown_target', 'citations', 'actor', 'output', 'binding', 'old_wire', 'length'])
def test_invalid_graph_or_old_answer_is_saved_once_before_rejection_without_retry(native_transport, fault):
    cap, source, plan = scene(); prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
    value = answer(prep); kwargs = {}; flow = value['business']['flows'][0]
    if fault == 'unknown_target': flow['edges'][0]['to_node'] = 'missing'
    elif fault == 'citations': flow['nodes'][0]['attribution']['citations'] = []
    elif fault == 'actor': flow['nodes'][0]['actor'] = 'u'
    elif fault == 'output': flow['nodes'][0]['outputs'] = ['u']
    elif fault == 'binding': value['binding_hash'] = 'f' * 64
    elif fault == 'old_wire': value = {'scope_id': 'custom:alpha', 'plan_hash': prep['plan_hash'],
        'result': {}, 'input_count': 208, 'reconciliation_groups': []}
    else: kwargs['finish'] = 'length'
    with pytest.raises(gateway.ModelBlocked): invoke(native_transport, value=value, **kwargs)
    state = native_transport; saved = capture.load(state.root, state.context)
    assert saved['ready'] and saved['response_complete']
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == state.response
    assert len(state.requests) == len(state.reservations) == len(state.callbacks) == 1
    assert state.events.index('callback') < state.events.index('finish') and state.finished[-1][1] == 'failed'
    content, receipt, cold = bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert content.encode('utf-8') == state.response and cold['capture_hash'] == saved['capture_hash']
    assert receipt['call_id'] == CALL and len(state.requests) == 1


def test_literal_quote_check_is_an_independent_projection_gate_not_claimed_by_gateway(native_transport):
    cap, source, plan = scene(); prep = preparation.build_preparation(cap, source, plan, 'custom:alpha')
    authored = answer(prep); authored['evidence'][0]['quote'] += ' 非原文'
    value, _ = invoke(native_transport, value=authored)
    assert value == authored and native_transport.finished[-1][1] == 'succeeded'
    with pytest.raises(ValueError): wire.build_projection(json.dumps(value, ensure_ascii=False).encode('utf-8'),
        scope_binding=prep['scope_binding'], catalog=source['catalog'], allowed_span_refs=prep['allowed_span_refs'],
        context=source['context'], input_claims=prep['input_claims'])
    assert len(native_transport.requests) == 1


def test_cold_read_keeps_flow_delivery_policy_and_rejects_relabelling_as_old_scope(native_transport):
    state = native_transport; _, expected = invoke(state)
    body, receipt, _ = bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert body.encode('utf-8') == state.response and receipt == expected
    for stage in (prior.STAGE, 'reduce-v179', 'reduce-v178', 'reduce-v176', None):
        with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
            native.input_policy(execution()), stage)
    changed = native.input_policy(execution()); changed['max_output_tokens'] -= 1
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL, changed, native.STAGE)
    assert len(state.requests) == 1


def test_old_scoped_181_capture_stays_readable_but_cannot_be_flow_182(native_transport):
    state = native_transport; _, expected = invoke_prior(state)
    body, receipt, _ = bridge.response_context(state.context, 'qwen', prior.MODEL,
        prior.input_policy(prior_execution()), prior.STAGE)
    assert body.encode('utf-8') == state.response and receipt == expected
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == 1 and state.call.data['delivery'] == prior.DELIVERY


@pytest.mark.parametrize('extra', [0, 1])
def test_actual_one_mib_http_limit_preserves_original_and_never_falls_back(native_transport, extra):
    state = native_transport; body = b'x' * (1024 * 1024 + extra)
    with pytest.raises(gateway.ModelBlocked): invoke(state, raw_http=body)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == len(body) and saved['body_sha256'] == hashlib.sha256(body).hexdigest()
    assert capture.read_body(state.root, state.context, saved['capture_hash']) == body
    assert saved['ready'] is (extra == 0) and saved['response_complete'] is (extra == 0)
    assert len(state.requests) == len(state.reservations) == 1
    assert len(state.callbacks) == (1 if extra == 0 else 0) and state.finished[-1][1] == 'failed'
    assert saved['can_adopt'] is False
    if extra:
        assert saved['reason'] == 'capacity_exceeded'
        with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
            native.input_policy(execution()), native.STAGE)


def test_partial_http_cannot_become_a_flow_or_trigger_another_post(native_transport):
    state = native_transport; state.interrupted = True
    with pytest.raises(gateway.ModelBlocked): invoke(state)
    saved = capture.load(state.root, state.context)
    assert saved['body_bytes'] == 17 and saved['response_complete'] is False
    with pytest.raises(ValueError): bridge.response_context(state.context, 'qwen', native.MODEL,
        native.input_policy(execution()), native.STAGE)
    assert len(state.requests) == len(state.reservations) == 1 and state.callbacks == []
