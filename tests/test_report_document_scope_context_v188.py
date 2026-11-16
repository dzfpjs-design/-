"""Actual inert DOCX/manifest/catalog context checks over synthetic bytes.

No real report, permissions, OCR provenance, publication or external scope is
proved. Reader, manifest rebuilding and public catalog resolution stay real.
"""
import copy
import hashlib

import pytest

from generic_agents import report_document_scope_context_v188 as subject
from generic_agents import docx_structure_manifest_v1 as structure
from generic_agents import native_report_reading as native
from generic_agents import report_evidence_plan as evidence
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_docx_structure_manifest_v1 import no_external, docx, p


def scene(body=None, styles=None, span_chars=4):
    if body is None:
        title = '同名标题🙂同名标题🙂'
        body = (p(title, '<w:outlineLvl w:val="0"/>')
            + p(title, '<w:outlineLvl w:val="1"/>')
            + p('重复正文甲乙重复正文甲乙') + p('') + p('后续正文丙丁')
            + p('另一真实章节', '<w:outlineLvl w:val="0"/>') + p('重复正文甲乙重复正文甲乙'))
    content = docx(body, styles)
    reading = native.read_docx(content)
    manifest = structure.build_manifest(content, reading=reading)
    ordered = [('w:' + item['id'], item['text']) for item in reading['blocks']]
    # A full report catalog may use an aggregate source identity; the helper
    # must not falsely equate it with this one Word file's source SHA.
    catalog = evidence.build_evidence_source(digest('public-combined-source'), ordered, span_chars)
    return {'content': content, 'reading': reading, 'manifest': manifest,
        'catalog': catalog, 'ordered': ordered}


def refs(c, paragraph_ref):
    record = next(item for item in c['manifest']['paragraphs'] if item['ref'] == paragraph_ref)
    return [ref for ref, item in c['catalog']['index'].items() if item['source_ref'] == record['legacy_ref']]


def build(c, selected, maximum=1024 * 1024):
    return subject.build_context(c['content'], reading=c['reading'], manifest=c['manifest'],
        catalog=c['catalog'], selected_span_refs=selected, max_bytes=maximum)


def frozen(c):
    return canonical_json([c['reading'], c['manifest'], c['catalog']])


def test_selected_subspans_keep_order_and_add_all_actual_ancestor_heading_refs_beyond_old_allowed():
    c = scene()
    selected = refs(c, 'ds:p3')[1:3][::-1]
    old_global_allowed = list(selected)
    before = frozen(c)
    result = build(c, selected)
    assert result['schema'] == subject.SCHEMA == 'report-document-scope-context-v188'
    assert result['selected_span_refs'] == selected
    assert [item['span_ref'] for item in result['selected_spans']] == selected
    assert [item['paragraph_ref'] for item in result['selected_spans']] == ['ds:p3', 'ds:p3']
    assert len(result['paragraphs']) == 1
    assert result['paragraphs'][0]['parent_ref'] == 'ds:p2'
    assert result['paragraphs'][0]['ancestor_refs'] == ['ds:p1', 'ds:p2']
    assert [item['paragraph_ref'] for item in result['headings']] == ['ds:p1', 'ds:p2']
    assert result['headings'][0]['text'] == result['headings'][1]['text']
    for heading in result['headings']:
        actual_refs = refs(c, heading['paragraph_ref'])
        assert heading['span_refs'] == actual_refs and len(actual_refs) > 1
        assert ''.join(item['text'] for item in heading['spans']) == heading['text']
        assert set(actual_refs).isdisjoint(old_global_allowed)
    assert result['unknown'] == []
    assert result['explicit_context_only'] is True and all(result[key] is False for key in subject._FLAGS)
    assert result['source_sha256'] == hashlib.sha256(c['content']).hexdigest()
    assert result['reading_hash'] == digest(c['reading'])
    assert result['manifest_hash'] == c['manifest']['manifest_hash']
    assert result['catalog_hash'] == c['catalog']['descriptor']['catalog_hash']
    assert result['catalog_source_hash'] == c['catalog']['descriptor']['source_hash'] != result['source_sha256']
    assert result['context_hash'] == digest({key: value for key, value in result.items() if key != 'context_hash'})
    assert frozen(c) == before and selected == old_global_allowed


