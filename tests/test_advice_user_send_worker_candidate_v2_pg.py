"""Synthetic dedicated PG tests: one failed worker attempt is never reclaimed."""
import pytest
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_user_send_worker_candidate_v2 as worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending, create
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root


def test_disabled_worker_cannot_claim_or_send(pending):
    s,created,_=pending;issued=create(pending)
    with Session(engine()) as db:
        item=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with pytest.raises(RuntimeError):worker.tick()
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);assert row.status=='queued' and row.attempts==0
        assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_failed_attempt_survives_restart_without_automatic_repeat(pending,monkeypatch,spool_root):
    s,created,_=pending;issued=create(pending)
    with Session(engine()) as db:
        item=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    monkeypatch.setattr(settings(),'v2_user_send_test_enabled',True)
    monkeypatch.setattr(settings(),'v2_user_send_worker_enabled',True)
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append('attempted');raise HTTPException(409,'合成来源暂不可用')
    monkeypatch.setattr(worker.lease,'acquire',unavailable)
    assert worker.tick()['failed']==1
    engine().dispose()
    assert worker.tick()['claimed']==0 and calls==['attempted']
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);assert row.status=='failed' and row.attempts==1
        assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_stale_worker_cannot_mark_another_lease_failed(pending):
    s,created,_=pending;issued=create(pending)
    with Session(engine()) as db:
        item=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with Session(engine()) as db:
        proof=control.claim(db,item['control_id']);db.commit()
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);row.lease_token=str(uuid4());db.commit()
    worker._record_failure(proof,'preparation')
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);assert row.status=='running' and row.attempts==1
