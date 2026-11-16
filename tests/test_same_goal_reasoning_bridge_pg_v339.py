"""Real PostgreSQL persistence and tampering tests for the advice link."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import graph_reasoning_retirement_v337 as retirement
from generic_agents import same_goal_reasoning_bridge_v339 as bridge
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_graph_reasoning_retirement_pg_v337 import scene, request, call


def make_plan(s):
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task)
        task.data={**task.data,'goal_hash':'e'*64,'current_goal_evaluation_v288':{'head_hash':s.ids.head}}
        body={'request_id':str(uuid4()),'reason':'prepare the new evaluation'}
        prep={'requested_by':s.ids.actor,'request':body,'request_hash':digest(body),'record_hash':'f'*64,
              'scope':{'task_id':task.id,'goal_hash':'e'*64},'mature_head':{'head_hash':s.ids.head},
              'model_pin':{'id':s.ids.model}}
        plan=m.Plan(workspace_id=s.ids.workspace,task_id=task.id,status='queued',data={})
        job=m.Job(workspace_id=s.ids.workspace,kind='synthetic-prepare',status='queued',attempts=0,
                  idempotency_key=str(uuid4()),data={})
        db.add_all([plan,job]);db.flush()
        plan_id,job_id=plan.id,job.id
        db.commit()
    return plan_id,job_id,prep


def create_link(s,plan_id,job_id,prep):
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task,with_for_update=True)
        record=bridge.create(db,s.p,task,db.get(m.Plan,plan_id),prep,db.get(m.Job,job_id))
        db.commit();return record


def test_link_and_original_node_survive_a_new_session_and_validate_before_send(scene):
    s=scene;old=request(s);call(s,old);plan,job,prep=make_plan(s)
    link=create_link(s,plan,job,prep)
    assert link['retirement_request_id']==old['request_id'] and link['head_hash']==s.ids.head
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task)
        assert bridge.inspect(db,s.p,task,db.get(m.Plan,plan),prep,db.get(m.Job,job),for_send=True)==link
        assert db.get(m.Job,s.ids.job).status=='cancelled'
        assert task.data['graph_runtime']['activations'][s.ids.activation]['inputs_snapshot']['arguments']==s.ids.inputs


@pytest.mark.parametrize('damage',['link','link_audit','retirement_audit','old_job','dispatch'])
def test_missing_or_changed_proof_never_authorizes_a_new_send(scene,damage):
    s=scene;call(s,request(s));plan,job,prep=make_plan(s);link=create_link(s,plan,job,prep)
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task)
        if damage=='link':
            data=deepcopy(task.data);del data[bridge.KEY][plan];task.data=data
        elif damage=='link_audit':db.get(m.Audit,link['audit_id']).data={'tampered':True}
        elif damage=='retirement_audit':
            record=task.data[retirement.KEY][link['retirement_request_id']]
            db.get(m.Audit,record['audit_id']).data={'tampered':True}
        elif damage=='old_job':db.get(m.Job,s.ids.job).status='queued'
        else:
            db.add(m.Invocation(workspace_id=s.ids.workspace,capability_id=s.ids.cap,idempotency_key=str(uuid4()),
                                status='running',data={'job_id':s.ids.job}))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            bridge.inspect(db,s.p,db.get(m.Task,s.ids.task),db.get(m.Plan,plan),prep,db.get(m.Job,job),for_send=True)


def test_link_failure_rolls_back_pointer_and_audit(scene):
    s=scene;call(s,request(s));plan,job,prep=make_plan(s)
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task,with_for_update=True)
        bridge.create(db,s.p,task,db.get(m.Plan,plan),prep,db.get(m.Job,job));db.rollback()
    with Session(engine()) as db:
        assert bridge.KEY not in db.get(m.Task,s.ids.task).data
        with pytest.raises(HTTPException):
            bridge.inspect(db,s.p,db.get(m.Task,s.ids.task),db.get(m.Plan,plan),prep,db.get(m.Job,job),for_send=True)


def test_a_second_link_cannot_be_written_for_the_same_plan(scene):
    s=scene;call(s,request(s));plan,job,prep=make_plan(s);create_link(s,plan,job,prep)
    with pytest.raises(HTTPException):create_link(s,plan,job,prep)


def test_another_active_link_blocks_a_second_plan(scene,monkeypatch):
    from generic_agents import same_goal_advice_store_v302 as store
    s=scene;call(s,request(s));first,first_job,prep=make_plan(s);create_link(s,first,first_job,prep)
    second,second_job,next_prep=make_plan(s)
    monkeypatch.setattr(store,'_saved',lambda db,p,task,plan:(prep,db.get(m.Job,first_job)))
    with pytest.raises(HTTPException):create_link(s,second,second_job,next_prep)
    with Session(engine()) as db:assert second not in db.get(m.Task,s.ids.task).data[bridge.KEY]


def test_old_unlinked_plan_remains_readable_but_cannot_start_after_retirement(scene):
    s=scene;plan,job,prep=make_plan(s);call(s,request(s))
    with Session(engine()) as db:
        task=db.get(m.Task,s.ids.task);old=db.get(m.Plan,plan);prepare_job=db.get(m.Job,job)
        assert bridge.inspect(db,s.p,task,old,prep,prepare_job) is None
        with pytest.raises(HTTPException):bridge.inspect(db,s.p,task,old,prep,prepare_job,for_send=True)


def test_task_lock_serializes_competing_link_writers(scene):
    s=scene;call(s,request(s));plan,job,prep=make_plan(s)
    with Session(engine()) as first, Session(engine()) as second:
        task=first.get(m.Task,s.ids.task,with_for_update=True)
        bridge.create(first,s.p,task,first.get(m.Plan,plan),prep,first.get(m.Job,job))
        second.execute(text("SET LOCAL lock_timeout = '500ms'"))
        with pytest.raises(OperationalError):
            second.scalar(select(m.Task).where(m.Task.id==s.ids.task).with_for_update())
        second.rollback();first.commit()
    with pytest.raises(HTTPException):create_link(s,plan,job,prep)
