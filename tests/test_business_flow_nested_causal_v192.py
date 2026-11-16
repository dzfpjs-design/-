"""Real recursive V192 parsing/walking and synthetic declared causality.

No saved report, provider, actual entry fact, permission or runtime is proved.
Root executes these tests. All source actions retain the public V184 contract;
only the independently versioned V192 phase tree is used here.
"""
import copy

import pytest

from generic_agents import business_flow_nested_causal_v192 as subject
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external, scene as original_scene


FP = '/business/flows/0'
CONTRACT = {'data_type': 'record', 'unit': None}


def scene():
    plan, source, old_action, _, attribution = original_scene(4)
    plan['schema'] = plans.PLAN

    def action(name='核对资料', *, inputs=None, outputs=None):
        return old_action(name, inputs=[] if inputs is None else inputs,
            outputs=['记录结果'] if outputs is None else outputs)

    flow = plan['business']['flows'][0]
    flow['phases'] = []
    flow['completion'] = action('保存最终记录', outputs=['完成记录'])
    return plan, source, action, attribution


def steps(actions, att):
    return {'kind': 'steps', 'attribution': copy.deepcopy(att), 'steps': actions}


def decision(action, att, branches):
    return {'kind': 'decision', 'attribution': copy.deepcopy(att),
        'decision': action('判断真实业务条件'), 'branches': [
            {'condition': '声明条件' + str(i), 'attribution': copy.deepcopy(att), 'phases': phases}
            for i, phases in enumerate(branches)]}


def parallel(action, att, branches, mode='all'):
    return {'kind': 'parallel', 'attribution': copy.deepcopy(att),
        'dispatch': action('安排独立并行工作'), 'branches': [{'phases': phases} for phases in branches],
        'wait_mode': mode, 'join_attribution': copy.deepcopy(att)}


def binary(action, att, prefix='条件资料'):
    return decision(action, att, [[steps([action(prefix + str(i))], att)] for i in range(2)])


def declared(plan, source):
    walked = plans.walk_action_ports(plan, scope_binding=source['scope_binding'])
    entries = [{'path': '/entry_ports/' + str(i), 'flow_path': '/business/flows/' + str(i),
        'contract': copy.deepcopy(CONTRACT)} for i in range(len(plan['business']['flows']))]
    return {'schema': subject.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(source['scope_binding']), 'entry_ports': entries,
        'output_contracts': [{'path': path, 'contract': copy.deepcopy(CONTRACT)}
            for path in walked['output_paths']],
        'input_bindings': [{'input_path': port['path'], 'contract': copy.deepcopy(CONTRACT),
            'sources': ['/entry_ports/' + str(row['flow_index'])]}
            for row in walked['actions'] for port in row['input_ports']]}


def bind(value, input_path, *sources):
    next(item for item in value['input_bindings'] if item['input_path'] == input_path)['sources'] = list(sources)


def analyze(plan, source, value, maximum=4096):
    return subject.analyze_availability(plan, value, scope_binding=source['scope_binding'],
        max_scenarios=maximum)


def two_nested_branches(action, att, mode='all'):
    return parallel(action, att, [[binary(action, att, '左支选择')], [binary(action, att, '右支选择')]], mode)


def nested_outputs(phase_path, branch):
    return [phase_path + '/branches/' + str(branch) + '/phases/0/branches/' + str(i)
        + '/phases/0/steps/0/outputs/0' for i in range(2)]


