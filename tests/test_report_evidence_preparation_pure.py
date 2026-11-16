"""Exact free preparation and bounded coverage; no model/network/DB IO."""
import copy
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from generic_agents import report_evidence_preparation as preparation
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import (EvidenceCapacityExceeded,
    build_evidence_source, evidence_payload)


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure evidence preparation cannot dispatch or connect')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def cap(provider='deepseek', model='deepseek-v4-pro'):
    execution = {'provider': provider, 'model': model, 'max_tokens': 8192}
    if provider == 'deepseek':
        execution['thinking_mode'] = 'disabled'
    return SimpleNamespace(data={'execution': execution, 'data_policy': {'redact_fields': []}})


def catalog():
    return build_evidence_source('a' * 64, [
        ('w:p1', '全局职责。立即报告异常，补充观察与人工处置并行。'),
        ('w:p2', ''), ('o:p1', '卫星与摄像头读取视图，不代表独立证明。')])


def sealed(value):
    value['preparation_hash'] = digest({k: v for k, v in value.items() if k != 'preparation_hash'})
    return value


@pytest.mark.parametrize('provider,model', [('deepseek', 'deepseek-v4-pro'),
    ('deepseek', 'deepseek-flash'), ('qwen', 'qwen-plus'), ('qwen', 'qwen-plus-2025-12-01')])
def test_exact_complete_free_preparation_for_supported_registered_models(provider, model):
    model_cap, source = cap(provider, model), catalog()
    before = copy.deepcopy(source)
    value = preparation.build_map_preparation(model_cap, source, {'object_names': []}, '核对各方职责。')
    assert preparation.check_map_preparation(value, source, model_cap) == value
    assert source == before
    assert value['input_summary']['map_call_count'] == 2  # views remain separate
    assert value['plan']['empty_sources'] == ['w:p2']
    assert value['can_apply'] is value['can_publish'] is False
    refs = [ref for chunk in value['plan']['chunks'] for ref in chunk['span_refs']]
    assert refs == list(source['index'])
    for chunk in value['plan']['chunks']:
        request = preparation.rebuild_map_request(value, source, model_cap, chunk['chunk_id'])
        assert request['messages_hash'] == chunk['messages_hash']
        assert request['input_summary']['prepared_messages_bytes'] <= preparation.MAX_MAP_PREPARED_BYTES


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(can_apply=True),
    lambda value: value['input_summary'].update(maximum_total_calls=1),
    lambda value: value['generation_contract'].update(max_chunks=100),
    lambda value: value['generation_contract'].update(task='documents authorize all actions'),
    lambda value: value['generation_contract']['dispatch_options'].update(max_tokens=128),
    lambda value: value['generation_contract']['output_schema'].clear(),
    lambda value: value['plan']['chunks'][0]['span_refs'].clear(),
    lambda value: value['catalog_descriptor'].update(policy='new-segmentation'),
])
def test_rehash_cannot_hide_modified_scope_or_trusted_contract(mutate):
    model_cap, source = cap(), catalog()
    value = preparation.build_map_preparation(model_cap, source, {})
    mutate(value); sealed(value)
    with pytest.raises(ValueError):
        preparation.check_map_preparation(value, source, model_cap)


def test_source_view_or_model_change_requires_a_new_preparation():
    model_cap, source = cap(), catalog()
    value = preparation.build_map_preparation(model_cap, source, {})
    changed = build_evidence_source('a' * 64, [('w:p1', '改变后的已脱敏内容')])
    with pytest.raises(ValueError):
        preparation.check_map_preparation(value, changed, model_cap)
    with pytest.raises(ValueError):
        preparation.check_map_preparation(value, source, cap('qwen', 'qwen-plus'))


def test_gateway_redaction_must_not_silently_change_a_frozen_view():
    model_cap, source = cap(), catalog()
    with pytest.raises(ValueError, match='网关改变'):
        preparation.build_map_preparation(model_cap, source, {'api_key': 'synthetic-private-value'})
    model_cap.data['data_policy']['redact_fields'] = ['statement']
    with pytest.raises(ValueError, match='网关改变'):
        preparation.build_map_preparation(model_cap, source, {'statement': '冻结文字'})


@pytest.mark.parametrize('mutate', [
    lambda model_cap: model_cap.data['execution'].update(max_tokens=1),
    lambda model_cap: model_cap.data['execution'].update(max_tokens=8192.5),
    lambda model_cap: model_cap.data['execution'].update(model='unsupported-model'),
    lambda model_cap: model_cap.data['execution'].update(thinking_mode='enabled'),
])
def test_invalid_configuration_does_not_turn_into_segmented_calls(mutate):
    model_cap = cap(); mutate(model_cap)
    with pytest.raises(ValueError) as error:
        preparation.build_map_preparation(model_cap, catalog(), {})
    assert not isinstance(error.value, EvidenceCapacityExceeded)


