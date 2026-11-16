"""Full five-field TXT projection, explicit capacity and closed original JSON."""
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import layered_case_uses_v210 as uses, layered_case_use_origin_v210 as origin
from generic_agents import runtime_task_documents as sources
from tests.test_layered_cases_v209 import value


def reference():
    return {'id': str(uuid4()), 'version': 3, 'revision_id': str(uuid4()), 'revision_hash': 'a' * 64,
        'source_fingerprint': 'b' * 64, 'business_name': '通用资料核对',
        'author': {'id': str(uuid4()), 'name': '真实原作者'}, 'value': value()}


def selection():
    return {'expected_task_version': 2, 'expected_approval_version': 2, 'case_id': str(uuid4()),
        'expected_case_version': 3, 'revision_id': str(uuid4()), 'revision_hash': 'a' * 64,
        'source_fingerprint': 'b' * 64, 'applicable_reason': '对照本任务来源、完整经验和限制，不当作当前事实'}


def test_full_values_and_newlines_have_real_multiple_native_blocks_and_codepoint_spans():
    ref = reference(); ref['value']['experience'] = '甲' * 1201 + '😀\n第二段完整经验'
    target = {'business_name': '另一业务'}
    raw = uses.reference_bytes(ref, target, '说明适用差异')
    for text in ref['value'].values(): assert text.encode() in raw
    blocks = uses.predicted_blocks(raw)
    assert len(blocks) > 5 and any(item['text'] == '第二段完整经验' for item in blocks)
    limit, selectors, entries = uses.capacity(raw, blocks, uses.file_name(ref))
    assert any(item['char_span'] == {'start': 1200, 'end': 1202} for item in selectors)
    assert all(0 < row['char_span']['end'] - row['char_span']['start'] <= 1200 for row in selectors)
    assert uses.rendered_bytes(entries) == sources.render(entries)
    assert limit['packet_bytes'] == len(sources.render(entries))
    assert uses.capacity_reason(limit) is None
    for block in blocks:
        chunks = [entry['text'] for entry in entries if entry['selector']['block_id'] == block['id']]
        assert ''.join(chunks) == block['text']


def test_capacity_reports_full_case_overflow_without_summarizing_or_dropping_caveats():
    ref = reference(); ref['value']['experience'] = '\n'.join('分段完整经验' for _ in range(25))
    raw = uses.reference_bytes(ref, {'business_name': '目标'}, '适用说明')
    limit, selectors, _ = uses.capacity(raw, uses.predicted_blocks(raw), uses.file_name(ref))
    assert limit['selector_count'] > 20 and '20' in uses.capacity_reason(limit)
    assert ref['value']['caveats'].encode() in raw and len(selectors) == limit['selector_count']
    assert uses.capacity_reason({**limit, 'selector_count': 20, 'packet_bytes': 20001})


@pytest.mark.parametrize('mutation', ['bool_version', 'float_version', 'extra', 'bad_uuid', 'bad_sha', 'blank_reason', 'nul_reason', 'long_reason'])
def test_selection_rejects_unclosed_or_coerced_input(mutation):
    body = selection()
    if mutation == 'bool_version': body['expected_task_version'] = True
    if mutation == 'float_version': body['expected_approval_version'] = 2.0
    if mutation == 'extra': body['auto_apply'] = True
    if mutation == 'bad_uuid': body['case_id'] = 'case_other'
    if mutation == 'bad_sha': body['revision_hash'] = 'A' * 64
    if mutation == 'blank_reason': body['applicable_reason'] = ' '
    if mutation == 'nul_reason': body['applicable_reason'] = '\x00'
    if mutation == 'long_reason': body['applicable_reason'] = '理' * 2001
    with pytest.raises(HTTPException) as caught: uses.parse(json.dumps(body).encode(), 'preview')
    assert caught.value.status_code == 422


@pytest.mark.parametrize('confirmation', [False, 1, 'true', None])
def test_create_requires_explicit_boundary_confirmation(confirmation):
    body = {**selection(), 'request_id': str(uuid4()), 'preview_hash': 'c' * 64, 'boundary_confirmed': confirmation}
    with pytest.raises(HTTPException): uses.parse(json.dumps(body).encode(), 'create')


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'])
def test_bad_original_json_is_not_normalized(raw):
    with pytest.raises(HTTPException): uses.parse(raw, 'create')


def test_feedback_activation_is_graph_identity_and_must_be_unique():
    body = {'request_id': str(uuid4()), 'expected_use_version': 1, 'reason': '引用目标独立人工核对结果',
        'consumption_fingerprint': 'a' * 64, 'acceptance_request_id': str(uuid4()), 'basis_hash': 'b' * 64,
        'evaluation': 'uncertain', 'human_activation_ids': ['activation_graph_human_1']}
    assert uses.parse(json.dumps(body).encode(), 'feedback')[0].human_activation_ids == body['human_activation_ids']
    body['human_activation_ids'] *= 2
    with pytest.raises(HTTPException): uses.parse(json.dumps(body).encode(), 'feedback')


def test_walk_rejects_cycles_and_depth_without_leaking_stack():
    walk = origin.Walk()
    with walk.entering('a'):
        with pytest.raises(HTTPException):
            with walk.entering('a'): pass
        assert walk.stack == ['a']
    assert walk.stack == []
    def descend(depth):
        with walk.entering(str(depth)):
            if depth < 17: descend(depth + 1)
    with pytest.raises(HTTPException): descend(1)
    assert walk.stack == []


def test_walk_node_capacity_does_not_treat_seen_as_verified():
    walk = origin.Walk()
    for number in range(128):
        with walk.entering(str(number)): pass
    with pytest.raises(HTTPException):
        with walk.entering('129'): pass
    with walk.entering('1'): assert not walk.memo