def test_real_nested_decision_and_parallel_keep_paths_and_full_alternatives_without_authority():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [steps([action('取得触发资料', inputs=['入口资料'])], att),
        decision(action, att, [
            [steps([action('形成分支原记录', inputs=['先前资料'])], att),
                parallel(action, att, [[steps([action('左支核验', inputs=['本支记录'])], att)],
                    [steps([action('右支核验', inputs=['本支记录'])], att)]])],
            [steps([action('形成另一类记录', inputs=['先前资料'])], att)]])]
    flow['completion']['inputs'] = ['核验结果一', '核验结果二']
    value = declared(plan, source)
    root_output = FP + '/phases/0/steps/0/outputs/0'
    dp = FP + '/phases/1'
    for i in range(2):
        bind(value, dp + '/branches/' + str(i) + '/phases/0/steps/0/inputs/0', root_output)
    branch_output = dp + '/branches/0/phases/0/steps/0/outputs/0'
    pp = dp + '/branches/0/phases/1'
    for i in range(2):
        bind(value, pp + '/branches/' + str(i) + '/phases/0/steps/0/inputs/0', branch_output)
        bind(value, FP + '/completion/inputs/' + str(i),
            pp + '/branches/' + str(i) + '/phases/0/steps/0/outputs/0',
            dp + '/branches/1/phases/0/steps/0/outputs/0')
    before = canonical_json([plan, source, value])
    result = analyze(plan, source, value)
    assert result['schema'] == subject.SCHEMA and result['consistent'] is True
    assert result['flows'][0]['scenario_count'] == result['total_scenarios'] == 2
    assert result['causal_declaration_consistency_only'] is True
    assert result['entry_contracts_are_proposals'] is True
    assert all(result[key] is False for key in subject.FLAGS)
    assert result['paths'] == plans.walk_action_ports(plan, scope_binding=source['scope_binding'])
    nested = next(row for row in result['paths']['actions'] if row['name'] == '左支核验')
    assert nested['path'] == pp + '/branches/0/phases/0/steps/0'
    assert len(nested['control_ancestors']) == 5
    assert result['availability_hash'] == digest({k: v for k, v in result.items() if k != 'availability_hash'})
    assert canonical_json([plan, source, value]) == before
    result['bindings']['entry_ports'][0]['contract']['data_type'] = '改动返回值'
    assert value['entry_ports'][0]['contract'] == CONTRACT


def test_all_join_computes_nested_cartesian_outcomes_instead_of_zipping_branch_lists():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [two_nested_branches(action, att)]
    flow['completion']['inputs'] = ['任意左支结果', '任意右支结果']
    value = declared(plan, source)
    for i in range(2):
        bind(value, FP + '/completion/inputs/' + str(i), *nested_outputs(FP + '/phases/0', i))
    result = analyze(plan, source, value, maximum=4)
    # Two independent binary choices give FOUR outcomes, not zip's two.
    assert result['total_scenarios'] == result['flows'][0]['scenario_count'] == 4
    assert result['peak_scenarios'] == 4
    with pytest.raises(subject.CausalAvailabilityError, match='scenario_capacity'):
        analyze(plan, source, value, maximum=3)


@pytest.mark.parametrize('complete_upstream_alternatives', [True, False])
def test_parallel_product_never_combines_distinct_parent_histories(complete_upstream_alternatives):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [binary(action, att, '上游原始分支'), two_nested_branches(action, att)]
    flow['completion']['inputs'] = ['本历史的上游结果', '左支资料', '右支资料']
    # Every nested action with an input receives the complete set of reaching
    # upstream histories, not one parent at a time (which would reject a phi).
    for branch in flow['phases'][1]['branches']:
        branch['phases'][0]['decision']['inputs'] = ['上游分支资料']
    value = declared(plan, source)
    upstream = [FP + '/phases/0/branches/' + str(i) + '/phases/0/steps/0/outputs/0' for i in range(2)]
    for i in range(2):
        bind(value, FP + '/phases/1/branches/' + str(i) + '/phases/0/decision/inputs/0', *upstream)
        bind(value, FP + '/completion/inputs/' + str(i + 1), *nested_outputs(FP + '/phases/1', i))
    bind(value, FP + '/completion/inputs/0', *(upstream if complete_upstream_alternatives else upstream[:1]))
    if complete_upstream_alternatives:
        result = analyze(plan, source, value, maximum=8)
        # Two parents, each with 2x2 descendants. A global product would give
        # sixteen and may make both mutually exclusive parent values visible.
        assert result['total_scenarios'] == 8
    else:
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, value)


