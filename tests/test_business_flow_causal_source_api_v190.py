"""Actual V190 cold source compilation and human detail projection, pure only.

The public source_case explicitly substitutes original Attempt inventory and
saved HTTP reading. Store.get is an explicit read boundary for API detail.
Native inspection, V190/V189/V184/V182 checks, diagnostics and presentation
remain real. This proves no IAM, provider, authentic capture, PG or live HTTP.
"""
import copy
import hashlib

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_review_source as source
from generic_agents import business_flow_review_api as api
from generic_agents import business_flow_review_store as store
from generic_agents import report_business_flow_wire_v190 as wire
from generic_agents import report_business_flow_wire_v184 as old_wire
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_business_flow_review_api_pure import no_io, source_case
from tests.test_business_flow_component_source_v186 import component_case
from tests.test_report_business_flow_protocol_v190 import prepared, raw
from tests.test_report_business_flow_protocol_v187 import prepared as old_prepared
from tests.test_report_business_flow_protocol_v184 import answer as plan_answer
from tests.test_report_business_flow_protocol_v182 import response
from tests.test_report_business_flow_wire_v190 import envelope


def causal_case(monkeypatch, *, legacy=False):
    c = source_case(monkeypatch)
    prepared_case = old_prepared() if legacy else prepared()
    prep, original = prepared_case.value, prepared_case.source
    plan = plan_answer(prep)
    value = plan if legacy else envelope(plan, prep['scope_binding'])
    compiler = old_wire if legacy else wire
    projection = compiler.build_projection(raw(value), scope_binding=prep['scope_binding'],
        catalog=original['catalog'], allowed_span_refs=prep['allowed_span_refs'],
        context=original['context'], input_claims=prep['input_claims'])
    # Newly authored in-memory adapter data. It is not an admitted/stored paid
    # Attempt, and checked_attempt/read_response are explicitly fixture seams.
    c.row.producer_id = original['snapshot']['producer']['id']
    c.row.model_id = prepared_case.cap.id
    c.row.revision = 1
    c.row.status = 'review'
    c.row.data = {'preview': {'preparation': copy.deepcopy(prep),
        'source_context': copy.deepcopy(original)}, 'projection': copy.deepcopy(projection)}
    c.saved['body'] = response(canonical_json(value))
    c.prepared, c.original, c.value, c.projection = prep, original, value, projection
    return c


def load(c):
    return source.load(c.db, c.p, c.row.id)


def saved_view(c, loaded, *, changed=False):
    value = copy.deepcopy(loaded['baseline_value'])
    if changed:
        value['business']['flows'][0]['nodes'][0]['inputs'] = ['人工修订的输入资料']
    return {'schema': store.VIEW, 'id': c.row.id, 'attempt_id': c.row.id,
        'version': 1, 'status': 'incomplete', 'source_binding': copy.deepcopy(loaded['source_binding']),
        'baseline_value': copy.deepcopy(loaded['baseline_value']), 'value': value,
        'note': '应完整保留的私有人工说明', 'history': [], **source.FALSE_FLAGS}


def mock_saved(monkeypatch, c, saved):
    reads = []

    def get(db, principal, attempt_id):
        assert db is c.db and principal is c.p and attempt_id == c.row.id
        reads.append(attempt_id)
        return copy.deepcopy(saved)

    monkeypatch.setattr(store, 'get', get)
    return reads


def test_cold_source190_rebuilds_the_actual_projection_and_preserves_original_call_and_compiled_baseline(monkeypatch):
    c = causal_case(monkeypatch)
    before = (canonical_json(c.row.data), copy.deepcopy(c.saved))
    loaded = load(c)
    binding = loaded['source_binding']
    assert binding['schema'] == 'business-flow-review-source-v1'
    assert binding['generation_profile'] == profiles.FLOW_CAUSAL == 'flow-v190'
    assert set(binding) == store.SOURCE_KEYS and store._source(binding, c.row.id) == binding
    assert binding['request_hash'] == c.prepared['request_hash']
    assert binding['reserved_call_id'] == c.row.reserved_call_id
    assert binding['capture_hash'] == c.saved['capture']['capture_hash']
    assert binding['response_body_sha256'] == hashlib.sha256(c.saved['body']).hexdigest()
    assert binding['arguments_sha256'] == hashlib.sha256(raw(c.value)).hexdigest()
    assert loaded['baseline_value'] == c.projection['wire']
    assert loaded['_causal_projection'] == c.projection
    assert loaded['baseline_value']['schema'] == 'report-business-flow-wire-v182'
    assert c.calls == ['original_inventory', 'read_original_no_recovery']
    assert source.diagnose(loaded, loaded['baseline_value'])['structural_valid'] is True
    loaded['_causal_projection']['raw_causal_plan']['causality']['entry_ports'].clear()
    loaded['baseline_value']['business']['flows'][0]['nodes'][0]['name'] = '本机改写'
    assert canonical_json(c.row.data) == before[0] and c.saved == before[1] and c.row.status == 'review'


@pytest.mark.parametrize('fault', ['missing_projection', 'changed_projection', 'old_answer',
    'wrong_call', 'incomplete', 'length', 'old_profile_marker'])
