"""Public synthetic protocol/source boundaries, no provider or persistence grant."""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_scoped_design_preparation_v181 as prep
from generic_agents import report_scoped_design_native_v181 as native
from generic_agents import report_scoped_design_wire_v181 as wire
from generic_agents import report_scoped_design_plan_v180 as planner
from generic_agents import report_indexed_inputs_v178 as codec
from generic_agents import report_design_grouped_native_v179 as old_native
from generic_agents.contracts import digest
from generic_agents.gateway import prepare_cloud_json, cloud_json_options
from generic_agents.report_evidence_plan import resolve_evidence
from tests.test_report_saved_map_design_preparation_v176 import source_scene, seal, identity

@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure scope protocols cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)

def raw(value): return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')

def plan_for(source):
    snapshot = source['snapshot']; claims = [copy.deepcopy(snapshot['input_items'][key]) for key in snapshot['input_order']]
    indexed = codec.build_indexed_inputs(claims, snapshot['snapshot_hash'])
    base = {'schema': planner.LEDGER, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'indexed_inputs_hash': indexed['inputs_hash'], 'input_claims_hash': indexed['input_claims_hash'],
        'input_count': len(claims), 'scopes': [
            {'scope_id': 'custom:alpha', 'name': '业务范围甲', 'claim_positions': [0, 2], 'context_positions': []},
            {'scope_id': 'custom:beta', 'name': '业务范围乙', 'claim_positions': [1, 3], 'context_positions': []}],
        'shared': [], 'background': []}
    ledger = {**base, 'confirmation': {'schema': planner.CONFIRMATION, 'status': 'confirmed',
        'request_id': identity(701), 'actor_id': snapshot['actor_id'], 'confirmed_at': '2026-10-23T12:00:00+00:00',
        'ledger_hash': digest(base)}}
    return planner.build_plan(indexed, snapshot['snapshot_hash'], ledger, input_claims=claims)

def scene():
    cap, source = source_scene()
    cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    cap.data['execution']['secret_ref'] = 'DASHSCOPE_API_KEY'
    source['context']['global_only_marker'] = 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL'
    source['snapshot']['context'] = copy.deepcopy(source['context'])
    source['snapshot']['context_hash'] = digest(source['context'])
    source['snapshot']['user_instruction'] = 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL'
    source['snapshot']['user_instruction_hash'] = digest(source['snapshot']['user_instruction'])
    seal(source['snapshot'])
    return cap, source, plan_for(source)

def arguments(value, source):
    snapshot = source['snapshot']
    full = [snapshot['input_items'][key] for key in snapshot['input_order']]
    return {'indexed': value['indexed_inputs'], 'input_claims': [copy.deepcopy(full[position])
        for position in value['selected_global_positions']], 'source_snapshot_hash': value['source_snapshot_hash'],
        'scope_binding': value['scope_binding']}

