"""Disposable-only migration rehearsals. Never repair or delete real history."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import copy
import time
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_intent_index_migration_candidate_v2 as migration
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as guard
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Job
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def disposable_indexes():
    # Cleanup is only for these fixed names in a verified disposable test DB;
    # the migration code never deletes an index or historical rows.
    def clear():
        with Session(engine()) as db:
            guard.require_disposable_database(db)
            for name in migration.NAMES:db.execute(text('DROP INDEX IF EXISTS public.'+name))
            db.commit()
    clear();yield;clear()


def original(db,p,job_id):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==intent._key(job_id)))
    audit=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,Audit.action==intent.ACTION))
    return row,audit


def duplicate(p,row,audit):
    return Audit(workspace_id=p.workspace_id,object_id=row.id,actor_id=audit.actor_id,action=intent.ACTION,data=copy.deepcopy(audit.data))


def catalog():
    with engine().connect() as conn:return [migration._catalog(conn,name) for name in migration.NAMES]


def count_original(p,job_id):
    with Session(engine()) as db:
        row,_=original(db,p,job_id)
        return db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.object_id==row.id,Audit.action==intent.ACTION))


def test_clean_install_is_idempotent_and_index_blocks_duplicate(ready,disposable_indexes):
    s,created,_=ready;p=s['principal']
    result=migration.rehearse();assert result=={'status':'installed','index':migration.NAMES[0],'invalid_retained':[]}
    assert migration.rehearse()=={'status':'already_installed','valid_indexes':[migration.NAMES[0]],'invalid_retained':[]}
    with pytest.raises(IntegrityError),Session(engine()) as db:
        row,audit=original(db,p,created['job_id']);db.add(duplicate(p,row,audit));db.commit()
    assert count_original(p,created['job_id'])==1


def test_duplicate_history_stops_without_ddl_or_record_deletion(ready,disposable_indexes):
    s,created,_=ready;p=s['principal']
    with Session(engine()) as db:
        row,audit=original(db,p,created['job_id']);db.add(duplicate(p,row,audit));db.commit()
    assert migration.rehearse()=={'status':'duplicate_history','invalid_retained':[]}
    assert [s['state'] for s in catalog()]==['missing','missing'] and count_original(p,created['job_id'])==2


def test_prior_failed_duplicate_build_is_retained_and_never_auto_repaired(ready,disposable_indexes):
    s,created,_=ready;p=s['principal']
    with Session(engine()) as db:
        row,audit=original(db,p,created['job_id']);db.add(duplicate(p,row,audit));db.commit()
    ext=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',hide_parameters=True)
    try:
        with pytest.raises(IntegrityError),ext.connect() as conn:migration._build(conn,migration.NAMES[0])
    finally:ext.dispose()
    before=catalog();assert before[0]['state']=='invalid'
    assert migration.rehearse()=={'status':'duplicate_history','invalid_retained':[migration.NAMES[0]]}
    assert catalog()==before and count_original(p,created['job_id'])==2


def test_foreign_same_name_is_never_dropped_or_modified(ready,disposable_indexes):
    with Session(engine()) as db:
        db.execute(text('CREATE INDEX '+migration.NAMES[0]+' ON public.audit_events (object_id)'));db.commit()
    before=catalog();assert before[0]['state']=='foreign_or_unknown'
    assert migration.rehearse()=={'status':'name_or_definition_conflict'}
    assert catalog()==before


def test_failed_concurrent_build_resumes_under_fixed_alternate_name_without_deletion(ready,disposable_indexes):
    s,created,_=ready;p=s['principal'];ext=create_engine(settings().database_url,pool_size=2,max_overflow=0,hide_parameters=True)
    try:
        backend=[];started=Event()
        def cancelled_builder():
            with ext.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
                conn.execute(text("SET statement_timeout='10s'"))
                backend.append(conn.scalar(select(func.pg_backend_pid())));started.set()
                with pytest.raises(DBAPIError):migration._build(conn,migration.NAMES[0])
        with Session(ext) as pending,ThreadPoolExecutor(max_workers=1) as pool:
            row,audit=original(pending,p,created['job_id']);pending.add(duplicate(p,row,audit));pending.flush()
            future=pool.submit(cancelled_builder);assert started.wait(3)
            deadline=time.monotonic()+5;active=False
            while time.monotonic()<deadline:
                with engine().connect() as probe:
                    if migration._building(probe):active=True;break
                time.sleep(.05)
            assert active
            with engine().connect() as probe:
                assert probe.scalar(select(func.pg_cancel_backend(backend[0]))) is True
            future.result(timeout=5);pending.rollback()
        before=catalog();assert before[0]['state']=='invalid' and before[1]['state']=='missing'
        result=migration.rehearse();assert result=={'status':'installed','index':migration.NAMES[1],
            'invalid_retained':[migration.NAMES[0]]}
        after=catalog();assert after[0]==before[0] and after[1]['state']=='valid'
        assert count_original(p,created['job_id'])==1
        assert migration.rehearse()=={'status':'already_installed','valid_indexes':[migration.NAMES[1]],
            'invalid_retained':[migration.NAMES[0]]}
    finally:ext.dispose()


def test_duplicate_committed_after_preflight_aborts_build_and_remains_for_review(ready,disposable_indexes,monkeypatch):
    s,created,_=ready;p=s['principal'];ext=create_engine(settings().database_url,hide_parameters=True);build=migration._build
    def insert_then_build(conn,name):
        with Session(ext) as db:
            row,audit=original(db,p,created['job_id']);db.add(duplicate(p,row,audit));db.commit()
        return build(conn,name)
    monkeypatch.setattr(migration,'_build',insert_then_build)
    try:
        result=migration.rehearse();assert result['status']=='build_failed_review'
        assert result['index']==migration.NAMES[0] and result['observed_state']=='invalid'
        assert result['invalid_retained']==[migration.NAMES[0]] and count_original(p,created['job_id'])==2
        assert migration.rehearse()=={'status':'duplicate_history','invalid_retained':[migration.NAMES[0]]}
    finally:ext.dispose()


def test_concurrent_rehearsals_are_serialized_without_waiting_on_each_other(ready,disposable_indexes,monkeypatch):
    ext=create_engine(settings().database_url,pool_size=2,max_overflow=0,hide_parameters=True)
    monkeypatch.setattr(migration,'engine',lambda:ext);build=migration._build;entered=Event();release=Event()
    def pause(conn,name):
        entered.set();assert release.wait(5);return build(conn,name)
    monkeypatch.setattr(migration,'_build',pause)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(migration.rehearse);assert entered.wait(3)
            second=pool.submit(migration.rehearse);assert second.result(timeout=3)=={'status':'migration_busy'}
            release.set();assert first.result(timeout=8)['status']=='installed'
    finally:release.set();ext.dispose()


def test_wrong_cluster_identity_fails_before_any_ddl(ready,disposable_indexes,monkeypatch):
    with monkeypatch.context() as patch:
        patch.setenv('GA_TEST_PG_SYSTEM_IDENTIFIER','0')
        with pytest.raises(HTTPException):migration.rehearse()
    assert [s['state'] for s in catalog()]==['missing','missing']


def test_uncooperative_active_build_is_detected_without_touching_its_index(ready,disposable_indexes,monkeypatch):
    s,created,_=ready;p=s['principal'];ext=create_engine(settings().database_url,pool_size=3,max_overflow=0,hide_parameters=True)
    monkeypatch.setattr(migration,'engine',lambda:ext)
    def concurrent_builder():
        with ext.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text("SET statement_timeout='8s'"));migration._build(conn,migration.NAMES[0])
    try:
        with Session(ext) as pending,ThreadPoolExecutor(max_workers=1) as pool:
            row,audit=original(pending,p,created['job_id']);pending.add(duplicate(p,row,audit));pending.flush()
            future=pool.submit(concurrent_builder);deadline=time.monotonic()+5;active=False
            while time.monotonic()<deadline:
                with engine().connect() as probe:
                    if migration._building(probe):active=True;break
                time.sleep(.05)
            assert active
            result=migration.rehearse();assert result['status']=='build_in_progress'
            assert result['invalid_retained']==[migration.NAMES[0]]
            pending.rollback();future.result(timeout=8)
        state=catalog();assert state[0]['state']=='valid' and state[1]['state']=='missing'
        assert count_original(p,created['job_id'])==1
    finally:ext.dispose()


def test_rehearsal_restores_pooled_connection_timeouts_on_success_and_failure(ready,disposable_indexes,monkeypatch):
    ext=create_engine(settings().database_url,pool_size=1,max_overflow=0,hide_parameters=True)
    monkeypatch.setattr(migration,'engine',lambda:ext)
    try:
        with ext.connect() as conn:
            conn.execute(text("SELECT set_config('lock_timeout','7s',false)"))
            conn.execute(text("SELECT set_config('statement_timeout','31s',false)"));conn.commit()
        assert migration.rehearse()['status']=='installed'
        with ext.connect() as conn:
            assert conn.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()==('7s','31s')
        # Force the failure path through an observed invalid index without
        # altering any history; unrelated exceptions still restore settings.
        monkeypatch.setattr(migration,'_catalog',lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
        with pytest.raises(RuntimeError):migration.rehearse()
        with ext.connect() as conn:
            assert conn.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()==('7s','31s')
        # Another physical connection must acquire it: advisory locks are
        # reentrant on the same session, so the pooled session cannot prove release.
        other=create_engine(settings().database_url,hide_parameters=True)
        try:
            with other.connect() as conn:
                assert conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':migration.LOCK}) is True
                assert conn.scalar(text('SELECT pg_advisory_unlock(:key)'),{'key':migration.LOCK}) is True
        finally:other.dispose()
    finally:ext.dispose()


def test_fixed_predicate_and_workspace_scope_do_not_touch_other_audit_history(ready,disposable_indexes):
    from generic_agents.models import Workspace
    s,created,_=ready;p=s['principal']
    with Session(engine()) as db:
        row,audit=original(db,p,created['job_id']);obj=row.id;actor=audit.actor_id
        other=Workspace(name='synthetic-other-workspace');db.add(other);db.flush();other_id=other.id
        db.add_all([Audit(workspace_id=p.workspace_id,object_id=obj,actor_id=actor,action='synthetic.unrelated',data={}),
            Audit(workspace_id=p.workspace_id,object_id=obj,actor_id=actor,action='synthetic.unrelated',data={}),
            Audit(workspace_id=other_id,object_id=obj,actor_id=actor,action=intent.ACTION,data={})]);db.commit()
    assert migration.rehearse()['status']=='installed'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.object_id==obj,Audit.action=='synthetic.unrelated'))==2
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==other_id,
            Audit.object_id==obj,Audit.action==intent.ACTION))==1
        db.add(Audit(workspace_id=p.workspace_id,object_id=obj,actor_id=actor,action='synthetic.unrelated',data={}));db.commit()
    with pytest.raises(IntegrityError),Session(engine()) as db:
        row,audit=original(db,p,created['job_id']);db.add(duplicate(p,row,audit));db.commit()
