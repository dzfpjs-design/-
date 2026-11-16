"""Real normal API/scoped worker/DB receipts with an explicitly emulated model seam.

Root runs against ga_test PostgreSQL. This file never selects/starts a database
or claims local model quality. The separate actual acceptance owns real OCR.
"""
import copy
import hashlib
import io
import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker, material_ocr_runtime as runtime
from generic_agents import runtime_document_ocr as o, runtime_document_ocr_receipts as receipts
from generic_agents.runtime_document_ocr_models import RuntimeDocumentOcrRun as Run, RuntimeDocumentOcrPage as Page
from generic_agents.runtime_document_ocr_models import RuntimeDocumentOcrResult as Result, RuntimeDocumentOcrSearch as Search
from generic_agents.runtime_document_models import RuntimeDocumentParseAttempt as NativeAttempt
from generic_agents.db import engine
from generic_agents.contracts import digest
from tests.test_business_data_gateway_pg import scene, response
from tests.test_runtime_documents_pg_v205 import instance, uploaded
from tests.test_runtime_document_parse_boundaries_v205 import pdf_bytes

BASE = '/api/runtime-document-ocr'


@pytest.fixture
def emulated_local(monkeypatch):
    summary = {'id': 'explicit-test-only-model-seam', 'manifest_hash': o.PROFILE}
    installed = {'id': summary['id'], 'manifest_hash': o.PROFILE, 'name': '测试引擎边界（未运行真实OCR）',
        'manifest': summary, 'summary_hash': digest(summary), 'ready': True, 'reason': None,
        'verification_tier': 'test_only_emulated_model'}
    monkeypatch.setattr(o, 'profile', lambda **_: copy.deepcopy(installed))
    calls = {'render': 0, 'recognize': 0, 'fail': False, 'text': '原识别 marker', 'during': None}

    def receipt(raw):
        return {'input_sha256': hashlib.sha256(raw).hexdigest(), 'manifest_hash': o.PROFILE,
            'manifest': copy.deepcopy(summary), 'summary_hash': digest(summary)}

    def render(raw, page, *, guard, expected_manifest_hash):
        assert expected_manifest_hash == o.PROFILE; guard(); calls['render'] += 1
        output = io.BytesIO(); Image.new('RGB', (20, 30), (page, 255, 255)).save(output, format='PNG')
        png = output.getvalue()
        return [{'page': page, 'png': png, 'width': 20, 'height': 30,
            'receipt': {**receipt(raw), 'output_sha256': hashlib.sha256(png).hexdigest()}}]

    def recognize(raw, *, guard, expected_manifest_hash):
        assert expected_manifest_hash == o.PROFILE; guard(); calls['recognize'] += 1
        if calls['during']: calls['during']()
        guard()
        if calls['fail']: raise HTTPException(409, '本地识别测试故障；原件保留')
        regions = [] if calls['text'] is None else [{'text': calls['text'], 'box': [1, 2, 10, 5], 'score': .9}]
        return {'regions': regions, 'receipt': {**receipt(raw), 'result_sha256': digest(regions)}}

    monkeypatch.setattr(runtime, 'render_pdf', render); monkeypatch.setattr(runtime, 'recognize', recognize)
    return calls


def start(s, release, document, mode='all', pages=None, *, command=None):
    body = command or {'request_id': str(uuid4()), 'release_id': release['id'], 'profile_hash': o.PROFILE,
        'range_mode': mode, 'pages': pages or []}
    return response(s.client.post(BASE + '/versions/' + document['latest_version_id'] + '/runs', json=body), 201), body


def job_for(db, model, identity, kind):
    row = db.get(model, identity)
    return row.data['origin']['prepare_job_id' if model is Run else 'job_id']


def run(s, model, identity, kind):
    with Session(engine()) as db: job_id = job_for(db, model, identity, kind)
    worker.process_once(job_id, kinds=[kind], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db: return db.get(m.Job, job_id).status


def pages(s, identity): return response(s.client.get(BASE + '/runs/' + identity + '/pages'))['records']
def page(s, identity): return response(s.client.get(BASE + '/pages/' + identity))
def saved(s, identity): return response(s.client.get(BASE + '/runs/' + identity))


def ready(s, release, raw=None, name='scanned.pdf'):
    document = response(uploaded(s, release, raw or pdf_bytes([None]), name)[0], 201)
    created, _ = start(s, release, document)
    assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'succeeded'
    for item in pages(s, created['id']): run(s, Page, item['id'], 'recognize_runtime_document_ocr_page')
    receipts.consume_once(s.workspace)
    return document, saved(s, created['id']), pages(s, created['id'])


def query(s, release, word='marker'):
    value = response(s.client.post(BASE + '/instances/' + release['instance_id'] + '/searches', json={
        'request_id': str(uuid4()), 'release_id': release['id'], 'keyword': word}), 201)
    assert run(s, Search, value['id'], 'search_runtime_document_ocr') == 'succeeded'
    return response(s.client.get(BASE + '/searches/' + value['id']))


def inventory_counts(s):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.workspace))
            for model in (Run, Page, Result, Search, m.Job, m.Event, m.Memory, m.Entity, NativeAttempt))


