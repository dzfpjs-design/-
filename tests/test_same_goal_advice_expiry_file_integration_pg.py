"""Isolated PG only; synthetic Goal/source, real DB clock and transaction locks.

This does not create a genuine user approval or prove a mature-Goal journey.
"""
import copy
import hashlib
import time
from datetime import timedelta
from threading import Event, Thread
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_expired_cancel_candidate_v2 as cancel
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_document_lease_candidate_v2 as lease
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Invocation, Job, uid
from tests import test_same_goal_advice_unlimited_store_v2_pg as synthetic


@pytest.fixture
def natural(client,account,monkeypatch):
    from generic_agents.same_goal_advice_intent_index_migration_candidate_v2 import rehearse
    assert rehearse()['status'] in {'installed', 'already_installed'}
    # Only choose a short lifetime when constructing new synthetic facts; no
    # clock, existing timestamp, real approval or existing state is altered.
    duration=timedelta
    with monkeypatch.context() as patch:
        patch.setattr(synthetic,'timedelta',lambda **kw: duration(seconds=8) if kw=={'minutes':15} else duration(**kw))
        s=synthetic.synthetic_custody.__wrapped__(client,account)
    raw='合成资料，验证申请锁与到期状态。'.encode();selection=s['prep']['request']['document_source']
    resolved={'selection':copy.deepcopy(selection),'approved_selection':{**selection['source_set_ref'],
        'submission_id':'00000000-0000-0000-0000-000000000001','submission_hash':'a'*64,'input_key':'runtime_document_sources'},
        'source_set':{'source_snapshot_hash':'b'*64},'content':raw,'sha256':hashlib.sha256(raw).hexdigest(),
        'size_bytes':len(raw),'packet_receipt':None,'evidence_ref':None}
    monkeypatch.setattr(intent.sources,'resolve_documents',lambda *_:copy.deepcopy(resolved))
    monkeypatch.setattr(document.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(s['prep'])})
    return s,resolved


def wait_expired(s):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        with Session(engine()) as db:
            clock=db.scalar(select(func.clock_timestamp()));event=db.get(Audit,s['policy_id'])
            if clock>=policy.instant(event.data['expires_at']):return
        time.sleep(.1)
    pytest.fail('policy did not naturally expire')


def cancel_body():return {'request_id':uid(),'expected_plan_version':1,'reason':'政策已到期，保留原资料并取消'}


def state_counts(db,s):
    return (db.scalar(select(func.count()).select_from(Audit).where(Audit.action==v2.CANCELLED_EXPIRED)),
        db.scalar(select(func.count()).select_from(Audit).where(Audit.action==intent.ACTION)),
        db.scalar(select(func.count()).select_from(Evidence)),len(list(db.scalars(select(CloudCall)))),
        len(list(db.scalars(select(Invocation)))))


def test_policy_expiring_during_registration_source_read_cannot_write(natural, monkeypatch):
    s, resolved = natural; p = s['principal']
    def expires_during_read(*_):
        wait_expired(s)
        return copy.deepcopy(resolved)
    monkeypatch.setattr(intent.sources, 'resolve_documents', expires_during_read)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            intent.register(db, p, s['task_id'], s['job_id'])
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == intent.ACTION)) == 0
        assert db.scalar(select(func.count()).select_from(Evidence)) == 0
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0


def test_register_cannot_commit_after_natural_policy_expiry(natural):
    s,_=natural;p=s['principal'];request=cancel_body();started=Event();done=Event();out=[]
    first=Session(engine())
    def other():
        with Session(engine()) as db:
            started.set()
            try:out.append(cancel.cancel(db,p,s['task_id'],s['plan_id'],request));db.commit()
            finally:done.set()
    try:
        intent.register(first,p,s['task_id'],s['job_id']);wait_expired(s)
        t=Thread(target=other,daemon=True);t.start();assert started.wait(3);assert not done.wait(.2)
        with pytest.raises(HTTPException):first.commit()
        first.rollback();assert done.wait(10);t.join();assert len(out)==1 and not out[0]['replayed']
    finally:first.close()
    with Session(engine()) as db:assert state_counts(db,s)==(1,0,0,0,0)


