"""Owned-PG quote drafts, using new synthetic data, never a real model result.

The root driver alone executes these tests in its empty isolated database.
Conversion/OCR/provider I/O are explicit local fakes.  Original uploads, Jobs,
provenance, coverage, revisions, real login, raw settlement, local review,
handoff and the source gates are production paths, not patched validators.
No old paid row, credential or real provider is consulted.
"""
import copy
import hashlib
import io
import json
import os
import zipfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job, Membership, SetupMaterial, SetupReportDraft
from generic_agents.report_quote_binding_models import QuoteBindingDraft
from generic_agents.worker import process_once
from tests import test_material_ocr as ocr_test
from tests.test_material_ocr import local_runtime
from tests.test_setup import config
from tests.test_vertical_slice import publish


QUOTE = '巡护🙂记录必须人工核验。'
PARTS = ('巡护🙂记录', '必须人工核验。')
ROLE = '引文核对责任人'


def reply(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def owned_database():
    """Explicit test-database fence; never accepts the normal application DB."""
    url = engine().url
    port = int(os.environ.get('GA_F11_DUAL_TESTS_OWNED', '0'))
    assert url.host in {'127.0.0.1', 'localhost'}
    assert url.database == 'ga_f11_dual_tests' and url.port == port
    assert 56828 <= port <= 60028 and port % 100 == 28


def opaque_state():
    """All 107 table row strings + files: no decoding of CloudCall payloads."""
    owned_database()
    with engine().connect() as connection:
        names = connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).all()
        assert 'setup_requirement_checks' in names
        assert len(set(names) - {'setup_requirement_checks', 'full_semantic_source_reviews',
            'full_semantic_region_sessions', 'full_semantic_region_entries', 'full_semantic_region_proofs',
            'full_semantic_schema_adoptions', 'full_semantic_runtime_adoptions',
            'full_semantic_runtime_revisions','full_semantic_graph_adoptions'}) == 106
        tables = {name: connection.scalars(text('SELECT row_to_json(t)::text FROM "' + name + '" t ORDER BY id')).all()
                  for name in names}
    storage = settings().storage_dir
    files = {str(path.relative_to(storage)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in storage.rglob('*') if path.is_file()}
    return tables, files


def new_operator(admin, stack, role='operator'):
    username, password = 'quote_' + uuid4().hex[:16], 'Synthetic-quote-' + uuid4().hex
    user = reply(admin.post('/api/users', json={'username': username, 'password': password, 'role': role}), 201)
    client = stack.enter_context(TestClient(app))
    login = reply(client.post('/api/login', json={'username': username, 'password': password}))
    client.headers['X-CSRF-Token'] = login['csrf_token']
    client.headers['X-Workspace-ID'] = reply(admin.get('/api/me'))['workspace_id']
    assert reply(client.get('/api/me'))['user_id'] == user['id']
    return client, user


def word_bytes():
    """Small synthetic DOCX with an image gap; not an excerpt of a real report."""
    value = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(ocr_test.image_docx())) as old, zipfile.ZipFile(value, 'w') as new:
        # The legacy OCR fixture intentionally needs only document.xml. The
        # accepted-final reader correctly requires the complete OPC package.
        # Supply genuine content types/root relationship, rather than patching
        # normalize_docx/read_docx or pretending a parse failure is a model input.
        new.writestr('[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        new.writestr('_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="office" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
        for name in old.namelist():
            content = old.read(name)
            if name == 'word/document.xml':
                content = content.replace('正文不代表图片已读取'.encode(), QUOTE.encode())
            new.writestr(name, content)
    return value.getvalue()


def refresh_options(scene):
    return reply(scene['client'].get(scene['base'] + '/source-region-options', params={'candidate_index': 0}))


def draft_body(options, *, revision=0, bindings=None, request_id=None):
    return {'request_id': request_id or str(uuid4()), 'expected_revision': revision,
        'anchor': {'handoff_id': options['handoff_id'], 'candidate_index': options['candidate_index'],
                   'copies': [item['anchor'] for item in options['copies']]},
        'value': {'configuration_revision': options['configuration']['revision'],
                  'configuration_hash': options['configuration']['hash'],
                  'fact_hash': options['configuration']['fact_hash'],
                  'mapping_basis_hash': options['configuration']['mapping_basis_hash'],
                  'copy_refs': [item['copy_ref'] for item in options['copies']],
                  'bindings': ([copy.deepcopy(item['candidates'][0]['binding']) for item in options['quotes']]
                               if bindings is None else copy.deepcopy(bindings))}}


def save_selection(scene, *, options=None, revision=0, identity=None, bindings=None):
    identity = identity or str(uuid4()); options = options or refresh_options(scene)
    body = draft_body(options, revision=revision, bindings=bindings)
    path = scene['base'] + '/quote-binding-drafts/' + identity
    return path, body, reply(scene['client'].put(path, json=body))


def confirm_copy(scene):
    client, material = scene['client'], scene['copy']
    overview = reply(client.get(ocr_test.prefix(material)))
    units = reply(client.get(ocr_test.prefix(material) + '/units', params={'run_id': overview['run']['id'], 'limit': 100}))['items']
    assert len(units) == scene['pages'] == 2
    for unit in units:
        current = ocr_test.detail(client, material, overview['run']['id'], unit['id'])
        ocr_test.confirm_page(client, material, current)
    ocr_test.confirm_document(client, material)
    final = reply(client.get('/api/setup/materials/' + material['id']))
    assert final['status'] == 'parsed' and final['ocr']['coverage_status'] == 'complete'
    return final


@pytest.fixture
def quote_scene(client, monkeypatch, local_runtime, request):
    """Real PG lifecycle; exactly one *synthetic* provider callback, zero HTTP.

    Only runtime I/O and this fixture's temporary budget are replaced.  The fake
    provider goes through reserve/on_reserved/on_response/settlement, so tests
    cannot bypass checked_call, saved_value, provenance or source approval.
    """
    from generic_agents import cloud_budget, document_copy_runtime, material_ocr_runtime
    from generic_agents import report_reading_drafts as rd
    owned_database()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    pages = getattr(request, 'param', 2)
    assert pages in {2, 235}
    with ExitStack() as stack:
        operator, user = new_operator(client, stack)
        other, other_user = new_operator(client, stack)
        me = reply(operator.get('/api/me'))
        original_bytes = word_bytes(); pdf = ocr_test.scan_pdf(pages)
        copy_manifest = digest({'test_only': 'synthetic-copy-adapter-v1'})
        monkeypatch.setattr(document_copy_runtime, 'engine_status', lambda: {
            'available': True, 'engine_id': 'libreoffice-docx-final-v1', 'manifest_hash': copy_manifest,
            'revision_policy': 'accepted_final_v1', 'reason': None, 'limitations': ['synthetic I/O, not a real conversion']})

        def convert(value, revision_policy, expected_manifest_hash, guard=None):
            assert value == original_bytes and revision_policy == 'accepted_final_v1'
            assert expected_manifest_hash == copy_manifest
            if guard: guard()
            return {'content': pdf, 'receipt': {'source_sha256': hashlib.sha256(value).hexdigest(),
                'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'engine_id': 'libreoffice-docx-final-v1',
                'manifest_hash': copy_manifest, 'revision_policy': revision_policy,
                'page_count': pages, 'warnings': ['synthetic converter; no quality claim']}}

        monkeypatch.setattr(document_copy_runtime, 'convert', convert)
        renderer = material_ocr_runtime.engine_status()
        def render(value, page=None, guard=None):
            assert value == pdf and type(page) is int and 1 <= page <= pages
            if guard: guard()
            image = ocr_test.png(96 + page, 60)
            return [{'page': page, 'png': image, 'width': 96 + page, 'height': 60,
                'rotation': 0, 'dpi': 72, 'transform': [1, 0, 0, 1, 0, 0],
                'receipt': {'input_sha256': hashlib.sha256(value).hexdigest(),
                    'output_sha256': hashlib.sha256(image).hexdigest(),
                    'manifest_hash': renderer['manifest_hash'], 'summary_hash': digest(renderer['manifest']),
                    'manifest': renderer['manifest'], 'test_only': True}}]

        def recognize(image, expected_manifest_hash, guard=None):
            assert expected_manifest_hash == renderer['manifest_hash']
            if guard: guard()
            texts = PARTS if image == ocr_test.png(97, 60) else ('此页为独立合成附页。',)
            regions = [{'id': 'r' + str(i + 1), 'text': value, 'box': [1, 2 + i * 22, 90, 20], 'score': .9}
                       for i, value in enumerate(texts)]
            return {'regions': regions, 'elapsed_ms': 1, 'receipt': {'input_sha256': hashlib.sha256(image).hexdigest(),
                'manifest_hash': expected_manifest_hash, 'result_sha256': digest(regions),
                'summary_hash': digest(renderer['manifest']), 'manifest': renderer['manifest'], 'test_only': True}}

        monkeypatch.setattr(material_ocr_runtime, 'render_pdf', render)
        monkeypatch.setattr(material_ocr_runtime, 'recognize', recognize)
        origin = ocr_test.upload(operator, original_bytes, 'synthetic-original.docx')
        assert origin['status'] == 'needs_input'
        copy_path = '/api/setup/materials/' + origin['id'] + '/document-copy'
        conversion = reply(operator.post(copy_path, json={'request_id': str(uuid4()),
            'expected_material_version': origin['version'], 'source_sha256': origin['hash'],
            'engine_manifest_hash': copy_manifest, 'revision_policy': 'accepted_final_v1'}))
        assert process_once(conversion['job_id'])
        copied = reply(operator.get(copy_path))['items'][0]
        assert copied['status'] == 'succeeded' and copied['output_material_id']
        material = reply(operator.get('/api/setup/materials/' + copied['output_material_id']))
        _, ocr_start = ocr_test.start(operator, material)
        if pages == 2:
            overview, units = ocr_test.finish_jobs(operator, material, ocr_start)
        else:
            # Actual 235-page coverage, but only page 1 prepared/recognized.  No
            # bulk confirmation or fake 235-page readiness is injected.
            assert process_once(ocr_start['view']['run']['job_id'])
            with Session(engine()) as db:
                jobs = list(db.scalars(select(Job.id).where(Job.kind == 'recognize_material_ocr_unit',
                    Job.data['run_id'].as_string() == ocr_start['view']['run']['id'])))
            assert len(jobs) == 1 and process_once(jobs[0])
            overview = reply(operator.get(ocr_test.prefix(material)))
            units = reply(operator.get(ocr_test.prefix(material) + '/units', params={'run_id': overview['run']['id']}))['items']
        revision = reply(operator.get('/api/setup/draft'))['revision']
        reply(operator.put('/api/setup/draft', json={'revision': revision, 'configuration': config([origin, material])}))
        cap = publish(client, {**json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text()),
            'name': 'synthetic_quote_provider', 'description': 'test-only fake callback; no provider HTTP'})
        snapshot = reply(operator.get('/api/setup/draft'))
        start_body = {'request_id': 'rd_' + str(uuid4()), 'revision': snapshot['revision'], 'model_id': cap['id'],
            'material_refs': [{'id': origin['id'], 'version': origin['version'], 'hash': origin['hash']}],
            'note': 'Synthetic test fixture only; not real report/model quality.'}
        # Environment/driver budget remain zero.  Only this empty-database test
        # temporarily opens one fake reservation; cloud_json itself is replaced.
        with monkeypatch.context() as fake:
            fake.setattr(settings(), 'cloud_daily_call_limit', 1)
            fake.setattr(settings(), 'cloud_daily_token_limit', 1000000)
            preview = reply(operator.post('/api/setup/report-drafts/preview', json=start_body))
            block = next(b for b in preview['source_snapshot']['materials'][0]['reading']['blocks'] if QUOTE in b['text'])
            citation = {'material_id': origin['id'], 'block_id': block['id'], 'quote': QUOTE}
            candidate = {'domain': '合成引文核对', 'objects': [], 'relationships': [], 'questions': ['合成测试，不证明模型质量。'],
                'businesses': [{'operation': 'upsert', 'original_name': '', 'name': '合成巡护记录核验',
                    'trigger': '人工登记记录', 'output': '人工核验记录', 'citations': [citation], 'inference_reason': '',
                    'steps': [{'key': 'check', 'name': '人工核对', 'purpose': '对照原文核对登记内容', 'agent': '人工确认',
                        'role': ROLE, 'output': '人工核验记录', 'next_key': 'end', 'condition': '', 'else_key': 'end',
                        'citations': [copy.deepcopy(citation)], 'inference_reason': ''}]}]}
            authorized = reply(operator.post('/api/setup/report-drafts', json={**start_body,
                'preview_hash': preview['preview_hash'], 'consent': True}), 202)
            draft_id = authorized['draft']['id']; calls = []

            def fake_cloud(capability, messages, schema, principal, **callbacks):
                assert not calls and callbacks['full_report'] is True
                calls.append('synthetic-local-provider')
                call_id = cloud_budget.reserve(principal, capability, preview['input_summary']['token_bound'],
                                               preview['first_dispatch_messages_hash'])
                with Session(engine()) as db:
                    row = db.get(CloudCall, call_id)
                    row.data = {**row.data, 'dispatch_options_hash': digest(preview['generation_contract']['dispatch_options']),
                        'input_policy_hash': digest(preview['generation_contract']['input_policy']), 'synthetic_test_only': True}
                    db.commit()
                callbacks['on_reserved'](call_id)
                receipt = {'call_id': call_id, 'provider': preview['provider'], 'model': preview['model'],
                    'usage': {'total_tokens': 20}, 'request_id': 'synthetic-only-' + str(uuid4())}
                callbacks['on_response'](json.dumps(candidate, ensure_ascii=False), receipt)
                return copy.deepcopy(candidate), receipt

            fake.setattr(rd, 'cloud_json', fake_cloud)
            assert process_once(authorized['draft']['job_id'])
            assert calls == ['synthetic-local-provider']
        generated = reply(operator.get('/api/setup/report-drafts/' + draft_id))
        assert generated['raw_response_saved'] is True and generated['model_result'] == candidate
        local = reply(operator.post('/api/setup/report-drafts/' + draft_id + '/local-review', json={
            'revision': generated['revision'], 'request_id': str(uuid4())}))
        saved = reply(operator.put('/api/setup/report-drafts/' + draft_id + '/review', json={
            'revision': local['draft']['revision'], 'request_id': str(uuid4()),
            'value': {'result': candidate, 'note': 'synthetic fixture human review, not original AI acceptance'}}))
        cfg = reply(operator.get('/api/setup/draft'))
        handoff_body = {'report_revision': saved['draft']['revision'], 'review_receipt_hash': saved['receipt']['receipt_hash'],
            'candidate_hash': digest(candidate), 'source_hash': preview['source_hash'],
            'validation_report_hash': saved['draft']['validation_report']['report_hash'],
            'configuration_revision': cfg['revision'], 'configuration_hash': digest(cfg['configuration']), 'business_indexes': [0]}
        hp = reply(operator.post('/api/setup/report-drafts/' + draft_id + '/configuration-preview', json=handoff_body))
        shared = reply(operator.post('/api/setup/report-drafts/' + draft_id + '/configuration-handoff', json={
            **handoff_body, 'request_id': str(uuid4()), 'preview_hash': hp['preview_hash'], 'consent': True}))
        scene = {'client': operator, 'admin': client, 'other': other, 'owner': user, 'other_user': other_user,
            'workspace_id': me['workspace_id'], 'origin': origin, 'copy': material, 'pages': pages, 'units': units,
            'run_id': overview['run']['id'], 'report_id': draft_id, 'handoff': shared['handoff'],
            'base': '/api/setup/report-handoffs/' + shared['handoff']['id']}
        options = refresh_options(scene)
        assert options['quotes'] and all(len(q['candidates']) == 1 for q in options['quotes'])
        assert all(len(q['candidates'][0]['binding']['spans']) == 2 for q in options['quotes'])
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 1
        yield scene