def test_normal_pdf_original_page_image_regions_citation_separate_ack_and_same_nonce(scene, emulated_local):
    s = scene; release = instance(s); raw = pdf_bytes([None])
    document = response(uploaded(s, release, raw, '扫描资料.pdf')[0], 201)
    created, body = start(s, release, document); before = inventory_counts(s)
    assert start(s, release, document, command=body)[0] == created and inventory_counts(s) == before
    recovered = response(s.client.get(BASE + '/requests/' + body['request_id'], params={'expected_hash': digest(body)}))
    assert recovered['schema'] == 'runtime-document-ocr-command-v206' and recovered['body'] == body and recovered['result'] == created
    assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'succeeded'
    item = pages(s, created['id'])[0]; assert item['status'] == 'queued'
    assert run(s, Page, item['id'], 'recognize_runtime_document_ocr_page') == 'succeeded'
    result = page(s, item['id'])['results'][0]
    assert result['status'] == 'text' and result['delivery']['status'] == 'pending_delivery'
    receipts.consume_once(s.workspace); result = page(s, item['id'])['results'][0]
    assert result['delivery']['verified'] and result['delivery']['status'] == 'received'
    region_page = response(s.client.get(BASE + '/results/' + result['id'] + '/regions'))
    assert region_page['records'] == [{'id': 'r1', 'text': '原识别 marker', 'box': [1, 2, 10, 5], 'score': .9, 'unreviewed': True}]
    image = s.client.get(result['image_url']); assert image.status_code == 200
    assert hashlib.sha256(image.content).hexdigest() == result['image_sha256'] == region_page['image_sha256']
    citation = response(s.client.get(BASE + '/results/' + result['id'] + '/citations/r1', params={'expected_result_hash': result['result_hash']}))
    assert citation['source_name'] == '扫描资料.pdf' and citation['version_sequence'] == 1
    assert citation['page_id'] == item['id'] and citation['unreviewed'] and citation['image_width'] == 20
    found = query(s, release); assert found['hits'][0] == {**citation, 'text_is_excerpt': False, 'char_span': {'start': 0, 'end': len(citation['text'])}}
    assert inventory_counts(s)[-2:] == (0, 0)  # No Entity writes or fabricated native parse attempts.
    assert emulated_local['render'] == emulated_local['recognize'] == 1


def test_native_only_missing_text_completes_zero_selected_without_result_or_fake_blank(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes(['Native original']), 'native.pdf')[0], 201)
    created, _ = start(s, release, document, 'missing_text')
    assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'succeeded'
    value = saved(s, created['id'])
    assert value['status'] == 'completed' and value['selected_count'] == 0 and value['coverage']['unselected_count'] == 1
    assert not value['coverage']['whole_document_text_verified'] and pages(s, created['id']) == []
    assert emulated_local['recognize'] == emulated_local['render'] == 0
    assert inventory_counts(s)[2] == 0


def test_no_text_is_archived_but_never_claims_blank_or_searchable(scene, emulated_local):
    s = scene; release = instance(s); emulated_local['text'] = None
    _, value, items = ready(s, release); result = items[0]['results'][0]
    assert value['counts']['no_text'] == 1 and result['delivery']['verified'] and result['regions_count'] == 0
    assert query(s, release)['hits'] == []
    with Session(engine()) as db:
        memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace,
            m.Memory.data['event_id'].as_string() == result['delivery']['event_id']))
        assert memory.status == 'candidate' and not memory.data['searchable'] and memory.data['unreviewed']


