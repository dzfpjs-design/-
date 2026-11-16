"""Owned empty-PG source selection, genuine provenance and persisted OCR chain.

Root alone runs this file. Only local conversion/render/recognition IO is fake;
no provider, paid draft, raw response or private historical row is accessed.
The real upload, conversion Job, evidence, incremental OCR, saved versions and
human confirmation paths create this synthetic two-page report.
"""
import copy
import hashlib
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import document_copy_runtime, material_ocr_runtime
from generic_agents import report_full_reading_source as full
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Job, SetupMaterial, MaterialOcrUnit as Unit, MaterialOcrVersion as Version
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests import test_material_ocr as ocr_test
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source_pure import word_bytes


def result(response):
    assert response.status_code == 200, response.text
    return response.json()


def fence():
    marker = os.environ.get('GA_F11_DUAL_TESTS_OWNED', '')
    assert marker.isdigit()
    port = int(marker); url = engine().url
    legacy = port == 63131 or (56828 <= port <= 65028 or port in {65128, 65228, 65328, 65428, 65528}) and port % 100 == 28
    current_owned = (port == 53679 and os.environ.get('GA_V197_LEGACY_EXACT_DATABASE')
        == 'owned-physical-pg-v175-v197')
    assert legacy or current_owned
    assert url.host in {'127.0.0.1', 'localhost'} and url.port == port and url.database == 'ga_f11_dual_tests'
    if current_owned:
        # This opt-in permits the existing exclusive fixture on the already
        # owned physical cluster. A marker alone cannot select any other DB,
        # endpoint or unowned database, and never permits application data.
        with engine().connect() as connection:
            actual = connection.execute(text("SELECT current_database(),inet_server_port(),"
                "shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")).one()
        assert tuple(actual) == ('ga_f11_dual_tests', 53679, 'owned-disposable-f11-dual')
    # The root-owned fixture additionally checks the exclusive database comment.


@contextmanager
def read_db():
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                yield db


@pytest.fixture
def full_source_scene(client, monkeypatch, local_runtime):
    fence(); word = word_bytes(); pdf = ocr_test.scan_pdf(2)
    manifest = digest({'synthetic-converter': 'full-source-test'})
    monkeypatch.setattr(document_copy_runtime, 'engine_status', lambda: {
        'available': True, 'engine_id': 'libreoffice-docx-final-v1', 'manifest_hash': manifest,
        'revision_policy': 'accepted_final_v1', 'reason': None, 'limitations': ['synthetic IO only']})

    def convert(value, revision_policy, expected_manifest_hash, guard=None):
        assert value == word and revision_policy == 'accepted_final_v1' and expected_manifest_hash == manifest
        if guard: guard()
        return {'content': pdf, 'receipt': {'source_sha256': hashlib.sha256(word).hexdigest(),
            'pdf_sha256': hashlib.sha256(pdf).hexdigest(), 'engine_id': 'libreoffice-docx-final-v1',
            'manifest_hash': manifest, 'revision_policy': revision_policy, 'page_count': 2,
            'warnings': ['synthetic converter, no quality claim']}}

    renderer = material_ocr_runtime.engine_status()

    def render(content, page=None, guard=None):
        assert content == pdf and page in (1, 2)
        if guard: guard()
        image = ocr_test.png(96 + page, 60)
        return [{'page': page, 'png': image, 'width': 96 + page, 'height': 60,
            'rotation': 0, 'dpi': 72, 'transform': [1, 0, 0, 1, 0, 0],
            'receipt': {'input_sha256': hashlib.sha256(content).hexdigest(),
                'output_sha256': hashlib.sha256(image).hexdigest(), 'manifest_hash': renderer['manifest_hash'],
                'summary_hash': digest(renderer['manifest']), 'manifest': renderer['manifest'], 'test_only': True}}]

    def recognize(image, expected_manifest_hash, guard=None):
        assert expected_manifest_hash == renderer['manifest_hash']
        if guard: guard()
        texts = ['先响应🙂', '；核验并行。'] if image == ocr_test.png(97, 60) else ['附页逐字保留。']
        regions = [{'id': 'r' + str(n), 'text': value, 'box': [1.0, 2 + n * 16, 70, 10], 'score': .9}
                   for n, value in enumerate(texts, 1)]
        return {'regions': regions, 'elapsed_ms': 1, 'receipt': {'input_sha256': hashlib.sha256(image).hexdigest(),
            'manifest_hash': expected_manifest_hash, 'summary_hash': digest(renderer['manifest']),
            'manifest': renderer['manifest'], 'result_sha256': digest(regions), 'test_only': True}}

    monkeypatch.setattr(document_copy_runtime, 'convert', convert)
    monkeypatch.setattr(material_ocr_runtime, 'render_pdf', render)
    monkeypatch.setattr(material_ocr_runtime, 'recognize', recognize)
    original = ocr_test.upload(client, word, 'synthetic-full-source.docx')
    path = '/api/setup/materials/' + original['id'] + '/document-copy'
    queued = result(client.post(path, json={'request_id': str(uuid4()), 'expected_material_version': original['version'],
        'source_sha256': original['hash'], 'engine_manifest_hash': manifest, 'revision_policy': 'accepted_final_v1'}))
    assert process_once(queued['job_id'])
    conversion = result(client.get(path))['items'][0]
    copied = result(client.get('/api/setup/materials/' + conversion['output_material_id']))
    _, start = ocr_test.start(client, copied)
    overview, units = ocr_test.finish_jobs(client, copied, start)
    me = result(client.get('/api/me'))
    principal = Principal(me['user_id'], me['workspace_id'], me['role'], '')
    return {'client': client, 'p': principal, 'original': original, 'copy': copied,
            'run_id': overview['run']['id'], 'units': units}


