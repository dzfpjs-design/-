"""Typed draft edits with synthetic bindings; no registered execution is claimed."""
import copy
import json
import socket
import subprocess
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.instance_compiler import signature_value


SEED = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('pure edit engine forbids external effects')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)


def engine():
    from generic_agents import setup_edit_operations
    return setup_edit_operations


def blocks():
    return [{'material_id': 'A', 'block_id': 'p1', 'text': '只修改第三步负责人，其余步骤保留。', 'location': {}}]


def config():
    steps = [
        {'id': '11111111-1111-4111-8111-111111111111', 'name': '先人工确认', 'agent': '人工确认',
         'role': '确认员', 'purpose': '确认原件', 'output': '确认记录', 'nextId': 'auto', 'elseId': 'end',
         'planning_tools': [{'capability_id': 'synthetic-read-tool', 'allow_external': False}], 'custom': {'kept': 1}},
        {'id': '22222222-2222-4222-8222-222222222222', 'name': '本机计算', 'agent': '数据管理智能体',
         'role': '', 'purpose': '计算原结果', 'output': '计算记录', 'nextId': 'auto', 'elseId': 'end',
         'execution': {'capability_id': 'synthetic-local-tool', 'automation_action': 'data',
                       'arguments': {'value': 1}, 'bindings': {}, 'allow_external': False}},
        {'id': '33333333-3333-4333-8333-333333333333', 'name': '后人工复核', 'agent': '人工确认',
         'role': '原复核员', 'purpose': '复核结果', 'output': '最终记录', 'nextId': 'end', 'elseId': 'end'},
    ]
    value = {'version': 1, 'instanceProfile': {'name': '合成实例', 'domain': '核查', 'goal': '保留人稿'},
             'businesses': [{'name': '原业务', 'custom': 'keep'}, '旁支'], 'businessAliases': {},
             'materials': [], 'connections': [], 'resources': [{'id': 'r', 'businesses': ['原业务', '旁支']}],
             'gaps': [{'business': '原业务', 'note': '保留说明'}],
             'flows': {'原业务': {'trigger': '人工发起', 'output': '核对记录', 'steps': steps, 'custom': 'flow-kept'},
                       '旁支': {'trigger': '人工发起', 'output': '旁支记录', 'steps': [
                           {'id': '44444444-4444-4444-8444-444444444444', 'name': '旁支人工', 'agent': '人工确认',
                            'role': '旁支员', 'output': '旁支结果', 'nextId': 'end', 'elseId': 'end'}]}}}
    from generic_agents.setup_automation import safe_policy, metadata
    value['automation'] = {'version': 1, 'policies': {}}
    for name, flow in value['flows'].items():
        flow['confirmedSignature'] = json.dumps(signature_value(value, flow), ensure_ascii=False)
        flow['confirmedAt'] = 'saved-at'
        policy = safe_policy(); policy['flow_review'] = metadata(value, name, policy, 'confirmed')
        value['automation']['policies'][name] = policy
    return value


def operation(kind='set_step', **fields):
    return {'id': 'edit_1', 'type': kind, 'dependencies': [], 'citations': [],
            'inference_reason': '用户明确要求', **fields}


def prepared(value, operations):
    e = engine(); context, handles = e.project(value, blocks(), scrub_text=lambda x: x)
    result = e.validate_edit_result({'operations': operations, 'questions': []}, blocks(), value, handles)
    return e, context, handles, result


def test_role_only_patch_keeps_all_unrelated_bindings_ids_and_metadata():
    value = config(); before = copy.deepcopy(value)
    e, _, handles, result = prepared(value, [operation(target='s_3', field='role', value='新复核员')])
    saved, rows = e.apply_edits(value, result, handles, ['edit_1'], SEED, blocks())
    old, new = value['flows']['原业务']['steps'], saved['flows']['原业务']['steps']
    assert [s['id'] for s in old] == [s['id'] for s in new]
    assert new[:2] == old[:2] and new[2] == {**old[2], 'role': '新复核员'}
    assert saved['flows']['原业务']['custom'] == 'flow-kept'
    assert saved['flows']['旁支'] == value['flows']['旁支']
    assert saved['automation']['policies']['旁支'] == value['automation']['policies']['旁支']
    assert saved['flows']['原业务']['confirmedSignature'] is None
    assert rows[0]['before'] == '原复核员' and rows[0]['after'] == '新复核员'
    assert value == before


