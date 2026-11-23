"""Read-only v1/v2 Goal totals; never grants a write or an external call.

This separate candidate is not registered with an API or worker. Only pristine
queued v2 preparation is understood; its presence is reported separately and
does not constitute a Start, a reservation, or permission to create either.
Unknown or partial v2 states are rejected by the common inventory reader.
"""
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session

from . import goal_budget_snapshot_candidate_v2 as guard
from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_read_session_v2 as isolation
from . import same_goal_advice_store_v302 as old
from .models import DataRun, Invocation, Job, Plan, Task


def _counts(reader, principal, task, v1_calls):
    """Match the legacy counters with the authenticated versioned inventory.

    Keep these expressions aligned with goal_queries.call_count and
    native_goals.total_steps until all write-side counters migrate together.
    In particular a queued v2 Plan is not a paid or reserved Start.
    """
    from .workflow_graph_goal_guard_v133 import verify_reservations, uninvoked_calls
    from .goal_evaluation_append_store_v297 import additional_budget_calls
    from .same_goal_continuation_execution_budget_v314 import additional_budget_calls as continuation_calls
    from .same_goal_continuation_execution_budget_v314 import step_count as continuation_steps
    from .native_step_execution import step_count
    from .native_goal_queries import step_count as query_steps

    verify_reservations(reader, principal, task)
    plans = select(Plan.id).where(Plan.workspace_id == principal.workspace_id, Plan.task_id == task.id)
    query_calls = select(Invocation.id).join(DataRun,
        Invocation.idempotency_key.like('data-tool:' + DataRun.id + ':%')).where(
            Invocation.workspace_id == principal.workspace_id, DataRun.workspace_id == principal.workspace_id,
            or_(DataRun.data['goal_delegation']['task_id'].as_string() == task.id,
                DataRun.data['native_task_query_contract']['task_id'].as_string() == task.id))
    kinds = ['execute_workflow_step', 'execute_full_semantic_graph_node']
    workflow_jobs = select(Job.id).where(Job.workspace_id == principal.workspace_id,
        Job.kind.in_(kinds), Job.data['task_id'].as_string() == task.id)
    workflow_calls = select(Job.data['invocation_key'].as_string()).where(Job.workspace_id == principal.workspace_id,
        Job.kind.in_(kinds), Job.data['task_id'].as_string() == task.id)
    actual = reader.scalar(select(func.count()).select_from(Invocation).where(
        Invocation.workspace_id == principal.workspace_id,
        or_(Invocation.data['approval']['plan_id'].as_string().in_(plans), Invocation.id.in_(query_calls),
            Invocation.idempotency_key.in_(workflow_calls), Invocation.data['job_id'].as_string().in_(workflow_jobs)))) or 0
    calls = (actual + uninvoked_calls(reader, principal, task) + additional_budget_calls(reader, principal, task)
        + v1_calls + continuation_calls(reader, principal, task))
    steps = (len(task.data['workflow_runtime']['receipts']) + step_count(task)
        + query_steps(reader, principal, task) + v1_calls + continuation_steps(reader, principal, task))
    return calls, steps


@old.checked
def read_locked(db, principal, task):
    """Read candidate counters under the actual Task and custody locks.

    This is only for the unrouted v2 policy reader. It neither creates nor
    reserves work, and deliberately cannot read progressed v2 lifecycle
    states. The caller retains its transaction and must still separately
    recheck permissions and all authorization at any future Start/send.
    """
    old.need(type(task) is Task)
    state = task._sa_instance_state
    old.need(state.identity is not None and len(state.identity) == 1
        and object_session(task) is db and db.identity_map.get(state.key) is task)
    task_id = state.identity[0]
    with isolation.isolated(db, task_id) as reader:
        guard._extra_state(db, reader, principal.workspace_id)
        basis = inventory._read(reader, principal, task_id, lock=True)
        saved = reader.get(Task, task_id)
        old.need(saved is not None and saved.workspace_id == principal.workspace_id)
        calls, steps = _counts(reader, principal, saved, basis['v1_calls'])
        guard._extra_state(db, reader, principal.workspace_id)
        result = {'calls': calls, 'steps': steps, 'queued_v2': basis['queued_v2']}
    with db.no_autoflush:
        with Session(bind=db.connection(), autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as last_reader:
            inventory.v2._lock_current_reader(last_reader, principal)
    return result


@old.checked
def read_snapshot(db, principal, task):
    """Read persisted totals in one repeatable-read, read-only PG snapshot.

    The result is display-only, including when v2 exists. It cannot replace a
    fresh write-transaction inventory, capacity check, actor check or lease.
    """
    old.need(type(task) is Task)
    try: UUID(principal.session_id)
    except (ValueError, TypeError, AttributeError): old.need(False, '只读展示必须使用真实用户会话')
    state = task._sa_instance_state
    old.need(state.identity is not None and len(state.identity) == 1
        and object_session(task) is db and db.identity_map.get(state.key) is task,
        '目标任务不属于当前会话，未展示预算')
    task_id = state.identity[0]
    with isolation.isolated(db, task_id) as reader:
        old.need(reader.get_bind().dialect.name == 'postgresql')
        level, readonly = reader.execute(select(func.current_setting('transaction_isolation'),
            func.current_setting('transaction_read_only'))).one()
        old.need(readonly == 'on' and level in {'repeatable read', 'serializable'},
            '只读展示需要一致的数据库快照；不能用于发送授权')
        guard._extra_state(db, reader, principal.workspace_id)
        saved = reader.get(Task, task_id)
        old.need(saved is not None and saved.workspace_id == principal.workspace_id)
        basis = inventory._read(reader, principal, task_id, lock=False)
        calls, steps = _counts(reader, principal, saved, basis['v1_calls'])
        guard._extra_state(db, reader, principal.workspace_id)
        result = {'calls': calls, 'steps': steps, 'queued_v2': basis['queued_v2']}
    with db.no_autoflush:
        with Session(bind=db.connection(), autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as last_reader:
            inventory.v2._lock_current_reader(last_reader, principal, lock=False)
    return result
