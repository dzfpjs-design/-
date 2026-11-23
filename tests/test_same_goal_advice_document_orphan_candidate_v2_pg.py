"""Orphan/reuse experiment in disposable PG and temporary local storage.

The Goal and approved-source resolution are test doubles inherited from the
worker test. No claim is made about production directory or symlink safety.
"""
import stat

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import evidence
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as worker
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, Evidence
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_orphan_permissions_retry_reuses_bytes_and_shared_reference_precludes_unlink(ready, monkeypatch):
    s, created, source = ready; p = s['principal']; job_id = created['job_id']
    directory = settings().storage_dir / p.workspace_id
    blob = directory / (source['sha256'] + '.bin')
    save = worker.evidence.save_evidence
    def abort_after_file(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError('simulate failure after file write before database commit')
    with monkeypatch.context() as patch:
        patch.setattr(worker.evidence, 'save_evidence', abort_after_file)
        with pytest.raises(RuntimeError), Session(engine()) as db:
            worker.process(db, p, s['task_id'], job_id)
    assert blob.is_file() and not blob.is_symlink() and blob.read_bytes() == source['content']
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    first = blob.stat()
    assert stat.S_IMODE(first.st_mode) == 0o600
    with Session(engine()) as db:
        assert not list(db.scalars(select(Evidence).where(Evidence.workspace_id == p.workspace_id)))
        assert not list(db.scalars(select(Audit).where(Audit.action == v2.DOCUMENT_PREPARED)))
    # Simulate an independent existing Evidence of identical content before
    # retry. We intentionally never unlink: either live record needs the blob.
    with Session(engine()) as db:
        shared = evidence.save_evidence(db, p, source['content'], 'text/plain')
        shared_id = shared.id; db.commit()
    assert (blob.stat().st_ino, blob.stat().st_mtime_ns, blob.stat().st_ctime_ns) == (
        first.st_ino, first.st_mtime_ns, first.st_ctime_ns)
    with Session(engine()) as db:
        assert worker.process(db, p, s['task_id'], job_id)['replayed'] is False
        db.commit()
    final = blob.stat()
    assert (final.st_ino, final.st_mtime_ns, final.st_ctime_ns) == (
        first.st_ino, first.st_mtime_ns, first.st_ctime_ns)
    with Session(engine()) as db:
        event = db.scalar(select(Audit).where(Audit.object_id == created['plan_id'],
            Audit.action == v2.DOCUMENT_PREPARED))
        document_id = event.data['evidence_ref']['evidence_id']
        assert document_id != shared_id
        shared, shared_bytes = evidence.evidence_bytes(db, p, shared_id)
        document, doc_bytes = evidence.evidence_bytes(db, p, document_id)
        assert shared.storage_key == document.storage_key
        assert shared_bytes == doc_bytes == source['content']
        assert blob.exists()