def test_unknown_or_mixed_original_never_becomes_a_new_causal_source_or_rewrites_history(monkeypatch, fault):
    c = causal_case(monkeypatch)
    if fault == 'missing_projection':
        c.row.status = 'failed'; c.row.data['projection'] = None
    elif fault == 'changed_projection':
        c.row.data['projection']['wire']['business']['flows'][0]['nodes'][0]['inputs'] = ['篡改基线']
        c.row.data['projection']['projection_hash'] = digest({k: v for k, v in c.row.data['projection'].items() if k != 'projection_hash'})
    elif fault == 'old_answer': c.saved['body'] = response(canonical_json(c.value['plan']))
    elif fault == 'wrong_call': c.saved['receipt']['call_id'] = c.row.model_id
    elif fault == 'incomplete': c.saved['capture']['response_complete'] = False
    elif fault == 'length': c.saved['body'] = response(canonical_json(c.value), finish='length')
    else: c.row.data['preview']['preparation']['schema'] = profiles.MARKERS[profiles.FLOW_REASONED]
    before = (canonical_json(c.row.data), copy.deepcopy(c.saved))
    with pytest.raises(HTTPException) as error:
        load(c)
    assert error.value.status_code == 409
    assert canonical_json(c.row.data) == before[0] and c.saved == before[1]
    assert c.calls == ['original_inventory', 'read_original_no_recovery']


@pytest.mark.parametrize('mode', ['unsaved', 'same', 'changed'])
def test_detail_presents_original_causality_only_and_marks_actual_saved_draft_changes(monkeypatch, mode):
    c = causal_case(monkeypatch)
    loaded = load(c)
    saved = None if mode == 'unsaved' else saved_view(c, loaded, changed=mode == 'changed')
    reads = mock_saved(monkeypatch, c, saved)
    c.calls.clear()
    before = (canonical_json(c.row.data), copy.deepcopy(c.saved), canonical_json(saved))
    result = api.detail(c.db, c.p, c.row.id)
    shown = result['generation_causality']
    assert shown['schema'] == 'business-flow-causal-source-view-v190'
    assert shown['original_candidate_only'] is True
    assert shown['current_draft_matches_original'] is (mode != 'changed')
    assert shown['runtime_entry_binding_verified'] is False
    assert shown['baseline_value_hash'] == digest(loaded['baseline_value'])
    assert shown['current_value_hash'] == digest(result['value'])
    assert shown['compiled_sidecar_hash'] == c.projection['compiled_sidecar_hash']
    assert shown['causal_analysis_hash'] == c.projection['causal_analysis_hash']
    assert shown['rows'] and all(row['step_name'] and row['input_name'] for row in shown['rows'])
    assert '_causal_projection' not in result and 'raw_causal_plan' not in result
    assert 'compiled_sidecar' not in result and 'causal_analysis' not in result
    assert 'diagnostic_context' not in result
    assert all(shown[key] is result[key] is False for key in source.FALSE_FLAGS)
    assert result['saved'] is (mode != 'unsaved') and result['source_available'] is True
    assert result['current_source_checked'] is False and result['historical_source'] is True
    assert result['baseline_value'] == loaded['baseline_value']
    if saved is not None:
        assert result['note'] == saved['note'] and result['value'] == saved['value']
        assert saved['note'] not in canonical_json(shown)
    assert reads == [c.row.id] and c.calls == ['original_inventory', 'read_original_no_recovery']
    assert canonical_json(c.row.data) == before[0] and c.saved == before[1] and canonical_json(saved) == before[2]


def test_saved_human_work_survives_temporarily_missing_original_without_a_stale_causal_view(monkeypatch):
    c = causal_case(monkeypatch)
    saved = saved_view(c, load(c), changed=True)
    mock_saved(monkeypatch, c, saved)

    def unavailable(*args, **kwargs):
        raise HTTPException(409, 'synthetic unavailable historical source')

    monkeypatch.setattr(source, 'load', unavailable)
    result = api.detail(c.db, c.p, c.row.id)
    assert result['saved'] is True and result['source_available'] is False
    assert result['value'] == saved['value'] and result['note'] == saved['note']
    assert result['baseline_value'] == saved['baseline_value']
    assert 'generation_causality' not in result and '_causal_projection' not in result
    assert all(result[k] is False for k in source.FALSE_FLAGS)


def test_old187_source_and_detail_do_not_acquire_causal190_extras(monkeypatch):
    c = causal_case(monkeypatch, legacy=True)
    loaded = load(c)
    assert loaded['source_binding']['generation_profile'] == profiles.FLOW_REASONED
    assert loaded['baseline_value'] == c.projection['wire']
    assert '_causal_projection' not in loaded and 'generation_causality' not in loaded
    mock_saved(monkeypatch, c, None)
    result = api.detail(c.db, c.p, c.row.id)
    assert result['source_binding']['generation_profile'] == 'flow-v187'
    assert result['value'] == loaded['baseline_value']
    assert 'generation_causality' not in result and '_causal_projection' not in result


def test_source_binding_stays_closed_and_component186_remains_exactly185_only(monkeypatch):
    c = causal_case(monkeypatch)
    binding = load(c)['source_binding']
    assert store._source(binding, c.row.id) == binding
    for extra in ({'can_apply': True}, {'compiled_sidecar': {}}, {'_causal_projection': c.projection}):
        with pytest.raises((HTTPException, ValueError)):
            store._source({**binding, **extra}, c.row.id)
    legacy = component_case(monkeypatch)
    component = source.load(legacy.db, legacy.p, legacy.row.id)['source_binding']
    assert component['schema'] == 'business-flow-component-review-source-v186'
    assert component['generation_profile'] == 'flow-v185'
    assert store._source(component, legacy.row.id) == component
    component['generation_profile'] = 'flow-v190'
    with pytest.raises((HTTPException, ValueError)):
        store._source(component, legacy.row.id)
