"""Real disposable-PG lease fences; never source, model or payment authority.

Minimal queued Consumer metadata exercises only the Worker's lightweight phase
selection. Tests replacing business execution say so explicitly. No CloudCall,
paid success, real source adoption or provider request is manufactured.
"""
import copy
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import leases, worker, models as m
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.contracts import digest
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer

PROFILE = 'catalogue-allocation-v213'


@pytest.fixture(autouse=True)
def disposable_only():
    assert engine().url.database and engine().url.database.startswith('ga_test_')


def case(account, *, queued=False):
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'worker')
        at = db.scalar(select(func.clock_timestamp()))
        consumer_id = str(uuid4())
        job = m.Job(workspace_id=p.workspace_id, kind='read_saved_map_design_v176',
            status='queued' if queued else 'running', attempts=0 if queued else 1,
            idempotency_key='allocation-phase-test:' + uuid4().hex, available_at=at,
            lease_token=None if queued else str(uuid4()), lease_until=None if queued else at + timedelta(seconds=30),
            data={'actor_id': p.user_id, 'consumer_id': consumer_id, 'generation_profile': PROFILE})
        # These are deliberately DRAFT metadata for phase selection only, not
        # published capability/source or a valid Consumer business inventory.
        cap = m.Capability(workspace_id=p.workspace_id, namespace='phase_test', name=uuid4().hex,
            release_version='1', kind='foundation_model', status='draft', content_hash=digest({}), data={})
        producer = m.SetupReportDraft(workspace_id=p.workspace_id, actor_id=p.user_id,
            request_id=str(uuid4()), status='queued', data={'test_only': 'phase-selection-not-source'})
        db.add_all([job, cap, producer]); db.flush()
        row = Consumer(id=consumer_id, workspace_id=p.workspace_id, actor_id=p.user_id,
            request_id=str(uuid4()), producer_id=producer.id, model_id=cap.id, revision=1,
            grant_hash=digest({'test_only': consumer_id}), status='queued', job_id=job.id,
            reserved_call_id=None, data={'dispatch_started': False, 'preview': {
                'generation_profile': PROFILE, 'body': {'generation_profile': PROFILE},
                'preparation': {'schema': 'report-business-catalogue-allocation-preparation-v213'}}})
        db.add(row); db.commit()
        return NS(p=p, job_id=job.id, token=job.lease_token, consumer_id=row.id)


@contextmanager
def recovered(s):
    with Session(engine(), expire_on_commit=False) as db:
        with leases.catalogue_allocation_handler_lease_phase(db, s.job_id, s.token) as phase:
            leases.attach(db, s.job_id, s.token)
            db.info['worker_actor'] = s.p
            job = db.get(m.Job, s.job_id)
            assert job.status == 'running'
            assert worker._catalogue_allocation_fresh_phase_job(db, job)
            db.commit()
            yield db, phase


def activate(phase, s):
    phase.activate(workspace_id=s.p.workspace_id, actor_id=s.p.user_id)


def probe(db, s):
    row = m.Audit(workspace_id=s.p.workspace_id, actor_id=s.p.user_id,
        action='test.catalogue_allocation_handler_phase_v213', object_id=s.job_id, data={'lease_test_only': True})
    db.add(row); db.flush()
    return row.id


def assert_renewal_is_locked(s, token=None):
    # renew() deliberately propagates the DB lock error. Only heartbeat's loop
    # catches it; do not weaken production fencing to turn it into False.
    with pytest.raises(DBAPIError) as blocked:
        leases.renew(engine(), s.job_id, token or s.token)
    code = getattr(blocked.value.orig, 'sqlstate', getattr(blocked.value.orig, 'pgcode', None))
    assert code == '55P03'


