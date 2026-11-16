"""Pure source adaptation over complete public synthetic V185 captures.

The existing source_case explicitly fakes checked Attempt inventory and saved
HTTP reading; real native envelope, V186, V184, V182 and diagnostic validators
run. API detail's no-existing-draft DB boundary is explicit. No IAM, capture
authenticity, actual DB persistence, provider or semantic quality is proved.
"""
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_review_api as api
from generic_agents import business_flow_review_source as source
from generic_agents import business_flow_review_store as store
from generic_agents import report_business_flow_component_recovery_v186 as components
from generic_agents import report_business_flow_revision_policy_v185 as policy
from generic_agents import report_business_flow_wire_v185 as revision
from generic_agents import report_business_flow_wire_v184 as plans
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents.contracts import digest
from tests.test_business_flow_review_api_pure import no_io, source_case
from tests.test_report_business_flow_component_recovery_v186 import broken_scene
from tests.test_report_business_flow_protocol_v182 import response
from tests.test_report_business_flow_wire_v185 import raw


def component_case(monkeypatch):
    c = source_case(monkeypatch)
    review, parent, inputs = broken_scene()
    # This adapter fixture intentionally does not pretend to be a newly
    # admitted preparation. checked_attempt is the documented inventory seam;
    # every component/source/graph field below is from the same real scene.
    preparation = {'schema': profiles.MARKERS[profiles.FLOW_REVISION],
        'scope_binding': copy.deepcopy(inputs['scope_binding']),
        'parent_source': copy.deepcopy(parent), 'input_claims': copy.deepcopy(inputs['input_claims']),
        'allowed_span_refs': copy.deepcopy(inputs['allowed_span_refs']),
        'source_snapshot_hash': inputs['scope_binding']['source_snapshot_hash'],
        'request_hash': digest('public-original-v185-adapter-request'),
        'platform_policy_hash': review['platform_policy_hash'],
        'payload': {'platform_policy': policy.platform_policy()}}
    c.row.data = {'preview': {'preparation': preparation,
        'source_context': {'catalog': copy.deepcopy(inputs['catalog']),
            'context': copy.deepcopy(inputs['context'])}}, 'projection': None}
    c.row.status = 'failed'
    c.saved['body'] = response(json.dumps(review, ensure_ascii=False))
    c.review, c.parent, c.inputs = review, parent, inputs
    return c


def load(c):
    return source.load(c.db, c.p, c.row.id)


def test_failed_capture_gets_an_explicit_component_source_without_changing_original_attempt(monkeypatch):
    c = component_case(monkeypatch)
    before_data, before_saved = copy.deepcopy(c.row.data), copy.deepcopy(c.saved)
    actual = raw(c.review)
    with pytest.raises(ValueError):
        revision.parse_review(actual, scope_binding=c.inputs['scope_binding'], parent_source=c.parent)
    result = load(c)
    binding = result['source_binding']
    expected = components.build_component(actual, parent_source=c.parent, **c.inputs)
    assert binding['schema'] == 'business-flow-component-review-source-v186'
    assert binding['generation_profile'] == profiles.FLOW_REVISION
    assert binding['component_path'] == '/revised_plan'
    assert binding['component_value_hash'] == expected['component_value_hash']
    assert binding['component_validation_hash'] == expected['component_hash']
    assert binding['original_protocol_valid'] is False
    assert binding['response_body_sha256'] == hashlib.sha256(c.saved['body']).hexdigest()
    assert binding['arguments_sha256'] == hashlib.sha256(actual).hexdigest()
    assert result['baseline_value'] == expected['wire']
    assert result['baseline_value'] == plans.build_projection(plans._bytes(c.review['revised_plan']), **c.inputs)['wire']
    assert c.calls == ['original_inventory', 'read_original_no_recovery']
    assert c.row.status == 'failed' and c.row.data['projection'] is None
    assert c.row.data == before_data and c.saved == before_saved
    assert store._source(binding, c.row.id) == binding
    # Even a successfully compiled component does not accept the old protocol.
    with pytest.raises(ValueError):
        revision.parse_review(actual, scope_binding=c.inputs['scope_binding'], parent_source=c.parent)


@pytest.mark.parametrize('fault', ['path', 'value_hash', 'validation_hash', 'original_true',
    'original_int', 'profile', 'extra', 'missing'])
def test_component_store_variant_is_closed_and_never_accepts_a_downgraded_or_authorized_source(monkeypatch, fault):
    c = component_case(monkeypatch)
    value = load(c)['source_binding']
    if fault == 'path': value['component_path'] = '/business'
    elif fault == 'value_hash': value['component_value_hash'] = 'G' * 64
    elif fault == 'validation_hash': value['component_validation_hash'] = 'a' * 63
    elif fault == 'original_true': value['original_protocol_valid'] = True
    elif fault == 'original_int': value['original_protocol_valid'] = 0
    elif fault == 'profile': value['generation_profile'] = profiles.FLOW_PLAN
    elif fault == 'extra': value['can_apply'] = True
    else: del value['component_validation_hash']
    with pytest.raises((HTTPException, ValueError)):
        store._source(value, c.row.id)


