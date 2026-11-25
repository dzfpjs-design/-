"""Disposable-only migration experiment: no production DDL or data repair."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import copy
import time
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from generic_agents import evidence
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_file_sequential_candidate_v2 as sequential
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as guard
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Job
from tests.test_same_goal_advice_document_lease_candidate_v2_pg import start
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

INDEX='ga_test_advice_file_intent_action_object_unique'
CREATE=("CREATE UNIQUE INDEX CONCURRENTLY " + INDEX + " ON audit_events (workspace_id, object_id) "
    "WHERE action = 'same_goal_advice.file_intent_candidate_v2'")
DROP='DROP INDEX IF EXISTS '+INDEX

@pytest.fixture
def disposable_index():
    with Session(engine()) as db:
        guard.require_disposable_database(db);db.execute(text(DROP));db.commit()
    yield
    with Session(engine()) as db:
        guard.require_disposable_database(db);db.execute(text(DROP));db.commit()


def original(db,p,job_id):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==intent._key(job_id)))
    event=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==intent.ACTION))
    return row,event


def duplicate(p,row,event):
    return Audit(workspace_id=p.workspace_id,object_id=row.id,actor_id=event.actor_id,action=intent.ACTION,data=copy.deepcopy(event.data))


def index_flags(db):
    return db.execute(text("SELECT i.indisvalid,i.indisready,i.indisunique,i.indislive "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname=:name"),{'name':INDEX}).one_or_none()


def create_index():
    external=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',hide_parameters=True)
    try:
        with external.connect() as c:c.execute(text(CREATE))
    finally:external.dispose()


def test_existing_duplicate_aborts_build_leaves_invalid_index_and_preserves_records(ready,disposable_index):
    s,created,_=ready;p=s['principal']
    with Session(engine()) as db:
        row,event=original(db,p,created['job_id']);record_id=row.id
        db.add(duplicate(p,row,event));db.commit()
    with pytest.raises(IntegrityError):create_index()
    with Session(engine()) as db:
        flags=index_flags(db);assert flags is not None and not flags.indisvalid
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.object_id==record_id,Audit.action==intent.ACTION))==2
        # A migration gate must reject invalid/partial builds regardless of
        # whether this PostgreSQL version has already marked them ready.
        assert not (flags.indisvalid and flags.indisready and flags.indisunique and flags.indislive)


def test_unique_index_blocks_phantom_writer_at_file_write(ready,disposable_index,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);create_index()
    with Session(engine()) as db:
        flags=index_flags(db);assert flags is not None and all(flags)
    external=create_engine(settings().database_url,hide_parameters=True);attempts=[];save=evidence.save_evidence
    def inject(*args,**kwargs):
        with pytest.raises(IntegrityError),Session(external) as db:
            row,event=original(db,p,created['job_id']);db.add(duplicate(p,row,event));db.commit()
        attempts.append('rejected');return save(*args,**kwargs)
    monkeypatch.setattr(evidence,'save_evidence',inject)
    try:
        assert sequential.process(p,control,token)['replayed'] is False
        assert attempts==['rejected']
        with Session(engine()) as db:
            row,_=original(db,p,created['job_id'])
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==row.id,Audit.action==intent.ACTION))==1
    finally:external.dispose()


def test_concurrent_delete_rollback_cannot_admit_duplicate(ready,disposable_index):
    s,created,_=ready;p=s['principal'];create_index()
    external=create_engine(settings().database_url,pool_size=3,max_overflow=0,hide_parameters=True)
    try:
        started=Event();backend=[]
        def insert():
            try:
                with Session(external) as db:
                    db.execute(text("SET LOCAL lock_timeout='5s'"))
                    backend.append(db.scalar(select(func.pg_backend_pid())))
                    row,event=original(db,p,created['job_id']);db.add(duplicate(p,row,event));started.set();db.commit()
                return 'unexpected-success'
            except IntegrityError:return 'rejected'
        with Session(external) as deleter, ThreadPoolExecutor(max_workers=1) as pool:
            row,event=original(deleter,p,created['job_id']);deleter.delete(event);deleter.flush()
            owner=deleter.scalar(select(func.pg_backend_pid()));future=pool.submit(insert);assert started.wait(3)
            deadline=time.monotonic()+3;blocked=False
            while time.monotonic()<deadline:
                if owner in deleter.scalar(select(func.pg_blocking_pids(backend[0]))):blocked=True;break
                time.sleep(.05)
            assert blocked;deleter.rollback();assert future.result(timeout=5)=='rejected'
        with Session(engine()) as db:
            row,_=original(db,p,created['job_id'])
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==row.id,Audit.action==intent.ACTION))==1
    finally:external.dispose()
