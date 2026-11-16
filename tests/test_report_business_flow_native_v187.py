"""Actual pure V187 native request/response checks over public V184 data.

No new preparation, registry, provider call, capture authenticity, publication,
budget grant, semantic quality or durable user flow is claimed by this file.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import report_business_flow_native_v187 as subject
from generic_agents import report_business_flow_native_v184 as old_native
from generic_agents import report_business_flow_wire_v184 as wire
from generic_agents import report_business_flow_revision_policy_v185 as policy
from generic_agents.cloud_input_profiles import measure_request_body
from generic_agents.contracts import digest
from generic_agents.gateway import cloud_json_options, prepare_cloud_json
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_protocol_v184 import no_io, prepared as prior_prepared, answer
from tests.test_report_business_flow_protocol_v182 import response


def raw(value):
    return canonical_json(value).encode('utf-8')


def scene():
    cap, source, _, prior, _ = prior_prepared()
    execution = {'provider': subject.PROVIDER, 'model': subject.MODEL, 'max_tokens': 2048,
        'report_evidence_output': {'schema': subject.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': subject.DELIVERY, 'endpoint': subject.ENDPOINT}}
    cap.data['execution'] = execution
    binding = copy.deepcopy(prior['scope_binding'])
    payload = {'schema': subject.REQUEST_PAYLOAD_SCHEMA, 'scope_binding': binding,
        'input_claims': copy.deepcopy(prior['input_claims']),
        'spans': copy.deepcopy(prior['payload']['spans']), 'design_note': ' 原说明＋🙂完整保留。 ',
        'platform_policy': policy.platform_policy()}
    schema = wire.output_schema(binding)
    messages = [{'role': 'system', 'content': '公开合成任务：保留来源与未确定事项，不执行任何动作。'},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=2)}]
    prepared, _, images = prepare_cloud_json(cap, messages, schema)
    assert images == 0
    options = subject.dispatch_options(execution, cloud_json_options(execution))
    source_inputs = {'scope_binding': binding, 'catalog': source['catalog'],
        'allowed_span_refs': prior['allowed_span_refs'], 'context': source['context'],
        'input_claims': prior['input_claims']}
    return NS(cap=cap, execution=execution, binding=binding, payload=payload, schema=schema,
        messages=prepared, options=options, source_inputs=source_inputs, plan=answer(prior))


def request(c):
    return subject.prepare_request(c.messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)


def envelope(c):
    return response(json.dumps(c.plan, ensure_ascii=False))


def parse(c, body, **overrides):
    params = {'http_status': 200, 'response_complete': True, 'http_content_encoding': None}
    params.update(overrides)
    return subject.parse_arguments(body, params.pop('http_status'), subject.STAGE,
        scope_binding=c.binding, **params)


def test_exact_actual_body_keeps_complete_schema_in_system_prompt_and_other_messages_unmodified():
    c = scene()
    before = copy.deepcopy(c.messages)
    actual = request(c)
    body = actual['request']
    assert subject.PROVIDER == 'deepseek' and body['model'] == subject.MODEL == 'deepseek-v4-pro'
    assert actual['delivery'] == subject.DELIVERY == 'deepseek-public-business-flow-native-v187'
    assert actual['endpoint'] == subject.ENDPOINT == 'https://api.deepseek.com/chat/completions'
    assert set(body) == {'model', 'max_tokens', 'thinking', 'reasoning_effort', 'stream', 'response_format', 'messages'}
    assert body['max_tokens'] == 65536 and body['thinking'] == {'type': 'enabled'}
    assert body['reasoning_effort'] == 'high' and body['stream'] is False
    assert body['response_format'] == {'type': 'json_object'}
    assert body['messages'][0] == {'role': 'system', 'content': subject.system_prefix(subject.STAGE)
        + subject.SCHEMA_PROMPT_PREFIX + canonical_json(c.schema)}
    assert body['messages'][1:] == c.messages[1:]
    assert json.loads(body['messages'][-1]['content']) == c.payload
    assert json.loads(body['messages'][-1]['content'])['schema'] == subject.REQUEST_PAYLOAD_SCHEMA
    assert c.messages == before and 'parent_source' not in c.payload
    assert actual['body'] == raw(body) and actual['body_bytes'] == len(actual['body'])
    assert actual['body_sha256'] == hashlib.sha256(actual['body']).hexdigest()
    assert actual['body_bytes'] <= subject.MAX_INPUT_BYTES == 900000


def test_proof_explicitly_denies_provider_schema_enforcement_and_keeps_every_local_constraint():
    c = scene()
    proof = subject.identity_schema_proof(c.schema, subject.STAGE, scope_binding=c.binding)
    assert proof['provider_json_schema_enforcement'] is False
    assert proof['provider_schema'] == c.schema
    assert proof['provider_schema_hash'] == proof['local_schema_hash'] == digest(c.schema)
    assert proof['schema_policy']['transformation'] == 'none'
    assert proof['schema_policy']['removed_assertions'] == []
    assert proof['schema_policy']['local_validation_required'] is True
    assert proof['schema_policy']['provider_json_schema_enforcement'] is False
    assert subject.check_schema_proof(proof, c.schema, subject.STAGE, scope_binding=c.binding) == proof
    shape = subject.schema_shape_proof(c.schema, subject.STAGE)
    assert shape['source_binding_verified'] is False and shape['provider_schema'] == c.schema
    with pytest.raises(ValueError):
        subject.check_schema_proof(shape, c.schema, subject.STAGE, scope_binding=c.binding)
    bad = copy.deepcopy(c.schema)
    bad['additionalProperties'] = True
    with pytest.raises(ValueError):
        subject.schema_shape_proof(bad, subject.STAGE)
    changed = copy.deepcopy(proof)
    changed['provider_json_schema_enforcement'] = True
    changed['proof_hash'] = digest({key: value for key, value in changed.items() if key != 'proof_hash'})
    with pytest.raises(ValueError):
        subject.check_schema_proof(changed, c.schema, subject.STAGE, scope_binding=c.binding)


@pytest.mark.parametrize('fault', ['provider', 'model', 'embedding', 'thinking_mode', 'base_bool',
    'base_low', 'base_high', 'declaration', 'delivery', 'endpoint', 'total_bool', 'total_low',
    'total_high', 'extra_declaration'])
def test_registration_rejects_mixed_provider_old_family_and_unbounded_output(fault):
    c = scene()
    value = copy.deepcopy(c.execution)
    declared = value['report_evidence_output']
    if fault == 'provider': value['provider'] = 'qwen'
    elif fault == 'model': value['model'] = old_native.MODEL
    elif fault == 'embedding': value['task'] = 'embedding'
    elif fault == 'thinking_mode': value['thinking_mode'] = 'enabled'
    elif fault == 'base_bool': value['max_tokens'] = True
    elif fault == 'base_low': value['max_tokens'] = 127
    elif fault == 'base_high': value['max_tokens'] = 8193
    elif fault == 'declaration': declared['schema'] = old_native.OUTPUT_DECLARATION
    elif fault == 'delivery': declared['reduce_transport'] = old_native.DELIVERY
    elif fault == 'endpoint': declared['endpoint'] = old_native.ENDPOINT
    elif fault == 'total_bool': declared['max_tokens'] = True
    elif fault == 'total_low': declared['max_tokens'] = 127
    elif fault == 'total_high': declared['max_tokens'] = 65537
    else: declared['approved'] = True
    with pytest.raises(ValueError):
        subject.input_policy(value)


@pytest.mark.parametrize('fault', ['n', 'temperature', 'top_p', 'tools', 'json_schema',
    'wrong_model', 'thinking_disabled', 'reasoning_medium', 'stream', 'bool_tokens'])
def test_only_the_explicit_supported_deepseek_options_can_reach_a_request(fault):
    c = scene()
    value = copy.deepcopy(c.options)
    if fault == 'n': value['n'] = 1
    elif fault == 'temperature': value['temperature'] = 0
    elif fault == 'top_p': value['top_p'] = 1
    elif fault == 'tools': value['tools'] = []
    elif fault == 'json_schema': value['response_format'] = {'type': 'json_schema'}
    elif fault == 'wrong_model': value['model'] = old_native.MODEL
    elif fault == 'thinking_disabled': value['thinking'] = {'type': 'disabled'}
    elif fault == 'reasoning_medium': value['reasoning_effort'] = 'medium'
    elif fault == 'stream': value['stream'] = True
    else: value['max_tokens'] = True
    with pytest.raises(ValueError):
        subject.prepare_request(c.messages, c.schema, value, subject.STAGE, scope_binding=c.binding)
    base = cloud_json_options(c.execution)
    base['thinking'] = {'type': 'enabled'}
    with pytest.raises(ValueError):
        subject.dispatch_options(c.execution, base)


@pytest.mark.parametrize('fault', ['policy', 'claim', 'span', 'scope', 'missing_policy', 'old_marker', 'parent'])
def test_source_payload_and_platform_policy_remain_exactly_bound_without_transmitting_validation_view(fault):
    c = scene()
    original = copy.deepcopy(c.messages)
    assert subject.request_inputs(c.messages) == {'scope_binding': c.binding}
    assert c.messages == original
    changed = copy.deepcopy(c.payload)
    if fault == 'policy': changed['platform_policy']['roles']['business_agent'] = 'Injected role override'
    elif fault == 'claim': changed['input_claims'][0]['statement'] += 'changed'
    elif fault == 'span': changed['spans'][0][1] += 'changed'
    elif fault == 'scope': changed['scope_binding']['scope_id'] = 'different-scope'
    elif fault == 'missing_policy': del changed['platform_policy']
    elif fault == 'old_marker': changed['schema'] = old_native.REQUEST_PAYLOAD_SCHEMA
    else: changed['parent_source'] = {}
    messages = copy.deepcopy(c.messages)
    messages[-1]['content'] = json.dumps(changed, ensure_ascii=False)
    with pytest.raises(ValueError):
        subject.prepare_request(messages, c.schema, c.options, subject.STAGE, scope_binding=c.binding)
    assert c.messages == original


@pytest.mark.parametrize('stage', [None, 'business-flow-v184', 'business-flow-v185', 'reduce-scope-v181'])
def test_new_stage_and_old_native_contracts_cannot_be_interchanged(stage):
    c = scene()
    with pytest.raises(ValueError):
        subject.prepare_request(c.messages, c.schema, c.options, stage, scope_binding=c.binding)
    with pytest.raises(ValueError):
        subject.inspect_response(envelope(c), 200, stage, response_complete=True, http_content_encoding=None)
    with pytest.raises(ValueError):
        old_native.strict_options(c.schema, c.options, old_native.STAGE, scope_binding=c.binding)
    with pytest.raises(ValueError):
        old_native.request_inputs(c.messages)


def test_total_generated_completion_is_reserved_once_without_qwen_reasoning_addition():
    c = scene()
    policy_value = subject.input_policy(c.execution)
    actual = request(c)
    assert policy_value['version'] == 'cloud-business-flow-v187/' + subject.STAGE
    assert policy_value['max_output_tokens'] == actual['request']['max_tokens'] == 65536
    assert policy_value['output_reservation'] == subject.OUTPUT_RESERVATION
    assert policy_value['context_token_bound'] == 1000000 and policy_value['transport_timeout_seconds'] == 600
    assert policy_value['full_report'] is False
    assert not any(key in policy_value for key in ('reserved_reasoning_tokens', 'thinking_budget', 'max_answer_tokens'))
    measured = measure_request_body(actual['body'], policy_value, actual['request']['max_tokens'])
    assert measured['token_bound'] == len(actual['body']) + 65536 + 1024
    assert subject.stage_policy(subject.STAGE)['max_response_bytes'] == subject.MAX_RESPONSE_BYTES == 1024 * 1024
    assert subject.native_policy(subject.STAGE)['output_reservation'] == subject.OUTPUT_RESERVATION


def test_complete_response_parses_only_final_content_and_never_promotes_reasoning_content_to_a_plan():
    c = scene()
    data = json.loads(envelope(c))
    data['choices'][0]['message']['reasoning_content'] = '{"schema":"wrong_hidden_plan"}'
    actual = raw(data)
    seen = subject.inspect_response(actual, 200, subject.STAGE,
        response_complete=True, http_content_encoding=None)
    assert seen['metadata']['envelope_valid'] is True and seen['metadata']['delivery'] == subject.DELIVERY
    assert json.loads(seen['arguments']) == c.plan
    assert seen['metadata']['response_body_sha256'] == hashlib.sha256(actual).hexdigest()
    assert seen['metadata']['arguments_sha256'] == hashlib.sha256(
        data['choices'][0]['message']['content'].encode('utf-8')).hexdigest()
    assert parse(c, actual) == c.plan
    assert wire.build_projection(raw(parse(c, actual)), **c.source_inputs)['wire']['business']['name'] == c.binding['name']
    data['choices'][0]['message']['content'] = None
    data['choices'][0]['message']['reasoning_content'] = json.dumps(c.plan, ensure_ascii=False)
    with pytest.raises(ValueError):
        parse(c, raw(data))


@pytest.mark.parametrize('fault', ['length', 'empty', 'multiple', 'tools', 'function_call', 'refusal',
    'role', 'incomplete', 'encoding', 'status', 'oversize'])
def test_incomplete_unsupported_or_nonfinal_envelopes_fail_without_content_fallback(fault):
    c = scene()
    data = json.loads(envelope(c))
    message = data['choices'][0]['message']
    overrides = {}
    if fault == 'length': data['choices'][0]['finish_reason'] = 'length'
    elif fault == 'empty': message['content'] = ''
    elif fault == 'multiple': data['choices'].append(copy.deepcopy(data['choices'][0]))
    elif fault == 'tools': message['tool_calls'] = [{'id': 'forbidden-tool'}]
    elif fault == 'function_call': message['function_call'] = {'name': 'forbidden'}
    elif fault == 'refusal': message['refusal'] = '拒绝'
    elif fault == 'role': message['role'] = 'system'
    elif fault == 'incomplete': overrides['response_complete'] = False
    elif fault == 'encoding': overrides['http_content_encoding'] = 'gzip'
    elif fault == 'status': overrides['http_status'] = 400
    else: message['reasoning_content'] = 'x' * subject.MAX_RESPONSE_BYTES
    with pytest.raises(ValueError):
        parse(c, raw(data), **overrides)


def test_schema_binding_local_plan_validation_and_actual_request_capacity_cannot_be_relaxed(monkeypatch):
    c = scene()
    invalid = copy.deepcopy(c.plan)
    invalid['binding_hash'] = digest('another-binding')
    with pytest.raises(ValueError):
        parse(c, response(json.dumps(invalid, ensure_ascii=False)))
    invalid = copy.deepcopy(c.plan)
    invalid['business']['flows'][0]['completion']['outputs'] = []
    with pytest.raises(ValueError):
        parse(c, response(json.dumps(invalid, ensure_ascii=False)))
    bad_prefix = copy.deepcopy(c.messages)
    bad_schema = copy.deepcopy(c.schema)
    bad_schema['additionalProperties'] = True
    bad_prefix[0]['content'] = subject.previous.envelope._OLD_PREFIX + json.dumps(bad_schema, ensure_ascii=False)
    with pytest.raises(ValueError):
        subject.prepare_request(bad_prefix, c.schema, c.options, subject.STAGE, scope_binding=c.binding)
    actual = request(c)
    before = copy.deepcopy(c.messages)
    monkeypatch.setattr(subject, 'MAX_INPUT_BYTES', len(actual['body']))
    assert request(c)['body'] == actual['body']
    monkeypatch.setattr(subject, 'MAX_INPUT_BYTES', len(actual['body']) - 1)
    with pytest.raises(ValueError):
        request(c)
    assert c.messages == before
