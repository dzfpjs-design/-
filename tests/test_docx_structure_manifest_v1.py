"""Actual inert DOCX normalization/reading/structure over synthetic zip files.

No original report, database, OCR, layout, model or external source permission
is proved.  The unchanged real reader and normalizer are never replaced.
"""
import copy
import io
import socket
import subprocess
import zipfile
from xml.sax.saxutils import escape, quoteattr

import pytest

from generic_agents import docx_structure_manifest_v1 as subject
from generic_agents import native_report_reading as native
from generic_agents.contracts import digest
from generic_agents.document_parsers import ParseProblem
from generic_agents.typed_payload_codec_v131 import canonical_json


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
OFFICE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('pure DOCX structure cannot contact a service or process')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def p(text='', props='', inner=None):
    run = '<w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r>' if inner is None else inner
    return '<w:p>' + ('<w:pPr>' + props + '</w:pPr>' if props else '') + run + '</w:p>'


def style(identity, *, level=None, base=None, default=False, name=None):
    result = '<w:style w:type="paragraph" w:styleId=' + quoteattr(identity)
    if default:
        result += ' w:default="1"'
    result += '>'
    if name:
        result += '<w:name w:val=' + quoteattr(name) + '/>'
    if base is not None:
        result += '<w:basedOn w:val=' + quoteattr(base) + '/>'
    if level is not None:
        result += '<w:pPr><w:outlineLvl w:val=' + quoteattr(str(level)) + '/></w:pPr>'
    return result + '</w:style>'


def docx(body, styles=None, *, relationship=''):
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            + ('<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' if styles is not None else '')
            + '</Types>',
        '_rels/.rels': '<Relationships xmlns="' + REL + '"><Relationship Id="root" Type="' + OFFICE
            + '/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="' + W + '" xmlns:mc="' + MC
            + '"><w:body>' + body + '<w:sectPr/></w:body></w:document>',
    }
    links = relationship
    if styles is not None:
        parts['word/styles.xml'] = '<w:styles xmlns:w="' + W + '">' + styles + '</w:styles>'
        links += '<Relationship Id="styles" Type="' + OFFICE + '/styles" Target="styles.xml"/>'
    if links:
        parts['word/_rels/document.xml.rels'] = '<Relationships xmlns="' + REL + '">' + links + '</Relationships>'
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value.encode('utf-8'))
    return result.getvalue()


def manifest(content):
    reading = native.read_docx(content)
    return subject.build_manifest(content, reading=reading), reading


def test_empty_paragraphs_keep_real_accepted_coordinates_and_actual_nonempty_legacy_ids():
    content = docx(p('  明确章节  ', '<w:outlineLvl w:val="0"/>') + p(' \t ') + p('正文'))
    value, reading = manifest(content); records = value['paragraphs']
    assert len(records) == 3 and len(reading['blocks']) == 2
    assert [item['ref'] for item in records] == ['ds:p1', 'ds:p2', 'ds:p3']
    assert [item['legacy_ref'] for item in records] == ['w:' + reading['blocks'][0]['id'], None, 'w:' + reading['blocks'][1]['id']]
    assert records[2]['location']['paragraph_index'] == 3 and records[2]['legacy_ref'] == 'w:p2'
    assert records[0]['accepted_xml_path'] == '/w:document[1]/w:body[1]/w:p[1]'
    assert records[0]['text'] == '明确章节' and records[1]['text'] == ''
    assert value['coordinate_space'] == 'accepted_final_word_body' and value['original_page_mapping'] == 'unavailable'
    assert all(value[key] is False for key in subject._FLAGS)
    assert subject.check_manifest(value, content, reading=reading) == value


