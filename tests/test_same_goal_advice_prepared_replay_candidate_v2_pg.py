"""The same request must recover its persisted original after preparation."""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents.db import engine
from generic_agents.models import uid
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_same_request_recovers_completed_preparation(ready):
    s, created, _ = ready; p = s['principal']; prep = s['prep']
    with Session(engine()) as db:
        worker.process(db, p, s['task_id'], created['job_id']); db.commit()
    with Session(engine()) as db:
        replay = writer.prepare(db, p, s['task_id'], prep['request'], prep['budget']['policy_ref'])
        assert replay == {**created, 'replayed': True}
    with Session(engine()) as db, pytest.raises(HTTPException):
        writer.prepare(db, p, s['task_id'], {**prep['request'], 'reason': '不同正文'}, prep['budget']['policy_ref'])
    with Session(engine()) as db, pytest.raises(HTTPException):
        writer.prepare(db, p, s['task_id'], prep['request'], {**prep['budget']['policy_ref'], 'id': uid()})
