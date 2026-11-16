"""Real V193 native/schema/selector checks over public synthetic source.

No provider, real registration, authority or semantic quality is proved. Only
pure functions run under explicit no-I/O fixtures when Root executes tests.
"""
import copy
import hashlib
import json

import pytest

from generic_agents import report_business_flow_native_v193 as subject
from generic_agents import report_business_flow_native_v190 as old_native
from generic_agents import report_business_flow_wire_v193 as wire
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents.cloud_evidence_profiles import evidence_policy, evidence_options, validate_evidence_output, declared_map_transport
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options, prepare_cloud_json
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_native_v187 import scene as old_scene
from tests.test_report_business_flow_protocol_v182 import response
from tests.test_report_business_flow_wire_v184 import no_external


def raw(value):
    return canonical_json(value).encode('utf-8')


def selector(action):
    pieces = action['path'].split('/')[4:]
    address = []
    while pieces and pieces[0] == 'phases':
        phase_index = int(pieces[1]); pieces = pieces[2:]
        branch_index = None
        if pieces and pieces[0] == 'branches':
            branch_index = int(pieces[1]); pieces = pieces[2:]
        address.append({'phase_index': phase_index, 'branch_index': branch_index})
    return {'flow_index': action['flow_index'], 'address': address, 'part': action['part'],
        'step_index': int(pieces[1]) if action['part'] in {'step', 'repeat_step'} else None}


def answer(plan, binding):
    walk = plans.walk_action_ports(plan, scope_binding=binding)
    contract = {'data_type': 'record', 'unit': None}
    outputs, inputs = [], []
    for action in walk['actions']:
        for port in action['output_ports']:
            outputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract)})
        for port in action['input_ports']:
            inputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract),
                'sources': [{'kind': 'entry', 'index': action['flow_index']}]})
    return {'schema': wire.WIRE, 'plan': copy.deepcopy(plan), 'causality': {
        'entry_ports': [{'flow_index': i, 'contract': copy.deepcopy(contract)}
            for i in range(len(plan['business']['flows']))],
        'output_contracts': outputs, 'input_bindings': inputs}}


def scene():
    c = old_scene()
    c.old_plan = copy.deepcopy(c.plan)
    c.old_plan['schema'] = plans.PLAN
    c.execution = {'provider': subject.PROVIDER, 'model': subject.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': subject.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': subject.DELIVERY, 'endpoint': subject.ENDPOINT}}
    c.cap.data['execution'] = copy.deepcopy(c.execution)
    c.payload['schema'] = subject.REQUEST_PAYLOAD_SCHEMA
    c.plan = answer(c.old_plan, c.binding)
    c.schema = wire.output_schema(c.binding)
    messages = copy.deepcopy(c.messages[1:])
    messages[-1]['content'] = json.dumps(c.payload, ensure_ascii=False, indent=2)
    c.messages, _, images = prepare_cloud_json(c.cap, messages, c.schema)
    assert images == 0
    c.options = subject.dispatch_options(c.execution, cloud_json_options(c.execution))
    return c


def request(c):
    return subject.prepare_request(c.messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)


def test_exact_nested_schema_and_complete_original_messages_are_sent_without_schema_enforcement_claim():
    c = scene(); before = canonical_json([c.messages, c.payload, c.plan])
    actual = request(c); sent = actual['request']
    assert actual['delivery'] == subject.DELIVERY == 'deepseek-public-business-flow-native-v193'
    assert actual['endpoint'] == subject.ENDPOINT == 'https://api.deepseek.com/chat/completions'
    assert {k: v for k, v in sent.items() if k != 'messages'} == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert sent['messages'][0]['content'] == subject.system_prefix(subject.STAGE) + subject.SCHEMA_PROMPT_PREFIX + canonical_json(c.schema)
    assert sent['messages'][1:] == c.messages[1:]
    assert json.loads(sent['messages'][-1]['content']) == c.payload
    assert actual['body'] == raw(sent) and actual['body_sha256'] == hashlib.sha256(actual['body']).hexdigest()
    assert actual['body_bytes'] == len(actual['body'])
    assert subject.request_inputs(c.messages) == {'scope_binding': c.binding}
    assert c.schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] == c.binding['name']
    assert c.schema['$defs']['BusinessFlowNestedPlanV192']['properties']['binding_hash']['const'] == digest(c.binding)
    proof = actual['schema_proof']
    assert proof['provider_schema'] == c.schema and proof['provider_json_schema_enforcement'] is False
    assert proof['schema_policy']['removed_assertions'] == []
    assert proof['schema_policy']['local_validation_required'] is True
    assert proof['schema_policy']['provider_support_verified'] is False
    assert proof['schema'] == subject.PROOF_SCHEMA and proof['delivery'] == subject.DELIVERY
    assert subject.check_schema_proof(proof, c.schema, subject.STAGE, scope_binding=c.binding) == proof
    assert canonical_json([c.messages, c.payload, c.plan]) == before