def options(scene):
    with read_db() as db:
        return full.full_source_options(db, scene['p'], {key: scene['original'][key] for key in ('id', 'version', 'hash')})


def freeze(scene, selection):
    with read_db() as db: return full.freeze_full_source(db, scene['p'], selection, lock=False)


def test_real_saved_ocr_proposal_is_exact_complete_read_only_and_no_formal_upgrade(full_source_scene):
    scene = full_source_scene; sql = []
    def observe(connection, cursor, statement, parameters, context, executemany): sql.append(statement)
    event.listen(engine(), 'before_cursor_execute', observe)
    try:
        choices = options(scene); selected = choices['copies'][0]['proposal']; snapshot = freeze(scene, selected)
        projection, index = full.project_full_source(snapshot)
    finally: event.remove(engine(), 'before_cursor_execute', observe)
    assert choices['options_hash'] == digest({k:v for k,v in choices.items() if k != 'options_hash'})
    assert choices['actor_id'] == scene['p'].user_id and choices['workspace_id'] == scene['p'].workspace_id
    assert choices['copies'][0]['proposal_hash'] == digest(selected)
    assert [page['page'] for page in selected['pages']] == [1, 2]
    assert snapshot['coverage']['versions_count'] == {'raw': 2, 'edited': 0, 'confirmed': 0}
    assert index['o:p1']['text'] == '先响应🙂；核验并行。' and index['o:p2']['text'] == '附页逐字保留。'
    assert projection['blocks'][-2][-1] == index['o:p1']['text']
    assert snapshot['pages'][0]['regions'][0]['box'][0] == '1.0'
    assert snapshot['coverage']['full_report_confirmed'] is snapshot['coverage']['can_apply'] is False
    assert not any('FOR UPDATE' in statement.upper() or 'FOR SHARE' in statement.upper() for statement in sql)
    assert all(statement.lstrip().upper().startswith(('SELECT', 'SET TRANSACTION')) for statement in sql)
    assert not any('cloud_calls' in statement or 'setup_report_drafts' in statement for statement in sql)


