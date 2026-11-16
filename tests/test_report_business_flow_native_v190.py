"""Real V190 request/schema/parse/projection over public synthetic V184 source.

No provider, credentials, DB, original report, runtime authority, consent or
semantic quality is proved. Product validators remain real; Root executes.
"""
import copy
import hashlib
import json

import pytest

from generic_agents import report_business_flow_native_v190 as subject
from generic_agents import report_business_flow_native_v187 as old_native
from generic_agents import report_business_flow_wire_v190 as wire
from generic_agents import business_flow_causal_paths_v189 as paths
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents.cloud_evidence_profiles import evidence_policy, evidence_options, validate_evidence_output, declared_map_transport
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options, prepare_cloud_json
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_native_v187 import scene as old_scene
from tests.test_report_business_flow_protocol_v184 import no_io
from tests.test_report_business_flow_protocol_v182 import response


def raw(value):
    return canonical_json(value).encode('utf-8')


def selector(action):
    part = action['part']
    branch = next((item['branch_index'] for item in action['control_ancestors']
        if item['kind'] in {'decision_branch', 'parallel_branch'}), None)
    return {'flow_index': action['flow_index'], 'phase_index': action['phase_index'], 'part': part,
        'branch_index': branch, 'step_index': int(action['path'].rsplit('/', 1)[1])
            if part in {'step', 'repeat_step', 'branch_step'} else None}


def scene():
    c = old_scene()
    c.old_plan = copy.deepcopy(c.plan)
    c.execution = {'provider': subject.PROVIDER, 'model': subject.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': subject.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': subject.DELIVERY, 'endpoint': subject.ENDPOINT}}
    c.cap.data['execution'] = copy.deepcopy(c.execution)
    c.payload['schema'] = subject.REQUEST_PAYLOAD_SCHEMA
    walked = paths.walk_action_ports(c.old_plan, scope_binding=c.binding)
    contract = {'data_type': 'record', 'unit': None}
    outputs, inputs = [], []
    for action in walked['actions']:
        for port in action['output_ports']:
            outputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract)})
        for port in action['input_ports']:
            inputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract),
                'sources': [{'kind': 'entry', 'index': action['flow_index']}]})
    c.plan = {'schema': wire.WIRE, 'plan': copy.deepcopy(c.old_plan), 'causality': {
        'entry_ports': [{'flow_index': i, 'contract': copy.deepcopy(contract)}
            for i in range(len(c.old_plan['business']['flows']))],
        'output_contracts': outputs, 'input_bindings': inputs}}
    c.schema = wire.output_schema(c.binding)
    messages = copy.deepcopy(c.messages[1:])
    messages[-1]['content'] = json.dumps(c.payload, ensure_ascii=False, indent=2)
    c.messages, _, images = prepare_cloud_json(c.cap, messages, c.schema)
    assert images == 0
    c.options = subject.dispatch_options(c.execution, cloud_json_options(c.execution))
    return c


def request(c):
    return subject.prepare_request(c.messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)


def parse(c, value=None, **overrides):
    params = {'http_status': 200, 'response_complete': True, 'http_content_encoding': None}
    params.update(overrides)
    body = response(canonical_json(c.plan if value is None else value))
    return subject.parse_arguments(body, params.pop('http_status'), subject.STAGE, scope_binding=c.binding, **params)


