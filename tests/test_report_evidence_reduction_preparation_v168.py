"""Synthetic pure complete-Map preparation; never provider or report evidence.

The journal/body fixtures below are deliberately fabricated public data for
actual local validators. They do not represent persisted receipts, successful
paid calls, a real report, authenticated source ownership or semantic quality.
"""
import copy
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents import report_claim_native_v168 as native
from generic_agents import report_evidence_map_preparation_v168 as maps
from generic_agents import report_evidence_pipeline_v168 as projector
from generic_agents import report_evidence_reduction_v168 as subject
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import build_evidence_source, EvidenceCapacityExceeded
from generic_agents.report_evidence_reduction_wire_v168 import ReductionWireV168
from generic_agents.typed_payload_codec_v131 import canonical_json


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure preparation cannot connect or start a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def raw(value):
    return canonical_json(value).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def model():
    return SimpleNamespace(data={'execution': {'provider': 'qwen', 'model': native.MODEL,
        'max_tokens': 8192, 'report_evidence_output': {'schema': 'report-evidence-output-v168',
            'max_tokens': 32768, 'map_transport': native.DELIVERY,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}},
        'data_policy': {'redact_fields': []}})


def refresh(item, catalog, preparation):
    """Author a new SYNTHETIC envelope, then use real native/projector checks."""
    arguments = json.dumps(item['wire'], ensure_ascii=False, indent=1)
    body = raw({'choices': [{'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': arguments}}]})
    inspected = native.inspect_response(body, 200, 'map-v168',
        response_complete=True, http_content_encoding=None)
    assert inspected['metadata']['envelope_valid'] is True
    assert native.parse_arguments(body, 200, 'map-v168',
        response_complete=True, http_content_encoding=None) == item['wire']
    item['metadata'] = inspected['metadata']
    item['http_body'] = body.decode('utf-8', errors='strict')
    item['response_complete'] = True
    item['http_content_encoding'] = None
    receipt = raw({**inspected['metadata'], 'evidence_profile': item['evidence_profile'],
        'call_id': item['binding']['reserved_call_id'], 'usage': {}})
    envelope = {'schema': 'report-evidence-journal-envelope-v1', 'binding': copy.deepcopy(item['binding']),
        'binding_hash': digest(item['binding']), 'wire_kind': 'text', 'encoding': 'utf-8-surrogatepass-v1',
        'wire_bytes': len(body), 'wire_sha256': sha(body), 'receipt_status': 'saved',
        'receipt_bytes': len(receipt), 'receipt_sha256': sha(receipt),
        'consumable': True, 'quarantine_reasons': []}
    envelope_bytes = json.dumps(envelope, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode('ascii')
    descriptor = {**envelope, 'schema': 'report-evidence-journal-descriptor-v1',
        'envelope_bytes': len(envelope_bytes), 'envelope_sha256': sha(envelope_bytes)}
    item['descriptor'] = {**descriptor, 'descriptor_hash': digest(descriptor)}
    item['projection'] = projector.build_map_projection(item['wire'], catalog=catalog,
        map_preparation=preparation, **{key: item[key] for key in ('binding', 'expected_binding',
            'descriptor', 'metadata', 'evidence_profile', 'allowed_span_refs',
            'http_body', 'response_complete', 'http_content_encoding')})


def scene(*, empty=False, context=None):
    cap = model()
    catalog = build_evidence_source('a' * 64, [
        ['w:p1', ' 仅当公开合成条件满足才登记；不具备另一能力。＋+🙂 '],
        ['w:p2', '未被原声明引用的旁支，不可借入Reduce。'],
        ['o:p1', '这是同源OCR合成视图，计划与现状冲突仍须人工核对。'], ['o:p2', '']])
    context = {'object_names': [], 'businesses': [], 'counter': 1.0, 'zero': -0.0} if context is None else context
    preparation = maps.build_map_preparation(catalog, cap, context, ' 保留否定、条件与来源矛盾。 ')
    assert len(preparation['plan']['chunks']) == 2
    profile = copy.deepcopy(preparation['generation_contract']['input_policy'])
    assert profile.pop('single_batch') is True
    items = []
    for position, chunk in enumerate(preparation['plan']['chunks']):
        claim = {'kind': '条件', 'statement': ' 原声明保留＋+与空白，不认定已具备。 ',
            'span_refs': [chunk['span_refs'][0]], 'status': 'explicit', 'explanation': ''}
        binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': identity(1),
            'workspace_id': identity(2), 'draft_id': identity(3), 'reserved_call_id': identity(10 + position),
            'stage': chunk['chunk_id'], 'source_hash': catalog['descriptor']['source_hash'],
            'catalog_hash': catalog['descriptor']['catalog_hash'],
            'generation_hash': digest({'published_model_hash': 'b' * 64, 'contract': preparation['generation_contract']}),
            'dispatch_hash': digest({'model_id': identity(4), 'authorized': chunk,
                'map_preparation_hash': preparation['preparation_hash'],
                'map_plan_hash': preparation['plan']['plan_hash'], 'request_hash': chunk['request_hash']}),
            'messages_hash': chunk['messages_hash']}
        item = {'wire': {'schema': 'report-evidence-map-v3',
            'claims': [] if empty else [copy.deepcopy(claim), copy.deepcopy(claim)]},
            'binding': binding, 'expected_binding': copy.deepcopy(binding),
            'allowed_span_refs': copy.deepcopy(chunk['span_refs']), 'evidence_profile': copy.deepcopy(profile)}
        refresh(item, catalog, preparation)
        items.append(item)
    return cap, catalog, preparation, items


