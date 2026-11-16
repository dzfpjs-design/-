"""Actual disposable PG/IAM/lease checkpoint and terminal-flush regressions.

The typed Job is a storage/fence fixture, deliberately not a proved Start,
Task/Plan/source, SDK, financial receipt or model-send authority. No HTTP or
cloud calls run. Actual lease callbacks, DB clock, Core UPDATE, independent
connections, membership revocation, rollback and ORM flush are unmocked.
"""
import copy
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import leases
from generic_agents.db import engine
from generic_agents.models import Job, Membership, User
from generic_agents.security import Principal

KIND='generate_same_goal_advice_v302'
SCHEMA='same-goal-advice-send-job-v308'


@pytest.fixture(autouse=True)
def disposable_pg_only(disposable_database):
    assert engine().dialect.name=='postgresql' and engine().url.database.startswith('ga_test_')


@pytest.fixture
def scene(account):
    with Session(engine(),expire_on_commit=False) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        p=Principal(user.id,account[2],'admin','worker')
        task,plan=str(uuid4()),str(uuid4());key='same-goal-advice-send:'+task+':'+plan
        at=db.scalar(select(func.clock_timestamp()))
        job=Job(workspace_id=p.workspace_id,kind=KIND,status='running',attempts=1,version=3,
            idempotency_key=key,lease_token=str(uuid4()),lease_until=at+timedelta(seconds=30),
            data={'schema':SCHEMA,'plan_id':plan,'task_id':task,'actor_id':p.user_id,
                'execution_snapshot_hash':'a'*64,'invocation_key':key})
        db.add(job);db.commit()
        return NS(p=p,job_id=job.id,token=job.lease_token)


def original(s):
    with Session(engine()) as db:
        row=db.get(Job,s.job_id)
        return {'deadline':row.lease_until,'token':row.lease_token,'status':row.status,
            'attempts':row.attempts,'version':row.version,'key':row.idempotency_key,'data':copy.deepcopy(row.data)}


@contextmanager
def sending_transaction(s):
    with Session(engine(),expire_on_commit=False) as db:
        with leases.local_goal_phase(db,s.job_id,s.token) as phase:
            leases.attach(db,s.job_id,s.token)
            job=db.get(Job,s.job_id)
            db.commit()  # Initial recovery has no worker_actor: no renewal.
            phase.activate();db.info['worker_actor']=s.p
            yield db,job


def test_real_checkpoint_renews_despite_heartbeat_lock_timeout_without_dirtying_old_orm_deadline(scene):
    s=scene;before=original(s)
    with sending_transaction(s) as (db,retained):
        job=db.scalar(select(Job).where(Job.id==s.job_id).with_for_update())
        assert job is retained and job.lease_until==before['deadline']
        # A real second connection cannot renew while this current transaction
        # holds the original Job. Its ordinary 250ms timeout remains unchanged.
        with pytest.raises(DBAPIError):leases.renew(engine(),s.job_id,s.token)
        db.commit()
        assert retained.lease_until==before['deadline'] and retained not in db.dirty
    after=original(s)
    assert after['deadline']>before['deadline']+timedelta(seconds=120)
    assert {key:value for key,value in after.items() if key!='deadline'}=={
        key:value for key,value in before.items() if key!='deadline'}
    assert leases.renew(engine(),s.job_id,s.token) is True
    assert leases.LEASE_SECONDS==180 and leases.HEARTBEAT_SECONDS==30


@pytest.mark.parametrize('scope',['initial_recovery','independent_session'])
def test_no_actor_or_unfenced_session_cannot_extend_a_real_sender(scene,scope):
    s=scene;before=original(s)
    with Session(engine(),expire_on_commit=False) as db:
        if scope=='initial_recovery':leases.attach(db,s.job_id,s.token)
        else:db.info['worker_actor']=s.p  # A JSON/scope hint installs no listeners.
        assert db.get(Job,s.job_id).lease_until==before['deadline']
        db.commit()
    assert original(s)==before


@pytest.mark.parametrize('kind,schema',[
    ('check_native_business_goal','legacy-goal-storage-fixture'),
    ('prepare_same_goal_advice_v302','same-goal-advice-prepare-job-v302'),
    (KIND,'unrecognized-send-schema')])