def response(value, source):
    args = arguments(value, source); b = args['scope_binding']; outputs, groups, questions = [], [], []
    for i, claim in enumerate(args['input_claims']):
        if claim['status'] == 'unknown':
            target, index, disposition = 'questions', len(questions), 'uncertain'
            questions.append('请人工核对未定位的本范围原声明。')
        else:
            target, index, disposition = 'global_claims', len(outputs), 'kept'; outputs.append(copy.deepcopy(claim))
        groups.append({'output_kind': target, 'output_index': index, 'disposition': disposition,
            'input_positions': [i], 'reason': '保留本范围原声明及原引用；不证明语义质量。'})
    step = {'name': '核对并记录', 'purpose': '形成待核记录', 'agent': '人工确认', 'role': '待明确负责人',
        'output': '待核结论', 'next_index': None, 'branch_condition': '', 'else_index': None,
        'citations': [], 'inference_reason': '合成待核流程，不证明原报告已定义此步骤。'}
    business = {'operation': 'upsert', 'original_name': '', 'name': b['scope_name'], 'trigger': '人工明确发起',
        'output': '待核范围结果', 'steps': [step], 'citations': [],
        'inference_reason': '合成单步流程，用于协议验证；真实流程仍须原文和人工核对。',
        'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}
    return {'scope_id': b['scope_id'], 'plan_hash': b['plan_hash'], 'input_count': len(args['input_claims']),
        'result': {'schema': 'report-object-semantic-candidate-wire-v166', 'source_hash': b['source_hash'],
            'catalog_hash': b['catalog_hash'], 'domain': '本范围待核候选', 'global_claims': outputs,
            'objects': [], 'relationships': [], 'businesses': [business], 'questions': questions},
        'reconciliation_groups': groups}

def project(value, source, answer):
    return wire.build_reduction_projection(raw(answer), catalog=source['catalog'],
        allowed_span_refs=value['allowed_span_refs'], context=source['context'], **arguments(value, source))

def envelope(answer, finish='stop'):
    return raw({'choices': [{'finish_reason': finish, 'message': {'role': 'assistant',
        'content': json.dumps(answer, ensure_ascii=False)}}], 'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}})

def test_real_preparation_only_sends_selected_full_claims_and_resolved_span_text():
    cap, source, plan = scene(); before = codec.canonical([cap.data, source, plan])
    value = prep.build_preparation(cap, source, plan, 'custom:alpha', note=' 原始说明保留。 ')
    snapshot = source['snapshot']; args = arguments(value, source)
    assert value['selected_global_positions'] == [0, 2] and value['not_sent_global_positions'] == [1, 3]
    assert value['scope_binding']['local_to_global'] == [0, 2]
    assert value['scope_binding']['global_input_ids'] == [snapshot['input_order'][0], snapshot['input_order'][2]]
    assert value['scope_binding']['global_input_count'] == 4 and len(value['input_claims']) == 2
    assert [entry['claim'] for entry in value['payload']['indexed_inputs']['input_claims']] == args['input_claims']
    expected_spans = [[item['span_ref'], item['text']] for item in resolve_evidence(source['catalog'],
        value['allowed_span_refs'], source['allowed_span_refs'])]
    assert value['payload']['spans'] == expected_spans
    encoded = codec.canonical(value['payload'])
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in encoded and 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in encoded
    assert 'context' not in value['payload'] and 'user_instruction' not in value['payload']
    assert value['source_context'] == source and value['scope_plan'] == plan
    rebuilt = prep.rebuild_request(value, cap, source, plan, 'custom:alpha', note=' 原始说明保留。 ')
    assert rebuilt['request_body'] == codec.canonical(rebuilt['request']).encode('utf-8')
    assert value['request_hash'] == hashlib.sha256(rebuilt['request_body']).hexdigest()
    assert value['input_summary']['prepared_request_bytes'] == len(rebuilt['request_body'])
    assert value['input_summary']['token_bound'] == len(rebuilt['request_body']) + 32768 + 1024
    assert native.request_inputs(rebuilt['messages']) == args
    assert prep.stage_spec(value) == value['generation_contract']
    assert value['partial_scope'] is True and value['aggregate_verified'] is False and value['can_apply'] is False
    assert codec.canonical([cap.data, source, plan]) == before

def test_complete_v166_one_business_and_source_bound_dynamic_native_schema():
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    spec = value['generation_contract']; schema = spec['output_schema']; args = arguments(value, source)
    assert set(schema['properties']) == {'scope_id', 'plan_hash', 'result', 'input_count', 'reconciliation_groups'}
    assert schema['properties']['input_count']['const'] == 2 and schema['properties']['scope_id']['const'] == 'custom:alpha'
    assert schema['$defs']['CandidateObjects']['properties']['businesses']['minItems'] == 1
    assert schema['$defs']['CandidateObjects']['properties']['businesses']['maxItems'] == 1
    assert schema['$defs']['BusinessWireV5']['properties']['steps']['maxItems'] == 16
    assert schema['$defs']['ObjectSemanticsV166']['properties']['fields']['maxItems'] == 40
    assert schema['$defs']['BusinessWireV5']['properties']['name']['const'] == '业务范围甲'
    proof = native.identity_schema_proof(schema, native.STAGE, **args)
    assert proof['scope_binding_hash'] == digest(value['scope_binding'])
    assert proof['schema_policy']['local_validation_required'] is True
    assert proof['schema_policy']['provider_support_verified'] is False
    assert proof['local_schema_hash'] != proof['provider_schema_hash']
    provider = proof['provider_schema']
    assert 'uniqueItems' not in provider['$defs']['ReconciliationGroupV181']['properties']['input_positions']
    restored = copy.deepcopy(provider)
    restored['$defs']['ReconciliationGroupV181']['properties']['input_positions']['uniqueItems'] = True
    assert codec.canonical(restored) == codec.canonical(schema)
    assert native.schema_shape_proof(schema, native.STAGE)['source_binding_verified'] is False

def test_actual_quality_projection_maps_sparse_original_positions_without_rewriting_answer():
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    answer = response(value, source); original = raw(answer); result = project(value, source, answer)
    assert result['wire'] == answer and result['wire_bytes_sha256'] == hashlib.sha256(original).hexdigest()
    assert [item['input_position'] for item in result['expanded_local_reconciliation']] == [0, 1]
    assert [item['global_input_position'] for item in result['mapped_global_reconciliation']] == [0, 2]
    assert [item['global_input_id'] for item in result['mapped_global_reconciliation']] == value['scope_binding']['global_input_ids']
    assert len(result['result']['businesses']) == 1 and len(result['result']['businesses'][0]['steps']) == 1
    assert result['partial_scope'] is True and result['aggregate_verified'] is False
    assert all(result[key] is False for key in ('whole_source_coverage_verified', 'source_anchors_verified',
        'provider_delivery_verified', 'semantic_quality_verified', 'can_apply', 'can_publish'))
    assert wire.check_reduction_projection(result, original, catalog=source['catalog'],
        allowed_span_refs=value['allowed_span_refs'], context=source['context'], **arguments(value, source)) == result

@pytest.mark.parametrize('fault', ['scope', 'plan', 'count', 'missing', 'duplicate', 'global_position',
    'bool_position', 'two_businesses', 'no_business', 'wrong_name', 'source_hash', 'old_wire'])
def test_raw_partial_answer_never_borrows_global_or_other_scope_coverage(fault):
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha'); answer = response(value, source)
    if fault == 'scope': answer['scope_id'] = 'custom:beta'
    elif fault == 'plan': answer['plan_hash'] = 'f' * 64
    elif fault == 'count': answer['input_count'] = 4
    elif fault == 'missing': answer['reconciliation_groups'].pop()
    elif fault == 'duplicate': answer['reconciliation_groups'][1]['input_positions'] = [0]
    elif fault == 'global_position': answer['reconciliation_groups'][1]['input_positions'] = [2]
    elif fault == 'bool_position': answer['reconciliation_groups'][0]['input_positions'] = [False]
    elif fault == 'two_businesses': answer['result']['businesses'].append(copy.deepcopy(answer['result']['businesses'][0]))
    elif fault == 'no_business': answer['result']['businesses'] = []
    elif fault == 'wrong_name': answer['result']['businesses'][0]['name'] = '其他业务'
    elif fault == 'source_hash': answer['result']['source_hash'] = 'f' * 64
    else: answer.pop('scope_id'); answer.pop('plan_hash')
    before = codec.canonical(answer)
    with pytest.raises(ValueError): project(value, source, answer)
    assert codec.canonical(answer) == before

@pytest.mark.parametrize('fault', ['local_map', 'scope_name', 'global_order', 'spans', 'claims', 'full_source', 'plan', 'note'])
def test_resealed_preparation_cannot_replace_actual_cold_source_plan_or_scope(fault):
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    altered = copy.deepcopy(value)
    if fault == 'local_map': altered['scope_binding']['local_to_global'] = [0, 1]
    elif fault == 'scope_name': altered['scope_binding']['scope_name'] = '其他业务'
    elif fault == 'global_order': altered['scope_binding']['global_input_ids'].reverse()
    elif fault == 'spans': altered['payload']['spans'][0][1] += '被修改原文'
    elif fault == 'claims': altered['input_claims'][0]['statement'] += 'changed'
    elif fault == 'full_source': altered['source_context']['context']['global_only_marker'] = 'changed'
    elif fault == 'plan': altered['scope_plan']['dispatch_allowed'] = True
    else: altered['payload']['design_note'] = '未经核对的新说明'
    altered['preparation_hash'] = digest({key: item for key, item in altered.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError): prep.check_preparation(altered, cap, source, plan, 'custom:alpha')

def test_native_original_envelope_and_request_binding_are_independent_from_prior_families():
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha'); args = arguments(value, source)
    answer = response(value, source); body = envelope(answer)
    inspected = native.inspect_response(body, 200, native.STAGE, response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['response_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert native.parse_arguments(body, 200, native.STAGE, response_complete=True, http_content_encoding=None, **args) == answer
    for finish in ('length', 'tool_calls'):
        with pytest.raises(ValueError): native.parse_arguments(envelope(answer, finish), 200, native.STAGE,
            response_complete=True, http_content_encoding=None, **args)
    for stage in ('reduce-v179', 'reduce-v178', 'reduce-v176', None):
        with pytest.raises(ValueError): native.stage_policy(stage)
    bad = copy.deepcopy(cap.data['execution']); bad['report_evidence_output'].update(
        schema=old_native.OUTPUT_DECLARATION, reduce_transport=old_native.DELIVERY)
    with pytest.raises(ValueError): native.input_policy(bad)
    rebuilt = prep.rebuild_request(value, cap, source, plan, 'custom:alpha')
    messages = copy.deepcopy(rebuilt['messages']); payload = json.loads(messages[-1]['content'])
    payload['spans'][0][1] += 'fake quote'; messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(ValueError): native.request_inputs(messages)

def test_global_background_is_not_automatically_sent_and_empty_unknown_refs_are_not_repaired():
    cap, source, _ = scene(); snapshot = source['snapshot']; key = snapshot['input_order'][0]
    snapshot['input_items'][key].update(span_refs=[], status='unknown', explanation='未定位原文，仍需核对。')
    snapshot['input_items_hash'] = digest(snapshot['input_items'])
    full = [snapshot['input_items'][item] for item in snapshot['input_order']]
    snapshot['input_claims_hash'] = digest(full); seal(snapshot)
    plan = plan_for(source); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    assert value['scope_binding']['unanchored_input_positions'] == [0]
    assert value['payload']['indexed_inputs']['input_claims'][0]['claim']['span_refs'] == []
    assert value['payload']['spans'] == [[item['span_ref'], item['text']] for item in resolve_evidence(
        source['catalog'], value['allowed_span_refs'], source['allowed_span_refs'])]
    answer = response(value, source); result = project(value, source, answer)
    assert result['unanchored_input_positions'] == [0] and result['source_anchors_verified'] is False
    assert result['result']['questions']

def test_actual_request_and_response_limits_remain_hard_without_truncation():
    cap, source, plan = scene(); value = prep.build_preparation(cap, source, plan, 'custom:alpha')
    spec = value['generation_contract']; args = arguments(value, source)
    assert native.stage_policy(native.STAGE)['max_response_bytes'] == wire.MAX_WIRE_BYTES == 1024 * 1024
    inspected = native.inspect_response(b'x' * (1024 * 1024 + 1), 200, native.STAGE,
        response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['envelope_valid'] is False
    assert 'response_body_capacity' in inspected['metadata']['envelope_errors']
    rebuilt = prep.rebuild_request(value, cap, source, plan, 'custom:alpha')
    narrow_messages = copy.deepcopy(rebuilt['messages'][1:])
    payload = json.loads(narrow_messages[-1]['content']); payload['design_note'] = 'x' * 900000
    narrow_messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    prepared, _, _ = prepare_cloud_json(cap, narrow_messages, spec['output_schema'])
    before = copy.deepcopy(prepared)
    with pytest.raises(ValueError): native.prepare_request(prepared, spec['output_schema'],
        native.dispatch_options(cap.data['execution'], cloud_json_options(cap.data['execution'])), native.STAGE, **args)
    assert prepared == before and native.MAX_INPUT_BYTES == 900000

def test_only_explicit_shared_recipients_and_selected_background_context_are_sent():
    cap, source, original_plan = scene(); snapshot = source['snapshot']
    claims = [snapshot['input_items'][key] for key in snapshot['input_order']]
    base = {key: copy.deepcopy(item) for key, item in original_plan['scope_ledger'].items()
        if key != 'confirmation'}
    base.update(scopes=[
        {'scope_id': 'custom:alpha', 'name': '业务范围甲', 'claim_positions': [0], 'context_positions': [3]},
        {'scope_id': 'custom:beta', 'name': '业务范围乙', 'claim_positions': [1], 'context_positions': []}],
        shared=[{'input_position': 2, 'scope_ids': ['custom:alpha', 'custom:beta'],
            'reason': '两个范围明确共享该完整声明。'}],
        background=[{'input_position': 3, 'reason': '全局保留，只有甲明确选择为背景上下文。'}])
    ledger = {**base, 'confirmation': {**copy.deepcopy(original_plan['scope_ledger']['confirmation']),
        'ledger_hash': digest(base)}}
    plan = planner.build_plan(original_plan['indexed_inputs'], snapshot['snapshot_hash'], ledger,
        input_claims=claims)
    alpha = prep.build_preparation(cap, source, plan, 'custom:alpha')
    beta = prep.build_preparation(cap, source, plan, 'custom:beta')
    assert alpha['selected_global_positions'] == [0, 2, 3]
    assert alpha['scope_binding']['selection_roles'] == ['primary', 'shared', 'context']
    assert beta['selected_global_positions'] == [1, 2]
    assert beta['scope_binding']['selection_roles'] == ['primary', 'shared']
    assert 3 in beta['not_sent_global_positions'] and beta['scope_plan'] == plan
    for value in (alpha, beta):
        positions = value['selected_global_positions']
        assert value['input_claims'] == [claims[position] for position in positions]
        assert [item['claim'] for item in value['payload']['indexed_inputs']['input_claims']] == value['input_claims']
        assert value['payload']['spans'] == [[item['span_ref'], item['text']] for item in resolve_evidence(
            source['catalog'], value['allowed_span_refs'], source['allowed_span_refs'])]
        assert value['partial_scope'] is True and value['aggregate_verified'] is False

def test_blocked_plan_and_other_scope_never_create_a_partial_request_by_fallback():
    cap, source, plan = scene(); snapshot = source['snapshot']
    full = [snapshot['input_items'][key] for key in snapshot['input_order']]
    policy = copy.deepcopy(planner.DEFAULT_CAPACITY); policy['max_scopes'] = 1
    blocked = planner.build_plan(plan['indexed_inputs'], snapshot['snapshot_hash'], plan['scope_ledger'],
        input_claims=full, capacity_policy=policy)
    assert blocked['status'] == 'blocked'
    with pytest.raises(ValueError): prep.build_preparation(cap, source, blocked, 'custom:alpha')
    with pytest.raises(ValueError): prep.build_preparation(cap, source, plan, 'scope-not-confirmed')
