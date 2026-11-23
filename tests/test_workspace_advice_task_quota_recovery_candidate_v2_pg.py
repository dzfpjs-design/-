"""Synthetic v310 quota custody with real price evidence; no Goal issuance."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import workspace_advice_task_quota_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.temporal import instant
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Task
from generic_agents.security import Principal
from tests.conftest import application
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene
from tests.test_workspace_advice_nonce_price_candidate_v2_pg import real_price_context


@pytest.fixture
def task_quota_original(real_price_context):
    s = real_price_context; principal = s['principal']
    with Session(engine()) as db:
        price = prices.authorize(db, principal, s['body']); task = db.get(Task, s['task_id'])
        now = quota._clock(db); expires = (now + timedelta(seconds=300)).astimezone(timezone.utc).isoformat()
        scope = copy.deepcopy(s['prep']['scope']); scope.pop('analysis_plan_id')
        scope['task_id'] = task.id
        body = {'request_id': str(uuid4()), 'expected_goal_hash': scope['goal_hash'],
            'expected_mature_head_hash': s['prep']['mature_head']['head_hash'],
            'max_cost_minor_units': 100, 'currency': 'CNY', 'validity_seconds': 300,
            'reason': '隔离合成任务额度外层'}
        requested = {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2,
            'max_cost_minor_units': 100, 'currency': 'CNY'}
        resolved = {'request_id': body['request_id'], 'expected_goal_hash': body['expected_goal_hash'],
            'expected_mature_head_hash': body['expected_mature_head_hash'], 'limits': requested,
            'expires_at': expires, 'reason': body['reason']}
        limits = {**requested, 'provider': 'deepseek', 'max_actual_calls': 1, 'reserved_calls': 1,
            'reserved_steps': 1, 'goal_max_tool_calls': 4, 'goal_max_steps': 4}
        value = {'schema': store.POLICY_SCHEMA, 'scope': scope,
            'source_business_capability_ref': s['prep']['source_business_capability_ref'],
            'advice_capability_ref': s['prep']['advice_capability_ref'], 'model_pin': s['body']['model_pin'],
            'issued_at': now.isoformat(), 'expires_at': expires, 'limits': limits}
        value['record_hash'] = contracts.record_hash(value)
        policy = Audit(id=str(uuid4()), workspace_id=principal.workspace_id, actor_id=principal.user_id,
            object_id=task.id, action=store.QUOTA_POLICY, data=value, created_at=now)
        auth = Audit(id=str(uuid4()), workspace_id=principal.workspace_id, actor_id=principal.user_id,
            object_id=task.id, action=quota.AUTHORIZATION, created_at=now,
            data=quota._authorization(task, principal, resolved, policy))
        normalized = quota.Authorize.model_validate(resolved).model_dump(mode='json')
        assert policy.actor_id == auth.actor_id and policy.created_at == auth.created_at
        assert digest(auth.data) == digest(quota._authorization(task, principal, normalized, policy))
        assert value['scope']['goal_hash'] == normalized['expected_goal_hash']
        assert value['expires_at'] == instant(normalized['expires_at']).isoformat()
        db.add_all([policy, auth]); db.flush()
        wrapper = options._save(db, principal, task, options.QUOTA_SCHEMA, options.QUOTA_ACTION, body,
            {'model_pin': s['body']['model_pin'], 'authorization_body': resolved,
             'authorization_ref': options._ref(auth), 'policy_ref': options._ref(policy),
             'price_basis_ref': price['basis_ref']})
        ids = {'wrapper': wrapper.id, 'authorization': auth.id, 'policy': policy.id,
            'price': price['basis_ref']['id']}
        price_row = db.get(Audit, ids['price'])
        ids.update(evidence=price_row.data['official_evidence_ref']['evidence_id'],
            upload=price_row.data['upload_audit_ref']['id'])
        policy_ref = options._ref(policy)
        db.commit()
    return {**s, 'quota_body': body, 'resolved': resolved, 'ids': ids, 'policy_ref': policy_ref}


def test_quota_wrapper_recovers_original_without_authorize_or_fetch(task_quota_original, monkeypatch):
    s = task_quota_original
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('恢复外层不得重新抓取'))
    monkeypatch.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('恢复外层不得重新发放'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        assert result['quota_policy_ref'] == s['policy_ref'] and result['cloud_send_authorized'] is False
        with pytest.raises(HTTPException):
            recovery.recover_only(db, s['principal'], s['task_id'], {**s['quota_body'], 'reason': '另一个正文'})
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == s['principal'].workspace_id)))
        db.rollback()


def test_missing_wrapper_never_recreated_from_existing_authorization(task_quota_original, monkeypatch):
    s = task_quota_original
    with Session(engine()) as db:
        db.delete(db.get(Audit, s['ids']['wrapper'])); db.commit()  # Deliberate disposable fixture fault.
    monkeypatch.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('不得补发'))
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        assert db.get(Audit, s['ids']['authorization']) is not None and db.get(Audit, s['ids']['policy']) is not None
        db.rollback()


def test_real_other_admin_cannot_recover_someone_elses_quota_wrapper(client, task_quota_original):
    s = task_quota_original; username = 'quota_other_' + uuid4().hex[:12]; password = 'Test-' + uuid4().hex
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
            with pytest.raises(HTTPException): recovery.recover_only(db, actor, s['task_id'], s['quota_body'])
            db.rollback()


@pytest.mark.parametrize('level', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_quota_wrapper_recovery_rejects_non_read_committed(task_quota_original, level):
    s = task_quota_original
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level))
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        db.rollback()


@pytest.mark.parametrize('kind', ['wrapper', 'authorization', 'policy', 'price', 'evidence', 'upload'])
def test_quota_wrapper_rejects_corrupt_original_chain(task_quota_original, kind):
    s = task_quota_original
    with Session(engine()) as db:
        if kind == 'evidence':
            db.get(Evidence, s['ids'][kind]).sha256 = 'f' * 64
        else:
            row = db.get(Audit, s['ids'][kind]); row.data = {**row.data, 'synthetic_tamper': True}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        db.rollback()


def test_quota_wrapper_checks_session_expiry_after_read(task_quota_original, monkeypatch):
    s = task_quota_original
    with Session(engine()) as db:
        db.get(AuthSession, s['principal'].session_id).expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=2)
        db.commit()
    original = options._quota_result
    def held(*args, **kwargs):
        result = original(*args, **kwargs); sleep(2.2); return result
    monkeypatch.setattr(options, '_quota_result', held)
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        db.rollback()


def test_expired_quota_wrapper_returns_original_receipt_without_renewal(task_quota_original, monkeypatch):
    s = task_quota_original; expiry = datetime.fromisoformat(s['resolved']['expires_at'])
    deadline = monotonic() + 330
    while True:
        with Session(engine()) as db:
            if db.scalar(select(func.clock_timestamp())) > expiry: break
        assert monotonic() < deadline, '隔离旧政策未在预期时间自然到期'
        sleep(1)
    monkeypatch.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('不得续发历史政策'))
    monkeypatch.setattr(prices, '_fetch', lambda: pytest.fail('不得重新抓取价格'))
    with Session(engine()) as db:
        result = recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
        assert result['quota_policy_ref'] == s['policy_ref'] and result['cloud_send_authorized'] is False
        assert datetime.fromisoformat(result['expires_at']) == expiry
        db.rollback()


@pytest.mark.parametrize('kind', ['wrapper', 'authorization', 'policy', 'price', 'evidence', 'upload'])
def test_quota_original_chain_rows_are_locked_until_transaction_end(task_quota_original, kind):
    s = task_quota_original; entered = Event(); pids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, s['principal'], s['task_id'], s['quota_body'])['quota_policy_ref'] == s['policy_ref']
        statement = ('UPDATE evidence_objects SET version=version WHERE id=:id' if kind == 'evidence'
            else 'UPDATE audit_events SET action=action WHERE id=:id')
        def change():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                pids.append(db.scalar(select(func.pg_backend_pid()))); entered.set()
                db.execute(text(statement), {'id': s['ids'][kind]}); db.rollback(); return 'rolled-back'
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


def test_mismatched_nested_nonce_rejects_before_cross_nonce_wait(task_quota_original, monkeypatch):
    """Two real PG transactions demonstrate no reverse wait for malformed history."""
    from generic_agents import workspace_command_nonce_lock_v312 as nonce_lock
    s = task_quota_original
    outer = s['quota_body']['request_id']; inner = str(uuid4())
    with Session(engine()) as db:
        wrapper = db.get(Audit, s['ids']['wrapper'])
        damaged = copy.deepcopy(wrapper.data)
        damaged['authorization_body']['request_id'] = inner
        damaged['record_hash'] = contracts.record_hash(damaged)
        wrapper.data = damaged
        assert options._shape(s['principal'], db.get(Task, s['task_id']), wrapper)['authorization_body']['request_id'] == inner
        db.commit()
    inner_held, outer_held, reverse_start, proceed = Event(), Event(), Event(), Event()
    pids = {}
    original_admin = options._admin_task
    def held_admin(*args, **kwargs):
        result = original_admin(*args, **kwargs)
        outer_held.set()
        assert proceed.wait(5)
        return result
    monkeypatch.setattr(options, '_admin_task', held_admin)

    def reverse():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='5000ms'"))
            pids['reverse'] = db.scalar(select(func.pg_backend_pid()))
            db.execute(text(nonce_lock.LOCK_SQL), {'workspace_command_nonce_key': nonce_lock.key(s['principal'].workspace_id, inner)})
            inner_held.set(); assert reverse_start.wait(5)
            db.execute(text(nonce_lock.LOCK_SQL), {'workspace_command_nonce_key': nonce_lock.key(s['principal'].workspace_id, outer)})
            db.rollback(); return 'released'

    def recover():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='5000ms'"))
            pids['recover'] = db.scalar(select(func.pg_backend_pid()))
            try:
                with pytest.raises(HTTPException) as rejected:
                    recovery.recover_only(db, s['principal'], s['task_id'], s['quota_body'])
                assert rejected.value.status_code == 409
                assert rejected.value.detail == '原额度申请编号不一致；请保留原记录核对'
                return 'rejected'
            finally:
                db.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reverse_future = pool.submit(reverse)
        assert inner_held.wait(3)
        recovery_future = pool.submit(recover)
        try:
            assert outer_held.wait(3)
            reverse_start.set()
            deadline = monotonic() + 3
            while True:
                with Session(engine()) as observer:
                    blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pids['reverse']})
                if pids['recover'] in blockers: break
                assert monotonic() < deadline and not reverse_future.done()
                sleep(.02)
            proceed.set()
            assert recovery_future.result(timeout=2) == 'rejected'
            assert reverse_future.result(timeout=2) == 'released'
        finally:
            proceed.set(); reverse_start.set()
