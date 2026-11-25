"""One-connection experiment; sources/Goal are synthetic test doubles."""
from concurrent.futures import ThreadPoolExecutor
import copy
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from generic_agents import evidence, leases
from generic_agents import same_goal_advice_file_sequential_candidate_v2 as sequential
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership
from tests.test_same_goal_advice_document_lease_candidate_v2_pg import start
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def path(p, resolved):
    return settings().storage_dir / p.workspace_id / (resolved['sha256'] + '.bin')


def test_one_connection_reuses_connection_and_original_without_second_intent(ready, monkeypatch):
    s, created, resolved = ready; p=s['principal'];control,token=start(s,created)
    assert engine().pool.size()==1 and engine().pool._max_overflow==0
    snapshots=[];process=sequential.worker.process;capture=intent._snapshot
    def watch_snapshot(db,*args,**kwargs):
        if not kwargs['lock']: snapshots.append(('probe',db.scalar(select(func.pg_backend_pid()))))
        return capture(db,*args,**kwargs)
    def watch_process(db,*args,**kwargs):
        snapshots.append(('work',db.scalar(select(func.pg_backend_pid()))));return process(db,*args,**kwargs)
    monkeypatch.setattr(intent,'_snapshot',watch_snapshot);monkeypatch.setattr(sequential.worker,'process',watch_process)
    result=sequential.process(p,control,token)
    assert result['replayed'] is False and snapshots[0][1]==snapshots[1][1]
    assert path(p,resolved).read_bytes()==resolved['content']
    assert sequential.process(p,control,token)['replayed'] is True
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],
            Audit.action==sequential.worker.v2.DOCUMENT_PREPARED))==1
        assert not list(db.scalars(select(CloudCall)))


def test_uncommitted_registration_in_another_connection_is_not_a_preflight(ready):
    s, created, resolved=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.idempotency_key==intent._key(created['job_id'])))
        for event in db.scalars(select(Audit).where(Audit.object_id==row.id,Audit.action==intent.ACTION)): db.delete(event)
        db.delete(row);db.commit()
    external=create_engine(settings().database_url, hide_parameters=True)
    try:
        with Session(external) as writer:
            intent.register(writer,p,s['task_id'],created['job_id']);writer.flush()
            with pytest.raises(HTTPException): sequential.process(p,control,token)
            writer.rollback()
        assert not path(p,resolved).exists()
    finally:external.dispose()


@pytest.mark.parametrize('change',['delete','replace_audit','replace_record','revoke'])
def test_change_between_transactions_is_rejected_before_write(ready, monkeypatch, change):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    external=create_engine(settings().database_url,hide_parameters=True); original=sequential.Session; counter=0
    class Intercept(original):
        def __enter__(self):
            nonlocal counter
            counter+=1
            if counter==2:
                with Session(external) as db:
                    row=db.scalar(select(Job).where(Job.idempotency_key==intent._key(created['job_id'])))
                    audit=db.scalar(select(Audit).where(Audit.object_id==row.id,Audit.action==intent.ACTION))
                    if change=='delete':db.delete(audit);db.delete(row)
                    elif change=='replace_audit':
                        data=copy.deepcopy(audit.data);actor=audit.actor_id;obj=audit.object_id
                        db.delete(audit);db.flush();db.add(Audit(workspace_id=p.workspace_id,object_id=obj,actor_id=actor,action=intent.ACTION,data=data))
                    elif change=='replace_record':
                        data=copy.deepcopy(row.data); key=row.idempotency_key; actor=audit.actor_id
                        db.delete(audit);db.delete(row);db.flush()
                        replacement=Job(workspace_id=p.workspace_id,kind=intent.KIND,status='internal',idempotency_key=key,data=data)
                        db.add(replacement);db.flush()
                        db.add(Audit(workspace_id=p.workspace_id,object_id=replacement.id,actor_id=actor,
                            action=intent.ACTION,data={'schema':intent.SCHEMA,'intent_hash':intent.digest(data)}))
                    else: db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id)).role='viewer'
                    db.commit()
            return super().__enter__()
    monkeypatch.setattr(sequential,'Session',Intercept)
    try:
        with pytest.raises((HTTPException,leases.ExecutionUnauthorized)):sequential.process(p,control,token)
        assert not path(p,resolved).exists()
    finally:external.dispose()