def test_noop_is_exact_configuration_and_preserves_confirmations():
    value = config(); e, _, h, result = prepared(value, [operation(target='s_3', field='role', value='原复核员')])
    saved, rows = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    assert saved == value and rows[0]['before'] == rows[0]['after']


def test_changed_bound_step_retains_raw_but_requires_specific_binding_review():
    value = config(); e, _, h, result = prepared(value, [operation(target='s_1', field='role', value='新确认员')])
    saved, rows = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    before, after = value['flows']['原业务']['steps'][0], saved['flows']['原业务']['steps'][0]
    assert after['planning_tools'] == before['planning_tools']
    assert after['bindingReview']['planning_tools_hash'] == digest(before['planning_tools'])
    assert rows[0]['effects'] and saved['flows']['原业务']['steps'][1] == value['flows']['原业务']['steps'][1]


def test_model_cannot_set_execution_or_private_fields():
    for fields in ({'target': 's_2', 'field': 'execution', 'value': '{}'},
                   {'target': 's_2', 'field': 'role', 'value': 'x', 'execution': {}},
                   {'target': 's_1', 'field': 'planning_tools', 'value': '[]'}):
        with pytest.raises(ValueError): prepared(config(), [operation(**fields)])


def test_projection_has_only_scoped_handles_and_handles_are_configuration_bound():
    value = config(); before = copy.deepcopy(value)
    e = engine(); context, handles = e.project(value, blocks(), scrub_text=lambda x: x)
    assert handles['configuration_hash'] == digest(value)
    assert list(handles['businesses']) == ['b_1', 'b_2']
    assert [s['handle'] for s in context['flows']['原业务']['steps']] == ['s_1', 's_2', 's_3']
    encoded = json.dumps(context)
    assert '11111111-1111' not in encoded and 'synthetic-read-tool' not in encoded
    handles['steps']['s_1']['step_id'] = 'guessed-id'
    with pytest.raises(ValueError): e.validate_edit_result({'operations': [], 'questions': []}, blocks(), value, handles)
    assert value == before


def test_projection_routes_use_handles_even_with_same_named_steps():
    value = config(); steps = value['flows']['原业务']['steps']
    steps[0]['nextId'] = steps[2]['id']; steps[1]['name'] = steps[2]['name']
    context, _ = engine().project(value, blocks(), scrub_text=lambda x: x)
    assert context['flows']['原业务']['steps'][0]['next_handle'] == 's_3'
    assert context['flows']['原业务']['steps'][1]['next_handle'] == 'auto'
    steps[0]['nextId'] = 'unknown-private-id'
    context, _ = engine().project(value, blocks(), scrub_text=lambda x: x)
    assert context['flows']['原业务']['steps'][0]['next_handle'] == '未核对'
    assert 'unknown-private-id' not in json.dumps(context)


@pytest.mark.parametrize('fields', [
    {'target': 's_99', 'field': 'role', 'value': 'x'},
    {'target': 's_1', 'field': 'nextId', 'value': 's_4'},
    {'target': 's_1', 'field': 'agent', 'value': '管理员'},
    {'target': 's_1', 'field': 'name', 'value': ''},
    {'target': 's_1', 'field': 'role', 'value': 1},
])
def test_outside_scope_or_invalid_field_values_fail(fields):
    with pytest.raises(ValueError): prepared(config(), [operation(**fields)])


def test_bad_citation_duplicate_ids_and_dependency_cycles_fail():
    for operations in (
        [operation(target='s_3', field='role', value='x', citations=[{'material_id': 'C', 'block_id': 'p1', 'quote': 'hidden'}])],
        [operation(target='s_3', field='role', value='x'), operation(target='s_2', field='purpose', value='y')],
        [operation(target='s_3', field='role', value='x', dependencies=['edit_2']),
         operation(id='edit_2', target='s_2', field='purpose', value='y', dependencies=['edit_1'])],
    ):
        with pytest.raises(ValueError): prepared(config(), operations)


