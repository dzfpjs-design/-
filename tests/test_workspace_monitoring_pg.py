"""Actual disposable PG/run_setup wiring; no real device or adopted workflow.

Only other scheduler roots and the domain consumer/device transport are named
seams. The real default root registry, workspace claim, lease, Job persistence,
queue audits, finalization, stop loop and HTTP overview readback are exercised.
Root must provide the owned disposable database; this file never selects one.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker, workspace_monitoring as s
from generic_agents import perception, workflow_monitor_schedule_v200 as monitor
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal


@pytest.fixture
def scene(account, client):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_'), \
        'This test must only use Root-owned disposable ga_test_ PostgreSQL.'
    with Session(bound, expire_on_commit=False) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        other = m.Workspace(name='必须保持不被工作区A处理的B')
        db.add(other); db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=other.id, role='admin'))
        namespaces = []
        for workspace_id, label in ((account[2], 'A'), (other.id, 'B')):
            namespace = 'workspace_monitor_' + label
            package_data = {'kind': 'domain_package', 'specification': {}, 'test_source_seam': True}
            source_data = {'kind': 'source', 'specification': {}, 'execution': {'adapter': 'upload'}, 'test_source_seam': True}
            package = m.Capability(workspace_id=workspace_id, namespace=namespace, name='package',
                release_version='1.0', kind='domain_package', status='published', data=package_data,
                content_hash=digest(package_data), validated_hash=digest(package_data))
            source = m.Capability(workspace_id=workspace_id, namespace=namespace, name='source',
                release_version='1.0', kind='source', status='published', data=source_data,
                content_hash=digest(source_data), validated_hash=digest(source_data))
            db.add_all([package, source]); db.flush()
            entity = m.Entity(workspace_id=workspace_id, namespace=namespace, entity_type='sample',
                external_id='explicit-synthetic-sample', schema_capability_id=package.id, data={'test_only': True})
            db.add(entity); db.flush()
            value = {'cooldown_seconds': 60, 'entity_ids': [entity.id], 'source_ids': [source.id],
                'fields': ['synthetic'], 'max_age_seconds': 60, 'max_acquisitions_per_day': 2,
                'valid_until': (db.scalar(select(func.clock_timestamp()))+timedelta(days=1)).isoformat(),
                'test_source_authority_seam': True}
            policy = m.MonitorPolicy(workspace_id=workspace_id, status='active', approved_by=user.id,
                data={**value, 'approved_hash': digest(value)})
            db.add(policy); db.flush()
            namespaces.append(NS(workspace_id=workspace_id, policy_id=policy.id, source_id=source.id, entity_id=entity.id))
        # A pre-existing foreign acquisition Job proves claim isolation, rather
        # than merely asserting that a workspace with no queue was untouched.
        b = namespaces[1]
        acquisition = m.Acquisition(workspace_id=b.workspace_id, entity_id=b.entity_id,
            capability_id=b.source_id, idempotency_key='foreign-original:' + str(uuid4()),
            status='authorized', data={'policy_id': b.policy_id, 'actor_id': user.id})
        db.add(acquisition); db.flush()
        foreign = m.Job(workspace_id=b.workspace_id, kind='dispatch_acquisition', status='queued',
            idempotency_key='dispatch:' + acquisition.id,
            data={'actor_id': user.id, 'acquisition_id': acquisition.id})
        db.add(foreign); db.flush(); db.commit()
        return NS(bound=bound, client=client, a=namespaces[0], b=b,
            p=Principal(user.id, account[2], 'admin', 'workspace-monitor-pg-fixture'),
            foreign_job_id=foreign.id, foreign_acquisition_id=acquisition.id)


def run_default_loop(scene, monkeypatch, complete):
    """Run the real normal loop; disable unrelated roots, never its registry."""
    assert ('workspace_monitoring', 'schedule_once') in worker.SETUP_DAILY_ROOTS
    assert s.KIND in worker.SETUP_KINDS and 'dispatch_acquisition' in worker.SETUP_KINDS
    stop = Event(); calls = []
    real_process = worker.process_once
    def imported(name, package):
        if name == '.workspace_monitoring': return s
        methods = {method for module, method in (*worker.SETUP_POLL_ROOTS, *worker.SETUP_DAILY_ROOTS)
            if name == '.' + module}
        return NS(**{method: (lambda **_kwargs: None) for method in methods})
    def process(*args, **kwargs):
        assert kwargs['workspace_id'] == scene.a.workspace_id and kwargs['deliver'] is False
        result = real_process(*args, **kwargs)
        calls.append(result)
        with Session(scene.bound) as db:
            if complete(db): stop.set()
        if len(calls) >= 12: stop.set()
        return result
    monkeypatch.setattr(worker, 'import_module', imported)
    monkeypatch.setattr(worker, '_StopEvent', lambda: stop)
    monkeypatch.setattr(worker.signal, 'getsignal', lambda _signum: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *_args: None)
    monkeypatch.setattr(worker, 'process_once', process)
    assert worker.run_setup(scene.a.workspace_id) == 0
    assert len(calls) < 12, 'The normal loop did not reach its durable acceptance condition.'
    return calls


def inventory(db, model, workspace_id):
    """Snapshot every application column, including lease/version and data.

    The private typed-payload storage columns are an ORM implementation detail;
    their decoded application payload is included through the real data field.
    """
    names = [column.key for column in model.__table__.columns
        if not column.info.get('typed_payload_internal')]
    return {row.id: {name: copy.deepcopy(getattr(row, name)) for name in names}
        for row in db.scalars(select(model).where(model.workspace_id == workspace_id))}


def test_default_setup_loop_queues_consumes_and_claims_acquisition_only_in_its_workspace(scene, monkeypatch):
    consume = []; dispatch = []
    foreign_models = (m.Job, m.Acquisition, m.MonitorPolicy, m.Capability, m.Entity,
        m.Audit, m.Event, m.Memory, m.ObjectState)
    with Session(scene.bound) as db:
        original_jobs = inventory(db, m.Job, scene.a.workspace_id)
        foreign_rows = {model: inventory(db, model, scene.b.workspace_id) for model in foreign_models}
    def original_consumer(db, p, policy, **expected):
        assert p.workspace_id == policy.workspace_id == scene.a.workspace_id
        assert expected['expected_head_pin'] is None
        assert expected['expected_policy_pin']['approved_hash'] == policy.data['approved_hash']
        consume.append(policy.id)
        return False  # explicit domain authority seam, not an adopted policy proof
    def propose(db, p, identity):
        assert identity == scene.a.policy_id
        acquisition = m.Acquisition(workspace_id=p.workspace_id, entity_id=scene.a.entity_id,
            capability_id=scene.a.source_id, idempotency_key='synthetic-authorized:' + str(uuid4()),
            status='authorized', data={'policy_id': identity, 'actor_id': p.user_id})
        db.add(acquisition); db.flush()
        db.add(m.Job(workspace_id=p.workspace_id, kind='dispatch_acquisition', status='queued',
            idempotency_key='dispatch:' + acquisition.id,
            data={'actor_id': p.user_id, 'acquisition_id': acquisition.id}))
        return [acquisition.id]
    def transport(db, p, identity):
        acquisition = db.get(m.Acquisition, identity)
        assert acquisition.workspace_id == p.workspace_id == scene.a.workspace_id
        acquisition.status = 'awaiting_observation'
        dispatch.append(identity)  # no HTTP/device call is claimed
    monkeypatch.setattr(monitor, 'consume_policy_tick', original_consumer)
    monkeypatch.setattr(perception, 'propose_acquisitions', propose)
    monkeypatch.setattr(perception, 'dispatch_acquisition', transport)
    def complete(db):
        return bool(db.scalar(select(m.Job.id).where(m.Job.workspace_id == scene.a.workspace_id,
            m.Job.kind == 'dispatch_acquisition', m.Job.status == 'succeeded')))
    run_default_loop(scene, monkeypatch, complete)
    scene.bound.dispose()
    with Session(scene.bound) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == scene.a.workspace_id)))
        after_jobs = inventory(db, m.Job, scene.a.workspace_id)
        assert {identity: after_jobs[identity] for identity in original_jobs} == original_jobs
        added = [row for row in jobs if row.id not in original_jobs]
        assert len(added) == 2 and {row.kind for row in added} == {s.KIND, 'dispatch_acquisition'}
        tick = next(row for row in added if row.kind == s.KIND)
        assert tick.status == 'succeeded' and tick.data[s.RESULT_KEY]['acquisition_ids'] == dispatch
        assert {model: inventory(db, model, scene.b.workspace_id) for model in foreign_models} == foreign_rows
    assert consume == [scene.a.policy_id] and len(dispatch) == 1
    assert s.schedule_once(scene.a.workspace_id) == 0  # interval not due; no gratuitous Job


def test_default_loop_failure_is_durable_and_readable_after_backend_reload(scene, monkeypatch):
    def reject(*_args, **_kwargs): raise HTTPException(409, '原监测调度独立审计无法核对')
    monkeypatch.setattr(monitor, 'consume_policy_tick', reject)
    monkeypatch.setattr(perception, 'propose_acquisitions', lambda *_a, **_kw: pytest.fail('failed monitor fell back'))
    def complete(db):
        return bool(db.scalar(select(m.Job.id).where(m.Job.workspace_id == scene.a.workspace_id,
            m.Job.kind == s.KIND, m.Job.status == 'failed')))
    run_default_loop(scene, monkeypatch, complete)
    scene.bound.dispose()
    overview = scene.client.get('/api/overview')
    assert overview.status_code == 200
    failed = next(row for row in overview.json()['jobs'] if row['kind'] == s.KIND)
    assert failed['status'] == 'failed' and '独立审计' in failed['data']['error'] and failed['data']['finished_at']
    assert s.schedule_once(scene.a.workspace_id) == 0  # original failed context is retained
    with Session(scene.bound) as db:
        assert len(list(db.scalars(select(m.Job).where(m.Job.workspace_id == scene.a.workspace_id, m.Job.kind == s.KIND)))) == 1


def test_queue_is_idempotent_and_unfinished_original_job_survives_scheduler_restart(scene):
    assert s.schedule_once(scene.a.workspace_id) == 1
    with Session(scene.bound, expire_on_commit=False) as db:
        job = db.scalar(select(m.Job).where(m.Job.workspace_id == scene.a.workspace_id, m.Job.kind == s.KIND))
        job_id, key, frozen = job.id, job.idempotency_key, copy.deepcopy(job.data)
        job.status = 'running'; job.lease_token = str(uuid4())
        job.lease_until = db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        db.commit()
    scene.bound.dispose()
    assert s.schedule_once(scene.a.workspace_id) == 0
    with Session(scene.bound) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == scene.a.workspace_id, m.Job.kind == s.KIND)))
        assert len(jobs) == 1 and jobs[0].id == job_id and jobs[0].idempotency_key == key and jobs[0].data == frozen


def test_two_workspace_coordinators_queue_one_original_job_and_one_audit(scene):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(s.schedule_once, [scene.a.workspace_id, scene.a.workspace_id]))
    assert sorted(results) == [0, 1]
    with Session(scene.bound) as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == scene.a.workspace_id, m.Job.kind == s.KIND)))
        assert len(jobs) == 1
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == scene.a.workspace_id,
            m.Audit.object_id == jobs[0].id, m.Audit.action == s.QUEUED)))
        assert len(events) == 1 and events[0].data == s._queue_anchor(jobs[0].id, jobs[0].data[s.KEY])


def test_legacy_head_none_fence_holds_policy_lock_before_fallback_and_rejects_changed_pin(scene):
    with Session(scene.bound, expire_on_commit=False) as db:
        policy = db.get(m.MonitorPolicy, scene.a.policy_id)
        original = monitor.policy_tick_policy_pin(policy)
        changed = copy.deepcopy(original); changed['approved_hash'] = 'c'*64
        with pytest.raises(HTTPException, match=''):
            monitor.consume_policy_tick(db, scene.p, policy, expected_head_pin=None, expected_policy_pin=changed)
        db.rollback()
        policy = db.get(m.MonitorPolicy, scene.a.policy_id)
        assert monitor.consume_policy_tick(db, scene.p, policy,
            expected_head_pin=None, expected_policy_pin=original) is False
