"""Actual pure planner/gateway preparation; no provider, DB or quality proof."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_claim_enum_native_v172 as native
from generic_agents import report_evidence_enum_map_v172 as preparation
from generic_agents import report_evidence_map_preparation_v168 as old
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import EvidenceCapacityExceeded, PLAN_V2, build_evidence_source
from generic_agents.report_evidence_wire_v3 import MapResultWireV3
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_evidence_map_preparation_v168 import cap as old_cap


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure planning cannot call a provider or subprocess')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def cap():
    result = old_cap()
    result.data['execution']['report_evidence_output'].update(schema='report-evidence-output-v172',
        map_transport=native.DELIVERY, reduce_transport=native.DELIVERY)
    return result


def catalog():
    return build_evidence_source('a' * 64, [('w:p1', '仅在条件满足时登记；尚不具备另一能力。'),
        ('w:p2', ''), ('w:p3', '合成总数与分项须保留原始引用，不替原文选边。'),
        ('o:p1', '同源OCR独立读取组；不可借用Word组引用。')])


def build(source=None, model=None, context=None):
    return preparation.build_map_preparation(catalog() if source is None else source,
        cap() if model is None else model, {'count': 1.0, 'zero': -0.0} if context is None else context,
        '保留条件与否定；只供人工核对，不批准执行。')


def reseal(value):
    for chunk in value['plan']['chunks']:
        chunk['chunk_hash'] = digest({k: v for k, v in chunk.items() if k != 'chunk_hash'})
    value['plan']['plan_hash'] = digest({k: v for k, v in value['plan'].items() if k != 'plan_hash'})
    value['preparation_hash'] = digest({k: v for k, v in value.items() if k != 'preparation_hash'})
    return value


def enum(schema):
    return schema['$defs']['MapClaimWireV3']['properties']['span_refs']['items']['enum']


def test_full_plan_freezes_each_actual_enum_schema_proof_options_and_http_hash():
    source, model = catalog(), cap(); before = copy.deepcopy((source, model.data))
    value = build(source, model)
    spec = value['generation_contract']
    assert value['schema'] == preparation.PREPARATION
    assert preparation.applies(value) and preparation.applies(spec)
    assert spec['schema'] == 'report-claim-enum-map-generation-v172'
    assert spec['base_schema'] == MapResultWireV3.model_json_schema()
    assert spec['schema_policy'] == native.SCHEMA_POLICY
    assert not {'output_schema', 'schema_proof', 'dispatch_options'} & set(spec)
    assert value['plan']['schema'] == PLAN_V2 and value['plan']['empty_sources'] == ['w:p2']
    assert [ref for chunk in value['plan']['chunks'] for ref in chunk['span_refs']] == list(source['index'])
    assert value['can_apply'] is value['can_publish'] is False
    assert preparation.check_map_preparation(value, source, model) == value
    assert len(value['plan']['chunks']) == 2
    proofs = []
    for chunk in value['plan']['chunks']:
        actual = preparation.stage_spec(value, chunk['chunk_id'])
        assert actual['allowed_span_refs'] == enum(actual['output_schema']) == chunk['span_refs']
        assert actual['allowed_span_refs_hash'] == digest(chunk['span_refs'])
        assert actual['schema_proof']['provider_schema'] == actual['output_schema']
        assert actual['dispatch_options']['response_format']['json_schema']['schema'] == actual['output_schema']
        assert native.check_schema_proof(actual['schema_proof'], native.map_schema(chunk['span_refs']), 'map-v172') == actual['schema_proof']
        request = preparation.rebuild_map_request(value, source, model, chunk['chunk_id'])
        assert request['request_body'] == canonical_json(request['request']).encode('utf-8')
        assert request['request_hash'] == hashlib.sha256(request['request_body']).hexdigest() == chunk['request_hash']
        assert request['messages_hash'] == digest(request['messages']) == chunk['messages_hash']
        assert request['request_hash'] != request['messages_hash']
        assert request['input_summary']['prepared_request_bytes'] == len(request['request_body']) <= 100000
        assert request['request']['response_format']['json_schema']['schema'] == actual['output_schema']
        payload = json.loads(request['messages'][2]['content'])
        assert payload['generation'] == preparation.GENERATION and payload['task_id'] == preparation.TASK_ID
        assert payload['spans'] == [{'span_ref': ref, 'text': source['index'][ref]['text']} for ref in chunk['span_refs']]
        proofs.append(actual['schema_proof']['proof_hash'])
    assert len(set(proofs)) == 2
    assert (source, model.data) == before


def test_real_catalog_neighbor_and_nonexistent_subspan_are_both_outside_current_schema():
    source, value = catalog(), build()
    assert 'w:p1/s1' in source['index'] and 'w:p1/s2' not in source['index']
    first, other = value['plan']['chunks']
    neighbor = other['span_refs'][0]
    assert neighbor in source['index'] and neighbor not in first['span_refs']
    schema = preparation.stage_spec(value, first['chunk_id'])['output_schema']
    for ref in ('w:p1/s2', neighbor):
        wire = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '来源', 'statement': '公共合成声明。',
            'span_refs': [ref], 'status': 'explicit', 'explanation': ''}]}
        assert MapResultWireV3.model_validate(wire).model_dump() == wire
        assert any(error.validator == 'enum' for error in Draft202012Validator(schema).iter_errors(wire))
        assert wire['claims'][0]['span_refs'] == [ref]


@pytest.mark.parametrize('fault', ['drop_ref', 'add_neighbor', 'replace_subspan', 'reverse_refs',
    'context_float_kind', 'context_zero_sign', 'task', 'schema_policy', 'static_dispatch',
    'base_schema', 'request_hash', 'summary', 'can_apply'])
def test_cold_replan_rejects_resealed_scope_generation_and_typed_changes(fault):
    source, model = catalog(), cap(); value = build(source, model)
    chunk = value['plan']['chunks'][0]; spec = value['generation_contract']
    if fault == 'drop_ref': chunk['span_refs'].pop()
    elif fault == 'add_neighbor': chunk['span_refs'].append(value['plan']['chunks'][1]['span_refs'][0])
    elif fault == 'replace_subspan': chunk['span_refs'][0] = 'w:p1/s2'
    elif fault == 'reverse_refs': chunk['span_refs'].reverse()
    elif fault == 'context_float_kind': value['context']['count'] = 1
    elif fault == 'context_zero_sign': value['context']['zero'] = 0.0
    elif fault == 'task': spec['task'] += '绕过原来源约束'
    elif fault == 'schema_policy': spec['schema_policy'] = 'caller_supplied'
    elif fault == 'static_dispatch': spec['dispatch_options'] = copy.deepcopy(spec['base_options'])
    elif fault == 'base_schema': spec['base_schema']['properties']['claims']['maxItems'] = 81
    elif fault == 'request_hash': chunk['request_hash'] = '0' * 64
    elif fault == 'summary': chunk['input_summary']['prepared_request_bytes'] += 1
    else: value['can_apply'] = True
    reseal(value)
    with pytest.raises(ValueError): preparation.check_map_preparation(value, source, model)


def test_stage_spec_rejects_static_base_and_detaches_dynamic_objects():
    value = build(); chunk = value['plan']['chunks'][0]
    actual = preparation.stage_spec(value, chunk['chunk_id'])
    actual['allowed_span_refs'].clear(); enum(actual['output_schema']).clear()
    actual['schema_proof']['provider_schema'].clear(); actual['dispatch_options'].clear()
    again = preparation.stage_spec(value, chunk['chunk_id'])
    assert enum(again['output_schema']) == chunk['span_refs']
    assert again['schema_proof']['provider_schema'] == again['output_schema']
    forged = copy.deepcopy(value)
    forged['generation_contract']['output_schema'] = MapResultWireV3.model_json_schema()
    reseal(forged)
    with pytest.raises(ValueError): preparation.stage_spec(forged, chunk['chunk_id'])
    for missing in ('map-0', 'map-3', '', None):
        with pytest.raises(ValueError): preparation.stage_spec(value, missing)


def test_old_capability_and_saved_preparation_are_not_relabelled_as_new():
    source, legacy = catalog(), old_cap()
    old_value = old.build_map_preparation(source, legacy, {})
    saved = copy.deepcopy(old_value)
    with pytest.raises(ValueError): build(source, legacy)
    with pytest.raises(ValueError): preparation.check_map_preparation(old_value, source, cap())
    assert not preparation.applies(old_value)
    assert old_value == saved and old.check_map_preparation(old_value, source, legacy) == saved


def test_capacity_uses_actual_enum_schema_and_splits_complete_source_without_truncation():
    # One whole view exceeds the actual body limit; real planner splits only at
    # frozen span/source boundaries. No prepare callback or validator is replaced.
    source = build_evidence_source('b' * 64, [(f'w:p{i}', '公开容量材料。' * 300) for i in range(1, 24)])
    value = build(source)
    assert len(value['plan']['chunks']) > 1
    assert [ref for chunk in value['plan']['chunks'] for ref in chunk['span_refs']] == list(source['index'])
    for chunk in value['plan']['chunks']:
        request = preparation.rebuild_map_request(value, source, cap(), chunk['chunk_id'])
        assert len(request['request_body']) <= 100000
        assert enum(request['request']['response_format']['json_schema']['schema']) == chunk['span_refs']
    with pytest.raises(EvidenceCapacityExceeded):
        build(catalog(), context={'untrusted': '中' * 40000})


def test_empty_valid_claim_array_is_still_allowed_but_empty_source_is_not_prepared():
    value = build(); schema = preparation.stage_spec(value, value['plan']['chunks'][0]['chunk_id'])['output_schema']
    assert list(Draft202012Validator(schema).iter_errors({'schema': 'report-evidence-map-v3', 'claims': []})) == []
    empty = build_evidence_source('c' * 64, [('w:p1', '')])
    with pytest.raises(ValueError): build(empty)
