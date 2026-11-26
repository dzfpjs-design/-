"""Bounded paging over synthetic metadata; full original verification is substituted.

The separate two-request test exercises genuine terminal verification. These
1001 synthetic rows test page traversal/orphan detection, not 1001 paid calls.
"""
import os
from uuid import UUID, uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents import same_goal_advice_terminal_paging_test_v2 as paging
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Plan
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def many_metadata_rows(started,monkeypatch):
    s,_,_=started;p=s['principal'];task_id=s['task_id'];total=1001
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db)
        terminal.install(db);db.commit()
        plans=[];calls=[];events=[];links=[]
        for i in range(total):
            plan_id=str(UUID(int=100+i));call_id=str(UUID(int=2000+i))
            intent_id=str(UUID(int=4000+i));audit_id=str(UUID(int=6000+i))
            plans.append(Plan(id=plan_id,workspace_id=p.workspace_id,task_id=task_id,status='awaiting_dispatch',data={}))
            calls.append(CloudCall(id=call_id,workspace_id=p.workspace_id,capability_id=s['prep']['model_pin']['id'],
                status='succeeded',reserved_tokens=1,charged_tokens=1,data={'synthetic_index':i}))
            events.extend([Audit(id=intent_id,workspace_id=p.workspace_id,actor_id=p.user_id,
                object_id=plan_id,action='synthetic.intent',data={'synthetic_index':i}),
                Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,
                object_id=plan_id,action=terminal.ACTION,data={'task_id':task_id,'synthetic_index':i})])
            links.append({'w':p.workspace_id,'p':plan_id,'c':call_id,'i':intent_id,'a':audit_id})
        db.add_all(plans+calls+events);db.flush()
        db.execute(text('INSERT INTO public.ga_test_advice_v2_terminals '
            '(workspace_id,plan_id,call_id,intent_id,audit_id) VALUES (:w,:p,:c,:i,:a)'),links)
        db.commit()
    monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
    observed={'count':0,'last':None,'fail_last':False}
    def fake_verify(_db,workspace,call_id,intent_id):
        index=UUID(call_id).int-2000
        assert workspace==p.workspace_id and intent_id==str(UUID(int=4000+index))
        observed['count']+=1;observed['last']=index
        if observed['fail_last'] and index==total-1:raise HTTPException(409,'synthetic last-page corruption')
        return {'plan_id':str(UUID(int=100+index)),'terminal_audit_id':str(UUID(int=6000+index)),
            'retry_authorized':False,'new_request_authorized':False}
    monkeypatch.setattr(terminal,'verify',fake_verify)
    return p,task_id,total,observed


def test_keyset_scans_over_1000_and_checks_last_page(many_metadata_rows):
    p,task_id,total,observed=many_metadata_rows
    with Session(engine()) as db:
        result=paging.scan(db,p,task_id,page_size=64);db.rollback()
    assert result=={'verified_terminals':total,'pages':16,'max_page_rows':64,
        'new_request_authorized':False,'retry_authorized':False}
    assert observed['count']==total and observed['last']==total-1
    observed.update(count=0,last=None,fail_last=True)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):paging.scan(db,p,task_id,page_size=64)
        db.rollback()
    assert observed['count']==total and observed['last']==total-1


def test_orphan_terminal_audit_is_not_hidden_by_pagination(many_metadata_rows):
    p,task_id,_,_=many_metadata_rows
    with Session(engine()) as db:
        db.add(Audit(id=str(uuid4()),workspace_id=p.workspace_id,actor_id=p.user_id,
            object_id=str(uuid4()),action=terminal.ACTION,data={'task_id':task_id}));db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):paging.scan(db,p,task_id,page_size=64)
        db.rollback()


def test_repeatable_read_started_before_fence_is_not_silently_treated_as_current(many_metadata_rows):
    p,task_id,_,_=many_metadata_rows
    with Session(engine()) as db:
        db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
        with pytest.raises(HTTPException):paging.scan(db,p,task_id,page_size=64)
        db.rollback()


def test_noncooperative_audit_insert_cannot_enter_between_pages(many_metadata_rows,monkeypatch):
    p,task_id,total,_=many_metadata_rows;ready=Event();release=Event()
    original=terminal.verify
    def paused(*args,**kwargs):
        if not ready.is_set():
            ready.set();assert release.wait(15)
        return original(*args,**kwargs)
    monkeypatch.setattr(terminal,'verify',paused)
    def scan():
        with Session(engine()) as db:
            result=paging.scan(db,p,task_id,page_size=64);db.rollback();return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(scan);assert ready.wait(15)
        try:
            with Session(engine()) as db:
                db.execute(text("SET LOCAL statement_timeout = '500ms'"))
                db.add(Audit(id=str(uuid4()),workspace_id=p.workspace_id,actor_id=p.user_id,
                    object_id=str(uuid4()),action=terminal.ACTION,data={'task_id':task_id}))
                with pytest.raises(SQLAlchemyError):db.flush()
                db.rollback()
        finally:release.set()
        assert future.result(timeout=30)['verified_terminals']==total
