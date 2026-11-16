"""Real PG Task/Policy locking around real maturity entry points.

The original observation ACK, policy command, witness and authority verification
are real; the append producer and candidate/maturity preparation are explicitly
synthetic. This is not a complete producer or actual publication journey.
"""
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit,Job,Task,Observation,Event
from generic_agents.contracts import digest
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from generic_agents import auto_evaluation_trigger_v356 as trigger,auto_evaluation_policy_v356 as policy
from generic_agents import auto_evaluation_ack_witness_v356 as witness,goal_evaluation_maturity_v299 as maturity
from generic_agents import native_goals,relations
from generic_agents.worker import process_once
from tests.test_auto_evaluation_ack_receiver_v356 import prepare


class AfterAuthority(Exception):pass


def fixture(client,account,monkeypatch):
    s=prepare(client,account,monkeypatch,True)
    assert process_once(s.job,deliver=False)
    with Session(engine(),expire_on_commit=False) as db:
        task=db.get(Task,s.task);obs=db.get(Observation,s.identity)
        evt=db.scalar(select(Event).where(Event.workspace_id==s.p.workspace_id,Event.kind=='observation.data',Event.data['observation_id'].as_string()==obs.id))
        ack=observation_receipt(db,s.p,obs)['data_reception']
        proof=db.scalar(select(witness.AutoEvaluationAckWitnessV356).where(witness.AutoEvaluationAckWitnessV356.event_id==evt.id))
        assert proof is not None
        cfg=db.scalar(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.task_id==task.id))
        producer=Job(workspace_id=s.p.workspace_id,kind='append_goal_evaluation_v297',status='succeeded',
            idempotency_key='fixture-producer:'+str(uuid4()),data={'task_id':task.id,'actor_id':s.p.user_id})
        candidate_job=Job(workspace_id=s.p.workspace_id,kind='check_native_business_goal',status='succeeded',
            idempotency_key='fixture-candidate:'+str(uuid4()),data={'task_id':task.id,'actor_id':s.p.user_id})
        db.add_all([producer,candidate_job]);db.flush()
        cmd=Command(workspace_id=s.p.workspace_id,task_id=task.id,actor_id=s.p.user_id,goal_version=1,
            goal_hash='a'*64,request_id=str(uuid4()),body_hash='b'*64,capability_id=task.package_id,
            capability_hash='c'*64,input_hash='d'*64,job_id=producer.id,invocation_key=producer.idempotency_key,
            record_hash='e'*64,data={'fixture':'synthetic producer; not a real append'})
        db.add(cmd);db.flush()
        binding={'schema':'auto-evaluation-trigger-v356','workspace_id':s.p.workspace_id,'task_id':task.id,
            'event_id':evt.id,'observation_id':obs.id,'receipt_hash':digest(ack),'policy_id':cfg.id,
            'policy_version':cfg.version,'policy_hash':cfg.data['policy_hash'],'command_id':cmd.id,'job_id':producer.id,
            'request_id':cmd.request_id,'actor_id':s.p.user_id,'witness_id':proof.id}
        binding['binding_hash']=digest(binding)
        origin=trigger.AutoEvaluationTriggerV356(workspace_id=s.p.workspace_id,task_id=task.id,event_id=evt.id,
            observation_id=obs.id,policy_id=cfg.id,policy_version=cfg.version,command_id=cmd.id,job_id=producer.id,data=binding)
        db.add(origin);db.flush();db.add(Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,action=trigger.ACTION,object_id=origin.id,data=binding))
        promotion=Job(workspace_id=s.p.workspace_id,kind=maturity.KIND,status='running',
            idempotency_key='fixture-promoter:'+str(uuid4()),data={'schema':maturity.PROMOTION,'task_id':task.id,
            'goal_check_job_id':candidate_job.id,'actor_id':s.p.user_id})
        db.add(promotion);db.commit()
        return NS(p=s.p,task=task.id,producer=producer.id,candidate=candidate_job.id,promotion=promotion.id,cfg=cfg.id)


def fake_maturity_dependencies(monkeypatch,s):
    """Only origin/policy/ACK verification and locking are real here."""
    row=NS(job_id=s.producer,evaluation_query_id=str(uuid4()),invocation_id=str(uuid4()))
    # This test isolates lock fencing; the independent evidence gate has its own tests.
    from generic_agents import goal_evaluation_append_query_v299 as frozen_query, auto_evaluation_evidence_gate_v357 as evidence_gate
    monkeypatch.setattr(frozen_query,'checked_frozen',lambda *a,**k:{})
    monkeypatch.setattr(evidence_gate,'verify',lambda *a,**k:{})
    monkeypatch.setattr(maturity,'handles',lambda *a:True)
    monkeypatch.setattr(maturity,'_lease',lambda *a,**k:None)
    monkeypatch.setattr(relations,'lock_current_actor',lambda *a:None)
    monkeypatch.setattr(native_goals,'guard',lambda *a,**k:None)
    monkeypatch.setattr(maturity,'_candidate',lambda *a:({'actor_id':s.p.user_id},row))
    monkeypatch.setattr(maturity,'_evaluation',lambda *a,**k:(row,{}))
    monkeypatch.setattr(maturity,'_rows',lambda *a:[NS()])
    monkeypatch.setattr(maturity,'_prepared',lambda *a:({'evaluation_ref':{}},row,{},NS(),NS()))
    return row


