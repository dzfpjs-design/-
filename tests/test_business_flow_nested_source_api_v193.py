"""Real recursive source/diagnostics adapters over explicit public I/O fixtures.

No provider, authentic capture, IAM, PostgreSQL or browser acceptance is claimed.
Those boundaries are substituted; preparation, wire and diagnostic gates are real.
"""
import copy
import hashlib

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_review_source as source
from generic_agents import business_flow_review_api as api
from generic_agents import business_flow_review_store as store
from generic_agents import report_business_flow_wire_v193 as wire
from generic_agents import business_flow_nested_review_diagnostics_v193 as diagnostics
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_business_flow_review_api_pure import no_io, source_case
from tests.test_report_business_flow_protocol_v193 import prepared, authored
from tests.test_report_business_flow_protocol_v182 import response


def nested_case(monkeypatch):
    c = source_case(monkeypatch)
    original = prepared()
    c.prepared, c.original = original.value, original.source
    c.value = authored(original)
    c.projection = wire.build_projection(canonical_json(c.value).encode(),
        scope_binding=c.prepared['scope_binding'], catalog=c.original['catalog'],
        allowed_span_refs=c.prepared['allowed_span_refs'], context=c.original['context'],
        input_claims=c.prepared['input_claims'])
    c.row.producer_id = c.original['snapshot']['producer']['id']
    c.row.model_id = original.cap.id
    c.row.status = 'review'
    c.row.data = {'preview': {'preparation': copy.deepcopy(c.prepared),
        'source_context': copy.deepcopy(c.original)}, 'projection': copy.deepcopy(c.projection)}
    c.saved['body'] = response(canonical_json(c.value))
    return c


def test_actual_recursive_cold_projection_and_source_binding_never_flatten_or_repair(monkeypatch):
    c = nested_case(monkeypatch)
    before = copy.deepcopy((c.row.data, c.saved))
    loaded = source.load(c.db, c.p, c.row.id)
    binding = loaded['source_binding']
    assert binding['schema'] == 'business-flow-nested-review-source-v193'
    assert binding['value_schema'] == 'report-business-flow-nested-plan-v192'
    assert binding['generation_profile'] == 'flow-v193'
    assert store._source(binding, c.row.id) == binding
    assert binding['arguments_sha256'] == hashlib.sha256(canonical_json(c.value).encode()).hexdigest()
    assert loaded['baseline_value'] == c.value['plan']
    assert loaded['_nested_causal_projection'] == c.projection
    assert c.calls == ['original_inventory', 'read_original_no_recovery']
    diagnostic = source.diagnose(loaded, loaded['baseline_value'])
    assert diagnostic['available'] and diagnostic['structural_valid'] and diagnostic['source_valid']
    assert not diagnostic['issues']
    assert all(diagnostic[key] is False for key in source.FALSE_FLAGS)
    loaded['baseline_value']['business']['flows'][0]['name'] = '人工改名'
    assert (c.row.data, c.saved) == before


@pytest.mark.parametrize('fault', ['marker', 'missing_schema', 'extra_key', 'wrong_profile', 'legacy_marker'])
def test_new_source_family_is_closed_and_cannot_hide_under_legacy_marker(monkeypatch, fault):
    c = nested_case(monkeypatch)
    binding = source.load(c.db, c.p, c.row.id)['source_binding']
    if fault == 'marker': binding['value_schema'] = 'report-business-flow-wire-v182'
    if fault == 'missing_schema': del binding['value_schema']
    if fault == 'extra_key': binding['permission'] = True
    if fault == 'wrong_profile': binding['generation_profile'] = 'flow-v190'
    if fault == 'legacy_marker':
        binding['schema'] = 'business-flow-review-source-v1'; del binding['value_schema']
    with pytest.raises(HTTPException): store._source(binding, c.row.id)


