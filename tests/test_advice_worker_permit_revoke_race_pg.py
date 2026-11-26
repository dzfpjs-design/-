"""Actual PostgreSQL row-lock ordering for synthetic delegated dispatch.

No provider is contacted. The shared fixture has synthetic source substitutes;
this checks serialization and durable records, not real source provenance.
"""
from queue import Queue
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permit
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from tests.test_advice_delegated_sender_candidate_v2_pg import delegated, lease_and_reserve
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending
from tests.test_advice_v2_deepseek_adapter_candidate_pg import private_response_spool
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.mark.parametrize('winner',['revoke','permit'])
def test_delegated_revoke_and_permit_serialize_by_pg_lock(delegated,winner):
    s,created,grant,proof,worker=delegated;p=s['principal']
    owned,reserved=lease_and_reserve(delegated)
    with Session(engine()) as db:
        made=intents.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],worker_proof=proof)
        db.commit()
    def issue(db):
        return permit.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],
            reserved['cloud_call_id'],made['intent_audit_id'],worker_proof=proof)
    def revoke(db):
        return delegation.revoke(db,p,s['task_id'],created['plan_id'],grant['delegation_id'],str(uuid4()))
    pids=Queue();results=Queue()
    def competing_action():
        with Session(engine()) as db:
            pids.put(db.scalar(select(func.pg_backend_pid())))
            try:
                result=(issue if winner=='revoke' else revoke)(db)
                db.commit();results.put(('committed',result))
            except HTTPException as exc:
                db.rollback();results.put(('rejected',exc.status_code))
            except BaseException as exc:
                db.rollback();results.put(('error',repr(exc)))
    with Session(engine()) as owner:
        owner_pid=owner.scalar(select(func.pg_backend_pid()))
        first=(revoke if winner=='revoke' else issue)(owner)
        if winner=='revoke':assert first['state']=='revoked'
        else:assert first['permit_audit_id']
        thread=Thread(target=competing_action,daemon=True);thread.start()
        pid=pids.get(timeout=5);blocked=False
        try:
            deadline=monotonic()+5
            while monotonic()<deadline:
                with engine().connect() as observer:
                    blocked=owner_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.03)
            assert blocked,'competing action must actually wait on the owner transaction'
            owner.commit()
        finally:
            if owner.in_transaction():owner.rollback()
            thread.join(timeout=10)
        assert not thread.is_alive()
    outcome,result=results.get(timeout=1)
    if winner=='revoke':assert outcome=='rejected' and result==409
    else:assert outcome=='committed' and result['state']=='dispatch_started_non_retractable'
    with Session(engine()) as db:
        permits=db.scalar(select(func.count()).select_from(Audit).where(Audit.action==permit.ACTION,Audit.object_id==created['plan_id']))
        revokes=db.scalar(select(func.count()).select_from(Audit).where(Audit.action==delegation.REVOKED,Audit.object_id==created['plan_id']))
        assert (permits,revokes)==((0,1) if winner=='revoke' else (1,0))
