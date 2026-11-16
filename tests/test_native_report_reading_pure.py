"""No service/DB/model: real normalizer plus exhaustive body-text projection."""
import io
import json
import socket
import subprocess
import zipfile
import pytest
from generic_agents import native_report_reading as reading
from generic_agents.document_parsers import ParseProblem

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def doc(body, extra=None):
    parts={'[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
      '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="'+R+'/officeDocument" Target="word/document.xml"/></Relationships>',
      'word/document.xml':'<w:document xmlns:w="'+W+'" xmlns:r="'+R+'" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'+body+'</w:body></w:document>',**(extra or {})}
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in parts.items():z.writestr(name,value)
    return out.getvalue()

def p(text):return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'

@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*a,**kw):pytest.fail('native report reading forbids external effects')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(socket,'getaddrinfo',deny);monkeypatch.setattr(subprocess,'Popen',deny)


def test_wrapped_and_nested_body_paragraphs_are_all_returned_once():
    body=p('首段')+'<w:sdt><w:sdtContent>'+p('内容控件')+'</w:sdtContent></w:sdt><w:customXml>'+p('自定义正文')+'</w:customXml>'
    body+='<w:tbl><w:tr><w:tc>'+p('外表格')+'<w:tbl><w:tr><w:tc>'+p('内表格')+'</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>'
    result=reading.read_docx(doc(body));blocks=result['blocks']
    assert [b['text'] for b in blocks]==['首段','内容控件','自定义正文','外表格','内表格']
    assert [b['id'] for b in blocks]==['p1','p2','p3','p4','p5']
    assert blocks[3]['kind']==blocks[4]['kind']=='table_paragraph'
    assert len({b['id'] for b in blocks})==len(blocks)
    assert all(b['location']['view']=='accepted_final_v1' and 'page' not in b['location'] for b in blocks)
    assert result['coverage']['counts']['tables']==2


def test_textbox_inner_text_belongs_only_to_nearest_paragraph_and_empty_is_counted():
    body='<w:p><w:r><w:t>外前</w:t></w:r><w:r><w:drawing><w:txbxContent>'+p('框内')+'</w:txbxContent></w:drawing></w:r><w:r><w:t>外后</w:t></w:r></w:p>'+p('   ')
    result=reading.read_docx(doc(body))
    assert [b['text'] for b in result['blocks']]==['外前外后','框内']
    assert result['blocks'][1]['kind']=='textbox_paragraph'
    assert sum(b['text'].count('框内') for b in result['blocks'])==1
    assert result['coverage']['counts']['paragraphs_total']==3
    assert result['coverage']['counts']['paragraphs_empty']==1
    assert any(g['kind']=='textbox_layout_or_reading_order' for g in result['coverage']['gaps'])


def test_revision_view_is_explicit_and_never_becomes_final_acceptance():
    body='<w:p><w:ins>'+p('不合法嵌套')+'</w:ins></w:p>'
    # Valid run insertion/deletion; deleted paragraph-mark joining shifts anchors.
    body='<w:p><w:r><w:t>保留</w:t></w:r><w:ins><w:r><w:t>插入</w:t></w:r></w:ins><w:del><w:r><w:delText>删除</w:delText></w:r></w:del></w:p>'
    result=reading.read_docx(doc(body));assert [b['text'] for b in result['blocks']]==['保留插入']
    assert result['revision_policy']=='accepted_final_v1'
    assert result['coverage']['policy_changes']['accepted_ins']==1
    assert result['coverage']['policy_changes']['accepted_del']==1
    assert result['full_report_confirmed'] is False and result['can_apply'] is False
    assert result['schema']=='report-body-draft-source-v1'
    assert result['coverage']['counts']['source_revision_insertions']==1


def test_hidden_text_policy_is_counted_and_disclosed_not_silently_omitted():
    result=reading.read_docx(doc('<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>原隐藏文字</w:t></w:r></w:p>'))
    assert result['blocks'][0]['text']=='原隐藏文字'
    assert result['hidden_content_policy']=='reveal_retained_text_v1'
    assert result['coverage']['counts']['source_hidden_markers']==1
    assert result['coverage']['policy_changes']['revealed_hidden_text_markers']==1
    assert any('隐藏' in warning for warning in result['warnings'])


