"""Real disposable PostgreSQL storage/DDL only; all business/ACK pins are fictional.

This proves row durability, typed storage, uniqueness and append-only guards.
It does not prove a real receiver, producer, permission or sufficient Goal.
"""
import copy
import pytest
from sqlalchemy import select,text,update,delete
from sqlalchemy.exc import DBAPIError,IntegrityError
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents import workflow_goal_evaluation_contracts_v288 as contract
from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288,row_values
from generic_agents.typed_payload_orm_v131 import CANONICAL,FORMAT
from tests.test_workflow_goal_evaluation_contracts_v288 import scene,uid,hashed

@pytest.fixture(scope='session',autouse=True)
def install_late_model(disposable_database):
    # conftest selected a new ga_test_* database before test collection.
    assert engine().url.database.startswith('ga_test_')
    GoalEvaluationV288.__table__.create(engine(),checkfirst=True)

def seed():
    _unsigned,record,_kwargs,_q0=scene()
    scope=record['scope'];w=scope['workspace_id'];actor=record['producer']['actor_id']
    with Session(engine()) as db:
        db.add_all([m.Workspace(id=w,name='Synthetic storage fixture'),m.User(id=actor,username='eval_storage_actor',password_hash='unusable-test-hash')]);db.flush()
        db.add_all([m.Capability(id=scope['package_id'],workspace_id=w,namespace='eval_storage',name='package',kind='domain_package',release_version='1.0.0',status='published',content_hash='a'*64,data={}),
            m.Capability(id=record['producer']['capability_id'],workspace_id=w,namespace='eval_storage',name='evaluation',kind='tool',release_version='1.0.0',status='published',content_hash='b'*64,data={})]);db.flush()
        db.add_all([m.Task(id=scope['task_id'],workspace_id=w,title='Storage contract only',owner_id=actor,package_id=scope['package_id'],data={}),
            m.DomainInstance(id=scope['instance_id'],workspace_id=w,namespace='eval_storage',data={})]);db.flush()
        db.add(m.InstanceRelease(id=scope['release_id'],workspace_id=w,instance_id=scope['instance_id'],release_number=1,configuration_hash='c'*64,package_id=scope['package_id'],actor_id=actor,data={}))
        db.add_all([m.DataRun(id=record['original_query_ref']['query_id'],workspace_id=w,owner_id=actor,question='Fictional original',status='succeeded',data={'fixture':'Q0'}),
            m.DataRun(id=record['evaluation_query_ref']['query_id'],workspace_id=w,owner_id=actor,question='Fictional new query',status='succeeded',data={'fixture':'Qe'}),
            m.Job(id=record['producer']['job_id'],workspace_id=w,kind='storage_fixture',idempotency_key=record['request_key'],status='running',data={}),
            m.Invocation(id=record['producer']['invocation_id'],workspace_id=w,capability_id=record['producer']['capability_id'],idempotency_key=record['request_key'],status='running',data={}),
            m.Evidence(id=record['evidence_ref']['evidence_id'],workspace_id=w,sha256=record['evidence_ref']['sha256'],media_type='text/plain',size_bytes=64,storage_key='synthetic/no-file',data={})]);db.flush()
        db.add(GoalEvaluationV288(**row_values(record)));db.commit()
    return record

def test_actual_insert_reload_typed_storage_and_Q0_remain_exact():
    record=seed()
    with Session(engine()) as db:
        row=db.get(GoalEvaluationV288,record['evaluation_id']);assert row.data==record and row.version==1
        row.data['generation']=999;db.expire_all();assert db.get(GoalEvaluationV288,row.id).data==record
        stored=db.execute(text(f'SELECT {FORMAT},{CANONICAL},data::text FROM workflow_goal_evaluations_v288 WHERE id=:id'),{'id':record['evaluation_id']}).one()
        assert stored[0] and stored[1] and stored[2]
        assert db.get(m.DataRun,record['original_query_ref']['query_id']).data=={'fixture':'Q0'}
    with Session(engine()) as db:assert db.get(GoalEvaluationV288,record['evaluation_id']).data==record

@pytest.mark.parametrize('operation',['orm_update','orm_delete','core_update','core_delete','raw_noop_update','raw_delete'])
def test_actual_orm_core_and_raw_mutations_rejected_and_rolled_back(operation):
    record=seed();identity=record['evaluation_id']
    with Session(engine()) as db:
        with pytest.raises((ValueError,DBAPIError)):
            if operation=='orm_update':
                row=db.get(GoalEvaluationV288,identity);row.record_hash='d'*64;db.flush()
            elif operation=='orm_delete':db.delete(db.get(GoalEvaluationV288,identity));db.flush()
            elif operation=='core_update':db.execute(update(GoalEvaluationV288).where(GoalEvaluationV288.id==identity).values(record_hash='d'*64))
            elif operation=='core_delete':db.execute(delete(GoalEvaluationV288).where(GoalEvaluationV288.id==identity))
            elif operation=='raw_noop_update':db.execute(text('UPDATE workflow_goal_evaluations_v288 SET record_hash=record_hash WHERE id=:id'),{'id':identity})
            else:db.execute(text('DELETE FROM workflow_goal_evaluations_v288 WHERE id=:id'),{'id':identity})
        db.rollback()
        assert db.get(GoalEvaluationV288,identity).data==record

def test_generation_uniqueness_and_column_payload_pairing():
    record=seed();raw=copy.deepcopy(record)
    raw['evaluation_id']=uid(61);raw['producer']['invocation_id']=uid(61);raw['producer']['job_id']=uid(62);raw['request_id']=uid(62)
    raw['request_key']='graph:synthetic-task:evaluation:2';raw['producer']['request_key_hash']=hashed(raw['request_key'])
    raw['evaluation_query_ref']['query_id']=uid(63);raw['evidence_ref']['evidence_id']=uid(64)
    raw['record_hash']=contract.record_hash(raw)
    w=record['scope']['workspace_id'];actor=record['producer']['actor_id']
    with Session(engine()) as db:
        db.add_all([m.Job(id=uid(62),workspace_id=w,kind='storage_fixture',idempotency_key=raw['request_key'],data={}),
            m.Invocation(id=uid(61),workspace_id=w,capability_id=record['producer']['capability_id'],idempotency_key=raw['request_key'],data={}),
            m.DataRun(id=uid(63),workspace_id=w,owner_id=actor,question='Another fixture',status='succeeded',data={}),
            m.Evidence(id=uid(64),workspace_id=w,sha256='e'*64,media_type='text/plain',size_bytes=64,storage_key='synthetic/no-second-file',data={})]);db.commit()
        with pytest.raises(IntegrityError):db.add(GoalEvaluationV288(**row_values(raw)));db.flush()
        db.rollback();assert len(list(db.scalars(select(GoalEvaluationV288))))==1
        values=row_values(raw);values['goal_hash']='f'*64
        with pytest.raises(contract.GoalEvaluationContractError):db.add(GoalEvaluationV288(**values));db.flush()
        db.rollback();assert db.get(GoalEvaluationV288,record['evaluation_id']).data==record
