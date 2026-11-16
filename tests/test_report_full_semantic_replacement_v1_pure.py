"""Inline synthetic replacement tests; no source/auth/ledger/API proof."""
import copy
from uuid import uuid4

import pytest

from generic_agents import report_full_semantic_configuration_v1 as semantic
from generic_agents import report_full_semantic_replacement_v1 as replacement
from tests.test_report_full_semantic_configuration_v1_pure import result, context


def design(names, *, handoff_id=None):
    value = result()
    template = copy.deepcopy(value['businesses'][0])
    value['businesses'] = [{**copy.deepcopy(template), 'name': name} for name in names]
    metadata = context(); metadata['handoff_id'] = handoff_id or str(uuid4())
    return semantic.build_design(value, **metadata)


def scene():
    previous = design(['旧业务甲', '同名业务'])
    other = design(['另一完整设计'])
    flows = {'外部前': {'trigger': '外部启动', 'output': '外部产出', 'steps': [], 'custom': ['原值🙂']}}
    flows.update(semantic.derive_flows(previous))
    flows.update(semantic.derive_flows(other))
    flows['外部后'] = {'trigger': '另一原触发', 'output': '', 'steps': []}
    for name in semantic.derive_flows(previous):
        target = flows[name]
        target['confirmedAt'] = '旧确认时间'
        target['confirmedSignature'] = '旧确认签名'
        target['steps'][0].update(execution={'capability_id': '旧能力'}, planning_tools=[{'old': True}])
    config = {'version': 1, 'businesses': [{'name': '外部前', 'untouched': ' 外部字节🙂 '}, '旧业务甲',
        '另一完整设计', '同名业务', '外部后'], 'flows': flows, 'fullSemanticDesigns': [other, previous],
        'resources': [{'id': '合成资源', 'businesses': ['外部前', '旧业务甲', '同名业务', '新业务', '同名业务'],
            'config': {'unchanged': ' 原资源末字🙂\n'}}, {'id': '无关联字段'}],
        'setupNotes': {'旧业务甲': '旧移除业务说明', '同名业务': ' 同名原说明🙂 ', '新业务': '新名孤儿说明', '外部前': '外部说明'},
        'gaps': [{'business': '旧业务甲', 'text': '旧缺口'}, {'business': '同名业务', 'text': '同名原缺口'},
            {'business': '新业务', 'text': '新名孤儿缺口'}, {'business': '外部前', 'text': '外部缺口'}, {'text': '全局原缺口'}],
        'businessAliases': {'旧业务甲': '旧别名', '原别名': '同名业务', '新业务': '另旧名', '孤儿别名': '新业务', '外部别名': '外部前'},
        'automation': {'version': 1, 'policies': {name: {'rules': {'data': {'mode': '自动运行'}}, 'history': [name]}
            for name in ['旧业务甲', '同名业务', '新业务', '外部前', '另一完整设计']}},
        'instanceProfile': {'name': '合成实例', 'domain': ' 原领域 '}, 'connections': [],
        'materials': [{'opaque': '原资料引用不变'}], 'unknown_user_field': {'tail': ' 保留字节🙂\n'}}
    return config, previous, other


def test_same_name_replacement_keeps_user_associations_but_never_old_execution_or_confirmation():
    config, previous, other = scene(); new = design(['旧业务甲', '同名业务'])
    before = copy.deepcopy((config, previous, new))
    value = replacement.apply_replacement(config, previous, new)
    assert value['fullSemanticDesigns'] == [other, new]
    assert value['businesses'] == [config['businesses'][0], '旧业务甲', '同名业务', '另一完整设计', '外部后']
    assert value['resources'] == config['resources'] and value['setupNotes'] == config['setupNotes'] and value['gaps'] == config['gaps']
    assert value['businessAliases'] == {'新业务': '另旧名', '孤儿别名': '新业务', '外部别名': '外部前'}
    assert set(value['automation']['policies']) == {'新业务', '外部前', '另一完整设计'}
    fresh = semantic.derive_flows(new)
    for name in ['旧业务甲', '同名业务']:
        assert value['flows'][name] == fresh[name]
        assert value['flows'][name]['confirmedAt'] is value['flows'][name]['confirmedSignature'] is None
        assert not {'execution', 'planning_tools'} & set(value['flows'][name]['steps'][0])
    assert value['flows']['另一完整设计'] == config['flows']['另一完整设计']
    assert (config, previous, new) == before


