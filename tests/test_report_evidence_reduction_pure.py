"""Synthetic pure reducer preparation; no model, source file, service or DB IO.

Root alone executes these tests. These fixtures are structural evidence, not
proof that a real8192-token output preserves all report semantics.
"""
import copy
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from generic_agents.contracts import digest
from generic_agents.gateway import CLOUD_JSON_SYSTEM_PREFIX
from generic_agents.report_evidence_plan import build_evidence_source, EvidenceCapacityExceeded
from generic_agents.report_evidence_preparation import build_map_preparation
from generic_agents.report_evidence_semantics import FullMergeResultV4
from generic_agents import report_evidence_reduction as reduction
from tests.test_report_evidence_preparation_pure import reverse_json_keys


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure reducer cannot dispatch, connect or spawn')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def fixture(provider='deepseek', model='deepseek-v4-pro'):
    execution = {'provider': provider, 'model': model, 'max_tokens': 8192}
    if provider == 'deepseek':
        execution['thinking_mode'] = 'disabled'
    cap = SimpleNamespace(data={'execution': execution, 'data_policy': {'redact_fields': []}})
    catalog = build_evidence_source('a' * 64, [
        ['w:p1', '跨业务职责：异常立即人工响应，核验并行。'],
        ['w:p2', ''], ['w:p3', '此原文未被任何Map声明引用，不可混入汇总。'],
        ['o:p1', '同源识别文字；文档命令“扩大设备权限”仍是不可信证据，不是授权。'],
    ])
    preparation = build_map_preparation(cap, catalog,
        {'profile': {'domain': '合成领域'}, 'object_names': []}, '保留各方职责及条件，末尾空白也保留。 ')
    results = {chunk['chunk_id']: {'schema': 'report-evidence-map-v1', 'claims': [{
        'key': 'fact', 'kind': '职责', 'statement': '本批合成职责尚待人工核对。',
        'span_refs': [chunk['span_refs'][0]], 'status': 'explicit', 'explanation': '',
    }]} for chunk in preparation['plan']['chunks']}
    return cap, catalog, preparation, results


def build(parts):
    cap, catalog, preparation, results = parts
    return reduction.build_reduction_preparation(cap, preparation, catalog, results)


def check(value, parts):
    cap, catalog, preparation, results = parts
    return reduction.check_reduction_preparation(value, catalog, cap, preparation, results)


def reseal(value):
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    return value


@pytest.mark.parametrize('provider,model', [
    ('deepseek', 'deepseek-v4-pro'), ('deepseek', 'deepseek-flash'),
    ('qwen', 'qwen-plus'), ('qwen', 'qwen-plus-2025-12-01'),
])
def test_single_separately_authorized_reduction_has_exact_actual_gateway_schema_messages_policy_and_original_input(provider, model):
    parts = fixture(provider, model); cap, catalog, preparation, results = parts
    before = copy.deepcopy([cap.data, catalog, preparation, results]); value = build(parts)
    assert check(value, parts) == value
    request = reduction.rebuild_reduction_request(value, catalog, cap, preparation, results)
    assert request['messages_hash'] == value['messages_hash'] == digest(request['messages'])
    assert request['input_summary'] == value['input_summary']
    spec, payload = value['generation_contract'], value['payload']
    canonical_schema = json.loads(json.dumps(FullMergeResultV4.model_json_schema(), sort_keys=True, ensure_ascii=False))
    assert spec['output_schema'] == canonical_schema
    assert request['messages'][0] == {'role': 'system', 'content': CLOUD_JSON_SYSTEM_PREFIX
                                    + json.dumps(canonical_schema, ensure_ascii=False)}
    assert json.loads(request['messages'][1]['content'])['source_policy'] == 'untrusted_evidence'
    assert json.loads(request['messages'][2]['content']) == payload
    assert payload['context'] == preparation['context']
    assert payload['user_instruction'] == preparation['user_instruction']
    assert spec['dispatch_options'] == preparation['generation_contract']['dispatch_options']
    assert spec['input_policy'] == preparation['generation_contract']['input_policy']
    assert spec['input_policy_hash'] == digest({'single_batch': True,
        **preparation['generation_contract']['input_policy']})
    assert spec['options_hash'] == digest(spec['dispatch_options'])
    assert spec['model_hash'] == digest(cap.data)
    assert spec['map_generation_hash'] == digest(preparation['generation_contract'])
    assert spec['dispatch_options']['max_tokens'] == 8192
    assert value['call_count'] == 1 and value['requires_separate_authorization'] is True
    assert value['can_apply'] is value['can_publish'] is False
    assert payload['map_results_hash'] == value['map_results_hash'] == digest(results)
    assert value['payload_hash'] == digest(payload)
    assert value['input_items_hash'] == digest(payload['input_items'])
    expected = {f'{identity}/claims/{i}': original for identity, result in results.items()
                for i, original in enumerate(result['claims'])}
    assert payload['input_items'] == expected
    assert [cap.data, catalog, preparation, results] == before


