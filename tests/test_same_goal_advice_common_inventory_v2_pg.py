"""Synthetic records on isolated PG; these are not real authorization flows."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents import workspace_command_nonce_lock_v312 as nonce
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Evidence, Invocation, Job, Membership, Plan, Task, uid
from generic_agents.security import Principal
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene


def add_synthetic_v1(db, s):
    """Valid v1 custody rows only; no real source authorization is asserted."""
    sample = contract_scene(); p = s['principal']; original = db.get(Plan, s['plan_id'])
    plan_id, job_id, event_id = uid(), uid(), uid()
    prep = copy.deepcopy(sample.preparation); prep.pop('record_hash')
    prep['scope'].update(workspace_id=p.workspace_id, task_id=s['task_id'],
        analysis_plan_id=plan_id, package_id=original.data['preparation_snapshot']['scope']['package_id'])
    prep['requested_by'] = prep['data_actor_id'] = prep['business_actor_id'] = p.user_id
    prep['request']['request_id'] = uid(); prep['request_hash'] = digest(prep['request'])
    prep = contracts.seal_preparation(prep, manifest=sample.manifest)
    key = legacy._key(s['task_id'], plan_id)
    plan = Plan(id=plan_id, workspace_id=p.workspace_id, task_id=s['task_id'], status='queued', version=1,
        data={'schema': contracts.PLAN, 'preparation_snapshot': prep,
            'jobs': {'prepare': {'id': job_id, 'kind': contracts.PREPARE_KIND, 'schema': legacy.JOB_SCHEMA, 'key': key}},
            'operations': [{'operation': 'prepare', 'request_id': prep['request']['request_id'], 'body': prep['request'],
                'body_hash': prep['request_hash'], 'actor_id': p.user_id, 'audit_id': event_id}],
            'document_preparation': None, 'reviews': []})
    job = Job(id=job_id, workspace_id=p.workspace_id, kind=contracts.PREPARE_KIND, status='queued', attempts=0,
        idempotency_key=key, data=legacy._job_data(plan_id, prep, key))
    event = Audit(id=event_id, workspace_id=p.workspace_id, actor_id=p.user_id, object_id=plan_id,
        action=legacy.ACCEPTED, data=legacy._accepted(plan_id, prep, job_id, key))
    db.add_all([plan, job, event]); db.flush()
    return plan


def test_valid_queued_v2_is_zero_calls_and_old_reader_rejects(synthetic_custody):
    s = synthetic_custody
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        assert common.read(db, s['principal'], task) == {'v1_calls': 0, 'queued_v2': 1}
        with pytest.raises(HTTPException):
            legacy._inventory(db, s['principal'], task)


@pytest.mark.parametrize('keep_v2', [False, True])
def test_v1_custody_counts_and_overlapping_open_versions_fail(synthetic_custody, keep_v2):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        add_synthetic_v1(db, s)
        if not keep_v2:
            db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
            db.delete(db.get(Plan, s['plan_id']))
        db.commit()
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        if keep_v2:
            with pytest.raises(HTTPException): common.read(db, p, task)
        else:
            assert len(legacy._inventory(db, p, task)) == 1
            assert common.read(db, p, task) == {'v1_calls': 0, 'queued_v2': 0}


def remove_v2(db, s):
    db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
    db.delete(db.get(Plan, s['plan_id']))


def test_empty_inventory_requires_current_session_and_preserves_viewer_and_worker_reads(synthetic_custody):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        remove_v2(db, s)
        member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id)); member.role = 'viewer'; db.commit()
    viewer = Principal(p.user_id, p.workspace_id, 'viewer', p.session_id)
    worker = Principal(p.user_id, p.workspace_id, 'viewer', 'internal-custody-test-worker')
    with Session(engine()) as db:
        assert common.read(db, viewer, db.get(Task, s['task_id'])) == {'v1_calls': 0, 'queued_v2': 0}
        assert common.read(db, worker, db.get(Task, s['task_id'])) == {'v1_calls': 0, 'queued_v2': 0}
    with Session(engine()) as db:
        db.get(AuthSession, p.session_id).revoked = True; db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):
        common.read(db, viewer, db.get(Task, s['task_id']))


def test_empty_inventory_rechecks_session_expiry_after_read(synthetic_custody, monkeypatch):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        remove_v2(db, s)
        db.get(AuthSession, p.session_id).expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=3)
        db.commit()
    original = v2._lock_current_reader; entered = []
    def delayed(db, actor):
        value = original(db, actor); entered.append(True); db.execute(select(func.pg_sleep(3.2))); return value
    monkeypatch.setattr(v2, '_lock_current_reader', delayed)
    with Session(engine()) as db, pytest.raises(HTTPException) as error:
        common.read(db, p, db.get(Task, s['task_id']))
    assert entered and '会话已过期' in str(error.value.detail)


@pytest.mark.parametrize('fault', ['schema', 'status', 'orphan_job', 'orphan_audit', 'unknown_action', 'duplicate_nonce'])
def test_unknown_or_orphan_records_block_shared_count(synthetic_custody, fault):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        plan = db.get(Plan, s['plan_id']); job = db.get(Job, s['job_id'])
        if fault == 'schema':
            data = copy.deepcopy(plan.data); data['schema'] = 'same-goal-advice-unlimited-plan-v99'; plan.data = data
        elif fault == 'status': job.status = 'running'
        elif fault == 'orphan_job':
            db.add(Job(id=uid(), workspace_id=p.workspace_id, kind=v2.PREPARE_KIND, status='queued', attempts=0,
                idempotency_key=v2.job_key(s['task_id'], uid()), data={'task_id': s['task_id']}))
        elif fault in ('orphan_audit', 'unknown_action'):
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=uid(),
                action=v2.ACCEPTED if fault == 'orphan_audit' else 'same_goal_advice.unlimited_future_v99',
                data={'task_id': s['task_id']}))
        else:
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=uid(),
                action=legacy.ACCEPTED, data={'task_id': s['task_id'], 'request_id': s['prep']['request']['request_id']}))
        db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):
        common.read(db, p, db.get(Task, s['task_id']))


@pytest.mark.parametrize('fault', ['v2_invocation', 'v2_cloud', 'v2_evidence', 'v1_money', 'v1_sdk', 'v1_cloud', 'v1_cloud_malformed'])
def test_task_bound_orphans_block_even_with_no_plan_job_or_acceptance(synthetic_custody, fault):
    s = synthetic_custody; p = s['principal']; cap = s['prep']['advice_capability_ref']['id']
    with Session(engine()) as db:
        db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
        db.delete(db.get(Plan, s['plan_id']))
        if fault == 'v2_invocation':
            db.add(Invocation(id=uid(), workspace_id=p.workspace_id, capability_id=cap, status='created',
                idempotency_key='synthetic:' + uid(), data={'same_goal_advice_unlimited_v2': {'task_id': s['task_id']}}))
        elif fault in ('v2_cloud', 'v1_cloud', 'v1_cloud_malformed'):
            marker = ({'same_goal_advice_unlimited_v2': {'task_id': s['task_id']}} if fault == 'v2_cloud' else
                {'gateway_dispatch': {'identity': {'task_id': s['task_id']}},
                 'same_goal_advice_spending_v308': None if fault == 'v1_cloud_malformed' else
                     {'schema': 'synthetic', 'plan_id': uid()}})
            db.add(CloudCall(id=uid(), workspace_id=p.workspace_id, capability_id=cap,
                status='reserved', reserved_tokens=1, charged_tokens=1, data=marker))
        elif fault == 'v2_evidence':
            db.add(Evidence(id=uid(), workspace_id=p.workspace_id, sha256='a' * 64, media_type='text/plain',
                size_bytes=1, storage_key='synthetic-missing', data={v2.DOCUMENT_KEY: {'task_id': s['task_id']}}))
        else:
            action = 'same_goal_advice.money_reserved_v308' if fault == 'v1_money' else 'same_goal_advice.captured_v308'
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=uid(),
                action=action, data={'task_id': s['task_id'], 'plan_id': uid()}))
        db.commit()
    with Session(engine()) as db, pytest.raises(HTTPException):
        common.read(db, p, db.get(Task, s['task_id']))


@pytest.mark.parametrize('first_action,second_action', [(legacy.ACCEPTED, v2.ACCEPTED), (v2.ACCEPTED, legacy.ACCEPTED)])
def test_same_nonce_serializes_before_task_lock_and_blocks_opposite_version(synthetic_custody, first_action, second_action):
    s = synthetic_custody; p = s['principal']; body = {'request_id': uid(), 'reason': '合成并发测试'}
    entered = Event(); second_pid = []
    with Session(engine(), expire_on_commit=False) as first:
        nonce.acquire(first, p, action=first_action, task_id=s['task_id'], body=body)
        first.scalar(select(Task).where(Task.id == s['task_id']).with_for_update())
        first_pid = first.scalar(text('SELECT pg_backend_pid()'))
        first.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=uid(),
            action=first_action, data={'task_id': s['task_id'], 'request_id': body['request_id'], 'body_hash': digest(body)}))
        first.flush()

        def second():
            with Session(engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='5s'"))
                second_pid.append(db.scalar(text('SELECT pg_backend_pid()'))); entered.set()
                try:
                    nonce.acquire(db, p, action=second_action, task_id=s['task_id'], body=body)
                    db.scalar(select(Task).where(Task.id == s['task_id']).with_for_update())
                    return 'unexpectedly accepted'
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second)
            try:
                assert entered.wait(3)
                until = monotonic() + 3
                while monotonic() < until:
                    blocking = first.scalar(text('SELECT :first = ANY(pg_blocking_pids(:second))'),
                        {'first': first_pid, 'second': second_pid[0]})
                    if blocking: break
                    assert not future.done(); sleep(0.01)
                assert blocking
                first.commit()
                assert future.result(timeout=6) == 409
            finally:
                first.rollback()
