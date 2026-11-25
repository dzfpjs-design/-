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
    return {'actor_id':intent.actor_id,'anchor_hash':digest({'call':c,'intent':i}),'provider':'deepseek','model':'synthetic-model'}


def receipt(s):
    return {'call_id':s['call'],'provider':'deepseek','model':'synthetic-model','http_status':200}


def test_missing_original_claim_fails_closed(synthetic):
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


def test_header_whitelist_keeps_only_safe_metadata_out_of_audit(synthetic,monkeypatch):
    import base64
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    encoded=lambda value:base64.b64encode(value).decode('ascii')
    selected={**receipt(s),'request_id':'request-123',
        'header_raw_base64':[[encoded(b'content-type'),encoded(b'application/json')],
            [encoded(b'content-encoding'),encoded(b'gzip')],
            [encoded(b'x-request-id'),encoded(b'request-123')]]}
    with Session(engine()) as db:
        saved=subject.capture(db,s['workspace'],s['call'],s['intent'],b'compressed',selected);db.commit()
    with Session(engine()) as db:
        assert subject.read(db,s['workspace'],s['call'],s['intent'])==saved
        audit=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
        assert 'SYNTHETIC_COOKIE_SENTINEL' not in str(audit.data)


@pytest.mark.parametrize('kind',['cookie_header','arbitrary_receipt','request_id'])
def test_sensitive_receipt_cannot_be_persisted(synthetic,monkeypatch,kind):
    import base64
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    encode=lambda value:base64.b64encode(value).decode('ascii')
    bad=receipt(s)
    if kind=='cookie_header':
        bad['header_raw_base64']=[[encode(b'set-cookie'),encode(b'SYNTHETIC_COOKIE_SENTINEL')]]
    elif kind=='arbitrary_receipt':bad['cookie']='SYNTHETIC_COOKIE_SENTINEL'
    else:bad['request_id']='cookie=SYNTHETIC_COOKIE_SENTINEL;'
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',bad)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(__import__('sqlalchemy').func.count()).select_from(Audit).where(Audit.action==subject.ACTION))==0
        assert not db.scalar(text('SELECT EXISTS(SELECT 1 FROM ga_test_advice_v2_first_response)'))


def test_historical_unsafe_receipt_rejects_without_rewriting(synthetic,monkeypatch):
    import base64
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        saved=subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s));db.commit()
    with Session(engine()) as db:
        audit=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
        original=copy.deepcopy(audit.data);tampered=copy.deepcopy(original)
        encode=lambda value:base64.b64encode(value).decode('ascii')
        tampered['receipt']['header_raw_base64']=[[encode(b'set-cookie'),encode(b'SYNTHETIC_COOKIE_SENTINEL')]]
        tampered['record_hash']=digest({key:value for key,value in tampered.items() if key!='record_hash'})
        audit.data=tampered;db.flush()
        with pytest.raises(HTTPException):subject.read(db,s['workspace'],s['call'],s['intent'])
        assert audit.data==tampered
        db.rollback()
    with Session(engine()) as db:assert subject.read(db,s['workspace'],s['call'],s['intent'])==saved


def test_permit_local_absence_check_neither_authorizes_nor_accepts_existing_or_orphan(synthetic,monkeypatch):
    monkeypatch.setattr(gate,'check_original',fake_gate);s=synthetic
    with Session(engine()) as db:
        assert subject.check_no_local_response_for_permit(db,s['workspace'],s['call'],s['intent']) is None
        db.rollback()
    with Session(engine()) as db:
        subject.capture(db,s['workspace'],s['call'],s['intent'],b'answer',receipt(s));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.check_no_local_response_for_permit(db,s['workspace'],s['call'],s['intent'])
        db.rollback()
    with Session(engine()) as db:
        db.execute(text('DELETE FROM ga_test_advice_v2_first_response'));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.check_no_local_response_for_permit(db,s['workspace'],s['call'],s['intent'])
        db.rollback()
