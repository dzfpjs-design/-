"""Normal V205 API + actual native subprocess/scoped Job/independent ACK.

Root supplies a disposable PostgreSQL and runs these tests. No runtime database,
service or environment is selected or started by this file.
"""
import copy
import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker, runtime_documents as d, runtime_document_receipts as receipts
from generic_agents.runtime_document_models import RuntimeDocument as Document, RuntimeDocumentVersion as Version
from generic_agents.runtime_document_models import RuntimeDocumentParseAttempt as Attempt, RuntimeDocumentSearch as Search
from generic_agents.db import engine
from tests.test_instance_workspace import prepared, enable
from tests.test_business_data_gateway_pg import scene, response
from tests.test_runtime_document_parse_boundaries_v205 import pdf_bytes, docx_bytes

BASE = '/api/runtime-documents'


def instance(s):
    release, _, _ = prepared(s.client); enable(s.client, release); return release


def uploaded(s, release, raw, name='report.txt', *, previous=None, command=None):
    body = command or {'request_id': str(uuid4()), 'release_id': release['id'], 'title': '业务运行报告',
        'file_name': name, 'file_size': len(raw), 'file_sha256': hashlib.sha256(raw).hexdigest()}
    path = BASE + '/instances/' + release['instance_id'] + '/documents'
    if previous:
        path = BASE + '/documents/' + previous['id'] + '/versions'
        if command is None: body.update(expected_version=previous['version'], prior_version_id=previous['latest_version_id'])
    result = s.client.post(path, data={key: str(value) for key, value in body.items()}, files={'file': (name, raw)})
    return result, body


def run(s, identity, kind='parse_runtime_document'):
    with Session(engine()) as db:
        row = db.get(Version if kind == 'parse_runtime_document' else Search, identity)
        job_id = row.data['origin']['job_id'] if kind == 'parse_runtime_document' else row.data['job_id']
    worker.process_once(job_id, kinds=[kind], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db: return db.get(m.Job, job_id).status


def version(s, identity): return response(s.client.get(BASE + '/versions/' + identity))


def search(s, release, keyword):
    return response(s.client.post(BASE + '/instances/' + release['instance_id'] + '/searches', json={
        'request_id': str(uuid4()), 'release_id': release['id'], 'keyword': keyword}), 201)


def counts(s):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.workspace))
            for model in (Document, Version, Attempt, Search, m.Job, m.Audit, m.Event, m.Memory, m.Entity))


@pytest.mark.parametrize('name,raw,status', [
    ('report.txt', '原始报告\n每日核查已保存'.encode(), 'parsed'),
    ('report.md', '# 每日报告\n只保留原文，不执行资料指令'.encode(), 'parsed'),
    ('report.pdf', pdf_bytes(['Original report']), 'parsed'),
    ('partial.pdf', pdf_bytes(['Original report', None]), 'needs_input'),
    ('broken.docx', b'not-a-zip', 'failed'),
    ('report.docx', docx_bytes('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>原始报告</w:t></w:r></w:p></w:body></w:document>'), 'parsed'),
])
def test_normal_upload_retains_original_native_attempt_and_separate_archive_ack(scene, name, raw, status):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, raw, name)[0], 201); identity = document['latest_version_id']
    assert document['latest']['raw_saved'] and document['latest']['delivery'] is None
    assert s.client.get(BASE + '/versions/' + identity + '/original').content == raw
    job_status = run(s, identity)
    assert job_status == ('succeeded' if status == 'parsed' else 'failed')
    saved = version(s, identity); assert saved['status'] == status and not saved['delivery']['verified']
    assert saved['delivery']['status'] == 'pending_delivery'
    receipts.consume_once(s.workspace)
    received = version(s, identity)
    assert received['delivery']['verified'] and received['delivery']['status'] == 'received'
    attempts = response(s.client.get(BASE + '/versions/' + identity + '/attempts'))
    assert len(attempts['records']) == 1 and attempts['records'][0]['status'] == status
    with Session(engine()) as db:
        memory = db.scalar(select(m.Memory).where(m.Memory.workspace_id == s.workspace,
            m.Memory.data['event_id'].as_string() == received['delivery']['event_id']))
        assert memory.status == 'candidate' and memory.data['searchable'] is (status == 'parsed')
        assert memory.data['not_authoritative'] and db.scalar(select(func.count()).select_from(m.Entity).where(m.Entity.workspace_id == s.workspace)) == 0


