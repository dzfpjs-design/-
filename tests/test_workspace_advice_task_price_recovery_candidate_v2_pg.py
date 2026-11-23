"""Synthetic task-wrapper custody with real original price/Evidence in PG.

The wrapper is produced by the real record builder, not by a real published
graph workflow. This test does not register a production route.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_task_price_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Capability, CloudCall
from generic_agents.security import Principal
from tests.conftest import application
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context


@pytest.fixture
def task_price_original(real_price_context):
    s = real_price_context
    with Session(engine()) as db:
        price = prices.authorize(db, s['principal'], s['body'])
        task = options._admin_task(db, s['principal'], s['task_id'])
        wrapper = options._save(db, s['principal'], task, options.PRICE_SCHEMA,
            options.PRICE_ACTION, s['body'], {'price_basis_ref': price['basis_ref']})
        identity = wrapper.id; db.commit()
    return {**s, 'wrapper_id': identity, 'basis_ref': price['basis_ref']}


def test_existing_task_wrapper_replays_original_without_new_fetch_or_write(task_price_original, monkeypatch):
    s = task_price_original
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('恢复原任务回执不得重新抓取'))
    monkeypatch.setattr(prices, 'authorize', lambda *a, **k: pytest.fail('恢复不得调用登记路径'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        assert result['basis_ref'] == s['basis_ref']
        with pytest.raises(HTTPException):
            recovery.recover_only(db, s['principal'], s['task_id'], {**s['body'], 'reason': '不同正文'})
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))
        db.rollback()


def test_standalone_price_is_not_promoted_to_missing_task_wrapper(real_price_context, monkeypatch):
    s = real_price_context
    with Session(engine()) as db:
        original = prices.authorize(db, s['principal'], s['body']); db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('不存在外层时不得重新抓取'))
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        assert db.get(Audit, original['basis_ref']['id']) is not None
        assert not list(db.scalars(select(Audit).where(Audit.workspace_id == s['principal'].workspace_id,
            Audit.action == options.PRICE_ACTION, Audit.object_id == s['task_id'],
            Audit.data['request_id'].as_string() == s['body']['request_id'])))
        db.rollback()


def test_real_other_admin_cannot_read_task_price_wrapper(client, task_price_original):
    s = task_price_original; username = 'price_other_' + uuid4().hex[:12]; password = 'Test-' + uuid4().hex
    created = client.post('/api/users', json={'username': username, 'password': password, 'role': 'admin'})
    assert created.status_code == 201, created.text
    with TestClient(application) as other:
        assert other.post('/api/login', json={'username': username, 'password': password}).status_code == 200
        with Session(engine()) as db:
            session = db.scalar(select(AuthSession).where(AuthSession.user_id == created.json()['id'],
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
                .order_by(AuthSession.created_at.desc()))
            assert session is not None
            actor = Principal(created.json()['id'], s['principal'].workspace_id, 'admin', session.id)
            with pytest.raises(HTTPException): recovery.recover_only(db, actor, s['task_id'], s['body'])
            db.rollback()


def test_retired_model_original_task_price_remains_historical(task_price_original, monkeypatch):
    s = task_price_original
    with Session(engine()) as db:
        db.get(Capability, s['body']['model_pin']['id']).status = 'retired'; db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('退役模型历史回放不得重新抓取'))
    with Session(engine()) as db:
        assert recovery.recover_only(db, s['principal'], s['task_id'], s['body'])['basis_ref'] == s['basis_ref']
        db.rollback()


@pytest.mark.parametrize('level', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_task_price_recovery_rejects_stale_transaction_isolation(task_price_original, level):
    s = task_price_original
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level))
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        db.rollback()


def test_task_price_recovery_rechecks_session_expiring_during_outer_read(task_price_original, monkeypatch):
    s = task_price_original
    with Session(engine()) as db:
        db.get(AuthSession, s['principal'].session_id).expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=2)
        db.commit()
    original = options._price_result
    def held_read(*args, **kwargs):
        result = original(*args, **kwargs); sleep(2.2); return result
    monkeypatch.setattr(options, '_price_result', held_read)
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        db.rollback()


def test_expired_task_price_is_only_an_original_receipt(real_price_context, monkeypatch):
    s = real_price_context; s['body']['validity_seconds'] = 60
    with Session(engine()) as db:
        price = prices.authorize(db, s['principal'], s['body'])
        task = options._admin_task(db, s['principal'], s['task_id'])
        options._save(db, s['principal'], task, options.PRICE_SCHEMA, options.PRICE_ACTION,
            s['body'], {'price_basis_ref': price['basis_ref']})
        original = db.get(Audit, price['basis_ref']['id'])
        expiry = datetime.fromisoformat(original.data['expires_at']); db.commit()
    deadline = monotonic() + 90
    while True:
        with Session(engine()) as db:
            if db.scalar(select(func.clock_timestamp())) > expiry: break
        assert monotonic() < deadline
        sleep(.5)
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('原任务价格回放不得延长有效期'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        assert result['basis_ref'] == price['basis_ref']
        assert result['purpose'] == 'historical-not-current-price-authorization'
        with pytest.raises(HTTPException): prices.current(db, s['principal'], s['body']['model_pin'])
        db.rollback()


def test_damaged_and_duplicate_task_wrappers_are_rejected(task_price_original):
    s = task_price_original
    with Session(engine()) as db:
        original = db.get(Audit, s['wrapper_id'])
        clone = Audit(id=str(uuid4()), workspace_id=original.workspace_id, actor_id=original.actor_id,
            object_id=original.object_id, action=original.action, data=copy.deepcopy(original.data),
            created_at=original.created_at)
        clone_id = clone.id; db.add(clone); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        db.rollback()
    with Session(engine()) as db:
        db.delete(db.get(Audit, clone_id))  # Deliberate disposable test duplicate only.
        original = db.get(Audit, s['wrapper_id'])
        original.data = {**original.data, 'price_basis_ref': {'id': str(uuid4()), 'record_hash': 'f' * 64}}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['body'])
        db.rollback()


@pytest.mark.parametrize('kind', ['wrapper', 'price', 'evidence', 'upload'])
def test_existing_original_rows_cannot_change_before_recovery_transaction_ends(task_price_original, kind):
    s = task_price_original; entered = Event(); pids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, s['principal'], s['task_id'], s['body'])['basis_ref'] == s['basis_ref']
        original = first.get(Audit, s['basis_ref']['id'])
        targets = {'wrapper': s['wrapper_id'], 'price': original.id,
            'evidence': original.data['official_evidence_ref']['evidence_id'],
            'upload': original.data['upload_audit_ref']['id']}
        statement = ('UPDATE evidence_objects SET version=version WHERE id=:id' if kind == 'evidence'
            else 'UPDATE audit_events SET action=action WHERE id=:id')
        def change():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                pids.append(db.scalar(select(func.pg_backend_pid()))); entered.set()
                db.execute(text(statement), {'id': targets[kind]}); db.rollback(); return 'rolled-back'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(change)
            try:
                assert entered.wait(3); deadline = monotonic() + 5
                while True:
                    with Session(engine()) as observer:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pids[0]})
                    if owner in blockers: break
                    assert not future.done() and monotonic() < deadline
                    sleep(.02)
                first.commit(); assert future.result(timeout=6) == 'rolled-back'
            finally:
                first.rollback()