def prepare(case):
    return subject.build_reduction_preparation(case[0], case[2], case[1], case[3])


def reseal(value):
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    return value


def reversed_keys(value):
    if type(value) is dict:
        return {key: reversed_keys(item) for key, item in reversed(list(value.items()))}
    if type(value) is list:
        return [reversed_keys(item) for item in value]
    return value


def test_actual_all_map_checks_retain_keyless_duplicates_order_and_only_cited_original_spans():
    cap, catalog, preparation, maps_input = scene()
    before = copy.deepcopy((cap.data, catalog, preparation, maps_input))
    result = subject.reduction_inputs_v3(preparation, catalog, cap, maps_input)
    assert result['scope'] == {'actor_id': identity(1), 'workspace_id': identity(2), 'draft_id': identity(3)}
    assert result['input_order'] == ['map-1/claims/0', 'map-1/claims/1', 'map-2/claims/0', 'map-2/claims/1']
    assert result['input_claims'] == [claim for item in maps_input for claim in item['wire']['claims']]
    assert all(set(claim) == {'kind', 'statement', 'span_refs', 'status', 'explanation'}
        for claim in result['input_items'].values())
    assert result['allowed_span_refs'] == ['w:p1/s1', 'o:p1/s1']
    assert result['spans'] == [[ref, catalog['index'][ref]['text']] for ref in result['allowed_span_refs']]
    assert '未被原声明引用' not in canonical_json(result['spans'])
    assert len(result['map_proofs']) == len(preparation['plan']['chunks'])
    for proof, item in zip(result['map_proofs'], maps_input):
        assert proof['projection_hash'] == item['projection']['projection_hash']
        assert proof['arguments_sha256'] == item['metadata']['arguments_sha256']
        assert proof['journal_descriptor_hash'] == item['descriptor']['descriptor_hash']
        assert not {'codec', 'view_hash', 'slot_positions_hash'} & set(proof)
    assert (cap.data, catalog, preparation, maps_input) == before


def test_real_gateway_native_schema_measurement_and_preview_require_separate_consent():
    case = scene(); cap, catalog, map_preparation, items = case
    before = copy.deepcopy((cap.data, catalog, map_preparation, items))
    value = prepare(case)
    request = subject.rebuild_reduction_request(value, catalog, cap, map_preparation, items)
    assert value['schema'] == subject.PREPARATION
    assert value['generation_contract']['schema'] == 'report-claim-array-reduction-generation-v168'
    assert value['generation_contract']['evidence_stage'] == 'reduce-v168'
    assert value['generation_contract']['output_schema'] == ReductionWireV168.model_json_schema()
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    assert value['can_apply'] is value['can_publish'] is False
    assert request['request_body'] == raw(request['request'])
    assert request['request_hash'] == sha(request['request_body']) == value['request_hash']
    assert request['messages_hash'] == digest(request['messages']) == value['messages_hash']
    assert request['request_hash'] != request['messages_hash']
    assert request['request']['max_tokens'] == 32768
    assert request['messages'][0]['content'] == native.system_prefix('reduce-v168')
    assert json.loads(request['messages'][1]['content'])['task'] == subject.TASK
    assert json.loads(request['messages'][2]['content']) == value['payload']
    assert not {'http_body', 'response_complete', 'http_content_encoding'} & set(value['payload'])
    assert canonical_json(value['payload']['context']) == canonical_json(map_preparation['context'])
    assert value['input_summary']['prepared_request_bytes'] == len(request['request_body']) <= 900000
    assert value['input_summary']['token_bound'] == len(request['request_body']) + 32768 + 1024
    capacity = value['output_capacity']
    assert capacity['max_response_body_bytes'] == capacity['max_wire_value_bytes'] == 1024 * 1024
    assert capacity['max_output_tokens'] == 32768 and capacity['completion_required'] == 'stop'
    assert capacity['tool_name'] is None and capacity['token_estimate'] is None
    assert capacity['fit_guaranteed'] is False and capacity['required_reconciliation_entries'] == 4
    assert subject.check_reduction_preparation(value, catalog, cap, map_preparation, items) == value
    assert (cap.data, catalog, map_preparation, items) == before


