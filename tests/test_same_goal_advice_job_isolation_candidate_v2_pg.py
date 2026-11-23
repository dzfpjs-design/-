"""A changed availability timestamp must invalidate v2 custody/inventory."""
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Job, Plan, Task
from generic_agents.worker import claim
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.mark.parametrize('prepared', [False, True])
def test_changed_availability_is_rejected_by_original_and_common_readers(ready, prepared):
    s, created, _ = ready; p = s['principal']; job_id = created['job_id']
    if prepared:
        with Session(engine()) as db:
            worker.process(db, p, s['task_id'], job_id); db.commit()
    else:
        assert claim(job_id, kinds=[v2.PREPARE_KIND], workspace_id=p.workspace_id) is None
    with Session(engine()) as db:
        db.get(Job, job_id).available_at = db.scalar(select(func.clock_timestamp())); db.commit()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, created['plan_id'])
        with pytest.raises(HTTPException):
            (v2.read_prepared if prepared else v2.read_queued)(db, p, task, plan)
        with pytest.raises(HTTPException): common.read(db, p, task)
    if not prepared:
        # Actual old generic claim behavior: an incorrectly released timestamp
        # is eligible even though the generic worker lacks a v2 handler.
        acquired = claim(job_id, kinds=[v2.PREPARE_KIND], workspace_id=p.workspace_id)
        assert acquired is not None and acquired[0] == job_id
