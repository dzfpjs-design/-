"""Synthetic recursive AST/coordinate checks, without external authority.

Real V192 Pydantic/schema/walk functions are exercised. Public V184 fixtures
provide declarations only; this is not source, provider, runtime or IAM proof.
Root executes these tests. The author did not import or run the project.
"""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from generic_agents import business_flow_nested_plan_v192 as subject
from generic_agents import report_business_flow_wire_v184 as previous
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import no_external, scene as previous_scene


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def scene():
    plan, source, action, _, attribution = previous_scene(4)
    plan['schema'] = subject.PLAN
    return plan, source, action, attribution


def steps(actions, attribution):
    return {'kind': 'steps', 'attribution': copy.deepcopy(attribution), 'steps': copy.deepcopy(actions)}


def decision(action, attribution, branches):
    return {'kind': 'decision', 'attribution': copy.deepcopy(attribution), 'decision': copy.deepcopy(action),
        'branches': [{'condition': '明确条件' + str(i + 1), 'attribution': copy.deepcopy(attribution),
            'phases': copy.deepcopy(phases)} for i, phases in enumerate(branches)]}


def parallel(action, attribution, branches, *, mode='all'):
    return {'kind': 'parallel', 'attribution': copy.deepcopy(attribution), 'dispatch': copy.deepcopy(action),
        'branches': [{'phases': copy.deepcopy(phases)} for phases in branches],
        'wait_mode': mode, 'join_attribution': copy.deepcopy(attribution)}


def repeat(action, attribution, *, count=1):
    return {'kind': 'repeat', 'attribution': copy.deepcopy(attribution),
        'steps': [action('重复核验' + str(i + 1)) for i in range(count)],
        'check': action('核对是否需要下一轮'), 'repeat_if': '仍有未完成的核验事项',
        'finish_if': '本轮核验事项已经完成'}


def nested_scene():
    plan, source, action, attribution = scene()
    conditional = decision(action('判断设备是否具备条件'), attribution,
        [[steps([action('执行补充核验')], attribution)], []])
    concurrent = parallel(action('安排独立通知与核验'), attribution,
        [[steps([action('立即通知')], attribution)], [conditional]])
    plan['business']['flows'][0]['phases'] = [decision(action('人工明确确认'), attribution,
        [[concurrent], [steps([action('仅保存未确认记录')], attribution)]])]
    return plan, source, action, attribution


def at(value, pointer):
    for token in pointer.split('/')[1:]:
        value = value[int(token)] if type(value) is list else value[token]
    return value


def parse(plan, source):
    return subject.parse_plan(raw(plan), scope_binding=source['scope_binding'])


def test_recursive_schema_keeps_old_action_contract_and_new_branches_are_phases():
    plan, source, _, _ = nested_scene()
    schema = subject.output_schema(source['scope_binding'])
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(plan)) == []
    assert schema['properties']['binding_hash']['const'] == digest(source['scope_binding'])
    assert schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] == plan['business']['name']
    for name, keys in [('DecisionBranchV192', {'condition', 'attribution', 'phases'}),
            ('ParallelBranchV192', {'phases'})]:
        assert set(schema['$defs'][name]['properties']) == keys
        assert schema['$defs'][name]['additionalProperties'] is False
    assert schema['$defs']['ActionV184']['properties'] == previous.ActionV184.model_json_schema()['properties']
    assert subject._schema_for_shape(plan['business']['name'], plan['binding_hash']) == schema
    assert subject.MAX_CONTROL_DEPTH == 3 and subject.MAX_WIRE_BYTES == 1024 * 1024


def test_three_controls_allow_leaf_steps_without_flattening_or_mutating_inputs():
    plan, source, _, _ = nested_scene()
    before = canonical_json([plan, source])
    result = parse(plan, source)
    assert canonical_json(result) == canonical_json(plan)
    assert canonical_json([plan, source]) == before
    result['business']['flows'][0]['name'] = '仅改返回副本'
    assert canonical_json([plan, source]) == before