def assert_revoke_waits(db,s):
    with Session(engine()) as competing:
        competing.execute(text("SET LOCAL lock_timeout='200ms'"))
        with pytest.raises(OperationalError):
            competing.execute(select(Task).where(Task.id==s.task).with_for_update())
        competing.rollback()
        competing.execute(text("SET LOCAL lock_timeout='200ms'"))
        with pytest.raises(OperationalError):
            competing.execute(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.id==s.cfg).with_for_update())
        competing.rollback()


def revoke(s):
    with Session(engine()) as db:
        policy.change(db,s.p,s.task,'revoke',{'request_id':str(uuid4()),'expected_version':1,'reason':'撤销竞态测试'})
        db.commit()


def test_real_candidate_path_holds_task_and_policy_until_transaction_ends(client,account,monkeypatch):
    s=fixture(client,account,monkeypatch);fake_maturity_dependencies(monkeypatch,s)
    with Session(engine()) as db:
        task=db.get(Task,s.task);job=db.get(Job,s.candidate)
        assert maturity.process_candidate(db,s.p,task,job) is True
        assert_revoke_waits(db,s)
        db.commit()
    revoke(s)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):maturity.process_candidate(db,s.p,db.get(Task,s.task),db.get(Job,s.candidate))


def test_real_promotion_path_holds_policy_before_publication_and_revocation_blocks(client,account,monkeypatch):
    s=fixture(client,account,monkeypatch);fake_maturity_dependencies(monkeypatch,s)
    # Stop immediately after real origin authority; all later business evidence
    # is synthetic and must not be presented as a published result.
    monkeypatch.setattr(maturity,'_evaluation',lambda *a,**k:(_ for _ in ()).throw(AfterAuthority()))
    with Session(engine()) as db:
        with pytest.raises(AfterAuthority):maturity.process_promotion(db,s.p,db.get(Job,s.promotion))
        assert_revoke_waits(db,s)
        db.rollback()
    revoke(s)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):maturity.process_promotion(db,s.p,db.get(Job,s.promotion))


def test_stale_automatic_promotion_preserves_original_head(client,account,monkeypatch):
    """Scope is synthetic after preparation; the publication gate is real."""
    import copy
    s=fixture(client,account,monkeypatch);fake_maturity_dependencies(monkeypatch,s)
    identity=str(uuid4());item={'observation_id':identity,'attributes':{'temperature':25}}
    row=NS(job_id=s.producer,id=str(uuid4()),record_hash='a'*64,
        data={'evaluation_result':{'result':{'observation_evidence':[item]}}})
    view={'state':'current','evaluation_ref':{'id':row.id,'record_hash':row.record_hash}}
    current={'status':'sufficient','observation_evidence':[item],maturity.provider.VIEW:view}
    memory=NS(data={'report':{'evidence':copy.deepcopy(current)}})
    monkeypatch.setattr(maturity,'_prepared',lambda *a:({},row,{},NS(),memory))
    monkeypatch.setattr(maturity,'_auto_producer_authority',lambda *a:NS(observation_id=identity))
    monkeypatch.setattr(maturity,'_evaluation',lambda *a,**k:(row,{}))
    stale={**copy.deepcopy(current),'status':'insufficient',maturity.provider.VIEW:{**view,'state':'stale'}}
    monkeypatch.setattr(maturity.provider,'_evidence',lambda *a:stale)
    with Session(engine()) as db:
        task=db.get(Task,s.task);before=copy.deepcopy(task.data)
        with pytest.raises(HTTPException) as error:maturity.process_promotion(db,s.p,db.get(Job,s.promotion))
        assert error.value.status_code==409 and task.data==before
        assert not db.scalar(select(Audit.id).where(Audit.workspace_id==s.p.workspace_id,Audit.action==maturity.MATURED))
        db.rollback()


