"""Synthetic substitute Goal/source, actual test-only reservation and intent services.

These tests cannot prove that a real HTTP request was attempted, or that a
real approval was granted. No live identity or cloud network is used.
"""
import copy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, Plan, User, uid
from generic_agents.contracts import digest
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reserve
from generic_agents import same_goal_advice_response_intent_gate_candidate_v2 as gate
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as transport
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from tests.test_advice_v2_cloud_reservation_candidate_pg import started, _reserve
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def recorded(started):
    s,created,owned=started;p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("select to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
    with Session(engine()) as db:
        reserved=_reserve(db,started);db.commit()
    with Session(engine()) as db:
        issued=transport.issue(db,p,s['task_id'],created['plan_id'],owned['job_id'],
            owned['lease_token'],reserved['cloud_call_id']);db.commit()
    with Session(engine()) as db:
        audit=db.get(Audit,reserved['reservation_audit_id']);r=audit.data
        pv=db.get(Audit,r['preview_ref']['id']);model=pv.data['model_snapshot']['execution']['model']
        return {'workspace':p.workspace_id,'actor':p.user_id,'plan':created['plan_id'],
            'job':owned['job_id'],'call':reserved['cloud_call_id'],'intent':issued['intent_audit_id'],
            'reservation':audit.id,'model':model}


def receipt(s):return {'call_id':s['call'],'provider':'deepseek','model':s['model'],'http_status':200}


def test_historical_capture_survives_logout_role_revocation_and_expired_lease(recorded):
    s=recorded
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==s['workspace'],Membership.user_id==s['actor']))
        member.role='viewer';db.get(User,s['actor']).active=False
        job=db.get(Job,s['job']);job.lease_token=None;job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        db.commit()
    raw=b'\x00\xfffirst-response'
    with Session(engine()) as db:
        saved=storage.capture(db,s['workspace'],s['call'],s['intent'],raw,receipt(s));db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert storage.read(db,s['workspace'],s['call'],s['intent'])==saved
        assert saved[0]==raw
        assert storage.capture(db,s['workspace'],s['call'],s['intent'],raw,receipt(s))==saved
        db.rollback()


@pytest.mark.parametrize('kind',['workspace','wrong_model','intent_tamper','claim_missing','duplicate_intent'])
def test_historical_gate_rejects_mismatched_originals(recorded,kind):
    s=recorded
    if kind=='workspace':
        with Session(engine()) as db:
            with pytest.raises(HTTPException):storage.capture(db,uid(),s['call'],s['intent'],b'a',receipt(s))
            db.rollback()
        return
    if kind=='wrong_model':
        bad={**receipt(s),'model':'other-model'}
        with Session(engine()) as db:
            with pytest.raises(HTTPException):storage.capture(db,s['workspace'],s['call'],s['intent'],b'a',bad)
            db.rollback()
        return
    with Session(engine()) as db:
        if kind=='intent_tamper':
            item=db.get(Audit,s['intent']);item.data={**item.data,'token_bound':item.data['token_bound']+1}
        elif kind=='claim_missing':
            db.execute(text('DELETE FROM ga_test_advice_v2_dispatch_claims WHERE cloud_call_id=:id'),{'id':s['call']})
        else:
            original=db.get(Audit,s['intent']);db.add(Audit(workspace_id=s['workspace'],actor_id=s['actor'],
                action=gate.ACTION,object_id=s['plan'],data=copy.deepcopy(original.data)))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):storage.capture(db,s['workspace'],s['call'],s['intent'],b'a',receipt(s))
        db.rollback()


def test_original_bytes_to_settlement_and_cold_recovery(recorded):
    import json
    from generic_agents import cloud_budget
    from generic_agents import same_goal_advice_first_response_candidate_v2 as response
    s=recorded
    raw=json.dumps({'id':'synthetic-only','choices':[{'finish_reason':'stop',
        'message':{'content':'{"advice":"verified local test"}'}}],
        'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}).encode()
    with Session(engine()) as db:
        saved=storage.capture(db,s['workspace'],s['call'],s['intent'],raw,receipt(s));db.commit()
    engine().dispose()
    value, proven=response.parse_saved(saved[2],cloud_call_id=s['call'],intent_audit_id=s['intent'],schema={'type':'object'})
    assert value['advice']=='verified local test' and proven['usage']['total_tokens']==5
    with Session(engine()) as db:
        original=db.get(CloudCall,s['call']);anchor=original.data[reserve.KEY]
        expected={'workspace_id':s['workspace'],'capability_id':original.capability_id,'actor_id':s['actor'],
            'request_hash':anchor['request_hash'],'dispatch_options_hash':anchor['dispatch_options_hash'],
            'provider':'deepseek','model':s['model']}
        cloud_budget.settle_paid_response(db,s['call'],proven,expected,'succeeded');db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert storage.read(db,s['workspace'],s['call'],s['intent'])==saved
        assert db.get(CloudCall,s['call']).charged_tokens==5
        cloud_budget.settle_paid_response(db,s['call'],proven,expected,'succeeded');db.commit()
    with Session(engine()) as db:
        assert db.get(CloudCall,s['call']).charged_tokens==5
        ledger=db.scalar(select(Job).where(Job.workspace_id==s['workspace'],Job.kind=='cloud_budget'))
        assert ledger.data['tokens']==5 and ledger.data['calls']==1
