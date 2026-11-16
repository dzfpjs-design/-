"""Synthetic pure compilation checks, not source authorization or AI quality."""
import copy
from uuid import uuid4

import pytest

from generic_agents.contracts import digest
from generic_agents.local_proposal_review_policy_v261 import LocalReviewInvalid, apply_local_review
from generic_agents.local_proposal_review_compile_v261 import compile_saved_review


def scene(version=5):
    citation = {'material_id': '资料甲', 'block_id': '段落一', 'quote': '旧错误引用'}
    raw = {'domain': '工程对照', 'businesses': [
        {'name': '业务甲', 'citations': [copy.deepcopy(citation)], 'steps': [
            {'agent': '未知智能体', 'role': '原职责', 'citations': [copy.deepcopy(citation)]}]},
        {'name': '业务乙', 'citations': [copy.deepcopy(citation)], 'steps': []}],
        'objects': [{'name': '对象甲', 'citations': [copy.deepcopy(citation)],
            'fields': [{'name': '字段甲', 'citations': [copy.deepcopy(citation)]}]}],
        'relationships': [{'name': '关系甲', 'source_object': '对象甲',
            'target_object': '对象甲', 'citations': [copy.deepcopy(citation)]}],
        'questions': ['原问题保持']}
    pins = {'analysis_id': str(uuid4()), 'source_version': 7,
        'raw_hash': digest({'synthetic_raw_response': raw}), 'contract_version': version}
    items = {'/businesses/0': digest(raw['businesses'][0]),
        '/businesses/1': digest(raw['businesses'][1]), '/objects/0': digest(raw['objects'][0])}
    citations = {path: digest(citation) for path in ('/businesses/0/citations/0',
        '/businesses/0/steps/0/citations/0', '/businesses/1/citations/0',
        '/objects/0/citations/0', '/objects/0/fields/0/citations/0')}
    if version == 5:
        items['/relationships/0'] = digest(raw['relationships'][0])
        citations['/relationships/0/citations/0'] = digest(citation)
    source = {**pins, 'raw_candidate': raw, 'source_blocks': [
        {'material_id': '资料甲', 'block_id': '段落一', 'material_name': '授权资料甲.txt',
            'ordinal': 1, 'text': '甲🌲乙\n丙丁。'}], 'item_hashes': items, 'citation_hashes': citations}
    return {'source': copy.deepcopy(pins), 'edits': []}, source


def replacement(path='/businesses/0/citations/0', *, start='2', end='5', reason='人工明确核对原文'):
    return {'op': 'replace_citation', 'path': path, 'reason': reason,
        'material_id': '资料甲', 'block_id': '段落一', 'start_text': start, 'end_text': end}


def discard(path='/businesses/1', *, reason='明确舍弃不支持条目'):
    return {'op': 'discard_item', 'path': path, 'reason': reason}


@pytest.mark.parametrize('path', ['/businesses/0/citations/0',
    '/businesses/0/steps/0/citations/0', '/objects/0/citations/0',
    '/objects/0/fields/0/citations/0', '/relationships/0/citations/0'])
def test_compile_exact_citation_op_recomputes_original_hash_and_preserves_all_inputs(path):
    value, source = scene(); value['edits'] = [replacement(path)]
    old_value = copy.deepcopy(value); old_source = copy.deepcopy(source)
    operations = compile_saved_review(value, source)
    assert operations == [{'op': 'replace_citation', 'path': path,
        'expected_hash': digest({'material_id': '资料甲', 'block_id': '段落一', 'quote': '旧错误引用'}),
        'replacement': {'material_id': '资料甲', 'block_id': '段落一', 'quote': '🌲乙\n丙'},
        'reason': '人工明确核对原文'}]
    assert value == old_value and source == old_source
    assert not any(key in operations[0] for key in ('trace', 'source_block_hash', 'validated', 'approved'))
    operations[0]['replacement']['quote'] = '调用者后续不能回写原文'
    assert value == old_value and source == old_source


def test_positions_are_inclusive_unicode_codepoints_and_never_utf16_code_units():
    value, source = scene(); text = source['source_blocks'][0]['text']
    assert len(text) == 7 and len(text.encode('utf-16-le')) // 2 == 8
    value['edits'] = [replacement(start='3', end='3')]
    assert compile_saved_review(value, source)[0]['replacement']['quote'] == '乙'
    value['edits'] = [replacement(start='1', end='7')]
    assert compile_saved_review(value, source)[0]['replacement']['quote'] == text


