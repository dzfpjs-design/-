"""Synthetic PostgreSQL tests for same-document unresolved paid-read guards."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import CloudCall, Job, Membership, SetupAnalysis, SetupDraft, SetupMaterial, SetupReportDraft, User, Workspace
from generic_agents.report_unresolved_overlap import guard_new_paid_sources
from generic_agents.security import Principal, password_hash
from tests.test_material_ocr import local_runtime, upload
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_second_batch_preview import scene_with_candidate


@pytest.fixture(autouse=True)
def owned_disposable_database(monkeypatch):
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests, 'fence', fenced)
    monkeypatch.setattr(pipeline_tests, 'fence', fenced)
    if not inspect(engine()).has_table('report_full_provisional_candidates', schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


def refs(scene):
    return [{key: scene[name][key] for key in ('id', 'version', 'hash')} for name in ('original', 'copy')]


def expect_overlap(db, p, values):
    with pytest.raises(HTTPException) as denied:
        guard_new_paid_sources(db, p, values)
    assert denied.value.status_code == 409
    assert denied.value.detail['code'] == 'unresolved_report_source'


def test_word_and_verified_copy_block_but_distinct_material_and_other_workspace_do_not(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    other = upload(scene['client'], '完全不同的合成资料。'.encode(), 'different-synthetic.txt')
    other_ref = {key: other[key] for key in ('id', 'version', 'hash')}
    original, copied = refs(scene)
    with Session(engine()) as db:
        expect_overlap(db, scene['p'], [original]); db.rollback()
        expect_overlap(db, scene['p'], [copied]); db.rollback()
        guard_new_paid_sources(db, scene['p'], [other_ref]); db.rollback()
        guard_new_paid_sources(db, scene['p'], [original], linked_original_id=old['id']); db.rollback()
    response = scene['client'].post('/api/setup/understanding/preview', json={
        'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [other['id']], 'instruction': '', 'contract_version': 1})
    assert response.status_code == 200, response.text
    assert len(provider.calls) == 1


def test_standard_evidence_full_and_body_preview_do_not_bypass_same_source(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    for prefix, endpoint in [('fe_', '/api/setup/report-evidence-readings/preview'),
                             ('fr_', '/api/setup/report-full-readings/preview')]:
        body = copy.deepcopy(scene['body']); body['request_id'] = prefix + str(uuid4())
        response = scene['client'].post(endpoint, json=body)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'unresolved_report_source'
    body = {k: copy.deepcopy(scene['body'][k]) for k in ('revision', 'model_id', 'material_refs', 'note')}
    body['request_id'] = 'rd_' + str(uuid4())
    response = scene['client'].post('/api/setup/report-drafts/preview', json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'unresolved_report_source'
    understanding = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    response = scene['client'].post('/api/setup/understanding/preview', json=understanding)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'unresolved_report_source'
    assert len(provider.calls) == 1


def test_nonce_replay_of_old_record_remains_read_only(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    response = scene['client'].get('/api/setup/report-full-readings/by-request/' + old['request_id'])
    assert response.status_code == 200, response.text
    assert response.json()['draft']['id'] == old['id'] and len(provider.calls) == 1


def test_clarification_existing_preview_cannot_start_after_same_source_failure(reading_scene, monkeypatch):
    from generic_agents import material_ocr
    scene = reading_scene
    # A synthetic reviewed understanding supplies a real saved question draft;
    # no model is called to construct this prerequisite.
    question_result = {'questions': ['需要怎样处理？']}
    with Session(engine()) as db:
        parent = SetupAnalysis(workspace_id=scene['p'].workspace_id, status='review', data={
            'request': {'contract_version': 5, 'model_id': scene['body']['model_id'],
                        'materials': [refs(scene)[0]]},
            'result': question_result, 'result_hash': digest(question_result)})
        db.add(parent); db.flush(); parent_id = parent.id; db.commit()
    actor = scene['p'].user_id; result_hash = digest(question_result)
    base = f'/api/setup/clarifications/{actor}/{parent_id}/{result_hash}'
    response = scene['client'].get(base)
    assert response.status_code == 200, response.text
    value = response.json()['defaults']
    value['answers'][0].update(text='合成测试回答', include=True)
    saved = scene['client'].put(base, json={'revision': 0, 'request_id': str(uuid4()), 'action': 'save', 'value': value})
    assert saved.status_code == 200, saved.text
    monkeypatch.setattr(material_ocr, 'ensure_publishable', lambda *args, **kwargs: None)
    nonce = str(uuid4())
    preview = scene['client'].post(base + '/previews', json={'revision': scene['body']['revision'],
        'draft_revision': 1, 'value_hash': digest(value), 'request_id': nonce})
    assert preview.status_code == 200, preview.text
    preview_hash = preview.json()['preview']['preview_hash']
    provider, _, _ = scene_with_candidate(scene, monkeypatch)
    response = scene['client'].post(base + '/followups/' + nonce + '/start',
        json={'preview_hash': preview_hash, 'consent': True})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'unresolved_report_source'
    assert len(provider.calls) == 1


def test_old_previews_cannot_start_same_source_after_a_later_unresolved_failure(reading_scene, monkeypatch):
    scene = reading_scene
    ready = []
    for prefix, base in [('fe_', '/api/setup/report-evidence-readings'),
                         ('fr_', '/api/setup/report-full-readings')]:
        body = copy.deepcopy(scene['body']); body['request_id'] = prefix + str(uuid4())
        response = scene['client'].post(base + '/preview', json=body)
        assert response.status_code == 200, response.text
        ready.append((base, {**body, 'preview_hash': response.json()['preview_hash'], 'consent': True}))
    body = {k: copy.deepcopy(scene['body'][k]) for k in ('revision', 'model_id', 'material_refs', 'note')}
    body['request_id'] = 'rd_' + str(uuid4())
    response = scene['client'].post('/api/setup/report-drafts/preview', json=body)
    assert response.status_code == 200, response.text
    ready.append(('/api/setup/report-drafts', {**body, 'preview_hash': response.json()['preview_hash'], 'consent': True}))
    understanding = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    response = scene['client'].post('/api/setup/understanding/preview', json=understanding)
    assert response.status_code == 200, response.text
    ready.append(('/api/setup/understanding', {**understanding, 'preview_hash': response.json()['preview_hash'], 'consent': True}))
    provider, _, _ = scene_with_candidate(scene, monkeypatch)
    for endpoint, command in ready:
        response = scene['client'].post(endpoint, json=command)
        assert response.status_code == 409, (endpoint, response.text)
        assert response.json()['detail']['code'] == 'unresolved_report_source'
    assert len(provider.calls) == 1


def test_standard_and_linked_same_source_concurrent_start_only_links_once(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    from generic_agents import report_evidence_delegated_budget_start as budget_start
    monkeypatch.setattr(gate, 'ready', lambda: True)  # synthetic concurrency protocol only
    monkeypatch.setattr(budget_start, '_enabled', lambda: True)
    from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview
    scene = reading_scene
    plain = copy.deepcopy(scene['body']); plain['request_id'] = 'fe_' + str(uuid4())
    response = scene['client'].post('/api/setup/report-evidence-readings/preview', json=plain)
    assert response.status_code == 200, response.text
    command = {**plain, 'preview_hash': response.json()['preview_hash'], 'consent': True}
    principal, _, _, linked_preview, _ = scene_and_preview(scene, monkeypatch)
    with Session(engine()) as db:
        calls_before = db.scalar(select(func.count()).select_from(CloudCall))
        assert calls_before == 1  # historical synthetic call; no cloud HTTP
    barrier = threading.Barrier(2)
    def start_plain():
        barrier.wait(timeout=5)
        return scene['client'].post('/api/setup/report-evidence-readings', json=command)
    def start_linked():
        barrier.wait(timeout=5)
        return scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/start',
            json={'preview_id':linked_preview['preview_id'],'consent':True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(start_plain), pool.submit(start_linked)
        plain_result, linked_result = a.result(timeout=15), b.result(timeout=15)
    assert plain_result.status_code == 409, plain_result.text
    assert plain_result.json()['detail']['code'] in {'unresolved_report_source', 'report_draft_pending'}
    assert linked_result.status_code == 202, linked_result.text
    assert linked_result.json()['replayed'] is False
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id == principal.workspace_id,
            Job.kind == 'report_evidence_origin_v1')) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id == principal.workspace_id,
            Job.kind == 'cloud_report_plan_allowance_v1')) == 1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == calls_before


def test_legacy_paid_retry_cannot_requeue_same_source_after_unresolved_failure(reading_scene, monkeypatch):
    scene = reading_scene
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    preview = scene['client'].post('/api/setup/understanding/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    identity = response.json()['id']
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, identity); analysis.status = 'failed'; version = analysis.version
        before_jobs = db.scalars(select(Job.id).where(Job.workspace_id == scene['p'].workspace_id,
            Job.kind == 'understand_setup')).all(); db.commit()
    provider, _, _ = scene_with_candidate(scene, monkeypatch)
    response = scene['client'].post('/api/setup/understanding/' + identity + '/retry', json={'version': version})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'unresolved_report_source'
    with Session(engine()) as db:
        assert db.get(SetupAnalysis, identity).status == 'failed'
        assert db.scalars(select(Job.id).where(Job.workspace_id == scene['p'].workspace_id,
            Job.kind == 'understand_setup')).all() == before_jobs
    assert len(provider.calls) == 1


def test_legacy_unknown_inflight_blocks_new_understanding_and_own_paid_retry(reading_scene):
    scene = reading_scene
    other = upload(scene['client'], '另一份独立资料'.encode(), 'other-understanding.txt')
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    preview = scene['client'].post('/api/setup/understanding/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    identity = response.json()['id']
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, identity)
        analysis.status = 'failed'; analysis.data = {**analysis.data, 'inflight': 0}
        version = analysis.version
        before_calls = db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == scene['p'].workspace_id)).all()
        db.commit()
    # Analysis writers historically take the analysis row before workspace.
    # A concurrent guard holding workspace must not wait on that row.
    with Session(engine()) as holder, Session(engine()) as checking:
        holder.scalar(select(SetupAnalysis).where(SetupAnalysis.id == identity).with_for_update())
        guard_new_paid_sources(checking, scene['p'], [{k: other[k] for k in ('id', 'version', 'hash')}])
        checking.rollback()
        with pytest.raises(HTTPException) as busy:
            guard_new_paid_sources(checking, scene['p'], [refs(scene)[0]])
        assert busy.value.detail['code'] == 'report_history_busy'
        holder.rollback()
    new_preview = scene['client'].post('/api/setup/understanding/preview', json=body)
    assert new_preview.status_code == 409, new_preview.text
    assert new_preview.json()['detail']['code'] == 'unresolved_report_source'
    retry = scene['client'].post('/api/setup/understanding/' + identity + '/retry', json={'version': version})
    assert retry.status_code == 409, retry.text
    assert retry.json()['detail']['code'] == 'unresolved_report_source'
    with Session(engine()) as db:
        assert db.get(SetupAnalysis, identity).status == 'failed'
        assert db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == scene['p'].workspace_id)).all() == before_calls


@pytest.mark.parametrize('version', [1, 3])
def test_empty_source_legacy_pending_does_not_block_another_document(reading_scene, version):
    scene = reading_scene
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [], 'instruction': '', 'contract_version': version}
    if version == 3:
        body['request_id'] = str(uuid4())
    preview = scene['client'].post('/api/setup/understanding/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, response.json()['id'])
        analysis.status = 'failed'; analysis.data = {**analysis.data, 'inflight': 0}; db.commit()
    with Session(engine()) as db:
        guard_new_paid_sources(db, scene['p'], [refs(scene)[0]])


def test_old_pdf_legacy_pending_blocks_new_word_for_other_workspace_member(reading_scene):
    scene = reading_scene
    with Session(engine()) as db:
        source = db.get(SetupMaterial, scene['original']['id'])
        copied = db.get(SetupMaterial, scene['copy']['id'])
        copied.status = 'parsed'; copied.data = {**copied.data, 'blocks': copy.deepcopy(source.data['blocks'])}; db.commit()
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['copy']['id']], 'instruction': '', 'contract_version': 1}
    preview = scene['client'].post('/api/setup/understanding/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, response.json()['id'])
        analysis.status = 'failed'; analysis.data = {**analysis.data, 'inflight': 0}
        user = User(username='other_' + uuid4().hex, password_hash=password_hash(uuid4().hex))
        db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=scene['p'].workspace_id, role='admin'))
        identity = user.id; db.commit()
    with Session(engine()) as db:
        other = Principal(identity, scene['p'].workspace_id, 'admin', '')
        expect_overlap(db, other, [refs(scene)[0]])


def test_old_word_body_pending_blocks_new_pdf(reading_scene):
    scene = reading_scene
    body = {k: copy.deepcopy(scene['body'][k]) for k in ('revision', 'model_id', 'note')}
    body.update(request_id='rd_' + str(uuid4()), material_refs=[refs(scene)[0]])
    preview = scene['client'].post('/api/setup/report-drafts/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = scene['client'].post('/api/setup/report-drafts', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, response.json()['draft']['id']); row.status = 'failed'; db.commit()
    with Session(engine()) as db:
        expect_overlap(db, scene['p'], [refs(scene)[1]])


def test_later_missing_batch_is_not_mistaken_for_saved_earlier_raw(reading_scene):
    scene = reading_scene
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    preview = scene['client'].post('/api/setup/understanding/preview', json=body).json()
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    raw = {'batch': 0, 'value': {}, 'receipt': {'provider': 'synthetic', 'model': 'synthetic'}}
    raw['hash'] = digest(raw)
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, response.json()['id'])
        analysis.status = 'failed'; analysis.data = {**analysis.data, 'inflight': 1, 'raw_response': raw}; db.commit()
    with Session(engine()) as db:
        expect_overlap(db, scene['p'], [refs(scene)[0]])


def test_worker_later_batch_failure_retains_old_raw_but_blocks_resend(reading_scene, monkeypatch):
    from generic_agents import setup_understanding as understanding
    from generic_agents.gateway import ModelBlocked
    from tests.test_setup_understanding import prepared, start, fake, process
    scene = reading_scene
    body, material = prepared(scene['client'], text='合成测试文字。' * 6000)
    row, preview = start(scene['client'], body)
    assert len(preview['chunks']) > 1
    calls = fake(monkeypatch)
    first = understanding.cloud_json
    def fail_second(*args, **kwargs):
        if calls:
            raise ModelBlocked('合成第二批响应丢失')
        return first(*args, **kwargs)
    monkeypatch.setattr(understanding, 'cloud_json', fail_second)
    process(row)
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, row['id'])
        assert analysis.status == 'failed'
        assert analysis.data['inflight'] == 1 and analysis.data['raw_response']['batch'] == 0
        version = analysis.version
        expect_overlap(db, scene['p'], [{k: material[k] for k in ('id', 'version', 'hash')}])
    retry = scene['client'].post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': version})
    assert retry.status_code == 409 and len(calls) == 1


def test_history_byte_limit_denies_before_loading_large_rows(reading_scene, monkeypatch):
    from generic_agents import report_unresolved_overlap as overlap
    scene = reading_scene
    body = {'revision': scene['body']['revision'], 'model_id': scene['body']['model_id'],
        'material_ids': [scene['original']['id']], 'instruction': '', 'contract_version': 1}
    preview = scene['client'].post('/api/setup/understanding/preview', json=body).json()
    response = scene['client'].post('/api/setup/understanding', json={**body,
        'preview_hash': preview['preview_hash'], 'consent': True})
    assert response.status_code == 202, response.text
    with Session(engine()) as db:
        analysis = db.get(SetupAnalysis, response.json()['id'])
        analysis.status = 'failed'; analysis.data = {**analysis.data, 'inflight': 0, 'synthetic_padding': 'x' * 200000}; db.commit()
    monkeypatch.setattr(overlap, '_MAX_HISTORY_BYTES', 100000)
    monkeypatch.setattr(overlap, '_history_rows', lambda *a, **k: pytest.fail('history should not be loaded'))
    with Session(engine()) as db, pytest.raises(HTTPException) as denied:
        guard_new_paid_sources(db, scene['p'], [refs(scene)[0]])
    assert denied.value.detail['code'] == 'report_history_capacity'


def test_another_workspace_member_cannot_bypass_or_learn_owner(reading_scene, monkeypatch):
    from generic_agents.api import app
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    username, password = 'other_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=scene['p'].workspace_id, role='admin')); db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        body = copy.deepcopy(scene['body']); body['request_id'] = 'fe_' + str(uuid4())
        denied = other.post('/api/setup/report-evidence-readings/preview', json=body)
        assert denied.status_code == 409, denied.text
        assert denied.json()['detail']['code'] == 'unresolved_report_source'
        assert old['id'] not in denied.text and scene['p'].user_id not in denied.text
    assert len(provider.calls) == 1


def test_identical_document_in_another_workspace_is_not_blocked(reading_scene, monkeypatch):
    from generic_agents.api import app
    scene = reading_scene
    provider, _, _ = scene_with_candidate(scene, monkeypatch)
    original = scene['client'].get('/api/setup/materials/' + scene['original']['id'] + '/content')
    assert original.status_code == 200
    username, password = 'separate_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password))
        workspace = Workspace(name='独立合成工作区'); db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin'))
        workspace_id = workspace.id; db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': username, 'password': password})
        assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': workspace_id})
        assert other.get('/api/setup/draft').status_code == 200
        material = upload(other, original.content, 'same-synthetic.docx')
        assert material['hash'] == scene['original']['hash']
        me = other.get('/api/me').json()
        principal = Principal(me['user_id'], me['workspace_id'], me['role'], '')
        with Session(engine()) as db:
            guard_new_paid_sources(db, principal, [{k: material[k] for k in ('id', 'version', 'hash')}])
            db.rollback()
    assert len(provider.calls) == 1


def test_unrelated_source_does_not_wait_for_locked_failed_report(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    other = upload(scene['client'], '另一个合成文档。'.encode(), 'other-synthetic.txt')
    other_ref = {key: other[key] for key in ('id', 'version', 'hash')}
    with Session(engine()) as locked, Session(engine()) as checking:
        assert locked.scalar(select(SetupReportDraft).where(SetupReportDraft.id == old['id']).with_for_update())
        checking.execute(text("SET LOCAL lock_timeout = '200ms'"))
        guard_new_paid_sources(checking, scene['p'], [other_ref])
        checking.rollback(); locked.rollback()
    assert len(provider.calls) == 1


def test_locked_same_source_fails_without_wait_or_new_call(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    with Session(engine()) as locked, Session(engine()) as checking:
        assert locked.scalar(select(SetupReportDraft).where(SetupReportDraft.id == old['id']).with_for_update())
        checking.execute(text("SET LOCAL lock_timeout = '2s'"))
        with pytest.raises(HTTPException) as denied:
            guard_new_paid_sources(checking, scene['p'], [refs(scene)[0]])
        assert denied.value.status_code == 409 and denied.value.detail['code'] == 'report_history_busy'
        checking.rollback(); locked.rollback()
    assert len(provider.calls) == 1


def test_linked_exemption_never_exempts_a_second_unresolved_record(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    with Session(engine()) as db:
        original = db.get(SetupReportDraft, old['id'])
        second = SetupReportDraft(workspace_id=original.workspace_id, actor_id=original.actor_id,
            request_id='fr_' + str(uuid4()), status='failed', data=copy.deepcopy(original.data))
        db.add(second); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as denied:
            guard_new_paid_sources(db, scene['p'], refs(scene), linked_original_id=old['id'])
        assert denied.value.detail['code'] == 'unresolved_report_source'
    assert len(provider.calls) == 1


def test_rehashed_inconsistent_history_cannot_hide_its_source(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, old['id']); value = copy.deepcopy(row.data)
        preview = value['preview']; source = preview['source_snapshot']
        # Change one declared selection but leave its original frozen document
        # unchanged, then recompute every enclosing non-authoritative digest.
        source['selection']['original_ref']['hash'] = 'a' * 64
        preview['body']['selection']['original_ref']['hash'] = 'a' * 64
        source['snapshot_hash'] = digest({k: v for k, v in source.items() if k != 'snapshot_hash'})
        preview['source_hash'] = digest(source)
        preview['preview_hash'] = digest({k: v for k, v in preview.items() if k != 'preview_hash'})
        row.data = value; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as denied:
            guard_new_paid_sources(db, scene['p'], [refs(scene)[0]])
        assert denied.value.detail['code'] == 'report_history_scope_unknown'
    assert len(provider.calls) == 1


def test_unidentifiable_history_blocks_even_other_source_with_explicit_reason(reading_scene, monkeypatch):
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    other = upload(scene['client'], '另一个独立文档'.encode(), 'independent-synthetic.txt')
    other_ref = {key: other[key] for key in ('id', 'version', 'hash')}
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, old['id']); value = copy.deepcopy(row.data)
        value['preview']['source_snapshot'] = None
        row.data = value; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as denied:
            guard_new_paid_sources(db, scene['p'], [other_ref])
        assert denied.value.detail['code'] == 'report_history_scope_unknown'
        assert '无法安全核对' in denied.value.detail['message']
    assert len(provider.calls) == 1


def test_queued_plain_evidence_rechecks_history_before_worker_can_send(reading_scene, monkeypatch):
    from generic_agents import report_evidence_pipeline
    from generic_agents.worker import process_once
    scene = reading_scene
    provider, old, _ = scene_with_candidate(scene, monkeypatch)
    # Simulate a competing stale authorization while the old status changes.
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, old['id']); row.status = 'review'; db.commit()
    body = copy.deepcopy(scene['body']); body['request_id'] = 'fe_' + str(uuid4())
    preview = scene['client'].post('/api/setup/report-evidence-readings/preview', json=body)
    assert preview.status_code == 200, preview.text
    started = scene['client'].post('/api/setup/report-evidence-readings', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'consent': True})
    assert started.status_code == 202, started.text
    created = started.json()['draft']
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, old['id']); row.status = 'failed'; db.commit()
    monkeypatch.setattr(report_evidence_pipeline, 'cloud_json',
                        lambda *a, **k: pytest.fail('guard allowed a new model call'))
    assert process_once(created['job_id'])
    status = scene['client'].get('/api/setup/report-evidence-readings/' + created['id'] + '/status')
    assert status.status_code == 200, status.text
    assert status.json()['error_code'] == 'unresolved_report_source'
    assert len(provider.calls) == 1


def test_resolved_full_history_does_not_block_unrelated_or_same_source(reading_scene, monkeypatch):
    from tests.test_report_full_reading_pipeline import SyntheticProvider, start, finish_extract, merge
    scene = reading_scene
    provider = SyntheticProvider(scene, monkeypatch)
    _, _, started = start(scene)
    extracted = finish_extract(scene, started)
    _, _, _, completed = merge(scene, extracted)
    assert completed['status'] in {'review', 'needs_clarification'}
    with Session(engine()) as db:
        guard_new_paid_sources(db, scene['p'], refs(scene)); db.rollback()
    assert len(provider.calls) >= 2


def test_failed_local_phase_with_all_paid_answers_settled_is_not_an_unknown_call(reading_scene, monkeypatch):
    from tests.test_report_full_reading_pipeline import SyntheticProvider, start, finish_extract, narrow_extract_capacity
    scene = reading_scene
    narrow_extract_capacity(scene, monkeypatch)
    provider = SyntheticProvider(scene, monkeypatch)
    _, _, started = start(scene)
    extracted = finish_extract(scene, started)
    assert len(extracted['extractions']) == 2
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, extracted['id'])
        # Simulate a local failure after both actual model callbacks and ledger
        # settlements, not a fabricated completed paid response.
        data = copy.deepcopy(row.data); data['phase'] = 'failed'; row.data = data; row.status = 'failed'; db.commit()
    with Session(engine()) as db:
        guard_new_paid_sources(db, scene['p'], refs(scene)); db.rollback()
    assert len(provider.calls) == 2
