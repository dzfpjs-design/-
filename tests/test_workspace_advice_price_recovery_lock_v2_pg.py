"""Additional real-PG lock tests for the unwired price recovery helper."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_price_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context


def _registered(s):
    with Session(engine()) as db:
        result = prices.authorize(db, s['principal'], s['body']); db.commit()
        return result['basis_ref']


def _wait_blocked(owner, waiter, future):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with Session(engine()) as observer:
            blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiter})
        if owner in blockers: return
        assert not future.done(), 'the competing transaction did not wait for the original lock'
        sleep(.02)
    pytest.fail('no PostgreSQL blocking relationship was observed')


@pytest.mark.parametrize('level', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_recovery_rejects_stale_snapshot_isolation(real_price_context, level):
    s = real_price_context; _registered(s)
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level))
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['body'])
        db.rollback()


def test_price_audit_cannot_be_deleted_during_original_recovery(real_price_context):
    s = real_price_context; basis = _registered(s); started = Event(); ids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, s['principal'], s['body'])['basis_ref'] == basis
        def try_delete():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                ids.append(db.scalar(select(func.pg_backend_pid()))); started.set()
                db.execute(text('DELETE FROM audit_events WHERE id=:id'), {'id': basis['id']})
                db.rollback(); return 'rolled-back'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(try_delete)
            try:
                assert started.wait(3); _wait_blocked(owner, ids[0], future); first.commit()
                assert future.result(timeout=6) == 'rolled-back'
            finally:
                first.rollback()
    with Session(engine()) as db:
        assert db.get(Audit, basis['id']) is not None
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))


def test_recovery_holds_model_lock_against_old_price_writer(real_price_context):
    s = real_price_context; basis = _registered(s); started = Event(); ids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, s['principal'], s['body'])['basis_ref'] == basis
        def old_writer():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                ids.append(db.scalar(select(func.pg_backend_pid()))); started.set()
                result = prices.authorize(db, s['principal'], {**s['body'], 'request_id': str(uuid4())})
                db.rollback(); return result['basis_ref']
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(old_writer)
            try:
                assert started.wait(3); _wait_blocked(owner, ids[0], future); first.commit()
                assert future.result(timeout=6)['id'] != basis['id']
            finally:
                first.rollback()
    # The old writer's SQL rows were rolled back; its content-addressed test
    # file may remain until the temporary test storage is disposed.


@pytest.mark.parametrize('kind', ['evidence', 'upload_audit'])
def test_original_evidence_and_upload_audit_cannot_change_during_recovery(real_price_context, kind):
    s = real_price_context; basis = _registered(s); started = Event(); ids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, s['principal'], s['body'])['basis_ref'] == basis
        value = first.get(Audit, basis['id']).data
        target = value['official_evidence_ref']['evidence_id'] if kind == 'evidence' else value['upload_audit_ref']['id']
        statement = ('UPDATE evidence_objects SET version=version WHERE id=:id' if kind == 'evidence'
                     else 'UPDATE audit_events SET action=action WHERE id=:id')
        def try_change():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                ids.append(db.scalar(select(func.pg_backend_pid()))); started.set()
                db.execute(text(statement), {'id': target}); db.rollback(); return 'rolled-back'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(try_change)
            try:
                assert started.wait(3); _wait_blocked(owner, ids[0], future); first.commit()
                assert future.result(timeout=6) == 'rolled-back'
            finally:
                first.rollback()


def test_session_expiring_during_receipt_read_is_rechecked(real_price_context, monkeypatch):
    s = real_price_context; _registered(s)
    with Session(engine()) as db:
        session = db.get(AuthSession, s['principal'].session_id)
        session.expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=2)
        db.commit()
    original_read = prices._read
    def held_read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        sleep(2.2)
        return result
    monkeypatch.setattr(prices, '_read', held_read)
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['body'])
        db.rollback()