@pytest.mark.parametrize('path', ['/businesses/1', '/objects/0', '/relationships/0'])
def test_whole_item_discard_expected_hash_is_computed_from_original_item(path):
    value, source = scene(); value['edits'] = [discard(path)]
    operations = compile_saved_review(value, source)
    collection, index = path[1:].split('/')
    assert operations == [{'op': 'discard_item', 'path': path,
        'expected_hash': digest(source['raw_candidate'][collection][int(index)]),
        'reason': '明确舍弃不支持条目'}]


def test_only_requested_operations_are_compiled_unknown_agent_and_dangling_relations_remain():
    value, source = scene(); value['edits'] = [discard('/objects/0'), replacement()]
    operations = compile_saved_review(value, source)
    corrected, _ = apply_local_review(source['raw_candidate'], operations,
        source['source_blocks'], contract_version=5)
    assert corrected['objects'] == []
    assert corrected['relationships'] == source['raw_candidate']['relationships']
    assert corrected['businesses'][0]['steps'][0]['agent'] == '未知智能体'
    assert corrected['businesses'][0]['steps'][0]['role'] == '原职责'
    assert corrected['questions'] == source['raw_candidate']['questions']


@pytest.mark.parametrize('key', ['analysis_id', 'source_version', 'raw_hash', 'contract_version'])
def test_private_draft_cannot_submit_against_a_changed_source_generation(key):
    value, source = scene(); value['edits'] = [discard()]
    source[key] = {'analysis_id': str(uuid4()), 'source_version': 8,
        'raw_hash': '0' * 64, 'contract_version': 4}[key]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


@pytest.mark.parametrize('damage', ['selected_item', 'selected_citation', 'unselected_item',
    'unselected_citation', 'missing', 'extra', 'not_dict', 'raw_item_changed', 'raw_citation_changed'])
def test_maps_are_recomputed_completely_never_trusted_as_client_expected_hashes(damage):
    value, source = scene(); value['edits'] = [discard(), replacement()]
    if damage == 'selected_item': source['item_hashes']['/businesses/1'] = '0' * 64
    elif damage == 'selected_citation': source['citation_hashes']['/businesses/0/citations/0'] = '0' * 64
    elif damage == 'unselected_item': source['item_hashes']['/objects/0'] = '0' * 64
    elif damage == 'unselected_citation': source['citation_hashes']['/objects/0/fields/0/citations/0'] = '0' * 64
    elif damage == 'missing': del source['item_hashes']['/objects/0']
    elif damage == 'extra': source['citation_hashes']['/objects/0/citations/88'] = '0' * 64
    elif damage == 'not_dict': source['item_hashes'] = []
    elif damage == 'raw_item_changed': source['raw_candidate']['businesses'][1]['name'] = '改变候选未更新索引'
    else: source['raw_candidate']['businesses'][0]['citations'][0]['quote'] = '改变引用未更新索引'
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


@pytest.mark.parametrize('start,end', [('', '5'), ('2', ''), ('0', '1'), ('01', '2'),
    ('-1', '2'), ('+1', '2'), ('1.0', '2'), ('1e0', '2'), ('１', '2'), ('١', '2'),
    (' 1', '2'), ('1 ', '2'), (True, '2'), ('1', False), (1, '2'), ('1', None),
    ('8', '8'), ('5', '2'), ('9' * 10000, '5')])
def test_position_input_must_be_complete_ascii_canonical_positive_integer_in_original_bounds(start, end):
    value, source = scene(); value['edits'] = [replacement(start=start, end=end)]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


def test_quote_maximum_and_whitespace_only_rejection_preserve_actual_source_characters():
    value, source = scene(); source['source_blocks'][0]['text'] = '甲' * 300
    value['edits'] = [replacement(start='1', end='250')]
    assert compile_saved_review(value, source)[0]['replacement']['quote'] == '甲' * 250
    value['edits'] = [replacement(start='1', end='251')]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)
    source['source_blocks'][0]['text'] = ' \n\t甲🌲\n '
    value['edits'] = [replacement(start='1', end='3')]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)
    value['edits'] = [replacement(start='1', end='7')]
    assert compile_saved_review(value, source)[0]['replacement']['quote'] == ' \n\t甲🌲\n '


@pytest.mark.parametrize('damage', ['no_material', 'no_block', 'wrong_material', 'wrong_block',
    'duplicate_same_text', 'duplicate_different_text', 'wrong_text_type'])
def test_original_block_selection_is_explicit_unique_and_cannot_be_ambiguous(damage):
    value, source = scene(); edit = replacement(); value['edits'] = [edit]
    if damage == 'no_material': edit['material_id'] = None
    elif damage == 'no_block': edit['block_id'] = None
    elif damage == 'wrong_material': edit['material_id'] = '其他材料'
    elif damage == 'wrong_block': edit['block_id'] = '其他段落'
    elif damage.startswith('duplicate'):
        duplicate = copy.deepcopy(source['source_blocks'][0])
        if damage == 'duplicate_different_text': duplicate['text'] = '不能任取不同内容'
        source['source_blocks'].append(duplicate)
    else: source['source_blocks'][0]['text'] = ['甲🌲乙\n丙丁。']
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