@pytest.mark.parametrize('mode', ['all', 'any'])
@pytest.mark.parametrize('with_entry', [False, True])
def test_nested_sibling_output_is_not_available_inside_other_parallel_branch(mode, with_entry):
    plan, source, action, att = scene()
    phase = two_nested_branches(action, att, mode)
    reader = phase['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    reader['inputs'] = ['尚未完成的兄弟工作']
    plan['business']['flows'][0]['phases'] = [phase]
    value = declared(plan, source)
    ip = FP + '/phases/0/branches/0/phases/0/branches/0/phases/0/steps/0/inputs/0'
    sources = (['/entry_ports/0'] if with_entry else []) + nested_outputs(FP + '/phases/0', 1)[:1]
    bind(value, ip, *sources)
    with pytest.raises(subject.CausalAvailabilityError, match='source_not_available'):
        analyze(plan, source, value)


@pytest.mark.parametrize('complete_winners', [True, False])
def test_any_retains_all_nested_winners_and_never_inherits_losing_branch_outputs(complete_winners):
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [two_nested_branches(action, att, mode='any')]
    flow['completion']['inputs'] = ['实际获胜工作结果']
    value = declared(plan, source)
    sources = nested_outputs(FP + '/phases/0', 0)
    if complete_winners:
        sources += nested_outputs(FP + '/phases/0', 1)
    bind(value, FP + '/completion/inputs/0', *sources)
    if complete_winners:
        assert analyze(plan, source, value)['total_scenarios'] == 4
    else:
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, value)


@pytest.mark.parametrize('entry_fallback', [False, True])
def test_empty_nested_decision_route_keeps_missing_value_visible(entry_fallback):
    plan, source, action, att = scene()
    inner = decision(action, att, [[], [steps([action('仅在非空分支形成的记录')], att)]])
    outer = decision(action, att, [[inner], [steps([action('外层另一分支记录')], att)]])
    flow = plan['business']['flows'][0]
    flow['phases'] = [outer]
    flow['completion']['inputs'] = ['最终所需记录']
    value = declared(plan, source)
    sources = [FP + '/phases/0/branches/0/phases/0/branches/1/phases/0/steps/0/outputs/0',
        FP + '/phases/0/branches/1/phases/0/steps/0/outputs/0']
    if entry_fallback:
        sources.append('/entry_ports/0')
    bind(value, FP + '/completion/inputs/0', *sources)
    if entry_fallback:
        assert analyze(plan, source, value)['total_scenarios'] == 3
    else:
        with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
            analyze(plan, source, value)


@pytest.mark.parametrize('future_check', [False, True])
def test_repeat_inside_decision_requires_real_first_iteration_without_implicit_loop_carry(future_check):
    plan, source, action, att = scene()
    loop = {'kind': 'repeat', 'attribution': copy.deepcopy(att),
        'steps': [action('本轮复核', inputs=['首轮原资料'])],
        'check': action('本轮结束判断', inputs=['本轮复核结果']),
        'repeat_if': '还有需再次复核的事项', 'finish_if': '本轮复核事项已完成'}
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action, att, [[loop], []])]
    value = declared(plan, source)
    lp = FP + '/phases/0/branches/0/phases/0'
    bind(value, lp + '/check/inputs/0', lp + '/steps/0/outputs/0')
    if future_check:
        bind(value, lp + '/steps/0/inputs/0', '/entry_ports/0', lp + '/check/outputs/0')
        with pytest.raises(subject.CausalAvailabilityError, match='source_not_available'):
            analyze(plan, source, value)
    else:
        assert analyze(plan, source, value)['total_scenarios'] == 2


def test_capacity_is_global_across_flows_without_counting_each_sequential_action_as_a_new_history():
    plan, source, action, att = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [steps([action('顺序工作' + str(i)) for i in range(10)], att)]
    value = declared(plan, source)
    assert analyze(plan, source, value, maximum=1)['total_scenarios'] == 1
    flow['phases'] = [binary(action, att)]
    plan['business']['flows'].append(copy.deepcopy(flow))
    value = declared(plan, source)
    assert analyze(plan, source, value, maximum=4)['total_scenarios'] == 4
    # Each flow individually fits two, but the whole declaration does not fit
    # a budget of three. No per-flow reset is allowed to evade the limit.
    before = canonical_json([plan, value])
    with pytest.raises(subject.CausalAvailabilityError, match='scenario_capacity'):
        analyze(plan, source, value, maximum=3)
    assert canonical_json([plan, value]) == before