def test_rename_remove_new_orphans_cleanup_is_exact_and_unrelated_values_keep_order():
    config, previous, other = scene(); new = design(['同名业务', '新业务'])
    before = copy.deepcopy(config)
    value = replacement.apply_replacement(config, previous, new)
    assert value['businesses'] == [config['businesses'][0], '同名业务', '新业务', '另一完整设计', '外部后']
    assert value['resources'][0]['businesses'] == ['外部前', '同名业务', '同名业务']  # No deduplication.
    assert value['resources'][1] == config['resources'][1]
    assert value['setupNotes'] == {'同名业务': config['setupNotes']['同名业务'], '外部前': '外部说明'}
    assert value['gaps'] == [config['gaps'][1], config['gaps'][3], config['gaps'][4]]
    assert value['businessAliases'] == {'外部别名': '外部前'}
    assert set(value['automation']['policies']) == {'外部前', '另一完整设计'}
    assert '旧业务甲' not in value['flows']
    for key in ['instanceProfile', 'connections', 'materials', 'unknown_user_field']:
        assert value[key] == config[key]
    assert [name for name in value['flows'] if name not in {'同名业务', '新业务'}] == ['外部前', '另一完整设计', '外部后']
    assert value['flows']['另一完整设计'] == config['flows']['另一完整设计']
    assert config == before and value['fullSemanticDesigns'] == [other, new]


def test_remove_whole_business_group_keeps_complete_global_design_and_no_empty_flow_fill():
    config, previous, other = scene(); new = design([])
    value = replacement.apply_replacement(config, previous, new)
    assert value['businesses'] == [config['businesses'][0], '另一完整设计', '外部后']
    assert set(value['flows']) == {'外部前', '另一完整设计', '外部后'}
    assert value['fullSemanticDesigns'] == [other, new]
    assert value['fullSemanticDesigns'][1]['result']['global_claims'] == new['result']['global_claims']
    assert value['setupNotes'] == {'新业务': '新名孤儿说明', '外部前': '外部说明'}


def test_zero_null_and_explicit_cycle_remain_raw_and_derived_without_rewiring():
    config, previous, _ = scene(); new = design(['同名业务', '新业务'])
    raw_new = copy.deepcopy(new['result']); raw_new['businesses'][1]['steps'][0]['next_index'] = None
    new = semantic.build_design(raw_new, **{key: new[key] for key in semantic.CONTEXT_KEYS})
    raw = copy.deepcopy(new['result'])
    value = replacement.apply_replacement(config, previous, new)
    assert new['result'] == raw == value['fullSemanticDesigns'][1]['result']
    for bi, binding in enumerate(new['business_bindings']):
        steps = value['flows'][binding['name']]['steps']
        assert steps[0]['nextId'] == ('end' if bi == 1 else binding['step_ids'][1])
        assert steps[1]['nextId'] == binding['step_ids'][0]
        assert all(step['elseId'] == 'end' for step in steps)


def test_summary_is_closed_ordered_and_only_reports_structural_family_changes():
    previous = design(['旧业务甲', '同名业务']); new = design(['同名业务', '新业务'])
    value = replacement.replacement_changes(previous, new)
    assert set(value) == replacement.SUMMARY_KEYS
    assert value == {'added_businesses': ['新业务'], 'removed_businesses': ['旧业务甲'],
        'retained_businesses': ['同名业务'], 'changed_sections': ['businesses']}
    same = design(['旧业务甲', '同名业务'])
    assert replacement.replacement_changes(previous, same)['changed_sections'] == []
    assert 'rename' not in value


