"""Actual pure reducer/input adapter; entry tests are policy boundaries, not IAM."""
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import patch
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_inputs as inputs
from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import full_semantic_graph_v1 as graph
from generic_agents.contracts import digest
from generic_agents.models import Task
from generic_agents import workflow_graph
from tests.test_business_flow_nested_execution_v195 import scene, build
from tests.test_business_flow_nested_execution_v192 import ids, finish
from tests.test_full_semantic_exclusive_reference_v195 import choice_graph, ambiguous_graph
from tests.test_full_semantic_graph_v1 import human, activation, finish as finish_node


@pytest.mark.parametrize('approved', [True, False])
@pytest.mark.parametrize('duplicate', [True, False])
def test_original_labels_and_current_branch_product_survive_cold_read(approved, duplicate):
    values = scene(); plan, _, bindings, sidecar, _ = values
    path = '/business/flows/0/completion'
    label = '当前实际分支\n的处置结果'
    plan['business']['flows'][0]['completion']['inputs'] = [label] * (2 if duplicate else 1)
    if duplicate:
        bindings['nodes'][path]['input_ports'].append({'ordinal': 1, 'reference': 'branch_result'})
        second = copy.deepcopy(sidecar['input_bindings'][0]); second['input_path'] = path + '/inputs/1'
        sidecar['input_bindings'].append(second)
    sidecar['source_plan_hash'] = digest(plan)
    translated = build(values); native = translated['graph']; names = ids(translated, values[4])
    state = finish(native, graph.new_run(native, {}, 10), names['确认修复建议'], {'approved': approved})
    if approved:
        state = finish(native, state, names['组织通知与补核'])
        state = finish(native, state, names['通知责任人员'], {'result': '通知已记录'})
        state = finish(native, state, names['核对飞行条件'], {'approved': False})
    else: state = finish(native, state, names['记录退回理由'], {'result': '退回已记录'})
    completion = next(row['graph_node_id'] for row in translated['source_mapping']['actions'] if row['ast_path'] == path)
    current = next(item for item in state['activations'].values() if item['node_id'] == completion)
    binding = {'schema': runtime.NESTED_BINDING, 'graph': native, 'translation': translated}
    before = copy.deepcopy(state)
    result = inputs.require_inputs(binding, json.loads(json.dumps(state)), current['id'])
    assert len(result) == (2 if duplicate else 1)
    for index, item in enumerate(result):
        assert item['label'] == label and item['ordinal'] == index
        assert item['available'] is True and item['availability_code'] == 'ready'
        assert item['source'] == 'exclusive_output'
        assert item['value'] == ('通知已记录' if approved else '退回已记录')
        producer = state['activations'][item['producer_activation_id']]
        assert producer['node_id'] == names['通知责任人员' if approved else '记录退回理由']
    assert state == before


def adapter_binding(native):
    """A public input presentation fixture, deliberately no adoption authority."""
    for index, node in enumerate(native['nodes']):
        if node['id'] == 'consume': native['nodes'][index] = human('consume')
    return {'schema': runtime.NESTED_BINDING, 'graph': native, 'translation': {'source_mapping': {'actions': [
        {'graph_node_id': 'consume', 'source_action': {'name': '人工核对', 'inputs': ['分支依据']},
            'input_ports': [{'ordinal': 0, 'reference': 'choice'}]},
        *[{'graph_node_id': node['id'], 'source_action': {'name': '来源 ' + node['id'], 'inputs': []},
            'input_ports': []} for node in native['nodes'] if node['id'] != 'consume']
    ]}}}


def test_nullable_null_is_available_and_has_the_actual_producer():
    binding = adapter_binding(choice_graph(nullable=True)); native = binding['graph']
    state = finish_node(native, graph.new_run(native, {}, 10), 'choose', {'ok': True})
    state = finish_node(native, state, 'left', {'ok': None})
    result = inputs.require_inputs(binding, state, activation(state, 'consume')['id'])
    assert result[0]['available'] is True and result[0]['value'] is None
    assert result[0]['availability_code'] == 'ready'
    assert state['activations'][result[0]['producer_activation_id']]['node_id'] == 'left'


@pytest.mark.parametrize('ambiguous', [True, False])
def test_unavailable_branch_never_becomes_null_success_or_mutates_state(ambiguous):
    binding = adapter_binding(ambiguous_graph() if ambiguous else choice_graph(empty=True)); native = binding['graph']
    state = graph.new_run(native, {}, 10)
    if ambiguous:
        state = finish_node(native, state, 'a', {'ok': True})
        state = finish_node(native, state, 'b', {'ok': True})
    else: state = finish_node(native, state, 'choose', {'ok': False})
    current = activation(state, 'consume', status='waiting_guard'); before = copy.deepcopy(state)
    result = inputs.describe(binding, state, current['id'])
    assert result[0]['available'] is False and result[0]['value'] is None
    assert result[0]['availability_code'] == ('ambiguous' if ambiguous else 'missing')
    assert result[0]['producer_activation_id'] is None
    with pytest.raises(HTTPException) as error: inputs.require_inputs(binding, state, current['id'])
    assert error.value.status_code == 409 and state == before


