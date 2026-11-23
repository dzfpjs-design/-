"""Transactional writer tests with a stubbed Goal/source candidate.

Real PG custody, nonce, roles, same-number replay and ordinary-worker exclusion
are used. This is not a full user journey or proof of a mature Goal/source.
"""
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, Job, Plan, uid
from generic_agents.worker import claim
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def empty_original(synthetic_custody, monkeypatch):
    s = synthetic_custody
    with Session(engine()) as db:
        db.delete(db.get(Job, s['job_id'])); db.delete(db.get(Audit, s['accepted_id']))
        db.delete(db.get(Plan, s['plan_id'])); db.commit()
    prep = copy.deepcopy(s['prep'])
    monkeypatch.setattr(writer.candidate, 'derive_candidate', lambda *_: {
        'preparation_candidate': copy.deepcopy(prep)})
    return s


def test_original_is_atomic_durable_and_invisible_to_generic_worker(empty_original):
    s = empty_original; p = s['principal']; request = s['prep']['request']; ref = s['prep']['budget']['policy_ref']
    with Session(engine()) as db:
        created = writer.prepare(db, p, s['task_id'], request, ref)
        assert created == {'plan_id': s['plan_id'], 'job_id': created['job_id'], 'replayed': False}
        db.commit()
    assert claim(created['job_id'], kinds=[v2.PREPARE_KIND], workspace_id=p.workspace_id) is None
    engine().dispose()
    with Session(engine()) as db:
        replay = writer.prepare(db, p, s['task_id'], request, ref)
        assert replay == {**created, 'replayed': True}
        assert db.get(Job, created['job_id']).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Plan).where(Plan.workspace_id == p.workspace_id,
            Plan.id == created['plan_id'])) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == v2.ACCEPTED, Audit.object_id == created['plan_id'])) == 1
        db.rollback()
    with Session(engine()) as db, pytest.raises(HTTPException):
        writer.prepare(db, p, s['task_id'], {**request, 'reason': '不同正文'}, ref)
    with Session(engine()) as db, pytest.raises(HTTPException):
        writer.prepare(db, p, s['task_id'], request, {**ref, 'id': uid()})


def test_rollback_never_leaves_a_partial_original(empty_original):
    s = empty_original; p = s['principal']; request = s['prep']['request']; ref = s['prep']['budget']['policy_ref']
    with Session(engine()) as db:
        created = writer.prepare(db, p, s['task_id'], request, ref)
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Plan, created['plan_id']) is None
        assert db.get(Job, created['job_id']) is None
        assert not db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == v2.ACCEPTED, Audit.object_id == created['plan_id']))
    with Session(engine()) as db:
        created = writer.prepare(db, p, s['task_id'], request, ref)
        assert created['replayed'] is False
        db.commit()
