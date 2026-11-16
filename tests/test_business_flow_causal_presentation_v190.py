"""Real causal projection-to-human-view mapping, using public synthetic data.

The frozen V184 compiler and V189/V190 validators run. This is not a cold
capture, source-ACL, durable UI, entry binding or semantic quality proof.
"""
import copy

import pytest

from generic_agents import business_flow_causal_presentation_v190 as subject
from generic_agents import report_business_flow_wire_v190 as wire
from generic_agents.contracts import digest
from tests.test_report_business_flow_wire_v184 import no_external, scene
from tests.test_report_business_flow_wire_v190 import fixture as protocol_fixture, envelope, project


def linked_candidate():
    value, source = protocol_fixture()
    first_output = value['causality']['output_contracts'][0]
    completion_input = next(item for item in value['causality']['input_bindings'] if item['action']['part'] == 'completion')
    completion_input['sources'] = [{'kind': 'output', 'action': copy.deepcopy(first_output['action']),
        'ordinal': first_output['ordinal']}]
    return project(value, source), value, source


def seal(projection):
    projection['projection_hash'] = digest({key: value for key, value in projection.items() if key != 'projection_hash'})
    return projection


def seal_manifest(projection):
    manifest = projection['compilation_manifest']
    projection['compilation_manifest_hash'] = digest(manifest)
    inner = projection['plan_projection']
    inner['compilation_manifest'] = copy.deepcopy(manifest)
    inner['compilation_manifest_hash'] = digest(manifest)
    inner['projection_hash'] = digest({key: value for key, value in inner.items() if key != 'projection_hash'})
    projection['plan_projection_hash'] = inner['projection_hash']
    return seal(projection)


def test_original_entry_and_prior_step_sources_have_real_names_roles_citations_and_manifest_node_ids():
    projection, value, _ = linked_candidate()
    result = subject.build_view(projection, current_value=projection['wire'])
    original_flow = value['plan']['business']['flows'][0]
    first = original_flow['phases'][0]['steps'][0]
    completion = original_flow['completion']
    rows = result['rows']
    assert result['schema'] == subject.SCHEMA == 'business-flow-causal-source-view-v190'
    assert result['original_candidate_only'] is True and result['runtime_entry_binding_verified'] is False
    assert result['current_draft_matches_original'] is True
    assert result['baseline_value_hash'] == result['current_value_hash'] == digest(projection['wire'])
    assert result['projection_hash'] == projection['projection_hash']
    assert result['compiled_sidecar_hash'] == projection['compiled_sidecar_hash']
    assert result['causal_analysis_hash'] == projection['causal_analysis_hash']
    assert len(rows) == 2
    assert rows[0]['input_name'] == first['inputs'][0] and rows[0]['step_name'] == first['name']
    assert rows[0]['sources'] == [{'kind': 'entry', 'label': '流程触发或配置需提供的资料（待绑定）'}]
    assert rows[1]['input_name'] == completion['inputs'][0] and rows[1]['step_name'] == completion['name']
    assert rows[1]['sources'] == [{'kind': 'step', 'step_name': first['name'],
        'output_name': first['outputs'][0], 'actor': first['actor'], 'role': first['role']}]
    mappings = projection['compilation_manifest']['flows'][0]['nodes']
    assert [item['node_id'] for item in rows] == [item['node_id'] for item in mappings]
    assert [item['citations'] for item in rows] == [first['attribution']['citations'], completion['attribution']['citations']]
    assert all(item['flow_index'] == 0 and item['flow_name'] == projection['wire']['business']['flows'][0]['name'] for item in rows)
    assert all(result[key] is False for key in subject._FLAGS)
    assert result['view_hash'] == digest({key: item for key, item in result.items() if key != 'view_hash'})


def test_repeated_labels_keep_rows_and_original_output_ordinals_not_compiler_set_offsets():
    plan, source, _, _, _ = scene(4)
    first = plan['business']['flows'][0]['phases'][0]['steps'][0]
    first['outputs'] = ['重复输出', '重复输出', '第三原序输出']
    plan['business']['flows'][0]['completion']['inputs'] = ['重复输入', '重复输入']
    value = envelope(plan, source['scope_binding'])
    first_selector = copy.deepcopy(value['causality']['output_contracts'][0]['action'])
    completion = [item for item in value['causality']['input_bindings'] if item['action']['part'] == 'completion']
    for item, ordinal in zip(completion, (1, 2), strict=True):
        item['sources'] = [{'kind': 'output', 'action': copy.deepcopy(first_selector), 'ordinal': ordinal}]
    projection = project(value, source)
    assert projection['wire']['business']['flows'][0]['nodes'][0]['outputs'] == ['重复输出', '第三原序输出']
    result = subject.build_view(projection, current_value=projection['wire'])
    rows = [item for item in result['rows'] if item['input_name'] == '重复输入']
    assert len(rows) == 2
    assert [item['sources'][0]['output_name'] for item in rows] == ['重复输出', '第三原序输出']
    assert rows[0]['node_id'] == rows[1]['node_id']
    assert len(result['rows']) == len(projection['compiled_sidecar']['input_bindings'])


