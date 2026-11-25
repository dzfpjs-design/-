"""Disposable PG only; synthetic historical policy, not real Goal or a send path."""
import copy
from datetime import timedelta
from threading import Event, Thread
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_expired_cancel_candidate_v2 as cancel
from generic_agents import same_goal_advice_common_inventory_v2 as inventory
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Plan, Task, uid
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def expired(synthetic_custody):
    """Construct internally consistent synthetic past facts before the test starts."""
    s=synthetic_custody; p=s['principal']
    with Session(engine(),expire_on_commit=False) as db:
        clock=db.scalar(select(func.clock_timestamp())); issued=clock-timedelta(minutes=2)
        expires=clock-timedelta(seconds=30); created=clock-timedelta(minutes=1)
        pol=db.get(Audit,s['policy_id']); val=copy.deepcopy(pol.data)
        val['issued_at']=policy.instant(issued.isoformat()).isoformat();val['expires_at']=policy.instant(expires.isoformat()).isoformat();val['record_hash']=c.record_hash(val)
        pol.data=val;pol.created_at=issued
        auth=db.scalar(select(Audit).where(Audit.object_id==s['task_id'],Audit.action==policy.AUTHORIZATION))
        body=copy.deepcopy(auth.data['body']);body['expires_at']=policy.instant(expires.isoformat()).isoformat()
        auth.created_at=issued;auth.data=policy._auth_body(db.get(Task,s['task_id']),p,body,pol)
        plan=db.get(Plan,s['plan_id']);job=db.get(Job,s['job_id']);event=db.get(Audit,s['accepted_id'])
        data=copy.deepcopy(plan.data);prep=copy.deepcopy(data['preparation_snapshot']);prep.pop('record_hash')
        prep['budget']['policy_ref']={'id':pol.id,'record_hash':digest(val)}
        cap=db.get(Capability,prep['advice_capability_ref']['id'])
        prep=contracts.seal_preparation(prep,manifest=cap.data);data['preparation_snapshot']=prep
        plan.data=data;plan.created_at=created;job.created_at=created;event.created_at=created
        key=v2.job_key(s['task_id'],plan.id);job.data=v2.job_data(plan.id,prep,key)
        event.data=v2.accepted_data(plan.id,prep,job.id,key);db.flush()
        assert pol.created_at==auth.created_at
        assert digest(auth.data)==digest(policy._auth_body(db.get(Task,s['task_id']),p,body,pol)), 'synthetic auth mismatch'
        assert pol.data['expires_at']==policy.instant(body['expires_at']).isoformat()
        db.commit();s['prep']=prep
    return s


def body():return {'request_id':uid(),'expected_plan_version':1,'reason':'政策已到期，取消原准备'}


def test_unexpired_policy_refuses_cancellation(synthetic_custody):
    s=synthetic_custody
    with Session(engine()) as db, pytest.raises(HTTPException):cancel.cancel(db,s['principal'],s['task_id'],s['plan_id'],body())
    with Session(engine()) as db:assert not list(db.scalars(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)))


def test_expired_original_is_closed_without_changing_or_sending_and_replays(expired):
    s=expired;p=s['principal'];request=body()
    with Session(engine()) as db:
        original=copy.deepcopy(db.get(Plan,s['plan_id']).data);job_data=copy.deepcopy(db.get(Job,s['job_id']).data)
        result=cancel.cancel(db,p,s['task_id'],s['plan_id'],request);assert result['state']=='cancelled_expired' and not result['replayed'];db.commit()
    engine().dispose()
    with Session(engine()) as db:
        result=cancel.cancel(db,p,s['task_id'],s['plan_id'],request);assert result['replayed']
        plan=db.get(Plan,s['plan_id']);job=db.get(Job,s['job_id']);task=db.get(Task,s['task_id'])
        assert plan.data==original and plan.status=='queued' and job.data==job_data and job.status=='queued'
        assert inventory.read(db,p,task)['queued_v2']==0
        assert writer.prepare(db,p,task.id,s['prep']['request'],s['prep']['budget']['policy_ref'])['state']=='cancelled_expired'
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))
    with Session(engine()) as db, pytest.raises(HTTPException):cancel.cancel(db,p,s['task_id'],s['plan_id'],{**request,'reason':'不同内容'})
    with Session(engine()) as db, pytest.raises(HTTPException):document.process(db,p,s['task_id'],s['job_id'])


def test_concurrent_same_cancel_commits_once(expired):
    s=expired;p=s['principal'];request=body();started=Event();done=Event();out=[]
    def other():
        with Session(engine()) as db:
            started.set()
            try:out.append(cancel.cancel(db,p,s['task_id'],s['plan_id'],request));db.commit()
            finally:done.set()
    with Session(engine()) as first:
        assert not cancel.cancel(first,p,s['task_id'],s['plan_id'],request)['replayed']
        t=Thread(target=other,daemon=True);t.start();assert started.wait(3);assert not done.wait(.25)
        first.commit();assert done.wait(10);t.join()
    assert len(out)==1 and out[0]['replayed']
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==v2.CANCELLED_EXPIRED))==1


def test_rollback_or_bad_original_never_leaves_cancel_receipt(expired):
    s=expired;p=s['principal'];request=body()
    with Session(engine()) as db:
        cancel.cancel(db,p,s['task_id'],s['plan_id'],request);db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)))
        job=db.get(Job,s['job_id']);job.available_at=db.scalar(select(func.clock_timestamp()));db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):cancel.cancel(db,p,s['task_id'],s['plan_id'],request)
    with Session(engine()) as db:assert not list(db.scalars(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)))


def test_revoked_session_cannot_cancel(expired):
    from generic_agents.models import AuthSession
    s=expired;p=s['principal']
    with Session(engine()) as db: db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):cancel.cancel(db,p,s['task_id'],s['plan_id'],body())
    with Session(engine()) as db:assert not list(db.scalars(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)))


def test_cancellation_serializes_original_document_attempt(expired):
    s=expired;p=s['principal'];request=body();started=Event();done=Event();out=[]
    def other():
        with Session(engine()) as db:
            started.set()
            try:
                document.process(db,p,s['task_id'],s['job_id']);out.append('unexpected-success')
            except HTTPException:out.append('refused')
            finally:done.set()
    with Session(engine()) as first:
        cancel.cancel(first,p,s['task_id'],s['plan_id'],request)
        t=Thread(target=other,daemon=True);t.start();assert started.wait(3);assert not done.wait(.25)
        first.commit();assert done.wait(10);t.join()
    assert out==['refused']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==v2.CANCELLED_EXPIRED))==1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


def test_tampered_cancel_receipt_never_reopens_or_replays(expired):
    s=expired;p=s['principal'];request=body()
    with Session(engine()) as db:cancel.cancel(db,p,s['task_id'],s['plan_id'],request);db.commit()
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action==v2.CANCELLED_EXPIRED))
        event.data={**event.data,'body_hash':'0'*64};db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):inventory.read(db,p,db.get(Task,s['task_id']))
    with Session(engine()) as db, pytest.raises(HTTPException):cancel.cancel(db,p,s['task_id'],s['plan_id'],request)
    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))
