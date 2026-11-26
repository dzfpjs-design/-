"""Uninstalled, disposable-PG-only user confirmation and status candidate.

The endpoint creates a waiting control, not a claim that the model ran. No
endpoint accepts a worker lease, provider address, credential, or raw result.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from .config import settings
from .models import Audit, AuthSession, Job
from .setup_api import DB, P
from . import same_goal_advice_dispatch_control_candidate_v2 as control
from . import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_user_result_candidate_v2 as results

router=APIRouter()
BASE='/tasks/{task_id}/business-advice-v2/preparations/{plan_id}/dispatch'


class Cancel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID


def _enabled(db):
    if not (settings().v2_advice_setup_test_enabled and settings().v2_user_send_test_enabled):
        raise HTTPException(404,'分析服务暂不可用')
    guard.require_disposable_database(db)


@router.post(BASE+'/start',status_code=201)
def begin(task_id: str,plan_id: str,body: delegation.Body,p: P,db: DB):
    _enabled(db)
    session=db.get(AuthSession,p.session_id)
    if session is None or session.kind!='cookie':
        raise HTTPException(403,'请登录后确认')
    issued=delegation.begin_with_delegation(db,p,task_id,plan_id,body.model_dump(mode='json'))
    if issued['state']=='authorized':
        queued=control.enqueue(db,p,task_id,plan_id,issued['delegation_id'])
        state=queued['state'] if queued['state']=='waiting' else 'needs_attention'
    elif issued['state']=='dispatch_started_non_retractable':state='needs_attention'
    else:state='needs_attention'
    db.commit()
    return {'state':state}


@router.post(BASE+'/cancel')
def cancel(task_id: str,plan_id: str,body: Cancel,p: P,db: DB):
    _enabled(db);task,plan=results._auth(db,p,task_id,plan_id)
    events=delegation._events(db,p,delegation.AUTHORIZED,plan.id)
    if len(events)!=1:raise HTTPException(409,'申请暂时无法核对')
    outcome=delegation.revoke(db,p,task.id,plan.id,events[0].id,str(body.request_id));db.commit()
    return {'state':'cancelled' if outcome['state']=='revoked' else 'cannot_cancel'}


@router.get(BASE)
def status(task_id: str,plan_id: str,p: P,db: DB):
    _enabled(db);task,plan=results._auth(db,p,task_id,plan_id)
    checked=results.read(task_id,plan_id,p,db)
    if checked['state']=='ready':return checked
    events=delegation._events(db,p,delegation.AUTHORIZED,plan.id)
    if len(events)!=1:return {'state':'needs_attention'}
    try:
        _,value=delegation._value(db,p.workspace_id,task,plan,events[0].id)
        if delegation._revocation(db,p.workspace_id,plan,value):return {'state':'cancelled'}
        if delegation._committed_permit(db,p,plan,value):return {'state':'needs_attention'}
        rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.kind==control.KIND,Job.idempotency_key=='advice-v2-dispatch-control:'+events[0].id)))
        if len(rows)!=1:return {'state':'needs_attention'}
        row,_=control._control(db,rows[0].id,lock=True)
        if row.status=='queued' and row.attempts==0:return {'state':'waiting'}
        if row.status=='running' and row.lease_until is not None and row.lease_until>db.scalar(select(func.clock_timestamp())):
            return {'state':'processing'}
    except (HTTPException,ValueError,TypeError,KeyError,AttributeError):
        pass
    return {'state':'needs_attention'}
