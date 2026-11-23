"""Real PG custody checks over synthetic rows, not real issuance or execution.

The constructed Audit and Plan are test records, not a claim that the real
Goal/policy/consent preparation journey has run. No worker or model is called.
"""
import copy
from datetime import timedelta
from queue import Queue
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_store_v302 as old_store
from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as subject
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Capability, CloudCall, Evidence, Invocation, Job, Membership, Plan, Task, User, uid
from generic_agents.security import Principal
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene


@pytest.fixture
def synthetic_custody(client, account):
    sample = contract_scene()
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        p = Principal(user.id, account[2], 'admin', session.id)
        now = db.scalar(select(func.clock_timestamp()))
        package = Capability(id=uid(), workspace_id=p.workspace_id, namespace='synthetic_v2',
            name='package', release_version='1.0.0', kind='domain_package', status='draft',
            content_hash=digest({}), data={})
        cap = Capability(id=sample.preparation['advice_capability_ref']['id'], workspace_id=p.workspace_id,
            namespace=sample.manifest['namespace'], name=sample.manifest['name'], release_version='1.0.0',
            kind='tool', status='published', content_hash=digest(sample.manifest),
            validated_hash=digest(sample.manifest), data=sample.manifest)
        db.add_all([package, cap]); db.flush()
        task = Task(id=uid(), workspace_id=p.workspace_id, owner_id=p.user_id, package_id=package.id,
            status='workflow_active', title='合成来源保管测试', data={})
        db.add(task); db.flush()
        plan_id, job_id, accepted_id, policy_id = uid(), uid(), uid(), uid()
        prep = copy.deepcopy(sample.preparation); prep.pop('record_hash'); prep['schema'] = contracts.PREPARATION
        prep['scope'].update(workspace_id=p.workspace_id, task_id=task.id, analysis_plan_id=plan_id, package_id=package.id)
        prep['requested_by'] = prep['data_actor_id'] = prep['business_actor_id'] = p.user_id
        budget = prep['budget']; budget['policy_ref'] = budget.pop('quota_policy_ref')
        budget.pop('max_cost_minor_units'); budget.pop('currency')
        budget.update(cost_policy='unlimited', allocation_basis='per_plan')
        requested = {key: budget[key] for key in ('max_tokens', 'max_request_bytes', 'max_evidence_items')}
        body = {'request_id': uid(), 'expected_goal_hash': prep['scope']['goal_hash'],
            'expected_mature_head_hash': prep['mature_head']['head_hash'], 'limits': requested,
            'expires_at': (now + timedelta(minutes=15)).isoformat(), 'reason': '合成政策保管'}
        policy_value = {'schema': policy.POLICY_SCHEMA,
            'scope': {key: value for key, value in prep['scope'].items() if key != 'analysis_plan_id'},
            'source_business_capability_ref': prep['source_business_capability_ref'],
            'advice_capability_ref': prep['advice_capability_ref'], 'model_pin': prep['model_pin'],
            'mature_head_hash': prep['mature_head']['head_hash'], 'issued_at': now.isoformat(),
            'expires_at': policy.instant(body['expires_at']).isoformat(),
            'limits': {key: value for key, value in budget.items() if key != 'policy_ref'}}
        policy_value['record_hash'] = c.record_hash(policy_value)
        policy_row = Audit(id=policy_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            object_id=task.id, action=policy.POLICY, created_at=now, data=policy_value)
        auth_row = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            object_id=task.id, action=policy.AUTHORIZATION, created_at=now,
            data=policy._auth_body(task, p, body, policy_row))
        budget['policy_ref'] = {'id': policy_id, 'record_hash': digest(policy_value)}
        prep = contracts.seal_preparation(prep, manifest=sample.manifest)
        key = subject.job_key(task.id, plan_id)
        accepted = Audit(id=accepted_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            object_id=plan_id, action=subject.ACCEPTED, created_at=now,
            data=subject.accepted_data(plan_id, prep, job_id, key))
        plan = Plan(id=plan_id, workspace_id=p.workspace_id, task_id=task.id, status='queued', version=1, created_at=now,
            data={'schema': subject.PLAN, 'preparation_snapshot': prep,
                'jobs': {'prepare': {'id': job_id, 'kind': subject.PREPARE_KIND, 'schema': subject.JOB_SCHEMA, 'key': key}},
                'operations': [{'operation': 'prepare', 'request_id': prep['request']['request_id'],
                    'body': prep['request'], 'body_hash': prep['request_hash'], 'actor_id': p.user_id, 'audit_id': accepted_id}],
                'document_preparation': None, 'reviews': []})
        job = Job(id=job_id, workspace_id=p.workspace_id, kind=subject.PREPARE_KIND, status='queued', attempts=0, created_at=now,
            idempotency_key=key, data=subject.job_data(plan_id, prep, key))
        db.add_all([policy_row, auth_row, plan]); db.flush(); db.add_all([job, accepted]); db.commit()
        return {'principal': p, 'task_id': task.id, 'plan_id': plan_id, 'job_id': job_id,
            'policy_id': policy_id, 'accepted_id': accepted_id, 'prep': prep}


