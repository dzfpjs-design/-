"""Actual V193 addresses, recursive source checks and V192 causal analysis.

Public synthetic material is used. These tests establish no provider quality,
actual source authority, entry facts, durable human work or execution rights.
"""
import copy
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_wire_v193 as subject
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import business_flow_nested_causal_v192 as causal
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import resolve_evidence
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external, scene as original_scene


FP = '/business/flows/0'


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def at(value, pointer):
    for token in pointer.split('/')[1:]:
        value = value[int(token)] if type(value) is list else value[token]
    return value


def selector(action):
    address, ancestors = [], action['control_ancestors']
    for i, ancestor in enumerate(ancestors):
        if ancestor['kind'] in {'steps', 'decision', 'parallel', 'repeat'}:
            child = ancestors[i + 1] if i + 1 < len(ancestors) else None
            branch = child['branch_index'] if child and child['kind'].endswith('_branch') else None
            address.append({'phase_index': int(ancestor['path'].rsplit('/', 1)[1]), 'branch_index': branch})
    return {'flow_index': action['flow_index'], 'address': address, 'part': action['part'],
        'step_index': int(action['path'].rsplit('/', 1)[1])
            if action['part'] in {'step', 'repeat_step'} else None}


def envelope(plan, binding):
    """Public independent fixture helper for Root's normal source/API tests."""
    walked = plans.walk_action_ports(plan, scope_binding=binding)
    contract = {'data_type': 'record', 'unit': None}
    outputs, inputs = [], []
    for action in walked['actions']:
        for port in action['output_ports']:
            outputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract)})
        for port in action['input_ports']:
            inputs.append({'action': selector(action), 'ordinal': port['ordinal'], 'contract': copy.deepcopy(contract),
                'sources': [{'kind': 'entry', 'index': action['flow_index']}]})
    return {'schema': subject.WIRE, 'plan': copy.deepcopy(plan), 'causality': {
        'entry_ports': [{'flow_index': i, 'contract': copy.deepcopy(contract)}
            for i in range(len(plan['business']['flows']))],
        'output_contracts': outputs, 'input_bindings': inputs}}


def fixture(nested=True):
    plan, source, old_action, _, att = original_scene(4)
    plan['schema'] = plans.PLAN
    a = lambda: copy.deepcopy(att)
    action = lambda name, **kwargs: old_action(name, **kwargs)
    steps = lambda name: {'kind': 'steps', 'attribution': a(), 'steps': [action(name)]}
    flow = plan['business']['flows'][0]
    if nested:
        flight = {'kind': 'decision', 'attribution': a(), 'decision': action('核对补采条件'),
            'branches': [{'condition': '当前条件允许', 'attribution': a(), 'phases': [steps('执行补充观测')]},
                {'condition': '当前条件不允许', 'attribution': a(), 'phases': []}]}
        parallel = {'kind': 'parallel', 'attribution': a(), 'dispatch': action('安排并行工作'),
            'branches': [{'phases': [steps('立即通知核验岗位')]}, {'phases': [flight]}],
            'wait_mode': 'all', 'join_attribution': a()}
        outer = {'kind': 'decision', 'attribution': a(), 'decision': action('核对异常是否成立'),
            'branches': [{'condition': '需要继续核验', 'attribution': a(), 'phases': [parallel]},
                {'condition': '无需继续核验', 'attribution': a(), 'phases': []}]}
        repeat = {'kind': 'repeat', 'attribution': a(), 'steps': [action('首轮复核原资料')],
            'check': action('判断本轮是否完成'), 'repeat_if': '仍有需要核对的事项', 'finish_if': '本轮核对完成'}
        flow['phases'] = [steps('接收触发资料'), outer, repeat]
    else:
        flow['phases'] = [steps('形成前一步记录')]
    return envelope(plan, source['scope_binding']), source


def project(value, source):
    return subject.build_projection(raw(value), **source)


