"""Real PG lease phases, NOT source, model, paid or business acceptance.

The old phase fixture supplies explicitly DRAFT/FK-only metadata. We give that
metadata the new outer/inner family shape; no accepted Consumer history,
published source, CloudCall or provider result is manufactured. Only the
normal-worker test substitutes its business handler, explicitly at that boundary.
Root alone executes these tests. The heartbeat test scales only the test lease
to two seconds; production remains its existing 180/30-second contract.
"""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session

from generic_agents import leases, worker, models as m
from generic_agents.db import engine
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
from tests.test_catalogue_allocation_handler_lease_phase_v213 import (
    case as old_case, recovered, activate, probe, assert_renewal_is_locked, disposable_only)


PROFILE = 'catalogue-current-allocation-v260'
OUTER = 'report-business-catalogue-current-allocation-preparation-v260'
INNER = 'report-business-catalogue-allocation-preparation-v213'


def case(account, *, queued=False):
    s = old_case(account, queued=queued)
    with Session(engine()) as db:
        job, row = db.get(m.Job, s.job_id), db.get(Consumer, s.consumer_id)
        job.data = {**job.data, 'generation_profile': PROFILE}
        row.data = {'dispatch_started': False, 'preview': {'generation_profile': PROFILE,
            'body': {'generation_profile': PROFILE}, 'preparation': {
                'schema': OUTER, 'native_preparation': {'schema': INNER}}}}
        db.commit()
    return s


def test_current_active_phase_renews_past_original_deadline_and_commit_still_takes_exclusive_fence(account, monkeypatch):
    s = case(account)
    monkeypatch.setattr(leases, 'LEASE_SECONDS', 2.0)
    monkeypatch.setattr(leases, 'HEARTBEAT_SECONDS', .05)
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
            audit_id = probe(db, s); held = []
            def exclusive_commit(session):
                assert_renewal_is_locked(s)
                held.append(True)
            event.listen(db, 'before_commit', exclusive_commit)
            db.commit()
            assert held == [True]
    assert phase.closed and not phase.active
    with Session(engine()) as db:
        assert db.get(m.Audit, audit_id) is not None
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(
            m.CloudCall.workspace_id == s.p.workspace_id)) == 0


def test_current_fresh_hint_checks_exact_false_and_both_frozen_families_without_admitting_execution(account):
    s = case(account)
    with Session(engine()) as db:
        row, job = db.get(Consumer, s.consumer_id), db.get(m.Job, s.job_id)
        original = copy.deepcopy(row.data)
        assert worker._catalogue_allocation_fresh_phase_job(db, job)
        bad_values = []
        for value in (0, None, 'false', True):
            bad_values.append({**copy.deepcopy(original), 'dispatch_started': value})
        for path in ('outer', 'inner', 'body', 'preview'):
            changed = copy.deepcopy(original)
            if path == 'outer': changed['preview']['preparation']['schema'] = INNER
            elif path == 'inner': changed['preview']['preparation']['native_preparation']['schema'] = 'catalogue-v211'
            elif path == 'body': changed['preview']['body']['generation_profile'] = 'catalogue-allocation-v213'
            else: changed['preview']['generation_profile'] = 'catalogue-allocation-v213'
            bad_values.append(changed)
        for changed in bad_values:
            row.data = changed; db.flush()
            assert worker._catalogue_allocation_fresh_phase_job(db, job) is False
        db.rollback()


@pytest.mark.parametrize('fault', ['consumer-removed', 'consumer-job', 'consumer-actor', 'outer', 'inner', 'body', 'job-profile'])
def test_each_active_begin_rechecks_actual_current_consumer_job_and_frozen_family(account, fault):
    s = case(account)
    with recovered(s) as (db, phase):
        activate(phase, s)
        with Session(engine()) as other:
            row, job = other.get(Consumer, s.consumer_id), other.get(m.Job, s.job_id)
            if fault == 'consumer-removed':
                other.delete(row)
            elif fault == 'consumer-job':
                unrelated = m.Job(workspace_id=s.p.workspace_id, kind='fixture_nonexecuted',
                    status='internal', idempotency_key='phase-unrelated:' + uuid4().hex, data={})
                other.add(unrelated); other.flush(); row.job_id = unrelated.id
            elif fault == 'consumer-actor':
                unrelated = m.User(username='phase_other_' + uuid4().hex[:12], password_hash='fixture-only-no-login')
                other.add(unrelated); other.flush(); row.actor_id = unrelated.id
            elif fault == 'job-profile':
                job.data = {**job.data, 'generation_profile': 'catalogue-v211'}
            else:
                changed = copy.deepcopy(row.data)
                if fault == 'outer': changed['preview']['preparation']['schema'] = INNER
                elif fault == 'inner': changed['preview']['preparation']['native_preparation']['schema'] = 'catalogue-v211'
                else: changed['preview']['body']['generation_profile'] = 'catalogue-allocation-v213'
                row.data = changed
            other.commit()
        with pytest.raises(leases.LeaseLost):
            db.get(m.Job, s.job_id, populate_existing=True)
        db.rollback()


