"""Stage the original promoter's success for its publication transaction.

This is an internal worker tail, not publication authority. The fixed worker
branch must first finish process_promotion and all its source/CAS reads, call
this once, and immediately use its existing fenced commit. Only after that
commit succeeds may the worker skip its separate terminal-status transaction.
No stored flag authorizes this tail and no Goal/source check is replaced here.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, inspect, select

from .contracts import digest
from .leases import fenced, ownership
from .models import Job
from .relations import lock_current_actor
from .security import Principal


KIND = 'promote_goal_evaluation_v299'
SCHEMA = 'goal-evaluation-promotion-job-v299'
ERROR = '原评价发布任务、执行身份或租约无法核对；本次发布和任务终态必须一并回滚'
_PAYLOAD = frozenset({'schema', 'workspace_id', 'task_id', 'actor_id',
    'goal_check_job_id', 'evaluation_ref', 'prepared_audit_id', 'prepared_hash',
    'memory_id', 'report_hash', 'promoter_job_id', 'invocation_key', 'record_hash'})


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def stage_success(db, p, job) -> None:
    """Check the actual running row, then stage success without further IO.

The caller owns Task -> Goal Job -> promoter Job locks already. In particular,
do not move this into the ordinary terminal session (which locks own Job first)
and do not refresh/flush this dirty Job before the existing before_commit fence
has checked the still-running database row. An exception requires full rollback
of the caller's publication transaction, not a partial success commit.
"""
    with db.no_autoflush:
        _need(type(p) is Principal and p.role in {'admin', 'operator'}
            and type(job) is Job and job.kind == KIND and job.status == 'running'
            and job.workspace_id == p.workspace_id and type(job.data) is dict
            and _PAYLOAD <= job.data.keys())
        state = inspect(job)
        _need(state.persistent and state.session is db and not state.deleted
            and db.info.get('worker_actor') == p and fenced(db))
        owner = ownership(db)
        _need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
            and owner['job_id'] == job.id and type(owner['lease_token']) is str
            and bool(owner['lease_token']) and owner['lease_token'] == job.lease_token)
        payload = {key: job.data[key] for key in _PAYLOAD}
        _need(payload['schema'] == SCHEMA and payload['workspace_id'] == p.workspace_id
            and payload['actor_id'] == p.user_id and payload['promoter_job_id'] == job.id
            and type(payload['task_id']) is str and bool(payload['task_id'])
            and type(payload['goal_check_job_id']) is str and bool(payload['goal_check_job_id'])
            and job.idempotency_key == payload['invocation_key']
            == 'goal-evaluation-maturity:' + payload['goal_check_job_id']
            and payload['record_hash'] == digest({key: value for key, value in payload.items()
                if key != 'record_hash'}))
        # This is current IAM, not the retained Principal alone. The original
        # attached commit fence rechecks it again after this helper returns.
        lock_current_actor(db, p)
        actual = db.execute(select(Job.id.label('job_id'), func.clock_timestamp().label('at')).where(
            Job.id == job.id, Job.workspace_id == p.workspace_id, Job.kind == KIND,
            Job.status == 'running', Job.lease_token == owner['lease_token'],
            Job.lease_until > func.clock_timestamp(), Job.attempts == job.attempts,
            Job.idempotency_key == job.idempotency_key,
            Job.data['schema'].as_string() == SCHEMA,
            Job.data['actor_id'].as_string() == p.user_id,
            Job.data['task_id'].as_string() == payload['task_id'],
            Job.data['goal_check_job_id'].as_string() == payload['goal_check_job_id'],
            Job.data['promoter_job_id'].as_string() == job.id,
            Job.data['record_hash'].as_string() == payload['record_hash'])).one_or_none()
        _need(actual is not None and actual.job_id == job.id
            and type(actual.at) is datetime and actual.at.tzinfo is not None
            and actual.at.utcoffset() is not None)
        finished_at = actual.at.isoformat()
        # No SQL, refresh, flush or commit after the first terminal assignment.
        # before_commit must see the actual old running row before ORM flush.
        job.status = 'succeeded'
        job.lease_until = None
        job.data = {**job.data, 'finished_at': finished_at}