def bind(value, target_path, *source_paths, scope_binding):
    walked = plans.walk_action_ports(value['plan'], scope_binding=scope_binding)
    target = next(row for row in walked['actions'] if target_path.startswith(row['path'] + '/inputs/'))
    ordinal = int(target_path.rsplit('/', 1)[1])
    item = next(item for item in value['causality']['input_bindings']
        if item['action'] == selector(target) and item['ordinal'] == ordinal)
    converted = []
    for path in source_paths:
        if path.startswith('/entry_ports/'):
            converted.append({'kind': 'entry', 'index': int(path.rsplit('/', 1)[1])})
        else:
            producer = next(row for row in walked['actions'] if path.startswith(row['path'] + '/outputs/'))
            converted.append({'kind': 'output', 'action': selector(producer), 'ordinal': int(path.rsplit('/', 1)[1])})
    item['sources'] = converted


def seal(value):
    value['projection_hash'] = digest({key: item for key, item in value.items() if key != 'projection_hash'})
    return value


def test_complete_schema_and_real_recursive_addresses_have_dedicated_markers_and_binding_consts():
    value, source = fixture()
    schema = subject.output_schema(source['scope_binding'])
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(value)) == []
    assert schema == subject._schema_for_shape(source['scope_binding']['name'], digest(source['scope_binding']))
    assert set(schema['properties']) == {'schema', 'plan', 'causality'}
    assert schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] == source['scope_binding']['name']
    assert schema['$defs']['BusinessFlowNestedPlanV192']['properties']['binding_hash']['const'] == digest(source['scope_binding'])
    assert set(schema['$defs']['ActionSelectorV193']['properties']) == {'flow_index', 'address', 'part', 'step_index'}
    assert len(schema['$defs']['ActionSelectorV193']['oneOf']) == 6
    assert subject.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    projected = project(value, source)
    deepest = FP + '/phases/1/branches/0/phases/0/branches/1/phases/0/branches/0/phases/0/steps/0'
    row = next(item for item in projected['causal_analysis']['paths']['actions'] if item['path'] == deepest)
    assert selector(row) == {'flow_index': 0, 'address': [
        {'phase_index': 1, 'branch_index': 0}, {'phase_index': 0, 'branch_index': 1},
        {'phase_index': 0, 'branch_index': 0}, {'phase_index': 0, 'branch_index': None}],
        'part': 'step', 'step_index': 0}
    assert projected['causal_analysis']['total_scenarios'] == 3


@pytest.mark.parametrize('capacity', ['phases', 'actions', 'depth'])
def test_whole_ast_capacity_preflight_precedes_outer_recursive_pydantic(monkeypatch, capacity):
    value, source = fixture(False)
    flow = value['plan']['business']['flows'][0]
    template = copy.deepcopy(flow['phases'][0])
    if capacity == 'phases':
        flow['phases'] = [copy.deepcopy(template) for _ in range(plans.MAX_PHASES_PER_FLOW + 1)]
    elif capacity == 'actions':
        template['steps'] *= 16
        flow['phases'] = [copy.deepcopy(template), copy.deepcopy(template)]
    else:
        att = copy.deepcopy(template['attribution'])
        action = copy.deepcopy(template['steps'][0])
        child = template
        for _ in range(plans.MAX_CONTROL_DEPTH + 1):
            child = {'kind': 'decision', 'attribution': copy.deepcopy(att), 'decision': copy.deepcopy(action),
                'branches': [{'condition': '需要继续核对', 'attribution': copy.deepcopy(att), 'phases': [child]},
                    {'condition': '无需继续核对', 'attribution': copy.deepcopy(att), 'phases': []}]}
        flow['phases'] = [child]
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Outer Pydantic must not descend into an over-capacity AST')
    monkeypatch.setattr(subject.BusinessFlowNestedCausalPlanV193, 'model_validate', forbidden)
    with pytest.raises(ValueError): subject.parse_plan(raw(value), scope_binding=source['scope_binding'])
    assert calls == []


