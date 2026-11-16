"""Actual pure V184 paths + V189 declared causality over synthetic plans.

No report, provider, IAM, real trigger fact, source authority, executable data
mapping, approval permission or semantic quality is proved. Root runs tests.
"""
import copy

import pytest

from generic_agents import business_flow_causal_availability_v189 as subject
from generic_agents import business_flow_causal_paths_v189 as paths
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external, scene as base_scene


FP = '/business/flows/0'
CONTRACT = {'data_type': 'record', 'unit': None}


def scene():
    plan, source, original_action, _, attribution = base_scene(4)

    def action(name='核对资料', *, inputs=None, outputs=None):
        return original_action(name, inputs=[] if inputs is None else inputs,
            outputs=['资料记录'] if outputs is None else outputs)

    flow = plan['business']['flows'][0]
    flow['phases'] = []
    flow['completion'] = action('保存完成结果', outputs=['完成记录'])
    return plan, source, action, attribution


def steps(actions, attribution):
    return {'kind': 'steps', 'steps': actions, 'attribution': copy.deepcopy(attribution)}


def parallel(action, attribution, *, mode='all', branch_inputs=None):
    return {'kind': 'parallel', 'dispatch': action('安排并行核对'),
        'attribution': copy.deepcopy(attribution), 'join_attribution': copy.deepcopy(attribution),
        'wait_mode': mode, 'branches': [{'steps': [action('分支核对' + str(i),
            inputs=[] if branch_inputs is None else branch_inputs)]} for i in range(2)]}


def decision(action, attribution, *, empty=False, count=2):
    return {'kind': 'decision', 'decision': action('判断当前条件'),
        'attribution': copy.deepcopy(attribution), 'branches': [
            {'condition': '条件' + str(i), 'attribution': copy.deepcopy(attribution),
                'steps': [] if empty else [action('条件核对' + str(i))]}
            for i in range(count)]}


def declared(plan, source):
    walk = paths.walk_action_ports(plan, scope_binding=source['scope_binding'])
    flow_paths = ['/business/flows/' + str(i) for i in range(len(plan['business']['flows']))]
    entries = [{'path': '/entry_ports/' + str(i), 'flow_path': fp, 'contract': copy.deepcopy(CONTRACT)}
        for i, fp in enumerate(flow_paths)]
    entry_for = {entry['flow_path']: entry['path'] for entry in entries}
    return {'schema': paths.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(source['scope_binding']), 'entry_ports': entries,
        'output_contracts': [{'path': p, 'contract': copy.deepcopy(CONTRACT)} for p in walk['output_paths']],
        'input_bindings': [{'input_path': port['path'], 'contract': copy.deepcopy(CONTRACT),
            'sources': [entry_for[row['flow_path']]]}
            for row in walk['actions'] for port in row['input_ports']]}


def bind(bindings, input_path, *sources):
    next(item for item in bindings['input_bindings'] if item['input_path'] == input_path)['sources'] = list(sources)


def analyze(plan, source, bindings, *, maximum=4096):
    return paths.analyze_availability(plan, bindings, scope_binding=source['scope_binding'],
        max_scenarios=maximum)['availability']


def test_real_source_order_sequence_checks_inputs_before_outputs_without_claiming_semantics():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [steps([action('形成记录', inputs=['触发资料'], outputs=['第一份记录']),
        action('读取记录', inputs=['名称不同也须显式绑定'], outputs=['第二份记录'])], att)]
    flow['completion']['inputs'] = ['完成依据']
    b = declared(plan, source)
    bind(b, FP + '/phases/0/steps/1/inputs/0', FP + '/phases/0/steps/0/outputs/0')
    bind(b, FP + '/completion/inputs/0', FP + '/phases/0/steps/1/outputs/0')
    before = canonical_json([plan, source, b])
    result = analyze(plan, source, b)
    assert result['schema'] == subject.SCHEMA and result['declared_inputs_available'] is True
    assert result['flows'] == [{'flow_path': FP, 'scenario_count': 1, 'peak_scenarios': 1,
        'entry_count': 1, 'action_count': 3, 'input_count': 3, 'output_count': 3}]
    assert result['causal_declaration_consistency_only'] is True
    assert all(result[key] is False for key in subject.FLAGS)
    assert result['source_plan_hash'] == digest(plan)
    assert result['input_bindings_hash'] == digest(b['input_bindings'])
    assert result['availability_hash'] == digest({k: v for k, v in result.items() if k != 'availability_hash'})
    assert canonical_json([plan, source, b]) == before


