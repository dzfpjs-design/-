"""Postgres locking and savepoint checks. ACK proof verifier is stubbed here."""
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,Capability,Entity,Task,Observation,Event
from generic_agents.security import Principal
from generic_agents import auto_evaluation_ack_witness_v356 as witness
from generic_agents import auto_evaluation_policy_v356 as policy

@pytest.fixture(scope='module',autouse=True)
def tables():
    policy.AutoEvaluationPolicyV356.__table__.create(engine(),checkfirst=True)
    policy.AutoEvaluationPolicyCommandV356.__table__.create(engine(),checkfirst=True)
    witness.AutoEvaluationAckWitnessV356.__table__.create(engine(),checkfirst=True)
    witness.AutoEvaluationAckFailureV356.__table__.create(engine(),checkfirst=True)

@pytest.fixture
def scene(monkeypatch):
    with Session(engine()) as db:
        user=User(username='w'+uuid4().hex,password_hash='unused');ws=Workspace(name='independent test')
        db.add_all([user,ws]);db.flush();db.add(Membership(workspace_id=ws.id,user_id=user.id,role='admin'))
        cap=Capability(workspace_id=ws.id,namespace='test',name='schema',release_version='1',kind='schema',status='draft',content_hash='a'*64,data={});db.add(cap);db.flush()
        obj=Entity(workspace_id=ws.id,namespace='test',entity_type='tree',external_id='one',schema_capability_id=cap.id,data={});db.add(obj);db.flush()
        task=Task(workspace_id=ws.id,title='test',owner_id=user.id,package_id=cap.id,data={'entity_ids':[obj.id]});db.add(task);db.flush()
        obs=Observation(workspace_id=ws.id,source_id=cap.id,source_record_id='one',processing_version='1',entity_id=obj.id,status='processed',data={});db.add(obs);db.flush()
        event=Event(workspace_id=ws.id,kind='observation.data',idempotency_key='data:'+obs.id,status='pending_delivery',data={});db.add(event);db.flush()
        cfg=policy.AutoEvaluationPolicyV356(workspace_id=ws.id,task_id=task.id,status='enabled',version=1,data={'policy_hash':'test'});db.add(cfg);db.commit()
        x=NS(p=Principal(user.id,ws.id,'admin','test'),task=task.id,obs=obs.id,event=event.id,policy=cfg.id)
    monkeypatch.setattr(policy,'verify',lambda *a:None)
    monkeypatch.setattr(witness,'observation_receipt',lambda *a:{'data_reception':{'test':'ack'}})
    return x


def test_policy_lock_serializes_revoke_and_witness_persists(scene):
    s=scene
    with Session(engine()) as first:
        evt=first.get(Event,s.event)
        with witness.reception(first,evt):
            evt.status='received';first.flush()
            with witness.bounded_savepoint(first):issued=witness.issue(first,s.p,s.task,s.obs)
            with Session(engine()) as competitor:
                competitor.execute(text("SET LOCAL lock_timeout = '300ms'"))
                with pytest.raises(OperationalError):
                    competitor.execute(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.id==s.policy).with_for_update())
                competitor.rollback()
        first.commit();identity=issued.id
    with Session(engine()) as db:
        assert db.get(witness.AutoEvaluationAckWitnessV356,identity) is not None
        row=db.get(policy.AutoEvaluationPolicyV356,s.policy);row.status='revoked';row.version+=1;db.commit()


def test_failed_witness_savepoint_does_not_roll_back_ack(scene,monkeypatch):
    s=scene
    monkeypatch.setattr(policy,'verify',lambda *a:(_ for _ in ()).throw(HTTPException(409,'rejected')))
    with Session(engine()) as db:
        evt=db.get(Event,s.event)
        with witness.reception(db,evt):
            evt.status='received';db.flush()
            with pytest.raises(HTTPException):
                with witness.bounded_savepoint(db):witness.issue(db,s.p,s.task,s.obs)
            with witness.bounded_savepoint(db):witness.record_failure(db,s.p,s.task,s.obs,'authority_unavailable')
        db.commit()
    with Session(engine()) as db:
        assert db.get(Event,s.event).status=='received'
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert db.scalar(select(witness.AutoEvaluationAckFailureV356)).data['code']=='authority_unavailable'