def test_duplicate_paragraph_text_and_empty_word_paragraph_do_not_cause_first_substring_or_id_guessing():
    c = scene()
    selected = refs(c, 'ds:p7')[-1:]
    result = build(c, selected)
    item = result['selected_spans'][0]
    assert item['paragraph_ref'] == 'ds:p7' and item['source_ref'] == 'w:p6'
    assert item['parent_ref'] == 'ds:p6' and item['ancestor_refs'] == ['ds:p6']
    assert [heading['paragraph_ref'] for heading in result['headings']] == ['ds:p6']
    paragraph = c['manifest']['paragraphs'][6]
    assert item['text'] == paragraph['text'][item['start']:item['end']]
    assert c['manifest']['paragraphs'][3]['legacy_ref'] is None
    assert paragraph['text'] == c['manifest']['paragraphs'][2]['text']


def test_unknown_outline_never_reuses_a_preceding_heading_as_a_proven_parent():
    c = scene(p('已知标题', '<w:outlineLvl w:val="0"/>')
        + p('未知样式', '<w:pStyle w:val="Missing"/>') + p('后续正文'), styles='')
    result = build(c, refs(c, 'ds:p3'))
    assert result['headings'] == []
    assert result['selected_spans'][0]['parent_ref'] == 'ds:p1'
    assert result['selected_spans'][0]['parent_status'] == 'unknown'
    assert all(item['context_heading_refs'] == [] for item in result['selected_spans'])
    assert result['unknown'] and all(item['reason'] == 'unknown' for item in result['unknown'])


def test_table_container_scope_is_not_inherited_from_nearby_main_body_heading():
    table = ('<w:tbl><w:tr><w:tc>' + p('表内标题', '<w:outlineLvl w:val="0"/>')
        + p('单元格正文') + '</w:tc><w:tc>' + p('另一单元格正文') + '</w:tc></w:tr></w:tbl>')
    c = scene(p('主标题', '<w:outlineLvl w:val="0"/>') + table + p('后续主正文'))
    selected = refs(c, 'ds:p3')[:1] + refs(c, 'ds:p4')[:1] + refs(c, 'ds:p5')[:1]
    result = build(c, selected)
    first, second, third = result['selected_spans']
    assert first['context_heading_refs'] == ['ds:p2']
    assert second['parent_ref'] is None and second['context_heading_refs'] == []
    assert third['context_heading_refs'] == ['ds:p1']
    assert [heading['paragraph_ref'] for heading in result['headings']] == ['ds:p2', 'ds:p1']
    assert any(item['paragraph_ref'] == 'ds:p4' and item['reason'] == 'none_declared_in_scope'
        for item in result['unknown'])


@pytest.mark.parametrize('fault', ['content', 'reading', 'resealed_manifest'])
def test_real_bytes_and_reading_are_required_not_a_resealed_manifest_self_hash(fault):
    c = scene()
    selected = refs(c, 'ds:p3')[:1]
    if fault == 'content':
        c['content'] = docx(p('不同的真实文件'))
    elif fault == 'reading':
        c['reading']['blocks'][0]['text'] = '被替换的读取文字'
    else:
        record = c['manifest']['paragraphs'][0]
        record['text'] = '被替换的标题'
        record['text_sha256'] = hashlib.sha256(record['text'].encode('utf-8')).hexdigest()
        c['manifest']['manifest_hash'] = digest({key: value for key, value in c['manifest'].items() if key != 'manifest_hash'})
    with pytest.raises(ValueError):
        build(c, selected)


