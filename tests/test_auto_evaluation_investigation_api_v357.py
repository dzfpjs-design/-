"""API permission, read-only boundary, and honest evidence labelling.
The receiver ACK is real; a synthetic origin deliberately cannot prove success.
"""
from uuid import uuid4
from fastapi import HTTPException
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Task,Invocation,Audit
from generic_agents.security import Principal
from generic_agents.worker import process_once
from generic_agents.auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356 as Witness
from generic_agents.auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356 as Trigger
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from generic_agents import auto_evaluation_investigation_api_v357 as api
from tests.test_auto_evaluation_ack_receiver_v356 import prepare


def scene(client,account,monkeypatch,origin=False):
    s=prepare(client,account,monkeypatch,True)
    assert process_once(s.job,deliver=False)
    with Session(engine()) as db:
        w=db.scalar(select(Witness).where(Witness.task_id==s.task));assert w
        reference=w.id
        if origin:
            task=db.get(Task,s.task);nonce=str(uuid4())
            job=Job(workspace_id=s.p.workspace_id,kind='append_goal_evaluation_v297',status='succeeded',idempotency_key='synthetic-'+nonce,data={});db.add(job);db.flush()
            command=Command(workspace_id=s.p.workspace_id,task_id=s.task,actor_id=s.p.user_id,goal_version=1,goal_hash='a'*64,request_id=nonce,body_hash='b'*64,capability_id=task.package_id,capability_hash='c'*64,input_hash='d'*64,job_id=job.id,invocation_key='synthetic-'+nonce,record_hash='e'*64,version=1,data={'synthetic':True});db.add(command);db.flush()
            db.add(Trigger(workspace_id=s.p.workspace_id,task_id=s.task,event_id=w.event_id,observation_id=w.observation_id,policy_id=w.policy_id,policy_version=w.policy_version,command_id=command.id,job_id=job.id,data={'witness_id':w.id}));db.commit()
    return s,reference


def test_missing_original_or_independent_audit_is_unverifiable_no_effects(client,account,monkeypatch):
    s,reference=scene(client,account,monkeypatch,True)
    from generic_agents import gateway
    monkeypatch.setattr(gateway,'invoke',lambda *a,**k:pytest.fail('must not invoke'))
    with Session(engine()) as db:
        before=tuple(db.scalar(select(func.count()).select_from(t)) for t in (Job,Invocation,Audit))
    response=client.get('/api/workspace/tasks/'+s.task+'/auto-evaluation-policy/investigations/'+reference)
    assert response.status_code==200,response.text
    assert response.json()['status']=='unverifiable' and response.json()['proof_level']=='unverifiable'
    assert '未执行恢复或新调用' in response.json()['message']
    with Session(engine()) as db:
        assert tuple(db.scalar(select(func.count()).select_from(t)) for t in (Job,Invocation,Audit))==before


def test_record_only_never_claims_success_and_uses_own_read_only_snapshot(client,account,monkeypatch):
    s,reference=scene(client,account,monkeypatch,True)
    def reported(db,p,task,job):
        assert db.scalar(text('SHOW transaction_read_only'))=='on'
        assert db.scalar(text('SHOW transaction_isolation'))=='repeatable read'
        return {'status':'requires_review','invocation_state':'succeeded','evaluation_record_present':True,'message':'test'}
    monkeypatch.setattr(api,'investigate',reported)
    r=client.get('/api/workspace/tasks/'+s.task+'/auto-evaluation-policy/investigations/'+reference)
    assert r.status_code==200,r.text
    assert r.json()['proof_level']=='record_only' and '尚未独立核验' in r.json()['message']
    assert '不能据此认定完成或允许重试' in r.json()['message']


def test_cross_task_and_viewer_cannot_inspect(client,account,monkeypatch):
    s,reference=scene(client,account,monkeypatch,False)
    from tests.test_native_goals import setup
    _,_,other,_,_=setup(client)
    r=client.get('/api/workspace/tasks/'+other['id']+'/auto-evaluation-policy/investigations/'+reference)
    assert r.status_code==404,r.text
    with pytest.raises(HTTPException):api.inspect_original(s.task,reference,Principal(s.p.user_id,s.p.workspace_id,'viewer','test'))
