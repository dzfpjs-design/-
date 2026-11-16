"""Explicit-workspace consumer for proved local source queries only.

Selection and typed wakeup end their Sessions before the ordinary exact-ID
worker is invoked. This is neither the global worker nor a substitute lease,
actor, source or cloud authorization. A fair scan cursor is operational state,
never authority; every candidate's independent original proof is reread.
"""
import signal
import threading
from sqlalchemy import and_,exists,func,or_,select
from sqlalchemy.orm import Session
from . import models as m,source_query_service as source,document_question_service as documents
from .source_query_contracts import CREATE_AUDIT,uuid,need
from .security import Principal
from .services import scoped


def _ready():
    return or_(m.Job.status=='queued',and_(m.Job.status=='running',
        or_(m.Job.lease_until.is_(None),m.Job.lease_until<func.clock_timestamp())))


def _page(db,workspace_id,after,limit):
    local=and_(m.Job.kind=='data_query',exists(select(m.Audit.id).where(
        m.Audit.workspace_id==workspace_id,m.Audit.action==CREATE_AUDIT,
        m.Audit.object_id==m.Job.data['query_id'].as_string())))
    child=and_(m.Job.kind=='search_runtime_documents',exists(select(m.Audit.id).where(
        m.Audit.workspace_id==workspace_id,m.Audit.action==documents.LINK,
        m.Audit.data['child_job_id'].as_string()==m.Job.id)))
    base=select(m.Job.id,m.Job.kind,m.Job.available_at).where(m.Job.workspace_id==workspace_id,
        m.Job.available_at<=func.clock_timestamp(),or_(local,child),
        or_(_ready(),and_(local,m.Job.status==documents.PHASE)))
    query=base
    if after is not None:
        query=query.where(or_(m.Job.available_at>after[0],
            and_(m.Job.available_at==after[0],m.Job.id>after[1])))
    rows=list(db.execute(query.order_by(m.Job.available_at,m.Job.id).limit(limit)))
    if not rows and after is not None:
        rows=list(db.execute(base.order_by(m.Job.available_at,m.Job.id).limit(limit)))
    return rows


def _proved(db,workspace_id,job_id,kind):
    job=db.scalar(select(m.Job).where(m.Job.workspace_id==workspace_id,m.Job.id==job_id,m.Job.kind==kind))
    need(job is not None,'原资料任务已变化，未领取',409)
    member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==workspace_id,
        m.Membership.user_id==job.data['actor_id']))
    # Actual membership is attribution, not a dispatch grant. The ordinary
    # worker still rejects inactive/revoked actors before it executes work.
    p=Principal(job.data['actor_id'],workspace_id,member.role if member else 'unavailable','source-query-selection')
    if kind=='data_query':
        row=scoped(db,m.DataRun,job.data['query_id'],p)
        need(source.is_v2(db,row,job=job),'此任务不是有独立原申请的本地资料查询',409)
        source.checked(db,p,row)
        if job.status==documents.PHASE:
            need(documents.is_record(db,row,job=job),documents.ERROR)
            documents.waiting_cancel_allowed(db,p,row,job)
            return 'wait'
        return 'run'
    need(kind=='search_runtime_documents',documents.ERROR)
    links=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==workspace_id,
        m.Audit.action==documents.LINK,m.Audit.data['child_job_id'].as_string()==job_id).limit(2)))
    need(len(links)==1,documents.ERROR)
    row=scoped(db,m.DataRun,links[0].object_id,p);source.checked(db,p,row)
    linked=documents._link(db,p,row)
    need(linked[2].id==job_id and row.owner_id==job.data['actor_id'],documents.ERROR)
    return 'skip' if row.status in {'cancelled','failed'} else 'run'


def step(workspace_id,*,after=None,stop_event=None,limit=20,diagnostic=None):
    """One bounded fair scan; no DB connection remains around process_once."""
    uuid(workspace_id)
    need(type(limit) is int and 1<=limit<=50,'资料处理扫描上限无效',422)
    if stop_event is not None and stop_event.is_set():return {'processed':False,'after':after}
    from .db import engine
    with Session(engine()) as db:rows=_page(db,workspace_id,after,limit)
    cursor=after
    for job_id,kind,available_at in rows:
        if stop_event is not None and stop_event.is_set():break
        cursor=(available_at,job_id)
        try:
            with Session(engine()) as db:action=_proved(db,workspace_id,job_id,kind)
        except Exception as error:
            from .leases import LeaseLost
            if isinstance(error,LeaseLost):raise
            if diagnostic:diagnostic('原资料任务的独立证明无法核对，已拒绝派发',job_id)
            continue
        if stop_event is not None and stop_event.is_set():break
        if action=='wait':
            # A corrupt/slow old wait cannot occupy the first page forever:
            # this exact-ID poll participates in the same fair scan cursor.
            try:documents.poll_once(workspace_id,job_ids=[job_id],limit=1)
            except Exception:
                if diagnostic:diagnostic('原报告等待暂时无法核对，已保留原任务',job_id)
            continue
        if action=='skip':continue
        if stop_event is not None and stop_event.is_set():break
        from .worker import process_once
        processed=process_once(job_id=job_id,kinds=[kind],deliver=False,workspace_id=workspace_id,
            _stop_event=stop_event)
        if processed:return {'processed':True,'after':cursor}
    return {'processed':False,'after':cursor if rows else None}


def run(workspace_id):
    uuid(workspace_id)  # scope is mandatory and checked before engine/DB
    stop=threading.Event();previous={};cursor=None
    def request_stop(signum,frame):stop.set()
    for signum in (signal.SIGINT,signal.SIGTERM):previous[signum]=signal.signal(signum,request_stop)
    def diagnostic(message,identity):print(message+'；原任务 '+identity,flush=True)
    print('本地资料查询处理器已启动，仅处理明确工作区内有独立申请的资料查询及其正式正文子任务。',flush=True)
    try:
        while not stop.is_set():
            try:
                result=step(workspace_id,after=cursor,stop_event=stop,diagnostic=diagnostic)
                cursor=result['after']
                if not result['processed']:stop.wait(.5)
            except Exception:
                print('资料处理暂时无法继续；原任务保留，未扩大工作区或改用其他处理器。',flush=True)
                stop.wait(2)
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)
        print('本地资料查询处理器已停止；原申请、已保存答案和任务进度保留。',flush=True)
    return 0
