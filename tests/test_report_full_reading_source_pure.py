"""Exact private full-source contract, synthetic documents, no IO services.

Root executes this file from an empty cwd with DB/Redis/keys/budget disabled.
No source selection silently promotes raw text or drops page/region evidence.
"""
import copy
import hashlib
import io
import socket
import subprocess
import zipfile
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents.contracts import digest
from generic_agents import report_full_reading_source as full


def identity(number):
    return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))


def word_bytes():
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>人工先响应🙂。</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>核验结论尚未确定。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return result.getvalue()


def seal(value, field):
    return {**value, field: digest(value)}


def snapshot(pages=2):
    """Transparent synthetic snapshot; not a fabricated formal OCR receipt."""
    word = word_bytes(); word_hash = hashlib.sha256(word).hexdigest(); pdf_hash = 'b' * 64
    reading = full.read_docx(word)
    original_ref = {'id': identity(1), 'version': 1, 'hash': word_hash}
    copy_ref = {'id': identity(2), 'version': 3, 'hash': pdf_hash}
    receipt = {'source_sha256': word_hash, 'pdf_sha256': pdf_hash, 'engine_id': 'libreoffice-docx-final-v1',
               'manifest_hash': 'c' * 64, 'revision_policy': 'accepted_final_v1', 'page_count': pages, 'warnings': []}
    provenance = seal({'contract_version': 1, 'copy_material_id': identity(2), 'workspace_id': identity(3),
        'copy_evidence_id': identity(4), 'copy_name': 'same-final.pdf', 'source_material_id': identity(1),
        'source_sha256': word_hash, 'source_material_version': 1, 'source_name': 'same.docx',
        'source_evidence_id': identity(5), 'request_id': identity(6), 'job_id': identity(7),
        'request_body_hash': 'd' * 64, 'revision_policy': 'accepted_final_v1',
        'engine_id': 'libreoffice-docx-final-v1', 'engine_manifest_hash': 'c' * 64,
        'pdf_sha256': pdf_hash, 'receipt': receipt}, 'integrity_hash')
    coverage = seal({'contract_version': 2, 'source_sha256': pdf_hash, 'status': 'needs_ocr',
        'unit_count': pages, 'units': [{'index': n, 'kind': 'pdf_page', 'page': n, 'body_index': None,
            'has_native_text': True, 'has_image': False, 'has_annotations': False} for n in range(1, pages + 1)],
        'gaps': [], 'reason': None, 'profile': {'kind': 'document_copy_full_pages_v1',
            'provenance_hash': provenance['integrity_hash']}, 'force_all_pages': True}, 'hash')
    choices, entries = [], []
    for page in range(1, pages + 1):
        kind = 'confirmed' if page == 2 else 'raw'
        unit_id, revision_id = identity(100 + page), identity(1000 + page)
        choices.append({'page': page, 'unit_id': unit_id, 'unit_version': 3 if page == 2 else 1,
                        'revision_id': revision_id, 'revision_hash': digest(['synthetic', page]), 'revision_kind': kind})
        regions = [{'id': 'r1', 'text': '先响应🙂', 'box': ['1.0', '2', '40.5', '10'],
                    'score': '0.9', 'source_region_id': 'r1' if kind == 'confirmed' else None},
                   {'id': 'r2', 'text': '；核验并行。' if page == 1 else '第二页原文。', 'box': ['1', '15', '50', '10'],
                    'score': None, 'source_region_id': 'r2' if kind == 'confirmed' else None}]
        entries.append(seal({'page': page, 'unit_id': unit_id, 'unit_version': choices[-1]['unit_version'],
            'revision': {'id': revision_id, 'number': 3 if page == 2 else 1, 'kind': kind, 'hash': choices[-1]['revision_hash']},
            'image': {'evidence_id': identity(2000 + page), 'sha256': digest(['image', page]), 'width': 96, 'height': 60},
            'recognition_engine_hash': 'e' * 64, 'regions': regions}, 'page_hash'))
    selection = {'source_mode': full.MODE, 'original_ref': original_ref, 'copy_ref': copy_ref,
        'provenance_hash': provenance['integrity_hash'], 'run_id': identity(8), 'generation': 2, 'run_version': 5, 'pages': choices}
    return seal({'schema': full.SOURCE, 'source_mode': full.MODE, 'workspace_id': identity(3), 'selection': selection,
        'original': {'ref': original_ref, 'name': 'same.docx', 'evidence_id': identity(5),
            'evidence_sha256': word_hash, 'evidence_size': len(word), 'reading': reading},
        'copy': {'ref': copy_ref, 'name': 'same-final.pdf', 'evidence_id': identity(4), 'provenance': provenance,
            'run': {'id': identity(8), 'generation': 2, 'version': 5, 'status': 'review', 'coverage': coverage,
                    'renderer': {'manifest_hash': 'f' * 64, 'summary_hash': 'a' * 64}}},
        'pages': entries, 'coverage': full._coverage(reading, entries)}, 'snapshot_hash')


