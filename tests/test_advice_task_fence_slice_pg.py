"""Disposable-PG-only feasibility slice; not a production migration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents.db import engine
from generic_agents.models import Audit, Plan, Task, uid
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled


def install_slice(db, p, task_ids):
    # The existing fixture's guard ensures this is a disposable test cluster.
    from generic_agents.same_goal_advice_prepare_writer_candidate_v2 import require_disposable_database
    require_disposable_database(db)
    db.execute(text('CREATE ROLE ga_slice_uncooperative NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'))
    db.execute(text('CREATE TABLE ga_slice_fence (workspace_id text NOT NULL, task_id text NOT NULL, PRIMARY KEY(workspace_id, task_id))'))
    for task_id in task_ids:
        db.execute(text('INSERT INTO ga_slice_fence VALUES (:w,:t)'), {'w':p.workspace_id,'t':task_id})
    db.execute(text('''CREATE FUNCTION ga_slice_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,public AS $$
    DECLARE w text; t text; oldw text; oldt text;
    BEGIN
      IF TG_TABLE_NAME='plan_versions' THEN
        IF TG_OP <> 'DELETE' THEN w:=NEW.workspace_id; t:=NEW.task_id; END IF;
        IF TG_OP <> 'INSERT' THEN oldw:=OLD.workspace_id; oldt:=OLD.task_id; END IF;
      ELSE
        IF TG_OP <> 'DELETE' AND NEW.action LIKE 'same_goal_advice.%' THEN
          w:=NEW.workspace_id; t:=NEW.data->>'task_id';
          IF t IS NULL THEN SELECT task_id INTO t FROM public.plan_versions WHERE id=NEW.object_id AND workspace_id=w; END IF;
          IF t IS NULL THEN RAISE EXCEPTION 'unresolved new task'; END IF;
        END IF;
        IF TG_OP <> 'INSERT' AND OLD.action LIKE 'same_goal_advice.%' THEN
          oldw:=OLD.workspace_id; oldt:=OLD.data->>'task_id';
          IF oldt IS NULL THEN SELECT task_id INTO oldt FROM public.plan_versions WHERE id=OLD.object_id AND workspace_id=oldw; END IF;
          IF oldt IS NULL THEN RAISE EXCEPTION 'unresolved old task'; END IF;
        END IF;
      END IF;
      IF oldt IS NOT NULL AND t IS NOT NULL AND (oldw,oldt) IS DISTINCT FROM (w,t) THEN
        RAISE EXCEPTION 'changing task association is unsupported';
      END IF;
      w:=coalesce(w,oldw); t:=coalesce(t,oldt);
      IF t IS NOT NULL THEN
        PERFORM 1 FROM public.ga_slice_fence WHERE workspace_id=w AND task_id=t FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'unregistered task'; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$'''))
    for table in ('plan_versions','audit_events'):
        db.execute(text(f'CREATE TRIGGER ga_slice_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ga_slice_guard()'))
        db.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO ga_slice_uncooperative'))
    db.commit()


def lock_task(db,p,task_id):
    assert db.scalar(text('SELECT 1 FROM ga_slice_fence WHERE workspace_id=:w AND task_id=:t FOR UPDATE'),{'w':p.workspace_id,'t':task_id})==1


def scan_v1_slice(db,p,task,exclude,*,page_size=64):
    """Narrow experiment: v1 plans and audit orphan check only, never a send gate."""
    lock_task(db,p,task.id)
    last=''; count=0
    while True:
        page=list(db.scalars(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id,
          Plan.id>last,Plan.id!=exclude).order_by(Plan.id).limit(page_size)))
        if not page: break
        for plan in page:
            legacy._saved(db,p,task,plan)
            last=plan.id;count+=1
            db.expunge(plan)
    orphan=db.scalar(text('''SELECT EXISTS(SELECT 1 FROM audit_events a
        WHERE a.workspace_id=:w AND a.action LIKE 'same_goal_advice.%'
          AND a.data->>'task_id'=:t
          AND a.action IN (:accepted,:cancelled)
          AND NOT EXISTS (SELECT 1 FROM plan_versions p WHERE p.id=a.object_id
             AND p.workspace_id=a.workspace_id AND p.task_id=:t))'''),
        {'w':p.workspace_id,'t':task.id,'accepted':legacy.ACCEPTED,'cancelled':legacy.CANCELLED})
    if orphan: raise ValueError('orphan')
    return count


def test_paged_v1_slice_task_fence_and_legacy_role(synthetic_custody):
    s=synthetic_custody;p=s['principal'];other=uid()
    with Session(engine()) as db:
        original=db.get(Task,s['task_id'])
        db.add(Task(id=other,workspace_id=p.workspace_id,owner_id=p.user_id,
            package_id=original.package_id,status='workflow_active',title='独立合成任务',data={}))
        for _ in range(1001):add_cancelled(db,s)
        # Deliberately place an unknown orphan before scanning. The narrow
        # v1-only slice below does not detect it, a release-blocking gap.
        db.add(Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,object_id=uid(),
            action='same_goal_advice.unknown_future_v99',data={'task_id':s['task_id']}))
        db.commit();install_slice(db,p,[s['task_id'],other])
    ready=Event();done=Event();writer_pids=[]
    def writer(task_id, signal):
        with Session(engine()) as db:
            db.execute(text('SET LOCAL ROLE ga_slice_uncooperative'))
            writer_pids.append(db.scalar(text('SELECT pg_backend_pid()')))
            ready.set()
            db.add(Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,object_id=uid(),
                action=legacy.CANCELLED,data={'task_id':task_id}))
            db.commit();signal.set()
    with Session(engine()) as reader,ThreadPoolExecutor(max_workers=2) as pool:
        assert reader.scalar(text('SHOW transaction_isolation'))=='read committed'
        task=reader.get(Task,s['task_id'])
        # This result is NOT an authorization proof: the unknown orphan above
        # is intentionally outside the experiment's action filter.
        assert scan_v1_slice(reader,p,task,s['plan_id'])==1001
        reader_pid=reader.scalar(text('SELECT pg_backend_pid()'))
        pending=pool.submit(writer,task.id,done)
        assert ready.wait(5)
        deadline=monotonic()+5
        with Session(engine()) as observer:
            while monotonic()<deadline:
                observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                waiting=observer.scalar(text('SELECT :blocker = ANY(pg_blocking_pids(:pid))'),
                    {'pid':writer_pids[0],'blocker':reader_pid})
                if waiting:break
                sleep(.02)
            assert waiting and not done.is_set()
        independent=Event(); pool.submit(writer,other,independent).result(timeout=5)
        assert independent.is_set() and not done.is_set()
        reader.commit();pending.result(timeout=5);assert done.is_set()
    with Session(engine()) as db:
        with pytest.raises(ValueError,match='orphan'):scan_v1_slice(db,p,db.get(Task,s['task_id']),s['plan_id'])
    with Session(engine()) as db:
        db.execute(text('SET LOCAL ROLE ga_slice_uncooperative'))
        with pytest.raises(DBAPIError):
            db.execute(text('ALTER TABLE audit_events DISABLE TRIGGER ga_slice_guard'))
        db.rollback()
    with Session(engine()) as db:
        db.execute(text('SET LOCAL ROLE ga_slice_uncooperative'))
        with pytest.raises(DBAPIError):
            db.execute(text('TRUNCATE audit_events'))
        db.rollback()
    # An old UPDATE can hold a row lock before its BEFORE trigger asks for the
    # fence. A newer reader that takes the fence first and then this row would
    # deadlock; timeout and rollback must release the gate and the old write.
    row_held=Event()
    def row_first_old_writer():
        with Session(engine()) as db:
            db.execute(text('SET LOCAL ROLE ga_slice_uncooperative'))
            db.execute(text('SELECT id FROM plan_versions WHERE id=:id FOR UPDATE'),{'id':s['plan_id']})
            row_held.set()
            db.execute(text('UPDATE plan_versions SET status=status WHERE id=:id'),{'id':s['plan_id']})
            db.rollback()
    with Session(engine()) as reader,ThreadPoolExecutor(max_workers=1) as pool:
        lock_task(reader,p,s['task_id'])
        pending=pool.submit(row_first_old_writer)
        assert row_held.wait(5)
        reader.execute(text("SET LOCAL lock_timeout='300ms'"))
        with pytest.raises(DBAPIError):
            reader.execute(text('SELECT id FROM plan_versions WHERE id=:id FOR UPDATE'),{'id':s['plan_id']})
        reader.rollback()
        pending.result(timeout=5)
