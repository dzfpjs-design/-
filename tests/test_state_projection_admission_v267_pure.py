"""Pure saved-cache/display contracts, not source or execution authority."""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import state_projection_admission_v267 as subject
from generic_agents.contracts import digest

W = '1a0bc001-a462-42da-b822-a89e541bdc8d'
E = '3a0bc001-a462-42da-b822-a89e541bdc8d'


def cache_row():
    value = {'entity_id': E, 'fields': {}, 'gaps': [], 'conflicts': [],
        'status': 'insufficient', 'physical_state': 'unknown', 'current_state_verified': False,
        'unclocked_candidates': [], 'rule_version': 'reported-state-v6'}
    return SimpleNamespace(version=2, data={**value, 'state_hash': digest(value),
        'evaluated_at': '2026-11-01T00:00:00+00:00',
        'history': [{'unchecked_historical_value': 'must-not-be-returned'}], 'history_truncated': False})


def read(row, jobs=()):
    db = SimpleNamespace(scalar=lambda _statement: row)
    return subject._cache(db, SimpleNamespace(workspace_id=W), SimpleNamespace(id=E), jobs)


def test_missing_cache_does_not_compute_or_fabricate_a_state(monkeypatch):
    from generic_agents import perception
    monkeypatch.setattr(perception, 'object_state', lambda *a, **k: pytest.fail('read recomputed state'))
    stored, cache = read(None)
    assert stored is None and cache['status'] == 'missing' and cache['background_result_matched'] is False


def test_saved_cache_returns_hash_payload_without_unauthenticated_history_or_mutation():
    row = cache_row(); original = copy.deepcopy(row.data)
    stored, cache = read(row)
    assert stored['version'] == 2 and stored['state_hash'] == digest(stored['value'])
    assert 'history' not in stored['value'] and 'history_truncated' not in stored['value']
    assert 'evaluated_at' not in stored['value'] and 'state_hash' not in stored['value']
    assert 'must-not-be-returned' not in str(stored)
    stored['value']['fields']['local-view-only'] = 1
    assert row.data == original and cache['status'] == 'saved'


@pytest.mark.parametrize('fault', ['hash', 'entity', 'unknown_key', 'nan', 'physical_true', 'bad_time'])
def test_bad_cache_is_blocked_without_instant_repair_or_values(fault):
    row = cache_row()
    if fault == 'hash': row.data['state_hash'] = '0' * 64
    elif fault == 'entity': row.data['entity_id'] = W
    elif fault == 'unknown_key': row.data['unsafely_injected'] = True
    elif fault == 'nan': row.data['fields'] = {'a': {'value': float('nan')}}
    elif fault == 'physical_true': row.data['current_state_verified'] = True
    else: row.data['evaluated_at'] = '2026-11-01'
    if fault != 'hash':
        row.data['state_hash'] = digest({key: value for key, value in row.data.items()
            if key not in {'state_hash', 'evaluated_at', 'history', 'history_truncated'}})
    original_hash = digest(row.data)
    stored, cache = read(row)
    assert stored is None and cache['status'] == 'blocked_integrity'
    assert cache['background_result_matched'] is False and digest(row.data) == original_hash


@pytest.mark.parametrize('status,version,expected', [('succeeded', 2, True), ('failed', 2, False),
    ('running', 2, False), ('succeeded', 1, False)])
def test_matching_is_saved_version_hash_only_and_never_live_worker_claim(status, version, expected):
    row = cache_row()
    job = {'status': status, 'result': {'state_hash': row.data['state_hash'], 'state_version': version},
        'admission': {'state': 'historical'}}
    _, cache = read(row, [job])
    assert cache['background_result_matched'] is expected


@pytest.mark.parametrize('mode', ['dirty', 'no_transaction', 'read_write'])
def test_non_snapshot_caller_cannot_be_silently_upgraded_or_borrow_another_reader(monkeypatch, mode):
    calls = []
    bind = SimpleNamespace(in_transaction=lambda: mode != 'no_transaction')
    def scalar(statement):
        calls.append(str(statement))
        return 'repeatable read' if str(statement) == 'SHOW transaction_isolation' else 'off'
    db = SimpleNamespace(new=set(), dirty={1} if mode == 'dirty' else set(), deleted=set(),
        get_bind=lambda: bind, in_transaction=lambda: mode != 'no_transaction', scalar=scalar)
    monkeypatch.setattr(subject.consumer, '_reader', lambda: pytest.fail('display borrowed another connection'))
    monkeypatch.setattr(subject, 'current_actor', lambda *a: pytest.fail('invalid snapshot reached scope lookup'))
    with pytest.raises(HTTPException):
        subject._context(db, SimpleNamespace(workspace_id=W), W, E)
    assert all(query.startswith('SHOW transaction_') for query in calls)
