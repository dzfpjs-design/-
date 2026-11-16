"""Read-assembly pins using real mapped rows and connection-free Session scopes.

The Session transactions below never execute SQL or attach the transient rows.
This checks the projection boundary, not PG isolation or full policy admission.
The quota test separately isolates the already-established cold inventory seam.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents import threshold_policy_store_v153 as store
from generic_agents.threshold_policy_models_v152 import ThresholdPolicyGrant, ThresholdPolicySelection
from generic_agents.threshold_policy_read_projection_v153 import PolicyReadProjection


def grant_row(identity='10000000-0000-4000-8000-000000000001'):
    row = ThresholdPolicyGrant(
        id=identity, workspace_id='10000000-0000-4000-8000-000000000002',
        actor_id='10000000-0000-4000-8000-000000000003',
        review_id='10000000-0000-4000-8000-000000000004', scope_key='a' * 64,
        status='active', version=1,
        data={'epoch': 1, 'scope': {'field': 'temperature',
              'baseline_threshold': 20.0, 'candidate_threshold': 18.0}},
    )
    row.record_hash = store.hash_value(store.snapshot(row))
    return row


def selection_row(grant, index=1, *, choice='candidate', status='queued'):
    row = ThresholdPolicySelection(
        id=f'20000000-0000-4000-8000-{index:012d}', workspace_id=grant.workspace_id,
        policy_id=grant.id, actor_id=grant.actor_id,
        source_id='10000000-0000-4000-8000-000000000005',
        source_record_id=f'original-source-{index}',
        observation_id=f'30000000-0000-4000-8000-{index:012d}',
        job_id=f'40000000-0000-4000-8000-{index:012d}',
        choice=choice, status=status, version=1,
        data={'original_request': {'attributes': {'temperature': 1.0}},
              'late': False, 'invocation': None},
    )
    row.record_hash = store.hash_value(execution.snapshot(row))
    return row


@pytest.fixture
def projection_case():
    db = Session()
    db.begin()
    grant = grant_row()
    principal = SimpleNamespace(user_id=grant.actor_id, workspace_id=grant.workspace_id,
                                role='admin', session_id='original-auth-session')
    rows = [selection_row(grant)]
    try:
        yield db, principal, grant, rows
    finally:
        db.close()


def assert_409(action):
    with pytest.raises(HTTPException) as error:
        action()
    assert error.value.status_code == 409


def test_same_transaction_and_identity_can_finish_and_read_without_mutating_rows(projection_case):
    db, p, grant, rows = projection_case
    original = (deepcopy(store.snapshot(grant)), deepcopy(execution.snapshot(rows[0])))
    context = PolicyReadProjection(db, p, grant, rows)
    assert context.check(db, p, grant) is context
    assert context.finish(db, p, grant, True, True) is context
    assert context.readable(db, p, grant, 'candidate', rows[0]) is True
    assert context.readable(db, p, grant, 'baseline') is True
    assert context.rows == tuple(rows)
    assert (store.snapshot(grant), execution.snapshot(rows[0])) == original


def test_unfinished_or_twice_finished_projection_cannot_supply_readiness(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows)
    assert_409(lambda: context.readable(db, p, grant, 'candidate', rows[0]))
    context.finish(db, p, grant, True, True)
    assert_409(lambda: context.finish(db, p, grant, True, True))


@pytest.mark.parametrize('candidate,baseline', [(1, True), (True, 0), ('true', True), (True, None)])
def test_finish_requires_actual_boolean_results(projection_case, candidate, baseline):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows)
    assert_409(lambda: context.finish(db, p, grant, candidate, baseline))


def test_unavailable_candidate_cannot_borrow_available_baseline(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, False, True)
    assert context.readable(db, p, grant, 'candidate', rows[0]) is False
    assert context.readable(db, p, grant, 'baseline') is True
    assert_409(lambda: context.readable(db, p, grant, 'other'))


def test_commit_ends_read_context_even_if_a_new_transaction_begins(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    original_transaction = db.get_transaction()
    db.commit()
    assert original_transaction.is_active is False
    assert_409(lambda: context.check(db, p, grant))
    db.begin()
    assert db.get_transaction() is not original_transaction
    assert_409(lambda: context.readable(db, p, grant, 'candidate', rows[0]))


def test_rollback_and_replacement_transaction_cannot_reuse_read_context(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows)
    db.rollback()
    db.begin()
    assert_409(lambda: context.finish(db, p, grant, True, True))


def test_other_session_cannot_use_identical_principal_and_rows(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    with Session() as other:
        other.begin()
        assert_409(lambda: context.readable(other, p, grant, 'candidate', rows[0]))


def test_projection_requires_a_live_transaction(projection_case):
    db, p, grant, rows = projection_case
    db.rollback()
    assert db.get_transaction() is None
    assert_409(lambda: PolicyReadProjection(db, p, grant, rows))


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'role', 'session_id'])
def test_identity_changes_cannot_reuse_readiness(projection_case, field):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    changed = SimpleNamespace(**vars(p))
    setattr(changed, field, 'other-' + getattr(changed, field))
    assert_409(lambda: context.readable(db, changed, grant, 'candidate', rows[0]))


@pytest.mark.parametrize('changed', ['version-bool', 'epoch-bool', 'float-kind', 'status', 'hash'])
def test_grant_typed_snapshot_changes_reject_even_with_a_recomputed_hash(projection_case, changed):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    if changed == 'version-bool':
        grant.version = True
    elif changed == 'epoch-bool':
        grant.data = deepcopy(grant.data)
        grant.data['epoch'] = True
    elif changed == 'float-kind':
        grant.data = deepcopy(grant.data)
        grant.data['scope']['baseline_threshold'] = 20
    elif changed == 'status':
        grant.status = 'paused'
    if changed == 'hash':
        grant.record_hash = 'b' * 64
    else:
        grant.record_hash = store.hash_value(store.snapshot(grant))
    assert_409(lambda: context.readable(db, p, grant, 'candidate', rows[0]))


def test_same_snapshot_in_a_different_grant_instance_is_not_the_original_owner(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows)
    equivalent = ThresholdPolicyGrant(**store.snapshot(grant), record_hash=grant.record_hash)
    assert_409(lambda: context.check(db, p, equivalent))


@pytest.mark.parametrize('changed', ['version-bool', 'float-kind', 'status', 'hash'])
def test_selection_typed_snapshot_changes_reject_on_row_and_final_checks(projection_case, changed):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    row = rows[0]
    if changed == 'version-bool':
        row.version = True
    elif changed == 'float-kind':
        row.data = deepcopy(row.data)
        row.data['original_request']['attributes']['temperature'] = 1
    elif changed == 'status':
        row.status = 'reserved'
    if changed == 'hash':
        row.record_hash = 'b' * 64
    else:
        row.record_hash = store.hash_value(execution.snapshot(row))
    assert_409(lambda: context.readable(db, p, grant, 'candidate', row))
    assert_409(lambda: context.check(db, p, grant))


def test_unowned_selection_and_duplicate_pins_are_rejected(projection_case):
    db, p, grant, rows = projection_case
    context = PolicyReadProjection(db, p, grant, rows).finish(db, p, grant, True, True)
    extra = selection_row(grant, 2)
    assert_409(lambda: context.readable(db, p, grant, 'candidate', extra))
    assert_409(lambda: PolicyReadProjection(db, p, grant, [rows[0], rows[0]]))


def test_original_quota_counts_every_candidate_state_without_dto_or_current_quality(monkeypatch, projection_case):
    db, p, grant, _ = projection_case
    statuses = ('queued', 'reserved', 'completed', 'failed', 'stopped')
    candidates = [selection_row(grant, index, status=status)
                  for index, status in enumerate(statuses, 1)]
    baselines = [selection_row(grant, index + 10, choice='baseline', status=status)
                 for index, status in enumerate(statuses, 1)]
    other_grant = grant_row('10000000-0000-4000-8000-000000000099')
    unrelated = selection_row(other_grant, 99)
    calls = []

    def checked_inventory(actual_db, principal):
        assert actual_db is db and principal is p
        calls.append('cold-inventory')
        return candidates + baselines + [unrelated], {}

    def forbidden_projection(*args, **kwargs):
        pytest.fail('quota must not assemble UI values, receipts or current-quality readiness')

    monkeypatch.setattr(execution, '_selection_records', checked_inventory)
    monkeypatch.setattr(execution, 'selection_view', forbidden_projection)
    monkeypatch.setattr(store, 'fresh_candidate', forbidden_projection)
    monkeypatch.setattr(store, 'fresh_baseline', forbidden_projection)
    values, occupied = store.selection_usage(db, p, grant)
    assert values == candidates + baselines
    assert occupied == len(statuses)
    assert calls == ['cold-inventory']
