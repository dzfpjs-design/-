"""Isolated PG and file-store tests; source/Goal resolvers are test doubles.

The handler itself creates the Evidence/upload Audit/document receipt. These
tests do not claim a real approved-source or mature-Goal journey.
"""
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Invocation, Job, Plan, Task
from generic_agents.worker import claim
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def ready(empty_original, monkeypatch):
    s = empty_original; prep = s['prep']; source = prep['request']['document_source']['source_set_ref']
    raw = '合成测试摘录：用于验证事务恢复。'.encode('utf-8')
    resolved = {'selection': copy.deepcopy(prep['request']['document_source']),
        'approved_selection': {**source, 'submission_id': '00000000-0000-0000-0000-000000000001',
            'submission_hash': 'a' * 64, 'input_key': 'runtime_document_sources'},
        'source_set': {'source_snapshot_hash': 'b' * 64}, 'content': raw,
        'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
        'packet_receipt': None, 'evidence_ref': None}
    monkeypatch.setattr(worker.sources, 'resolve_documents', lambda *_: copy.deepcopy(resolved))
    with Session(engine()) as db:
        result = writer.prepare(db, s['principal'], s['task_id'], prep['request'], prep['budget']['policy_ref'])
        db.commit()
    return s, result, resolved


def test_job_transaction_and_cold_read_use_real_receipt_and_are_idempotent(ready):
    s, created, resolved = ready; p = s['principal']; job_id = created['job_id']
    assert claim(job_id, kinds=[v2.PREPARE_KIND], workspace_id=p.workspace_id) is None
    with Session(engine()) as db:
        result = worker.process(db, p, s['task_id'], job_id); db.commit()
        assert result['replayed'] is False
    engine().dispose()
    with Session(engine()) as db:
        task, plan, job = db.get(Task, s['task_id']), db.get(Plan, created['plan_id']), db.get(Job, job_id)
        prep, checked = v2.read_prepared(db, p, task, plan)
        assert checked.id == job.id and job.status == 'succeeded' and plan.status == 'awaiting_consent'
        assert prep['request'] == s['prep']['request']
        assert common.read(db, p, task) == {'v1_calls': 0, 'queued_v2': 1}
        receipt = db.scalar(select(Audit).where(Audit.object_id == plan.id, Audit.action == v2.DOCUMENT_PREPARED))
        item = db.get(Evidence, receipt.data['evidence_ref']['evidence_id'])
        assert item.sha256 == resolved['sha256'] and item.data['cloud_model_ids'] == []
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id == plan.id,
            Audit.action == v2.DOCUMENT_PREPARED)) == 1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))
        assert worker.process(db, p, task.id, job.id)['replayed'] is True
        db.rollback()


def test_crash_after_file_save_rolls_back_rows_and_same_original_retries(ready, monkeypatch):
    s, created, _ = ready; p = s['principal']; job_id = created['job_id']
    save = worker.evidence.save_evidence
    def fail_after_save(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError('simulated process failure before database commit')
    with monkeypatch.context() as patch:
        patch.setattr(worker.evidence, 'save_evidence', fail_after_save)
        with pytest.raises(RuntimeError), Session(engine()) as db:
            worker.process(db, p, s['task_id'], job_id)
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'queued'
        assert db.get(Plan, created['plan_id']).data['document_preparation'] is None
        assert not list(db.scalars(select(Evidence)))
        assert not list(db.scalars(select(Audit).where(Audit.action == v2.DOCUMENT_PREPARED)))
    with Session(engine()) as db:
        assert worker.process(db, p, s['task_id'], job_id)['replayed'] is False
        db.commit()
    with Session(engine()) as db:
        assert worker.process(db, p, s['task_id'], job_id)['replayed'] is True


def test_two_workers_serialize_on_original_job(ready):
    s, created, _ = ready; p = s['principal']; job_id = created['job_id']
    def run():
        with Session(engine()) as db:
            value = worker.process(db, p, s['task_id'], job_id); db.commit(); return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(row['replayed'] for row in results) == [False, True]
    assert all(row['job_id'] == job_id for row in results)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == v2.DOCUMENT_PREPARED,
            Audit.object_id == created['plan_id'])) == 1