def test_original_raw_ast_quotes_and_causal_proof_are_kept_without_compiling_an_old_wire():
    value, source = fixture()
    before = canonical_json([value, source])
    payload = b'\n' + json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    projected = subject.build_projection(payload, **source)
    assert projected['schema'] == subject.PROJECTION
    assert projected['wire'] == projected['raw_plan'] == value['plan']
    assert projected['wire']['schema'] == plans.PLAN
    assert projected['raw_causal_plan'] == value and projected['raw_causal_plan_schema'] == subject.WIRE
    assert projected['raw_causal_plan_bytes_sha256'] == hashlib.sha256(payload).hexdigest()
    assert projected['raw_causal_plan_bytes'] == len(payload)
    assert projected['raw_causal_plan_value_hash'] == digest(value)
    assert projected['wire_canonical_bytes_sha256'] == hashlib.sha256(subject._bytes(value['plan'])).hexdigest()
    assert projected['raw_causal_plan_bytes_sha256'] != projected['wire_canonical_bytes_sha256']
    assert projected['compiled_sidecar']['schema'] == causal.BINDINGS
    assert projected['compiled_sidecar_hash'] == digest(projected['compiled_sidecar'])
    assert projected['causal_analysis_hash'] == projected['causal_analysis']['availability_hash']
    assert projected['plan_counts'] == {'flow_count': 1, 'phase_count': 7, 'action_count': 9,
        'input_count': 9, 'output_count': 9}
    originals = {item['span_ref']: item['text'] for item in resolve_evidence(
        source['catalog'], source['allowed_span_refs'], source['allowed_span_refs'])}
    assert projected['evidence'] == [{'span_ref': ref, 'quote': originals[ref]} for ref in projected['used_refs']]
    assert projected['quote_origin'] == 'platform_resolved_source'
    assert all(projected[key] is False for key in subject._FLAGS)
    assert projected['selected_input_claims'] == source['input_claims']
    assert subject.check_projection(projected, payload, **source) == projected
    assert canonical_json([value, source]) == before


@pytest.mark.parametrize('fault', ['bool_flow', 'float_ordinal', 'negative', 'unknown_flow', 'unknown_phase',
    'unknown_branch', 'unknown_step', 'middle_null', 'last_nonnull', 'wrong_part', 'legacy_part',
    'completion_address', 'completion_step', 'empty_noncompletion', 'extra_selector', 'branch_under_leaf'])
def test_selector_is_a_strict_actual_address_not_a_name_or_flattened_phase_index(fault):
    value, source = fixture()
    selected = next(item for item in value['causality']['output_contracts'] if len(item['action']['address']) == 4)
    action = selected['action']
    if fault == 'bool_flow': action['flow_index'] = True
    elif fault == 'float_ordinal': selected['ordinal'] = 0.0
    elif fault == 'negative': action['step_index'] = -1
    elif fault == 'unknown_flow': action['flow_index'] = 99
    elif fault == 'unknown_phase': action['address'][-1]['phase_index'] = 99
    elif fault == 'unknown_branch': action['address'][0]['branch_index'] = 99
    elif fault == 'unknown_step': action['step_index'] = 99
    elif fault == 'middle_null': action['address'][1]['branch_index'] = None
    elif fault == 'last_nonnull': action['address'][-1]['branch_index'] = 0
    elif fault == 'wrong_part': action['part'] = 'repeat_step'
    elif fault == 'legacy_part': action['part'] = 'branch_step'
    elif fault == 'completion_address': action.update(part='completion', step_index=None)
    elif fault == 'completion_step': action.update(part='completion', address=[], step_index=0)
    elif fault == 'empty_noncompletion': action['address'] = []
    elif fault == 'branch_under_leaf': action['address'][0] = {'phase_index': 0, 'branch_index': 0}
    else: action['path'] = FP + '/phases/0/steps/0'
    with pytest.raises(ValueError):
        subject.parse_plan(raw(value), scope_binding=source['scope_binding'])


@pytest.mark.parametrize('fault', ['missing_input', 'duplicate_input', 'missing_output', 'duplicate_output',
    'entry_flow', 'entry_index', 'unknown_ordinal', 'duplicate_source', 'empty_source', 'unit', 'type', 'extra'])
def test_complete_original_port_contracts_and_same_flow_rules_are_not_optional(fault):
    value, source = fixture(False)
    declared = value['causality']
    if fault == 'missing_input': declared['input_bindings'].pop()
    elif fault == 'duplicate_input': declared['input_bindings'].append(copy.deepcopy(declared['input_bindings'][0]))
    elif fault == 'missing_output': declared['output_contracts'].pop()
    elif fault == 'duplicate_output': declared['output_contracts'].append(copy.deepcopy(declared['output_contracts'][0]))
    elif fault == 'entry_flow': declared['entry_ports'][0]['flow_index'] = 9
    elif fault == 'entry_index': declared['input_bindings'][0]['sources'][0]['index'] = 9
    elif fault == 'unknown_ordinal': declared['input_bindings'][0]['ordinal'] = 19
    elif fault == 'duplicate_source': declared['input_bindings'][0]['sources'] *= 2
    elif fault == 'empty_source': declared['input_bindings'][0]['sources'] = []
    elif fault == 'unit': declared['input_bindings'][0]['contract']['unit'] = '℃'
    elif fault == 'type': declared['input_bindings'][0]['contract']['data_type'] = 'number'
    else: declared['loop_carried'] = True
    with pytest.raises(ValueError):
        subject.parse_plan(raw(value), scope_binding=source['scope_binding'])


