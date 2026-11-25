"""Disposable PG experiment: real synthetic document resolution under the gate.

This deliberately injects an I/O stall and does not test the HTTP business flow.
The checkpoint and database must come from the dedicated isolated test runner.
"""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
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
def test_real_local_document_under_gate_blocks_other_workspace_until_rollback(monkeypatch, reject_non_loopback_network):
    monkeypatch.setattr(settings(), 'device_gateway_key_file', settings().device_gateway_key_file)
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_RESTORE'])
    login = json.loads((Path(settings().storage_dir) / '__synthetic_checkpoint_login__.json').read_text())
    with TestClient(app) as client:
        response = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert response.status_code == 200
    assert migration.rehearse()['status'] in {'installed', 'already_installed'}
    with Session(engine()) as db:
        task = db.get(Task, task_id)
        assert task.workspace_id == login['workspace_id']
        user = db.scalar(select(User).where(User.username == login['username'], User.active.is_(True)))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id,
            Membership.workspace_id == task.workspace_id))
        auth = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert membership is not None and auth is not None
        principal = Principal(user.id, task.workspace_id, membership.role, auth.id)
        row = db.scalar(select(runtime_task_documents.SourceSet).where(
            runtime_task_documents.SourceSet.workspace_id == task.workspace_id,
            runtime_task_documents.SourceSet.task_id == task.id))
        assert row is not None and row.data['entries']
        entries = row.data['entries']
        native = next(entry for entry in entries if entry['kind'] == 'native')
        version = db.get(runtime_documents.Version, native['selector']['version_id'])
        assert version is not None
        evidence_id = version.evidence_id
        ref = {'kind': 'approved_source_set', 'source_set_ref': {
            'source_set_id': row.id, 'source_set_hash': row.source_set_hash}}
        workspace_b = Workspace(name='Synthetic gate contention workspace')
        db.add(workspace_b); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace_b.id, role='admin'))
        other_workspace_id = workspace_b.id
        db.commit()
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0

    entered, release = Event(), Event()
    real_read = runtime_documents.evidence_bytes
    timings = {}
    def stalled_read(db, principal_arg, identity):
        if identity == evidence_id and not entered.is_set():
            timings['entered'] = monotonic(); entered.set()
            if not release.wait(15):
                raise TimeoutError('injected local read stall not released')
            timings['released'] = monotonic()
        result = real_read(db, principal_arg, identity)
        if identity == evidence_id:
            assert result[1] and isinstance(result[1], bytes)
            timings['real_read'] = True
        return result
    monkeypatch.setattr(runtime_documents, 'evidence_bytes', stalled_read)

    def resolve_under_gate():
        with Session(engine()) as db:
            gate.require(db, principal.workspace_id)
            timings['locked'] = monotonic()
            timings['pid'] = db.scalar(text('SELECT pg_backend_pid()'))
            resolved = sources.resolve_documents(db, principal, db.get(Task, task_id), ref)
            assert resolved['content'] and resolved['source_set']['source_set_id'] == ref['source_set_ref']['source_set_id']
            db.commit()
            timings['committed'] = monotonic()
            return resolved['size_bytes']

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(resolve_under_gate)
        try:
            assert entered.wait(10), 'real source did not reach native local evidence read'
            with Session(engine()) as db:
                held = db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND granted "
                    "AND mode='ShareUpdateExclusiveLock' AND relation=to_regclass('public.audit_events'))"),
                    {'pid': timings['pid']})
                assert held
                start = monotonic()
                with pytest.raises(HTTPException) as error:
                    gate.require(db, other_workspace_id)
                timings['conflict'] = monotonic() - start
                assert error.value.status_code == 409
                db.rollback()  # PostgreSQL failed transaction must be discarded before retry.
            # Deliberate stall, not a filesystem performance benchmark.
            Event().wait(1.0)
            release.set()
            assert future.result(timeout=15) > 0
        finally:
            release.set()
    with Session(engine()) as db:
        assert gate.require(db, other_workspace_id)['index_checked'] is True
        db.commit()
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    assert timings.get('real_read') and timings['committed'] >= timings['released'] >= timings['entered'] >= timings['locked']
    print('REAL_LOCAL_READ_GATE', {'held_seconds': round(timings['committed'] - timings['locked'], 3),
        'injected_stall_seconds': round(timings['released'] - timings['entered'], 3),
        'conflict_seconds': round(timings['conflict'], 3), 'retry': 'passed'}, flush=True)