def test_old_source_variant_keeps_its_original_closed_shape(monkeypatch):
    c = source_case(monkeypatch)
    result = load(c)
    binding = result['source_binding']
    assert set(binding) == store.SOURCE_KEYS
    assert binding['schema'] == 'business-flow-review-source-v1'
    assert 'generation_review' not in result
    assert store._source(binding, c.row.id) == binding
    for extra in ({'component_path': '/revised_plan'}, {'original_protocol_valid': False},
            {'component_validation_hash': 'a' * 64}):
        with pytest.raises(HTTPException):
            store._source({**binding, **extra}, c.row.id)


@pytest.mark.parametrize('where', ['finding', 'unused_plan_attribution'])
def test_unknown_citation_is_fatal_even_with_recoverable_locations(monkeypatch, where):
    c = component_case(monkeypatch)
    if where == 'finding':
        c.review['findings'][0]['citations'] = ['w:p999999/s1']
    else:
        c.review['revised_plan']['business']['flows'][0]['phase_order']['citations'] = ['w:p999999/s1']
    c.saved['body'] = response(json.dumps(c.review, ensure_ascii=False))
    before = copy.deepcopy(c.row.data)
    with pytest.raises(HTTPException) as error:
        load(c)
    assert error.value.status_code == 409
    assert c.row.status == 'failed' and c.row.data == before and c.row.data['projection'] is None


def test_component_adapter_still_uses_actual_final_graph_validation(monkeypatch):
    c = component_case(monkeypatch)
    compile_plan = plans._compile
    calls = []
    def corrupted(*args, **kwargs):
        graph, mappings, ordered = compile_plan(*args, **kwargs)
        graph['business']['flows'][0]['edges'] = []
        calls.append('compiler_fault')
        return graph, mappings, ordered
    monkeypatch.setattr(plans, '_compile', corrupted)
    with pytest.raises(HTTPException) as error:
        load(c)
    assert error.value.status_code == 409 and calls == ['compiler_fault']
    assert c.row.status == 'failed' and c.row.data['projection'] is None


@pytest.mark.parametrize('fault', ['incomplete', 'not_ready', 'length', 'wrong_call'])
def test_original_incomplete_or_mismatched_capture_never_becomes_a_component(monkeypatch, fault):
    c = component_case(monkeypatch)
    if fault == 'incomplete': c.saved['capture']['response_complete'] = False
    elif fault == 'not_ready': c.saved['capture']['ready'] = False
    elif fault == 'length': c.saved['body'] = response(json.dumps(c.review, ensure_ascii=False), finish='length')
    else: c.saved['receipt']['call_id'] = c.parent['binding']['reserved_call_id']
    with pytest.raises(HTTPException) as error:
        load(c)
    assert error.value.status_code == 409
    assert c.row.status == 'failed' and c.row.data['projection'] is None
    assert c.calls == ['original_inventory', 'read_original_no_recovery']


def test_detail_exposes_original_format_warning_beside_incomplete_draft_and_old_family_is_unchanged(monkeypatch):
    # The only additional seam is the persistent-store no-existing-draft read.
    # Source load, original envelope and diagnostics remain actual functions.
    store_reads = []
    def no_saved(db, principal, attempt_id):
        store_reads.append((db, principal, attempt_id))
        return None
    monkeypatch.setattr(store, 'get', no_saved)
    c = component_case(monkeypatch)
    result = api.detail(c.db, c.p, c.row.id)
    warning = result['generation_review']
    assert warning['schema'] == 'business-flow-source-component-review-v186'
    assert warning['component_only'] is True and warning['original_protocol_valid'] is False
    assert warning['component_path'] == '/revised_plan' and warning['machine_review_only'] is True
    assert warning['findings'] == c.review['findings']
    assert len(warning['location_issues']) == 2
    assert all(item['reason'] == 'invalid_pointer' for item in warning['location_issues'])
    assert warning['component_validation_hash'] == result['source_binding']['component_validation_hash']
    assert all(warning[key] is result[key] is False for key in source.FALSE_FLAGS)
    assert result['status'] == 'incomplete' and result['saved'] is False and result['version'] == 0
    assert result['source_available'] is True and result['current_source_checked'] is False
    assert result['baseline_value'] == result['value']
    assert result['diagnostics']['structural_valid'] is result['diagnostics']['source_valid'] is True
    assert c.row.status == 'failed' and c.row.data['projection'] is None
    old = source_case(monkeypatch)
    legacy = api.detail(old.db, old.p, old.row.id)
    assert 'generation_review' not in legacy
    assert set(legacy['source_binding']) == store.SOURCE_KEYS
    assert len(store_reads) == 2


@pytest.mark.parametrize('fault', ['valid_original', 'wrong_family', 'success_without_projection'])
def test_component_recovery_does_not_replace_other_family_or_success_interpretations(monkeypatch, fault):
    c = component_case(monkeypatch)
    if fault == 'valid_original':
        pointer = '/business/flows/0/phases/0/steps/0/role'
        c.review['findings'][0]['base_paths'] = [pointer]
        c.review['findings'][0]['revised_paths'] = [pointer]
        assert revision.parse_review(raw(c.review), scope_binding=c.inputs['scope_binding'], parent_source=c.parent) == c.review
        c.saved['body'] = response(json.dumps(c.review, ensure_ascii=False))
    elif fault == 'wrong_family':
        c.row.data['preview']['preparation']['schema'] = profiles.MARKERS[profiles.FLOW_PLAN]
    else:
        c.row.status = 'review'
    before = copy.deepcopy(c.row.data)
    with pytest.raises(HTTPException) as error:
        load(c)
    assert error.value.status_code == 409 and c.row.data == before
    assert c.row.data['projection'] is None