def test_active_phase_keeps_legitimate_issued_and_terminal_consumer_checkpoints_readable(account):
    s = case(account)
    with recovered(s) as (db, phase):
        activate(phase, s)
        # These metadata-only transitions exercise BEGIN association, not an
        # accepted business answer. Job remains the actual running leased job.
        for status in ('running', 'response_unknown', 'review', 'needs_clarification', 'failed'):
            with Session(engine()) as other:
                row = other.get(Consumer, s.consumer_id)
                row.data = {**row.data, 'dispatch_started': True}; row.status = status
                other.commit()
            assert db.get(m.Job, s.job_id, populate_existing=True).status == 'running'
            assert leases.renew(engine(), s.job_id, s.token) is True
            db.commit()
    assert phase.closed


@pytest.mark.parametrize('fault', ['token', 'expired', 'status', 'actor-revoked'])
def test_current_phase_commit_keeps_original_token_expiry_status_and_actor_fences(account, fault):
    s = case(account)
    expected = leases.ExecutionUnauthorized if fault == 'actor-revoked' else leases.LeaseLost
    with recovered(s) as (db, phase):
        activate(phase, s)
        db.get(m.Job, s.job_id, populate_existing=True); audit_id = probe(db, s)
        with engine().begin() as connection:
            if fault == 'actor-revoked':
                connection.execute(update(m.User).where(m.User.id == s.p.user_id).values(active=False))
            else:
                values = {'lease_token': str(uuid4())} if fault == 'token' else {'status': 'cancelled'} \
                    if fault == 'status' else {'lease_until': func.clock_timestamp() - timedelta(seconds=1)}
                connection.execute(update(m.Job).where(m.Job.id == s.job_id).values(**values))
        with pytest.raises(expected): db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(m.Audit, audit_id) is None


@pytest.mark.parametrize('issued', [False, True])
def test_normal_worker_activates_new_phase_only_after_fresh_recovery_commit(account, monkeypatch, issued):
    s = case(account, queued=True)
    if issued:
        with Session(engine()) as db:
            row = db.get(Consumer, s.consumer_id); row.data = {**row.data, 'dispatch_started': True}; db.commit()
    from generic_agents import report_saved_map_design_consumer_v176 as handler
    real_activate, real_actor = leases._CatalogueAllocationHandlerPhase.activate, worker.actor
    seen = []
    def activated(self, **identity):
        assert not self.db.in_transaction(); seen.append('activate-after-commit')
        return real_activate(self, **identity)
    def actor(db, workspace, user):
        phase = db.info.get(leases._CATALOGUE_ALLOCATION_PHASE)
        assert type(phase) is leases._CatalogueAllocationHandlerPhase and phase.active is (not issued)
        seen.append('actor'); return real_actor(db, workspace, user)
    def business_boundary(db, p, job):
        # Explicit omission of source/model/business execution. Normal claim,
        # recovery, typed phase, actor and final lease fence remain production.
        phase = db.info[leases._CATALOGUE_ALLOCATION_PHASE]
        assert phase.active is (not issued)
        if issued: assert_renewal_is_locked(s, job.lease_token)
        else: assert leases.renew(engine(), job.id, job.lease_token) is True
        seen.append('business-boundary')
        return {'status': 'response_unknown' if issued else 'review'}
    monkeypatch.setattr(leases._CatalogueAllocationHandlerPhase, 'activate', activated)
    monkeypatch.setattr(worker, 'actor', actor)
    monkeypatch.setattr(handler, 'run', business_boundary)
    assert worker.process_once(job_id=s.job_id, workspace_id=s.p.workspace_id, deliver=False)
    assert seen == (['actor', 'business-boundary'] if issued
        else ['activate-after-commit', 'actor', 'business-boundary'])
    with Session(engine()) as db:
        assert db.get(m.Job, s.job_id).status == ('failed' if issued else 'succeeded')
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(
            m.CloudCall.workspace_id == s.p.workspace_id)) == 0
