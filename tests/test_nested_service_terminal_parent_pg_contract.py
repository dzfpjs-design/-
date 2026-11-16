"""Terminal proof boundaries for a recursive service's actual translated node.

Uses the explicit IO-boundary fixture from the V264 terminal custody tests.
Real nested publication, HTTP, and independent ACK are exercised separately.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_nested_adoption_v195 as adoption
from generic_agents import business_flow_nested_execution_v195 as compiler
from generic_agents import workflow_job_dependencies_v264 as dependencies
from generic_agents import workflow_service_context_v196 as variant
from tests.test_workflow_service_terminal_parent_v293 import read, scene, write


def recursive(monkeypatch):
    s = scene(monkeypatch)
    s.node['id'] = 'action_recursive_17'
    execution = s.node['execution']
    execution.update(adapter='workflow_service', agent_role='business',
        release_version=s.cap.release_version, input_schema=s.cap.data['input_schema'],
        output_schema=s.cap.data['output_schema'], max_attempts=1,
        input_bindings={'context': 'original_context'}, arguments={})
    s.binding.update(schema=adoption.BINDING, translation={
        'schema': compiler.SCHEMA,
        'source_mapping': {'actions': [{'graph_node_id': s.node['id'],
            'service_delegation': compiler.service_delegation('execution_service', variant.OP)}]},
    }, capability_pins={s.node['id']: copy.deepcopy(execution)})
    return s


def test_recursive_terminal_parent_preserves_original_waiting_invoke_and_proves_later_receipt(monkeypatch):
    s = recursive(monkeypatch)
    original = copy.deepcopy(s.initial.data)
    assert write(s) is True
    terminal, = s.rows[dependencies.SERVICE_TERMINAL]
    assert terminal.data['initial_invoke_audit_id'] == s.initial.id
    assert terminal.data['source_binding_hash'] == s.binding['binding_hash']
    assert terminal.data['activation_id'] == s.job.data['activation_id']
    read(s)
    assert s.initial.data == original == {'status': 'waiting_receipt'}


@pytest.mark.parametrize('fault', ['other_node', 'legacy_operation', 'no_delegation', 'not_simulation',
    'no_side_effect', 'other_service_actor', 'wrong_capability_pin', 'wrong_capability_release', 'wrong_node_role'])
def test_recursive_terminal_parent_rejects_unrelated_or_non_simulation_delegation(monkeypatch, fault):
    s = recursive(monkeypatch)
    action = s.binding['translation']['source_mapping']['actions'][0]
    if fault == 'other_node':
        action['graph_node_id'] = 'resense'
    elif fault == 'legacy_operation':
        action['service_delegation'] = compiler.service_delegation('execution_service', 'resense_with_receipt')
    elif fault == 'no_delegation':
        del action['service_delegation']
    elif fault == 'not_simulation':
        s.binding['simulation_only'] = False
    elif fault == 'no_side_effect':
        s.cap.data['execution']['side_effect'] = False
    elif fault == 'other_service_actor':
        action['service_delegation'] = compiler.service_delegation('task_center', 'in_app_notice')
    elif fault == 'wrong_capability_pin':
        s.binding['capability_pins'][s.node['id']]['capability_hash'] = 'f' * 64
    elif fault == 'wrong_capability_release':
        s.cap.release_version = '3.0.0'
    else:
        s.node['execution']['agent_role'] = 'data'
        s.binding['capability_pins'][s.node['id']]['agent_role'] = 'data'
    assert write(s) is False
    with pytest.raises(HTTPException) as blocked:
        read(s)
    assert blocked.value.status_code == 409 and s.rows[dependencies.SERVICE_TERMINAL] == []


def test_recursive_terminal_parent_rejects_ambiguous_translated_node(monkeypatch):
    s = recursive(monkeypatch)
    actions = s.binding['translation']['source_mapping']['actions']
    actions.append(copy.deepcopy(actions[0]))
    with pytest.raises(HTTPException):
        write(s)
    assert s.rows[dependencies.SERVICE_TERMINAL] == []


def test_recursive_terminal_parent_rejects_relabelled_delegate_role(monkeypatch):
    s = recursive(monkeypatch)
    action = s.binding['translation']['source_mapping']['actions'][0]
    action['service_delegation']['agent_role'] = 'data'
    with pytest.raises(HTTPException):
        write(s)
    assert s.rows[dependencies.SERVICE_TERMINAL] == []