@pytest.mark.parametrize('mode', ['all', 'any'])
def test_empty_declared_branches_do_not_invent_actions_or_else_conditions(mode):
    plan, source, action, attr = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action('判断输入条件'), attr, [[], []]),
        parallel(action('安排并行核验'), attr, [[], []], mode=mode)]
    assert parse(plan, source) == plan
    paths = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    assert [row['part'] for row in paths['actions']] == ['decision', 'dispatch', 'completion']
    assert all('/branches/' not in row['path'] for row in paths['actions'])
    assert flow['phases'][0]['branches'][0]['condition'] != flow['phases'][0]['branches'][1]['condition']


def test_walk_preserves_exact_recursive_positions_source_order_and_all_port_ordinals():
    plan, source, _, _ = nested_scene()
    leaf = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    leaf['inputs'] = ['相同资料', '相同资料']
    leaf['outputs'] = ['相同记录', '相同记录']
    before = canonical_json(plan)
    walked = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    prefix = '/business/flows/0/phases/0'
    notify = prefix + '/branches/0/phases/0/branches/0/phases/0/steps/0'
    condition = prefix + '/branches/0/phases/0/branches/1/phases/0'
    expected = [prefix + '/decision', prefix + '/branches/0/phases/0/dispatch', notify,
        condition + '/decision', condition + '/branches/0/phases/0/steps/0',
        prefix + '/branches/1/phases/0/steps/0', '/business/flows/0/completion']
    assert [row['path'] for row in walked['actions']] == expected
    assert walked['schema'] == subject.PATHS and walked['source_plan_schema'] == subject.PLAN
    assert walked['source_plan_hash'] == digest(plan) and walked['scope_binding_hash'] == digest(source['scope_binding'])
    assert walked['walk_hash'] == digest({key: value for key, value in walked.items() if key != 'walk_hash'})
    assert set(walked) == {'schema', 'source_plan_schema', 'source_plan_hash', 'scope_binding_hash',
        'actions', 'input_paths', 'output_paths', 'causal_declaration_consistency_only', 'walk_hash',
        'source_authority_verified', 'semantic_quality_verified', 'entry_facts_verified',
        'permissions_verified', 'can_apply', 'can_publish', 'can_execute'}
    assert walked['causal_declaration_consistency_only'] is True
    assert all(walked[key] is False for key in ('source_authority_verified', 'semantic_quality_verified',
        'entry_facts_verified', 'permissions_verified', 'can_apply', 'can_publish', 'can_execute'))
    row = walked['actions'][2]
    assert row['input_ports'] == [{'path': notify + '/inputs/' + str(i), 'ordinal': i, 'label': '相同资料'} for i in range(2)]
    assert row['output_ports'] == [{'path': notify + '/outputs/' + str(i), 'ordinal': i, 'label': '相同记录'} for i in range(2)]
    assert [item['kind'] for item in row['control_ancestors']] == [
        'decision', 'decision_branch', 'parallel', 'parallel_branch', 'steps']
    assert [item['branch_index'] for item in row['control_ancestors']] == [None, 0, None, 0, None]
    assert walked['actions'][-1]['phase_index'] is None and walked['actions'][-1]['control_ancestors'] == []
    for item in walked['actions']:
        assert at(plan, item['path'])['name'] == item['name']
        assert item['flow_path'] == '/business/flows/0' and item['flow_index'] == 0
        for port in item['input_ports'] + item['output_ports']:
            assert at(plan, port['path']) == port['label']
        for ancestor in item['control_ancestors']:
            assert type(at(plan, ancestor['path'])) is dict
    assert walked['input_paths'] == [port['path'] for item in walked['actions'] for port in item['input_ports']]
    assert walked['output_paths'] == [port['path'] for item in walked['actions'] for port in item['output_ports']]
    assert canonical_json(plan) == before


def test_repeat_remains_nonrecursive_and_its_check_has_an_original_pointer():
    plan, source, action, attr = scene()
    plan['business']['flows'][0]['phases'] = [decision(action('判断是否需要复核'), attr,
        [[repeat(action, attr, count=2)], []])]
    assert parse(plan, source) == plan
    walked = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    assert [row['part'] for row in walked['actions']] == ['decision', 'repeat_step', 'repeat_step', 'check', 'completion']
    assert walked['actions'][3]['path'] == '/business/flows/0/phases/0/branches/0/phases/0/check'
    phase = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]
    phase['steps'][0] = steps([action()], attr)
    with pytest.raises(ValueError):
        parse(plan, source)


