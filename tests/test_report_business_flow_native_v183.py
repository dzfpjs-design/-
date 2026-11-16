"""Public synthetic native compatibility, not provider/DB/quality evidence.

The actual V183 preparation, gateway serializer and V182 local graph validators
are used. Only external I/O is forbidden; no source validator is made to pass.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_native_v183 as subject
from generic_agents import report_business_flow_native_v182 as previous
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents.contracts import digest
from generic_agents.gateway import prepare_cloud_json, cloud_json_options
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_preparation_v183 import prepared
from tests.test_report_business_flow_protocol_v182 import answer, response


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Native pure tests cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, _, _, value, rebuilt = prepared()
    schema = value['generation_contract']['output_schema']
    # Reconstruct the actual gateway prefix that the native adapter checks.
    # The remaining system/user messages are the real preparation's messages.
    messages, _, _ = prepare_cloud_json(cap, rebuilt['messages'][1:], schema)
    options = subject.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution']))
    return cap, value, rebuilt, schema, messages, options


def test_new_namespace_all_the_way_through_body_proof_policy_and_envelope_without_old_constructors(monkeypatch):
    cap, value, rebuilt, schema, messages, options = scene(); b = value['scope_binding']
    before = canonical_json([cap.data, value, schema, messages, options])
    def forbidden(*args, **kwargs): raise AssertionError('V183 must not close over a V182 proof or request constructor')
    for name in ('identity_schema_proof', 'check_schema_proof', 'schema_shape_proof',
        'strict_options', 'prepare_request', 'input_policy', 'dispatch_options', 'native_policy'):
        monkeypatch.setattr(previous, name, forbidden)
    policy = subject.native_policy(subject.STAGE)
    assert policy['schema'] == subject.NATIVE_POLICY_SCHEMA and policy['delivery'] == subject.DELIVERY
    assert policy['stage'] == 'business-flow-v183' and policy['max_output_tokens'] == 32768
    assert subject.input_policy(cap.data['execution'])['version'] == 'cloud-business-flow-v183/' + subject.STAGE
    proof = subject.identity_schema_proof(schema, subject.STAGE, scope_binding=b)
    assert proof['schema'] == subject.PROOF_SCHEMA and proof['delivery'] == subject.DELIVERY
    assert proof['native_policy_hash'] == digest(policy)
    assert subject.check_schema_proof(proof, schema, subject.STAGE, scope_binding=b) == proof
    assert subject.schema_shape_proof(schema, subject.STAGE)['source_binding_verified'] is False
    actual = subject.prepare_request(messages, schema, options, subject.STAGE, scope_binding=b)
    assert actual['delivery'] == subject.DELIVERY and actual['endpoint'] == subject.ENDPOINT
    assert actual['schema_proof'] == proof
    assert actual['body'] == rebuilt['request_body'] == canonical_json(actual['request']).encode('utf-8')
    assert actual['body_sha256'] == hashlib.sha256(actual['body']).hexdigest()
    assert actual['body_bytes'] == len(actual['body']) <= 900000
    assert actual['request']['max_tokens'] == 32768 and actual['request']['response_format']['json_schema']['name'] == 'report_business_flow_v183'
    assert actual['request']['messages'][1:] == messages[1:]
    assert json.loads(actual['request']['messages'][-1]['content'])['schema'] == subject.REQUEST_PAYLOAD_SCHEMA
    assert subject.request_inputs(actual['request']['messages']) == {'scope_binding': b}
    authored = answer(value); body = response(json.dumps(authored, ensure_ascii=False))
    inspected = subject.inspect_response(body, 200, subject.STAGE, response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['delivery'] == subject.DELIVERY
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert inspected['metadata']['arguments_sha256'] == hashlib.sha256(inspected['arguments'].encode('utf-8')).hexdigest()
    assert inspected['usage'] == {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}
    assert subject.parse_arguments(body, 200, subject.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=b) == authored
    assert canonical_json([cap.data, value, schema, messages, options]) == before


@pytest.mark.parametrize('fault', ['drop_pattern', 'other_pattern', 'lower_minimum', 'loosen_ref', 'extra_property', 'arbitrary_schema'])
def test_transform_only_accepts_the_exact_complete_local_contract_before_removing_assertions(fault):
    _, value, _, schema, _, _ = scene(); changed = copy.deepcopy(schema)
    role = changed['$defs']['NodeV182']['properties']['role']
    if fault == 'drop_pattern': role.pop('pattern')
    elif fault == 'other_pattern': role['pattern'] = r'\S+'
    elif fault == 'lower_minimum': role['minLength'] = 1
    elif fault == 'loosen_ref': changed['$defs']['QuoteV182']['properties']['span_ref']['pattern'] = '.*'
    elif fault == 'extra_property': changed['properties']['unverified'] = {'type': 'boolean'}
    else: changed = {'type': 'object', 'properties': {'role': {'type': 'string', 'pattern': r'\S'}}}
    with pytest.raises(ValueError): subject._transport_schema(changed)
    with pytest.raises(ValueError): subject.identity_schema_proof(changed, subject.STAGE, scope_binding=value['scope_binding'])


def test_every_removed_assertion_is_exactly_logged_and_reversible_without_losing_lengths_refs_or_consts():
    _, value, _, schema, _, _ = scene(); before = canonical_json(schema)
    provider, policy = subject._transport_schema(schema)
    assert policy['schema'] == subject.SCHEMA_POLICY and policy['transformation'] == subject.SCHEMA_TRANSFORMATION
    assert policy['local_validation_required'] is True and policy['provider_support_verified'] is False
    removed = policy['removed_assertions']; assert removed and len({item['path'] for item in removed}) == len(removed)
    restored = copy.deepcopy(provider)
    for item in removed:
        path = [part.replace('~1', '/').replace('~0', '~') for part in item['path'].split('/')[1:]]
        original, target = schema, restored
        for part in path[:-1]:
            original = original[int(part)] if type(original) is list else original[part]
            target = target[int(part)] if type(target) is list else target[part]
        key = path[-1]
        assert (original.get('type') == 'string' and key == 'pattern' and item['value'] == r'\S') or (
            original.get('type') == 'array' and key == 'uniqueItems')
        assert key not in target and item['value'] == original[key]
        target[key] = copy.deepcopy(item['value'])
    assert canonical_json(restored) == before == canonical_json(schema)
    node = provider['$defs']['NodeV182']['properties']
    assert node['role']['minLength'] == 2 and node['role']['maxLength'] == 200
    assert node['outputs']['items']['minLength'] == 2 and node['outputs']['items']['maxLength'] == 200
    assert provider['$defs']['QuoteV182']['properties']['span_ref']['pattern'] == schema['$defs']['QuoteV182']['properties']['span_ref']['pattern']
    assert provider['properties']['binding_hash'] == schema['properties']['binding_hash']
    assert provider['$defs']['BusinessDefinitionV182']['properties']['name']['const'] == value['scope_binding']['name']


@pytest.mark.parametrize('field', ['role', 'output'])
def test_transport_nonwhitespace_omission_does_not_make_blank_local_values_valid(field):
    _, value, _, schema, _, _ = scene(); candidate = answer(value)
    if field == 'role': candidate['business']['flows'][0]['nodes'][0]['role'] = '  '
    else:
        flow = candidate['business']['flows'][0]
        flow['nodes'][1]['outputs'] = ['  ']; flow['deliverables'][0]['name'] = '  '
    provider = subject.identity_schema_proof(schema, subject.STAGE, scope_binding=value['scope_binding'])['provider_schema']
    Draft202012Validator(provider).validate(candidate)
    assert list(Draft202012Validator(schema).iter_errors(candidate))
    before = canonical_json(candidate)
    with pytest.raises(ValueError): subject.parse_arguments(response(json.dumps(candidate, ensure_ascii=False)),
        200, subject.STAGE, response_complete=True, http_content_encoding=None, scope_binding=value['scope_binding'])
    assert canonical_json(candidate) == before


@pytest.mark.parametrize('fault', ['stage', 'old_proof', 'changed_proof', 'old_payload', 'claim', 'span', 'other_binding'])
def test_old_or_mixed_namespace_and_changed_actual_payload_never_fall_back(fault):
    _, value, _, schema, messages, options = scene(); b = value['scope_binding']
    if fault == 'stage':
        with pytest.raises(ValueError): subject.identity_schema_proof(schema, previous.STAGE, scope_binding=b)
        return
    if fault in {'old_proof', 'changed_proof'}:
        proof = previous.identity_schema_proof(schema, previous.STAGE, scope_binding=b) if fault == 'old_proof' else subject.identity_schema_proof(schema, subject.STAGE, scope_binding=b)
        if fault == 'changed_proof':
            proof['delivery'] = previous.DELIVERY
            proof['proof_hash'] = digest({key: item for key, item in proof.items() if key != 'proof_hash'})
        with pytest.raises(ValueError): subject.check_schema_proof(proof, schema, subject.STAGE, scope_binding=b)
        return
    if fault == 'other_binding':
        other = copy.deepcopy(b); other['scope_id'] = 'other-scope'
        with pytest.raises(ValueError): subject.prepare_request(messages, schema, options, subject.STAGE, scope_binding=other)
        return
    payload = json.loads(messages[-1]['content'])
    if fault == 'old_payload': payload['schema'] = previous.REQUEST_PAYLOAD_SCHEMA
    elif fault == 'claim': payload['input_claims'][0]['statement'] += ' changed'
    else: payload['spans'][0][1] += ' changed'
    messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(ValueError): subject.request_inputs(messages)


@pytest.mark.parametrize('fault', ['incomplete', 'length', 'gzip', 'tools', 'duplicate_content'])
def test_new_delivery_keeps_original_eof_envelope_encoding_and_strict_local_json(fault):
    _, value, _, _, _, _ = scene(); content = json.dumps(answer(value), ensure_ascii=False)
    if fault == 'duplicate_content': content = content[:-1] + ',"schema":"' + wire.WIRE + '"}'
    body = response(content, finish='length' if fault == 'length' else 'stop',
        tool_calls=[{'id': 'public-synthetic-tool'}] if fault == 'tools' else None)
    with pytest.raises(ValueError): subject.parse_arguments(body, 200, subject.STAGE,
        response_complete=fault != 'incomplete', http_content_encoding='gzip' if fault == 'gzip' else None,
        scope_binding=value['scope_binding'])


@pytest.mark.parametrize('maximum', [32769, True, 32768.0, 0])
def test_new_output_budget_is_finite_and_strictly_typed_not_an_unlimited_compatibility_fallback(maximum):
    cap, _, _, _, _, _ = scene(); execution = copy.deepcopy(cap.data['execution'])
    execution['report_evidence_output']['max_tokens'] = maximum
    with pytest.raises(ValueError): subject.input_policy(execution)
