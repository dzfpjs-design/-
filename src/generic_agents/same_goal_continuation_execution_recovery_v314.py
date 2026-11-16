"""Recover only the SDK owned by the original continuation worker Job."""

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import inspect, or_, select

from . import leases
from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_grant_contracts_v313 as tools
from .contracts import digest
from .models import Invocation, Job, now
from .services import audit


def _need(value):
    if not value:
        raise HTTPException(409, '原办理调用或后台任务无法核对；保留原号，未重新计算')


def _original(db, p, job, key):
    approval = Invocation.data['approval']
    return list(db.scalars(select(Invocation).where(
        Invocation.workspace_id == p.workspace_id, or_(
            Invocation.idempotency_key == key,
            Invocation.data['job_id'].as_string() == job.id,
            (approval['schema'].as_string() == c.APPROVAL)
            & (approval['job_id'].as_string() == job.id),
        )).order_by(Invocation.id).limit(2).with_for_update().execution_options(populate_existing=True)))


def recover_calls(db, p, job):
    """Run inside the initial Job transaction, before any Task or Plan lock."""
    try:
        _need(isinstance(job, Job))
        value = job.data
        state = inspect(job)
        _need(state.persistent and state.session is db
            and job.kind == c.KIND and job.workspace_id == p.workspace_id
            and job.status == 'running' and job.lease_until is not None
            and type(value) is dict and value.get('schema') == c.JOB
            and value.get('actor_id') == p.user_id
            and leases.fenced(db)
            and leases.ownership(db) == {'job_id': job.id, 'lease_token': job.lease_token})
        for identity in (job.id, job.lease_token, value['task_id'], value['plan_id'], value['actor_id']):
            TypeAdapter(c.ID).validate_python(identity)
        for fingerprint in (value['submission_hash'], value['input_hash']):
            TypeAdapter(c.Hash).validate_python(fingerprint)
        key = 'continuation-read-v314:' + value['plan_id'] + ':1'
        _need(job.idempotency_key == value['invocation_key'] == key)
        with db.no_autoflush:
            calls = _original(db, p, job, key)
            _need(len(calls) <= 1)
            if not calls:
                return 0
            call = calls[0]
            data = call.data
            authorization = data['approval']
            expected = {'schema': c.APPROVAL, 'task_id': value['task_id'],
                'plan_id': value['plan_id'], 'job_id': job.id,
                'grant_ref': c.dump(c.GrantRef, authorization['grant_ref']),
                'submission_hash': value['submission_hash'], 'input_hash': value['input_hash']}
            TypeAdapter(c.ID).validate_python(call.capability_id)
            previous_token = TypeAdapter(c.ID).validate_python(data['lease_token'])
            _need(call.workspace_id == p.workspace_id and call.idempotency_key == key
                and data.get('job_id') == job.id and digest(authorization) == digest(expected)
                and data.get('role') == 'data' and data.get('side_effect') is False
                and data['input_hash'] == value['input_hash'] == digest(data['inputs'])
                and data['capability_hash'] == digest(data['capability_snapshot'])
                and call.status in {'running', 'unknown', 'failed', 'succeeded', 'not_executed'})
            tools.validate_manifest(data['capability_snapshot'])
            if call.status == 'running':
                _need(previous_token != job.lease_token)
                call.status = 'unknown'
                call.version += 1
                call.data = {**data, 'error': '原办理调用中断，结果待核，未重新计算',
                    'recovery': {'recovered_at': now().isoformat(), 'previous_lease_token': previous_token,
                        'recovering_job_id': job.id, 'reason': 'expired_worker_lease'}}
                audit(db, p, 'worker.invocation_recovered', call.id, {'status': 'unknown', 'job_id': job.id})
            job.data = {**value, 'recovery_only': True}
            return 1
    except (AttributeError, KeyError, TypeError, ValueError):
        _need(False)
