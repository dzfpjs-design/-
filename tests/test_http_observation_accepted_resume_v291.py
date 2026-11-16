"""Accepted sender history with actual V270 proof functions and explicit IO.

checked_store/cold Source scope/query rows are IO spies, not a real supplier,
PG ledger or permission proof. verify_retry_receipt_origin, normal-key checks,
all old UNSENT proof checks and consumer classification run as implemented.
No ownership token is installed and the original records are never rewritten.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from generic_agents import http_observation_task_consumer_v270 as subject
from generic_agents import http_observation_exchange_v177 as ledger
from generic_agents import managed_source_unsent_v270 as unsent
from generic_agents.contracts import digest
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from tests.test_http_observation_unsent_resume_v291 import scene as pending_scene


def scene(monkeypatch):
    s = pending_scene(monkeypatch)
    _, original_row, pin = unsent._cold_scope(None, None, None)
    row = Exchange(id=original_row.id, workspace_id=s.p.workspace_id, acquisition_id=s.acq.id,
        source_id=pin['source_id'], environment_id=str(uuid4()), instance_id=original_row.instance_id,
        release_id=original_row.release_id, dispatch_job_id=original_row.dispatch_job_id,
        actor_id=pin['actor_id'], status=original_row.status, observation_id=None, version=3,
        created_at=original_row.created_at, data=copy.deepcopy(original_row.data))
    s.row = row; s.binding['exchange'] = row
    monkeypatch.setattr(unsent, '_cold_scope', lambda *args: (s.acq, row, copy.deepcopy(pin)))
    s.acq.status = 'awaiting_observation'; s.acq.idempotency_key = 'sensing:original-frozen-request:1'
    wire = {'request_key': s.acq.idempotency_key, 'action': 'observe'}
    s.acq.data.update(dispatch_started_at='2026-11-01T12:03:00+00:00', request_hash=digest(wire))
    sender = s.resume
    sender.status = 'succeeded'; sender.attempts = 1; sender.lease_token = 'actual-historical-sender-token'
    sender.data['finished_at'] = '2026-11-01T12:04:00+00:00'
    for job in s.jobs: job.workspace_id = s.p.workspace_id
    # A second actually supplied old terminal/no-send record is included in the
    # complete proof inventory; its custody is still an explicitly spied row.
    earlier = copy.deepcopy(s.old); earlier.id = str(uuid4())
    earlier.idempotency_key = 'dispatch:' + s.acq.id + ':resume:2'; earlier.lease_token = 'earlier-original-token'
    earlier.data['finished_at'] = '2026-11-01T12:00:30+00:00'
    s.jobs.insert(1, earlier)
    s.audits.append(NS(id=str(uuid4()), action=unsent.UNSENT, actor_id=pin['actor_id'],
        created_at='2026-11-01T11:59:30+00:00',
        data=unsent._proof_data(pin, unsent._job_pin(earlier, earlier.lease_token))))
    proof_ids = sorted(anchor.id for anchor in s.audits)
    send = NS(id=str(uuid4()), action=unsent.DISPATCH, actor_id=pin['actor_id'],
        created_at='2026-11-01T12:03:00+00:00',
        data=unsent._sent_data(pin, unsent._job_pin(sender, sender.lease_token), proof_ids))
    s.audits.append(send)
    row.actor_id = pin['actor_id']; row.acquisition_id = s.acq.id
    row.data['freeze'] = {'original_request_key': s.acq.idempotency_key,
        'wire_frozen': {'request': wire, 'request_hash': 'a' * 64}}
    origin = {'schema': 'http-observation-receipt-origin-v177', 'operation': 'dispatch_post',
        'job_id': sender.id, 'lease_token': sender.lease_token, 'actor_id': pin['actor_id'],
        'exchange_id': row.id, 'exchange_version': 2, 'original_request_key': s.acq.idempotency_key,
        'wire_request_hash': 'a' * 64, 'read_enqueue_audit_id': None}
    raw = {'status': 'accepted', 'request_key': s.acq.idempotency_key, 'receipt_id': 'synthetic-supplier-id'}
    projection = {'status': 'waiting'}
    row.data['receipt_history'] = [{'raw': raw, 'raw_hash': digest(raw), 'projection': projection,
        'projection_hash': digest(projection), 'receipt_origin': origin}]
    monkeypatch.setattr(ledger, 'checked_store', lambda db, p, identity: row)
    monkeypatch.setattr(unsent, 'scoped', lambda db, model, identity, p: sender)
    return NS(**s.__dict__, sender=sender, earlier=earlier, send=send, origin=origin)


def test_accepted_first_send_with_successful_real_sender_and_all_old_proofs_makes_only_old_failures_history(monkeypatch):
    s = scene(monkeypatch)
    before = copy.deepcopy((s.old.__dict__, s.earlier.__dict__, s.sender.__dict__, s.acq.__dict__, s.row.data))
    for old in (s.old, s.earlier):
        assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, old, db=s.db, p=s.p) is False
        assert old.status == 'failed'
    assert s.sender.status == 'succeeded' and s.sender.lease_until is None
    assert (s.old.__dict__, s.earlier.__dict__, s.sender.__dict__, s.acq.__dict__, s.row.data) == before


@pytest.mark.parametrize('change', [
    lambda s: setattr(s.sender, 'status', 'failed'),
    lambda s: setattr(s.sender, 'status', 'unknown'),
    lambda s: setattr(s.acq, 'status', 'unknown'),
    lambda s: setattr(s.sender, 'lease_token', 'a-different-historical-lease'),
    lambda s: s.audits.remove(s.send),
    lambda s: s.send.data.update(unsent_proof_ids=[]),
    lambda s: s.audits.pop(0),
    lambda s: s.audits.append(copy.deepcopy(s.audits[0])),
    lambda s: s.row.data['receipt_history'][0]['raw'].update(status='rejected'),
    lambda s: s.row.data['receipt_history'][0]['projection'].update(status='no_data'),
    lambda s: s.origin.update(job_id=s.old.id),
    lambda s: s.acq.data.update(request_hash='b' * 64),
])
def test_current_unknown_or_failed_sender_and_missing_or_contradictory_proofs_keep_old_failure_blocked(monkeypatch, change):
    s = scene(monkeypatch); change(s)
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.old, db=s.db, p=s.p) is True


def test_later_failed_sender_is_never_classified_as_its_own_old_unsent_failure(monkeypatch):
    s = scene(monkeypatch); s.sender.status = 'failed'
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.sender, db=s.db, p=s.p) is True


def test_unlisted_old_target_cannot_borrow_other_jobs_independent_proofs(monkeypatch):
    s = scene(monkeypatch); target = copy.deepcopy(s.old); target.id = str(uuid4())
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, target, db=s.db, p=s.p) is True


def test_later_contradictory_receipt_is_checked_even_with_an_intact_accepted_post(monkeypatch):
    s = scene(monkeypatch)
    later = copy.deepcopy(s.row.data['receipt_history'][0])
    later['receipt_origin']['operation'] = 'original_receipt_get'
    later['raw']['status'] = 'completed_no_data'; later['raw_hash'] = digest(later['raw'])
    later['projection']['status'] = 'no_data'; later['projection_hash'] = digest(later['projection'])
    s.row.data['receipt_history'].append(later)
    assert subject._unresolved_failure(s.task, {s.acq.id: s.binding}, {}, s.old, db=s.db, p=s.p) is True
