"""Disposable PG + local-only v2 file-intent recovery, not production GC."""
import copy
import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import evidence
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Evidence, Job, Membership, Plan
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def intent_record(db, p, job_id):
    return db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == intent._key(job_id)))


@pytest.mark.parametrize('block_gate', [False, True])
def test_registration_slow_source_does_not_hold_gate_and_rechecks_index(ready, monkeypatch, block_gate):
    """Real PG locks; synthetic source resolver, not an approval-chain test."""
    from generic_agents import same_goal_advice_intent_index_gate_candidate_v2 as gate
    s, created, resolved = ready; p = s['principal']; job_id = created['job_id']
    with Session(engine()) as db:
        original = intent_record(db, p, job_id)
        for row in db.scalars(select(Audit).where(Audit.object_id == original.id, Audit.action == intent.ACTION)):
            db.delete(row)
        db.delete(original); db.commit()
    entered, release = Event(), Event()
    def slow_source(*_):
        entered.set()
        assert release.wait(15)
        return copy.deepcopy(resolved)
    monkeypatch.setattr(intent.sources, 'resolve_documents', slow_source)
    def register():
        with Session(engine()) as db:
            try:
                value = intent.register(db, p, s['task_id'], job_id)
                db.commit()
                return ('saved', value)
            except HTTPException as exc:
                db.rollback()
                return ('refused', exc.status_code)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(register)
        try:
            assert entered.wait(15)
            with Session(engine()) as db:
                # Simulates another workspace, which must be able to finish
                # its own gate check while this workspace reads a slow source.
                assert gate.require(db, str(uuid4()))['index_checked']
                if block_gate:
                    # A competing fenced operation acquired the gate during
                    # the read: the writer must recheck and fail before write.
                    release.set()
                    outcome = future.result(timeout=15)
                    db.rollback()
                else:
                    db.commit()
                    release.set()
                    outcome = future.result(timeout=15)
        finally:
            release.set()
    if block_gate:
        assert outcome == ('refused', 409)
        with Session(engine()) as db: assert intent_record(db, p, job_id) is None
    else:
        assert outcome[0] == 'saved'
        with Session(engine()) as db:
            assert intent_record(db, p, job_id).id == outcome[1]


def test_crash_after_write_recovers_same_original_without_duplicate_record(ready, monkeypatch):
    s, created, resolved = ready; p = s['principal']; job_id = created['job_id']; save = worker.evidence.save_evidence
    with Session(engine()) as db:
        original = intent_record(db,p,job_id); identity = original.id; data = copy.deepcopy(original.data)
    def crash(*args, **kwargs):
        save(*args, **kwargs); raise RuntimeError('simulated crash before commit')
    with monkeypatch.context() as patch:
        patch.setattr(worker.evidence,'save_evidence',crash)
        with pytest.raises(RuntimeError), Session(engine()) as db: worker.process(db,p,s['task_id'],job_id)
    path=settings().storage_dir / p.workspace_id / (resolved['sha256'] + '.bin')
    assert path.read_bytes()==resolved['content'] and stat.S_IMODE(path.stat().st_mode)==0o600
    with Session(engine()) as db:
        assert intent_record(db,p,job_id).id==identity and intent_record(db,p,job_id).data==data
        assert db.get(Job,job_id).status=='queued' and not list(db.scalars(select(Evidence)))
        assert intent.register(db,p,s['task_id'],job_id)==identity; db.commit()
    with Session(engine()) as db:
        assert worker.process(db,p,s['task_id'],job_id)['replayed'] is False; db.commit()
    with Session(engine()) as db:
        assert worker.process(db,p,s['task_id'],job_id)['replayed'] is True
        assert intent_record(db,p,job_id).id==identity


def test_uncommitted_intent_cannot_authorize_file_write(ready):
    s, created, resolved = ready; p=s['principal']; job_id=created['job_id']
    with Session(engine()) as db:
        original=intent_record(db,p,job_id)
        events=list(db.scalars(select(Audit).where(Audit.object_id==original.id, Audit.action==intent.ACTION)))
        for row in events: db.delete(row)
        db.delete(original); db.commit()
    with Session(engine()) as db:
        intent.register(db,p,s['task_id'],job_id)
        with pytest.raises(HTTPException):worker.process(db,p,s['task_id'],job_id)
        db.rollback()
    assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()
    with Session(engine()) as db: assert intent_record(db,p,job_id) is None