def test_cancel_then_register_refuses_without_creating_intent(natural):
    s,_=natural;p=s['principal'];wait_expired(s);request=cancel_body();started=Event();done=Event();out=[]
    def other():
        with Session(engine()) as db:
            started.set()
            try:intent.register(db,p,s['task_id'],s['job_id']);out.append('unexpected')
            except HTTPException:out.append('refused')
            finally:done.set()
    with Session(engine()) as first:
        cancel.cancel(first,p,s['task_id'],s['plan_id'],request)
        t=Thread(target=other,daemon=True);t.start();assert started.wait(3);assert not done.wait(.2)
        first.commit();assert done.wait(10);t.join()
    assert out==['refused']
    with Session(engine()) as db:assert state_counts(db,s)==(1,0,0,0,0)


def test_existing_control_after_cancel_cannot_be_claimed(natural):
    s,_=natural;p=s['principal']
    with Session(engine()) as db:
        original=intent.register(db,p,s['task_id'],s['job_id']);control=lease.register(db,p,s['task_id'],s['job_id']);db.commit()
    wait_expired(s)
    with pytest.raises(HTTPException),Session(engine()) as db:lease.claim(db,p,control)
    with Session(engine()) as db:
        result=cancel.cancel(db,p,s['task_id'],s['plan_id'],cancel_body());db.commit()
        assert '未执行清理' in result['notice']
    with pytest.raises(HTTPException),Session(engine()) as db:lease.claim(db,p,control)
    with Session(engine()) as db:
        assert db.get(Job,original) is not None and db.get(Job,control).status=='queued'
        assert state_counts(db,s)==(1,1,0,0,0)


def test_file_write_crossing_expiry_keeps_intent_and_rolls_back_db(natural,monkeypatch):
    s,resolved=natural;p=s['principal']
    with Session(engine()) as db:original=intent.register(db,p,s['task_id'],s['job_id']);db.commit()
    saved=document.evidence.save_evidence
    def cross(*args,**kwargs):
        result=saved(*args,**kwargs);wait_expired(s);return result
    monkeypatch.setattr(document.evidence,'save_evidence',cross)
    with pytest.raises(HTTPException),Session(engine()) as db:document.process(db,p,s['task_id'],s['job_id'])
    path=settings().storage_dir/p.workspace_id/(resolved['sha256']+'.bin');assert path.read_bytes()==resolved['content']
    with Session(engine()) as db:
        assert db.get(Job,original) is not None and state_counts(db,s)==(0,1,0,0,0)
        result=cancel.cancel(db,p,s['task_id'],s['plan_id'],cancel_body());db.commit();assert '未执行清理' in result['notice']
    assert path.exists()
    with pytest.raises(HTTPException),Session(engine()) as db:document.process(db,p,s['task_id'],s['job_id'])


def test_session_naturally_expires_before_registration_commit(natural,monkeypatch):
    from generic_agents import security
    from generic_agents.models import AuthSession, User
    s,_=natural;duration=timedelta
    # New synthetic login is issued with a short lifetime; no existing session
    # or system/database clock is changed.
    with monkeypatch.context() as patch:
        patch.setattr(security,'timedelta',lambda **kw: duration(seconds=3) if 'hours' in kw else duration(**kw))
        with Session(engine()) as db:
            row,_,_=security.new_session(db,db.get(User,s['principal'].user_id));db.commit();session_id=row.id
    p=security.Principal(s['principal'].user_id,s['principal'].workspace_id,'admin',session_id)
    with Session(engine()) as db:
        intent.register(db,p,s['task_id'],s['job_id']);deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            with Session(engine()) as probe:
                clock=probe.scalar(select(func.clock_timestamp()));expiry=probe.get(AuthSession,session_id).expires_at
            if clock>=expiry:break
            time.sleep(.1)
        else:pytest.fail('session did not naturally expire')
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:assert state_counts(db,s)==(0,0,0,0,0)