@pytest.mark.parametrize('path', [
    '/business/flows/0/phase_order', '/business/flows/0/trigger/attribution',
    '/business/flows/0/phases/1/attribution', '/business/flows/0/phases/1/branches/1/attribution',
    '/business/flows/0/phases/1/branches/0/phases/0/join_attribution',
    '/business/flows/0/phases/1/branches/0/phases/0/branches/1/phases/0/decision/attribution',
    '/business/flows/0/phases/1/branches/0/phases/0/branches/0/phases/0/steps/0/attribution',
    '/business/flows/0/phases/2/check/attribution', '/business/flows/0/completion/attribution'])
def test_every_recursive_attribution_including_empty_branch_and_order_has_real_source(path):
    value, source = fixture()
    at(value['plan'], path)['citations'] = ['w:p999999/s1']
    assert subject.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    with pytest.raises(ValueError): project(value, source)
    with pytest.raises(ValueError): subject.validate_wire_value(value['plan'], **source)


def test_questions_retain_unknowns_but_cannot_invent_source_references():
    value, source = fixture()
    question = {'question': '补采条件还需要谁核对？', 'reason': '原资料未明确执行岗位。', 'citations': []}
    value['plan']['business']['questions'] = [question]
    projected = project(value, source)
    assert projected['wire']['business']['questions'] == [question]
    question['citations'] = ['w:p999999/s1']
    with pytest.raises(ValueError): project(value, source)


@pytest.mark.parametrize('fault', ['context', 'claim_value', 'claim_order', 'allowed', 'scope', 'catalog_pin'])
def test_independent_cold_source_inputs_cannot_be_replaced_by_projection_self_hashes(fault):
    value, source = fixture()
    if fault == 'context': source['context']['user_instruction'] = '改变源上下文'
    elif fault == 'claim_value': source['input_claims'][0]['statement'] += '改变原声明'
    elif fault == 'claim_order': source['input_claims'] = list(reversed(source['input_claims']))
    elif fault == 'allowed': source['allowed_span_refs'] = list(reversed(source['allowed_span_refs']))
    elif fault == 'scope': source['scope_binding']['scope_id'] = 'another-scope'
    else: source['catalog']['descriptor']['catalog_hash'] = '0' * 64
    with pytest.raises(ValueError): project(value, source)


def test_future_value_and_skipped_nested_observation_cannot_be_hidden_by_entry_or_label():
    value, source = fixture()
    observe_path = FP + '/phases/1/branches/0/phases/0/branches/1/phases/0/branches/0/phases/0/steps/0/outputs/0'
    bind(value, FP + '/phases/0/steps/0/inputs/0', '/entry_ports/0', observe_path,
        scope_binding=source['scope_binding'])
    assert subject.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    with pytest.raises(ValueError, match='source_not_available'): project(value, source)
    value, source = fixture()
    bind(value, FP + '/completion/inputs/0', observe_path, scope_binding=source['scope_binding'])
    with pytest.raises(ValueError, match='input_not_guaranteed'): project(value, source)


def test_dedicated_human_draft_recurses_acknowledgements_without_inheriting_original_causality():
    value, source = fixture()
    projected = project(value, source)
    manual = subject.new_manual(projected)
    assert manual == {'schema': subject.MANUAL, 'result': value['plan'], 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}
    assert list(Draft202012Validator(subject.manual_value_schema(source['scope_binding'])).iter_errors(manual)) == []
    assert subject.validate_value(manual, **source) == manual
    with pytest.raises(ValueError): subject.confirm_value(manual)
    manual['acknowledge_partial_scope'] = True
    assert subject.confirm_value(manual) == manual
    nested = manual['result']['business']['flows'][0]['phases'][1]['branches'][0]['phases'][0]
    nested['join_attribution']['inferred'] = True
    manual['note'] = '单独修改人工稿；原侧车不跟随修改。'
    assert subject.validate_value(manual, **source) == manual
    with pytest.raises(ValueError): subject.confirm_value(manual)
    manual['acknowledge_inferences'] = True
    assert subject.confirm_value(manual) == manual
    assert projected['wire'] == value['plan'] and projected['wire'] != manual['result']
    assert 'causality' not in manual and 'causal_analysis' not in manual


