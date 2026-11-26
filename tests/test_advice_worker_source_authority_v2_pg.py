"""Delegated source checks with real sessions/PG and synthetic source fixtures.

The approval and Goal inputs are test substitutes. No model or device is called.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents import same_goal_advice_file_intent_candidate_v2 as current
from generic_agents import same_goal_advice_unlimited_analysis_candidate_v2 as analysis
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as previews
from generic_agents import worker
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Plan, Task
from generic_agents.security import Principal
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending, create
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

REAL_BUILD = analysis.build_current


@pytest.fixture
def controlled(pending):
    s,created,_=pending; issued=create(pending)
    with Session(engine()) as db:
        task=control.enqueue(db,s['principal'],s['task_id'],created['plan_id'],issued['delegation_id']);db.commit()
    with Session(engine()) as db:
        proof=control.claim(db,task['control_id']);db.commit()
    with Session(engine()) as db:
        principal=worker.actor(db,s['principal'].workspace_id,s['principal'].user_id)
    return s,created,issued,proof,principal


def test_worker_needs_live_proof_with_exact_task_actor_and_policy(controlled):
    s,created,issued,proof,p=controlled
    with Session(engine()) as db:
        policy_ref=db.get(Plan,created['plan_id']).data['preparation_snapshot']['budget']['policy_ref']
        with pytest.raises(HTTPException):current._check_current(db,p,s['task_id'],policy_ref)
        db.rollback()
        assert current._check_current(db,p,s['task_id'],policy_ref,worker_proof=proof) is None
        db.rollback()
    for principal,task_id,ref,token in [
        (Principal(str(uuid4()),p.workspace_id,p.role,'worker'),s['task_id'],policy_ref,proof),
        (Principal(p.user_id,str(uuid4()),p.role,'worker'),s['task_id'],policy_ref,proof),
        (p,str(uuid4()),policy_ref,proof),
        (p,s['task_id'],{**policy_ref,'record_hash':'0'*64},proof),
        (p,s['task_id'],policy_ref,{**proof,'lease_token':str(uuid4())}),
        (Principal(p.user_id,p.workspace_id,p.role,s['principal'].session_id),s['task_id'],policy_ref,proof),
    ]:
        with Session(engine()) as db:
            with pytest.raises(HTTPException):current._check_current(db,principal,task_id,ref,worker_proof=token)
            db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_worker_commit_rechecks_same_lease_and_original_session(controlled):
    s,created,issued,proof,p=controlled
    with Session(engine()) as db:
        ref=db.get(Plan,created['plan_id']).data['preparation_snapshot']['budget']['policy_ref']
        current._check_current(db,p,s['task_id'],ref,worker_proof=proof)
        current._bind_commit_guard(db,p,s['task_id'],ref,worker_proof=proof)
        db.get(Job,proof['control_id']).lease_token=str(uuid4())
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_source_frame_checks_worker_proof_in_outer_and_isolated_sessions(controlled,monkeypatch):
    s,created,issued,proof,p=controlled
    with Session(engine()) as db:prep=copy.deepcopy(db.get(Plan,created['plan_id']).data['preparation_snapshot'])
    monkeypatch.setattr(analysis,'build_current',REAL_BUILD)
    monkeypatch.setattr(analysis.authority,'current_policy',lambda *_a,**_k:{
        'scope':{key:value for key,value in prep['scope'].items() if key!='analysis_plan_id'},
        'limits':copy.deepcopy(prep['budget']),'model_pin':prep['model_pin'],'mature_head_hash':prep['mature_head']['head_hash']})
    monkeypatch.setattr(analysis.quota,'_configuration_base',lambda *_a,**_k:{})
    monkeypatch.setattr(analysis.analysis,'_match_scope_facts',lambda *_a,**_k:{})
    monkeypatch.setattr(analysis.documents,'cold_read',lambda *_a,**_k:{})
    monkeypatch.setattr(analysis.analysis,'_verified_sources',lambda *_a,**_k:{})
    monkeypatch.setattr(analysis.frames,'from_resolved',lambda *_a,**_k:{'synthetic_frame':True})
    original=control.verify_worker;seen=[]
    def audited(db,token):
        seen.append(id(db));return original(db,token)
    monkeypatch.setattr(control,'verify_worker',audited)
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        assert analysis.build_current(db,p,task,plan,started=True,worker_proof=proof)=={'synthetic_frame':True}
        assert len(set(seen))>=2
        db.rollback()
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        with pytest.raises(HTTPException):analysis.build_current(db,p,task,plan,started=True,worker_proof={**proof,'lease_token':str(uuid4())})
        db.rollback()


def test_preview_rechecks_authority_and_propagates_worker_proof(controlled,monkeypatch):
    s,created,issued,proof,p=controlled
    original=control.verify_worker;seen=[]
    def audited(db,token):
        seen.append(token);return original(db,token)
    monkeypatch.setattr(control,'verify_worker',audited)
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        prep=copy.deepcopy(plan.data['preparation_snapshot'])
        event=db.scalar(select(Audit).where(Audit.object_id==plan.id,Audit.action==previews.ACTION))
        frame=previews.verify_current_for_start(db,p,task,plan,prep,event,started=True,worker_proof=proof)
        assert frame and len(seen)>=2 and all(value==proof for value in seen)
        db.rollback()


@pytest.mark.parametrize('change',['grant_revoked','session_revoked','session_expired','role_changed','lease_expired'])
def test_source_checks_fail_closed_when_original_authority_ends(controlled,change):
    from datetime import timedelta
    from generic_agents.models import Membership
    s,created,issued,proof,p=controlled;human=s['principal']
    with Session(engine()) as db:
        if change=='grant_revoked':delegation.revoke(db,human,s['task_id'],created['plan_id'],issued['delegation_id'],str(uuid4()))
        elif change=='session_revoked':db.get(AuthSession,human.session_id).revoked=True
        elif change=='session_expired':db.get(AuthSession,human.session_id).expires_at=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        elif change=='role_changed':db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==p.workspace_id)).role='viewer'
        else:db.get(Job,proof['control_id']).lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        db.commit()
    with Session(engine()) as db:
        ref=db.get(Plan,created['plan_id']).data['preparation_snapshot']['budget']['policy_ref']
        with pytest.raises(HTTPException):current._check_current(db,p,s['task_id'],ref,worker_proof=proof)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
