"""Accidental calls outside the explicit synthetic DB must fail closed.

These test markers are defense in depth, not a production security boundary.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents.db import engine
from generic_agents.models import Job
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.mark.parametrize('field,value', [('GA_TEST_DATABASE_URL', ''),
    ('GA_TEST_PG_SYSTEM_IDENTIFIER', '0'), ('GA_TEST_PG_DATA_DIR', '/not-a-test-cluster/data')])
def test_candidate_writer_and_worker_reject_missing_or_mismatched_test_cluster(ready, monkeypatch, field, value):
    s, created, _ = ready; p = s['principal']; prep = s['prep']
    monkeypatch.setenv(field, value)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            writer.prepare(db, p, s['task_id'], prep['request'], prep['budget']['policy_ref'])
        with pytest.raises(HTTPException): worker.process(db, p, s['task_id'], created['job_id'])
        assert db.get(Job, created['job_id']).status == 'queued'
