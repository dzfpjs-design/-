"""Root-owned disposable PG: real upload, default worker, built-in extraction.

Only unrelated timer roots are disabled. No model or device transport is
substituted for the ordinary successful extraction. These are tests to run in
the existing isolated PG harness, never a command to select/start a database.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event as StopEvent
from types import SimpleNamespace as NS
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m, worker, workspace_observation_intake as intake
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.api import app
from .test_vertical_slice import entity, manifest, publish


@pytest.fixture
def scene(client, account):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_'), \
        'Only Root-owned disposable ga_test_ PostgreSQL may run this test.'
    package = publish(client, manifest())
    obj = entity(client, package)
    source = publish(client, manifest('source', name='ordinary_upload_' + uuid4().hex[:8],
        execution={'adapter': 'upload'}, specification={'timezone': 'Asia/Shanghai'},
        data_policy={'cloud_allowed': False, 'external_allowed': False}))
    tool = publish(client, manifest('tool', name='ordinary_numeric_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        specification={'rules': []}, data_policy={'cloud_allowed': False, 'external_allowed': False}))
    with Session(bound) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        actor_id = user.id
    return NS(bound=bound, client=client, workspace_id=account[2], actor_id=actor_id,
        p=Principal(actor_id, account[2], 'admin', 'ordinary-intake-pg'),
        package=package, entity=obj, source=source, tool=tool)


def ingest(scene, *, stamp=None, value=20):
    stamp = stamp or datetime.now(timezone.utc) - timedelta(days=10)
    request = {'source_id': scene.source['id'], 'source_record_id': str(uuid4()),
        'entity_id': scene.entity['id'], 'observed_at': stamp.isoformat(),
        'business_day': str(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date()),
        'attributes': {'temperature': value}, 'extractor_id': scene.tool['id'], 'startup_mode': 'manual'}
    response = scene.client.post('/api/observations', json=request)
    assert response.status_code == 201, response.text
    observation = response.json()
    with Session(scene.bound) as db:
        job = db.scalar(select(m.Job).where(m.Job.workspace_id == scene.workspace_id,
            m.Job.idempotency_key == 'extract:' + observation['id']))
        assert job.kind == 'process_observation'
        return observation, job.id


def run_default(scene, monkeypatch, complete):
    assert 'process_observation' in worker.SETUP_KINDS
    assert ('workspace_observation_intake', 'deliver_pending') in worker.SETUP_POLL_ROOTS
    assert ('workspace_observation_intake', 'reconcile_daily') in worker.SETUP_DAILY_ROOTS
    stop = StopEvent(); calls = []
    real_process = worker.process_once
    def imported(name, package):
        if name == '.workspace_observation_intake': return intake
        names = {method for module, method in (*worker.SETUP_POLL_ROOTS, *worker.SETUP_DAILY_ROOTS)
            if name == '.' + module}
        return NS(**{method: (lambda **_kwargs: None) for method in names})
    def process(*args, **kwargs):
        assert kwargs['workspace_id'] == scene.workspace_id and kwargs['deliver'] is False
        result = real_process(*args, **kwargs); calls.append(result)
        with Session(scene.bound) as db:
            if complete(db): stop.set()
        if len(calls) >= 8: stop.set()
        return result
    monkeypatch.setattr(worker, 'import_module', imported)
    monkeypatch.setattr(worker, '_StopEvent', lambda: stop)
    monkeypatch.setattr(worker.signal, 'getsignal', lambda _signum: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *_args: None)
    monkeypatch.setattr(worker, 'process_once', process)
    monkeypatch.setattr(worker, 'daily_reconcile', lambda: pytest.fail('global legacy reconciliation started'))
    monkeypatch.setattr(worker, 'monitor_once', lambda: pytest.fail('global legacy monitoring started'))
    assert worker.run_setup(scene.workspace_id) == 0
    assert len(calls) < 8, 'The default workspace loop never reached its saved acceptance condition.'


def data_event(db, scene, identity):
    return db.scalar(select(m.Event).where(m.Event.workspace_id == scene.workspace_id,
        m.Event.idempotency_key == 'data:' + identity))


def daily_events(db, scene):
    return list(db.scalars(select(m.Event).where(m.Event.workspace_id == scene.workspace_id,
        m.Event.data['schema'].as_string() == intake.SCHEMA).order_by(m.Event.created_at, m.Event.id)))


def inventory(db, cls, workspace_id):
    names = [column.key for column in cls.__table__.columns if not column.info.get('typed_payload_internal')]
    return {row.id: {name: copy.deepcopy(getattr(row, name)) for name in names}
        for row in db.scalars(select(cls).where(cls.workspace_id == workspace_id))}


def foreign_rows(scene):
    """Explicit synthetic foreign rows prove both queue and ACK isolation."""
    with Session(scene.bound, expire_on_commit=False) as db:
        other = m.Workspace(name='intake foreign must remain unchanged'); db.add(other); db.flush()
        db.add(m.Membership(user_id=scene.actor_id, workspace_id=other.id, role='admin'))
        source = db.get(m.Capability, scene.source['id'])
        foreign = m.Capability(workspace_id=other.id, namespace='foreign_intake', name='source',
            release_version='1.0.0', kind='source', status='published', data=copy.deepcopy(source.data),
            content_hash=source.content_hash, validated_hash=source.validated_hash)
        db.add(foreign); db.flush()
        row = m.Observation(workspace_id=other.id, source_id=foreign.id,
            source_record_id='explicit-foreign-synthetic', processing_version='1.0.0',
            business_day='2026-10-01', data={'submitted_by': scene.actor_id})
        db.add(row); db.flush()
        db.add(m.Job(workspace_id=other.id, kind='process_observation', idempotency_key='extract:' + row.id,
            data={'actor_id': scene.actor_id, 'observation_id': row.id, 'extractor_id': scene.tool['id']}))
        db.add(m.Event(workspace_id=other.id, kind='perception.daily', idempotency_key='foreign-pending',
            data={'explicit_test_only': True}))
        db.commit()
        models = (m.Capability, m.Observation, m.Job, m.Event, m.Memory, m.Audit)
        return other.id, {cls: inventory(db, cls, other.id) for cls in models}


def test_default_loop_extracts_plain_upload_and_receives_daily_without_specialized_groups(scene, monkeypatch):
    observation, job_id = ingest(scene)
    other, before = foreign_rows(scene)
    def complete(db):
        event = data_event(db, scene, observation['id'])
        return bool(event and event.status == 'received' and any(e.status == 'received' for e in daily_events(db, scene)))
    run_default(scene, monkeypatch, complete)
    # A new reconciliation revision reflects the now-ACKed original; no model rerun.
    assert intake.reconcile_daily(scene.workspace_id) == 1
    intake.deliver_pending(scene.workspace_id)
    scene.bound.dispose()
    with Session(scene.bound) as db:
        job = db.get(m.Job, job_id); row = db.get(m.Observation, observation['id'])
        assert job.status == 'succeeded' and job.attempts == 1 and row.status == 'processed'
        pin = job.data[intake.EXECUTION_KEY]
        assert pin['source_hash'] == scene.source['content_hash']
        assert pin['source_pin_basis'] == 'first_scoped_execution_current_definition'
        assert len(job.data[intake.ATTEMPTS_KEY]) == 1
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == scene.workspace_id)))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].data['output']['attributes'] == {'temperature': 20}
        assert not list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id == scene.workspace_id)))
        event = data_event(db, scene, row.id)
        assert event.status == 'received' and event.attempts == 1 and event.received_at is not None
        assert len(list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == scene.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id)))) == 1
        latest = daily_events(db, scene)[-1]
        assert latest.status == 'received' and latest.data['status'] == 'complete'
        assert latest.data['business_day'] == observation['business_day'] and latest.data['processed_ids'] == [row.id]
        assert {cls: inventory(db, cls, other) for cls in before} == before
        count = len(daily_events(db, scene))
    assert intake.reconcile_daily(scene.workspace_id) == 0
    intake.deliver_pending(scene.workspace_id)
    with Session(scene.bound) as db:
        assert len(daily_events(db, scene)) == count and data_event(db, scene, row.id).attempts == 1


def test_pending_history_ack_does_not_require_current_source_or_actor_execution_permission(scene, monkeypatch):
    observation, job_id = ingest(scene)
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    with Session(scene.bound) as db:
        event = data_event(db, scene, observation['id']); assert event.status == 'pending_delivery'
        db.get(m.Capability, scene.source['id']).status = 'revoked'
        db.get(m.Capability, scene.tool['id']).status = 'revoked'
        db.get(m.User, scene.actor_id).active = False
        calls = inventory(db, m.Invocation, scene.workspace_id); db.commit()
    def complete(db): return data_event(db, scene, observation['id']).status == 'received'
    run_default(scene, monkeypatch, complete)
    scene.bound.dispose()
    with Session(scene.bound) as db:
        assert inventory(db, m.Invocation, scene.workspace_id) == calls
        assert db.get(m.Job, job_id).attempts == 1
        assert data_event(db, scene, observation['id']).status == 'received'


@pytest.mark.parametrize('revocation', ['source', 'source_hash', 'extractor', 'actor', 'membership'])
def test_old_queued_input_cannot_bypass_current_execution_revocation(scene, monkeypatch, revocation):
    observation, job_id = ingest(scene)
    with Session(scene.bound) as db:
        if revocation == 'source': db.get(m.Capability, scene.source['id']).status = 'revoked'
        if revocation == 'source_hash':
            cap = db.get(m.Capability, scene.source['id']); cap.data = {**cap.data, 'description': 'unvalidated change'}
        if revocation == 'extractor': db.get(m.Capability, scene.tool['id']).status = 'revoked'
        if revocation == 'actor': db.get(m.User, scene.actor_id).active = False
        if revocation == 'membership':
            db.scalar(select(m.Membership).where(m.Membership.workspace_id == scene.workspace_id,
                m.Membership.user_id == scene.actor_id)).role = 'viewer'
        db.commit()
    def complete(db):
        failure = db.scalar(select(m.Event).where(m.Event.workspace_id == scene.workspace_id,
            m.Event.idempotency_key == 'failure:' + observation['id']))
        return bool(db.get(m.Job, job_id).status == 'failed' and failure and failure.status == 'received')
    run_default(scene, monkeypatch, complete)
    with Session(scene.bound) as db:
        assert not list(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id == scene.workspace_id)))
        assert db.get(m.Job, job_id).attempts == 1
        assert db.get(m.Observation, observation['id']).status == 'failed'


def test_frozen_source_pin_cannot_be_resigned_after_legitimate_manifest_change(scene):
    observation, job_id = ingest(scene)
    with Session(scene.bound) as db:
        job = db.get(m.Job, job_id); job.attempts = 1
        intake.guard_execution(db, scene.p, job, db.get(m.Observation, observation['id']), first=True)
        db.commit(); original = copy.deepcopy(job.data[intake.EXECUTION_KEY])
    with Session(scene.bound) as db:
        cap = db.get(m.Capability, scene.source['id'])
        cap.data = {**cap.data, 'description': 'a different but internally valid definition'}
        cap.content_hash = cap.validated_hash = digest(cap.data); db.commit()
    with Session(scene.bound) as db:
        with pytest.raises(HTTPException):
            intake.guard_execution(db, scene.p, db.get(m.Job, job_id), db.get(m.Observation, observation['id']), first=True)
        db.rollback()
        assert db.get(m.Job, job_id).data[intake.EXECUTION_KEY] == original


@pytest.mark.parametrize('pin_before_failure', [False, True])
def test_real_other_admin_retry_keeps_submitter_source_pin_and_per_attempt_actor(scene, pin_before_failure):
    observation, job_id = ingest(scene)
    original_pin = None
    if pin_before_failure:
        # A saved reservation fixture tests a later actor without inventing a
        # model result. The API retry and subsequent extraction below are real.
        with Session(scene.bound) as db:
            job = db.get(m.Job, job_id); job.attempts = 1
            intake.guard_execution(db, scene.p, job, db.get(m.Observation, observation['id']), first=True)
            original_pin = copy.deepcopy(job.data[intake.EXECUTION_KEY])
            job.attempts = 0; db.commit()
    with Session(scene.bound) as db:
        db.get(m.Capability, scene.source['id']).status = 'revoked'; db.commit()
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    with Session(scene.bound) as db:
        row = db.get(m.Observation, observation['id']); version = row.version
        assert row.status == 'failed' and db.get(m.Job, job_id).attempts == 1
        db.get(m.Capability, scene.source['id']).status = 'published'; db.commit()
    username = 'other_intake_' + uuid4().hex
    password = 'Synthetic-intake-test-only-47!'
    response = scene.client.post('/api/users', json={'username': username, 'password': password, 'role': 'admin'})
    assert response.status_code == 201, response.text
    other_actor = response.json()['id']
    with TestClient(app) as other:
        response = other.post('/api/login', json={'username': username, 'password': password})
        assert response.status_code == 200, response.text
        other.headers.update({'X-CSRF-Token': response.json()['csrf_token'], 'X-Workspace-ID': scene.workspace_id})
        response = other.post('/api/observations/' + observation['id'] + '/retry',
            json={'expected_version': version, 'reason': 'explicit other administrator recovery', 'request_id': uuid4().hex})
        assert response.status_code == 200, response.text
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    with Session(scene.bound) as db:
        job = db.get(m.Job, job_id); row = db.get(m.Observation, observation['id'])
        assert job.status == 'succeeded' and job.attempts == 2 and row.status == 'processed'
        assert row.data['submitted_by'] == scene.actor_id and job.data['actor_id'] == other_actor
        pin = job.data[intake.EXECUTION_KEY]
        if original_pin is not None:
            assert pin == original_pin
        else:
            assert pin['initial_attempt'] == 2 and pin['initial_actor_id'] == other_actor
        assert pin['original_submitter_id'] == scene.actor_id
        attempts = job.data[intake.ATTEMPTS_KEY]
        assert attempts[-1]['attempt'] == 2 and attempts[-1]['actor_id'] == other_actor
        retry = db.get(m.Audit, attempts[-1]['retry_audit_id'])
        assert retry.action == 'observation.retry' and retry.actor_id == other_actor and retry.data['next_attempt'] == 2


def test_actor_switch_without_real_retry_audit_is_rejected_even_without_a_first_pin(scene):
    observation, job_id = ingest(scene)
    response = scene.client.post('/api/users', json={'username': 'unapproved_attempt_' + uuid4().hex,
        'password': 'Synthetic-intake-test-only-48!', 'role': 'admin'})
    assert response.status_code == 201, response.text
    actor_id = response.json()['id']
    with Session(scene.bound) as db:
        job = db.get(m.Job, job_id); job.attempts = 2
        job.data = {**job.data, 'actor_id': actor_id}
        p = Principal(actor_id, scene.workspace_id, 'admin', 'negative-fixture')
        with pytest.raises(HTTPException):
            intake.guard_execution(db, p, job, db.get(m.Observation, observation['id']), first=True)
        db.rollback()
        assert intake.EXECUTION_KEY not in db.get(m.Job, job_id).data


def test_source_revoked_after_actual_extraction_cannot_publish_new_data_event(scene, monkeypatch):
    observation, job_id = ingest(scene)
    real = worker.perception_agent
    def revoke_after_result(*args, **kwargs):
        result = real(*args, **kwargs)
        with Session(scene.bound) as db:
            db.get(m.Capability, scene.source['id']).status = 'revoked'; db.commit()
        return result
    monkeypatch.setattr(worker, 'perception_agent', revoke_after_result)
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    with Session(scene.bound) as db:
        assert db.get(m.Job, job_id).status == 'failed'
        assert db.get(m.Observation, observation['id']).status == 'failed'
        assert data_event(db, scene, observation['id']) is None
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == scene.workspace_id)))
        assert len(calls) == 1 and calls[0].status == 'succeeded'  # preserve the real original call


def test_failure_retry_of_ten_day_old_input_preserves_frozen_failure_and_creates_complete_revision(scene):
    observation, job_id = ingest(scene)
    with Session(scene.bound) as db:
        db.get(m.Capability, scene.tool['id']).status = 'revoked'; db.commit()
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    assert intake.reconcile_daily(scene.workspace_id) == 1
    intake.deliver_pending(scene.workspace_id)
    with Session(scene.bound) as db:
        failed = db.get(m.Observation, observation['id'])
        old = daily_events(db, scene)[0]; old_id = old.id; old_value = copy.deepcopy(old.data)
        assert old_value['failed_ids'] == [failed.id] and old_value['status'] == 'incomplete'
        version = failed.version
        db.get(m.Capability, scene.tool['id']).status = 'published'; db.commit()
    response = scene.client.post('/api/observations/' + observation['id'] + '/retry',
        json={'expected_version': version, 'reason': 'restore the original local tool', 'request_id': uuid4().hex})
    assert response.status_code == 200, response.text
    assert worker.process_once(job_id, kinds=['process_observation'], workspace_id=scene.workspace_id, deliver=False)
    intake.deliver_pending(scene.workspace_id)
    assert intake.reconcile_daily(scene.workspace_id) == 1
    intake.deliver_pending(scene.workspace_id)
    with Session(scene.bound) as db:
        assert db.get(m.Event, old_id).data == old_value
        latest = daily_events(db, scene)[-1]
        assert latest.data['business_day'] == observation['business_day'] and latest.data['status'] == 'complete'
        assert latest.data['failed_ids'] == [] and latest.data['processed_ids'] == [observation['id']]
        assert db.get(m.Job, job_id).attempts == 2
        assert db.scalar(select(m.Event).where(m.Event.workspace_id == scene.workspace_id,
            m.Event.idempotency_key == 'failure:' + observation['id'])).status == 'received'


def test_actual_day_keyset_and_two_coordinators_do_not_duplicate_or_starve_late_groups(scene, monkeypatch):
    monkeypatch.setattr(intake, 'PAGE', 2)
    for days in (10, 11, 12): ingest(scene, stamp=datetime.now(timezone.utc) - timedelta(days=days))
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(intake.reconcile_daily, [scene.workspace_id, scene.workspace_id]))
    assert sum(counts) == 3
    with Session(scene.bound) as db:
        events = daily_events(db, scene); assert len(events) == 3
        assert len({event.data['business_day'] for event in events}) == 3
        for event in events:
            assert intake.validate_daily(db, event) == event.data
        assert len(list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == scene.workspace_id,
            m.Audit.action == intake.DAILY_AUDIT)))) == 3


def test_blocked_daily_is_never_reset_or_rebuilt_and_other_original_ack_can_continue(scene):
    observation, job_id = ingest(scene)
    assert intake.reconcile_daily(scene.workspace_id) == 1
    with Session(scene.bound) as db:
        daily = daily_events(db, scene)[0]; identity = daily.id
        row = db.scalar(select(m.Audit).where(m.Audit.workspace_id == scene.workspace_id,
            m.Audit.object_id == daily.id, m.Audit.action == intake.DAILY_AUDIT))
        row.data = {'damaged_for_contract_test': True}; db.commit()
    intake.deliver_pending(scene.workspace_id)
    with Session(scene.bound) as db:
        assert db.get(m.Event, identity).status == 'blocked_delivery'
        assert db.get(m.Event, identity).attempts == 1
    assert intake.reconcile_daily(scene.workspace_id) == 0
    intake.deliver_pending(scene.workspace_id)
    with Session(scene.bound) as db:
        assert db.get(m.Event, identity).status == 'blocked_delivery' and db.get(m.Event, identity).attempts == 1
        assert db.get(m.Job, job_id).attempts == 0
