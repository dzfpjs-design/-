"""Real PostgreSQL budget/locks/API negatives, without an accepted-source fake.

The reserved budget comes from real reserve(), not a provider receipt. These
tests do not manufacture a successful source Consumer or a paid response.
Root separately accepts the complete end path on H's actual interrupted call.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, cloud_budget
from generic_agents import report_business_catalogue_allocation_end_v213 as subject
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene


def bound():
    value=engine();assert value.dialect.name=='postgresql' and value.url.database.startswith('ga_test_');return value


def budget_only_cap(s):
    data={'budget_arithmetic_only':True,'no_provider_request':True}
    with Session(bound()) as db:
        cap=m.Capability(id=str(uuid4()),workspace_id=s.workspace,namespace='test-budget-only',name=str(uuid4()),
            release_version='1',kind='foundation_model',status='draft',content_hash=digest(data),data=data)
        db.add(cap);db.commit();return NS(id=cap.id,workspace_id=s.workspace)


def read(call_id):
    with Session(bound()) as db:
        call=db.get(m.CloudCall,call_id);budget=db.scalar(select(m.Job).where(m.Job.workspace_id==call.workspace_id,
            m.Job.idempotency_key=='cloud-budget:'+call.data['day']))
        return copy.deepcopy((call.status,call.reserved_tokens,call.charged_tokens,call.data,budget.data))


def test_finish_tx_rollback_then_commit_keeps_the_reserved_budget_and_does_not_open_a_second_session(scene,monkeypatch):
    # The isolated runner intentionally denies all cloud calls. Override only
    # this arithmetic module's local view for two real reservations totalling
    # 22 tokens; credentials, HTTP and all global settings remain untouched.
    arithmetic=cloud_budget.settings().model_copy(update={'cloud_daily_call_limit':2,'cloud_daily_token_limit':22})
    monkeypatch.setattr(cloud_budget,'settings',lambda:arithmetic)
    s=scene;cap=budget_only_cap(s);call_id=cloud_budget.reserve(s.p,cap,17,'a'*64);before=read(call_id)
    with Session(bound()) as db:
        assert cloud_budget.finish_tx(db,call_id,None,'failed') is True
        db.rollback()
    assert read(call_id)==before
    with Session(bound()) as db:
        assert cloud_budget.finish_tx(db,call_id,None,'failed') is True;db.commit()
    after=read(call_id);assert after[:3]==('failed',17,17);assert after[4]==before[4];assert after[3]['receipt']=={}
    with Session(bound()) as db:
        assert cloud_budget.finish_tx(db,call_id,{'usage':{'total_tokens':0}},'succeeded') is False;db.commit()
    assert read(call_id)==after,'a later finish cannot refund or rewrite the old terminal call'
    cloud_budget.reserve(s.p,cap,5,'b'*64)
    assert read(call_id)[:4]==after[:4] and read(call_id)[4]['tokens']==after[4]['tokens']+5


def test_original_job_nowait_rejects_an_exclusive_worker_lock_and_rechecks_database_clock(scene):
    s=scene;identifier=str(uuid4());row=NS(id=str(uuid4()),job_id=identifier,request_id=str(uuid4()))
    with Session(bound()) as db:
        clock=db.scalar(select(func.clock_timestamp()));job=m.Job(id=identifier,workspace_id=s.workspace,kind='read_saved_map_design_v176',
            status='running',idempotency_key='saved-map-design:'+row.request_id,lease_token=str(uuid4()),lease_until=clock+timedelta(minutes=1),
            data={'actor_id':s.p.user_id,'consumer_id':row.id,'generation_profile':'catalogue-allocation-v213'})
        db.add(job);db.commit()
    with Session(bound()) as owner,Session(bound()) as other:
        owner.scalar(select(m.Job).where(m.Job.id==identifier).with_for_update())
        with pytest.raises(HTTPException) as error:subject._job(other,s.p,row,lock=True)
        assert error.value.status_code==409;other.rollback()
    with Session(bound()) as db:
        job=subject._job(db,s.p,row,lock=True);clock=db.scalar(select(func.clock_timestamp()))
        assert subject._stopped(job,clock) is False
        job.lease_until=clock-timedelta(seconds=1);db.flush()
        assert subject._stopped(job,db.scalar(select(func.clock_timestamp()))) is True;db.rollback()


def test_unknown_end_handler_and_nonce_never_construct_a_source_or_a_budget(scene):
    s=scene;nonce=str(uuid4());body={'request_id':nonce,'expected_version':1,'confirm':True,'retain_reserved_budget':True}
    with Session(bound()) as db:
        before=tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace))
            for model in (m.CloudCall,m.Job,m.Audit))
    response=s.client.post(subject.PREFIX+'/'+str(uuid4())+'/end-interrupted',json=body)
    assert response.status_code==404 and response.json().get('detail')!='Not Found'
    original=s.client.get(subject.PREFIX+'/requests/'+nonce,params={'expected_sha256':'a'*64})
    assert original.status_code==200 and original.json()['found'] is False
    invalid=s.client.post(subject.PREFIX+'/'+str(uuid4())+'/end-interrupted',json={**body,'retain_reserved_budget':False})
    assert invalid.status_code==422
    with Session(bound()) as db:
        after=tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace))
            for model in (m.CloudCall,m.Job,m.Audit))
    assert before==after
