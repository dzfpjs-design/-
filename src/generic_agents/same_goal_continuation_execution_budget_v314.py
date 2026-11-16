"""Charge real started read work against the unchanged cumulative Goal limit."""
from fastapi import HTTPException
from sqlalchemy import or_, select

from .contracts import digest
from .models import Invocation
from . import same_goal_continuation_execution_contracts_v314 as c


def need(value):
    if not value: raise HTTPException(409, '办理调用、预留或预算原件不能完整核对，未清零或另行执行')


def _records(db, p, task):
    # Only the formal, transactionally indexed family uses these reservations.
    # Final acceptance always runs the positive inventory even when absent.
    if c.INDEX not in task.data:
        return []
    from .same_goal_continuation_execution_store_v314 import accounting
    saved = accounting(db, p, task)
    ids = [row['plan'].id for row in saved]
    keys = [row['invocation_key'] for row in saved]
    jobs = [row['job'].id for row in saved]
    rows = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id, or_(
        Invocation.idempotency_key.in_(keys), Invocation.data['approval']['plan_id'].as_string().in_(ids),
        Invocation.data['job_id'].as_string().in_(jobs),
        (Invocation.data['approval']['schema'].as_string() == c.APPROVAL)
            & (Invocation.data['approval']['task_id'].as_string() == task.id)))
        .execution_options(populate_existing=True)))
    from .same_goal_continuation_execution_runtime_v314 import approval
    result = []; seen = set()
    for record in saved:
        plan, job = record['plan'], record['job']
        matches = [row for row in rows if row.idempotency_key == record['invocation_key']]
        need(len(matches) <= 1)
        call = matches[0] if matches else None
        if call is not None:
            basis = record['proposal']['grant']['basis']
            need(call.id not in seen and call.capability_id == basis['capability_pin']['id']
                and call.data['capability_hash'] == basis['capability_pin']['content_hash']
                and call.data['input_hash'] == basis['input_hash']
                and call.data['role'] == 'data' and call.data.get('job_id') == job.id
                and call.data.get('approval') == approval(record))
            seen.add(call.id)
        stopped_before_claim = (plan.status == 'cancelled' and job.status == 'cancelled'
            and job.attempts == 0 and job.lease_token is None and job.lease_until is None and call is None)
        result.append({'id': plan.id, 'charged': not stopped_before_claim, 'invocation': call})
    need(seen == {row.id for row in rows})
    return result


def step_count(db, p, task):
    with db.no_autoflush:
        return sum(row['charged'] for row in _records(db, p, task))


def additional_budget_calls(db, p, task):
    # Generic call_count already counts every actual SDK via approval.plan_id.
    with db.no_autoflush:
        return sum(row['charged'] and row['invocation'] is None for row in _records(db, p, task))


def check_new_action(db, p, task, execution_plan_id=None):
    from .native_goals import total_steps
    from .goal_queries import call_count
    rows = _records(db, p, task)
    own = [row for row in rows if row['id'] == execution_plan_id]
    need(len(own) <= 1)
    credit = int(bool(own and own[0]['charged']))
    calls, steps = call_count(db, p, task), total_steps(db, p, task)
    limits = task.data['goal_spec']
    if calls - credit + 1 > limits['max_tool_calls'] or steps - credit + 1 > limits['max_steps']:
        raise HTTPException(409, '本次办理超出原目标累计调用或步骤预算，请明确追加资源后再提交；旧花费和未知调用保留')
    return {'used_calls': calls, 'max_calls': limits['max_tool_calls'], 'used_steps': steps,
        'max_steps': limits['max_steps'], 'reserved_calls': 1, 'reserved_steps': 1,
        'cost_minor_units': 0, 'extra_goal_budget': False}
