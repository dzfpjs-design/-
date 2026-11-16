"""Explicit-workspace rule tick and proved ordinary Task family consumer.

Selection closes its Session before the exact-ID ordinary worker acquires its
own real lease. Neither the grant scan nor heartbeat can authorize device IO,
cloud calls, human approval, unrelated Jobs or a wider workspace.
"""
import signal
import threading
from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased
from . import models as m, business_automation_service as service
from .business_automation_models import BusinessAutomationSubscription as Subscription
from .business_automation_contracts import JOB_KIND, TASK, need, uuid
from .security import Principal
from .services import scoped

GRAPH_KINDS = ('execute_full_semantic_graph_node', 'advance_full_semantic_graph')


def _principal(db, workspace_id, actor_id):
    membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == workspace_id,
        m.Membership.user_id == actor_id))
    return Principal(actor_id, workspace_id, membership.role if membership else 'unavailable', 'automation-selection')


def tick(workspace_id, *, after=None, limit=20, subscription_id=None, diagnostic=None):
    uuid(workspace_id)
    if subscription_id is not None: uuid(subscription_id)
    need(type(limit) is int and 1 <= limit <= 50, '规则扫描上限无效', 422)
    from .db import engine
    with Session(engine()) as db:
        base = select(Subscription.id).where(Subscription.workspace_id == workspace_id,
            Subscription.status == 'active')
        if subscription_id: base = base.where(Subscription.id == subscription_id)
        query = base.where(Subscription.id > after) if after else base
        ids = list(db.scalars(query.order_by(Subscription.id).limit(limit)))
        if not ids and after: ids = list(db.scalars(base.order_by(Subscription.id).limit(limit)))
    for identity in ids:
        try:
            with Session(engine()) as db:
                row = db.get(Subscription, identity)
                need(row is not None and row.workspace_id == workspace_id)
                p = _principal(db, workspace_id, row.actor_id)
                service.poll_subscription(db,p,identity,limit=limit); db.commit()
        except Exception:
            if diagnostic: diagnostic('规则本轮未派发，原授权及发生记录保留',identity)
    return ids[-1] if ids else None


def _page(db, workspace_id, after, limit, subscription_id):
    occurrence = and_(m.Job.kind == JOB_KIND, exists(select(Subscription.id).where(
        Subscription.workspace_id == workspace_id, Subscription.id == m.Job.data['subscription_id'].as_string())))
    task = and_(m.Job.kind.in_(GRAPH_KINDS), exists(select(m.Audit.id).where(
        m.Audit.workspace_id == workspace_id, m.Audit.action == TASK,
        m.Audit.object_id == m.Job.data['task_id'].as_string())))
    if subscription_id:
        occurrence = and_(occurrence, m.Job.data['subscription_id'].as_string() == subscription_id)
        original = exists(select(m.Audit.id).where(m.Audit.workspace_id == workspace_id,
            m.Audit.action == TASK, m.Audit.object_id == m.Job.data['task_id'].as_string(),
            m.Audit.data['subscription_id'].as_string() == subscription_id))
        linked = aliased(m.Job)
        associated = exists(select(linked.id).where(linked.workspace_id == workspace_id,
            linked.kind == JOB_KIND, linked.data['subscription_id'].as_string() == subscription_id,
            linked.data['state']['task_id'].as_string() == m.Job.data['task_id'].as_string(),
            linked.data['state']['status'].as_string() == 'linked_existing_task'))
        task = and_(task, or_(original, associated))
    base = select(m.Job.id,m.Job.kind,m.Job.available_at).where(m.Job.workspace_id == workspace_id,
        m.Job.available_at <= func.clock_timestamp(), or_(occurrence,task),
        or_(m.Job.status == 'queued',and_(m.Job.status == 'running',
            or_(m.Job.lease_until.is_(None),m.Job.lease_until < func.clock_timestamp()))))
    query = base
    if after is not None:
        query = query.where(or_(m.Job.available_at > after[0],and_(m.Job.available_at == after[0],m.Job.id > after[1])))
    rows = list(db.execute(query.order_by(m.Job.available_at,m.Job.id).limit(limit)))
    if not rows and after is not None: rows = list(db.execute(base.order_by(m.Job.available_at,m.Job.id).limit(limit)))
    return rows


