"""Pure local editing policy, not authorization or machine-quality evidence.

These small synthetic structures prove only the two allowed editing operations.
Root's service must separately preserve the paid raw answer, verify source
ownership, save the derived review record, and call the full original validator.
"""
import copy

import pytest

from generic_agents.contracts import digest
from generic_agents import local_proposal_review_policy_v261 as policy


def citation(quote='未匹配的旧引用'):
    return {'material_id': '资料甲', 'block_id': '段落一', 'quote': quote}


def proposal():
    return {'domain': '工程对照', 'businesses': [
        {'name': '原业务甲', 'citations': [citation()], 'steps': [
            {'name': '原步骤', 'agent': '未知智能体', 'role': '原人工职责',
                'capability_id': '不得新增权限', 'citations': [citation()]}]},
        {'name': '原业务乙', 'citations': [citation()], 'steps': []}],
        'objects': [{'name': '对象甲', 'citations': [citation()], 'fields': [
            {'name': '字段甲', 'type': 'text', 'citations': [citation()]}]},
            {'name': '对象乙', 'citations': [citation()], 'fields': []}],
        'relationships': [{'name': '关系甲', 'source_object': '对象甲',
            'target_object': '对象乙', 'citations': [citation()]}],
        'questions': ['原模型遗留问题']}


def blocks():
    return [{'material_id': '资料甲', 'block_id': '段落一',
        'text': '  林区🌲的原音仅作声类候选；先由值班人员核查。\n不得据此自动控制设备。  ',
        'location': {'page': 2, 'line': 4}}]


def selected(value, path):
    for token in path[1:].split('/'):
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def operation(value, path, *, replacement=None, reason='人工明确核对原文'):
    result = {'op': 'discard_item' if replacement is None else 'replace_citation',
        'path': path, 'expected_hash': digest(selected(value, path)), 'reason': reason}
    if replacement is not None:
        result['replacement'] = replacement
    return result


def apply(value, operations, source=None, *, version=5):
    return policy.apply_local_review(value, operations, blocks() if source is None else source,
        contract_version=version)


@pytest.mark.parametrize('quote', [' ', '\t', '\n', ' \n\t '])
def test_whitespace_only_source_substrings_cannot_become_evidence(quote):
    value=proposal();before=copy.deepcopy(value)
    source=blocks();source[0]['text']+=' \n\t '
    op=operation(value,'/businesses/0/citations/0',replacement=citation(quote))
    with pytest.raises(policy.LocalReviewInvalid):apply(value,[op],source)
    assert value==before


@pytest.mark.parametrize('quote', ['  林区🌲的原音', '\n不得据此自动控制设备。  '])
def test_content_with_original_boundary_whitespace_is_preserved_byte_for_byte(quote):
    value=proposal();op=operation(value,'/businesses/0/citations/0',replacement=citation(quote))
    corrected,_=apply(value,[op])
    assert corrected['businesses'][0]['citations'][0]['quote']==quote


@pytest.mark.parametrize('path', ['/businesses/0/citations/0',
    '/businesses/0/steps/0/citations/0', '/objects/0/citations/0',
    '/objects/0/fields/0/citations/0', '/relationships/0/citations/0'])
def test_exact_existing_citation_paths_preserve_original_and_audit_complete_source_block(path):
    original = proposal(); before = copy.deepcopy(original); source = blocks()
    replacement = citation('林区🌲的原音仅作声类候选')
    op = operation(original, path, replacement=replacement)
    corrected, trace = apply(original, [op], source)
    expected = copy.deepcopy(original)
    tokens = path[1:].split('/'); parent = expected
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    parent[int(tokens[-1])] = replacement
    assert corrected == expected and original == before
    assert trace == [{'policy': policy.POLICY, 'op': 'replace_citation', 'path': path,
        'original_hash': digest(selected(original, path)), 'replacement_hash': digest(replacement),
        'source_block_hash': digest(source[0]), 'reason': '人工明确核对原文'}]
    corrected['businesses'][0]['steps'][0]['role'] = '后续人稿不能回写原件'
    assert original == before
    assert source == blocks()


