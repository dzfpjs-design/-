"""Actual diagnostic/strong gates over public synthetic flow/source examples.

These are not real report, provider, IAM, persistence or business-quality tests.
Failed values are preserved, never repaired into a successfully adopted result.
"""
import copy
import json
import socket
import subprocess

import pytest

from generic_agents import business_flow_review_diagnostics as subject
from generic_agents import report_business_flow_wire_v182 as wire
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v182 import scene, parallel_scene


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure diagnostics cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value): return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
def codes(value): return {item['code'] for item in value['issues']}


def no_authority(result):
    assert result['schema'] == 'business-flow-review-diagnostics-v1'
    assert all(result[key] is False for key in ('semantic_quality_verified', 'can_apply', 'can_publish', 'can_execute'))
    assert all(set(item) == {'code', 'path', 'message', 'span_ref'} for item in result['issues'])


def test_complete_flow_passes_actual_gates_without_semantic_or_execution_authority_and_returns_detached_source_text():
    value, inputs = scene(208); before = canonical_json([value, inputs])
    result = subject.review(value, **inputs); no_authority(result)
    assert set(result) == {'schema', 'available', 'value', 'issues', 'source_evidence',
        'structural_valid', 'source_valid', 'semantic_quality_verified', 'can_apply', 'can_publish', 'can_execute'}
    assert result['available'] is result['structural_valid'] is result['source_valid'] is True
    assert result['value'] == value and result['value'] is not value and result['issues'] == []
    assert result['source_evidence'] == [{'span_ref': ref, 'source_text': inputs['catalog']['index'][ref]['text'],
        'text_origin': 'platform_resolved_source'} for ref in inputs['allowed_span_refs']]
    assert wire.build_projection(raw(value), **inputs)['wire'] == value
    result['value']['business']['name'] = 'local editor change'
    result['source_evidence'][0]['source_text'] = 'local display change'
    assert canonical_json([value, inputs]) == before


def test_truncated_quote_is_located_with_the_actual_platform_full_text_and_original_answer_is_not_repaired():
    value, inputs = scene(); value['evidence'][0]['quote'] = value['evidence'][0]['quote'][:-1]
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is result['structural_valid'] is True and result['source_valid'] is False
    issue = next(item for item in result['issues'] if item['code'] == 'quote_mismatch')
    assert issue['path'] == '/evidence/0/quote' and issue['span_ref'] == value['evidence'][0]['span_ref']
    original = next(item for item in result['source_evidence'] if item['span_ref'] == issue['span_ref'])
    assert original['source_text'] == inputs['catalog']['index'][issue['span_ref']]['text']
    assert original['source_text'] != result['value']['evidence'][0]['quote']
    assert canonical_json(result['value']) == before == canonical_json(value)
    with pytest.raises(ValueError): wire.build_projection(raw(value), **inputs)


@pytest.mark.parametrize('fault,path', [('empty', '/business'), ('missing_business', '/business'),
    ('role_type', '/business/flows/0/nodes/0/role'), ('short_output', '/business/flows/0/nodes/0/outputs/0'),
    ('unknown_actor', '/business/flows/0/nodes/0/actor'),
    ('missing_edge_origin', '/business/flows/0/edges/0/from_node'), ('extra', '/private_note')])
def test_safe_incomplete_or_bad_shape_draft_is_preserved_with_actual_pydantic_json_pointer(fault, path):
    value, inputs = scene()
    if fault == 'empty': value = {}
    elif fault == 'missing_business': value.pop('business')
    elif fault == 'role_type': value['business']['flows'][0]['nodes'][0]['role'] = 1
    elif fault == 'short_output': value['business']['flows'][0]['nodes'][0]['outputs'] = ['物']
    elif fault == 'unknown_actor': value['business']['flows'][0]['nodes'][0]['actor'] = 'fictional_agent'
    elif fault == 'missing_edge_origin':
        flow = value['business']['flows'][0]
        flow['edges'] = [{'kind': 'sequence', 'to_node': 'review', 'attribution': copy.deepcopy(flow['trigger']['attribution'])}]
    else: value['private_note'] = 'should remain in unfinished draft, never become a valid flow'
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is True and result['structural_valid'] is result['source_valid'] is False
    assert result['source_evidence'] and any(item['code'] == 'shape' and item['path'] == path for item in result['issues'])
    assert canonical_json(result['value']) == before == canonical_json(value)


@pytest.mark.parametrize('fault', ['duplicate_id', 'missing_target', 'missing_start', 'missing_end',
    'unreachable', 'cannot_reach_end', 'end_has_outgoing', 'deliverable_output_missing',
    'invalid_parallel_group', 'duplicate_citation'])
