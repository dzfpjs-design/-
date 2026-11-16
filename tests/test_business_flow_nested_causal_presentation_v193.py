"""Actual V193 projection to a precise, read-only original-candidate view.

No cold capture, source ACL, saved user work, entry binding or runtime behavior
is proved by these pure synthetic checks. Root executes them.
"""
import copy

import pytest

from generic_agents import business_flow_nested_causal_presentation_v193 as subject
from generic_agents import report_business_flow_wire_v193 as wire
from generic_agents.contracts import digest
from tests.test_report_business_flow_wire_v184 import no_external
from tests.test_report_business_flow_wire_v193 import FP, fixture, envelope, project, bind, seal


def linked_candidate():
    value, source = fixture(False)
    bind(value, FP + '/completion/inputs/0', FP + '/phases/0/steps/0/outputs/0',
        scope_binding=source['scope_binding'])
    return project(value, source), value, source


def test_original_entry_and_step_sources_keep_real_paths_names_roles_and_evidence_together():
    projection, value, _ = linked_candidate()
    result = subject.build_view(projection, current_value=projection['wire'])
    rows = result['rows']
    first = value['plan']['business']['flows'][0]['phases'][0]['steps'][0]
    completion = value['plan']['business']['flows'][0]['completion']
    assert result['schema'] == 'business-flow-nested-causality-view-v193'
    assert result['original_candidate_only'] is True and result['current_draft_matches_original'] is True
    assert result['runtime_entry_binding_verified'] is False and result['entry_contracts_are_proposals'] is True
    assert len(rows) == 2
    assert rows[0] == {'flow_index': 0, 'flow_name': value['plan']['business']['flows'][0]['name'],
        'action_path': FP + '/phases/0/steps/0', 'action_name': first['name'], 'step_name': first['name'],
        'actor': first['actor'], 'role': first['role'],
        'control_ancestors': [{'path': FP + '/phases/0', 'kind': 'steps', 'branch_index': None}],
        'input_path': FP + '/phases/0/steps/0/inputs/0', 'input_ordinal': 0, 'input_name': first['inputs'][0],
        'sources': [{'kind': 'entry', 'entry_path': '/entry_ports/0', 'label': subject.ENTRY_LABEL}],
        'citations': first['attribution']['citations']}
    assert rows[1]['action_path'] == FP + '/completion' and rows[1]['control_ancestors'] == []
    assert rows[1]['step_name'] == completion['name']
    assert rows[1]['sources'] == [{'kind': 'step', 'action_path': FP + '/phases/0/steps/0',
        'step_name': first['name'], 'output_path': FP + '/phases/0/steps/0/outputs/0',
        'output_ordinal': 0, 'output_name': first['outputs'][0], 'actor': first['actor'], 'role': first['role']}]
    assert result['baseline_value_hash'] == result['current_value_hash'] == digest(projection['wire'])
    assert result['causal_analysis_hash'] == projection['causal_analysis_hash']
    assert result['compiled_sidecar_hash'] == projection['compiled_sidecar_hash']
    assert all(result[key] is False for key in subject._FLAGS)
    assert result['view_hash'] == digest({key: item for key, item in result.items() if key != 'view_hash'})
    assert all('node_id' not in row for row in rows)  # No execution graph exists yet.


def test_deep_recursive_input_keeps_all_control_ancestors_without_flattening_or_name_matching():
    value, source = fixture()
    projection = project(value, source)
    result = subject.build_view(projection, current_value=projection['wire'])
    pointer = FP + '/phases/1/branches/0/phases/0/branches/1/phases/0/branches/0/phases/0/steps/0'
    row = next(item for item in result['rows'] if item['action_path'] == pointer)
    actual = next(item for item in projection['causal_analysis']['paths']['actions'] if item['path'] == pointer)
    assert row['control_ancestors'] == actual['control_ancestors']
    assert [a['kind'] for a in row['control_ancestors']] == [
        'decision', 'decision_branch', 'parallel', 'parallel_branch', 'decision', 'decision_branch', 'steps']
    assert row['input_path'] == pointer + '/inputs/0' and row['input_ordinal'] == 0
    assert len(result['rows']) == projection['plan_counts']['input_count']
    assert row['sources'] == [{'kind': 'entry', 'entry_path': '/entry_ports/0', 'label': subject.ENTRY_LABEL}]


