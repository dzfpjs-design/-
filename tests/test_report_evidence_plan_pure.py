"""Root-executed pure evidence planning: synthetic text, no paid/source IO."""
import copy
import json
import socket
import subprocess

import pytest
from pydantic import ValidationError

from generic_agents.contracts import digest
from generic_agents import report_evidence_plan as evidence


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Evidence planning must not connect or launch anything')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def source():
    return evidence.build_evidence_source('a' * 64, [
        ('w:p1', ' 甲🙂；乙\n丙。'), ('w:p2', ''), ('w:p3', '另一个正文块。'),
        ('o:p1', '第一页文字＋原标点。'), ('o:p2', ' \t\n')], max_span_chars=4)


def prepare(payload):
    # Actual serialization of complete synthetic task/schema/user messages.
    # This is a pure prepare callback, not a provider or model result.
    messages = [{'role': 'system', 'content': '仅提取本批声明；保留未知。'},
        {'role': 'system', 'content': json.dumps(evidence.MapResult.model_json_schema(), ensure_ascii=False, sort_keys=True)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    count = len(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    return {'messages_hash': digest(messages), 'input_summary': {
        'prepared_messages_bytes': count, 'token_bound': count + 1536, 'max_input_bytes': 1000000}}


def limited(max_spans):
    def callback(payload):
        if len(payload['spans']) > max_spans:
            raise evidence.EvidenceCapacityExceeded('synthetic explicit capacity')
        return prepare(payload)
    return callback


def reseal_plan(plan):
    for chunk in plan['chunks']:
        chunk['chunk_hash'] = digest({k: v for k, v in chunk.items() if k != 'chunk_hash'})
    plan['plan_hash'] = digest({k: v for k, v in plan.items() if k != 'plan_hash'})
    return plan


def test_unicode_codepoint_offsets_reconstruct_every_source_without_normalization():
    catalog = source(); descriptor = catalog['descriptor']
    assert evidence.check_evidence_source(catalog) is catalog
    refs = list(catalog['index'])
    originals = [('w:p1', ' 甲🙂；乙\n丙。'), ('w:p2', ''), ('w:p3', '另一个正文块。'),
        ('o:p1', '第一页文字＋原标点。'), ('o:p2', ' \t\n')]
    for source_ref, text in originals:
        parts = [evidence.resolve_span(catalog, ref, refs) for ref in refs if ref.startswith(source_ref + '/')]
        assert ''.join(item['text'] for item in parts) == text
        assert all(item['text'] == text[item['start']:item['end']] for item in parts)
    assert catalog['index']['w:p1/s1']['text'] == ' 甲🙂；'
    assert descriptor['empty_sources'] == ['w:p2']
    assert descriptor['counts']['source_blocks'] == 5
    assert descriptor['counts']['codepoints'] == sum(len(text) for _, text in originals)
    assert set(descriptor) == {'schema', 'source_hash', 'source_blocks_hash', 'policy', 'max_span_chars',
        'ordered_source_refs', 'counts', 'empty_sources', 'catalog_hash'}
    assert descriptor['source_blocks_hash'] == digest(originals)
    assert 'text' not in descriptor and 'index' not in descriptor and 'spans' not in descriptor
    assert descriptor['ordered_source_refs'][:3] == ['w:p1', 'w:p2', 'w:p3']


def test_default_spans_keep_existing_250_character_quote_bound():
    catalog = evidence.build_evidence_source('a' * 64, [('w:p1', '字' * 501)])
    assert [len(item['text']) for item in catalog['index'].values()] == [240, 240, 21]
    assert evidence.build_evidence_source('a' * 64, [('w:p1', '字' * 251)], 250)['descriptor']['counts']['spans'] == 2


@pytest.mark.parametrize('limit', [0, -1, True, 251, 600, 1.0])
def test_invalid_span_policy_rejects_without_truncation(limit):
    with pytest.raises(ValueError):
        evidence.build_evidence_source('a' * 64, [('w:p1', '原文')], limit)


@pytest.mark.parametrize('blocks', [[('p1', 'x')], [('w:p0', 'x')], [('w:p1', 'a'), ('w:p1', 'b')],
    [('o:p1', 1)], [('w:p1', 'bad\x00')], [('w:p1', '\ud800')]])
def test_bad_source_identity_or_json_text_is_not_silently_repaired(blocks):
    with pytest.raises(ValueError):
        evidence.build_evidence_source('a' * 64, blocks)


@pytest.mark.parametrize('mutate', [
    lambda c: c['index']['w:p1/s1'].update(text='改写🙂；'),
    lambda c: c['index']['w:p1/s2'].update(start=3),
    lambda c: c['index'].pop('w:p1/s1'),
    lambda c: c['descriptor']['empty_sources'].append('o:p2'),
    lambda c: c['descriptor']['ordered_source_refs'].reverse(),
    lambda c: c['descriptor']['counts'].update(spans=True),
    lambda c: c['descriptor'].update(max_span_chars=250),
])
def test_catalog_integrity_binds_text_offsets_order_empties_and_policy(mutate):
    catalog = source(); mutate(catalog)
    with pytest.raises(ValueError):
        evidence.check_evidence_source(catalog)


def test_resolver_needs_explicit_batch_scope_and_never_falls_back_to_catalog():
    catalog = source(); ref = 'w:p1/s1'
    assert evidence.resolve_span(catalog, ref, [ref])['text'] == ' 甲🙂；'
    with pytest.raises(TypeError):
        evidence.resolve_span(catalog, ref)
    for allowed in [[], ['o:p1/s1'], [ref, ref], ['w:p99/s1']]:
        with pytest.raises(ValueError):
            evidence.resolve_span(catalog, ref, allowed)


def test_complete_plan_preserves_order_and_separates_views_without_persistent_payload_text():
    catalog = source(); plan = evidence.plan_evidence_reads(catalog, prepare, 1000000)
    assert evidence.check_plan(plan, catalog, prepare) is plan
    assert [chunk['chunk_id'] for chunk in plan['chunks']] == ['map-1', 'map-2']
    assert [chunk['source_refs'] for chunk in plan['chunks']] == [['w:p1', 'w:p3'], ['o:p1', 'o:p2']]
    assert [ref for chunk in plan['chunks'] for ref in chunk['span_refs']] == list(catalog['index'])
    assert plan['empty_sources'] == ['w:p2']
    for chunk in plan['chunks']:
        payload = evidence.evidence_payload(catalog, chunk['span_refs'])
        assert chunk['payload_hash'] == digest(payload)
        assert chunk['messages_hash'] == prepare(payload)['messages_hash']
        assert 'payload' not in chunk and 'text' not in chunk and 'messages' not in chunk


def test_capacity_split_prefers_whole_blocks_and_long_blocks_use_contiguous_frozen_spans():
    catalog = source(); callback = limited(2)
    plan = evidence.plan_evidence_reads(catalog, callback, 1000000)
    assert evidence.check_plan(plan, catalog, callback) == plan
    assert plan['chunks'][0]['span_refs'] == ['w:p1/s1', 'w:p1/s2']
    assert plan['chunks'][1]['source_refs'] == ['w:p3']
    assert all(len(chunk['span_refs']) <= 2 for chunk in plan['chunks'])
    assert [ref for chunk in plan['chunks'] for ref in chunk['span_refs']] == list(catalog['index'])


def test_reported_real_prepared_byte_capacity_also_splits_without_dropping_text():
    catalog = evidence.build_evidence_source('a' * 64, [('w:p1', '甲' * 240), ('w:p2', '乙' * 240)])
    one = prepare(evidence.evidence_payload(catalog, ['w:p1/s1']))['input_summary']['prepared_messages_bytes']
    plan = evidence.plan_evidence_reads(catalog, prepare, one)
    assert len(plan['chunks']) == 2
    assert evidence.check_plan(plan, catalog, prepare) is plan


def test_single_span_and_total_chunk_limits_reject_instead_of_truncating_or_adding_calls():
    catalog = source(); before = copy.deepcopy(catalog)
    with pytest.raises(evidence.EvidenceCapacityExceeded):
        evidence.plan_evidence_reads(catalog, limited(0), 1000000)
    with pytest.raises(evidence.EvidenceCapacityExceeded):
        evidence.plan_evidence_reads(catalog, limited(1), 1000000, max_chunks=2)
    assert catalog == before


@pytest.mark.parametrize('maximum', [0, True, 25])
def test_chunk_count_is_bounded_before_any_prepare(maximum):
    def forbidden(payload):
        raise AssertionError('invalid plan policy cannot prepare messages')
    with pytest.raises(ValueError):
        evidence.plan_evidence_reads(source(), forbidden, 1000000, max_chunks=maximum)


@pytest.mark.parametrize('error', [ValueError('not capacity'), RuntimeError('program failure'), TypeError('bad schema')])
def test_arbitrary_prepare_error_propagates_once_without_hidden_split(error):
    calls = []
    def broken(payload):
        calls.append(payload); raise error
    with pytest.raises(type(error)) as caught:
        evidence.plan_evidence_reads(source(), broken, 1000000)
    assert caught.value is error and len(calls) == 1


def test_empty_sources_are_covered_but_never_claimed_as_model_reads():
    catalog = evidence.build_evidence_source('a' * 64, [('w:p1', ''), ('o:p1', '')])
    def forbidden(payload):
        raise AssertionError('empty source must not fabricate a read')
    plan = evidence.plan_evidence_reads(catalog, forbidden, 1000000)
    assert plan['chunks'] == [] and plan['empty_sources'] == ['w:p1', 'o:p1']
    assert evidence.check_plan(plan, catalog, forbidden) is plan


@pytest.mark.parametrize('change', ['drop', 'duplicate', 'reorder', 'message', 'summary', 'empty', 'payload'])
def test_outer_rehash_cannot_hide_coverage_or_preparation_change(change):
    catalog = source(); plan = evidence.plan_evidence_reads(catalog, prepare, 1000000)
    if change == 'drop': plan['chunks'].pop()
    if change == 'duplicate': plan['chunks'][0]['span_refs'].append(plan['chunks'][0]['span_refs'][0])
    if change == 'reorder': plan['chunks'][0]['span_refs'].reverse()
    if change == 'message': plan['chunks'][0]['messages_hash'] = 'b' * 64
    if change == 'summary': plan['chunks'][0]['input_summary']['token_bound'] += 1
    if change == 'empty': plan['empty_sources'] = []
    if change == 'payload': plan['chunks'][0]['payload_hash'] = 'b' * 64
    reseal_plan(plan)
    with pytest.raises(ValueError):
        evidence.check_plan(plan, catalog, prepare)


def test_database_object_key_reordering_preserves_exact_plan_and_resolver():
    catalog = source(); plan = evidence.plan_evidence_reads(catalog, prepare, 1000000)
    def reversed_keys(value):
        if isinstance(value, dict):
            return {key: reversed_keys(item) for key, item in reversed(list(value.items()))}
        return [reversed_keys(item) for item in value] if isinstance(value, list) else value
    reordered = reversed_keys(catalog)
    assert evidence.check_evidence_source(reordered) == catalog
    assert evidence.check_plan(reversed_keys(plan), reordered, prepare) == plan


def map_result():
    return {'schema': 'report-evidence-map-v1', 'claims': [{'key': 'claim_1', 'kind': '职责',
        'statement': '这是一条需要独立语义核对的声明。', 'span_refs': ['w:p1/s1'],
        'status': 'explicit', 'explanation': ''}]}


def test_map_output_bound_is_80_and_empty_claims_do_not_fabricate_fact_coverage():
    assert evidence.MapResult.model_json_schema()['properties']['claims']['maxItems'] == 80
    empty = {'schema': 'report-evidence-map-v1', 'claims': []}
    assert evidence.validate_map_result(empty, source(), ['w:p1/s1']) == empty
    value = map_result()
    value['claims'] = [{**copy.deepcopy(value['claims'][0]), 'key': f'claim_{n}'} for n in range(81)]
    with pytest.raises(ValidationError):
        evidence.validate_map_result(value, source(), ['w:p1/s1'])


@pytest.mark.parametrize('kind', ['职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他'])
def test_map_claim_kinds_preserve_model_statement_and_derive_only_evidence_text(kind):
    catalog = source(); value = map_result(); value['claims'][0]['kind'] = kind
    before = copy.deepcopy(value)
    assert evidence.validate_map_result(value, catalog, ['w:p1/s1']) == value == before
    resolved = evidence.resolve_span(catalog, 'w:p1/s1', ['w:p1/s1'])
    assert resolved['text'] != value['claims'][0]['statement']
    assert 'quote' not in value['claims'][0]  # literal resolution is not model semantic correctness.


@pytest.mark.parametrize('status', ['inferred', 'unknown'])
def test_uncertainty_requires_explanation_without_fabricating_a_citation(status):
    value = map_result(); claim = value['claims'][0]
    claim.update(status=status, span_refs=[], explanation='该项原文支持不足，仍待核对。')
    assert evidence.validate_map_result(value, source(), ['w:p1/s1']) == value
    claim['explanation'] = ' '
    with pytest.raises(ValidationError):
        evidence.validate_map_result(value, source(), ['w:p1/s1'])


@pytest.mark.parametrize('change', ['outside_batch', 'unknown_ref', 'duplicate_ref', 'empty_explicit', 'duplicate_key',
    'quote_field', 'bad_kind', 'blank', 'nul', 'surrogate', 'nonfinite'])
def test_invalid_claim_is_rejected_not_normalized_or_repaired(change):
    value = map_result(); claim = value['claims'][0]
    if change == 'outside_batch': claim['span_refs'] = ['o:p1/s1']
    if change == 'unknown_ref': claim['span_refs'] = ['w:p99/s1']
    if change == 'duplicate_ref': claim['span_refs'] *= 2
    if change == 'empty_explicit': claim['span_refs'] = []
    if change == 'duplicate_key': value['claims'].append(copy.deepcopy(claim))
    if change == 'quote_field': claim['quote'] = '模型任意改写不应被resolver修成通过。'
    if change == 'bad_kind': claim['kind'] = '执行权限'
    if change == 'blank': claim['statement'] = '  '
    if change == 'nul': claim['statement'] = '\x00'
    if change == 'surrogate': claim['statement'] = '\ud800'
    if change == 'nonfinite': claim['explanation'] = float('inf')
    original = copy.deepcopy(value)
    with pytest.raises(ValueError):
        evidence.validate_map_result(value, source(), ['w:p1/s1'])
    assert value == original