def test_new_business_step_ids_are_stable_and_dependency_selection_is_explicit():
    value = config(); operations = [operation('add_business', new_handle='n_b_1', name='新业务', trigger='人工', output='记录'),
        operation('add_step', id='edit_2', dependencies=['edit_1'], business='n_b_1', new_handle='n_s_1', index=0,
                  step={'name': '新人工', 'agent': '人工确认', 'role': '核对员', 'output': '记录'})]
    e, _, h, result = prepared(value, operations)
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_2'], SEED, blocks())
    first, rows = e.apply_edits(value, result, h, ['edit_1', 'edit_2'], SEED, blocks())
    second, _ = e.apply_edits(value, result, h, ['edit_1', 'edit_2'], SEED, blocks())
    assert first == second
    created = first['flows']['新业务']['steps'][0]
    assert str(UUID(created['id'])) == created['id']
    assert 'execution' not in created and 'planning_tools' not in created
    assert rows[1]['dependencies'] == ['edit_1'] and rows[1]['blocked_reasons'] == []
    assert first['flows']['原业务'] == value['flows']['原业务']


def test_reference_to_new_handle_requires_declared_creator_dependency():
    ops = [operation('add_business', new_handle='n_b_1', name='新业务'),
           operation('set_business', id='edit_2', target='n_b_1', field='trigger', value='人工')]
    with pytest.raises(ValueError): prepared(config(), ops)


def test_remove_step_does_not_silently_rewire_explicit_routes():
    value = config(); value['flows']['原业务']['steps'][0]['nextId'] = value['flows']['原业务']['steps'][1]['id']
    e, _, h, result = prepared(value, [operation('remove_step', target='s_2')])
    rows = e.review_edits(value, result, h, SEED, blocks())
    assert rows[0]['blocked_reasons']
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())


def test_explicit_route_and_delete_selected_together_preserve_other_bindings():
    value = config(); value['flows']['原业务']['steps'][0]['nextId'] = value['flows']['原业务']['steps'][1]['id']
    ops = [operation(target='s_1', field='nextId', value='s_3'),
           operation('remove_step', id='edit_2', dependencies=['edit_1'], target='s_2')]
    e, _, h, result = prepared(value, ops)
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_2'], SEED, blocks())
    saved, _ = e.apply_edits(value, result, h, ['edit_1', 'edit_2'], SEED, blocks())
    assert [s['name'] for s in saved['flows']['原业务']['steps']] == ['先人工确认', '后人工复核']
    assert saved['flows']['原业务']['steps'][0]['planning_tools'] == value['flows']['原业务']['steps'][0]['planning_tools']


def with_consumer():
    value = config(); steps = value['flows']['原业务']['steps']
    steps[2]['agent'] = '数据管理智能体'; steps[2]['execution'] = {
        'capability_id': 'synthetic-consumer', 'automation_action': 'data', 'arguments': {},
        'bindings': {'value': {'source': 'previous_output', 'step_id': steps[1]['id'], 'field': 'result'}}}
    return value


def test_previous_output_cannot_survive_deleted_or_bypassed_producer():
    value = with_consumer()
    for ops in ([operation('remove_step', target='s_2')],
                [operation(target='s_1', field='nextId', value='s_3')]):
        e, _, h, result = prepared(value, ops)
        assert e.review_edits(value, result, h, SEED, blocks())[0]['blocked_reasons']
        with pytest.raises(ValueError): e.apply_edits(value, result, h, [op['id'] for op in ops], SEED, blocks())


def test_reorder_keeps_ids_but_rechecks_bound_steps_and_previous_output_order():
    value = config(); ops = [operation('reorder_steps', business='b_1', order=['s_2', 's_1', 's_3']),
        operation(id='edit_2', target='s_1', field='nextId', value='auto', dependencies=['edit_1']),
        operation(id='edit_3', target='s_2', field='nextId', value='auto', dependencies=['edit_1'])]
    e, _, h, result = prepared(value, ops); saved, _ = e.apply_edits(value, result, h, ['edit_1', 'edit_2', 'edit_3'], SEED, blocks())
    assert saved['flows']['原业务']['steps'][0]['execution'] == value['flows']['原业务']['steps'][1]['execution']
    assert saved['flows']['原业务']['steps'][0]['bindingReview']['execution_hash']
    value = with_consumer(); e, _, h, result = prepared(value, [operation('reorder_steps', business='b_1', order=['s_3', 's_2', 's_1'])])
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())


