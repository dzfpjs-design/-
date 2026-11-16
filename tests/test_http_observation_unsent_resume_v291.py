"""Historical consumer classification over explicit cold source/SQL IO spies.

The actual normal-key, complete _prior_proofs and _unsent checks run. Source,
Exchange ledger rows and database results are IO boundaries, not real PG/IAM,
leases or a first POST. No synthetic running ownership is installed.
"""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from generic_agents import http_observation_task_consumer_v270 as subject
from generic_agents import managed_source_unsent_v270 as unsent
from tests.test_managed_source_unsent_v270 import records, FROZEN_AT


def at(value): return datetime.fromisoformat(value)


def scene(monkeypatch):
    pin, facts, proofs, current = records()
    workspace = '11111111-1111-4111-8111-111111111111'
    p = NS(workspace_id=workspace)
    task = NS(id='22222222-2222-4222-8222-222222222222',
        data={'instance_id': '33333333-3333-4333-8333-333333333333',
            'instance_release_id': '44444444-4444-4444-8444-444444444444'})
    acq = NS(id=pin['acquisition_id'], status='authorized', version=3, data={'actor_id': pin['actor_id']})
    plan = NS(id='55555555-5555-4555-8555-555555555555', status='awaiting_dispatch',
        data={'current_acquisition_id': acq.id, 'history': [{'kind': 'resource_recovered',
            'acquisition_id': acq.id, 'at': '2026-11-01T12:01:00+00:00'}]})
    row = NS(id=pin['exchange_id'], instance_id=task.data['instance_id'], release_id=task.data['instance_release_id'],
        status='awaiting_receipt', observation_id=None, dispatch_job_id=pin['original_dispatch_job_id'],
        created_at=at(FROZEN_AT), data={'receipt_history': [], 'active_read_job_id': None, 'read_jobs': []})
    jobs = []
    for fact in facts:
        jobs.append(NS(id=fact['id'], kind='dispatch_acquisition', idempotency_key=fact['key'],
            status='failed' if fact['id'] != current else 'queued', attempts=1 if fact['id'] != current else 0,
            lease_until=None, lease_token=fact['lease_token'] if fact['id'] != current else None,
            created_at=at('2026-11-01T12:02:00+00:00'), data={'actor_id': fact['actor_id'],
                'acquisition_id': fact['acquisition_id'], **({'finished_at': fact['finished_at']} if fact['id'] != current else {})}))
    audits = [NS(**value) for value in proofs]
    retry = NS(actor_id=pin['actor_id'], created_at=at('2026-11-01T12:02:01+00:00'),
        data={'reason': '明确核对同一原未发送请求后恢复'})
    retry_rows = [retry]
    class DB:
        def scalars(self, statement):
            text = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert 'sensor.plan_retried' in text and plan.id in text and workspace in text
            return retry_rows
        def scalar(self, statement):
            text = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert pin['actor_id'] in text and workspace in text and 'memberships' in text
            return pin['actor_id']
    db = DB()
    monkeypatch.setattr(unsent, '_cold_scope', lambda *args: (acq, row, copy.deepcopy(pin)))
    monkeypatch.setattr(unsent, '_rows', lambda *args: (jobs, audits))
    monkeypatch.setattr(unsent, '_leased', lambda *args: pytest.fail('Inventory must never fabricate worker ownership'))
    binding = {'acquisition': acq, 'plan': plan, 'exchange': row}
    return NS(p=p, task=task, acq=acq, plan=plan, row=row, jobs=jobs, audits=audits,
        old=jobs[0], resume=jobs[1], retries=retry_rows, db=db, binding=binding)


def test_proven_official_same_acquisition_resume_preserves_failed_history_without_blocking_queued_first_send(monkeypatch):
    s = scene(monkeypatch)
    before = copy.deepcopy((s.old.__dict__, s.resume.__dict__, s.acq.__dict__, s.row.__dict__))
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.old) is True
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.old, db=s.db, p=s.p) is False
    assert s.old.status == 'failed' and s.resume.status == 'queued' and s.resume.attempts == 0
    assert (s.old.__dict__, s.resume.__dict__, s.acq.__dict__, s.row.__dict__) == before


@pytest.mark.parametrize('change', [
    lambda s: s.audits.clear(),
    lambda s: s.audits.append(copy.deepcopy(s.audits[0])),
    lambda s: s.audits[0].data.update(lease_token='another-old-lease'),
    lambda s: setattr(s.old, 'lease_until', at('2026-11-01T12:10:00+00:00')),
    lambda s: setattr(s.resume, 'attempts', 1),
    lambda s: setattr(s.resume, 'lease_token', 'not-a-queued-worker'),
    lambda s: setattr(s.resume, 'idempotency_key', 'dispatch:' + s.acq.id + ':resume:2'),
    lambda s: s.resume.data.update(recovery_only=True),
    lambda s: s.plan.data.update(history=[]),
    lambda s: s.plan.data['history'][0].update(at='2026-11-01T11:59:00+00:00'),
    lambda s: s.retries.clear(),
    lambda s: s.acq.data.update(dispatch_started_at='2026-11-01T12:02:00+00:00'),
    lambda s: s.row.data['receipt_history'].append({'saved': True}),
    lambda s: s.row.data['read_jobs'].append({'original_get': True}),
])
def test_missing_or_changed_proof_queue_history_or_outcome_keeps_original_failure_blocked(monkeypatch, change):
    s = scene(monkeypatch); change(s)
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.old, db=s.db, p=s.p) is True


def test_another_unknown_original_or_current_resume_failure_is_never_hidden(monkeypatch):
    s = scene(monkeypatch)
    unknown = copy.deepcopy(s.old); unknown.id = '66666666-6666-4666-8666-666666666666'; unknown.status = 'unknown'
    s.jobs.append(unknown)
    assert subject._pending_unsent_resume(s.db, s.p, s.task, s.binding, s.old) is False
    s = scene(monkeypatch); s.resume.status = 'failed'
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.resume, db=s.db, p=s.p) is True