def test_active_allocation_can_renew_past_original_deadline_and_keep_exclusive_commit_fence(account, monkeypatch):
    s = case(account)
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', 0.05)
    with engine().begin() as connection:
        connection.execute(update(m.Job).where(m.Job.id == s.job_id).values(
            lease_until=func.clock_timestamp() + timedelta(seconds=2)))
    with recovered(s) as (db, phase):
        activate(phase, s)
        with leases.heartbeat(engine(), s.job_id, s.token):
            original = db.get(m.Job, s.job_id).lease_until
            db.execute(text('SELECT pg_sleep(2.4)'))
            with engine().connect() as connection:
                token, deadline, at = connection.execute(select(m.Job.lease_token, m.Job.lease_until,
                    func.clock_timestamp()).where(m.Job.id == s.job_id)).one()
            assert token == s.token and at > original and deadline > at and deadline > original
            audit_id = probe(db, s)
            held = []
            def after_lease_commit_fence(session):
                # Attached after leases.attach: its original FOR UPDATE fence
                # must already be held, so another renewal cannot take the row.
                assert_renewal_is_locked(s)
                held.append(True)
            event.listen(db, 'before_commit', after_lease_commit_fence)
            db.commit()
            assert held == [True]
        assert phase.active and not phase.closed
    assert phase.closed and not phase.active
    with Session(engine()) as db:
        assert db.get(m.Audit, audit_id) is not None
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(
            m.CloudCall.workspace_id == s.p.workspace_id)) == 0


@pytest.mark.parametrize('fault', ['token', 'expired', 'status', 'actor_revoked'])
def test_commit_rechecks_actual_token_deadline_status_and_current_actor(account, fault):
    s = case(account)
    expected = leases.ExecutionUnauthorized if fault == 'actor_revoked' else leases.LeaseLost
    with recovered(s) as (db, phase):
        activate(phase, s)
        db.get(m.Job, s.job_id, populate_existing=True)
        audit_id = probe(db, s)
        with engine().begin() as connection:
            if fault == 'actor_revoked':
                connection.execute(update(m.User).where(m.User.id == s.p.user_id).values(active=False))
            else:
                values = {'lease_token': str(uuid4())} if fault == 'token' else {'status': 'cancelled'} \
                    if fault == 'status' else {'lease_until': func.clock_timestamp() - timedelta(seconds=1)}
                connection.execute(update(m.Job).where(m.Job.id == s.job_id).values(**values))
        with pytest.raises(expected):
            db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(m.Audit, audit_id) is None


@pytest.mark.parametrize('fault', ['kind', 'profile', 'actor', 'workspace', 'recovery_only'])
def test_every_active_begin_reads_actual_job_identity_and_scope_not_retained_hint(account, fault):
    s = case(account)
    with recovered(s) as (db, phase):
        activate(phase, s)
        with Session(engine()) as other:
            job = other.get(m.Job, s.job_id)
            if fault == 'kind': job.kind = 'read_report_draft'
            elif fault == 'profile': job.data = {**job.data, 'generation_profile': 'catalogue-v211'}
            elif fault == 'actor': job.data = {**job.data, 'actor_id': str(uuid4())}
            elif fault == 'recovery_only': job.data = {**job.data, 'recovery_only': True}
            else:
                # No foreign workspace/FK manufacture; change only the typed
                # retained expectation and require the ACTUAL DB row to reject.
                phase.workspace_id = str(uuid4())
            other.commit()
        with pytest.raises(leases.LeaseLost):
            db.get(m.Job, s.job_id, populate_existing=True)
        db.rollback()


def test_inactive_and_json_counterfeit_keep_the_original_job_lock(account):
    s = case(account)
    with recovered(s) as (db, phase):
        assert not phase.active
        db.get(m.Job, s.job_id, populate_existing=True)
        assert_renewal_is_locked(s)
        with pytest.raises(ValueError): activate(phase, s)
        db.rollback()
    with Session(engine()) as db:
        leases.attach(db, s.job_id, s.token)
        db.info[leases._CATALOGUE_ALLOCATION_PHASE] = {'active': True, 'job_id': s.job_id,
            'token': s.token, 'generation_profile': PROFILE}
        db.get(m.Job, s.job_id)
        assert_renewal_is_locked(s)
        db.rollback()


@pytest.mark.parametrize('value', [0, None, 'false', True, {}, []])
def test_fresh_worker_query_requires_actual_json_boolean_false_not_coercible_values(account, value):
    s = case(account)
    with Session(engine()) as db:
        row = db.get(Consumer, s.consumer_id)
        row.data = {**row.data, 'dispatch_started': value}
        db.commit()
        job = db.get(m.Job, s.job_id)
        assert worker._catalogue_allocation_fresh_phase_job(db, job) is False


