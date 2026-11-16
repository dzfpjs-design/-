"""Actual worker branches, recovery and lease callbacks at explicit IO seams.

SQL, Session/commit, IAM, full protocol/source/paid-receipt authority and model
transport are declared spies. These tests do not prove real PG concurrency,
leases, durable capture, permission or a paid request in the target runtime.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import job_recovery as recovery, leases, worker
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents.security import Principal
from tests.test_local_goal_renewable_lease import attached, begin, last_job_sql, JOB, TOKEN, NOW, no_external
from tests.test_same_goal_advice_contracts_v302 import ref
from tests.test_same_goal_advice_execution_v308 import scene as execution_scene, _sdk


@pytest.fixture
def scene(monkeypatch):
    t=execution_scene.__wrapped__(monkeypatch);t.start();value=t.checked();t.send=value['job']
    t.send.status='running';t.send.attempts=2;t.send.lease_token=ref('current_reclaim_token')['id']
    t.call=_sdk(t);t.call.version=5
    t.recovery_actor=Principal(t.p.user_id,t.p.workspace_id,'viewer','worker-recovery')
    t.recovery_audits=[];t.cap_lookups=[];t.recovery_queries=[]
    original=t.db.scalars
    class Rows(list):
        def all(self):return list(self)
    def rows(statement):
        t.recovery_queries.append(statement)
        return Rows(original(statement))
    monkeypatch.setattr(t.db,'scalars',rows)
    def scoped(db,model,identity,p,*args,**kwargs):
        assert db is t.db and p is t.recovery_actor
        # Recovery may look up the original published capability; it must not
        # lock or interpret Task/Plan/step_index while holding the initial Job.
        assert model is execution.Capability and identity==t.advice.id and not args and not kwargs
        t.cap_lookups.append(identity);return t.advice
    monkeypatch.setattr(recovery,'scoped',scoped)
    def audit(db,p,action,identity,body):
        assert db is t.db and p is t.recovery_actor
        t.recovery_audits.append((action,identity,copy.deepcopy(body)))
    monkeypatch.setattr(recovery,'audit',audit)
    monkeypatch.setattr(recovery,'now',lambda:t.clock)
    t.recover=lambda:recovery.recover_calls(t.db,t.recovery_actor,t.send)
    return t


def test_typed_paid_sdk_is_unknown_after_interruption_even_with_no_device_side_effect(scene):
    t=scene;call=t.call;before=copy.deepcopy(call.data);original=(call.id,call.idempotency_key,call.capability_id)
    assert call.data['side_effect'] is False
    assert t.recover()==1 and call.status=='unknown' and call.version==6
    assert (call.id,call.idempotency_key,call.capability_id)==original
    assert all(call.data[key]==value for key,value in before.items())
    assert call.data['recovery']['previous_lease_token']==before['lease_token']!=t.send.lease_token
    assert t.recovery_audits==[('worker.invocation_recovered',call.id,{'status':'unknown','job_id':t.send.id})]
    assert t.send.data['recovery_only'] is True and len(t.rows[execution.Invocation])==1
    assert t.recovery_queries[-1]._for_update_arg is not None
    saved=copy.deepcopy(call.data);assert t.recover()==1 and call.data==saved and len(t.recovery_audits)==1


@pytest.mark.parametrize('status',['created','unknown','failed','blocked_evidence','blocked_model','succeeded','not_executed'])
def test_existing_sdk_status_and_original_capture_are_not_rewritten_or_replayed(scene,status):
    t=scene;t.call.status=status;t.call.data['same_goal_advice_v308']={'synthetic_original_capture_IO':'retained'}
    before=copy.deepcopy(t.call.data)
    assert t.recover()==1 and t.call.status==status and t.call.version==5 and t.call.data==before
    assert t.send.data['recovery_only'] is True and not t.recovery_audits


@pytest.mark.parametrize('fault',['job_schema','job_key','approval','inputs','role','capability_hash','duplicate_sdk'])
def test_wrong_typed_inventory_fails_before_any_original_invocation_mutation(scene,fault):
    t=scene
    if fault=='job_schema':t.send.data['schema']='legacy-job'
    if fault=='job_key':t.send.idempotency_key+=':other'
    if fault=='approval':t.call.data['approval']['job_id']=ref('foreign_job')['id']
    if fault=='inputs':t.call.data['inputs']['plan_ref']['record_hash']='0'*64
    if fault=='role':t.call.data['role']='data'
    if fault=='capability_hash':t.call.data['capability_hash']='0'*64
    if fault=='duplicate_sdk':
        t.rows[execution.Invocation].append(execution.Invocation(id=ref('alternate_sdk')['id'],
            workspace_id=t.p.workspace_id,capability_id=t.call.capability_id,
            idempotency_key=t.call.idempotency_key+':other',status='running',data=copy.deepcopy(t.call.data)))
    before=copy.deepcopy(t.call.data)
    with pytest.raises(HTTPException):t.recover()
    assert t.call.status=='running' and t.call.version==5 and t.call.data==before and not t.recovery_audits


def test_missing_sdk_does_not_create_a_replacement_call_or_receipt_in_recovery(scene):
    t=scene;t.rows[execution.Invocation]=[];data=copy.deepcopy(t.send.data)
    assert t.recover()==0 and not t.rows[execution.Invocation] and not t.recovery_audits and t.send.data==data


@pytest.mark.parametrize('fault',[None,'schema','time','token','expired','revoked'])
def test_actual_send_family_begin_is_task_first_while_original_commit_remains_exclusive(monkeypatch,fault):
    db,callbacks=attached(monkeypatch);row=db.connection_value.row
    row.kind=execution.c.SEND_KIND;row.data={'schema':execution.JOB_SCHEMA}
    with leases.local_goal_phase(db,JOB,TOKEN) as phase:
        begin(db,callbacks);assert 'FOR UPDATE' in last_job_sql(db)
        phase.activate()
        if fault=='schema':row.data['schema']='same-goal-advice-prepare-job-v302'
        if fault=='time':row.data['time_trigger']=None
        if fault=='token':row.lease_token='new-owner-token'
        if fault=='expired':row.lease_until=NOW
        if fault=='revoked':
            db.info['worker_actor']=NS(user_id='actor',workspace_id='workspace',role='operator')
            db.connection_value.actor_allowed=False
        if fault:
            expected=leases.ExecutionUnauthorized if fault=='revoked' else leases.LeaseLost
            with pytest.raises(expected):begin(db,callbacks)
        else:
            begin(db,callbacks);assert 'FOR UPDATE' not in last_job_sql(db)
            callbacks['before_commit'](db);assert 'FOR UPDATE' in last_job_sql(db)


@pytest.mark.parametrize('result',['succeeded','cancelled','failed','exception'])
def test_actual_worker_dispatches_fixed_handler_after_recovery_commit_and_bookkeeps_failure_in_new_phase(scene,monkeypatch,result):
    t=scene;job=t.send;events=[];sessions=[];job.data['recovery_only']=True
    class DB:
        def __init__(self):self.info={};self.transaction=False;sessions.append(self)
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def in_transaction(self):return self.transaction
        def get(self,model,identity):
            assert model is worker.Job and identity==job.id
            self.transaction=True;return job
        def commit(self):
            events.append(('commit',sessions.index(self),job.status));self.transaction=False
        def add(self,row):events.append(('diagnostic',row.action))
        def rollback(self):self.transaction=False
        def begin_nested(self):return nullcontext()
    monkeypatch.setattr(worker,'Session',lambda *args,**kwargs:DB())
    monkeypatch.setattr(worker,'engine',lambda:object())
    monkeypatch.setattr(worker,'claim',lambda *args,**kwargs:(job.id,job.lease_token))
    monkeypatch.setattr(leases,'heartbeat',lambda *args:nullcontext())
    def attach(db,identity,token):
        assert identity==job.id and token==job.lease_token
        db.info['worker_lease']={'job_id':identity,'lease_token':token}
    monkeypatch.setattr(leases,'attach',attach)  # Real callbacks are tested separately above.
    def recovered(db,p,actual):
        assert actual is job and p.role=='viewer';events.append(('recover',sessions.index(db)));return 1
    monkeypatch.setattr(recovery,'recover_calls',recovered)  # SDK recovery itself runs in the cases above.
    def actor(db,*args):events.append(('actor',sessions.index(db)));return t.p
    monkeypatch.setattr(worker,'actor',actor)  # Explicit current IAM boundary.
    def process(db,p,actual):
        assert actual is job and p is t.p and db.info[leases._LOCAL_GOAL_PHASE].active
        assert ('commit',0,'running') in events
        events.append(('process',sessions.index(db)));db.transaction=True
        if result=='exception':raise ValueError('synthetic handler failure')
        return result
    monkeypatch.setattr(runtime,'process',process)  # Full source/capture/paid protocol is not stubbed as verified.
    def fail(db,p,actual,reason):
        assert actual is job and p.role=='viewer' and db.info[leases._LOCAL_GOAL_PHASE].active
        assert ('commit',1,'running') in events
        events.append(('fail',sessions.index(db)));db.transaction=True;return 'failed'
    monkeypatch.setattr(runtime,'fail_job',fail)
    assert execution.c.SEND_KIND in worker.SETUP_KINDS
    assert worker.process_once(job.id,deliver=False) is True
    expected='failed' if result in {'failed','exception'} else result
    assert job.status==expected and job.lease_until is None and job.data['finished_at']
    assert events.index(('recover',0))<events.index(('commit',0,'running'))<events.index(('process',0))
    if result=='exception':assert events.index(('commit',1,'running'))<events.index(('fail',1))