def test_private_raw_draft_has_real_receipt_history_no_approval_and_gets_write_nothing(quote_scene):
    scene = quote_scene; options = refresh_options(scene)
    assert options['copies'][0]['whole_document_confirmed'] is False
    path, body, saved = save_selection(scene, options=options)
    assert saved['draft']['revision'] == 1 and saved['current_matches'] is True
    assert saved['draft']['value'] == body['value'] and saved['draft']['can_apply'] is False
    assert saved['draft']['can_publish'] is False
    assert all(b['spans'][0]['revision_kind'] == 'raw' for b in body['value']['bindings'])
    before = opaque_state()
    read = reply(scene['client'].get(path)); assert read == saved['draft']
    recovered = reply(scene['client'].get(path + '/receipts/' + body['request_id']))
    assert recovered == saved
    listing = reply(scene['client'].get(scene['base'] + '/quote-binding-drafts', params={'candidate_index': 0}))
    assert listing['items'] == [read]
    assert reply(scene['client'].put(path, json=body)) == saved
    assert opaque_state() == before
    assert reply(scene['client'].get('/api/setup/draft'))['configuration']['flows']['合成巡护记录核验']['reportSource'].get('source_review_id') is None


def test_cas_nonce_variant_and_old_receipt_keep_both_accepted_values(quote_scene):
    scene = quote_scene
    path, first_body, first = save_selection(scene, bindings=[])
    second_body = draft_body(refresh_options(scene), revision=1)
    second = reply(scene['client'].put(path, json=second_body))
    assert second['draft']['revision'] == 2 and len(second['draft']['history']) == 2
    before = opaque_state()
    old = reply(scene['client'].get(path + '/receipts/' + first_body['request_id']))
    assert old['receipt'] == first['receipt'] and old['current_matches'] is False
    assert old['draft']['history'][0]['value'] == first_body['value']
    assert reply(scene['client'].put(path, json=first_body)) == old
    changed = copy.deepcopy(first_body); changed['value'] = second_body['value']
    reply(scene['client'].put(path, json=changed), 409)
    stale = {**second_body, 'request_id': str(uuid4()), 'expected_revision': 1}
    reply(scene['client'].put(path, json=stale), 409)
    reply(scene['client'].get(path + '/receipts/' + str(uuid4())), 404)
    assert opaque_state() == before