def _task_scope(db, p, task, witness, subscription_id):
    if subscription_id is None or witness['subscription'].id == subscription_id: return
    links = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,m.Job.kind == JOB_KIND,
        m.Job.data['subscription_id'].as_string() == subscription_id,
        m.Job.data['state']['task_id'].as_string() == task.id,
        m.Job.data['state']['status'].as_string() == 'linked_existing_task').limit(2)))
    need(len(links) == 1)
    row,origin,state = service.checked_occurrence(db,p,links[0])
    need(row.id == subscription_id and state['task_id'] == task.id
        and state['status'] == 'linked_existing_task' and state['source_unit'] == witness['unit']
        and state['task_body'] == witness['body'] and state['task_body_hash'] == witness['body_hash']
        and service.merge_key(row,origin,state['source_unit']) == task.data['business_automation_v1']['merge_key'])


def _proved(db, workspace_id, identity, kind, subscription_id=None):
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == workspace_id,m.Job.id == identity,m.Job.kind == kind))
    need(job is not None)
    p = _principal(db,workspace_id,job.data['actor_id'])
    if kind == JOB_KIND:
        row, _, state = service.checked_occurrence(db,p,job)
        if subscription_id is not None: need(row.id == subscription_id)
        # Paused originals still acquire their own exact lease once, so the
        # ordinary handler can persist a blocked/cancelled receipt. No Task.
        return True  # Includes a committed Task awaiting its Job finalization.
    task = scoped(db,m.Task,job.data['task_id'],p)
    from .business_automation_admission import verify_task
    need(verify_task(db,p,task) is not None)
    witness = service.checked_task_witness(db,p,task)
    _task_scope(db,p,task,witness,subscription_id)
    from . import workflow_graph as graph
    if kind == 'execute_full_semantic_graph_node':
        transport = task.data.get(graph.TRANSPORT,{}).get(job.data['activation_id'])
        need(type(transport) is dict and transport['job_id'] == job.id
            and transport['invocation_key'] == job.idempotency_key == job.data['invocation_key']
            and transport['input_hash'] == job.data['input_hash'] == service.digest(job.data['inputs']))
    else:
        need(job.id in task.data.get('graph_timers',{}).values())
    # Ordinary worker performs fresh actor/graph/node/approval/lease checks.
    return True


def step(workspace_id, *, after=None, tick_after=None, stop_event=None, limit=20,
         subscription_id=None, diagnostic=None):
    uuid(workspace_id)
    if subscription_id is not None: uuid(subscription_id)
    need(type(limit) is int and 1 <= limit <= 50, '处理扫描上限无效', 422)
    if stop_event is not None and stop_event.is_set():
        return {'processed':False,'after':after,'tick_after':tick_after}
    tick_after = tick(workspace_id,after=tick_after,limit=limit,subscription_id=subscription_id,diagnostic=diagnostic)
    from .db import engine
    with Session(engine()) as db: rows = _page(db,workspace_id,after,limit,subscription_id)
    cursor = after
    for identity,kind,available_at in rows:
        if stop_event is not None and stop_event.is_set(): break
        cursor = (available_at,identity)
        try:
            with Session(engine()) as db: allowed = _proved(db,workspace_id,identity,kind,subscription_id)
        except Exception:
            if diagnostic: diagnostic('原发生或任务家族证明无法核对，已拒绝领取',identity)
            continue
        if not allowed: continue
        if stop_event is not None and stop_event.is_set(): break
        from .worker import process_once
        if process_once(job_id=identity,kinds=[kind],deliver=False,workspace_id=workspace_id,_stop_event=stop_event):
            return {'processed':True,'after':cursor,'tick_after':tick_after}
    return {'processed':False,'after':cursor if rows else None,'tick_after':tick_after}


def run(workspace_id, *, subscription_id=None):
    uuid(workspace_id)
    if subscription_id is not None: uuid(subscription_id)
    stop = threading.Event(); previous = {}; after = tick_after = None
    def request_stop(signum,frame): stop.set()
    for signum in (signal.SIGINT,signal.SIGTERM): previous[signum] = signal.signal(signum,request_stop)
    def diagnostic(message,identity): print(message+'；原规则/任务 '+identity,flush=True)
    print('业务自动启动处理器已启动，仅处理明确工作区及授权范围的新发生和已证明任务；人工审批保留。',flush=True)
    try:
        while not stop.is_set():
            try:
                result = step(workspace_id,after=after,tick_after=tick_after,stop_event=stop,
                    subscription_id=subscription_id,diagnostic=diagnostic)
                after,tick_after = result['after'],result['tick_after']
                if not result['processed']: stop.wait(1)
            except Exception:
                print('业务自动处理暂时停止推进；原授权与任务保留，未改用全局处理。',flush=True); stop.wait(2)
    finally:
        for signum,handler in previous.items(): signal.signal(signum,handler)
        print('业务自动启动处理器已停止；已保存授权、发生、人工审批和任务结果保留。',flush=True)
    return 0