def test_declared_reference_inventory_includes_empty_branches_and_join_attribution():
    plan, source, action, attr = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [decision(action('核对条件'), attr, [[], []]),
        parallel(action('独立核对两路结果'), attr, [[], []])]
    # These remain syntactic declarations; this helper cannot certify a catalog.
    first, branch_ref, join_ref, question_ref = attr['citations'][0], 'w:p987/s1', 'w:p988/s1', 'w:p989/s1'
    flow['phases'][0]['branches'][1]['attribution']['citations'] = [branch_ref, branch_ref]
    flow['phases'][1]['join_attribution']['citations'] = [join_ref]
    plan['business']['questions'] = [{'question': '是否需要业务人员补充明确范围？',
        'reason': '纯合成待核事项，不将声明当成权威原文。', 'citations': [question_ref]}]
    assert subject.attribution_refs(parse(plan, source)) == [first, branch_ref, join_ref, question_ref]


@pytest.mark.parametrize('fault', ['extra_top', 'old_schema', 'old_branch_steps', 'actor', 'bool_inferred',
    'string_steps', 'missing_outputs', 'null_phases', 'bad_wait', 'repeat_nested', 'too_many_flows'])
def test_closed_new_ast_never_silently_converts_old_or_malformed_values(fault):
    plan, source, action, attr = nested_scene()
    top = plan['business']['flows'][0]['phases'][0]
    if fault == 'extra_top': plan['private_note'] = '不属于AST'
    elif fault == 'old_schema': plan['schema'] = previous.WIRE
    elif fault == 'old_branch_steps': top['branches'][0] = {'condition': '旧形状', 'attribution': attr, 'steps': [action()]}
    elif fault == 'actor': top['decision']['actor'] = 'automatic_administrator'
    elif fault == 'bool_inferred': top['attribution']['inferred'] = 1
    elif fault == 'string_steps': plan['business']['flows'][0]['phases'] = [steps('not-actions', attr)]
    elif fault == 'missing_outputs': del top['decision']['outputs']
    elif fault == 'null_phases': top['branches'][0]['phases'] = None
    elif fault == 'bad_wait': top['branches'][0]['phases'][0]['wait_mode'] = 'first_two'
    elif fault == 'repeat_nested': plan['business']['flows'][0]['phases'] = [{**repeat(action, attr), 'phases': []}]
    else: plan['business']['flows'] = [copy.deepcopy(plan['business']['flows'][0]) for _ in range(17)]
    with pytest.raises(ValueError):
        parse(plan, source)


@pytest.mark.parametrize('fault', ['binding_hash', 'name', 'binding_source_hash', 'binding_extra'])
def test_scope_name_and_complete_original_binding_are_required(fault):
    plan, source, _, _ = scene()
    if fault == 'binding_hash': plan['binding_hash'] = 'b' * 64
    elif fault == 'name': plan['business']['name'] = '另一个业务'
    elif fault == 'binding_source_hash': source['scope_binding']['source_hash'] = 'c' * 64
    else: source['scope_binding']['current_user_is_admin'] = True
    with pytest.raises(ValueError):
        parse(plan, source)


@pytest.mark.parametrize('bad', [b'', b'[]', b'null', b'\xff', b'{"schema":1,"schema":2}',
    b'{"value":NaN}', b'{"value":Infinity}', bytearray(b'{}'), '{}'])
def test_original_wire_requires_bytes_unique_keys_and_finite_json(bad):
    _, source, _, _ = scene()
    with pytest.raises(ValueError):
        subject.parse_plan(bad, scope_binding=source['scope_binding'])


def test_fourth_control_is_rejected_before_recursive_pydantic(monkeypatch):
    plan, source, action, attr = scene()
    inner = steps([action('真实叶子')], attr)
    for _ in range(4):
        inner = decision(action('保留明确控制条件'), attr, [[inner], []])
    plan['business']['flows'][0]['phases'] = [inner]
    def forbidden(*args, **kwargs):
        pytest.fail('capacity must be checked before recursive model validation')
    monkeypatch.setattr(subject.BusinessFlowNestedPlanV192, 'model_validate', forbidden)
    with pytest.raises(subject.NestedPlanCapacityError):
        parse(plan, source)