def test_foreign_operator_and_admin_cannot_read_or_overwrite_owner_private_selection(quote_scene):
    scene = quote_scene; path, body, _ = save_selection(scene)
    before = opaque_state()
    for foreign in (scene['other'], scene['admin']):
        reply(foreign.get(path), 404)
        reply(foreign.get(path + '/receipts/' + body['request_id']), 404)
        reply(foreign.put(path, json=body), 404)
        items = reply(foreign.get(scene['base'] + '/quote-binding-drafts', params={'candidate_index': 0}))['items']
        assert items == []
    assert opaque_state() == before


def test_old_raw_revision_never_promotes_silently_after_real_page_confirmation(quote_scene):
    scene = quote_scene; path, body, first = save_selection(scene)
    confirm_copy(scene)
    stale = reply(scene['client'].get(path))
    assert stale['value'] == body['value'] and stale['history'] == first['draft']['history']
    assert stale['current']['matches'] is False
    replay = reply(scene['client'].put(path, json=body))
    assert replay['receipt'] == first['receipt'] and replay['draft']['current']['matches'] is False
    fresh = draft_body(refresh_options(scene), revision=1)
    assert all(b['spans'][0]['revision_kind'] == 'confirmed' for b in fresh['value']['bindings'])
    changed = reply(scene['client'].put(path, json=fresh))
    assert changed['draft']['revision'] == 2 and changed['draft']['history'][0] == first['draft']['history'][0]
    assert changed['draft']['current']['matches'] is True


