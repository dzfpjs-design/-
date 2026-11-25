"""Disposable PG only; this fixture substitutes source/Goal resolution.

Does not test offline delegation, an HTTP route, real user data or a model send.
"""
from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_file_sequential_candidate_v2 as sequential
from generic_agents import same_goal_advice_intent_index_gate_candidate_v2 as gate
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Job, Workspace
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_document_lease_candidate_v2_pg import start


def test_conflict_retries_same_job_and_lease_without_duplicate_file_or_receipt(ready):
    setup, created, resolved = ready
    principal = setup['principal']; control_id, token = start(setup, created)
    with Session(engine()) as db:
        second = Workspace(name='Isolated second workspace'); db.add(second); db.commit(); other = second.id
    path = __import__('generic_agents.config', fromlist=['settings']).settings().storage_dir / principal.workspace_id / (resolved['sha256'] + '.bin')
    with Session(engine()) as holder:
        gate.require(holder, other)
        outcome = sequential.process_or_defer(principal, control_id, token)
        assert outcome == {'state': 'retry_later', 'control_id': control_id}
        assert not path.exists()
        with Session(engine()) as db:
            control = db.get(Job, control_id); original = db.get(Job, created['job_id'])
            assert control.status == 'running' and control.lease_token == token
            assert original.status == 'queued'
            assert db.scalar(select(func.count()).select_from(Audit).where(
                Audit.object_id == created['plan_id'], Audit.action == v2.DOCUMENT_PREPARED)) == 0
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        holder.rollback()
    outcome = sequential.process_or_defer(principal, control_id, token)
    assert outcome['state'] == 'processed' and outcome['result']['job_id'] == created['job_id']
    assert outcome['result']['replayed'] is False and path.read_bytes() == resolved['content']
    identity = path.stat().st_ino
    replay = sequential.process_or_defer(principal, control_id, token)
    assert replay['result']['replayed'] is True and path.stat().st_ino == identity
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.object_id == created['plan_id'], Audit.action == v2.DOCUMENT_PREPARED)) == 1
        assert db.scalar(select(func.count()).select_from(Evidence).where(
            Evidence.data[v2.DOCUMENT_KEY]['prepare_job_id'].as_string() == created['job_id'])) == 1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(
            Job.workspace_id == principal.workspace_id,
            Job.idempotency_key == intent._key(created['job_id']))) == 1


def test_other_409_is_not_converted_to_retry(monkeypatch):
    def reject(*args):
        raise HTTPException(409, 'revoked or mismatched')
    monkeypatch.setattr(sequential, 'process', reject)
    with pytest.raises(HTTPException) as failure:
        sequential.process_or_defer(None, 'control', 'lease')
    assert failure.value.detail == 'revoked or mismatched'


def test_revocation_after_conflict_never_becomes_retry_or_write(ready):
    from generic_agents.models import Membership
    setup, created, resolved = ready
    principal = setup['principal']; control_id, token = start(setup, created)
    with Session(engine()) as db:
        second = Workspace(name='Isolated second workspace'); db.add(second); db.commit(); other = second.id
    with Session(engine()) as holder:
        gate.require(holder, other)
        assert sequential.process_or_defer(principal, control_id, token)['state'] == 'retry_later'
        holder.rollback()
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == principal.workspace_id,
            Membership.user_id == principal.user_id))
        membership.role = 'viewer'; db.commit()
    with pytest.raises(HTTPException):
        sequential.process_or_defer(principal, control_id, token)
    with Session(engine()) as db:
        assert db.get(Job, created['job_id']).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.object_id == created['plan_id'], Audit.action == v2.DOCUMENT_PREPARED)) == 0
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


def test_expired_lease_reclaims_same_control_then_retries(ready, monkeypatch):
    from time import monotonic, sleep
    from generic_agents import leases
    from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
    setup, created, _ = ready
    principal = setup['principal']
    # Only new synthetic lease duration is shortened; no stored timestamps or
    # authorization records are changed.
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2)
    control_id, old_token = start(setup, created)
    with Session(engine()) as db:
        second = Workspace(name='Isolated second workspace'); db.add(second); db.commit(); other = second.id
    with Session(engine()) as holder:
        gate.require(holder, other)
        assert sequential.process_or_defer(principal, control_id, old_token)['state'] == 'retry_later'
        holder.rollback()
    deadline = monotonic() + 6
    while True:
        with Session(engine()) as db:
            row = db.get(Job, control_id)
            expired = row.lease_until < db.scalar(select(func.clock_timestamp()))
        if expired: break
        assert monotonic() < deadline
        sleep(.05)
    with Session(engine()) as db:
        new_token = control.claim(db, principal, control_id); db.commit()
    assert new_token != old_token
    # New synthetic lease should be long enough to complete normal checks.
    # The issued lease itself remains unmodified and the same job is used.
    outcome = sequential.process_or_defer(principal, control_id, new_token)
    assert outcome['state'] == 'processed' and outcome['result']['job_id'] == created['job_id']
    with Session(engine()) as db:
        row = db.get(Job, control_id)
        assert row.attempts == 2 and row.lease_token == new_token
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.object_id == created['plan_id'], Audit.action == v2.DOCUMENT_PREPARED)) == 1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
