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


def test_start_persists_one_parked_job_and_exact_replay(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_common_inventory_v2 as common
    from generic_agents.models import Job
    from generic_agents.worker import claim
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    assert claim(first['job_id'],kinds=[start.KIND],workspace_id=p.workspace_id) is None
    engine().dispose()
    with Session(engine()) as db:
        assert start.begin(db,p,s['task_id'],created['plan_id'],body)==first
        job=db.get(Job,first['job_id']);assert job.kind==start.KIND and job.attempts==0
        assert job.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS
        assert db.get(Plan,created['plan_id']).status=='awaiting_dispatch'
        assert common.read(db,p,db.get(Task,s['task_id']))['queued_v2']==1
        assert not list(db.scalars(select(CloudCall)))
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],{**body,'reason':'不同申请'})


def test_start_with_revoked_grant_creates_no_job(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:
        _,_,_,event=saved(db,s,created);identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND)))
        assert not list(db.scalars(select(CloudCall)))


def test_tampered_start_job_cannot_replay(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:
        job=db.get(Job,first['job_id']);job.attempts=1;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        assert not list(db.scalars(select(CloudCall)))


def test_concurrent_same_start_nonce_creates_one_job(prepared_preview):
    from queue import Queue
    from threading import Thread
    from time import monotonic, sleep
    from sqlalchemy import func, text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal'];second_pid=Queue();results=[];errors=[]
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成并发测试'}
    def contender():
        try:
            with Session(engine()) as db:
                second_pid.put(db.scalar(select(func.pg_backend_pid())))
                results.append(start.begin(db,p,s['task_id'],created['plan_id'],body));db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        first_pid=first.scalar(select(func.pg_backend_pid()))
        original=start.begin(first,p,s['task_id'],created['plan_id'],body)
        thread=Thread(target=contender,daemon=True);thread.start();pid=second_pid.get(timeout=5)
        try:
            deadline=monotonic()+5;blocked=False
            while monotonic()<deadline:
                with engine().connect() as observer:
                    blocked=first_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    assert results==[original]
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Job).where(Job.kind==start.KIND))))==1
        assert len(list(db.scalars(select(Audit).where(Audit.action==start.ACTION))))==1
        assert not list(db.scalars(select(CloudCall)))


def test_old_receipt_still_replays_after_v2_start(prepared_preview,client,monkeypatch):
    from copy import deepcopy
    from generic_agents.contracts import digest
    from generic_agents.models import Capability, Job, Invocation, uid
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled
    from tests.test_same_goal_advice_v1_replay_compat_pg import use_synthetic_task
    from tests.test_same_goal_advice_contracts_v302 import scene
    from tests import test_same_goal_advice_common_inventory_v2_pg as factory
    s,created,request,_=prepared_preview;p=s['principal'];use_synthetic_task(monkeypatch)
    sample=scene();cap_id=uid();sample.preparation['advice_capability_ref']['id']=cap_id
    with Session(engine()) as db:
        db.add(Capability(id=cap_id,workspace_id=p.workspace_id,namespace='historical_start_fixture',
            name='historical_advice',release_version='1.0.0',kind='tool',status='published',
            content_hash=digest(sample.manifest),validated_hash=digest(sample.manifest),data=sample.manifest));db.flush()
        with monkeypatch.context() as m:
            m.setattr(factory,'contract_scene',lambda:sample);old_id=add_cancelled(db,s)
        old=db.get(Plan,old_id);old_prepare=deepcopy(old.data['operations'][0]['body']);db.commit()
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    response=client.post(base,json=old_prepare);assert response.status_code==201,response.text
    assert client.post(base,json={**old_prepare,'request_id':uid()}).status_code==409
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Job).where(Job.kind==start.KIND))))==1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))