@pytest.mark.parametrize('mode,numbers,expected', [('all', [], 0), ('missing_text', [], 0), ('pages', [1], 1)])
def test_new_unprepared_run_fences_declared_pages_without_old_result_fallback(scene, emulated_local, mode, numbers, expected):
    s = scene; release = instance(s)
    document, _, items = ready(s, release, pdf_bytes([None, None])); old = query(s, release)
    assert len(old['hits']) == 2
    pending, _ = start(s, release, document, mode, numbers)
    fresh = query(s, release)
    assert len(fresh['hits']) == expected and fresh['coverage']['pending_plans'] == 1
    assert fresh['coverage']['notice'] and all(hit['page_number'] == 2 for hit in fresh['hits'])
    assert response(s.client.get(BASE + '/searches/' + old['id'])) == old
    if mode == 'pages':
        assert run(s, Run, pending['id'], 'prepare_runtime_document_ocr') == 'succeeded'
        emulated_local['fail'] = True
        run(s, Page, pages(s, pending['id'])[0]['id'], 'recognize_runtime_document_ocr_page')
        assert len(query(s, release)['hits']) == 1  # Failed newest selected page also cannot fall back.


def test_failed_page_explicit_retry_retains_old_result_and_same_original_job(scene, emulated_local):
    s = scene; release = instance(s); emulated_local['fail'] = True
    document, value, items = ready(s, release); failed = items[0]; old = failed['results'][0]
    assert old['status'] == 'failed' and old['delivery']['verified']
    with Session(engine()) as db:
        old_data = copy.deepcopy(db.get(Result, old['id']).data); job_id = db.get(Page, failed['id']).data['origin']['job_id']
    emulated_local['fail'] = False
    command = {'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '核对本地引擎恢复后明确重试此原页'}
    response(s.client.post(BASE + '/pages/' + failed['id'] + '/retry', json=command))
    assert run(s, Page, failed['id'], 'recognize_runtime_document_ocr_page') == 'succeeded'
    receipts.consume_once(s.workspace); latest = page(s, failed['id'])
    assert len(latest['results']) == 2 and latest['results'][0]['status'] == 'text' and latest['results'][1] == old
    assert saved(s, value['id'])['previous_errors']
    with Session(engine()) as db:
        assert db.get(Result, old['id']).data == old_data and db.get(Page, failed['id']).data['origin']['job_id'] == job_id
    before = inventory_counts(s)
    response(s.client.post(BASE + '/pages/' + failed['id'] + '/retry', json=command))
    assert inventory_counts(s) == before and emulated_local['recognize'] == 2


def test_acked_citation_and_saved_search_pin_old_version_and_old_run_after_later_work(scene, emulated_local):
    s = scene; release = instance(s)
    document, _, _ = ready(s, release, name='原资料.pdf'); first = query(s, release); hit = first['hits'][0]
    updated = response(uploaded(s, release, pdf_bytes([None, None]), '第二版.pdf', previous=document)[0], 201)
    assert query(s, release)['hits'] == []  # Queued v2 cannot borrow v1.
    new, _ = start(s, release, updated); run(s, Run, new['id'], 'prepare_runtime_document_ocr')
    emulated_local['text'] = '第二版 marker'
    for item in pages(s, new['id']): run(s, Page, item['id'], 'recognize_runtime_document_ocr_page')
    receipts.consume_once(s.workspace)
    assert all(x['source_name'] == '第二版.pdf' and x['version_sequence'] == 2 for x in query(s, release)['hits'])
    citation = response(s.client.get(hit['citation_url']))
    assert citation['source_name'] == '原资料.pdf' and citation['version_sequence'] == 1
    assert response(s.client.get(BASE + '/searches/' + first['id'])) == first


def test_receipt_repair_needs_same_event_explicit_retry_without_rerender_and_current_error_clears(scene, emulated_local):
    s = scene; release = instance(s); document, value, items = ready(s, release)
    result = items[0]['results'][0]
    with Session(engine()) as db:
        event = db.get(m.Event, result['delivery']['event_id']); event.status = 'pending_delivery'; event.received_at = None
        anchor = db.scalar(select(m.Audit).where(m.Audit.action == o.FINISH, m.Audit.object_id == result['id']))
        original, audit_id = copy.deepcopy(anchor.data), anchor.id; anchor.data = {'damaged': True}; db.commit()
    receipts.consume_once(s.workspace); blocked = page(s, items[0]['id'])['results'][0]
    assert blocked['delivery']['status'] == 'blocked_delivery'
    with Session(engine()) as db: db.get(m.Audit, audit_id).data = original; db.commit()
    receipts.consume_once(s.workspace); assert page(s, items[0]['id'])['results'][0]['delivery']['status'] == 'blocked_delivery'
    command = {'request_id': str(uuid4()), 'expected_version': blocked['delivery']['version'], 'reason': '原证据恢复后确认同原结果收档'}
    path = BASE + '/results/' + result['id'] + '/receipts/' + result['delivery']['event_id'] + '/retry'
    response(s.client.post(path, json=command)); receipts.consume_once(s.workspace)
    delivery = page(s, items[0]['id'])['results'][0]['delivery']
    assert delivery['verified'] and delivery['error'] is None and delivery['previous_error']
    assert emulated_local['render'] == emulated_local['recognize'] == 1


def test_removed_original_command_refuses_prepare_and_safe_recovery_does_not_mutate_target(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None]), 'original.pdf')[0], 201)
    created, _ = start(s, release, document)
    with Session(engine()) as db:
        row = db.get(Run, created['id']); job_id = row.data['origin']['prepare_job_id']
        anchor = db.get(m.Audit, row.data['origin']['command_audit_id']); anchor.data = {'damaged': True}; db.commit()
        original_status = db.get(Run, created['id']).status
    assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'failed'
    with Session(engine()) as db:
        assert db.get(Run, created['id']).status == original_status
        assert db.scalar(select(m.Audit.id).where(m.Audit.action == 'runtime_document_ocr.recovery_refused_v206', m.Audit.object_id == job_id))
    assert not emulated_local['recognize']


