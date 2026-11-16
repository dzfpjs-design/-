"""PG selection tests; deliberately forged origins never prove a real evaluation."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Job,Event
from generic_agents import native_goal_scoped_worker_v355 as scoped
from generic_agents import auto_evaluation_policy_api_v356 as api
from generic_agents.auto_evaluation_policy_v356 import AutoEvaluationPolicyV356
from generic_agents.auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from tests.test_auto_evaluation_ack_receiver_v356 import prepare


def fake_origin(s):
    with Session(engine()) as db:
        task=db.get(Task,s.task);policy=db.scalar(select(AutoEvaluationPolicyV356).where(AutoEvaluationPolicyV356.task_id==task.id))
        event=db.scalar(select(Event).where(Event.idempotency_key=='data:'+s.identity))
        assert event is not None and policy is not None
        def job(key):
            row=Job(workspace_id=s.p.workspace_id,kind='append_goal_evaluation_v297',status='queued',idempotency_key=key,data={'task_id':task.id,'actor_id':s.p.user_id});db.add(row);db.flush();return row
        registered=job('test-registered:'+str(uuid4()));manual=job('test-manual:'+str(uuid4()));request=str(uuid4())
        command=Command(workspace_id=s.p.workspace_id,task_id=task.id,actor_id=s.p.user_id,goal_version=1,goal_hash='a'*64,request_id=request,body_hash='b'*64,capability_id=task.package_id,capability_hash='c'*64,input_hash='d'*64,job_id=registered.id,invocation_key='test-command:'+request,record_hash='e'*64,version=1,data={});db.add(command);db.flush()
        origin=AutoEvaluationTriggerV356(workspace_id=s.p.workspace_id,task_id=task.id,event_id=event.id,observation_id=s.identity,policy_id=policy.id,policy_version=policy.version,command_id=command.id,job_id=registered.id,data={});db.add(origin);db.commit()
        return registered.id,manual.id


def candidates(s):
    with Session(engine()) as db:
        task=db.get(Task,s.task)
        return [key for key,_ in scoped._rows(db,s.p.workspace_id,task,scoped._pin(task),None,None)]


def test_scope_never_selects_manual_append_and_gate_blocks_even_registered_origin(client,account,monkeypatch):
    from generic_agents.worker import process_once
    s=prepare(client,account,monkeypatch,True);assert process_once(s.job,deliver=False)
    registered,manual=fake_origin(s)
    monkeypatch.setattr(api,'automatic_runner_available',lambda:False)
    assert registered not in candidates(s) and manual not in candidates(s)
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    assert registered in candidates(s) and manual not in candidates(s)
    # The database row alone is forged: live proof must not accept or claim it.
    with Session(engine()) as db:
        with pytest.raises(HTTPException):scoped._prove(db,s.p.workspace_id,s.instance,s.task,registered)
        assert db.get(Job,registered).status=='queued' and db.get(Job,registered).attempts==0


def test_scanner_gate_off_never_calls_scanner(monkeypatch):
    from generic_agents import auto_evaluation_scanner_v356 as scan
    monkeypatch.setattr(api,'automatic_runner_available',lambda:False)
    monkeypatch.setattr(scan,'tick',lambda *a,**k:pytest.fail('disabled scanner must not run'))
    values=[str(uuid4()) for _ in range(3)]
    assert scoped.scan_once(*values)=={'queued':0,'blocked':0,'unknown':0,'unrecorded':0,'superseded':0,'after':None}


def test_loop_scans_only_original_task_and_preserves_independent_cursor(monkeypatch):
    from generic_agents import auto_evaluation_scanner_v356 as scan
    values=[str(uuid4()) for _ in range(3)];seen=[]
    class Stop:
        def __init__(self):self.done=False;self.waits=0
        def is_set(self):return self.done
        def set(self):self.done=True
        def wait(self,_):self.waits+=1;self.done=self.waits>=2
    monkeypatch.setattr(scoped.threading,'Event',Stop)
    monkeypatch.setattr(scoped.signal,'signal',lambda *args:None)
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    monkeypatch.setattr(scoped,'tick',lambda w,i,t,stop,after=None:{'processed':0,'blocked':0,'after':'goal-cursor'})
    def receive(w,i,t,after=None):
        seen.append((w,i,t,after));return {'queued':0,'blocked':0,'unknown':0,'unrecorded':0,'after':'scanner-cursor'}
    monkeypatch.setattr(scan,'tick',receive)
    assert scoped.run(*values)==0
    assert seen==[(*values,None),(*values,'scanner-cursor')]
