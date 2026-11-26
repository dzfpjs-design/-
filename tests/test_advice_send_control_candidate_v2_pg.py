"""Fenced one-shot control uses synthetic originals in a disposable PG only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending, create
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_control_is_durable_idempotent_and_never_sends(pending):
    s,created,_=pending;issued=create(pending);p=s['principal']
    with Session(engine()) as db:
        first=control.enqueue(db,p,s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert control.enqueue(db,p,s['task_id'],created['plan_id'],issued['delegation_id'])==first
        assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_exactly_one_worker_claim_and_no_expired_lease_reissue(pending):
    s,created,_=pending;issued=create(pending)
    with Session(engine()) as db:
        item=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    barrier=Barrier(2)
    def claim(_):
        with Session(engine()) as db:
            barrier.wait(timeout=5)
            try:
                result=control.claim(db,item['control_id']);db.commit();return ('claimed',result)
            except HTTPException:db.rollback();return ('refused',None)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,[1,2]))
    assert sorted(row[0] for row in results)==['claimed','refused']
    proof=next(row[1] for row in results if row[0]=='claimed')
    with Session(engine()) as db:
        assert control.verify_worker(db,proof)['delegation_id']==issued['delegation_id']
    with Session(engine()) as db:
        assert control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id'])['state']=='needs_attention'
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);row.lease_until=db.scalar(select(func.clock_timestamp()))-__import__('datetime').timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):control.claim(db,item['control_id'])
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_session_revocation_and_grant_revocation_block_worker(pending):
    s,created,_=pending;issued=create(pending);p=s['principal']
    with Session(engine()) as db:
        item=control.enqueue(db,p,s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with Session(engine()) as db:
        proof=control.claim(db,item['control_id']);db.commit()
    with Session(engine()) as db:
        delegation.revoke(db,p,s['task_id'],created['plan_id'],issued['delegation_id'],str(uuid4()));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):control.verify_worker(db,proof)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0


@pytest.mark.parametrize('change',['session_revoked','role_changed','control_tampered','token_changed'])
def test_invalidated_worker_proof_never_creates_cloud_call(pending,change):
    from generic_agents.models import Membership
    s,created,_=pending;issued=create(pending);p=s['principal']
    with Session(engine()) as db:
        item=control.enqueue(db,p,s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with Session(engine()) as db:
        proof=control.claim(db,item['control_id']);db.commit()
    with Session(engine()) as db:
        if change=='session_revoked':db.get(AuthSession,p.session_id).revoked=True
        elif change=='role_changed':db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==p.workspace_id)).role='viewer'
        elif change=='control_tampered':
            row=db.get(Job,item['control_id']);row.data={**row.data,'actor_id':str(uuid4())}
        else:db.get(Job,item['control_id']).lease_token=str(uuid4())
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):control.verify_worker(db,proof)
        db.rollback();assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_control_lease_is_rechecked_at_commit(pending):
    s,created,_=pending;issued=create(pending)
    with Session(engine()) as db:
        item=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with Session(engine()) as db:proof=control.claim(db,item['control_id']);db.commit()
    with Session(engine()) as db:
        control.verify_worker(db,proof)
        db.get(Job,item['control_id']).lease_token=str(uuid4())
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_confirmation_and_control_are_atomic_before_commit(pending):
    s,created,body=pending;p=s['principal']
    with Session(engine()) as db:
        issued=delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        item=control.enqueue(db,p,s['task_id'],created['plan_id'],issued['delegation_id'])
        db.commit()
    engine().dispose()
    with Session(engine()) as db:
        row=db.get(Job,item['control_id']);assert row is not None and row.status=='queued'
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