def test_saved_recursive_edit_detail_keeps_actual_original_causality_and_does_not_inherit_it(monkeypatch):
    c = nested_case(monkeypatch); loaded = source.load(c.db, c.p, c.row.id)
    value = copy.deepcopy(loaded['baseline_value'])
    value['business']['flows'][0]['name'] = '人工修改的流程名称'
    saved = {'schema': store.VIEW, 'attempt_id': c.row.id, 'version': 1, 'value': value,
        'source_binding': copy.deepcopy(loaded['source_binding']),
        'baseline_value': copy.deepcopy(loaded['baseline_value']), 'note': '保留人的说明',
        'history': [], 'status': 'incomplete', **source.FALSE_FLAGS}
    monkeypatch.setattr(store, 'get', lambda *_: copy.deepcopy(saved))
    detail = api.detail(c.db, c.p, c.row.id)
    assert detail['value'] == value and detail['note'] == saved['note']
    assert detail['generation_causality']['schema'] == 'business-flow-nested-causality-view-v193'
    assert detail['generation_causality']['current_draft_matches_original'] is False
    assert detail['generation_causality']['original_candidate_only'] is True
    assert detail['source_available'] is True and detail['current_source_checked'] is False
    assert all(detail[key] is False for key in source.FALSE_FLAGS)
    with pytest.raises(HTTPException) as error:
        source.current_review_source(c.db, c.p, c.row.id, saved)
    assert error.value.status_code == 422 and '实际能力绑定' in error.value.detail


@pytest.mark.parametrize('fault', ['binding', 'unknown_nested_ref', 'missing_branch', 'wrong_role_type'])
def test_incomplete_recursive_draft_is_diagnosed_without_mutation_or_permission(monkeypatch, fault):
    c = nested_case(monkeypatch); loaded = source.load(c.db, c.p, c.row.id)
    value = copy.deepcopy(loaded['baseline_value'])
    if fault == 'binding': value['binding_hash'] = 'f' * 64
    elif fault == 'unknown_nested_ref':
        value['business']['flows'][0]['completion']['attribution']['citations'] = ['w:p999999/s1']
    elif fault == 'missing_branch': value['business']['flows'][0]['phases'] = [{'kind': 'decision'}]
    else: value['business']['flows'][0]['completion']['role'] = 42
    before = copy.deepcopy(value)
    result = source.diagnose(loaded, value)
    assert result['available'] is True and result['issues'] and result['value'] == before and value == before
    assert result['structural_valid'] is False or result['source_valid'] is False
    assert all(result[key] is False for key in source.FALSE_FLAGS)
    assert result['source_evidence']


def test_source_missing_saved_recursive_work_remains_readable_without_new_call(monkeypatch):
    c = nested_case(monkeypatch); loaded = source.load(c.db, c.p, c.row.id)
    saved = {'schema': store.VIEW, 'attempt_id': c.row.id, 'version': 2,
        'value': copy.deepcopy(loaded['baseline_value']), 'source_binding': loaded['source_binding'],
        'baseline_value': loaded['baseline_value'], 'note': '已保存，不可丢失',
        'history': [], 'status': 'incomplete', **source.FALSE_FLAGS}
    monkeypatch.setattr(store, 'get', lambda *_: copy.deepcopy(saved))
    def absent(*_): raise HTTPException(409, 'unavailable')
    monkeypatch.setattr(source, 'load', absent)
    detail = api.detail(c.db, c.p, c.row.id)
    assert detail['value'] == saved['value'] and detail['note'] == saved['note']
    assert detail['source_available'] is False and detail['saved'] is True
    assert all(detail[key] is False for key in source.FALSE_FLAGS)


def test_unsafe_diagnostic_is_unavailable_not_a_saved_repair():
    cycle = {}; cycle['cycle'] = cycle
    result = diagnostics.review(cycle, scope_binding={}, catalog={}, allowed_span_refs=[], context={}, input_claims=[])
    assert result['available'] is False and result['value'] is None
    assert all(result[key] is False for key in source.FALSE_FLAGS)