def rehash(value):
    value['snapshot_hash'] = digest({k:v for k,v in value.items() if k != 'snapshot_hash'})
    return value


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('pure source tests must not dispatch/connect')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def test_all_views_and_regions_are_literal_complete_and_not_formal_blocks():
    original = snapshot(235); frozen = full.check_full_source(original)
    projection, index = full.project_full_source(frozen)
    assert frozen == original and frozen is not original
    assert len(projection['blocks']) == 237 and len(index) == 237
    assert projection['blocks'][0][0] == 'w:p1' and projection['blocks'][-1][0] == 'o:p235'
    assert index['o:p1']['text'] == '先响应🙂；核验并行。'
    assert index['w:p1']['text'] == '人工先响应🙂。'
    assert 'page' not in index['w:p1']['location']
    regions = index['o:p1']['regions']
    assert [(r['start'], r['end']) for r in regions] == [(0, 4), (4, 10)]
    assert ''.join(index['o:p1']['text'][r['start']:r['end']] for r in regions) == index['o:p1']['text']
    assert regions[0]['box'][0] == '1.0' and regions[0]['score'] == '0.9'
    assert frozen['coverage']['versions_count'] == {'raw': 234, 'edited': 0, 'confirmed': 1}
    assert frozen['coverage']['can_apply'] is frozen['coverage']['can_publish'] is False
    assert frozen['coverage']['full_report_confirmed'] is False
    assert all('block_id' not in value for value in index.values())
    assert not any(key in str(frozen) for key in ('engine_result', 'raw_response', 'api_key'))


def test_source_snapshot_and_projection_do_not_mutate_each_other():
    value = snapshot(); checked = full.check_full_source(value); projection, index = full.project_full_source(checked)
    index['o:p1']['regions'][0]['text'] = 'changed locally'
    projection['coverage']['can_apply'] = True
    assert full.check_full_source(value) == checked
    assert checked['pages'][0]['regions'][0]['text'] == '先响应🙂'
    assert checked['coverage']['can_apply'] is False


@pytest.mark.parametrize('mutate', [
    lambda s: s['pages'].pop(),
    lambda s: s['pages'].reverse(),
    lambda s: s['pages'][0]['revision'].update(kind='confirmed'),
    lambda s: s['pages'][0]['image'].update(width=True),
    lambda s: s['pages'][0]['regions'][0].update(text='changed'),
    lambda s: s['pages'][0].update(engine_result={'regions': []}),
    lambda s: s['coverage'].update(full_report_confirmed=True),
    lambda s: s['coverage']['versions_count'].update(raw=0),
    lambda s: s['copy']['run'].update(generation=999),
    lambda s: s['copy']['provenance'].update(source_material_version=99),
    lambda s: s['original']['reading']['blocks'].append(copy.deepcopy(s['original']['reading']['blocks'][0])),
    lambda s: s['original']['reading'].update(can_apply=True),
])
def test_hash_resealed_but_inconsistent_or_unsupported_snapshot_fails_closed(mutate):
    value = snapshot(); mutate(value); rehash(value)
    with pytest.raises(HTTPException) as error: full.check_full_source(value)
    assert error.value.status_code == 409


@pytest.mark.parametrize('coordinate', [1.0, True, 'NaN', 'Infinity', '1e999', '0x10', '\x00'])
def test_public_coordinates_have_no_nonfinite_or_cross_language_number_ambiguity(coordinate):
    value = snapshot(); page = value['pages'][0]; page['regions'][0]['box'][0] = coordinate
    page['page_hash'] = digest({k:v for k,v in page.items() if k != 'page_hash'})
    with pytest.raises(HTTPException): full.check_full_source(rehash(value))


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(pages='latest'),
    lambda s: s['pages'][0].update(page=True),
    lambda s: s['pages'].reverse(),
    lambda s: s['pages'].append(copy.deepcopy(s['pages'][0])),
    lambda s: s['pages'][0].update(revision_kind='latest'),
    lambda s: s['pages'][0].update(revision_id='not-a-uuid'),
    lambda s: s['pages'][0].update(extra='silently accepted'),
    lambda s: s.update(copy_ref=copy.deepcopy(s['original_ref'])),
])
def test_selection_is_exact_closed_and_never_a_latest_or_partial_selector(mutate):
    selected = snapshot()['selection']; mutate(selected)
    with pytest.raises(ValidationError): full.FullSourceSelection.model_validate(selected)


def test_page_text_keeps_whitespace_and_duplicate_views_without_normalization():
    value = snapshot(); page = value['pages'][0]
    page['regions'][0]['text'] = '  ＋🙂\n'
    page['regions'][1]['text'] = '＋🙂 '
    page['page_hash'] = digest({k:v for k,v in page.items() if k != 'page_hash'})
    value['coverage'] = full._coverage(value['original']['reading'], value['pages']); rehash(value)
    projection, index = full.project_full_source(value)
    assert index['o:p1']['text'] == '  ＋🙂\n＋🙂 '
    assert projection['blocks'][2][-1] == '  ＋🙂\n＋🙂 '


def test_complete_snapshot_capacity_is_checked_after_hash_without_trimming(monkeypatch):
    value = snapshot()
    monkeypatch.setattr(full, 'MAX_SNAPSHOT_BYTES', 128)
    with pytest.raises(HTTPException) as error: full.check_full_source(value)
    assert error.value.status_code == 413
    assert value['pages'][0]['regions'][0]['text'] == '先响应🙂'