def test_automatic_promotion_crossing_expiry_before_write_preserves_head(client,account,monkeypatch):
    """Synthetic prepared inputs exercise the actual promotion write boundary."""
    import copy
    s=fixture(client,account,monkeypatch);fake_maturity_dependencies(monkeypatch,s)
    identity=str(uuid4());item={'observation_id':identity,'attributes':{'temperature':25}}
    row=NS(job_id=s.producer,id=str(uuid4()),record_hash='a'*64,generation=2,
        data={'evaluation_result':{'result':{'observation_evidence':[item]}}})
    ref={'id':row.id,'record_hash':row.record_hash};view={'state':'current','evaluation_ref':ref}
    current={'status':'sufficient','observation_evidence':[item],maturity.provider.VIEW:view}
    memory=NS(id=str(uuid4()),data={'report':{'evidence':copy.deepcopy(current)}})
    candidate={'evaluation_ref':ref,'base_head':None}
    prepared={'record_hash':'b'*64,'memory_id':memory.id,'report_hash':'c'*64,'proposed_head':{'generation':2}}
    event=NS(id=str(uuid4()))
    outbox={'prepared_audit_id':event.id,'prepared_hash':prepared['record_hash'],
        'evaluation_ref':ref,'memory_id':memory.id,'report_hash':prepared['report_hash']}
    monkeypatch.setattr(maturity,'_prepared',lambda *a:(candidate,row,prepared,event,memory))
    monkeypatch.setattr(maturity,'_auto_producer_authority',lambda *a:NS(observation_id=identity))
    monkeypatch.setattr(maturity,'_evaluation',lambda *a,**k:(row,{}))
    monkeypatch.setattr(maturity,'_outbox',lambda *a:(outbox,NS()))
    monkeypatch.setattr(maturity.provider,'_head',lambda *a:None)
    monkeypatch.setattr(maturity,'_rows',lambda *a:[])
    monkeypatch.setattr(maturity,'_mature_value',lambda *a:{})
    calls=[]
    def advance(*args):
        calls.append(True)
        return current if len(calls)==1 else {**copy.deepcopy(current),'status':'insufficient',
            maturity.provider.VIEW:{**view,'state':'stale'}}
    monkeypatch.setattr(maturity.provider,'_evidence',advance)
    with Session(engine()) as db:
        task=db.get(Task,s.task);before=copy.deepcopy(task.data)
        with pytest.raises(HTTPException) as error:maturity.process_promotion(db,s.p,db.get(Job,s.promotion))
        assert error.value.status_code==409 and len(calls)==2 and task.data==before
        assert not db.scalar(select(Audit.id).where(Audit.workspace_id==s.p.workspace_id,Audit.action==maturity.MATURED))
        db.rollback()


def test_automatic_promotion_expiring_after_write_rolls_back_head_on_commit(client,account,monkeypatch):
    """Synthetic prepared evidence, actual promotion branch and PG transaction."""
    import copy
    from datetime import timedelta
    from sqlalchemy import func
    s=fixture(client,account,monkeypatch);fake_maturity_dependencies(monkeypatch,s)
    identity=str(uuid4());origin=NS(observation_id=identity)
    monkeypatch.setattr(maturity,'_auto_producer_authority',lambda *a:origin)
    monkeypatch.setattr(maturity.provider,'_head',lambda *a:None)
    monkeypatch.setattr(maturity,'_rows',lambda *a:[])
    monkeypatch.setattr(maturity,'_mature_value',lambda *a:{})
    with Session(engine()) as db:
        task=db.get(Task,s.task);before=copy.deepcopy(task.data)
        at=db.scalar(select(func.clock_timestamp()));item={'observation_id':identity,'observed_at':at.isoformat(),
            'attributes':{'temperature':25}}
        record={'cut_at':at.isoformat(),'preparation_policy':{'time_basis':'rolling_latest','max_age_seconds':2},
            'evaluation_result':{'result':{'status':'sufficient','evidence':[],'observation_evidence':[item]}}}
        row=NS(job_id=s.producer,id=str(uuid4()),record_hash='a'*64,generation=2,
            evaluation_query_id=str(uuid4()),source_binding_hash='b'*64,goal_snapshot_hash='c'*64,data=record)
        evidence=maturity.provider._evidence(row,record,at)
        ref={'id':row.id,'record_hash':row.record_hash};memory=NS(id=str(uuid4()),data={'report':{'evidence':evidence}})
        candidate={'evaluation_ref':ref,'base_head':None}
        prepared={'record_hash':'d'*64,'memory_id':memory.id,'report_hash':'e'*64,'proposed_head':{'generation':2}}
        prepared_event=NS(id=str(uuid4()))
        outbox={'prepared_audit_id':prepared_event.id,'prepared_hash':prepared['record_hash'],
            'evaluation_ref':ref,'memory_id':memory.id,'report_hash':prepared['report_hash']}
        monkeypatch.setattr(maturity,'_prepared',lambda *a:(candidate,row,prepared,prepared_event,memory))
        monkeypatch.setattr(maturity,'_evaluation',lambda *a,**k:(row,record))
        monkeypatch.setattr(maturity,'_outbox',lambda *a:(outbox,NS()))
        assert maturity.process_promotion(db,s.p,db.get(Job,s.promotion))=='succeeded'
        assert task.data!=before
        db.flush()
        assert maturity.provider._evidence(row,record,db.scalar(select(func.clock_timestamp())))[maturity.provider.VIEW]['state']=='current'
        db.execute(select(func.pg_sleep(2.2)))
        with pytest.raises(HTTPException) as error:db.commit()
        assert error.value.status_code==409
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Task,s.task).data==before
        assert not db.scalar(select(Audit.id).where(Audit.workspace_id==s.p.workspace_id,Audit.action==maturity.MATURED))
