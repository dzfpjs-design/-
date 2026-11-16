"""Operator evidence, recovery and cancellation for persisted data needs."""
from .data_gaps import locked_gap
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from .db import session
from .security import authenticated,require
from .models import DataGap, GapCheck, now
from .services import scoped,as_dict,check_version
from .contracts import VersionAction
from .data_gaps import queue,record,TERMINAL

router=APIRouter(prefix='/api/data/gaps')
P=Annotated[object,Depends(authenticated)]
DB=Annotated[object,Depends(session)]


@router.get('')
def list_gaps(p:P,db:DB,query_id:str|None=None):
    q=select(DataGap).where(DataGap.workspace_id==p.workspace_id)
    if query_id:q=q.where(DataGap.query_id==query_id)
    values=db.scalars(q.order_by(DataGap.created_at.desc(),DataGap.id).limit(101)).all()
    return {'items':[as_dict(g) for g in values[:100]],'truncated':len(values)>100}


@router.get('/{identifier}')
def get_gap(identifier:str,p:P,db:DB):return as_dict(scoped(db,DataGap,identifier,p))


@router.get('/{identifier}/history')
def history(identifier:str,p:P,db:DB):
    scoped(db,DataGap,identifier,p)
    values=db.scalars(select(GapCheck).where(GapCheck.workspace_id==p.workspace_id,GapCheck.gap_id==identifier)
        .order_by(GapCheck.created_at.desc(),GapCheck.id).limit(101)).all()
    return {'items':[as_dict(g) for g in values[:100]],'truncated':len(values)>100}


def editable(db,p,identifier,expected):
    require(p,'admin','operator');gap=locked_gap(db,p,identifier);check_version(gap,expected)
    if gap.status in TERMINAL:raise HTTPException(409,'此缺口已结束，原问题与历史保留')
    return gap


def request_work(db,p,gap,body,kind):
    if kind=='recheck_data_gap' and gap.status!='verifying':
        gap.data={**gap.data,'recheck_resume_status':gap.status}
    gap.status='verifying' if kind=='recheck_data_gap' else 'received';gap.version+=1
    gap.data={**gap.data,'last_request_reason':body.reason,'requested_at':now().isoformat()}
    queue(db,p.workspace_id,kind,'gap-manual:'+gap.id+':'+str(gap.version),
        {'gap_id':gap.id,'actor_id':p.user_id,'trigger':'operator_recheck'})
    record(db,p,gap,'requested',reason=body.reason,kind=kind)
    db.commit();return as_dict(gap)


@router.post('/{identifier}/recheck')
def recheck(identifier:str,body:VersionAction,p:P,db:DB):
    return request_work(db,p,editable(db,p,identifier,body.expected_version),body,'recheck_data_gap')


@router.post('/{identifier}/retry')
def retry(identifier:str,body:VersionAction,p:P,db:DB):
    return request_work(db,p,editable(db,p,identifier,body.expected_version),body,'route_data_gap')


@router.post('/{identifier}/cancel')
def cancel(identifier:str,body:VersionAction,p:P,db:DB):
    gap=editable(db,p,identifier,body.expected_version);gap.status='cancelled';gap.version+=1
    gap.data={**gap.data,'cancel_reason':body.reason,'cancelled_at':now().isoformat()}
    record(db,p,gap,'cancelled',reason=body.reason,
        boundary='停止此证据请求；已发送或其他请求共用的设备动作仍需核对，未取消实际设备')
    db.commit();return as_dict(gap)
