"""Prove only the non-authoritative pre-read boundary in a disposable PG.

This does not implement the commit phase or provide permission to use the
returned bytes. In particular it is not registered with any production API or
worker. All inputs must be a synthetic checkpoint and a new isolated database.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import runtime_documents, runtime_task_documents
from generic_agents import same_goal_advice_intent_index_gate_candidate_v2 as gate
from generic_agents import same_goal_advice_intent_index_migration_candidate_v2 as migration
from generic_agents import same_goal_advice_sources_v302 as sources
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Membership, Task, User, Workspace
from generic_agents.security import Principal
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_RESTORE'), reason='requires isolated sealed synthetic checkpoint')
def test_snapshot_is_not_authority_and_slow_read_does_not_hold_global_gate(monkeypatch, reject_non_loopback_network):
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_RESTORE'])
    login = json.loads((Path(settings().storage_dir) / '__synthetic_checkpoint_login__.json').read_text())
    with TestClient(app) as client:
        assert client.post('/api/login', json={'username': login['username'], 'password': login['password']}).status_code == 200
    assert migration.rehearse()['status'] in {'installed', 'already_installed'}
    with Session(engine()) as db:
        task = db.get(Task, task_id)
        assert task.workspace_id == login['workspace_id']
        user = db.scalar(select(User).where(User.username == login['username'], User.active.is_(True)))
        member = db.scalar(select(Membership).where(Membership.user_id == user.id,
            Membership.workspace_id == task.workspace_id))
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert member is not None and member.role in {'admin', 'operator'} and session is not None
        principal = Principal(user.id, task.workspace_id, member.role, session.id)
        row = db.scalar(select(runtime_task_documents.SourceSet).where(
            runtime_task_documents.SourceSet.workspace_id == task.workspace_id,
            runtime_task_documents.SourceSet.task_id == task.id))
        native = next(entry for entry in row.data['entries'] if entry['kind'] == 'native')
        version = db.get(runtime_documents.Version, native['selector']['version_id'])
        source_id, source_hash, evidence_id = row.id, row.source_set_hash, version.evidence_id
        ref = {'kind': 'approved_source_set', 'source_set_ref': {
            'source_set_id': source_id, 'source_set_hash': source_hash}}
        workspace_b = Workspace(name='Synthetic unrelated gate workspace')
        db.add(workspace_b); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace_b.id, role='admin'))
        other_workspace_id, user_id, workspace_id = workspace_b.id, user.id, task.workspace_id
        db.commit()
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0

    entered, release = Event(), Event()
    real_read = runtime_documents.evidence_bytes
    def stalled_read(db, actor, identity):
        if identity == evidence_id and not entered.is_set():
            entered.set()
            if not release.wait(20):
                raise TimeoutError('injected read did not resume')
        return real_read(db, actor, identity)
    monkeypatch.setattr(runtime_documents, 'evidence_bytes', stalled_read)

    def pre_read_without_gate():
        with Session(engine()) as db:
            resolved = sources.resolve_documents(db, principal, db.get(Task, task_id), ref)
            # A caller must not treat this tentative value as a dispatch grant.
            db.rollback()
            return resolved['sha256'], resolved['content']

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pre_read_without_gate)
        try:
            assert entered.wait(15)
            with Session(engine()) as db:
                assert gate.require(db, other_workspace_id)['index_checked'] is True
                db.commit()
            release.set()
            snapshot_hash, snapshot_content = future.result(timeout=30)
        finally:
            release.set()
    assert snapshot_hash and isinstance(snapshot_content, bytes) and snapshot_content

    # Real current-role check must still run after the tentative bytes exist.
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == user_id,
            Membership.workspace_id == workspace_id).with_for_update())
        original_role = member.role
        member.role = 'viewer'; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            sources.resolve_documents(db, principal, db.get(Task, task_id), ref)
        db.rollback()
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == user_id,
            Membership.workspace_id == workspace_id).with_for_update())
        member.role = original_role; db.commit()

    # A rollback cannot be confused with a committed modification; a committed
    # change to the original binding must invalidate a fresh verification.
    with Session(engine()) as db:
        row = db.get(runtime_task_documents.SourceSet, source_id)
        row.source_set_hash = '0' * 64; db.flush(); db.rollback()
    with Session(engine()) as db:
        assert db.get(runtime_task_documents.SourceSet, source_id).source_set_hash == source_hash
        row = db.get(runtime_task_documents.SourceSet, source_id)
        row.source_set_hash = '0' * 64; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            sources.resolve_documents(db, principal, db.get(Task, task_id), ref)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