def entry(mode, kind='manual', *, simulation=True):
    return {'schema': runtime.NESTED_BINDING, 'flow': {'source_flow': {'trigger': {'kind': kind}}},
        'entry_policy': {'mode': mode, 'source_trigger_kind': kind}, 'simulation_only': simulation,
        'review_mode': 'engineering_simulation' if simulation else 'author_self_review',
        'event_admission_policy': None}


@pytest.mark.parametrize('kind', ['manual', 'event', 'schedule'])
def test_explicit_engineering_start_preserves_source_trigger(kind):
    binding = entry('engineering_manual', kind); before = copy.deepcopy(binding)
    runtime.require_task_start(binding, verified_anomaly_handoff=False)
    assert binding == before


@pytest.mark.parametrize('fault', ['implicit', 'author_engineering', 'sim_declared', 'event_manual',
    'unbound_handoff', 'unverified_handoff', 'trigger_changed', 'boolean_coercion'])
def test_entry_policy_cannot_infer_event_human_or_production_permission(fault):
    binding = entry('engineering_manual'); verified = False
    if fault == 'implicit': binding.pop('entry_policy')
    elif fault == 'author_engineering': binding.update(review_mode='author_self_review', simulation_only=False)
    elif fault == 'sim_declared': binding['entry_policy']['mode'] = 'declared_manual'
    elif fault == 'event_manual': binding = entry('declared_manual', 'event', simulation=False)
    elif fault in {'unbound_handoff', 'unverified_handoff'}:
        binding = entry('authorized_anomaly_handoff', 'event', simulation=False)
        if fault == 'unverified_handoff': binding['event_admission_policy'] = {'schema': 'business-flow-nested-event-admission-v195'}
        else: verified = True
    elif fault == 'trigger_changed': binding['entry_policy']['source_trigger_kind'] = 'event'
    else: binding['simulation_only'] = 1
    before = copy.deepcopy(binding)
    with pytest.raises(HTTPException) as error: runtime.require_task_start(binding, verified_anomaly_handoff=verified)
    assert error.value.status_code == 409 and binding == before


def test_author_manual_is_distinct_from_simulation():
    runtime.require_task_start(entry('declared_manual', simulation=False), verified_anomaly_handoff=False)


def projection_case(availability):
    """Real kernel + input/public serializers; synthetic adoption selection only."""
    binding = adapter_binding(ambiguous_graph() if availability == 'ambiguous'
        else choice_graph(nullable=True, empty=availability == 'missing'))
    binding.update(review_mode='engineering_simulation', simulation_only=True)
    native = binding['graph']; state = graph.new_run(native, {}, 10)
    if availability == 'ambiguous':
        state = finish_node(native, state, 'a', {'ok': True})
        state = finish_node(native, state, 'b', {'ok': True})
    else:
        state = finish_node(native, state, 'choose', {'ok': availability == 'ready'})
        if availability == 'ready': state = finish_node(native, state, 'left', {'ok': None})
    current = activation(state, 'consume', status='ready' if availability == 'ready' else 'waiting_guard')
    state = graph.save_draft(native, state, current['id'], {'ok': True}, 'draft_saved_01',
        'operator_consume', current['version'], current['epoch'], 10)
    task = Task(id='public_nested_task_' + availability, workspace_id='public_workspace',
        owner_id='operator_consume', package_id='public_package', status='workflow_waiting', version=2,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        data={'graph_runtime': state, 'graph_jobs': {}, 'workflow_runtime': {'receipts': []}})
    business = {'name': '分支结果核对', 'flow': {'steps': [
        {'id': node['source_step_id'], 'name': '人工核对分支结果', 'purpose': '保留依据并等待输入可用'}
        for node in native['nodes'] if node['kind'] == 'human']}}
    release = NS(data={'snapshot': {'roles': {}, 'profile': {}}})
    principal = NS(user_id='operator_consume', workspace_id='public_workspace', role='operator')
    with patch.object(workflow_graph, 'business_binding', return_value=binding):
        result = workflow_graph.public_view(task, release, business, principal)
    return binding, state, result


@pytest.mark.parametrize('availability', ['ready', 'missing', 'ambiguous'])
def test_actual_public_view_preserves_waiting_human_and_saved_draft_without_readiness_grant(availability):
    binding, state, result = projection_case(availability)
    assert len(result['graph_pending_humans']) == 1
    human_view = result['graph_pending_humans'][0]
    assert human_view['activation_status'] == ('ready' if availability == 'ready' else 'waiting_guard')
    assert human_view['can_complete'] is (availability == 'ready')
    assert human_view['can_edit'] is True and human_view['draft'] == {'ok': True}
    assert human_view['required_inputs'][0]['availability_code'] == availability
    assert human_view['activation_id'] in state['ready_human_ids'] if availability == 'ready' else not state['ready_human_ids']
    assert graph.check_run(binding['graph'], json.loads(json.dumps(state))) == state


def test_checked_in_public_ui_fixture_is_actual_projection_not_a_fake_ready_missing_pair():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/nested_task_waiting_inputs_v195.json').read_text())
    assert fixture['source'] == 'native_public_view'
    assert fixture['adoption_authority_verified'] is False
    for item in fixture['cases']:
        assert item['detail'] == projection_case(item['availability'])[2]