@pytest.mark.parametrize('kind', ['edited_wire', 'empty_draft', 'null_draft', 'manual_wrapper'])
def test_changed_or_incomplete_manual_draft_never_inherits_original_causal_proof(kind):
    projection, _, _ = linked_candidate()
    baseline = copy.deepcopy(projection)
    if kind == 'edited_wire':
        current = copy.deepcopy(projection['wire'])
        current['business']['flows'][0]['nodes'][0]['name'] += '人工修订'
    elif kind == 'empty_draft': current = {}
    elif kind == 'null_draft': current = None
    else: current = wire.new_manual(projection)
    result = subject.build_view(projection, current_value=current)
    assert result['current_draft_matches_original'] is False
    assert result['original_candidate_only'] is True and result['runtime_entry_binding_verified'] is False
    assert result['rows'] == subject.build_view(projection, current_value=projection['wire'])['rows']
    assert result['baseline_value_hash'] == digest(projection['wire'])
    assert result['current_value_hash'] == digest(current)
    assert projection == baseline and all(result[key] is False for key in subject._FLAGS)


def test_zero_input_actions_produce_empty_rows_without_claiming_real_entry_or_runtime_binding():
    plan, source, _, _, _ = scene(4)
    plan['business']['flows'][0]['phases'] = []
    plan['business']['flows'][0]['completion']['inputs'] = []
    projection = project(envelope(plan, source['scope_binding']), source)
    result = subject.build_view(projection, current_value=projection['wire'])
    assert result['rows'] == [] and result['current_draft_matches_original'] is True
    assert result['entry_facts_verified'] is result['runtime_entry_binding_verified'] is False


@pytest.mark.parametrize('fault', ['source_pointer', 'sidecar_hash', 'analysis', 'authority'])
def test_bad_projection_or_source_pointer_cannot_be_rendered_as_an_original_valid_source(fault):
    projection, _, _ = linked_candidate()
    if fault == 'source_pointer': projection['compiled_sidecar']['input_bindings'][0]['sources'] = ['/unrelated/source']
    elif fault == 'sidecar_hash': projection['compiled_sidecar_hash'] = digest('other')
    elif fault == 'analysis': projection['causal_analysis']['paths']['actions'][0]['input_ports'][0]['path'] = '/not/a/port'
    else: projection['can_execute'] = True
    seal(projection)
    with pytest.raises(ValueError): subject.build_view(projection, current_value=projection['wire'])


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'swapped', 'unknown_path', 'algorithm'])
def test_resealed_manifest_still_needs_actual_frozen_compiler_mapping_for_every_action(fault):
    projection, _, _ = linked_candidate()
    manifest = projection['compilation_manifest']
    nodes = manifest['flows'][0]['nodes']
    if fault == 'missing': nodes.pop()
    elif fault == 'duplicate': nodes.append(copy.deepcopy(nodes[0]))
    elif fault == 'swapped': nodes[0]['node_id'], nodes[1]['node_id'] = nodes[1]['node_id'], nodes[0]['node_id']
    elif fault == 'unknown_path': nodes[0]['ast_path'] = '/business/flows/0/not_an_action'
    else: manifest['algorithm'] = 'unknown-compiler'
    seal_manifest(projection)
    # This demonstrates the presenter's own mapping check, beyond self hashes
    # in the already source-free manual helper. Cold callers still rebuild.
    assert wire.new_manual(projection)['result'] == projection['wire']
    with pytest.raises(ValueError): subject.build_view(projection, current_value=projection['wire'])


def test_derived_view_and_current_draft_are_detached_from_original_projection_and_quotes():
    projection, _, _ = linked_candidate()
    current = copy.deepcopy(projection['wire'])
    before = copy.deepcopy([projection, current])
    result = subject.build_view(projection, current_value=current)
    original = copy.deepcopy(result)
    result['rows'][0]['sources'][0]['label'] = '本机更改'
    result['rows'][1]['citations'].clear()
    assert [projection, current] == before
    assert subject.build_view(projection, current_value=current) == original