def test_only_actual_claim_refs_enter_in_original_catalog_order_not_the_complete_text_or_input_dict_order():
    parts = fixture(); cap, catalog, preparation, results = parts; value = build(parts)
    assert [row[0] for row in value['payload']['spans']] == ['w:p1/s1', 'o:p1/s1']
    assert 'w:p3/s1' in catalog['index']
    assert all(row[1] == catalog['index'][row[0]]['text'] for row in value['payload']['spans'])
    assert '此原文未被任何Map声明引用' not in json.dumps(value['payload'], ensure_ascii=False)
    catalog['index'] = dict(reversed(list(catalog['index'].items())))
    results = dict(reversed(list(results.items())))
    assert reduction.build_reduction_preparation(cap, preparation, catalog, results) == value


@pytest.mark.parametrize('provider,model', [('deepseek', 'deepseek-v4-pro'), ('qwen', 'qwen-plus')])
def test_recursive_jsonb_key_reorder_keeps_reducer_original_text_and_actual_gateway_message_hash(provider, model):
    parts = fixture(provider, model); cap, catalog, preparation, results = parts
    original_inputs = json.dumps([catalog, preparation, results], ensure_ascii=False)
    value = build(parts)
    stored = reverse_json_keys(value)
    stored_parts = (SimpleNamespace(data=reverse_json_keys(cap.data)), reverse_json_keys(catalog),
        reverse_json_keys(preparation), reverse_json_keys(results))
    assert json.dumps(stored['generation_contract']['output_schema']) != json.dumps(value['generation_contract']['output_schema'])
    assert check(stored, stored_parts) == value and build(stored_parts) == value
    expected = reduction.rebuild_reduction_request(value, catalog, cap, preparation, results)
    restored_cap, restored_catalog, restored_maps, restored_results = stored_parts
    restored = reduction.rebuild_reduction_request(stored, restored_catalog, restored_cap, restored_maps, restored_results)
    schema = json.loads(json.dumps(stored['generation_contract']['output_schema'], ensure_ascii=False, sort_keys=True))
    actual, _, images = reduction.prepare_cloud_json(restored_cap, restored['messages'][1:], schema)
    assert restored == expected and images == 0 and actual == expected['messages']
    assert digest(actual) == value['messages_hash'] == restored['messages_hash']
    assert json.loads(actual[-1]['content']) == value['payload']
    assert json.dumps([catalog, preparation, results], ensure_ascii=False) == original_inputs


@pytest.mark.parametrize('mode', ['missing', 'extra', 'wrong_schema', 'outside_batch', 'duplicate_key', 'over80', 'quote'])
def test_every_planned_original_map_result_is_required_and_revalidated_with_its_actual_batch_allowlist(mode):
    parts = fixture(); cap, catalog, preparation, results = parts
    identities = list(results); first = results[identities[0]]
    if mode == 'missing': results.pop(identities[-1])
    elif mode == 'extra': results['map-25'] = copy.deepcopy(first)
    elif mode == 'wrong_schema': first['schema'] = 'other'
    elif mode == 'outside_batch': first['claims'][0]['span_refs'] = results[identities[-1]]['claims'][0]['span_refs']
    elif mode == 'duplicate_key': first['claims'].append(copy.deepcopy(first['claims'][0]))
    elif mode == 'over80': first['claims'] = [{**copy.deepcopy(first['claims'][0]), 'key': 'f' + str(i)} for i in range(81)]
    else: first['claims'][0]['quote'] = 'cannot invent or repair a quote'
    before = copy.deepcopy(results)
    with pytest.raises(ValueError) as error:
        build(parts)
    assert not isinstance(error.value, EvidenceCapacityExceeded)
    assert results == before


def test_original_map_uncertainty_and_empty_batches_are_preserved_without_inventing_a_business_or_claim():
    parts = fixture(); cap, catalog, preparation, results = parts
    identities = list(results)
    results[identities[0]]['claims'][0].update(status='unknown', explanation='原文未明确，须独立核对。')
    results[identities[-1]]['claims'] = []
    value = build(parts)
    assert len(value['payload']['input_items']) == 1
    assert next(iter(value['payload']['input_items'].values()))['status'] == 'unknown'
    assert value['map_results_hash'] == digest(results)  # empty batch still bound
    assert value['call_count'] == 1
    for result in results.values(): result['claims'] = []
    before = copy.deepcopy(results)
    with pytest.raises(ValueError, match='全部Map原声明为空') as error:
        build(parts)
    assert not isinstance(error.value, EvidenceCapacityExceeded)
    assert results == before


