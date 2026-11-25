"""Current v2 preview recheck with disposable PG and synthetic evidence.

This tests the local prerequisite, not Start, transport, real approvals or
any model call. Goal frame/evaluation/source-selection remain test substitutes.
"""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Plan, Task
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_store_v2 as custody
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def saved(db,s,created):
    p=s['principal'];task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id'])
    prep,_=custody.read_prepared(db,p,task,plan)
    event=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
    return task,plan,prep,event


def test_current_recheck_keeps_preview_unsent(prepared_preview):
    s,created,request,frame=prepared_preview;p=s['principal']
    with Session(engine()) as db:subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        task,plan,prep,event=saved(db,s,created)
        assert subject.verify_current_for_start(db,p,task,plan,prep,event)==frame
        assert not list(db.scalars(select(CloudCall)))


def test_revoked_current_grant_blocks_start_guard(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        _,_,_,event=saved(db,s,created);identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
    with Session(engine()) as db:
        task,plan,prep,event=saved(db,s,created)
        with pytest.raises(HTTPException):subject.verify_current_for_start(db,p,task,plan,prep,event)
        assert not list(db.scalars(select(CloudCall)))


def test_old_preview_not_valid_for_current_start(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:first=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        subject.prepare(db,p,s['task_id'],created['plan_id'],{**request,'request_id':str(uuid4()),'expected_plan_version':3});db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id']);prep,_=custody.read_prepared(db,p,task,plan)
        old=next(e for e in db.scalars(select(Audit).where(Audit.action==subject.ACTION)) if e.data['request']['request_id']==request['request_id'])
        with pytest.raises(HTTPException):subject.verify_current_for_start(db,p,task,plan,prep,old)