@pytest.mark.parametrize('with_entry', [False, True])
def test_future_output_cannot_supply_input_or_hide_behind_an_entry_alternative(with_entry):
    plan, source, action, att = scene()
    plan['business']['flows'][0]['phases'] = [steps([action('先审核', inputs=['尚未形成的草案'])], att)]
    b = declared(plan, source)
    sources = ([b['entry_ports'][0]['path']] if with_entry else []) + [FP + '/completion/outputs/0']
    bind(b, FP + '/phases/0/steps/0/inputs/0', *sources)
    with pytest.raises(subject.CausalAvailabilityError, match='source_not_available'):
        analyze(plan, source, b)


def test_and_join_guarantees_both_sibling_outputs_even_without_ordinary_dominators():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [parallel(action, att)]
    flow['completion']['inputs'] = ['左分支结果', '右分支结果']
    b = declared(plan, source)
    for i in range(2):
        bind(b, FP + '/completion/inputs/' + str(i), FP + '/phases/0/branches/' + str(i) + '/steps/0/outputs/0')
    assert analyze(plan, source, b)['flows'][0]['scenario_count'] == 1


@pytest.mark.parametrize('phi', [False, True])
def test_any_join_keeps_every_possible_winner_and_requires_a_complete_phi(phi):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [parallel(action, att, mode='any')]
    flow['completion']['inputs'] = ['赢家记录']
    b = declared(plan, source)
    producers = [FP + '/phases/0/branches/' + str(i) + '/steps/0/outputs/0' for i in range(2)]
    bind(b, FP + '/completion/inputs/0', *(producers if phi else producers[:1]))
    if phi:
        assert analyze(plan, source, b)['flows'][0]['scenario_count'] == 2
    else:
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, b)


def test_all_join_does_not_make_sibling_data_available_inside_an_unfinished_branch():
    plan, source, action, att = scene()
    phase = parallel(action, att)
    phase['branches'][0]['steps'][0]['inputs'] = ['兄弟分支尚未完成的结果']
    plan['business']['flows'][0]['phases'] = [phase]
    b = declared(plan, source)
    bind(b, FP + '/phases/0/branches/0/steps/0/inputs/0', FP + '/phases/0/branches/1/steps/0/outputs/0')
    with pytest.raises(subject.CausalAvailabilityError, match='source_not_available'):
        analyze(plan, source, b)


@pytest.mark.parametrize('empty_branch', [False, True])
def test_decision_join_requires_sources_on_every_branch_including_empty_routes(empty_branch):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    phase = decision(action, att)
    if empty_branch:
        phase['branches'][1]['steps'] = []
    flow['phases'] = [phase]
    flow['completion']['inputs'] = ['分支结果']
    b = declared(plan, source)
    producers = [FP + '/phases/0/branches/' + str(i) + '/steps/0/outputs/0'
        for i in range(1 if empty_branch else 2)]
    bind(b, FP + '/completion/inputs/0', *producers)
    if empty_branch:
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, b)
        bind(b, FP + '/completion/inputs/0', *producers, '/entry_ports/0')
    assert analyze(plan, source, b)['flows'][0]['scenario_count'] == 2


@pytest.mark.parametrize('complete_final_phi', [False, True])
def test_and_join_preserves_history_and_checks_branch_phi_against_all_reaching_histories(complete_final_phi):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action, att), parallel(action, att, branch_inputs=['当前分支资料'])]
    flow['completion']['inputs'] = ['此前条件分支资料', '左支完成结果', '右支完成结果']
    b = declared(plan, source)
    earlier = [FP + '/phases/0/branches/' + str(i) + '/steps/0/outputs/0' for i in range(2)]
    for i in range(2):
        bind(b, FP + '/phases/1/branches/' + str(i) + '/steps/0/inputs/0', *earlier)
        bind(b, FP + '/completion/inputs/' + str(i + 1), FP + '/phases/1/branches/' + str(i) + '/steps/0/outputs/0')
    bind(b, FP + '/completion/inputs/0', *(earlier if complete_final_phi else earlier[:1]))
    if complete_final_phi:
        assert analyze(plan, source, b)['flows'][0]['scenario_count'] == 2
    else:
        # Globally unioning distinct incoming histories would wrongly pass.
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, b)


