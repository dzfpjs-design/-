"""Isolated PG and one local fake HTTP request; no production credentials."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as old_reconcile
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup, synthetic_source_expiry
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated fake provider only')


def _install():
    with Session(engine()) as db:
        terminal.install(db);db.commit()


def test_atomic_terminal_survives_restart_and_does_not_authorize_repeat(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            assert proof['charged_tokens']==5 and proof['status']=='succeeded'
            assert not proof['retry_authorized'] and not proof['new_request_authorized']
        engine().dispose()
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
            assert terminal.settle_and_record(db,p.workspace_id,call_id,intent)==proof
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert (ledger.data['calls'],ledger.data['tokens'])==(1,5)
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==1
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_unknown_usage_keeps_reservation_and_never_mints_terminal(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion)) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,p.workspace_id,call_id,first['intent_audit_id'])
            db.rollback(); call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_existing_settled_record_cannot_be_retroactively_certified(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}):
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        old_reconcile.reconcile(p.workspace_id,call_id,intent)
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            db.rollback()
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))


def test_tampered_terminal_is_rejected_and_original_is_not_resent(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            record=db.get(Audit,proof['terminal_audit_id']);record.data={**record.data,'charged_tokens':4};db.commit()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.verify(db,p.workspace_id,call_id,intent)
        assert len(server.requests)==1


def test_rollback_and_commit_time_ledger_tamper_do_not_make_a_terminal(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            db.rollback()
        with Session(engine()) as db:
            call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
            terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            ledger.data={**ledger.data,'tokens':999999};db.flush()
            with pytest.raises(HTTPException):db.commit()
            db.rollback()
        with Session(engine()) as db:
            call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_two_concurrent_settlements_cannot_create_two_terminal_receipts(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        ready=Event();release=Event()
        def worker():
            with Session(engine()) as db:
                proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent)
                ready.set();assert release.wait(20);db.commit();return proof
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(worker);assert ready.wait(20)
            try:
                with Session(engine()) as db:
                    with pytest.raises((HTTPException,SQLAlchemyError)):
                        terminal.settle_and_record(db,p.workspace_id,call_id,intent)
                    db.rollback()
            finally:release.set()
            proof=future.result(timeout=20)
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert (ledger.data['calls'],ledger.data['tokens'])==(1,5)
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==1
        assert len(server.requests)==1


def test_cross_workspace_cannot_verify_or_settle_another_call(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,str(uuid4()),call_id,intent)
            db.rollback()
            assert db.get(CloudCall,call_id).status=='reserved'
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_failed_provider_response_with_known_usage_is_recorded_as_failure(started):
    _,args,_=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='http_error',usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            assert proof['status']=='failed' and proof['charged_tokens']==5
            assert not proof['new_request_authorized'] and not proof['retry_authorized']
        engine().dispose()
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
        assert len(server.requests)==1
