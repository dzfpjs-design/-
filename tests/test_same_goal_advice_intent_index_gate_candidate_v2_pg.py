"""Dedicated PG tests only: real catalog locks, synthetic Task and sources."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import copy
import time
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from generic_agents import evidence
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_file_sequential_candidate_v2 as sequential
from generic_agents import same_goal_advice_intent_index_migration_candidate_v2 as migration
from generic_agents import same_goal_advice_intent_index_gate_candidate_v2 as gate
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as guard
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Job
from tests.test_same_goal_advice_document_lease_candidate_v2_pg import start
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

@pytest.fixture(autouse=True)
def installed_index(isolate_worker_queue):
    result=migration.rehearse();assert result['status'] in {'installed','already_installed'}
    yield
    with Session(engine()) as db:
        guard.require_disposable_database(db)
        for name in migration.NAMES:db.execute(text('DROP INDEX IF EXISTS public.'+name))
        db.commit()


def record(db,p,job_id):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==intent._key(job_id)))
    audit=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==intent.ACTION))
    return row,audit


def drop_index():
    with Session(engine()) as db:
        db.execute(text('DROP INDEX public.'+migration.NAMES[0]));db.commit()


def assert_no_file(p,resolved):
    assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()


def test_missing_index_rejects_registration_and_processing(ready):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);drop_index()
    with pytest.raises(HTTPException),Session(engine()) as db:intent.register(db,p,s['task_id'],created['job_id'])
    with pytest.raises(HTTPException):sequential.process(p,control,token)
    assert_no_file(p,resolved)


def test_foreign_same_name_is_not_treated_as_unique_index(ready):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);drop_index()
    with Session(engine()) as db:
        db.execute(text('CREATE INDEX '+migration.NAMES[0]+' ON public.audit_events (object_id)'));db.commit()
    with pytest.raises(HTTPException):sequential.process(p,control,token)
    assert_no_file(p,resolved)
    with engine().connect() as c:assert migration._catalog(c,migration.NAMES[0])['state']=='foreign_or_unknown'


def test_invalid_index_and_duplicate_history_are_retained_and_refuse_write(ready):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);drop_index()
    with Session(engine()) as db:
        row,first=record(db,p,created['job_id']);db.add(Audit(workspace_id=p.workspace_id,object_id=row.id,
            actor_id=first.actor_id,action=intent.ACTION,data=copy.deepcopy(first.data)));db.commit()
    ext=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',hide_parameters=True)
    try:
        with pytest.raises(IntegrityError),ext.connect() as conn:migration._build(conn,migration.NAMES[0])
        with pytest.raises(HTTPException):sequential.process(p,control,token)
        assert_no_file(p,resolved)
        with Session(engine()) as db:
            row,_=record(db,p,created['job_id'])
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==row.id,Audit.action==intent.ACTION))==2
        with engine().connect() as conn:assert migration._catalog(conn,migration.NAMES[0])['state']=='invalid'
    finally:ext.dispose()


def test_index_drop_after_preflight_is_detected_before_any_write(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);ext=create_engine(settings().database_url,hide_parameters=True)
    original=sequential.Session;counter=0
    class Intercept(original):
        def __enter__(self):
            nonlocal counter
            counter+=1
            if counter==2:
                with Session(ext) as db:db.execute(text('DROP INDEX public.'+migration.NAMES[0]));db.commit()
            return super().__enter__()
    monkeypatch.setattr(sequential,'Session',Intercept)
    try:
        with pytest.raises(HTTPException):sequential.process(p,control,token)
        assert_no_file(p,resolved)
    finally:ext.dispose()


def test_fence_blocks_concurrent_drop_but_allows_ordinary_audit_insert(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    ext=create_engine(settings().database_url,pool_size=3,max_overflow=0,hide_parameters=True)
    started=Event();release=Event();drop_started=Event();worker_pid=[];drop_pid=[];save=evidence.save_evidence
    def pause(db,*args,**kwargs):
        worker_pid.append(db.scalar(select(func.pg_backend_pid())));started.set();assert release.wait(8)
        return save(db,*args,**kwargs)
    monkeypatch.setattr(evidence,'save_evidence',pause)
    def drop():
        with ext.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text("SET statement_timeout='8s'"))
            drop_pid.append(conn.scalar(select(func.pg_backend_pid())));drop_started.set()
            conn.execute(text('DROP INDEX CONCURRENTLY public.'+migration.NAMES[0]))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            work=pool.submit(sequential.process,p,control,token);assert started.wait(5)
            removing=pool.submit(drop);assert drop_started.wait(3)
            deadline=time.monotonic()+4;blocked=False
            while time.monotonic()<deadline:
                with ext.connect() as probe:
                    if worker_pid[0] in probe.scalar(select(func.pg_blocking_pids(drop_pid[0]))):blocked=True;break
                time.sleep(.05)
            assert blocked and not removing.done()
            with ext.connect() as probe:assert migration._catalog(probe,migration.NAMES[0])['state']=='valid'
            # SHARE UPDATE EXCLUSIVE does not block normal audit insertion.
            with Session(ext) as db:
                db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,object_id=created['plan_id'],
                    action='synthetic.unrelated',data={}));db.commit()
            release.set();assert work.result(timeout=8)['replayed'] is False
            removing.result(timeout=8)
        assert (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
        with engine().connect() as c:assert migration._catalog(c,migration.NAMES[0])['state']=='missing'
    finally:release.set();ext.dispose()


def test_normal_unfenced_duplicate_insert_is_rejected_by_actual_index(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    ext=create_engine(settings().database_url,hide_parameters=True);save=evidence.save_evidence;attempts=[]
    def duplicate(*args,**kwargs):
        with pytest.raises(IntegrityError),Session(ext) as db:
            row,first=record(db,p,created['job_id']);db.add(Audit(workspace_id=p.workspace_id,object_id=row.id,
                actor_id=first.actor_id,action=intent.ACTION,data=copy.deepcopy(first.data)));db.commit()
        attempts.append('rejected');return save(*args,**kwargs)
    monkeypatch.setattr(evidence,'save_evidence',duplicate)
    try:
        assert sequential.process(p,control,token)['replayed'] is False and attempts==['rejected']
        assert (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    finally:ext.dispose()


@pytest.mark.parametrize('ddl',[
    'REINDEX INDEX CONCURRENTLY public.'+migration.NAMES[0],
    "CREATE UNIQUE INDEX CONCURRENTLY ga_test_intent_extra ON public.audit_events (workspace_id, object_id) WHERE action = 'synthetic.extra'",
])
def test_reindex_and_new_index_cannot_begin_during_gate(ddl,account):
    ext=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',hide_parameters=True)
    try:
        with Session(engine()) as keeper:
            gate.require(keeper,account[2])
            with ext.connect() as conn:
                conn.execute(text("SET lock_timeout='500ms'"))
                with pytest.raises(Exception) as failure:conn.execute(text(ddl))
                # Distinguish the expected lock wait from syntax/permission errors.
                assert getattr(failure.value.orig,'sqlstate',None)=='55P03'
            assert migration._catalog(keeper.connection(),migration.NAMES[0])['state']=='valid'
            keeper.rollback()
    finally:ext.dispose()


def test_rename_retains_physical_unique_oid_and_blocks_drop_and_duplicates(ready):
    s,created,_=ready;p=s['principal'];ext=create_engine(settings().database_url,pool_size=2,max_overflow=0,hide_parameters=True)
    renamed='ga_test_intent_index_renamed'
    try:
        with Session(engine()) as keeper:
            gate.require(keeper,p.workspace_id)
            before=migration._catalog(keeper.connection(),migration.NAMES[0]);oid=before['oid']
            with ext.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
                conn.execute(text('ALTER INDEX public.'+migration.NAMES[0]+' RENAME TO '+renamed))
                state=conn.execute(text("SELECT i.indisvalid,i.indisready,i.indisunique,i.indislive "
                    "FROM pg_index i WHERE i.indexrelid=:oid"),{'oid':oid}).one()
                assert all(state)
                conn.execute(text("SET lock_timeout='500ms'"))
                with pytest.raises(Exception) as failure:conn.execute(text('DROP INDEX CONCURRENTLY public.'+renamed))
                assert getattr(failure.value.orig,'sqlstate',None)=='55P03'
            with pytest.raises(IntegrityError),Session(ext) as db:
                row,first=record(db,p,created['job_id']);db.add(Audit(workspace_id=p.workspace_id,object_id=row.id,
                    actor_id=first.actor_id,action=intent.ACTION,data=copy.deepcopy(first.data)));db.commit()
            assert keeper.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation=:oid AND mode='AccessShareLock' AND granted)"),{'oid':oid}) is True
            keeper.rollback()
        with ext.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text('ALTER INDEX public.'+renamed+' RENAME TO '+migration.NAMES[0]))
    finally:ext.dispose()


def test_gate_contention_fails_fast_and_same_control_retries_without_duplicate(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    ext=create_engine(settings().database_url,pool_size=3,max_overflow=0,hide_parameters=True)
    monkeypatch.setattr(sequential,'engine',lambda:ext)
    try:
        with Session(ext) as keeper:
            before=keeper.scalar(text("SELECT current_setting('enable_seqscan')"))
            gate.require(keeper,p.workspace_id)
            assert keeper.scalar(text("SELECT current_setting('enable_seqscan')"))==before
            with pytest.raises(HTTPException) as failure:sequential.process(p,control,token)
            assert failure.value.status_code==409 and '占用' in failure.value.detail
            assert_no_file(p,resolved)
            keeper.rollback()
        assert sequential.process(p,control,token)['replayed'] is False
        assert sequential.process(p,control,token)['replayed'] is True
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],
                Audit.action==sequential.worker.v2.DOCUMENT_PREPARED))==1
    finally:ext.dispose()