def test_queued_custody_survives_new_connection_but_is_not_authority(synthetic_custody):
    s = synthetic_custody; engine().dispose()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        prep, job = subject.read_queued(db, s['principal'], task, plan)
        assert prep == s['prep'] and job.id == s['job_id']
        assert not list(db.scalars(select(CloudCall)))
        # The old reader must not mistake an unknown v2 Plan for a v1 Plan.
        with pytest.raises(HTTPException):
            old_store._inventory(db, s['principal'], task)


def test_read_only_role_and_revoked_session_are_checked_on_real_pg(synthetic_custody):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
            Membership.user_id == p.user_id))
        member.role = 'viewer'; db.commit()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        with pytest.raises(HTTPException):
            subject.read_queued(db, p, task, plan)
        viewer = Principal(p.user_id, p.workspace_id, 'viewer', p.session_id)
        assert subject.read_queued(db, viewer, task, plan)[1].id == s['job_id']
        # End the reader transaction before a separate revocation takes effect.
        db.rollback()
    with Session(engine()) as db:
        session = db.get(AuthSession, p.session_id); session.revoked = True; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            subject.read_queued(db, viewer, db.get(Task, s['task_id']), db.get(Plan, s['plan_id']))


def test_session_expiring_during_custody_read_is_rechecked(synthetic_custody, monkeypatch):
    s = synthetic_custody; p = s['principal']
    with Session(engine()) as db:
        session = db.get(AuthSession, p.session_id)
        session.expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=4)
        db.commit()
    original = subject._lock_current_reader; entered = []
    def delayed(db, actor):
        value = original(db, actor)
        entered.append(True)
        db.execute(select(func.pg_sleep(4.2)))
        return value
    monkeypatch.setattr(subject, '_lock_current_reader', delayed)
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as error:
            subject.read_queued(db, p, db.get(Task, s['task_id']), db.get(Plan, s['plan_id']))
        assert entered and '会话已过期' in str(error.value.detail)


@pytest.mark.parametrize('target', ['task', 'plan', 'job', 'session', 'user', 'membership'])
def test_reader_holds_real_rows_against_concurrent_writers(synthetic_custody, target):
    s = synthetic_custody; p = s['principal']; reader = Session(engine(), expire_on_commit=False)
    ready, finished = Queue(), Event(); outcome = {}; thread = None
    try:
        subject.read_queued(reader, p, reader.get(Task, s['task_id']), reader.get(Plan, s['plan_id']))
        reader_pid = reader.scalar(select(func.pg_backend_pid()))
        def writer():
            with Session(engine()) as db:
                try:
                    db.execute(text("SET LOCAL statement_timeout = '10000ms'"))
                    ready.put(db.scalar(select(func.pg_backend_pid())))
                    if target == 'membership':
                        statement = select(Membership).where(Membership.workspace_id == p.workspace_id,
                            Membership.user_id == p.user_id)
                    else:
                        model, identity = {'task': (Task, s['task_id']), 'plan': (Plan, s['plan_id']),
                            'job': (Job, s['job_id']), 'session': (AuthSession, p.session_id),
                            'user': (User, p.user_id)}[target]
                        statement = select(model).where(model.id == identity)
                    row = db.scalar(statement.with_for_update())
                    assert row is not None
                    if target == 'job': row.status = 'running'
                    elif target == 'session': row.revoked = True
                    elif target == 'user': row.active = False
                    elif target == 'membership': row.role = 'viewer'
                    db.commit(); outcome['done'] = True
                except BaseException as error:
                    db.rollback(); outcome['error'] = error
                finally: finished.set()
        thread = Thread(target=writer, daemon=True); thread.start(); pid = ready.get(timeout=5)
        deadline = monotonic() + 5
        while True:
            with Session(engine()) as observer:
                blocked = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pid})
            if reader_pid in blocked: break
            assert not finished.is_set() and monotonic() < deadline, '未证明并发写入等待原读取锁'
            sleep(.05)
        reader.rollback()
        assert finished.wait(8) and outcome.get('done') and 'error' not in outcome
    finally:
        reader.rollback(); reader.close()
        if thread is not None: thread.join(timeout=10)
    if target in {'job', 'session', 'user', 'membership'}:
        with Session(engine()) as db:
            with pytest.raises(HTTPException):
                subject.read_queued(db, p, db.get(Task, s['task_id']), db.get(Plan, s['plan_id']))


