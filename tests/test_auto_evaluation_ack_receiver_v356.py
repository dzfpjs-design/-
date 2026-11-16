"""Real observation receiver + independent ACK; mature-goal context is stubbed.

This does not establish real append authority, goal maturity or model quality.
"""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Event,Job,Task,Membership,User,Observation
from generic_agents.security import Principal
from generic_agents.worker import process_once
from generic_agents import auto_evaluation_ack_witness_v356 as witness,auto_evaluation_policy_v356 as policy
from generic_agents.instance_data_receipts import observation_receipt
from tests.test_native_goals import setup,define
from tests.test_vertical_slice import manifest,publish


def prepare(client,account,monkeypatch,enabled):
    from generic_agents import auto_evaluation_policy_api_v356 as api
    monkeypatch.setattr(api,'automatic_runner_available',lambda:True)
    release,obj,task,base,body=setup(client,source='observations');define(client,base,body)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));user=db.get(User,member.user_id)
        p=Principal(user.id,account[2],member.role,'test')
    if enabled:
        context=NS() # The mature-evaluation binding is outside this receiver test.
        context={'selected':{'release':NS(id=str(uuid4()))},'goal':{'hash':'a'*64},
            'cap':NS(id=str(uuid4()),content_hash='b'*64)}
        monkeypatch.setattr(policy,'_local_context',lambda *_:context)
        with Session(engine()) as db:
            policy.change(db,p,task['id'],'enable',{'request_id':str(uuid4()),'expected_version':0,
                'reason':'真实接收事务隔离测试','min_interval_minutes':1,'max_evaluations_per_day':2});db.commit()
    field=body['goal_spec']['required_fields'][0]
    source=publish(client,manifest('source',name='ack_source_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    rule=publish(client,manifest('tool',name='ack_rule_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'测试数值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(seconds=1)
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json={
        'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':rule['id'],
        'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual',
        'attributes':{'测试数值':1,field:obj['attributes'][field]}})
    assert response.status_code==201,response.text
    identity=response.json()['id']
    with Session(engine()) as db:
        job_id=db.scalar(select(Job.id).where(Job.idempotency_key=='extract:'+identity))
    return NS(p=p,task=task['id'],identity=identity,job=job_id,instance=release['instance_id'])


def prove_ack(s):
    with Session(engine()) as db:
        obs=db.get(Observation,s.identity)
        event=db.scalar(select(Event).where(Event.workspace_id==s.p.workspace_id,Event.kind=='observation.data',Event.data['observation_id'].as_string()==obs.id))
        assert event.status=='received' and observation_receipt(db,s.p,obs)['data_reception']
        return event.id


def test_receiver_without_policy_keeps_original_ack(client,account,monkeypatch):
    s=prepare(client,account,monkeypatch,False);assert process_once(s.job,deliver=False);prove_ack(s)
    with Session(engine()) as db:
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert not db.scalars(select(witness.AutoEvaluationAckFailureV356)).all()


def test_receiver_signs_original_independent_ack_for_enabled_policy(client,account,monkeypatch):
    s=prepare(client,account,monkeypatch,True);assert process_once(s.job,deliver=False);event_id=prove_ack(s)
    with Session(engine()) as db:
        records=db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert len(records)==1 and records[0].event_id==event_id and records[0].task_id==s.task
        assert not db.scalars(select(witness.AutoEvaluationAckFailureV356)).all()


def test_receiver_saves_lock_contention_and_does_not_block_ack(client,account,monkeypatch):
    s=prepare(client,account,monkeypatch,True)
    with Session(engine()) as locked:
        locked.scalar(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.task_id==s.task).with_for_update())
        assert process_once(s.job,deliver=False)
        event_id=prove_ack(s)
        with Session(engine()) as db:
            assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
            failed=db.scalar(select(witness.AutoEvaluationAckFailureV356));assert failed and failed.event_id==event_id
            assert failed.data['code']=='lock_contended'
        locked.rollback()