def test_all_pointers_use_original_indices_then_citations_replace_before_descending_discard():
    original = proposal()
    original['objects'].append({'name': '对象丙', 'citations': [citation()], 'fields': []})
    before = copy.deepcopy(original)
    replacement = citation('先由值班人员核查')
    operations = [operation(original, '/objects/0'),
        operation(original, '/objects/2/citations/0', replacement=replacement),
        operation(original, '/objects/1'), operation(original, '/businesses/1')]
    corrected, trace = apply(original, operations)
    assert [item['name'] for item in corrected['objects']] == ['对象丙']
    assert corrected['objects'][0]['citations'] == [replacement]
    assert [item['name'] for item in corrected['businesses']] == ['原业务甲']
    assert corrected['relationships'] == original['relationships']  # Never delete/reconnect by inference.
    assert corrected['businesses'][0]['steps'] == original['businesses'][0]['steps']
    assert [item['path'] for item in trace] == [item['path'] for item in operations]
    assert all(item['replacement_hash'] is None and item['source_block_hash'] is None
        for item in trace if item['op'] == 'discard_item')
    assert original == before


@pytest.mark.parametrize('version', [4, 5])
def test_discard_is_an_explicit_top_level_edit_and_preserves_unrelated_unknown_values(version):
    original = proposal(); before = copy.deepcopy(original)
    operations = [operation(original, '/businesses/1'), operation(original, '/objects/1')]
    corrected, trace = apply(original, operations, version=version)
    assert len(corrected['businesses']) == len(corrected['objects']) == 1
    assert corrected['businesses'][0]['steps'][0]['agent'] == '未知智能体'
    assert corrected['businesses'][0]['steps'][0]['capability_id'] == '不得新增权限'
    assert corrected['relationships'] == before['relationships']
    assert original == before and len(trace) == 2


@pytest.mark.parametrize('quote', ['  林区🌲', '核查。\n不得据此自动控制设备', '设备。  '])
def test_unicode_spaces_and_newlines_are_exact_contiguous_source_not_stripped(quote):
    original = proposal(); replacement = citation(quote)
    corrected, _ = apply(original, [operation(original, '/objects/0/citations/0', replacement=replacement)])
    assert corrected['objects'][0]['citations'][0]['quote'] == quote


@pytest.mark.parametrize('quote', ['林区的原音', '核查。不得据此自动控制设备',
    '林区🌲的原音……先由值班人员核查', '', '甲' * 251])
def test_missing_fuzzy_cross_line_or_overlong_quote_is_rejected_without_partial_changes(quote):
    original = proposal(); before = copy.deepcopy(original)
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [operation(original, '/objects/0/citations/0', replacement=citation(quote))])
    assert original == before


@pytest.mark.parametrize('bad_source', ['wrong_material', 'wrong_block', 'duplicate', 'wrong_text_type'])
def test_replacement_cannot_adopt_wrong_or_ambiguous_source(bad_source):
    original = proposal(); source = blocks()
    if bad_source == 'wrong_material': source[0]['material_id'] = '其他资料'
    elif bad_source == 'wrong_block': source[0]['block_id'] = '其他段落'
    elif bad_source == 'duplicate': source.append(copy.deepcopy(source[0]))
    else: source[0]['text'] = ['林区🌲的原音仅作声类候选']
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [operation(original, '/objects/0/citations/0',
            replacement=citation('林区🌲的原音仅作声类候选'))], source)


