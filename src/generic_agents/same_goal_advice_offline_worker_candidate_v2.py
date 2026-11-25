"""Opt-in isolated worker for approved packet preparation only.

Never reads a provider credential or sends a model request. This process stays
restricted to a verified disposable PG; it is not installed by app startup.
"""
import os
import signal
import time
from uuid import uuid4
from contextlib import contextmanager
from threading import Event, Lock, Thread
from sqlalchemy import func, or_, select, text, tuple_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from .config import settings
from .db import engine
from .models import Audit, Job, uid
from . import leases, worker as runtime_worker
from . import same_goal_advice_document_lease_candidate_v2 as control
from . import same_goal_advice_offline_grant_candidate_v2 as grants
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from .temporal import instant
from .contracts import digest

HEARTBEAT_KIND='offline_doc_worker_heartbeat_v2'
HEARTBEAT_PREFIX='offline-doc-worker-heartbeat-v2:'
WORKER_ID=str(uuid4())
HEARTBEAT_KEY=HEARTBEAT_PREFIX+WORKER_ID
HEARTBEAT_INTERVAL_SECONDS=5
_CURSOR=None
_CURSOR_LOCK=Lock()
FAILURE='same_goal_advice.offline_doc_worker_failed_v2'


def _check(db):
    if not (settings().offline_document_delegation_enabled and settings().offline_document_worker_enabled):
        raise RuntimeError('离线资料整理 worker 未启用')
    guard.require_disposable_database(db)


def _heartbeat(db,workspace,lease=None):
    """Record liveness only; never change the original control lease."""
    clock=db.scalar(select(func.clock_timestamp()))
    if lease is not None:
        actual=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==lease['control_id'],
            Job.kind==control.KIND,Job.status=='running',Job.lease_token==lease['lease_token'],
            Job.lease_until>clock).execution_options(populate_existing=True))
        if actual is None:return False
        state={'state':'processing','control_id':actual.id,'lease_hash':digest(actual.lease_token),
            'lease_until':actual.lease_until.isoformat()}
    else:state={'state':'idle','control_id':None,'lease_hash':None,'lease_until':None}
    value={'checked_at':clock.isoformat(),'worker_id':WORKER_ID,'pid':os.getpid(),**state}
    db.execute(insert(Job).values(workspace_id=workspace,kind=HEARTBEAT_KIND,status='internal',
        idempotency_key=HEARTBEAT_KEY,data=value).on_conflict_do_nothing(
        index_elements=['workspace_id','idempotency_key']))
    row=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.idempotency_key==HEARTBEAT_KEY)
        .with_for_update().execution_options(populate_existing=True))
    if row.kind!=HEARTBEAT_KIND or row.status!='internal':raise RuntimeError('后台心跳记录不一致')
    row.data=value;row.version+=1
    return True


def stop_heartbeat():
    """Stop only this process's heartbeats; crash still expires by TTL."""
    with Session(engine()) as db:
        _check(db)
        rows=list(db.scalars(select(Job).where(Job.kind==HEARTBEAT_KIND,Job.idempotency_key==HEARTBEAT_KEY)
            .with_for_update()))
        for row in rows:row.data={**row.data,'state':'stopped'};row.version+=1
        db.commit()


def _candidates(db):
    now=db.scalar(select(func.clock_timestamp()))
    global _CURSOR
    eligible=or_(Job.status=='queued',
        (Job.status=='running') & Job.lease_until.is_not(None) & (Job.lease_until<now))
    # Progress past blocked or revoked controls so they cannot starve newer
    # eligible work. A restart safely starts the scan again at the beginning.
    with _CURSOR_LOCK:
        query=select(Job).where(Job.kind==control.KIND,eligible)
        if _CURSOR is not None:query=query.where(tuple_(Job.created_at,Job.id)>_CURSOR)
        rows=list(db.scalars(query.order_by(Job.created_at,Job.id).limit(50)))
        if not rows and _CURSOR is not None:
            _CURSOR=None
            rows=list(db.scalars(select(Job).where(Job.kind==control.KIND,eligible)
                .order_by(Job.created_at,Job.id).limit(50)))
        _CURSOR=(rows[-1].created_at,rows[-1].id) if rows else None
    work=[]
    for row in rows:
        target=row.data.get('target',{}) if type(row.data) is dict else {}
        events=list(db.scalars(select(Audit).where(Audit.workspace_id==row.workspace_id,
            Audit.object_id==target.get('plan_id'),Audit.action==grants.GRANT).order_by(Audit.created_at.desc(),Audit.id.desc()).limit(50)))
        for event in events:
            value=event.data
            if (type(value) is not dict or value.get('job_id')!=target.get('job_id')
                    or value.get('actor_id')!=row.data.get('actor_id')):continue
            try:
                if instant(value.get('expires_at'))<=now:continue
            except (ValueError,TypeError,AttributeError):continue
            revoked=db.scalar(select(Audit.id).where(Audit.workspace_id==row.workspace_id,
                Audit.action==grants.REVOKE,Audit.object_id==event.id).limit(1))
            if revoked is None:work.append((row.id,event.id,row.workspace_id));break
    return work


