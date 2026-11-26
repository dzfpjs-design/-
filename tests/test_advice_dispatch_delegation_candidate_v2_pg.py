"""Synthetic, disposable-PG tests for same-confirmation delegated custody.

Fixture Goal and sources are substitutes. No external model or device is used.
"""
import copy
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership, Plan, Task
from generic_agents.security import Principal
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as previews
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def pending(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:
        shown=previews.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()), 'start':{
        'request_id':str(uuid4()),'expected_plan_version':3,
        'expected_goal_hash':request['expected_goal_hash'],'expected_mature_head_hash':request['expected_mature_head_hash'],
        'preview_hash':shown['preview_hash'],'cloud_authorized':True,'reason':'合成用户确认'},
        'confirmed':True}
    return s,created,body


def create(pending):
    s,created,body=pending
    with Session(engine()) as db:
        result=delegation.begin_with_delegation(db,s['principal'],s['task_id'],created['plan_id'],body);db.commit()
    return result


def test_real_session_confirmation_is_durable_and_exact_replay_only(pending):
    s,created,body=pending;made=create(pending)
    assert made['state']=='authorized' and made['cloud_send_authorized'] is False
    engine().dispose()
    with Session(engine()) as db:
        assert delegation.begin_with_delegation(db,s['principal'],s['task_id'],created['plan_id'],body)==made
        checked=delegation.inspect_authorization(db,s['principal'],s['task_id'],created['plan_id'],made['delegation_id'])
        assert checked['state']=='authorized' and checked['transport_authorized'] is False
        assert not list(db.scalars(select(CloudCall.id)))
    changed=copy.deepcopy(body);changed['request_id']=str(uuid4())
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.begin_with_delegation(db,s['principal'],s['task_id'],created['plan_id'],changed)
        db.rollback()


def test_confirmation_rejects_non_boolean_and_untrusted_session(pending):
    s,created,body=pending;bad=copy.deepcopy(body);bad['confirmed']=1
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.begin_with_delegation(db,s['principal'],s['task_id'],created['plan_id'],bad)
        db.rollback()
    p=s['principal'];fake=Principal(p.user_id,p.workspace_id,p.role,'internal-worker-marker')
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.begin_with_delegation(db,fake,s['task_id'],created['plan_id'],body)
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.AUTHORIZED)))


def test_expiry_and_session_revocation_fail_closed(pending):
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.inspect_authorization(db,p,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()
    with Session(engine()) as db:
        session=db.get(AuthSession,p.session_id);session.revoked=False;session.expires_at=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.inspect_authorization(db,p,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_role_change_and_workspace_mismatch_fail_closed(pending):
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.inspect_authorization(db,Principal(p.user_id,str(uuid4()),p.role,p.session_id),s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='viewer';db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.inspect_authorization(db,p,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_revocation_is_durable_and_worker_cannot_obtain_permit(pending):
    s,created,_=pending;made=create(pending);p=s['principal'];nonce=str(uuid4())
    with Session(engine()) as db:
        stopped=delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],nonce);db.commit()
    assert stopped['state']=='revoked'
    engine().dispose()
    with Session(engine()) as db:
        assert delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],nonce)==stopped
        with pytest.raises(HTTPException):delegation.inspect_authorization(db,p,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_task_lock_serializes_revocation_before_worker_check(pending):
    s,created,_=pending;made=create(pending);p=s['principal'];ready,done=Event(),Event();out=[]
    owner=Session(engine())
    try:
        delegation.revoke(owner,p,s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))
        def worker():
            with Session(engine()) as db:
                ready.set()
                try:delegation.inspect_authorization(db,p,s['task_id'],created['plan_id'],made['delegation_id']);out.append('authorized')
                except HTTPException:out.append('refused');db.rollback()
                finally:done.set()
        thread=Thread(target=worker,daemon=True);thread.start();assert ready.wait(3);assert not done.wait(.25)
        owner.commit();assert done.wait(15);thread.join();assert out==['refused']
    finally:owner.close()


def test_internal_original_check_never_grants_transport_and_rechecks_session(pending):
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:
        info=delegation.verify_original(db,p.workspace_id,s['task_id'],created['plan_id'],made['delegation_id'])
        assert info['job_id']==made['job_id'] and info['session_id']==p.session_id
        assert info['source_revalidated'] is False and info['transport_authorized'] is False
        db.commit()
    with Session(engine()) as db:db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.verify_original(db,p.workspace_id,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_original_check_rejects_tampered_and_revoked_records(pending):
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:
        event=db.get(Audit,made['delegation_id']);event.data={**event.data,'source_hash':'0'*64};db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.verify_original(db,p.workspace_id,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_grant_expiring_before_outer_commit_rolls_back_start_and_grant(pending,monkeypatch):
    import time
    s,created,body=pending;p=s['principal'];monkeypatch.setattr(delegation,'MAX_VALIDITY',timedelta(seconds=1))
    with Session(engine()) as db:
        delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        # Nested savepoint completion must not remove the outer commit guard.
        with db.begin_nested():pass
        time.sleep(1.2)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Plan,created['plan_id']).status=='awaiting_consent'
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.AUTHORIZED)))


def test_new_session_cannot_reissue_original_confirmation(pending,account):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import User
    s,created,body=pending;made=create(pending);p=s['principal']
    with TestClient(app) as another:
        login=another.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        with Session(engine()) as db:
            session=db.scalar(select(AuthSession).where(AuthSession.user_id==p.user_id,AuthSession.id!=p.session_id,
                AuthSession.revoked.is_(False)).order_by(AuthSession.created_at.desc()))
            assert session is not None
            other=Principal(p.user_id,p.workspace_id,p.role,session.id)
        with Session(engine()) as db:
            with pytest.raises(HTTPException):delegation.begin_with_delegation(db,other,s['task_id'],created['plan_id'],body)
            db.rollback()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):delegation.inspect_authorization(db,other,s['task_id'],created['plan_id'],made['delegation_id'])
            db.rollback()


