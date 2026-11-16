"""Real V190 selector/coverage/causality/compiler/source checks, synthetic data.

Declared entries are not real trigger data, and a passing plan does not prove
business meaning, source ACL, provider quality, adoption or execution rights.
"""
import copy
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_wire_v190 as subject
from generic_agents import report_business_flow_wire_v184 as plans
from generic_agents import report_business_flow_wire_v182 as base
from generic_agents import business_flow_causal_paths_v189 as causal
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external, scene, phases


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def selector(action):
    part = action['part']
    return {'flow_index': action['flow_index'], 'phase_index': action['phase_index'], 'part': part,
        'branch_index': action['control_ancestors'][-1]['branch_index'] if part == 'branch_step' else None,
        'step_index': int(action['path'].rsplit('/', 1)[1]) if part in {'step', 'repeat_step', 'branch_step'} else None}


def envelope(plan, binding):
    walked = causal.walk_action_ports(plan, scope_binding=binding)
    contract = {'data_type': 'text', 'unit': None}
    output_contracts, input_bindings = [], []
    for action in walked['actions']:
        for port in action['output_ports']:
            output_contracts.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract)})
        for port in action['input_ports']:
            input_bindings.append({'action': selector(action), 'ordinal': port['ordinal'],
                'contract': copy.deepcopy(contract), 'sources': [{'kind': 'entry', 'index': action['flow_index']}]})
    return {'schema': subject.WIRE, 'plan': copy.deepcopy(plan), 'causality': {
        'entry_ports': [{'flow_index': index, 'contract': copy.deepcopy(contract)}
            for index in range(len(plan['business']['flows']))],
        'output_contracts': output_contracts, 'input_bindings': input_bindings}}


def fixture(all_phases=False):
    plan, source, action, steps, attribution = scene(4)
    if all_phases:
        plan['business']['flows'][0]['phases'] = phases(action, steps, attribution)
    return envelope(plan, source['scope_binding']), source


def project(value, source):
    return subject.build_projection(raw(value), **source)


def reseal(value):
    value['projection_hash'] = digest({key: item for key, item in value.items() if key != 'projection_hash'})
    return value