def test_direct_outline_and_explicit_nine_override_style_inheritance_without_name_guessing():
    styles = style('Parent', level=0) + style('Child', level=1, base='Parent')
    content = docx(p('直接三级', '<w:pStyle w:val="Child"/><w:outlineLvl w:val="2"/>')
        + p('明确正文', '<w:pStyle w:val="Child"/><w:outlineLvl w:val="9"/>'), styles)
    value, _ = manifest(content); first, second = value['paragraphs']
    assert first['outline']['heading_level'] == 3 and first['outline']['origin'] == 'paragraph_outline'
    assert second['outline']['status'] == 'body' and second['outline']['outline_value'] == 9
    assert second['outline']['heading_level'] is None


def test_explicit_based_on_chain_and_parent_levels_have_property_provenance():
    styles = style('Root', level=0) + style('Local', base='Root') + style('Second', level=1)
    value, _ = manifest(docx(p('主章节', '<w:pStyle w:val="Local"/>')
        + p('子章节', '<w:pStyle w:val="Second"/>') + p('正文'), styles))
    root, child, body = value['paragraphs']
    assert root['outline']['based_on_chain'] == ['Local', 'Root']
    assert all(item['part'] == 'word/styles.xml' for item in root['outline']['evidence'])
    assert child['parent_ref'] == root['ref'] and body['parent_ref'] == child['ref']
    assert body['ancestor_refs'] == [root['ref'], child['ref']]


def test_default_style_precedes_doc_defaults_and_absent_outline_is_not_a_heading():
    defaults = '<w:docDefaults><w:pPrDefault><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:pPrDefault></w:docDefaults>'
    selected, _ = manifest(docx(p('采用明确默认样式'), defaults + style('Default', level=0, default=True)))
    assert selected['paragraphs'][0]['outline']['heading_level'] == 1
    fallback, _ = manifest(docx(p('采用明确docDefaults'), defaults))
    assert fallback['paragraphs'][0]['outline']['heading_level'] == 3
    named, _ = manifest(docx(p('第一章看起来像标题', '<w:pStyle w:val="Custom"/>'), style('Custom', name='Heading 1')))
    assert named['paragraphs'][0]['outline']['status'] == 'not_declared'
    absent, _ = manifest(docx(p('1. 粗体或编号文字')))
    assert absent['paragraphs'][0]['outline']['heading_level'] is None


@pytest.mark.parametrize('styles,props', [
    ('', '<w:pStyle w:val="Missing"/>'),
    (style('A', base='Missing'), '<w:pStyle w:val="A"/>'),
    (style('A', base='B') + style('B', base='A'), '<w:pStyle w:val="A"/>'),
    (style('A', level=0) + style('A', level=1), '<w:pStyle w:val="A"/>'),
    ('', '<w:outlineLvl w:val="10"/>'),
    ('', '<w:outlineLvl w:val="0"/><w:outlineLvl w:val="1"/>'),
])
def test_unknown_missing_cyclic_duplicate_and_invalid_outline_never_guess_a_heading(styles, props):
    value, _ = manifest(docx(p('不能猜成标题', props), styles))
    outline = value['paragraphs'][0]['outline']
    assert outline['status'] == 'unknown' and outline['heading_level'] is None and outline['issues']


def test_revision_view_hidden_text_and_deleted_paragraph_mark_use_the_real_unchanged_normalizer():
    revised = p(inner='<w:r><w:t>保留</w:t></w:r><w:del w:id="1"><w:r><w:delText>删除</w:delText></w:r></w:del>'
        '<w:ins w:id="2"><w:r><w:t>新增</w:t></w:r></w:ins><w:r><w:rPr><w:vanish/></w:rPr><w:t>隐藏保留</w:t></w:r>',
        props='<w:outlineLvl w:val="1"/><w:pPrChange w:id="3"><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:pPrChange>')
    joined = p('甲', '<w:rPr><w:del w:id="4"/></w:rPr>') + p('乙')
    value, reading = manifest(docx(revised + joined))
    assert [item['text'] for item in value['paragraphs']] == ['保留新增隐藏保留', '甲乙']
    assert value['paragraphs'][0]['outline']['heading_level'] == 2
    assert [item['location'] for item in value['paragraphs']] == [item['location'] for item in reading['blocks']]
    assert value['revision_policy'] == 'accepted_final_v1' and value['hidden_content_policy'] == 'reveal_retained_text_v1'
    assert value['original_page_mapping'] == 'unavailable'


