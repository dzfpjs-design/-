"""Actual fixed coverage child and parent-owned cancellation/bounds; no DB or OCR mock success."""
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from generic_agents.document_parsers import ParseProblem
from generic_agents import runtime_document_ocr_coverage as coverage


def native_then_blank():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 40 780 Td (OCR206 original native page.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_blank_page(width=595, height=842)
    out = BytesIO(); writer.write(out); return out.getvalue()


def test_real_fixed_child_distinguishes_native_and_no_text_without_recognition():
    value = coverage.examine_pdf(native_then_blank())
    assert value['page_count'] == 2
    assert [page['page_number'] for page in value['pages']] == [1, 2]
    assert [page['has_native_text'] for page in value['pages']] == [True, False]
    assert all(not page['has_image'] for page in value['pages'])


@pytest.mark.parametrize('raw', [b'', 'not bytes', bytearray(b'%PDF'), b'0' * (20 * 1024 * 1024 + 1)])
def test_invalid_or_oversized_original_rejected_before_spawn(raw, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('invalid original spawned a child')
    monkeypatch.setattr(coverage.subprocess, 'Popen', forbidden)
    with pytest.raises(ParseProblem): coverage.examine_pdf(raw)


def test_damaged_original_is_not_a_fabricated_empty_page_plan():
    with pytest.raises(ParseProblem): coverage.examine_pdf(b'%PDF-1.7\nnot-a-page-tree')


def test_cancel_after_real_spawn_kills_reaps_and_cleans_owned_directory(monkeypatch):
    actual = coverage.subprocess.Popen; children = []
    def spawn(*args, **kwargs):
        process = actual(*args, **kwargs); children.append((process, Path(kwargs['cwd']))); return process
    monkeypatch.setattr(coverage.subprocess, 'Popen', spawn)
    class Cancelled(Exception): pass
    def guard():
        if children: raise Cancelled('current actor cancelled original run')
    with pytest.raises(Cancelled): coverage.examine_pdf(native_then_blank(), guard=guard)
    assert len(children) == 1 and children[0][0].poll() is not None and not children[0][1].exists()


def test_wall_budget_stops_real_child_and_cleans(monkeypatch):
    actual = coverage.subprocess.Popen; children = []
    def spawn(*args, **kwargs):
        process = actual(*args, **kwargs); children.append((process, Path(kwargs['cwd']))); return process
    clock = iter([0, 61])
    monkeypatch.setattr(coverage.subprocess, 'Popen', spawn)
    monkeypatch.setattr(coverage.time, 'monotonic', lambda: next(clock, 61))
    with pytest.raises(ParseProblem, match='60秒'): coverage.examine_pdf(native_then_blank())
    assert children[0][0].poll() is not None and not children[0][1].exists()


@pytest.mark.parametrize('payload', [
    '{"page_count":1,"page_count":2,"pages":[],"warnings":[]}',
    '{"page_count":0,"pages":[],"warnings":[]}',
    '{"page_count":1,"pages":[{"page_number":true,"has_native_text":false,"has_image":false,"has_annotations":false}],"warnings":[]}',
    '{"page_count":1,"pages":[{"page_number":1,"has_native_text":"yes","has_image":false,"has_annotations":false}],"warnings":[]}',
])
def test_invalid_child_coverage_is_not_a_successful_plan(payload, monkeypatch):
    monkeypatch.setattr(coverage, '_BOOTSTRAP', 'import sys;sys.stdout.write(' + repr(payload) + ')')
    with pytest.raises(ParseProblem): coverage.examine_pdf(native_then_blank())
