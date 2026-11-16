"""Real PG persistence for V202; source/live authority is an explicit seam.

Root alone runs this against one disposable ga_test_ DB. It is not evidence
that an approved business Task, paid model or real device completed.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text, event
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import workflow_simulation_review_v202 as s
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from tests.test_workflow_simulation_review_v202 import result


@pytest.fixture
def scene(account, client, monkeypatch):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_'), \
        'Root must use an explicitly disposable ga_test_ database.'
    with Session(bound, expire_on_commit=False) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == account[2],
            m.Membership.user_id == user.id))
        assert user.active is True and member.role == 'admin'
        p = Principal(user.id, member.workspace_id, member.role, 'simulation-review-pg-test')
        package = m.Capability(workspace_id=p.workspace_id, namespace='review_pg', name='package',
            release_version=str(uuid4()), kind='domain_package', status='published',
            data={'test_only': True}, content_hash=digest({'test_only': True}),
            validated_hash=digest({'test_only': True}))
        db.add(package); db.flush()
        task = m.Task(workspace_id=p.workspace_id, package_id=package.id, owner_id=p.user_id,
            title='仅验证模拟核对的真实数据库事务', status='workflow_waiting',
            data={'graph_runtime': {'cancel_epoch': 0, 'graph_hash': 'g'}})
        db.add(task); db.flush()
        inputs = {'reasoning_result': result(), 'candidate_plans': []}
        key = 'review-pg:' + str(uuid4())
        clock = db.scalar(select(func.clock_timestamp()))
        job = m.Job(workspace_id=p.workspace_id, kind='execute_full_semantic_graph_node',
            idempotency_key=key, status='failed', attempts=1,
            available_at=clock, lease_token=str(uuid4()), lease_until=clock-timedelta(seconds=5),
            data={'task_id': task.id, 'activation_id': 'a', 'actor_id': p.user_id,
                'graph_hash': 'g', 'cancel_epoch': 0, 'activation_version': 0,
                'input_hash': digest(inputs), 'inputs': inputs, 'invocation_key': key,
                'attempt': 1, 'recovery_only': False})
        db.add(job); db.flush()
        task.data = {**task.data, 'graph_jobs': {'a': {'job_id': job.id,
            'invocation_key': key, 'phase': 'blocked', 'attempt': 1, 'input_hash': digest(inputs),
            'cancel_epoch': 0, 'invocation_id': None}}}
        db.commit()
        value = NS(bound=bound, p=p, task_id=task.id, job_id=job.id, original=copy.deepcopy(inputs))

    def context(db, _p, task, activation_id, job=None, *, read_mode=False):
        job = db.get(m.Job, value.job_id) if job is None else job
        pin = {'job': s._stable_job(job), 'graph_hash': 'g', 'cancel_epoch': 0,
            'binding_hash': 'binding', 'capability_id': package.id, 'capability_hash': package.content_hash,
            'producer': {'original_capture_hash': 'synthetic-source-seam'},
            'result_hash': digest(job.data['inputs']['reasoning_result']), 'targets': []}
        selected, assessment = s.selected_with_gaps(job.data['inputs']['reasoning_result'])
        return (task, job, task.data['graph_runtime'], {'id': 'a', 'version': 0, 'epoch': 0},
            task.data['graph_jobs']['a'], selected, assessment,
            [{'display_name': '已声明模拟目标（source seam）', 'simulation_only': True}], pin)
    monkeypatch.setattr(s, '_context', context)
    return value


def command(db, scene):
    task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
    basis = s.describe(db, scene.p, task, 'a')
    assert basis['state'] == 'reviewable'
    return s.ReviewCommand(request_id=uuid4(), activation_id='a',
        expected_task_version=task.version, expected_job_version=job.version,
        basis_hash=basis['basis_hash'], acknowledged_missing_information=basis['missing_information'],
        acknowledge_simulation_only=True, reason='仅核对明确模拟工具，原资料缺项继续保留')


def test_review_job_queue_and_independent_audit_rollback_together(scene):
    with Session(scene.bound, expire_on_commit=False) as db:
        body = command(db, scene)
        task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
        s.submit(db, scene.p, task, job, body)
        assert job.status == 'queued'
        db.rollback()
    with Session(scene.bound) as db:
        task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
        assert job.status == 'failed' and s.KEY not in task.data
        assert s.historical_record(db, task, body.request_id) is None


def test_cold_read_keeps_original_gaps_and_replay_never_queues_again(scene):
    with Session(scene.bound, expire_on_commit=False) as db:
        body = command(db, scene)
        task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
        original_key = job.data['invocation_key']
        receipt = s.submit(db, scene.p, task, job, body)
        db.commit()
    scene.bound.dispose()
    with Session(scene.bound, expire_on_commit=False) as db:
        task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
        assert job.status == 'queued' and job.data['inputs'] == scene.original
        assert 'invocation_id' not in task.data['graph_jobs']['a']
        assert job.attempts == 1 and job.data['invocation_key'] == original_key
        stored = s.historical_record(db, task, body.request_id)
        assert stored['prior_job']['status'] == 'failed'
        assert stored['prior_transport']['invocation_id'] is None
        assert stored['body']['acknowledged_missing_information'] == body.acknowledged_missing_information
        job.status = 'succeeded'; db.commit()
        replay = s.submit(db, scene.p, task, None, body)
        assert replay == {**receipt, 'replayed': True} and job.status == 'succeeded'


def test_cold_missing_independent_audit_blocks_history_and_replay(scene):
    with Session(scene.bound, expire_on_commit=False) as db:
        body = command(db, scene)
        task = db.get(m.Task, scene.task_id); job = db.get(m.Job, scene.job_id)
        s.submit(db, scene.p, task, job, body); db.commit()
        event = db.scalar(select(m.Audit).where(m.Audit.object_id == task.id, m.Audit.action == s.ACTION))
        db.delete(event); db.commit()
    with Session(scene.bound) as db:
        task = db.get(m.Task, scene.task_id)
        with pytest.raises(HTTPException): s.historical_record(db, task, body.request_id)
        with pytest.raises(HTTPException): s.submit(db, scene.p, task, None, body)


def test_advisory_live_check_reads_real_policy_in_read_only_transaction(scene, monkeypatch):
    """Catch the actual FOR SHARE failure; source/role spec remains a seam.

    The production guard's real policy SELECT is used inside real graph.live.
    This test does not claim a synthetic policy has full business authority.
    """
    from generic_agents import workflow_graph as graph
    from generic_agents import layered_approval_store_v175 as approval
    from generic_agents import instance_semantic_runtime_dispatch as dispatch
    from generic_agents import entity_release_compat as entities
    from generic_agents.layered_approval_models_v175 import ApprovalPolicyV175
    with Session(scene.bound, expire_on_commit=False) as db:
        task = db.get(m.Task, scene.task_id)
        instance = m.DomainInstance(workspace_id=scene.p.workspace_id, namespace='review_readonly_pg',
            status='partially_active', data={'enabled_business_ids': ['b']})
        db.add(instance); db.flush()
        policy = ApprovalPolicyV175(workspace_id=scene.p.workspace_id,
            instance_id=instance.id, business_id='b', enabled=True, data={'synthetic_spec': True})
        db.add(policy); db.flush()
        policy_id = policy.id
        task.data = {**task.data, 'instance_id': instance.id, 'workflow_runtime': {'receipts': []}}
        package_id = task.package_id
        db.commit()
    current_policy = approval.current_policy
    policy_checks = []
    def guard(db, p, task, *, lock_sources=True):
        actual = current_policy(db, p, policy_id, lock_sources=lock_sources)
        assert actual.enabled is True
        policy_checks.append(lock_sources)
    monkeypatch.setattr(approval, 'guard', guard)
    monkeypatch.setattr(dispatch, 'modern', lambda *_args: NS(verify_release=lambda *_a, **_kw: None))
    monkeypatch.setattr(entities, 'verify_task_objects', lambda *_a, **kw: None)
    statements = []
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(scene.bound, 'before_cursor_execute', capture)
    try:
        with Session(scene.bound) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            task = db.get(m.Task, scene.task_id)
            graph.live(db, scene.p, task, NS(data={'snapshot': {}}, package_id=package_id),
                {'id': 'b', 'name': 'Business'}, lock_sources=False)
            assert db.scalar(select(func.current_setting('transaction_read_only'))) == 'on'
            assert not db.new and not db.dirty and not db.deleted
        assert policy_checks == [False]
        policy_sql = [value for value in statements if 'approval_policies_v175' in value]
        assert policy_sql and not any('FOR SHARE' in value.upper() or 'FOR UPDATE' in value.upper()
            for value in policy_sql)
    finally:
        event.remove(scene.bound, 'before_cursor_execute', capture)
