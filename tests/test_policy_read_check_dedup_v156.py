"""Display-check boundaries, using real inventory validators and mapped rows.

Only the read-only scalar/scalars IO seam from V155 is synthetic. The real
unbound Session supplies transaction events. These tests do not prove actual
approval, reception, PG execution, or a latency improvement.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.models import Audit, Observation
from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents.threshold_policy_models_v152 import ThresholdPolicyGrant as Grant
from generic_agents.threshold_policy_read_inventory_v155 import (
    PolicyReadInventory, checked_read_inventory, scoped_read_inventory,
)
from tests.test_policy_inventory_integrity_v154 import identity
from tests.test_policy_read_inventory_v155 import STAMP, loaded, read_case, rejected


def cold(db, case):
    read = PolicyReadInventory(db, case.principal)
    db.scalar(select(Grant).where(Grant.id == case.grants[0].id))
    return read


def during_last_observation(monkeypatch, db, case, change):
    """Change actual mapped state at a later IO boundary, not a validator."""
    scalar = db.scalar
    target = case.entries[-1][1]
    fired = []

    def read(statement):
        result = scalar(statement)
        if result is target and not fired:
            assert statement.column_descriptions[0]['entity'] is Observation
            fired.append(True)
            change()
        return result

    monkeypatch.setattr(db, 'scalar', read)
    return fired


def unpublished(read, db, p):
    assert read.failed is True
    assert read._rows is None and read._chains is None and read._bindings == {}
    assert read._staged == {} and read._staged_bindings == {} and read._staged_refs == {}
    rejected(lambda: scoped_read_inventory(read, db, p))
    rejected(lambda: read.selection_data(db, p))


@pytest.mark.parametrize('change', ['bool-version', 'float-kind', 'sql-time-kind'])
def test_default_checked_helper_still_checks_complete_model_pins(read_case, change):
    db, case = read_case
    with loaded(db, case) as read:
        job = case.entries[0][2]
        if change == 'bool-version':
            job.version = True
        elif change == 'float-kind':
            observation = case.entries[0][1]
            observation.data = deepcopy(observation.data)
            observation.data['attributes']['temperature'] = 19
        else:
            job.available_at = STAMP.isoformat()
        # The cheap boundary guard grants no model-integrity assertion.
        assert scoped_read_inventory(read, db, case.principal) is read
        rejected(lambda: checked_read_inventory(read, db, case.principal))


@pytest.mark.parametrize('operation', ['selection-data', 'rows-for', 'binding'])
def test_loaded_model_return_paths_still_require_full_pins(read_case, operation):
    db, case = read_case
    with loaded(db, case) as read:
        row, _, job, *_ = case.entries[0]
        job.status = 'cancelled'
        assert scoped_read_inventory(read, db, case.principal) is read
        if operation == 'selection-data':
            action = lambda: read.selection_data(db, case.principal)
        elif operation == 'rows-for':
            action = lambda: execution.selection_records(db, case.principal,
                case.grants[0], _read_inventory=read)
        else:
            action = lambda: read.binding(db, case.principal, case.grants[0], row)
        rejected(action)


def test_staging_never_authorizes_binding_or_rows_reuse(read_case):
    db, case = read_case
    p, grant = case.principal, case.grants[0]
    with cold(db, case) as read:
        row, obs, job, call, *_ = case.entries[0]
        read.loading = True
        try:
            read.stage_binding(db, p, row, grant, (obs, job, call), case.states)
            assert read._staged_bindings and read._rows is None and read._bindings == {}
            assert scoped_read_inventory(read, db, p) is read
            rejected(lambda: read.binding(db, p, grant, row))
            rejected(lambda: read.rows_for(db, p, grant))
        finally:
            read.loading = False


@pytest.mark.parametrize('target', ['observation', 'job', 'invocation', 'selection-state'])
def test_loading_stage_mutation_poisoned_before_any_publication(read_case, monkeypatch, target):
    db, case = read_case
    # These fields are already staged before the final Observation IO and are
    # not revisited by the remaining original row's binding checks.
    def change():
        if target == 'observation':
            case.entries[0][1].version = True
        elif target == 'job':
            case.entries[0][2].available_at = STAMP + timedelta(seconds=1)
        elif target == 'invocation':
            case.entries[2][3].created_at = STAMP + timedelta(seconds=1)
        else:
            case.entries[0][6][0].version = True

    fired = during_last_observation(monkeypatch, db, case, change)
    with cold(db, case) as read:
        rejected(lambda: read.selection_data(db, case.principal))
        assert fired == [True]
        unpublished(read, db, case.principal)


def test_preexisting_pin_mutated_during_cold_io_also_blocks_publication(read_case, monkeypatch):
    db, case = read_case
    prior = Audit(id=identity(9201), workspace_id=case.principal.workspace_id,
        actor_id=case.principal.user_id, action='structural-display-prior',
        object_id=identity(9202), version=1, created_at=STAMP, data={'display': 1.0})
    with cold(db, case) as read:
        read.pin_rows(db, case.principal, [prior])
        fired = during_last_observation(monkeypatch, db, case,
            lambda: setattr(prior, 'data', {'display': True}))
        rejected(lambda: read.selection_data(db, case.principal))
        assert fired == [True]
        unpublished(read, db, case.principal)


def test_final_global_check_keeps_unrelated_model_pins(read_case):
    db, case = read_case
    with loaded(db, case) as read:
        case.entries[0][2].status = 'cancelled'
        unaffected = case.entries[2]
        assert read.binding(db, case.principal, case.grants[0], unaffected[0]) == unaffected[1:4]
        rejected(lambda: read.check(db, case.principal))


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'role', 'session_id'])
def test_scope_only_guard_still_rejects_every_identity_change(read_case, field):
    db, case = read_case
    with loaded(db, case) as read:
        changed = SimpleNamespace(**vars(case.principal))
        setattr(changed, field, 'different-' + str(getattr(changed, field)))
        rejected(lambda: scoped_read_inventory(read, db, changed))


@pytest.mark.parametrize('boundary', ['session', 'commit', 'closed'])
def test_scope_only_guard_requires_same_live_actual_transaction(read_case, boundary):
    db, case = read_case
    with loaded(db, case) as read:
        if boundary == 'session':
            with Session() as other:
                other.begin()
                rejected(lambda: scoped_read_inventory(read, other, case.principal))
        elif boundary == 'commit':
            db.commit()
            db.begin()
            rejected(lambda: scoped_read_inventory(read, db, case.principal))
        else:
            read.close()
            rejected(lambda: scoped_read_inventory(read, db, case.principal))


@pytest.mark.parametrize('value', [None, True, {}, SimpleNamespace()],
    ids=['none', 'bool', 'dict', 'other-type'])
def test_scope_only_wrapper_does_not_accept_untyped_collectors(read_case, value):
    db, case = read_case
    rejected(lambda: scoped_read_inventory(value, db, case.principal))


def test_commit_during_cold_collection_cannot_publish_into_a_new_transaction(read_case, monkeypatch):
    db, case = read_case

    def change():
        db.commit()
        db.begin()

    fired = during_last_observation(monkeypatch, db, case, change)
    with cold(db, case) as read:
        rejected(lambda: read.selection_data(db, case.principal))
        assert fired == [True]
        unpublished(read, db, case.principal)


def test_default_inventory_remains_cold_tuple_and_loaded_rows_keep_all_states(read_case):
    db, case = read_case
    first = execution._selection_records(db, case.principal)
    second = execution._selection_records(db, case.principal)
    assert type(first) is tuple and type(first[0]) is list and type(first[1]) is dict
    assert first[0] == second[0] == [entry[0] for entry in case.entries]
    with loaded(db, case) as read:
        rows, chains = read.selection_data(db, case.principal)
        assert len(rows) == len(chains) == 5
        assert [row.status for row in rows] == ['queued', 'reserved', 'completed', 'failed', 'stopped']
        bindings = [read.binding(db, case.principal, case.grants[0], row) for row in rows]
        assert [binding[2] is None for binding in bindings] == [True, True, False, False, True]
        assert checked_read_inventory(read, db, case.principal) is read