@pytest.mark.parametrize('fault', ['job_bytes', 'policy_hash', 'accepted_audit', 'unexpected_receipt', 'unstarted_lease',
    'orphan_receipt_audit', 'orphan_evidence', 'orphan_cloud_call', 'duplicate_workspace_nonce', 'duplicate_plan_nonce',
    'acceptance_before_policy', 'invocation_created', 'invocation_running', 'invocation_succeeded',
    'invocation_approval_only', 'invocation_inputs_only', 'invocation_key_only',
    'orphan_sdk_capture', 'orphan_result_audit', 'orphan_unknown_v2_audit',
    'duplicate_v1_policy_nonce', 'duplicate_v2_policy_nonce'])
def test_queued_custody_rejects_damage_without_repair(synthetic_custody, fault):
    s = synthetic_custody
    with Session(engine()) as db:
        task, plan, job = db.get(Task, s['task_id']), db.get(Plan, s['plan_id']), db.get(Job, s['job_id'])
        if fault == 'job_bytes': job.data = {**job.data, 'actor_id': uid()}
        elif fault == 'policy_hash':
            row = db.get(Audit, s['policy_id']); row.data = {**row.data, 'mature_head_hash': 'f' * 64}
        elif fault == 'accepted_audit':
            row = db.get(Audit, s['accepted_id']); row.data = {**row.data, 'job_id': uid()}
        elif fault == 'unexpected_receipt': plan.data = {**plan.data, 'document_preparation': {'unexpected': True}}
        elif fault == 'unstarted_lease': job.lease_token = uid()
        elif fault == 'orphan_receipt_audit':
            db.add(Audit(id=uid(), workspace_id=s['principal'].workspace_id, actor_id=s['principal'].user_id,
                object_id=plan.id, action='same_goal_advice.documents_prepared_v304', data={'plan_id': plan.id}))
        elif fault == 'orphan_evidence':
            db.add(Evidence(id=uid(), workspace_id=s['principal'].workspace_id, sha256='a' * 64,
                media_type='text/plain', size_bytes=1, storage_key='synthetic-missing-file',
                data={'same_goal_advice_document_preparation_v304': {'plan_id': plan.id}}))
        elif fault == 'orphan_cloud_call':
            db.add(CloudCall(id=uid(), workspace_id=s['principal'].workspace_id,
                capability_id=s['prep']['advice_capability_ref']['id'], status='reserved', reserved_tokens=1,
                charged_tokens=1, data={'gateway_dispatch': {'identity': {'job_id': job.id}}}))
        elif fault == 'duplicate_workspace_nonce':
            db.add(Audit(id=uid(), workspace_id=s['principal'].workspace_id, actor_id=s['principal'].user_id,
                object_id=uid(), action=old_store.ACCEPTED,
                data={'request_id': s['prep']['request']['request_id']}))
        elif fault == 'acceptance_before_policy':
            row = db.get(Audit, s['accepted_id']); row.created_at -= timedelta(seconds=1)
        elif fault.startswith('invocation_'):
            data, status, key = {}, 'created', 'synthetic-invocation:' + uid()
            if fault in {'invocation_created', 'invocation_running', 'invocation_succeeded'}:
                data['job_id'] = job.id; status = fault.removeprefix('invocation_')
            elif fault == 'invocation_approval_only': data['approval'] = {'plan_ref': {'id': plan.id}}
            elif fault == 'invocation_inputs_only': data['inputs'] = {'plan_ref': {'id': plan.id}}
            else: key = job.idempotency_key
            db.add(Invocation(id=uid(), workspace_id=s['principal'].workspace_id,
                capability_id=s['prep']['advice_capability_ref']['id'], idempotency_key=key,
                status=status, data=data))
        elif fault in {'orphan_sdk_capture', 'orphan_result_audit', 'orphan_unknown_v2_audit'}:
            db.add(Audit(id=uid(), workspace_id=s['principal'].workspace_id, actor_id=s['principal'].user_id,
                object_id=uid() if fault == 'orphan_sdk_capture' else plan.id, action=(
                    'same_goal_advice.captured_v308' if fault == 'orphan_sdk_capture' else
                    'same_goal_advice.result_ready_v308' if fault == 'orphan_result_audit' else
                    'same_goal_advice.unlimited_future_state_v2'),
                data={'plan_id': plan.id}))
        elif fault in {'duplicate_v1_policy_nonce', 'duplicate_v2_policy_nonce'}:
            db.add(Audit(id=uid(), workspace_id=s['principal'].workspace_id, actor_id=s['principal'].user_id,
                object_id=task.id, action='same_goal_advice.quota_authorized_v304'
                    if fault == 'duplicate_v1_policy_nonce' else policy.AUTHORIZATION,
                data={'request_id': s['prep']['request']['request_id']}))
        else:
            db.add(Plan(id=uid(), workspace_id=s['principal'].workspace_id, task_id=task.id, status='queued',
                data={'operations': [{'request_id': s['prep']['request']['request_id']}]}))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            subject.read_queued(db, s['principal'], db.get(Task, s['task_id']), db.get(Plan, s['plan_id']))
        calls = list(db.scalars(select(CloudCall)))
        assert len(calls) == (1 if fault == 'orphan_cloud_call' else 0)
