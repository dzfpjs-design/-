"""Real pure preparation/native/wire over explicitly public synthetic inputs.

No provider response, report ownership, DB persistence, budget or semantic
quality is proved by these authored examples. Source/plan selection, native
body/schema/envelope binding and local graph validation are actual functions.
"""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_native_v182 as native
from generic_agents import report_business_flow_preparation_v182 as prep
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents import report_scoped_design_native_v181 as old_native
from generic_agents import report_scoped_design_preparation_v181 as old_prep
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents.contracts import digest
from generic_agents.gateway import prepare_cloud_json, cloud_json_options
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded, resolve_evidence
from tests.test_report_scoped_design_v181 import scene as prior_scene, plan_for
from tests.test_report_saved_map_design_preparation_v176 import seal


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure flow protocols cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value): return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def scene():
    cap, source, plan = prior_scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 16384, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    return cap, source, plan


def prepared():
    cap, source, plan = scene()
    value = prep.build_preparation(cap, source, plan, 'custom:alpha', note=' 原说明＋+🙂不裁剪。 ')
    request = prep.rebuild_request(value, cap, source, plan, 'custom:alpha', note=' 原说明＋+🙂不裁剪。 ')
    return cap, source, plan, value, request


def answer(value):
    """Two proposed human steps, related to evidence but not semantically true."""
    b = value['scope_binding']; ref, text = value['payload']['spans'][0]
    attribution = lambda: {'citations': [ref], 'inferred': True,
        'reason': '公共合成流程建议，引用只证明关联原文；实际步骤与职责仍需人工核对。'}
    def node(identity, name, outputs):
        return {'node_id': identity, 'name': name, 'actor': 'human', 'role': '人工核查岗位',
            'responsibility': '核对原资料并保存待核说明', 'inputs': ['原资料'], 'outputs': outputs,
            'attribution': attribution()}
    return {'schema': wire.WIRE, 'binding_hash': digest(b),
        'business': {'name': b['name'], 'flows': [{'flow_id': 'review', 'name': '公开合成核对流程',
            'trigger': {'kind': 'manual', 'description': '人工明确发起核对', 'attribution': attribution()},
            'start_node': 'read', 'end_nodes': ['record'],
            'nodes': [node('read', '阅读依据', ['待核说明']), node('record', '保存意见', ['待核记录'])],
            'edges': [{'kind': 'sequence', 'from_node': 'read', 'to_node': 'record', 'attribution': attribution()}],
            'deliverables': [{'name': '待核记录', 'description': '供人工继续核对，不能当作事实或采用授权',
                'produced_by': ['record'], 'attribution': attribution()}]}], 'questions': []},
        'evidence': [{'span_ref': ref, 'quote': text}]}


def response(content, *, finish='stop', role='assistant', tool_calls=None):
    message = {'role': role, 'content': content}
    if tool_calls is not None: message['tool_calls'] = tool_calls
    return raw({'choices': [{'finish_reason': finish, 'message': message}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}})


def test_new_flow_request_preserves_every_selected_original_claim_and_text_but_not_unsent_context():
    cap, source, plan = scene(); before = codec.canonical([cap.data, source, plan])
    value = prep.build_preparation(cap, source, plan, 'custom:alpha', note=' 原说明＋+🙂不裁剪。 ')
    request = prep.rebuild_request(value, cap, source, plan, 'custom:alpha', note=' 原说明＋+🙂不裁剪。 ')
    b = value['scope_binding']; snapshot = source['snapshot']
    assert set(b) == {'schema', 'scope_id', 'name', 'plan_hash', 'source_snapshot_hash', 'source_hash',
        'catalog_hash', 'context_hash', 'global_input_count', 'global_input_claims_hash',
        'global_input_order_hash', 'selected_global_positions', 'selected_global_input_ids',
        'selected_claims_hash', 'allowed_span_refs_hash', 'spans_hash'}
    assert b['schema'] == wire.BINDING and b['selected_global_positions'] == [0, 2]
    assert b['selected_global_input_ids'] == [snapshot['input_order'][i] for i in [0, 2]]
    selected = [snapshot['input_items'][key] for key in b['selected_global_input_ids']]
    assert value['input_claims'] == value['payload']['input_claims'] == selected
    assert b['selected_claims_hash'] == digest(selected)
    assert set(value['payload']) == {'schema', 'scope_binding', 'input_claims', 'spans', 'design_note'}
    assert value['payload']['design_note'] == ' 原说明＋+🙂不裁剪。 '
    assert value['payload']['spans'] == [[item['span_ref'], item['text']] for item in resolve_evidence(
        source['catalog'], value['allowed_span_refs'], source['allowed_span_refs'])]
    encoded = codec.canonical(value['payload'])
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in encoded and 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in encoded
    assert 'indexed_inputs' not in value['payload'] and 'input_count' not in value['payload']
    assert value['not_sent_global_positions'] == [1, 3] and value['source_context'] == source and value['scope_plan'] == plan
    assert request['request_body'] == codec.canonical(request['request']).encode('utf-8')
    assert value['request_hash'] == hashlib.sha256(request['request_body']).hexdigest()
    assert value['input_summary']['prepared_request_bytes'] == len(request['request_body']) <= 900000
    assert value['input_summary']['token_bound'] == len(request['request_body']) + 16384 + 1024
    assert native.request_inputs(request['messages']) == {'scope_binding': b}
    assert prep.stage_spec(value) == value['generation_contract'] and native.input_policy(cap.data['execution'])['full_report'] is False
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    assert all(value[key] is False for key in ('aggregate_verified', 'semantic_quality_verified', 'consumer_authorized', 'can_apply', 'can_publish'))
    assert codec.canonical([cap.data, source, plan]) == before