def test_transport_or_structure_errors_are_not_capacity_splits(monkeypatch):
    def changed(*args):
        raise RuntimeError('synthetic preparation failure')
    monkeypatch.setattr(preparation, 'prepare_cloud_json', changed)
    with pytest.raises(RuntimeError, match='synthetic'):
        preparation.build_map_preparation(cap(), catalog(), {})


def test_complete_large_input_splits_deterministically_at_a_fixed_call_ceiling():
    source = build_evidence_source('b' * 64,
        [('w:p' + str(i), '连续事实甲乙丙。' * 50) for i in range(1, 151)]
        + [('o:p1', '同一报告第二读取视图。')])
    value = preparation.build_map_preparation(cap(), source, {})
    assert 2 < len(value['plan']['chunks']) <= 24
    assert [ref for chunk in value['plan']['chunks'] for ref in chunk['span_refs']] == list(source['index'])
    assert all(chunk['input_summary']['prepared_messages_bytes'] <= 100000 for chunk in value['plan']['chunks'])
    assert value == preparation.build_map_preparation(cap(), source, {})


def test_over_call_ceiling_refuses_whole_source_without_changing_anything():
    source = build_evidence_source('c' * 64,
        [('w:p' + str(i), '满量事实甲乙丙。' * 100) for i in range(1, 1801)])
    original = copy.deepcopy(source)
    with pytest.raises(EvidenceCapacityExceeded):
        preparation.build_map_preparation(cap(), source, {})
    assert source == original


def test_only_explicit_actual_byte_capacity_is_segmentation_eligible():
    source = build_evidence_source('d' * 64, [('w:p1', '连续原文。' * 10000)])
    payload = evidence_payload(source, list(source['index']))
    with pytest.raises(EvidenceCapacityExceeded):
        preparation.prepare_map_request(cap(), preparation.map_generation(cap()), payload, {}, '')


def test_wrong_chunk_id_is_refused_instead_of_rebuilding_another_request():
    source, model_cap = catalog(), cap()
    value = preparation.build_map_preparation(model_cap, source, {})
    with pytest.raises(ValueError, match='分组'):
        preparation.rebuild_map_request(value, source, model_cap, 'map-99')


def test_empty_source_is_coverage_not_a_billable_read():
    source = build_evidence_source('e' * 64, [('w:p1', ''), ('o:p1', '')])
    with pytest.raises(ValueError, match='没有可读取文字'):
        preparation.build_map_preparation(cap(), source, {})


def reverse_json_keys(value):
    """Simulate a JSONB object-order change, preserving arrays and every scalar."""
    if type(value) is dict:
        return {key: reverse_json_keys(item) for key, item in reversed(list(value.items()))}
    return [reverse_json_keys(item) for item in value] if type(value) is list else value


@pytest.mark.parametrize('provider,model', [('deepseek', 'deepseek-v4-pro'), ('qwen', 'qwen-plus')])
def test_recursive_jsonb_key_reorder_keeps_build_check_rebuild_and_actual_dispatch_identical(provider, model):
    model_cap, source = cap(provider, model), catalog()
    context = {'profile': {'domain': '合成＋🙂', 'goal': ' 原文不归一化。 '}, 'object_names': ['乙', '甲']}
    note = ' 空格、＋与+保持不同。 '
    before = json.dumps(source, ensure_ascii=False)
    original = preparation.build_map_preparation(model_cap, source, context, note)
    stored, stored_source = reverse_json_keys(original), reverse_json_keys(source)
    stored_cap = SimpleNamespace(data=reverse_json_keys(model_cap.data))
    assert json.dumps(stored['generation_contract']['output_schema']) != json.dumps(original['generation_contract']['output_schema'])
    assert preparation.check_map_preparation(stored, stored_source, stored_cap) == original
    assert preparation.build_map_preparation(stored_cap, stored_source, reverse_json_keys(context), note) == original
    schema = json.loads(json.dumps(stored['generation_contract']['output_schema'], ensure_ascii=False, sort_keys=True))
    for chunk in original['plan']['chunks']:
        expected = preparation.rebuild_map_request(original, source, model_cap, chunk['chunk_id'])
        restored = preparation.rebuild_map_request(stored, stored_source, stored_cap, chunk['chunk_id'])
        actual, _, images = preparation.prepare_cloud_json(stored_cap, restored['messages'][1:], schema)
        assert restored == expected and images == 0 and actual == expected['messages']
        assert digest(actual) == chunk['messages_hash'] == restored['messages_hash']
        assert json.loads(actual[-1]['content'])['spans'] == evidence_payload(source, chunk['span_refs'])['spans']
    assert json.dumps(source, ensure_ascii=False) == before