def test_exact_old_raw_can_be_selected_after_confirmation_without_automatic_promotion(full_source_scene):
    scene = full_source_scene; original_options = options(scene); old = original_options['copies'][0]['proposal']
    saved_snapshot = freeze(scene, old)
    current = ocr_test.detail(scene['client'], scene['copy'], scene['run_id'], scene['units'][0]['id'])
    confirmed = ocr_test.confirm_page(scene['client'], scene['copy'], current)
    with pytest.raises(HTTPException) as changed: freeze(scene, old)
    assert changed.value.status_code == 409
    now = options(scene); selected = copy.deepcopy(now['copies'][0]['proposal'])
    assert selected['pages'][0]['revision_kind'] == 'confirmed'
    raw = next(v for v in now['copies'][0]['pages'][0]['versions'] if v['revision_kind'] == 'raw')
    selected['pages'][0].update({key: raw[key] for key in ('revision_id', 'revision_hash', 'revision_kind')})
    snapshot = freeze(scene, selected)
    assert snapshot['pages'][0]['revision']['kind'] == 'raw'
    assert snapshot['pages'][0]['revision']['id'] != confirmed['confirmed']['id']
    assert full.check_full_source(saved_snapshot) == saved_snapshot
    assert snapshot['coverage']['versions_count']['confirmed'] == 0


@pytest.mark.parametrize('field', ['revision_hash', 'revision_kind', 'unit_version', 'run_version', 'generation', 'provenance_hash'])
def test_changed_or_mixed_selection_cannot_retarget_saved_source(full_source_scene, field):
    scene = full_source_scene; selected = copy.deepcopy(options(scene)['copies'][0]['proposal'])
    if field in {'run_version', 'generation'}: selected[field] += 1
    elif field == 'provenance_hash': selected[field] = '0' * 64
    elif field == 'revision_kind': selected['pages'][0][field] = 'confirmed'
    elif field == 'revision_hash': selected['pages'][0][field] = '0' * 64
    else: selected['pages'][0][field] += 1
    with pytest.raises(HTTPException) as error: freeze(scene, selected)
    assert error.value.status_code == 409


def test_raw_chain_and_true_conversion_provenance_corruption_are_not_just_public_hash_checks(full_source_scene):
    scene = full_source_scene; selection = options(scene)['copies'][0]['proposal']
    with Session(engine()) as db:
        raw = db.get(Version, selection['pages'][0]['revision_id'])
        raw.data = {**raw.data, 'regions': [{**raw.data['regions'][0], 'text': 'tampered'}]}
        # Even a recomputed public Version hash must retain true receipt/result identity.
        raw.content_hash = full.ocr.version_hash(raw)
        unit = db.get(Unit, raw.unit_id); unit.data = {**unit.data, 'last_hash': raw.content_hash}; full.ocr.seal(unit)
        db.commit()
    with pytest.raises(HTTPException): options(scene)


def test_cross_workspace_ref_and_archive_are_denied_and_saved_snapshot_remains_readable(full_source_scene):
    scene = full_source_scene; selected = options(scene)['copies'][0]['proposal']; snapshot = freeze(scene, selected)
    foreign = Principal(scene['p'].user_id, str(uuid4()), scene['p'].role, '')
    with read_db() as db, pytest.raises(HTTPException) as error:
        full.freeze_full_source(db, foreign, selected, lock=False)
    assert error.value.status_code == 404
    with Session(engine()) as db:
        row = db.get(SetupMaterial, scene['copy']['id']); row.archived = True; db.commit()
    with pytest.raises(HTTPException): freeze(scene, selected)
    assert full.check_full_source(snapshot) == snapshot


def test_missing_page_blocks_complete_view_even_if_existing_pages_have_valid_raw(full_source_scene):
    scene = full_source_scene
    with Session(engine()) as db:
        unit = db.get(Unit, scene['units'][1]['id'])
        # Explicit source corruption stays visible; no suffix is silently omitted.
        unit.unit_index = 3; full.ocr.seal(unit); db.commit()
    with pytest.raises(HTTPException) as error: options(scene)
    assert error.value.status_code == 409


def test_actual_locking_freeze_keeps_caller_transaction_and_does_not_commit(full_source_scene):
    scene = full_source_scene; selection = options(scene)['copies'][0]['proposal']
    with Session(engine()) as db:
        value = full.freeze_full_source(db, scene['p'], selection)
        assert db.in_transaction() and not db.new and not db.dirty and not db.deleted
        assert value['selection'] == selection
        db.rollback()
