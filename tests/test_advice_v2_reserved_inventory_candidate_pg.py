"""Real PG inventory and reservation; Goal/source frame use explicit test substitutes."""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall
from generic_agents import same_goal_advice_common_inventory_v2 as inventory
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from tests.test_advice_v2_cloud_reservation_candidate_pg import started, _reserve
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_original_reservation_and_intent_have_scoped_inventory_only(started):
    s, created, owned = started
    p, task, plan = s['principal'], s['task_id'], created['plan_id']
    with Session(engine()) as db:
        reserved = _reserve(db, started)
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): inventory._read(db, p, task)
        db.rollback()
    with Session(engine()) as db:
        state = inventory._read(db, p, task, reserved_plan_test_only=plan)
        assert state['queued_v2'] == 1
        db.rollback()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): inventory._read(db, p, task, reserved_plan_test_only='wrong')
        db.rollback()
    with Session(engine()) as db:
        intents.issue(db, p, task, plan, owned['job_id'], owned['lease_token'], reserved['cloud_call_id'])
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): inventory._read(db, p, task, reserved_plan_test_only=plan)
        db.rollback()
    with Session(engine()) as db:
        state = inventory._read(db, p, task, reserved_plan_test_only=plan, intent_test_only=True)
        assert state['queued_v2'] == 1
        db.rollback()
    with Session(engine()) as db:
        row = db.get(CloudCall, reserved['cloud_call_id'])
        row.reserved_tokens += 1
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            inventory._read(db, p, task, reserved_plan_test_only=plan, intent_test_only=True)
        db.rollback()


def test_capacity_rechecks_same_reserved_proposal_using_real_inventory(started, monkeypatch):
    from generic_agents.models import Task
    from generic_agents import goal_budget_versioned_snapshot_candidate_v3 as capacity
    s, created, _ = started
    p, task, plan = s['principal'], s['task_id'], created['plan_id']
    monkeypatch.setattr(capacity, "_counts", lambda _db, _p, _task, v1: (3 + v1, 2 + v1))
    with Session(engine()) as db:
        before = capacity.read_locked(db, p, db.get(Task, task))
        db.rollback()
    with Session(engine()) as db:
        _reserve(db, started)
        db.commit()
    with Session(engine()) as db:
        after = capacity.read_locked(db, p, db.get(Task, task), reserved_plan_test_only=plan)
        # This compares other usage against the same prospective slot; it is
        # not authority to create a second proposal.
        assert after["queued_v2"] == 1
        assert (after['calls'], after['steps']) == (before['calls'], before['steps'])
        db.rollback()



@pytest.mark.parametrize('orphan', ['audit', 'cloud'])
def test_scoped_inventory_rejects_extra_task_bound_originals(started, orphan):
    from copy import deepcopy
    from generic_agents.models import Audit, uid
    s, created, _ = started
    p, task, plan = s['principal'], s['task_id'], created['plan_id']
    with Session(engine()) as db:
        reserved = _reserve(db, started)
        db.commit()
    with Session(engine()) as db:
        if orphan == 'audit':
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action='same_goal_advice.unlimited_unknown', object_id=task, data={'task_id': task}))
        else:
            row = db.get(CloudCall, reserved['cloud_call_id'])
            db.add(CloudCall(id=uid(), workspace_id=p.workspace_id, capability_id=row.capability_id,
                status=row.status, reserved_tokens=row.reserved_tokens, charged_tokens=row.charged_tokens,
                data=deepcopy(row.data)))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            inventory._read(db, p, task, reserved_plan_test_only=plan)
        db.rollback()