def test_new_task_keeps_conditions_negation_conflicts_and_all_optional_identity_boundary():
    for phrase in ('必要', '仅当', '不具备', '计划建设', '矛盾', '全为false',
                   'not_established', 'unknown', 'questions', '原生null', 'user_instruction'):
        assert phrase in subject.TASK
    assert 'report-object-semantic-candidate-wire-v166' in subject.TASK
    assert 'report-evidence-candidate-wire-v6' not in subject.TASK
    assert subject.GENERATION != maps.GENERATION


def test_all_empty_maps_are_preserved_but_never_prepare_an_extra_reduce_call():
    cap, catalog, map_preparation, items = scene(empty=True)
    result = subject.reduction_inputs_v3(map_preparation, catalog, cap, items)
    assert result['input_order'] == [] and result['input_items'] == {}
    assert len(result['map_proofs']) == 2 and result['allowed_span_refs'] == result['spans'] == []
    with pytest.raises(ValueError): subject.build_reduction_preparation(cap, map_preparation, catalog, items)


@pytest.mark.parametrize('fault', ['missing-tail', 'extra-map', 'reverse', 'wrong-refs',
    'same-call', 'different-actor', 'different-workspace', 'different-draft', 'different-generation',
    'wrong-messages', 'old-map-wire', 'old-preparation', 'unexpected-field'])
def test_complete_plan_and_real_per_map_identity_are_not_replaced_by_partial_or_mixed_inputs(fault):
    cap, catalog, preparation, items = scene()
    if fault == 'missing-tail': items.pop()
    elif fault == 'extra-map': items.append(copy.deepcopy(items[-1]))
    elif fault == 'reverse': items.reverse()
    elif fault == 'wrong-refs': items[0]['allowed_span_refs'].reverse()
    elif fault in {'same-call', 'different-actor', 'different-workspace', 'different-draft', 'different-generation'}:
        key = {'same-call': 'reserved_call_id', 'different-actor': 'actor_id',
            'different-workspace': 'workspace_id', 'different-draft': 'draft_id',
            'different-generation': 'generation_hash'}[fault]
        replacement = items[0]['binding'][key] if fault == 'same-call' else 'f' * 64 if key == 'generation_hash' else identity(88)
        items[1]['binding'][key] = replacement; items[1]['expected_binding'][key] = replacement
        refresh(items[1], catalog, preparation)
    elif fault == 'wrong-messages': items[0]['binding']['messages_hash'] = 'f' * 64
    elif fault == 'old-map-wire': items[0]['wire']['schema'] = 'report-evidence-map-slots-v5'
    elif fault == 'old-preparation': preparation['generation_contract']['schema'] = 'report-evidence-map-generation-v7'
    else: items[0]['extra'] = True
    with pytest.raises(ValueError): subject.reduction_inputs_v3(preparation, catalog, cap, items)


@pytest.mark.parametrize('fault', ['descriptor', 'metadata', 'profile', 'projection', 'input-ref',
    'metadata-arguments-hash', 'metadata-http-hash', 'metadata-bool-status', 'metadata-tools',
    'http-body', 'incomplete', 'completion-bool', 'encoding'])
def test_actual_projection_and_original_envelope_cross_links_are_not_ignored(fault):
    cap, catalog, preparation, items = scene(); item = items[0]
    if fault == 'descriptor': item['descriptor']['receipt_status'] = 'missing'
    elif fault == 'metadata': item['metadata']['finish_reason'] = 'length'
    elif fault == 'profile': item['evidence_profile']['max_images'] = 1
    elif fault == 'projection': item['projection']['claims'].pop()
    elif fault == 'input-ref': item['wire']['claims'][0]['span_refs'] = ['o:p1/s1']
    elif fault == 'http-body': item['http_body'] += ' '
    elif fault == 'incomplete': item['response_complete'] = False
    elif fault == 'completion-bool': item['response_complete'] = 1
    elif fault == 'encoding': item['http_content_encoding'] = 'gzip'
    else:
        if fault == 'metadata-arguments-hash': item['metadata']['arguments_sha256'] = 'not-a-sha'
        elif fault == 'metadata-http-hash': item['metadata']['response_body_sha256'] = 'f' * 64
        elif fault == 'metadata-bool-status': item['metadata']['http_status'] = True
        else: item['metadata']['tool_call_count'] = 1
        # Rebuilding public hashes cannot replace descriptor/content linkage.
        with pytest.raises(ValueError):
            projector.build_map_projection(item['wire'], catalog=catalog, map_preparation=preparation,
                **{key: item[key] for key in ('binding', 'expected_binding', 'descriptor',
                    'metadata', 'evidence_profile', 'allowed_span_refs',
                    'http_body', 'response_complete', 'http_content_encoding')})
        return
    with pytest.raises(ValueError): subject.reduction_inputs_v3(preparation, catalog, cap, items)