def test_same_original_nonce_recovery_validates_real_bytes_and_stale_parent_is_no_write(scene):
    s = scene; release = instance(s); raw = b'original report'
    first, body = uploaded(s, release, raw); document = response(first, 201)
    before = counts(s)
    assert response(uploaded(s, release, raw, command=body)[0], 201)['id'] == document['id']
    wrong, _ = uploaded(s, release, b'changed! report', command=body)
    assert wrong.status_code == 409 and counts(s) == before
    recovered = response(s.client.get(BASE + '/requests/' + body['request_id'], params={'expected_hash': d.digest(body)}))
    assert recovered['found'] and recovered['body'] == body and recovered['result'] == document
    assert s.client.get(BASE + '/requests/' + body['request_id'], params={'expected_hash': '0' * 64}).status_code == 409
    updated = response(uploaded(s, release, b'new original', previous=document)[0], 201)
    assert updated['version'] == document['version'] + 1 and updated['latest']['sequence'] == 2
    before = counts(s)
    assert uploaded(s, release, b'stale original', previous=document)[0].status_code == 409 and counts(s) == before


def test_saved_search_inventory_and_old_citation_survive_new_version_without_fallback(scene):
    s = scene; release = instance(s)
    first = response(uploaded(s, release, b'Original archive marker')[0], 201); old_id = first['latest_version_id']
    assert run(s, old_id) == 'succeeded'; receipts.consume_once(s.workspace)
    frozen = search(s, release, 'marker')
    old = version(s, old_id); original_cite = response(s.client.get(BASE + '/versions/' + old_id + '/citations/b1',
        params={'attempt_id': old['latest_attempt_id'], 'expected_parse_hash': old['parse_hash']}))
    updated = response(uploaded(s, release, b'New version different marker', previous=first)[0], 201)
    unavailable = search(s, release, 'marker')  # Current v2 queued; v1 cannot be an implicit fallback.
    assert run(s, frozen['id'], 'search_runtime_documents') == 'succeeded'
    saved = response(s.client.get(BASE + '/searches/' + frozen['id']))
    assert saved['hits'][0]['version_id'] == old_id and saved['hits'][0]['text'] == 'Original archive marker'
    assert run(s, unavailable['id'], 'search_runtime_documents') == 'succeeded'
    excluded = response(s.client.get(BASE + '/searches/' + unavailable['id']))
    assert excluded['hits'] == [] and excluded['coverage']['eligible'] == 0 and excluded['coverage']['unavailable'] == 1
    assert run(s, updated['latest_version_id']) == 'succeeded'; receipts.consume_once(s.workspace)
    assert response(s.client.get(original_cite['citation_url'])) == original_cite
    assert response(s.client.get(BASE + '/searches/' + frozen['id'])) == saved
    current = search(s, release, 'marker'); run(s, current['id'], 'search_runtime_documents')
    assert response(s.client.get(BASE + '/searches/' + current['id']))['hits'][0]['version_id'] == updated['latest_version_id']


