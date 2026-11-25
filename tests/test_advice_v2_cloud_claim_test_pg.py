"""Test-only DDL; never use an application DB or delete historic records."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Capability,CloudCall
from generic_agents import same_goal_advice_cloud_claim_test_v2 as claim


def test_install_schema_and_verify_exact_constraints():
    with Session(engine()) as db:
        claim.install(db);db.commit()
    with Session(engine()) as db:
        claim.install(db);claim.require(db);db.rollback()


def test_preexisting_cloud_history_is_preserved_and_blocks_install(account):
    with Session(engine()) as db:
        cap=Capability(workspace_id=account[2],namespace='synthetic',name=uuid4().hex,release_version='1.0.0',
            kind='foundation_model',status='published',content_hash='a'*64,data={})
        db.add(cap);db.flush()
        call=CloudCall(workspace_id=account[2],capability_id=cap.id,reserved_tokens=1,charged_tokens=1,
            data={'same_goal_advice_unlimited_v2':{'job_id':str(uuid4()),'plan_id':str(uuid4())}})
        db.add(call);db.commit();identity=call.id
    with Session(engine()) as db:
        before=db.scalar(text("select to_regclass('public.ga_test_advice_v2_dispatch_claims')"))
        with pytest.raises(HTTPException):claim.install(db)
        db.rollback()
    with Session(engine()) as db:
        assert db.get(CloudCall,identity) is not None
        assert db.scalar(text("select to_regclass('public.ga_test_advice_v2_dispatch_claims')")) == before


def _rows(account):
    from generic_agents.models import Audit,Job,Plan,Task,User
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));workspace=account[2]
        cap=Capability(workspace_id=workspace,namespace='synthetic',name=uuid4().hex,release_version='1.0.0',
            kind='foundation_model',status='published',content_hash='a'*64,data={});db.add(cap);db.flush()
        task=Task(workspace_id=workspace,title='synthetic',owner_id=user.id,package_id=cap.id,data={});db.add(task);db.flush()
        plan=Plan(workspace_id=workspace,task_id=task.id,data={});db.add(plan)
        job=Job(workspace_id=workspace,kind='generate_same_goal_advice_unlimited_v2',idempotency_key=uuid4().hex,data={});db.add(job);db.flush()
        calls=[];audits=[]
        for _ in range(2):
            c=CloudCall(workspace_id=workspace,capability_id=cap.id,reserved_tokens=1,charged_tokens=1,data={})
            a=Audit(workspace_id=workspace,actor_id=user.id,action='synthetic.test',object_id=plan.id,data={})
            db.add_all([c,a]);db.flush();calls.append(c.id);audits.append(a.id)
        db.commit();return workspace,plan.id,job.id,calls,audits


def test_unique_constraint_blocks_parallel_same_identity(account):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.exc import IntegrityError
    with Session(engine()) as db:claim.install(db);db.commit()
    workspace,plan,job,calls,audits=_rows(account);barrier=Barrier(2)
    def insert(i):
        with Session(engine()) as db:
            barrier.wait(timeout=5)
            try:
                db.execute(text('INSERT INTO public.ga_test_advice_v2_dispatch_claims '
                    '(workspace_id,job_id,plan_id,cloud_call_id,audit_id) VALUES (:w,:j,:p,:c,:a)'),
                    {'w':workspace,'j':job,'p':plan,'c':calls[i],'a':audits[i]})
                db.commit();return 'inserted'
            except IntegrityError:
                db.rollback();return 'duplicate'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(insert,[0,1]))
    assert sorted(results)==['duplicate','inserted']
    with Session(engine()) as db:
        assert db.scalar(text('SELECT count(*) FROM public.ga_test_advice_v2_dispatch_claims'))==1


def test_unmarked_v2_job_cloud_call_is_not_treated_as_absent(account):
    from generic_agents.models import Job
    with Session(engine()) as db:claim.install(db);db.commit()
    workspace,plan,job,calls,audits=_rows(account)
    with Session(engine()) as db:
        row=db.get(CloudCall,calls[0]);row.data={'gateway_dispatch':{'identity':{'job_id':job}}};db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):claim.install(db)
        db.rollback()
    with Session(engine()) as db:
        assert db.get(CloudCall,calls[0]) is not None and db.get(Job,job) is not None