def test_headers_footnotes_and_diagrams_are_explicit_unread_gaps():
    extras={'word/header1.xml':'<w:hdr xmlns:w="'+W+'">'+p('不得混入正文')+'</w:hdr>',
            'word/footnotes.xml':'<w:footnotes xmlns:w="'+W+'"><w:footnote w:id="1">'+p('脚注未读')+'</w:footnote></w:footnotes>'}
    body=p('正文')+'<w:p><w:r><w:drawing><a:graphic><a:graphicData uri="urn:diagram"/></a:graphic></w:drawing></w:r></w:p>'
    result=reading.read_docx(doc(body,extras));assert [b['text'] for b in result['blocks']]==['正文']
    gaps={g['kind']:g['count'] for g in result['coverage']['gaps']}
    assert gaps['non_body_parts']==2 and gaps['embedded_visual_semantics']>=1
    assert result['coverage']['counts']['non_body_text_characters']==10
    assert result['full_report_confirmed'] is False


def test_compatibility_branches_are_retained_and_clearly_unresolved():
    body='<mc:AlternateContent><mc:Choice Requires="w">'+p('选择表示')+'</mc:Choice><mc:Fallback>'+p('回退表示')+'</mc:Fallback></mc:AlternateContent>'
    result=reading.read_docx(doc(body));assert [b['text'] for b in result['blocks']]==['选择表示','回退表示']
    assert [b['location']['compatibility_branch'] for b in result['blocks']]==['choice','fallback']
    assert any(g['kind']=='compatibility_display_branch_unresolved' for g in result['coverage']['gaps'])


def test_tabs_and_breaks_preserve_word_boundaries():
    body='<w:p><w:r><w:t>甲</w:t><w:tab/><w:t>乙</w:t><w:br/><w:t>丙</w:t><w:cr/><w:t>丁</w:t></w:r></w:p>'
    assert reading.read_docx(doc(body))['blocks'][0]['text']=='甲\t乙\n丙\n丁'


@pytest.mark.parametrize('body',[ '<w:t>段落外未知文字</w:t>', '<w:p><w:r><w:t>混合<w:t>嵌套</w:t></w:t></w:r></w:p>'])
def test_unbounded_or_malformed_text_ownership_fails_closed(body):
    with pytest.raises(ParseProblem):reading.read_docx(doc(body))


@pytest.mark.parametrize('body',['<w:altChunk r:id="r2"/>','<w:p><w:commentRangeStart w:id="1"/></w:p>','<w:p><w:moveFromRangeStart w:id="1"/></w:p>'])
def test_old_normalizer_unsafe_structures_still_fail_closed(body):
    with pytest.raises(ParseProblem):reading.read_docx(doc(body))


def test_limits_reject_whole_read_instead_of_silently_truncating(monkeypatch):
    monkeypatch.setattr(reading,'MAX_BLOCKS',1)
    with pytest.raises(ParseProblem):reading.read_docx(doc(p('第一')+p('第二')))
    monkeypatch.setattr(reading,'MAX_BLOCKS',12000);monkeypatch.setattr(reading,'MAX_CHARACTERS',2)
    with pytest.raises(ParseProblem):reading.read_docx(doc(p('三个字')))


def test_source_bytes_immutable_deterministic_json_and_no_empty_body_success():
    content=doc(p('原件不写🌲'));old=bytes(content);a=reading.read_docx(content);b=reading.read_docx(content)
    assert content==old and a==b and json.loads(json.dumps(a,ensure_ascii=False))==a
    assert len(a['source_sha256'])==len(a['normalized_sha256'])==64
    with pytest.raises(ParseProblem):reading.read_docx(doc(p('  ')))


def test_drawingml_text_is_counted_as_an_unread_semantic_gap():
    body=p('真正正文')+'<w:p><w:r><w:drawing><a:p><a:r><a:t>绘图说明未读</a:t></a:r></a:p></w:drawing></w:r></w:p>'
    result=reading.read_docx(doc(body))
    assert [b['text'] for b in result['blocks']]==['真正正文']
    assert result['coverage']['counts']['unread_drawing_text_characters']==6
    assert {'kind':'drawing_text_outside_word_paragraphs','count':1} in result['coverage']['gaps']


def test_surviving_ambiguous_deleted_text_is_not_silently_dropped():
    body=p('正文')+'<w:p><w:r><w:delText>无法判定的删除文字</w:delText></w:r></w:p>'
    with pytest.raises(ParseProblem):reading.read_docx(doc(body))


def test_dynamic_fields_keep_cached_text_with_explicit_warning():
    body='<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText>DATE</w:instrText><w:fldChar w:fldCharType="separate"/><w:t>旧缓存日期</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    result=reading.read_docx(doc(body))
    assert [b['text'] for b in result['blocks']]==['旧缓存日期']
    assert result['coverage']['policy_changes']['frozen_field_markers']==4
    assert any('缓存' in text and '未更新' in text for text in result['warnings'])
