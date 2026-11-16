"""Draft tests: no claim that application runs or physical COMMIT is fresh.

Move into tests/ only after _stale(value, trusted) and second clock recheck
are implemented and all shared-source test runs have finished. Requires
conftest's *disposable* PostgreSQL and never a user database.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import auto_evaluation_evidence_gate_v357 as gate
from generic_agents import goal_evaluation_provider_v288 as provider
from generic_agents.db import engine
from generic_agents.models import Audit, User


def sample(at, *, age=10, basis='rolling_latest'):
    identity=str(uuid4())
    item={'observation_id':identity,'observed_at':at.isoformat(),'attributes':{'temperature':25}}
    value={'cut_at':at.isoformat(), 'preparation_policy':{'time_basis':basis,'max_age_seconds':age},
        'evaluation_result':{'result':{'status':'sufficient','evidence':[],'observation_evidence':[item]}}}
    row=NS(id=str(uuid4()),record_hash='a'*64,generation=1,evaluation_query_id=str(uuid4()),
        source_binding_hash='b'*64,goal_snapshot_hash='c'*64,data=value)
    return NS(observation_id=identity),row,value


def test_pure_stale_boundaries_and_missing_times():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    origin,row,value=sample(start)
    point=start+timedelta(seconds=10); epsilon=timedelta(microseconds=1)
    assert provider._stale(value,point) is False
    assert provider._stale(value,point+epsilon) is True  # cut and observation aged out
    value['cut_at']=(start+timedelta(seconds=1)).isoformat()
    assert provider._stale(value,point) is False
    assert provider._stale(value,point+epsilon) is True  # observation, while cut remains current
    item=value['evaluation_result']['result']['observation_evidence'][0]
    item['observed_at']=(start+timedelta(seconds=1)).isoformat();item['valid_until']=point.isoformat()
    assert provider._stale(value,point) is False
    assert provider._stale(value,point+epsilon) is True  # validity instant, not cut or observation
    item['valid_until']=(point+timedelta(days=1)).isoformat();item['observed_at']=None
    assert provider._stale(value,point) is True
    value['preparation_policy']['time_basis']='fixed_window'
    assert provider._stale(value,point+timedelta(days=1)) is False


def test_projection_delay_crosses_ttl_before_second_database_clock_and_rolls_back(account,monkeypatch):
    """Recorded row/ACK are synthetic; check only hook timing and rollback."""
    marker='slow-evaluation-'+uuid4().hex
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));assert user
        initial=db.scalar(select(func.clock_timestamp()))
        origin,row,record=sample(initial,age=2)
        gate.install_commit_freshness(db,origin,row,record)
        user.username=marker
        db.add(Audit(workspace_id=account[2],actor_id=user.id,action='test.second_clock',object_id=marker,data={}))
        db.flush()
        assert db.scalar(select(func.clock_timestamp())) <= initial+timedelta(seconds=2)
        original=provider._evidence
        calls=[]
        def slow_projection(actual_row,actual_value,first_clock):
            evidence=original(actual_row,actual_value,first_clock)
            assert evidence[provider.VIEW]['state']=='current'
            calls.append(first_clock)
            db.connection().execute(select(func.pg_sleep(2.2)))
            return evidence
        monkeypatch.setattr(provider,'_evidence',slow_projection)
        with pytest.raises(HTTPException) as error:db.commit()
        assert error.value.status_code==409 and len(calls)==1
        db.rollback()
        assert 'auto_evaluation_commit_freshness_v357' not in db.info
    with Session(engine()) as check:
        assert check.scalar(select(User).where(User.username==account[0])) is not None
        assert check.scalar(select(User).where(User.username==marker)) is None
        assert check.scalar(select(Audit.id).where(Audit.workspace_id==account[2],Audit.object_id==marker)) is None
