"""Real isolated PG and local fake HTTP; no production credentials/model."""
import json,os
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit,AuthSession,CloudCall,Job
from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as subject
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup, synthetic_source_expiry
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated fake provider only')


def test_first_answer_settles_once_and_recovers_after_restart(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args)
        assert first['state']=='captured' and len(server.requests)==1
        p,_,_,_,_,call_id=args;intent_id=first['intent_audit_id']
        engine().dispose()
        settled=subject.reconcile(p.workspace_id,call_id,intent_id)
        assert settled=={'state':'settled','answer_state':'succeeded','usage_unavailable':False,
            'retry_authorized':False,'business_result_verified':False}
        engine().dispose()
        assert subject.reconcile(p.workspace_id,call_id,intent_id)==settled
        with Session(engine()) as db:
            output=subject.read_result(db,p,call_id,intent_id)
            assert output['model_value']==opinion and output['business_result_verified'] is False
            from generic_agents.security import Principal
            spoof=Principal(p.user_id,p.workspace_id,p.role,'worker-internal-marker')
            with pytest.raises(HTTPException):subject.read_result(db,spoof,call_id,intent_id)
            row=db.get(CloudCall,call_id)
            daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert row.charged_tokens==5 and daily.data['tokens']==5 and daily.data['calls']==1
        assert len(server.requests)==1
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_missing_usage_is_conservatively_retained(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion)) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        result=subject.reconcile(p.workspace_id,call_id,first['intent_audit_id'])
        assert result['usage_unavailable'] is True and result['answer_state']=='succeeded'
        with Session(engine()) as db:
            row=db.get(CloudCall,call_id);assert row.charged_tokens==row.reserved_tokens>0
        assert len(server.requests)==1


def test_non_200_settles_failed_without_retry(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='http_error',content=json.dumps(opinion)) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        result=subject.reconcile(p.workspace_id,call_id,first['intent_audit_id'])
        assert result['answer_state']=='failed' and result['usage_unavailable'] is True
        with Session(engine()) as db:
            row=db.get(CloudCall,call_id);assert row.status=='failed' and row.charged_tokens==row.reserved_tokens
        assert len(server.requests)==1


def test_settlement_survives_revocation_but_user_read_does_not(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        with Session(engine()) as db:db.get(AuthSession,p.session_id).revoked=True;db.commit()
        assert subject.reconcile(p.workspace_id,call_id,first['intent_audit_id'])['state']=='settled'
        with Session(engine()) as db:
            with pytest.raises(HTTPException):subject.read_result(db,p,call_id,first['intent_audit_id'])
        assert len(server.requests)==1
