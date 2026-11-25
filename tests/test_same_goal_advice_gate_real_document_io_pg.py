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


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_RESTORE'), reason='requires isolated sealed synthetic checkpoint')
def test_real_original_job_conflict_and_same_number_retry(monkeypatch, reject_non_loopback_network):
    from datetime import timedelta
    from uuid import uuid4
    from generic_agents import same_goal_advice_contracts_v302 as contracts
    from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
    from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
    from generic_agents import same_goal_advice_file_sequential_candidate_v2 as sequential
    from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
    from generic_agents import same_goal_advice_store_v302 as old
    from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
    from generic_agents import same_goal_advice_unlimited_store_v2 as unlimited
    from generic_agents.goal_evaluation_provider_v288 import KEY
    from generic_agents.models import Audit, Capability, Evidence, Job
    from generic_agents.security import token_hash
    from tests.test_authored_service_workflow_pg_v270 import publish

    monkeypatch.setattr(settings(), 'device_gateway_key_file', settings().device_gateway_key_file)
    task_id = restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_RESTORE'])
    login_path = Path(settings().storage_dir) / '__synthetic_checkpoint_login__.json'
    assert login_path.stat().st_mode & 0o777 == 0o600
    login = json.loads(login_path.read_text())
    with TestClient(app) as client:
        response = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert response.status_code == 200
        client.headers.update({'X-CSRF-Token': response.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
        who = client.get('/api/me'); assert who.status_code == 200
        cookie = client.cookies.get(settings().session_cookie_name)
        with Session(engine()) as db:
            auth = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(cookie), AuthSession.revoked.is_(False)))
            assert auth is not None and auth.user_id == who.json()['user_id']
            p = Principal(auth.user_id, login['workspace_id'], who.json()['role'], auth.id)
            task = db.get(Task, task_id); head = task.data[KEY]
            # Restore the fixture's original exact loopback origin policy. The
            # OS sandbox still permits only the dedicated PostgreSQL port.
            import re
            caps = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
                Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
            origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
            assert len(origins) == 1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', x) for x in origins)
            monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', next(iter(origins)))
            binding = old.provider.binding(db, p, task)
            reasoning = next(n for n in binding['binding']['graph']['nodes'] if n['id'] == 'reasoning')
            cap = db.get(Capability, reasoning['execution']['capability_id'])
            manifest = contracts.build_manifest(cap.data, old._pin(cap))
            source = db.scalar(select(runtime_task_documents.SourceSet).where(
                runtime_task_documents.SourceSet.workspace_id == p.workspace_id,
                runtime_task_documents.SourceSet.task_id == task_id))
            assert source is not None and all(item['kind'] == 'native' for item in source.data['entries'])
            selector = source.data['entries'][0]['selector']
            evidence_id = db.get(runtime_documents.Version, selector['version_id']).evidence_id
            selection = {'kind': 'approved_source_set', 'source_set_ref': {
                'source_set_id': source.id, 'source_set_hash': source.source_set_hash}}
            now = db.scalar(select(func.clock_timestamp()))
            body = {'request_id': str(uuid4()), 'expected_goal_hash': task.data['goal_hash'],
                'expected_mature_head_hash': head['head_hash'], 'limits': {'max_tokens': 512,
                    'max_request_bytes': 50000, 'max_evidence_items': 2},
                'expires_at': (now + timedelta(minutes=20)).isoformat(), 'reason': '合成本地资料测试'}
        assert publish(client, manifest)['status'] == 'published'
        with Session(engine()) as db:
            issued = policy.authorize(db, p, task_id, body); db.commit()
        request = {'request_id': str(uuid4()), 'expected_goal_hash': body['expected_goal_hash'],
            'expected_mature_head_hash': body['expected_mature_head_hash'],
            'document_source': selection, 'reason': '准备合成资料来源'}
        with Session(engine()) as db:
            created = writer.prepare(db, p, task_id, request, issued['policy_ref']); db.commit()
        assert migration.rehearse()['status'] in {'installed', 'already_installed'}
        with Session(engine()) as db:
            intent_id = intent.register(db, p, task_id, created['job_id']); db.commit()
        with Session(engine()) as db:
            control_id = control.register(db, p, task_id, created['job_id']); db.commit()
        with Session(engine()) as db:
            token = control.claim(db, p, control_id); db.commit()
        with Session(engine()) as db:
            second = Workspace(name='Synthetic other workspace'); db.add(second); db.commit(); other = second.id
        with Session(engine()) as blocker:
            gate.require(blocker, other)
            deferred = sequential.process_or_defer(p, control_id, token)
            assert deferred == {'state': 'retry_later', 'control_id': control_id}
            with Session(engine()) as db:
                assert db.get(Job, created['job_id']).status == 'queued'
                assert db.get(Job, control_id).lease_token == token
                assert db.get(Job, intent_id) is not None
                assert db.scalar(select(func.count()).select_from(Audit).where(
                    Audit.object_id == created['plan_id'], Audit.action == unlimited.DOCUMENT_PREPARED)) == 0
                assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
            blocker.rollback()

        entered, release = Event(), Event()
        real_read = runtime_documents.evidence_bytes
        timings = {}
        def slow_under_lock(db, actor, identity):
            locked = db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND granted AND mode='ShareUpdateExclusiveLock' AND relation=to_regclass('public.audit_events'))"))
            if identity == evidence_id and locked and not entered.is_set():
                timings['start'] = monotonic(); entered.set()
                if not release.wait(15): raise TimeoutError('test stall not released')
                timings['end'] = monotonic()
            return real_read(db, actor, identity)
        monkeypatch.setattr(runtime_documents, 'evidence_bytes', slow_under_lock)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(sequential.process_or_defer, p, control_id, token)
            try:
                assert entered.wait(120), 'real worker did not read original while holding gate'
                with Session(engine()) as db:
                    with pytest.raises(gate.GateBusy): gate.require(db, other)
                    db.rollback()
                Event().wait(1.0)
                release.set()
                result = future.result(timeout=120)
            finally:
                release.set()
        assert result['state'] == 'processed' and result['result']['job_id'] == created['job_id']
        assert result['result']['replayed'] is False
        repeated = sequential.process_or_defer(p, control_id, token)
        assert repeated['result']['replayed'] is True
        with Session(engine()) as db:
            records = list(db.scalars(select(Audit).where(Audit.object_id == created['plan_id'],
                Audit.action == unlimited.DOCUMENT_PREPARED)))
            assert len(records) == 1
            file = db.get(Evidence, records[0].data['evidence_ref']['evidence_id'])
            assert file is not None and file.data[unlimited.DOCUMENT_KEY]['prepare_job_id'] == created['job_id']
            assert db.scalar(select(func.count()).select_from(Evidence).where(
                Evidence.data[unlimited.DOCUMENT_KEY]['prepare_job_id'].as_string() == created['job_id'])) == 1
            assert db.get(Job, intent_id) is not None and db.scalar(select(func.count()).select_from(CloudCall)) == 0
        print('REAL_JOB_RETRY', {'stall_seconds': round(timings['end'] - timings['start'], 3),
            'same_job': True, 'same_intent': True, 'single_receipt': True, 'cloud_calls': 0}, flush=True)