def test_nested_capacity_rejects_full_product_even_when_each_local_branch_fits():
    plan, source, action, att = scene()
    # Each child decision has four outcomes. Their all-join has sixteen;
    # only two control levels are needed, independent of the depth ceiling.
    children = [[decision(action, att, [[steps([action('分支资料' + str(i))], att)]
        for i in range(4)])] for _ in range(2)]
    phase = parallel(action, att, children)
    plan['business']['flows'][0]['phases'] = [phase]
    value = declared(plan, source)
    assert analyze(plan, source, value, maximum=16)['total_scenarios'] == 16
    with pytest.raises(subject.CausalAvailabilityError, match='scenario_capacity'):
        analyze(plan, source, value, maximum=15)


def test_confirmed_event_can_notify_in_parallel_with_conditionally_available_observation():
    plan, source, action, att = scene()
    flight = decision(action, att, [[steps([action('条件允许才补充观测')], att)], []])
    work = parallel(action, att, [[steps([action('立即发送待核通知')], att)], [flight]])
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action, att, [[work], []])]
    value = declared(plan, source)
    result = analyze(plan, source, value)
    # Confirmed: observe or skip; unconfirmed: empty route. Neither condition
    # is evaluated here, and the skipped observation never supplies a value.
    assert result['total_scenarios'] == 3
    assert result['entry_facts_verified'] is False and result['can_execute'] is False
    flow['completion']['inputs'] = ['不能无条件声称已取得补采结果']
    value = declared(plan, source)
    observation = FP + '/phases/0/branches/0/phases/0/branches/1/phases/0/branches/0/phases/0/steps/0/outputs/0'
    bind(value, FP + '/completion/inputs/0', observation)
    with pytest.raises(subject.CausalAvailabilityError, match='input_not_guaranteed'):
        analyze(plan, source, value)


def test_duplicate_labels_keep_original_ordinals_and_zero_phase_zero_input_is_valid():
    plan, source, _, _ = scene()
    completion = plan['business']['flows'][0]['completion']
    completion['inputs'] = ['同名输入', '同名输入']
    completion['outputs'] = ['同名输出', '同名输出']
    value = declared(plan, source)
    result = analyze(plan, source, value, maximum=1)
    assert result['flows'][0]['input_count'] == result['flows'][0]['output_count'] == 2
    assert result['paths']['input_paths'] == [FP + '/completion/inputs/0', FP + '/completion/inputs/1']
    assert result['paths']['output_paths'] == [FP + '/completion/outputs/0', FP + '/completion/outputs/1']
    completion['inputs'] = []
    completion['outputs'] = ['仍须保留原合同要求的产出']
    value = declared(plan, source)
    value['entry_ports'] = []
    assert value['input_bindings'] == [] and len(value['output_contracts']) == 1
    assert analyze(plan, source, value)['flows'][0]['scenario_count'] == 1


@pytest.mark.parametrize('fault', ['plan_hash', 'binding_hash', 'schema', 'missing_input',
    'duplicate_input', 'missing_output', 'duplicate_output', 'unknown_source', 'input_as_source',
    'empty_sources', 'duplicate_sources', 'entry_path', 'entry_flow', 'extra', 'contract_extra',
    'unit_number', 'datatype_bool', 'loop_carried'])
