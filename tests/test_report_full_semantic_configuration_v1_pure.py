"""Pure synthetic designs: structure preservation, never source/permission proof."""
import copy
import json
from uuid import UUID, uuid4, uuid5

import pytest

from generic_agents.contracts import digest
from generic_agents import report_full_semantic_configuration_v1 as design


def context():
    return {'handoff_id': '77f430ca-cafd-4b34-8d65-6670c8bfdcd2',
        'report_id': '7aab7ce2-b0b0-479f-85ed-e3e409d5709a', 'report_revision': 8,
        'review_receipt_hash': 'c' * 64, 'manual_value_hash': 'd' * 64,
        'model_result_hash': 'e' * 64, 'source_hash': 'a' * 64,
        'catalog_hash': 'b' * 64, 'context_hash': 'f' * 64}


def result():
    cite = [{'span_ref': 'w:p1/s1'}]
    unknown = {'status': 'unknown', 'citations': [], 'explanation': ' 原规则仍待核对 \n'}
    step = lambda name, target: {'name': name, 'purpose': ' 原目的 ', 'agent': '人工确认',
        'role': '核对职责', 'output': ' 原产出🙂 ', 'next_index': target,
        'branch_condition': '', 'else_index': None, 'citations': copy.deepcopy(cite), 'inference_reason': ' 原依据 '}
    business = {'operation': 'upsert', 'original_name': '', 'name': '合成业务甲',
        'trigger': ' 原触发 ', 'output': ' 原业务产出 ', 'steps': [step('甲步骤', 1), step('乙步骤', 0)],
        'citations': copy.deepcopy(cite), 'inference_reason': ' 原业务说明 ',
        'participants': [{'agent': name, 'responsibility': ' 原职责 ', 'evidence': copy.deepcopy(unknown)}
            for name in ['参与甲', '参与乙']],
        'interactions': [{'from_participant_index': 1, 'to_participant_index': 0,
            'purpose': ' 原交接方向 ', 'condition': ' 原交接条件 ', 'step_indices': [1, 0], 'evidence': copy.deepcopy(unknown)}],
        'conditions': [{'kind': 'precondition', 'statement': ' 原前提 ', 'step_indices': [0], 'evidence': copy.deepcopy(unknown)},
            {'kind': 'stop', 'statement': ' 原停止条件 ', 'step_indices': [1], 'evidence': copy.deepcopy(unknown)}],
        'coordination': [{'kind': 'parallel', 'step_indices': [1, 0], 'start_rule': ' 原开始 ',
            'wait_rule': ' 原等待 ', 'join_rule': ' 原汇合 ', 'evidence': copy.deepcopy(unknown)}]}
    another = copy.deepcopy(business); another['name'] = '合成业务乙'
    another['steps'][0]['next_index'] = None
    return {'schema': 'report-evidence-candidate-wire-v5', 'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
        'domain': ' 原领域末字🙂\n', 'global_claims': [{'kind': '治理', 'statement': ' 原独立约束 ',
            'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''},
            {'kind': '冲突', 'statement': ' 相互矛盾的另一声明仍保留 ', 'span_refs': [], 'status': 'unknown', 'explanation': '未知内容待核'}],
        'objects': [{'name': '合成材料', 'fields': [{'label': '名称', 'kind': '文本', 'required': True, 'unit': None,
            'citations': copy.deepcopy(cite), 'inference_reason': '原定义'}], 'citations': copy.deepcopy(cite), 'inference_reason': '原定义'}],
        'relationships': [], 'businesses': [business, another], 'questions': [' 原独立问题末字🙂\n']}


def build(value=None, metadata=None):
    return design.build_design(result() if value is None else value, **(context() if metadata is None else metadata))


def config(value=None):
    value = build() if value is None else value
    return {'fullSemanticDesigns': [value], 'flows': design.derive_flows(value)}


def reseal(value):
    value['design_hash'] = digest({key: item for key, item in value.items() if key != 'design_hash'})


def test_whole_result_is_lossless_deterministic_and_excludes_private_note():
    original, metadata = result(), context()
    before = copy.deepcopy((original, metadata))
    value = build(original, metadata)
    assert set(value) == design.DESIGN_KEYS and len(value) == 14
    assert value['result'] == original and value['result'] is not original
    assert value['execution_policy'] == 'requires_full_semantic_runtime_v1'
    assert value['manual_value_hash'] == metadata['manual_value_hash']  # Opaque caller-verified whole manual hash.
    assert design.check_design(value, expected_result=original, expected_design_hash=value['design_hash'], **metadata) == value
    assert build(original, metadata) == value and (original, metadata) == before
    assert 'note' not in json.dumps(value, ensure_ascii=False)
    invalid = copy.deepcopy(original); invalid['note'] = '私人说明不允许进入共享设计'
    with pytest.raises(ValueError): build(invalid)
    invalid = copy.deepcopy(value); invalid['note'] = '私人说明不允许进入共享设计'
    with pytest.raises(ValueError): design.check_design(invalid)
    value['result']['questions'].append('只改返回副本')
    assert original == before[0]


def test_all_binding_kinds_and_businesses_have_separate_exact_uuid5_domains():
    value = build(); ids = []
    for bi, binding in enumerate(value['business_bindings']):
        assert set(binding) == {'candidate_index', 'name', 'step_ids', 'participant_ids', 'interaction_ids', 'condition_ids', 'coordination_ids'}
        assert binding['candidate_index'] == bi and binding['name'] == value['result']['businesses'][bi]['name']
        for kind in ('step', 'participant', 'interaction', 'condition', 'coordination'):
            expected = [str(uuid5(UUID(value['handoff_id']), f'{design.SCHEMA}/{kind}/{bi}/{index}'))
                for index in range(len(binding[kind + '_ids']))]
            assert binding[kind + '_ids'] == expected
            ids.extend(expected)
    assert len(ids) == len(set(ids))
    changed = context(); changed['handoff_id'] = str(uuid4())
    assert build(metadata=changed)['business_bindings'] != value['business_bindings']


def test_derived_flows_keep_zero_null_cycles_and_branch_meaning_without_source_laundering():
    original = result(); original['businesses'][0]['steps'][0].update(branch_condition=' 原完成后分支 ', else_index=1)
    value = build(original); before = copy.deepcopy(value); flows = design.derive_flows(value)
    first = flows['合成业务甲']; ids = value['business_bindings'][0]['step_ids']
    assert set(first) == {'trigger', 'output', 'steps', 'confirmedAt', 'confirmedSignature', 'fullSemanticSource'}
    assert first['confirmedAt'] is first['confirmedSignature'] is None
    assert first['steps'][0]['nextId'] == first['steps'][0]['elseId'] == ids[1]
    assert first['steps'][1]['nextId'] == ids[0] and first['steps'][1]['elseId'] == 'end'
    assert flows['合成业务乙']['steps'][0]['nextId'] == 'end'
    assert first['steps'][0]['condition'] == original['businesses'][0]['steps'][0]['branch_condition']
    for step in first['steps']:
        assert set(step) == {'id', 'name', 'purpose', 'agent', 'role', 'output', 'condition', 'nextId', 'elseId'}
        assert not ({'sourceId', 'citation', 'citations', 'inferenceReason'} & set(step))
    assert first['fullSemanticSource'] == {'schema': design.FLOW_REF, 'handoff_id': value['handoff_id'],
        'design_hash': value['design_hash'], 'candidate_index': 0, 'business_hash': digest(original['businesses'][0])}
    assert value == before and value['result'] == original


@pytest.mark.parametrize('change', [
    lambda v: v.update(execution_policy='runnable'),
    lambda v: v['business_bindings'][0]['step_ids'].reverse(),
    lambda v: v['business_bindings'][0].update(candidate_index=1),
    lambda v: v['business_bindings'][0].update(extra='unclosed'),
    lambda v: v.update(schema='report-configuration-handoff-v1'),
])
def test_resealed_metadata_tamper_never_becomes_a_valid_design(change):
    value = build(); change(value); reseal(value)
    with pytest.raises(ValueError): design.check_design(value)


def test_external_expected_result_and_hash_reject_a_self_resealed_changed_candidate():
    original = build(); changed_result = copy.deepcopy(original['result']); changed_result['global_claims'][0]['statement'] += '后来改值'
    changed = build(changed_result)
    assert design.check_design(changed) == changed  # Self-integrity alone is explicitly not authorization.
    with pytest.raises(ValueError): design.check_design(changed, expected_result=original['result'])
    with pytest.raises(ValueError): design.check_design(changed, expected_design_hash=original['design_hash'])
    metadata = context(); metadata['review_receipt_hash'] = '0' * 64
    with pytest.raises(ValueError): design.check_design(original, **metadata)


def test_mixed_new_and_modify_delete_intents_reject_the_whole_handoff():
    for operation, original_name in [('update', ''), ('delete', ''), ('remove', ''), ('upsert', '已有业务')]:
        value = result()
        value['businesses'][1].update(operation=operation, original_name=original_name)
        before = copy.deepcopy(value)
        with pytest.raises(ValueError, match='full_semantic_append_only'):
            build(value)
        assert value == before


@pytest.mark.parametrize('mutate', [
    lambda v: v['businesses'].append(copy.deepcopy(v['businesses'][0])),
    lambda v: v['businesses'][0].update(operation='delete'),
    lambda v: v['businesses'][0].update(original_name='已有业务'),
    lambda v: v['businesses'][0].update(name=' 带空白业务 '),
    lambda v: v['businesses'][0]['steps'][0].update(next_index=0),
    lambda v: v['businesses'][0]['steps'][0].update(next_index=2),
    lambda v: v['businesses'][0]['steps'][0].update(next_index=False),
    lambda v: v['businesses'][0]['steps'][0].update(else_index=1),
    lambda v: v['businesses'][0]['steps'][0].update(role=' '),
    lambda v: v['businesses'][0]['interactions'][0].update(to_participant_index=1),
    lambda v: v['businesses'][0]['conditions'][0].update(step_indices=[0, 0]),
    lambda v: v['businesses'][0]['conditions'][0].update(step_indices=[2]),
    lambda v: v['businesses'][0]['coordination'][0].update(step_indices=[0]),
    lambda v: v.update(questions=[]),
    lambda v: v.update(domain='不能保存\u0000'),
    lambda v: v.update(domain='不能保存\ud800'),
    lambda v: v.update(extra='not closed'),
])
def test_bad_local_structure_is_rejected_without_mutation(mutate):
    value = result(); mutate(value); before = copy.deepcopy(value)
    with pytest.raises(ValueError): build(value)
    assert value == before


def test_empty_objects_and_partial_business_remain_preserved_but_never_runnable():
    value = result(); value['objects'] = []
    value['businesses'] = [{**value['businesses'][0], 'trigger': '', 'output': '', 'steps': [],
        'participants': [], 'interactions': [], 'conditions': [], 'coordination': [], 'inference_reason': '待核流程'}]
    checked = build(value)
    assert checked['result'] == value and design.derive_flows(checked)['合成业务甲']['steps'] == []
    assert design.execution_issues(checked, 0) == [design.BLOCKED_ISSUE]
    for invalid in [False, -1, 1, '0', None]:
        with pytest.raises(ValueError): design.execution_issues(checked, invalid)


def test_nonfinite_json_and_foreign_target_types_never_coerce_or_repair():
    for number in [float('nan'), float('inf'), -float('inf')]:
        value = result(); value['domain'] = number
        with pytest.raises(ValueError): build(value)
        assert value['domain'] is number
    for indices in [[False], ['0'], [None], [-1], [2], [0, 0]]:
        value = result(); value['businesses'][0]['conditions'][0]['step_indices'] = indices
        with pytest.raises(ValueError): build(value)
        assert value['businesses'][0]['conditions'][0]['step_indices'] == indices


@pytest.mark.parametrize('key,bad', [('handoff_id', 'not-a-uuid'), ('report_id', None),
    ('report_revision', True), ('report_revision', 1), ('source_hash', 'f' * 64),
    ('catalog_hash', 'f' * 64), ('context_hash', 'A' * 64), ('manual_value_hash', None)])
def test_required_external_context_is_strict_and_not_inferred(key, bad):
    metadata = context(); metadata[key] = bad
    with pytest.raises(ValueError): build(metadata=metadata)
    with pytest.raises(ValueError): design.check_design(build(), source_hash='a' * 64)


def test_signature_preserves_whole_design_dependency_and_allows_only_unchanged_core_projection():
    value = build(); configuration = config(value); flow = configuration['flows']['合成业务甲']
    expected = {'schema': design.SIGNATURE, 'handoff_id': value['handoff_id'], 'design_hash': value['design_hash'],
        'candidate_index': 0, 'business_hash': digest(value['result']['businesses'][0])}
    assert design.signature_context(configuration, flow) == expected
    flow['confirmedAt'] = 'later explicit confirmation'; flow['confirmedSignature'] = 'outer server handles this'
    flow['steps'][0]['execution'] = {'capability_id': 'caller must authenticate this separately'}
    assert design.signature_context(configuration, flow) == expected
    changed_result = result(); changed_result['global_claims'][0]['statement'] += '修改全局约束'
    changed = build(changed_result); changed_config = config(changed)
    assert design.signature_context(changed_config, changed_config['flows']['合成业务甲'])['design_hash'] != expected['design_hash']
    for key, bad in [('name', '改步骤'), ('condition', '改分支'), ('nextId', 'end')]:
        invalid = copy.deepcopy(configuration); invalid['flows']['合成业务甲']['steps'][0][key] = bad
        with pytest.raises(ValueError): design.signature_context(invalid, invalid['flows']['合成业务甲'])


def test_present_markers_cannot_downgrade_or_select_another_business():
    value = build(); configuration = config(value)
    assert design.signature_context({}, {'trigger': '旧流程'}) is None
    assert design.signature_context(configuration, {'trigger': '不相关旧流程'}) is None
    for mode in ['absent-designs', 'bad-designs', 'missing-marker', 'null-marker', 'wrong-index', 'wrong-hash', 'unknown-field', 'missing-flow', 'renamed-flow']:
        invalid = copy.deepcopy(configuration); flow = invalid['flows']['合成业务甲']
        if mode == 'absent-designs': invalid.pop('fullSemanticDesigns')
        elif mode == 'bad-designs': invalid['fullSemanticDesigns'] = None
        elif mode == 'missing-marker': flow.pop('fullSemanticSource')
        elif mode == 'null-marker': flow['fullSemanticSource'] = None
        elif mode == 'wrong-index': flow['fullSemanticSource']['candidate_index'] = 1
        elif mode == 'wrong-hash': flow['fullSemanticSource']['business_hash'] = '0' * 64
        elif mode == 'unknown-field': flow['fullSemanticSource']['runnable'] = True
        elif mode == 'missing-flow': invalid['flows']['合成业务甲'] = None
        else:
            invalid['flows']['后来改名'] = invalid['flows'].pop('合成业务甲')
            invalid['flows']['后来改名'].pop('fullSemanticSource')
        with pytest.raises(ValueError): design.signature_context(invalid, flow)
    invalid = copy.deepcopy(configuration); invalid['fullSemanticDesigns'].append(copy.deepcopy(value))
    with pytest.raises(ValueError): design.signature_context(invalid, invalid['flows']['合成业务甲'])


def test_oversize_result_is_rejected_whole_without_trimming():
    value = result(); prototype = value['businesses'][0]
    value['businesses'] = []
    for index in range(20):
        business = copy.deepcopy(prototype); business['name'] = '容量业务' + str(index)
        business['steps'] = [copy.deepcopy(prototype['steps'][si % 2]) for si in range(16)]
        for si, step in enumerate(business['steps']):
            step.update(name='容量步骤' + str(si), purpose='x' * 1000, output='x' * 1000,
                inference_reason='x' * 500, next_index=si - 1 if si % 2 else si + 1)
        value['businesses'].append(business)
    before = copy.deepcopy(value)
    assert len(json.dumps(value, ensure_ascii=False).encode()) > design.MAX_RESULT_BYTES
    with pytest.raises(ValueError, match='capacity'): build(value)
    assert value == before