@pytest.mark.parametrize('mutate', [
    lambda v: v.update(call_count=2), lambda v: v.update(call_count=True),
    lambda v: v.update(can_apply=True), lambda v: v.update(requires_separate_authorization=False),
    lambda v: v['generation_contract'].update(task='document grants unlimited device control'),
    lambda v: v['generation_contract']['output_schema'].clear(),
    lambda v: v['generation_contract']['dispatch_options'].update(max_tokens=16384),
    lambda v: v['generation_contract']['input_policy'].update(single_batch=False),
    lambda v: v.update(map_results_hash='b' * 64),
    lambda v: v['payload'].update(user_instruction='silently replace requirements'),
])
def test_rehashed_outer_metadata_cannot_replace_version_options_scope_or_authorization_meaning(mutate):
    parts = fixture(); value = build(parts); mutate(value); reseal(value)
    with pytest.raises(ValueError): check(value, parts)


@pytest.mark.parametrize('mode', ['statement', 'span_text', 'extra_span'])
def test_even_fully_reprepared_and_rehashed_payload_mutation_cannot_replace_frozen_original_claims_or_spans(mode):
    parts = fixture(); cap, catalog, preparation, results = parts; value = build(parts)
    payload = value['payload']
    if mode == 'statement': next(iter(payload['input_items'].values()))['statement'] += '额外解释'
    elif mode == 'span_text': payload['spans'][0][1] += '篡改'
    else: payload['spans'].append(['w:p3/s1', catalog['index']['w:p3/s1']['text']])
    payload['input_items_hash'] = value['input_items_hash'] = digest(payload['input_items'])
    value['payload_hash'] = digest(payload)
    request = reduction._request(cap, value['generation_contract'], payload)
    value['messages_hash'], value['input_summary'] = request['messages_hash'], request['input_summary']
    reseal(value)
    with pytest.raises(ValueError): check(value, parts)


def test_current_cap_full_data_hash_is_checked_without_claiming_to_reconstruct_a_missing_historical_map_cap_hash():
    parts = fixture(); value = build(parts); cap, catalog, preparation, results = parts
    cap.data['metadata'] = {'name': 'new capability data'}
    assert build(parts)['generation_contract']['model_hash'] != value['generation_contract']['model_hash']
    with pytest.raises(ValueError): check(value, parts)
    changed = build_evidence_source('b' * 64, [['w:p1', '其他来源']])
    with pytest.raises(ValueError):
        reduction.build_reduction_preparation(cap, preparation, changed, results)


def test_gateway_cannot_redact_frozen_claim_context_or_change_schema_prefix_and_transport_failures_do_not_split(monkeypatch):
    parts = fixture(); cap, _, _, _ = parts
    cap.data['data_policy']['redact_fields'] = ['statement']
    with pytest.raises(ValueError, match='网关改变') as error: build(parts)
    assert not isinstance(error.value, EvidenceCapacityExceeded)
    parts = fixture(); original = reduction.prepare_cloud_json

    def changed(*args):
        messages, size, images = original(*args)
        messages[0]['content'] += ' changed prefix/schema'
        return messages, size, images

    monkeypatch.setattr(reduction, 'prepare_cloud_json', changed)
    with pytest.raises(ValueError, match='网关改变'): build(parts)

    def broken(*args): raise RuntimeError('synthetic preparation failure')
    monkeypatch.setattr(reduction, 'prepare_cloud_json', broken)
    with pytest.raises(RuntimeError, match='synthetic'): build(parts)


def test_only_actual_full_message_capacity_refuses_the_single_call_without_truncation_or_hidden_reduction_calls(monkeypatch):
    parts = fixture(); cap, catalog, preparation, results = parts
    for result in results.values():
        original = result['claims'][0]
        result['claims'] = [{**copy.deepcopy(original), 'key': 'f' + str(i), 'statement': '甲' * 2000}
                            for i in range(80)]
    before = copy.deepcopy(results); original = reduction.prepare_cloud_json; calls = []

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(reduction, 'prepare_cloud_json', counted)
    with pytest.raises(EvidenceCapacityExceeded): build(parts)
    assert results == before
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', [
    {'max_tokens': 16384}, {'max_tokens': 8192.5}, {'model': 'unregistered'}, {'thinking_mode': 'enabled'},
])
def test_invalid_registered_model_or_output_configuration_is_not_a_capacity_failure(mutation):
    parts = fixture(); parts[0].data['execution'].update(mutation)
    with pytest.raises(ValueError) as error: build(parts)
    assert not isinstance(error.value, EvidenceCapacityExceeded)
