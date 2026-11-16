"""Real queued cancellation must settle its original Job before retry is shown.

Root runs this on disposable ga_test PostgreSQL. All state transitions use
normal APIs and the actual worker; no Job status, lease or clock is fabricated.
"""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m, runtime_documents as native
from generic_agents import runtime_document_receipts as receipts
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene, response
from tests.test_runtime_documents_pg_v205 import instance, uploaded, run, version

BASE = '/api/runtime-documents'


def test_cancelled_version_waits_for_real_original_job_settlement_before_retry(scene):
    s = scene; release = instance(s); raw = '原文保留\n取消要等原后台任务确认终结再恢复😀'.encode()
    document = response(uploaded(s, release, raw, 'queued-cancel-reference.txt')[0], 201)
    identity = document['latest_version_id']; path = BASE + '/versions/' + identity
    with Session(engine()) as db:
        original = db.get(native.Version, identity)
        job_id = original.data['origin']['job_id']
        job = db.get(m.Job, job_id)
        assert job.status == 'queued' and job.data['runtime_document_id'] == identity
        original_key = job.idempotency_key

    original_version = version(s, identity)
    cancel_body = {'request_id': str(uuid4()), 'expected_version': original_version['version'],
        'reason': '保留原件，先取消待处理的本地解析'}
    cancelled = response(s.client.post(path + '/cancel', json=cancel_body))
    assert cancelled['status'] == 'cancelled' and cancelled['stopping'] is True
    assert cancelled['can_retry'] is False
    readback = version(s, identity)
    assert readback['status'] == 'cancelled' and readback['stopping'] is True and not readback['can_retry']
    assert s.client.get(path + '/original').content == raw
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        pending = copy.deepcopy((job.status, job.attempts, job.data))
        assert job.status == 'queued'
    # The operator sees the same restriction enforced by the write endpoint.
    assert s.client.post(path + '/retry', json={'request_id': str(uuid4()),
        'expected_version': readback['version'], 'reason': '旧后台任务尚未确认终结，不能提前恢复'}).status_code == 409
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert (job.status, job.attempts, job.data) == pending

    assert run(s, identity) == 'cancelled'  # Actual original worker acknowledges cancellation.
    settled = version(s, identity)
    assert settled['status'] == 'cancelled' and settled['stopping'] is False and settled['can_retry'] is True
    # The historical cancel command retains its actual earlier response.
    assert response(s.client.post(path + '/cancel', json=cancel_body)) == cancelled
    retried = response(s.client.post(path + '/retry', json={'request_id': str(uuid4()),
        'expected_version': settled['version'], 'reason': '原后台已确认停止，沿同一任务恢复本地解析'}))
    assert retried['status'] == 'queued' and not retried['stopping'] and not retried['can_retry']
    with Session(engine()) as db:
        original = db.get(native.Version, identity); job = db.get(m.Job, job_id)
        assert original.data['origin']['job_id'] == job.id and job.idempotency_key == original_key
        assert job.status == 'queued'
    assert run(s, identity) == 'succeeded'
    receipts.consume_once(s.workspace)
    parsed = version(s, identity)
    assert parsed['status'] == 'parsed' and not parsed['stopping'] and not parsed['can_retry']
    assert parsed['delivery']['verified'] and s.client.get(path + '/original').content == raw
    with Session(engine()) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace,
            m.Job.kind == 'parse_runtime_document', m.Job.data['runtime_document_id'].as_string() == identity)))
        assert len(jobs) == 1 and jobs[0].id == job_id and jobs[0].status == 'succeeded'
