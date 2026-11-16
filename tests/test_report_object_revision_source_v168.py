"""Public synthetic source-dispatch units, not API/PG or real-report evidence.

Fake DB selection, row-authority/material checks and the saved-reduction reader
are explicit boundary spies. CandidateObjects, the V168 manual adapter and the
complete V166 semantic validator remain real. Root alone executes these tests.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import report_full_semantic_handoff as handoff
from generic_agents import report_object_revision_source_v166 as source
from generic_agents import report_saved_map_review_source_v178 as selected_source
from generic_agents import report_evidence_pipeline_v168 as native
from generic_agents.report_object_semantics_v166 import CandidateObjects, CANDIDATE, SCHEMA
from tests.test_report_evidence_semantics_pure import catalog, CONTEXT
from tests.test_report_evidence_wire_v3_pure import candidate_wire


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic source adapter units cannot perform external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(monkeypatch):
    sources = catalog()
    candidate = candidate_wire(sources)
    candidate['schema'] = CANDIDATE
    for obj in candidate['objects']:
        obj['business_identity'] = {'status': 'not_established', 'field_labels': [],
            'citations': [], 'explanation': '合成候选尚未确立业务标识，不能认定原文没有标识。'}
        for field in obj['fields']:
            field['required'] = False
    candidate['objects'][1]['fields'] = [candidate['objects'][1]['fields'][1]]
    assert CandidateObjects.model_validate(candidate).model_dump() == candidate
    row = NS(id='16800000-0000-4000-8000-000000000001', version=7, status='review', data={
        'preview': {'source_hash': sources['descriptor']['source_hash'],
            'map_preparation': {'generation_contract': {
                'schema': 'report-claim-array-map-generation-v168'},
                'context': copy.deepcopy(CONTEXT)}},
        'model_result_hash': digest(candidate), 'review_value': None,
        'unrelated_saved_marker': {'note': '既有保存内容保持原样', 'revision': 3}})
    p = NS(user_id='synthetic-actor', workspace_id='synthetic-workspace')
    calls = []

    class DB:
        # No commit, flush, add or other write surface exists in this unit seam.
        def scalar(self, query):
            calls.append(('select', query))
            return row
    db = DB()
    def no_selected_source(actual_db, actual_p, report_id, lock=False):
        # Explicit legacy source boundary: these tests exercise original V168
        # candidates, not an independently selected saved-Map consumer.
        assert actual_db is db and actual_p is p and report_id == row.id and type(lock) is bool
        return None
    monkeypatch.setattr(selected_source, 'load_selected_source', no_selected_source)

    def check_row(actual, actor):
        assert actual is row and actor is p
        calls.append(('check_row_boundary',))

    def read_reduction(actual, actual_catalog):
        assert actual is row and actual_catalog is sources
        calls.append(('saved_reduction_boundary',))
        return {'result': copy.deepcopy(candidate)}

    def material_refs(db, actor, actual, lock=False):
        assert actor is p and actual is row
        calls.append(('material_refs_boundary', lock))

    def old_path(*args, **kwargs):
        pytest.fail('The new family must not convert through an old manual/result contract')

    monkeypatch.setattr(source.pipeline, 'check_row', check_row)
    monkeypatch.setattr(source.pipeline, '_catalog', lambda preview: sources)
    monkeypatch.setattr(source.pipeline, '_reduction_result', read_reduction)
    monkeypatch.setattr(handoff, 'material_refs', material_refs)
    for name in ('manual_from_projection', 'manual_from_projection_v6', 'new_from_value'):
        monkeypatch.setattr(source, name, old_path)
    return db, p, row, candidate, sources, calls


def test_new_family_uses_real_candidate_adapter_without_old_required_field_conversion(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    before = copy.deepcopy((row.data, candidate))
    actual_adapter = native.manual_value_from_candidate
    adapter_calls = []

    def tracked_adapter(value, actual_catalog, refs, context):
        adapter_calls.append(copy.deepcopy((value, refs, context)))
        assert actual_catalog is sources
        return actual_adapter(value, actual_catalog, refs, context)

    monkeypatch.setattr(native, 'manual_value_from_candidate', tracked_adapter)
    result = source.load_source(db, p, row.id)
    assert len(adapter_calls) == 1 and adapter_calls[0][0] == candidate
    assert result['value']['schema'] == SCHEMA
    assert result['value']['result'] == candidate
    assert result['value']['note'] == ''
    assert all(field['required'] is False for obj in result['value']['result']['objects']
        for field in obj['fields'])
    assert [field['kind'] for field in result['value']['result']['objects'][1]['fields']] == ['整数']
    assert all(obj['business_identity']['status'] == 'not_established'
        and obj['business_identity']['field_labels'] == []
        for obj in result['value']['result']['objects'])
    assert result['value']['object_identity_confirmations'] == [
        {'object_name': obj['name'], 'acknowledge_platform_identity': False}
        for obj in candidate['objects']]
    assert (row.data, candidate) == before


def test_new_family_preserves_exact_machine_hash_and_complete_basis_with_share_lock(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    before = copy.deepcopy(row.data)
    result = source.load_source(db, p, row.id, lock=True)
    expected_base = native.manual_value_from_candidate(candidate, sources,
        list(sources['index']), copy.deepcopy(CONTEXT))
    expected_basis = {'report_id': row.id, 'report_revision': row.version,
        'model_result_hash': digest(candidate),
        'source_hash': sources['descriptor']['source_hash'],
        'catalog_hash': sources['descriptor']['catalog_hash'],
        'context_hash': digest(CONTEXT), 'base_value_hash': digest(expected_base)}
    assert result['basis'] == expected_basis
    assert result['basis_hash'] == digest(expected_basis)
    assert result['value'] == expected_base
    assert result['allowed_span_refs'] == list(sources['index'])
    assert calls[0][1]._for_update_arg.read is True
    assert ('material_refs_boundary', True) in calls
    assert row.data == before and row.data['review_value'] is None


def test_new_family_returns_detached_human_value_without_rewriting_saved_machine_candidate(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    before = copy.deepcopy((row.data, candidate))
    result = source.load_source(db, p, row.id)
    result['value']['result']['objects'][0]['fields'][0]['citations'].clear()
    result['value']['object_identity_confirmations'][0]['acknowledge_platform_identity'] = True
    result['value']['note'] = '仅修改返回的独立人工值'
    assert (row.data, candidate) == before
    assert row.data['model_result_hash'] == digest(candidate)


def test_new_family_without_complete_machine_hash_does_not_read_or_invent_candidate(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    row.status = 'needs_clarification'
    row.data['model_result_hash'] = None
    before = copy.deepcopy(row.data)
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '未自动重跑' in error.value.detail
    assert not any(item[0] in {'saved_reduction_boundary', 'material_refs_boundary'} for item in calls)
    assert row.data == before


def test_new_family_rejects_existing_old_review_value_without_lending_it_authority(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    row.data['review_value'] = {'schema': 'report-evidence-manual-review-v5',
        'result': candidate_wire(sources), 'note': '旧人工内容不能转借给新生成合同'}
    before = copy.deepcopy(row.data)
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '不能借用旧人工稿合同' in error.value.detail
    assert not any(item[0] == 'saved_reduction_boundary' for item in calls)
    assert row.data == before


def test_new_family_rejects_candidate_hash_mismatch_and_retains_entire_original_row(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    row.data['model_result_hash'] = '0' * 64
    before = copy.deepcopy((row.data, candidate))
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '摘要不一致' in error.value.detail
    assert (row.data, candidate) == before


def test_new_family_rejects_rehashed_invalid_candidate_without_replacing_original_content(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    candidate['objects'][0]['business_identity']['unique'] = True
    row.data['model_result_hash'] = digest(candidate)
    before = copy.deepcopy((row.data, candidate))
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '不能无损转换' in error.value.detail
    assert (row.data, candidate) == before


def test_new_family_keeps_real_source_scope_validation_after_candidate_hash_check(monkeypatch):
    db, p, row, candidate, sources, calls = scene(monkeypatch)
    candidate['catalog_hash'] = 'f' * 64
    assert CandidateObjects.model_validate(candidate).model_dump() == candidate
    row.data['model_result_hash'] = digest(candidate)
    before = copy.deepcopy((row.data, candidate))
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '不能无损转换' in error.value.detail
    assert (row.data, candidate) == before
