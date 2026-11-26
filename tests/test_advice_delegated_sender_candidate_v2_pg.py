"""Disposable-PG checks for one confirmed worker send; no external network.

Goal/source fixtures are synthetic substitutes. No production data is loaded.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reservations
from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permit
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as leases
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Job
from generic_agents.security import Principal
from generic_agents.worker import actor as worker_actor
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_advice_v2_deepseek_adapter_candidate_pg import private_response_spool
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def delegated(pending, synthetic_source_expiry):
    s,created,body=pending;p=s['principal']
    with Session(engine()) as db:
        claims.install(db)
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:storage.install(db)
        db.commit()
    with Session(engine()) as db:
        grant=delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        ctl=control.enqueue(db,p,s['task_id'],created['plan_id'],grant['delegation_id']);db.commit()
    with Session(engine()) as db:proof=control.claim(db,ctl['control_id']);db.commit()
    with Session(engine()) as db:worker=worker_actor(db,p.workspace_id,p.user_id)
    return s,created,grant,proof,worker


def lease_and_reserve(case):
    s,created,grant,proof,worker=case
    with Session(engine()) as db:
        owned=leases.acquire(db,worker,s['task_id'],created['plan_id'],worker_proof=proof);db.commit()
    with Session(engine()) as db:
        reserved=reservations.reserve(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],worker_proof=proof);db.commit()
    return owned,reserved


def test_worker_uses_confirmed_delegation_through_one_permit_without_sending(delegated):
    s,created,grant,proof,worker=delegated
    owned,reserved=lease_and_reserve(delegated)
    with Session(engine()) as db:
        made=intents.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],worker_proof=proof);db.commit()
    with Session(engine()) as db:
        issued=permit.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],made['intent_audit_id'],worker_proof=proof);db.commit()
    assert issued['permit_audit_id'] and reserved['transport_authorized'] is False
    with Session(engine()) as db:
        with pytest.raises(HTTPException):permit.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],made['intent_audit_id'],worker_proof=proof)
        db.rollback();assert db.scalar(select(func.count()).select_from(CloudCall))==1


def test_worker_identity_requires_its_exact_live_control(delegated):
    s,created,grant,proof,worker=delegated
    with Session(engine()) as db:
        with pytest.raises(HTTPException):leases.acquire(db,worker,s['task_id'],created['plan_id'])
        db.rollback()
    with Session(engine()) as db:
        wrong=Principal(str(uuid4()),worker.workspace_id,worker.role,'worker')
        with pytest.raises(HTTPException):leases.acquire(db,wrong,s['task_id'],created['plan_id'],worker_proof=proof)
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


@pytest.mark.parametrize('stop',['grant','session','control_lease'])
def test_revocation_or_expiry_before_permit_prevents_dispatch(delegated,stop):
    s,created,grant,proof,worker=delegated;p=s['principal']
    owned,reserved=lease_and_reserve(delegated)
    with Session(engine()) as db:
        made=intents.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],worker_proof=proof);db.commit()
    with Session(engine()) as db:
        if stop=='grant':delegation.revoke(db,p,s['task_id'],created['plan_id'],grant['delegation_id'],str(uuid4()))
        elif stop=='session':db.get(AuthSession,p.session_id).revoked=True
        else:db.get(Job,proof['control_id']).lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):permit.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],made['intent_audit_id'],worker_proof=proof)
        db.rollback()


@pytest.mark.parametrize('expiring',['session','control_lease'])
def test_expiry_after_permit_is_issued_but_before_commit_rolls_back(delegated,expiring):
    """Use real DB time; the flushed permit must never survive an expired grant context."""
    import time
    from generic_agents.models import Audit
    s,created,grant,proof,worker=delegated
    owned,reserved=lease_and_reserve(delegated)
    with Session(engine()) as db:
        made=intents.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],worker_proof=proof);db.commit()
    with Session(engine()) as db:
        until=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=4)
        if expiring=='session':db.get(AuthSession,s['principal'].session_id).expires_at=until
        else:db.get(Job,proof['control_id']).lease_until=until
        db.commit()
    with Session(engine()) as db:
        permit.issue(db,worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'],made['intent_audit_id'],worker_proof=proof)
        time.sleep(4.1)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==permit.ACTION))==0


def test_delegated_worker_one_loopback_send_survives_db_reconnect(delegated,private_response_spool):
    """A synthetic local provider only; no cloud credential or external network."""
    import json,os
    from generic_agents.gateway import ModelBlocked
    from generic_agents.models import Audit
    from generic_agents import same_goal_advice_deepseek_adapter_candidate_v2 as adapter
    from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as reconcile
    from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
    from tests.synthetic_deepseek_loopback_v2 import provider
    from tests.test_same_goal_advice_runtime_v308 import _opinion
    s,created,grant,proof,worker=delegated
    owned,reserved=lease_and_reserve(delegated)
    with Session(engine()) as db:
        shown=db.scalar(select(Audit).where(Audit.action==preview.ACTION))
        opinion=_opinion(shown.data['preview_record']['cloud_request']['decision_input'])
    args=(worker,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'])
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        result=adapter.execute_synthetic_test_only(*args,worker_proof=proof)
        assert result['state']=='settled' and result['answer_state']=='succeeded'
        assert result['retry_authorized'] is False and len(server.requests)==1
        engine().dispose()
        with Session(engine()) as db:
            value=reconcile.read_result(db,s['principal'],reserved['cloud_call_id'],result['intent_audit_id'])
            assert value['model_value']==opinion
        with pytest.raises((HTTPException,ModelBlocked)):
            adapter.execute_synthetic_test_only(*args,worker_proof=proof)
        assert len(server.requests)==1