def test_policy_profile_and_total_output_reservation_are_independent_of_old_190():
    c = scene(); actual = request(c)
    policy = evidence_policy(c.execution, subject.STAGE)
    assert validate_evidence_output(c.execution) is c.execution
    assert policy == subject.input_policy(c.execution)
    assert evidence_options(c.execution, cloud_json_options(c.execution), subject.STAGE) == c.options
    assert policy['max_output_tokens'] == actual['request']['max_tokens'] == 65536
    assert policy['output_reservation'] == 'max_tokens_includes_reasoning_and_answer'
    assert measure_request_body(actual['body'], policy, 65536)['token_bound'] == len(actual['body']) + 65536 + 1024
    assert subject.stage_policy(subject.STAGE)['max_response_bytes'] == 1024 * 1024
    assert subject.native_policy(subject.STAGE)['provider_json_schema_enforcement'] is False
    family = profiles.modules(profiles.FLOW_NESTED)
    assert family.native is subject and family.wire is wire and profiles.is_flow(family) is True
    assert profiles.modules(profiles.FLOW_CAUSAL).native is old_native
    with pytest.raises(ValueError): declared_map_transport(c.execution)
    with pytest.raises(ValueError): old_native.schema_shape_proof(c.schema, old_native.STAGE)


@pytest.mark.parametrize('fault', ['provider', 'model', 'old_declaration', 'old_delivery', 'endpoint',
    'bool_output', 'output_overflow', 'thinking_mode', 'extra_declaration'])
def test_invalid_or_old_registration_never_selects_the_new_transport(fault):
    c = scene(); value = copy.deepcopy(c.execution); declaration = value['report_evidence_output']
    if fault == 'provider': value['provider'] = 'qwen'
    elif fault == 'model': value['model'] = 'deepseek-flash'
    elif fault == 'old_declaration': declaration['schema'] = old_native.OUTPUT_DECLARATION
    elif fault == 'old_delivery': declaration['reduce_transport'] = old_native.DELIVERY
    elif fault == 'endpoint': declaration['endpoint'] = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    elif fault == 'bool_output': declaration['max_tokens'] = True
    elif fault == 'output_overflow': declaration['max_tokens'] = 65537
    elif fault == 'thinking_mode': value['thinking_mode'] = 'enabled'
    else: declaration['map_transport'] = subject.DELIVERY
    with pytest.raises(ValueError): subject.input_policy(value)
    with pytest.raises(ValueError): validate_evidence_output(value)


@pytest.mark.parametrize('fault', ['old_marker', 'claim', 'span', 'policy', 'parent', 'missing_policy'])
def test_source_message_and_policy_cannot_be_relabelled_or_changed(fault):
    c = scene(); before = canonical_json(c.messages); payload = copy.deepcopy(c.payload)
    if fault == 'old_marker': payload['schema'] = old_native.REQUEST_PAYLOAD_SCHEMA
    elif fault == 'claim': payload['input_claims'][0]['statement'] += '改写'
    elif fault == 'span': payload['spans'][0][1] += '改写'
    elif fault == 'policy': payload['platform_policy']['roles']['business_agent'] = '改写'
    elif fault == 'parent': payload['parent_source'] = {}
    else: del payload['platform_policy']
    messages = copy.deepcopy(c.messages); messages[-1]['content'] = canonical_json(payload)
    with pytest.raises(ValueError): subject.prepare_request(messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)
    assert canonical_json(c.messages) == before


