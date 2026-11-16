"""Root-run synthetic PG source authority, not formal design approval.

Only conversion/render/OCR IO and NativeHTTP are synthetic. Real upload,
bounded current-run jobs, every saved OCR revision, whole-document confirmation,
manual5/full handoff and authenticated source-options use production paths.
The author does not execute this file or access any real report/paid answer.
"""
import copy
import hashlib
import io
import zipfile
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import document_copy_runtime, material_ocr_runtime
from generic_agents import report_full_semantic_region_source as authority
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Job, MaterialOcrUnit as Unit, Membership, Workspace
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests import test_material_ocr as ocr_test
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import fence, read_db
from tests.test_report_full_reading_source_pure import word_bytes
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene, BASE
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows
from tests.test_report_full_semantic_source_review_postgres import (
    complete_source_review_seed, command as review_command,
)
from tests.test_report_full_semantic_handoff_security_postgres import authenticated
from tests.test_report_full_semantic_replacement_postgres import evidence_snapshot, assert_evidence


WORD_TEXTS = ('人工先响应🙂。', '核验结论尚未确定。',
    '联络 synthetic@example.invalid；核对资料关联核对任务。')
OPTIONS = '/api/setup/full-semantic-region-mappings/'


def synthetic_word():
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(word_bytes())) as before, zipfile.ZipFile(buffer, 'w') as after:
        for name in before.namelist():
            content = before.read(name)
            if name == 'word/document.xml':
                content = content.replace(b'</w:body>', (
                    '<w:p><w:r><w:t>' + WORD_TEXTS[2] + '</w:t></w:r></w:p></w:body>').encode())
            after.writestr(name, content)
    return buffer.getvalue()