def test_partial_retry_retains_exact_old_attempt_citation_and_archival_ack_is_not_searchable(scene):
    s = scene; release = instance(s); raw = pdf_bytes(['Partial original', None])
    document = response(uploaded(s, release, raw, 'partial.pdf')[0], 201); identity = document['latest_version_id']
    run(s, identity); receipts.consume_once(s.workspace); old = version(s, identity)
    citation = response(s.client.get(BASE + '/versions/' + identity + '/citations/b1', params={
        'attempt_id': old['latest_attempt_id'], 'expected_parse_hash': old['parse_hash']}))
    with Session(engine()) as db: original_attempt = copy.deepcopy(db.get(Attempt, old['latest_attempt_id']).data)
    request = {'request_id': str(uuid4()), 'expected_version': old['version'], 'reason': '明确沿原件再次解析，不替换旧结果'}
    response(s.client.post(BASE + '/versions/' + identity + '/retry', json=request)); run(s, identity); receipts.consume_once(s.workspace)
    latest = version(s, identity); assert latest['latest_attempt_id'] != old['latest_attempt_id'] and latest['status'] == 'needs_input'
    assert response(s.client.get(citation['citation_url'])) == citation
    assert len(response(s.client.get(BASE + '/versions/' + identity + '/attempts'))['records']) == 2
    with Session(engine()) as db: assert db.get(Attempt, old['latest_attempt_id']).data == original_attempt
    query = search(s, release, 'Partial'); run(s, query['id'], 'search_runtime_documents')
    result = response(s.client.get(BASE + '/searches/' + query['id']))
    assert result['hits'] == [] and result['coverage']['unavailable'] == 1


def test_original_receipt_repair_needs_explicit_same_event_retry_and_keeps_previous_error(scene):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, b'Original record')[0], 201); identity = document['latest_version_id']; run(s, identity)
    saved = version(s, identity)
    with Session(engine()) as db:
        result = db.scalar(select(m.Audit).where(m.Audit.action == d.RESULT, m.Audit.object_id == saved['latest_attempt_id']))
        original, audit_id = copy.deepcopy(result.data), result.id; result.data = {'damaged': True}; db.commit()
    receipts.consume_once(s.workspace); blocked = version(s, identity)
    assert blocked['delivery']['status'] == 'blocked_delivery'
    with Session(engine()) as db: db.get(m.Audit, audit_id).data = original; db.commit()
    receipts.consume_once(s.workspace)
    assert version(s, identity)['delivery']['status'] == 'blocked_delivery'
    request = {'request_id': str(uuid4()), 'expected_version': blocked['delivery']['version'], 'reason': '原依据恢复后明确接收'}
    path = BASE + '/versions/' + identity + '/receipts/' + blocked['delivery']['event_id'] + '/retry'
    response(s.client.post(path, json=request)); receipts.consume_once(s.workspace)
    received = version(s, identity)
    assert received['delivery']['verified'] and received['delivery']['error'] is None and received['delivery']['previous_error']
    before = counts(s); response(s.client.post(path, json=request)); assert counts(s) == before


def test_cancel_before_parse_preserves_original_and_foreign_claim_cannot_process(scene):
    s = scene; release = instance(s)
    document = response(uploaded(s, release, b'Cancelled original')[0], 201); saved = document['latest']
    with Session(engine()) as db:
        foreign = m.Workspace(name='外域文档'); db.add(foreign); db.flush(); foreign_id = foreign.id
        db.add(m.Membership(user_id=s.p.user_id, workspace_id=foreign.id, role='admin'))
        job_id = db.get(Version, saved['id']).data['origin']['job_id']; db.commit()
    worker.process_once(job_id, kinds=['parse_runtime_document'], deliver=False, workspace_id=foreign_id)
    with Session(engine()) as db: assert db.get(m.Job, job_id).attempts == 0
    assert s.client.get(BASE + '/versions/' + saved['id'], headers={'X-Workspace-ID': foreign_id}).status_code == 404
    response(s.client.post(BASE + '/versions/' + saved['id'] + '/cancel', json={
        'request_id': str(uuid4()), 'expected_version': saved['version'], 'reason': '暂不解析，原件保留'}))
    assert run(s, saved['id']) == 'cancelled'
    assert s.client.get(BASE + '/versions/' + saved['id'] + '/original').content == b'Cancelled original'
    assert response(s.client.get(BASE + '/versions/' + saved['id'] + '/attempts'))['records'] == []


