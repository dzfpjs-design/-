import copy

import pytest

from generic_agents.contracts import digest
from generic_agents import native_pixel_data_plan_v250 as subject


def snapshot():
    return {'businesses': [{'id': 'business-opaque-7', 'name': '通用图像核查', 'flow': {'steps': [
        {'id': 'pixel', 'execution': {'adapter': 'local_pixel_change', 'capability_id': 'cap-1',
            'capability_hash': 'a' * 64, 'release_version': '1.0.0', 'agent_role': 'perception',
            'output_schema': {'type': 'object', 'properties': {'pixel_run_id': {'type': 'string'}},
                              'required': ['pixel_run_id'], 'additionalProperties': False}}},
        {'id': 'human', 'agent': '人工确认'}]}}]}


def test_plan_is_deterministic_additive_and_does_not_rewrite_input():
    value = snapshot(); before = copy.deepcopy(value)
    plan = subject.build(value)
    assert value == before and plan == subject.build(copy.deepcopy(value))
    assert plan['plan_hash'] == digest({k: v for k, v in plan.items() if k != 'plan_hash'})
    assert plan['requires_independent_ack'] and not plan['automatic_domain_use']
    assert not plan['field_truth_verified'] and not plan['deletion_authorized']
    assert plan['bindings'][0]['storage']['human_work'] == 'original_task_receipts_and_independent_human_audit'
    assert plan['bindings'][0]['source_family'] == subject.FAMILY
    assert set(plan['bindings'][0]['artifact_kinds']) == set(subject.ARTIFACTS)
    value[subject.KEY] = plan
    assert subject.verify(value) == plan


@pytest.mark.parametrize('mutation', ['missing', 'capability', 'schema', 'registration', 'storage', 'bool_as_int', 'hash'])
def test_missing_or_resealed_changed_design_is_not_a_valid_new_release(mutation):
    value = snapshot(); value[subject.KEY] = subject.build(value)
    if mutation == 'missing':
        del value[subject.KEY]
    else:
        plan = value[subject.KEY]
        if mutation == 'capability': plan['bindings'][0]['capability_hash'] = 'b' * 64
        elif mutation == 'schema': plan['bindings'][0]['output_schema']['required'] = []
        elif mutation == 'registration': plan['date_basis'] = 'observed_at'
        elif mutation == 'storage': plan['bindings'][0]['storage']['reception'] = 'workflow_result_memory'
        elif mutation == 'bool_as_int': plan['requires_independent_ack'] = 1
        elif mutation == 'hash': plan['plan_hash'] = '0' * 64
        if mutation != 'hash': plan['plan_hash'] = digest({k: v for k, v in plan.items() if k != 'plan_hash'})
    with pytest.raises(ValueError): subject.verify(value)


def test_unrelated_legacy_snapshot_keeps_bytes_and_cannot_claim_pixel_plan():
    value = snapshot(); value['businesses'][0]['flow']['steps'][0]['execution']['adapter'] = 'builtin'
    before = copy.deepcopy(value)
    assert subject.build(value) is None and subject.verify(value) is None and value == before
    value[subject.KEY] = {'schema': subject.SCHEMA}
    with pytest.raises(ValueError): subject.verify(value)


def test_graph_nodes_are_the_actual_pinned_sources_without_double_counting_flow_copy():
    value = snapshot(); pin = copy.deepcopy(value['businesses'][0]['flow']['steps'][0]['execution'])
    value['semantic_runtime_plan'] = {'graph_adoptions': [{'binding': {'business_name': '通用图像核查',
        'graph': {'nodes': [{'id': 'node-pixel', 'kind': 'ability', 'source_step_id': 'pixel', 'execution': pin},
                            {'id': 'end', 'kind': 'end'}]}}}]}
    plan = subject.build(value)
    assert len(plan['bindings']) == 1 and plan['bindings'][0]['node_id'] == 'node-pixel'


def test_unknown_business_or_model_cannot_borrow_declared_output():
    value = snapshot(); value[subject.KEY] = subject.build(value)
    plan, bindings = subject.declared(value, 'business-opaque-7', 'cap-1', 'a' * 64)
    assert plan == value[subject.KEY] and len(bindings) == 1
    for business, capability, sha in [('other', 'cap-1', 'a' * 64),
                                      ('business-opaque-7', 'cap-2', 'a' * 64),
                                      ('business-opaque-7', 'cap-1', 'b' * 64)]:
        with pytest.raises(ValueError): subject.declared(value, business, capability, sha)