def test_aggregate_phase_limit_counts_all_declared_branches_not_only_one_path():
    plan, source, action, attr = scene()
    branch = [steps([action()], attr) for _ in range(6)]
    plan['business']['flows'][0]['phases'] = [decision(action('明确选择核验分支'), attr, [branch, branch])]
    with pytest.raises(subject.NestedPlanCapacityError):
        parse(plan, source)  # 1 decision + 12 leaf phases, not merely a path of 7.
    plan['business']['flows'][0]['phases'][0]['branches'][1]['phases'].pop()
    assert parse(plan, source) == plan


def test_action_limit_includes_check_dispatch_decision_and_completion():
    plan, source, action, attr = scene()
    flow = plan['business']['flows'][0]
    flow['phases'] = [repeat(action, attr, count=16), steps([action() for _ in range(14)], attr)]
    assert len(subject.walk_action_ports(plan, scope_binding=source['scope_binding'])['actions']) == 32
    flow['phases'][1]['steps'].append(action())
    with pytest.raises(subject.NestedPlanCapacityError):
        parse(plan, source)
    flow['phases'] = [parallel(action('安排两路核验'), attr,
        [[steps([action() for _ in range(16)], attr)], [steps([action() for _ in range(15)], attr)]])]
    with pytest.raises(subject.NestedPlanCapacityError):
        parse(plan, source)  # 31 leaves + dispatch + completion.
    flow['phases'][0]['branches'][1]['phases'][0]['steps'].pop()
    assert len(subject.walk_action_ports(plan, scope_binding=source['scope_binding'])['actions']) == 32


@pytest.mark.parametrize('kind,count', [('decision', 1), ('decision', 7), ('parallel', 1), ('parallel', 7)])
def test_branch_capacity_is_closed_and_never_truncated(kind, count):
    plan, source, action, attr = scene()
    constructor = decision if kind == 'decision' else parallel
    plan['business']['flows'][0]['phases'] = [constructor(action('保留各声明分支'), attr, [[] for _ in range(count)])]
    with pytest.raises(ValueError):
        parse(plan, source)


def test_zero_phases_multiple_flows_and_exact_byte_capacity_keep_full_input(monkeypatch):
    plan, source, _, _ = scene()
    plan['business']['flows'][0]['phases'] = []
    plan['business']['flows'].append(copy.deepcopy(plan['business']['flows'][0]))
    walked = subject.walk_action_ports(plan, scope_binding=source['scope_binding'])
    assert [row['path'] for row in walked['actions']] == ['/business/flows/0/completion', '/business/flows/1/completion']
    compact = canonical_json(plan).encode('utf-8')
    monkeypatch.setattr(subject, 'MAX_WIRE_BYTES', len(compact))
    assert subject.parse_plan(compact, scope_binding=source['scope_binding']) == plan
    with pytest.raises(subject.NestedPlanCapacityError):
        subject.parse_plan(compact + b' ', scope_binding=source['scope_binding'])
    monkeypatch.setattr(subject, 'MAX_WIRE_BYTES', len(compact) - 1)
    with pytest.raises(subject.NestedPlanCapacityError):
        subject.check_plan(plan, scope_binding=source['scope_binding'])


def test_old_paid_schema_and_original_plan_remain_independent():
    original, source, _, _, _ = previous_scene(4)
    before = canonical_json([original, previous.output_schema(source['scope_binding'])])
    assert previous.parse_plan(raw(original), scope_binding=source['scope_binding']) == original
    new = copy.deepcopy(original); new['schema'] = subject.PLAN
    assert parse(new, source) == new
    with pytest.raises(ValueError):
        subject.check_plan(original, scope_binding=source['scope_binding'])
    with pytest.raises(ValueError):
        previous.parse_plan(raw(new), scope_binding=source['scope_binding'])
    assert canonical_json([original, previous.output_schema(source['scope_binding'])]) == before