def test_table_cells_nested_textbox_and_main_body_do_not_share_heading_stacks():
    title = p('主正文标题', '<w:outlineLvl w:val="0"/>')
    table = '<w:tbl><w:tr><w:tc>' + p('表内标题', '<w:outlineLvl w:val="0"/>') + p('同单元格正文') \
        + '</w:tc><w:tc>' + p('另一单元格正文') + '</w:tc></w:tr></w:tbl>'
    textbox = p('外层正文', inner='<w:r><w:t>外层正文</w:t><w:pict><w:txbxContent>'
        + p('框内标题', '<w:outlineLvl w:val="0"/>') + p('框内正文') + '</w:txbxContent></w:pict></w:r>')
    value, reading = manifest(docx(title + table + textbox + p('后续主正文')))
    rows = {item['text']: item for item in value['paragraphs']}
    assert rows['同单元格正文']['parent_ref'] == rows['表内标题']['ref']
    assert rows['另一单元格正文']['parent_ref'] is None
    assert rows['框内正文']['parent_ref'] == rows['框内标题']['ref']
    assert rows['后续主正文']['parent_ref'] == rows['主正文标题']['ref']
    assert rows['框内正文']['kind'] == 'textbox_paragraph' and rows['同单元格正文']['kind'] == 'table_paragraph'
    for record, block in zip([item for item in value['paragraphs'] if item['text']], reading['blocks'], strict=True):
        assert record['location'] == block['location'] and record['kind'] == block['kind']


def test_choice_and_fallback_remain_separate_unresolved_display_branches():
    variants = '<mc:AlternateContent><mc:Choice Requires="w">' + p('Choice标题', '<w:outlineLvl w:val="0"/>') \
        + p('Choice正文') + '</mc:Choice><mc:Fallback>' + p('Fallback标题', '<w:outlineLvl w:val="0"/>') \
        + p('Fallback正文') + '</mc:Fallback></mc:AlternateContent>'
    value, _ = manifest(docx(p('主标题', '<w:outlineLvl w:val="0"/>') + variants + p('主正文')))
    rows = {item['text']: item for item in value['paragraphs']}
    assert rows['Choice正文']['parent_ref'] == rows['Choice标题']['ref']
    assert rows['Fallback正文']['parent_ref'] == rows['Fallback标题']['ref']
    assert rows['主正文']['parent_ref'] == rows['主标题']['ref']
    assert rows['Choice正文']['location']['compatibility_branch'] == 'choice'
    assert rows['Fallback正文']['location']['compatibility_branch'] == 'fallback'
    assert rows['Choice正文']['scope'] != rows['Fallback正文']['scope']
    assert value['whole_report_verified'] is False


def test_duplicate_text_is_mapped_by_full_coordinates_not_the_first_equal_sha():
    value, reading = manifest(docx(p('重复') + p('') + p('重复')))
    first, _, last = value['paragraphs']
    assert first['text_sha256'] == last['text_sha256'] and first['location'] != last['location']
    assert first['legacy_ref'] != last['legacy_ref']
    damaged = copy.deepcopy(reading); damaged['blocks'].reverse()
    with pytest.raises(ParseProblem): subject.build_manifest(docx(p('重复') + p('') + p('重复')), reading=damaged)