def test_same_kind_foreign_original_job_cannot_process_or_recover_another_run(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None]), 'original.pdf')[0], 201)
    first, _ = start(s, release, document); second, _ = start(s, release, document)
    with Session(engine()) as db:
        source = db.get(Run, first['id']); job = db.get(m.Job, source.data['origin']['prepare_job_id'])
        job.data = {**job.data, 'runtime_document_ocr_id': second['id']}; db.commit()
    assert run(s, Run, first['id'], 'prepare_runtime_document_ocr') == 'failed'
    assert saved(s, second['id'])['status'] == 'queued' and not pages(s, second['id'])
    assert not emulated_local['recognize'] and not emulated_local['render']


def test_foreign_workspace_cannot_claim_read_or_recover_original_ocr(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None]), 'original.pdf')[0], 201); created, body = start(s, release, document)
    with Session(engine()) as db:
        other = m.Workspace(name='隔离文档'); db.add(other); db.flush()
        db.add(m.Membership(user_id=s.p.user_id, workspace_id=other.id, role='admin'))
        other_id = other.id; job_id = db.get(Run, created['id']).data['origin']['prepare_job_id']; db.commit()
    worker.process_once(job_id, kinds=['prepare_runtime_document_ocr'], deliver=False, workspace_id=other_id)
    with Session(engine()) as db: assert db.get(m.Job, job_id).attempts == 0
    headers = {'X-Workspace-ID': other_id}
    assert s.client.get(BASE + '/runs/' + created['id'], headers=headers).status_code == 404
    assert not response(s.client.get(BASE + '/requests/' + body['request_id'], headers=headers))['found']


def test_missing_original_before_plan_is_visible_failure_without_fabricated_result_then_same_job_retry(scene, emulated_local):
    from generic_agents.config import settings
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None]), 'source.pdf')[0], 201); created, _ = start(s, release, document)
    with Session(engine()) as db:
        original = db.get(o.Version, document['latest_version_id'])
        evidence = db.get(m.Evidence, original.evidence_id); path = settings().storage_dir / evidence.storage_key
        job_id = db.get(Run, created['id']).data['origin']['prepare_job_id']
    raw = path.read_bytes(); path.unlink()
    try:
        assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'failed'
        failed = saved(s, created['id'])
        assert failed['status'] == 'failed' and failed['error'] and failed['previous_errors']
        assert pages(s, created['id']) == [] and inventory_counts(s)[2] == 0
    finally: path.write_bytes(raw)
    response(s.client.post(BASE + '/runs/' + created['id'] + '/retry', json={
        'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '原始字节已按原SHA恢复，继续原批次'}))
    assert run(s, Run, created['id'], 'prepare_runtime_document_ocr') == 'succeeded'
    with Session(engine()) as db: assert db.get(Run, created['id']).data['origin']['prepare_job_id'] == job_id
    assert len(pages(s, created['id'])) == 1 and emulated_local['recognize'] == 0


def test_cancel_and_continue_keeps_successful_page_and_original_unfinished_job(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None, None]), 'two-pages.pdf')[0], 201)
    created, _ = start(s, release, document); run(s, Run, created['id'], 'prepare_runtime_document_ocr')
    first, second = pages(s, created['id']); run(s, Page, first['id'], 'recognize_runtime_document_ocr_page')
    receipts.consume_once(s.workspace); preserved = page(s, first['id']); current = saved(s, created['id'])
    response(s.client.post(BASE + '/runs/' + created['id'] + '/cancel', json={
        'request_id': str(uuid4()), 'expected_version': current['version'], 'reason': '先保留已完成原页，暂停余页'}))
    assert run(s, Page, second['id'], 'recognize_runtime_document_ocr_page') == 'cancelled'
    assert page(s, first['id']) == preserved and emulated_local['recognize'] == 1
    cancelled = saved(s, created['id'])
    response(s.client.post(BASE + '/runs/' + created['id'] + '/retry', json={
        'request_id': str(uuid4()), 'expected_version': cancelled['version'], 'reason': '明确继续原未完成页，不重识别成功页'}))
    assert run(s, Page, second['id'], 'recognize_runtime_document_ocr_page') == 'succeeded'
    receipts.consume_once(s.workspace)
    assert page(s, first['id']) == preserved and saved(s, created['id'])['status'] == 'completed'
    assert emulated_local['recognize'] == 2