@pytest.mark.parametrize('fault', ['extra', 'disabled', 'effort', 'stream', 'bool_tokens', 'json_schema'])
def test_request_options_have_no_hidden_fallback(fault):
    c = scene(); options = copy.deepcopy(c.options)
    if fault == 'extra': options['temperature'] = 0
    elif fault == 'disabled': options['thinking'] = {'type': 'disabled'}
    elif fault == 'effort': options['reasoning_effort'] = 'medium'
    elif fault == 'stream': options['stream'] = True
    elif fault == 'bool_tokens': options['max_tokens'] = True
    else: options['response_format'] = {'type': 'json_schema'}
    with pytest.raises(ValueError): subject.prepare_request(c.messages, c.schema, options, subject.STAGE, scope_binding=c.binding)


def test_original_final_content_not_thinking_is_parsed_without_editing_or_granting_authority():
    c = scene(); envelope = json.loads(response(canonical_json(c.plan)))
    envelope['choices'][0]['message']['reasoning_content'] = '{"schema":"not_the_final_answer"}'
    body = raw(envelope)
    seen = subject.inspect_response(body, 200, subject.STAGE, response_complete=True, http_content_encoding=None)
    assert seen['metadata']['envelope_valid'] is True and seen['metadata']['delivery'] == subject.DELIVERY
    assert seen['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert json.loads(seen['arguments']) == c.plan
    assert subject.parse_arguments(body, 200, subject.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=c.binding) == c.plan
    assert set(c.plan) == {'schema', 'plan', 'causality'} and c.plan['plan']['schema'] == plans.PLAN
    changed = copy.deepcopy(c.plan); changed['schema'] = 'report-business-flow-causal-plan-v190'
    with pytest.raises(ValueError): subject.parse_arguments(response(canonical_json(changed)), 200,
        subject.STAGE, response_complete=True, http_content_encoding=None, scope_binding=c.binding)


@pytest.mark.parametrize('fault', ['length', 'incomplete', 'encoding', 'status', 'tools', 'oversize'])
def test_incomplete_nonfinal_or_wrong_transport_cannot_become_a_plan(fault):
    c = scene(); envelope = json.loads(response(canonical_json(c.plan)))
    params = {'response_complete': True, 'http_content_encoding': None}; status = 200
    if fault == 'length': envelope['choices'][0]['finish_reason'] = 'length'
    elif fault == 'incomplete': params['response_complete'] = False
    elif fault == 'encoding': params['http_content_encoding'] = 'gzip'
    elif fault == 'status': status = 503
    elif fault == 'tools': envelope['choices'][0]['message']['tool_calls'] = [{'id': 'not_allowed'}]
    body = b'x' * (subject.MAX_RESPONSE_BYTES + 1) if fault == 'oversize' else raw(envelope)
    with pytest.raises(ValueError): subject.parse_arguments(body, status, subject.STAGE, scope_binding=c.binding, **params)


@pytest.mark.parametrize('stage', [None, 'business-flow-v184', 'business-flow-v187', 'business-flow-v190'])
def test_new_stage_schema_and_proof_do_not_borrow_old_decoders(stage):
    c = scene()
    with pytest.raises(ValueError): subject.prepare_request(c.messages, c.schema, c.options, stage, scope_binding=c.binding)
    with pytest.raises(ValueError): subject.inspect_response(response(canonical_json(c.plan)), 200, stage,
        response_complete=True, http_content_encoding=None)
    proof = subject.identity_schema_proof(c.schema, subject.STAGE, scope_binding=c.binding)
    proof['delivery'] = old_native.DELIVERY
    proof['proof_hash'] = digest({key: value for key, value in proof.items() if key != 'proof_hash'})
    with pytest.raises(ValueError): subject.check_schema_proof(proof, c.schema, subject.STAGE, scope_binding=c.binding)
