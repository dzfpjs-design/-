"""Exact original AST paths and closed causal sidecar coverage over public data.

No original report, paid answer, entry facts, semantic quality or permission is
proved. Availability-algorithm behavior belongs to its independent test suite;
the explicit delegation seam checks the path layer's actual input contract.
"""
import copy

import pytest

from generic_agents import business_flow_causal_paths_v189 as subject
from generic_agents import report_business_flow_wire_v184 as plans
from generic_agents.contracts import digest
from tests.test_report_business_flow_wire_v184 import no_external, scene, phases, at


def fixture_plan():
    plan, source, action, steps, attribution = scene(4)
    plan['business']['flows'][0]['phases'] = phases(action, steps, attribution)
    return plan, source


def declared(plan, binding):
    walked = subject.walk_action_ports(plan, scope_binding=binding)
    contract = {'data_type': 'text', 'unit': None}
    entries = [{'path': '/entry_ports/' + str(index), 'flow_path': '/business/flows/' + str(index),
        'contract': copy.deepcopy(contract)} for index in range(len(plan['business']['flows']))]
    inputs, outputs = [], []
    for action in walked['actions']:
        for port in action['input_ports']:
            inputs.append({'input_path': port['path'], 'contract': copy.deepcopy(contract),
                'sources': ['/entry_ports/' + str(action['flow_index'])]})
        for port in action['output_ports']:
            outputs.append({'path': port['path'], 'contract': copy.deepcopy(contract)})
    return {'schema': subject.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(binding), 'entry_ports': entries,
        'output_contracts': outputs, 'input_bindings': inputs}


def test_every_phase_action_is_walked_in_original_order_with_real_control_positions():
    plan, source = fixture_plan()
    result = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    prefix = '/business/flows/0'
    expected = [prefix + suffix for suffix in (
        '/phases/0/steps/0', '/phases/0/steps/1', '/phases/1/decision',
        '/phases/1/branches/1/steps/0', '/phases/2/dispatch',
        '/phases/2/branches/0/steps/0', '/phases/2/branches/0/steps/1',
        '/phases/2/branches/1/steps/0', '/phases/3/steps/0',
        '/phases/3/steps/1', '/phases/3/check', '/completion')]
    assert [item['path'] for item in result['actions']] == expected
    assert result['source_plan_schema'] == plans.WIRE
    assert result['source_plan_hash'] == digest(plan)
    assert result['scope_binding_hash'] == digest(source['scope_binding'])
    branch = result['actions'][3]
    assert branch['control_ancestors'] == [
        {'path': prefix + '/phases/1', 'kind': 'decision', 'branch_index': None},
        {'path': prefix + '/phases/1/branches/1', 'kind': 'decision_branch', 'branch_index': 1}]
    assert result['actions'][5]['control_ancestors'][-1] == {
        'path': prefix + '/phases/2/branches/0', 'kind': 'parallel_branch', 'branch_index': 0}
    assert result['actions'][10]['part'] == 'check'
    assert result['actions'][-1]['control_ancestors'] == [] and result['actions'][-1]['phase_index'] is None
    assert result['causal_declaration_consistency_only'] is True
    assert all(result[key] is False for key in subject._FLAGS)
    assert result['walk_hash'] == digest({key: value for key, value in result.items() if key != 'walk_hash'})


def test_zero_phases_and_duplicate_flow_or_action_names_never_merge_source_identity():
    plan, source, _, _, _ = scene(4)
    flow = plan['business']['flows'][0]
    flow['phases'] = []
    plan['business']['flows'].append(copy.deepcopy(flow))
    result = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    assert [item['path'] for item in result['actions']] == [
        '/business/flows/0/completion', '/business/flows/1/completion']
    assert [item['flow_index'] for item in result['actions']] == [0, 1]
    assert result['actions'][0]['name'] == result['actions'][1]['name']
    assert all(item['phase_index'] is None and item['part'] == 'completion' for item in result['actions'])


