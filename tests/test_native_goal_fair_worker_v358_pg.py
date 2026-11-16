"""Isolated PG regression for opt-in scoped scheduling; no production migration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job, Task, Membership
from generic_agents.native_goal_fair_state_v358 import NativeGoalFairStateV358, require_schema
from generic_agents.native_goal_fair_worker_v358 import fair_tick
from tests.test_native_goals import setup, define


def goal(client):
    release, _, task, base, body = setup(client)
    define(client, base, body)
    with Session(engine()) as db:
        job = db.get(Task, task['id']).data['native_goal_check_job_pin']['job_id']
    return release['instance_id'], task['id'], job


def test_migration_schema_and_real_scoped_claim(client, account):
    instance, task, job = goal(client)
    _, other_task, other_job = goal(client)
    with engine().connect() as connection: require_schema(connection)
    result = fair_tick(account[2], instance, task, Event())
    assert result['processed'] == 1
    with Session(engine()) as db:
        assert db.get(Job, job).status == 'succeeded'
        assert db.get(Job, other_job).status == 'queued'
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.workspace_id == account[2], NativeGoalFairStateV358.task_id == task))
        assert state and state.data['task'] == task and state.data['instance'] == instance
        assert db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == other_task)) is None


def test_restart_persists_schedule_and_empty_snapshot_is_not_declared_empty(client, account):
    instance, task, _ = goal(client)
    fair_tick(account[2], instance, task, Event())
    result = fair_tick(account[2], instance, task, Event())
    assert result['retry_mode'] in {'scheduled_rescan', 'continue_bounded'}
    with Session(engine()) as db:
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        before = state.version
    fair_tick(account[2], instance, task, Event())
    with Session(engine()) as db:
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        assert state.version >= before and state.data['task'] == task
        if state.next_due_at is not None:
            before = state.version
    if state.next_due_at is not None:
        result = fair_tick(account[2], instance, task, Event())
        assert result['retry_mode'] == 'scheduled_rescan'
        with Session(engine()) as db:
            state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
            assert state.version == before


def test_two_competing_workers_do_not_double_claim(client, account):
    instance, task, job = goal(client)
    def run():
        try: return fair_tick(account[2], instance, task, Event())
        except DBAPIError: return {'processed':0}  # lock timeout is fail-closed
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))
    assert sum(result['processed'] for result in outcomes) == 1
    with Session(engine()) as db:
        original = db.get(Job, job)
        assert original.status == 'succeeded' and original.attempts == 1
        assert len(list(db.scalars(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task)))) == 1


def test_revoked_membership_is_not_claimed(client, account):
    instance, task, job = goal(client)
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        db.delete(membership); db.commit()
    result = fair_tick(account[2], instance, task, Event())
    assert result['processed'] == 0 and result['blocked'] >= 1
    with Session(engine()) as db:
        original = db.get(Job, job)
        assert original.status == 'queued' and original.attempts == 0


def test_corrupt_state_and_cross_instance_are_refused(client, account):
    instance, task, _ = goal(client)
    fair_tick(account[2], instance, task, Event())
    with Session(engine()) as db:
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        data = dict(state.data); data['workspace'] = str(uuid4()); state.data = data; state.next_due_at = None; db.commit()
    with pytest.raises(ValueError): fair_tick(account[2], instance, task, Event())
    with pytest.raises((ValueError, HTTPException)): fair_tick(account[2], str(uuid4()), task, Event())


def test_missing_and_incompatible_schema_are_rejected_without_ddl_at_runtime():
    from sqlalchemy import text
    with engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text('ALTER TABLE native_goal_fair_states_v358 RENAME TO fair_state_temporarily_hidden_v358'))
            with pytest.raises(RuntimeError, match='尚未迁移'): require_schema(connection)
        finally: transaction.rollback()
    with engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text('ALTER TABLE native_goal_fair_states_v358 DROP CONSTRAINT uq_goal_fair_workspace_task_v358'))
            with pytest.raises(RuntimeError, match='唯一约束'): require_schema(connection)
        finally: transaction.rollback()


def test_other_workspace_and_invalid_version_fail_closed(client, account):
    from generic_agents.models import Workspace
    instance, task, _ = goal(client)
    fair_tick(account[2], instance, task, Event())
    with Session(engine()) as db:
        other = Workspace(name='other isolated workspace'); db.add(other); db.flush(); other_id = other.id
        row = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        row.version = 0; row.next_due_at = None; db.commit()
    with pytest.raises(ValueError): fair_tick(account[2], instance, task, Event())
    with pytest.raises(HTTPException): fair_tick(other_id, instance, task, Event())
    with Session(engine()) as db:
        assert db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.workspace_id == other_id)) is None


def test_ordinary_check_gets_opportunity_amid_unproven_candidate_backlog(client, account, monkeypatch):
    """Real PG pagination and ordinary proof; fake candidates receive no authority."""
    import generic_agents.native_goal_fair_worker_v358 as fair
    from generic_agents.native_goals import KIND
    instance, task, job = goal(client)
    with Session(engine()) as db:
        context_hash = db.get(Task, task).data['native_goal_context_hash']
        for _ in range(110):
            db.add(Job(workspace_id=account[2],kind=KIND,status='queued',
                       idempotency_key='native-goal:'+task+':'+context_hash+':candidate-v299:'+str(uuid4()),
                       data={'task_id':task}))
        db.commit()
    original = fair._prove
    monkeypatch.setattr(fair, '_prove', lambda db,w,i,t,j: original(db,w,i,t,j) if j == job else None)
    results = [fair_tick(account[2], instance, task, Event()) for _ in range(8)]
    assert sum(r['processed'] for r in results) == 1
    assert sum(r['waiting'] for r in results) > 0
    with Session(engine()) as db:
        assert db.get(Job, job).status == 'succeeded'
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        assert state.version > 1 and state.data['workspace'] == account[2]


def test_same_horizon_arrival_before_persisted_cursor_is_rescanned(client, account, monkeypatch):
    """Proof port is deliberately synthetic; this checks PG pagination/restart only."""
    import generic_agents.native_goal_fair_worker_v358 as fair
    from generic_agents.native_goals import KIND
    from sqlalchemy import func
    from datetime import timedelta
    instance, task, _ = goal(client)
    with Session(engine()) as db:
        original = db.get(Task, task)
        now = db.scalar(select(func.clock_timestamp())); available = now - timedelta(seconds=2)
        prefix = 'native-goal:'+task+':'+original.data['native_goal_context_hash']+':candidate-v299:'
        late = str(uuid4()); later = str(uuid4())
        db.add_all([Job(id=late,workspace_id=account[2],kind=KIND,status='queued',available_at=available,created_at=now,
                       idempotency_key=prefix+'late',data={'task_id':task}),
                    Job(id=later,workspace_id=account[2],kind=KIND,status='queued',available_at=now-timedelta(seconds=1),created_at=now,
                       idempotency_key=prefix+'later',data={'task_id':task})])
        data = fair._initial(account[2],instance,task);data['next_lane']=2
        data['lanes']['candidate']={'horizon':now.isoformat(),'cursor':[available.isoformat(),'ffffffff-ffff-ffff-ffff-ffffffffffff']}
        db.add(NativeGoalFairStateV358(workspace_id=account[2],instance_id=instance,task_id=task,data=data,version=1));db.commit()
    seen=[]
    monkeypatch.setattr(fair,'_prove',lambda db,w,i,t,j: seen.append(j) or None)
    fair_tick(account[2],instance,task,Event())
    assert later in seen and late not in seen
    with Session(engine()) as db:
        state=db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id==task))
        # Simulate the scheduled deadline having elapsed; state survives the new Session.
        state.next_due_at=None;db.commit()
    fair_tick(account[2],instance,task,Event())
    assert late in seen


def test_persisted_version_row_lock_conflict_fails_closed(client, account):
    """A second worker cannot overwrite a concurrent locked state update."""
    instance, task, _ = goal(client)
    fair_tick(account[2], instance, task, Event())
    with Session(engine()) as locked:
        row = locked.scalar(select(NativeGoalFairStateV358).where(
            NativeGoalFairStateV358.workspace_id == account[2], NativeGoalFairStateV358.task_id == task).with_for_update())
        old = row.version
        def contender():
            with pytest.raises(DBAPIError): fair_tick(account[2], instance, task, Event())
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(contender).result(timeout=5)
        row.version = old + 1; locked.commit()
    with Session(engine()) as db:
        row = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        assert row.version == old + 1


def test_corrupt_far_future_retry_is_not_treated_as_valid_idle(client, account):
    from sqlalchemy import func
    from datetime import timedelta
    instance, task, _ = goal(client)
    fair_tick(account[2], instance, task, Event())
    with Session(engine()) as db:
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.task_id == task))
        state.next_due_at = db.scalar(select(func.clock_timestamp())) + timedelta(days=1); db.commit()
    with pytest.raises(ValueError, match='超出上限'): fair_tick(account[2], instance, task, Event())


def test_wrong_schema_foreign_key_is_rejected_by_target_oid():
    from sqlalchemy import inspect, text
    with engine().connect() as connection:
        tx=connection.begin()
        try:
            fks=inspect(connection).get_foreign_keys('native_goal_fair_states_v358',schema='public')
            fk=next(item['name'] for item in fks if item['constrained_columns']==['task_id'])
            quote=connection.dialect.identifier_preparer.quote
            connection.execute(text('CREATE SCHEMA fair_shadow_v358'))
            connection.execute(text('CREATE TABLE fair_shadow_v358.tasks (id varchar(36) PRIMARY KEY)'))
            connection.execute(text('ALTER TABLE public.native_goal_fair_states_v358 DROP CONSTRAINT '+quote(fk)))
            connection.execute(text('ALTER TABLE public.native_goal_fair_states_v358 ADD CONSTRAINT test_wrong_target_v358 FOREIGN KEY (task_id) REFERENCES fair_shadow_v358.tasks(id) NOT VALID'))
            with pytest.raises(RuntimeError,match='外键目标'): require_schema(connection)
        finally: tx.rollback()


def test_unvalidated_public_foreign_key_is_rejected():
    from sqlalchemy import inspect, text
    with engine().connect() as connection:
        tx=connection.begin()
        try:
            fks=inspect(connection).get_foreign_keys('native_goal_fair_states_v358',schema='public')
            fk=next(item['name'] for item in fks if item['constrained_columns']==['task_id'])
            quote=connection.dialect.identifier_preparer.quote
            connection.execute(text('ALTER TABLE public.native_goal_fair_states_v358 DROP CONSTRAINT '+quote(fk)))
            connection.execute(text('ALTER TABLE public.native_goal_fair_states_v358 ADD CONSTRAINT test_unvalidated_v358 FOREIGN KEY (task_id) REFERENCES public.tasks(id) NOT VALID'))
            with pytest.raises(RuntimeError,match='外键目标'): require_schema(connection)
        finally: tx.rollback()


def test_wrong_column_type_is_rejected():
    from sqlalchemy import text
    with engine().connect() as connection:
        tx=connection.begin()
        try:
            connection.execute(text('ALTER TABLE public.native_goal_fair_states_v358 ALTER COLUMN instance_id TYPE varchar(35)'))
            with pytest.raises(RuntimeError,match='长度不匹配'): require_schema(connection)
        finally: tx.rollback()


def test_invalid_unique_index_metadata_fails_closed_without_catalog_mutation(monkeypatch):
    """Synthetic catalog response only; not a real corrupt PostgreSQL index."""
    import generic_agents.native_goal_fair_state_v358 as module
    from sqlalchemy.types import TypeDecorator
    table=NativeGoalFairStateV358.__table__
    class FakeInspector:
        def has_table(self,*a,**kw): return True
        def get_columns(self,*a,**kw):
            return [{'name':c.name,'nullable':c.nullable,'type':c.type.impl if isinstance(c.type,TypeDecorator) else c.type} for c in table.columns]
        def get_unique_constraints(self,*a,**kw): return [{'column_names':['workspace_id','task_id']}]
        def get_pk_constraint(self,*a,**kw): return {'constrained_columns':['id']}
        def get_foreign_keys(self,*a,**kw):
            return [{'constrained_columns':[source],'referred_table':target,'referred_columns':['id']} for source,target in [('workspace_id','workspaces'),('task_id','tasks')]]
    class Connection:
        dialect=type('Dialect',(),{'name':'postgresql'})()
        def scalar(self,statement,parameters=None):
            sql=str(statement)
            if 'current_schema' in sql: return 'public'
            if "c.contype='f'" in sql: return True
            if "c.contype='u'" in sql: return False
            raise AssertionError(sql)
    monkeypatch.setattr(module,'inspect',lambda _:FakeInspector())
    with pytest.raises(RuntimeError,match='唯一索引无效'): module.require_schema(Connection())


def test_stop_does_not_preempt_a_slow_in_progress_proof(client, account, monkeypatch):
    """A slow proof can exceed the soft budget; stop prevents later claim."""
    import generic_agents.native_goal_fair_worker_v358 as fair
    from threading import Event as Signal
    instance, task, job = goal(client)
    with Session(engine()) as db:
        state=fair._initial(account[2],instance,task);state['next_lane']=4
        db.add(NativeGoalFairStateV358(workspace_id=account[2],instance_id=instance,task_id=task,data=state,version=1));db.commit()
    entered=Signal();release=Signal();stopped=Signal()
    def slow_proof(db,w,i,t,j):
        assert j == job
        entered.set()
        assert release.wait(5)
        return None
    monkeypatch.setattr(fair,'_prove',slow_proof)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(fair_tick,account[2],instance,task,stopped)
        try:
            assert entered.wait(5), 'proof was not reached within the test limit'
            stopped.set()
            # Exceed 0.5s soft budget while the proof is still held.
            assert not release.wait(.6) and not future.done()
        finally:
            release.set()
        assert future.result(timeout=5)['processed']==0
    with Session(engine()) as db:
        row=db.get(Job,job);assert row.status=='queued' and row.attempts==0
