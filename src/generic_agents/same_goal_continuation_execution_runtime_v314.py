"""Real single-read SDK authority, immutable output custody and safe recovery."""
import copy
import math
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_execution_store_v314 as store
from . import same_goal_continuation_execution_sources_v314 as sources
from . import same_goal_continuation_grant_contracts_v313 as tools
from . import leases
from .contracts import digest
from .models import Audit, Event, Invocation, Job, Memory, Plan, uid
from .services import validate
from .temporal import instant

DISPATCHED = 'same_goal_continuation.execution_dispatched_v314'
DISPATCH_SCHEMA = 'same-goal-continuation-read-dispatch-v314'
HALTED = 'same_goal_continuation.execution_halted_v314'


def need(value, detail='办理的实际批准、租约、调用或结果原件不完整，未发送或替换调用'):
    if not value: raise HTTPException(409, detail)


def _clock(db):
    at = db.scalar(select(func.clock_timestamp()))
    need(isinstance(at, datetime) and at.tzinfo is not None)
    return at


def _same(left, right): return digest(left) == digest(right)


def no_original_effects(db, p, saved):
    """A missing SDK cannot authorize replacement of a dispatched original."""
    from .same_goal_continuation_result_receiver_v314 import KIND, EVENT_SCHEMA, MEMORY_TYPE, RECEIVED
    plan, job = saved['plan'], saved['job']
    need(plan.data.get('output_ref') is None and plan.data.get('receipt_ref') is None)
    audits = store._rows(db, p, Audit, Audit.action.in_([DISPATCHED, store.OUTPUT, store.RECEIVED, HALTED, RECEIVED]),
        or_(Audit.object_id == plan.id, Audit.data['plan_id'].as_string() == plan.id,
            Audit.data['job_id'].as_string() == job.id))
    events = store._rows(db, p, Event, or_(Event.idempotency_key == 'continuation-read-result-v314:' + plan.id,
        or_(Event.kind == KIND, Event.data['schema'].as_string() == EVENT_SCHEMA)
            & or_(Event.data['plan_id'].as_string() == plan.id, Event.data['job_id'].as_string() == job.id)))
    memories = store._rows(db, p, Memory,
        Memory.data['type'].as_string() == MEMORY_TYPE,
        or_(Memory.data['plan_id'].as_string() == plan.id, Memory.data['job_id'].as_string() == job.id))
    need(not audits and not events and not memories,
        '原调用记录缺失但仍有派发或结果原件，未创建替代调用；请核对原办理记录')


def approval(saved):
    proposal, plan, job = saved['proposal'], saved['plan'], saved['job']
    return {'schema': c.APPROVAL, 'task_id': proposal['task_id'], 'plan_id': plan.id,
        'job_id': job.id, 'grant_ref': copy.deepcopy(proposal['grant_ref']),
        'submission_hash': proposal['record_hash'],
        'input_hash': proposal['grant']['basis']['input_hash']}


def _load(db, p, task_id, plan_id, *, lock=False):
    task = store._task(db, p, task_id, lock=lock)
    rows = [row for row in store._inventory(db, p, task) if row['plan'].id == plan_id]
    need(len(rows) == 1 and rows[0]['start'] is not None and rows[0]['job'] is not None)
    return task, rows[0]


def _call_identity(p, task, saved, job, call):
    basis = saved['proposal']['grant']['basis']; pin = basis['capability_pin']
    need(job.id == saved['job'].id and job.kind == c.KIND and job.workspace_id == p.workspace_id
        and job.data.get('schema') == c.JOB and job.data['task_id'] == task.id
        and job.data['plan_id'] == saved['plan'].id and job.data['actor_id'] == saved['start'].actor_id
        and job.data['submission_hash'] == saved['proposal']['record_hash']
        and job.data['input_hash'] == basis['input_hash'])
    need(call.workspace_id == p.workspace_id and call.capability_id == pin['id']
        and call.idempotency_key == job.idempotency_key == job.data['invocation_key']
        and call.data.get('approval') == approval(saved) and call.data.get('job_id') == job.id
        and call.data['role'] == 'data' and call.data['capability_hash'] == pin['content_hash']
        and call.data['input_hash'] == basis['input_hash'] == digest(call.data['inputs'])
        and _same(call.data['inputs'], basis['inputs'])
        and digest(call.data['capability_snapshot']) == pin['content_hash'])
    tools.validate_manifest(call.data['capability_snapshot'])
    return basis