def test_expired_original_job_adopts_committed_result_without_reinference(scene, emulated_local, monkeypatch):
    # Unit-level lease setup only; actual acceptance uses SIGKILL and real DB time.
    s = scene; release = instance(s); _, _, items = ready(s, release); original = items[0]
    with Session(engine()) as db:
        job = db.get(m.Job, db.get(Page, original['id']).data['origin']['job_id'])
        job.status = 'running'; job.lease_until = m.now() - timedelta(seconds=1); job.available_at = m.now(); db.commit()
    def forbidden(*_args, **_kwargs): pytest.fail('Committed original OCR must be adopted, never recognized again')
    monkeypatch.setattr(runtime, 'render_pdf', forbidden); monkeypatch.setattr(runtime, 'recognize', forbidden)
    assert run(s, Page, original['id'], 'recognize_runtime_document_ocr_page') == 'succeeded'
    assert page(s, original['id']) == original and emulated_local['recognize'] == 1


def test_cancelled_run_projects_stopping_until_actual_original_worker_lease_finishes(scene, emulated_local):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, pdf_bytes([None]), 'stopping.pdf')[0], 201)
    created, _ = start(s, release, document); run(s, Run, created['id'], 'prepare_runtime_document_ocr')
    item = pages(s, created['id'])[0]
    entered, leave = threading.Event(), threading.Event(); errors = []

    def pause_recognition():
        entered.set()
        if not leave.wait(15): raise RuntimeError('Test did not release its emulated recognizer')

    emulated_local['during'] = pause_recognition
    with Session(engine()) as db: job_id = db.get(Page, item['id']).data['origin']['job_id']

    def process_original():
        try: worker.process_once(job_id, kinds=['recognize_runtime_document_ocr_page'], deliver=False, workspace_id=s.workspace)
        except BaseException as failure: errors.append(failure)

    thread = threading.Thread(target=process_original, daemon=True); thread.start()
    try:
        assert entered.wait(15), errors
        with Session(engine()) as db:
            original_job = db.get(m.Job, job_id)
            assert original_job.status == 'running' and original_job.lease_token and original_job.attempts == 1
        current = saved(s, created['id'])
        command = {'request_id': str(uuid4()), 'expected_version': current['version'], 'reason': '等待原本地处理停止后再沿原任务继续'}
        cancelled = response(s.client.post(BASE + '/runs/' + created['id'] + '/cancel', json=command))
        assert cancelled['status'] == 'cancelled' and cancelled['stopping'] and not cancelled['can_retry']
        before = inventory_counts(s)
        projection = saved(s, created['id']); stopped_page = page(s, item['id'])
        assert projection['stopping'] and not projection['can_retry'] and not stopped_page['can_retry']
        assert stopped_page['results'][0]['status'] == 'cancelled' and inventory_counts(s) == before
        result_id = stopped_page['results'][0]['id']
        with Session(engine()) as db:
            pinned = copy.deepcopy(db.get(Result, result_id).data)
            token = db.get(m.Job, job_id).lease_token
            assert db.get(m.Job, job_id).status == 'running'
    finally:
        leave.set(); thread.join(15)
    assert not thread.is_alive() and not errors
    with Session(engine()) as db:
        assert db.get(m.Job, job_id).status == 'cancelled'
        assert db.get(m.Job, job_id).lease_token == token and db.get(m.Job, job_id).attempts == 1
        assert db.get(Result, result_id).data == pinned
    before = inventory_counts(s)
    projection = saved(s, created['id'])
    assert not projection['stopping'] and projection['can_retry'] and page(s, item['id'])['can_retry']
    assert inventory_counts(s) == before
    # An already saved canonical response remains its original stopping receipt.
    assert response(s.client.post(BASE + '/runs/' + created['id'] + '/cancel', json=command)) == cancelled
    assert emulated_local['recognize'] == emulated_local['render'] == 1