def test_repeated_port_labels_keep_original_ordinals_even_when_the_real_compiler_deduplicates():
    plan, source, _, _, _ = scene(4)
    action = plan['business']['flows'][0]['phases'][0]['steps'][0]
    action['inputs'] = ['同名输入', '同名输入']
    action['outputs'] = ['同名输出', '同名输出']
    result = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    first = result['actions'][0]
    assert first['input_ports'] == [{'path': first['path'] + '/inputs/' + str(i), 'ordinal': i,
        'label': '同名输入'} for i in range(2)]
    assert first['output_ports'] == [{'path': first['path'] + '/outputs/' + str(i), 'ordinal': i,
        'label': '同名输出'} for i in range(2)]
    projection = plans.build_projection(plans._bytes(plan), **source)
    compiled = projection['wire']['business']['flows'][0]['nodes'][0]
    assert compiled['inputs'] == ['同名输入'] and compiled['outputs'] == ['同名输出']
    assert len(first['input_ports']) == len(first['output_ports']) == 2


def test_all_returned_ports_resolve_to_actual_raw_arrays_not_compiled_or_named_positions():
    plan, source = fixture_plan()
    walked = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    flat_inputs, flat_outputs = [], []
    for item in walked['actions']:
        action = at(plan, item['path'])
        assert (item['name'], item['actor'], item['role']) == (action['name'], action['actor'], action['role'])
        assert at(plan, item['flow_path']) is plan['business']['flows'][item['flow_index']]
        for field, flat in (('input_ports', flat_inputs), ('output_ports', flat_outputs)):
            for ordinal, port in enumerate(item[field]):
                assert port['ordinal'] == ordinal and at(plan, port['path']) == port['label']
                flat.append(port['path'])
    assert walked['input_paths'] == flat_inputs and walked['output_paths'] == flat_outputs
    assert len(set(flat_inputs)) == len(flat_inputs) and len(set(flat_outputs)) == len(flat_outputs)


@pytest.mark.parametrize('fault', ['schema', 'name', 'binding_hash', 'scope', 'extra', 'bad_role'])
def test_actual_v184_shape_and_independent_binding_cannot_be_replaced_by_a_walk_self_hash(fault):
    plan, source = fixture_plan()
    binding = copy.deepcopy(source['scope_binding'])
    if fault == 'schema': plan['schema'] = 'report-business-flow-wire-v182'
    elif fault == 'name': plan['business']['name'] = '另一业务'
    elif fault == 'binding_hash': plan['binding_hash'] = digest('different')
    elif fault == 'scope': binding['scope_id'] = 'another-scope'
    elif fault == 'extra': plan['authority'] = True
    else: plan['business']['flows'][0]['phases'][0]['steps'][0]['role'] = ' '
    with pytest.raises(ValueError):
        subject.walk_action_ports(plan, scope_binding=binding)


@pytest.mark.parametrize('fault', ['plan_hash', 'binding_hash', 'missing_input', 'duplicate_input',
    'missing_output', 'duplicate_output', 'unknown_input', 'unknown_output', 'entry_path',
    'entry_flow', 'contract_extra', 'unit_type', 'datatype_type', 'unknown_source',
    'input_as_source', 'no_source', 'duplicate_source', 'extra', 'loop_carried'])
def test_closed_sidecar_requires_exact_whole_port_coverage_before_availability(monkeypatch, fault):
    from generic_agents import business_flow_causal_availability_v189 as availability
    plan, source = fixture_plan()
    value = declared(plan, source['scope_binding'])
    if fault == 'plan_hash': value['source_plan_hash'] = digest('other')
    elif fault == 'binding_hash': value['scope_binding_hash'] = digest('other')
    elif fault == 'missing_input': value['input_bindings'].pop()
    elif fault == 'duplicate_input': value['input_bindings'].append(copy.deepcopy(value['input_bindings'][0]))
    elif fault == 'missing_output': value['output_contracts'].pop()
    elif fault == 'duplicate_output': value['output_contracts'].append(copy.deepcopy(value['output_contracts'][0]))
    elif fault == 'unknown_input': value['input_bindings'][0]['input_path'] = '/business/flows/0/outputs/0'
    elif fault == 'unknown_output': value['output_contracts'][0]['path'] = '/business/flows/0/outputs/0'
    elif fault == 'entry_path': value['entry_ports'][0]['path'] = '/entries/0'
    elif fault == 'entry_flow': value['entry_ports'][0]['flow_path'] = '/business/flows/99'
    elif fault == 'contract_extra': value['input_bindings'][0]['contract']['permission'] = True
    elif fault == 'unit_type': value['output_contracts'][0]['contract']['unit'] = 1
    elif fault == 'datatype_type': value['input_bindings'][0]['contract']['data_type'] = True
    elif fault == 'unknown_source': value['input_bindings'][0]['sources'] = ['some_external_source']
    elif fault == 'input_as_source': value['input_bindings'][0]['sources'] = [value['input_bindings'][0]['input_path']]
    elif fault == 'no_source': value['input_bindings'][0]['sources'] = []
    elif fault == 'duplicate_source': value['input_bindings'][0]['sources'] *= 2
    elif fault == 'loop_carried': value['input_bindings'][0]['loop_carried'] = True
    else: value['permission'] = True
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('Bad sidecar must fail before availability analysis')
    monkeypatch.setattr(availability, 'analyze_availability', forbidden)
    with pytest.raises(ValueError):
        subject.analyze_availability(plan, value, scope_binding=source['scope_binding'])
    assert called == []