def authorize_gateway(db, p, cap, agent_role, inputs, key, authorization):
    need(type(authorization) is dict and authorization.get('schema') == c.APPROVAL)
    owned = leases.ownership(db)
    need(leases.fenced(db) and type(owned) is dict and owned.get('job_id') == authorization.get('job_id')
        and owned.get('lease_token') is not None)
    task, saved = _load(db, p, authorization.get('task_id'), authorization.get('plan_id'), lock=True)
    job = saved['job']; basis = saved['proposal']['grant']['basis']
    need(saved['plan'].status == 'queued' and job.status == 'running'
        and job.lease_token is not None and job.lease_until is not None
        and job.lease_until > _clock(db) and leases.fenced(db)
        and leases.ownership(db) == {'job_id': job.id, 'lease_token': job.lease_token}
        and p.user_id == job.data['actor_id'] == saved['start'].actor_id
        and authorization == approval(saved) and agent_role == 'data'
        and key == job.idempotency_key and cap.id == basis['capability_pin']['id']
        and cap.content_hash == basis['capability_pin']['content_hash']
        and _same(inputs, basis['inputs']) and digest(inputs) == basis['input_hash'])
    current = sources.live(db, p, task, saved['proposal'], execution_plan_id=saved['plan'].id, lock=True)
    if not store._invocations(db, p, saved):
        no_original_effects(db, p, saved)
    from .layered_approval_store_v175 import role_allowed
    spec = current['policy']['spec']
    need(len(saved['approvers']) == len(spec['stages']))
    for stage, actor in zip(spec['stages'], saved['approvers']):
        role_allowed(db, p, stage, actor_id=actor)
    role_allowed(db, p, spec['executor'])
    return task, saved


def before_dispatch(db, p, cap, agent_role, inputs, key, authorization):
    task, saved = authorize_gateway(db, p, cap, agent_role, inputs, key, authorization)
    job = saved['job']; rows = store._invocations(db, p, saved)
    need(len(rows) == 1 and rows[0].status == 'running')
    call = rows[0]; _call_identity(p, task, saved, job, call)
    need(call.data.get('lease_token') == job.lease_token)
    previous = store._rows(db, p, Audit, Audit.action == DISPATCHED, Audit.object_id == saved['plan'].id)
    need(not previous, '本次调用已有派发原件，未再次计算')
    at = _clock(db)
    from . import same_goal_advice_store_v302 as original_advice
    advice = original_advice._fresh(db, p, Plan, saved['proposal']['advice_plan_id'])
    prep, _prepare_job = original_advice._saved(db, p, task, advice)
    need(prep['record_hash'] == saved['proposal']['source']['preparation_snapshot_hash'])
    value = {'schema': DISPATCH_SCHEMA, 'task_id': task.id, 'plan_id': saved['plan'].id,
        'job_id': job.id, 'invocation_id': call.id, 'submission_hash': saved['proposal']['record_hash'],
        'input_hash': call.data['input_hash'], 'capability_hash': call.data['capability_hash'],
        'approval_hash': digest(authorization), 'executor_id': p.user_id, 'data_actor_id': prep['data_actor_id'],
        'lease_token': job.lease_token, 'lease_until': job.lease_until.isoformat(), 'dispatched_at': at.isoformat()}
    value['record_hash'] = c.record_hash(value)
    db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=DISPATCHED, object_id=saved['plan'].id, created_at=at, data=value))
    db.flush()


def checked_output(db, p, task, saved, job, call):
    basis = _call_identity(p, task, saved, job, call)
    need(call.status == 'succeeded')
    rows = store._rows(db, p, Audit, Audit.action == DISPATCHED, Audit.object_id == saved['plan'].id)
    outcomes = store._rows(db, p, Audit, Audit.action == 'capability.invoke', Audit.object_id == call.id)
    need(len(rows) == len(outcomes) == 1 and outcomes[0].actor_id == saved['start'].actor_id
        and outcomes[0].data == {'status': 'succeeded'})
    event = rows[0]; value = event.data
    need(value['schema'] == DISPATCH_SCHEMA and value['record_hash'] == c.record_hash(value)
        and event.actor_id == value['executor_id'] == saved['start'].actor_id
        and value['task_id'] == task.id and value['plan_id'] == saved['plan'].id
        and value['job_id'] == job.id and value['invocation_id'] == call.id
        and value['submission_hash'] == saved['proposal']['record_hash']
        and value['input_hash'] == basis['input_hash']
        and value['capability_hash'] == basis['capability_pin']['content_hash']
        and value['approval_hash'] == digest(approval(saved))
        and value['lease_token'] == call.data['lease_token']
        and instant(value['dispatched_at']) < instant(value['lease_until']))
    output = copy.deepcopy(call.data['output'])
    validate(call.data['capability_snapshot']['output_schema'], output)
    records = basis['inputs']['records']; field = basis['inputs']['field']; operation = basis['inputs']['operation']
    values = [row['attributes'].get(field) for row in records]
    selected = [v for v in values if type(v) in {int, float} and math.isfinite(v)]
    need(output['field'] == field and output['operation'] == operation
        and output['sample_count'] == len(selected) and output['missing_count'] == len(records) - len(selected)
        and output['provenance'] == [{'entity_id': row['entity_id'], 'version': row['version']} for row in records])
    expected = (len(selected) if operation == 'count' else None if not selected else
        sum(selected) / len(selected) if operation == 'mean' else sum(selected) if operation == 'sum' else
        min(selected) if operation == 'min' else max(selected))
    actual = output['value']
    need(actual is None if expected is None else type(actual) in {int, float}
        and math.isfinite(actual) and math.isfinite(expected)
        and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12), '实际计算结果与批准记录不一致，未采用或伪造回执')
    return output