def test_replay_survives_owner_role_loss_but_new_nonce_cannot_write(quote_scene):
    scene = quote_scene; path, body, saved = save_selection(scene)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == scene['owner']['id'], Membership.workspace_id == scene['workspace_id']))
        member.role = 'viewer'; db.commit()
    before = opaque_state()
    recovered = reply(scene['client'].put(path, json=body))
    assert recovered['receipt'] == saved['receipt'] and recovered['draft']['can_save'] is False
    newer = {**body, 'request_id': str(uuid4()), 'expected_revision': 1}
    reply(scene['client'].put(path, json=newer), 403)
    assert opaque_state() == before


def test_state_capacity_rejects_whole_write_and_preserves_prior_history(quote_scene, monkeypatch):
    from generic_agents import report_source_region_binding as bindings
    scene = quote_scene; path, _, saved = save_selection(scene, bindings=[])
    body = draft_body(refresh_options(scene), revision=1)
    before = opaque_state()
    # Small local limit exercises the same whole-row gate without manufacturing
    # megabytes of fake history or removing any accepted entry.
    with Session(engine()) as db:
        size = len(json.dumps(db.get(QuoteBindingDraft, saved['draft']['id']).data, ensure_ascii=False).encode())
    monkeypatch.setattr(bindings, 'MAX_STATE_BYTES', size + 1)
    reply(scene['client'].put(path, json=body), 413)
    assert opaque_state() == before


