"""Pure V183 preparation checks over public synthetic source/plan fixtures.

These use actual selection, request, schema proof and local wire validation.
They establish neither provider compatibility/quality nor IAM, spending,
PostgreSQL durability or a real report's business semantics.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_native_v182 as prior_native
from generic_agents import report_business_flow_preparation_v182 as prior_prep
from generic_agents import report_business_flow_native_v183 as native
from generic_agents import report_business_flow_preparation_v183 as prep
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import resolve_evidence
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_report_business_flow_protocol_v182 as protocol

NOTE = ' 原说明＋+🙂逐字保留，不代替原文。 '


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure preparation cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene():
    cap, source, plan = protocol.scene()
    cap.data['execution']['report_evidence_output'] = {
        'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
        'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source, plan


def prepared():
    cap, source, plan = scene()
    value = prep.build_preparation(cap, source, plan, 'custom:alpha', note=NOTE)
    rebuilt = prep.rebuild_request(value, cap, source, plan, 'custom:alpha', note=NOTE)
    return cap, source, plan, value, rebuilt


def reseal(value):
    value['preparation_hash'] = digest({key: item for key, item in value.items()
        if key != 'preparation_hash'})
    return value


def test_new_markers_preserve_original_task_and_explicit_local_wire_family():
    cap, source, plan, value, rebuilt = prepared()
    spec = value['generation_contract']
    assert prep.PREPARATION == value['schema'] == 'report-business-flow-preparation-v183'
    assert prep.GENERATION == spec['schema'] == 'report-business-flow-generation-v183'
    assert prep.PAYLOAD == value['payload']['schema'] == 'report-business-flow-payload-v183'
    assert prep.TASK_ID == 'report-business-flow-task-v183'
    assert prep.TASK == prior_prep.TASK and spec['task_hash'] == digest(prior_prep.TASK)
    assert prep.STAGE == spec['evidence_stage'] == native.STAGE == 'business-flow-v183'
    assert spec['delivery'] == native.DELIVERY == 'qwen-public-business-flow-native-v183'
    assert spec['wire_result_schema'] == wire.WIRE and spec['projection_schema'] == wire.PROJECTION
    assert value['scope_binding']['schema'] == wire.BINDING
    assert value['output_capacity']['schema'] == 'report-business-flow-capacity-v183'
    messages = rebuilt['request']['messages']
    assert messages[0]['content'] == native.system_prefix(native.STAGE)
    task = json.loads(messages[1]['content'])
    assert task == {'schema': prep.TASK_ID, 'task': prior_prep.TASK,
        'purpose': 'partial_business_flow_candidate_only', 'source_policy': 'untrusted_evidence'}
    assert prep.check_preparation(value, cap, source, plan, 'custom:alpha', note=NOTE) == value
    assert prep.stage_spec(value) == spec


def test_every_selected_claim_full_span_and_note_survives_actual_request_and_rebuild():
    cap, source, plan = scene(); before = canonical_json([cap.data, source, plan])
    value = prep.build_preparation(cap, source, plan, 'custom:alpha', note=NOTE)
    rebuilt = prep.rebuild_request(value, cap, source, plan, 'custom:alpha', note=NOTE)
    snapshot = source['snapshot']; binding = value['scope_binding']
    assert binding['selected_global_positions'] == [0, 2]
    selected = [snapshot['input_items'][snapshot['input_order'][i]] for i in [0, 2]]
    payload = json.loads(rebuilt['messages'][-1]['content'])
    assert payload == value['payload'] and payload['design_note'] == NOTE
    assert payload['input_claims'] == value['input_claims'] == selected
    assert payload['spans'] == [[item['span_ref'], item['text']] for item in resolve_evidence(
        source['catalog'], value['allowed_span_refs'], source['allowed_span_refs'])]
    assert binding['selected_claims_hash'] == digest(selected)
    assert binding['spans_hash'] == digest(payload['spans'])
    assert native.request_inputs(rebuilt['messages']) == {'scope_binding': binding}
    assert value['source_context'] == source and value['scope_plan'] == plan
    assert value['not_sent_global_positions'] == [1, 3]
    sent = canonical_json(rebuilt['messages'])
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in sent
    assert 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in sent
    assert 'indexed_inputs' not in payload and 'reconciliation_groups' not in payload
    assert canonical_json([cap.data, source, plan]) == before


def test_exact_body_options_proof_and_all_preparation_hashes_bind_the_new_transport():
    _, _, _, value, rebuilt = prepared(); spec = value['generation_contract']
    request = rebuilt['request']; body = rebuilt['request_body']
    assert body == canonical_json(request).encode('utf-8')
    assert rebuilt['request_hash'] == value['request_hash'] == hashlib.sha256(body).hexdigest()
    assert value['messages_hash'] == digest(request['messages'])
    assert {key: item for key, item in request.items() if key != 'messages'} == spec['dispatch_options']
    assert spec['options_hash'] == digest(spec['dispatch_options'])
    assert spec['schema_proof'] == native.identity_schema_proof(spec['output_schema'], native.STAGE,
        scope_binding=value['scope_binding'])
    assert spec['gateway_prefix_hash'] == digest(native.system_prefix(native.STAGE))
    assert value['generation_contract_hash'] == digest(spec) and value['payload_hash'] == digest(value['payload'])
    assert value['preparation_hash'] == digest({key: item for key, item in value.items()
        if key != 'preparation_hash'})
    assert value['input_summary']['prepared_request_bytes'] == len(body) <= 900000
    assert value['input_summary']['token_bound'] == len(body) + 32768 + 1024
    assert request['max_tokens'] == 32768 and request['stream'] is request['enable_thinking'] is False
    assert value['output_capacity']['max_output_tokens'] == native.MAX_OUTPUT_TOKENS == 32768


def test_actual_provider_schema_drops_only_bare_nonwhitespace_pattern_and_array_uniqueness():
    _, _, _, value, rebuilt = prepared(); spec = value['generation_contract']
    local = spec['output_schema']; before = canonical_json(local)
    expected = copy.deepcopy(local)
    def remove(node):
        if type(node) is list:
            for item in node: remove(item)
        elif type(node) is dict:
            if node.get('type') == 'string' and node.get('pattern') == r'\S': del node['pattern']
            if node.get('type') == 'array': node.pop('uniqueItems', None)
            for item in node.values(): remove(item)
    remove(expected)
    actual = rebuilt['request']['response_format']['json_schema']['schema']
    assert actual == expected == spec['schema_proof']['provider_schema']
    node = actual['$defs']['NodeV182']['properties']
    assert node['role']['minLength'] == 2 and node['role']['maxLength'] == 200
    assert 'pattern' not in node['role']
    assert node['outputs']['items']['minLength'] == 2
    assert actual['$defs']['QuoteV182']['properties']['span_ref']['pattern'] == local['$defs']['QuoteV182']['properties']['span_ref']['pattern']
    assert actual['properties']['binding_hash']['pattern'] == r'^[a-f0-9]{64}$'
    assert actual['properties']['binding_hash']['const'] == digest(value['scope_binding'])
    assert actual['$defs']['BusinessDefinitionV182']['properties']['name']['const'] == value['scope_binding']['name']
    assert canonical_json(local) == before
    assert local['$defs']['NodeV182']['properties']['role']['pattern'] == r'\S'


def test_old_preparation_and_provider_projection_remain_identical_after_new_request():
    old_cap, source, plan = protocol.scene()
    old_before = prior_prep.build_preparation(old_cap, source, plan, 'custom:alpha', note=NOTE)
    old_request = prior_prep.rebuild_request(old_before, old_cap, source, plan, 'custom:alpha', note=NOTE)
    new_cap = copy.deepcopy(old_cap)
    new_cap.data = copy.deepcopy(old_cap.data)
    new_cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    new = prep.build_preparation(new_cap, source, plan, 'custom:alpha', note=NOTE)
    new_request = prep.rebuild_request(new, new_cap, source, plan, 'custom:alpha', note=NOTE)
    assert prior_prep.build_preparation(old_cap, source, plan, 'custom:alpha', note=NOTE) == old_before
    assert prior_prep.rebuild_request(old_before, old_cap, source, plan, 'custom:alpha', note=NOTE) == old_request
    old_provider = old_request['request']['response_format']['json_schema']['schema']
    assert old_provider['$defs']['NodeV182']['properties']['role']['pattern'] == r'\S'
    assert new_request['request']['response_format']['json_schema']['schema'] != old_provider
    assert new['request_hash'] != old_before['request_hash']
    assert new['scope_binding'] == old_before['scope_binding']
    assert new['generation_contract']['output_schema'] == old_before['generation_contract']['output_schema']
    with pytest.raises(ValueError): prep.check_preparation(old_before, old_cap, source, plan, 'custom:alpha', note=NOTE)
    with pytest.raises(ValueError): prior_prep.check_preparation(new, new_cap, source, plan, 'custom:alpha', note=NOTE)


@pytest.mark.parametrize('fault', ['schema', 'generation', 'payload_family', 'claims', 'spans',
    'scope', 'note', 'source', 'plan', 'options', 'proof'])
def test_resealing_cannot_replace_cold_source_request_or_actual_new_schema(fault):
    cap, source, plan, value, _ = prepared(); changed = copy.deepcopy(value)
    if fault == 'schema': changed['schema'] = prior_prep.PREPARATION
    elif fault == 'generation': changed['generation_contract']['schema'] = prior_prep.GENERATION
    elif fault == 'payload_family': changed['payload']['schema'] = prior_native.REQUEST_PAYLOAD_SCHEMA
    elif fault == 'claims': changed['input_claims'][0]['statement'] += 'changed'
    elif fault == 'spans': changed['payload']['spans'][0][1] += 'changed'
    elif fault == 'scope': changed['scope_binding']['name'] = '另一个范围'
    elif fault == 'note': changed['payload']['design_note'] = 'changed'
    elif fault == 'source': changed['source_context']['context']['global_only_marker'] = 'changed'
    elif fault == 'plan': changed['scope_plan']['dispatch_allowed'] = True
    elif fault == 'options': changed['generation_contract']['dispatch_options']['max_tokens'] -= 1
    else: changed['generation_contract']['schema_proof']['provider_schema']['$defs']['NodeV182']['properties']['role']['minLength'] = 1
    changed['generation_contract_hash'] = digest(changed['generation_contract'])
    changed['payload_hash'] = digest(changed['payload']); reseal(changed)
    with pytest.raises(ValueError): prep.check_preparation(changed, cap, source, plan, 'custom:alpha', note=NOTE)


@pytest.mark.parametrize('note', [True, None, 0, 'x' * 4001, '\x00'])
def test_invalid_notes_do_not_create_a_preparation(note):
    cap, source, plan = scene(); before = canonical_json([cap.data, source, plan])
    with pytest.raises(ValueError): prep.build_preparation(cap, source, plan, 'custom:alpha', note=note)
    assert canonical_json([cap.data, source, plan]) == before


@pytest.mark.parametrize('fault', ['role_one', 'output_one', 'role_blank', 'quote_substring', 'graph'])
def test_transport_change_never_weakens_local_strings_full_quotes_or_graph_validation(fault):
    _, source, _, value, _ = prepared(); answer = protocol.answer(value)
    flow = answer['business']['flows'][0]
    if fault == 'role_one': flow['nodes'][0]['role'] = '人'
    elif fault == 'output_one': flow['nodes'][0]['outputs'] = ['果']
    elif fault == 'role_blank': flow['nodes'][0]['role'] = '  '
    elif fault == 'quote_substring': answer['evidence'][0]['quote'] = answer['evidence'][0]['quote'][:1]
    else: flow['edges'][0]['to_node'] = 'missing'
    body = protocol.response(json.dumps(answer, ensure_ascii=False))
    inspected = native.inspect_response(body, 200, native.STAGE, response_complete=True, http_content_encoding=None)
    assert inspected['arguments'] == json.loads(body)['choices'][0]['message']['content']
    with pytest.raises(ValueError): wire.build_projection(inspected['arguments'].encode('utf-8'),
        scope_binding=value['scope_binding'], catalog=source['catalog'],
        allowed_span_refs=value['allowed_span_refs'], context=source['context'], input_claims=value['input_claims'])


def test_complete_synthetic_answer_keeps_original_text_and_no_adoption_authority():
    _, source, _, value, _ = prepared(); answer = protocol.answer(value)
    local = value['generation_contract']['output_schema']; Draft202012Validator(local).validate(answer)
    content = json.dumps(answer, ensure_ascii=False); body = protocol.response(content)
    inspected = native.inspect_response(body, 200, native.STAGE, response_complete=True, http_content_encoding=None)
    assert inspected['arguments'] == content and inspected['metadata']['delivery'] == native.DELIVERY
    parsed = native.parse_arguments(body, 200, native.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=value['scope_binding'])
    assert parsed == answer
    projection = wire.build_projection(content.encode('utf-8'), scope_binding=value['scope_binding'],
        catalog=source['catalog'], allowed_span_refs=value['allowed_span_refs'],
        context=source['context'], input_claims=value['input_claims'])
    manual = wire.new_manual(projection)
    assert manual['acknowledge_partial_scope'] is manual['acknowledge_inferences'] is False
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    assert all(value[key] is False for key in ('aggregate_verified', 'semantic_quality_verified',
        'consumer_authorized', 'can_apply', 'can_publish'))
    assert all(projection[key] is False for key in ('aggregate_verified', 'semantic_quality_verified',
        'whole_source_coverage_verified', 'can_apply', 'can_publish', 'can_execute'))