@pytest.mark.parametrize('edits', ['empty', 'too_many', 'unknown', 'extra', 'discard_extra',
    'missing', 'step_discard', 'field_discard', 'role_path', 'duplicate', 'parent_child'])
def test_incomplete_or_nonclosed_or_overlapping_intentions_never_compile(edits):
    value, source = scene(); value['edits'] = [replacement()]
    if edits == 'empty': value['edits'] = []
    elif edits == 'too_many': value['edits'] *= 101
    elif edits == 'unknown': value['edits'][0]['op'] = 'replace_agent'
    elif edits == 'extra': value['edits'][0]['expected_hash'] = '0' * 64
    elif edits == 'discard_extra': value['edits'] = [{**discard(), 'replacement': {}}]
    elif edits == 'missing': del value['edits'][0]['reason']
    elif edits == 'step_discard': value['edits'] = [discard('/businesses/0/steps/0')]
    elif edits == 'field_discard': value['edits'] = [discard('/objects/0/fields/0')]
    elif edits == 'role_path': value['edits'][0]['path'] = '/businesses/0/steps/0/role'
    elif edits == 'duplicate': value['edits'].append(copy.deepcopy(value['edits'][0]))
    else: value['edits'] = [discard('/businesses/0'), replacement()]
    original = copy.deepcopy(value); original_source = copy.deepcopy(source)
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)
    assert value == original and source == original_source


@pytest.mark.parametrize('reason', ['', '短理由', ' ' * 20, '理' * 1001, True, '不能有空字符\x00'])
def test_reason_is_not_generated_or_trimmed_to_force_an_incomplete_draft_through_policy(reason):
    value, source = scene(); value['edits'] = [discard(reason=reason)]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


def test_reason_original_text_and_operation_order_are_preserved():
    value, source = scene(); reason = '  值班人员🌲明确核对原文\n '
    value['edits'] = [replacement(reason=reason), discard(reason=reason)]
    compiled = compile_saved_review(value, source)
    assert [op['op'] for op in compiled] == ['replace_citation', 'discard_item']
    assert all(op['reason'] == reason for op in compiled)


@pytest.mark.parametrize('damage', ['source_extra', 'value_extra', 'pins_extra', 'bool_version',
    'float_version', 'str_contract', 'bool_contract', 'uppercase_uuid', 'bad_hash', 'wrong_raw_container'])
def test_source_and_draft_wrappers_are_exact_strict_and_do_not_authorize_other_fields(damage):
    value, source = scene(); value['edits'] = [discard()]
    if damage == 'source_extra': source['approved'] = True
    elif damage == 'value_extra': value['permission'] = 'admin'
    elif damage == 'pins_extra': value['source']['capability_id'] = str(uuid4())
    elif damage == 'bool_version': value['source']['source_version'] = True
    elif damage == 'float_version': value['source']['source_version'] = 7.0
    elif damage == 'str_contract': value['source']['contract_version'] = '5'
    elif damage == 'bool_contract': value['source']['contract_version'] = True
    elif damage == 'uppercase_uuid': value['source']['analysis_id'] = 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'
    elif damage == 'bad_hash': value['source']['raw_hash'] = 'A' * 64
    else: source['raw_candidate'] = []
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


@pytest.mark.parametrize('op', ['discard_item', 'replace_citation'])
def test_v4_never_compiles_relationship_edits_even_if_raw_candidate_contains_them(op):
    value, source = scene(version=4)
    value['edits'] = [discard('/relationships/0') if op == 'discard_item'
        else replacement('/relationships/0/citations/0')]
    with pytest.raises(LocalReviewInvalid):
        compile_saved_review(value, source)


def test_display_source_trace_is_never_exposed_as_original_request_block_proof():
    value, source = scene(); value['edits'] = [replacement()]
    compiled = compile_saved_review(value, source)
    original_blocks = [{'material_id': '资料甲', 'block_id': '段落一',
        'text': source['source_blocks'][0]['text'], 'location': {'page': 3}}]
    _, authoritative_trace = apply_local_review(source['raw_candidate'], compiled,
        original_blocks, contract_version=5)
    assert authoritative_trace[0]['source_block_hash'] == digest(original_blocks[0])
    assert authoritative_trace[0]['source_block_hash'] != digest(source['source_blocks'][0])
    assert all('source_block_hash' not in item for item in compiled)
