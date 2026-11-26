"""Real synthetic PostgreSQL inventory stress; no external network or user data."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents.db import engine
from generic_agents.models import Audit, Plan, Task, uid
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled


def test_actual_valid_v1_history_over_1000_fails_closed(synthetic_custody):
    """Record the remaining limit with genuine linked synthetic history."""
    s=synthetic_custody;p=s['principal'];ids=[]
    with Session(engine()) as db:
        for _ in range(1001):ids.append(add_cancelled(db,s))
        db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id'])
        assert db.scalar(select(func.count()).select_from(Plan).where(Plan.task_id==task.id))==1002
        # Both extremes are individually authentic v1 originals, with their
        # saved operation/receipt relationships checked by the real reader.
        for identity in (ids[0],ids[-1]):
            prep,job=legacy._saved(db,p,task,db.get(Plan,identity))
            assert prep['scope']['task_id']==task.id and job is not None
        # Prove the explicit row cap, rather than accepting an unrelated 409
        # while building or individually validating the originals.
        with pytest.raises(HTTPException) as limited:
            legacy._rows(db,p,Plan,Plan.task_id==task.id)
        assert limited.value.status_code==409
        with pytest.raises(HTTPException) as inventory:
            common.read(db,p,task)
        assert inventory.value.status_code==409


def test_task_row_lock_alone_does_not_stop_noncooperative_orphan_audit(synthetic_custody):
    """Expose why pagination cannot rely only on cooperative Task locks."""
    s=synthetic_custody;p=s['principal'];committed=Event()
    def orphan_writer():
        with Session(engine()) as db:
            db.add(Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
                object_id=uid(),action='same_goal_advice.unlimited_future_v99',
                data={'task_id':s['task_id']}));db.commit()
            committed.set()
    with Session(engine()) as reader,ThreadPoolExecutor(max_workers=1) as pool:
        task=reader.get(Task,s['task_id'])
        assert common.read(reader,p,task)=={'v1_calls':0,'queued_v2':1}
        future=pool.submit(orphan_writer)
        assert committed.wait(10),'独立审计写入意外被任务行锁阻塞'
        future.result(timeout=10)
        # A fresh statement detects the orphan, but a write based only on the
        # earlier complete scan would have a time-of-check gap.
        with pytest.raises(HTTPException):common.read(reader,p,task)
        reader.rollback()