def test_dirty_session_or_reused_preflight_does_not_authorize_write(ready, monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);original=sequential.worker.process
    def dirty(db,*args,**kwargs):
        db.add(Job(workspace_id=p.workspace_id,kind='synthetic',status='internal',idempotency_key='synthetic-dirty',data={}))
        db.flush()  # A flushed edit must still poison the witness.
        return original(db,*args,**kwargs)
    monkeypatch.setattr(sequential.worker,'process',dirty)
    with pytest.raises(HTTPException):sequential.process(p,control,token)
    assert not path(p,resolved).exists()


def test_different_transaction_isolation_fails_closed(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    other=create_engine(settings().database_url,pool_size=1,max_overflow=0,isolation_level='REPEATABLE READ',hide_parameters=True)
    monkeypatch.setattr(sequential,'engine',lambda:other)
    try:
        with pytest.raises(HTTPException):sequential.process(p,control,token)
        assert not path(p,resolved).exists()
    finally:other.dispose()


def test_shared_file_survives_and_parallel_replay_is_unique(ready):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    with Session(engine()) as db:
        shared=evidence.save_evidence(db,p,resolved['content'],'text/plain');shared_id=shared.id;db.commit()
    before=path(p,resolved).stat()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(sequential.process,p,control,token) for _ in range(2)]
        results=[f.result(timeout=20) for f in futures]
    assert sorted(x['replayed'] for x in results)==[False,True]
    assert path(p,resolved).stat().st_ino==before.st_ino
    with Session(engine()) as db:
        assert evidence.evidence_bytes(db,p,shared_id)[1]==resolved['content']
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.object_id==created['plan_id'],
            Audit.action==sequential.worker.v2.DOCUMENT_PREPARED))==1


def test_preflight_is_single_use_inside_one_transaction(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created);original=intent.require_committed
    def twice(*args,**kwargs):
        original(*args,**kwargs)
        return original(*args,**kwargs)
    monkeypatch.setattr(intent,'require_committed',twice)
    with pytest.raises(HTTPException):sequential.process(p,control,token)
    assert not path(p,resolved).exists()


def test_caller_info_cannot_forge_coordinator_witness(ready):
    from sqlalchemy.exc import TimeoutError
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    with pytest.raises(TimeoutError),Session(engine()) as db:
        leases.attach(db,control,token);db.info['worker_actor']=p
        db.info['preflight']={'committed':True}
        sequential.worker.process(db,p,control,token)
    assert not path(p,resolved).exists()


def test_uncooperative_audit_phantom_is_not_blocked_by_existing_row_locks(ready,monkeypatch):
    """Known blocker: read-committed row locks do not lock the missing row."""
    s,created,resolved=ready;p=s['principal'];control,token=start(s,created)
    external=create_engine(settings().database_url,hide_parameters=True);original=evidence.save_evidence
    # Reproduce the older unfenced experiment only. The real new gate is
    # tested separately and rejects this absent-index state before writing.
    from generic_agents import same_goal_advice_intent_index_gate_candidate_v2 as index_gate
    from generic_agents import same_goal_advice_intent_index_migration_candidate_v2 as migration
    with Session(engine()) as db:
        for name in migration.NAMES:db.execute(__import__('sqlalchemy').text('DROP INDEX IF EXISTS public.'+name))
        db.commit()
    monkeypatch.setattr(index_gate,'require',lambda *_: None)
    def insert_uncooperative_duplicate(*args,**kwargs):
        with Session(external) as db:
            record=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key==intent._key(created['job_id'])))
            first=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==record.id,Audit.action==intent.ACTION))
            db.add(Audit(workspace_id=p.workspace_id,object_id=record.id,actor_id=first.actor_id,
                action=intent.ACTION,data=copy.deepcopy(first.data)));db.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(evidence,'save_evidence',insert_uncooperative_duplicate)
    try:
        # This success documents the unfenced writer; it is not an acceptance
        # test. Do not enable the coordinator until all writers are fenced or
        # a validated database constraint prevents this insertion.
        assert sequential.process(p,control,token)['replayed'] is False
        with Session(engine()) as db:
            record=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key==intent._key(created['job_id'])))
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==record.id,Audit.action==intent.ACTION))==2
        assert path(p,resolved).exists()
    finally:external.dispose()