@pytest.mark.parametrize('fault', ['unselected_heading', 'truncated_paragraph', 'unknown_word', 'offset'])
def test_every_word_catalog_span_is_cold_checked_even_if_not_selected_and_self_consistent(fault):
    c = scene()
    selected = refs(c, 'ds:p3')[:1]
    if fault == 'offset':
        c['catalog']['index'][selected[0]]['start'] = 1
    else:
        blocks = copy.deepcopy(c['ordered'])
        if fault == 'unselected_heading': blocks[0] = (blocks[0][0], '伪造的目录标题')
        elif fault == 'truncated_paragraph':
            blocks[0] = (blocks[0][0], blocks[0][1][:4])
        else: blocks.append(('w:p999', '原Word不存在的段落'))
        c['catalog'] = evidence.build_evidence_source(c['catalog']['descriptor']['source_hash'], blocks, 4)
        assert evidence.check_evidence_source(c['catalog']) == c['catalog']
    with pytest.raises(ValueError):
        build(c, selected)


@pytest.mark.parametrize('fault', ['unknown', 'duplicate', 'empty', 'ocr', 'block_only'])
def test_requested_refs_must_resolve_exactly_to_real_word_spans_without_ref_splitting(fault):
    c = scene()
    selected = refs(c, 'ds:p3')[:1]
    if fault == 'unknown': selected = ['w:p999/s1']
    elif fault == 'duplicate': selected *= 2
    elif fault == 'empty': selected = []
    elif fault == 'block_only': selected = ['w:p3']
    else:
        c['catalog'] = evidence.build_evidence_source(c['catalog']['descriptor']['source_hash'],
            c['ordered'] + [('o:p1', '已存在的OCR片段，不属于本Word层级')], 4)
        selected = [ref for ref, item in c['catalog']['index'].items() if item['source_ref'] == 'o:p1'][:1]
    with pytest.raises(ValueError):
        build(c, selected)


def test_capacity_is_exact_utf8_whole_result_rejection_without_mutating_or_truncating_inputs():
    c = scene()
    selected = refs(c, 'ds:p3')[1:3]
    before = frozen(c)
    result = build(c, selected)
    size = len(canonical_json(result).encode('utf-8'))
    assert build(c, selected, size) == result
    with pytest.raises(evidence.EvidenceCapacityExceeded):
        build(c, selected, size - 1)
    for limit in (True, 0, subject.MAX_CONTEXT_BYTES + 1):
        with pytest.raises(ValueError):
            build(c, selected, limit)
    assert frozen(c) == before


def test_result_is_detached_and_exact_codepoint_slices_preserve_unicode_and_internal_whitespace():
    c = scene(p('真实标题🙂', '<w:outlineLvl w:val="0"/>') + p('甲🙂乙\t丙\n丁🙂戊'), span_chars=3)
    selected = refs(c, 'ds:p2')[1:3]
    before = frozen(c)
    result = build(c, selected)
    paragraph = c['manifest']['paragraphs'][1]
    for span in result['selected_spans']:
        assert span['text'] == paragraph['text'][span['start']:span['end']]
    original = copy.deepcopy(result)
    result['headings'][0]['text'] = '本机改写'
    result['headings'][0]['span_refs'].clear()
    result['paragraphs'][0]['ancestor_refs'].clear()
    result['selected_spans'][0]['text'] = '本机改写'
    assert frozen(c) == before and build(c, selected) == original


def test_actual_heading_without_catalog_rows_is_explicit_unknown_not_a_fabricated_span():
    c = scene(p('真实标题', '<w:outlineLvl w:val="0"/>') + p('真实正文'))
    c['catalog'] = evidence.build_evidence_source(c['catalog']['descriptor']['source_hash'], c['ordered'][1:], 4)
    selected = [ref for ref, item in c['catalog']['index'].items()]
    result = build(c, selected[:1])
    heading = result['headings'][0]
    assert heading['paragraph_ref'] == 'ds:p1' and heading['text'] == '真实标题'
    assert heading['span_refs'] == heading['spans'] == [] and heading['catalog_status'] == 'not_catalogued'
    assert result['unknown'] == [{'span_ref': selected[0], 'paragraph_ref': 'ds:p1', 'reason': 'heading_not_in_catalog'}]
    assert result['explicit_context_only'] is True and all(result[key] is False for key in subject._FLAGS)