@pytest.mark.parametrize('path', ['/businesses/0/steps/0', '/objects/0/fields/0',
    '/businesses/0/name', '/businesses/0/steps/0/agent', '/businesses/0/steps/0/role',
    '/businesses/0/steps/0/capability_id', '/relationships/0/source_object',
    '/permissions/0', '/objects/-1', '/objects/00', '/objects/01', '/objects/True',
    '/objects/false', '/objects/0.0', '/objects/٠', '/objects/999999999999999999999999',
    '/objects/2', '/objects/0/citations/-1', '/objects/0/citations/01',
    '/objects/0/citations/true', '/objects/0/citations/1', '/objects/0/citations/0/quote',
    '/objects/0/~1citations/0', '/objects/0/citations/0/'])
def test_only_closed_canonical_paths_can_change_and_never_permission_fields(path):
    original = proposal(); op = {'op': 'replace_citation' if 'citations' in path else 'discard_item',
        'path': path, 'expected_hash': 'a' * 64, 'reason': '必须拒绝越界修改'}
    if op['op'] == 'replace_citation': op['replacement'] = citation('先由值班人员核查')
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [op])


@pytest.mark.parametrize('kind', ['duplicate', 'parent_then_child', 'child_then_parent'])
def test_duplicate_or_parent_child_overlap_is_rejected_against_original_paths(kind):
    original = proposal(); discard = operation(original, '/objects/0')
    replacement = operation(original, '/objects/0/citations/0', replacement=citation('先由值班人员核查'))
    operations = [discard, copy.deepcopy(discard)] if kind == 'duplicate' else (
        [discard, replacement] if kind == 'parent_then_child' else [replacement, discard])
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, operations)


@pytest.mark.parametrize('version', [True, False, 4.0, 5.0, '4', '5', 3, 6, None])
def test_contract_version_requires_literal_supported_int(version):
    original = proposal()
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [operation(original, '/objects/0')], version=version)


@pytest.mark.parametrize('op', ['discard_item', 'replace_citation'])
def test_v4_rejects_every_relationship_operation(op):
    original = proposal(); path = '/relationships/0' + ('/citations/0' if op == 'replace_citation' else '')
    operation_value = operation(original, path,
        replacement=citation('先由值班人员核查') if op == 'replace_citation' else None)
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [operation_value], version=4)
    if op == 'discard_item':
        corrected, _ = apply(original, [operation_value], version=5)
        assert corrected['relationships'] == []


def test_all_operations_validate_before_any_effect_and_wrong_hash_never_changes_original():
    original = proposal(); before = copy.deepcopy(original)
    first = operation(original, '/businesses/0/citations/0', replacement=citation('先由值班人员核查'))
    bad = operation(original, '/objects/0'); bad['expected_hash'] = '0' * 64
    operations = [first, bad]; operations_before = copy.deepcopy(operations)
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, operations)
    assert original == before and operations == operations_before
    # A server digest must match this original item, not a frontend's guessed map.
    correct = operation(original, '/objects/0'); correct['expected_hash'] = digest(original['objects'][1])
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [correct])


@pytest.mark.parametrize('mutation', ['unknown_op', 'extra', 'missing', 'bool_path',
    'bool_hash', 'upper_hash', 'short_reason', 'long_reason', 'space_reason', 'nul_reason',
    'surrogate_reason', 'replacement_extra', 'original_extra', 'original_wrong_type',
    'replacement_wrong_type', 'bool_quote', 'wrong_container', 'dict_index_container'])