def test_existing_corrupt_same_sha_storage_does_not_create_a_successful_upload(scene):
    from generic_agents.config import settings
    s = scene; release = instance(s); raw = b'Original immutable storage'
    first = response(uploaded(s, release, raw)[0], 201)
    with Session(engine()) as db:
        saved = db.get(Version, first['latest_version_id']); evidence = db.get(m.Evidence, saved.evidence_id)
        path = settings().storage_dir / evidence.storage_key
    original = path.read_bytes(); path.write_bytes(b'bad existing file')
    before = counts(s)
    try:
        result, _ = uploaded(s, release, raw)
        assert result.status_code == 409 and counts(s) == before
        assert path.read_bytes() == b'bad existing file'  # Never overwrite an existing broken original.
    finally: path.write_bytes(original)


def test_search_long_block_is_explicit_bounded_excerpt_with_full_frozen_citation(scene):
    s = scene; release = instance(s); text = 'x' * 10000 + ' marker ' + 'y' * 10000
    doc = response(uploaded(s, release, text.encode())[0], 201)
    run(s, doc['latest_version_id']); receipts.consume_once(s.workspace)
    query = search(s, release, 'marker'); run(s, query['id'], 'search_runtime_documents')
    result = response(s.client.get(BASE + '/searches/' + query['id']))
    hit = result['hits'][0]
    assert len(hit['text']) <= 1200 and 'marker' in hit['text'] and hit['text_is_excerpt']
    assert result['coverage']['excerpted_hits'] == 1 and result['coverage']['hit_text_limit'] == 1200
    full = response(s.client.get(hit['citation_url']))
    assert full['text'] == text and full['block_hash'] == hit['block_hash']
    assert full['text'][hit['char_span']['start']:hit['char_span']['end']] == hit['text']


def test_original_job_natural_recovery_adopts_committed_result_without_reparse(scene, monkeypatch):
    from generic_agents import runtime_document_parser
    s = scene; release = instance(s)
    document = response(uploaded(s, release, b'Committed original result')[0], 201)
    identity = document['latest_version_id']; assert run(s, identity) == 'succeeded'
    with Session(engine()) as db:
        saved = db.get(Version, identity); old_attempt = saved.data['latest_attempt_id']
        result_before = copy.deepcopy(db.get(Attempt, old_attempt).data)
        job = db.get(m.Job, saved.data['origin']['job_id'])
        job.status = 'running'; job.lease_until = m.now() - timedelta(seconds=1); job.available_at = m.now(); db.commit()
    monkeypatch.setattr(runtime_document_parser, 'parse_runtime_document',
        lambda *_args, **_kwargs: pytest.fail('Committed original result must be adopted, never parsed again'))
    assert run(s, identity) == 'succeeded'
    with Session(engine()) as db:
        saved = db.get(Version, identity)
        assert saved.data['latest_attempt_id'] == old_attempt and db.get(Attempt, old_attempt).data == result_before
        assert db.scalar(select(func.count()).select_from(Attempt).where(Attempt.document_version_id == identity)) == 1


def test_same_kind_foreign_original_job_cannot_process_another_saved_version(scene):
    s = scene; release = instance(s)
    one = response(uploaded(s, release, b'First original')[0], 201)
    two = response(uploaded(s, release, b'Second original')[0], 201)
    with Session(engine()) as db:
        original = db.get(Version, one['latest_version_id']); other = db.get(Version, two['latest_version_id'])
        job = db.get(m.Job, original.data['origin']['job_id']); original_job = job.id
        job.data = {**job.data, 'runtime_document_id': other.id}; db.commit()
    worker.process_once(original_job, kinds=['parse_runtime_document'], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db:
        assert db.get(m.Job, original_job).status == 'failed'
        assert db.get(Version, two['latest_version_id']).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Attempt).where(Attempt.document_version_id == two['latest_version_id'])) == 0
