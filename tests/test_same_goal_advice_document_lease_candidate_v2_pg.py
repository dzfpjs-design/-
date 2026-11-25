"""Isolated PG, synthetic source and Goal. No public route or real cloud."""
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import leases
from generic_agents import same_goal_advice_document_lease_candidate_v2 as worker
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def start(s, created):
    p=s['principal']
    with Session(engine()) as db:
        control=worker.register(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:
        token=worker.claim(db,p,control);db.commit()
    return control,token


def process(s,control,token):
    with Session(engine()) as db:
        leases.attach(db,control,token);db.info['worker_actor']=s['principal']
        result=worker.process(db,s['principal'],control,token);db.commit();return result


def test_saved_receipt_reclaimed_and_settled_without_duplicate(ready):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    assert process(s,control,token)['replayed'] is False
    # Simulate a process death after the receipt commit and before settlement.
    with Session(engine()) as db:
        row=db.get(Job,control);row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        replacement=worker.claim(db,p,control);db.commit()
    assert replacement!=token and process(s,control,replacement)['replayed'] is True
    with Session(engine()) as db:
        worker.settle(db,p,control,replacement);db.commit()
    with Session(engine()) as db:
        assert worker.register(db,p,s['task_id'],created['job_id'])==control
    with Session(engine()) as db:
        assert db.get(Job,control).status=='succeeded'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],Audit.action==worker.v2.DOCUMENT_PREPARED))==1
        assert not list(db.scalars(select(CloudCall)))


def test_revocation_blocks_processing_and_preserves_queued_job(ready):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with pytest.raises((HTTPException,leases.ExecutionUnauthorized,leases.LeaseLost)):
        process(s,control,token)
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert not list(db.scalars(select(CloudCall)))


def test_stale_lease_cannot_write_and_actor_deactivation_blocks_reclaim(ready):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        row=db.get(Job,control);row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        replacement=worker.claim(db,p,control);db.commit()
    with pytest.raises((leases.LeaseLost,HTTPException)):
        process(s,control,token)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        membership.role='viewer';db.commit()
    with pytest.raises((HTTPException,leases.ExecutionUnauthorized,leases.LeaseLost)):
        process(s,control,replacement)
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert not list(db.scalars(select(CloudCall)))


def test_interrupted_receipt_transaction_reclaims_original_job(ready,monkeypatch):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    save=worker.document.evidence.save_evidence
    def interrupted(*args,**kwargs):
        save(*args,**kwargs)
        raise RuntimeError('synthetic termination after blob write')
    with monkeypatch.context() as patch:
        patch.setattr(worker.document.evidence,'save_evidence',interrupted)
        with pytest.raises(RuntimeError):process(s,control,token)
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],Audit.action==worker.v2.DOCUMENT_PREPARED))==0
        row=db.get(Job,control);row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        replacement=worker.claim(db,p,control);db.commit()
    assert process(s,control,replacement)['replayed'] is False
    with Session(engine()) as db:
        worker.settle(db,p,control,replacement);db.commit()
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],Audit.action==worker.v2.DOCUMENT_PREPARED))==1


def test_control_record_tampering_is_rejected(ready):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        row=db.get(Job,control);row.data={**row.data,'actor_id':'00000000-0000-0000-0000-000000000000'};db.commit()
    with pytest.raises(HTTPException):process(s,control,token)
    with Session(engine()) as db: assert db.get(Job,created['job_id']).status=='queued'


def test_new_session_of_same_actor_can_finish_after_revocation(ready,client,account):
    from generic_agents.security import Principal
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    assert process(s,control,token)['replayed'] is False
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with pytest.raises(HTTPException), Session(engine()) as db:
        worker.settle(db,p,control,token)
    username,password,_=account
    assert client.post('/api/login',json={'username':username,'password':password}).status_code==200
    with Session(engine()) as db:
        session=db.scalar(select(AuthSession).where(AuthSession.user_id==p.user_id,AuthSession.id!=p.session_id,
            AuthSession.revoked.is_(False)).order_by(AuthSession.created_at.desc()))
        assert session is not None
        renewed=Principal(p.user_id,p.workspace_id,p.role,session.id)
        worker.settle(db,renewed,control,token);db.commit()
    with Session(engine()) as db:
        assert db.get(Job,control).status=='succeeded'
        assert not list(db.scalars(select(CloudCall)))


def test_existing_register_waits_on_control_before_locking_task(ready):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import text
    s,created,_=ready;p=s['principal'];control,_=start(s,created);begun=Event();pid=[]
    def registration():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            pid.append(db.scalar(select(func.pg_backend_pid())));begun.set()
            result=worker.register(db,p,s['task_id'],created['job_id']);db.commit();return result
    with Session(engine()) as first,ThreadPoolExecutor(max_workers=1) as pool:
        first.scalar(select(Job).where(Job.id==control).with_for_update())
        owner=first.scalar(select(func.pg_backend_pid()))
        future=pool.submit(registration);assert begun.wait(3)
        import time
        deadline=time.monotonic()+3;blocked=False
        while time.monotonic()<deadline:
            if first.scalar(select(func.pg_blocking_pids(pid[0])))==[owner]:blocked=True;break
            time.sleep(.05)
        assert blocked
        # A reversed Task -> control order would make this NOWAIT fail.
        assert first.scalar(select(worker.Task.id).where(worker.Task.id==s['task_id']).with_for_update(nowait=True))==s['task_id']
        first.rollback()
        assert future.result(timeout=5)==control


def test_session_expiry_before_commit_rolls_back_receipt(ready):
    s,created,_=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        session=db.get(AuthSession,p.session_id)
        session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=2);db.commit()
    with pytest.raises(HTTPException), Session(engine()) as db:
        leases.attach(db,control,token);db.info['worker_actor']=p
        worker.process(db,p,control,token)
        db.execute(select(func.pg_sleep(2.1)))
        db.commit()
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],Audit.action==worker.v2.DOCUMENT_PREPARED))==0