def test_closed_union_and_original_citation_structure_are_strict(mutation):
    original = proposal(); op = operation(original, '/objects/0/citations/0',
        replacement=citation('先由值班人员核查'))
    if mutation == 'unknown_op': op['op'] = 'replace_role'
    elif mutation == 'extra': op['confirm_authority'] = True
    elif mutation == 'missing': del op['reason']
    elif mutation == 'bool_path': op['path'] = True
    elif mutation == 'bool_hash': op['expected_hash'] = True
    elif mutation == 'upper_hash': op['expected_hash'] = 'A' * 64
    elif mutation == 'short_reason': op['reason'] = '短理由'
    elif mutation == 'long_reason': op['reason'] = '理' * 1001
    elif mutation == 'space_reason': op['reason'] = ' ' * 20
    elif mutation == 'nul_reason': op['reason'] = '不能保留空字符\x00理由'
    elif mutation == 'surrogate_reason': op['reason'] = '不能保留代理字符\ud800理由'
    elif mutation == 'replacement_extra': op['replacement']['role'] = 'admin'
    elif mutation == 'original_extra':
        original['objects'][0]['citations'][0]['role'] = 'admin'
        op['expected_hash'] = digest(original['objects'][0]['citations'][0])
    elif mutation == 'original_wrong_type':
        original['objects'][0]['citations'][0]['quote'] = True
        op['expected_hash'] = digest(original['objects'][0]['citations'][0])
    elif mutation == 'replacement_wrong_type': op['replacement'] = []
    elif mutation == 'bool_quote': op['replacement']['quote'] = True
    elif mutation == 'wrong_container': original['objects'][0]['citations'] = {'0': citation()}
    else: original['objects'] = {'0': original['objects'][0]}
    before = copy.deepcopy(original)
    with pytest.raises(policy.LocalReviewInvalid):
        apply(original, [op])
    assert original == before


@pytest.mark.parametrize('operations', [None, {}, [], [None], [True], [1], [{}], [{}] * 101])
def test_operation_container_and_count_are_bounded(operations):
    with pytest.raises(policy.LocalReviewInvalid):
        apply(proposal(), operations)


def test_unchanged_invalid_agent_and_dangling_relation_still_require_original_full_validator():
    original = proposal()
    corrected, _ = apply(original, [operation(original, '/objects/0')])
    assert corrected['relationships'][0]['source_object'] == '对象甲'
    assert corrected['businesses'][0]['steps'][0]['agent'] == '未知智能体'
    assert corrected['questions'] == original['questions']
    assert not any(key in corrected for key in ('validated', 'approved', 'published', 'capabilities'))


def test_python_shared_citation_lists_cannot_widen_a_literal_edit_path():
    original = proposal(); shared = [citation()]
    original['businesses'][0]['citations'] = shared
    original['objects'][0]['citations'] = shared
    replacement = citation('先由值班人员核查')
    corrected, _ = apply(original, [operation(original, '/businesses/0/citations/0', replacement=replacement)])
    assert corrected['businesses'][0]['citations'] == [replacement]
    assert corrected['objects'][0]['citations'] == [citation()]
    assert original['businesses'][0]['citations'] == original['objects'][0]['citations'] == [citation()]


def test_distinct_numeric_indices_are_not_mistaken_for_parent_child_prefixes():
    original = proposal()
    original['objects'] = [copy.deepcopy(original['objects'][0]) for _ in range(11)]
    replacement = citation('先由值班人员核查')
    corrected, _ = apply(original, [operation(original, '/objects/1'),
        operation(original, '/objects/10/citations/0', replacement=replacement)])
    assert len(corrected['objects']) == 10
    assert corrected['objects'][9]['citations'] == [replacement]


def test_exact_one_hundred_citation_edits_are_allowed_without_broadening_the_policy():
    original = proposal()
    business = copy.deepcopy(original['businesses'][0])
    step = copy.deepcopy(business['steps'][0])
    business['steps'] = [copy.deepcopy(step) for _ in range(10)]
    original['businesses'] = [copy.deepcopy(business) for _ in range(10)]
    replacement = citation('先由值班人员核查')
    operations = [operation(original, '/businesses/' + str(i) + '/steps/' + str(j)
        + '/citations/0', replacement=copy.deepcopy(replacement))
        for i in range(10) for j in range(10)]
    corrected, trace = apply(original, operations)
    assert len(trace) == 100
    assert all(step['citations'] == [replacement]
        for business in corrected['businesses'] for step in business['steps'])