def test_changed_content_or_revoked_role_never_writes(ready, monkeypatch):
    s, created, resolved=ready;p=s['principal'];job_id=created['job_id']
    different=copy.deepcopy(resolved);different['content']=b'other';different['size_bytes']=5;different['sha256']=hashlib.sha256(b'other').hexdigest()
    with monkeypatch.context() as patch:
        patch.setattr(worker.sources,'resolve_documents',lambda *_:copy.deepcopy(different))
        with pytest.raises(HTTPException), Session(engine()) as db: worker.process(db,p,s['task_id'],job_id)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id));membership.role='viewer';db.commit()
    with pytest.raises(HTTPException), Session(engine()) as db: worker.process(db,p,s['task_id'],job_id)
    assert not (settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')).exists()


def test_shared_existing_file_is_never_deleted_after_rollback(ready, monkeypatch):
    s, created, resolved=ready;p=s['principal'];job_id=created['job_id']
    with Session(engine()) as db:
        shared=evidence.save_evidence(db,p,resolved['content'],'text/plain'); shared_id=shared.id;db.commit()
    path=settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin');before=path.stat();save=worker.evidence.save_evidence
    def crash(*args, **kwargs): save(*args, **kwargs);raise RuntimeError('simulated rollback')
    with monkeypatch.context() as patch:
        patch.setattr(worker.evidence,'save_evidence',crash)
        with pytest.raises(RuntimeError),Session(engine()) as db:worker.process(db,p,s['task_id'],job_id)
    assert path.exists() and path.stat().st_ino==before.st_ino
    with Session(engine()) as db: assert evidence.evidence_bytes(db,p,shared_id)[1]==resolved['content']


@pytest.mark.parametrize('fault',['root_perms','workspace_perms','workspace_symlink','file_symlink'])
def test_unsafe_paths_refuse_file_write(ready, fault, tmp_path):
    s, created, resolved=ready;p=s['principal'];root=settings().storage_dir;workspace=root/p.workspace_id
    if fault=='root_perms':os.chmod(root,0o755)
    elif fault=='workspace_symlink':workspace.symlink_to(tmp_path,target_is_directory=True)
    else:
        workspace.mkdir(mode=0o700)
        if fault=='workspace_perms':os.chmod(workspace,0o755)
        else:(workspace/(resolved['sha256']+'.bin')).symlink_to(tmp_path/'target')
    try:
        with pytest.raises(HTTPException),Session(engine()) as db:worker.process(db,p,s['task_id'],created['job_id'])
    finally:
        if fault=='root_perms':os.chmod(root,0o700)
    assert not (tmp_path/'target').exists()


def test_concurrent_same_content_writer_keeps_both_references_and_never_deletes_shared_file(ready, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Barrier
    s, created, resolved=ready;p=s['principal'];job_id=created['job_id'];barrier=Barrier(2)
    canonical=settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin')
    original_save=evidence.save_evidence; original_unlink=Path.unlink
    def synchronized_save(*args,**kwargs):
        barrier.wait(timeout=10);return original_save(*args,**kwargs)
    def guarded_unlink(path,*args,**kwargs):
        assert path != canonical, 'shared canonical evidence must not be deleted'
        return original_unlink(path,*args,**kwargs)
    monkeypatch.setattr(evidence,'save_evidence',synchronized_save);monkeypatch.setattr(Path,'unlink',guarded_unlink)
    def prepare():
        with Session(engine()) as db: value=worker.process(db,p,s['task_id'],job_id);db.commit();return value
    def upload():
        with Session(engine()) as db: value=evidence.save_evidence(db,p,resolved['content'],'text/plain');identity=value.id;db.commit();return identity
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(prepare);b=pool.submit(upload);assert a.result(timeout=15)['replayed'] is False;other=b.result(timeout=15)
    with Session(engine()) as db:
        records=list(db.scalars(select(Evidence).where(Evidence.workspace_id==p.workspace_id)))
        assert len(records)==2 and other in {row.id for row in records}
        assert len({row.storage_key for row in records})==1
        assert all(evidence.evidence_bytes(db,p,row.id)[1]==resolved['content'] for row in records)
        assert intent_record(db,p,job_id) is not None
    assert canonical.exists() and canonical.read_bytes()==resolved['content']
