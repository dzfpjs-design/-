"""Actual cold dispatch proof cross-links and the pre-terminal Job crash gap.

Every positive record is issued by the real API/Worker/budget path. Re-sealing
below is deliberate rejecting corruption, never a source of successful rights.
The crash case interrupts only the final Job flush, then advances only that
original Job's lease deadline; no status/token/business result is invented.
"""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job, Memory, Task, now
from generic_agents.services import as_dict
from generic_agents.worker import process_once
from tests import test_native_method_suggestions as old
from tests.test_tool_accepted_experience_v129 import _records
from tests.test_native_suggestion_not_sent_v130 import (
    run_not_sent_retry_journey, _capacity, _start, _rows, _clouds, _new_clouds,
    _inventory, _origin, _not_sent,
    test_actual_reservation_then_public_policy_withdrawal_is_confirmed_not_sent as _issue_confirmed,
)


def _sealed(data):
    value = copy.deepcopy(data)
    value['record_hash'] = digest({key: item for key, item in value.items() if key != 'record_hash'})
    return value


def _proof_sealed(proof):
    value = copy.deepcopy(proof)
    value['hash'] = digest({key: item for key, item in value.items() if key != 'hash'})
    return value


def _denied_read(client, task_id, suggestion_id, *, statuses=(409,)):
    before = _inventory(); original = _origin(task_id)
    with Session(engine()) as db: task_before = copy.deepcopy(as_dict(db.get(Task, task_id)))
    response = client.get('/api/workspace/tasks/' + task_id + '/experience/suggestions/' + suggestion_id)
    assert response.status_code in statuses, response.text
    assert _inventory() == before and _origin(task_id) == original
    with Session(engine()) as db: assert as_dict(db.get(Task, task_id)) == task_before
    return response


def _confirmed(client, monkeypatch):
    # Reuse the already verified real reserve-commit -> public policy-disable
    # timing fixture; it returns no fabricated proof or database authority.
    _issue_confirmed(client, monkeypatch)
    with Session(engine()) as db:
        rows = list(db.scalars(select(Memory).where(
            Memory.workspace_id == client.headers['X-Workspace-ID'],
            Memory.data['dispatch_not_sent']['reservation_state'].as_string() == 'confirmed')))
        assert len(rows) == 1
        row = rows[0]
        data = copy.deepcopy(row.data)
        call = db.get(CloudCall, data['cloud_call_id'])
        assert call.status == 'failed' and not call.data.get('receipt')
        assert call.data['gateway_dispatch']['attempt_id'] == data['dispatch_not_sent']['attempt_id']
        return row.id, row.task_id, data, copy.deepcopy(call.data), call.id


def test_denied_parent_cannot_borrow_the_successful_child_call_or_its_actual_attempt(client, monkeypatch):
    proof = run_not_sent_retry_journey(client, monkeypatch)
    with Session(engine()) as db:
        parent = db.get(Memory, proof['parent_id']); data = copy.deepcopy(parent.data)
        call = db.get(CloudCall, proof['cloud_call_id']); ledger = copy.deepcopy(call.data)
        assert call.status == 'succeeded' and ledger['native_suggestion']['suggestion_id'] == proof['child_id']
    assert data['dispatch_not_sent']['reservation_state'] == 'denied'
    assert data.get('cloud_call_id') is None
    assert ledger['request_hash'] == data['request']['dispatch_messages_hash']
    changed = copy.deepcopy(data['dispatch_not_sent'])
    changed.update(stage='authorization', reservation_state='confirmed', call_id=proof['cloud_call_id'],
        reservation_request_hash=ledger['request_hash'], attempt_id=ledger['gateway_dispatch']['attempt_id'])
    # Even the real child nonce and same wire bytes cannot replace the actual
    # parent identity frozen by the real reservation in another record.
    assert changed['identity']['suggestion_id'] == proof['parent_id']
    assert ledger['gateway_dispatch']['identity']['suggestion_id'] == proof['child_id']
    data['dispatch_not_sent'] = _proof_sealed(changed)
    with _records([(Memory, proof['parent_id'], {'data': _sealed(data)})]):
        rejected = _denied_read(client, proof['task_id'], proof['parent_id'])
        assert '实际预留的网关尝试' in rejected.json()['detail']
    restored = client.get('/api/workspace/tasks/' + proof['task_id'] + '/experience/suggestions/' + proof['parent_id'])
    assert restored.status_code == 200 and restored.json()['call']['dispatch']['reservation_state'] == 'denied'


