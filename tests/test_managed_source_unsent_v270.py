"""Pure no-send proof policy tests; no fake DB, capability or lease authority.

Live helpers must obtain all facts from actual fenced rows. These tests exercise
only their closed historical-proof comparison, never a valid acquisition.
"""
import copy
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import managed_source_unsent_v270 as unsent

FROZEN_AT = '2026-11-01T11:58:00+00:00'


def records():
    actor, acq, original, current = (str(uuid4()) for _ in range(4))
    pin = {'acquisition_id': acq, 'exchange_id': str(uuid4()), 'source_id': str(uuid4()),
        'source_hash': 'a' * 64, 'actor_id': actor, 'original_request_key_hash': 'b' * 64,
        'freeze_hash': 'c' * 64, 'parent_pins_hash': 'd' * 64, 'original_dispatch_job_id': original}
    old = {'id': original, 'key': 'dispatch:' + acq, 'status': 'succeeded', 'lease_until': None,
        'lease_token': 'original-worker-token', 'finished_at': '2026-11-01T12:00:00+00:00',
        'actor_id': actor, 'acquisition_id': acq}
    new = {'id': current, 'key': 'dispatch:' + acq + ':resume:3', 'status': 'running',
        'lease_until': 'current-actual-lease', 'lease_token': 'retry-worker-token',
        'finished_at': None, 'actor_id': actor, 'acquisition_id': acq}
    proof = {'id': str(uuid4()), 'action': unsent.UNSENT, 'actor_id': actor,
        'created_at': '2026-11-01T11:59:00+00:00',
        'data': unsent._proof_data(pin, {'job_id': original, 'job_key': old['key'], 'lease_token': old['lease_token']})}
    return pin, [old, new], [proof], current


def test_complete_terminal_original_and_independent_no_send_proof_match_exactly():
    pin, facts, audits, current = records()
    assert unsent._prior_proofs(pin, current, facts, audits,
        exchange_created_at=FROZEN_AT) == [audits[0]['id']]


@pytest.mark.parametrize('change', ['missing_proof', 'duplicate_proof', 'unknown_job', 'still_running',
    'unfinished', 'lease_retained', 'wrong_actor', 'wrong_acquisition', 'changed_freeze',
    'changed_key', 'changed_scope', 'claimed_sent', 'changed_original_job'])
def test_missing_or_changed_independent_proof_never_permits_first_send(change):
    pin, facts, audits, current = records()
    if change == 'missing_proof':
        audits.clear()
    elif change == 'duplicate_proof':
        duplicate = copy.deepcopy(audits[0])
        duplicate['id'] = str(uuid4())
        audits.append(duplicate)
    elif change == 'unknown_job':
        unknown = copy.deepcopy(facts[0])
        unknown.update(id=str(uuid4()), key='dispatch:' + pin['acquisition_id'] + ':resume:2')
        facts.append(unknown)
    elif change == 'still_running':
        facts[0]['status'] = 'running'
    elif change == 'unfinished':
        facts[0]['finished_at'] = None
    elif change == 'lease_retained':
        facts[0]['lease_until'] = 'unexpired'
    elif change == 'wrong_actor':
        facts[0]['actor_id'] = str(uuid4())
    elif change == 'wrong_acquisition':
        facts[0]['acquisition_id'] = str(uuid4())
    elif change == 'changed_freeze':
        pin['freeze_hash'] = 'e' * 64
    elif change == 'changed_key':
        pin['original_request_key_hash'] = 'e' * 64
    elif change == 'changed_scope':
        pin['parent_pins_hash'] = 'e' * 64
    elif change == 'claimed_sent':
        audits[0]['data']['post_sent'] = True
    else:
        pin['original_dispatch_job_id'] = str(uuid4())
    with pytest.raises(HTTPException):
        unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)


def test_extra_malformed_no_send_record_is_damage_not_something_to_ignore():
    pin, facts, audits, current = records()
    audits.append({'id': str(uuid4()), 'action': unsent.UNSENT,
        'actor_id': pin['actor_id'], 'data': {'post_sent': False}})
    with pytest.raises(HTTPException):
        unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)


def test_current_job_already_recorded_unsent_cannot_continue_to_send():
    pin, facts, audits, current = records()
    audits.append({'id': str(uuid4()), 'action': unsent.UNSENT, 'actor_id': pin['actor_id'],
        'data': unsent._proof_data(pin, {'job_id': current, 'job_key': facts[1]['key'],
            'lease_token': facts[1]['lease_token']})})
    with pytest.raises(HTTPException):
        unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)


def test_retry_send_intent_binds_actual_retry_job_and_every_prior_proof():
    pin, facts, audits, current = records()
    proof_ids = unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)
    job = {'job_id': current, 'job_key': facts[1]['key'], 'lease_token': facts[1]['lease_token']}
    sent = unsent._sent_data(pin, job, proof_ids)
    assert sent['schema'] == unsent.SENT_SCHEMA and sent['post_sent'] is True
    assert sent['job_id'] == current and sent['original_dispatch_job_id'] == facts[0]['id']
    assert sent['unsent_proof_ids'] == proof_ids
    assert sent['freeze_hash'] == pin['freeze_hash']
    pin['freeze_hash'] = 'f' * 64
    assert sent['freeze_hash'] != pin['freeze_hash']