@pytest.mark.parametrize('fault', ['task', 'schema', 'typed-context', 'zero-sign', 'claim', 'spans',
    'map-proof', 'request-hash', 'messages-hash', 'call-bool', 'authorization', 'publish', 'extra'])
def test_resealed_preview_never_substitutes_the_cold_complete_original_inputs(fault):
    case = scene(); cap, catalog, map_preparation, items = case; value = prepare(case)
    if fault == 'task': value['generation_contract']['task'] = 'replacement task'
    elif fault == 'schema': value['generation_contract']['output_schema'].clear()
    elif fault == 'typed-context': value['payload']['context']['counter'] = 1
    elif fault == 'zero-sign': value['payload']['context']['zero'] = 0.0
    elif fault == 'claim': value['payload']['input_claims'].pop()
    elif fault == 'spans': value['payload']['spans'].reverse()
    elif fault == 'map-proof': value['map_proofs'][-1]['projection_hash'] = 'f' * 64
    elif fault == 'request-hash': value['request_hash'] = 'f' * 64
    elif fault == 'messages-hash': value['messages_hash'] = value['request_hash']
    elif fault == 'call-bool': value['call_count'] = True
    elif fault == 'authorization': value['requires_separate_authorization'] = 1
    elif fault == 'publish': value['can_publish'] = True
    else: value['extra'] = False
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['payload_hash'] = digest(value['payload']); reseal(value)
    with pytest.raises(ValueError):
        subject.check_reduction_preparation(value, catalog, cap, map_preparation, items)


def test_jsonb_key_order_preserves_typed_original_request_but_mutable_outputs_are_independent():
    case = scene(); cap, catalog, preparation, items = case; value = prepare(case)
    other = (SimpleNamespace(data=reversed_keys(cap.data)), reversed_keys(catalog),
        reversed_keys(preparation), reversed_keys(items))
    assert prepare(other) == value
    original = copy.deepcopy((cap.data, catalog, preparation, items))
    value['payload']['input_claims'][0]['span_refs'].clear(); value['map_proofs'].clear()
    assert (cap.data, catalog, preparation, items) == original
    assert prepare(case)['payload']['input_claims'][0]['span_refs']


def test_gateway_redaction_of_frozen_context_is_not_silently_accepted():
    # Prepare the Maps with harmless context, then exercise the real reduction
    # request builder with a sensitive-name field. No source authentication is
    # claimed by this direct pure request-boundary test.
    case = scene(); value = prepare(case)
    payload = copy.deepcopy(value['payload']); payload['context']['api_key'] = 'synthetic-not-a-secret'
    with pytest.raises(ValueError): subject._request(case[0], value['generation_contract'], payload)


@pytest.mark.parametrize('fault', ['prefix', 'image', 'role', 'content', 'extra'])
def test_actual_gateway_output_must_preserve_prefix_and_all_typed_tail_messages(monkeypatch, fault):
    case = scene(); original = subject.prepare_cloud_json
    def changed(*args, **kwargs):
        messages, count, images = original(*args, **kwargs)
        if fault == 'prefix': messages[0]['content'] = 'another prefix'
        elif fault == 'image': images = 1
        elif fault == 'role': messages[-1]['role'] = 'system'
        elif fault == 'content':
            payload = json.loads(messages[-1]['content']); payload['context']['counter'] = 1
            messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
        else: messages[-1]['extra'] = True
        return messages, count, images
    monkeypatch.setattr(subject, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError): prepare(case)


def test_measured_whole_request_limit_never_creates_split_trimmed_or_extra_calls(monkeypatch):
    case = scene(); original = copy.deepcopy((case[0].data, *case[1:]))
    value = prepare(case)
    assert value['input_summary']['prepared_request_bytes'] > 0
    monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', value['input_summary']['prepared_request_bytes'] - 1)
    with pytest.raises(EvidenceCapacityExceeded): prepare(case)
    assert (case[0].data, *case[1:]) == original
