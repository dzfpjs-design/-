"""Disposable PG commit-hook tests; the evaluation record here is synthetic.

These tests prove ordinary transactional rollback and time checks, not actual
physical commit-time freshness or the full published agent journey.
"""
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from generic_agents import auto_evaluation_evidence_gate_v357 as gate
from generic_agents.db import engine
from generic_agents.models import Audit, User


def evidence_at(at, *, age, observed_delta=0, time_basis='rolling_latest'):
    observation_id=str(uuid4())
    item={'observation_id':observation_id,'observed_at':(at-timedelta(seconds=observed_delta)).isoformat(),
          'attributes':{'temperature':25}}
    record={'cut_at':at.isoformat(), 'preparation_policy':{'time_basis':time_basis,'max_age_seconds':age},
            'evaluation_result':{'result':{'status':'sufficient','evidence':[],'observation_evidence':[item]}}}
    row=NS(id=str(uuid4()),record_hash='a'*64,generation=1,evaluation_query_id=str(uuid4()),
           source_binding_hash='b'*64,goal_snapshot_hash='c'*64,data=record)
    return NS(observation_id=observation_id),row,record


def current_account(db,account):
    user=db.scalar(select(User).where(User.username==account[0]))
    assert user is not None
    return user


def test_expired_between_stage_and_commit_rolls_back_original_transaction(account):
    marker='expiry-proof-'+uuid4().hex
    with Session(engine()) as db:
        user=current_account(db,account)
        current=db.scalar(select(func.clock_timestamp()))
        origin,row,record=evidence_at(current,age=2)
        gate.install_commit_freshness(db,origin,row,record)
        callback=db.info['auto_evaluation_commit_freshness_v357']['callback']
        assert event.contains(db,'before_commit',callback)
        user.username=marker
        db.add(Audit(workspace_id=account[2],actor_id=user.id,action='test.auto_commit',object_id=marker,data={}))
        db.flush()  # Writes exist inside this transaction but are not yet committed.
        still_valid=db.scalar(select(func.clock_timestamp()))
        assert still_valid <= current+timedelta(seconds=2)
        db.execute(select(func.pg_sleep(2.2)))
        with pytest.raises(HTTPException) as error:db.commit()
        assert error.value.status_code==409
        db.rollback()
        assert 'auto_evaluation_commit_freshness_v357' not in db.info
        assert not event.contains(db,'before_commit',callback)
    with Session(engine()) as db:
        assert current_account(db,account).username==account[0]
        assert db.scalar(select(Audit.id).where(Audit.workspace_id==account[2],Audit.object_id==marker)) is None


def test_current_commit_rejects_duplicate_guard_and_session_can_be_reused(account):
    with Session(engine()) as db:
        user=current_account(db,account);now=db.scalar(select(func.clock_timestamp()))
        origin,row,record=evidence_at(now,age=60)
        gate.install_commit_freshness(db,origin,row,record)
        with pytest.raises(HTTPException) as error:gate.install_commit_freshness(db,origin,row,record)
        assert error.value.status_code==409
        callback=db.info['auto_evaluation_commit_freshness_v357']['callback']
        marker='valid-proof-'+uuid4().hex
        user.username=marker;db.commit()
        assert 'auto_evaluation_commit_freshness_v357' not in db.info
        assert not event.contains(db,'before_commit',callback)
        # Reuse the same session with a new root transaction and new guard.
        now2=db.scalar(select(func.clock_timestamp()))
        origin2,row2,record2=evidence_at(now2,age=60)
        gate.install_commit_freshness(db,origin2,row2,record2)
        callback2=db.info['auto_evaluation_commit_freshness_v357']['callback']
        user.username=account[0];db.commit()
        assert not event.contains(db,'before_commit',callback2)
        assert current_account(db,account).username==account[0]


def test_rollback_cleans_guard_and_session_reuse_does_not_apply_old_expiry(account):
    with Session(engine()) as db:
        user=current_account(db,account);now=db.scalar(select(func.clock_timestamp()))
        origin,row,record=evidence_at(now,age=1,observed_delta=2)
        gate.install_commit_freshness(db,origin,row,record)
        callback=db.info['auto_evaluation_commit_freshness_v357']['callback']
        user.username='rolled-back-'+uuid4().hex
        db.rollback()
        assert 'auto_evaluation_commit_freshness_v357' not in db.info
        assert not event.contains(db,'before_commit',callback)
        assert current_account(db,account).username==account[0]
        db.commit()


def test_nested_transaction_is_not_authorized_to_install_guard(account):
    with Session(engine()) as db:
        current_account(db,account);now=db.scalar(select(func.clock_timestamp()))
        origin,row,record=evidence_at(now,age=60)
        with db.begin_nested():
            with pytest.raises(HTTPException) as error:gate.install_commit_freshness(db,origin,row,record)
            assert error.value.status_code==409
        assert 'auto_evaluation_commit_freshness_v357' not in db.info
        db.rollback()
