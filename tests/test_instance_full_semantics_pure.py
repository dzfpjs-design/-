"""Synthetic unit snapshots only; no PG/API, source approval or execution proof.

Designs/flows and compiler business identifiers use the real pure functions.
The one fake Capability lookup below tests only a negative release boundary.
"""
import copy
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents import instance_full_semantics as plans
from generic_agents import report_full_semantic_configuration_v1 as semantic
from generic_agents.contracts import digest
from generic_agents.instance_compiler import identifier
from tests.test_report_full_semantic_configuration_v1_pure import build, result


def snapshot():
    design = build()
    flows = semantic.derive_flows(design)
    businesses = [{'id': identifier('business', name), 'name': name, 'flow': flow,
        'issues': semantic.execution_issues(design, index), 'available': False, 'execution_mode': 'blocked'}
        for index, (name, flow) in enumerate(flows.items())]
    return {'businesses': businesses, 'semantic_plan': plans.plan_value([design], businesses)}


def reseal_plan(value):
    plan = value['semantic_plan']
    plan['plan_hash'] = digest({key: item for key, item in plan.items() if key != 'plan_hash'})


def rejected(call):
    with pytest.raises((HTTPException, ValueError)) as error:
        call()
    if isinstance(error.value, HTTPException):
        assert error.value.status_code == 409


def test_real_plan_keeps_whole_result_raw_indices_and_immutable_input():
    value = snapshot(); before = copy.deepcopy(value)
    checked = plans.verify_plan(value)
    assert checked == value['semantic_plan'] and checked is not value['semantic_plan']
    assert checked['designs'][0]['result'] == result()
    assert checked['execution_policy'] == 'blocked_pending_full_semantic_runtime_v1'
    assert checked['business_bindings'] == [{'business_id': business['id'], 'name': business['name'],
        'source': semantic.signature_context({'fullSemanticDesigns': checked['designs'],
            'flows': {b['name']: b['flow'] for b in value['businesses']}}, business['flow'])}
        for business in value['businesses']]
    assert [step['next_index'] for step in checked['designs'][0]['result']['businesses'][0]['steps']] == [1, 0]
    assert checked['designs'][0]['result']['businesses'][1]['steps'][0]['next_index'] is None
    assert plans.pins(value) == {'full_semantic_plan_schema': plans.SCHEMA,
        'full_semantic_plan_hash': checked['plan_hash']}
    assert value == before
    checked['designs'][0]['result']['questions'].append('只改返回副本')
    assert value == before


def test_all_absent_markers_remain_legacy_without_fabricating_an_empty_plan():
    for value in [{}, {'businesses': []}, {'businesses': [{'id': 'legacy', 'name': '旧业务',
            'flow': {'trigger': '旧触发'}, 'available': True, 'execution_mode': 'confirmed_human_workflow'}]}]:
        before = copy.deepcopy(value)
        assert plans.verify_plan(value) is None and plans.pins(value) == {}
        assert value == before


@pytest.mark.parametrize('mode', ['plan', 'all-flow-markers', 'one-flow-marker'])
def test_one_sided_marker_removal_never_downgrades_to_legacy(mode):
    value = snapshot()
    if mode == 'plan': value.pop('semantic_plan')
    else:
        targets = value['businesses'] if mode == 'all-flow-markers' else value['businesses'][:1]
        for business in targets: business['flow'].pop('fullSemanticSource')
    before = copy.deepcopy(value)
    rejected(lambda: plans.verify_plan(value)); rejected(lambda: plans.pins(value))
    assert value == before


@pytest.mark.parametrize('mode', ['all', 'last', 'duplicate-id', 'duplicate-name'])
def test_missing_or_duplicate_business_bindings_are_rejected_by_build_and_cold_read(mode):
    value = snapshot()
    if mode == 'all': value['businesses'] = []
    elif mode == 'last': value['businesses'].pop()
    elif mode == 'duplicate-id': value['businesses'][1]['id'] = value['businesses'][0]['id']
    else: value['businesses'][1]['name'] = value['businesses'][0]['name']
    before = copy.deepcopy(value)
    rejected(lambda: plans.plan_value(value['semantic_plan']['designs'], value['businesses']))
    rejected(lambda: plans.verify_plan(value))
    assert value == before


def test_resealing_plan_hash_and_removing_a_binding_does_not_hide_the_omitted_business():
    value = snapshot(); value['businesses'].pop()
    value['semantic_plan']['business_bindings'].pop()
    reseal_plan(value)
    assert len(value['semantic_plan']['designs'][0]['result']['businesses']) == 2
    assert len(value['businesses']) == len(value['semantic_plan']['business_bindings']) == 1
    before = copy.deepcopy(value)
    rejected(lambda: plans.verify_plan(value)); assert value == before


@pytest.mark.parametrize('businesses', [None, {}, '', [None], ['invalid'], [{'flow': None}],
    [{'id': False, 'name': '坏类型', 'flow': {}}], [{'id': 'bad', 'name': None, 'flow': {}}]])
def test_malformed_business_containers_fail_closed_without_typeerror_or_attributeerror(businesses):
    value = snapshot(); value['businesses'] = copy.deepcopy(businesses)
    before = copy.deepcopy(value)
    rejected(lambda: plans.verify_plan(value))
    rejected(lambda: plans.plan_value(value['semantic_plan']['designs'], value['businesses']))
    assert value == before