def checked_receipt(db, p, task, saved, output_audit, ack):
    from .same_goal_continuation_result_receiver_v314 import checked_receipt as checked
    return checked(db, p, task, saved, output_audit, ack)


def ensure_event(db, p, task, saved, output, *, retry_blocked=False):
    from .same_goal_continuation_result_receiver_v314 import enqueue
    return enqueue(db, p, task, saved, output, retry_blocked=retry_blocked)


def process(db, p, job):
    task, saved = _load(db, p, job.data['task_id'], job.data['plan_id'], lock=True)
    need(saved['job'].id == job.id and saved['start'].actor_id == p.user_id)
    calls = store._invocations(db, p, saved)
    if saved['plan'].status == 'cancelled':
        need(not calls); return 'cancelled'
    if calls:
        need(len(calls) == 1)
        call = calls[0]
        if call.status != 'succeeded':
            raise HTTPException(409, '原调用结果尚未核清，未按新编号或原编号重新计算')
        checked_output(db, p, task, saved, job, call)
    else:
        no_original_effects(db, p, saved)
        need(not job.data.get('recovery_only'), '恢复申请没有可信原结果，未发送新调用')
        if saved['plan'].status == 'cancel_requested':
            need(leases.fenced(db) and leases.ownership(db) == {'job_id': job.id, 'lease_token': job.lease_token})
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action=HALTED, object_id=saved['plan'].id, data={
                    'task_id': task.id, 'plan_id': saved['plan'].id, 'job_id': job.id,
                    'submission_hash': saved['proposal']['record_hash'],
                    'lease_token': job.lease_token, 'reason': 'cancelled_before_sdk'}))
            db.flush()
            return 'cancelled'
        sources.live(db, p, task, saved['proposal'], execution_plan_id=saved['plan'].id, lock=True)
        from .gateway import invoke
        basis = saved['proposal']['grant']['basis']
        call = invoke(db, p, basis['capability_pin']['id'], 'data', copy.deepcopy(basis['inputs']),
            job.idempotency_key, approval=approval(saved))
        need(call.status == 'succeeded')
    result = store.save_output(db, p, task.id, saved['plan'].id, job, call)
    ensure_event(db, p, task, saved, result)
    return 'succeeded'


def recover_saved(db, p, task, saved, body):
    job = saved['job']; calls = store._invocations(db, p, saved)
    need(job is not None and len(calls) == 1 and calls[0].status == 'succeeded',
        '尚无可信原结果可收尾，未重发调用')
    checked_output(db, p, task, saved, job, calls[0])
    need(job.status != 'running' or job.lease_until is None or job.lease_until <= _clock(db),
        '原后台处理仍在进行，请读取原进度，不另行恢复')
    if job.status == 'succeeded':
        output, _ = store._results(db, p, task, saved)
        need(output is not None)
        ensure_event(db, p, task, saved, {'output_ref': store._ref(output),
            'output': output.data['output'], 'output_hash': output.data['output_hash']}, retry_blocked=True)
        return {'phase': 'awaiting_original_receipt'}
    old = {'status': job.status, 'attempts': job.attempts, 'request_id': body['request_id'],
        'error': job.data.get('error')}
    job.data = {**job.data, 'recovery_only': True,
        'custody_recoveries_v314': [*job.data.get('custody_recoveries_v314', []), old]}
    job.status = 'queued'; job.available_at = _clock(db); job.lease_token = None; job.lease_until = None
    job.version += 1
    return {'phase': 'same_job_saved_result_recovery_queued'}