@pytest.mark.parametrize('mutate', [
    lambda c: c.update(fullSemanticDesigns=[]),
    lambda c: c['fullSemanticDesigns'].append(copy.deepcopy(c['fullSemanticDesigns'][1])),
    lambda c: c.update(flows={}),
    lambda c: c.update(businesses=[]),
    lambda c: c['flows']['旧业务甲'].pop('fullSemanticSource'),
    lambda c: c['flows']['旧业务甲']['steps'][0].update(nextId='end'),
])
def test_missing_or_tampered_previous_configuration_rejects_whole_without_mutating(mutate):
    config, previous, _ = scene(); mutate(config); before = copy.deepcopy(config)
    with pytest.raises(ValueError): replacement.apply_replacement(config, previous, design(['新业务']))
    assert config == before


@pytest.mark.parametrize('name', ['外部前', '另一完整设计', '外部后', '保留但不在业务清单的流程'])
def test_conflicting_names_outside_the_target_group_never_overwrite(name):
    config, previous, _ = scene()
    if name == '保留但不在业务清单的流程': config['flows'][name] = {'steps': []}
    before = copy.deepcopy(config)
    with pytest.raises(ValueError, match='conflict'):
        replacement.apply_replacement(config, previous, design([name]))
    assert config == before


@pytest.mark.parametrize('key,bad', [('resources', {}), ('resources', [None]),
    ('resources', [{'businesses': None}]), ('resources', [{'businesses': [1]}]),
    ('setupNotes', []), ('setupNotes', {'旧业务甲': None}), ('gaps', {}), ('gaps', [None]),
    ('gaps', [{'business': None}]), ('businessAliases', []), ('businessAliases', {'旧业务甲': 1}),
    ('automation', None), ('automation', {'policies': []}), ('automation', {'policies': {'旧业务甲': None}})])
def test_malformed_cleanup_fields_fail_whole_instead_of_being_coerced(key, bad):
    config, previous, _ = scene(); config[key] = bad; before = copy.deepcopy(config)
    with pytest.raises(ValueError): replacement.apply_replacement(config, previous, design(['新业务']))
    assert config == before


def test_same_id_stale_design_and_another_live_design_id_are_rejected():
    config, previous, other = scene()
    for new in [previous, design(['新业务'], handoff_id=previous['handoff_id']),
            design(['新业务'], handoff_id=other['handoff_id'])]:
        with pytest.raises(ValueError): replacement.apply_replacement(config, previous, new)
    stale = design(['旧业务甲', '同名业务'], handoff_id=previous['handoff_id'])
    changed_result = copy.deepcopy(stale['result']); changed_result['domain'] = '后来领域'
    metadata = {key: stale[key] for key in semantic.CONTEXT_KEYS}
    stale = semantic.build_design(changed_result, **metadata)
    with pytest.raises(ValueError, match='previous_changed'):
        replacement.apply_replacement(config, stale, design(['新业务']))


def test_absent_optional_fields_stay_absent_and_empty_old_group_appends_only_new_names():
    previous, new = design([]), design(['新业务'])
    config = {'businesses': [{'name': '外部前', 'original': '原值'}], 'flows': {'外部前': {'steps': []}},
        'fullSemanticDesigns': [previous]}
    before = copy.deepcopy(config)
    value = replacement.apply_replacement(config, previous, new)
    assert value['businesses'] == [config['businesses'][0], '新业务']
    assert not {'resources', 'setupNotes', 'gaps', 'businessAliases', 'automation'} & set(value)
    assert config == before


def test_aggregate_business_cap_rejects_whole_replacement_without_clipping():
    config, previous, _ = scene()
    at_limit = design(['容量业务' + str(index) for index in range(17)])
    assert len(replacement.apply_replacement(config, previous, at_limit)['businesses']) == 20
    too_many = design(['容量业务' + str(index) for index in range(18)])
    before = copy.deepcopy(config)
    with pytest.raises(ValueError, match='capacity'):
        replacement.apply_replacement(config, previous, too_many)
    assert config == before