def test_actual_causal_request_has_exact_new_schema_and_preserves_complete_source_messages():
    c = scene()
    before = canonical_json([c.messages, c.payload, c.plan])
    actual = request(c)
    sent = actual['request']
    assert actual['delivery'] == subject.DELIVERY == 'deepseek-public-business-flow-native-v190'
    assert actual['endpoint'] == subject.ENDPOINT == 'https://api.deepseek.com/chat/completions'
    assert {k: v for k, v in sent.items() if k != 'messages'} == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert sent['messages'][0] == {'role': 'system', 'content': subject.system_prefix(subject.STAGE)
        + subject.SCHEMA_PROMPT_PREFIX + canonical_json(c.schema)}
    assert sent['messages'][1:] == c.messages[1:]
    assert json.loads(sent['messages'][-1]['content']) == c.payload
    assert subject.request_inputs(c.messages) == {'scope_binding': c.binding}
    assert actual['body'] == raw(sent) and actual['body_bytes'] == len(actual['body'])
    assert actual['body_sha256'] == hashlib.sha256(actual['body']).hexdigest()
    assert canonical_json([c.messages, c.payload, c.plan]) == before
    assert set(c.schema['properties']) == {'schema', 'plan', 'causality'}
    assert c.schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] == c.binding['name']
    assert c.schema['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] == digest(c.binding)


def test_new_proofs_keep_all_schema_constraints_and_cannot_borrow_or_reseal_old_proofs():
    c = scene()
    proof = subject.identity_schema_proof(c.schema, subject.STAGE, scope_binding=c.binding)
    assert proof['schema'] == subject.PROOF_SCHEMA and proof['delivery'] == subject.DELIVERY
    assert proof['provider_schema'] == c.schema
    assert proof['provider_schema_hash'] == proof['local_schema_hash'] == digest(c.schema)
    assert proof['provider_json_schema_enforcement'] is False
    assert proof['schema_policy']['removed_assertions'] == []
    assert proof['schema_policy']['local_validation_required'] is True
    assert subject.check_schema_proof(proof, c.schema, subject.STAGE, scope_binding=c.binding) == proof
    shape = subject.schema_shape_proof(c.schema, subject.STAGE)
    assert shape['schema'] == subject.SHAPE_SCHEMA and shape['source_binding_verified'] is False
    with pytest.raises(ValueError): subject.check_schema_proof(shape, c.schema, subject.STAGE, scope_binding=c.binding)
    changed = copy.deepcopy(c.schema)
    changed['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] = 'f' * 64
    with pytest.raises(ValueError): subject.identity_schema_proof(changed, subject.STAGE, scope_binding=c.binding)
    changed_proof = copy.deepcopy(proof)
    changed_proof['delivery'] = old_native.DELIVERY
    changed_proof['proof_hash'] = digest({k: v for k, v in changed_proof.items() if k != 'proof_hash'})
    with pytest.raises(ValueError): subject.check_schema_proof(changed_proof, c.schema, subject.STAGE, scope_binding=c.binding)
    with pytest.raises(ValueError): old_native.schema_shape_proof(c.schema, old_native.STAGE)


def test_new_policy_and_central_options_reserve_total_generation_once_without_a_reasoning_multiplier():
    c = scene()
    assert validate_evidence_output(c.execution) is c.execution
    policy = evidence_policy(c.execution, subject.STAGE)
    assert policy == subject.input_policy(c.execution)
    assert evidence_options(c.execution, cloud_json_options(c.execution), subject.STAGE) == c.options
    actual = request(c)
    assert policy['version'] == 'cloud-business-flow-v190/' + subject.STAGE
    assert policy['max_output_tokens'] == actual['request']['max_tokens'] == 65536
    assert policy['output_reservation'] == subject.OUTPUT_RESERVATION
    assert subject.native_policy(subject.STAGE)['output_reservation'] == subject.OUTPUT_RESERVATION
    assert measure_request_body(actual['body'], policy, 65536)['token_bound'] == len(actual['body']) + 65536 + 1024
    assert subject.MAX_RESPONSE_BYTES == subject.stage_policy(subject.STAGE)['max_response_bytes'] == 1024 * 1024
    with pytest.raises(ValueError): declared_map_transport(c.execution)


@pytest.mark.parametrize('fault', ['provider', 'model', 'old_declaration', 'old_delivery', 'endpoint',
    'total_bool', 'total_overflow', 'thinking_mode', 'extra_declaration'])
def test_registration_cannot_mix_old_family_qwen_endpoint_or_output_limits(fault):
    c = scene()
    value = copy.deepcopy(c.execution)
    declaration = value['report_evidence_output']
    if fault == 'provider': value['provider'] = 'qwen'
    elif fault == 'model': value['model'] = 'deepseek-flash'
    elif fault == 'old_declaration': declaration['schema'] = old_native.OUTPUT_DECLARATION
    elif fault == 'old_delivery': declaration['reduce_transport'] = old_native.DELIVERY
    elif fault == 'endpoint': declaration['endpoint'] = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    elif fault == 'total_bool': declaration['max_tokens'] = True
    elif fault == 'total_overflow': declaration['max_tokens'] = 65537
    elif fault == 'thinking_mode': value['thinking_mode'] = 'enabled'
    else: declaration['map_transport'] = subject.DELIVERY
    with pytest.raises(ValueError): subject.input_policy(value)
    with pytest.raises(ValueError): validate_evidence_output(value)


@pytest.mark.parametrize('fault', ['old_marker', 'claim', 'span', 'policy', 'extra_parent', 'missing_policy'])
def test_payload_keeps_actual_source_binding_and_never_transmits_a_legacy_validation_view(fault):
    c = scene()
    before = canonical_json(c.messages)
    changed = copy.deepcopy(c.payload)
    if fault == 'old_marker': changed['schema'] = old_native.REQUEST_PAYLOAD_SCHEMA
    elif fault == 'claim': changed['input_claims'][0]['statement'] += 'changed'
    elif fault == 'span': changed['spans'][0][1] += 'changed'
    elif fault == 'policy': changed['platform_policy']['roles']['business_agent'] = 'override'
    elif fault == 'extra_parent': changed['parent_source'] = {}
    else: del changed['platform_policy']
    messages = copy.deepcopy(c.messages)
    messages[-1]['content'] = canonical_json(changed)
    with pytest.raises(ValueError):
        subject.prepare_request(messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)
    assert canonical_json(c.messages) == before


@pytest.mark.parametrize('fault', ['extra_option', 'disabled_thinking', 'medium_effort', 'stream', 'bool_tokens', 'json_schema'])
def test_unsupported_request_options_have_no_provider_fallback(fault):
    c = scene()
    changed = copy.deepcopy(c.options)
    if fault == 'extra_option': changed['temperature'] = 0
    elif fault == 'disabled_thinking': changed['thinking'] = {'type': 'disabled'}
    elif fault == 'medium_effort': changed['reasoning_effort'] = 'medium'
    elif fault == 'stream': changed['stream'] = True
    elif fault == 'bool_tokens': changed['max_tokens'] = True
    else: changed['response_format'] = {'type': 'json_schema'}
    with pytest.raises(ValueError):
        subject.prepare_request(c.messages, c.schema, changed, subject.STAGE, scope_binding=c.binding)


def test_complete_final_content_stays_distinct_from_thinking_and_compiles_with_real_causal_checks():
    c = scene()
    value = json.loads(response(canonical_json(c.plan)))
    value['choices'][0]['message']['reasoning_content'] = '{"schema":"not_the_final_plan"}'
    body = raw(value)
    seen = subject.inspect_response(body, 200, subject.STAGE, response_complete=True, http_content_encoding=None)
    assert seen['metadata']['envelope_valid'] is True and seen['metadata']['delivery'] == subject.DELIVERY
    assert seen['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert json.loads(seen['arguments']) == c.plan
    assert subject.parse_arguments(body, 200, subject.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=c.binding) == c.plan
    projected = wire.build_projection(raw(c.plan), **c.source_inputs)
    assert projected['causal_analysis']['availability']['declared_inputs_available'] is True
    assert projected['wire']['schema'] == 'report-business-flow-wire-v182'
    assert projected['can_apply'] is projected['semantic_quality_verified'] is False
    value['choices'][0]['message']['content'] = None
    with pytest.raises(ValueError):
        subject.parse_arguments(raw(value), 200, subject.STAGE, response_complete=True,
            http_content_encoding=None, scope_binding=c.binding)


def test_native_parse_does_not_claim_causal_availability_before_the_independent_projection_gate():
    c = scene()
    invalid = copy.deepcopy(c.plan)
    completion = next(item for item in invalid['causality']['output_contracts'] if item['action']['part'] == 'completion')
    invalid['causality']['input_bindings'][0]['sources'] = [
        {'kind': 'output', 'action': copy.deepcopy(completion['action']), 'ordinal': completion['ordinal']}]
    before = canonical_json(invalid)
    assert parse(c, invalid) == invalid  # Exact selectors/coverage, not a causal pass.
    with pytest.raises(ValueError): wire.build_projection(raw(invalid), **c.source_inputs)
    assert canonical_json(invalid) == before


@pytest.mark.parametrize('fault', ['length', 'incomplete', 'encoding', 'status', 'tools', 'oversize'])
def test_incomplete_or_nonfinal_envelopes_cannot_become_a_causal_plan(fault):
    c = scene()
    envelope = json.loads(response(canonical_json(c.plan)))
    params = {'response_complete': True, 'http_content_encoding': None}
    status = 200
    if fault == 'length': envelope['choices'][0]['finish_reason'] = 'length'
    elif fault == 'incomplete': params['response_complete'] = False
    elif fault == 'encoding': params['http_content_encoding'] = 'gzip'
    elif fault == 'status': status = 503
    elif fault == 'tools': envelope['choices'][0]['message']['tool_calls'] = [{'id': 'not_allowed'}]
    body = b'x' * (subject.MAX_RESPONSE_BYTES + 1) if fault == 'oversize' else raw(envelope)
    with pytest.raises(ValueError):
        subject.parse_arguments(body, status, subject.STAGE, scope_binding=c.binding, **params)


@pytest.mark.parametrize('stage', [None, 'business-flow-v184', 'business-flow-v185', 'business-flow-v187'])
def test_new_stage_and_old_answer_never_share_a_decoder(stage):
    c = scene()
    with pytest.raises(ValueError):
        subject.prepare_request(c.messages, c.schema, c.options, stage, scope_binding=c.binding)
    with pytest.raises(ValueError):
        subject.inspect_response(response(canonical_json(c.plan)), 200, stage,
            response_complete=True, http_content_encoding=None)
    with pytest.raises(ValueError): parse(c, c.old_plan)
    with pytest.raises(ValueError): old_native.request_inputs(c.messages)


def test_new_constructors_do_not_borrow_old_global_closures_or_change_old_request_bytes(monkeypatch):
    old = old_scene()
    before = old_native.prepare_request(old.messages, old.schema, old.options, old_native.STAGE, scope_binding=old.binding)
    c = scene()

    def forbidden(*args, **kwargs):
        pytest.fail('V190 borrowed a V187 constructor or decoder')

    with monkeypatch.context() as patch:
        for name in ('input_policy', 'dispatch_options', 'strict_options', 'identity_schema_proof',
                'schema_shape_proof', 'prepare_request', 'parse_arguments'):
            patch.setattr(old_native, name, forbidden)
        assert request(c)['delivery'] == subject.DELIVERY
        assert parse(c) == c.plan
    after = old_native.prepare_request(old.messages, old.schema, old.options, old_native.STAGE, scope_binding=old.binding)
    assert after == before
    assert profiles.MARKERS[profiles.FLOW_REASONED] == 'report-business-flow-preparation-v187'
    assert profiles.MARKERS[profiles.FLOW_CAUSAL] == 'report-business-flow-preparation-v190'
