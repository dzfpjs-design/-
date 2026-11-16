"""Candidate stop endpoint; DO NOT register until all publication gates pass.

It only records the local execution fence. Any external call already past its
dispatch boundary remains unknown; it never sends or retries an invocation.
"""
from uuid import UUID
from functools import wraps
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, IntegrityError
from .setup_api import P
from .db import engine
from .models import Task, User, Membership, Audit, AuthSession
from .contracts import digest
from .native_goals import manage
from .goal_evaluation_append_store_v297 import _reader
from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356 as Witness
from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356 as Trigger
from .auto_evaluation_history_reader_v361 import checked_history
from .auto_evaluation_recovery_v360 import Control, Refused, _hash, create_fence, locked_producer, rollout_enabled

router = APIRouter()
ACTION='goal_evaluation.recovery_fence_recorded_v360'

def _bounded_conflict(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try: return fn(*args,**kwargs)
        except OperationalError as exc:
            if getattr(exc.orig,'sqlstate',None) not in {'55P03','40P01'}: raise
            raise HTTPException(409,'原任务正在处理，请稍后重新核对') from None
        except IntegrityError as exc:
            if getattr(exc.orig,'sqlstate',None) != '23505': raise
            raise HTTPException(409,'请求或原任务已有控制记录，请查询原请求') from None
    return wrapped

def _audit_payload(row):
    return {'schema':'auto-evaluation-stop-audit-v360','control_id':row.id,
            'task_id':row.task_id,'producer_job_id':row.producer_job_id,
            'witness_id':row.witness_id,'trigger_id':row.trigger_id,
            'request_hash':row.request_hash,'outcome':row.outcome}

def _verify_receipt(db,p,row,task,witness,origin,job):
    if (row.workspace_id!=p.workspace_id or row.task_id!=task.id or row.producer_job_id!=job.id
            or row.witness_id!=witness.id or row.trigger_id!=origin.id):
        raise HTTPException(409,'原控制记录无法核对')
    body={'workspace_id':row.workspace_id,'task_id':row.task_id,'job_id':row.producer_job_id,
          'trigger_id':row.trigger_id,'witness_id':row.witness_id,'actor_id':row.actor_id,
          'request_id':row.request_id,'reason':row.reason,'lease_hash':row.lease_hash,
          'attempt':row.attempt,'expected_view_hash':row.expected_view_hash,'outcome':row.outcome}
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==ACTION,Audit.object_id==row.id).limit(2)))
    if (row.request_hash!=_hash(body) or len(events)!=1 or events[0].actor_id!=row.actor_id
            or digest(events[0].data)!=digest(_audit_payload(row))):
        raise HTTPException(409,'原控制记录无法核对')

class Stop(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id: StrictStr
    expected_version: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    reason: StrictStr = Field(min_length=1,max_length=500)

def _id(value):
    try:
        if type(value) is not str or str(UUID(value)) != value: raise ValueError()
        return value
    except (TypeError,ValueError,AttributeError):
        raise HTTPException(422,'请求编号无效') from None

def _current(db,p,task_id,witness_id):
    if not rollout_enabled(): raise HTTPException(503,'任务停止功能尚未启用，请继续核对原记录')
    _reader(db,p,write=True)
    session=db.scalar(select(AuthSession).where(AuthSession.id==_id(p.session_id),
        AuthSession.user_id==p.user_id,AuthSession.revoked.is_(False),
        AuthSession.expires_at>func.clock_timestamp()).with_for_update(nowait=True))
    if session is None: raise HTTPException(401,'登录已过期或撤销')
    # Fresh membership is protected in the stop transaction, not just the cookie.
    membership=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(
        Membership.user_id==p.user_id,Membership.workspace_id==p.workspace_id,
        Membership.role==p.role,User.active.is_(True)).with_for_update(nowait=True))
    if membership is None: raise HTTPException(403,'当前账户没有任务管理权限')
    task=db.scalar(select(Task).where(Task.id==_id(task_id),Task.workspace_id==p.workspace_id)
                   .with_for_update(nowait=True))
    if task is None: raise HTTPException(404,'业务任务不存在')
    manage(p,task)
    witness=db.scalar(select(Witness).where(Witness.id==_id(witness_id),
        Witness.workspace_id==p.workspace_id,Witness.task_id==task.id))
    if witness is None: raise HTTPException(404,'原接收记录不存在')
    origins=list(db.scalars(select(Trigger).where(Trigger.workspace_id==p.workspace_id,
        Trigger.task_id==task.id,Trigger.event_id==witness.event_id,Trigger.policy_id==witness.policy_id,
        Trigger.policy_version==witness.policy_version).limit(2)))
    if len(origins)!=1: raise HTTPException(409,'原请求无法核对')
    origin=origins[0]
    if type(origin.data) is not dict or not origin.data.get('actor_id'):
        raise HTTPException(409,'原请求无法核对')
    _id(origin.data['actor_id'])
    command,record,job=checked_history(db,p,task,origin,witness)
    if job.id!=origin.job_id: raise HTTPException(409,'原请求无法核对')
    job=locked_producer(db,p.workspace_id,task.id,job.id)
    return task,witness,origin,job,record