@pytest.mark.parametrize('field,bad', [('available', True), ('available', 0), ('available', None),
    ('execution_mode', 'confirmed_human_workflow'), ('execution_mode', 'registered_ability_workflow'),
    ('execution_mode', None)])
def test_truthy_flags_and_execution_mode_labels_cannot_make_a_design_runnable(field, bad):
    value = snapshot(); value['businesses'][0][field] = bad
    before = copy.deepcopy(value)
    rejected(lambda: plans.verify_plan(value)); rejected(lambda: plans.pins(value))
    assert value == before


@pytest.mark.parametrize('mode', ['design-hash', 'raw-fact', 'mapping', 'policy', 'extra-field'])
def test_full_plan_hash_reseal_does_not_validate_damaged_design_or_unknown_plan_shape(mode):
    value = snapshot(); plan = value['semantic_plan']
    if mode == 'design-hash': plan['designs'][0]['design_hash'] = '0' * 64
    elif mode == 'raw-fact': plan['designs'][0]['result']['global_claims'][0]['statement'] += '篡改'
    elif mode == 'mapping': plan['designs'][0]['business_bindings'][0]['participant_ids'].reverse()
    elif mode == 'policy': plan['execution_policy'] = 'runnable'
    else: plan['can_execute'] = True
    reseal_plan(value); before = copy.deepcopy(value)
    rejected(lambda: plans.verify_plan(value)); assert value == before


def test_plan_value_rejects_bad_design_container_and_does_not_mutate_either_input():
    value = snapshot(); designs = value['semantic_plan']['designs']; businesses = value['businesses']
    for bad in [None, {}, 'invalid', [None], [{}]]:
        rejected(lambda: plans.plan_value(bad, businesses))
    before = copy.deepcopy((designs, businesses))
    assert plans.plan_value(designs, businesses) == value['semantic_plan']
    assert (designs, businesses) == before


def release(value, *, package_id=None):
    return NS(workspace_id='synthetic-workspace', package_id=package_id,
        configuration_hash=digest(value), data={'snapshot': copy.deepcopy(value), **plans.pins(value)})


def test_unit_release_gate_remains_blocked_even_when_every_structural_pin_is_correct(monkeypatch):
    value = snapshot(); current = release(value, package_id='synthetic-package')
    spec = {'business_definitions': copy.deepcopy(value['businesses']), **plans.pins(value)}
    package = NS(data={'specification': spec})
    calls = []
    def fake_scoped(db, model, identity, principal):
        calls.append(identity); assert identity == current.package_id
        return package
    monkeypatch.setattr(plans, 'scoped', fake_scoped)
    before = copy.deepcopy(current.data)
    with pytest.raises(HTTPException) as error:
        plans.verify_release(None, NS(workspace_id=current.workspace_id), current)
    assert error.value.status_code == 409 and '正式来源和协同执行核对' in error.value.detail
    assert calls == ['synthetic-package'] and current.data == before
    # This fake lookup is solely a unit denial, not proof of a real published Capability.


@pytest.mark.parametrize('mode', ['release-pins', 'package-pins', 'snapshot-plan', 'snapshot-marker',
    'only-package-marker', 'snapshot-hash', 'workspace'])
def test_release_single_side_damage_cannot_fall_back_to_legacy(monkeypatch, mode):
    value = snapshot(); current = release(value, package_id='synthetic-package')
    spec = {'business_definitions': copy.deepcopy(value['businesses']), **plans.pins(value)}
    if mode == 'release-pins':
        current.data.pop('full_semantic_plan_hash')
    elif mode == 'package-pins':
        spec.pop('full_semantic_plan_hash')
    elif mode == 'snapshot-plan':
        current.data['snapshot'].pop('semantic_plan')
        current.configuration_hash = digest(current.data['snapshot'])
    elif mode == 'snapshot-marker':
        current.data['snapshot']['businesses'][0]['flow'].pop('fullSemanticSource')
        current.configuration_hash = digest(current.data['snapshot'])
    elif mode == 'only-package-marker':
        current.data = {'snapshot': {'businesses': []}}
        current.configuration_hash = digest(current.data['snapshot'])
    elif mode == 'snapshot-hash': current.configuration_hash = '0' * 64
    else: current.workspace_id = 'another-workspace'
    monkeypatch.setattr(plans, 'scoped', lambda *args: NS(data={'specification': spec}))
    before = copy.deepcopy(current.data)
    rejected(lambda: plans.verify_release(None, NS(workspace_id='synthetic-workspace'), current))
    assert current.data == before


def test_all_absent_release_markers_stay_legacy_without_a_db_lookup(monkeypatch):
    def no_lookup(*args):
        raise AssertionError('legacy package-less unit path must not query a fake DB')
    monkeypatch.setattr(plans, 'scoped', no_lookup)
    current = NS(workspace_id='synthetic-workspace', package_id=None, configuration_hash='unused',
        data={'snapshot': {'businesses': []}})
    assert plans.verify_release(None, NS(workspace_id=current.workspace_id), current) is None
