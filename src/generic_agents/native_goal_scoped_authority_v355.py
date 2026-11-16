"""Ephemeral selection fence checked after the original Task row is locked.

No caller-supplied payload can install this witness through an API. Ordinary
workers without this context retain their existing behavior and authority.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fastapi import HTTPException
from .contracts import digest

_WITNESS=object()
_ACTIVE=ContextVar('native_goal_scoped_authority_v355',default=None)

@dataclass(frozen=True,slots=True)
class Selection:
    witness:object
    workspace_id:str
    task_id:str
    job_id:str
    family:str
    proof_hash:str

@contextmanager
def selected(workspace_id,task_id,job_id,family,proof_hash):
    if family not in {'ordinary','timer','candidate','superseded_recovery'} or not isinstance(proof_hash,str):
        raise ValueError('原目标后台范围不完整')
    token=_ACTIVE.set(Selection(_WITNESS,workspace_id,task_id,job_id,family,proof_hash))
    try:yield
    finally:_ACTIVE.reset(token)


def before_locked_task(db,p,task,job):
    chosen=_ACTIVE.get()
    if chosen is None:return
    def need(ok):
        if not ok:raise HTTPException(409,'原目标检查在领取时已被替代或授权已变化；原请求保留')
    need(isinstance(chosen,Selection) and chosen.witness is _WITNESS
        and chosen.workspace_id==p.workspace_id==task.workspace_id==job.workspace_id
        and chosen.task_id==task.id==job.data.get('task_id') and chosen.job_id==job.id
        and job.kind=='check_native_business_goal')
    if chosen.family=='superseded_recovery':
        raise HTTPException(409,'原检查在中断后已被新请求替代；仅保留并结束原恢复，未继续执行')
    if chosen.family=='ordinary':
        pin=task.data.get('native_goal_check_job_pin')
        need(type(pin) is dict and digest(pin)==task.data.get('native_goal_check_job_pin_hash')==chosen.proof_hash
            and pin.get('job_id')==job.id and pin.get('idempotency_key')==job.idempotency_key
            and pin.get('actor_id')==job.data.get('actor_id') and pin.get('context_hash')==job.data.get('context_hash'))
    elif chosen.family=='timer':
        from .native_goal_time import timer,admitted
        value=timer(task);need(type(value) is dict and digest(value)==chosen.proof_hash
            and value.get('job_id')==job.id and admitted(task,job))
    else:
        from .goal_evaluation_maturity_v299 import candidate_scope
        value=candidate_scope(db,p,task,job)
        need(type(value) is dict and digest(value)==chosen.proof_hash)