def test_closed_sidecar_and_original_identity_reject_before_history_analysis(fault):
    plan, source, action, att = scene()
    plan['business']['flows'][0]['phases'] = [steps([action('读取入口资料', inputs=['入口资料'])], att)]
    value = declared(plan, source)
    if fault == 'plan_hash': value['source_plan_hash'] = digest('别的原计划')
    elif fault == 'binding_hash': value['scope_binding_hash'] = digest('别的绑定')
    elif fault == 'schema': value['schema'] = 'business-flow-causal-bindings-v189'
    elif fault == 'missing_input': value['input_bindings'].pop()
    elif fault == 'duplicate_input': value['input_bindings'].append(copy.deepcopy(value['input_bindings'][0]))
    elif fault == 'missing_output': value['output_contracts'].pop()
    elif fault == 'duplicate_output': value['output_contracts'].append(copy.deepcopy(value['output_contracts'][0]))
    elif fault == 'unknown_source': value['input_bindings'][0]['sources'] = ['external:天气']
    elif fault == 'input_as_source': value['input_bindings'][0]['sources'] = [value['input_bindings'][0]['input_path']]
    elif fault == 'empty_sources': value['input_bindings'][0]['sources'] = []
    elif fault == 'duplicate_sources': value['input_bindings'][0]['sources'] *= 2
    elif fault == 'entry_path': value['entry_ports'][0]['path'] = '/entries/0'
    elif fault == 'entry_flow': value['entry_ports'][0]['flow_path'] = '/business/flows/99'
    elif fault == 'contract_extra': value['input_bindings'][0]['contract']['authorized'] = True
    elif fault == 'unit_number': value['output_contracts'][0]['contract']['unit'] = 1
    elif fault == 'datatype_bool': value['input_bindings'][0]['contract']['data_type'] = True
    elif fault == 'loop_carried': value['input_bindings'][0]['loop_carried'] = True
    else: value['authorized'] = True
    with pytest.raises(ValueError):
        subject.check_bindings(plan, value, scope_binding=source['scope_binding'])
    with pytest.raises(ValueError):
        analyze(plan, source, value)


@pytest.mark.parametrize('field,new_value', [('data_type', 'number'), ('unit', '℃'), ('unit', '')])
def test_explicit_contracts_do_not_convert_types_or_units(field, new_value):
    plan, source, _, _ = scene()
    plan['business']['flows'][0]['completion']['inputs'] = ['真实单位的资料']
    value = declared(plan, source)
    value['input_bindings'][0]['contract'][field] = new_value
    with pytest.raises(subject.CausalAvailabilityError, match='source_contract_mismatch|contract_type'):
        analyze(plan, source, value)


@pytest.mark.parametrize('source_kind', ['entry', 'output'])
def test_cross_flow_ports_cannot_supply_another_trigger(source_kind):
    plan, source, _, _ = scene()
    plan['business']['flows'].append(copy.deepcopy(plan['business']['flows'][0]))
    plan['business']['flows'][0]['completion']['inputs'] = ['属于本流程的资料']
    value = declared(plan, source)
    source_path = '/entry_ports/1' if source_kind == 'entry' else '/business/flows/1/completion/outputs/0'
    bind(value, FP + '/completion/inputs/0', source_path)
    with pytest.raises(subject.CausalAvailabilityError, match='source_flow_or_position'):
        analyze(plan, source, value)


@pytest.mark.parametrize('maximum', [True, 0, -1, 1.0, 4097, '4096'])
def test_capacity_limit_is_a_strict_finite_integer(maximum):
    plan, source, _, _ = scene()
    with pytest.raises(subject.CausalAvailabilityError, match='scenario_limit'):
        analyze(plan, source, declared(plan, source), maximum=maximum)


@pytest.mark.parametrize('fault', ['old_plan_schema', 'wrong_scope', 'changed_plan_after_sidecar', 'extra_plan_key'])
def test_real_nested_plan_binding_is_still_required_even_with_resealed_sidecar(fault):
    plan, source, _, _ = scene()
    value = declared(plan, source)
    if fault == 'old_plan_schema': plan['schema'] = 'report-business-flow-plan-v184'
    elif fault == 'wrong_scope': source['scope_binding']['scope_id'] = '另一范围'
    elif fault == 'changed_plan_after_sidecar': plan['business']['flows'][0]['completion']['name'] = '已改计划动作'
    else: plan['source_authority_verified'] = True
    if fault != 'changed_plan_after_sidecar':
        value['source_plan_hash'] = digest(plan)
        value['scope_binding_hash'] = digest(source['scope_binding'])
    with pytest.raises(ValueError):
        analyze(plan, source, value)