def test_new_wire_proof_and_native_http_keep_graph_distinct_from_old_candidate_and_coverage_families():
    _, _, _, value, _ = prepared(); b = value['scope_binding']; schema = value['generation_contract']['output_schema']
    assert set(schema['properties']) == {'schema', 'binding_hash', 'business', 'evidence'}
    assert schema['properties']['binding_hash']['const'] == digest(b)
    assert schema['$defs']['BusinessDefinitionV182']['properties']['name']['const'] == b['name']
    proof = native.identity_schema_proof(schema, native.STAGE, scope_binding=b)
    assert proof == native.check_schema_proof(proof, schema, native.STAGE, scope_binding=b)
    assert proof['scope_binding_hash'] == digest(b) and proof['schema_policy']['provider_support_verified'] is False
    assert proof['schema_policy']['local_validation_required'] is True
    assert native.schema_shape_proof(schema, native.STAGE)['source_binding_verified'] is False
    authored = answer(value); Draft202012Validator(schema).validate(authored)
    body = response(json.dumps(authored, ensure_ascii=False))
    inspected = native.inspect_response(body, 200, native.STAGE, response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['delivery'] == native.DELIVERY
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert inspected['metadata']['arguments_sha256'] == hashlib.sha256(inspected['arguments'].encode('utf-8')).hexdigest()
    assert inspected['metadata']['arguments_sha256'] != inspected['metadata']['response_body_sha256']
    assert inspected['usage'] == {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}
    parsed = native.parse_arguments(body, 200, native.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=b)
    assert parsed == authored and len(parsed['business']['flows'][0]['nodes']) == 2
    assert 'result' not in parsed and 'objects' not in parsed and 'reconciliation_groups' not in parsed
    old_answer = {'scope_id': b['scope_id'], 'plan_hash': b['plan_hash'], 'result': {},
        'input_count': 2, 'reconciliation_groups': []}
    with pytest.raises(ValueError): native.parse_arguments(response(json.dumps(old_answer)), 200, native.STAGE,
        response_complete=True, http_content_encoding=None, scope_binding=b)


def test_provider_schema_transformation_removes_only_actual_array_uniqueness_and_preserves_local_input():
    local = {'type': 'object', 'properties': {'uniqueItems': {'type': 'string'},
        'items': {'type': 'array', 'uniqueItems': True, 'minItems': 2, 'maxItems': 5,
            'items': {'type': 'integer', 'minimum': 1}}}, 'additionalProperties': False}
    before = codec.canonical(local); provider, policy = native._transport_schema(local)
    assert policy['removed_assertions'] == [{'path': '/properties/items/uniqueItems', 'value': True}]
    provider['properties']['items']['uniqueItems'] = True
    assert codec.canonical(provider) == before == codec.canonical(local)


@pytest.mark.parametrize('fault', ['binding', 'claims', 'spans', 'local_scope', 'global_source', 'plan', 'note', 'old_schema'])
def test_resealed_preparation_is_not_a_substitute_for_actual_source_selection(fault):
    cap, source, plan, value, _ = prepared(); altered = copy.deepcopy(value)
    if fault == 'binding': altered['scope_binding']['selected_global_positions'] = [0, 1]
    elif fault == 'claims': altered['input_claims'][0]['statement'] += ' changed'
    elif fault == 'spans': altered['payload']['spans'][0][1] += ' changed'
    elif fault == 'local_scope': altered['scope_binding']['name'] = '另一个业务'
    elif fault == 'global_source': altered['source_context']['context']['global_only_marker'] = 'changed'
    elif fault == 'plan': altered['scope_plan']['dispatch_allowed'] = True
    elif fault == 'note': altered['payload']['design_note'] = 'different note'
    else: altered['schema'] = old_prep.PREPARATION
    altered['preparation_hash'] = digest({key: item for key, item in altered.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError): prep.check_preparation(altered, cap, source, plan, 'custom:alpha', note=' 原说明＋+🙂不裁剪。 ')


@pytest.mark.parametrize('fault', ['claim_statement', 'claim_extra', 'claim_refs', 'empty_claims', 'spans_text',
    'spans_missing', 'spans_duplicate', 'scope_id', 'bool_position', 'extra_payload', 'old_payload', 'duplicate_json', 'nonfinite'])
def test_actual_payload_cannot_borrow_claims_or_text_from_another_binding(fault):
    cap, _, _, value, request = prepared(); messages = copy.deepcopy(request['messages'])
    payload = json.loads(messages[-1]['content'])
    if fault == 'claim_statement': payload['input_claims'][0]['statement'] += ' changed'
    elif fault == 'claim_extra': payload['input_claims'][0]['trusted'] = True
    elif fault == 'claim_refs': payload['input_claims'][0]['span_refs'] = ['w:p1/s999']
    elif fault == 'empty_claims': payload['input_claims'] = []
    elif fault == 'spans_text': payload['spans'][0][1] += ' changed'
    elif fault == 'spans_missing': payload['spans'].pop()
    elif fault == 'spans_duplicate': payload['spans'].append(copy.deepcopy(payload['spans'][0]))
    elif fault == 'scope_id': payload['scope_binding']['scope_id'] = 'another-scope'
    elif fault == 'bool_position': payload['scope_binding']['selected_global_positions'][0] = False
    elif fault == 'extra_payload': payload['context'] = {}
    elif fault == 'old_payload': payload['schema'] = old_native.REQUEST_PAYLOAD_SCHEMA
    # An internally consistent invented scope is not itself source authority;
    # compare against the actual caller-held binding in prepare_request too.
    if fault == 'scope_id':
        schema = value['generation_contract']['output_schema']
        messages, _, _ = prepare_cloud_json(cap, [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], schema)
        with pytest.raises(ValueError): native.prepare_request(messages, schema,
            {key: request['request'][key] for key in ('model', 'max_tokens', 'enable_thinking', 'stream', 'n')},
            native.STAGE, scope_binding=value['scope_binding'])
        return
    messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    if fault == 'duplicate_json': messages[-1]['content'] = messages[-1]['content'][:-1] + ',"design_note":"other"}'
    elif fault == 'nonfinite': messages[-1]['content'] = messages[-1]['content'][:-1] + ',"hidden":NaN}'
    with pytest.raises(ValueError): native.request_inputs(messages)


@pytest.mark.parametrize('fault', ['stage', 'provider', 'model', 'endpoint', 'legacy_decl', 'output_ceiling',
    'output_bool', 'base_ceiling', 'base_bool', 'thinking'])
def test_new_registration_keeps_fixed_provider_endpoint_and_independent_stage_limits(fault):
    cap, _, _ = scene(); execution = copy.deepcopy(cap.data['execution'])
    if fault == 'stage':
        with pytest.raises(ValueError): native.stage_policy(old_native.STAGE)
        return
    if fault == 'provider': execution['provider'] = 'other'
    elif fault == 'model': execution['model'] = 'other'
    elif fault == 'endpoint': execution['report_evidence_output']['endpoint'] += '/other'
    elif fault == 'legacy_decl': execution['report_evidence_output'].update(schema=old_native.OUTPUT_DECLARATION, reduce_transport=old_native.DELIVERY)
    elif fault == 'output_ceiling': execution['report_evidence_output']['max_tokens'] = 16385
    elif fault == 'output_bool': execution['report_evidence_output']['max_tokens'] = True
    elif fault == 'base_ceiling': execution['max_tokens'] = 8193
    elif fault == 'base_bool': execution['max_tokens'] = True
    else: execution['thinking_mode'] = 'disabled'
    with pytest.raises(ValueError): native.input_policy(execution)


@pytest.mark.parametrize('fault', ['schema_name', 'schema_extra', 'proof_changed', 'options_stream',
    'options_n_bool', 'options_tokens', 'wrong_prefix', 'wrong_embedded_schema'])
def test_native_exact_schema_options_and_checked_gateway_prefix_do_not_fall_back(fault):
    cap, _, _, value, _ = prepared(); b = value['scope_binding']; schema = copy.deepcopy(value['generation_contract']['output_schema'])
    options = native.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution']))
    if fault == 'schema_name': schema['$defs']['BusinessDefinitionV182']['properties']['name']['const'] = '另一业务'
    elif fault == 'schema_extra': schema['properties']['objects'] = {'type': 'array'}
    elif fault == 'proof_changed':
        proof = native.identity_schema_proof(schema, native.STAGE, scope_binding=b); proof['plan_hash'] = 'f' * 64
        with pytest.raises(ValueError): native.check_schema_proof(proof, schema, native.STAGE, scope_binding=b)
        return
    elif fault == 'options_stream': options['stream'] = True
    elif fault == 'options_n_bool': options['n'] = True
    elif fault == 'options_tokens': options['max_tokens'] = 32768
    else:
        messages, _, _ = prepare_cloud_json(cap, [{'role': 'user', 'content': json.dumps(value['payload'], ensure_ascii=False)}], schema)
        if fault == 'wrong_prefix': messages[0]['content'] = native.system_prefix(native.STAGE)
        else: messages[0]['content'] += ' '
        if fault == 'wrong_embedded_schema':
            # Whitespace alone is valid JSON, so make the embedded schema
            # actually different rather than lowering the acceptance bar.
            bad = copy.deepcopy(schema); bad['additionalProperties'] = True
            messages[0]['content'] = messages[0]['content'][:messages[0]['content'].index('{')] + json.dumps(bad)
        with pytest.raises(ValueError): native.prepare_request(messages, schema, options, native.STAGE, scope_binding=b)
        return
    with pytest.raises(ValueError): native.strict_options(schema, options, native.STAGE, scope_binding=b)


@pytest.mark.parametrize('fault', ['length', 'tools', 'wrong_role', 'incomplete', 'gzip', 'status', 'duplicate_content'])
def test_original_http_completion_encoding_and_unique_assistant_content_are_required(fault):
    _, _, _, value, _ = prepared(); authored = answer(value); content = json.dumps(authored, ensure_ascii=False)
    finish = 'length' if fault == 'length' else 'stop'
    if fault == 'duplicate_content': content = content[:-1] + ',"schema":"' + wire.WIRE + '"}'
    body = response(content, finish=finish, role='user' if fault == 'wrong_role' else 'assistant',
        tool_calls=[{'id': 'synthetic-tool'}] if fault == 'tools' else None)
    with pytest.raises(ValueError): native.parse_arguments(body, 400 if fault == 'status' else 200, native.STAGE,
        response_complete=fault != 'incomplete', http_content_encoding='gzip' if fault == 'gzip' else None,
        scope_binding=value['scope_binding'])


def test_empty_original_refs_are_retained_without_repair_and_complete_http_capacity_is_measured(monkeypatch):
    cap, source, _ = scene(); snapshot = source['snapshot']; key = snapshot['input_order'][0]
    snapshot['input_items'][key].update(span_refs=[], status='unknown', explanation='未定位原文，待人工核对。')
    snapshot['input_items_hash'] = digest(snapshot['input_items'])
    snapshot['input_claims_hash'] = digest([snapshot['input_items'][item] for item in snapshot['input_order']]); seal(snapshot)
    plan = plan_for(source); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    assert value['payload']['input_claims'][0]['span_refs'] == []
    assert value['payload']['input_claims'][0]['status'] == 'unknown'
    assert native.request_inputs(prep.rebuild_request(value, cap, source, plan, 'custom:alpha')['messages']) == {'scope_binding': value['scope_binding']}
    before = codec.canonical([source, plan])
    monkeypatch.setattr(prep, 'MAX_REQUEST_BYTES', value['input_summary']['prepared_request_bytes'] - 1)
    with pytest.raises(EvidenceCapacityExceeded): prep.build_preparation(cap, source, plan, 'custom:alpha')
    assert codec.canonical([source, plan]) == before


def test_scope_with_no_citable_original_text_is_blocked_without_borrowing_unsent_scope_evidence():
    cap, source, _ = scene(); snapshot = source['snapshot']
    for position in (0, 2):
        snapshot['input_items'][snapshot['input_order'][position]].update(
            span_refs=[], status='unknown', explanation='本项未定位原文，待补充资料或人工核对。')
    snapshot['input_items_hash'] = digest(snapshot['input_items'])
    snapshot['input_claims_hash'] = digest([snapshot['input_items'][item] for item in snapshot['input_order']]); seal(snapshot)
    plan = plan_for(source); before = codec.canonical([cap.data, source, plan])
    assert source['allowed_span_refs'] and old_prep._selected(source, plan, 'custom:alpha')[3] == []
    with pytest.raises(ValueError, match='没有可引用的原文'):
        prep.build_preparation(cap, source, plan, 'custom:alpha')
    assert codec.canonical([cap.data, source, plan]) == before