@pytest.mark.parametrize('future_check', [False, True])
def test_repeat_runs_a_real_first_iteration_before_check_without_implicit_loop_carry(future_check):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [{'kind': 'repeat', 'attribution': copy.deepcopy(att),
        'steps': [action('重新核对', inputs=['首轮资料'])], 'check': action('判断完成', inputs=['本轮核对结果']),
        'repeat_if': '仍需核对', 'finish_if': '本轮要求完成'}]
    flow['completion']['inputs'] = ['结束判断']
    b = declared(plan, source)
    bind(b, FP + '/phases/0/check/inputs/0', FP + '/phases/0/steps/0/outputs/0')
    bind(b, FP + '/completion/inputs/0', FP + '/phases/0/check/outputs/0')
    if future_check:
        bind(b, FP + '/phases/0/steps/0/inputs/0', '/entry_ports/0', FP + '/phases/0/check/outputs/0')
        with pytest.raises(subject.CausalAvailabilityError, match='source_not_available'):
            analyze(plan, source, b)
    else:
        assert analyze(plan, source, b)['flows'][0]['scenario_count'] == 1


@pytest.mark.parametrize('field,new_value', [('data_type', 'number'), ('unit', '°C')])
def test_explicit_contracts_cannot_implicitly_convert_data_types_or_units(field, new_value):
    plan, source, _, _ = scene()
    plan['business']['flows'][0]['completion']['inputs'] = ['输入资料']
    b = declared(plan, source)
    b['input_bindings'][0]['contract'][field] = new_value
    with pytest.raises(subject.CausalAvailabilityError, match='source_contract_mismatch'):
        analyze(plan, source, b)


def test_entry_or_output_from_another_flow_cannot_authorize_the_current_flow():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['completion']['inputs'] = ['本流程资料']
    plan['business']['flows'].append(copy.deepcopy(flow))
    b = declared(plan, source)
    bind(b, FP + '/completion/inputs/0', '/entry_ports/1')
    with pytest.raises(subject.CausalAvailabilityError, match='source_flow_or_position'):
        analyze(plan, source, b)


@pytest.mark.parametrize('phase_count,branch_count,maximum', [(3, 2, 4), (8, 3, 4096)])
def test_scenario_capacity_refuses_the_complete_analysis_without_truncating_histories(phase_count, branch_count, maximum):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action, att, empty=True, count=branch_count) for _ in range(phase_count)]
    b = declared(plan, source)
    before = canonical_json([plan, source, b])
    with pytest.raises(subject.CausalAvailabilityError, match='scenario_capacity'):
        analyze(plan, source, b, maximum=maximum)
    assert canonical_json([plan, source, b]) == before
    if phase_count == 3:
        result = analyze(plan, source, b, maximum=8)
        assert result['flows'][0]['scenario_count'] == result['flows'][0]['peak_scenarios'] == 8


@pytest.mark.parametrize('invalid', [True, 0, -1, 4097, 4.0])
def test_capacity_is_a_strict_bounded_integer(invalid):
    plan, source, _, _ = scene()
    b = declared(plan, source)
    with pytest.raises(ValueError):
        analyze(plan, source, b, maximum=invalid)


def test_original_duplicate_port_labels_keep_distinct_positional_contracts():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [steps([action(outputs=['同名记录', '同名记录'])], att)]
    flow['completion']['inputs'] = ['同名记录', '同名记录']
    b = declared(plan, source)
    for i in range(2):
        bind(b, FP + '/completion/inputs/' + str(i), FP + '/phases/0/steps/0/outputs/' + str(i))
    result = analyze(plan, source, b)
    assert result['flows'][0]['input_count'] == 2 and result['flows'][0]['output_count'] == 3
    walk = paths.walk_action_ports(plan, scope_binding=source['scope_binding'])
    assert len(walk['actions'][0]['output_ports']) == 2
    assert walk['actions'][0]['output_ports'][1]['path'].endswith('/outputs/1')


@pytest.mark.parametrize('fault', ['missing_input', 'duplicate_input', 'missing_output', 'conversion', 'loop_carry', 'bad_action_port'])
def test_direct_kernel_retains_complete_unique_inventory_and_rejects_unsupported_fields(fault):
    plan, source, _, _ = scene()
    plan['business']['flows'][0]['completion']['inputs'] = ['待核对资料']
    b = declared(plan, source)
    walk = paths.walk_action_ports(plan, scope_binding=source['scope_binding'])
    if fault == 'missing_input': b['input_bindings'].clear()
    elif fault == 'duplicate_input': b['input_bindings'].append(copy.deepcopy(b['input_bindings'][0]))
    elif fault == 'missing_output': b['output_contracts'].clear()
    elif fault == 'conversion': b['input_bindings'][0]['conversion'] = 'implicit'
    elif fault == 'loop_carry': b['input_bindings'][0]['loop_carried'] = True
    else: walk['actions'][0]['input_ports'][0]['path'] += '/forged'
    with pytest.raises(subject.CausalAvailabilityError):
        subject.analyze_availability(plan, action_ports=walk['actions'], entry_ports=b['entry_ports'],
            input_bindings=b['input_bindings'], output_contracts=b['output_contracts'])