def _version(db,p,task,job,record):
    from .auto_evaluation_stop_state_v361 import classify
    from .goal_evaluation_provider_v288 import KEY as HEAD_KEY
    outcome=classify(db,p,task,job)
    view=digest({'task_id':task.id,'job_id':job.id,'status':job.status,
                 'attempts':job.attempts,'lease_hash':_hash(job.lease_token),
                 'request_actor':p.user_id,'record_hash':record['record_hash'],
                 'verified_outcome':outcome,'current_head':task.data.get(HEAD_KEY)})
    return view,outcome

def _result(row):
    if row.outcome=='terminal_record_only':
        return {'id':row.id,'status':'terminal_record_only',
                'message':'该评价已结案，仅记录核查请求；已有结果没有撤销。'}
    return {'id':row.id,'status':'fenced_effect_unknown',
            'message':'平台后续处理受限；外部效果需核对。'}

def _require_session_live(db,p):
    # The original session and membership locks from _current are still held.
    active=db.scalar(select(AuthSession.id).where(AuthSession.id==p.session_id,
        AuthSession.user_id==p.user_id,AuthSession.revoked.is_(False),
        AuthSession.expires_at>func.clock_timestamp()))
    if active!=p.session_id: raise HTTPException(401,'登录已过期或撤销')

def _session_commit_guard(db,p):
    # Lock serializes explicit revocation; clock must be rechecked immediately
    # before commit because expiration can occur while proof is being checked.
    def verify(session):
        with session.no_autoflush:
            found=session.scalar(select(AuthSession.id).where(AuthSession.id==p.session_id,
                AuthSession.user_id==p.user_id,AuthSession.revoked.is_(False),
                AuthSession.expires_at>func.clock_timestamp()))
        if found!=p.session_id: raise HTTPException(401,'登录已过期或撤销')
    event.listen(db,'before_commit',verify,once=True)

@router.get('/tasks/{task_id}/auto-evaluation-policy/investigations/{witness_id}/stop-preview')
@_bounded_conflict
def preview(task_id:str,witness_id:str,p:P):
    with Session(engine()) as db:
        task,witness,origin,job,record=_current(db,p,task_id,witness_id)
        view,outcome=_version(db,p,task,job,record)
        return {'version':view,'job_state':job.status,'proposed_outcome':outcome,
                'message':'仅预览；已发送的外部请求可能仍会生效。'}

@router.post('/tasks/{task_id}/auto-evaluation-policy/investigations/{witness_id}/stop')
@_bounded_conflict
def stop(task_id:str,witness_id:str,body:Stop,p:P):
    _id(body.request_id)
    if not body.reason.strip(): raise HTTPException(422,'请填写原因')
    with Session(engine()) as db:
        task,witness,origin,job,record=_current(db,p,task_id,witness_id)
        existing=db.scalar(select(Control).where(Control.workspace_id==p.workspace_id,
                                                  Control.request_id==body.request_id))
        if existing is not None:
            if (existing.actor_id!=p.user_id or existing.task_id!=task.id or
                existing.producer_job_id!=job.id or existing.trigger_id!=origin.id or
                existing.witness_id!=witness.id or existing.reason!=body.reason or
                existing.expected_view_hash!=body.expected_version):
                raise HTTPException(409,'请求编号与原记录不一致')
            _verify_receipt(db,p,existing,task,witness,origin,job)
            _require_session_live(db,p)
            return _result(existing)
        view,outcome=_version(db,p,task,job,record)
        if view!=body.expected_version: raise HTTPException(409,'原任务已变化，请重新核对')
        try:
            row=create_fence(db,workspace_id=p.workspace_id,task_id=task.id,job_id=job.id,
                trigger_id=origin.id,witness_id=witness.id,actor_id=p.user_id,request_id=body.request_id,
                reason=body.reason,expected_attempt=job.attempts,expected_lease_hash=_hash(job.lease_token),
                expected_view_hash=body.expected_version,outcome=outcome)
            db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,
                         object_id=row.id,data=_audit_payload(row)))
            _session_commit_guard(db,p)
            db.commit()
        except Refused:
            db.rollback();raise HTTPException(409,'原任务不能停止，请查看原记录') from None
        return _result(row)

@router.get('/tasks/{task_id}/auto-evaluation-policy/investigations/{witness_id}/controls/{request_id}')
@_bounded_conflict
def read_control(task_id:str,witness_id:str,request_id:str,p:P):
    with Session(engine()) as db:
        task,witness,origin,job,_record=_current(db,p,task_id,witness_id)
        row=db.scalar(select(Control).where(Control.workspace_id==p.workspace_id,
                                             Control.request_id==_id(request_id)))
        if row is None: raise HTTPException(404,'没有找到该请求的记录')
        _verify_receipt(db,p,row,task,witness,origin,job)
        _require_session_live(db,p)
        return _result(row)