def test_cold_reading_and_manifest_rebuild_reject_omission_resealing_or_changed_bytes():
    content = docx(p('标题', '<w:outlineLvl w:val="0"/>') + p('正文'))
    value, reading = manifest(content); before = canonical_json([value, reading])
    damaged = copy.deepcopy(reading); damaged['blocks'].pop()
    with pytest.raises(ParseProblem): subject.build_manifest(content, reading=damaged)
    changed = copy.deepcopy(value); changed['paragraphs'][1]['parent_ref'] = None
    changed['manifest_hash'] = digest({key: item for key, item in changed.items() if key != 'manifest_hash'})
    with pytest.raises(ParseProblem): subject.check_manifest(changed, content, reading=reading)
    with pytest.raises(ParseProblem): subject.check_manifest(value, docx(p('不同正文')), reading=reading)
    assert canonical_json([value, reading]) == before


def test_section_context_only_returns_nearest_explicit_parent_and_keeps_independent_namespace():
    value, _ = manifest(docx(p('一级', '<w:outlineLvl w:val="0"/>')
        + p('二级', '<w:outlineLvl w:val="1"/>') + p('正文一') + p('正文二')))
    before = canonical_json(value)
    result = subject.section_context(value, ['w:p3', 'w:p4'], max_bytes=50000)
    assert [item['text'] for item in result['contexts']] == ['二级']
    assert result['contexts'][0]['ref'].startswith('ds:') and result['contexts'][0]['legacy_ref'] == 'w:p2'
    assert result['manifest_hash'] == value['manifest_hash'] and not result['unknown']
    assert all(result[key] is False for key in subject._FLAGS)
    result['contexts'][0]['text'] = 'local edit'
    assert canonical_json(value) == before


def test_unknown_outline_and_unresolved_or_subspan_refs_are_not_guessed_into_context():
    value, _ = manifest(docx(p('已知标题', '<w:outlineLvl w:val="0"/>')
        + p('未知样式', '<w:pStyle w:val="Missing"/>') + p('后续正文'), ''))
    result = subject.section_context(value, ['w:p3', 'w:p3/s1', 'w:p999'], max_bytes=50000)
    assert result['contexts'] == []
    assert [item['reason'] for item in result['unknown']] == ['unknown',
        'legacy_reference_not_in_manifest', 'legacy_reference_not_in_manifest']
    assert result['matches'][0]['parent_ref'] == 'ds:p1' and result['matches'][0]['parent_status'] == 'unknown'


def test_empty_heading_and_conflicting_defaults_remain_unknown_not_blank_source_context():
    value, _ = manifest(docx(p('', '<w:outlineLvl w:val="0"/>') + p('正文')))
    assert value['paragraphs'][0]['legacy_ref'] is None
    assert value['paragraphs'][0]['context_issues'] == ['empty_heading_text']
    result = subject.section_context(value, ['w:p1'], max_bytes=50000)
    assert not result['contexts'] and result['unknown'][0]['reason'] == 'unknown'
    conflict, _ = manifest(docx(p('正文'), style('A', level=0, default=True) + style('B', level=1, default=True)))
    assert conflict['paragraphs'][0]['outline']['status'] == 'unknown'


def test_capacity_is_whole_result_rejection_and_external_relationships_stay_blocked(monkeypatch):
    content = docx(p('明确标题', '<w:outlineLvl w:val="0"/>') + p('正文'))
    value, reading = manifest(content); before = canonical_json([value, reading])
    with pytest.raises(ParseProblem): subject.section_context(value, ['w:p2'], max_bytes=1)
    with pytest.raises(ParseProblem): subject.section_context(value, ['w:p2'], max_bytes=True)
    monkeypatch.setattr(subject, 'MAX_MANIFEST_BYTES', 1)
    with pytest.raises(ParseProblem): subject.build_manifest(content, reading=reading)
    assert canonical_json([value, reading]) == before
    external = '<Relationship Id="external" Type="' + OFFICE + '/image" Target="https://invalid.example/image.png" TargetMode="External"/>'
    with pytest.raises(ParseProblem): manifest(docx(p('正文'), relationship=external))