@pytest.mark.parametrize('fault', ['issued', 'recovery_only', 'old_profile', 'wrong_body', 'wrong_preparation', 'wrong_job'])
def test_issued_unknown_and_old_families_never_get_fresh_worker_phase(account, fault):
    s = case(account)
    with Session(engine()) as db:
        row, job = db.get(Consumer, s.consumer_id), db.get(m.Job, s.job_id)
        if fault == 'issued': row.data = {**row.data, 'dispatch_started': True}
        elif fault == 'recovery_only': job.data = {**job.data, 'recovery_only': True}
        elif fault == 'old_profile': job.data = {**job.data, 'generation_profile': 'catalogue-v211'}
        elif fault == 'wrong_job': job.data = {**job.data, 'consumer_id': str(uuid4())}
        else:
            changed = copy.deepcopy(row.data)
            if fault == 'wrong_body': changed['preview']['body']['generation_profile'] = 'catalogue-v211'
            else: changed['preview']['preparation']['schema'] = 'report-business-catalogue-preparation-v211'
            row.data = changed
        db.commit()
        assert worker._catalogue_allocation_fresh_phase_job(db, job) is False


def test_activation_is_after_recovery_commit_and_closes_on_exception_without_borrowing_other_phase(account):
    s = case(account)
    with Session(engine()) as db:
        with leases.catalogue_allocation_handler_lease_phase(db, s.job_id, s.token) as phase:
            leases.attach(db, s.job_id, s.token)
            db.get(m.Job, s.job_id)
            with pytest.raises(ValueError): activate(phase, s)
            db.commit()
            with leases.graph_handler_lease_phase(db, s.job_id, s.token) as graph:
                graph.activate()
                with pytest.raises(ValueError): activate(phase, s)
            activate(phase, s)
            with pytest.raises(ValueError): activate(phase, s)
        assert phase.closed and not phase.active and leases._CATALOGUE_ALLOCATION_PHASE not in db.info
        with pytest.raises(ValueError): activate(phase, s)
    with pytest.raises(RuntimeError):
        with recovered(s) as (db, failed):
            activate(failed, s)
            raise RuntimeError('public synthetic handler interruption')
    assert failed.closed and not failed.active


@pytest.mark.parametrize('issued', [False, True])
def test_normal_worker_recovers_commits_activates_then_loads_actor_only_for_fresh_candidate(account, monkeypatch, issued):
    s = case(account, queued=True)
    if issued:
        with Session(engine()) as db:
            row = db.get(Consumer, s.consumer_id)
            row.data = {**row.data, 'dispatch_started': True}
            db.commit()
    from generic_agents import report_saved_map_design_consumer_v176 as handler
    original_activate, original_actor = leases._CatalogueAllocationHandlerPhase.activate, worker.actor
    seen = []
    def activated(self, **identity):
        assert not self.db.in_transaction()
        seen.append('activate-after-commit')
        return original_activate(self, **identity)
    def current_actor(db, workspace, user):
        phase = db.info.get(leases._CATALOGUE_ALLOCATION_PHASE)
        if phase is None:
            return original_actor(db, workspace, user)
        assert type(phase) is leases._CatalogueAllocationHandlerPhase
        assert phase.active is (not issued)
        seen.append('current-actor')
        return original_actor(db, workspace, user)
    def business_boundary(db, p, job):
        # Substitute ONLY actual report/model execution: claim/recovery/query/
        # typed phases/actor/transaction/normal finalization remain unchanged.
        phase = db.info[leases._CATALOGUE_ALLOCATION_PHASE]
        assert phase.active is (not issued)
        if issued:
            assert_renewal_is_locked(s, job.lease_token)
        else:
            assert leases.renew(engine(), job.id, job.lease_token) is True
        seen.append('business-boundary')
        return {'status': 'response_unknown' if issued else 'review'}
    monkeypatch.setattr(leases._CatalogueAllocationHandlerPhase, 'activate', activated)
    monkeypatch.setattr(worker, 'actor', current_actor)
    monkeypatch.setattr(handler, 'run', business_boundary)
    assert worker.process_once(job_id=s.job_id, deliver=False)
    assert seen[:3] == (['activate-after-commit', 'current-actor', 'business-boundary'] if not issued
        else ['current-actor', 'business-boundary'])
    with Session(engine()) as db:
        assert db.get(m.Job, s.job_id).status == ('failed' if issued else 'succeeded')
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(
            m.CloudCall.workspace_id == s.p.workspace_id)) == 0
