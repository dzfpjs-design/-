"""Synthetic historical policy custody only, not a real Goal authorization."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents import workspace_advice_policy_recovery_candidate_v2 as recovery
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Task
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody, contract_scene


@pytest.fixture
def policy_receipts(synthetic_custody):
    s = synthetic_custody; principal = s['principal']; nonce = str(uuid4())
    with Session(engine()) as db:
        first = db.get(Task, s['task_id'])
        second = Task(id=str(uuid4()), workspace_id=principal.workspace_id, owner_id=principal.user_id,
            package_id=first.package_id, status='workflow_active', title='隔离第二任务', data={})
        db.add(second); db.flush()
        now = db.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)
        rows = []
        for index, task in enumerate((first, second)):
            issued = now - timedelta(seconds=3) if index == 0 else now
            expiry = (now - timedelta(seconds=1) if index == 0 else now + timedelta(minutes=10)).isoformat()
            scope = copy.deepcopy(s['prep']['scope']); scope.pop('analysis_plan_id'); scope['task_id'] = task.id
            limits = {'provider': 'deepseek', 'max_tokens': 512, 'max_request_bytes': 50000,
                'max_evidence_items': 2, 'max_cost_minor_units': 100, 'currency': 'CNY',
                'max_actual_calls': 1, 'reserved_calls': 1, 'reserved_steps': 1,
                'goal_max_tool_calls': 4, 'goal_max_steps': 4}
            body = {'request_id': nonce, 'expected_goal_hash': scope['goal_hash'],
                'expected_mature_head_hash': s['prep']['mature_head']['head_hash'],
                'limits': {key: limits[key] for key in ('max_tokens', 'max_request_bytes', 'max_evidence_items',
                    'max_cost_minor_units', 'currency')}, 'expires_at': expiry, 'reason': '合成历史策略原件'}
            value = {'schema': store.POLICY_SCHEMA, 'scope': scope,
                'source_business_capability_ref': s['prep']['source_business_capability_ref'],
                'advice_capability_ref': s['prep']['advice_capability_ref'], 'model_pin': s['prep']['model_pin'],
                'issued_at': issued.isoformat(), 'expires_at': expiry, 'limits': limits}
            value['record_hash'] = contracts.record_hash(value)
            policy = Audit(id=str(uuid4()), workspace_id=principal.workspace_id, actor_id=principal.user_id,
                object_id=task.id, action=store.QUOTA_POLICY, data=value, created_at=issued)
            event = Audit(id=str(uuid4()), workspace_id=principal.workspace_id, actor_id=principal.user_id,
                object_id=task.id, action=quota.AUTHORIZATION, created_at=issued,
                data=quota._authorization(task, principal, body, policy))
            db.add_all([policy, event]); rows.append((task.id, body, event.data['policy_ref']))
        db.commit()
    return principal, rows


def test_synthetic_two_task_old_policy_receipts_can_be_read_without_authorizing_again(policy_receipts, monkeypatch):
    principal, rows = policy_receipts
    monkeypatch.setattr(quota, 'authorize', lambda *a, **k: pytest.fail('恢复路径不得发放新政策'))
    with Session(engine()) as db:
        for task_id, body, expected in rows:
            assert recovery.recover_only(db, principal, task_id, body)['quota_policy_ref'] == expected
            with pytest.raises(HTTPException):
                candidate.acquire(db, principal, action=candidate.V1_POLICY_REQUEST, task_id=task_id, body=body)
        with pytest.raises(HTTPException):
            recovery.recover_only(db, principal, rows[0][0], {**rows[0][1], 'reason': '另一请求'})
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, str(uuid4()), rows[0][1])
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id)))
        db.rollback()


@pytest.mark.parametrize('level', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_policy_recovery_rejects_other_transaction_isolation(policy_receipts, level):
    principal, rows = policy_receipts
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + level))
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, rows[0][0], rows[0][1])
        db.rollback()


def test_policy_recovery_rejects_duplicate_original_and_tampered_policy(policy_receipts):
    principal, rows = policy_receipts; task_id, body, reference = rows[0]
    with Session(engine()) as db:
        original = db.scalar(select(Audit).where(Audit.workspace_id == principal.workspace_id,
            Audit.action == quota.AUTHORIZATION, Audit.object_id == task_id,
            Audit.data['request_id'].as_string() == body['request_id']))
        clone = Audit(id=str(uuid4()), workspace_id=original.workspace_id, actor_id=original.actor_id,
            object_id=original.object_id, action=original.action, data=copy.deepcopy(original.data),
            created_at=original.created_at)
        clone_id = clone.id; db.add(clone); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, task_id, body)
        db.rollback()
    with Session(engine()) as db:
        db.delete(db.get(Audit, clone_id))  # Only the deliberately created isolated duplicate.
        policy = db.get(Audit, reference['id']); policy.data = {**policy.data, 'record_hash': 'f' * 64}; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, task_id, body)
        db.rollback()


@pytest.mark.parametrize('kind', ['authorization', 'policy'])
def test_policy_recovery_holds_original_rows_against_deletion(policy_receipts, kind):
    principal, rows = policy_receipts; task_id, body, reference = rows[0]
    entered = Event(); pids = []
    with Session(engine()) as first:
        owner = first.scalar(select(func.pg_backend_pid()))
        assert recovery.recover_only(first, principal, task_id, body)['quota_policy_ref'] == reference
        original = first.scalar(select(Audit).where(Audit.workspace_id == principal.workspace_id,
            Audit.action == quota.AUTHORIZATION, Audit.object_id == task_id,
            Audit.data['request_id'].as_string() == body['request_id']))
        target = original.id if kind == 'authorization' else reference['id']
        def remove():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                pids.append(db.scalar(select(func.pg_backend_pid()))); entered.set()
                db.execute(text('DELETE FROM audit_events WHERE id=:id'), {'id': target})
                db.rollback(); return 'rolled-back'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(remove)
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


def test_policy_recovery_checks_session_expiry_during_read(policy_receipts, monkeypatch):
    principal, rows = policy_receipts
    with Session(engine()) as db:
        db.get(AuthSession, principal.session_id).expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=2)
        db.commit()
    original = quota._receipt
    def held_read(*args, **kwargs):
        result = original(*args, **kwargs); sleep(2.2); return result
    monkeypatch.setattr(quota, '_receipt', held_read)
    with Session(engine()) as db:
        with pytest.raises(HTTPException): recovery.recover_only(db, principal, rows[0][0], rows[0][1])
        db.rollback()
