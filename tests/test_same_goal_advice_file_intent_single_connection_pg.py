"""The unsafe single-connection proof stays disabled, even for committed rows."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Job
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_single_connection_proof_is_unconditionally_disabled():
    with pytest.raises(HTTPException, match='') as result:
        intent.require_committed(None, None, None, None, None, None, None)
    assert '禁用' in result.value.detail


def test_even_committed_intent_cannot_write_file(ready):
    s, created, resolved = ready; p = s['principal']; job_id = created['job_id']
    with pytest.raises(HTTPException) as result, Session(engine()) as db:
        worker.process(db, p, s['task_id'], job_id)
    assert '禁用' in result.value.detail
    assert not (settings().storage_dir / p.workspace_id / (resolved['sha256'] + '.bin')).exists()
    with Session(engine()) as db:
        row = db.scalar(select(Job).where(Job.idempotency_key == intent._key(job_id)))
        assert row is not None and db.get(Job, job_id).status == 'queued'
        assert len(list(db.scalars(select(Audit).where(Audit.object_id == row.id, Audit.action == intent.ACTION)))) == 1
        assert not list(db.scalars(select(Evidence))) and not list(db.scalars(select(CloudCall)))