@pytest.mark.parametrize('damage', ['state_hash', 'history_value', 'command', 'receipt'])
def test_jsonb_saved_state_damage_is_not_repaired_or_exposed_as_empty(quote_scene, damage):
    scene = quote_scene; path, body, saved = save_selection(scene)
    with Session(engine()) as db:
        row = db.get(QuoteBindingDraft, saved['draft']['id']); value = copy.deepcopy(row.data)
        if damage == 'state_hash': value['state_hash'] = '0' * 64
        elif damage == 'history_value': value['history'][0]['value']['bindings'] = []
        elif damage == 'command': value['history'][0]['command']['request_id'] = str(uuid4())
        else: value['history'][0]['receipt']['accepted_revision'] = 3
        row.data = value; db.commit()
    before = opaque_state()
    reply(scene['client'].get(path), 409)
    reply(scene['client'].get(path + '/receipts/' + body['request_id']), 409)
    reply(scene['client'].get(scene['base'] + '/quote-binding-drafts', params={'candidate_index': 0}), 409)
    assert opaque_state() == before


@pytest.mark.parametrize('quote_scene', [235], indirect=True)
def test_235_page_unconfirmed_selection_saves_without_granting_formal_source(quote_scene):
    from tests.test_report_region_projection import formal_body
    scene = quote_scene; options = refresh_options(scene)
    assert options['copies'][0]['page_count'] == 235 and options['copies'][0]['prepared_pages'] == 1
    path, body, saved = save_selection(scene, options=options)
    before = opaque_state()
    response = scene['client'].post(scene['base'] + '/region-source-review', json=formal_body(scene, options, saved))
    result = reply(response, 409)
    assert result['detail']['code'] == 'projection_coverage_pending'
    assert reply(scene['client'].get(path))['value'] == body['value']
    assert opaque_state() == before
