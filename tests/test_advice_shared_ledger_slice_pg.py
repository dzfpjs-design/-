"""Shared-ledger feasibility against real code in a disposable synthetic PG."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from generic_agents.cloud_budget import finish_tx, settle_paid_response
from generic_agents.db import engine
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Capability, CloudCall, Job, Task, Workspace, uid
from generic_agents.same_goal_advice_prepare_writer_candidate_v2 import require_disposable_database
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def bind(db, workspace, task, call, job):
    db.execute(text('INSERT INTO ga_slice_call_binding VALUES (:w,:t,:c,:j)'),
        {'w':workspace,'t':task,'c':call,'j':job})


def test_shared_daily_ledger_and_scoped_call_binding(synthetic_custody):
    s=synthetic_custody;p=s['principal'];day='2026-01-17';other_workspace=uid()
    tasks=[s['task_id'],uid(),uid()];jobs=[s['job_id'],uid(),uid()]
    calls=[uid() for _ in range(4)];unbound=[uid(),uid()];ledgers=[uid(),uid()]
    expected=[];receipts=[]
    with Session(engine()) as db:
        require_disposable_database(db)
        first=db.get(Task,tasks[0]);cap=db.get(Capability,first.package_id);other_cap=uid()
        db.add(Workspace(id=other_workspace,name='合成工作区'))
        db.flush()
        db.add(Capability(id=other_cap,workspace_id=other_workspace,namespace='slice',name='package',
            release_version='1',kind=cap.kind,status=cap.status,content_hash=cap.content_hash,
            validated_hash=cap.validated_hash,data=cap.data))
        db.flush()
        db.add_all([Task(id=tasks[1],workspace_id=p.workspace_id,owner_id=p.user_id,
            package_id=cap.id,status='workflow_active',title='第二合成任务',data={}),
          Task(id=tasks[2],workspace_id=other_workspace,owner_id=p.user_id,
            package_id=other_cap,status='workflow_active',title='另一工作区任务',data={})])
        db.add_all([Job(id=ledgers[0],workspace_id=p.workspace_id,kind='cloud_budget',status='internal',
            idempotency_key='cloud-budget:'+day,data={'day':day,'tokens':260,'calls':3}),
          Job(id=ledgers[1],workspace_id=other_workspace,kind='cloud_budget',status='internal',
            idempotency_key='cloud-budget:'+day,data={'day':day,'tokens':100,'calls':1})])
        db.add_all([Job(id=jobs[1],workspace_id=p.workspace_id,kind='synthetic_advice',status='queued',
            idempotency_key='synthetic:'+jobs[1],data={'task_id':tasks[1]}),
          Job(id=jobs[2],workspace_id=other_workspace,kind='synthetic_advice',status='queued',
            idempotency_key='synthetic:'+jobs[2],data={'task_id':tasks[2]})])
        for i,(w,c,r) in enumerate([(p.workspace_id,cap.id,100),(p.workspace_id,cap.id,100),
                                    (other_workspace,other_cap,100),(p.workspace_id,cap.id,60)]):
            source={'workspace_id':w,'capability_id':c,'actor_id':p.user_id,
                'request_hash':format(i+1,'064x'),'dispatch_options_hash':format(i+11,'064x'),
                'provider':'synthetic','model':'synthetic-model'}
            expected.append(source)
            receipts.append({'call_id':calls[i],'provider':'synthetic','model':'synthetic-model',
                'request_id':'synthetic-'+str(i),'usage':{'total_tokens':[30,40,50,10][i]}})
            db.add(CloudCall(id=calls[i],workspace_id=w,capability_id=c,status='reserved',
                reserved_tokens=r,charged_tokens=r,data={'day':day,**source}))
        for identity,w,c in ((unbound[0],p.workspace_id,cap.id),(unbound[1],other_workspace,other_cap)):
            db.add(CloudCall(id=identity,workspace_id=w,capability_id=c,status='synthetic_unbound',
                reserved_tokens=0,charged_tokens=0,data={'day':day}))
        db.flush()
        # These indexes and bindings exist only in the dedicated cluster.
        db.execute(text('CREATE UNIQUE INDEX ga_slice_task_workspace ON tasks(workspace_id,id)'))
        db.execute(text('CREATE UNIQUE INDEX ga_slice_call_workspace ON cloud_model_calls(workspace_id,id)'))
        db.execute(text('CREATE UNIQUE INDEX ga_slice_job_workspace ON jobs(workspace_id,id)'))
        db.execute(text('''CREATE TABLE ga_slice_task_job(
          workspace_id varchar(36) NOT NULL,task_id varchar(36) NOT NULL,job_id varchar(36) NOT NULL,
          PRIMARY KEY(workspace_id,task_id,job_id),UNIQUE(workspace_id,job_id),
          FOREIGN KEY(workspace_id,task_id) REFERENCES tasks(workspace_id,id),
          FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id))'''))
        db.execute(text('''CREATE TABLE ga_slice_call_binding(
          workspace_id varchar(36) NOT NULL,task_id varchar(36) NOT NULL,call_id varchar(36) NOT NULL,
          job_id varchar(36) NOT NULL,
          PRIMARY KEY(workspace_id,call_id), UNIQUE(call_id),
          FOREIGN KEY(workspace_id,task_id,job_id) REFERENCES ga_slice_task_job(workspace_id,task_id,job_id),
          FOREIGN KEY(workspace_id,call_id) REFERENCES cloud_model_calls(workspace_id,id))'''))
        for w,t,j in ((p.workspace_id,tasks[0],jobs[0]),(p.workspace_id,tasks[1],jobs[1]),
                      (other_workspace,tasks[2],jobs[2])):
            db.execute(text('INSERT INTO ga_slice_task_job VALUES (:w,:t,:j)'),{'w':w,'t':t,'j':j})
        for i,t in enumerate((tasks[0],tasks[1],tasks[2],tasks[0])):
            bind(db,expected[i]['workspace_id'],t,calls[i],jobs[(0,1,2,0)[i]])
        db.commit()
    # Both directions of cross-workspace mismapping fail at the database FK.
    for workspace,task,call,job in ((p.workspace_id,tasks[2],unbound[0],jobs[2]),
             (other_workspace,tasks[0],unbound[1],jobs[0]),
             (p.workspace_id,tasks[0],unbound[1],jobs[0]),
             (other_workspace,tasks[2],unbound[0],jobs[2]),
             (p.workspace_id,tasks[0],unbound[0],jobs[1])):
        with Session(engine()) as db:
            with pytest.raises(IntegrityError) as error:bind(db,workspace,task,call,job)
            assert error.value.orig.sqlstate=='23503'
            db.rollback()
    # Composite FKs do not force an old writer to create a binding at all.
    with Session(engine()) as db:
        assert db.scalar(text('''SELECT count(*) FROM cloud_model_calls c
          LEFT JOIN ga_slice_call_binding b ON b.call_id=c.id WHERE c.id IN (:a,:b)
          AND b.call_id IS NULL'''),{'a':unbound[0],'b':unbound[1]})==2
    # Even when directly addressing another workspace's call, the real
    # settlement refuses a forged expected workspace before changing money.
    with Session(engine()) as db:
        with pytest.raises(ModelBlocked):
            settle_paid_response(db,calls[2],receipts[2],{**expected[2],'workspace_id':p.workspace_id},'succeeded')
        db.rollback()
    started=[Event(),Event()];pids=[None,None]
    def settle(index,slot):
        with Session(engine()) as db:
            if slot is not None:
                pids[slot]=db.scalar(text('SELECT pg_backend_pid()'));started[slot].set()
            settle_paid_response(db,calls[index],receipts[index],expected[index],'succeeded')
            db.commit()
    with Session(engine()) as first,ThreadPoolExecutor(max_workers=3) as pool:
        settle_paid_response(first,calls[0],receipts[0],expected[0],'succeeded')
        first_pid=first.scalar(text('SELECT pg_backend_pid()'))
        shared=pool.submit(settle,1,0)
        replay=pool.submit(settle,0,1)
        assert all(item.wait(5) for item in started)
        independent=pool.submit(settle,2,None);independent.result(timeout=5)
        deadline=monotonic()+5;blocked=[False,False]
        with Session(engine()) as observer:
            while monotonic()<deadline:
                blocked=[observer.scalar(text('SELECT :pid = ANY(pg_blocking_pids(:other))'),
                    {'pid':first_pid,'other':pid}) for pid in pids]
                if all(blocked):break
                sleep(.02)
        assert all(blocked) and not shared.done() and not replay.done()
        first.commit();shared.result(timeout=5);replay.result(timeout=5)
    with Session(engine()) as db:
        assert db.get(Job,ledgers[0]).data=={'day':day,'tokens':130,'calls':3}
        assert db.get(Job,ledgers[1]).data=={'day':day,'tokens':50,'calls':1}
        assert [db.get(CloudCall,c).charged_tokens for c in calls]==[30,40,50,60]
    # Rollback must leave both original reservation and shared ledger intact.
    with Session(engine()) as db:
        settle_paid_response(db,calls[3],receipts[3],expected[3],'succeeded');db.rollback()
    # Legacy finish accepts only a call id, with no workspace or task
    # argument; existing callers need migration, not just a new mapping table.
    with Session(engine()) as db:
        assert finish_tx(db,calls[3],{'usage':{'total_tokens':9}},'succeeded')
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,ledgers[0]).data['tokens']==130
        assert db.get(CloudCall,calls[3]).status=='reserved'
        assert db.get(CloudCall,calls[3]).charged_tokens==60
    # A limited trigger experiment demonstrates that old, unbound calls fail
    # closed on UPDATE. It cannot protect INSERT, generic Job writes or files.
    with Session(engine()) as db:
        admin_role=db.scalar(text('SELECT session_user'))
        db.execute(text('''CREATE TABLE ga_slice_task_fence(
          workspace_id varchar(36),task_id varchar(36),PRIMARY KEY(workspace_id,task_id))'''))
        db.execute(text('INSERT INTO ga_slice_task_fence VALUES (:w,:t)'),{'w':p.workspace_id,'t':tasks[0]})
        db.execute(text('''CREATE FUNCTION ga_slice_call_fence() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $$
          DECLARE t varchar(36); BEGIN
          IF TG_OP='UPDATE' AND (NEW.workspace_id,NEW.id) IS DISTINCT FROM (OLD.workspace_id,OLD.id)
            THEN RAISE EXCEPTION 'call identity changed'; END IF;
          SELECT task_id INTO t FROM ga_slice_call_binding
            WHERE workspace_id=OLD.workspace_id AND call_id=OLD.id;
          IF t IS NULL THEN RAISE EXCEPTION 'unbound cloud call'; END IF;
          PERFORM 1 FROM ga_slice_task_fence WHERE workspace_id=OLD.workspace_id AND task_id=t FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'unregistered fence'; END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
          END $$'''))
        db.execute(text('''CREATE TRIGGER ga_slice_call_fence BEFORE UPDATE OR DELETE ON cloud_model_calls
            FOR EACH ROW EXECUTE FUNCTION ga_slice_call_fence()'''))
        db.execute(text('CREATE ROLE ga_slice_legacy LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'))
        db.execute(text('GRANT SELECT,UPDATE ON cloud_model_calls TO ga_slice_legacy'))
        db.commit()
    # Unlike SET ROLE inside a superuser session, this connection's session
    # identity really is the unprivileged legacy login. The function owner is
    # privileged only in this throwaway experiment, not a deployment design.
    old_engine=create_engine(engine().url.set(username='ga_slice_legacy'))
    try:
        with Session(old_engine) as db:
            assert db.execute(text('SELECT session_user,current_user')).one()==('ga_slice_legacy','ga_slice_legacy')
            with pytest.raises(DBAPIError) as error:
                db.execute(text('UPDATE cloud_model_calls SET status=status WHERE id=:id'),{'id':unbound[0]})
            assert 'unbound cloud call' in str(error.value.orig);db.rollback()
        for statement in ('ALTER TABLE cloud_model_calls DISABLE TRIGGER ga_slice_call_fence',
                          'TRUNCATE cloud_model_calls',
                          'SET ROLE '+engine().dialect.identifier_preparer.quote(admin_role)):
            with Session(old_engine) as db:
                with pytest.raises(DBAPIError):db.execute(text(statement))
                db.rollback()
    finally:
        old_engine.dispose()
    # Existing settlement takes the CloudCall row before this trigger can
    # acquire its task fence. Show the old/new inverse order and safe rollback.
    held=Event()
    def old_row_first():
        with Session(engine()) as db:
            db.execute(text('SELECT id FROM cloud_model_calls WHERE id=:id FOR UPDATE'),{'id':calls[3]})
            held.set()
            db.execute(text('UPDATE cloud_model_calls SET status=status WHERE id=:id'),{'id':calls[3]})
            db.rollback()
    with Session(engine()) as reader,ThreadPoolExecutor(max_workers=1) as pool:
        reader.execute(text('SELECT 1 FROM ga_slice_task_fence WHERE workspace_id=:w AND task_id=:t FOR UPDATE'),
            {'w':p.workspace_id,'t':tasks[0]})
        future=pool.submit(old_row_first);assert held.wait(5)
        reader.execute(text("SET LOCAL lock_timeout='300ms'"))
        with pytest.raises(DBAPIError):
            reader.execute(text('SELECT id FROM cloud_model_calls WHERE id=:id FOR UPDATE'),{'id':calls[3]})
        reader.rollback();future.result(timeout=5)
    with Session(engine()) as db:
        assert db.get(Job,ledgers[0]).data['tokens']==130
        assert db.get(CloudCall,calls[3]).status=='reserved'