def test_other_logged_in_operator_cannot_revoke_but_admin_can(pending,client):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    s,created,_=pending;made=create(pending);p=s['principal'];users=[]
    for role in ('operator','admin'):
        username='synthetic_'+uuid4().hex;password=uuid4().hex
        result=client.post('/api/users',json={'username':username,'password':password,'role':role})
        assert result.status_code==201,result.status_code
        browser=TestClient(app);login=browser.post('/api/login',json={'username':username,'password':password})
        assert login.status_code==200
        with Session(engine()) as db:
            session=db.scalar(select(AuthSession).where(AuthSession.user_id==result.json()['id'],AuthSession.revoked.is_(False))
                .order_by(AuthSession.created_at.desc()))
            assert session is not None
            users.append((browser,Principal(result.json()['id'],p.workspace_id,role,session.id)))
    try:
        with Session(engine()) as db:
            with pytest.raises(HTTPException):delegation.revoke(db,users[0][1],s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))
            db.rollback()
        with Session(engine()) as db:
            assert delegation.revoke(db,users[1][1],s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))['state']=='revoked'
            db.commit()
    finally:
        for browser,_ in users:browser.close()


def test_real_committed_permit_cannot_be_reported_as_revoked(pending,monkeypatch):
    from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
    from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reserve
    from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intent
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    s,created,body=pending;made=create(pending);p=s['principal']
    monkeypatch.setattr(delegation.permits,'_source_expiry',lambda db,*_:db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5))
    with Session(engine()) as db:
        claims.install(db)
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:storage.install(db)
        db.commit()
    with Session(engine()) as db:owned=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    with Session(engine()) as db:
        allocated=reserve.reserve(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token']);db.commit()
    with Session(engine()) as db:
        made_intent=intent.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],allocated['cloud_call_id']);db.commit()
    with Session(engine()) as db:
        delegation.permits.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],
            allocated['cloud_call_id'],made_intent['intent_audit_id']);db.commit()
    with Session(engine()) as db:
        replay=delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        assert replay['state']=='dispatch_started_non_retractable' and replay['cloud_send_authorized'] is False
        assert len(list(db.scalars(select(Audit).where(Audit.action==delegation.AUTHORIZED))))==1
        db.rollback()
    with Session(engine()) as db:
        result=delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))
        assert result['state']=='dispatch_started_non_retractable'
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.REVOKED)))
        # A permit is a dispatch boundary; this test never executes transport.
        assert db.get(CloudCall,allocated['cloud_call_id']).status=='reserved'
        db.rollback()


def test_preexisting_separate_start_cannot_be_rebound_to_new_delegation(pending):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    s,created,body=pending;p=s['principal']
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body['start']);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.AUTHORIZED)))


def test_tampered_revocation_never_restores_authority(pending):
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()));db.commit()
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action==delegation.REVOKED));event.data={'schema':'tampered'};db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.verify_original(db,p.workspace_id,s['task_id'],created['plan_id'],made['delegation_id'])
        db.rollback()


def test_grant_and_revoke_nonce_collisions_fail_without_new_effects(pending):
    s,created,body=pending;p=s['principal'];same=copy.deepcopy(body);same['request_id']=same['start']['request_id']
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],same)
        db.rollback()
        assert db.get(Plan,created['plan_id']).status=='awaiting_consent'
    made=create(pending)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],body['start']['request_id'])
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.REVOKED)))


def test_original_check_expiry_before_commit_is_refused(pending,monkeypatch):
    import time
    s,created,_=pending;p=s['principal'];monkeypatch.setattr(delegation,'MAX_VALIDITY',timedelta(seconds=2));made=create(pending)
    with Session(engine()) as db:
        delegation.verify_original(db,p.workspace_id,s['task_id'],created['plan_id'],made['delegation_id'])
        with db.begin_nested():pass
        time.sleep(2.1)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.permits.ACTION)))


def test_revoke_waits_for_real_permit_and_reports_irrevocable(pending,monkeypatch):
    from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
    from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reserve
    from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intent
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    s,created,_=pending;made=create(pending);p=s['principal'];ready,done=Event(),Event();out=[]
    monkeypatch.setattr(delegation.permits,'_source_expiry',lambda db,*_:db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5))
    with Session(engine()) as db:
        claims.install(db)
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:storage.install(db)
        db.commit()
    with Session(engine()) as db:owned=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    with Session(engine()) as db:
        allocated=reserve.reserve(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token']);db.commit()
    with Session(engine()) as db:
        made_intent=intent.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],allocated['cloud_call_id']);db.commit()
    owner=Session(engine())
    try:
        delegation.permits.issue(owner,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],
            allocated['cloud_call_id'],made_intent['intent_audit_id'])
        def cancel():
            with Session(engine()) as db:
                ready.set()
                try:out.append(delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))['state'])
                finally:db.rollback();done.set()
        thread=Thread(target=cancel,daemon=True);thread.start();assert ready.wait(3);assert not done.wait(.25)
        owner.commit();assert done.wait(20);thread.join();assert out==['dispatch_started_non_retractable']
    finally:owner.close()


def test_revoker_session_expiring_before_commit_rolls_back_revocation(pending):
    import time
    s,created,_=pending;made=create(pending);p=s['principal']
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=2);db.commit()
    with Session(engine()) as db:
        assert delegation.revoke(db,p,s['task_id'],created['plan_id'],made['delegation_id'],str(uuid4()))['state']=='revoked'
        with db.begin_nested():pass
        time.sleep(2.2)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==delegation.REVOKED)))