def test_repeated_input_output_labels_retain_original_ordinals_and_duplicate_flow_names_stay_separate():
    value, source = fixture(False)
    first = value['plan']['business']['flows'][0]['phases'][0]['steps'][0]
    first['outputs'] = ['重复输出', '重复输出', '第三原序输出']
    value['plan']['business']['flows'][0]['completion']['inputs'] = ['重复输入', '重复输入']
    value['plan']['business']['flows'].append(copy.deepcopy(value['plan']['business']['flows'][0]))
    value = envelope(value['plan'], source['scope_binding'])
    for fi in range(2):
        fp = '/business/flows/' + str(fi)
        for ii, oi in enumerate((1, 2)):
            bind(value, fp + '/completion/inputs/' + str(ii), fp + '/phases/0/steps/0/outputs/' + str(oi),
                scope_binding=source['scope_binding'])
    projection = project(value, source)
    result = subject.build_view(projection, current_value=projection['wire'])
    rows = [row for row in result['rows'] if row['input_name'] == '重复输入']
    assert len(rows) == 4
    assert [row['flow_index'] for row in rows] == [0, 0, 1, 1]
    assert [row['input_ordinal'] for row in rows] == [0, 1, 0, 1]
    assert [row['sources'][0]['output_ordinal'] for row in rows] == [1, 2, 1, 2]
    assert [row['sources'][0]['output_name'] for row in rows] == ['重复输出', '第三原序输出'] * 2
    assert rows[0]['flow_name'] == rows[2]['flow_name']
    assert rows[0]['action_path'] != rows[2]['action_path']


@pytest.mark.parametrize('kind', ['edited_tree', 'empty_draft', 'null_draft', 'manual_wrapper'])
def test_modified_or_incomplete_manual_tree_does_not_inherit_the_original_causal_analysis(kind):
    projection, _, _ = linked_candidate()
    if kind == 'edited_tree':
        current = copy.deepcopy(projection['wire'])
        current['business']['flows'][0]['phases'][0]['steps'][0]['responsibility'] += ' 人工增加独立要求。'
    elif kind == 'empty_draft': current = {}
    elif kind == 'null_draft': current = None
    else: current = wire.new_manual(projection)
    before = copy.deepcopy(projection)
    result = subject.build_view(projection, current_value=current)
    assert result['current_draft_matches_original'] is False and result['original_candidate_only'] is True
    assert result['baseline_value_hash'] != result['current_value_hash']
    assert result['runtime_entry_binding_verified'] is False and result['can_execute'] is False
    assert result['rows'][1]['sources'][0]['step_name'] == projection['wire']['business']['flows'][0]['phases'][0]['steps'][0]['name']
    assert projection == before


def test_zero_input_plan_has_an_empty_view_without_fabricating_entry_or_step_rows():
    value, source = fixture(False)
    flow = value['plan']['business']['flows'][0]
    flow['phases'] = []
    flow['completion']['inputs'] = []
    value = envelope(value['plan'], source['scope_binding'])
    value['causality']['entry_ports'] = []
    projection = project(value, source)
    result = subject.build_view(projection, current_value=projection['wire'])
    assert result['rows'] == [] and result['current_draft_matches_original'] is True
    assert result['entry_facts_verified'] is False and result['semantic_quality_verified'] is False


@pytest.mark.parametrize('fault', ['pointer', 'analysis_path', 'ancestor', 'raw_plan', 'wire', 'evidence_ref', 'authority'])
def test_bad_original_projection_or_resealed_source_positions_are_not_presented_as_a_valid_candidate(fault):
    projection, _, _ = linked_candidate()
    if fault == 'pointer':
        projection['compiled_sidecar']['input_bindings'][0]['sources'] = ['/business/flows/0/unknown/outputs/0']
        projection['compiled_sidecar_hash'] = digest(projection['compiled_sidecar'])
    elif fault in {'analysis_path', 'ancestor'}:
        paths = projection['causal_analysis']['paths']
        if fault == 'analysis_path': paths['actions'][0]['path'] = FP + '/phases/99/steps/0'
        else: paths['actions'][0]['control_ancestors'] = []
        paths['walk_hash'] = digest({key: item for key, item in paths.items() if key != 'walk_hash'})
        analysis = projection['causal_analysis']
        analysis['availability_hash'] = digest({key: item for key, item in analysis.items() if key != 'availability_hash'})
        projection['causal_analysis_hash'] = analysis['availability_hash']
    elif fault == 'raw_plan': projection['raw_plan']['business']['flows'][0]['name'] = '改变原结构'
    elif fault == 'wire': projection['wire']['business']['flows'][0]['name'] = '改变原视图'
    elif fault == 'evidence_ref':
        projection['evidence'][0]['span_ref'] = 'w:p999999/s1'
        projection['evidence_hash'] = digest(projection['evidence'])
    else: projection['source_authority_verified'] = True
    seal(projection)
    with pytest.raises(ValueError): subject.build_view(projection, current_value=projection['wire'])


def test_returned_view_is_detached_from_original_plan_sidecar_and_evidence():
    projection, _, _ = linked_candidate()
    before = copy.deepcopy(projection)
    result = subject.build_view(projection, current_value=projection['wire'])
    result['rows'][0]['control_ancestors'][0]['kind'] = '改变返回值'
    result['rows'][1]['sources'][0]['output_name'] = '改变返回值'
    result['rows'][0]['citations'].append('w:p999999/s1')
    assert projection == before
    assert subject.build_view(projection, current_value=projection['wire'])['rows'][0]['control_ancestors'][0]['kind'] == 'steps'
