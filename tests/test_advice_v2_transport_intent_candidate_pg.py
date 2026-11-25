"""Disposable PG intent tests; reservation verification is substituted.

No network, real approval, real dispatch or durable CloudCall provenance is
proved. The unpatched implementation rejects every attempted intent.
"""
import copy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import Audit,CloudCall,Job,Plan,Task,uid
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as subject
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_advice_v2_cloud_reservation_candidate_pg import started


@pytest.fixture
def reserved(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成意图测试'}
    with Session(engine()) as db:started=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:leased=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    with Session(engine()) as db:
        plan=db.get(Plan,created['plan_id']);pre=db.scalar(select(Audit).where(Audit.action==preview.ACTION))
        st=db.scalar(select(Audit).where(Audit.action==start.ACTION));le=db.scalar(select(Audit).where(Audit.action==lease.ACTION))
        call_id,audit_id=uid(),uid();prep=s['prep'];prepared=pre.data['prepared']
        data={'workspace_id':p.workspace_id,'actor_id':p.user_id,'task_id':s['task_id'],'plan_id':plan.id,
            'job_id':started['job_id'],'call_id':call_id,'audit_id':audit_id,
            'start_ref':{'id':st.id,'hash':digest(st.data)},'lease_ref':{'id':le.id,'hash':digest(le.data)},
            'preview_ref':{'id':pre.id,'hash':digest(pre.data)},'lease_token_hash':digest(leased['lease_token']),
            'model_pin':copy.deepcopy(prep['model_pin']),'policy_ref':copy.deepcopy(prep['budget']['policy_ref']),
            'request_hash':prepared['request_hash'],'dispatch_options_hash':prepared['dispatch_options_hash'],
            'token_bound':prepared['token_bound']}
        db.add(CloudCall(id=call_id,workspace_id=p.workspace_id,capability_id=prep['model_pin']['id'],
            status='reserved',reserved_tokens=prepared['token_bound'],charged_tokens=prepared['token_bound'],
            data={'same_goal_advice_unlimited_v2':data}))
        db.add(Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,
            action='synthetic.reservation',object_id=plan.id,data=data));db.commit()
    return s,created,started,leased,call_id,audit_id


def arguments(r):
    s,created,started,leased,call_id,_=r
    return s['principal'],s['task_id'],created['plan_id'],started['job_id'],leased['lease_token'],call_id


def fake_verifier(r):
    s,_,_,_,call_id,audit_id=r
    def verify(db,p,task,plan,job,token,identity):
        return {'prep':copy.deepcopy(s['prep']),
            'preview':db.scalar(select(Audit).where(Audit.action==preview.ACTION)),
            'reservation':db.get(Audit,audit_id),'call':db.get(CloudCall,call_id)}
    return verify


def test_unintegrated_gate_rejects_even_existing_synthetic_reservation(reserved):
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.issue(db,*arguments(reserved))
        assert not list(db.scalars(select(Audit).where(Audit.action==subject.ACTION)))


def test_synthetic_intent_is_durable_and_never_grants_replay_send(reserved,monkeypatch):
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    with Session(engine()) as db:first=subject.issue(db,*arguments(reserved));db.commit()
    assert first['replayed'] is False and first['send_outcome']=='unknown' and first['transport_authorized'] is False
    engine().dispose()
    with Session(engine()) as db:
        second=subject.issue(db,*arguments(reserved))
        assert second=={**first,'replayed':True}
        assert len(list(db.scalars(select(Audit).where(Audit.action==subject.ACTION))))==1
        assert len(list(db.scalars(select(CloudCall))))==1


def test_changed_synthetic_call_cannot_create_intent(reserved,monkeypatch):
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    with Session(engine()) as db:
        call=db.get(CloudCall,reserved[4]);call.data={'same_goal_advice_unlimited_v2':{'wrong':True}};db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.issue(db,*arguments(reserved))
        assert not list(db.scalars(select(Audit).where(Audit.action==subject.ACTION)))


def test_rehashed_wrong_original_intent_is_not_accepted(reserved,monkeypatch):
    from generic_agents import same_goal_advice_contracts_v302 as contracts
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    with Session(engine()) as db:subject.issue(db,*arguments(reserved));db.commit()
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION));data=copy.deepcopy(event.data)
        data['request_hash']='0'*64;data['record_hash']=contracts.record_hash(data);event.data=data;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.issue(db,*arguments(reserved))
        assert len(list(db.scalars(select(Audit).where(Audit.action==subject.ACTION))))==1


def test_concurrent_intent_cannot_duplicate_or_authorize_send(reserved,monkeypatch):
    from queue import Queue
    from threading import Thread
    from time import monotonic,sleep
    from sqlalchemy import func,text
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    output=[];errors=[];pidq=Queue()
    def contender():
        try:
            with Session(engine()) as db:
                pidq.put(db.scalar(select(func.pg_backend_pid())))
                output.append(subject.issue(db,*arguments(reserved)));db.commit()
        except Exception as exc:errors.append(exc)
    with Session(engine()) as first:
        owner=first.scalar(select(func.pg_backend_pid()));original=subject.issue(first,*arguments(reserved))
        thread=Thread(target=contender,daemon=True);thread.start();pid=pidq.get(timeout=5)
        try:
            until=monotonic()+5;blocked=False
            while monotonic()<until:
                with engine().connect() as obs:blocked=owner in obs.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    assert output==[{**original,'replayed':True}]
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Audit).where(Audit.action==subject.ACTION))))==1
        assert len(list(db.scalars(select(CloudCall))))==1


def test_local_cloudcall_change_before_commit_rolls_back_intent(reserved,monkeypatch):
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    with Session(engine()) as db:
        subject.issue(db,*arguments(reserved))
        db.get(CloudCall,reserved[4]).status='failed'
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==subject.ACTION)))
        assert db.get(CloudCall,reserved[4]).status=='reserved'


def test_isolated_actual_reservation_binds_one_intent(started):
    """Real claim/ledger; synthetic Goal/frame/approval fixture, no HTTP."""
    from tests.test_advice_v2_cloud_reservation_candidate_pg import _reserve
    s,created,owned=started;p=s['principal']
    with Session(engine()) as db:receipt=_reserve(db,started);db.commit()
    with Session(engine()) as db:
        original=subject.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],receipt['cloud_call_id'])
        db.commit()
    assert original['transport_authorized'] is False and original['send_outcome']=='unknown'
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Audit).where(Audit.action==subject.ACTION))))==1
        assert len(list(db.scalars(select(CloudCall))))==1
        # A saved intent cannot be treated as a grant or silently replayed.
        with pytest.raises(HTTPException):subject.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],receipt['cloud_call_id'])


@pytest.mark.parametrize('target',['call_tokens','job_version','audit_action'])
def test_same_transaction_raw_scalar_tampering_rolls_back_intent(reserved,monkeypatch,target):
    from sqlalchemy import text
    monkeypatch.setattr(subject,'_verify_live_reservation',fake_verifier(reserved))
    with Session(engine()) as db:
        intent=subject.issue(db,*arguments(reserved))
        if target=='call_tokens':
            db.execute(text('UPDATE cloud_model_calls SET reserved_tokens=reserved_tokens+1 WHERE id=:id'),{'id':reserved[4]})
        elif target=='job_version':
            db.execute(text('UPDATE jobs SET version=version+1 WHERE id=:id'),{'id':reserved[2]['job_id']})
        else:
            db.execute(text("UPDATE audit_events SET action='synthetic.changed' WHERE id=:id"),{'id':intent['intent_audit_id']})
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==subject.ACTION)))