def test_confirmed_proof_wrong_original_identity_nonce_call_or_wire_is_cold_rejected(client, monkeypatch):
    suggestion_id, task_id, original, _, _ = _confirmed(client, monkeypatch)
    cases = []
    for key in ('suggestion_id', 'job_id', 'task_id'):
        changed = copy.deepcopy(original['dispatch_not_sent']); changed['identity'][key] = str(uuid4())
        cases.append((key, changed, (409,)))
    changed = copy.deepcopy(original['dispatch_not_sent']); changed['identity']['request_hash'] = '0' * 64
    cases.append(('request_hash', changed, (409,)))
    changed = copy.deepcopy(original['dispatch_not_sent']); changed['attempt_id'] = str(uuid4())
    cases.append(('attempt_nonce', changed, (409,)))
    changed = copy.deepcopy(original['dispatch_not_sent']); changed['reservation_request_hash'] = '0' * 64
    cases.append(('wire_hash', changed, (409,)))
    changed = copy.deepcopy(original['dispatch_not_sent']); changed['call_id'] = str(uuid4())
    cases.append(('call_id', changed, (409,)))
    for label, damaged, statuses in cases:
        data = {**original, 'dispatch_not_sent': _proof_sealed(damaged)}
        assert data['dispatch_not_sent']['hash'] == digest({key: value for key, value in data['dispatch_not_sent'].items() if key != 'hash'}), label
        with _records([(Memory, suggestion_id, {'data': _sealed(data)})]):
            _denied_read(client, task_id, suggestion_id, statuses=statuses)


def test_actual_ledger_attempt_and_suggestion_link_cannot_be_replaced_by_self_consistent_json(client, monkeypatch):
    suggestion_id, task_id, _, original, call_id = _confirmed(client, monkeypatch)
    cases = []
    nonce = copy.deepcopy(original); nonce['gateway_dispatch']['attempt_id'] = str(uuid4())
    cases.append({'data': nonce})
    identity = copy.deepcopy(original); identity['gateway_dispatch']['identity']['job_id'] = str(uuid4())
    cases.append({'data': identity})
    wire = copy.deepcopy(original); wire['gateway_dispatch']['reservation_request_hash'] = '0' * 64
    cases.append({'data': wire})
    linked = copy.deepcopy(original); linked['native_suggestion']['suggestion_id'] = str(uuid4())
    cases.append({'data': linked})
    cases.append({'status': 'succeeded'})
    received = copy.deepcopy(original); received['receipt'] = {'call_id': call_id, 'usage': {'total_tokens': 91}}
    cases.append({'data': received})
    for values in cases:
        with _records([(CloudCall, call_id, values)]):
            _denied_read(client, task_id, suggestion_id)


def test_original_not_sent_commit_survives_crash_before_job_terminal_without_automatic_send(client, monkeypatch):
    _, task, _, row, _, original, sent, initial_cloud_ids = _start(client, monkeypatch)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 0)
    _, job = _rows(row['id']); target_job_id = job['id']

    class SimulatedCrash(BaseException):
        pass

    crash = {'fired': False}

    def interrupt_only_terminal_job_flush(session, context, instances):
        if crash['fired']: return
        for item in session.dirty:
            if isinstance(item, Job) and item.id == target_job_id and item.status == 'failed' and item.data.get('finished_at'):
                crash['fired'] = True
                raise SimulatedCrash('after target commit, before actual Job terminal commit')

    event.listen(Session, 'before_flush', interrupt_only_terminal_job_flush)
    try:
        with pytest.raises(SimulatedCrash):
            process_once(target_job_id, deliver=False)
    finally:
        event.remove(Session, 'before_flush', interrupt_only_terminal_job_flush)
    assert crash['fired'] and not sent and not _new_clouds(initial_cloud_ids)
    stored, running = _rows(row['id'])
    assert stored['status'] == 'failed' and stored['data']['inflight'] is False
    assert stored['data']['dispatch_not_sent']['reservation_state'] == 'denied'
    assert running['status'] == 'running' and not running['data'].get('finished_at')
    with Session(engine()) as db:
        actual = db.get(Job, target_job_id)
        # The only scheduling simulation: time has elapsed for this lease.
        # Actual running status, original token, body and attempts are retained.
        actual.lease_until = now() - timedelta(seconds=1)
        db.commit()
    _capacity(monkeypatch)
    assert process_once(target_job_id, deliver=False)
    resumed, finished = _rows(row['id'])
    assert finished['status'] == 'failed' and finished['attempts'] == running['attempts'] + 1
    assert resumed == stored
    assert not sent and not _new_clouds(initial_cloud_ids) and _origin(task['id']) == original
    _not_sent(old.get(client, task, row), 'denied')