def test_exact_new_schema_reuses_original_v184_and_exposes_binding_const_without_model_plan_hash():
    value, source = fixture(True)
    binding = source['scope_binding']
    schema = subject.output_schema(binding)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(value)) == []
    assert set(schema['properties']) == {'schema', 'plan', 'causality'}
    assert schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] == binding['name']
    assert schema['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] == digest(binding)
    assert schema == subject._schema_for_shape(binding['name'], digest(binding))
    assert set(schema['$defs']['ActionSelectorV190']['properties']) == {
        'flow_index', 'phase_index', 'part', 'branch_index', 'step_index'}
    assert len(schema['$defs']['ActionSelectorV190']['oneOf']) == 7
    assert subject.parse_plan(raw(value), scope_binding=binding) == value
    assert value['plan']['schema'] == plans.WIRE and value['plan']['binding_hash'] == digest(binding)


def test_actual_projection_keeps_raw_layers_sidecar_and_real_availability_separate():
    value, source = fixture(True)
    before = copy.deepcopy([value, source])
    actual = b'\n' + json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    result = subject.build_projection(actual, **source)
    assert result['schema'] == subject.PROJECTION and result['raw_causal_plan'] == value
    assert result['raw_causal_plan_schema'] == subject.WIRE
    assert result['raw_causal_plan_value_hash'] == digest(value)
    assert result['raw_causal_plan_bytes_sha256'] == hashlib.sha256(actual).hexdigest()
    assert result['raw_causal_plan_bytes'] == len(actual)
    assert result['raw_plan'] == value['plan'] and result['raw_plan_schema'] == plans.WIRE
    assert result['wire']['schema'] == base.WIRE
    assert result['wire'] == result['plan_projection']['wire']
    assert result['plan_projection_hash'] == result['plan_projection']['projection_hash']
    sidecar = result['compiled_sidecar']
    assert sidecar['schema'] == causal.BINDINGS and sidecar['source_plan_hash'] == digest(value['plan'])
    assert sidecar['entry_ports'][0]['path'] == '/entry_ports/0'
    assert result['compiled_sidecar_hash'] == digest(sidecar)
    assert result['causal_analysis']['availability']['declared_inputs_available'] is True
    assert result['causal_analysis_hash'] == result['causal_analysis']['analysis_hash']
    assert result['causal_declaration_consistency_only'] is True
    assert all(result[key] is False for key in subject._FLAGS)
    assert subject.check_projection(result, actual, **source) == result
    manual = subject.new_manual(result)
    assert manual == {'schema': base.MANUAL, 'result': result['wire'], 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}
    assert subject.validate_value(manual, **source) == manual
    with pytest.raises(ValueError): subject.confirm_value(manual)
    assert [value, source] == before


@pytest.mark.parametrize('fault', ['bool_flow', 'float_ordinal', 'negative', 'unknown_flow',
    'unknown_phase', 'unknown_step', 'unknown_branch', 'wrong_part', 'nonnull_completion', 'extra_selector'])
def test_typed_logical_selectors_resolve_only_unique_original_actions_without_repair(fault):
    value, source = fixture(True)
    output = value['causality']['output_contracts'][0]
    action = output['action']
    if fault == 'bool_flow': action['flow_index'] = True
    elif fault == 'float_ordinal': output['ordinal'] = 0.0
    elif fault == 'negative': action['step_index'] = -1
    elif fault == 'unknown_flow': action['flow_index'] = 99
    elif fault == 'unknown_phase': action['phase_index'] = 99
    elif fault == 'unknown_step': action['step_index'] = 99
    elif fault == 'unknown_branch':
        action.update(part='branch_step', phase_index=1, branch_index=0, step_index=0)
    elif fault == 'wrong_part': action['part'] = 'repeat_step'
    elif fault == 'nonnull_completion': action.update(part='completion', step_index=None)
    else: action['path'] = '/business/flows/0/phases/0/steps/0'
    with pytest.raises(ValueError): subject.parse_plan(raw(value), scope_binding=source['scope_binding'])


def test_duplicate_labels_and_duplicate_flow_names_still_require_every_original_ordinal():
    plan, source, _, _, _ = scene(4)
    action = plan['business']['flows'][0]['phases'][0]['steps'][0]
    action['inputs'] = ['相同输入', '相同输入']
    action['outputs'] = ['相同输出', '相同输出']
    plan['business']['flows'].append(copy.deepcopy(plan['business']['flows'][0]))
    value = envelope(plan, source['scope_binding'])
    result = project(value, source)
    paths = result['causal_analysis']['paths']
    first = paths['actions'][0]
    assert len(first['input_ports']) == len(first['output_ports']) == 2
    assert result['wire']['business']['flows'][0]['nodes'][0]['inputs'] == ['相同输入']
    assert len(paths['actions']) == 4 and paths['actions'][2]['flow_index'] == 1
    assert result['wire']['business']['flows'][0]['name'] != result['wire']['business']['flows'][1]['name']
    missing = copy.deepcopy(value)
    missing['causality']['input_bindings'].pop(1)
    with pytest.raises(ValueError): subject.parse_plan(raw(missing), scope_binding=source['scope_binding'])


@pytest.mark.parametrize('fault', ['missing_output', 'duplicate_output', 'missing_input', 'duplicate_input',
    'output_ordinal', 'input_ordinal', 'entry_flow', 'entry_source', 'unknown_source_kind',
    'contract_extra', 'unit_bool', 'loop_carried'])
def test_full_input_output_coverage_and_closed_source_contracts_are_mandatory(fault):
    value, source = fixture()
    declared = value['causality']
    if fault == 'missing_output': declared['output_contracts'].pop()
    elif fault == 'duplicate_output': declared['output_contracts'].append(copy.deepcopy(declared['output_contracts'][0]))
    elif fault == 'missing_input': declared['input_bindings'].pop()
    elif fault == 'duplicate_input': declared['input_bindings'].append(copy.deepcopy(declared['input_bindings'][0]))
    elif fault == 'output_ordinal': declared['output_contracts'][0]['ordinal'] = 99
    elif fault == 'input_ordinal': declared['input_bindings'][0]['ordinal'] = 99
    elif fault == 'entry_flow': declared['entry_ports'][0]['flow_index'] = 99
    elif fault == 'entry_source': declared['input_bindings'][0]['sources'][0]['index'] = 99
    elif fault == 'unknown_source_kind': declared['input_bindings'][0]['sources'][0]['kind'] = 'trigger'
    elif fault == 'contract_extra': declared['output_contracts'][0]['contract']['permission'] = True
    elif fault == 'unit_bool': declared['entry_ports'][0]['contract']['unit'] = True
    else: declared['input_bindings'][0]['loop_carried'] = True
    with pytest.raises(ValueError): subject.parse_plan(raw(value), scope_binding=source['scope_binding'])


@pytest.mark.parametrize('fault', ['future', 'self', 'entry_plus_future', 'unit', 'cross_flow'])
def test_valid_positions_do_not_make_unavailable_or_mismatched_data_available(fault):
    value, source = fixture()
    declared = value['causality']
    first = declared['input_bindings'][0]
    if fault in {'future', 'entry_plus_future'}:
        future = copy.deepcopy(declared['output_contracts'][-1])
        source_port = {'kind': 'output', 'action': future['action'], 'ordinal': future['ordinal']}
        first['sources'] = [source_port] if fault == 'future' else [{'kind': 'entry', 'index': 0}, source_port]
    elif fault == 'self':
        output = declared['output_contracts'][0]
        first['sources'] = [{'kind': 'output', 'action': copy.deepcopy(output['action']), 'ordinal': output['ordinal']}]
    elif fault == 'unit': first['contract']['unit'] = 'm'
    else:
        value['plan']['business']['flows'].append(copy.deepcopy(value['plan']['business']['flows'][0]))
        value = envelope(value['plan'], source['scope_binding'])
        foreign = next(item for item in value['causality']['output_contracts'] if item['action']['flow_index'] == 1)
        value['causality']['input_bindings'][0]['sources'] = [{'kind': 'output',
            'action': copy.deepcopy(foreign['action']), 'ordinal': foreign['ordinal']}]
    # Positions and coverage are structurally correct; actual V189 must reject.
    assert subject.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    assert plans.build_projection(plans._bytes(value['plan']), **source)['wire']['schema'] == base.WIRE
    with pytest.raises(ValueError): project(value, source)


@pytest.mark.parametrize('kind', ['decision', 'parallel_all', 'parallel_any', 'repeat'])
def test_control_flow_availability_is_checked_on_real_ast_not_action_order_or_labels(kind):
    plan, source, action, _, attribution = scene(4)
    if kind == 'decision':
        phase = {'kind': 'decision', 'attribution': copy.deepcopy(attribution), 'decision': action('检查条件'),
            'branches': [{'condition': '条件满足', 'attribution': copy.deepcopy(attribution), 'steps': [action('支路动作')]},
                {'condition': '条件不满足', 'attribution': copy.deepcopy(attribution), 'steps': []}]}
    elif kind.startswith('parallel'):
        phase = {'kind': 'parallel', 'attribution': copy.deepcopy(attribution), 'dispatch': action('启动并行'),
            'branches': [{'steps': [action('并行甲')]}, {'steps': [action('并行乙')]}],
            'wait_mode': 'all' if kind == 'parallel_all' else 'any', 'join_attribution': copy.deepcopy(attribution)}
    else:
        phase = {'kind': 'repeat', 'attribution': copy.deepcopy(attribution), 'steps': [action('首轮动作')],
            'check': action('循环检查'), 'repeat_if': '仍须继续', 'finish_if': '已满足要求'}
    plan['business']['flows'][0]['phases'] = [phase]
    value = envelope(plan, source['scope_binding'])
    declared = value['causality']
    if kind == 'repeat':
        target = next(item for item in declared['input_bindings'] if item['action']['part'] == 'repeat_step')
        origin = next(item for item in declared['output_contracts'] if item['action']['part'] == 'check')
    else:
        target = next(item for item in declared['input_bindings'] if item['action']['part'] == 'completion')
        origin = next(item for item in declared['output_contracts'] if item['action']['part'] == 'branch_step')
    target['sources'] = [{'kind': 'output', 'action': copy.deepcopy(origin['action']), 'ordinal': origin['ordinal']}]
    if kind == 'parallel_all':
        assert project(value, source)['causal_analysis']['availability']['declared_inputs_available'] is True
    else:
        with pytest.raises(ValueError): project(value, source)


def test_zero_phase_completion_is_legal_and_scenario_explosion_rejects_whole_candidate():
    plan, source, action, _, attribution = scene(4)
    plan['business']['flows'][0]['phases'] = []
    assert len(project(envelope(plan, source['scope_binding']), source)['wire']['business']['flows'][0]['nodes']) == 1
    decision = {'kind': 'decision', 'attribution': copy.deepcopy(attribution), 'decision': action('检查条件'),
        'branches': [{'condition': '条件分支' + str(i), 'attribution': copy.deepcopy(attribution), 'steps': []} for i in range(3)]}
    plan['business']['flows'][0]['phases'] = [copy.deepcopy(decision) for _ in range(8)]
    value = envelope(plan, source['scope_binding'])
    assert subject.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    with pytest.raises(ValueError, match='scenario_capacity'): project(value, source)


@pytest.mark.parametrize('fault', ['unknown_ref', 'changed_claim', 'binding', 'truncated', 'duplicate_key', 'old_wire'])
def test_existing_source_graph_and_exact_raw_gates_remain_mandatory(fault):
    value, source = fixture()
    if fault == 'unknown_ref': value['plan']['business']['flows'][0]['phase_order']['citations'] = ['w:p999999/s1']
    elif fault == 'changed_claim': source['input_claims'][0]['statement'] += '改变独立原声明'
    elif fault == 'binding': value['plan']['binding_hash'] = digest('other')
    elif fault == 'old_wire': value['schema'] = plans.WIRE
    payload = raw(value)
    if fault == 'truncated': payload = payload[:-1]
    elif fault == 'duplicate_key': payload = b'{"schema":"wrong",' + payload[1:]
    with pytest.raises(ValueError): subject.build_projection(payload, **source)


@pytest.mark.parametrize('fault', ['raw_causal', 'sidecar', 'analysis', 'compiled', 'authority', 'extra'])
def test_cold_projection_rebuild_rejects_resealed_tampering_and_capacity_never_truncates(monkeypatch, fault):
    value, source = fixture()
    projected = project(value, source)
    if fault == 'raw_causal': projected['raw_causal_plan']['causality']['entry_ports'][0]['contract']['data_type'] = 'changed'
    elif fault == 'sidecar': projected['compiled_sidecar']['entry_ports'][0]['path'] = '/entries/0'
    elif fault == 'analysis': projected['causal_analysis']['entry_facts_verified'] = True
    elif fault == 'compiled': projected['wire']['business']['flows'][0]['nodes'][0]['role'] = '被改写岗位'
    elif fault == 'authority': projected['can_execute'] = True
    else: projected['grant'] = True
    reseal(projected)
    with pytest.raises(ValueError): subject.check_projection(projected, raw(value), **source)
    valid = project(value, source)
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', len(base._bytes(valid)) - 1)
    with pytest.raises(subject.BusinessFlowCausalCapacityError): project(value, source)
