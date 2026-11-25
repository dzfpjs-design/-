"""Disposable PG only; Goal/source resolvers are synthetic test doubles.

This verifies durable local delegation and a real lease, not a released worker,
real source approval, UI or model dispatch.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import leases, worker as base_worker
from generic_agents import same_goal_advice_offline_grant_candidate_v2 as subject
from generic_agents import same_goal_advice_document_lease_candidate_v2 as control
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Job, Membership
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def issue(s, created, request_id=None):
    with Session(engine()) as db:
        result=subject.issue(db,s['principal'],s['task_id'],created['job_id'],request_id or str(uuid4()))
        db.commit();return result


def control_lease(s, created):
    with Session(engine()) as db:
        control_id=control.register(db,s['principal'],s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:
        token=control.claim(db,s['principal'],control_id);db.commit()
    return control_id,token


def check(s, created, grant_id, control_id, token):
    with Session(engine()) as source:
        actor=base_worker.actor(source,s['principal'].workspace_id,s['principal'].user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor
        leases.attach(db,control_id,token)
        return subject.check_worker(db,actor,s['task_id'],created['job_id'],grant_id)


def test_grant_survives_logout_new_connection_and_valid_real_lease(ready):
    s,created,_=ready;p=s['principal'];request_id=str(uuid4());first=issue(s,created,request_id)
    assert issue(s,created,request_id)=={**first,'replayed':True}
    control_id,token=control_lease(s,created)
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    engine().dispose()
    assert check(s,created,first['grant_id'],control_id,token)['job_id']==created['job_id']
    with Session(engine()) as db:
        assert not list(db.scalars(select(CloudCall)))
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.GRANT))==1


def test_explicit_revoke_blocks_original_worker_and_replays_same_number(ready):
    s,created,_=ready;p=s['principal'];grant=issue(s,created);control_id,token=control_lease(s,created)
    rid=str(uuid4())
    with Session(engine()) as db:
        result=subject.revoke(db,p,s['task_id'],created['job_id'],grant['grant_id'],rid,'工作岗位调整');db.commit()
    with Session(engine()) as db:
        assert subject.revoke(db,p,s['task_id'],created['job_id'],grant['grant_id'],rid,'工作岗位调整')=={**result,'replayed':True}
        db.rollback()
    with pytest.raises(HTTPException):check(s,created,grant['grant_id'],control_id,token)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.REVOKE))==1
        assert not list(db.scalars(select(CloudCall)))


def test_worker_role_downgrade_and_changed_source_fail_closed(ready,monkeypatch):
    s,created,resolved=ready;p=s['principal'];grant=issue(s,created);control_id,token=control_lease(s,created)
    changed={**resolved,'sha256':'a'*64}
    monkeypatch.setattr(subject.sources,'resolve_documents',lambda *_:changed)
    with pytest.raises(HTTPException):check(s,created,grant['grant_id'],control_id,token)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='viewer';db.commit()
    with pytest.raises((HTTPException,base_worker.ActorRevoked)):check(s,created,grant['grant_id'],control_id,token)


def test_stale_lease_and_forged_worker_identity_rejected(ready):
    s,created,_=ready;grant=issue(s,created);control_id,token=control_lease(s,created)
    with Session(engine()) as db:
        row=db.get(Job,control_id);row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with pytest.raises((HTTPException,leases.LeaseLost)):
        check(s,created,grant['grant_id'],control_id,token)
    p=s['principal']
    with Session(engine()) as db,pytest.raises(HTTPException):
        subject.check_worker(db,p,s['task_id'],created['job_id'],grant['grant_id'])


def test_competing_same_grant_number_creates_one_receipt(ready):
    s,created,_=ready;rid=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:issue(s,created,rid),range(2)))
    assert len({v['grant_id'] for v in values})==1
    assert sorted(v['replayed'] for v in values)==[False,True]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.GRANT))==1


def test_source_change_after_worker_check_rolls_back_at_commit(ready,monkeypatch):
    s,created,resolved=ready;grant=issue(s,created);control_id,token=control_lease(s,created)
    current=[resolved]
    monkeypatch.setattr(subject.sources,'resolve_documents',lambda *_:current[0])
    with Session(engine()) as source:
        actor=base_worker.actor(source,s['principal'].workspace_id,s['principal'].user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,token)
        subject.check_worker(db,actor,s['task_id'],created['job_id'],grant['grant_id'])
        current[0]={**resolved,'sha256':'c'*64}
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert not list(db.scalars(select(CloudCall)))


def test_concurrent_revocation_waits_for_locked_worker_and_blocks_later_check(ready):
    from threading import Event
    from time import monotonic, sleep
    from sqlalchemy import text
    s,created,_=ready;p=s['principal'];grant=issue(s,created);control_id,token=control_lease(s,created)
    with Session(engine()) as source:
        actor=base_worker.actor(source,p.workspace_id,p.user_id)
    entered=Event();pids=[]
    def revoke():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout = '7s'"));pids.append(db.scalar(select(func.pg_backend_pid())));entered.set()
            value=subject.revoke(db,p,s['task_id'],created['job_id'],grant['grant_id'],str(uuid4()),'撤销');db.commit();return value
    with Session(engine()) as processing,ThreadPoolExecutor(max_workers=1) as pool:
        processing.info['worker_actor']=actor;leases.attach(processing,control_id,token)
        subject.check_worker(processing,actor,s['task_id'],created['job_id'],grant['grant_id'])
        owner=processing.scalar(select(func.pg_backend_pid()));future=pool.submit(revoke);assert entered.wait(3)
        deadline=monotonic()+3
        while True:
            with Session(engine()) as monitor: blocked=monitor.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids[0]})
            if owner in blocked:break
            assert not future.done() and monotonic()<deadline
            sleep(.05)
        processing.commit();assert future.result(timeout=5)['revoked']
    with pytest.raises(HTTPException):check(s,created,grant['grant_id'],control_id,token)


def test_source_change_before_grant_commit_does_not_save_authorization(ready,monkeypatch):
    s,created,resolved=ready;current=[resolved]
    monkeypatch.setattr(subject.sources,'resolve_documents',lambda *_:current[0])
    with Session(engine()) as db:
        subject.issue(db,s['principal'],s['task_id'],created['job_id'],str(uuid4()))
        current[0]={**resolved,'sha256':'d'*64}
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.GRANT))==0


@pytest.fixture
def packet_ready(empty_original,monkeypatch,request):
    """Persist evidence/worker/receipts; packet origin and Goal are test doubles."""
    import copy
    from types import SimpleNamespace
    from generic_agents import evidence
    from generic_agents.contracts import digest
    from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
    from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
    from generic_agents import same_goal_advice_preparation_v304 as legacy
    from generic_agents import same_goal_advice_unlimited_plan_candidate_v2 as candidate
    from generic_agents import same_goal_advice_unlimited_contracts_v2 as contract
    from generic_agents.models import Capability
    s=empty_original;p=s['principal'];raw='合成已批准资料包'.encode()
    with Session(engine(),expire_on_commit=False) as db:
        file=evidence.save_evidence(db,p,raw,'text/plain');db.commit();db.refresh(file)
        ref=document.sources._text(file,raw);file_id,sha=file.id,file.sha256
        cap=db.get(Capability,s['prep']['advice_capability_ref']['id']);manifest=copy.deepcopy(cap.data)
    source_id=str(uuid4());packet_id=str(uuid4())
    selected={'kind':'document_packet_v207','packet':{'id':packet_id,'version':1,'evidence_id':file_id,
        'sha256':sha,'source_set_id':source_id,'source_set_hash':'b'*64}}
    prep=copy.deepcopy(s['prep']);prep.pop('record_hash');prep['request']['document_source']=selected
    business = p
    if getattr(request, 'param', False):
        from generic_agents.models import User
        from generic_agents.security import Principal, new_session, password_hash
        with Session(engine()) as db:
            user=User(username='synthetic_business_'+uuid4().hex[:10], password_hash=password_hash('synthetic-long-password'))
            db.add(user);db.flush();db.add(Membership(user_id=user.id,workspace_id=p.workspace_id,role='operator'))
            session,_,_=new_session(db,user);business=Principal(user.id,p.workspace_id,'operator',session.id);db.commit()
        prep['requested_by']=business.user_id;prep['business_actor_id']=business.user_id
    prep['request_hash']=digest(prep['request']);prep=contract.seal_preparation(prep,manifest=manifest);s['prep']=prep
    s['business_principal']=business
    resolved={'selection':selected,'approved_selection':{'source_set_id':source_id,'source_set_hash':'b'*64,
        'submission_id':str(uuid4()),'submission_hash':'c'*64,'input_key':'runtime_document_sources'},
        'source_set':{'source_snapshot_hash':'d'*64},'content':raw,'sha256':sha,'size_bytes':len(raw),
        'packet_receipt':{'producer_hash':'e'*64},'evidence_ref':ref}
    monkeypatch.setattr(document.sources,'resolve_documents',lambda *_:copy.deepcopy(resolved))
    original_fresh=legacy._fresh
    monkeypatch.setattr(legacy,'_fresh',lambda db,principal,model,identity,*args,**kw:
        SimpleNamespace(actor_id=p.user_id) if model is document.sources.packets.Packet else original_fresh(db,principal,model,identity,*args,**kw))
    monkeypatch.setattr(writer.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(prep)})
    def derive_with_original_requester(db,principal,task_id,body,ref,requested_by):
        assert requested_by == prep['requested_by']
        return {'preparation_candidate':copy.deepcopy(prep)}
    monkeypatch.setattr(candidate,'_derive_core',derive_with_original_requester)
    with Session(engine()) as db:
        created=writer.prepare(db,business,s['task_id'],prep['request'],prep['budget']['policy_ref']);db.commit()
    return s,created


def test_delegated_real_worker_prepares_original_packet_after_logout(packet_ready):
    from generic_agents.models import Plan
    s,created=packet_ready;p=s['principal'];grant=issue(s,created);control_id,token=control_lease(s,created)
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db:actor=base_worker.actor(db,p.workspace_id,p.user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,token)
        result=subject.process_packet(db,actor,control_id,token,grant['grant_id']);db.commit()
    assert result['replayed'] is False
    engine().dispose()
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='succeeded'
        assert db.get(Plan,created['plan_id']).status=='awaiting_consent'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==control.v2.DOCUMENT_PREPARED,
            Audit.object_id==created['plan_id']))==1
        assert not list(db.scalars(select(CloudCall)))


def test_delegation_cannot_write_new_source_set_file(ready):
    s,created,_=ready;p=s['principal'];grant=issue(s,created);control_id,token=control_lease(s,created)
    with Session(engine()) as db:actor=base_worker.actor(db,p.workspace_id,p.user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,token)
        with pytest.raises(HTTPException):subject.process_packet(db,actor,control_id,token,grant['grant_id'])
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,created['job_id']).status=='queued'
        assert not list(db.scalars(select(CloudCall)))


def test_offline_claim_receipt_restart_reclaim_and_settle(packet_ready):
    from generic_agents.models import Plan
    s,created=packet_ready;p=s['principal'];grant=issue(s,created)
    with Session(engine()) as db:
        control_id=control.register(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    # User is offline before any worker claims the original control Job.
    with Session(engine()) as db:
        lease=subject.claim_worker(db,control_id,grant['grant_id']);db.commit()
    with Session(engine()) as source:actor=base_worker.actor(source,p.workspace_id,p.user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,lease['lease_token'])
        assert not subject.process_packet(db,actor,control_id,lease['lease_token'],grant['grant_id'])['replayed'];db.commit()
    # A dead worker left a durable receipt. Only test lease expiry is forced.
    engine().dispose()
    with Session(engine()) as db:
        row=db.get(Job,control_id);row.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1);db.commit()
    with Session(engine()) as db:
        replacement=subject.claim_worker(db,control_id,grant['grant_id']);db.commit()
    assert replacement['lease_token']!=lease['lease_token']
    with Session(engine()) as source:actor=base_worker.actor(source,p.workspace_id,p.user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,replacement['lease_token'])
        assert subject.process_packet(db,actor,control_id,replacement['lease_token'],grant['grant_id'])['replayed'];db.commit()
    with Session(engine()) as db:
        subject.settle_worker(db,control_id,replacement['lease_token'],grant['grant_id']);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert db.get(Job,control_id).status=='succeeded'
        assert db.get(Plan,created['plan_id']).status=='awaiting_consent'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==control.v2.DOCUMENT_PREPARED,
            Audit.object_id==created['plan_id']))==1
        assert not list(db.scalars(select(CloudCall)))


def test_revoked_offline_grant_cannot_claim(ready):
    s,created,_=ready;p=s['principal'];grant=issue(s,created)
    with Session(engine()) as db:
        control_id=control.register(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:
        subject.revoke(db,p,s['task_id'],created['job_id'],grant['grant_id'],str(uuid4()),'撤权');db.commit()
    with Session(engine()) as db,pytest.raises(HTTPException):
        subject.claim_worker(db,control_id,grant['grant_id'])
    with Session(engine()) as db:assert db.get(Job,control_id).status=='queued'


def test_other_operator_cannot_revoke_but_admin_can_revoke(ready):
    from generic_agents.models import User
    from generic_agents.security import Principal, new_session, password_hash
    s,created,_=ready;p=s['principal'];grant=issue(s,created);actors=[]
    with Session(engine()) as db:
        for role in ('operator','admin'):
            user=User(username='synthetic_'+uuid4().hex[:12],password_hash=password_hash('synthetic-long-password'))
            db.add(user);db.flush();db.add(Membership(user_id=user.id,workspace_id=p.workspace_id,role=role))
            session,_,_=new_session(db,user);actors.append(Principal(user.id,p.workspace_id,role,session.id))
        db.commit()
    rid=str(uuid4())
    with Session(engine()) as db,pytest.raises(HTTPException):
        subject.revoke(db,actors[0],s['task_id'],created['job_id'],grant['grant_id'],rid,'不相关岗位')
    with Session(engine()) as db:
        assert not db.scalar(select(Audit.id).where(Audit.action==subject.REVOKE))
        assert subject.revoke(db,actors[1],s['task_id'],created['job_id'],grant['grant_id'],rid,'管理员撤销')['revoked']
        db.commit()
    with Session(engine()) as db:
        record=db.scalar(select(Audit).where(Audit.action==subject.REVOKE))
        assert record.actor_id==actors[1].user_id and record.data['actor_role']=='admin'


@pytest.fixture
def natural_short_policy(client,account,monkeypatch):
    from tests.test_same_goal_advice_expiry_file_integration_pg import natural
    return natural.__wrapped__(client,account,monkeypatch)


def test_grant_commit_rechecks_natural_policy_expiry(natural_short_policy):
    from tests.test_same_goal_advice_expiry_file_integration_pg import wait_expired
    s,_=natural_short_policy
    with Session(engine()) as db:
        subject.issue(db,s['principal'],s['task_id'],s['job_id'],str(uuid4()))
        wait_expired(s)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.GRANT))==0


def test_grant_commit_rechecks_natural_http_session_expiry(ready):
    s,created,_=ready;p=s['principal']
    with Session(engine()) as db:
        row=db.get(AuthSession,p.session_id);row.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=2);db.commit()
    with Session(engine()) as db:
        subject.issue(db,p,s['task_id'],created['job_id'],str(uuid4()))
        db.execute(select(func.pg_sleep(2.1)))
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==subject.GRANT))==0


@pytest.mark.parametrize('packet_ready',[True],indirect=True)
def test_distinct_business_requester_and_data_role_keep_original_requester(packet_ready):
    from generic_agents.models import Plan
    s,created=packet_ready;p=s['principal'];business=s['business_principal']
    assert business.user_id != p.user_id
    grant=issue(s,created)
    with Session(engine()) as db:
        control_id=control.register(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:
        db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db:
        lease=subject.claim_worker(db,control_id,grant['grant_id']);db.commit()
    with Session(engine()) as source:actor=base_worker.actor(source,p.workspace_id,p.user_id)
    with Session(engine()) as db:
        db.info['worker_actor']=actor;leases.attach(db,control_id,lease['lease_token'])
        assert subject.process_packet(db,actor,control_id,lease['lease_token'],grant['grant_id'])['replayed'] is False
        db.commit()
    with Session(engine()) as db:
        plan=db.get(Plan,created['plan_id'])
        assert plan.data['preparation_snapshot']['requested_by']==business.user_id
        assert plan.data['preparation_snapshot']['data_actor_id']==p.user_id
        event=db.get(Audit,grant['grant_id'])
        assert event.data['requested_by']==business.user_id and event.actor_id==p.user_id
        assert not list(db.scalars(select(CloudCall)))