def test_auto_neighbour_delete_needs_explicit_route_even_if_stored_auto_is_unchanged():
    value = config(); e, _, h, result = prepared(value, [operation('remove_step', target='s_2')])
    assert e.review_edits(value, result, h, SEED, blocks())[0]['blocked_reasons']
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    ops = [operation('remove_step', target='s_2'),
           operation(id='edit_2', dependencies=['edit_1'], target='s_1', field='nextId', value='auto')]
    e, _, h, result = prepared(value, ops); saved, rows = e.apply_edits(value, result, h, ['edit_1', 'edit_2'], SEED, blocks())
    assert saved['flows']['原业务']['steps'][0]['nextId'] == 'auto'
    assert saved['flows']['原业务']['steps'][0]['bindingReview']['planning_tools_hash']
    assert rows[1]['before'] == '本机计算' and rows[1]['after'] == '后人工复核'


def test_rename_preserves_business_metadata_binding_and_updates_associations():
    value = config(); e, _, h, result = prepared(value, [operation('rename_business', target='b_1', name='新名称')])
    saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    assert saved['businesses'][0] == {'name': '新名称', 'custom': 'keep'}
    assert saved['resources'][0]['businesses'] == ['新名称', '旁支']
    assert saved['gaps'][0]['business'] == '新名称'
    assert saved['flows']['新名称']['steps'] == value['flows']['原业务']['steps']
    assert saved['automation']['policies']['新名称']['flow_review']['status'] == 'needs_review'


def test_remove_business_unlinks_resources_without_changing_retained_flow_authority():
    value = config(); e, _, h, result = prepared(value, [operation('remove_business', target='b_1')])
    saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    assert saved['businesses'] == ['旁支'] and saved['resources'][0]['businesses'] == ['旁支']
    assert saved['flowRecovery']['原业务'] == value['flows']['原业务']
    assert saved['flows']['旁支'] == value['flows']['旁支']
    assert saved['gaps'][0] == {'business': '', 'note': '保留说明'}


def test_basis_change_is_exact_without_reauthorizing_unchanged_execution():
    value = config(); ref = {'material_id': 'A', 'block_id': 'p1', 'quote': '只修改第三步负责人'}
    op = operation('set_basis', target='s_2', citations=[ref], inference_reason='明确依据')
    e, _, h, result = prepared(value, [op]); saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    step = saved['flows']['原业务']['steps'][1]
    assert step['citations'] == [ref] and step['sourceId'] == 'A' and step['execution'] == value['flows']['原业务']['steps'][1]['execution']
    assert 'bindingReview' not in step and saved['flows']['原业务']['steps'][0] == value['flows']['原业务']['steps'][0]


def test_profile_name_is_local_but_goal_rechecks_all_active_flows():
    for field in ('name', 'goal'):
        value = config(); e, _, h, result = prepared(value, [operation('set_profile', field=field, value='新文字')])
        saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
        if field == 'name':
            assert saved['flows'] == value['flows'] and saved['automation'] == value['automation']
        else:
            assert all(f['confirmedSignature'] is None for f in saved['flows'].values())
            assert saved['flows']['原业务']['steps'][1]['bindingReview']['execution_hash']


def test_name_edit_only_clears_confirmation_and_route_review_uses_human_labels():
    value = config(); e, _, h, result = prepared(value, [operation(target='s_2', field='name', value='本机核算')])
    saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    assert 'bindingReview' not in saved['flows']['原业务']['steps'][1]
    assert saved['flows']['原业务']['steps'][1]['execution'] == value['flows']['原业务']['steps'][1]['execution']
    e, _, h, result = prepared(value, [operation(target='s_1', field='nextId', value='s_3')])
    rows = e.review_edits(value, result, h, SEED, blocks())
    assert rows[0]['before'] == '顺序进入下一步' and rows[0]['after'] == '后人工复核'
    assert '33333333' not in json.dumps(rows)