def test_graph_failures_have_actionable_codes_and_still_fail_the_original_graph_gate(fault):
    value, inputs = parallel_scene(); flow = value['business']['flows'][0]
    if fault == 'duplicate_id': flow['nodes'][1]['node_id'] = flow['nodes'][0]['node_id']
    elif fault == 'missing_target': flow['edges'][0]['to_node'] = 'missing'
    elif fault == 'missing_start': flow['start_node'] = 'missing'
    elif fault == 'missing_end': flow['end_nodes'] = ['missing']
    elif fault == 'unreachable':
        node = copy.deepcopy(flow['nodes'][0]); node['node_id'] = 'orphan'; flow['nodes'].append(node)
    elif fault == 'cannot_reach_end': flow['edges'].pop()
    elif fault == 'end_has_outgoing':
        flow['edges'].append({'kind': 'sequence', 'from_node': 'review', 'to_node': 'archive',
            'attribution': copy.deepcopy(flow['nodes'][0]['attribution'])})
    elif fault == 'deliverable_output_missing': flow['nodes'][-1]['outputs'] = ['另一记录']
    elif fault == 'invalid_parallel_group': flow['edges'].pop(2)
    else: flow['nodes'][0]['attribution']['citations'] *= 2
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is True and result['structural_valid'] is False
    assert fault in codes(result) and 'graph_contract' in codes(result)
    assert all(item['path'].startswith('/business') or item['path'].startswith('/evidence') for item in result['issues'])
    assert canonical_json(result['value']) == before == canonical_json(value)
    with pytest.raises(ValueError): wire.parse_wire(raw(value), scope_binding=inputs['scope_binding'])


@pytest.mark.parametrize('fault', ['unknown_ref', 'missing_evidence', 'unused_evidence', 'duplicate_evidence'])
def test_evidence_table_and_citations_cannot_borrow_or_omit_or_duplicate_original_references(fault):
    value, inputs = scene(); first = value['evidence'][0]['span_ref']
    other = next(ref for ref in inputs['allowed_span_refs'] if ref != first)
    if fault == 'unknown_ref':
        value['evidence'][0]['span_ref'] = 'w:p1/s999'
        for attribution in wire._attributions(value): attribution['citations'] = ['w:p1/s999']
    elif fault == 'missing_evidence': value['business']['flows'][0]['nodes'][0]['attribution']['citations'].append(other)
    elif fault == 'unused_evidence': value['evidence'].append({'span_ref': other, 'quote': inputs['catalog']['index'][other]['text']})
    else: value['evidence'].append(copy.deepcopy(value['evidence'][0]))
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is True and result['source_valid'] is False and fault in codes(result)
    assert all(item['span_ref'] in inputs['allowed_span_refs'] for item in result['source_evidence'])
    assert canonical_json(result['value']) == before == canonical_json(value)
    with pytest.raises(ValueError): wire.build_projection(raw(value), **inputs)


@pytest.mark.parametrize('fault', ['binding', 'context', 'claims', 'catalog', 'allowed_refs'])
def test_bad_cold_source_fails_closed_and_never_fills_source_text_from_the_answer(fault):
    value, inputs = scene()
    if fault == 'binding': inputs['scope_binding']['source_hash'] = '0' * 64
    elif fault == 'context': inputs['context']['extra'] = 'changed'
    elif fault == 'claims': inputs['input_claims'][0]['statement'] += ' changed'
    elif fault == 'catalog': inputs['catalog']['index'][inputs['allowed_span_refs'][0]]['text'] += ' changed'
    else: inputs['allowed_span_refs'].append('w:p1/s999')
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is result['structural_valid'] is result['source_valid'] is False
    assert result['source_evidence'] == [] and 'source_unavailable' in codes(result)
    assert canonical_json(result['value']) == before == canonical_json(value)


@pytest.mark.parametrize('fault', ['nan', 'infinity', 'nul', 'surrogate', 'cycle', 'tuple', 'nonstring_key', 'oversize'])
def test_non_json_or_unsafe_oversize_input_is_unavailable_not_a_repaired_draft(fault):
    value, inputs = scene()
    if fault == 'nan': value = {'value': float('nan')}
    elif fault == 'infinity': value = {'value': float('inf')}
    elif fault == 'nul': value = {'value': '\x00'}
    elif fault == 'surrogate': value = {'value': '\ud800'}
    elif fault == 'cycle': value = {}; value['self'] = value
    elif fault == 'tuple': value = {'value': ('not', 'json')}
    elif fault == 'nonstring_key': value = {1: 'not a JSON key'}
    else: value = {'value': 'x' * (subject.MAX_VALUE_BYTES + 1)}
    result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is result['structural_valid'] is result['source_valid'] is False
    assert result['value'] is None and result['source_evidence'] == []
    assert codes(result) <= {'unsafe_draft', 'draft_capacity'}
    if fault == 'cycle': assert value['self'] is value


@pytest.mark.parametrize('bound', ['issues', 'result'])
def test_capacity_never_turns_partial_diagnostics_into_a_complete_valid_result(monkeypatch, bound):
    value, inputs = scene()
    if bound == 'issues': value = {}; monkeypatch.setattr(subject, 'MAX_ISSUES', 1)
    else: monkeypatch.setattr(subject, 'MAX_RESULT_BYTES', 1)
    before = canonical_json(value); result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is result['structural_valid'] is result['source_valid'] is False
    assert result['source_evidence'] == [] and codes(result) == {'diagnostic_capacity'}
    assert canonical_json(result['value']) == before == canonical_json(value)


def test_unexpected_validator_failure_is_unavailable_and_does_not_leak_the_exception_or_claim_validity(monkeypatch):
    value, inputs = scene(); before = canonical_json(value)
    def failed(*args, **kwargs): raise RuntimeError('PRIVATE EXCEPTION SENTINEL')
    # Failure injection only, never a replacement which makes a validator pass.
    monkeypatch.setattr(wire, '_wire_value', failed)
    result = subject.review(value, **inputs); no_authority(result)
    assert result['available'] is result['structural_valid'] is result['source_valid'] is False
    assert codes(result) == {'diagnostic_unavailable'} and 'PRIVATE EXCEPTION' not in canonical_json(result)
    assert canonical_json(result['value']) == before == canonical_json(value)