def test_cannot_issue_for_already_received_event_outside_original_transaction(scene):
    with Session(engine()) as db:
        evt=db.get(Event,scene.event);evt.status='received';db.commit()
        with pytest.raises(HTTPException):witness.issue(db,scene.p,scene.task,scene.obs)
        with pytest.raises(HTTPException):
            with witness.reception(db,evt):pass


def test_uncommitted_enable_does_not_block_ack_or_sign(scene):
    import threading
    s=scene
    with Session(engine()) as db:
        row=db.get(policy.AutoEvaluationPolicyV356,s.policy);row.status='revoked';db.commit()
    finished=threading.Event();out=[]
    with Session(engine()) as enabling:
        enabling.execute(select(Task).where(Task.id==s.task).with_for_update()).scalar_one()
        pending=enabling.execute(select(policy.AutoEvaluationPolicyV356).where(policy.AutoEvaluationPolicyV356.id==s.policy).with_for_update()).scalar_one()
        pending.status='enabled';enabling.flush()
        def receive():
            try:
                with Session(engine()) as db:
                    evt=db.get(Event,s.event)
                    with witness.reception(db,evt):
                        evt.status='received';db.flush()
                        try:
                            with witness.bounded_savepoint(db):witness.issue(db,s.p,s.task,s.obs)
                        except OperationalError:
                            with witness.bounded_savepoint(db):witness.record_failure(db,s.p,s.task,s.obs,'lock_contended')
                    db.commit();out.append('ack committed')
            except BaseException as error:out.append(error)
            finally:finished.set()
        thread=threading.Thread(target=receive);thread.start()
        assert finished.wait(5),'ACK must not await an uncommitted authorization'
        enabling.rollback()
    thread.join();assert out==['ack committed']
    with Session(engine()) as db:
        assert db.get(Event,s.event).status=='received'
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
        assert db.scalar(select(witness.AutoEvaluationAckFailureV356)).data['code']=='lock_contended'


def test_task_then_observation_vs_ack_observation_then_task_does_not_deadlock(scene):
    import threading
    s=scene;holds_task=threading.Event();ack_holds_observation=threading.Event();task_done=threading.Event();out=[]
    def task_side():
        try:
            with Session(engine()) as db:
                db.execute(select(Task).where(Task.id==s.task).with_for_update()).scalar_one();holds_task.set()
                assert ack_holds_observation.wait(5)
                db.execute(select(Observation).where(Observation.id==s.obs).with_for_update()).scalar_one()
                db.commit();out.append('task completed')
        except BaseException as error:out.append(error)
        finally:task_done.set()
    thread=threading.Thread(target=task_side);thread.start();assert holds_task.wait(5)
    with Session(engine()) as db:
        db.execute(select(Observation).where(Observation.id==s.obs).with_for_update()).scalar_one()
        evt=db.get(Event,s.event)
        with witness.reception(db,evt):
            evt.status='received';db.flush();ack_holds_observation.set()
            with pytest.raises(OperationalError):
                with witness.bounded_savepoint(db):witness.issue(db,s.p,s.task,s.obs)
            with witness.bounded_savepoint(db):witness.record_failure(db,s.p,s.task,s.obs,'lock_contended')
        db.commit()
    assert task_done.wait(5);thread.join();assert out==['task completed']
    with Session(engine()) as db:
        assert db.get(Event,s.event).status=='received'
        assert db.scalar(select(witness.AutoEvaluationAckFailureV356)).data['code']=='lock_contended'
        assert not db.scalars(select(witness.AutoEvaluationAckWitnessV356)).all()
