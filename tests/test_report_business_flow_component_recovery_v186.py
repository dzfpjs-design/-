"""Pure layered recovery with real V185/V184/V182 and source validators.

Public synthetic data only. No capture authenticity, IAM, persistence, provider
or target-runtime claim is made. The imported autouse fixture forbids I/O.
"""
import copy
import hashlib
import json

import pytest

from generic_agents import report_business_flow_component_recovery_v186 as subject
from generic_agents import report_business_flow_wire_v185 as original
from generic_agents import report_business_flow_wire_v184 as plans
from generic_agents import report_business_flow_wire_v182 as base
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v185 import no_external, raw, scene


def broken_scene():
    review, parent, source = scene()
    review['findings'][0]['base_paths'] = ['business.flows[0].phases[0].steps[0].role']
    review['findings'][0]['revised_paths'] = ['business.flows[0].phases[0].steps[0].role']
    return review, parent, source


def build(review, parent, source):
    return subject.build_component(raw(review), parent_source=parent, **source)


def reseal(value):
    value['component_hash'] = digest({key: item for key, item in value.items() if key != 'component_hash'})
    return value


def test_dot_locations_are_preserved_beside_an_independent_unconfirmed_real_component():
    review, parent, source = broken_scene()
    frozen = canonical_json([review, parent, source])
    actual = b'\n' + json.dumps(review, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    with pytest.raises(ValueError):
        original.parse_review(actual, scope_binding=source['scope_binding'], parent_source=parent)
    result = subject.build_component(actual, parent_source=parent, **source)
    assert result['schema'] == subject.SCHEMA == 'report-business-flow-component-recovery-v186'
    assert result['component_path'] == '/revised_plan'
    assert result['original_protocol'] == original.WIRE and result['original_protocol_valid'] is False
    assert result['raw_review'] == review and result['raw_review_value_hash'] == digest(review)
    assert result['raw_review_bytes_sha256'] == hashlib.sha256(actual).hexdigest()
    assert result['raw_review_bytes'] == len(actual)
    assert result['component_value_hash'] == digest(review['revised_plan'])
    assert result['parent_source'] == parent and result['parent_source_hash'] == digest(parent)
    assert result['location_issues'] == [{'finding_index': 0, 'field': field,
        'path': review['findings'][0][field][0], 'reason': 'invalid_pointer'}
        for field in ('base_paths', 'revised_paths')]
    assert result['machine_review_only'] is True and result['partial_scope'] is True
    assert all(result[key] is False for key in subject._FALSE_FLAGS)
    projected = plans.build_projection(plans._bytes(review['revised_plan']), **source)
    assert result['revised_projection'] == projected
    assert result['revised_projection_hash'] == projected['projection_hash']
    assert result['wire'] == projected['wire'] and result['wire_value_hash'] == digest(projected['wire'])
    assert base.parse_wire(base._bytes(result['wire']), scope_binding=source['scope_binding']) == result['wire']
    assert subject.check_component(result, actual, parent_source=parent, **source) == result
    manual = subject.new_manual(result)
    assert manual == {'schema': base.MANUAL, 'result': result['wire'], 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}
    assert base.validate_value(manual, **source) == manual
    with pytest.raises(ValueError):
        base.confirm_value(manual)
    assert canonical_json([review, parent, source]) == frozen
    with pytest.raises(ValueError):
        original.parse_review(actual, scope_binding=source['scope_binding'], parent_source=parent)


def test_valid_pointer_with_missing_target_is_reported_without_guessing_an_index():
    review, parent, source = scene()
    missing = '/business/flows/999/trigger'
    review['findings'][0]['base_paths'] = [missing]
    result = build(review, parent, source)
    assert result['location_issues'] == [{'finding_index': 0, 'field': 'base_paths',
        'path': missing, 'reason': 'path_not_found'}]
    assert result['raw_review']['findings'][0]['base_paths'] == [missing]
    assert result['raw_review']['findings'][0]['revised_paths'] == review['findings'][0]['revised_paths']


def test_a_strictly_valid_original_v185_answer_cannot_use_failed_component_recovery():
    review, parent, source = scene()
    assert original.parse_review(raw(review), scope_binding=source['scope_binding'], parent_source=parent) == review
    assert original.build_projection(raw(review), parent_source=parent, **source)['wire']['schema'] == base.WIRE
    with pytest.raises(ValueError):
        build(review, parent, source)
    review['findings'][0]['base_paths'] = ['']
    review['findings'][0]['revised_paths'] = ['']
    with pytest.raises(ValueError):
        build(review, parent, source)


@pytest.mark.parametrize('fault', ['binding_hash', 'parent_source_hash', 'base_plan_hash',
    'platform_policy_hash', 'parent_binding', 'component_binding', 'component_name'])
def test_bad_binding_hash_parent_or_component_const_is_not_a_location_only_failure(fault):
    review, parent, source = broken_scene()
    if fault in {'binding_hash', 'parent_source_hash', 'base_plan_hash', 'platform_policy_hash'}:
        review[fault] = digest('unrelated-source')
    elif fault == 'parent_binding':
        parent['binding']['scope_id'] = 'another-scope'
        parent['parent_hash'] = digest({key: value for key, value in parent.items() if key != 'parent_hash'})
        review['parent_source_hash'] = digest(parent)
    elif fault == 'component_binding':
        review['revised_plan']['binding_hash'] = digest('unrelated-component')
    else:
        review['revised_plan']['business']['name'] = '另一个业务范围'
    before = canonical_json([review, parent, source])
    with pytest.raises(ValueError):
        build(review, parent, source)
    assert canonical_json([review, parent, source]) == before


def test_unknown_finding_ref_remains_fatal_even_when_the_revised_graph_does_not_use_it():
    review, parent, source = broken_scene()
    review['findings'][0]['citations'] = ['w:p999999/s1']
    before = raw(review)
    with pytest.raises(ValueError):
        build(review, parent, source)
    assert raw(review) == before


@pytest.mark.parametrize('fault', ['unknown_unused_attribution', 'capacity', 'invalid_action'])
def test_revised_plan_is_not_partially_salvaged_when_its_actual_contract_fails(fault):
    review, parent, source = broken_scene()
    flow = review['revised_plan']['business']['flows'][0]
    if fault == 'unknown_unused_attribution':
        flow['phase_order']['citations'] = ['w:p999999/s1']
    elif fault == 'capacity':
        action = copy.deepcopy(flow['phases'][0]['steps'][0])
        flow['phases'] = [copy.deepcopy(flow['phases'][0]), copy.deepcopy(flow['phases'][0])]
        flow['phases'][0]['steps'] = [copy.deepcopy(action) for _ in range(16)]
        flow['phases'][1]['steps'] = [copy.deepcopy(action) for _ in range(16)]
    else:
        flow['completion']['outputs'] = []
    before = raw(review)
    with pytest.raises(ValueError):
        build(review, parent, source)
    assert raw(review) == before


def test_actual_v182_graph_gate_still_rejects_a_corrupted_compiler_graph(monkeypatch):
    review, parent, source = broken_scene()
    compile_plan = plans._compile
    calls = []
    def corrupt_graph(*args, **kwargs):
        compiled, mappings, ordered = compile_plan(*args, **kwargs)
        compiled['business']['flows'][0]['edges'] = []
        calls.append('actual compiler graph fault')
        return compiled, mappings, ordered
    monkeypatch.setattr(plans, '_compile', corrupt_graph)
    with pytest.raises(ValueError):
        build(review, parent, source)
    assert calls == ['actual compiler graph fault']


@pytest.mark.parametrize('fault', ['truncated', 'duplicate_key', 'invalid_utf8', 'extra',
    'changed_without_target', 'removed_with_target', 'nonfinite'])
def test_invalid_raw_shape_or_resolution_is_not_reinterpreted_as_recoverable_locations(fault):
    review, parent, source = broken_scene()
    if fault == 'truncated':
        payload = raw(review)[:-1]
    elif fault == 'duplicate_key':
        payload = b'{"schema":"duplicate",' + raw(review)[1:]
    elif fault == 'invalid_utf8':
        payload = b'\xff' + raw(review)
    else:
        if fault == 'extra':
            review['paid_success'] = True
        elif fault == 'changed_without_target':
            review['findings'][0]['revised_paths'] = []
        elif fault == 'removed_with_target':
            review['findings'][0]['resolution'] = 'removed'
        else:
            review['findings'][0]['explanation'] = float('nan')
        payload = json.dumps(review, ensure_ascii=False, allow_nan=True).encode('utf-8')
    with pytest.raises(ValueError):
        subject.build_component(payload, parent_source=parent, **source)


@pytest.mark.parametrize('fault', ['raw_review', 'wire', 'manifest', 'location', 'flag', 'extra'])
def test_resealed_component_cannot_replace_raw_paths_graph_or_original_rejection(fault):
    review, parent, source = broken_scene()
    value = build(review, parent, source)
    if fault == 'raw_review':
        value['raw_review']['findings'][0]['base_paths'] = ['/business/name']
    elif fault == 'wire':
        value['wire']['business']['flows'][0]['nodes'][0]['role'] = '改写后的岗位'
    elif fault == 'manifest':
        value['revised_projection']['compilation_manifest']['algorithm'] = 'another-compiler'
    elif fault == 'location':
        value['location_issues'][0]['path'] = '/business/name'
    elif fault == 'flag':
        value['original_protocol_valid'] = True
    else:
        value['current_permission'] = True
    reseal(value)
    with pytest.raises(ValueError):
        subject.check_component(value, raw(review), parent_source=parent, **source)


def test_independent_source_must_still_match_and_returned_values_are_detached():
    review, parent, source = broken_scene()
    before = canonical_json([review, parent, source])
    first = build(review, parent, source)
    assert build(review, parent, source) == first
    changed = copy.deepcopy(source)
    changed['input_claims'][0]['statement'] += '独立来源已变化'
    with pytest.raises(ValueError):
        subject.check_component(first, raw(review), parent_source=parent, **changed)
    first['raw_review']['findings'][0]['base_paths'].clear()
    first['wire']['business']['flows'].clear()
    first['parent_source']['raw_plan']['business']['questions'].clear()
    assert canonical_json([review, parent, source]) == before
    assert build(review, parent, source)['raw_review'] == review