@contextmanager
def _keep_heartbeat(workspace):
    # Actual source validation may take minutes; do not claim the worker is
    # offline solely because one legitimate job is still processing.
    stop=Event();state={'lease':None}
    def pulse():
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                with Session(engine()) as db:
                    _check(db)
                    db.execute(text("SET LOCAL lock_timeout = '1s'"))
                    db.execute(text("SET LOCAL statement_timeout = '2s'"))
                    _heartbeat(db,workspace,state['lease']);db.commit()
            except Exception:
                pass
    thread=Thread(target=pulse,daemon=True);thread.start()
    try:yield state
    finally:
        stop.set();thread.join(timeout=3)


def tick():
    """Run one bounded sweep; returned counts contain no source text."""
    with Session(engine()) as db:
        _check(db)
        workspaces=list(db.scalars(select(Job.workspace_id).where(Job.kind==control.KIND).distinct()))
        for workspace in workspaces:_heartbeat(db,workspace)
        work=_candidates(db);db.commit()
    completed=0
    for control_id,grant_id,workspace in work:
        lease=None
        heartbeat=_keep_heartbeat(workspace)
        heartbeat_state=heartbeat.__enter__()
        try:
            with Session(engine()) as db:
                _check(db);lease=grants.claim_worker(db,control_id,grant_id);db.commit()
            heartbeat_state['lease']=lease
            with Session(engine()) as db:_heartbeat(db,workspace,lease);db.commit()
            with Session(engine()) as source:
                actor=runtime_worker.actor(source,lease['workspace_id'],lease['actor_id'])
            with Session(engine()) as db:
                db.info['worker_actor']=actor;leases.attach(db,control_id,lease['lease_token'])
                grants.process_packet(db,actor,control_id,lease['lease_token'],grant_id);db.commit()
            with Session(engine()) as db:
                grants.settle_worker(db,control_id,lease['lease_token'],grant_id);db.commit()
            completed+=1
        except Exception as error:
            kind='access_or_source' if isinstance(error,HTTPException) else 'database' if isinstance(error,SQLAlchemyError) else 'internal'
            # Record only a generic, deduplicated status. Never persist the
            # exception, source material, credentials or lease token.
            try:
                with Session(engine()) as db:
                    _check(db)
                    ctl=db.scalar(select(Job).where(Job.id==control_id,Job.kind==control.KIND).with_for_update())
                    grant=db.get(Audit,grant_id)
                    clock=db.scalar(select(func.clock_timestamp()))
                    competing=bool(ctl and lease is None and ctl.status=='running'
                        and ctl.lease_until is not None and ctl.lease_until>clock)
                    changed=bool(ctl and lease is not None and ctl.lease_token!=lease['lease_token'])
                    if ctl is not None and grant is not None and grant.workspace_id==ctl.workspace_id and not competing and not changed:
                        existing=db.scalar(select(Audit.id).where(Audit.workspace_id==ctl.workspace_id,
                            Audit.action==FAILURE,Audit.object_id==control_id,
                            Audit.data['grant_id'].as_string()==grant_id).limit(1))
                        if existing is None:
                            db.add(Audit(id=uid(),workspace_id=ctl.workspace_id,actor_id=grant.actor_id,
                                action=FAILURE,object_id=control_id,data={'schema':FAILURE,'grant_id':grant_id,'kind':kind}))
                    db.commit()
            except Exception:
                pass
            # A failed/unknown lease is left for fenced recovery.
            continue
        finally:
            heartbeat.__exit__(None,None,None)
            try:
                with Session(engine()) as db:_heartbeat(db,workspace);db.commit()
            except Exception:pass

    return {'candidates':len(work),'completed':completed}


def main():
    with Session(engine()) as db:_check(db)
    def terminate(_signum,_frame):raise SystemExit(0)
    previous=signal.signal(signal.SIGTERM,terminate)
    try:
        while True:
            try:tick()
            except Exception:pass  # No source text or credentials in process logs.
            time.sleep(5)
    finally:
        try:stop_heartbeat()
        except Exception:pass
        signal.signal(signal.SIGTERM,previous)


if __name__=='__main__':main()
