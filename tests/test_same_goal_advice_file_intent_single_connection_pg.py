"""Disposable PG with a one-connection pool; no public route or cloud calls."""
from threading import Event, Thread
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Job, Membership
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def single_pool():
    return create_engine(settings().database_url, pool_size=1, max_overflow=0, pool_timeout=1, hide_parameters=True)


def clear_intent(s,job_id):
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==s['principal'].workspace_id,Job.idempotency_key==intent._key(job_id)))
        for event in db.scalars(select(Audit).where(Audit.object_id==row.id,Audit.action==intent.ACTION)):db.delete(event)
        db.delete(row);db.commit()


def test_committed_intent_completes_with_only_one_database_connection(ready):
    s,created,resolved=ready;p=s['principal'];pool=single_pool()
    try:
        with Session(pool) as db:
            result=worker.process(db,p,s['task_id'],created['job_id']);assert not result['replayed'];db.commit()
        with Session(pool) as db:
            assert worker.process(db,p,s['task_id'],created['job_id'])['replayed']
            assert db.get(Job,created['job_id']).status=='succeeded'
            assert len(list(db.scalars(select(Evidence))))==1 and not list(db.scalars(select(CloudCall)))
    finally:pool.dispose()


def test_released_savepoint_is_not_a_committed_intent(ready):
    s,created,resolved=ready;p=s['principal'];clear_intent(s,created['job_id']);pool=single_pool()
    try:
        with Session(pool) as db:
            with db.begin_nested():intent.register(db,p,s['task_id'],created['job_id'])
            with pytest.raises(HTTPException):worker.process(db,p,s['task_id'],created['job_id'])
            db.rollback()
        assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
        with Session(pool) as db:
            assert not list(db.scalars(select(Job).where(Job.idempotency_key==intent._key(created['job_id']))))
    finally:pool.dispose()


def test_uncommitted_replacement_audit_is_not_a_committed_receipt(ready):
    s,created,resolved=ready;p=s['principal'];pool=single_pool()
    try:
        with Session(pool) as db:
            record=db.scalar(select(Job).where(Job.idempotency_key==intent._key(created['job_id'])))
            old=db.scalar(select(Audit).where(Audit.object_id==record.id,Audit.action==intent.ACTION))
            data=dict(old.data);db.delete(old);db.commit();record_id=record.id
        with Session(pool) as db:
            db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,object_id=record_id,action=intent.ACTION,data=data));db.flush()
            with pytest.raises(HTTPException):worker.process(db,p,s['task_id'],created['job_id'])
            db.rollback()
        assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    finally:pool.dispose()


def test_two_single_connection_pools_wait_for_registration_rollback(ready):
    s,created,resolved=ready;p=s['principal'];clear_intent(s,created['job_id']);first_pool=single_pool();second_pool=single_pool();started=Event();done=Event();result=[]
    def work():
        with Session(second_pool) as db:
            started.set()
            try:worker.process(db,p,s['task_id'],created['job_id']);result.append('unexpected')
            except HTTPException:result.append('refused')
            finally:done.set()
    try:
        with Session(first_pool) as db:
            intent.register(db,p,s['task_id'],created['job_id'])
            thread=Thread(target=work,daemon=True);thread.start();assert started.wait(3);assert not done.wait(.25)
            db.rollback();assert done.wait(10);thread.join()
        assert result==['refused']
        assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    finally:first_pool.dispose();second_pool.dispose()


def test_revocation_still_refuses_single_pool_processing(ready):
    s,created,resolved=ready;p=s['principal'];pool=single_pool()
    try:
        with Session(engine()) as db:
            row=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id));row.role='viewer';db.commit()
        with pytest.raises(HTTPException),Session(pool) as db:worker.process(db,p,s['task_id'],created['job_id'])
        assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    finally:pool.dispose()


def test_xid_wrap_ambiguity_and_wide_snapshot_fail_closed():
    wrap=1<<32;half=1<<31
    assert intent._nearby_xid(wrap+10,wrap-5,wrap,wrap+20)==wrap-5
    assert intent._nearby_xid(wrap-10,5,wrap-20,wrap+10)==wrap+5
    with pytest.raises(HTTPException):intent._nearby_xid(wrap+100,half+100,wrap+90,wrap+110)
    with pytest.raises(HTTPException):intent._nearby_xid(wrap+100,101,wrap+100-half,wrap+110)
    with pytest.raises(HTTPException):intent._nearby_xid(wrap+100,2,wrap+90,wrap+110)


def test_unknown_commit_status_never_authorizes_file_write(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];pool=single_pool()
    try:
        with Session(pool) as db:
            original=db.scalar
            def status_unknown(statement,*args,**kwargs):
                if 'pg_xact_status' in str(statement):return None
                return original(statement,*args,**kwargs)
            monkeypatch.setattr(db,'scalar',status_unknown)
            with pytest.raises(HTTPException):worker.process(db,p,s['task_id'],created['job_id'])
            db.rollback()
        assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    finally:pool.dispose()
