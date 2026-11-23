"""Unwired candidate vs real PG price receipts; no real cloud or price HTTP.

The price registration and original evidence use production code. A wrapper
is created with the production record builder over a synthetic Task; this is
not evidence that the real v310 route or sensor route has adopted the guard.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Evidence, Membership, Task
from generic_agents.security import Principal
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_business_reasoning_v197 import manifest
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_price_v307 import html
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_command_nonce_lock_pg_v312 import wait_for_real_waiter


@pytest.fixture
def real_price_context(client, synthetic_custody, monkeypatch):
    s = synthetic_custody
    model = copy.deepcopy(manifest())
    model['name'] = 'synthetic_workspace_nonce_price'
    model['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 1024, 'thinking_mode': 'disabled'}
    released = publish(client, model)
    body = {'request_id': str(uuid4()), 'model_pin': {key: released[key] for key in
        ('id', 'content_hash', 'release_version')}, 'validity_seconds': 3600, 'reason': '隔离合成价格登记'}
    monkeypatch.setattr(prices, '_fetch', lambda: (html(), 'text/html; charset=utf-8'))
    return {**s, 'body': body}


def _original(db, s):
    return db.scalar(select(Audit).where(Audit.workspace_id == s['principal'].workspace_id,
        Audit.action == candidate.PRICE_BASIS, Audit.data['request_id'].as_string() == s['body']['request_id']))


def _register(db, s):
    candidate.acquire(db, s['principal'], action=candidate.PRICE_BASIS, task_id=None, body=s['body'])
    return prices.authorize(db, s['principal'], s['body'])


def test_real_model_price_receipt_replays_without_new_fetch_or_model_call(real_price_context, monkeypatch):
    s = real_price_context
    with Session(engine()) as db:
        initial = _register(db, s); db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('价格原号重放不得再次抓取'))
    with Session(engine()) as db:
        result = _register(db, s)
        assert result['basis_ref'] == initial['basis_ref']
        assert result['purpose'] == 'original-registration-receipt-not-send-permission'
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=candidate.PRICE_BASIS, task_id=None,
                body={**s['body'], 'reason': '另一个正文'})
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))
        db.rollback()


def test_retired_model_original_receipt_still_recovers_but_revoked_user_does_not(real_price_context, monkeypatch):
    s = real_price_context
    with Session(engine()) as db:
        initial = _register(db, s)
        model = db.get(Capability, s['body']['model_pin']['id']); model.status = 'retired'; db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('退役模型原号重放不得重新抓取'))
    with Session(engine()) as db:
        assert _register(db, s)['basis_ref'] == initial['basis_ref']
        membership = db.scalar(select(Membership).where(Membership.workspace_id == s['principal'].workspace_id,
            Membership.user_id == s['principal'].user_id))
        membership.role = 'viewer'; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): _register(db, s)
        db.rollback()


def test_real_original_is_not_recovered_after_immutable_evidence_is_tampered(real_price_context):
    s = real_price_context
    with Session(engine()) as db:
        _register(db, s); original = _original(db, s)
        evidence_id = original.data['official_evidence_ref']['evidence_id']; db.commit()
    with Session(engine()) as db:
        evidence = db.get(Evidence, evidence_id); evidence.sha256 = 'f' * 64; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): _register(db, s)
        db.rollback()


def test_standalone_price_cannot_be_upgraded_into_missing_wrapper(real_price_context):
    s = real_price_context
    with Session(engine()) as db:
        _register(db, s); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
                task_id=s['task_id'], body=s['body'])
        other = Principal(str(uuid4()), s['principal'].workspace_id, 'admin', 'foreign-actor')
        with pytest.raises(HTTPException):
            candidate.acquire(db, other, action=candidate.PRICE_BASIS, task_id=None, body=s['body'])
        db.rollback()


def test_price_naturally_expires_but_original_receipt_remains_recoverable(real_price_context, monkeypatch):
    s = real_price_context; s['body']['validity_seconds'] = 60
    with Session(engine()) as db:
        initial = _register(db, s); original = _original(db, s)
        expiry = datetime.fromisoformat(original.data['expires_at']); db.commit()
    deadline = monotonic() + 90
    while True:
        with Session(engine()) as db:
            if db.scalar(select(func.clock_timestamp())) > expiry: break
        assert monotonic() < deadline, '隔离价格未按服务器时钟自然过期'
        sleep(0.5)
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('原号重放不得延长价格有效期'))
    with Session(engine()) as db:
        recovered = _register(db, s)
        assert recovered['basis_ref'] == initial['basis_ref']
        assert recovered['purpose'] == 'original-registration-receipt-not-send-permission'
        with pytest.raises(HTTPException): prices.current(db, s['principal'], s['body']['model_pin'])
        db.rollback()


def test_v310_two_audit_shape_checks_real_price_anchor_and_recovers_both_views(real_price_context, monkeypatch):
    s = real_price_context
    with Session(engine()) as db:
        candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
            task_id=s['task_id'], subject_id=s['task_id'], body=s['body'])
        basis = prices.authorize(db, s['principal'], s['body'])['basis_ref']
        task = db.get(Task, s['task_id'])
        outer = options._save(db, s['principal'], task, options.PRICE_SCHEMA,
            options.PRICE_ACTION, s['body'], {'price_basis_ref': basis})
        outer_id = outer.id; db.commit()
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('历史回执不得再次抓取'))
    with Session(engine()) as db:
        candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
            task_id=s['task_id'], subject_id=s['task_id'], body=s['body'])
        candidate.acquire(db, s['principal'], action=candidate.PRICE_BASIS, task_id=None, body=s['body'])
        assert prices.authorize(db, s['principal'], s['body'])['basis_ref'] == basis
        assert db.get(Audit, outer_id).data['price_basis_ref'] == basis
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
                task_id=str(uuid4()), body=s['body'])
        db.rollback()


@pytest.mark.parametrize('commit_first', [True, False])
def test_real_price_audit_serializes_opposite_task_wrapper(real_price_context, commit_first):
    s = real_price_context; started = Event(); pids = []
    with Session(engine(), expire_on_commit=False) as first:
        _register(first, s)
        def contender():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                pids.append(db.scalar(text('SELECT pg_backend_pid()'))); started.set()
                try:
                    candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
                        task_id=s['task_id'], body=s['body'])
                    db.rollback(); return 'available'
                except HTTPException as exc:
                    db.rollback(); return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            try:
                assert started.wait(3); wait_for_real_waiter(first, pids[0], future)
                if commit_first: first.commit()
                else: first.rollback()
                result = future.result(timeout=6)
            finally:
                first.rollback()
        assert result == (409 if commit_first else 'available')
    with Session(engine()) as db:
        assert (_original(db, s) is not None) is commit_first
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))
