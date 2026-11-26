"""Synthetic policy-only cross-version fence on the disposable PG."""
import copy
from threading import Event, Thread
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_store_v302 as old
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Plan, Task
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def policy_only(s):
    with Session(engine(), expire_on_commit=False) as db:
        db.execute(delete(Job).where(Job.id == s['job_id']))
        db.execute(delete(Audit).where(Audit.id == s['accepted_id']))
        db.execute(delete(Plan).where(Plan.id == s['plan_id']))
        db.commit()


def new_request(s):
    value=copy.deepcopy(s['prep']['request']);value['request_id']=str(uuid4());return value


def test_v1_creation_rejects_policy_only_without_creating_plan(synthetic_custody):
    s=synthetic_custody;policy_only(s)
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as failure:
            old.prepare(db,s['principal'],s['task_id'],new_request(s))
        assert '已有待处理的业务建议' in str(failure.value.detail)
        db.rollback()
        assert db.scalar(select(Plan.id).where(Plan.task_id==s['task_id'])) is None
        assert not list(db.scalars(select(CloudCall.id)))


def test_task_lock_serializes_policy_commit_before_v1_creation(synthetic_custody):
    s=synthetic_custody;policy_only(s)
    with Session(engine(),expire_on_commit=False) as setup:
        saved=setup.get(Audit,s['policy_id'])
        event={'id':saved.id,'workspace_id':saved.workspace_id,'actor_id':saved.actor_id,
            'action':saved.action,'object_id':saved.object_id,'created_at':saved.created_at,'data':copy.deepcopy(saved.data)}
        setup.delete(saved);setup.commit()
    owner=Session(engine(),expire_on_commit=False);started,done=Event(),Event();outcome=[]
    try:
        owner.scalar(select(Task).where(Task.id==s['task_id']).with_for_update())
        owner.add(Audit(**event));owner.flush()
        def attempt():
            with Session(engine()) as db:
                started.set()
                try: old.prepare(db,s['principal'],s['task_id'],new_request(s));outcome.append('created')
                except HTTPException as exc: outcome.append(str(exc.detail));db.rollback()
                finally: done.set()
        thread=Thread(target=attempt,daemon=True);thread.start();assert started.wait(3);assert not done.wait(.25)
        owner.commit();assert done.wait(15);thread.join()
        assert len(outcome)==1 and '已有待处理的业务建议' in outcome[0]
        with Session(engine()) as db:
            assert db.scalar(select(Plan.id).where(Plan.task_id==s['task_id'])) is None
            assert not list(db.scalars(select(CloudCall.id)))
    finally: owner.close()