def test_delegation_receives_only_the_exact_parsed_plan_original_port_metadata_and_declared_contracts(monkeypatch):
    from generic_agents import business_flow_causal_availability_v189 as availability
    plan, source = fixture_plan()
    binding = source['scope_binding']
    declared_value = declared(plan, binding)
    walked = subject.walk_action_ports(plan, scope_binding=binding)
    before = copy.deepcopy([plan, binding, declared_value])
    calls = []
    def pure_boundary(actual_plan, *, action_ports, entry_ports, input_bindings, output_contracts, max_scenarios):
        assert actual_plan == plan and action_ports == walked['actions']
        assert entry_ports == declared_value['entry_ports']
        assert input_bindings == declared_value['input_bindings']
        assert output_contracts == declared_value['output_contracts'] and max_scenarios == 17
        calls.append(True)
        # Copies protect independent caller inputs even from a changed callee.
        action_ports.clear()
        entry_ports.clear()
        input_bindings.clear()
        output_contracts.clear()
        actual_plan['business']['name'] = '被调用方的本机改写'
        return {'schema': 'synthetic-availability-boundary', 'issues': []}
    monkeypatch.setattr(availability, 'analyze_availability', pure_boundary)
    result = subject.analyze_availability(plan, declared_value, scope_binding=binding, max_scenarios=17)
    assert calls == [True] and [plan, binding, declared_value] == before
    assert result['paths'] == walked and result['bindings'] == declared_value
    assert result['bindings_hash'] == digest(declared_value)
    assert result['availability'] == {'schema': 'synthetic-availability-boundary', 'issues': []}
    assert result['causal_declaration_consistency_only'] is True
    assert all(result[key] is False for key in subject._FLAGS)
    assert result['analysis_hash'] == digest({key: value for key, value in result.items() if key != 'analysis_hash'})


@pytest.mark.parametrize('maximum', [True, 0, -1, 4097, 1.0])
def test_scenario_limit_is_strict_and_cannot_expand_the_analysis_budget(maximum):
    plan, source = fixture_plan()
    value = declared(plan, source['scope_binding'])
    with pytest.raises(ValueError):
        subject.analyze_availability(plan, value, scope_binding=source['scope_binding'], max_scenarios=maximum)


def test_walk_is_detached_and_never_mutates_raw_names_arrays_or_source_binding():
    plan, source = fixture_plan()
    before = copy.deepcopy([plan, source])
    walked = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    original = copy.deepcopy(walked)
    walked['actions'][0]['input_ports'][0]['label'] = '本机修改'
    walked['actions'][3]['control_ancestors'].clear()
    walked['input_paths'].clear()
    assert [plan, source] == before
    assert subject.walk_action_ports(plan, scope_binding=source['scope_binding']) == original


def test_real_availability_module_is_used_without_promoting_proposed_entries_to_facts():
    from generic_agents import business_flow_causal_availability_v189 as availability
    plan, source, _, _, _ = scene(4)
    binding = source['scope_binding']
    value = declared(plan, binding)
    walked = subject.walk_action_ports(plan, scope_binding=binding)
    expected = availability.analyze_availability(plan, action_ports=walked['actions'],
        entry_ports=value['entry_ports'], input_bindings=value['input_bindings'],
        output_contracts=value['output_contracts'], max_scenarios=4096)
    result = subject.analyze_availability(plan, value, scope_binding=binding)
    assert result['availability'] == expected
    assert result['source_plan_hash'] == digest(plan) and result['scope_binding_hash'] == digest(binding)
    assert result['causal_declaration_consistency_only'] is True and result['entry_facts_verified'] is False
    assert all(result[key] is False for key in subject._FLAGS)
