"""Disposable PG only: a synthetic intent verifier substitutes for missing v2 integration.

No test here proves an actual provider response, send intent, live authorization,
production recovery, or external network behavior.
"""
import copy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, User
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as subject
from generic_agents import same_goal_advice_response_intent_gate_candidate_v2 as gate
from generic_agents.contracts import digest


@pytest.fixture
def synthetic(account):
    with Session(engine()) as db:
        if db.scalar(text("select to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            subject.install(db);db.commit()
        user=db.scalar(select(User).where(User.username==account[0]))
        cap=Capability(workspace_id=account[2],namespace='synthetic',name='stub-model',release_version='1',
            kind='foundation_model',status='published',content_hash='a'*64,validated_hash='a'*64,data={})
        db.add(cap);db.flush()
        call=CloudCall(workspace_id=account[2],capability_id=cap.id,status='reserved',
            reserved_tokens=10,charged_tokens=10,data={'purpose':'synthetic'})
        db.add(call);db.flush()
        intent=Audit(workspace_id=account[2],actor_id=user.id,action='synthetic.intent',object_id=call.id,data={'test':True})
        db.add(intent);db.commit()
        return {'workspace':account[2],'actor':user.id,'call':call.id,'intent':intent.id}


def fake_gate(db,w,c,i):
    """A test substitute, not the missing fixed v2 server authority."""
    call=db.get(CloudCall,c,populate_existing=True);intent=db.get(Audit,i,populate_existing=True)
    assert call is not None and intent is not None and call.workspace_id==intent.workspace_id==w
    assert intent.object_id==c and intent.action=='synthetic.intent'
    return {'actor_id':intent.actor_id,'anchor_hash':digest({'call':c,'intent':i})}


def receipt(s):
    return {'call_id':s['call'],'provider':'deepseek','model':'synthetic-model','http_status':200}


def test_unconnected_fixed_gate_fails_closed(synthetic):
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            subject.capture(db,synthetic['workspace'],synthetic['call'],synthetic['intent'],b'{}',receipt(synthetic))
        assert not db.scalar(text('SELECT EXISTS(SELECT 1 FROM ga_test_advice_v2_first_response)'))


def test_round_trip_exact_replay_without_live_role(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic;raw=b'\x00\xff\nraw-bytes'
    with Session(engine()) as db:
        first=subject.capture(db,s['workspace'],s['call'],s['intent'],raw,receipt(s));db.commit()
    engine().dispose()
    with Session(engine()) as db:
        user=db.get(User,s['actor']);user.active=False;db.commit()
    with Session(engine()) as db:
        assert subject.read(db,s['workspace'],s['call'],s['intent'])==first
        assert subject.capture(db,s['workspace'],s['call'],s['intent'],raw,receipt(s))==first
        db.rollback()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.capture(db,s['workspace'],s['call'],s['intent'],b'different',receipt(s))
        db.rollback()


def test_receipt_missing_and_raw_tamper_fail_closed(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s));db.commit()
    with Session(engine()) as db:
        db.execute(text('UPDATE ga_test_advice_v2_first_response SET raw_bytes=:x'),{'x':b'altered'});db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.read(db,s['workspace'],s['call'],s['intent'])
        db.rollback()
    with Session(engine()) as db:
        db.execute(text('DELETE FROM ga_test_advice_v2_first_response'));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))
        db.rollback()


def test_same_transaction_sql_tamper_rolls_back(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))
        db.execute(text('UPDATE ga_test_advice_v2_first_response SET raw_bytes=:x'),{'x':b'alt'})
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:assert subject.read(db,s['workspace'],s['call'],s['intent']) == {'state':'unknown','retry_authorized':False}


def test_capture_rejects_ambient_savepoint(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        with db.begin_nested():
            with pytest.raises(HTTPException):subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))
        db.rollback()


def test_concurrent_capture_fences_then_same_bytes_replay(synthetic,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import DBAPIError
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    def compete():
        with Session(engine()) as db:
            try:
                subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))
                db.commit();return 'created'
            except DBAPIError:
                db.rollback();return 'locked'
    with Session(engine()) as first:
        saved=subject.capture(first,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(compete).result(timeout=10)=='locked'
        first.commit()
    with Session(engine()) as db:
        assert subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s))==saved
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(text('SELECT count(*) FROM ga_test_advice_v2_first_response'))==1
        assert db.scalar(select(__import__('sqlalchemy').func.count()).select_from(Audit).where(Audit.action==subject.ACTION))==1


def test_altered_audit_metadata_fails_offline_recovery(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s));db.commit()
    with Session(engine()) as db:
        db.execute(text('UPDATE audit_events SET version=version+1 WHERE action=:action'),{'action':subject.ACTION});db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.read(db,s['workspace'],s['call'],s['intent'])
        db.rollback()


def test_missing_unique_constraint_refuses_instead_of_repair(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        db.execute(text('ALTER TABLE ga_test_advice_v2_first_response DROP CONSTRAINT ga_test_response_intent'))
        with pytest.raises(HTTPException):subject.read(db,s['workspace'],s['call'],s['intent'])
        db.rollback()


def test_completed_transaction_does_not_attach_guard_to_later_commit(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s));db.commit()
        db.execute(text('UPDATE ga_test_advice_v2_first_response SET raw_bytes=:x'),{'x':b'altered'})
        db.commit()
        assert db.info.get('v2_first_response_test_guard') is None
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.read(db,s['workspace'],s['call'],s['intent'])
        db.rollback()
