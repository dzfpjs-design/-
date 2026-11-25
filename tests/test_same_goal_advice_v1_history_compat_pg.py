"""Synthetic isolated PG custody; history reads only, never enables v2 writes."""
import copy
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Plan, Task, uid
from tests.test_same_goal_advice_common_inventory_v2_pg import add_synthetic_v1
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def add_cancelled(db, s):
    plan=add_synthetic_v1(db,s); prep=plan.data['preparation_snapshot']; p=s['principal']; job=plan.data['jobs']['prepare']
    request=uid(); audit=uid(); body={'request_id':request,'expected_plan_version':1,'reason':'合成历史测试'}
    expected={'schema':legacy.c.PLAN,'plan_id':plan.id,'task_id':s['task_id'],
        'preparation_snapshot_hash':prep['record_hash'],'request_id':request,
        'body_hash':digest(body),'job_id':job['id'],'actor_id':p.user_id}
    plan.data={**plan.data,'operations':[*plan.data['operations'],{'operation':'cancel','request_id':request,
        'body':body,'body_hash':digest(body),'actor_id':p.user_id,'audit_id':audit}]}
    plan.status='cancelled';plan.version=2
    db.add(Audit(id=audit,workspace_id=p.workspace_id,actor_id=p.user_id,object_id=plan.id,
        action=legacy.CANCELLED,data=expected)); db.flush(); return plan.id


def test_history_reads_old_valid_record_without_enabling_writes(synthetic_custody):
    s=synthetic_custody;p=s['principal']
    with Session(engine()) as db:
        old_id=add_cancelled(db,s);db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id'])
        rows=legacy._inventory(db,p,task,history_compatible=True)
        assert [row[0].id for row in rows]==[old_id]
        with pytest.raises(HTTPException):legacy._inventory(db,p,task)


def test_history_read_rejects_corrupt_v2_and_does_not_hide_unknown_schema(synthetic_custody):
    s=synthetic_custody;p=s['principal']
    with Session(engine()) as db:
        add_cancelled(db,s);job=db.get(Job,s['job_id']);job.available_at=db.scalar(select(legacy.func.clock_timestamp()));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):legacy._inventory(db,p,db.get(Task,s['task_id']),history_compatible=True)


def test_existing_http_history_reads_verified_v1_when_v2_exists(client,synthetic_custody,monkeypatch):
    s=synthetic_custody
    with Session(engine()) as db:
        old_id=add_cancelled(db,s); request_id=db.get(Plan,old_id).data['operations'][0]['request_id']; db.commit()
    def task_for(db,p,identity,**kwargs):
        task=db.get(Task,identity)
        if task is None or task.workspace_id!=p.workspace_id: raise HTTPException(404)
        return task
    monkeypatch.setattr(legacy.existing,'_task',task_for)
    monkeypatch.setattr(legacy,'_view',lambda db,p,task,plan,prep,job:{'id':plan.id,'status':plan.status})
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    r=client.get(base);assert r.status_code==200,r.text
    assert r.json()['records']==[{'id':old_id,'status':'cancelled'}]
    assert client.get(base+'/'+old_id).json()['id']==old_id
    assert client.get(base+'/operations/'+request_id).json()['id']==old_id
    assert client.get(base+'/'+s['plan_id']).status_code==404
    with Session(engine()) as db:
        job=db.get(Job,s['job_id']);job.available_at=db.scalar(select(legacy.func.clock_timestamp()));db.commit()
    assert client.get(base).status_code==409
    with Session(engine()) as db:
        assert db.scalar(select(CloudCall.id).limit(1)) is None
        assert db.get(Plan,s['plan_id']) is not None
        db.get(AuthSession,s['principal'].session_id).revoked=True;db.commit()
    assert client.get(base).status_code==401


def test_history_holds_task_lock_until_reader_transaction_finishes(synthetic_custody):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    s=synthetic_custody;p=s['principal']; acquired=Event();begun=Event()
    def writer():
        with Session(engine()) as other:
            begun.set()
            other.scalar(select(Task).where(Task.id==s['task_id']).with_for_update())
            acquired.set()
    with Session(engine()) as db, ThreadPoolExecutor(max_workers=1) as pool:
        legacy._inventory(db,p,db.get(Task,s['task_id']),history_compatible=True)
        future=pool.submit(writer)
        try:
            assert begun.wait(3)
            assert not acquired.wait(.3)
        finally:
            db.rollback()
        future.result(timeout=5)
        assert acquired.is_set()
