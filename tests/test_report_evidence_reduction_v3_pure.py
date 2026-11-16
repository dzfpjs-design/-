"""Synthetic full-plan Reduce preparation checks; no IO/provider/auth claims.

The receipt/descriptor fixtures are deliberately fabricated caller inputs. They
exercise pure cold consistency, not actual journal or provider authenticity.
Root alone runs these tests; no real report, paid answer or credentials occur.
"""
import copy
import hashlib
import json
import socket
import subprocess
from uuid import UUID

import pytest

from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_preparation as maps
from generic_agents import report_evidence_projection_v5 as projection
from generic_agents import report_evidence_reduction_v3 as subject
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded, build_evidence_source
from tests.test_report_evidence_v5_preparation_pure import cap


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure reduction preparation cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def refresh(item, catalog):
    """Rebuild fabricated original-body metadata after a synthetic mutation."""
    arguments = json.dumps(item['wire'], ensure_ascii=False, indent=1)
    tools = [{'id': 'synthetic_' + item['binding']['stage'], 'type': 'function',
        'function': {'name': projection.TOOL_NAME, 'arguments': arguments}}]
    body = raw({'choices': [{'finish_reason': 'tool_calls',
        'message': {'tool_calls': tools}}]})
    inspected = tool.inspect_response(body, 200, 'map-v5')
    assert inspected['metadata']['envelope_valid']
    item['metadata'] = inspected['metadata']
    receipt = raw({**inspected['metadata'], 'evidence_profile': item['evidence_profile'],
        'call_id': item['binding']['reserved_call_id'], 'usage': {}})
    envelope = {'schema': 'report-evidence-journal-envelope-v1',
        'binding': copy.deepcopy(item['binding']), 'binding_hash': digest(item['binding']),
        'wire_kind': 'text', 'encoding': 'utf-8-surrogatepass-v1',
        'wire_bytes': len(body), 'wire_sha256': sha(body), 'receipt_status': 'saved',
        'receipt_bytes': len(receipt), 'receipt_sha256': sha(receipt),
        'consumable': True, 'quarantine_reasons': []}
    envelope_bytes = json.dumps(envelope, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode('ascii')
    descriptor = {**envelope, 'schema': 'report-evidence-journal-descriptor-v1',
        'envelope_bytes': len(envelope_bytes), 'envelope_sha256': sha(envelope_bytes)}
    item['descriptor'] = {**descriptor, 'descriptor_hash': digest(descriptor)}
    item['projection'] = projection.build_map_projection(item['wire'],
        catalog=catalog, **{key: item[key] for key in ('binding', 'expected_binding',
            'descriptor', 'metadata', 'evidence_profile', 'allowed_span_refs')})


def scene(*, empty=False):
    model = cap()
    catalog = build_evidence_source('a' * 64, [
        ['w:p1', ' 保留＋与+、原标点与尾部空白。 '], ['w:p2', '未引用合成段落。'],
        ['o:p1', '同源OCR合成视图，不是独立证明。'], ['o:p2', '']])
    prepared = maps.build_map_preparation(model, catalog,
        {'object_names': ['合成对象'], 'businesses': []}, ' 合成人工要求。 ',
        generation_version=maps.GENERATION_V5)
    assert len(prepared['plan']['chunks']) == 2  # Separate Word/OCR view boundary.
    profile = copy.deepcopy(prepared['generation_contract']['input_policy'])
    assert profile.pop('single_batch') is True
    inputs = []
    for position, chunk in enumerate(prepared['plan']['chunks']):
        wire = {'schema': slots.WIRE_SCHEMA,
            'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
        if not empty:
            claim = {'kind': '职责', 'statement': ' 原声明＋+，不删空白。 ',
                'span_refs': [chunk['span_refs'][0]], 'status': 'explicit', 'explanation': ''}
            # Same declaration at two distinct original positions is not deduped.
            for slot in ('slot_001', 'slot_080'):
                wire['slots'][slot] = {'state': 'present', 'claim': copy.deepcopy(claim)}
        binding = {'schema': 'report-evidence-journal-binding-v1',
            'actor_id': identity(1), 'workspace_id': identity(2), 'draft_id': identity(3),
            'reserved_call_id': identity(10 + position), 'stage': chunk['chunk_id'],
            'source_hash': catalog['descriptor']['source_hash'],
            'catalog_hash': catalog['descriptor']['catalog_hash'],
            'generation_hash': digest({'published_model_hash': 'b' * 64,
                'contract': prepared['generation_contract']}),
            'dispatch_hash': digest({'model_id': identity(4), 'authorized': chunk,
                'map_preparation_hash': prepared['preparation_hash'],
                'map_plan_hash': prepared['plan']['plan_hash'], 'request_hash': chunk['request_hash']}),
            'messages_hash': chunk['messages_hash']}
        item = {'wire': wire, 'binding': binding, 'expected_binding': copy.deepcopy(binding),
            'allowed_span_refs': copy.deepcopy(chunk['span_refs']),
            'evidence_profile': copy.deepcopy(profile)}
        refresh(item, catalog)
        inputs.append(item)
    return model, catalog, prepared, inputs


def seal(value, field):
    value[field] = digest({key: item for key, item in value.items() if key != field})
    return value


def reorder(value):
    if type(value) is dict:
        return {key: reorder(value[key]) for key in reversed(list(value))}
    if type(value) is list:
        return [reorder(item) for item in value]
    return value


def test_all_original_claims_keys_order_scope_and_full_chain_proofs_are_retained_once():
    model, catalog, prepared, inputs = scene()
    before = copy.deepcopy((model.data, catalog, prepared, inputs))
    result = subject.reduction_inputs_v3(prepared, catalog, model, inputs)
    assert result['scope'] == {'actor_id': identity(1), 'workspace_id': identity(2), 'draft_id': identity(3)}
    assert result['input_order'] == ['map-1/claims/0', 'map-1/claims/1', 'map-2/claims/0', 'map-2/claims/1']
    assert result['input_claims'] == [claim for item in inputs for claim in item['projection']['claims']]
    assert list(result['input_items']) == result['input_order']
    assert len({item['key'] for item in result['input_claims']}) == 4
    assert result['input_claims'][0]['statement'] == result['input_claims'][1]['statement']
    assert all(set(item) == {'key', 'kind', 'statement', 'span_refs', 'status', 'explanation'}
        for item in result['input_claims'])
    assert result['allowed_span_refs'] == ['w:p1/s1', 'o:p1/s1']
    assert result['spans'] == [[ref, catalog['index'][ref]['text']] for ref in result['allowed_span_refs']]
    assert 'w:p2/s1' not in result['allowed_span_refs']
    assert result['map_inputs_hash'] == digest(result['map_proofs'])
    for chunk, item, proof in zip(prepared['plan']['chunks'], inputs, result['map_proofs']):
        assert proof['map_preparation_hash'] == prepared['preparation_hash']
        assert proof['map_plan_hash'] == prepared['plan']['plan_hash']
        assert proof['request_hash'] == chunk['request_hash'] != proof['messages_hash']
        assert proof['binding_hash'] == digest(item['expected_binding'])
        for key in ('journal_descriptor_hash', 'journal_wire_sha256', 'http_body_sha256',
            'arguments_sha256', 'wire_value_hash', 'view_hash', 'codec_hash',
            'tool_metadata_hash', 'evidence_profile_hash', 'projection_hash'):
            assert proof[key] == item['projection'][key]
    assert (model.data, catalog, prepared, inputs) == before


def test_new_single_strict_reduce_request_freezes_full_post_hash_capacity_and_shared_schema():
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    request = subject.rebuild_reduction_request(value, catalog, model, prepared, inputs)
    spec, capacity = value['generation_contract'], value['output_capacity']
    assert value['schema'] == 'report-evidence-reduction-preparation-v3'
    assert spec['schema'] == 'report-evidence-reduction-generation-v3'
    assert value['payload']['schema'] == 'report-evidence-reduction-payload-v3'
    assert spec['evidence_stage'] == 'reduce-v3' and spec['codec'] == 'report-evidence-reduction-codec-v6'
    assert spec['dispatch_options']['max_tokens'] == 131072
    function = request['request']['tools'][0]['function']
    assert function['name'] == 'submit_report_evidence_reduction' and function['strict'] is True
    assert '$def' in function['parameters'] and '$defs' not in function['parameters']
    assert request['request_body'] == tool._bytes(request['request'])
    assert value['request_hash'] == sha(request['request_body'])
    assert value['messages_hash'] == digest(request['messages']) != value['request_hash']
    assert value['input_summary'] == {'prepared_request_bytes': len(request['request_body']),
        'token_bound': len(request['request_body']) + 131072 + 1024, 'max_input_bytes': 900000}
    assert value['input_summary']['token_bound'] <= 1000000
    assert capacity['max_wire_value_bytes'] == capacity['max_response_body_bytes'] == 1024 * 1024
    assert capacity['completion_required'] == 'tool_calls' and capacity['fit_guaranteed'] is False
    assert capacity['token_estimate'] is None and capacity['required_reconciliation_entries'] == 4
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    assert value['can_apply'] is value['can_publish'] is False
    assert 'request_body' not in value and 'input_items' not in value
    assert subject.check_reduction_preparation(value, catalog, model, prepared, inputs) == value


def test_payload_carries_all_original_declarations_context_and_only_referenced_text():
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    request = subject.rebuild_reduction_request(value, catalog, model, prepared, inputs)
    sent = json.loads(request['messages'][-1]['content'])
    assert sent == value['payload']
    assert sent['context'] == prepared['context']
    assert sent['user_instruction'] == ' 合成人工要求。 '
    assert sent['input_claims'] == [claim for item in inputs for claim in item['projection']['claims']]
    assert [text for _, text in sent['spans']] == [catalog['index'][ref]['text'] for ref in ('w:p1/s1', 'o:p1/s1')]
    assert sent['input_items_hash'] == value['input_items_hash']
    assert sent['input_order_hash'] == digest(value['input_order'])
    assert json.loads(request['messages'][1]['content'])['schema'] == subject.TRUSTED_TASK


@pytest.mark.parametrize('status', ['inferred', 'unknown'])
def test_uncertain_claims_keep_original_status_explanation_and_can_have_no_cited_spans(status):
    model, catalog, prepared, inputs = scene()
    for item in inputs:
        for name in ('slot_001', 'slot_080'):
            item['wire']['slots'][name]['claim'].update(status=status, span_refs=[],
                explanation=' 仅为合成不确定说明，尚未核实。 ')
        refresh(item, catalog)
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    assert value['payload']['spans'] == []
    assert len(value['payload']['input_claims']) == 4
    assert all(claim['status'] == status and claim['span_refs'] == []
        and claim['explanation'] == ' 仅为合成不确定说明，尚未核实。 '
        for claim in value['payload']['input_claims'])
    assert value['can_apply'] is value['can_publish'] is False


@pytest.mark.parametrize('mode', ['missing_tail', 'reverse', 'duplicate', 'extra', 'missing_field',
    'extra_field', 'wrong_allowlist', 'old_wire', 'old_preparation', 'request_hash', 'plan_hash'])
def test_missing_reordered_extra_old_or_publicly_resigned_map_inputs_are_rejected(mode):
    model, catalog, prepared, inputs = scene()
    if mode == 'missing_tail': inputs.pop()
    elif mode == 'reverse': inputs.reverse()
    elif mode == 'duplicate': inputs[1] = copy.deepcopy(inputs[0])
    elif mode == 'extra': inputs.append(copy.deepcopy(inputs[0]))
    elif mode == 'missing_field': del inputs[0]['descriptor']
    elif mode == 'extra_field': inputs[0]['trusted'] = True
    elif mode == 'wrong_allowlist': inputs[0]['allowed_span_refs'] = inputs[1]['allowed_span_refs']
    elif mode == 'old_wire': inputs[0]['wire'] = {'schema': 'report-evidence-map-v3', 'claims': []}
    elif mode == 'old_preparation': prepared['generation_contract']['schema'] = maps.GENERATION_V4
    elif mode == 'request_hash':
        prepared['plan']['chunks'][0]['request_hash'] = 'f' * 64
        seal(prepared['plan']['chunks'][0], 'chunk_hash'); seal(prepared['plan'], 'plan_hash')
    else: prepared['plan']['plan_hash'] = 'f' * 64
    seal(prepared, 'preparation_hash')
    with pytest.raises(ValueError): subject.reduction_inputs_v3(prepared, catalog, model, inputs)


@pytest.mark.parametrize('field', ['actor_id', 'workspace_id', 'draft_id', 'generation_hash',
    'messages_hash', 'reserved_call_id'])
def test_individually_valid_resealed_projection_cannot_cross_scope_or_duplicate_calls(field):
    model, catalog, prepared, inputs = scene()
    item = inputs[1]
    replacement = (inputs[0]['binding']['reserved_call_id'] if field == 'reserved_call_id'
        else identity(99) if field.endswith('_id') else 'e' * 64)
    item['binding'][field] = replacement
    item['expected_binding'] = copy.deepcopy(item['binding'])
    refresh(item, catalog)  # Its isolated projection really passes its own cold verifier.
    with pytest.raises(ValueError): subject.reduction_inputs_v3(prepared, catalog, model, inputs)


@pytest.mark.parametrize('mode', ['source', 'catalog', 'binding_expectation', 'profile',
    'descriptor', 'metadata', 'projection', 'claim'])
def test_complete_per_map_source_descriptor_metadata_profile_and_projection_are_rechecked(mode):
    model, catalog, prepared, inputs = scene(); item = inputs[0]
    if mode in {'source', 'catalog'}: item['binding'][mode + '_hash'] = 'f' * 64
    elif mode == 'binding_expectation': item['expected_binding']['dispatch_hash'] = 'f' * 64
    elif mode == 'profile': item['evidence_profile']['max_output_tokens'] = 128
    elif mode == 'descriptor':
        item['descriptor']['receipt_sha256'] = 'f' * 64; seal(item['descriptor'], 'descriptor_hash')
    elif mode == 'metadata': item['metadata']['tool_call_count'] = 2
    elif mode == 'projection': item['projection']['slot_positions'] = [1, 79]; seal(item['projection'], 'projection_hash')
    else: item['wire']['slots']['slot_001']['claim']['statement'] = '改动后原投影不变'
    with pytest.raises(ValueError): subject.build_reduction_preparation(model, prepared, catalog, inputs)


def test_all_empty_maps_never_prepare_a_paid_reduction(monkeypatch):
    model, catalog, prepared, inputs = scene(empty=True)
    result = subject.reduction_inputs_v3(prepared, catalog, model, inputs)
    assert result['input_order'] == result['input_claims'] == result['spans'] == []
    def forbidden(*args, **kwargs): raise AssertionError('No empty Reduce request may be prepared')
    monkeypatch.setattr(subject, '_request', forbidden)
    with pytest.raises(ValueError, match='全部Map原声明为空'):
        subject.build_reduction_preparation(model, prepared, catalog, inputs)


def test_json_key_order_and_catalog_index_order_do_not_change_frozen_result():
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    other_cap = copy.deepcopy(model); other_cap.data = reorder(other_cap.data)
    assert subject.check_reduction_preparation(reorder(value), reorder(catalog),
        other_cap, reorder(prepared), reorder(inputs)) == reorder(value)
    actual = subject.rebuild_reduction_request(value, catalog, model, prepared, inputs)
    other = subject.rebuild_reduction_request(reorder(value), reorder(catalog),
        other_cap, reorder(prepared), reorder(inputs))
    assert actual == other


@pytest.mark.parametrize('mode', ['input_order', 'input_claims', 'spans', 'context', 'note',
    'map_proofs', 'spec', 'request_hash', 'summary', 'capacity', 'extra', 'call_count', 'authorization'])
def test_public_resealing_cannot_replace_original_reduce_input_model_request_or_authority(mode):
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    if mode == 'input_order': value['input_order'].reverse()
    elif mode == 'input_claims': value['payload']['input_claims'][0]['key'] = 'self_signed'
    elif mode == 'spans': value['payload']['spans'][0][1] += '改动'
    elif mode == 'context': value['payload']['context']['object_names'] = ['改动']
    elif mode == 'note': value['payload']['user_instruction'] += '改动'
    elif mode == 'map_proofs': value['map_proofs'][0]['arguments_sha256'] = 'f' * 64
    elif mode == 'spec':
        value['generation_contract']['dispatch_options']['max_tokens'] = 8192
        value['generation_contract_hash'] = digest(value['generation_contract'])
    elif mode == 'request_hash': value['request_hash'] = value['messages_hash']
    elif mode == 'summary': value['input_summary']['token_bound'] -= 1
    elif mode == 'capacity': value['output_capacity']['fit_guaranteed'] = True
    elif mode == 'extra': value['automatically_authorized'] = True
    elif mode == 'call_count': value['call_count'] = True
    else: value['requires_separate_authorization'] = False
    value['payload_hash'] = digest(value['payload']); seal(value, 'preparation_hash')
    with pytest.raises(ValueError): subject.check_reduction_preparation(value, catalog, model, prepared, inputs)


@pytest.mark.parametrize('mode', ['source', 'context', 'note', 'schema'])
def test_gateway_must_preserve_all_frozen_values_before_strict_request_construction(monkeypatch, mode):
    model, catalog, prepared, inputs = scene()
    original = subject.prepare_cloud_json
    def changed(*args, **kwargs):
        messages, size, images = original(*args, **kwargs)
        messages = copy.deepcopy(messages)
        if mode == 'schema':
            prefix = subject.CLOUD_JSON_SYSTEM_PREFIX
            value = json.loads(messages[0]['content'][len(prefix):]); value['title'] = 'changed'
            messages[0]['content'] = prefix + json.dumps(value, ensure_ascii=False)
        else:
            payload = json.loads(messages[-1]['content'])
            if mode == 'source': payload['spans'][0][1] += '改'
            elif mode == 'context': payload['context']['object_names'] = ['改']
            else: payload['user_instruction'] += '改'
            messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
        return messages, size, images
    monkeypatch.setattr(subject, 'prepare_cloud_json', changed)
    def forbidden(*args, **kwargs): raise AssertionError('Changed input must fail before tool preparation')
    monkeypatch.setattr(subject, 'prepare_request', forbidden)
    with pytest.raises(ValueError, match='网关改变了汇总'):
        subject.build_reduction_preparation(model, prepared, catalog, inputs)


@pytest.mark.parametrize('case', ['request_bytes', 'context_with_output'])
def test_full_post_and_output_reservation_both_bound_the_single_call(case):
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    payload = copy.deepcopy(value['payload'])
    # Exercise the pure request boundary with a synthetic complete payload,
    # without claiming this oversized context passed the original Map plan.
    payload['context']['synthetic_padding'] = ''
    baseline = subject._request(model, value['generation_contract'], payload)
    target = (900001 if case == 'request_bytes' else 1000000 - 131072 - 1024 + 1)
    assert target > len(baseline['request_body'])
    payload['context']['synthetic_padding'] = 'x' * (target - len(baseline['request_body']))
    with pytest.raises(EvidenceCapacityExceeded):
        subject._request(model, value['generation_contract'], payload)


def test_legacy_protocol_defaults_and_model_hash_binding_remain_separate():
    from generic_agents import report_evidence_reduction as legacy
    model, catalog, prepared, inputs = scene()
    value = subject.build_reduction_preparation(model, prepared, catalog, inputs)
    assert maps.GENERATION == maps.GENERATION_V2
    assert legacy.GENERATION == legacy.GENERATION_V1
    assert value['generation_contract']['model_hash'] == digest(model.data)
    assert value['generation_contract']['map_generation_hash'] == digest(prepared['generation_contract'])
    other = copy.deepcopy(model); other.data['execution']['report_evidence_output']['max_tokens'] = 32768
    with pytest.raises(ValueError): subject.check_reduction_preparation(value, catalog, other, prepared, inputs)