def test_real_old_family_or_schema_commit_keeps_its_original_lease_untouched(scene,kind,schema):
    s=scene
    with Session(engine()) as db:
        row=db.get(Job,s.job_id);row.kind=kind;row.data={**row.data,'schema':schema};db.commit()
    before=original(s)
    with Session(engine()) as db:
        leases.attach(db,s.job_id,s.token);db.info['worker_actor']=s.p
        assert db.get(Job,s.job_id).status=='running';db.commit()
    assert original(s)==before


@pytest.mark.parametrize('fault',['expired','taken_over','terminal','revoked','wrong_actor'])
def test_actual_expiry_token_status_and_current_account_reject_checkpoint_without_changes(scene,fault):
    s=scene
    with Session(engine()) as db:
        job=db.get(Job,s.job_id)
        if fault=='expired':job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(microseconds=1)
        if fault=='taken_over':job.lease_token=str(uuid4())
        if fault=='terminal':job.status='succeeded';job.lease_until=None
        if fault=='revoked':
            member=db.scalar(select(Membership).where(Membership.workspace_id==s.p.workspace_id,
                Membership.user_id==s.p.user_id));member.role='viewer'
        if fault=='wrong_actor':job.data={**job.data,'actor_id':str(uuid4())}
        db.commit()
    before=original(s)
    expected=leases.ExecutionUnauthorized if fault in {'revoked','wrong_actor'} else leases.LeaseLost
    with Session(engine()) as db:
        leases.attach(db,s.job_id,s.token);db.info['worker_actor']=s.p
        with pytest.raises(expected):
            db.get(Job,s.job_id);db.commit()
        db.rollback()
    assert original(s)==before


def test_actual_takeover_after_nonlocking_begin_is_still_rejected_at_original_commit(scene):
    s=scene
    with sending_transaction(s) as (db,retained):
        assert db.get(Job,s.job_id) is retained  # identity-map get starts no transaction.
        db.scalar(select(Job.id).where(Job.id==s.job_id))  # Actual nonlocking after_begin fence.
        with engine().begin() as connection:
            connection.execute(update(Job).where(Job.id==s.job_id).values(
                lease_token=str(uuid4()),lease_until=func.clock_timestamp()+timedelta(seconds=180)))
        successor=original(s)
        with pytest.raises(leases.LeaseLost):db.commit()
        db.rollback()
    assert original(s)==successor


def test_real_db_expiry_after_original_check_cannot_be_revived_by_the_checkpoint_update(scene,monkeypatch):
    s=scene;before=original(s);actual_actor_check=leases._check_actor;injected=[];armed=[]
    def expiry_fault(connection,db):
        actual_actor_check(connection,db)  # Real current account query, not a permissive spy.
        if armed and db.info.get('worker_actor') is s.p and not injected:
            injected.append(True)
            # Same-transaction fault injection isolates the UPDATE's final
            # unexpired predicate, after the original _check already passed.
            connection.execute(update(Job).where(Job.id==s.job_id).values(
                lease_until=func.clock_timestamp()-timedelta(microseconds=1)))
    monkeypatch.setattr(leases,'_check_actor',expiry_fault)
    with sending_transaction(s) as (db,_job):
        db.scalar(select(Job.id).where(Job.id==s.job_id))  # Start the actual BEGIN before arming the COMMIT fault.
        armed.append(True)
        with pytest.raises(leases.LeaseLost):db.commit()
        db.rollback()
    assert injected and original(s)==before


def test_final_dirty_success_and_cleared_lease_win_over_the_core_checkpoint_without_refresh(scene):
    s=scene;before=original(s)
    with sending_transaction(s) as (db,job):
        job.status='succeeded';job.lease_until=None
        job.data={**job.data,'finished_at':'synthetic worker-terminal payload'}
        db.commit()
    after=original(s)
    assert after['status']=='succeeded' and after['deadline'] is None and after['token']==before['token']
    assert after['data']=={**before['data'],'finished_at':'synthetic worker-terminal payload'}
    assert after['attempts']==before['attempts'] and after['version']==before['version']


def test_rollback_after_checkpoint_also_rolls_back_its_extended_deadline(scene):
    s=scene;before=original(s)
    with sending_transaction(s) as (db,_job):
        def fail_after_original_callback(session):raise RuntimeError('synthetic later commit failure')
        event.listen(db,'before_commit',fail_after_original_callback)
        with pytest.raises(RuntimeError,match='later commit failure'):db.commit()
        db.rollback()
    assert original(s)==before