def test_global_goal_binding_review_effects_name_each_affected_step():
    value = config(); e, _, h, result = prepared(value, [operation('set_profile', field='goal', value='新目标')])
    rows = e.review_edits(value, result, h, SEED, blocks())
    effects = ' '.join(rows[0]['effects'])
    assert '先人工确认' in effects and '本机计算' in effects and '旁支人工' not in effects


def test_alias_noop_selected_with_other_change_does_not_materialize_unrelated_flow():
    value = config(); value['businesses'][0]['name'] = '已改名'; value['businessAliases']['已改名'] = '原业务'
    ops = [operation(target='s_3', field='role', value='原复核员'),
           operation('set_profile', id='edit_2', field='name', value='新实例')]
    e, _, h, result = prepared(value, ops); saved, _ = e.apply_edits(value, result, h, ['edit_1', 'edit_2'], SEED, blocks())
    assert saved['flows'] == value['flows']


def test_association_edit_does_not_add_metadata_to_unrelated_resources():
    value = config(); value['resources'].append({'id': 'unrelated', 'custom': 'same'})
    e, _, h, result = prepared(value, [operation('rename_business', target='b_1', name='新业务')])
    saved, _ = e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
    assert saved['resources'][1] == value['resources'][1]


def test_review_rows_are_bounded_human_values_and_safe_string_arrays():
    value = config(); e, _, h, result = prepared(value, [operation(target='s_3', field='role', value='新复核员')])
    rows = e.review_edits(value, result, h, SEED, blocks())
    assert set(rows[0]) == {'id', 'type', 'target_label', 'field_label', 'before', 'after', 'citations', 'inference_reason', 'effects', 'dependencies', 'blocked_reasons'}
    for key in ('effects', 'dependencies', 'blocked_reasons'): assert all(isinstance(x, str) for x in rows[0][key])
    assert 'synthetic-local-tool' not in json.dumps(rows)


def test_review_labels_remain_frozen_if_unselected_rename_was_proposed_first():
    value = config(); ops = [operation('rename_business', target='b_1', name='新业务名'),
        operation(id='edit_2', target='s_3', field='role', value='新复核员')]
    e, _, h, result = prepared(value, ops); rows = e.review_edits(value, result, h, SEED, blocks())
    assert rows[1]['target_label'] == '原业务 / 后人工复核'
    selected, _ = e.apply_edits(value, result, h, ['edit_2'], SEED, blocks())
    assert selected['businesses'] == value['businesses']


def test_route_label_does_not_require_an_unselected_step_rename():
    value = config(); ops = [operation(target='s_3', field='name', value='新步骤名'),
        operation(id='edit_2', target='s_1', field='nextId', value='s_3')]
    e, _, h, result = prepared(value, ops)
    assert e.review_edits(value, result, h, SEED, blocks())[1]['after'] == '后人工复核'


def test_invalid_route_in_one_business_does_not_block_unrelated_selected_role():
    value = config(); value['flows']['原业务']['steps'][0]['nextId'] = value['flows']['原业务']['steps'][1]['id']
    ops = [operation('remove_step', target='s_2'), operation(id='edit_2', target='s_4', field='role', value='新旁支员')]
    e, _, h, result = prepared(value, ops); rows = e.review_edits(value, result, h, SEED, blocks())
    assert rows[0]['blocked_reasons'] and not rows[1]['blocked_reasons']
    saved, _ = e.apply_edits(value, result, h, ['edit_2'], SEED, blocks())
    assert saved['flows']['原业务'] == value['flows']['原业务']
    assert saved['flows']['旁支']['steps'][0]['role'] == '新旁支员'


def test_preexisting_bad_route_cannot_mask_another_new_route_error():
    value = config(); value['flows']['原业务']['steps'][0]['nextId'] = 'already-missing'
    e, _, h, result = prepared(value, [operation(target='s_2', field='nextId', value='s_2')])
    assert e.review_edits(value, result, h, SEED, blocks())[0]['blocked_reasons']
    with pytest.raises(ValueError): e.apply_edits(value, result, h, ['edit_1'], SEED, blocks())