@pytest.mark.parametrize('proof_ids', [[], ['b', 'a'], ['a', 'a']])
def test_send_intent_cannot_omit_duplicate_or_reorder_prior_proof_ids(proof_ids):
    pin, facts, _, current = records()
    with pytest.raises(HTTPException):
        unsent._sent_data(pin, {'job_id': current, 'job_key': facts[1]['key'],
            'lease_token': facts[1]['lease_token']}, proof_ids)


def pre_freeze_records():
    """Early failure -> retry creates freeze -> its decrypt fails -> next retry."""
    pin, facts, _, second = records()
    early, freeze_job = facts
    pin['original_dispatch_job_id'] = second
    early['finished_at'] = '2026-11-01T11:57:00+00:00'
    early_proof = {'id': str(uuid4()), 'action': unsent.PRE_UNSENT, 'actor_id': pin['actor_id'],
        'created_at': '2026-11-01T11:56:00+00:00',
        'data': unsent._pre_data({key: pin[key] for key in unsent.PRE_KEYS},
            {'job_id': early['id'], 'job_key': early['key'], 'lease_token': early['lease_token']})}
    assert unsent._prior_proofs(pin, freeze_job['id'], facts, [early_proof],
        exchange_created_at=FROZEN_AT, allow_empty=True) == [early_proof['id']]
    freeze_job.update(status='failed', lease_until=None, finished_at='2026-11-01T12:00:00+00:00')
    freeze_proof = {'id': str(uuid4()), 'action': unsent.UNSENT, 'actor_id': pin['actor_id'],
        'created_at': '2026-11-01T11:59:00+00:00',
        'data': unsent._proof_data(pin, {'job_id': freeze_job['id'], 'job_key': freeze_job['key'],
            'lease_token': freeze_job['lease_token']})}
    current = str(uuid4())
    facts.append({'id': current, 'key': 'dispatch:' + pin['acquisition_id'] + ':resume:4',
        'status': 'running', 'lease_until': 'current-actual-lease', 'lease_token': 'third-worker-token',
        'finished_at': None, 'actor_id': pin['actor_id'], 'acquisition_id': pin['acquisition_id']})
    return pin, facts, [early_proof, freeze_proof], current


def test_early_failure_then_frozen_decrypt_failure_can_recover_only_the_first_send():
    pin, facts, audits, current = pre_freeze_records()
    unchanged = copy.deepcopy(pin)
    proofs = unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)
    assert proofs == sorted(item['id'] for item in audits)
    sent = unsent._sent_data(pin, {'job_id': current, 'job_key': facts[-1]['key'],
        'lease_token': facts[-1]['lease_token']}, proofs)
    assert pin == unchanged
    assert sent['freeze_hash'] == unchanged['freeze_hash']
    assert sent['original_request_key_hash'] == unchanged['original_request_key_hash']
    assert sent['original_dispatch_job_id'] == facts[1]['id']


@pytest.mark.parametrize('damage', ['missing', 'late_audit', 'late_finish', 'after_finish',
    'wrong_source', 'wrong_key', 'changed_token', 'claimed_sent', 'extra_field', 'malformed_extra',
    'frozen_proof_for_early_job'])
def test_pre_freeze_evidence_cannot_be_missing_late_or_rewritten_after_freeze(damage):
    pin, facts, audits, current = pre_freeze_records()
    if damage == 'missing':
        audits.pop(0)
    elif damage == 'late_audit':
        audits[0]['created_at'] = '2026-11-01T12:01:00+00:00'
    elif damage == 'late_finish':
        facts[0]['finished_at'] = '2026-11-01T12:00:00+00:00'
    elif damage == 'after_finish':
        audits[0]['created_at'] = '2026-11-01T11:57:30+00:00'
    elif damage == 'wrong_source':
        audits[0]['data']['source_hash'] = 'f' * 64
    elif damage == 'wrong_key':
        audits[0]['data']['original_request_key_hash'] = 'f' * 64
    elif damage == 'changed_token':
        facts[0]['lease_token'] = 'not-the-original-worker'
    elif damage == 'claimed_sent':
        audits[0]['data']['post_sent'] = True
    elif damage == 'extra_field':
        audits[0]['data']['freeze_hash'] = pin['freeze_hash']
    elif damage == 'malformed_extra':
        audits.append({'id': str(uuid4()), 'action': unsent.PRE_UNSENT, 'actor_id': pin['actor_id'],
            'created_at': '2026-11-01T11:56:00+00:00', 'data': {'post_sent': False}})
    else:
        audits[0]['action'] = unsent.UNSENT
        audits[0]['data'] = unsent._proof_data(pin, {'job_id': facts[0]['id'],
            'job_key': facts[0]['key'], 'lease_token': facts[0]['lease_token']})
    with pytest.raises(HTTPException):
        unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)