def test_zero_phase_plan_and_duplicate_labels_keep_full_original_ordinals():
    value, source = fixture(False)
    plan = value['plan']
    plan['business']['flows'][0]['phases'] = []
    plan['business']['flows'][0]['completion']['inputs'] = []
    projected = project(envelope(plan, source['scope_binding']), source)
    assert projected['plan_counts']['action_count'] == 1 and projected['compiled_sidecar']['input_bindings'] == []
    assert subject.validate_value(subject.new_manual(projected), **source)['result'] == plan
    value, source = fixture(False)
    first = value['plan']['business']['flows'][0]['phases'][0]['steps'][0]
    first['outputs'] = ['同名输出', '同名输出', '第三份记录']
    value['plan']['business']['flows'][0]['completion']['inputs'] = ['同名输入', '同名输入']
    value = envelope(value['plan'], source['scope_binding'])
    for i, ordinal in enumerate((1, 2)):
        bind(value, FP + '/completion/inputs/' + str(i), FP + '/phases/0/steps/0/outputs/' + str(ordinal),
            scope_binding=source['scope_binding'])
    projected = project(value, source)
    assert projected['wire']['business']['flows'][0]['phases'][0]['steps'][0]['outputs'] == first['outputs']
    assert len(projected['compiled_sidecar']['output_contracts']) == 4


@pytest.mark.parametrize('fault', ['raw_plan', 'sidecar', 'analysis', 'wire', 'evidence', 'flag', 'count_type'])
def test_resealed_projection_tampering_fails_cold_rebuild_and_internal_analysis(fault):
    value, source = fixture(False)
    projected = project(value, source)
    if fault == 'raw_plan': projected['raw_causal_plan']['plan']['business']['flows'][0]['completion']['name'] = '改写原计划'
    elif fault == 'sidecar': projected['compiled_sidecar']['entry_ports'][0]['path'] = '/entries/0'
    elif fault == 'analysis': projected['causal_analysis']['paths']['actions'][0]['control_ancestors'] = []
    elif fault == 'wire': projected['wire']['business']['flows'][0]['name'] = '改写展示名称'
    elif fault == 'evidence': projected['evidence'][0]['quote'] += '伪造引用'
    elif fault == 'count_type': projected['plan_counts']['flow_count'] = True
    else: projected['can_execute'] = True
    seal(projected)
    with pytest.raises(ValueError): subject.check_projection(projected, raw(value), **source)
    with pytest.raises(ValueError): subject.new_manual(projected)


@pytest.mark.parametrize('fault', ['truncated', 'duplicate_key', 'old_outer', 'old_inner', 'extra_outer'])
def test_new_decoder_never_relabels_old_or_incomplete_paid_protocols(fault):
    value, source = fixture(False)
    if fault == 'old_outer': value['schema'] = 'report-business-flow-causal-plan-v190'
    elif fault == 'old_inner': value['plan']['schema'] = 'report-business-flow-plan-v184'
    elif fault == 'extra_outer': value['source_authority_verified'] = True
    payload = raw(value)
    if fault == 'truncated': payload = payload[:-1]
    elif fault == 'duplicate_key': payload = b'{"schema":"wrong",' + payload[1:]
    with pytest.raises(ValueError): subject.build_projection(payload, **source)


def test_complete_wire_and_projection_capacity_reject_without_truncation(monkeypatch):
    value, source = fixture()
    projected = project(value, source)
    before = canonical_json(value)
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', len(subject._bytes(projected)) - 1)
    with pytest.raises(subject.BusinessFlowNestedCausalCapacityError): project(value, source)
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', 8 * 1024 * 1024)
    monkeypatch.setattr(subject, 'MAX_WIRE_BYTES', len(raw(value)) - 1)
    with pytest.raises(ValueError): project(value, source)
    assert canonical_json(value) == before
