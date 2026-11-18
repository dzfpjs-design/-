"""Synthetic DOCX fixtures only; existing accepted-final v1 remains immutable."""
import copy
import pytest
from generic_agents import native_report_reading as reading
from generic_agents.contracts import digest
from generic_agents.document_parsers import ParseProblem
from generic_agents.report_text_parse_scope import report_text_parse_scope
from tests.test_native_report_reading_pure import doc, p


def test_v2_preserves_visible_and_discretionary_hyphens_without_mutating_v1():
    content = doc('<w:p><w:r><w:noBreakHyphen/><w:t>20℃</w:t><w:softHyphen/><w:t>+50℃</w:t></w:r></w:p>' + p('🌲'))
    with report_text_parse_scope():
        before = reading.read_docx(content)
        frozen = copy.deepcopy(before)
        new = reading.read_docx_visible_v2(content)
        assert reading.read_docx(content) == frozen
    assert before['blocks'][0]['text'] == '20℃+50℃'
    assert before['blocks'][0]['id'] == 'p1' and before['schema'] == 'report-body-draft-source-v1'
    assert new['blocks'][0]['text'] == '\u201120℃\u00ad+50℃'
    assert new['blocks'][0]['id'] == 'v2:p1' and new['schema'] == 'report-body-draft-source-v2'
    assert new['text_policy'] == 'preserve-ooxml-run-hyphens-v2'
    assert new['source_sha256'] == before['source_sha256']
    assert new['full_report_confirmed'] is False and new['can_apply'] is False
    assert digest(new) != digest(before)


def test_hyphen_only_paragraph_is_not_lost_or_mislabeled_as_an_old_block():
    content = doc('<w:p><w:r><w:noBreakHyphen/></w:r></w:p>' + p('后文'))
    old = reading.read_docx(content); new = reading.read_docx_visible_v2(content)
    assert [(x['id'], x['text'], x['location']['paragraph_index']) for x in old['blocks']] == [('p1', '后文', 2)]
    assert [(x['id'], x['text'], x['location']['paragraph_index']) for x in new['blocks']] == [
        ('v2:p1', '\u2011', 1), ('v2:p2', '后文', 2)]
    assert new['coverage']['counts']['paragraphs_empty'] == 0


def test_table_and_textbox_keep_hyphens_in_the_correct_paragraph():
    body = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>温度</w:t><w:noBreakHyphen/><w:t>20</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    body += '<w:p><w:r><w:t>外</w:t><w:drawing><w:txbxContent><w:p><w:r><w:t>框</w:t><w:softHyphen/><w:t>内</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>'
    result = reading.read_docx_visible_v2(doc(body))
    assert [(x['kind'], x['text']) for x in result['blocks']] == [
        ('table_paragraph', '温度\u201120'), ('paragraph', '外'), ('textbox_paragraph', '框\u00ad内')]
    assert result['blocks'][0]['location']['table_index'] == 1
    assert any(x['kind'] == 'textbox_layout_or_reading_order' for x in result['coverage']['gaps'])


def test_v2_rejects_unowned_hyphen_and_respects_limits(monkeypatch):
    with pytest.raises(ParseProblem): reading.read_docx_visible_v2(doc('<w:p><w:noBreakHyphen/></w:p>'))
    monkeypatch.setattr(reading, 'MAX_CHARACTERS', 1)
    with pytest.raises(ParseProblem): reading.read_docx_visible_v2(doc('<w:p><w:r><w:noBreakHyphen/><w:t>2</w:t></w:r></w:p>'))
