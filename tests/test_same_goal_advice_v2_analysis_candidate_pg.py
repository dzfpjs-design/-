"""Synthetic-v2 auth boundaries on real PG; Goal/frame sources are substitutes.

No preview, source approval, paid authority, worker or cloud integration is
proved by this test. The fixed document receipt and policy rows are real in
this disposable DB, but original Goal and frame IO are deliberately stubbed.
"""
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import same_goal_advice_unlimited_analysis_candidate_v2 as subject
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as documents
from generic_agents import same_goal_advice_quota_v304 as quota
from generic_agents import native_step_execution as native_steps
from generic_agents import native_goal_queries as native_queries
from generic_agents import same_goal_continuation_execution_budget_v314 as continuation
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Invocation, Membership, Plan, Task, User
from generic_agents.security import Principal
from tests.test_same_goal_advice_prepare_document_candidate_v2_pg import ready
from tests.test_same_goal_advice_prepare_writer_candidate_v2_pg import empty_original
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_goal_budget_versioned_snapshot_candidate_v3_pg import simplified_other_goal_components, seed_goal


def patch_context(monkeypatch, s):
    prep=s['prep']; budget=prep['budget']; p=s['principal']
    def cap(pin,data=None):return SimpleNamespace(id=pin['id'],content_hash=pin['content_hash'],
        release_version=pin['release_version'],data=data or {})
    source=cap(prep['source_business_capability_ref'],{'specification':{'instruction':prep['instruction']}})
    context={'scope':{k:v for k,v in prep['scope'].items() if k!='analysis_plan_id'}, 'source':source,
        'advice':cap(prep['advice_capability_ref']),
        'model':cap(prep['model_pin'],{'execution':{'provider':'deepseek','max_tokens':budget['max_tokens']}}),
        'goal':{'spec':{'max_tool_calls':budget['goal_max_tool_calls'],'max_steps':budget['goal_max_steps']}}}
    monkeypatch.setattr(quota,'_configuration_base',lambda *_:context)
    monkeypatch.setattr(native_steps,'step_count',lambda *_:1);monkeypatch.setattr(native_queries,'step_count',lambda *_:1)
    monkeypatch.setattr(continuation,'step_count',lambda *_:1)
    nodes=[{'id':'reasoning','actor_id':p.user_id,'execution':{'capability_id':source.id}},
           {'id':'packet','actor_id':p.user_id}]
    monkeypatch.setattr(subject.analysis.provider,'binding',lambda *_:{'binding':{'graph':{'nodes':nodes}}})
    monkeypatch.setattr(subject.analysis.provider,'_q0',lambda *_:'original-test-q0')
    monkeypatch.setattr(subject.analysis.queries,'query_ref',lambda *_:prep['original_query_ref'])
    calls=[]
    def verified(_db,_p,_task,_plan,actual,job,selected,doc,seal):
        calls.append(('originals_stub',doc['evidence_ref']['sha256']))
        assert seal is subject.contracts.seal_source and actual['record_hash']==prep['record_hash']
        return {'actual_original_evidence':doc['evidence_ref']['evidence_id']}
    monkeypatch.setattr(subject.analysis,'_verified_sources',verified)
    monkeypatch.setattr(subject.frames,'from_resolved',lambda _db,_p,_task,_plan,r: {'source_id':r['actual_original_evidence']})
    return calls


def prepared(ready):
    s, created, resolved=ready; p=s['principal']
    with Session(engine()) as db:documents.process(db,p,s['task_id'],created['job_id']);db.commit()
    seed_goal(s)
    return s,created,resolved


def test_versioned_frame_uses_saved_v2_document_without_cloud(ready,simplified_other_goal_components,monkeypatch):
    s,created,resolved=prepared(ready);calls=patch_context(monkeypatch,s);p=s['principal']
    with Session(engine()) as db:
        frame=subject.build_current(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
        assert calls==[('originals_stub',resolved['sha256'])] and frame['source_id']
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


def test_session_natural_expiry_during_frame_blocks_result(ready,simplified_other_goal_components,monkeypatch):
    s,created,_=prepared(ready);patch_context(monkeypatch,s);p=s['principal']
    with Session(engine()) as db:
        row=db.get(AuthSession,p.session_id);row.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=2);db.commit()
    def slow(_db,_p,_task,_plan,r):time.sleep(2.5);return {'source_id':r['actual_original_evidence']}
    monkeypatch.setattr(subject.frames,'from_resolved',slow)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.build_current(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


def test_other_active_operator_cannot_consume_business_actor_frame(ready,simplified_other_goal_components,monkeypatch):
    s,created,_=prepared(ready);calls=patch_context(monkeypatch,s)
    with Session(engine(),expire_on_commit=False) as db:
        user=User(username='other_'+str(uuid4()),password_hash='synthetic-only',active=True);db.add(user);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=s['principal'].workspace_id,role='operator'))
        session=AuthSession(user_id=user.id,token_hash='a'*64,csrf_hash='b'*64,
            expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5),revoked=False)
        db.add(session);db.flush();p=Principal(user.id,s['principal'].workspace_id,'operator',session.id);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.build_current(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
        assert not calls and not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))
