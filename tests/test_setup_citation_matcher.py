"""Only synthetic data: a match is evidence location, never confirmation."""
from generic_agents.setup_citation_matcher import resolve_material_citation


FILE_HASH = 'a' * 64
OTHER_HASH = 'b' * 64


def material(*blocks, **overrides):
    return {'id': 'material-1', 'hash': FILE_HASH, 'version': 4,
            'engine': 'native-docx-v1', 'blocks': list(blocks), **overrides}


def block(identity, text, **extras):
    return {'id': identity, 'text': text, **extras}


def resolve(value, *, source='发现疑似异常后安排现场核查。', quote='安排现场核查',
            expected_hash=FILE_HASH, version=4, engine='native-docx-v1'):
    return resolve_material_citation(material=value, expected_file_hash=expected_hash,
        expected_material_version=version, expected_engine=engine,
        source_text=source, quote=quote)


def test_unique_paragraph_produces_real_block_and_version_binding():
    value = material(block('b7', '发现疑似异常后安排现场核查。'))
    result = resolve(value)
    assert result == {'status': 'matched',
        'citation': {'material_id': 'material-1', 'block_id': 'b7', 'quote': '安排现场核查'},
        'binding': {'material_id': 'material-1', 'hash': FILE_HASH, 'version': 4,
                    'engine': 'native-docx-v1'}}


def test_table_row_uses_real_block_and_never_copies_manual_index():
    value = material(block('b52', '设备 | 执行时间 | 夜间暂停', kind='table_row',
                           location={'body_index': 8, 'row': 2}))
    result = resolve(value, source='执行时间 | 夜间暂停', quote='夜间暂停')
    assert result['citation'] == {'material_id': 'material-1', 'block_id': 'b52', 'quote': '夜间暂停'}


def test_wrong_original_document_or_reparsed_material_requires_review():
    value = material(block('b1', '发现疑似异常后安排现场核查。'))
    for kwargs in ({'expected_hash': OTHER_HASH}, {'version': 5}, {'engine': 'other-parser'}):
        assert resolve(value, **kwargs) == {'status': 'needs_review', 'reason': 'material_changed'}


def test_duplicate_source_and_repeated_quote_are_ambiguous():
    text = '发现疑似异常后安排现场核查。'
    assert resolve(material(block('b1', text), block('b2', text))) == {
        'status': 'needs_review', 'reason': 'ambiguous'}
    assert resolve(material(block('b1', text + text))) == {
        'status': 'needs_review', 'reason': 'ambiguous'}
    assert resolve(material(block('b1', text + '安排现场核查'))) == {
        'status': 'needs_review', 'reason': 'ambiguous'}


def test_missing_revision_text_does_not_guess():
    assert resolve(material(block('b1', '发现异常后安排现场核查。'))) == {
        'status': 'needs_review', 'reason': 'not_found'}


def test_unicode_is_exact_not_normalized():
    value = material(block('b1', '林场é观察'))
    assert resolve(value, source='林场e\u0301观察', quote='e\u0301')['reason'] == 'not_found'
    assert resolve(value, source='林场é观察', quote='é')['status'] == 'matched'


def test_invalid_unicode_or_embedded_nul_never_becomes_a_citation():
    value = material(block('b1', '可核查资料'))
    assert resolve(value, source='可\x00核查', quote='可\x00')['reason'] == 'invalid_quote'
    assert resolve(value, source='不可编码\ud800', quote='\ud800')['reason'] == 'invalid_quote'
    assert resolve(material(block('b1', '可核查\ud800')), source='可核查', quote='可核查')['reason'] == 'invalid_material'


def test_overlapping_source_or_quote_is_ambiguous():
    assert resolve(material(block('b1', 'aaaa')), source='aaa', quote='aaa')['reason'] == 'ambiguous'
    assert resolve(material(block('b1', 'aaaa甲')), source='aaaa甲', quote='aaa')['reason'] == 'ambiguous'


def test_identical_text_from_another_document_never_matches():
    text = '发现疑似异常后安排现场核查。'
    value = material(block('b1', text), hash=OTHER_HASH)
    assert resolve(value, expected_hash=FILE_HASH)['reason'] == 'material_changed'


def test_bad_later_block_invalidates_previously_matched_candidate():
    text = '发现疑似异常后安排现场核查。'
    assert resolve(material(block('b1', text), {'id': 'b2'}))['reason'] == 'invalid_material'


def test_quote_limits_and_no_quote_outside_source():
    text = '甲' * 250 + '乙'
    value = material(block('b1', text))
    assert resolve(value, source=text, quote='甲' * 250)['status'] == 'matched'
    assert resolve(value, source=text, quote=text)['reason'] == 'invalid_quote'
    assert resolve(value, source='甲' * 250, quote='乙')['reason'] == 'invalid_quote'
    assert resolve(value, source=text, quote='')['reason'] == 'invalid_quote'


def test_invalid_or_duplicate_material_block_ids_fail_closed():
    source = '发现疑似异常后安排现场核查。'
    for value in (material(block('b1', source), block('b1', '其他')),
                  material(block('', source)), material({'id': 'b1', 'text': None}),
                  material(block('b1', source), id='')):
        assert resolve(value)['status'] == 'needs_review'


def test_reference_resolution_never_changes_material():
    value = material(block('b1', '发现疑似异常后安排现场核查。'), status='needs_input')
    import copy
    before = copy.deepcopy(value)
    resolve(value)
    assert value == before


def test_wrong_material_shape_and_hash_are_never_trusted():
    for value in (None, {}, material(block('b1', 'x'), hash='bad'),
                  material(block('b1', 'x'), version=True),
                  material(block('b1', 'x'), blocks='not-a-list')):
        assert resolve(value)['status'] == 'needs_review'


def test_oversized_material_and_source_fail_closed():
    value = material(*(block('b' + str(i), '') for i in range(12001)))
    assert resolve(value)['reason'] == 'invalid_material'
    value = material(block('b1', '甲' * 2000001))
    assert resolve(value, source='甲', quote='甲')['reason'] == 'invalid_material'
    assert resolve(material(block('b1', '甲')), source='甲' * 2000001, quote='甲')['reason'] == 'invalid_quote'
