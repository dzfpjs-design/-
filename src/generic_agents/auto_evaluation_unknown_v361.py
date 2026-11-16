"""Candidate preservation of an interrupted original invocation.

Only runs after a verified producer fence blocks a gateway success commit.
No dispatch, no new idempotency key, no alteration of a terminal outcome.
This module is staged and unregistered; it needs real PG restart/race tests.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import engine
from .models import Invocation, Job, Audit
from .contracts import digest
from .auto_evaluation_recovery_v360 import Control, rollout_enabled, _hash

ACTION='goal_evaluation.fenced_invocation_unknown_v361'

class Unverifiable(ValueError): pass

def decision(call, *, workspace_id, job_id, invocation_id, capability_id, key, inputs_hash, approval_hash):
    """Pure classification; does not grant permission or produce a receipt."""
    data=call.data
    if (call.id!=invocation_id or call.workspace_id!=workspace_id or call.capability_id!=capability_id
        or call.idempotency_key!=key or type(data) is not dict or data.get('job_id')!=job_id
        or data.get('role')!='data' or data.get('side_effect') is not True
        or data.get('input_hash')!=inputs_hash or digest(data.get('inputs'))!=inputs_hash
        or digest(data.get('approval'))!=approval_hash):
        raise Unverifiable('original invocation mismatch')
    if call.status=='running': return 'record_unknown'
    if call.status=='unknown': return 'already_unknown'
    # Never downgrade succeeded, failed, blocked or an unexpected status.
    return 'terminal_preserved'

def mark_unknown(*, workspace_id, task_id, job_id, invocation_id, capability_id,
                 key, inputs_hash, approval_hash):
    """Persist unknown in an independent transaction after caller rollback.

    The caller must pass values frozen from the original gateway path. All
    identifiers and the immutable audit chain are checked again while holding
    the producer and invocation locks. No blind retry occurs on any failure.
    """
    if not rollout_enabled(): raise Unverifiable('rollout disabled')
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.id==job_id,Job.workspace_id==workspace_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True))
        if (job is None or job.kind!='append_goal_evaluation_v297' or type(job.data) is not dict
            or job.data.get('task_id')!=task_id): raise Unverifiable('producer mismatch')
        fence=db.scalar(select(Control).where(Control.workspace_id==workspace_id,
            Control.producer_job_id==job.id,Control.task_id==task_id))
        if fence is None: raise Unverifiable('fence unavailable')
        body={'workspace_id':fence.workspace_id,'task_id':fence.task_id,'job_id':fence.producer_job_id,
              'trigger_id':fence.trigger_id,'witness_id':fence.witness_id,'actor_id':fence.actor_id,
              'request_id':fence.request_id,'reason':fence.reason,'lease_hash':fence.lease_hash,
              'attempt':fence.attempt,'expected_view_hash':fence.expected_view_hash,'outcome':fence.outcome}
        if fence.outcome!='fenced_effect_unknown' or fence.request_hash!=_hash(body):
            raise Unverifiable('fence binding invalid')
        # Stop audit is a separate persisted record, not a caller assertion.
        from .auto_evaluation_stop_api_v360 import ACTION as STOP_ACTION, _audit_payload
        events=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,
            Audit.action==STOP_ACTION,Audit.object_id==fence.id).limit(2)))
        if len(events)!=1 or events[0].actor_id!=fence.actor_id or digest(events[0].data)!=digest(_audit_payload(fence)):
            raise Unverifiable('fence audit unavailable')
        call=db.scalar(select(Invocation).where(Invocation.id==invocation_id,
            Invocation.workspace_id==workspace_id).with_for_update(nowait=True).execution_options(populate_existing=True))
        if call is None: raise Unverifiable('original invocation absent')
        result=decision(call,workspace_id=workspace_id,job_id=job_id,invocation_id=invocation_id,
            capability_id=capability_id,key=key,inputs_hash=inputs_hash,approval_hash=approval_hash)
        if result!='record_unknown': return result
        prior=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,
            Audit.action==ACTION,Audit.object_id==call.id).limit(2)))
        if prior: raise Unverifiable('conflicting unknown audit')
        # Existing payload, original key and any original evidence are kept.
        call.status='unknown'
        call.data={**call.data,'recovery_unknown_v361':{'fence_id':fence.id,
            'reason':'fence_blocked_success_commit','effect':'unknown'}}
        db.add(Audit(workspace_id=workspace_id,actor_id=fence.actor_id,action=ACTION,
            object_id=call.id,data={'schema':'fenced-invocation-unknown-v361',
                'fence_id':fence.id,'job_id':job.id,'invocation_id':call.id,'effect':'unknown'}))
        db.commit();return 'recorded_unknown'