def test_a_late_job_cannot_claim_the_pre_freeze_schema():
    pin, facts, audits, current = records()
    audits[0]['action'] = unsent.PRE_UNSENT
    audits[0]['data'] = unsent._pre_data({key: pin[key] for key in unsent.PRE_KEYS},
        {'job_id': facts[0]['id'], 'job_key': facts[0]['key'], 'lease_token': facts[0]['lease_token']})
    with pytest.raises(HTTPException):
        unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)


def test_a_genuinely_first_freeze_has_no_invented_prior_job_or_proof():
    pin, facts, _, _ = records()
    original = facts[0]
    original.update(status='running', lease_until='actual-current-lease', finished_at=None)
    assert unsent._prior_proofs(pin, original['id'], [original], [],
        exchange_created_at=FROZEN_AT, allow_empty=True) == []


def test_different_historical_reader_does_not_become_the_origin_actor_or_gain_send_authority():
    owner, reader = str(uuid4()), str(uuid4())
    assert unsent._scope_actor(owner, owner, reader, live_actor=False) == owner
    assert unsent._scope_actor(owner, owner, owner, live_actor=True) == owner
    with pytest.raises(HTTPException):
        unsent._scope_actor(owner, owner, reader, live_actor=True)
    with pytest.raises(HTTPException):
        unsent._scope_actor(owner, reader, reader, live_actor=False)


def test_historical_scope_still_checks_the_actual_current_reader_iam(monkeypatch):
    # Only the denied IAM boundary is stubbed; this never constructs a valid
    # Source/Acq/database authorization or claims live worker authority.
    owner, reader, acq_id, source_id = (str(uuid4()) for _ in range(4))
    p = SimpleNamespace(user_id=reader)
    acq = SimpleNamespace(id=acq_id, capability_id=source_id, data={'actor_id': owner})
    row = SimpleNamespace(acquisition_id=acq_id, source_id=source_id, actor_id=owner)
    seen = []
    monkeypatch.setattr(unsent, 'scoped', lambda *args: SimpleNamespace(id=source_id))
    monkeypatch.setattr(unsent.sources, 'managed', lambda *args: True)
    def denied(db, principal, source, purpose):
        seen.append((principal, purpose))
        raise HTTPException(403, 'current reader denied')
    monkeypatch.setattr(unsent.sources, 'validate_source', denied)
    with pytest.raises(HTTPException) as caught:
        unsent._scope_pin(None, p, acq, row, live_actor=False)
    assert caught.value.status_code == 403 and seen == [(p, 'receipt')]
    seen.clear()
    with pytest.raises(HTTPException):
        unsent._scope_pin(None, p, acq, row)
    assert seen == []


def test_retry_receipt_origin_keeps_owner_audits_when_reader_is_another_member(monkeypatch):
    # Loaded-row boundaries are replaced here to exercise only the historical
    # origin comparison. Real membership/SQL/fence evidence belongs to PG tests.
    pin, facts, audits, current = pre_freeze_records()
    workspace = str(uuid4()); reader = str(uuid4()); key = 'original-request-001'
    owner = pin['actor_id']; request_hash = 'e' * 64
    acq = SimpleNamespace(id=pin['acquisition_id'], idempotency_key=key, data={'actor_id': owner})
    row = unsent.Exchange(id=pin['exchange_id'], workspace_id=workspace,
        actor_id=owner, acquisition_id=acq.id, created_at=datetime.fromisoformat(FROZEN_AT),
        data={'freeze': {'wire_frozen': {'request_hash': request_hash}}})
    jobs = [SimpleNamespace(id=fact['id'], idempotency_key=fact['key'], status=fact['status'],
        kind='dispatch_acquisition', workspace_id=workspace, lease_until=fact['lease_until'],
        lease_token=fact['lease_token'], data={'actor_id': fact['actor_id'],
            'acquisition_id': fact['acquisition_id'], 'finished_at': fact['finished_at']}) for fact in facts]
    proofs = unsent._prior_proofs(pin, current, facts, audits, exchange_created_at=FROZEN_AT)
    audit_rows = [SimpleNamespace(**item) for item in audits]
    audit_rows.append(SimpleNamespace(id=str(uuid4()), action=unsent.DISPATCH,
        actor_id=owner, created_at='2026-11-01T12:02:00+00:00',
        data=unsent._sent_data(pin, {'job_id': current, 'job_key': facts[-1]['key'],
            'lease_token': facts[-1]['lease_token']}, proofs)))
    monkeypatch.setattr(unsent, '_cold_scope', lambda *args: (acq, row, pin))
    monkeypatch.setattr(unsent, 'scoped', lambda *args: jobs[-1])
    monkeypatch.setattr(unsent, '_rows', lambda *args: (jobs, audit_rows))
    p = SimpleNamespace(user_id=reader, workspace_id=workspace)
    origin = {'operation': 'dispatch_post', 'read_enqueue_audit_id': None, 'actor_id': owner,
        'exchange_id': row.id, 'original_request_key': key, 'wire_request_hash': request_hash,
        'job_id': current, 'lease_token': facts[-1]['lease_token']}
    assert unsent.verify_retry_receipt_origin(None, p, row, origin) is True
    assert unsent.verify_retry_receipt_origin(None, p, row, {**origin, 'actor_id': reader}) is False