def drain_current_run(client, material, started, page_count):
    """Bounded prepare/recognize pairs; never drain another run or all Jobs."""
    run_id = started['view']['run']['id']
    root = reply(client.get(ocr_test.prefix(material)))
    for page in range(1, page_count + 1):
        assert root['run']['id'] == run_id
        assert root['run']['progress']['total'] == page_count
        assert root['run']['progress']['prepared'] == page - 1
        job_id = root['run']['job_id']
        with Session(engine()) as db:
            job = db.get(Job, job_id)
            assert job.kind == 'prepare_material_ocr' and job.status == 'queued'
            assert job.data['run_id'] == run_id and job.data['material_id'] == material['id']
            workspace = job.workspace_id
        assert process_once(job_id)
        with Session(engine()) as db:
            jobs = list(db.scalars(select(Job).where(Job.workspace_id == workspace,
                Job.kind == 'recognize_material_ocr_unit', Job.status == 'queued',
                Job.data['run_id'].as_string() == run_id,
                Job.data['material_id'].as_string() == material['id'])))
            assert len(jobs) == 1
            unit = db.get(Unit, jobs[0].data['unit_id'])
            assert unit.run_id == run_id and unit.material_id == material['id']
            assert unit.workspace_id == workspace and unit.unit_index == page
            recognize_job_id = jobs[0].id
        assert process_once(recognize_job_id)
        root = reply(client.get(ocr_test.prefix(material)))
        assert root['run']['progress']['prepared'] == page
        assert root['run']['progress']['review'] == page
    units, cursor = [], None
    for _ in range((page_count + 99) // 100):
        params = {'run_id': run_id, 'limit': 100}
        if cursor is not None:
            params['after'] = cursor
        listing = reply(client.get(ocr_test.prefix(material) + '/units', params=params))
        assert listing['run_id'] == run_id and listing['total'] == page_count
        units.extend(listing['items']); cursor = listing['next_cursor']
    assert cursor is None and len(units) == page_count
    assert [unit['index'] for unit in units] == list(range(1, page_count + 1))
    assert len({unit['id'] for unit in units}) == page_count
    assert all(unit['status'] == 'review' and unit['raw_revision'] for unit in units)
    return root, units


@pytest.fixture
def full_source_scene(request, client, monkeypatch, local_runtime):
    fence()
    page_count = getattr(request, 'param', 2)
    assert type(page_count) is int and page_count in (2, 235)
    word, pdf = synthetic_word(), ocr_test.scan_pdf(page_count)
    converter = digest({'synthetic-region-source-converter': page_count})
    monkeypatch.setattr(document_copy_runtime, 'engine_status', lambda: {
        'available': True, 'engine_id': 'libreoffice-docx-final-v1', 'manifest_hash': converter,
        'revision_policy': 'accepted_final_v1', 'reason': None, 'limitations': ['synthetic IO only']})

    def convert(value, revision_policy, expected_manifest_hash, guard=None):
        assert value == word and revision_policy == 'accepted_final_v1'
        assert expected_manifest_hash == converter
        if guard:
            guard()
        return {'content': pdf, 'receipt': {'source_sha256': hashlib.sha256(word).hexdigest(),
            'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'engine_id': 'libreoffice-docx-final-v1',
            'manifest_hash': converter, 'revision_policy': revision_policy, 'page_count': page_count,
            'warnings': ['synthetic converter, not real document/OCR quality']}}

    renderer = material_ocr_runtime.engine_status()
    image_pages = {}

    def render(content, page=None, guard=None):
        assert content == pdf and type(page) is int and 1 <= page <= page_count
        if guard:
            guard()
        image = ocr_test.png(160 + page, 200)
        image_pages[hashlib.sha256(image).hexdigest()] = page
        return [{'page': page, 'png': image, 'width': 160 + page, 'height': 200,
            'rotation': 0, 'dpi': 72, 'transform': [1, 0, 0, 1, 0, 0],
            'receipt': {'input_sha256': hashlib.sha256(content).hexdigest(),
                'output_sha256': hashlib.sha256(image).hexdigest(),
                'manifest_hash': renderer['manifest_hash'], 'summary_hash': digest(renderer['manifest']),
                'manifest': renderer['manifest'], 'test_only': True}}]

    def recognize(image, expected_manifest_hash, guard=None):
        assert expected_manifest_hash == renderer['manifest_hash']
        if guard:
            guard()
        page = image_pages[hashlib.sha256(image).hexdigest()]
        # Full literal page order, including headers and repeated quotes. The
        # email crosses adjacent regions; no test silently removes a page header.
        texts = (['先响应🙂', '；核验并行。', WORD_TEXTS[0], WORD_TEXTS[1],
            '联络 synthetic@exa', 'mple.invalid；核对资料关联核对任务。', WORD_TEXTS[0]]
            if page == 1 else ['附页逐字保留。', WORD_TEXTS[0], f'第{page}页完整合成文字。'])
        regions = [{'id': 'r' + str(number), 'text': text, 'box': [1, 2 + number * 16, 140, 10],
            'score': .9} for number, text in enumerate(texts, 1)]
        return {'regions': regions, 'elapsed_ms': 1, 'receipt': {
            'input_sha256': hashlib.sha256(image).hexdigest(), 'manifest_hash': expected_manifest_hash,
            'summary_hash': digest(renderer['manifest']), 'manifest': renderer['manifest'],
            'result_sha256': digest(regions), 'test_only': True}}

    monkeypatch.setattr(document_copy_runtime, 'convert', convert)
    monkeypatch.setattr(material_ocr_runtime, 'render_pdf', render)
    monkeypatch.setattr(material_ocr_runtime, 'recognize', recognize)
    original = ocr_test.upload(client, word, 'synthetic-region-source.docx')
    path = '/api/setup/materials/' + original['id'] + '/document-copy'
    queued = reply(client.post(path, json={'request_id': str(uuid4()),
        'expected_material_version': original['version'], 'source_sha256': original['hash'],
        'engine_manifest_hash': converter, 'revision_policy': 'accepted_final_v1'}))
    assert process_once(queued['job_id'])
    conversion = reply(client.get(path))['items'][0]
    copied = reply(client.get('/api/setup/materials/' + conversion['output_material_id']))
    _, started = ocr_test.start(client, copied)
    overview, units = drain_current_run(client, copied, started, page_count)
    me = reply(client.get('/api/me'))
    return {'client': client, 'p': Principal(me['user_id'], me['workspace_id'], me['role'], ''),
        'original': original, 'copy': copied, 'run_id': overview['run']['id'],
        'units': units, 'page_count': page_count}


@pytest.fixture
def source_scene2(v7_scene, monkeypatch):
    return complete_source_review_seed(v7_scene, monkeypatch)


def options(seed):
    response = reply(seed['client'].get(OPTIONS + seed['record']['id'] + '/source-options'))
    assert response['options_hash'] == digest({k: v for k, v in response.items() if k != 'options_hash'})
    assert response['active'] is True
    assert response['can_confirm'] is response['can_publish'] is False
    assert len(response['copies']) == 1
    return response['copies'][0]


def chain(seed, selection, *, current=True, formal=False):
    with read_db() as db:
        result = authority.source_chain(db, seed['scene']['p'],
            seed['report']['preview']['source_snapshot'], selection, current=current, formal=formal)
        return {'origin': result[0].id, 'copy': result[1].id, 'run': result[2].id,
            'pages': copy.deepcopy(result[3]),
            'confirmation_id': result[4].id if result[4] is not None else None}


def rejected(seed, selection, *, current=True, formal=False):
    before = all_rows()
    with pytest.raises(HTTPException) as caught:
        chain(seed, selection, current=current, formal=formal)
    assert caught.value.status_code == 409
    assert all_rows() == before


def confirm_page(seed, index):
    scene = seed['scene']
    detail = ocr_test.detail(seed['client'], scene['copy'], scene['run_id'], scene['units'][index - 1]['id'])
    return ocr_test.confirm_page(seed['client'], scene['copy'], detail)


def reject_document_confirmation(seed):
    client, material = seed['client'], seed['scene']['copy']
    root = reply(client.get(ocr_test.prefix(material)))
    body = {'request_id': str(uuid4()), 'expected_material_version': root['material']['version'],
        'run_id': root['run']['id'], 'expected_run_version': root['run']['version'],
        'units_manifest_hash': root['run']['units_manifest_hash'], 'reason': '合成边界：尚缺最后一页'}
    before = all_rows()
    assert client.post(ocr_test.prefix(material) + '/confirm', json=body).status_code == 409
    assert all_rows() == before


def assert_frozen_reading(seed, original):
    stored = reply(seed['client'].get(BASE + '/' + seed['report']['id']))
    assert stored == original
    assert stored['preview']['source_snapshot']['coverage']['full_report_confirmed'] is False


def test_full_design_source_selection_raw_to_confirmed_and_adversarial_scope(source_scene2, account):
    seed = source_scene2
    paid = evidence_snapshot(seed['http'], seed['report'])
    frozen = reply(seed['client'].get(BASE + '/' + seed['report']['id']))
    raw_option = options(seed); raw = raw_option['selection']
    assert raw_option['page_count'] == 2 and raw_option['confirmed_pages'] == 0
    assert raw_option['whole_document_confirmed'] is False
    assert [page['revision_kind'] for page in raw['pages']] == ['raw', 'raw']
    original_raw = copy.deepcopy(raw)
    raw_chain = chain(seed, raw)
    assert [page['page'] for page in raw_chain['pages']] == [1, 2]
    text = ''.join(region['text'] for page in raw_chain['pages'] for region in page['regions'])
    assert all(value in text for value in WORD_TEXTS)
    assert text.count(WORD_TEXTS[0]) >= 3  # Duplicate text never supplies an implicit location.
    assert all(set(region) == {'id', 'text', 'box'} for page in raw_chain['pages'] for region in page['regions'])
    state = reply(seed['client'].get(seed['path']))
    assert {'global_claim', 'business', 'step', 'participant', 'interaction', 'condition',
        'coordination', 'object', 'field', 'relationship'} <= {owner['kind'] for owner in state['manifest']['owners']}
    assert any(ref['privacy_relation'] == 'privacy_projected'
        and ref['source_anchor']['contains_redaction'] for ref in state['manifest']['refs'])
    rejected(seed, raw, formal=True)
    for index in (1, 2):
        confirm_page(seed, index)
    pages_only = options(seed)
    assert pages_only['confirmed_pages'] == 2 and not pages_only['whole_document_confirmed']
    rejected(seed, pages_only['selection'], formal=True)
    ocr_test.confirm_document(seed['client'], seed['scene']['copy'])
    confirmed = options(seed)
    assert confirmed['whole_document_confirmed'] and confirmed['confirmed_pages'] == 2
    assert confirmed['selection']['copy_ref']['version'] > raw['copy_ref']['version']
    assert confirmed['selection']['selection_hash'] != raw['selection_hash']
    rejected(seed, raw)
    historical = chain(seed, raw, current=False)
    assert historical['pages'] == raw_chain['pages'] and historical['confirmation_id'] is None
    with pytest.raises(ValueError, match='explicitly current'):
        chain(seed, raw, current=False, formal=True)
    current = chain(seed, confirmed['selection'], formal=True)
    assert current['confirmation_id'] and all(page['revision_kind'] == 'confirmed' for page in current['pages'])
    assert [page['revision_id'] for page in current['pages']] == [page['revision_id'] for page in confirmed['selection']['pages']]

    # Re-signing the public selection cannot create original/copy/region authority.
    attacks = []
    changed = copy.deepcopy(confirmed['selection']); changed['anchor']['source_ref']['version'] += 1; attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['anchor']['provenance_hash'] = '0' * 64; attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['pages'][0]['revision_id'] = str(uuid4()); attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['pages'][0]['revision_hash'] = '0' * 64; attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['pages'][0]['image_sha256'] = '0' * 64; attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['pages'][0]['unit_id'] = str(uuid4()); attacks.append(changed)
    changed = copy.deepcopy(confirmed['selection']); changed['copy_ref']['hash'] = changed['anchor']['copy_sha256'] = '0' * 64; attacks.append(changed)
    for attack in attacks:
        attack['selection_hash'] = digest({k: v for k, v in attack.items() if k != 'selection_hash'})
        rejected(seed, attack, formal=True)
    assert raw == original_raw
    assert_frozen_reading(seed, frozen)
    assert_evidence(seed['http'], seed['report'], paid)

    # Same authenticated user in another real workspace has no source authority.
    with Session(engine()) as db:
        workspace = Workspace(name='Synthetic isolated region-source workspace'); db.add(workspace); db.flush()
        workspace_id = workspace.id
        db.add(Membership(user_id=seed['scene']['p'].user_id, workspace_id=workspace_id, role='operator')); db.commit()
    with authenticated(seed['client'].app, account, workspace_id, 'operator') as other:
        reply(other.get('/api/setup/draft'))
        before = all_rows(); checkpoint = evidence_snapshot(seed['http'], seed['report'])
        assert other.get(OPTIONS + seed['record']['id'] + '/source-options').status_code == 404
        assert all_rows() == before
        assert_evidence(seed['http'], seed['report'], checkpoint)


@pytest.mark.parametrize('full_source_scene', [235], indirect=True)
def test_235_page_source_requires_last_page_and_document_confirmation(source_scene2, full_source_scene):
    seed = source_scene2
    assert seed['scene']['page_count'] == full_source_scene['page_count'] == 235
    paid = evidence_snapshot(seed['http'], seed['report'])
    frozen = reply(seed['client'].get(BASE + '/' + seed['report']['id']))
    initial = options(seed); raw = copy.deepcopy(initial['selection'])
    assert initial['page_count'] == len(raw['pages']) == 235
    assert [page['page'] for page in raw['pages']] == list(range(1, 236))
    assert all(page['revision_kind'] == 'raw' for page in raw['pages'])
    rejected(seed, raw, formal=True)
    raw_pages = chain(seed, raw)['pages']
    assert raw_pages[-1]['regions'][-1]['text'] == '第235页完整合成文字。'
    confirm_page(seed, 1)
    first_only = options(seed)
    assert first_only['confirmed_pages'] == 1 and not first_only['whole_document_confirmed']
    rejected(seed, first_only['selection'], formal=True)

    # Human work can be saved and recovered while formal source trust is pending.
    state = reply(seed['client'].get(seed['path']))
    unknown = next(owner for owner in state['manifest']['owners'] if owner['evidence_status'] == 'unknown')
    pending = review_command(state, unknown, decision='pending', note='235页尚在核对；未知保持未知')
    saved = reply(seed['client'].post(seed['path'] + '/entries', json=pending))
    recovered = reply(seed['client'].get(seed['path'] + '/requests/' + pending['request_id']))
    assert recovered['found'] and recovered['current_matches'] and recovered['receipt'] == saved['receipt']
    assert recovered['entry']['value'] == pending['value']
    for page in range(2, 235):
        confirm_page(seed, page)
    before_last = options(seed)
    assert before_last['confirmed_pages'] == 234 and not before_last['whole_document_confirmed']
    assert before_last['selection']['pages'][-1]['revision_kind'] == 'raw'
    rejected(seed, before_last['selection'], formal=True)
    reject_document_confirmation(seed)
    confirm_page(seed, 235)
    all_pages = options(seed)
    assert all_pages['confirmed_pages'] == 235 and not all_pages['whole_document_confirmed']
    rejected(seed, all_pages['selection'], formal=True)
    ocr_test.confirm_document(seed['client'], seed['scene']['copy'])
    explicit = options(seed)
    assert explicit['whole_document_confirmed'] and explicit['confirmed_pages'] == 235
    assert explicit['selection']['copy_ref']['version'] > all_pages['selection']['copy_ref']['version']
    rejected(seed, all_pages['selection'])
    historical = chain(seed, raw, current=False)
    assert historical['pages'] == raw_pages and historical['confirmation_id'] is None
    final = chain(seed, explicit['selection'], formal=True)
    assert final['confirmation_id'] and len(final['pages']) == 235
    assert [page['page'] for page in final['pages']] == list(range(1, 236))
    assert all(page['revision_kind'] == 'confirmed' for page in final['pages'])
    assert_frozen_reading(seed, frozen)
    assert_evidence(seed['http'], seed['report'], paid)