def test_paused_instance_ack_records_restriction_and_never_signs(client,account,monkeypatch):
    from generic_agents import worker
    from generic_agents.models import Audit,Memory
    from fastapi import HTTPException
    s=prepare(client,account,monkeypatch,True)
    with monkeypatch.context() as patch:
        patch.setattr(worker,'deliver_events',lambda *a,**k:None)
        assert process_once(s.job,deliver=False)
    instance=next(x for x in client.get('/api/instances').json() if x['id']==s.instance)
    response=client.post('/api/instances/'+s.instance+'/pause',json={'version':instance['version']})
    assert response.status_code==200,response.text
    worker.deliver_events(workspace_id=account[2],observation_id=s.identity,event_kind='observation.data')
    event_id=prove_ack(s)
    with Session(engine()) as db:
        event=db.get(Event,event_id);assert event.data['derivation_receipt']['code']=='instance_not_active'
        assert db.scalar(select(Audit.id).where(Audit.object_id==event_id,Audit.action=='observation.derivation_skipped'))
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert db.scalar(select(witness.AutoEvaluationAckFailureV356)).data['code']=='derivation_restricted'
        with pytest.raises(HTTPException):witness.require_unrestricted(db,s.p,event)
        data=dict(event.data);data.pop('derivation_receipt');event.data=data
        for memory in db.scalars(select(Memory).where(Memory.data['event_id'].as_string()==event_id)):
            md=dict(memory.data);md.pop('derivation_receipt',None);memory.data=md
        db.flush()
        with pytest.raises(HTTPException):witness.require_unrestricted(db,s.p,event)
        db.rollback()


def test_membership_lookup_failure_keeps_real_ack_and_failure_record(client,account,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    s=prepare(client,account,monkeypatch,True);original=Session.scalar;fired=[]
    def fail_once(db,statement,*args,**kwargs):
        if db.info.get('auto_eval_ack_scope_v356') and 'memberships' in str(statement) and not fired:
            fired.append(True);raise SQLAlchemyError('injected receiver check failure')
        return original(db,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'scalar',fail_once)
    assert process_once(s.job,deliver=False);prove_ack(s);assert fired
    with Session(engine()) as db:
        assert db.scalar(select(witness.AutoEvaluationAckFailureV356)).data['code']=='unexpected_error'
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()


def test_too_many_candidate_tasks_preserves_ack_and_exposes_uncertain_event(client,account,monkeypatch):
    import copy
    s=prepare(client,account,monkeypatch,True)
    with Session(engine()) as db:
        original=db.get(Task,s.task);cfg=db.scalar(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.task_id==s.task))
        for n in range(8):
            task=Task(workspace_id=original.workspace_id,title='candidate '+str(n),owner_id=original.owner_id,
                package_id=original.package_id,status=original.status,data=copy.deepcopy(original.data))
            db.add(task);db.flush()
            # Selection overload test; these rows are intentionally not accepted
            # as valid authorization and must never be signed.
            db.add(policy.AutoEvaluationPolicyV356(workspace_id=original.workspace_id,task_id=task.id,
                status='enabled',version=1,data=copy.deepcopy(cfg.data)))
        db.commit()
    assert process_once(s.job,deliver=False);event_id=prove_ack(s)
    with Session(engine()) as db:
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        row=db.scalar(select(witness.AutoEvaluationAckOverflowV356));assert row and row.event_id==event_id
        assert row.data['code']=='too_many_policies'
        summary=witness.task_summary(db,s.p,s.task)
        assert any(x['event_id']==event_id and x['status']=='possibly_not_captured' for x in summary['records'])


def test_exhausted_batch_budget_keeps_real_ack_and_batches_uncertain_events(client,account,monkeypatch):
    from generic_agents import worker
    first=prepare(client,account,monkeypatch,False);second=prepare(client,account,monkeypatch,False)
    with monkeypatch.context() as patch:
        patch.setattr(worker,'deliver_events',lambda *a,**k:None)
        assert process_once(first.job,deliver=False) and process_once(second.job,deliver=False)
    original=witness.capture_enabled_for_ack
    def exhaust(db,event,observation,*,restricted=False,budget=None):
        budget['remaining']=0
        return original(db,event,observation,restricted=restricted,budget=budget)
    monkeypatch.setattr(witness,'capture_enabled_for_ack',exhaust)
    worker.deliver_events(workspace_id=account[2],event_kind='observation.data')
    ids={prove_ack(first),prove_ack(second)}
    with Session(engine()) as db:
        rows=db.scalars(select(witness.AutoEvaluationAckOverflowV356)).all()
        assert {row.event_id for row in rows}==ids
        assert all(row.data['code']=='budget_exhausted' and row.data['witness_capture_skipped'] for row in rows)
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()


def test_default_closed_readiness_does_not_scan_or_record_any_receipt_status(client,account,monkeypatch):
    from generic_agents import auto_evaluation_policy_api_v356 as api
    s=prepare(client,account,monkeypatch,True)
    monkeypatch.setattr(api,'automatic_runner_available',lambda:False)
    assert process_once(s.job,deliver=False);prove_ack(s)
    with Session(engine()) as db:
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert not db.scalars(select(witness.AutoEvaluationAckFailureV356)).all()
        assert not db.scalars(select(witness.AutoEvaluationAckOverflowV356)).all()
